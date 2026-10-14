#!/usr/bin/env python3
"""Render a client manifest into runnable configuration.

The manifest is what a client buys. This turns it into the files the deployment actually
reads, so configuration is never hand-edited on a server.

Writes, into the output directory:

    env             secrets and settings for compose, mode 600, never committed
    litellm.yaml    the gateway's model list, built from the manifest's model roles
    services.txt    the services the chosen packs declare (informational for now)
    manifest.json   the normalized manifest, for drift comparison later
    access.json     the declared permission model: roots, groups and users (SEC-ACL-002)

Idempotent on purpose: if env already exists, its secrets are preserved. Regenerating the
database password would lock the stack out of its own data.

Usage:
    python3 scripts/render_manifest.py --manifest manifests/dev-office.yaml
"""

from __future__ import annotations

import argparse
import json
import os
import secrets
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(Path(__file__).resolve().parent))

import yaml  # noqa: E402

from validate_manifests import check, load_catalog_packs  # noqa: E402

# The access model is defined once, in the module the retriever and the session tool both read. The renderer
# imports it rather than repeating its shape, so a field added there cannot be silently dropped here — the
# failure that would leave a deployment enforcing a permission model nobody declared.
sys.path.insert(0, str(ROOT / "services" / "retrieval"))
import access  # noqa: E402

DEFAULT_CTX = 8192
DATA_CLASSES = ("public", "internal", "confidential", "regulated")
SERVICES_FROM_CATALOG = ("core",)


def read_env_file(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if path.exists():
        for line in path.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                key, _, value = line.partition("=")
                values[key.strip()] = value.strip()
    return values


def pack_services(pack_ids: list[str]) -> list[str]:
    """Union of the services declared by the chosen packs, following `requires`.

    The catalog says every sellable pack requires core, and until this followed those edges a
    manifest listing only sellable packs rendered a stack with no gateway in it: the services a
    client buys, and none of the ones that make them work.
    """
    catalog = yaml.safe_load((ROOT / "packs" / "catalog.yaml").read_text())
    by_id = {p["id"]: p for p in catalog.get("packs", [])}

    resolved: list[str] = []

    def add(pack_id: str) -> None:
        if pack_id in resolved:
            return
        resolved.append(pack_id)
        for required in (by_id.get(pack_id, {}) or {}).get("requires") or []:
            add(str(required))

    for pack_id in pack_ids:
        add(str(pack_id))

    services: list[str] = []
    for pack_id in resolved:
        for service in (by_id.get(pack_id, {}) or {}).get("services") or []:
            name = str(service).split()[0]  # strips trailing comments if any
            if name not in services:
                services.append(name)
    return services


def build_litellm_config(manifest: dict, env: dict[str, str]) -> dict:
    """One gateway entry per role per available lane.

    A cloud manifest has no local models and a frontier lane, so the earlier version, which only
    understood local entries, rendered an empty model list for exactly the deployment we build
    first. Local entries point at the model server; frontier entries point at Bedrock in the
    client's own account.
    """
    models = []
    frontier_enabled = bool(((manifest.get("lanes") or {}).get("frontier") or {}).get("enabled"))
    profile = manifest.get("profile")

    for role, entry in (manifest.get("models") or {}).items():
        entry = entry or {}
        local = entry.get("local") or ""
        frontier = entry.get("frontier") or ""

        if local and profile != "cloud":
            models.append({
                "model_name": role,
                "litellm_params": {
                    "model": f"openai/{local}",
                    "api_base": "http://llamacpp:8080/v1",
                    "api_key": "not-needed-on-a-private-network",
                },
                "model_info": {"lane": "local"},
            })

        if frontier and (frontier_enabled or profile == "cloud"):
            model_id = frontier.split("/", 1)[1] if frontier.startswith("bedrock/") else frontier
            models.append({
                "model_name": role if not local else f"{role}-frontier",
                "litellm_params": {"model": f"bedrock/{model_id}"},
                "model_info": {"lane": "frontier"},
            })
    return {
        "model_list": models,
        "litellm_settings": {"drop_params": True, "set_verbose": False},
        "general_settings": {"master_key": "os.environ/LITELLM_MASTER_KEY"},
    }


def build_access_registry(manifest: dict) -> dict:
    """The declared permission model, normalized into the file the runtime reads.

    **Absent and empty are different, and the difference is the whole point of `declared`.** A manifest with
    no `access.roots`, `access.groups` or `access.users` keeps the built-in two folder roles, which is what
    every manifest on the box does today. A manifest that declares any of the three gets exactly what it
    declared, and the validator has already refused it if the three disagree.

    **The model block is deliberately not read here.** A session resolves access and never a model role: which
    model answers is a property of what the software is doing, not of who asked, so `models` belongs to the
    gateway's config and this file never mentions it. A reader looking for a group-level model field will not
    find one, and that is the decision rather than an omission.
    """
    block = manifest.get("access") or {}
    declared = any(key in block for key in ("roots", "groups", "users"))

    if not declared:
        roles = {name: sorted(roots) for name, roots in access.DEFAULT_ROLES.items()}
        return {
            "registry_version": 1,
            "declared": False,
            "deployment_id": manifest["client"]["deployment_id"],
            "roles": roles,
            "groups": {},
            "users": [],
            "roots": sorted(access.DEFAULT_ROOTS),
        }

    groups = {name: sorted(group_roots(spec, f"access.groups.{name}"))
              for name, spec in (block.get("groups") or {}).items()}
    users = [
        {
            "subject": entry["subject"],
            "name": entry.get("name", ""),
            "groups": list(entry.get("groups") or []),
        }
        for entry in (block.get("users") or [])
    ]
    # Every root the deployment names, from either level. A root that no role and no group carries is a
    # document label nothing can ever reach, which the validator reports as a warning rather than an error
    # because a deployment may be mid-migration.
    roots = sorted(set(block.get("roots") or []) | {r for rs in groups.values() for r in rs})
    return {
        "registry_version": 1,
        "declared": True,
        "deployment_id": manifest["client"]["deployment_id"],
        "roles": declared_roles(block, groups, sorted(block.get("roots") or [])),
        "groups": groups,
        "users": users,
        "roots": roots,
    }


def group_roots(spec, where: str) -> list:
    """The roots a group carries, from the mapping form the manual documents.

    ```yaml
    groups:
      staff: {roots: [company]}
    ```

    A mapping rather than a bare list, because a group will grow fields that are not its roots — a display
    name, a description for the client's own review — and a bare list has nowhere to put them without
    changing the shape later.
    """
    if isinstance(spec, dict):
        roots = spec.get("roots")
        if roots is None:
            raise SystemExit(f"{where} has no roots. A group with no roots grants nothing.")
        return roots
    if isinstance(spec, list):
        # The bare-list form, accepted so an older manifest still renders. The manual documents the mapping.
        return spec
    raise SystemExit(f"{where} must be a mapping with a roots key, got {type(spec).__name__}")


def declared_roles(block: dict, groups: dict, roots: list) -> dict:
    """The role map a declared registry implies.

    **A role and a group answer different questions.** A role says what kind of caller this is and comes from
    the session; a group says what a person may read and comes from the manifest. A deployment may write its
    access at either level.

    **When no roles are declared, this emits one that carries no roots of its own**, named `viewer`, which is
    the case a grouped deployment wants: every person's permission comes from their groups and there is exactly
    one place access is written down. `access.roles` overrides that for a deployment that would rather write
    permissions beside the roles they belong to.

    A declared role is still the session's claim and not the person's whole permission: the subject's groups
    are unioned on top, which is stated here because a reader who assumes a role is the ceiling would expect
    the opposite.
    """
    declared = block.get("roles")
    if declared:
        return {name: sorted(rts) for name, rts in declared.items()}
    return {"viewer": []}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", required=True)
    parser.add_argument("--root", default="/opt/oi-core", help="deployment root on the target host")
    parser.add_argument("--out", default=None, help="output directory (default: <root>/rendered)")
    args = parser.parse_args()

    manifest_path = Path(args.manifest).resolve()
    manifest = yaml.safe_load(manifest_path.read_text()) or {}

    packs = load_catalog_packs()
    problems = check(manifest, packs, manifest_path)
    if problems:
        print(f"manifest is not installable: {manifest_path}")
        for problem in problems:
            print(f"  - {problem}")
        return 1

    # Read the catalog before writing anything, so a broken catalog fails loudly instead of
    # leaving half a deployment on disk.
    services = pack_services(manifest["packs"])

    out_dir = Path(args.out) if args.out else Path(args.root) / "rendered"
    out_dir.mkdir(parents=True, exist_ok=True)

    env_path = out_dir / "env"
    env = read_env_file(env_path)
    generated = not env

    env.setdefault("OI_ROOT", args.root)
    env.setdefault("POSTGRES_USER", "oicore")
    env.setdefault("POSTGRES_DB", "oicore")
    env.setdefault("POSTGRES_PASSWORD", secrets.token_urlsafe(24))
    env.setdefault("LITELLM_MASTER_KEY", "sk-" + secrets.token_urlsafe(32))
    # The assertion signing key. One secret shared by the API, which mints statements, and the gateway, which
    # verifies them. It is deliberately not a model credential: whoever holds it can speak as a role for five
    # minutes and cannot call a model with it.
    env.setdefault("OI_ASSERTION_SECRET", secrets.token_urlsafe(48))
    env.setdefault("GATEWAY_PORT", "4000")
    # The audit tool runs as this user so exported evidence belongs to the operator rather than
    # to root. Files a client is meant to keep should not need sudo to read or copy.
    env.setdefault("HOST_UID", str(os.getuid()))
    env.setdefault("HOST_GID", str(os.getgid()))

    local_models = [e.get("local", "") for e in (manifest.get("models") or {}).values() if e]
    # The local lane's settings exist only where there is a local lane. A cloud build that
    # rendered MODEL_FILE would leave whoever reads this file wondering which lane answers.
    if any(local_models):
        env.setdefault("MODEL_FILE", os.environ.get("MODEL_FILE", "Qwen3-1.7B-Q4_K_M.gguf"))
        env.setdefault("MODEL_CTX", str(DEFAULT_CTX))
        env.setdefault("MODEL_THREADS", str(os.cpu_count() or 2))
    else:
        for key in ("MODEL_FILE", "MODEL_CTX", "MODEL_THREADS"):
            env.pop(key, None)
    # Bedrock needs a Region and the gateway reads it from the environment. A host-level fact with
    # a default, the same pattern as MODEL_FILE. It belongs in the manifest once a client's
    # contract names a Region, which is a C7 decision; the manifest has no field for one today.
    env.setdefault("AWS_REGION", os.environ.get("AWS_REGION", "us-east-1"))
    env.setdefault("AWS_DEFAULT_REGION", env["AWS_REGION"])

    # Content modes reach the audit tool through the environment, because the tool runs in its own
    # container with the deployment root mounted rather than the repository. Every class is written
    # explicitly, so the tool reads a decision rather than applying a fallback of its own.
    content = manifest.get("content") or {}
    default_mode = content.get("default", "hash")
    declared = content.get("classes") or {}
    env["CONTENT_DEFAULT"] = default_mode
    for data_class in sorted(DATA_CLASSES):
        env[f"CONTENT_MODE_{data_class.upper()}"] = declared.get(data_class, default_mode)

    # What a denied user is told. A client's declared policy, so the renderer writes it explicitly and
    # the retriever reads it rather than holding a default of its own. Written in every case, including
    # the default, so a deployment reads a decision rather than applying a fallback.
    access = manifest.get("access") or {}
    env["ACCESS_DENIAL_MODE"] = str(access.get("denial", "denied"))

    # The egress policy. Written explicitly for every manifest, including the empty case, because the gate
    # reads an unset variable as an empty permission set and a manifest that declares nothing must render to
    # something rather than to an absence it relies on.
    egress = access.get("egress") or {}
    env["FRONTIER_CLASSES"] = ",".join(egress.get("frontier_classes") or [])
    env["EGRESS_REDACTION"] = str(egress.get("redaction", "on"))

    # Whether this deployment is running on a manifest that says one thing in the lane block and another in
    # the access block, rendered into the environment so the divergence is discoverable ON THE BOX and not
    # only to whoever last ran the validator. The two fields carry the same enum, the gate reads the access
    # one, and nothing else reconciles them; a client asking "why did that get sent" should be answerable by
    # reading the deployment's own env file rather than by finding the manifest and diffing two lists.
    #
    # Absent rather than empty when there is no divergence, so `grep MANIFEST_DIVERGENCE rendered/env` is a
    # check that either finds something or finds nothing, with no third case to interpret.
    from validate_manifests import divergences  # noqa: PLC0415
    found = divergences(manifest)
    if found:
        env["MANIFEST_DIVERGENCE"] = " | ".join(found)
    else:
        env.pop("MANIFEST_DIVERGENCE", None)

    # Offsite delivery of audit artifacts. Absent means this build keeps everything on the box,
    # which is a valid office shape and the default. Set and removed explicitly rather than
    # defaulted, so a manifest without a destination cannot inherit one from an earlier render.
    offsite = manifest.get("offsite") or {}
    if offsite:
        env["AUDIT_SHIP_BUCKET"] = str(offsite["bucket"])
        env["AUDIT_SHIP_ARTIFACTS"] = ",".join(offsite.get("ship") or [])
        env["AUDIT_SHIP_REGION"] = str(offsite.get("region") or env["AWS_REGION"])
    else:
        for key in ("AUDIT_SHIP_BUCKET", "AUDIT_SHIP_ARTIFACTS", "AUDIT_SHIP_REGION"):
            env.pop(key, None)

    models = list(dict.fromkeys(m for m in local_models if m))
    env["MODEL_ROLES"] = ",".join(
        f"{role}={entry.get('local','')}" for role, entry in sorted((manifest.get("models") or {}).items())
    )
    env["DEPLOYMENT_ID"] = manifest["client"]["deployment_id"]
    env["PROFILE"] = manifest["profile"]
    # One compose file serves both builds. The local lane's containers sit behind the "office"
    # profile, so a cloud deployment starts the gateway and the database without them. Set
    # explicitly rather than defaulted, so rendering a cloud manifest over an office env file
    # cannot leave a stale profile behind and start a container the build has no model for.
    if manifest["profile"] == "cloud":
        env.pop("COMPOSE_PROFILES", None)
    else:
        env["COMPOSE_PROFILES"] = "office"

    env_path.write_text("".join(f"{k}={v}\n" for k, v in sorted(env.items())))
    env_path.chmod(0o600)

    gateway = build_litellm_config(manifest, env)
    (out_dir / "litellm.yaml").write_text(
        "# Generated from the manifest by scripts/render_manifest.py. Do not edit by hand.\n"
        + yaml.safe_dump(gateway, sort_keys=False)
    )
    (out_dir / "services.txt").write_text(
        "# Services declared by the chosen packs. Informational until the installer consumes it.\n"
        + "".join(f"{s}\n" for s in services)
    )
    (out_dir / "manifest.json").write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")

    # The signing key as a file, mode 600, because the gateway hook reads a file rather than an environment
    # variable: an environment variable is visible to `docker inspect` and to any process that can read /proc.
    key_path = out_dir / "assertion.key"
    if not key_path.exists():
        key_path.write_text(env["OI_ASSERTION_SECRET"] + "\n")
    key_path.chmod(0o600)

    registry = build_access_registry(manifest)
    (out_dir / "access.json").write_text(json.dumps(registry, indent=2, sort_keys=True) + "\n")

    print(f"rendered {manifest_path.name} -> {out_dir}")
    print(f"  profile      {manifest['profile']}")
    print(f"  packs        {', '.join(manifest['packs'])}")
    print(f"  local models {', '.join(models) if models else '(none)'}")
    print(f"  gateway      {len(gateway['model_list'])} model entries")
    print(f"  access       {'declared: ' + str(len(registry['groups'])) + ' group(s), ' + str(len(registry['users'])) + ' user(s)' if registry['declared'] else 'built-in: two folder roles, no users'}")
    print(f"  secrets      {'generated' if generated else 'reused from the existing env file'}")
    print(f"  env file     {env_path} (mode 600)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
