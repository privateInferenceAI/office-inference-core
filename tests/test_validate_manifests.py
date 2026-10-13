"""Tests for the manifest validator.

The validator is where the residency rules live. Two documents once claimed those rules were
enforced when nothing read them, so the rules get tests rather than a paragraph. Each case below
builds a manifest, mutates one field, and requires the validator to say the specific thing.

Run: python3 -m pytest tests/
"""
from __future__ import annotations

import copy
import sys
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import validate_manifests as vm  # noqa: E402

COMPLIANT = ROOT / "manifests" / "example-compliant-cloud.yaml"
STANDARD = ROOT / "manifests" / "example-standard-cloud.yaml"
FIXTURES = Path(__file__).resolve().parent / "fixtures"


@pytest.fixture(scope="module")
def packs() -> set[str]:
    return vm.load_catalog_packs()


def load(path: Path) -> dict:
    return yaml.safe_load(path.read_text())


def problems(manifest: dict, packs: set[str]) -> list[str]:
    return vm.check(copy.deepcopy(manifest), packs, Path("test-manifest.yaml"))


def mentions(problems_: list[str], needle: str) -> bool:
    return any(needle in problem for problem in problems_)


def test_every_shipped_manifest_validates(packs):
    for path in sorted((ROOT / "manifests").glob("*.yaml")):
        assert problems(load(path), packs) == [], f"{path.name} should validate cleanly"


def test_a_global_inference_profile_under_a_regulated_profile_is_refused(packs):
    manifest = load(COMPLIANT)
    manifest["models"]["analyst"]["frontier"] = "bedrock/global.anthropic.claude-sonnet-5"
    found = problems(manifest, packs)
    assert mentions(found, "global"), found


def test_a_global_inference_profile_is_allowed_when_compliance_is_unrestricted(packs):
    """The rule is conditional, not a blanket refusal of global profiles.

    A client with no compliance claim may use one, and a validator that refused every global
    profile would be wrong in the other direction.
    """
    manifest = load(STANDARD)
    manifest["models"]["analyst"]["frontier"] = "bedrock/global.anthropic.claude-sonnet-5"
    found = problems(manifest, packs)
    assert not mentions(found, "global"), found


def test_fable_is_refused_under_a_regulated_profile(packs):
    manifest = load(COMPLIANT)
    manifest["models"]["reader"]["frontier"] = "bedrock/anthropic.claude-fable-5"
    found = problems(manifest, packs)
    assert mentions(found, "Fable"), found


def test_sec_17a4_requires_the_six_year_floor(packs):
    manifest = load(COMPLIANT)
    manifest["compliance"]["profile"] = "sec-17a4"
    manifest["compliance"]["audit_retention_days"] = 365
    found = problems(manifest, packs)
    assert mentions(found, "2190"), found


def test_an_unknown_pack_is_refused(packs):
    manifest = load(STANDARD)
    manifest["packs"] = ["document-chat", "pack-that-does-not-exist"]
    found = problems(manifest, packs)
    assert mentions(found, "unknown pack ids"), found


def test_a_required_model_role_missing_is_refused(packs):
    manifest = load(STANDARD)
    del manifest["models"]["reader"]
    found = problems(manifest, packs)
    assert mentions(found, "models.reader"), found


def test_the_frontier_lane_pack_requires_a_cloud_or_hybrid_profile(packs):
    manifest = load(STANDARD)
    manifest["profile"] = "office"
    manifest["packs"] = ["document-chat", "frontier-lane"]
    found = problems(manifest, packs)
    assert mentions(found, "frontier-lane"), found


def test_the_committed_broken_fixture_is_still_broken(packs):
    """The fixture CI depends on. If someone repairs it, this fails instead of the gate silently
    passing everything."""
    found = problems(load(FIXTURES / "broken-manifest.yaml"), packs)
    assert found, "tests/fixtures/broken-manifest.yaml now validates; CI's gate can no longer fail"
    assert mentions(found, "unknown pack ids"), found
    assert mentions(found, "global"), found


def test_our_own_account_is_allowed_for_the_hosted_tier_and_dev_boxes(packs):
    """The schema has always allowed account_owner: office_inference. The validator demanded
    "client" and so refused the hosted tier and every development manifest."""
    manifest = load(STANDARD)
    manifest["lanes"]["frontier"]["account_owner"] = "office_inference"
    found = problems(manifest, packs)
    assert not mentions(found, "account_owner"), found


def test_our_own_account_is_refused_with_a_compliance_profile(packs):
    """The hosted tier is standard-only. A compliance claim means the client's own account,
    because otherwise regulated data sits in ours."""
    manifest = load(COMPLIANT)
    manifest["lanes"]["frontier"]["account_owner"] = "office_inference"
    found = problems(manifest, packs)
    assert mentions(found, "standard-only"), found


def test_an_unknown_account_owner_is_refused(packs):
    manifest = load(STANDARD)
    manifest["lanes"]["frontier"]["account_owner"] = "somebody_else"
    found = problems(manifest, packs)
    assert mentions(found, "account_owner"), found


def test_the_shipped_dev_cloud_manifest_validates(packs):
    """The cloud build's smoke-test manifest: no local lane, every role on Bedrock."""
    manifest = load(ROOT / "manifests" / "dev-cloud.yaml")
    assert problems(manifest, packs) == []


# --- offsite delivery -------------------------------------------------------------------------
#
# Evidence and backups may leave the building when the manifest names them. Nothing else may, which
# is the machine-checkable half of "nothing leaves the building".


def offsite_manifest(**overrides) -> dict:
    manifest = load(STANDARD)
    block = {"provider": "s3", "account_owner": "client", "bucket": "acme-audit",
             "region": "us-east-1", "ship": ["anchors"]}
    block.update(overrides)
    manifest["offsite"] = block
    return manifest


def test_an_offsite_destination_for_evidence_is_accepted(packs):
    assert problems(offsite_manifest(), packs) == []


def test_documents_may_never_be_shipped(packs):
    """The rule that keeps the office promise true. A list that accepted "documents" would let
    anyone switch "nothing leaves the building" off by editing configuration."""
    found = problems(offsite_manifest(ship=["anchors", "documents"]), packs)
    assert mentions(found, "may not leave the building"), found
    assert mentions(found, "documents"), found


def test_prompts_may_never_be_shipped(packs):
    found = problems(offsite_manifest(ship=["prompts"]), packs)
    assert mentions(found, "may not leave the building"), found


def test_an_empty_ship_list_is_refused(packs):
    found = problems(offsite_manifest(ship=[]), packs)
    assert mentions(found, "must name what may leave"), found


def test_an_office_build_may_ship_evidence(packs):
    """An office appliance with its own AWS account is a legitimate shape, and it is not hybrid:
    hybrid means both inference lanes. That account holds evidence and backups."""
    manifest = offsite_manifest(ship=["anchors", "backups"])
    manifest["profile"] = "office"
    manifest["lanes"] = {"frontier": {"enabled": False}}
    found = problems(manifest, packs)
    assert not mentions(found, "offsite"), found


def test_an_office_build_still_cannot_enable_the_frontier_lane(packs):
    """The account does not make inference remote. That stays true whatever the offsite block says."""
    manifest = offsite_manifest()
    manifest["profile"] = "office"
    manifest["lanes"] = {"frontier": {"enabled": True, "provider": "bedrock",
                                      "account_owner": "client",
                                      "data_classes_allowed": ["internal"]}}
    found = problems(manifest, packs)
    assert mentions(found, "frontier lane enabled on the office profile"), found


def test_itar_cui_refuses_offsite_delivery(packs):
    manifest = offsite_manifest()
    manifest["profile"] = "office"
    manifest["lanes"] = {"frontier": {"enabled": False}}
    manifest["compliance"] = {"profile": "itar-cui", "baa_with_office_inference": True,
                              "audit_retention_days": 365}
    found = problems(manifest, packs)
    assert mentions(found, "itar-cui refuses offsite delivery"), found


def test_an_invalid_bucket_name_is_refused(packs):
    found = problems(offsite_manifest(bucket="Not A Bucket"), packs)
    assert mentions(found, "not a valid S3 bucket name"), found


def test_an_unknown_provider_is_refused(packs):
    found = problems(offsite_manifest(provider="gcs"), packs)
    assert mentions(found, "offsite.provider must be s3"), found


def test_a_missing_account_owner_is_refused(packs):
    found = problems(offsite_manifest(account_owner=None), packs)
    assert mentions(found, "offsite.account_owner"), found


# --- budgets ----------------------------------------------------------------------------------
#
# A ceiling is only a ceiling if it is refused rather than reported. These lock the declaration
# rules; the refusal itself is proven against the running gateway, in the runbook's drill.


def budgeted_manifest(**overrides) -> dict:
    manifest = load(STANDARD)
    block = {"period_days": 30, "roles": {"front_desk": 5, "analyst": 50, "reader": 50}}
    block.update(overrides)
    manifest["budgets"] = block
    return manifest


def test_a_budget_for_every_role_is_accepted(packs):
    assert problems(budgeted_manifest(), packs) == []


def test_a_role_without_a_budget_is_refused_when_the_frontier_lane_is_on(packs):
    """The rule that exists because a remote call costs money. An uncapped role is how a runaway
    loop empties an account before anyone looks at a dashboard."""
    found = problems(budgeted_manifest(roles={"analyst": 50, "reader": 50}), packs)
    assert mentions(found, "have no budget"), found
    assert mentions(found, "front_desk"), found


def test_a_budget_for_a_role_that_does_not_exist_is_refused(packs):
    """Typo protection. 'analist: 50' would otherwise look like a budget while capping nothing."""
    found = problems(budgeted_manifest(roles={"analyst": 50, "reader": 50, "front_desk": 5,
                                              "analist": 50}), packs)
    assert mentions(found, "not model roles"), found


def test_a_zero_or_negative_budget_is_refused(packs):
    for bad in (0, -1):
        found = problems(budgeted_manifest(roles={"analyst": bad, "reader": 50, "front_desk": 5}), packs)
        assert mentions(found, "positive amount"), (bad, found)


def test_a_non_positive_period_is_refused(packs):
    found = problems(budgeted_manifest(period_days=0), packs)
    assert mentions(found, "period_days"), found


def test_a_local_only_build_may_have_no_budget(packs):
    """Nothing leaves the building and nothing is billed per call, so a ceiling there would be
    decoration rather than a control."""
    manifest = load(ROOT / "manifests" / "example-in-office.yaml")
    assert not (manifest.get("budgets") or {}).get("roles"), "the office example should ship uncapped"
    assert problems(manifest, packs) == []


# --- content modes ----------------------------------------------------------------------------

def content_manifest(**overrides) -> dict:
    manifest = load(STANDARD)
    block = {"default": "hash", "classes": {"public": "full", "internal": "redacted"}}
    block.update(overrides)
    manifest["content"] = block
    return manifest


def test_a_content_policy_for_each_class_is_accepted(packs):
    assert problems(content_manifest(), packs) == []


def test_an_unknown_content_mode_is_refused(packs):
    """A typo here decides whether a client's documents end up in the evidence file, so it is refused
    rather than defaulted."""
    found = problems(content_manifest(classes={"public": "everything"}), packs)
    assert mentions(found, "content.classes.public"), found


def test_an_unknown_data_class_in_the_content_policy_is_refused(packs):
    found = problems(content_manifest(classes={"secret": "hash"}), packs)
    assert mentions(found, "not a data class"), found


def test_an_unknown_default_mode_is_refused(packs):
    found = problems(content_manifest(default="none"), packs)
    assert mentions(found, "content.default"), found


def test_the_shipped_dev_manifest_declares_its_content_policy(packs):
    manifest = load(ROOT / "manifests" / "dev-cloud.yaml")
    assert manifest["content"]["classes"]["public"] == "full"
    assert manifest["content"]["classes"]["internal"] == "redacted"
    assert problems(manifest, packs) == []


def test_a_pack_list_that_never_requires_core_is_refused(packs):
    """A manifest listing only sellable packs renders a stack with no gateway, database or audit sink.
    The catalog's requires edges are what make core implicit, and until this rule existed nothing read
    them."""
    manifest = load(STANDARD)
    manifest["packs"] = ["document-chat"]
    assert problems(manifest, packs) == [], "document-chat requires core, so this is fine"
    manifest["packs"] = []
    assert problems(manifest, packs), "an empty pack list is still refused"


def test_a_cloud_build_cannot_switch_off_the_frontier_lane(packs):
    """A cloud build has no local lane, so switching off the remote one leaves it with no models at
    all. The renderer used to emit frontier entries anyway, which quietly ignored the flag; the shape
    is refused now."""
    manifest = load(STANDARD)
    manifest["lanes"]["frontier"]["enabled"] = False
    found = problems(manifest, packs)
    assert mentions(found, "needs the frontier lane enabled"), found


def test_a_frontier_lane_without_an_egress_block_is_refused(packs):
    """**The silent failure this rule exists for.**

    The egress gate renders `FRONTIER_CLASSES` from `access.egress.frontier_classes` and reads an empty list as
    a permission set with nothing in it, so it refuses every request. A manifest that declares classes under
    `lanes.frontier.data_classes_allowed` and no `access` block therefore validates, then sends nothing, and the
    first symptom is a model call that will not go out.

    Three of the shipped example manifests had exactly this shape before the rule was added: they named three
    classes in the lane block and rendered an empty gate list.
    """
    manifest = load(ROOT / "manifests" / "dev-cloud.yaml")
    manifest.pop("access", None)
    assert manifest["lanes"]["frontier"]["enabled"] is True
    # The lane block still declares classes, so the older rule is satisfied and only the new one fires.
    assert manifest["lanes"]["frontier"]["data_classes_allowed"]
    found = problems(manifest, packs)
    assert mentions(found, "access.egress.frontier_classes"), found


def test_an_empty_egress_class_list_is_refused(packs):
    """Declaring the block and leaving the list empty is the same failure with more typing."""
    manifest = load(ROOT / "manifests" / "dev-cloud.yaml")
    manifest["access"]["egress"]["frontier_classes"] = []
    found = problems(manifest, packs)
    assert mentions(found, "access.egress.frontier_classes"), found


def test_an_office_build_with_no_frontier_lane_needs_no_egress_block(packs):
    """The rule is about an ENABLED lane, and an office build's is off.

    `dev-office.yaml` declares an empty `frontier_classes` on purpose, which is that build's policy rather than
    an oversight: it has no frontier lane, so nothing may leave. The rule must not turn that into a failure.
    """
    manifest = load(ROOT / "manifests" / "dev-office.yaml")
    assert manifest["lanes"]["frontier"]["enabled"] is False
    assert problems(manifest, packs) == []


# --------------------------------------------------------------------------- divergence warnings
#
# Two fields carry the same enum and the same meaning: `lanes.frontier.data_classes_allowed` and
# `access.egress.frontier_classes`. The validator reads the first, the egress gate reads the second, and
# nothing reconciled them until these warnings. Both fields stay, so a disagreement has to be VISIBLE
# rather than fatal — a manifest that works today must not stop working on upgrade.

def test_agreeing_fields_produce_no_warning(packs):
    """The common case, and the one that must stay quiet.

    `example-hybrid` declares both blocks with the same classes on purpose: it is what a client manifest
    should look like, and a warning here would teach people to ignore warnings.
    """
    manifest = load(ROOT / "manifests" / "example-hybrid.yaml")
    assert vm.divergences(manifest) == []


def test_a_gate_wider_than_the_declaration_is_reported_as_dangerous(packs):
    """The direction that matters: material leaves that the lane block does not permit.

    This is the case a client's contract would call a breach, and it is the one the warning has to name.
    Saying only "the two lists differ" would leave a reader to work out which way was bad.
    """
    manifest = load(ROOT / "manifests" / "dev-cloud.yaml")
    manifest["lanes"]["frontier"]["data_classes_allowed"] = ["public"]
    manifest["access"]["egress"]["frontier_classes"] = ["public", "internal"]
    # The redaction pair is made to agree so this test asserts one thing.
    manifest["lanes"]["frontier"]["redaction"] = True
    manifest["access"]["egress"]["redaction"] = "on"

    found = vm.divergences(manifest)
    assert len(found) == 1, found
    assert "may leave for a frontier model although the lane block does not permit it" in found[0]
    assert "[internal]" in found[0]


def test_a_gate_narrower_than_the_declaration_is_reported_as_safe_but_misleading(packs):
    """The other direction, and it deserves different words.

    Nothing leaves that was forbidden, so this is not a breach — it is a manifest that reads as though
    something may be sent when it may not. Calling both directions "a disagreement" would make the safe
    one look as urgent as the dangerous one.
    """
    manifest = load(ROOT / "manifests" / "dev-cloud.yaml")
    manifest["lanes"]["frontier"]["data_classes_allowed"] = ["public", "internal"]
    manifest["access"]["egress"]["frontier_classes"] = ["public"]
    manifest["lanes"]["frontier"]["redaction"] = True
    manifest["access"]["egress"]["redaction"] = "on"

    found = vm.divergences(manifest)
    assert len(found) == 1, found
    assert "safe, but the manifest reads as though" in found[0]
    assert "may leave for a frontier model although" not in found[0]


def test_one_field_absent_is_not_a_divergence(packs):
    """A manifest written against the old table declares only the lane field.

    That is stale rather than contradictory, and warning about it would fire on manifests that are working
    exactly as intended. The check is about two declarations disagreeing, not about one being missing.
    """
    manifest = load(ROOT / "manifests" / "dev-cloud.yaml")
    manifest.pop("access", None)
    assert vm.divergences(manifest) == []


def test_redaction_disagreeing_is_reported(packs):
    """The same pairing on the other decision, and it is divergent in two shipped manifests already.

    `dev-cloud` says `redaction: false` in the lane block and `on` in the access block. The renderer's
    default has been deciding which wins, silently, which is the whole problem this warning exists for.
    """
    manifest = load(ROOT / "manifests" / "dev-cloud.yaml")
    manifest["lanes"]["frontier"]["data_classes_allowed"] = ["public", "internal"]
    manifest["access"]["egress"]["frontier_classes"] = ["public", "internal"]
    manifest["lanes"]["frontier"]["redaction"] = False
    manifest["access"]["egress"]["redaction"] = "on"

    found = vm.divergences(manifest)
    assert len(found) == 1, found
    assert "access.egress.redaction disagree" in found[0]


def test_the_two_shipped_manifests_that_diverge_are_reported(packs):
    """A regression guard on the real files, so a quiet revert shows up here.

    `dev-cloud` and `example-standard-cloud` both declare `redaction: false` in the lane block while the
    access block says `on`. Either the manifests get corrected or this test gets corrected; what must not
    happen is the warning silently disappearing.
    """
    for name in ("dev-cloud.yaml", "example-standard-cloud.yaml"):
        manifest = load(ROOT / "manifests" / name)
        found = vm.divergences(manifest)
        assert any("redaction" in w for w in found), f"{name} should report its redaction divergence"


def test_a_warning_never_makes_a_valid_manifest_fail(packs):
    """The property the whole design rests on: visible, not fatal.

    A divergence warning printed beside a FAIL would make every divergent manifest unshippable, and refusing
    a manifest that is working today is a worse outcome than a warning nobody reads. `check` and
    `divergences` are separate functions for this reason.
    """
    manifest = load(ROOT / "manifests" / "dev-cloud.yaml")
    manifest["lanes"]["frontier"]["data_classes_allowed"] = ["public"]
    manifest["access"]["egress"]["frontier_classes"] = ["public", "internal"]
    assert vm.divergences(manifest), "this manifest diverges"
    assert vm.check(copy.deepcopy(manifest), packs, Path("test-manifest.yaml")) == [], (
        "a divergence must not add a problem that fails validation")


def test_an_absent_egress_field_and_an_empty_one_are_reported_differently(packs):
    """Two shapes of the same failure, and the fix differs, so the message must too.

    `access.egress` is what leaves, so an empty one is a configuration error rather than a policy: the lane
    above it says classes may go and the empty list makes the deployment refuse every call while reading as
    permissive.

    An ABSENT field is a manifest written against the older table, where the lane block was the only place
    to say this — nothing has been decided. A field PRESENT and EMPTY is somebody having written the policy
    out and left it permitting nothing. Both fail; a reader needs to know which one they have.
    """
    absent = load(ROOT / "manifests" / "dev-cloud.yaml")
    absent["access"]["egress"].pop("frontier_classes")
    absent_problems = problems(absent, packs)
    assert mentions(absent_problems, "access.egress declares nothing"), absent_problems

    empty = load(ROOT / "manifests" / "dev-cloud.yaml")
    empty["access"]["egress"]["frontier_classes"] = []
    empty_problems = problems(empty, packs)
    assert mentions(empty_problems, "is empty"), empty_problems
    assert mentions(empty_problems, "override"), empty_problems

    # The two must not produce the same text, or the distinction is decoration.
    assert absent_problems != empty_problems


def test_an_empty_egress_list_is_an_error_not_a_warning(packs):
    """A warning is for a divergence a person should look at. This is a deployment that cannot send anything.

    The distinction matters because warnings do not fail the run: a manifest with an empty authoritative
    field would pass validation and then refuse every model call, which is the silent failure this rule
    exists to prevent.
    """
    manifest = load(ROOT / "manifests" / "dev-cloud.yaml")
    manifest["access"]["egress"]["frontier_classes"] = []
    assert problems(manifest, packs), "an empty authoritative field must fail validation, not warn"


def test_an_office_build_may_declare_an_empty_egress_list(packs):
    """The rule is about an ENABLED lane. An office build's is off, and an empty list is its policy.

    `dev-office.yaml` declares exactly that, and it is correct: the build has no frontier lane, so nothing
    may leave, and the empty list says so. Turning that into a failure would be the rule overreaching.
    """
    manifest = load(ROOT / "manifests" / "dev-office.yaml")
    assert manifest["lanes"]["frontier"]["enabled"] is False
    assert manifest["access"]["egress"]["frontier_classes"] == []
    assert problems(manifest, packs) == []
