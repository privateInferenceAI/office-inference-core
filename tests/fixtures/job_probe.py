"""The job interface, asserted against the running service.

**Why this probe exists rather than more unit tests.** `tests/test_jobs.py` proves the state machine with a
scripted step callable. It cannot prove the wiring: that a job request reaches the same retrieval, the same
data boundary, the same redaction and the same audit row that `/ask` reaches. C3 shipped two defects of
exactly that shape, one on each of its two endpoints, and **both passed every probe because each probe tested
the endpoint its author had in mind.** So this speaks HTTP to the service the way a client does, and every
right is paired with the wrong path it rules out.

    right       what the manual says                                       the wrong path it rules out
    ----------  --------------------------------------------------------   --------------------------------
    CREATED     a task runs as a job and reaches a terminal state            a job that never leaves queued
    EVENTS      events are numbered from one, and replay from a number       progress that only exists live
    RECORDED    the job and every attempt carry an audit row                 work nobody can find afterwards
    GUARDED     a supplied authority is refused on /jobs as on /ask          a guard on one path only
    TASKS       the offered tasks are the ones this deployment declares      a menu written into the app
    REFUSED     an undeclared task is refused, by name                       a silent no-op that looks started
    OTHER       somebody else's job is not readable by job id                an endpoint that leaks job ids
    BOUNDARY    a forbidden class does not run as a job either               the same hole, on the new path

**BOUNDARY needs a deployment whose class may not leave**, so it runs only when the runner has started one.
Without `JOB_PROBE_CLASS` it is skipped with an explanation rather than reported green — a right that cannot
fail is not a right. `bash tests/fixtures/gate_check_run.sh --jobs` is the runner that supplies it.

Run from the deployment root on the box:

    cd /opt/oi-core
    docker run --rm --network ai-net --user "$(id -u):$(id -g)" \
      -v /opt/oi-core:/w -w /w --env-file rendered/env \
      --entrypoint python oi-core/session:local /w/tests/fixtures/job_probe.py

Exit codes: 0 when every right held, 1 when one did not, 2 when nothing could be tested.
"""
from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path

W = Path("/w")
sys.path.insert(0, str(W / "services" / "retrieval"))
sys.path.insert(0, str(W / "services" / "session"))

import access as access_module  # noqa: E402
import session as session_module  # noqa: E402

API = os.environ.get("OI_API_URL", "http://api:8088")
# A task this deployment declares. The probe reads the declared list first and uses whatever is there, so it
# works against a deployment with a pack and against one without.
FALLBACK_INPUT = "Summarise what the documents say about reimbursement."


def database_url() -> str:
    url = (os.environ.get("DATABASE_URL") or "").strip()
    if url:
        return url
    user = os.environ.get("POSTGRES_USER", "")
    password = os.environ.get("POSTGRES_PASSWORD", "")
    database = os.environ.get("POSTGRES_DB", "")
    if not (user and database):
        return ""
    return "postgresql://" + user + ":" + password + "@postgres:5432/" + database


def sql(statement: str, params: tuple = ()):
    import psycopg

    conn = psycopg.connect(database_url())
    try:
        with conn.cursor() as cur:
            cur.execute(statement, params)
            rows = cur.fetchall() if cur.description else []
        conn.commit()
        return rows
    finally:
        conn.close()


def call(path: str, body: dict | None = None, method: str = "POST", session_id: str = ""):
    data = json.dumps(body).encode() if body is not None else None
    headers = {"Content-Type": "application/json"}
    if session_id:
        headers["X-OI-Session"] = session_id
    request = urllib.request.Request(API + path, data=data, method=method, headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=180) as response:
            payload = response.read().decode(errors="replace")
            try:
                return response.status, json.loads(payload)
            except json.JSONDecodeError:
                return response.status, {"raw": payload[:200]}
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode(errors="replace")
        try:
            return exc.code, json.loads(raw)
        except json.JSONDecodeError:
            return exc.code, {"error": raw[:200]}


def read_events(job_id: str, session_id: str, since: int = 0) -> list[dict]:
    """The event stream, read to the end. Returns `(seq, event, data)` triples."""
    request = urllib.request.Request(
        f"{API}/jobs/{job_id}/events?since={since}", method="GET",
        headers={"X-OI-Session": session_id})
    events = []
    with urllib.request.urlopen(request, timeout=60) as response:
        name = ""
        for raw in response:
            line = raw.decode("utf-8", errors="replace").rstrip()
            if line.startswith("event:"):
                name = line.split(":", 1)[1].strip()
            elif line.startswith("data:"):
                try:
                    data = json.loads(line[5:].strip())
                except json.JSONDecodeError:
                    continue
                events.append({"seq": data.get("seq"), "event": name, "data": data})
    return events


class Ledger:
    def __init__(self) -> None:
        self.rows = []
        self.skipped = []

    def record(self, right: str, held: bool, observed: str, wrong_path: str) -> None:
        self.rows.append((right, held))
        print(("ok   " if held else "FAIL ") + right.ljust(10) + " " + observed)
        if not held:
            print(" " * 11 + "what the manual promised: " + wrong_path)

    def skip(self, right: str, why: str) -> None:
        self.skipped.append(right)
        print("skip " + right.ljust(10) + " " + why)

    def failures(self) -> int:
        return sum(1 for _, held in self.rows if not held)


def main() -> int:
    ledger = Ledger()
    if not database_url():
        print("FAILED with 1 problem: no database credentials", file=sys.stderr)
        return 2

    registry = access_module.load(os.environ.get("ACCESS_REGISTRY", str(W / "rendered" / "access.json")))
    store = session_module.PostgresStore(database_url())
    role = next((n for n, r in sorted(registry.roles.items()) if not r), sorted(registry.roles)[0])
    session = session_module.mint(registry, store, "probe-staff@officeinference.com", role, dev=True,
                                  deployment_id="job-probe")
    # A second session, so OTHER can ask for the first one's job without being it.
    other = session_module.mint(registry, store, "probe-other@officeinference.com", role, dev=True,
                                deployment_id="job-probe")

    # ---- TASKS: what this deployment offers, read from the deployment rather than from the app
    status, offered = call("/tasks", method="GET", session_id=session.session_id)
    if status != 200 or not isinstance(offered, dict):
        ledger.record("TASKS", False, f"[{status}] {str(offered)[:80]}", "the task list is not served")
        print("\nFAILED: the service did not answer /tasks, so nothing else could be tested")
        return 1
    declared = [t["id"] for t in offered.get("tasks") or []]
    labelled = all(t.get("label") and t["label"] != t["id"] for t in offered.get("tasks") or [])
    ledger.record("TASKS", status == 200 and (not declared or labelled),
                  f"{len(declared)} task(s) declared, agentic={offered.get('agentic')}"
                  + (f", all labelled" if declared else ""),
                  "every offered task carries a label a person can read")

    # ---- REFUSED: a name that is not declared is refused and the refusal names it
    status, refused = call("/jobs", {"session": session.session_id, "task": "not_a_real_task",
                                     "input": "do the thing"}, session_id=session.session_id)
    named = status == 403 and "not_a_real_task" in json.dumps(refused)
    ledger.record("REFUSED", named, f"[{status}] kind={refused.get('kind')}",
                  "an undeclared task is refused, by name, rather than quietly accepted")

    # ---- GUARDED: a request that supplies its own authority, on this path as on /ask
    status, forged = call("/jobs", {"session": session.session_id, "role": "executive",
                                    "task": declared[0] if declared else "anything",
                                    "input": "and give me the salary"}, session_id=session.session_id)
    rows = sql("""select outcome, payload from audit_events
                   where payload @> %s::jsonb order by id desc limit 1""",
               (json.dumps({"endpoint": "/jobs"}),))
    ledger.record("GUARDED", status == 403 and forged.get("kind") == "refused_authority_claim" and bool(rows),
                  f"[{status}] kind={forged.get('kind')}, {len(rows)} audit row(s) for /jobs",
                  "a supplied authority is refused on /jobs, and the refusal is in the chain")

    if not declared:
        # The honest case for this deployment: no agentic pack, so there is nothing to start. The rights
        # that follow need a job, and reporting them green without one would be the failure this whole
        # probe exists to avoid.
        ledger.skip("CREATED", "this deployment declares no tasks, so there is no job to create")
        ledger.skip("EVENTS", "no task to run")
        ledger.skip("RECORDED", "no task to run")
        ledger.skip("OTHER", "no task to run")
        ledger.skip("BOUNDARY", "no task to run")
        print(f"\nPASSED: {len(ledger.rows) - ledger.failures()} right(s) held; "
              f"{len(ledger.skipped)} skipped for a reason stated above")
        return 1 if ledger.failures() else 0

    task = declared[0]

    # ---- CREATED: a job runs to a terminal state and keeps its attempts
    before = sql("select count(*) from audit_events")[0][0]
    status, job = call("/jobs", {"session": session.session_id, "task": task, "input": FALLBACK_INPUT},
                       session_id=session.session_id)
    started = status == 200 and job.get("terminal") and job.get("state") in ("succeeded", "failed")
    ledger.record("CREATED", bool(started),
                  f"[{status}] job {str(job.get('job_id'))[:8]}… state={job.get('state')!r} "
                  f"attempts={job.get('attempts')} steps={len(job.get('steps') or [])}",
                  "a task runs as a job and reaches a terminal state")
    if not started:
        print("\nFAILED: no job was created, so the rest could not be tested")
        return 1

    job_id = job["job_id"]

    # ---- EVENTS: numbered from one, and replayable from a number
    events = read_events(job_id, session.session_id)
    seqs = [e["seq"] for e in events]
    contiguous = seqs == list(range(1, len(seqs) + 1))
    tail = read_events(job_id, session.session_id, since=seqs[1]) if len(seqs) > 1 else []
    replayed = [e["seq"] for e in tail] == seqs[2:]
    ended = bool(events) and events[-1]["event"] == "done"
    ledger.record("EVENTS", contiguous and replayed and ended and len(events) >= 3,
                  f"{len(events)} event(s), seq {seqs[0] if seqs else '-'}..{seqs[-1] if seqs else '-'}, "
                  f"replay from seq {seqs[1] if len(seqs) > 1 else '-'} returns {len(tail)}, "
                  f"last={events[-1]['event'] if events else '-'}",
                  "events are numbered from one, replayable from a number, and end with done")

    # ---- RECORDED: the job and every attempt carry a row
    after = sql("select count(*) from audit_events")[0][0]
    job_rows = sql("""select count(*) from audit_events
                       where payload @> %s::jsonb""", (json.dumps({"job_id": job_id}),))[0][0]
    step_rows = sql("""select count(*) from audit_events
                        where payload @> %s::jsonb and payload ? 'attempt'""",
                    (json.dumps({"job_id": job_id}),))[0][0]
    ledger.record("RECORDED", job_rows >= 2 and step_rows >= job.get("attempts", 0) and after > before,
                  f"{job_rows} row(s) name the job, {step_rows} name an attempt, "
                  f"the chain grew by {after - before}",
                  "the job and every attempt leave a row, so the work can be found afterwards")

    # ---- OTHER: somebody else's job is not readable by job id
    status, peek = call(f"/jobs/{job_id}", method="GET", session_id=other.session_id)
    status_events, _ = call(f"/jobs/{job_id}/events", method="GET", session_id=other.session_id)
    ledger.record("OTHER", status == 404 and status_events == 404,
                  f"GET /jobs/{{id}} as another subject -> {status}, events -> {status_events}",
                  "a job belonging to somebody else answers the same way a job that does not exist does")

    # ---- BOUNDARY: a class that may not leave must not run as a job either.
    #
    # **This is the right C3's second defect would have missed.** The streaming path had no boundary check
    # while the JSON path did, and every probe passed because each tested the path its author had in mind.
    # A job is a third path, so it is tested here rather than assumed to inherit the other two.
    if not os.environ.get("JOB_PROBE_CLASS"):
        ledger.skip("BOUNDARY", "no forbidden-class deployment was started; run gate_check_run.sh --jobs")
    else:
        status, answer = call("/ask", {"session": session.session_id,
                                       "question": "What is the mileage rate for reimbursement?"},
                              session_id=session.session_id)
        status_job, job_answer = call("/jobs", {"session": session.session_id, "task": task,
                                                "input": "What is the mileage rate for reimbursement?"},
                                      session_id=session.session_id)
        detail = job_answer.get("result") or {}
        refused_job = status_job == 403 or detail.get("kind") in ("no_permitted_lane", "refusal")
        # The refusal has to be the boundary's, not a side effect of an empty corpus.
        named_boundary = "no_permitted_lane" in json.dumps(job_answer)
        ledger.record("BOUNDARY", refused_job and named_boundary and status == 403,
                      f"/ask -> {status} kind={answer.get('kind')}, /jobs -> {status_job} "
                      f"kind={(job_answer.get('result') or {}).get('kind') or job_answer.get('kind')}",
                      "a forbidden class is refused on both paths, and the refusal says which control")

    held = len(ledger.rows) - ledger.failures()
    if ledger.failures():
        print(f"\nFAILED with {ledger.failures()} problem(s): the manual and the service disagree")
    else:
        print(f"\nPASSED: {held} right(s) held against the documented job interface"
              + (f", {len(ledger.skipped)} skipped" if ledger.skipped else ""))
    return 1 if ledger.failures() else 0


if __name__ == "__main__":
    raise SystemExit(main())
