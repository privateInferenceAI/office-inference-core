"""The client's model-change procedure, held as a tool.

**Why this exists.** The system is sold, and may not be serviced afterwards. Models are deprecated on the
provider's schedule, so the client's operator has to be able to move a role to another model, validate the
result and render it without editing a gateway config by hand. These tests hold that procedure: the tool
lists what the lifecycle table knows, refuses a change that would leave the manifest invalid, and rewrites
only the models block.
"""
from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
TOOL = ROOT / "scripts" / "model_admin.py"
DEV_CLAUDE = ROOT / "manifests" / "dev-cloud-claude.yaml"


def run(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, str(TOOL), *args], capture_output=True, text=True)


def copy_manifest(tmp_path: Path) -> Path:
    target = tmp_path / "dev-cloud-claude.yaml"
    shutil.copy(DEV_CLAUDE, target)
    return target


def test_list_names_the_models_the_table_knows():
    result = run("list")
    assert result.returncode == 0, result.stderr
    assert "anthropic.claude-haiku-4-5-20251001-v1:0" in result.stdout
    assert "amazon.nova-pro-v1:0" in result.stdout
    assert "ACTIVE" in result.stdout


def test_list_marks_a_legacy_model_and_its_replacement():
    result = run("list")
    legacy_line = next(line for line in result.stdout.splitlines()
                       if "anthropic.claude-sonnet-4-5-20250929-v1:0" in line)
    assert "LEGACY" in legacy_line
    assert "anthropic.claude-sonnet-5" in legacy_line


def test_check_accepts_the_tracked_deployment_manifest():
    result = run("check", str(DEV_CLAUDE))
    assert result.returncode == 0, result.stdout + result.stderr
    assert "ok" in result.stdout


def test_check_refuses_a_manifest_with_an_unknown_model(tmp_path):
    target = copy_manifest(tmp_path)
    text = target.read_text()
    target.write_text(text.replace("bedrock/amazon.nova-pro-v1:0", "bedrock/retired.v9", 1))
    result = run("check", str(target))
    assert result.returncode == 1
    assert "retired.v9" in result.stdout


def test_dry_run_does_not_touch_the_manifest(tmp_path):
    target = copy_manifest(tmp_path)
    before = target.read_bytes()
    result = run("set", str(target), "--role", "analyst",
                 "--model", "bedrock/amazon.nova-pro-v1:0", "--dry-run")
    assert result.returncode == 0, result.stdout + result.stderr
    assert "dry run" in result.stdout
    assert target.read_bytes() == before


def test_set_rewrites_only_the_models_block(tmp_path):
    target = copy_manifest(tmp_path)
    before = yaml.safe_load(target.read_text())
    result = run("set", str(target), "--role", "analyst",
                 "--model", "bedrock/amazon.nova-lite-v1:0",
                 "--fallback", "bedrock/amazon.nova-pro-v1:0@us-east-2")
    assert result.returncode == 0, result.stdout + result.stderr
    after_text = target.read_text()
    after = yaml.safe_load(after_text)
    assert after["models"]["analyst"]["frontier"] == "bedrock/amazon.nova-lite-v1:0"
    assert after["models"]["analyst"]["fallback"] == "bedrock/amazon.nova-pro-v1:0@us-east-2"
    # Everything outside the models block is unchanged.
    before.pop("models")
    after.pop("models")
    assert before == after
    # The header comment survives, because the procedure documents itself in the file.
    assert "The development deployment's manifest" in after_text
    # And the result passes the same gates CI runs.
    checked = run("check", str(target))
    assert checked.returncode == 0, checked.stdout


def test_set_writes_a_list_for_a_chain(tmp_path):
    target = copy_manifest(tmp_path)
    result = run("set", str(target), "--role", "analyst",
                 "--model", "bedrock/us.anthropic.claude-haiku-4-5-20251001-v1:0",
                 "--fallback", "bedrock/us.anthropic.claude-haiku-4-5-20251001-v1:0@us-east-2",
                 "--fallback", "bedrock/amazon.nova-pro-v1:0")
    assert result.returncode == 0, result.stdout + result.stderr
    after = yaml.safe_load(target.read_text())
    assert after["models"]["analyst"]["fallback"] == [
        "bedrock/us.anthropic.claude-haiku-4-5-20251001-v1:0@us-east-2",
        "bedrock/amazon.nova-pro-v1:0"]


def test_set_refuses_a_legacy_model_and_leaves_the_file_alone(tmp_path):
    target = copy_manifest(tmp_path)
    before = target.read_bytes()
    result = run("set", str(target), "--role", "analyst",
                 "--model", "bedrock/anthropic.claude-sonnet-4-5-20250929-v1:0")
    assert result.returncode == 1
    assert "LEGACY" in result.stdout
    assert target.read_bytes() == before


def test_set_refuses_an_unknown_role(tmp_path):
    target = copy_manifest(tmp_path)
    result = run("set", str(target), "--role", "narrator", "--model", "bedrock/amazon.nova-pro-v1:0")
    assert result.returncode == 1
    assert "no role" in result.stdout


def test_set_refuses_a_malformed_region_before_writing(tmp_path):
    target = copy_manifest(tmp_path)
    before = target.read_bytes()
    result = run("set", str(target), "--role", "analyst",
                 "--model", "bedrock/amazon.nova-lite-v1:0",
                 "--fallback", "bedrock/amazon.nova-pro-v1:0@east")
    assert result.returncode == 1
    assert "not an AWS Region code" in result.stdout
    assert target.read_bytes() == before


def test_set_without_a_fallback_removes_it(tmp_path):
    target = copy_manifest(tmp_path)
    result = run("set", str(target), "--role", "analyst", "--model", "bedrock/amazon.nova-lite-v1:0")
    assert result.returncode == 0, result.stdout + result.stderr
    after = yaml.safe_load(target.read_text())
    assert "fallback" not in after["models"]["analyst"]
