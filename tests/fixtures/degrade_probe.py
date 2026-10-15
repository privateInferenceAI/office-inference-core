#!/usr/bin/env python3
"""Degradation, through the interface the manual names.

Three rights that can be asserted against the deployment as it stands. The fault-dependent ones, a retry that
must NOT happen after the first token and a stall that must trip the stall threshold, need the gateway pointed at
the fault stub. That is a documented procedure in the manual rather than something a probe should do to a running
deployment.

    right     the act                                    the wrong path it rules out
    --------  -----------------------------------------  --------------------------------------
    INTACT    ask a normal question                      a budget that degrades healthy requests
    DEGRADE   force a terminal failure, then ask          a hang, or an error with no answer
    RECORDED  read the row the degraded answer wrote      a degradation nobody can account for

**The failure is forced by putting the role spend over its ceiling**, which makes the gateway refuse with a
terminal error. That exercises the same classification, degraded payload and audit fields as a throttle. It is not
evidence of a real throttle, and the runbook says which is which: a real throttle needs load against a quota
small enough to reach, and the method is recorded in the design note at 7g.

**The gateway caches the team row**, so a spend written straight into the database is not what it enforces until
it restarts. That is a measurement from the audit work and this probe depends on it.

Run from the deployment root on the box:

    cd /opt/oi-core
    alias oi='docker compose -f deploy/office/compose/docker-compose.yml --env-file rendered/env'
    docker run --rm --network ai-net --user "$(id -u):$(id -g)" \
      -v /opt/oi-core:/w -w /w --env-file rendered/env \
      --entrypoint python oi-core/session:local /w/tests/fixtures/degrade_probe.py

Exit codes: 0 when every right held, 1 when one did not, 2 when nothing could be tested.
"""
from __future__ import annotations

import argparse
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


def ask(session_id: str, question: str, timeout: int = 180):
    body = json.dumps({"session": session_id, "question": question}).encode()
    request = urllib.request.Request(API + "/ask", data=body, method="POST",
                                     headers={"Content-Type": "application/json"})
    started = time.monotonic()
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.status, json.loads(response.read()), time.monotonic() - started
    except urllib.error.HTTPError as exc:
        return exc.code, {"error": exc.read().decode(errors="replace")[:200]}, time.monotonic() - started
    except Exception as exc:  # noqa: BLE001
        return 0, {"error": str(exc)}, time.monotonic() - started


def compose(*args: str, timeout: int = 120):
    """The documented compose command, when this is running where docker exists.

    **It does not exist inside a container**, and this probe runs in one. The first version called it
    unconditionally and died with FileNotFoundError, which is the third time this project has made that
    mistake in a probe. So it returns None instead of raising, and the caller turns that into the
    operator instruction it actually is.
    """
    command = ["docker", "compose", "-f", "deploy/office/compose/docker-compose.yml",
               "--env-file", "rendered/env", *args]
    try:
        return subprocess.run(command, capture_output=True, text=True, cwd=str(W), timeout=timeout)
    except FileNotFoundError:
        return None


class Ledger:
    def __init__(self) -> None:
        self.rows = []

    def record(self, right: str, held: bool, observed: str, wrong_path: str) -> None:
        self.rows.append((right, held, observed, wrong_path))
        print(("ok   " if held else "FAIL ") + right.ljust(9) + " " + observed)
        print(" " * 10 + "if it were absent: " + wrong_path)

    def failures(self) -> int:
        return sum(1 for _, held, _, _ in self.rows if not held)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--subject", default="probe-staff@officeinference.com")
    parser.add_argument("--model-role", default=os.environ.get("OI_DEFAULT_MODEL_ROLE", "analyst"))
    args = parser.parse_args()

    ledger = Ledger()
    if not database_url():
        print("FAILED with 1 problem: no database credentials", file=sys.stderr)
        return 2

    registry = access_module.load(os.environ.get("ACCESS_REGISTRY", str(W / "rendered" / "access.json")))
    store = session_module.PostgresStore(database_url())
    role = next((n for n, r in sorted(registry.roles.items()) if not r), sorted(registry.roles)[0])
    session = session_module.mint(registry, store, args.subject, role, dev=True, deployment_id="probe")

    question = "What is the mileage rate for reimbursement?"

    # ------------------------------------------------------------------ INTACT first
    # Ordered before the forced failure so that a broken budget is caught before anything is provoked.
    status, body, elapsed = ask(session.credential, question)
    intact = status == 200 and body.get("kind") != "degraded" and body.get("citations")
    ledger.record("INTACT", bool(intact),
                  "[%s] %.2fs, kind=%r, %d citation(s)"
                  % (status, elapsed, body.get("kind") or "answer", len(body.get("citations") or [])),
                  "a budget that degrades healthy requests is an outage with extra steps")

    # ------------------------------------------------------------------ DEGRADE
    rows = sql('select max_budget from "LiteLLM_TeamTable" where team_id = %s', (args.model_role,))
    if not rows:
        ledger.record("DEGRADE", False, "no team named %r, so no ceiling could be forced" % args.model_role,
                      "the failure has to be forced to be observed")
    else:
        ceiling = float(rows[0][0] or 1)
        sql('update "LiteLLM_TeamTable" set spend = %s where team_id = %s', (ceiling + 1, args.model_role))
        try:
            restart = compose("restart", "litellm")
            if restart is None:
                ledger.record("DEGRADE", False,
                              "this probe cannot restart the gateway from inside a container, so the ceiling "
                              "it wrote is not what the gateway is enforcing",
                              "the gateway caches the team row, so a spend written here is inert until it "
                              "restarts. Run this from the host, or restart litellm and run it again")
                store.close()
                return 1
            if restart.returncode != 0:
                print(" " * 10 + "the gateway would not restart: " + restart.stderr.strip()[:120])
            time.sleep(int(os.environ.get("OI_GATEWAY_RESTART_WAIT", "16")))
            status, body, elapsed = ask(session.credential, question)
        finally:
            sql('update "LiteLLM_TeamTable" set spend = 0 where team_id = %s', (args.model_role,))
            compose("restart", "litellm")
            time.sleep(int(os.environ.get("OI_GATEWAY_RESTART_WAIT", "16")))

        degraded = status == 200 and body.get("kind") == "degraded"
        passages = body.get("passages") or []
        ledger.record("DEGRADE", bool(degraded and passages),
                      "[%s] %.2fs, kind=%r reason=%r attempts=%s threshold=%r %d passage(s)"
                      % (status, elapsed, body.get("kind"), body.get("reason"), body.get("attempts"),
                         body.get("threshold"), len(passages)),
                      "a hang, or an error with no answer, is the failure this right exists for")

        # ------------------------------------------------------------------ RECORDED
        rows = sql("select payload from audit_events where outcome = 'denied' "
                   "and payload->>'stage' = 'degraded' order by id desc limit 1")
        payload = (rows[0][0] if rows else {}) or {}
        needed = ("reason", "attempts", "threshold", "partial_output", "lane", "data_class",
                  "taxonomy_version", "remaining_ms")
        missing = [k for k in needed if k not in payload]
        ledger.record("RECORDED", not missing,
                      "the row carries " + (", ".join(needed) if not missing
                                            else "everything except " + ", ".join(missing)),
                      "a degradation nobody can account for is not a control")

    store.close()
    print()
    failures = ledger.failures()
    if failures:
        print("FAILED with " + str(failures) + " problem(s)")
        return 1
    print("PASSED: " + str(len(ledger.rows)) + " right(s) held against the degraded path")
    return 0


if __name__ == "__main__":
    sys.exit(main())
