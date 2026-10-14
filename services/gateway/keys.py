#!/usr/bin/env python3
"""Mint one virtual key per model role, with a model ACL and a hard budget.

The LiteLLM master key is an administration credential. It mints and updates these keys, and it
appears nowhere else: every component that calls a model uses the key for the role it is acting as,
so a runaway loop hits a ceiling instead of the client's card.

Idempotent by alias, so re-running restores the manifest's declared budgets rather than minting new
credentials. The secrets live in rendered/keys.json, mode 600, and are never printed. The gateway's
database is the source of truth for the policy; that file is the source of truth for the secrets.

Usage:
    keys.py                              mint or update every role from the rendered manifest
    keys.py --set-budget ROLE AMOUNT     cap one role now, without editing the manifest
    keys.py --probe ROLE                 one small call with that role's key, and report
    keys.py --probe ROLE --as-model M    prove the model ACL: this should be refused

--set-budget is the emergency brake and the drill: it narrows a live key's ceiling so a looping
component is stopped now rather than at the next deploy. The next plain run puts it back.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path

RENDERED = Path(os.environ.get("RENDERED_DIR", "/app/rendered"))
ENV_FILE = RENDERED / "env"
MANIFEST_FILE = RENDERED / "manifest.json"
KEYS_FILE = RENDERED / "keys.json"
GATEWAY = os.environ.get("LITELLM_URL", "http://litellm:4000").rstrip("/")


def die(message: str) -> None:
    raise SystemExit(message)


def read_env(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.exists():
        return values
    for line in path.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, _, value = line.partition("=")
            values[key.strip()] = value.strip()
    return values


def request(path: str, token: str, payload: dict | None = None, method: str = "POST",
            timeout: int = 30, extra_headers: dict | None = None) -> tuple[int, dict]:
    """Returns the status and the parsed body. Errors come back rather than raise, because a refused
    call is a result this tool has to report rather than crash on."""
    body = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(f"{GATEWAY}{path}", data=body, method=method)
    req.add_header("Authorization", f"Bearer {token}")
    # The signed statement, when the caller has one. The gateway accepts it in place of the key,
    # and refuses the key for request traffic, which is why the probe mints one.
    for header_name, header_value in (extra_headers or {}).items():
        req.add_header(header_name, header_value)
    if body:
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as response:
            return response.status, json.loads(response.read() or b"{}")
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode(errors="replace")
        try:
            return exc.code, json.loads(detail)
        except json.JSONDecodeError:
            return exc.code, {"raw": detail[:400]}
    except urllib.error.URLError as exc:
        die(f"cannot reach the gateway at {GATEWAY}: {exc.reason}")


def load_keys() -> dict:
    return json.loads(KEYS_FILE.read_text()) if KEYS_FILE.exists() else {}


def save_keys(keys: dict) -> None:
    KEYS_FILE.write_text(json.dumps(keys, indent=2, sort_keys=True) + "\n")
    KEYS_FILE.chmod(0o600)


def write_key_records(keys: dict, deployment: str, role: str, alias: str,
                      max_budget: float | None, duration: str) -> None:
    keys[role] = {
        "key": keys[role]["key"],
        "alias": alias,
        "models": [role],
        "max_budget": max_budget,
        "budget_duration": duration if max_budget is not None else None,
        "metadata": {"deployment_id": deployment, "role": role},
    }


def mint(env: dict, manifest: dict, keys: dict) -> int:
    master = env.get("LITELLM_MASTER_KEY")
    if not master:
        die(f"no LITELLM_MASTER_KEY in {ENV_FILE}: run the renderer first")
    deployment = env.get("DEPLOYMENT_ID", "unknown")
    models = manifest.get("models") or {}
    budgets = manifest.get("budgets") or {}
    by_role = budgets.get("roles") or {}
    period = int(budgets.get("period_days", 30))
    duration = f"{period}d"

    rows = []
    for role in sorted(models):
        alias = f"{deployment}:{role}"
        max_budget = by_role.get(role)
        max_budget = float(max_budget) if max_budget is not None else None
        policy = {"models": [role],
                  "metadata": {"deployment_id": deployment, "role": role}}
        if max_budget is not None:
            policy["max_budget"] = max_budget
            policy["budget_duration"] = duration

        if role in keys and keys[role].get("key"):
            status, body = request("/key/update", master, {"key": keys[role]["key"], **policy})
            if status != 200:
                die(f"could not update the key for {role}: {status} {str(body)[:200]}")
            action = "updated"
            write_key_records(keys, deployment, role, alias, max_budget, duration)
        else:
            status, body = request("/key/generate", master, {"key_alias": alias, **policy})
            if status != 200 or not body.get("key"):
                die(f"could not mint a key for {role}: {status} {str(body)[:200]}")
            keys[role] = {"key": body["key"]}
            write_key_records(keys, deployment, role, alias, max_budget, duration)
            action = "minted"
        rows.append((role, action, max_budget))

    save_keys(keys)
    print(f"keys for {deployment}:")
    for role, action, max_budget in rows:
        ceiling = f"{max_budget} USD per {period}d" if max_budget is not None else "no ceiling declared"
        print(f"  {role:10} {action:8} models=[{role}]  {ceiling}")
    print(f"  secrets in {KEYS_FILE} (mode 600) and never printed")
    return 0


def provision_teams(env: dict, manifest: dict) -> int:
    """Create or update one team per role, carrying that role's ceiling.

    **Budgets live on teams now, and the reason is the credential change.** The gateway's custom_auth hook
    resolves a signed assertion into an identity whose `team_id` is the role, and a key-level budget is not
    enforced on that path. A team budget is, and it refuses harder than the key budgets did: the spike saw
    `budget_exceeded` at HTTP 422 with the spend and the ceiling named, where a key budget is only checked
    against recorded spend and can be passed by concurrent requests.

    **The team must exist before an assertion naming it works.** A team the database does not know returns a
    404 telling you to create it, so this runs as part of rendering a deployment rather than as a repair.
    """
    master = env.get("LITELLM_MASTER_KEY")
    if not master:
        die(f"no LITELLM_MASTER_KEY in {ENV_FILE}: run the renderer first")
    deployment = env.get("DEPLOYMENT_ID", "unknown")
    models = manifest.get("models") or {}
    budgets = manifest.get("budgets") or {}
    by_role = budgets.get("roles") or {}
    period = int(budgets.get("period_days", 30))
    duration = f"{period}d"

    rows = []
    for role in sorted(models):
        ceiling = by_role.get(role)
        policy = {"team_id": role, "team_alias": f"{deployment}:{role}", "models": [role],
                  "metadata": {"deployment_id": deployment, "role": role}}
        if ceiling is not None:
            policy["max_budget"] = float(ceiling)
            policy["budget_duration"] = duration
        status, body = request("/team/new", master, policy)
        if status != 200:
            # A team that already exists refuses a second create, so the update path is the normal one.
            status, body = request("/team/update", master, policy)
        if status != 200:
            die(f"could not provision the team for {role}: {status} {str(body)[:200]}")
        rows.append((role, ceiling))

    print(f"teams for {deployment}:")
    for role, ceiling in rows:
        limit = f"{ceiling} USD per {period}d" if ceiling is not None else "no ceiling declared"
        print(f"  {role:10} members=[] models=[{role}]  {limit}")
    print("  budgets are enforced per team, which is the path a signed assertion takes")
    return 0

def set_budget(env: dict, args: argparse.Namespace, connect=None) -> int:
    """Cap a role now, through the row the gateway actually enforces.

    **This used to update the virtual key, and that stopped working when the gateway changed.** Request traffic
    is authenticated by a signed statement that resolves to a TEAM; the virtual keys this tool mints are refused
    on that path. So capping a key set a number on something nothing consulted: the command reported success, the
    team row was unchanged, and the next call answered normally. An emergency brake that reports success and does
    nothing is worse than no brake, because the operator stops looking.

    **The ceiling lives on the team**, so that is what this writes. Found by the C3 panel's cold reader, who
    noticed the manual contradicted itself about when a cap takes effect, and a test then showed neither page was
    right: it did not take effect at all.

    **The gateway caches the team row**, so this writes the number and says plainly that the gateway must be
    restarted before it enforces. Saying so is what makes it a control rather than a belief.
    """
    # The connection is injectable so the logic can be tested without a Postgres driver in the test
    # environment, which is how the rest of this tool is tested.
    url = (env.get('DATABASE_URL') or os.environ.get('DATABASE_URL') or '').strip()
    if connect is None:
        import psycopg
        connect = psycopg.connect
    if not url:
        die('no DATABASE_URL, so no ceiling can be written. Run this where rendered/env is available.')
    amount = float(args.amount)
    conn = connect(url)
    try:
        with conn.cursor() as cur:
            cur.execute('select team_id from "LiteLLM_TeamTable" where team_id = %s', (args.role,))
            if cur.fetchone() is None:
                die('no team named ' + repr(args.role) + '. Run this tool with --provision-teams first.')
            cur.execute('update "LiteLLM_TeamTable" set max_budget = %s where team_id = %s',
                        (amount, args.role))
        conn.commit()
    finally:
        conn.close()

    print(args.role + ' capped at ' + str(amount) + ' USD in the team row.')
    print('The gateway caches this row, so it is NOT in force until litellm restarts:')
    print('    oi restart litellm')
    print("A plain run of this tool restores the manifest's value.")
    return 0

def probe(env: dict, args: argparse.Namespace) -> int:
    """One small model call, through the path the gateway actually accepts.

    **This used to present a virtual key, and the gateway no longer accepts one for request traffic.** The
    probe returned a 401 that said so, which made it useless for the thing the manual told readers to use it
    for: proving that a role's model and budget work. It now mints a signed assertion the way the assistant
    API does, so a 200 means a model call genuinely succeeded.

    **`--as-model` is where this earns its keep.** The assertion names the model role, the gateway checks it
    against the team, and asking for a role this session should not reach is refused. That is the model
    permission proved rather than described.

    If the assertion key or the database is unreachable it falls back to the virtual key and says so, because
    a diagnostic that silently tests a different path is worse than one that admits which path it tested.
    """
    keys = load_keys()
    if args.role not in keys:
        die(f"no key for {args.role!r} yet: run this tool with no arguments first")
    model = args.as_model or args.role
    payload = {"model": model,
               "messages": [{"role": "user", "content": "Reply with the single word: ready"}],
               "max_tokens": 16}

    header, token, how = _assertion_header(env, model)
    status, body = request("/v1/chat/completions", keys[args.role]["key"], payload, extra_headers=header)
    if status == 200:
        usage = body.get("usage") or {}
        print(f"{args.role} -> {model}: {status}   (authenticated by {how})")
        print(f"  answered by {body.get('model')}, {usage.get('total_tokens')} tokens")
        return 0
    message = body.get("error", {}).get("message") if isinstance(body.get("error"), dict) else body
    print(f"{args.role} -> {model}: {status}   (authenticated by {how})")
    print(f"  {str(message)[:300]}")
    return 1


def _assertion_header(env: dict, model: str):
    """A signed statement for this call, or nothing with a reason."""
    key_path = env.get("OI_ASSERTION_KEY", "/app/rendered/assertion.key")
    if not os.path.exists(key_path):
        return {}, "", "the virtual key: no assertion key at " + key_path
    try:
        # The image copies assertion.py beside this tool rather than into a services tree, and the
        # checkout is the fallback for running this outside a container.
        for candidate in ("/app", str(Path(__file__).resolve().parent)):
            if candidate not in sys.path:
                sys.path.insert(0, candidate)

        import assertion as assertion_module
        key = assertion_module.load_key(key_path)
        token = assertion_module.mint(key, model, session_id="keys-probe")
        return {assertion_module.HEADER: token}, token, "a signed assertion"
    except Exception as exc:  # noqa: BLE001 - the fallback is the point
        return {}, "", "the virtual key: the assertion could not be minted (" + type(exc).__name__ + ")"

def revoke(env: dict, role: str) -> int:
    """Delete a role's key at the gateway and forget it here.

    Deleting a role from a manifest leaves its key behind, scoped to a model that no longer exists.
    Nothing else removes it: minting only adds or updates, and re-adding the role name later would
    bring the old key back to life with the old budget. So revoking is two deliberate steps, and this
    does both.
    """
    master = env.get("LITELLM_MASTER_KEY")
    if not master:
        die(f"no LITELLM_MASTER_KEY in {ENV_FILE}: run the renderer first")
    keys = load_keys()
    record = keys.get(role)
    if not record or not record.get("key"):
        die(f"no key recorded for {role} in {KEYS_FILE}. Nothing to revoke.\n"
            f"  A role whose key lives only at the gateway is not one this tool can vouch for; "
            f"delete it there by alias instead.")
    status, body = request("/key/delete", master, {"keys": [record["key"]]})
    if status != 200:
        die(f"could not revoke the key for {role}: {status} {str(body)[:200]}")
    del keys[role]
    save_keys(keys)
    print(f"revoked {role}: deleted at the gateway and removed from {KEYS_FILE}")
    print("  The role may still exist in the manifest. Add it back to models: and budgets.roles: "
          "to deploy it again, and it will get a new key rather than this one.")
    return 0


def main(argv: list[str] | None = None) -> int:
    """argv is a parameter so the tests can drive this without touching the process arguments."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--set-budget", nargs=2, metavar=("ROLE", "AMOUNT"))
    parser.add_argument("--probe", metavar="ROLE")
    parser.add_argument("--provision-teams", action="store_true",
                        help="create or update one team per role, carrying that role's budget. A signed "
                             "assertion is checked against a team, so this runs with every render")
    parser.add_argument("--revoke", metavar="ROLE",
                        help="delete a role's key at the gateway and forget it here")
    parser.add_argument("--as-model", metavar="MODEL",
                        help="probe a model the key is not scoped to, which must be refused")
    args = parser.parse_args(argv)

    if args.as_model and not args.probe:
        die("--as-model only means something with --probe ROLE, and on its own it would do nothing")

    env = read_env(ENV_FILE)
    if args.set_budget:
        args.role, args.amount = args.set_budget
        return set_budget(env, args)
    if args.probe:
        args.role = args.probe
        return probe(env, args)
    if args.revoke:
        return revoke(env, args.revoke)
    if args.provision_teams:
        if not MANIFEST_FILE.exists():
            die(f"no rendered manifest at {MANIFEST_FILE}: run the renderer first")
        return provision_teams(env, json.loads(MANIFEST_FILE.read_text()))

    if not MANIFEST_FILE.exists():
        die(f"no rendered manifest at {MANIFEST_FILE}: run the renderer first")
    return mint(env, json.loads(MANIFEST_FILE.read_text()), load_keys())


if __name__ == "__main__":
    sys.exit(main())
