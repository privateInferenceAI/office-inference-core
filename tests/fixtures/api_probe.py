#!/usr/bin/env python3
"""The assistant API, through the interface the manual names.

Six rights, each with the wrong path it rules out beside it, as `access_probe.py` does. The distinction that
matters: **every call goes over HTTP to the running service**, because the API is the thing being tested. A
probe that imported the handler would test the functions and not the service, which is the gap this project
calls layer 3 and has paid for twice.

    | right   | the act                                        | the wrong path it rules out          |
    |---------|------------------------------------------------|--------------------------------------|
    | ANSWER  | ask a question the corpus answers              | a service that returns nothing       |
    | CITED   | read the citations on that answer              | an answer with no source behind it   |
    | SCOPED  | ask the executive question as a staff session  | a service that ignores the permit    |
    | FORGED  | send a request with a `role` field             | a role honoured because it was typed |
    | NO-SESS | call with no session token                     | an unauthenticated answer            |
    | BUDGET  | exhaust the role's team ceiling, then ask      | a budget the API reports as an answer|
    | ADMIN   | list teams with the master key                 | the lockout the hook must avoid      |

Run from the deployment root on the box:

    cd /opt/oi-core
    alias oi='docker compose -f deploy/office/compose/docker-compose.yml --env-file rendered/env'
    oi build api && oi up -d api
    docker run --rm --network ai-net --user "$(id -u):$(id -g)" \
      -v /opt/oi-core:/w -w /w --env-file rendered/env \
      --entrypoint python oi-core/session:local /w/tests/fixtures/api_probe.py

Exit codes: 0 when every right held, 1 when one did not, 2 when nothing could be tested.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path

W = Path("/w")
sys.path.insert(0, str(W / "services" / "retrieval"))
sys.path.insert(0, str(W / "services" / "ingestion"))
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


def get(path: str, timeout: int = 30):
    try:
        with urllib.request.urlopen(API + path, timeout=timeout) as r:
            return r.status, json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read() or b"{}")
        except Exception:  # noqa: BLE001
            return e.code, {}
    except Exception as e:  # noqa: BLE001
        return 0, {"error": str(e)}


def post(path: str, body: dict, timeout: int = 180):
    req = urllib.request.Request(API + path, data=json.dumps(body).encode(), method="POST",
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read() or b"{}")
        except Exception:  # noqa: BLE001
            return e.code, {}
    except Exception as e:  # noqa: BLE001
        return 0, {"error": str(e)}


def gateway(path: str, method: str = "GET"):
    """The gateway itself, for the checks that are about the hook rather than about the API."""
    env = dict(os.environ)
    master = ""
    env_file = W / "rendered" / "env"
    if env_file.exists():
        for line in env_file.read_text().splitlines():
            if line.startswith("LITELLM_MASTER_KEY="):
                master = line.split("=", 1)[1]
    req = urllib.request.Request("http://litellm:4000" + path, method=method,
                                 headers={"Authorization": "Bearer " + master})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return r.status, r.read().decode()[:200]
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode()[:200]
    except Exception as e:  # noqa: BLE001
        return 0, str(e)[:200]


def sql(statement: str, params: tuple = ()):
    """One statement against the deployment's database.

    **A direct connection rather than `docker exec`.** The first version shelled out to the postgres container,
    which cannot work from inside a container and would have failed on the first run for a reason that has
    nothing to do with what is being tested. The driver is psycopg2 because the image this runs from is the one
    that talks to Postgres through it.
    """
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

class Ledger:
    def __init__(self) -> None:
        self.rows = []

    def record(self, right: str, held: bool, observed: str, wrong_path: str) -> None:
        self.rows.append((right, held, observed, wrong_path))
        print(("ok   " if held else "FAIL ") + right.ljust(8) + " " + observed)
        print(" " * 9 + "if it were absent: " + wrong_path)

    def skipped(self, right: str, why: str) -> None:
        print("skip " + right.ljust(8) + " " + why)

    def failures(self) -> int:
        return sum(1 for _, held, _, _ in self.rows if not held)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--subject", default="probe-staff@officeinference.com")
    parser.add_argument("--exec-subject", default="probe-exec@officeinference.com")
    args = parser.parse_args()

    ledger = Ledger()
    if not database_url():
        print("FAILED with 1 problem: no database credentials, so no session can be established",
              file=sys.stderr)
        return 2

    registry = access_module.load(os.environ.get("ACCESS_REGISTRY", str(W / "rendered" / "access.json")))
    store = session_module.PostgresStore(database_url())
    role = next((n for n, r in sorted(registry.roles.items()) if not r), sorted(registry.roles)[0])

    status, health = get("/health")
    print("THE SERVICE")
    print("  " + API + "  status=" + str(status) + "  " + json.dumps(health)[:150])
    print("  registry " + os.environ.get("ACCESS_REGISTRY", str(W / "rendered" / "access.json")))
    print("  role held: " + repr(role))
    print()
    if status != 200:
        print("FAILED with 1 problem: the API is not answering, so nothing below could be tested",
              file=sys.stderr)
        return 2

    session = session_module.mint(registry, store, args.subject, role, dev=True,
                                  deployment_id="probe")

    # ------------------------------------------------------------------ ANSWER
    status, body = post("/ask", {"session": session.session_id,
                                  "question": "What is the mileage rate for reimbursement?"})
    answer = (body.get("answer") or "")
    ledger.record("ANSWER", status == 200 and bool(answer),
                  "[%s] %s" % (status, answer[:90].replace(chr(10), " ")),
                  "a question the corpus answers would return nothing")

    # ------------------------------------------------------------------ CITED
    cites = body.get("citations") or []
    model = body.get("model")
    cited = bool(cites) and all(c.get("doc_id") and c.get("page_start") is not None for c in cites)
    ledger.record("CITED", cited,
                  "%d citation(s) from the retrieved set, model=%r, unsupported=%d"
                  % (len(cites), model, len(body.get("unsupported") or [])),
                  "an answer with no source behind it is an answer nobody can check")

    # ------------------------------------------------------------------ SCOPED
    status, scoped_body = post("/ask", {"session": session.session_id,
                                          "question": "What is the Chief Executive Officer's base salary?"})
    roots = scoped_body.get("roots") or []
    scoped = status == 200 and "executive" not in roots
    ledger.record("SCOPED", scoped,
                  "[%s] roots=%s answer=%r" % (status, roots,
                                               (scoped_body.get("answer") or "")[:60].replace(chr(10), " ")),
                  "the service would answer from a root the session does not hold")

    # ------------------------------------------------------------------ FORGED
    status, forged = post("/ask", {"session": session.session_id, "role": "executive",
                                   "question": "What is the mileage rate for reimbursement?"})
    kind = forged.get("kind")
    rows = sql("select count(*) from audit_events where payload->>'session_id' = %s "
               "and outcome = 'denied'", (session.session_id,))
    count = str(rows[0][0]) if rows else "0"
    ledger.record("FORGED", status == 403 and kind == "refused_authority_claim" and count.isdigit()
                                and int(count) >= 1,
                  "[%s] kind=%s and %s denial row(s) in the chain" % (status, kind, count),
                  "a role would be honoured because a caller typed it")

    # ------------------------------------------------------------------ NO-SESSION
    status, nosess = post("/ask", {"question": "What is the mileage rate?"})
    ledger.record("NO-SESS", status == 401,
                  "[%s] kind=%s" % (status, nosess.get("kind")),
                  "an unauthenticated caller would get an answer")

    # ------------------------------------------------------------------ BUDGET
    team = sql("select team_id, spend, max_budget from \"LiteLLM_TeamTable\" where team_id = %s", (role,))
    if not team:
        ledger.skipped("BUDGET", "no team named " + repr(role) + " exists, so there is no ceiling to hit.")
        print(" " * 9 + "Run: oi --profile tools run --rm keys --provision-teams")
    else:
        print(" " * 9 + "team before: " + repr(team[0]))
        sql("update \"LiteLLM_TeamTable\" set spend = coalesce(max_budget, 1) + 1 where team_id = %s", (role,))
        status, over = post("/ask", {"session": session.session_id,
                                     "question": "What is the mileage rate for reimbursement?"})
        refused_by_budget = status == 502 and "budget" in json.dumps(over).lower()
        ledger.record("BUDGET", refused_by_budget,
                      "[%s] the API reported %r rather than an answer"
                      % (status, (over.get("error") or "")[:70]),
                      "a request over the ceiling would return an answer and be billed to nobody")
        sql("update \"LiteLLM_TeamTable\" set spend = 0 where team_id = %s", (role,))

    # ------------------------------------------------------------------ ADMIN
    status, listing = gateway("/team/list")
    ledger.record("ADMIN", status == 200, "[%s] %s" % (status, listing[:80]),
                  "enabling the identity hook would lock the deployment out of its own administration")

    store.close()
    print()
    failures = ledger.failures()
    if failures:
        print("FAILED with " + str(failures) + " problem(s)")
        return 1
    print("PASSED: " + str(len(ledger.rows)) + " right(s) held against the running service")
    return 0


if __name__ == "__main__":
    sys.exit(main())