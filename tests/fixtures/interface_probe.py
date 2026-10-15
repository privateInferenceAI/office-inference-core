"""Every interface claim this build makes, asserted against the running service.

**Why this exists rather than more unit tests.** The unit suite proves the pieces and not the wiring: two
runtime defects reached the deployed API while 587 tests passed, because the tests never exercised a handler.
This probe speaks to the service over HTTP the way a client does, and each right is a sentence the manual tells a
reader to expect. If the manual and the service disagree, this fails.

    right       the manual says                                      the wrong path it rules out
    ----------  --------------------------------------------------  ------------------------------------
    ANSWER      a question answers, with citations                   a broken default path
    NO-JUDGE    a plain question escalates nothing                  a trigger that fires on everything
    TRIGGERS    the two triggers each escalate and name themselves   a trigger that fires silently
    TOLD        an escalation the gate refuses is announced          a silent downgrade
    FACTS       the row carries asked and answered separately        the two conflated into one field
    LANE-AGREE  the lane shown and the lane recorded are one fact     a claim the record contradicts
    DEGRADED    a model failure yields passages, not an error        a hang, or a 500 with no answer
    STREAM      the stream carries a first token before the last     a response that arrives whole

Run from the deployment root on the box:

    cd /opt/oi-core
    docker run --rm --network ai-net --user "$(id -u):$(id -g)" \
      -v /opt/oi-core:/w -w /w --env-file rendered/env \
      --entrypoint python oi-core/session:local /w/tests/fixtures/interface_probe.py

Exit codes: 0 when every right held, 1 when one did not, 2 when nothing could be tested.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

W = Path("/w")
sys.path.insert(0, str(W / "services" / "retrieval"))
sys.path.insert(0, str(W / "services" / "session"))

import access as access_module  # noqa: E402
import session as session_module  # noqa: E402

API = os.environ.get("OI_API_URL", "http://api:8088")
QUESTION = "What is the mileage rate for reimbursement?"


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


def call(path: str, session_id: str):
    """A GET against the service, with the session header. For the task list, which is where the probe finds
    out what this deployment can actually be asked to do."""
    request = urllib.request.Request(API + path, method="GET",
                                     headers={"X-OI-Session": session_id})
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            return response.status, json.loads(response.read())
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode(errors="replace")
        try:
            return exc.code, json.loads(raw)
        except json.JSONDecodeError:
            return exc.code, {"error": raw[:160]}


def ask(session_id: str, extra: dict | None = None):
    body = {"session": session_id, "question": QUESTION}
    body.update(extra or {})
    request = urllib.request.Request(API + "/ask", data=json.dumps(body).encode(), method="POST",
                                     headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=180) as response:
            return response.status, json.loads(response.read())
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode(errors="replace")
        try:
            return exc.code, json.loads(raw)
        except json.JSONDecodeError:
            return exc.code, {"error": raw[:160]}


def stream(session_id: str):
    body = json.dumps({"session": session_id, "question": QUESTION}).encode()
    request = urllib.request.Request(API + "/ask/stream", data=body, method="POST",
                                     headers={"Content-Type": "application/json"})
    started = time.monotonic()
    frames = 0
    first_delta = None
    done = {}
    with urllib.request.urlopen(request, timeout=180) as response:
        for raw in response:
            line = raw.decode("utf-8", errors="replace").rstrip()
            if line.startswith("event:"):
                frames += 1
                if "delta" in line and first_delta is None:
                    first_delta = time.monotonic() - started
            if line.startswith("data:") and "total_ms" in line:
                done = json.loads(line[5:].strip())
    return frames, first_delta, done


class Ledger:
    def __init__(self) -> None:
        self.rows = []

    def record(self, right: str, held: bool, observed: str, wrong_path: str) -> None:
        self.rows.append((right, held))
        print(("ok   " if held else "FAIL ") + right.ljust(10) + " " + observed)
        if not held:
            print(" " * 11 + "what the manual promised: " + wrong_path)

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
                                  deployment_id="interface-probe")

    # ANSWER and NO-JUDGE from one request: the manual says a plain question answers, and that it escalates
    # nothing. A trigger that fired on everything would show here as escalated true.
    status, body = ask(session.credential)
    answered = status == 200 and not body.get("kind") and body.get("citations")
    ledger.record("ANSWER", bool(answered),
                  "[%s] %d citation(s), lane=%r" % (status, len(body.get("citations") or []), body.get("lane")),
                  "a question about a document in the corpus answers with citations")
    ledger.record("NO-JUDGE", body.get("escalated") is False and not body.get("trigger"),
                  "escalated=%r trigger=%r notice=%r" % (body.get("escalated"), body.get("trigger"),
                                                        bool(body.get("notice"))),
                  "a plain question escalates nothing and tells the user nothing")

    # TRIGGERS: each names itself, and the record separates what was asked for from what ran.
    #
    # **The task is read from the deployment, not written here.** This right used to send `reconcile`, a name
    # that was hardcoded in the API before C4 moved the list into the manifest. When the manifest replaced the
    # hardcoded list, `reconcile` stopped existing — and this probe kept passing, because the trigger it was
    # sent for no longer fired and nothing it asserted noticed. It failed honestly once the router began
    # refusing a task the deployment does not declare. A probe that names its own task is a probe that passes
    # on a task that is gone.
    status_tasks, offered = call("/tasks", session_id=session.credential)
    declared = [t.get("id") for t in (offered.get("tasks") or [])]
    if not declared:
        ledger.record("TRIGGERS", False, "[%s] no task is declared" % status_tasks,
                      "the task lookup escalates and names itself")
        declared = []
    task_id = declared[0] if declared else ""
    _, agentic = ask(session.credential, {"task": task_id}) if task_id else (0, {})
    _, asked = ask(session.credential, {"stronger": True})
    named = bool(task_id) and agentic.get("trigger") == "agentic_task" \
        and asked.get("trigger") == "user_requested_stronger"
    ledger.record("TRIGGERS", bool(named),
                  "task %r -> %r, stronger -> %r" % (task_id, agentic.get("trigger"),
                                                     asked.get("trigger")),
                  "the task lookup and the request field each escalate and name themselves")

    rows = sql("select payload from audit_events where payload ? 'routing_version' order by id desc limit 6")
    payloads = [r[0] or {} for r in rows]
    separated = any(p.get("escalation_requested") is True for p in payloads) and \
        any(p.get("escalation_requested") is False for p in payloads) and all("lane" in p for p in payloads)
    ledger.record("FACTS", bool(separated),
                  "%d row(s), asked true and false both present, every row names a lane" % len(payloads),
                  "the row carries escalation_requested and lane as two facts, never one")

    # LANE-AGREE: what the answer says about its lane and what the record says are one fact, not two.
    #
    # **This is the check the phase's own plan names and it did not exist.** FACTS proved that rows carry a
    # lane; it never compared one to the answer it belongs to, so a response that claimed `local` while the
    # call went to a provider would have passed every right here. The answer's `request_id` is what makes the
    # comparison possible, which is why the id had to be wired through before this could be written.
    _, escalated = ask(session.credential, {"stronger": True})
    claimed = escalated.get("lane") or ""
    answer_id = escalated.get("request_id") or ""
    row = sql("select payload, egress_allowed from audit_events where request_id = %s and event_type = 'model_call' "
              "order by id desc limit 1", (answer_id,))
    recorded = ((row[0][0] or {}).get("lane") if row else None) or ""
    egress = row[0][1] if row else None
    # Three conditions, because agreement alone is satisfiable by two matching lies: the lanes must match, the
    # lane must be named at all, and the egress verdict must be the one that lane implies. A frontier call that
    # recorded no egress would be a permitted departure with nothing saying so.
    agrees = bool(claimed) and claimed == recorded
    verdict_matches = (egress is True) if claimed == "frontier" else (egress is not True)
    ledger.record("LANE-AGREE", bool(agrees and verdict_matches),
                  "answer lane=%r, row lane=%r, egress_allowed=%r for request %s"
                  % (claimed, recorded, egress, answer_id[:12]),
                  "the lane a client is shown and the lane the record keeps are one fact")

    # TOLD: this deployment has no local model, so a refused escalation must say so rather than promise an
    # answer that cannot be produced.
    status, refused = ask(session.credential, {"stronger": True})
    told = bool(refused.get("notice")) or refused.get("kind") == "no_permitted_lane"
    ledger.record("TOLD", bool(told),
                  "notice=%r kind=%r" % (bool(refused.get("notice")), refused.get("kind")),
                  "an escalation the gate refuses is announced rather than silently ignored")

    # STREAM: a first token before the last, and a total on the client clock.
    #
    # **A frame count is not the property.** This asserted `frames > 3`, which is a statement about how the
    # model chunked its answer on the day it was written: a short reply arrives in three frames and the right
    # went red on a service that was streaming correctly. What the right is for is that the answer arrives in
    # pieces and that the first piece precedes the last, so it asserts that. `stream_probe`'s INCREMENTAL right
    # is where the count and the arrival times belong, because it measures them properly.
    frames, first_delta, done = stream(session.credential)
    streamed = frames >= 2 and first_delta is not None and (done.get("total_ms") or 0) > 0
    ledger.record("STREAM", bool(streamed),
                  "%d frame(s), first delta at %sms, total %sms"
                  % (frames, round((first_delta or 0) * 1000), done.get("total_ms")),
                  "the stream carries a first token well before its last")

    # DEGRADED: asserted from the rows a forced degradation wrote, because forcing one needs a gateway
    # restart that a container cannot perform. An earlier run recorded a real terminal failure and the
    # degraded answer it produced, and what matters is that the shape the manual promises is the shape the
    # system writes. If no degradation has ever been recorded here, the right is unproven and says so
    # rather than passing on an absence.
    rows = sql("select payload from audit_events where outcome = 'denied' "
               "and payload->>'stage' = 'degraded' order by id desc limit 1")
    payload = (rows[0][0] if rows else {}) or {}
    needed = ("reason", "attempts", "threshold", "partial_output", "lane", "data_class",
              "taxonomy_version", "remaining_ms")
    missing = [k for k in needed if k not in payload]
    if not payload:
        observed = "no degradation has been recorded on this deployment yet"
    elif missing:
        observed = "the last recorded degradation is missing " + ", ".join(missing)
    else:
        observed = "the last recorded degradation carries " + ", ".join(needed)
    ledger.record("DEGRADED", bool(payload) and not missing, observed,
                  "a forced model failure is recorded with its reason, attempts, threshold and lane")
    store.close()
    print()
    failures = ledger.failures()
    if failures:
        print("FAILED with " + str(failures) + " problem(s): the manual and the service disagree")
        return 1
    print("PASSED: " + str(len(ledger.rows)) + " right(s) held against the documented interface")
    return 0


if __name__ == "__main__":
    sys.exit(main())
