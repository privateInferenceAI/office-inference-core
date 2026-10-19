"""The fallback chain: one model in a second Region first, a different model second.

**Why a chain and not one fallback.** The measured failure shape on 2026-10-19 was a provider that was
unavailable in one Region while the same model answered in another. Falling straight to a different model
recovered the answer but changed its quality and price; asking the same model through another Region's
endpoint recovers both. The chain makes that the first step and a different model the second.

These tests hold the three places the chain is read: the renderer's output, the validator's rules, and the
lifecycle check's coverage. They also hold the tracked manifest the development deployment renders from.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import check_model_lifecycle as lifecycle  # noqa: E402
import render_manifest  # noqa: E402
import validate_manifests as vm  # noqa: E402
from manifest_fields import fallback_chain, split_fallback  # noqa: E402

STANDARD = ROOT / "manifests" / "example-standard-cloud.yaml"
DEV_CLAUDE = ROOT / "manifests" / "dev-cloud-claude.yaml"


@pytest.fixture(scope="module")
def packs() -> set[str]:
    return vm.load_catalog_packs()


def cloud_manifest() -> dict:
    return yaml.safe_load(STANDARD.read_text())


def render(manifest: dict, tmp_path: Path) -> dict:
    path = tmp_path / "manifest.yaml"
    path.write_text(yaml.safe_dump(manifest))
    out = tmp_path / "rendered"
    built = render_manifest.build_manifest(manifest, out) if hasattr(render_manifest, "build_manifest") else None
    if built is not None:
        return built
    return None


# ------------------------------------------------------------------ parsing


def test_a_single_fallback_is_a_chain_of_one():
    assert fallback_chain({"fallback": "bedrock/amazon.nova-pro-v1:0"}) == ["bedrock/amazon.nova-pro-v1:0"]
    assert fallback_chain({}) == []


def test_a_chain_is_read_in_order_and_the_region_split_out():
    chain = fallback_chain({"fallback": [
        "bedrock/us.anthropic.claude-haiku-4-5-20251001-v1:0@us-east-2",
        "bedrock/amazon.nova-pro-v1:0"]})
    assert chain[0].endswith("@us-east-2")
    assert split_fallback(chain[0]) == ("bedrock/us.anthropic.claude-haiku-4-5-20251001-v1:0", "us-east-2")
    assert split_fallback(chain[1]) == ("bedrock/amazon.nova-pro-v1:0", "")


# ------------------------------------------------------------------ renderer


def test_a_chain_renders_one_group_per_entry_with_the_region(tmp_path):
    manifest = cloud_manifest()
    manifest["models"]["analyst"]["fallback"] = [
        "bedrock/us.anthropic.claude-haiku-4-5-20251001-v1:0@us-east-2",
        "bedrock/amazon.nova-pro-v1:0",
    ]
    path = tmp_path / "chain.yaml"
    path.write_text(yaml.safe_dump(manifest))
    out = tmp_path / "rendered"
    subprocess.run([sys.executable, str(ROOT / "scripts" / "render_manifest.py"),
                    "--manifest", str(path), "--out", str(out)], check=True, capture_output=True)
    config = yaml.safe_load((out / "litellm.yaml").read_text())
    names = [entry["model_name"] for entry in config["model_list"]]
    assert "analyst-fallback" in names
    assert "analyst-fallback-2" in names
    first = next(entry for entry in config["model_list"] if entry["model_name"] == "analyst-fallback")
    second = next(entry for entry in config["model_list"] if entry["model_name"] == "analyst-fallback-2")
    assert first["litellm_params"]["aws_region_name"] == "us-east-2"
    assert first["litellm_params"]["model"] == "bedrock/us.anthropic.claude-haiku-4-5-20251001-v1:0"
    assert "aws_region_name" not in second["litellm_params"]
    assert config["litellm_settings"]["fallbacks"] == [
        {"analyst": ["analyst-fallback", "analyst-fallback-2"]}]


def test_the_tracked_deployment_manifest_renders_the_chain_it_declares(tmp_path):
    manifest = yaml.safe_load(DEV_CLAUDE.read_text())
    path = tmp_path / "dev-cloud-claude.yaml"
    path.write_text(yaml.safe_dump(manifest))
    out = tmp_path / "rendered"
    subprocess.run([sys.executable, str(ROOT / "scripts" / "render_manifest.py"),
                    "--manifest", str(path), "--out", str(out)], check=True, capture_output=True)
    config = yaml.safe_load((out / "litellm.yaml").read_text())
    assert config["litellm_settings"]["fallbacks"] == [
        {"analyst": ["analyst-fallback", "analyst-fallback-2"]}]
    first = next(entry for entry in config["model_list"] if entry["model_name"] == "analyst-fallback")
    assert first["litellm_params"]["aws_region_name"] == "us-east-2"


# ------------------------------------------------------------------ validator


def test_a_valid_chain_passes(packs):
    manifest = cloud_manifest()
    manifest["models"]["analyst"]["fallback"] = [
        "bedrock/us.anthropic.claude-haiku-4-5-20251001-v1:0@us-east-2",
        "bedrock/amazon.nova-pro-v1:0",
    ]
    found = vm.check(manifest, packs, Path("test.yaml"))
    assert not [problem for problem in found if "fallback" in problem], found


def test_a_region_suffix_must_be_a_region(packs):
    manifest = cloud_manifest()
    manifest["models"]["analyst"]["fallback"] = ["bedrock/amazon.nova-pro-v1:0@east"]
    found = vm.check(manifest, packs, Path("test.yaml"))
    assert any("not an AWS Region code" in problem for problem in found), found


def test_a_chain_longer_than_three_is_refused(packs):
    manifest = cloud_manifest()
    manifest["models"]["analyst"]["fallback"] = [
        "bedrock/amazon.nova-pro-v1:0@us-east-2",
        "bedrock/amazon.nova-lite-v1:0@us-west-2",
        "bedrock/amazon.nova-micro-v1:0@us-east-2",
        "bedrock/deepseek.v3.2",
    ]
    found = vm.check(manifest, packs, Path("test.yaml"))
    assert any("bounded at 3" in problem for problem in found), found


def test_repeating_the_primary_in_the_same_region_is_refused(packs):
    manifest = cloud_manifest()
    primary = manifest["models"]["analyst"]["frontier"]
    manifest["models"]["analyst"]["fallback"] = [primary]
    found = vm.check(manifest, packs, Path("test.yaml"))
    assert any("repeats the primary model" in problem for problem in found), found


def test_a_duplicate_chain_entry_is_refused(packs):
    manifest = cloud_manifest()
    manifest["models"]["analyst"]["fallback"] = [
        "bedrock/amazon.nova-pro-v1:0", "bedrock/amazon.nova-pro-v1:0"]
    found = vm.check(manifest, packs, Path("test.yaml"))
    assert any("repeats an earlier entry" in problem for problem in found), found


def test_a_chain_entry_is_still_cloud_only(packs):
    manifest = cloud_manifest()
    manifest["profile"] = "office"
    manifest["models"]["analyst"]["fallback"] = ["bedrock/amazon.nova-pro-v1:0"]
    found = vm.check(manifest, packs, Path("test.yaml"))
    assert any("Fallbacks are cloud-only" in problem for problem in found), found


# ------------------------------------------------------------------ lifecycle


def test_the_lifecycle_check_follows_every_entry_in_the_chain():
    manifest = cloud_manifest()
    manifest["models"]["analyst"]["fallback"] = [
        "bedrock/us.anthropic.claude-haiku-4-5-20251001-v1:0@us-east-2",
        "bedrock/amazon.nova-pro-v1:0",
    ]
    table = lifecycle.load_table(lifecycle.TABLE)
    problems = lifecycle.check(manifest, table, lifecycle.date.today())
    assert not problems, problems


def test_the_lifecycle_check_names_the_chain_position():
    manifest = cloud_manifest()
    manifest["models"]["analyst"]["fallback"] = ["bedrock/amazon.nova-pro-v1:0", "bedrock/gone.v9"]
    table = lifecycle.load_table(lifecycle.TABLE)
    problems = lifecycle.check(manifest, table, lifecycle.date.today())
    assert any("retired.gone.v9" in problem or "gone.v9" in problem for problem in problems), problems
    assert any("fallback[1]" in problem for problem in problems), problems


def test_the_tracked_deployment_manifest_is_clean_under_both_gates(packs):
    manifest = yaml.safe_load(DEV_CLAUDE.read_text())
    assert not vm.check(manifest, packs, DEV_CLAUDE)
    table = lifecycle.load_table(lifecycle.TABLE)
    assert not lifecycle.check(manifest, table, lifecycle.date.today())
