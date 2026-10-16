"""What the credential can reach, enumerated.

`CON-LEAST-001` and `SEC-INTAKE-002`, and the register says why both are tested by enumeration rather than by
assertion: "the test enumerates what the credential can reach rather than asserting the permission it was
granted, because the two differ when a scope is additive."

**The negative control is the point of this file.** `test_a_credential_that_reaches_a_second_mailbox_is_caught`
gives the fake tenant the semantics of an application permission, which is what a deployment looks like when
somebody grants `Mail.Read` tenant-wide, and asserts that the enumeration notices. Without that test, an
enumeration that returned "refused" for everything would pass.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tests" / "fixtures"))
sys.path.insert(0, str(ROOT / "services" / "intake"))

import intake_env  # noqa: E402

env = intake_env
intake = intake_env.load("intake")
config_module = intake_env.load("config")
graph = intake_env.load("graph")
net = intake_env.load("net")
reach = intake_env.load("reach")
secretstore = intake_env.load("secretstore")


def report_for(tmp_path, server):
    env.install(server)
    config_path = env.environment(tmp_path, server)
    env.write_credential(config_path)
    config = config_module.load(config_path)
    assert not config.problems, config.problems
    token = graph.acquire_token(config, secretstore.load(config.secret_path))
    return reach.enumerate_reach(config, token)


@pytest.fixture
def tenant(tmp_path):
    server = env.FakeGraph(messages=env.sample_messages()).start()
    yield server, tmp_path
    server.stop()
    net.set_transport(None)


def probes_by_name(report):
    return {probe["name"]: probe for probe in report["probes"]}


# --------------------------------------------------------------------------- CON-LEAST-001

def test_exactly_one_probe_reaches_anything(tenant):
    """One mailbox. Everything else is refused, and the report says which is which."""
    server, tmp_path = tenant
    report = report_for(tmp_path, server)

    assert report["ok"] is True, report["probes"] + report["scope_problems"]
    assert report["reached"] == ["own_mailbox"], report["reached"]

    probes = probes_by_name(report)
    for name in ("second_mailbox", "directory", "profile", "other_service", "vendor_host"):
        assert probes[name]["observed"] == "refused", f"{name} was reached"


def test_the_report_is_an_enumeration_rather_than_a_permission_list(tenant):
    """Every probe carries what actually came back, with the status, so it can be read as evidence."""
    server, tmp_path = tenant
    probes = probes_by_name(report_for(tmp_path, server))
    assert probes["own_mailbox"]["status"] == 200
    assert probes["second_mailbox"]["status"] == 403
    assert probes["directory"]["status"] == 403
    assert probes["vendor_host"]["status"] is None, "the allowlist refused before any status existed"
    assert "allowlist" in probes["vendor_host"]["detail"]


def test_a_credential_that_reaches_a_second_mailbox_is_caught(tmp_path):
    """The negative control, and the whole reason the enumeration exists.

    `application_permission` gives the fake tenant the semantics of `Mail.Read` granted application-wide:
    `/users/{any}/messages` answers. That is a deployment an administrator can build by completing three of
    the four Exchange RBAC steps, and the declared permission would still read `delegated` in the
    configuration. This test is what makes the enumeration worth running.
    """
    server = env.FakeGraph(messages=env.sample_messages()).start()
    server.application_permission = True
    try:
        report = report_for(tmp_path, server)
        assert report["ok"] is False
        probes = probes_by_name(report)
        assert probes["second_mailbox"]["observed"] == "reached"
        assert probes["directory"]["observed"] == "reached"
        assert "second_mailbox" in report["reached"]
    finally:
        server.stop()
        net.set_transport(None)


def test_a_write_scope_in_the_grant_is_reported(tmp_path):
    """The scopes the token actually came back with, checked against the two this deployment uses."""
    server = env.FakeGraph(messages=env.sample_messages(),
                           scopes="Mail.Read Mail.ReadWrite offline_access").start()
    try:
        report = report_for(tmp_path, server)
        assert report["ok"] is False
        assert any("Mail.ReadWrite" in problem for problem in report["scope_problems"])
    finally:
        server.stop()
        net.set_transport(None)


def test_a_shared_mailbox_cannot_even_be_configured(tmp_path):
    """It is refused at configuration time, before a credential is read, because it cannot sign in at all."""
    path = env.write_config(tmp_path, mailbox={"kind": "shared"})
    codes = {problem.code for problem in config_module.load(path).problems}
    assert "shared-mailbox" in codes


def test_an_application_permission_is_refused_before_anything_runs(tmp_path):
    """The scoping mechanism is additive, so an administrator can build something that looks scoped and is not.

    The refusal names the mechanism rather than only the rule, because the reason is what stops somebody
    "fixing" this by adding the Exchange scope on top.
    """
    path = env.write_config(tmp_path, identity={"permission": {"type": "application"}})
    problems = config_module.load(path).problems
    assert any(problem.code == "application-permission" for problem in problems)
    text = " ".join(str(problem) for problem in problems)
    assert "additive" in text and "PowerShell" in text


def test_the_refused_scopes_are_refused_by_name(tmp_path):
    """A denylist would stop covering the problem the first time Microsoft added a permission."""
    for scope, word in (("Mail.ReadWrite", "delete"), ("Mail.Send", "sends"), ("Mail.ReadBasic", "unusable")):
        path = env.write_config(tmp_path, identity={
            "permission": {"type": "delegated", "scopes": ["Mail.Read", "offline_access", scope]}})
        problems = " ".join(str(problem) for problem in config_module.load(path).problems)
        assert scope in problems and word in problems, f"{scope} was not refused with its reason"


# --------------------------------------------------------------------------- SEC-INTAKE-002

def test_a_write_method_against_the_mail_api_is_refused_before_it_is_sent():
    """The credential is read-only in the code as well as in the tenant. Both, because either can be widened."""
    with pytest.raises(net.MethodRefused):
        net.request("https://graph.microsoft.com/v1.0/me/messages/abc", method="DELETE")
    with pytest.raises(net.MethodRefused):
        net.request("https://graph.microsoft.com/v1.0/me/sendMail", method="POST")


def test_every_refusal_came_from_the_tenant_rather_than_from_our_own_code(tenant):
    """Four of the six probes are refused by Graph, and one by the allowlist. The distinction is recorded."""
    server, tmp_path = tenant
    report = report_for(tmp_path, server)
    probes = probes_by_name(report)
    for name in ("second_mailbox", "directory", "profile", "other_service"):
        assert "HTTP 403" in probes[name]["detail"], f"{name}: {probes[name]['detail']}"
    assert "allowlist" in probes["vendor_host"]["detail"]


def test_the_cli_exits_non_zero_when_something_is_reachable(tmp_path, capsys):
    server = env.FakeGraph(messages=env.sample_messages()).start()
    server.application_permission = True
    try:
        env.install(server)
        config_path = env.environment(tmp_path, server)
        env.write_credential(config_path)
        assert intake.main(["enumerate-reach", "--config", str(config_path)]) == 1
    finally:
        server.stop()
        net.set_transport(None)


def test_the_cli_exits_zero_on_a_deployment_that_is_scoped(tmp_path):
    server = env.FakeGraph(messages=env.sample_messages()).start()
    try:
        env.install(server)
        config_path = env.environment(tmp_path, server)
        env.write_credential(config_path)
        assert intake.main(["enumerate-reach", "--config", str(config_path)]) == 0
    finally:
        server.stop()
        net.set_transport(None)
