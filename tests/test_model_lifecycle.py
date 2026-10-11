"""Tests for the model lifecycle check.

The requirement's verification is specific: a check fails when a manifest names a model past its
end-of-life date. That is the second test below, and it uses a fixture table rather than the real one,
because reaching into the real table to make it fail would mean editing the thing being checked.
"""
from __future__ import annotations

import importlib.util
import json
from datetime import date
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
CHECK_PY = ROOT / "scripts" / "check_model_lifecycle.py"
FIXTURES = Path(__file__).resolve().parent / "fixtures"
TODAY = date(2026, 10, 11)


def load_check():
    spec = importlib.util.spec_from_file_location("oi_model_lifecycle", CHECK_PY)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def check():
    return load_check()


@pytest.fixture(scope="module")
def real_table():
    return json.loads((ROOT / "data" / "model-lifecycle.json").read_text())


def manifest_naming(frontier: str) -> dict:
    return {"models": {"analyst": {"local": "", "frontier": frontier}}}


def test_the_shipped_manifests_pass_against_the_real_table(check, real_table):
    for path in sorted((ROOT / "manifests").glob("*.yaml")):
        manifest = yaml.safe_load(path.read_text())
        assert check.check(manifest, real_table, TODAY) == [], f"{path.name} should be clean"


def test_a_model_past_its_end_of_life_fails_the_check(check):
    """The requirement, in one assertion."""
    table = json.loads((FIXTURES / "lifecycle-past-eol.json").read_text())
    problems = check.check(manifest_naming("bedrock/amazon.nova-micro-v1:0"), table, TODAY)
    assert problems, "a model past its end-of-life date must fail the check"
    assert any("end-of-life" in p for p in problems), problems
    assert any("nova-lite" in p for p in problems), "the replacement should be named: " + str(problems)


def test_a_model_that_is_not_active_fails_even_without_a_date(check):
    table = {"models": {"amazon.nova-micro-v1:0": {"status": "LEGACY", "eol_date": None,
                                                  "replacement": "amazon.nova-lite-v1:0"}}}
    problems = check.check(manifest_naming("bedrock/amazon.nova-micro-v1:0"), table, TODAY)
    assert any("LEGACY" in p for p in problems), problems


def test_a_model_the_table_does_not_know_fails(check):
    """A new model in a manifest should be a decision someone made, not something that slipped past
    because nobody wrote its lifecycle down."""
    problems = check.check(manifest_naming("bedrock/some.brand-new-model"), {"models": {}}, TODAY)
    assert any("not in" in p for p in problems), problems


def test_an_inference_profile_resolves_to_the_model_underneath(check):
    """us.anthropic.claude-sonnet-5 is a routing scope on a model, so the lifecycle belongs to the
    model underneath it rather than to the profile."""
    assert check.model_id("bedrock/us.anthropic.claude-sonnet-5") == "anthropic.claude-sonnet-5"
    assert check.model_id("bedrock/global.amazon.nova-micro-v1:0") == "amazon.nova-micro-v1:0"
    assert check.model_id("bedrock/amazon.nova-micro-v1:0") == "amazon.nova-micro-v1:0"


def test_the_lifecycle_table_is_tracked_by_git():
    """It lives under data/, which .gitignore excludes as runtime data. The first version of this file
    was silently untracked for that reason: every test passed locally because the file was there, and
    CI failed because it was not. A check that only works on the machine that wrote it is not a check.
    """
    import subprocess
    result = subprocess.run(["git", "ls-files", "--error-unmatch", "data/model-lifecycle.json"],
                            capture_output=True, text=True, cwd=ROOT)
    assert result.returncode == 0, (
        "data/model-lifecycle.json is not tracked by git, so CI cannot see it. Check .gitignore.")


def test_the_real_table_records_where_its_facts_came_from(real_table):
    """A table nobody can date is a table nobody can trust."""
    assert real_table["verified_on"] == "2026-10-11"
    assert "bedrock" in real_table["source"]
    for model, entry in real_table["models"].items():
        assert entry["status"], f"{model} has no status"
        assert entry.get("replacement"), f"{model} has no recorded replacement"
