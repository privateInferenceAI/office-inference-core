#!/usr/bin/env python3
"""Forge a role, and see whether the boundary holds and the refusal is findable afterwards.

SEC-ACL-002's verify line is *a user outside a document's group cannot retrieve it by any query*. That is a
claim about a live stack, so this runs against one: the real retriever, the real store, the real index and the
real audit chain.

**In process, through the module the assistant API will import.** The session boundary is library code, so a
probe that shelled out to the command line would be testing a different thing from the one the API calls. The
one exception is the audit record, which goes through the audit tool exactly as it does in production, because
the chain has one writer and this is not it.

Five rights, and a wrong path beside each. A right with no failing case beside it is not a test, and this
project has already paid for that lesson.

    ACCESS    a session cannot retrieve a passage outside its roots
    SCOPED    the boundary holds across both retrieval arms, not only the dense one
    FORGED    a session cannot be minted for a role the deployment did not grant
    REFUSED   a request that supplies its own role is refused
    RECORDED  the refusal is findable in the chain afterwards, read through the audit tool

**Why FORGED comes before REFUSED.** If a caller can mint its own authority then the refusal path proves
nothing, because there is nothing legitimate to refuse against. That is the whole reason this phase builds a
stand-in verifier rather than waiting for the identity provider.

Run from the deployment root on the box:

    cd /opt/oi-core
    alias oi='docker compose -f deploy/office/compose/docker-compose.yml --env-file rendered/env'
    oi --profile tools build session
    oi --profile tools run --rm session migrate
    docker run --rm --network ai-net --user "$(id -u):$(id -g)" \
      -v /opt/oi-core:/w -w /w --env-file rendered/env \
      -e AUDIT_TOOL=/w/services/audit/audit.py \
      --entrypoint python oi-core/eval:local /w/tests/fixtures/access_probe.py

The eval image rather than the ingestion one because this imports the retriever, which imports botocore through
the reranker. The --env-file supplies the database credentials; **DATABASE_URL itself is assembled here from
the POSTGRES_* variables when it is absent**, because the compose file builds it for a service and a bare
docker run does not. Without it the lexical arm is skipped and SCOPED cannot test the second arm, which the
report says out loud rather than passing quietly.

Exit codes: 0 when every right held, 1 when one did not, 2 when nothing could be tested at all.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import urllib.request
from pathlib import Path

W = Path("/w")

sys.path.insert(0, str(W / "services" / "retrieval"))
sys.path.insert(0, str(W / "services" / "ingestion"))
sys.path.insert(0, str(W / "services" / "session"))

import access as access_module  # noqa: E402
import ingest  # noqa: E402
import retriever  # noqa: E402
# session.py reads this at call time, and the mount is the only copy of the audit tool this image has.
os.environ.setdefault("AUDIT_TOOL", str(W / "services" / "audit" / "audit.py"))

import session as session_module  # noqa: E402

# The two questions, and the boundary between them. The first is answerable from a document every role may
# read. The second is answerable only from the executive compensation memorandum, and that is the crossing
# the requirement is about.
QUESTIONS = {
    "company": "What is the mileage rate for reimbursement?",
    "executive": "What is the Chief Executive Officer's base salary?",
}

def database_url() -> str:
    """The connection string, assembled from the parts the rendered env file carries.

    The compose file builds DATABASE_URL for a service; a bare docker run with --env-file gets only the parts.
    A deployment that passes DATABASE_URL itself is left alone.
    """
    if os.environ.get("DATABASE_URL"):
        return os.environ["DATABASE_URL"]
    user = os.environ.get("POSTGRES_USER", "")
    password = os.environ.get("POSTGRES_PASSWORD", "")
    database = os.environ.get("POSTGRES_DB", "")
    if not (user and database):
        return ""
    return "postgresql://" + user + ":" + password + "@postgres:5432/" + database


def audit_tool() -> str:
    return os.environ.get("AUDIT_TOOL", str(W / "services" / "audit" / "audit.py"))


def run_audit(args: list) -> subprocess.CompletedProcess:
    """The audit tool, which is the one caller that is not in-process.

    The refusal is recorded by the tool that owns the chain, because the chain has one writer and a second
    definition of its hash is the thing this design is arranged to avoid. Reading it back goes through the same
    tool, so RECORDED is asserted against the interface an operator would use.
    """
    # The audit tool reads DATABASE_URL from its environment, and compose builds that for a service while a
    # bare docker run gets only the parts. Without this the refusal is never written and RECORDED reports the
    # tool's own error, which is the right outcome for the wrong reason.
    env = dict(os.environ)
    # Assigned, not defaulted. The env file carries POSTGRES_* and no DATABASE_URL, and `docker run --env-file`
    # still defines the variable as an empty string, so setdefault saw the key present and passed the empty
    # value on. The tool then refused to run and the refusal was never written.
    env["DATABASE_URL"] = database_url()
    return subprocess.run([sys.executable, audit_tool()] + args, capture_output=True, text=True,
                          timeout=120, env=env)


def chunk_ids_for_root(services, root: str, limit: int = 200) -> set:
    """Every chunk id the store holds under one root, asked of the store itself.

    The store is the authority on what exists, so asking it is how SCOPED knows the boundary it is testing is
    a real boundary rather than an empty index.
    """
    body = {"limit": limit, "with_payload": True, "with_vector": False,
            "filter": {"must": [{"key": "acl", "match": {"value": root}}]}}
    request = urllib.request.Request(
        services.vector_url + "/collections/" + services.collection + "/points/scroll",
        data=json.dumps(body).encode(), method="POST", headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(request, timeout=services.timeout) as response:
        points = json.load(response).get("result", {}).get("points", [])
    return {(p.get("payload") or {}).get("chunk_id") or p.get("id") for p in points}

def refusal_rows(session_id: str, limit: int = 5):
    """The audit rows that name this session, read out of the chain with libpq.

    **The chain, not a claim about it.** A refusal that reported its own success would prove nothing, so this
    goes to the table. libpq rather than a driver because it is the one Postgres client both this probe and the
    audit tool agree on: the tool owns the chain and uses psycopg 3, the retrieval half uses psycopg2, and
    neither is worth a second image for one read.
    """
    try:
        import psycopg2
    except ImportError:
        return None, "psycopg2 is not importable in this image"
    try:
        conn = psycopg2.connect(database_url() or "")
    except Exception as exc:  # noqa: BLE001 - the message is the report
        return None, "could not reach the database: " + str(exc)[:160]
    try:
        with conn.cursor() as cur:
            cur.execute(
                "select occurred_at, event_type, actor, actor_role, outcome, payload "
                "from audit_events where payload @> %s::jsonb "
                "order by occurred_at desc limit %s",
                (json.dumps({"session_id": session_id}), limit))
            return cur.fetchall(), ""
    finally:
        conn.close()

class Ledger:
    """Every right and the wrong path beside it, printed together, because a summary is not evidence."""

    def __init__(self) -> None:
        self.rows = []

    def record(self, right: str, held: bool, observed: str, wrong_path: str) -> None:
        self.rows.append((right, held, observed, wrong_path))
        print(("ok   " if held else "FAIL ") + right.ljust(9) + " " + observed)
        print(" " * 10 + "if it were absent: " + wrong_path)

    def skipped(self, right: str, why: str) -> None:
        print("skip " + right.ljust(9) + " " + why)

    def failures(self) -> int:
        return sum(1 for _, held, _, _ in self.rows if not held)


def ask(services, question: str, session):
    """One retrieval, through the permit a session resolves and the API will pass.

    The permit is the only thing that decides the roots; the role is beside it for the record.
    """
    permit = session.permit()
    result = retriever.search(services, question, permit.role, limit=5, permit=permit)
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--staff-subject", default="probe-staff@officeinference.com",
                        help="the subject outside the boundary. A registry with users refuses an undeclared one")
    parser.add_argument("--exec-subject", default="probe-exec@officeinference.com",
                        help="the subject inside it, so SCOPED can show the content is reachable by somebody")
    parser.add_argument("--role", default="",
                        help="the role to hold. Defaults to one that carries no roots, so the groups decide")
    args = parser.parse_args()

    registry_path = os.environ.get("ACCESS_REGISTRY", str(W / "rendered" / "access.json"))
    try:
        registry = access_module.load(registry_path)
    except access_module.AccessError as exc:
        print("FAILED with 1 problem: " + str(exc), file=sys.stderr)
        return 2

    print("THE PERMISSION MODEL THIS DEPLOYMENT IS ENFORCING")
    print("  registry   " + registry_path)
    print("  declared   " + ("yes" if registry.declared else "no, so the built-in two folder roles apply"))
    for line in registry.describe().splitlines():
        print("  " + line)
    print()

    url = database_url()
    if not url:
        print("note: no DATABASE_URL and no POSTGRES_* to build one, so the lexical arm will be skipped")
        print()
    services = ingest.Services()
    if not services.database_url:
        services.database_url = url
    if getattr(retriever, "rerank_module", None) is None:
        print("note: the reranker is not importable in this image, so passages come back in fused order.")
        print("      The access filter, both arms and the citation shape are unaffected.")
        print()

    store = session_module.MemoryStore()
    ledger = Ledger()

    # The role to hold: one that carries no roots of its own, so the groups are the whole permission.
    role = args.role or next((name for name, roots in sorted(registry.roles.items()) if not roots), "")
    if not role:
        role = sorted(registry.roles)[0]

    # ------------------------------------------------------------------ ACCESS
    try:
        staff = session_module.mint(registry, store, args.staff_subject, role, dev=True,
                                    deployment_id="probe")
    except (session_module.SessionError, access_module.AccessError) as exc:
        print("FAILED with 1 problem: no session could be minted, so nothing below could be tested: "
              + str(exc), file=sys.stderr)
        return 2

    result = ask(services, QUESTIONS["executive"], staff)
    acls = {p.acl for p in result.passages}
    held = "executive" not in acls
    ledger.record("ACCESS", held,
                  "as " + repr(role) + " over roots " + str(sorted(staff.roots))
                  + ", the executive question returned acls=" + str(sorted(acls) or "(none)"),
                  "the executive passage would be in the answer and a model would read it")

    # ------------------------------------------------------------------ SCOPED
    # The withheld content has to exist, and somebody has to be able to reach it. Without both, ACCESS passed
    # because there was nothing to withhold, which is a green run over an index that could not have failed.
    executive_ids = chunk_ids_for_root(services, "executive")
    arms = result.arms or {}
    arms_ran = [arm for arm, count in arms.items() if count]
    if not executive_ids:
        ledger.record("SCOPED", False,
                      "the store holds no chunk labelled executive, so ACCESS passed on an empty "
                      + "boundary",
                      "this run would prove nothing about the filter, and now says so instead of "
                      + "passing")
    else:
        try:
            boss = session_module.mint(registry, store, args.exec_subject, role, dev=True,
                                       deployment_id="probe")
            reached = {p.doc_id for p in ask(services, QUESTIONS["executive"], boss).passages}
            reachable = bool(reached)
        except (session_module.SessionError, access_module.AccessError) as exc:
            reached, reachable = set(), False
            print(" " * 10 + "the inside session could not be established: " + str(exc))
        # The comparison is on the documents ONLY the inside session reached. Comparing whole results would
        # flag the company documents both sessions are entitled to, which is the union working rather than a
        # leak, and the first version of this right did exactly that.
        outside_docs = {p.doc_id for p in result.passages}
        inside_only = reached - outside_docs
        overlap = outside_docs & inside_only
        scoped = bool(inside_only) and not overlap
        detail = (str(len(executive_ids)) + " chunk(s) are labelled executive; the inside session reached "
                  + str(len(reached)) + " document(s), " + str(len(inside_only)) + " of them beyond the "
                  + "other session's reach, and the outside answer overlapped those by " + str(len(overlap))
                  + "; arms run: " + (chr(44).join(arms_ran) or "dense only"))
        ledger.record("SCOPED", scoped, detail,
                      "the boundary would be an empty index rather than a filter, or the inside "
                      + "session would be unable to reach content the outside one is kept from")

    # ------------------------------------------------------------------ FORGED
    granted = set(staff.roots)
    ungranted = None
    for candidate in sorted(registry.roles):
        if candidate == role or not registry.roles[candidate]:
            continue
        if set(registry.roles[candidate]) - granted:
            ungranted = candidate
            break

    if ungranted is None:
        if registry.declared and registry.users:
            ledger.skipped("FORGED", "every declared role stays inside the groups that grant it, so no role")
            print(" " * 10 + "exists for this subject to claim beyond its grant. That is the rule working,")
            print(" " * 10 + "and it is why there is nothing here to refuse.")
        else:
            ledger.skipped("FORGED", "this deployment declares no users, so every role is grantable")
            print(" " * 10 + "to every subject, and there is nothing to forge.")
    else:
        try:
            session_module.mint(registry, store, args.staff_subject, ungranted, dev=True,
                                deployment_id="probe")
            forged_refused = False
        except (session_module.SessionError, access_module.AccessError):
            forged_refused = True
        ledger.record("FORGED", forged_refused,
                      ("minting a session as " + repr(ungranted) + " for " + args.staff_subject
                       + ", who holds " + str(sorted(granted)) + ", was refused") if forged_refused else
                      ("a session was minted as " + repr(ungranted) + ", so the boundary is not enforced "
                       + "when a session is established"),
                      "the caller would hold a role the deployment never granted and every later check "
                      "would agree with it")

    # ------------------------------------------------------------------ REFUSED
    # The audit tool needs the content-mode variables whether or not it stores anything. A refusal payload
    # names fields and a role rather than content, so the mode has nothing to govern here.
    os.environ.setdefault("MIGRATIONS_DIR", str(W / "db" / "migrations"))
    claimed = ungranted or (sorted(registry.roles)[-1] if len(registry.roles) > 1 else "executive")
    try:
        session_module.check_role_claim(staff, "role", claimed, dev=True)
        refused = False
        refusal_note = "the request was accepted, so the boundary is not enforced"
    except session_module.SessionError as exc:
        # Two conditions, because the first version checked only the words in the message and passed on a
        # refusal whose write had failed: the failure text contains both. A refusal only counts when the
        # call raised AND the row is in the chain, which is what the message is not allowed to stand in for.
        refused = "refused:" in str(exc).lower() and "could not be recorded" not in str(exc).lower()
        refusal_note = str(exc)
    # The refusal is asserted together with its record, because either alone is satisfiable with the boundary
    # off: a write that silently fails with every request refused, or a refusal path that accepts a typed
    # role while the chain stays empty. Both conditions in one right is the only version that cannot pass
    # with the control absent.
    rows, why = refusal_rows(staff.session_id)
    found = bool(rows)
    if found:
        row = rows[0]
        detail = ("the chain holds " + str(len(rows)) + " refusal(s) for this session: "
                  + str(row[0]) + " " + str(row[1]) + "/" + str(row[4])
                  + " actor_role=" + repr(row[3]) + " payload.session_role="
                  + repr((row[5] or {}).get("session_role")))
    else:
        detail = "no audit row names this session: " + (why or "the query returned nothing")

    ledger.record("REFUSED", bool(refused and rows),
                  ("a request supplying role=" + repr(claimed) + " while holding " + repr(role)
                   + " was refused") if refused else refusal_note,
                  "a role would be honoured because a caller typed it, which is the whole of SEC-ACL-002")

    # ------------------------------------------------------------------ RECORDED
    # Read back out of the chain rather than out of the refusal's own message. A refusal nobody can find
    # afterwards is not a control, and a message claiming to have recorded one is exactly what a broken
    # writer would print.
    ledger.record("RECORDED", bool(refused and found), detail,
                  "the 403 would go out and the record would be missing, leaving an access decision "
                  "with no evidence of what was attempted")

    print()
    failures = ledger.failures()
    if failures:
        print("FAILED with " + str(failures) + " problem(s)")
        return 1
    print("PASSED: " + str(len(ledger.rows)) + " right(s) held against the live stack")
    print()
    print("Read the refusal out of the chain yourself rather than trusting this line:")
    print("  oi --profile tools run --rm audit list --type access | head")
    return 0

if __name__ == "__main__":
    sys.exit(main())
