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
