"""The approval gate over HTTP, against the running deployment.

**Why this is a probe rather than more unit tests.** C5's panel found that the approval route had never run: a
wildcard route declared first captured `/jobs/{id}/approvals`, so every request hit the wrong handler and the
refusal looked like an authentication problem. A schema check cannot see declaration order. Only a request with
a valid session can tell the two apart. This probe sends those requests.

    right        the promise                                                the wrong path it rules out
    -----------  -------------------------------------------------------    ---------------------------------
    GATED        the invoice job stops at the gate holding a digest         a job that runs past the gate
    LISTED       the gate appears in the approval queue                     a job waiting where nobody looks
    APPROVED     the approval resumes the job, post_entry runs once,        an approval that records but does not act
                 and the effect is in the ledger once                       an action applied twice
    DENIED       a denial stops the job and nothing after the gate runs     a denial that only changes a label
    REFUSED      no session and a forged role are refused and recorded      a control with no evidence it fired
    HONEST       the record says the identity is unverified, and the feed   a sentence that invents a person
                 does not name one
    CHAIN        the chain still verifies after all of it                   rows written outside the chain

Run from the deployment root on the box:

    cd /opt/oi-core
    bash scripts/probe.sh approval_probe.py

Exit codes: 0 when every right held, 1 when one did not, 2 when nothing could be tested.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
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
AUDIT_TOOL = os.environ.get("AUDIT_TOOL") or str(W / "services" / "audit" / "audit.py")
INVOICE = "INV-2026-0417 from accounts@ferguson.example total 4210.00 due 2026-11-15"


def database_url() -> str:
    url = (os.environ.get("DATABASE_URL") or "").strip()
    if url:
        return url
    user = os.environ.get("POSTGRES_USER", "")
    password = os.environ.get("POSTGRES_PASSWORD", "")
    database = os.environ.get("POSTGRES_DB", "")
    if not (user and database):
        return ""
    return f"postgresql://{user}:{password}@postgres:5432/{database}"


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


def http(path: str, *, token: str = "", body: dict | None = None, method: str = "GET"):
    data = None if body is None else json.dumps(body).encode()
    headers = {"Content-Type": "application/json"}
    if token:
        headers["X-OI-Session"] = token
    request = urllib.request.Request(API + path, data=data, method=method, headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=90) as response:
            return response.status, json.loads(response.read() or b"{}")
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode(errors="replace")
        try:
            return exc.code, json.loads(raw)
        except json.JSONDecodeError:
            return exc.code, {"error": raw[:200]}


class Ledger:
    def __init__(self):
        self.rows = []
        self.failures = 0

    def record(self, right: str, held: bool, observed: str, wrong_path: str) -> None:
        self.rows.append((right, held))
        print(("ok   " if held else "FAIL ") + right.ljust(9) + " " + observed, flush=True)
        if not held:
            self.failures += 1
            print(" " * 11 + "promised: " + wrong_path, flush=True)


def start_job(token: str, text: str = INVOICE) -> dict:
    status, body = http("/jobs", token=token, method="POST",
                        body={"session": token, "task": "invoice_intake", "input": text})
    if status != 200:
        raise RuntimeError(f"POST /jobs returned {status}: {body}")
    return body


def wait_for(job_id: str, token: str, states: tuple, timeout: float = 60.0) -> dict:
    deadline = time.monotonic() + timeout
    last = {}
    while time.monotonic() < deadline:
        status, last = http(f"/jobs/{job_id}", token=token)
        if status != 200:
            raise RuntimeError(f"GET /jobs/{job_id} returned {status}: {last}")
        if last.get("state") in states:
            return last
        time.sleep(0.25)
    raise RuntimeError(f"job {job_id} did not reach {states}; last was {last}")


def decide(job_id: str, token: str, decision: str, reason: str = "", digest: str = ""):
    return http(f"/jobs/{job_id}/approvals", token=token, method="POST",
                body={"session": token, "decision": decision, "reason": reason,
                      "action_digest": digest})


def main() -> int:
    ledger = Ledger()
    if not database_url():
        print("FAILED with 1 problem: no database credentials", file=sys.stderr)
        return 2

    registry = access_module.load(os.environ.get("ACCESS_REGISTRY", str(W / "rendered" / "access.json")))
    store = session_module.PostgresStore(database_url())
    role = next((n for n, r in sorted(registry.roles.items()) if not r), sorted(registry.roles)[0])
    session = session_module.mint(registry, store, "probe-staff@officeinference.com", role, dev=True,
                                  deployment_id="approval-probe")
    token = session.credential

    # GATED: the deterministic invoice plan reaches the gate holding a digest, and nothing after it has run.
    job = start_job(token)
    gate = wait_for(job["job_id"], token, ("awaiting_approval", "failed", "succeeded", "cancelled"))
    steps = sql("select name, state from job_steps where job_id = %s order by attempt", (job["job_id"],))
    after = [row for row in steps if row[0] in ("post_entry", "close_out")]
    held = (gate.get("state") == "awaiting_approval" and bool(gate.get("pending_action_digest"))
            and not after)
    ledger.record("GATED", held,
                  f"state={gate.get('state')!r}, digest={(gate.get('pending_action_digest') or '')[:18]}…, "
                  f"{len(after)} step(s) after the gate",
                  "the invoice job stops at the gate and no step after it has a row")

    # LISTED: the job is in the approval queue the Review screen reads.
    status, queue = http("/approvals", token=token)
    listed = any(item.get("job_id") == job["job_id"] for item in (queue.get("approvals") or []))
    ledger.record("LISTED", status == 200 and listed, f"[{status}] {len(queue.get('approvals') or [])} waiting",
                  "the waiting job appears in the approval queue")

    # APPROVED: the decision records, resumes the plan, posts the entry once and moves the effect ledger once.
    status, decided = decide(job["job_id"], token, "approve", digest=gate.get("pending_action_digest", ""))
    final = wait_for(job["job_id"], token, ("succeeded", "failed", "cancelled")) if status == 200 else decided
    post = sql("select count(*) from job_steps where job_id = %s and name = 'post_entry' and state = 'succeeded'",
               (job["job_id"],))[0][0]
    effects = sql("""select count(*) filter (where mode = 'transactional'),
                                    count(*) filter (where mode = 'external')
                             from workflow_effects where job_id = %s and step_name = 'post_entry'""",
                  (job["job_id"],))[0]
    ledger.record("APPROVED", status == 200 and final.get("state") == "succeeded" and post == 1
                  and effects == (1, 1),
                  f"[{status}] state={final.get('state')!r}, post_entry rows={post}, "
                  f"effect ledger rows=transactional {effects[0]} / external {effects[1]}",
                  "an approval resumes the job, runs the post-gate step once and records each effect once")

    # DENIED: a second job stops on denial and never reaches the post gate steps.
    denied_job = start_job(token)
    denied_gate = wait_for(denied_job["job_id"], token, ("awaiting_approval", "failed", "succeeded", "cancelled"))
    status, denied = decide(denied_job["job_id"], token, "deny", reason="the vendor is wrong",
                            digest=denied_gate.get("pending_action_digest", ""))
    denied_final = wait_for(denied_job["job_id"], token, ("failed", "succeeded", "cancelled")) if status == 200 else denied
    denied_post = sql("select count(*) from job_steps where job_id = %s and name in ('post_entry','close_out')",
                      (denied_job["job_id"],))[0][0]
    ledger.record("DENIED", status == 200 and denied_final.get("state") == "failed" and denied_post == 0,
                  f"[{status}] state={denied_final.get('state')!r}, post-gate rows={denied_post}",
                  "a denial stops the job and nothing after the gate runs")

    # REFUSED: no session and a forged role are refused, and both attempts are in the chain.
    third = start_job(token)
    third_gate = wait_for(third["job_id"], token, ("awaiting_approval", "failed", "succeeded", "cancelled"))
    no_session_status, _ = http(f"/jobs/{third['job_id']}/approvals", method="POST",
                                body={"decision": "approve",
                                      "action_digest": third_gate.get("pending_action_digest", "")})
    forged_status, forged = http(f"/jobs/{third['job_id']}/approvals", token=token, method="POST",
                                 body={"session": token, "role": "executive", "decision": "approve",
                                       "action_digest": third_gate.get("pending_action_digest", "")})
    refusal_rows = sql("""select count(*) from audit_events
                            where payload->>'stage' = 'approval_refused'""")[0][0]
    authority_rows = sql("""select count(*) from audit_events
                              where payload->>'supplied_field' = 'role'
                                and position('/approvals' in coalesce(payload->>'endpoint', '')) > 0""")[0][0]
    ledger.record("REFUSED", no_session_status == 401 and forged_status == 403 and refusal_rows >= 1
                  and authority_rows >= 1,
                  f"no-session={no_session_status}, forged-role={forged_status}, "
                  f"refusal rows={refusal_rows}, authority rows={authority_rows}",
                  "a request with no session and a request supplying its own role are both refused and recorded")

    # HONEST: the approval row carries an unverified identity, and the feed sentence names no person.
    approval = sql("""select id, payload from audit_events
                        where event_type = 'approval' and payload->>'job_id' = %s
                        order by id desc limit 1""", (job["job_id"],))
    identity = (approval[0][1]["approval"]["identity"] if approval else {})
    summary = sql("select summary from audit_feed where id = %s", (approval[0][0],)) if approval else []
    summary = summary[0][0] if summary else ""
    honest = (identity.get("assurance") == "unverified" and identity.get("basis") == "session_token"
              and "probe-staff" not in summary and "not identified" in summary)
    ledger.record("HONEST", honest,
                  f"assurance={identity.get('assurance')!r}, basis={identity.get('basis')!r}, "
                  f"summary={summary[:70]!r}",
                  "the record says the identity is unverified and the feed sentence does not invent a person")

    # CHAIN: the export verifies after the HTTP decisions wrote their rows.
    out = Path(tempfile.mkdtemp(prefix="approval-probe-export-"))
    exported = subprocess.run([sys.executable, AUDIT_TOOL, "export", "--out", str(out)],
                              capture_output=True, text=True, env=dict(os.environ))
    jsonl = out / "audit-events.jsonl"
    if exported.returncode != 0 or not jsonl.exists():
        ledger.record("CHAIN", False, f"export failed: {(exported.stderr or '')[-180:]}",
                      "the chain exports and verifies after the approval decisions")
    else:
        verified = subprocess.run([sys.executable, AUDIT_TOOL, "verify", str(jsonl)],
                                  capture_output=True, text=True, env=dict(os.environ))
        first = (verified.stdout or "").strip().splitlines()
        ledger.record("CHAIN", verified.returncode == 0,
                      (first[0] if first else (verified.stderr or "")[-140:]),
                      "the chain verifies after a full run, a denied run and two refused attempts")

    held = len(ledger.rows) - ledger.failures
    if ledger.failures:
        print(f"\nFAILED with {ledger.failures} problem(s): the runbook and the approval route disagree")
        return 1
    print(f"\nPASSED: {held} right(s) held against the documented approval route")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
