#!/usr/bin/env python3
"""The session tool: establish a session, inspect one, and rehearse the refusal.

**This is a tool, not a service.** It runs when called and exits, like the audit sink and the retention
commands, and it holds no state of its own: the sessions live in the deployment's database and the permission
lives in the rendered registry.

**It exists in this form because C7 replaces it rather than the thing it protects.** The mint command is the
development stand-in for an identity provider, and it refuses without `--dev` and says so. What C7 keeps is
everything else: the record, the resolution, and the refusal path, which is rehearsed here by the `forge`
command and asserted by `tests/fixtures/access_probe.py` against the live stack.

Usage on the box:

    alias oi='docker compose -f deploy/office/compose/docker-compose.yml --env-file rendered/env'
    oi --profile tools run --rm session migrate
    oi --profile tools run --rm session show
    oi --profile tools run --rm session mint --dev --subject dana@client.example --role viewer
    oi --profile tools run --rm session forge --dev --token <token> --field role --role executive
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

# The access model sits beside this file in the image, where the Dockerfile copies it, and one directory up
# in the checkout. Both are the same file; the image is the one a deployment runs.
_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE))
if not (_HERE / "access.py").exists():
    sys.path.insert(0, str(_HERE.parent / "retrieval"))

import access  # noqa: E402
import session as session_module  # noqa: E402

# Where the deployment root is. The registry and the migrations both live there, and both are mounted rather
# than baked into the image, so a render takes effect without a rebuild.
OI_ROOT = Path(os.environ.get("OI_ROOT", "/opt/oi-core"))


def die(message: str, code: int = 1) -> int:
    print(f"error: {message}", file=sys.stderr)
    return code


def registry_path() -> Path:
    override = os.environ.get("ACCESS_REGISTRY")
    if override:
        return Path(override)
    return OI_ROOT / "rendered" / "access.json"


def load_registry() -> access.Registry:
    """The registry, or a refusal naming the render command rather than a traceback."""
    try:
        return access.load(str(registry_path()))
    except access.AccessError as exc:
        raise SystemExit(
            f"error: {exc}\n\nThe registry is rendered from the manifest. Run:\n"
            f"  python3 scripts/render_manifest.py --manifest manifests/<the one you deployed>") from None


def deployment_id() -> str:
    return os.environ.get("DEPLOYMENT_ID", "unknown")


# --------------------------------------------------------------------------- commands


def cmd_show(args) -> int:
    """What this deployment's permission model actually is, which is not always what the manifest says."""
    registry = load_registry()
    print(f"registry   {registry_path()}")
    print(f"declared   {'yes' if registry.declared else 'no, so the built-in two folder roles apply'}")
    print(f"deployment {deployment_id()}")
    print()
    print(registry.describe())
    if not registry.users:
        print()
        print("No users are declared, so any subject may hold a session at a role the registry knows. A")
        print("deployment that declares users stops accepting subjects it has not been told about.")
    return 0


def cmd_migrate(args) -> int:
    """Apply the session migration, using the same bookkeeping as every other migration."""
    store = session_module.open_store()
    if not isinstance(store, session_module.PostgresStore):
        return die("migrate needs DATABASE_URL. Without it there is no schema to migrate.")
    try:
        applied = store.migrate(os.environ.get("MIGRATIONS_DIR", str(OI_ROOT / "db" / "migrations")))
    finally:
        store.close()
    if applied:
        for name in applied:
            print(f"applied {name}")
    else:
        print("no migration needed; the sessions table is present")
    return 0


def cmd_mint(args) -> int:
    registry = load_registry()
    store = session_module.open_store()
    try:
        session = session_module.mint(
            registry, store, args.subject, args.role, ttl_hours=args.ttl,
            deployment_id=deployment_id(), dev=args.dev, actor_name=args.name or "")
    except (session_module.SessionError, access.AccessError) as exc:
        return die(str(exc))
    finally:
        store.close()

    if args.json:
        print(json.dumps({
            # **The credential, not the identifier.** They were the same value until migration 019, which is
            # why printing the id used to work and now resolves nothing: the id is not a secret and does not
            # authenticate, so a caller needs the credential this command generated.
            "token": session.credential, "subject": session.subject, "role": session.role,
            "roots": sorted(session.roots), "groups": list(session.groups),
            "expires_at": session.expires_at.isoformat(),
        }, indent=2))
    else:
        print(f"token      {session.credential}")
        print(f"subject    {session.subject}")
        print(f"role       {session.role}")
        print(f"roots      {sorted(session.roots)}")
        if session.groups:
            print(f"groups     {list(session.groups)}")
        print(f"expires_at {session.expires_at.isoformat()}")
    return 0


def cmd_verify(args) -> int:
    """Resolve a token to the access it holds, which is also what a request does."""
    store = session_module.open_store()
    try:
        session = session_module.resolve_token(store, args.token)
    except session_module.SessionError as exc:
        return die(str(exc), 3)
    finally:
        store.close()
    permit = session.permit()
    print(f"subject    {session.subject}")
    print(f"role       {session.role}")
    print(f"groups     {list(session.groups)}")
    print(f"roots      {sorted(session.roots)}")
    print(f"expires_at {session.expires_at.isoformat()}")
    print(f"permit     {json.dumps(permit.as_record(), sort_keys=True)}")
    return 0


def cmd_forge(args) -> int:
    """Attempt to supply authority in a request, and expect to be refused.

    **The expected outcome is a refusal, so this command succeeds when it is refused and fails when it is
    not.** A rehearsal that exits zero either way tells you nothing, and the whole point of this one is that
    the acceptance criterion — a forged role refused and recorded — is falsifiable on demand.
    """
    store = session_module.open_store()
    try:
        session = session_module.resolve_token(store, args.token)
    except session_module.SessionError as exc:
        return die(str(exc), 3)
    finally:
        store.close()

    try:
        session_module.check_role_claim(session, args.field, args.role, dev=args.dev)
    except session_module.SessionError as exc:
        # Refused, which is the pass. The message says where the record is.
        print(f"REFUSED   {exc}")
        print()
        print("The refusal is the evidence. Read it back out of the chain rather than trusting this line:")
        print(f"  oi --profile tools run --rm audit list --type access | head")
        return 0
    print(f"NOT REFUSED  a request supplying {args.field!r} was accepted, so the boundary is not enforced",
          file=sys.stderr)
    return 1


def cmd_refusals(args) -> int:
    """The refusals recorded against one session, read from the chain rather than from a message."""
    store = session_module.open_store()
    if not hasattr(store, "refusals_for"):
        return die("refusals needs DATABASE_URL: the record is in the database, not in this process.")
    try:
        rows = store.refusals_for(args.session, limit=args.limit)
    finally:
        store.close()
    if not rows:
        print(f"no refusal is recorded against session {args.session}")
        print("If a request was refused, the record is missing and the deployment has no working evidence.")
        return 1
    for row in rows:
        payload = row["payload"] or {}
        print(f"{row['occurred_at']}  {row['event_type']}/{row['outcome']}  "
              f"claimed_role={row['actor_role']!r}  session_role={payload.get('session_role')!r}  "
              f"field={payload.get('supplied_field')!r}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = parser.add_subparsers(dest="command", required=True)

    show = sub.add_parser("show", help="the permission model this deployment is enforcing")
    show.set_defaults(func=cmd_show)

    migrate = sub.add_parser("migrate", help="apply the sessions migration")
    migrate.set_defaults(func=cmd_migrate)

    mint = sub.add_parser("mint", help="establish a session. Development only")
    mint.add_argument("--subject", required=True, help="a subject the manifest declares, once it declares any")
    mint.add_argument("--role", required=True, help="a role the registry knows")
    mint.add_argument("--ttl", type=int, default=8, help="hours, at most 24 (default 8)")
    mint.add_argument("--name", default="", help="a display name for the record")
    mint.add_argument("--dev", action="store_true",
                      help="accept that this is a hand-established identity. Refused without it")
    mint.add_argument("--json", action="store_true", help="machine-readable, for a probe")
    mint.set_defaults(func=cmd_mint)

    verify = sub.add_parser("verify", help="resolve a token to the access it holds")
    verify.add_argument("--token", required=True)
    verify.set_defaults(func=cmd_verify)

    refusals = sub.add_parser("refusals", help="the refusals recorded against one session")
    refusals.add_argument("--session", required=True, help="the session id the refusals were recorded against")
    refusals.add_argument("--limit", type=int, default=5)
    refusals.set_defaults(func=cmd_refusals)

    forge = sub.add_parser("forge", help="rehearse a request that supplies its own role; refused is a pass")
    forge.add_argument("--token", required=True, help="a live token, which the forged request holds")
    forge.add_argument("--field", default="role", help="the field the request supplies (default: role)")
    forge.add_argument("--role", default="executive", help="the role the request claims")
    forge.add_argument("--dev", action="store_true",
                       help="record through the audit tool's development path")
    forge.set_defaults(func=cmd_forge)

    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    try:
        return args.func(args)
    except access.AccessError as exc:
        return die(str(exc))


if __name__ == "__main__":
    sys.exit(main())
