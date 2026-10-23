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
# What the record may keep for a piece of content.
CONTENT_MODES = frozenset({"hash", "redacted", "full"})
DATA_CLASSES = frozenset({"public", "internal", "confidential", "regulated"})

from manifest_fields import MAX_FALLBACKS, fallback_chain, is_region, split_fallback  # noqa: E402

# The data classes in order of sensitivity. This exists so a divergence can be reported as a DIRECTION
# rather than as two lists that differ, which is the difference between a warning somebody reads and one
# they learn to ignore. `regulated` is most sensitive, `public` least.
DATA_CLASS_ORDER = ("public", "internal", "confidential", "regulated")


def _sensitivity(classes) -> int:
    """How far up the sensitivity order a set of classes reaches. -1 for none."""
    ranked = [DATA_CLASS_ORDER.index(str(c)) for c in (classes or []) if str(c) in DATA_CLASS_ORDER]
    return max(ranked) if ranked else -1


def divergences(manifest: dict) -> list[str]:
    """Where declaration and enforcement disagree about the same policy, as warnings.

    **Why this exists and why it warns rather than refuses.** Two fields carry the same enum and the same
    meaning: `lanes.frontier.data_classes_allowed` and `access.egress.frontier_classes`. The validator reads
    the first and `services/egress/lane.py` reads the second, which is rendered from it. A client's manifest
    can therefore declare one policy in one place and enforce another in the other, pass validation, and
    behave according to whichever field the reader happened to look at.

    Both fields stay, because every shipped manifest before C2 declared only the lane one and removing it
    would make those manifests invalid rather than merely stale. What this adds is that a disagreement is
    **visible** instead of silent. It is not an error for two reasons: a manifest that declares one field and
    not the other is not contradictory, and a manifest that is genuinely contradictory today is working, so
    failing it would break a deployment on upgrade — which is a worse outcome than a warning nobody reads.

    The direction matters more than the fact. `access.egress` is what leaves, so:

      access.egress wider than the lane   -> material leaves that the declaration does not permit.
                                             This is the dangerous direction and the message says so.
      access.egress narrower              -> nothing leaves that was not permitted, which is safe and
                                             misleading rather than unsafe.

    The same pairing applies to redaction, and it is already divergent in a shipped manifest: the lane block
    says `redaction: false` while the egress block says `on`, and the renderer's default has been deciding.
    """
    warnings: list[str] = []
    lane = (manifest.get("lanes") or {}).get("frontier") or {}
    egress = (manifest.get("access") or {}).get("egress") or {}

    lane_classes = list(lane.get("data_classes_allowed") or [])
    egress_classes = list(egress.get("frontier_classes") or [])

    if not lane_classes or not egress_classes:
        return warnings

    def show(classes) -> str:
        return "[" + ", ".join(str(c) for c in classes) + "]"

    missing_from_egress = [c for c in lane_classes if c not in egress_classes]
    extra_in_egress = [c for c in egress_classes if c not in lane_classes]
    if missing_from_egress or extra_in_egress:
        header = (
            "lanes.frontier.data_classes_allowed and access.egress.frontier_classes disagree, and "
            "access.egress is the one that governs egress: "
            f"declared {show(lane_classes)}, enforced {show(egress_classes)}."
        )
        if extra_in_egress:
            warnings.append(
                header + f" **{show(extra_in_egress)} may leave for a frontier model although the lane "
                "block does not permit it.** Set both fields to the classes the client's contract allows, "
                "and read the contract before changing either: this is a divergence a person has to "
                "resolve, not one the renderer should guess at.")
        else:
            warnings.append(
                header + f" The gate is narrower, so nothing leaves that the declaration forbids — safe, "
                f"but the manifest reads as though {show(missing_from_egress)} may be sent when it may "
                "not. Set both fields to the same list.")

    lane_redaction = lane.get("redaction")
    egress_redaction = egress.get("redaction")
    if lane_redaction is not None and egress_redaction is not None:
        def truthy(value) -> bool:
            return str(value).strip().lower() in ("1", "true", "yes", "on")
        if truthy(lane_redaction) != truthy(egress_redaction):
            warnings.append(
                "lanes.frontier.redaction and access.egress.redaction disagree, and access.egress is the "
                f"one the egress gate reads: declared {lane_redaction!r}, enforced {egress_redaction!r}. "
                "Nothing downstream reconciles them, so the rendered value decides and the lane block is "
                "decoration. Set both to what the client asked for.")

    return warnings


def load_catalog_requires() -> dict[str, list[str]]:
    """The `requires` edges between packs, read from the same catalog as the ids."""
    import yaml

    catalog = yaml.safe_load(CATALOG_PATH.read_text()) or {}
    return {str(p["id"]): [str(r) for r in (p.get("requires") or [])]
            for p in catalog.get("packs", []) if p.get("id")}


def resolve_packs(used: list[str], known: set[str]) -> set[str]:
    """The closure of the chosen packs under `requires`, which is what actually gets deployed."""
    requires = load_catalog_requires()
    resolved: set[str] = set()
    queue = [p for p in used if p in known]
    while queue:
        pack = queue.pop()
        if pack in resolved:
            continue
        resolved.add(pack)
        queue.extend(requires.get(pack, []))
    return resolved


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


def check_access(manifest: dict) -> list[str]:
    """The declared permission model, checked before it is rendered rather than at a request.

    SEC-ACL-002's verify line is about retrieval, so a mistake here is invisible until somebody reads a
    document they should not. Every check below turns a class of that into a refusal at render time, when the
    person reading the message is holding the manifest.

    **The registry fields are all or nothing.** A manifest that declares groups and forgets roots, or users
    and forgets groups, is not a manifest with defaults: it is one where the author meant something the
    renderer would have to guess. Guessing is how a deployment ends up enforcing a model nobody wrote.
    """
    problems: list[str] = []
    block = manifest.get("access") or {}
    registry_fields = ("roles", "roots", "groups", "users")
    declared = [k for k in registry_fields if k in block]

    # denial and egress are not part of the registry, so a manifest carrying only those has declared no
    # permission model and keeps the built-in roles.
    if not declared:
        return problems

    for field in ("roots", "groups", "users"):
        if field not in block:
            problems.append(
                f"access.{field} is missing while the rest of the permission model is declared. The four "
                f"registry fields are all or nothing, because a renderer filling in the gap would be "
                f"enforcing a model the manifest does not state. Declared so far: {sorted(declared)}")

    roots = block.get("roots")
    groups = block.get("groups") or {}
    users = block.get("users") or []
    roles = block.get("roles") or {}

    if roots is not None and not roots:
        problems.append("access.roots is empty, so no document label could ever be reached")

    for name, rts in (roles or {}).items():
        if not rts and not groups:
            problems.append(
                f"access.roles.{name} permits no roots and no groups are declared, so nothing a session "
                f"of that role does could reach a document")

    def roots_of(spec):
        """A group's roots from either the mapping form or the older bare list."""
        if isinstance(spec, dict):
            return spec.get("roots") or []
        return spec or []

    declared_roots = set(roots or [])
    for name, spec in (groups or {}).items():
        rts = roots_of(spec)
        if not rts:
            problems.append(
                f"access.groups.{name} permits no roots, which is a configuration error rather than a "
                f"group that sees nothing")
        unknown = sorted(set(rts or []) - declared_roots) if declared_roots else []
        if unknown:
            problems.append(
                f"access.groups.{name} names root(s) {unknown} that access.roots does not declare. Either "
                f"add them to access.roots or correct the name: a root nothing declares is a label no "
                f"document carries")

    if not groups and not any((roles or {}).values()):
        problems.append(
            "the permission model declares users but neither groups nor roles that carry roots, so every "
            "subject would resolve to no access at all")

    seen: set[str] = set()
    for index, entry in enumerate(users):
        entry = entry or {}
        subject = entry.get("subject", "")
        if not subject:
            problems.append(f"access.users[{index}] has no subject")
            continue
        if subject in seen:
            problems.append(f"access.users[{index}] declares {subject!r} a second time, and one of the "
                            f"two would be dead")
        seen.add(subject)
        entry_groups = entry.get("groups") or []
        unknown_groups = sorted(set(entry_groups) - set(groups))
        if unknown_groups:
            problems.append(
                f"access.users[{index}] ({subject}) belongs to group(s) {unknown_groups} that "
                f"access.groups does not declare. Known groups: {sorted(groups)}")
        if not entry_groups:
            problems.append(
                f"access.users[{index}] ({subject}) belongs to no group, so it would resolve to no "
                f"access")

    # A root no role and no group carries is a document label nothing can reach. Reported rather than
    # refused, so a deployment mid-migration can declare a root before the group that will use it.
    carried = set()
    for spec in list((groups or {}).values()) + [v for v in (roles or {}).values() if v]:
        carried |= set(roots_of(spec))

    unreachable = sorted(declared_roots - carried)
    if unreachable:
        problems.append(
            f"access.roots declares {unreachable}, which no role and no group carries, so a document "
            f"labelled with one is reachable by nobody")

    return problems


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

    problems.extend(check_access(manifest))

    used = manifest.get("packs", [])
    unknown = sorted(set(used) - packs)
    if unknown:
        problems.append(f"unknown pack ids: {unknown}. Known packs: {sorted(packs)}")
    if not used:
        problems.append("packs must list at least one sellable pack")

    # **The workspace is a cloud product.** Its sandbox, egress service and artifact store are the parts an
    # office build does not have, and shipping the definition there would offer a task the deployment cannot
    # run. The refusal is at the pack, so a copied office manifest cannot smuggle it in one definition later.
    if "workspace" in used and profile != "cloud":
        problems.append(f"the workspace pack is cloud and hosted only, not {profile!r}")

    # **One runtime, and the manifest can say so only by declaring one.** A second runtime means a second
    # policy, a second audit path and two places for a bug to hide; the field is here so a copied manifest
    # that adds one is refused rather than deployed.
    agentic = manifest.get("agentic") or {}
    runtime_count = agentic.get("runtime_count", 1)
    if runtime_count != 1:
        problems.append(
            f"agentic.runtime_count is {runtime_count!r}; this product runs exactly one agent runtime per "
            f"deployment. A second runtime is a second policy and a second audit path")

    # The catalog's `requires` edges were documentation until now. Resolve them, because a pack list
    # that never reaches core renders a stack with no gateway, no database and no audit sink, and the
    # manifests here rely on core being implied rather than listed.
    resolved = resolve_packs(used, packs)
    if used and "core" not in resolved:
        problems.append(f"packs {used} do not require 'core', so this deployment would have no "
                        f"gateway, database or audit sink. Resolved to {sorted(resolved)}")

    models = manifest.get("models", {})
    for role in REQUIRED_MODEL_ROLES:
        if role not in models:
            problems.append(f"models.{role} is required (roles are required, model names are not)")
            continue
        entry = models[role]
        if not isinstance(entry, dict) or "local" not in entry:
            problems.append(f"models.{role} must declare a local field (may be empty on cloud)")

    # **A fallback is a remote model, so only a cloud build may declare one.** It exists so a provider
    # failure answers from a second deployment instead of degrading the question. On an office or hybrid
    # build the primary may be the local lane, and failing over to a remote model would send a client's
    # content off the box without a declared egress decision. The rule is profile-level rather than
    # per-role because a role that is frontier-only today can be given a local model tomorrow, and the
    # permission would change silently.
    for role, entry in (models or {}).items():
        if not isinstance(entry, dict):
            continue
        raw = entry.get("fallback")
        if raw is None:
            continue
        if not isinstance(raw, (str, list)) or not fallback_chain(entry):
            problems.append(
                f"models.{role}.fallback must be a non-empty model id, a list of them, or absent")
            continue
        if profile != "cloud":
            problems.append(
                f"models.{role}.fallback is declared on a {profile!r} build, but a fallback is a remote "
                f"model and a local primary failing over to it would be an undeclared egress path. "
                f"Fallbacks are cloud-only")
        chain = fallback_chain(entry)
        if len(chain) > MAX_FALLBACKS:
            problems.append(
                f"models.{role}.fallback declares {len(chain)} entries; the gateway chain is bounded "
                f"at {MAX_FALLBACKS} because each entry is a provider call in the failure path")
        primary = (entry.get("frontier") or "").strip()
        seen: set[str] = set()
        for index, item in enumerate(chain):
            label = f"models.{role}.fallback[{index}]"
            model, region = split_fallback(item)
            if not model.startswith("bedrock/"):
                problems.append(f"{label} must name a Bedrock model as bedrock/<id>, got {item!r}")
                continue
            if region and not is_region(region):
                problems.append(
                    f"{label} names a region {region!r} that is not an AWS Region code; "
                    f"write it like us-east-2, or omit the @region suffix")
            if primary and model == primary and not region:
                problems.append(
                    f"{label} repeats the primary model in the same Region, so a failover to it would "
                    f"answer nothing; give it a region or choose another model")
            if item in seen:
                problems.append(f"{label} repeats an earlier entry in the same chain")
            seen.add(item)

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
            # The message used to read "that allows everything", which was true when this was the field the
            # gate read. It is not: `services/egress/lane.py` reads `FRONTIER_CLASSES`, rendered from
            # `access.egress.frontier_classes`. So an empty list here cannot allow anything — it is an intent
            # check, and the dangerous case is the reverse one, which is why the rule below exists.
            problems.append(
                "frontier lane enabled with an empty data_classes_allowed: the lane declares no intent about "
                "which classes may leave. Note the gate reads access.egress.frontier_classes, so this field "
                "alone does not govern egress")
        if profile == "cloud" and frontier.get("redaction") is False and "regulated" in allowed:
            problems.append("regulated data allowed through the frontier lane without redaction")

        # The rule that closes the silent failure. The gate renders `FRONTIER_CLASSES` from
        # `access.egress.frontier_classes`, and an absent block renders an EMPTY string, which the gate reads as
        # a permission set with nothing in it and refuses every request. A manifest that says three classes are
        # allowed then sends nothing, validation passes, and the first symptom is a model call that will not go
        # out. Three of the shipped example manifests had exactly this shape.
        egress = (manifest.get("access") or {}).get("egress") or {}
        # **`access.egress` is the authoritative declaration of what may leave, so an empty one is a
        # configuration error rather than a policy.** The two shapes it arrives in are different mistakes and
        # are reported differently, because the fix for each is different:
        #
        #   the block or the field is ABSENT   the manifest was written against the older table, where
        #                                      `lanes.frontier.data_classes_allowed` was the only place to say
        #                                      this. Nothing has been decided about egress.
        #   the field is present and EMPTY     somebody wrote the policy out and left it permitting nothing,
        #                                      while the lane above it says classes may go. The two disagree
        #                                      and the empty one governs, so the manifest reads as permissive
        #                                      and behaves as closed.
        #
        # Both refuse every frontier call, and both are errors rather than warnings: a warning beside a valid
        # manifest is for a divergence a person should look at, and this is a deployment that cannot send
        # anything at all. The reader is told to check the manifest either way, which is the point.
        if "frontier_classes" not in egress:
            problems.append(
                "the frontier lane is enabled and access.egress declares nothing. The gate reads "
                "access.egress.frontier_classes, renders it to FRONTIER_CLASSES, and refuses every call when "
                "it is absent -- so this deployment would send nothing while lanes.frontier."
                "data_classes_allowed appears to permit it. Declare the classes you intend, or switch the "
                "lane off. **Check the manifest against the client's contract before choosing**: the lane "
                "block and this field are two statements of one policy, and only this one is enforced")
        elif not (egress.get("frontier_classes") or []):
            problems.append(
                "the frontier lane is enabled and access.egress.frontier_classes is empty. An empty list is "
                "not 'unset': it is a decision that nothing may leave, and it overrides the classes named in "
                "lanes.frontier.data_classes_allowed. **That is almost never what was intended**, because the "
                "lane above says classes may go and this field makes the deployment refuse every call while "
                "reading as permissive. Set it to the classes the client's contract allows, or set "
                "lanes.frontier.enabled to false and let the empty list mean what it says")

    # A cloud build has no local lane, so switching the frontier lane off leaves it with no models at
    # all. The renderer used to emit frontier entries regardless on a cloud profile, which quietly
    # ignored the flag; the shape is refused now instead of being ignored.
    if profile == "cloud" and not frontier.get("enabled"):
        problems.append("profile 'cloud' needs the frontier lane enabled: a cloud build has no local "
                        "lane, so switching it off leaves the deployment with no models")

    # Residency. Nearly every frontier model on Bedrock is inference-profile only, and a global
    # profile can route a prompt anywhere AWS operates. The schema carried data_residency and
    # nothing read it; these rules read it.
    for role, entry in (manifest.get("models") or {}).items():
        # **The fallback is held to the same residency rules as the primary.** A fallback is where a
        # prompt goes when the primary fails, so a global profile hidden in a fallback field routes a
        # prompt just as far as one in the primary field would. Checking only the primary would leave
        # the one field a reviewer looks at last as the way around the rule.
        candidates = [(f"models.{role}", (entry or {}).get("frontier") or "")]
        for index, item in enumerate(fallback_chain(entry)):
            candidates.append((f"models.{role}.fallback[{index}]", split_fallback(item)[0]))
        for label, frontier in candidates:
            if not frontier.startswith("bedrock/"):
                continue
            model_id = frontier.split("/", 1)[1]
            if model_id.startswith("global.") and compliance_for_residency not in unrestricted:
                problems.append(
                    f"{label}: a global inference profile ({model_id}) can route a prompt to any "
                    f"Region, which is not acceptable with compliance profile "
                    f"{compliance_for_residency!r}. Use a geographic profile such as us.")
            if model_id.startswith("anthropic.claude-fable"):
                problems.append(
                    f"{label}: Fable models permit only aws_review or provider_data_share retention, "
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

    # Content modes. What the record keeps, per data class. The prompt and completion hashes are
    # recorded whatever this says; the text is what this governs, so a typo here decides whether a
    # client's documents end up in the evidence file.
    content = manifest.get("content") or {}
    if content:
        if content.get("default", "hash") not in CONTENT_MODES:
            problems.append(f"content.default must be one of {sorted(CONTENT_MODES)}, "
                            f"got {content.get('default')!r}")
        for data_class, mode in sorted((content.get("classes") or {}).items()):
            if data_class not in DATA_CLASSES:
                problems.append(f"content.classes names {data_class!r}, which is not a data class. "
                                f"Known classes: {sorted(DATA_CLASSES)}")
            if mode not in CONTENT_MODES:
                problems.append(f"content.classes.{data_class} must be one of {sorted(CONTENT_MODES)}, "
                                f"got {mode!r}")

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
        # Warnings are printed whether or not the manifest validates, and they never affect the exit code.
        # A divergence between the two places the same policy can be declared is a thing for a person to go
        # and read the contract about; it is not a reason to refuse a manifest that is working.
        warnings = divergences(manifest)
        if problems:
            failed += 1
            print(f"FAIL {path.name}")
            for p in problems:
                print(f"  - {p}")
        else:
            print(f"ok   {path.name}  (profile={manifest['profile']}, packs={manifest['packs']})")
        for w in warnings:
            print(f"  ! {path.name}: {w}")

    print()
    print(f"{len(paths) - failed}/{len(paths)} manifests valid; {len(packs)} packs known")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
