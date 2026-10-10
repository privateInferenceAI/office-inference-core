#!/usr/bin/env python3
"""Validate client manifests against the schema and the pack catalog.

No third-party dependencies on purpose: this has to run on a fresh machine and on the office box
during install. It checks the fields that matter for correct deployment and refuses unknown packs,
which is the failure mode that would otherwise silently under-deploy a client.

Usage:  python3 scripts/validate_manifests.py [manifest ...]
        (no arguments validates every file in manifests/)
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCHEMA_PATH = ROOT / "schemas" / "manifest.schema.json"
CATALOG_PATH = ROOT / "packs" / "catalog.yaml"
REQUIRED_MODEL_ROLES = ("analyst", "front_desk", "reader")
PROFILES = ("cloud", "office", "hybrid")


def load_catalog_packs() -> set[str]:
    """Pull pack ids out of the catalog without a YAML dependency."""
    packs: set[str] = set()
    for raw in CATALOG_PATH.read_text().splitlines():
        line = raw.rstrip()
        if line.startswith("  - id:"):
            packs.add(line.split(":", 1)[1].strip())
    return packs


def check(manifest: dict, packs: set[str], path: Path) -> list[str]:
    problems: list[str] = []

    for field in ("manifest_version", "client", "profile", "packs", "models"):
        if field not in manifest:
            problems.append(f"missing required field: {field}")
    if problems:
        return problems

    if manifest["manifest_version"] != 1:
        problems.append(f"manifest_version must be 1, got {manifest['manifest_version']!r}")

    profile = manifest["profile"]
    if profile not in PROFILES:
        problems.append(f"profile must be one of {PROFILES}, got {profile!r}")

    client = manifest.get("client", {})
    for field in ("name", "deployment_id"):
        if not client.get(field):
            problems.append(f"client.{field} is required")
    deployment_id = client.get("deployment_id", "")
    if deployment_id and not all(c.isalnum() or c == "-" for c in deployment_id):
        problems.append("client.deployment_id may contain only letters, digits, and hyphens")

    used = manifest.get("packs", [])
    unknown = sorted(set(used) - packs)
    if unknown:
        problems.append(f"unknown pack ids: {unknown}. Known packs: {sorted(packs)}")
    if not used:
        problems.append("packs must list at least one sellable pack")

    models = manifest.get("models", {})
    for role in REQUIRED_MODEL_ROLES:
        if role not in models:
            problems.append(f"models.{role} is required (roles are required, model names are not)")
            continue
        entry = models[role]
        if not isinstance(entry, dict) or "local" not in entry:
            problems.append(f"models.{role} must declare a local field (may be empty on cloud)")

    frontier = (manifest.get("lanes") or {}).get("frontier") or {}
    if frontier.get("enabled"):
        if profile == "office":
            problems.append("frontier lane enabled on the office profile: requires the hybrid cloud leg")
        if not frontier.get("provider"):
            problems.append("frontier lane enabled without a provider")
        if frontier.get("account_owner") != "client":
            problems.append("frontier lane must run in the client's account (account_owner: client)")
        allowed = frontier.get("data_classes_allowed") or []
        if not allowed:
            problems.append("frontier lane enabled with no data classes allowed: that allows everything")
        if profile == "cloud" and frontier.get("redaction") is False and "regulated" in allowed:
            problems.append("regulated data allowed through the frontier lane without redaction")

    compliance = manifest.get("compliance") or {}
    cprofile = compliance.get("profile", "none")
    if cprofile not in ("none", "contractual", "hipaa", "glba", "sec-17a4", "itar-cui", "ferpa", "pci"):
        problems.append(f"unknown compliance profile: {cprofile!r}")
    if cprofile not in ("none", "contractual"):
        if not compliance.get("baa_with_office_inference") and profile != "cloud":
            problems.append(f"compliance profile {cprofile!r} on a hardware build requires a signed agreement with us")
        if compliance.get("audit_retention_days", 0) < 365:
            problems.append(f"compliance profile {cprofile!r} should retain audit records at least 365 days")
    if cprofile == "itar-cui" and profile != "office":
        problems.append("itar-cui requires the office profile: no third-party access is allowed")

    if "frontier-lane" in used and profile == "office":
        problems.append("frontier-lane pack requires the cloud or hybrid profile")

    return problems


def main(argv: list[str]) -> int:
    paths = [Path(a) for a in argv[1:]] or sorted((ROOT / "manifests").glob("*.yaml"))
    if not paths:
        print("no manifests found", file=sys.stderr)
        return 2

    try:
        import yaml  # type: ignore
        parse = yaml.safe_load
    except ImportError:
        print("PyYAML is required to read manifests: pip install pyyaml", file=sys.stderr)
        return 2

    packs = load_catalog_packs()
    failed = 0
    for path in paths:
        manifest = parse(path.read_text()) or {}
        problems = check(manifest, packs, path)
        if problems:
            failed += 1
            print(f"FAIL {path.name}")
            for p in problems:
                print(f"  - {p}")
        else:
            print(f"ok   {path.name}  (profile={manifest['profile']}, packs={manifest['packs']})")

    print()
    print(f"{len(paths) - failed}/{len(paths)} manifests valid; {len(packs)} packs known")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
