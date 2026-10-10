#!/usr/bin/env python3
"""Render a client manifest into runnable configuration.

The manifest is what a client buys. This turns it into the files the deployment actually
reads, so configuration is never hand-edited on a server.

Writes, into the output directory:

    env             secrets and settings for compose, mode 600, never committed
    litellm.yaml    the gateway's model list, built from the manifest's model roles
    services.txt    the services the chosen packs declare (informational for now)
    manifest.json   the normalized manifest, for drift comparison later

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

DEFAULT_CTX = 8192
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
    """Union of the services declared by the chosen packs."""
    catalog = yaml.safe_load((ROOT / "packs" / "catalog.yaml").read_text())
    by_id = {p["id"]: p for p in catalog.get("packs", [])}
    services: list[str] = []
    for pack_id in pack_ids:
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
    env.setdefault("GATEWAY_PORT", "4000")
    # The audit tool runs as this user so exported evidence belongs to the operator rather than
    # to root. Files a client is meant to keep should not need sudo to read or copy.
    env.setdefault("HOST_UID", str(os.getuid()))
    env.setdefault("HOST_GID", str(os.getgid()))

    local_models = [e.get("local", "") for e in (manifest.get("models") or {}).values() if e]
    primary = next((m for m in local_models if m), "")
    env.setdefault("MODEL_FILE", os.environ.get("MODEL_FILE", "Qwen3-1.7B-Q4_K_M.gguf"))
    env.setdefault("MODEL_CTX", str(DEFAULT_CTX))
    env.setdefault("MODEL_THREADS", str(os.cpu_count() or 2))

    models = list(dict.fromkeys(m for m in local_models if m))
    env["MODEL_ROLES"] = ",".join(
        f"{role}={entry.get('local','')}" for role, entry in sorted((manifest.get("models") or {}).items())
    )
    env["DEPLOYMENT_ID"] = manifest["client"]["deployment_id"]
    env["PROFILE"] = manifest["profile"]

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

    print(f"rendered {manifest_path.name} -> {out_dir}")
    print(f"  profile      {manifest['profile']}")
    print(f"  packs        {', '.join(manifest['packs'])}")
    print(f"  local models {', '.join(models) if models else '(none)'}")
    print(f"  gateway      {len(gateway['model_list'])} model entries")
    print(f"  secrets      {'generated' if generated else 'reused from the existing env file'}")
    print(f"  env file     {env_path} (mode 600)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
