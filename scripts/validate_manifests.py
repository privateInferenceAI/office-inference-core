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
    """Pack ids from the catalog.

    Parses the file properly rather than scanning lines. An earlier version grepped for
    "  - id:" lines, which meant a catalog that was not valid YAML still validated
    manifests happily. The pack catalog is a contract, so it gets parsed like one.
    """
    import yaml  # imported here so the module still imports on a machine without it

    try:
        catalog = yaml.safe_load(CATALOG_PATH.read_text()) or {}
    except yaml.YAMLError as exc:
        raise SystemExit(f"{CATALOG_PATH} is not valid YAML:\n{exc}") from None
    packs = {str(p["id"]) for p in catalog.get("packs", []) if p.get("id")}
    if not packs:
        raise SystemExit(f"no packs found in {CATALOG_PATH}: is the catalog valid YAML?")
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

    # Residency. Nearly every frontier model on Bedrock is inference-profile only, and a global
    # profile can route a prompt anywhere AWS operates. The schema carried data_residency and
    # nothing read it; these rules read it.
    unrestricted = ("none", "contractual")
    compliance_for_residency = (manifest.get("compliance") or {}).get("profile", "none")
    for role, entry in (manifest.get("models") or {}).items():
        frontier = ((entry or {}).get("frontier") or "")
        if not frontier.startswith("bedrock/"):
            continue
        model_id = frontier.split("/", 1)[1]
        if model_id.startswith("global.") and compliance_for_residency not in unrestricted:
            problems.append(
                f"models.{role}: a global inference profile ({model_id}) can route a prompt to any "
                f"Region, which is not acceptable with compliance profile "
                f"{compliance_for_residency!r}. Use a geographic profile such as us.")
        if model_id.startswith("anthropic.claude-fable"):
            problems.append(
                f"models.{role}: Fable models permit only aws_review or provider_data_share retention, "
                "which means instructing AWS to retain inputs and outputs for human review")
    # Only where inference is remote. An office build's residency is the client's building, and
    # demanding a Region there is noise.
    if compliance_for_residency not in unrestricted and manifest.get("profile") in ("cloud", "hybrid"):
        residency = (manifest.get("compliance") or {}).get("data_residency") or []
        if not residency:
            problems.append(
                f"compliance profile {compliance_for_residency!r} on a remote-inference profile should "
                "name its data residency, because it decides which model forms are allowed")

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
