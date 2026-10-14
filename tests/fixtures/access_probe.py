#!/usr/bin/env python3
"""Forge a role, and see whether the boundary holds and the refusal is findable afterwards.

`SEC-ACL-002`'s verify line is *a user outside a document's group cannot retrieve it by any query*. That is a
claim about a live stack, so this runs against one: the real retriever, the real store, the real index and the
real audit chain. A unit test can prove the registry resolves the roots it should; only this can prove the
store never returns the passage.

**Four rights, and a wrong path beside each.** A right with no failing case beside it is not a test, and this
project has already paid for that lesson: a probe that asserted the mechanism worked while every documented
command was broken. The ledger prints both, so a reader can see what would have happened if the control were
absent rather than taking the summary's word for it.

    | right     | the act                                   | the wrong path it rules out            |
    |-----------|-------------------------------------------|----------------------------------------|
    | ACCESS    | ask a finance question as a staff session | the store returning the finance text   |
    | SCOPED    | ask across the boundary and count         | a filter that only applies to one arm  |
    | FORGED    | mint a session claiming an ungranted role | an identity invented by the caller     |
    | REFUSED   | send a request supplying its own role     | a role honoured because it was typed   |
    | RECORDED  | read the refusal out of the chain         | a refusal nobody can find afterwards   |

**Why a forged session first.** If a caller can mint its own authority then the refusal path proves nothing,
because there is nothing legitimate to refuse against. That is the whole reason C3 builds a stand-in verifier
and does not wait for the identity provider.

Run from the repository root on the box, with the alias set in the same shell:

    alias oi='docker compose -f deploy/office/compose/docker-compose.yml --env-file rendered/env'
    docker run --rm --network ai-net --user "$(id -u):$(id -g)" \
      -v /opt/oi-core:/w -w /w --env-file rendered/env \
      --entrypoint python oi-core/eval:local /w/tests/fixtures/access_probe.py

The `eval` image rather than the ingestion one because this imports the retriever, which imports botocore
through the reranker. `--env-file rendered/env` supplies DATABASE_URL for the lexical arm; without it the
lexical arm is skipped and the second half of `SCOPED` cannot run.

Exit codes: 0 when every right held, 1 when one did not, 2 when the fixture is missing so nothing could be
tested. A missing database is NOT an error exit here, because the access filter lives in the vector query and
applies to both arms; but the report says which arms ran, so a green run that tested half the claim is visible
rather than implied.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, "/w/services/retrieval")
sys.path.insert(0, "/w/services/ingestion")
sys.path.insert(0, "/w/services/session")

import access as access_module  # noqa: E402
import ingest  # noqa: E402
import retriever  # noqa: E402

W = Path("/w")

# The questions, and the root each one's answer lives under. They are the two the corpus already answers:
# the finance document carries a fee schedule, and the company document carries the mileage rate.
QUESTIONS = {
    "finance": "What is the annual account fee on the fee schedule?",
    "company": "What is the mileage rate for reimbursement?",
}


class Ledger:
    """Every right and its wrong path, printed together, because a summary line is not evidence."""

    def __init__(self) -> None:
        self.rows: list[tuple[str, bool, str, str]] = []

    def record(self, right: str, held: bool, observed: str, wrong_path: str) -> None:
        self.rows.append((right, held, observed, wrong_path))
        mark = "ok  " if held else "FAIL"
        print(f"{mark} {right:<9} {observed}")
        print(f"          if it were absent: {wrong_path}")

    def failures(self) -> int:
        return sum(1 for _, held, _, _ in self.rows if not held)


def run_session_tool(args: list[str], timeout: int = 120) -> subprocess.CompletedProcess:
    """One session-tool invocation, through the compose command rather than by importing the module.

    **Through the tool on purpose.** The manual tells an operator to run `oi --profile tools run --rm session`,
    and a probe that called the functions directly would pass while that command was broken. That is the exact
    gap this project's last review round was about: testing from the code tests your knowledge of the code.
    """
    oi = os.environ.get("OI_COMPOSE", "docker compose -f deploy/office/compose/docker-compose.yml "
                                      "--env-file rendered/env")
    command = f"{oi} --profile tools run --rm -T session " + " ".join(args)
    return subprocess.run(command, shell=True, capture_output=True, text=True, timeout=timeout,
                          cwd=str(W))


def mint(subject: str, role: str) -> dict | None:
    """A session, or None with the reason already printed."""
    result = run_session_tool(["mint", "--dev", "--json", "--subject", subject, "--role", role])
    if result.returncode != 0:
        print(f"    the mint was refused: {(result.stderr or result.stdout).strip()[:400]}")
        return None
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError:
        print(f"    the mint printed something that is not JSON: {result.stdout.strip()[:300]}")
        return None


def ask(services, question: str, session: dict):
    """One retrieval, through a permit built exactly the way a session builds one.

    The permit is the only thing that decides the roots; the role is passed beside it for the record. That is
    the same call the assistant API will make, so this exercises the path a request takes rather than a
    convenient one.
    """
    permit = access_module.Permit(subject=session["subject"], role=session["role"],
                                  roots=frozenset(session["roots"]),
                                  groups=tuple(session.get("groups") or []),
                                  session_id=session.get("token", ""))
    result = retriever.search(services, question, permit.role, limit=5, permit=permit)
    return {p.acl for p in result.passages}, result

def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--subject", default="probe-staff@officeinference.com",
                        help="a subject to act as. A registry that declares users refuses an undeclared one")
    parser.add_argument("--staff-role", default=None,
                        help="the role to hold. Defaults to the registry's staff role, or its first role")
    parser.add_argument("--forged-role", default=None,
                        help="the role to claim. Defaults to a role the registry has that staff does not")
    args = parser.parse_args()

    registry_path = os.environ.get("ACCESS_REGISTRY", str(W / "rendered" / "access.json"))
    try:
        registry = access_module.load(registry_path)
    except access_module.AccessError as exc:
        print(f"FAILED with 1 problem: {exc}", file=sys.stderr)
        return 2

    print("THE PERMISSION MODEL THIS DEPLOYMENT IS ENFORCING")
    print(f"  registry   {registry_path}")
    print(f"  declared   {'yes' if registry.declared else 'no, so the built-in two folder roles apply'}")
    for line in registry.describe().splitlines():
        print(f"  {line}")
    print()

    services = ingest.Services.from_env()
    ledger = Ledger()

    # ------------------------------------------------------------------ ACCESS
    staff_role = args.staff_role or ("staff" if "staff" in registry.roles else sorted(registry.roles)[0])
    staff = mint(args.subject, staff_role)
    if staff is None:
        print("FAILED with 1 problem: no session could be minted, so nothing below could be tested",
              file=sys.stderr)
        return 2

    acls, _ = ask(services, QUESTIONS["finance"], staff)
    held = "finance" not in acls
    ledger.record("ACCESS", held,
                  f"as {staff_role!r} over roots {sorted(staff['roots'])}, the finance question returned "
                  f"acls={sorted(acls) or '(none)'}",
                  "the finance passage would be in the answer, and the model would read it")

    # ------------------------------------------------------------------ SCOPED
    _acls, result = ask(services, QUESTIONS["finance"], staff)
    arms_ran = [arm for arm, count in (result.arms or {}).items() if count]
    # The count is what makes this more than a repeat of ACCESS. It comes from a query against the COMPLEMENT
    # of the permitted roots, so a filter applied to the dense arm and not the lexical one shows up here as a
    # zero while ACCESS still passes on the dense arm alone.
    scoped = result.restricted_matched > 0 and "finance" not in {p.acl for p in result.passages}
    ledger.record("SCOPED", scoped,
                  f"across {len(arms_ran) + 1} retrieval arm(s) ({chr(44).join(arms_ran)}, plus the count) "
                  f"the boundary held, and {result.restricted_matched} passage(s) matched outside it and "
                  f"were withheld",
                  "one arm would return what the other filtered, which is how a leak hides in a hybrid search")

    # ------------------------------------------------------------------ FORGED
    # A role this subject was not granted.
    #
    # **Two cases, and only one of them is testable.** Where the deployment declares users and their groups
    # carry roots, a role granting more than the subject holds is genuinely ungranted and the mint has to
    # fail. Where every role is grantable to every subject — the built-in two-role model, or a declared
    # registry whose users carry no groups — there is nothing to forge. That is a property of the model
    # rather than a hole in the boundary, and it is reported as a skip with the reason rather than passed
    # quietly or failed for a cause nobody can see.
    subject_groups = registry.users[args.subject].groups if args.subject in registry.users else ()
    granted_roots = set(staff['roots'])
    for group in subject_groups:
        granted_roots |= set(registry.groups.get(group, ()))
    ungranted = None
    for role in sorted(registry.roles):
        if role == staff_role or not registry.roles[role]:
            continue
        if set(registry.roles[role]) - granted_roots:
            # This role reaches a root the subject's groups do not, so claiming it is a forgery.
            ungranted = role
            break

    if ungranted is None:
        print('skip FORGED     no role in this registry reaches a root this subject does not already hold,')
        print('                so there is nothing to forge. A deployment with declared users and groups')
        print('                has one; the built-in two-role model does not.')
        forged_refused = None
    else:
        forged = mint(args.subject, ungranted)
        forged_refused = forged is None
        ledger.record('FORGED', forged_refused,
                      f'minting a session as {ungranted!r} for {args.subject}, who holds {sorted(staff["roots"])}, '
                      f'was refused' if forged_refused else
                      f'a session was minted as {ungranted!r}, so the boundary is not enforced when a session '
                      f'is established',
                      'the caller would hold a role the deployment never granted, and every later check would '
                      'agree with it')

    # The role a forged request claims: the first that reaches past what the session holds.
    other = ungranted or next((r for r in sorted(registry.roles) if r != staff_role), staff_role)

    # ------------------------------------------------------------------ REFUSED
    forge = run_session_tool(["forge", "--dev", "--token", staff["token"], "--field", "role",
                              "--role", other])
    refused = forge.returncode == 0 and "REFUSED" in forge.stdout
    ledger.record("REFUSED", refused,
                  f"a request supplying role={other!r} was refused; the session held {staff_role!r}",
                  "a role would be honoured because a caller typed it, which is the whole of SEC-ACL-002")

    # ------------------------------------------------------------------ RECORDED
    # Read back out of the chain rather than out of the refusal's own message. A refusal nobody can find
    # afterwards is not a control, and a message claiming to have recorded one is exactly what a broken
    # writer would print.
    reading = run_session_tool(["refusals", "--session", staff["token"]])
    found = reading.returncode == 0 and "claimed_role=" in reading.stdout
    ledger.record("RECORDED", bool(refused and found),
                  "the chain holds a refusal naming the claimed role, read back by session id: "
                  + (reading.stdout.strip().splitlines()[0] if found else "(nothing found)"),
                  "the 403 would go out and the record would be missing, leaving an access decision with "
                  "no evidence of what was attempted")
    print()
    failures = ledger.failures()
    if failures:
        print(f"FAILED with {failures} problem(s)")
        return 1
    print(f"PASSED: {len(ledger.rows)} rights held against the live stack")
    print()
    print("Read the refusal out of the chain yourself rather than trusting this line:")
    print("  docker compose -f deploy/office/compose/docker-compose.yml --env-file rendered/env \\")
    print("    --profile tools run --rm audit list --type access | head")
    return 0


if __name__ == "__main__":
    sys.exit(main())
