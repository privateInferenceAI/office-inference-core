"""Tests for the manifest renderer.

The renderer turns a manifest into the files a deployment reads, and the failure these tests guard
against is a cloud manifest rendering an empty model list. That happened: the renderer understood
only local models, so the deployment we build first had no gateway entries at all, and nothing
noticed until someone read the file.

Run: python3 -m pytest tests/
"""
from __future__ import annotations

import json
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
# The manifest this deployment actually renders, and the only one that declares an offsite
# destination. The example manifests stay illustrative.
DEV_CLOUD = ROOT / "manifests" / "dev-cloud.yaml"
WORKSPACE = ROOT / "manifests" / "dev-cloud-workspace.yaml"


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
    """The gateway gets its credentials from the environment. A literal key in litellm.yaml would end up
    wherever that file is copied.

    **Two assertions in this test were passing on absences and one was wrong, all found the same day.** The
    original checked that the substring `sk-` was absent, which passed only because nothing in the file
    contained it; the gateway then gained an identity hook whose module path is `custom_auth.user_api_key_auth`,
    which contains `sk-` inside `api_key_auth`, and the test failed on a legitimate change. It also asserted
    that the string `os.environ/LITELLM_MASTER_KEY` appeared, which nothing here ever wrote: the config is
    static YAML and the gateway reads the master key from its own environment, so that assertion was checking
    for text that only a different design would produce.

    What it checks now is what the name says: the secret the renderer minted is not in this file, and nothing
    key-shaped is either.
    """
    assert render(OFFICE, tmp_path).returncode == 0
    text = (tmp_path / "litellm.yaml").read_text()

    # The real secret, which belongs in the env file beside it and nowhere else.
    env = (tmp_path / "env").read_text()
    master = next(line.split("=", 1)[1] for line in env.splitlines()
                  if line.startswith("LITELLM_MASTER_KEY="))
    assert master not in text

    # And the shape of a provider key, which is what the original assertion was reaching for.
    import re
    assert not re.search(r"sk-[A-Za-z0-9_-]{20,}", text), "a key-shaped string reached the gateway config"

    # The identity hook is configured, which is the reason this file mentions a key at all.
    assert "custom_auth:" in text

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
    # The four role entries, plus the analyst fallback the manifest declares. A fallback is a second
    # Bedrock deployment of the same lane, so the "no local lane" claim still holds for every entry.
    assert {entry["model_name"] for entry in entries} == {
        "front_desk", "analyst", "reader", "coder", "analyst-fallback"}
    for entry in entries:
        assert entry["model_info"]["lane"] == "frontier"
        assert entry["litellm_params"]["model"].startswith("bedrock/")


def test_an_offsite_destination_reaches_the_env_file(tmp_path):
    """The shipping job reads its destination from the environment, because a client's bucket name
    comes from their Terraform and nothing is typed on a server."""
    assert render(DEV_CLOUD, tmp_path).returncode == 0
    values = env_values(tmp_path)
    assert values["AUDIT_SHIP_BUCKET"] == "oi-core-audit-dev"
    assert values["AUDIT_SHIP_ARTIFACTS"] == "anchors,exports"
    assert values["AUDIT_SHIP_REGION"] == "us-east-1"


def test_a_manifest_without_a_destination_gets_no_shipping_settings(tmp_path):
    """An office build with no account keeps everything on the box, and the env file says so by
    saying nothing. Rendering a cloud manifest over an office env file must also clear the settings,
    or a box could inherit a destination it was never given."""
    assert render(OFFICE, tmp_path).returncode == 0
    for key in ("AUDIT_SHIP_BUCKET", "AUDIT_SHIP_ARTIFACTS", "AUDIT_SHIP_REGION"):
        assert key not in env_values(tmp_path), f"{key} should be absent"

    assert render(DEV_CLOUD, tmp_path).returncode == 0
    assert env_values(tmp_path)["AUDIT_SHIP_BUCKET"] == "oi-core-audit-dev"

    assert render(OFFICE, tmp_path).returncode == 0
    for key in ("AUDIT_SHIP_BUCKET", "AUDIT_SHIP_ARTIFACTS", "AUDIT_SHIP_REGION"):
        assert key not in env_values(tmp_path), f"{key} survived a render with no destination"


def test_a_sellable_pack_pulls_in_core(tmp_path):
    """The catalog says every sellable pack requires core. Until the renderer followed those edges, a
    manifest listing only sellable packs produced a service list with no gateway, no database and no
    audit sink: everything a client bought, and nothing that makes it work."""
    manifest = tmp_path / "packs-only.yaml"
    data = yaml.safe_load(CLOUD.read_text())
    data["packs"] = ["document-chat"]
    manifest.write_text(yaml.safe_dump(data))
    out = tmp_path / "rendered"

    result = render(manifest, out)
    assert result.returncode == 0, result.stderr

    services = out.joinpath("services.txt").read_text()
    declared = [line for line in services.splitlines() if line and not line.startswith("#")]
    for required in ("gateway", "database", "audit-sink"):
        assert required in declared, f"{required} came from core and should be here: {declared}"
    assert "ingestion" in declared, "and the pack the client bought is still there"


def test_the_render_names_the_files_it_writes(tmp_path):
    """Seven of them, and the manual says so.

    `access.json` is the permission model the session tool and the retriever both read (SEC-ACL-002), and
    `assertion.key` is the signing key the API mints model-call assertions with and the gateway verifies them
    against. Both are listed here rather than added quietly, because this test exists so that a file appearing
    in a deployment's rendered directory is a decision somebody wrote down.
    """
    out = tmp_path / "rendered"
    assert render(DEV_CLOUD, out).returncode == 0
    written = sorted(p.name for p in out.iterdir())
    assert written == ["access.json", "agent_definitions.json", "assertion.key", "env",
                       "litellm.yaml", "manifest.json", "services.txt"], written


def test_a_deployment_without_the_agentic_pack_has_no_definitions(tmp_path):
    """The refusal is data, not a hidden default: no pack means an empty list and the API has nothing to run."""
    assert render(DEV_CLOUD, tmp_path).returncode == 0
    rendered = json.loads((tmp_path / "agent_definitions.json").read_text())
    assert rendered["definitions"] == []
    assert rendered["runtime_count"] == 1
    assert env_values(tmp_path)["OI_AGENT_DEFINITIONS"] == "/app/rendered/agent_definitions.json"


def test_the_agentic_pack_renders_its_definitions_and_their_budgets(tmp_path):
    """AG-DEFS-001: the definition carries the role, the tools and the budgets the runtime will enforce."""
    assert render(WORKSPACE, tmp_path).returncode == 0
    rendered = json.loads((tmp_path / "agent_definitions.json").read_text())
    definitions = {entry["id"]: entry for entry in rendered["definitions"]}
    assert set(definitions) == {"research_brief"}
    brief = definitions["research_brief"]
    assert brief["role"] == "analyst"
    assert brief["tools"] == ["search_documents", "read_passages", "query_rows"]
    assert brief["child_tools"] == ["search_documents", "read_passages"]
    assert brief["max_steps"] == 12 and brief["max_seconds"] == 600
    assert "send_email" in brief["irreversible_tools"]

    normalized = json.loads((tmp_path / "manifest.json").read_text())
    assert normalized["agentic"]["definitions"][0]["id"] == "research_brief"
    assert normalized["agentic"]["runtime_count"] == 1
    assert env_values(tmp_path)["OI_AGENT_RUNTIME_COUNT"] == "1"



def test_the_denial_policy_reaches_the_env_file(tmp_path):
    """A client's declared policy has to arrive where the retriever reads it.

    This is the wiring that rots silently: the retriever reads `ACCESS_DENIAL_MODE` from the environment and
    falls back to its own default if the variable is absent, so a renderer that stopped writing it would
    leave every deployment on `denied` while its manifest said `silent`. The wrong behaviour would be a
    disclosure the client had explicitly turned off, and nothing would report it.
    """
    out = tmp_path / "rendered"
    out.mkdir()
    result = render(DEV_CLOUD, out)
    assert result.returncode == 0, result.stderr
    env = dict(
        line.split("=", 1)
        for line in (out / "env").read_text().splitlines()
        if line and not line.startswith("#") and "=" in line
    )
    assert "ACCESS_DENIAL_MODE" in env, "the retriever reads this variable and would fall back silently"
    # The dev cloud manifest declares counted, so the renderer must carry that rather than the default.
    assert env["ACCESS_DENIAL_MODE"] == "counted"

    out2 = tmp_path / "rendered-office"
    out2.mkdir()
    assert render(OFFICE, out2).returncode == 0
    env2 = dict(
        line.split("=", 1)
        for line in (out2 / "env").read_text().splitlines()
        if line and not line.startswith("#") and "=" in line
    )
    assert env2["ACCESS_DENIAL_MODE"] == "denied"


def test_every_shipped_manifest_declares_a_valid_denial_mode():
    """A typo would be refused at runtime by the retriever, which is the right behaviour and a bad way to
    find out. The schema validates the enum, so this checks the shipped manifests actually satisfy it."""
    for manifest in MANIFESTS:
        declared = yaml.safe_load(manifest.read_text()).get("access") or {}
        if "denial" in declared:
            assert declared["denial"] in ("silent", "denied", "counted"), (
                f"{manifest.name} declares denial: {declared['denial']!r}")


def test_the_fault_endpoint_is_unset_by_default(tmp_path):
    """**A rendered config carrying this would send every model call to a stub**, and the deployment would look
    healthy while answering from a test double. So the default is asserted rather than assumed."""
    out = tmp_path / "rendered"
    result = render(DEV_CLOUD, out)
    assert result.returncode == 0
    assert "api_base" not in (out / "litellm.yaml").read_text()


def test_the_fault_endpoint_redirects_every_model_when_asked(tmp_path, monkeypatch):
    """The hook the fault-injection stub is reached through."""
    monkeypatch.setenv("OI_FAULT_ENDPOINT", "http://stub:8099")
    out = tmp_path / "rendered"
    result = render(DEV_CLOUD, out)
    assert result.returncode == 0
    text = (out / "litellm.yaml").read_text()
    assert text.count("api_base: http://stub:8099") >= 4, "every model entry should carry it"
    assert "FAULT" in result.stdout, "and the run should say so out loud"


def test_a_declared_fallback_renders_a_group_and_the_failover_setting(tmp_path):
    """The gateway does the failover, so the rendered config needs both halves: the second model group and
    the setting that names it. A group without the setting is a model nobody calls; a setting without the
    group is a failover to nothing."""
    manifest = yaml.safe_load(DEV_CLOUD.read_text())
    manifest["models"]["reader"]["fallback"] = "bedrock/amazon.nova-lite-v1:0"
    path = tmp_path / "with-fallback.yaml"
    path.write_text(yaml.safe_dump(manifest), encoding="utf-8")

    out = tmp_path / "rendered"
    result = render(path, out)
    assert result.returncode == 0, result.stderr

    config = yaml.safe_load((out / "litellm.yaml").read_text())
    names = [entry["model_name"] for entry in config["model_list"]]
    assert "reader-fallback" in names
    fallback = next(entry for entry in config["model_list"] if entry["model_name"] == "reader-fallback")
    assert fallback["litellm_params"]["model"] == "bedrock/amazon.nova-lite-v1:0"
    assert fallback["model_info"]["fallback_for"] == "reader"
    # The shipped dev-cloud manifest already declares an analyst fallback, so this asserts the reader
    # mapping is present without pretending it is the only one.
    assert {"reader": ["reader-fallback"]} in config["litellm_settings"]["fallbacks"]


def test_an_office_build_renders_no_fallback(tmp_path):
    """The office build has no frontier lane, and a remote failover from a local primary is the egress path
    the validator refuses. The renderer must not emit one either, so a manifest that slipped past the
    validator still cannot produce a config that sends content off the box."""
    manifest = yaml.safe_load(OFFICE.read_text())
    manifest["models"]["analyst"]["fallback"] = "bedrock/amazon.nova-pro-v1:0"
    path = tmp_path / "office-with-fallback.yaml"
    path.write_text(yaml.safe_dump(manifest), encoding="utf-8")

    out = tmp_path / "rendered"
    result = render(path, out)
    assert result.returncode != 0, "the renderer runs the validator, so this manifest is refused"
    assert "cloud-only" in result.stdout, result.stdout

    # And the guard inside the builder holds even if the manifest reached it, which is what keeps a
    # validator change from turning into an egress path.
    import importlib.util
    spec = importlib.util.spec_from_file_location("renderer_under_test", RENDERER)
    renderer = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(renderer)
    built = renderer.build_litellm_config(manifest, {})
    assert "analyst-fallback" not in [entry["model_name"] for entry in built["model_list"]]
    assert "fallbacks" not in built["litellm_settings"]
