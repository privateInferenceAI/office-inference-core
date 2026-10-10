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
