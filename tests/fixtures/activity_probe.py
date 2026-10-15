"""The activity interface, asserted against the running service.

**Why this probe exists.** `tests/test_activity.py` proves the scope rule with a fake session and a mapping of
grants. It cannot prove the wiring: that the endpoint resolves a session, applies the scope inside the SQL,
returns the rows a client renders, and refuses a limit it will not honour. Those are the parts C3's two
defects lived in.

    right       what the manual says                                       the wrong path it rules out
    ----------  --------------------------------------------------------   --------------------------------
    READ        rows come back newest first, with the fields a screen uses   a query nobody exercised
    SCOPED      a session cannot read a subject outside its own roots        rows leaving the store
    ACTOR       asking for an actor outside the scope returns nothing        "Dana did nothing" from a leak
    FILTERED    type and outcome filter, and a bad value is refused          a filter that silently no-ops
    BYREQUEST   a row is findable by the request_id its answer carried       an answer with no traceable row
    BYJOB       every row a job wrote is returned by asking for that job     work that cannot be found again
    CAPPED      a limit above the cap is refused rather than trimmed         a caller lied to about its read
    RECORDED    reading the record is itself written to the record           a read nobody can account for

**BYREQUEST and BYJOB create their own row first.** An earlier probe that searched the chain for something it
hoped was there would pass or fail depending on unrelated history, which is the failure mode this project has
paid for more than once. Each of those rights makes the thing it then looks for.

Run from the deployment root on the box:

    cd /opt/oi-core
    docker run --rm --network ai-net --user "$(id -u):$(id -g)" \
      -v /opt/oi-core:/w -w /w --env-file rendered/env \
      --entrypoint python oi-core/session:local /w/tests/fixtures/activity_probe.py

Exit codes: 0 when every right held, 1 when one did not, 2 when nothing could be tested.
"""
from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

W = Path("/w")
sys.path.insert(0, str(W / "services" / "retrieval"))
sys.path.insert(0, str(W / "services" / "session"))

import access as access_module  # noqa: E402
import session as session_module  # noqa: E402

API = os.environ.get("OI_API_URL", "http://api:8088")


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


def call(path: str, *, body: dict | None = None, method: str = "GET", session_id: str = ""):
    data = json.dumps(body).encode() if body is not None else None
    headers = {"Content-Type": "application/json"}
    if session_id:
        headers["X-OI-Session"] = session_id
    request = urllib.request.Request(API + path, data=data, method=method, headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=180) as response:
            return response.status, json.loads(response.read())
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode(errors="replace")
        try:
            return exc.code, json.loads(raw)
        except json.JSONDecodeError:
            return exc.code, {"error": raw[:200]}


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

    # **The sessions are minted on the deployment the API actually is, and the first version was not.**
    # It used the label "activity-probe", which is the audit label a session carries; the read filters rows by
    # the deployment the API serves, so every row was excluded and READ returned nothing. That looked exactly
    # like an empty chain, which is the worst way for a probe to be wrong: it reported a fact about the record
    # that was really a fact about the probe.
    #
    # The id comes from the service rather than from a constant here, so the probe works on any deployment.
    status_health, health = call("/health")
    deployment_id = (health or {}).get("deployment_id") or "unknown"
    if deployment_id == "unknown":
        print(f"FAILED: could not read the deployment id from /health ([{status_health}])", file=sys.stderr)
        return 2
    print(f"deployment: {deployment_id}")

    # The subjects come from the deployment's registry. The first version of the job probe invented one and the
    # registry refused it, which is the control working: a subject is refused rather than defaulted.
    users = sorted(getattr(registry, "users", {}) or {})
    if not users:
        print("FAILED: this deployment declares no subjects, so no session can be minted", file=sys.stderr)
        return 2

    def mint_for(subject: str):
        last = None
        for candidate in sorted(registry.roles or {"viewer": None}):
            try:
                return session_module.mint(registry, store, subject, candidate, dev=True,
                                           deployment_id=deployment_id)
            except access_module.AccessError as exc:
                last = exc
        print(f"FAILED: no role admits {subject}: {last}", file=sys.stderr)
        raise SystemExit(2)

    session = mint_for(users[0])
    other = mint_for(users[-1]) if len(users) > 1 else session

    # ---- READ
    status, page = call("/activity?limit=10", session_id=session.credential)
    events = page.get("events") or []
    if status != 200:
        ledger.record("READ", False, f"[{status}] {str(page)[:120]}",
                      "the chain is readable over the API")
        print("\nFAILED: /activity did not answer, so nothing else could be tested")
        return 1
    ids = [e["id"] for e in events]
    descending = ids == sorted(ids, reverse=True)
    fields = {"id", "at", "type", "actor", "outcome", "payload"}
    complete = all(fields <= set(e) for e in events) if events else False
    ledger.record("READ", descending and (complete or not events),
                  f"{len(events)} row(s), ids {ids[:1]}..{ids[-1:]}, newest first={descending}, "
                  f"every row carries {sorted(fields)}",
                  "rows come back newest first with the fields a screen renders")

    scope_actors = (page.get("scope") or {}).get("actors") or []

    # ---- SCOPED
    #
    # The reader may see only the actors whose roots its own contain. This is asserted against the service's
    # own reported scope rather than against a guess about the registry, so it says something true on any
    # deployment: every actor named in the scope must be one the reader is allowed, and an actor with a wider
    # grant must not appear.
    mine = set(session.roots)
    seen_rows_ok = all(
        # A row's actor must be either the reader or one the scope named.
        e["actor"] == session.subject or e["actor"] in scope_actors for e in events)
    if len(users) > 1 and other.roots != mine:
        # The two declared subjects hold different grants, so the narrowing is testable directly.
        wider = other if other.roots > mine else session
        narrower = session if wider is other else other
        status_forbidden, forbidden = call(
            "/activity?actor=" + urllib.parse.quote(wider.subject), session_id=narrower.credential)
        refused = status_forbidden == 200 and (forbidden.get("events") or []) == [] \
            and (forbidden.get("scope") or {}).get("restricted") is True
        ledger.record("SCOPED", refused and seen_rows_ok,
                      f"a {sorted(narrower.roots)} session asking for {wider.subject} "
                      f"({sorted(wider.roots)}) -> {status_forbidden}, "
                      f"{len(forbidden.get('events') or [])} row(s), restricted="
                      f"{(forbidden.get('scope') or {}).get('restricted')}",
                      "a session cannot read a subject holding a root it does not hold")
    else:
        # Every declared subject holds the same grant on this deployment, so there is no narrower reader to
        # test with. Reporting green here would be reporting a check that did not run.
        ledger.skip("SCOPED", f"this deployment's subjects all hold {sorted(mine)}, so no narrowing exists "
                              f"to test")

    # ---- ACTOR: asking for somebody outside the scope returns nothing, not the rest of the page
    stranger = "nobody@officeinference.com"
    status_stranger, stranger_page = call("/activity?actor=" + urllib.parse.quote(stranger),
                                          session_id=session.credential)
    empty = status_stranger == 200 and (stranger_page.get("events") or []) == []
    ledger.record("ACTOR", empty,
                  f"actor={stranger} -> {status_stranger}, {len(stranger_page.get('events') or [])} row(s)",
                  "an actor outside the scope returns nothing rather than a partial page")

    # ---- FILTERED
    status_type, typed = call("/activity?type=model_call&limit=5", session_id=session.credential)
    only_calls = all(e["type"] == "model_call" for e in (typed.get("events") or []))
    status_bad, bad = call("/activity?type=not_a_type", session_id=session.credential)
    ledger.record("FILTERED", status_type == 200 and only_calls and status_bad == 400,
                  f"type=model_call -> {len(typed.get('events') or [])} row(s), all model calls="
                  f"{only_calls}; type=not_a_type -> {status_bad} {bad.get('kind')}",
                  "a filter filters, and a value this deployment does not use is refused")

    # ---- BYREQUEST: make an answer, then find its row by the request id that answer carried
    status_ask, answer = call("/ask", method="POST",
                              body={"session": session.credential,
                                    "question": "What is the mileage rate for reimbursement?"},
                              session_id=session.credential)
    request_id = answer.get("request_id") if isinstance(answer, dict) else None
    if status_ask == 200 and request_id:
        status_req, by_request = call("/activity?request=" + urllib.parse.quote(request_id),
                                      session_id=session.credential)
        found = [e for e in (by_request.get("events") or []) if e.get("request_id") == request_id]
        ledger.record("BYREQUEST", len(found) >= 1,
                      f"request_id {request_id[:8]}… -> {len(found)} row(s)",
                      "a row is findable by the request id its answer carried")
    else:
        ledger.record("BYREQUEST", False,
                      f"could not produce an answer to look up: [{status_ask}] {str(answer)[:100]}",
                      "an answer that carries a request id, so its row can be found")

    # ---- BYJOB: run a job, then read back everything it wrote
    status_tasks, offered = call("/tasks", session_id=session.credential)
    tasks = [t["id"] for t in (offered.get("tasks") or [])]
    if not tasks:
        ledger.skip("BYJOB", "this deployment declares no tasks, so there is no job to look up")
    else:
        status_job, job = call("/jobs", method="POST",
                               body={"session": session.credential, "task": tasks[0],
                                     "input": "What does the fee schedule say about hourly rates?"},
                               session_id=session.credential)
        job_id = job.get("job_id") if isinstance(job, dict) else None
        if status_job == 200 and job_id:
            status_byjob, by_job = call("/activity?job=" + urllib.parse.quote(job_id),
                                        session_id=session.credential)
            rows = by_job.get("events") or []
            named = [e for e in rows if (e.get("payload") or {}).get("job_id") == job_id]
            # A job that succeeded writes at least a start, an attempt and a finish.
            ledger.record("BYJOB", len(named) >= 2,
                          f"job {job_id[:8]}… -> {len(named)} row(s) naming it",
                          "every row a job wrote is returned by asking for that job")
        else:
            ledger.record("BYJOB", False, f"could not start a job: [{status_job}] {str(job)[:100]}",
                          "a job whose rows can be read back")

    # ---- CAPPED
    status_ok, _ = call(f"/activity?limit={500}", session_id=session.credential)
    status_over, over = call("/activity?limit=5000", session_id=session.credential)
    ledger.record("CAPPED", status_ok == 200 and status_over == 400,
                  f"limit=500 -> {status_ok}, limit=5000 -> {status_over} {over.get('error', '')[:60]}",
                  "a limit above the cap is refused rather than silently trimmed")

    # ---- RECORDED: reading the record is itself in the record
    status_reads, reads = call("/activity?type=access&limit=20", session_id=session.credential)
    read_rows = [e for e in (reads.get("events") or [])
                 if (e.get("payload") or {}).get("stage") == "activity_read"]
    ledger.record("RECORDED", len(read_rows) >= 1,
                  f"{len(read_rows)} activity read(s) recorded in the chain",
                  "reading the record leaves a row, so a read nobody can account for is not possible")

    held = len(ledger.rows) - ledger.failures()
    if ledger.failures():
        print(f"\nFAILED with {ledger.failures()} problem(s): the manual and the service disagree")
    else:
        skipped = f", {len(ledger.skipped)} skipped" if ledger.skipped else ""
        print(f"\nPASSED: {held} right(s) held against the documented activity interface{skipped}")
    return 1 if ledger.failures() else 0


if __name__ == "__main__":
    raise SystemExit(main())
