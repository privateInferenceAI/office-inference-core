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
import os
import subprocess
import sys
from pathlib import Path

W = Path("/w")

sys.path.insert(0, str(W / "services" / "retrieval"))
sys.path.insert(0, str(W / "services" / "ingestion"))
sys.path.insert(0, str(W / "services" / "session"))

import access as access_module  # noqa: E402
import ingest  # noqa: E402
import retriever  # noqa: E402
import session as session_module  # noqa: E402

# The two questions, and the boundary each one crosses. They are the two the corpus already answers: the
# finance document carries a fee schedule, and the company document carries the mileage rate.
QUESTIONS = {
    "finance": "What is the annual account fee on the fee schedule?",
    "company": "What is the mileage rate for reimbursement?",
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
    return subprocess.run([sys.executable, audit_tool()] + args, capture_output=True, text=True, timeout=120)


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
    return {p.acl for p in result.passages}, result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--subject", default="probe-staff@officeinference.com",
                        help="a subject the manifest declares. A registry with users refuses an undeclared one")
    parser.add_argument("--role", default=None, help="the role to hold. Defaults to the registry's first")
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
    store = session_module.MemoryStore()
    ledger = Ledger()

    # ------------------------------------------------------------------ ACCESS
    role = args.role or sorted(registry.roles)[0]
    try:
        session = session_module.mint(registry, store, args.subject, role, dev=True, deployment_id="probe")
    except (session_module.SessionError, access_module.AccessError) as exc:
        print("FAILED with 1 problem: no session could be minted, so nothing below could be tested: "
              + str(exc), file=sys.stderr)
        return 2

    acls, _ = ask(services, QUESTIONS["finance"], session)
    held = "finance" not in acls
    ledger.record("ACCESS", held,
                  "as " + repr(role) + " over roots " + str(sorted(session.roots))
                  + ", the finance question returned acls=" + str(sorted(acls) or "(none)"),
                  "the finance passage would be in the answer and a model would read it")

    # ------------------------------------------------------------------ SCOPED
    _, result = ask(services, QUESTIONS["finance"], session)
    arms = result.arms or {}
    arms_ran = [arm for arm, count in arms.items() if count]
    if not arms.get("lexical"):
        ledger.skipped("SCOPED", "the lexical arm did not run, so only the dense arm was tested. Pass a")
        print(" " * 10 + "DATABASE_URL, or the POSTGRES_* variables to build one, to test both arms.")
    else:
        scoped = result.restricted_matched > 0 and "finance" not in {p.acl for p in result.passages}
        ledger.record("SCOPED", scoped,
                      "both arms ran (" + ", ".join(arms_ran) + "), the boundary held in each, and "
                      + str(result.restricted_matched) + " passage(s) matched outside it and were withheld",
                      "one arm would return what the other filtered, which is how a leak hides in a hybrid "
                      "search")

    # ------------------------------------------------------------------ FORGED
    subject_groups = registry.users[args.subject].groups if args.subject in registry.users else ()
    granted = set(session.roots)
    for group in subject_groups:
        granted |= set(registry.groups.get(group, ()))
    ungranted = None
    for candidate in sorted(registry.roles):
        if candidate == role or not registry.roles[candidate]:
            continue
        if set(registry.roles[candidate]) - granted:
            ungranted = candidate
            break

    if ungranted is None:
        ledger.skipped("FORGED", "no role in this registry reaches a root this subject does not already hold,")
        print(" " * 10 + "so there is nothing to forge. A deployment with declared users and groups has one;")
        print(" " * 10 + "the built-in two-role model does not.")
    else:
        try:
            session_module.mint(registry, store, args.subject, ungranted, dev=True, deployment_id="probe")
            forged_refused = False
        except (session_module.SessionError, access_module.AccessError):
            forged_refused = True
        ledger.record("FORGED", forged_refused,
                      ("minting a session as " + repr(ungranted) + " for " + args.subject + ", who holds "
                       + str(sorted(granted)) + ", was refused") if forged_refused else
                      ("a session was minted as " + repr(ungranted) + ", so the boundary is not enforced when "
                       "a session is established"),
                      "the caller would hold a role the deployment never granted and every later check would "
                      "agree with it")

    # ------------------------------------------------------------------ REFUSED
    # The audit tool needs the content-mode variables whether or not it stores anything. A refusal payload names
    # fields and a role rather than content, so the mode has nothing to govern here.
    os.environ.setdefault("MIGRATIONS_DIR", str(W / "db" / "migrations"))
    claimed = ungranted or "executive"
    try:
        session_module.check_role_claim(session, "role", claimed, dev=True)
        refused = False
        refusal_note = "the request was accepted, so the boundary is not enforced"
    except session_module.SessionError as exc:
        refused = "refused" in str(exc).lower() and "recorded" in str(exc).lower()
        refusal_note = str(exc)
    ledger.record("REFUSED", refused,
                  "a request supplying role=" + repr(claimed) + " while holding " + repr(role)
                  + " was refused" if refused else refusal_note,
                  "a role would be honoured because a caller typed it, which is the whole of SEC-ACL-002")

    # ------------------------------------------------------------------ RECORDED
    reading = run_audit(["list", "--type", "access", "--limit", "5"])
    lines_out = [line for line in reading.stdout.strip().splitlines() if line.strip()]
    found = reading.returncode == 0 and any(session.session_id[:12] in line for line in lines_out)
    if found:
        detail = "the chain holds the refusal, read back through the audit tool: " + lines_out[-1].strip()
    else:
        detail = ("no event naming this session is in the chain ("
                  + (reading.stderr.strip()[:160] or "the audit tool printed nothing") + ")")
    ledger.record("RECORDED", bool(refused and found), detail,
                  "the 403 would go out and the record would be missing, leaving an access decision with no "
                  "evidence of what was attempted")

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
