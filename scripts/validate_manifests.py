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
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCHEMA_PATH = ROOT / "schemas" / "manifest.schema.json"
CATALOG_PATH = ROOT / "packs" / "catalog.yaml"
REQUIRED_MODEL_ROLES = ("analyst", "front_desk", "reader")
PROFILES = ("cloud", "office", "hybrid")
# What may leave the machine under offsite.ship. Deliberately closed: no documents, no prompts.
OFFSITE_ARTIFACTS = frozenset({"anchors", "exports", "backups"})


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

    # The two compliance profiles that make no regulated-data claim. Read by the account-owner
    # rule below and by the residency rules after it.
    unrestricted = ("none", "contractual")
    compliance_for_residency = (manifest.get("compliance") or {}).get("profile", "none")

    frontier = (manifest.get("lanes") or {}).get("frontier") or {}
    if frontier.get("enabled"):
        if profile == "office":
            problems.append("frontier lane enabled on the office profile: requires the hybrid cloud leg")
        if not frontier.get("provider"):
            problems.append("frontier lane enabled without a provider")
        # The schema has always allowed office_inference, for the hosted tier and for our own
        # development boxes. Nothing read it: this rule demanded "client" and so refused both.
        owner = frontier.get("account_owner")
        if owner not in ("client", "office_inference"):
            problems.append(
                "frontier lane needs account_owner: client, or office_inference for the hosted "
                "tier and our own development boxes")
        elif owner == "office_inference" and compliance_for_residency not in unrestricted:
            problems.append(
                f"frontier lane in our account under compliance profile "
                f"{compliance_for_residency!r}: the hosted tier is standard-only, so a "
                "compliance claim belongs in the client's own account")
        allowed = frontier.get("data_classes_allowed") or []
        if not allowed:
            problems.append("frontier lane enabled with no data classes allowed: that allows everything")
        if profile == "cloud" and frontier.get("redaction") is False and "regulated" in allowed:
            problems.append("regulated data allowed through the frontier lane without redaction")

    # Residency. Nearly every frontier model on Bedrock is inference-profile only, and a global
    # profile can route a prompt anywhere AWS operates. The schema carried data_residency and
    # nothing read it; these rules read it.
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

    # Budgets. Required wherever a call costs money, because an uncapped deployment is the runaway
    # loop that spends the client's balance, and spend-at-cap is one of the five alarms in the brief.
    # A local-only build costs nothing per call, so a ceiling there would be decoration.
    budgets = manifest.get("budgets") or {}
    declared_budgets = budgets.get("roles") or {}
    if budgets:
        period = budgets.get("period_days", 30)
        if not isinstance(period, int) or period <= 0:
            problems.append(f"budgets.period_days must be a positive whole number, got {period!r}")
        unknown_roles = sorted(set(declared_budgets) - set(models))
        if unknown_roles:
            problems.append(f"budgets.roles names {unknown_roles}, which are not model roles in this "
                            f"manifest. Known roles: {sorted(models)}")
        for role, amount in sorted(declared_budgets.items()):
            if not isinstance(amount, (int, float)) or isinstance(amount, bool) or amount <= 0:
                problems.append(f"budgets.roles.{role} must be a positive amount, got {amount!r}")
    if ((manifest.get("lanes") or {}).get("frontier") or {}).get("enabled"):
        unbudgeted = sorted(set(models) - set(declared_budgets))
        if unbudgeted:
            problems.append(
                f"the frontier lane is enabled and {unbudgeted} have no budget. A remote call costs "
                "money, and an uncapped role is how a loop empties an account")

    # Offsite delivery. Evidence and backups may leave the building when the manifest says so, and
    # nothing else may. The list is an allowlist rather than a set of suggestions, because
    # "nothing leaves the building" is the office offering's central promise and a promise that can
    # be switched off by editing a list is not one.
    #
    # An AWS account here does not make a build hybrid. Hybrid means both inference lanes; this is
    # the offsite axis, and the two are independent. See docs/audit-evidence.md.
    offsite = manifest.get("offsite") or {}
    if offsite:
        provider = offsite.get("provider")
        if provider != "s3":
            problems.append(f"offsite.provider must be s3, got {provider!r}")
        owner = offsite.get("account_owner")
        if owner not in ("client", "office_inference"):
            problems.append("offsite.account_owner is required: client, or office_inference for our "
                            "own deployments")
        bucket = str(offsite.get("bucket") or "")
        if not bucket:
            problems.append("offsite.bucket is required, because the destination is configuration "
                            "rather than something typed on a server")
        elif not re.fullmatch(r"[a-z0-9][a-z0-9.-]{1,61}[a-z0-9]", bucket):
            problems.append(f"offsite.bucket {bucket!r} is not a valid S3 bucket name")
        ship = offsite.get("ship") or []
        if not ship:
            problems.append("offsite.ship must name what may leave, using "
                            f"{sorted(OFFSITE_ARTIFACTS)}")
        unknown = sorted(set(ship) - OFFSITE_ARTIFACTS)
        if unknown:
            problems.append(
                f"offsite.ship names {unknown}, which may not leave the building. The only accepted "
                f"values are {sorted(OFFSITE_ARTIFACTS)}: documents and prompts are never shipped")
        if cprofile == "itar-cui":
            problems.append("itar-cui refuses offsite delivery: that profile exists so no third "
                            "party, including a cloud provider, can reach the material")

    if cprofile not in ("none", "contractual", "hipaa", "glba", "sec-17a4", "itar-cui", "ferpa", "pci"):
        problems.append(f"unknown compliance profile: {cprofile!r}")
    if cprofile not in ("none", "contractual"):
        if not compliance.get("baa_with_office_inference") and profile != "cloud":
            problems.append(f"compliance profile {cprofile!r} on a hardware build requires a signed agreement with us")
        # 17 CFR 240.17a-4(a) requires six years, the first two readily accessible. A one-year floor
        # was six times too short for a broker-dealer, and that is the kind of gap an examiner finds
        # rather than a client.
        floor = 365 * 6 if cprofile == "sec-17a4" else 365
        if compliance.get("audit_retention_days", 0) < floor:
            problems.append(f"compliance profile {cprofile!r} should retain audit records at least "
                            f"{floor} days (currently {compliance.get('audit_retention_days', 0)})")
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
