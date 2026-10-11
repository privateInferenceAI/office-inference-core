"""Tests for the manifest renderer.

The renderer turns a manifest into the files a deployment reads, and the failure these tests guard
against is a cloud manifest rendering an empty model list. That happened: the renderer understood
only local models, so the deployment we build first had no gateway entries at all, and nothing
noticed until someone read the file.

Run: python3 -m pytest tests/
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
RENDERER = ROOT / "scripts" / "render_manifest.py"
MANIFESTS = sorted((ROOT / "manifests").glob("*.yaml"))
CLOUD = ROOT / "manifests" / "example-standard-cloud.yaml"
OFFICE = ROOT / "manifests" / "dev-office.yaml"


def render(manifest: Path, out: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(RENDERER), "--manifest", str(manifest), "--out", str(out)],
        capture_output=True,
        text=True,
        cwd=ROOT,
    )


def gateway_entries(out: Path) -> list[dict]:
    return yaml.safe_load((out / "litellm.yaml").read_text())["model_list"]


def env_values(out: Path) -> dict[str, str]:
    values = {}
    for line in (out / "env").read_text().splitlines():
        if "=" in line:
            key, _, value = line.partition("=")
            values[key] = value
    return values


@pytest.mark.parametrize("manifest", MANIFESTS, ids=lambda path: path.stem)
def test_every_shipped_manifest_renders_a_gateway_config(manifest, tmp_path):
    result = render(manifest, tmp_path)
    assert result.returncode == 0, result.stderr

    entries = gateway_entries(tmp_path)
    assert entries, f"{manifest.name} rendered an empty model list"

    names = [entry["model_name"] for entry in entries]
    assert len(names) == len(set(names)), "the gateway config has a duplicate model_name"

    assert (tmp_path / "manifest.json").exists()
    assert (tmp_path / "services.txt").read_text().strip(), "no services were declared"


def test_a_cloud_manifest_renders_one_frontier_entry_per_role(tmp_path):
    """The exact regression. A cloud manifest has no local models, so the frontier lane is the
    only thing that can produce gateway entries. If it does not, the list comes out empty."""
    assert render(CLOUD, tmp_path).returncode == 0

    entries = {entry["model_name"]: entry for entry in gateway_entries(tmp_path)}
    assert set(entries) == {"analyst", "front_desk", "reader"}
    for entry in entries.values():
        assert entry["model_info"]["lane"] == "frontier"
        assert entry["litellm_params"]["model"].startswith("bedrock/")


def test_an_office_manifest_keeps_its_local_models(tmp_path):
    manifest = yaml.safe_load(OFFICE.read_text())
    declared = {entry["local"] for entry in manifest["models"].values() if entry and entry.get("local")}
    assert declared, "dev-office.yaml is supposed to declare local models"

    assert render(OFFICE, tmp_path).returncode == 0

    rendered = {entry["litellm_params"]["model"] for entry in gateway_entries(tmp_path)}
    for model_file in declared:
        assert f"openai/{model_file}" in rendered


def test_the_env_file_is_readable_only_by_its_owner(tmp_path):
    assert render(OFFICE, tmp_path).returncode == 0
    mode = (tmp_path / "env").stat().st_mode & 0o777
    assert mode == 0o600, f"the env file holds secrets and is mode {oct(mode)}"


def test_rerendering_preserves_the_existing_secrets(tmp_path):
    """The renderer is idempotent on purpose. Regenerating the database password would lock the
    stack out of its own data."""
    assert render(OFFICE, tmp_path).returncode == 0
    first = env_values(tmp_path)

    second_run = render(OFFICE, tmp_path)
    assert second_run.returncode == 0
    second = env_values(tmp_path)

    assert first["POSTGRES_PASSWORD"] == second["POSTGRES_PASSWORD"]
    assert first["LITELLM_MASTER_KEY"] == second["LITELLM_MASTER_KEY"]
    assert "reused" in second_run.stdout


def test_no_key_material_reaches_the_gateway_config(tmp_path):
    """The gateway reads the master key from the environment. A literal key in litellm.yaml would
    end up wherever that file is copied."""
    assert render(OFFICE, tmp_path).returncode == 0
    text = (tmp_path / "litellm.yaml").read_text()
    assert "os.environ/LITELLM_MASTER_KEY" in text
    assert "sk-" not in text


def test_an_office_build_turns_the_local_lane_on(tmp_path):
    """The local lane's container sits behind a compose profile, so the rendered env has to ask
    for it or the office build starts with no model to answer."""
    assert render(OFFICE, tmp_path).returncode == 0
    assert env_values(tmp_path)["COMPOSE_PROFILES"] == "office"


def test_a_cloud_build_leaves_the_local_lane_off(tmp_path):
    """The cloud build sends every generation call to Bedrock, so llama.cpp must not start. The
    renderer removes the profile rather than leaving it unset, so rendering a cloud manifest over
    an office env file cannot leave the local lane running."""
    assert render(CLOUD, tmp_path).returncode == 0
    values = env_values(tmp_path)
    assert "COMPOSE_PROFILES" not in values
    # And no local-lane settings either, so the env file does not imply a lane this build lacks.
    for key in ("MODEL_FILE", "MODEL_CTX", "MODEL_THREADS"):
        assert key not in values, f"{key} belongs to the local lane"


def test_the_bedrock_region_reaches_the_env_file(tmp_path):
    """Bedrock needs a Region and the gateway reads it from the environment. Without it the
    container cannot resolve an endpoint, which looks like a credential failure."""
    assert render(CLOUD, tmp_path).returncode == 0
    values = env_values(tmp_path)
    assert values["AWS_REGION"]
    assert values["AWS_DEFAULT_REGION"] == values["AWS_REGION"]


def test_the_dev_cloud_manifest_routes_every_role_to_bedrock(tmp_path):
    """The cloud build's smoke-test manifest. Four roles, four Bedrock entries, no local lane."""
    dev_cloud = ROOT / "manifests" / "dev-cloud.yaml"
    assert render(dev_cloud, tmp_path).returncode == 0

    entries = gateway_entries(tmp_path)
    assert {entry["model_name"] for entry in entries} == {"front_desk", "analyst", "reader", "coder"}
    for entry in entries:
        assert entry["model_info"]["lane"] == "frontier"
        assert entry["litellm_params"]["model"].startswith("bedrock/")
