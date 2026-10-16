"""Who owns the pieces, and where the credential can be read from.

`CON-OWNED-001` and `SEC-CON-001`, which are the sharp end of `BIZ-INDEPENDENT-001`: cut every cord to Office
Inference and the deployment still works.

**The ownership test has a negative control for each of the five components.** A check that passes on a
client-owned deployment proves nothing on its own; what makes it a check is that moving any one component to
us makes it fail. Each of the five is moved in turn below, and the failure has to name the component it is
about rather than reporting a generic problem.

**The credential test states its own limit.** There is no container runtime in this suite, so "read it from
inside the application container and fail" is proved by two facts instead: the application container declares
no access to the path, and the file's mode denies every account but its owner. Those are the two things that
make the read fail in a real deployment, and the run over real containers is a deployment step rather than a
unit test. It is named in the manual as the half that is not covered here.
"""
from __future__ import annotations

import os
import re
import stat
import sys
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tests" / "fixtures"))
sys.path.insert(0, str(ROOT / "services" / "intake"))

import intake_env  # noqa: E402

env = intake_env
intake = intake_env.load("intake")
config_module = intake_env.load("config")
net = intake_env.load("net")
provenance = intake_env.load("provenance")
secretstore = intake_env.load("secretstore")

INTAKE_DIR = ROOT / "services" / "intake"

# What a key looks like. The patterns are deliberately loose: a false positive here costs a look at a file,
# and a false negative costs a credential in a public repository.
KEY_PATTERNS = (
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
    re.compile(r'"client_secret"\s*:\s*"[^"]{8,}"'),
    re.compile(r'"refresh_token"\s*:\s*"[^"]{8,}"'),
    re.compile(r"client_secret\s*=\s*['\"][^'\"]{8,}['\"]"),
    re.compile(r"\beyJ[A-Za-z0-9_-]{20,}\.[A-Za-z0-9_-]{20,}\."),  # a JWT, which a client assertion is
)


# --------------------------------------------------------------------------- CON-OWNED-001

def test_the_five_components_all_belong_to_the_client(tmp_path):
    path = env.write_config(tmp_path)
    config = config_module.load(path)
    assert config.problems == [], config.problems

    rows = config_module.ownership(config)
    assert [row["component"] for row in rows] == [
        "intake address", "mailbox", "app registration", "consent grant", "credential"]
    for row in rows:
        assert row["owner"] == "client", f"{row['component']} is owned by {row['owner']}"


@pytest.mark.parametrize("mutation,expected_code", [
    # 1. The address. On a domain we control, which is the version of this design that was thrown away.
    ({"client": {"domains": ["officeinference.com"]}}, "vendor-domain"),
    # 2. The mailbox. A shared mailbox is the one most small businesses already have.
    ({"mailbox": {"kind": "shared"}}, "shared-mailbox"),
    # 3. The app registration, registered in a tenant that is not the deployment's. This is the realistic
    #    shape of the mistake: everything else is the client's and the app object is somewhere else.
    ({"identity": {"app": {"tenant_id": "99999999-9999-9999-9999-999999999999"}}}, "app-elsewhere"),
    # 4. The consent grant, recorded against us.
    ({"identity": {"consent": {"granted_by": "office_inference"}}}, "consent-not-clients"),
    # 5. The credential, in our store.
    ({"secret_store": {"owner": "office_inference"}}, "secret-not-clients"),
])
def test_moving_any_component_to_us_fails_the_check(tmp_path, mutation, expected_code):
    """Five negative controls, one per component. This is what makes the check a check."""
    path = env.write_config(tmp_path, **mutation)
    problems = config_module.load(path).problems
    codes = {problem.code for problem in problems}
    assert expected_code in codes, f"expected {expected_code}, got {sorted(codes)}: {problems}"


def test_a_deployment_in_our_own_tenant_is_refused(tmp_path, monkeypatch):
    """The case the consistency rules cannot catch on their own: a configuration that names our tenant
    everywhere and is internally consistent.

    **This is the half of the check that needs a value rather than a rule.** `VENDOR_TENANT_IDS` is empty in
    the checkout, so the rule is exercised here by setting it. Recording the real ids in `config.py` is the
    whole fix, and it belongs to whoever holds the business records. Until then this test proves the rule
    works and the manual says the value is missing, which is the difference between a gap that is visible and
    one that is not.
    """
    monkeypatch.setattr(config_module, "VENDOR_TENANT_IDS", ("99999999-9999-9999-9999-999999999999",))
    path = env.write_config(tmp_path, identity={
        "tenant_id": "99999999-9999-9999-9999-999999999999",
        "app": {"tenant_id": "99999999-9999-9999-9999-999999999999"},
        "consent": {"tenant_id": "99999999-9999-9999-9999-999999999999"}})
    codes = {problem.code for problem in config_module.load(path).problems}
    assert "tenant-is-ours" in codes


def test_an_address_on_a_domain_we_control_is_refused_by_name(tmp_path):
    """The exact design that was discarded, refused with the reason rather than a generic failure."""
    path = env.write_config(tmp_path, client={"domains": ["officeinference.com"]},
                            mailbox={"address": "invoices@client.officeinference.com"})
    messages = " ".join(str(problem) for problem in config_module.load(path).problems)
    assert "BIZ-INDEPENDENT-001" in messages or "Office Inference controls" in messages


def test_the_connector_asks_nothing_but_microsoft(tmp_path):
    """Every URL the connector actually requested, over a whole poll, on the client's own tenant.

    This is the cord check done by observation rather than by inspection: the list is what the code asked for,
    and every host on it has to be Microsoft's. Underneath it, `net.request` refuses anything else before a
    socket is opened, so this is the second of two independent controls.
    """
    server = env.FakeGraph(messages=env.sample_messages()).start()
    try:
        asked = env.install_recording(server)
        config_path = env.environment(tmp_path, server)
        env.write_credential(config_path)
        config = config_module.load(config_path)
        intake.poll_once(config, provenance.Store(config.state_dir))

        assert asked, "the poll made no requests at all, so this proves nothing"
        hosts = {url.split("/")[2] for url in asked}
        assert hosts == {"graph.microsoft.com", "login.microsoftonline.com"}, \
            f"the connector asked for {hosts}"
    finally:
        server.stop()
        net.set_transport(None)


def test_a_host_we_control_is_refused_before_a_socket_is_opened():
    """Proved by refusal rather than by reading the allowlist. If this stops raising, the control is gone."""
    with pytest.raises(net.HostRefused):
        net.request("https://intake.officeinference.com/v1.0/me/messages")
    with pytest.raises(net.HostRefused):
        net.request("https://licence.officeinference.com/check")


def test_cord_check_passes_on_a_client_configuration(tmp_path, capsys):
    path = env.write_config(tmp_path)
    assert intake.main(["cord-check", "--config", str(path)]) == 0
    printed = capsys.readouterr().out
    assert "no cord" in printed
    for component in ("intake address", "mailbox", "app registration", "consent grant", "credential"):
        assert component in printed


def test_cord_check_refuses_a_configuration_that_names_us(tmp_path, capsys):
    path = env.write_config(tmp_path, secret_store={"owner": "office_inference"})
    assert intake.main(["cord-check", "--config", str(path)]) == 1
    assert "FAIL" in capsys.readouterr().err


def test_a_licence_check_or_an_update_check_in_the_configuration_is_refused(tmp_path, capsys):
    """A licence server, a telemetry endpoint and an update check are all one configuration key away."""
    path = env.write_config(tmp_path, intake={"telemetry_endpoint": "https://metrics.example/v1"})
    assert intake.main(["cord-check", "--config", str(path)]) == 1
    assert "telemetry_endpoint" in capsys.readouterr().err


def test_a_plaintext_host_is_refused():
    """https only, so a redirect to a plaintext endpoint cannot be followed."""
    with pytest.raises(net.HostRefused):
        net.request("http://graph.microsoft.com/v1.0/me/messages")


# --------------------------------------------------------------------------- SEC-CON-001

def test_the_repository_holds_no_key():
    """Scan every file the service ships, including the Dockerfile and the example configuration."""
    hits = []
    for path in sorted(INTAKE_DIR.rglob("*")):
        if not path.is_file():
            continue
        text = path.read_text(errors="replace")
        for pattern in KEY_PATTERNS:
            if pattern.search(text):
                hits.append(f"{path.relative_to(ROOT)}: {pattern.pattern[:40]}")
    assert hits == [], f"key material in the service: {hits}"


def test_the_example_configuration_holds_no_credential():
    """The file a client copies must not carry a value that looks like one."""
    text = (INTAKE_DIR / "intake.example.yaml").read_text()
    assert "refresh_token:" not in text
    assert "client_secret:" not in text


def dockerfile_instructions() -> list[str]:
    """The Dockerfile's instructions, without the prose around them.

    **The first version of this test grepped the whole file and failed on a comment** that explained why there
    is no blanket copy, which is a test reading its own documentation. Comments are not instructions, and a
    check that cannot tell them apart is a check somebody weakens until it passes.
    """
    return [line.strip() for line in (INTAKE_DIR / "Dockerfile").read_text().splitlines()
            if line.strip() and not line.strip().startswith("#")]


def test_the_image_copies_no_credential_and_no_dotenv():
    instructions = dockerfile_instructions()
    assert "COPY . ." not in instructions, "a blanket copy is how a credential ends up in an image"
    assert not any(re.match(r"^ADD\s+\.\s", line) for line in instructions)
    assert not any("credential" in line.lower() for line in instructions)
    assert not any(re.search(r"\.env\b", line) for line in instructions)
    for line in instructions:
        if line.startswith(("ENV ", "ARG ")):
            # An ENV may name a path. It may not carry a value.
            assert not re.search(r"(secret|token|password)\s*=\s*\S+", line, re.I), line
    assert any("intake.py" in line for line in instructions), "the entrypoint's code is not copied"


def test_the_application_container_has_no_access_to_the_credential():
    """The api container is the one a client's staff reach through, so it is the one that must not hold it.

    Read out of the deployment's own compose file rather than from a description of it. What the file says is
    what the container gets, so this is the deployment rather than a proxy for it.
    """
    compose = yaml.safe_load((ROOT / "deploy" / "office" / "compose" / "docker-compose.yml").read_text())
    api = compose["services"]["api"]
    environment = api.get("environment") or {}
    names = " ".join(environment if isinstance(environment, dict) else environment)
    assert "INTAKE" not in names
    assert "SECRET_STORE" not in names
    assert "credential" not in names.lower()
    mounts = [str(volume) for volume in (api.get("volumes") or [])]
    assert not any("oi-intake" in mount or "credential" in mount for mount in mounts), mounts


def test_the_credential_is_read_at_the_point_of_use_and_a_rotation_takes_effect(tmp_path):
    """A rotation by the client's administrator, with no restart and no redeploy.

    The test asserts on what the token endpoint was *presented*, which is the only place the difference
    between a cached credential and a read one is visible.
    """
    server = env.FakeGraph(messages=env.sample_messages()).start()
    try:
        env.install(server)
        config_path = env.environment(tmp_path, server)
        env.write_credential(config_path)
        config = config_module.load(config_path)
        store = provenance.Store(config.state_dir)

        intake.poll_once(config, store)
        assert server.token_requests[-1]["client_secret"] == "not-a-real-secret"

        # The administrator rotates the secret while the connector keeps running.
        rotated = yaml.safe_load(config_path.read_text())
        secretstore.write(rotated["secret_store"]["path"], {
            "client_id": rotated["identity"]["app"]["client_id"],
            "tenant_id": rotated["identity"]["tenant_id"],
            "refresh_token": "refresh-token-rotated",
            "client_secret": "the-new-secret",
        })

        intake.poll_once(config, store)
        assert server.token_requests[-1]["client_secret"] == "the-new-secret"
        assert server.token_requests[-1]["refresh_token"] == "refresh-token-rotated"
    finally:
        server.stop()
        net.set_transport(None)


def test_a_credential_another_account_can_read_is_refused(tmp_path):
    """Mode 0644 is what a script with a default umask writes. Reading it would be the failure."""
    path = tmp_path / "credential.json"
    secretstore.write(path, {"client_id": "c", "tenant_id": "t", "refresh_token": "r"}, mode=0o600)
    assert secretstore.load(path).refresh_token == "r"

    os.chmod(path, 0o644)
    with pytest.raises(secretstore.CredentialRefused) as refused:
        secretstore.load(path)
    assert "other than the one running" in str(refused.value)


def test_the_credential_never_reaches_the_record(tmp_path):
    """`credential_path` and `credential_mode` are recorded. The value is not, anywhere."""
    server = env.FakeGraph(messages=env.sample_messages()).start()
    try:
        env.install(server)
        config_path = env.environment(tmp_path, server)
        env.write_credential(config_path)
        config = config_module.load(config_path)
        store = provenance.Store(config.state_dir)
        intake.poll_once(config, store)

        journal = store.journal.read_text()
        assert "not-a-real-secret" not in journal
        assert "refresh-token" not in journal
        row = next(row for row in store.entries() if row["status"] == "deposited")
        assert row["credential_path"].endswith("credential.json")
        assert row["credential_mode"] == "0600"
    finally:
        server.stop()
        net.set_transport(None)


def test_no_secret_is_read_from_the_environment():
    """An environment variable is visible in `docker inspect` and in `/proc/<pid>/environ`."""
    offenders = []
    for path in sorted(INTAKE_DIR.glob("*.py")):
        for number, line in enumerate(path.read_text().splitlines(), start=1):
            if "environ" not in line:
                continue
            if re.search(r"environ[^\n]*(secret|token|password|credential)", line, re.I):
                offenders.append(f"{path.name}:{number}")
    assert offenders == [], f"a secret is read from the environment: {offenders}"


def test_the_credential_file_is_the_only_thing_the_service_needs_to_read():
    """The path is configuration, and the value is in the file the client's administrator controls."""
    text = (INTAKE_DIR / "intake.py").read_text()
    assert "config.secret_path" in text
    assert "secretstore.load" in text
