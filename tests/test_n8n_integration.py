"""The n8n integration registry, signatures and the per-workflow privilege bound.

These tests are about the two things that must not be wrong before any workflow runs: the signature check,
because it is the only authentication on the integration route, and the owner-bound root validation,
because it is the per-workflow privilege guarantee. The route and the live n8n flows are exercised by the
deployment acceptance record, not here.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
for extra in (ROOT / "services", ROOT / "services" / "api", ROOT / "services" / "session"):
    if str(extra) not in sys.path:
        sys.path.insert(0, str(extra))

import n8n_integrations as ni  # noqa: E402
import session as session_module  # noqa: E402


# --------------------------------------------------------------------------- registry loading

def write_registry(tmp_path, entries) -> Path:
    p = tmp_path / "n8n_integrations.json"
    p.write_text(json.dumps({"integrations": entries}))
    return p


def entry(**kw):
    base = {
        "id": "scheduled_report",
        "secret": "s" * 48,
        "task": "summarise_across_documents",
        "role": "viewer",
        "roots": ["company"],
        "owner_subject": "zoe.reyes@northwind.example",
    }
    base.update(kw)
    return base


def test_missing_registry_means_no_integrations(tmp_path):
    assert ni.load(tmp_path / "absent.json") == {}


def test_valid_registry_loads(tmp_path):
    loaded = ni.load(write_registry(tmp_path, [entry()]))
    assert set(loaded) == {"scheduled_report"}
    integration = loaded["scheduled_report"]
    assert integration.roots == frozenset({"company"})
    assert integration.automation_subject("oi-core-product") == \
        "automation+scheduled_report@oi-core-product"


@pytest.mark.parametrize("broken, message", [
    ([entry(id="Bad Id")], "invalid id"),
    ([entry(id="dup"), entry(id="dup", task="draft")], "appears twice"),
    ([entry(secret="short")], "at least 32"),
    ([entry(task="")], "task"),
    ([entry(role="")], "role"),
    ([entry(roots=[])], "document root"),
    ([entry(roots=["  "])], "empty document root"),
    ([entry(owner_subject="")], "owner"),
])
def test_invalid_registry_is_refused(tmp_path, broken, message):
    with pytest.raises(ni.RegistryError) as refused:
        ni.load(write_registry(tmp_path, broken))
    assert message in str(refused.value)


def test_registry_that_is_not_an_object_is_refused(tmp_path):
    p = tmp_path / "n8n_integrations.json"
    p.write_text(json.dumps(["not", "a", "registry"]))
    with pytest.raises(ni.RegistryError):
        ni.load(p)


# --------------------------------------------------------------------------- signatures

def integration():
    return ni.Integration(id="scheduled_report", secret="s" * 48, task="draft", role="viewer",
                          roots=frozenset({"company"}), owner_subject="zoe@example.com")


def test_valid_signature_is_accepted():
    secret = integration().secret
    body = b'{"input":"good morning"}'
    timestamp = "1760000000"
    signature = "sha256=" + ni.recompute_signature(secret, timestamp, body)
    ni.verify_request(integration(), timestamp=timestamp, signature=signature, body=body,
                      now=1760000000)


def test_wrong_signature_is_refused():
    body = b'{"input":"good morning"}'
    with pytest.raises(ni.IntegrationRefused) as refused:
        ni.verify_request(integration(), timestamp="1760000000", signature="sha256=" + "0" * 64,
                          body=body, now=1760000000)
    assert "does not match" in str(refused.value)


def test_missing_headers_are_refused():
    with pytest.raises(ni.IntegrationRefused):
        ni.verify_request(integration(), timestamp="", signature="", body=b"{}", now=1760000000)


def test_stale_timestamp_is_refused():
    body = b"{}"
    timestamp = "1760000000"
    signature = ni.recompute_signature(integration().secret, timestamp, body)
    with pytest.raises(ni.IntegrationRefused) as refused:
        ni.verify_request(integration(), timestamp=timestamp, signature=signature, body=body,
                          now=1760000000 + ni.MAX_SKEW_SECONDS + 1)
    assert "window" in str(refused.value)


def test_signature_covers_the_body():
    integration_obj = integration()
    timestamp = "1760000000"
    good = ni.recompute_signature(integration_obj.secret, timestamp, b'{"input":"a"}')
    with pytest.raises(ni.IntegrationRefused):
        ni.verify_request(integration_obj, timestamp=timestamp, signature=good,
                          body=b'{"input":"b"}', now=1760000000)


# --------------------------------------------------------------------------- the owner bound

def access_registry():
    return {
        "users": [
            {"subject": "zoe.reyes@northwind.example", "groups": ["staff"]},
            {"subject": "adam.hale@northwind.example", "groups": ["exec"]},
        ],
        "groups": {
            "staff": {"roots": ["company"]},
            "exec": {"roots": ["company", "executive"]},
        },
    }


def test_owner_roots_resolve_through_groups():
    assert ni.owner_roots(access_registry(), "zoe.reyes@northwind.example") == frozenset({"company"})
    assert ni.owner_roots(access_registry(), "adam.hale@northwind.example") == \
        frozenset({"company", "executive"})
    assert ni.owner_roots(access_registry(), "nobody@example.com") is None


def test_a_workflow_cannot_exceed_its_owners_roots():
    staff_owned = ni.Integration(id="x", secret="s" * 48, task="draft", role="viewer",
                                 roots=frozenset({"company", "executive"}),
                                 owner_subject="zoe.reyes@northwind.example")
    problems = ni.owner_problems(staff_owned, access_registry())
    assert problems and "does not hold root" in problems[0]


def test_an_executive_owner_may_provision_an_executive_workflow():
    exec_owned = ni.Integration(id="x", secret="s" * 48, task="draft", role="viewer",
                                roots=frozenset({"company", "executive"}),
                                owner_subject="adam.hale@northwind.example")
    assert ni.owner_problems(exec_owned, access_registry()) == []


def test_an_unknown_owner_is_refused():
    unknown = ni.Integration(id="x", secret="s" * 48, task="draft", role="viewer",
                             roots=frozenset({"company"}), owner_subject="nobody@example.com")
    problems = ni.owner_problems(unknown, access_registry())
    assert problems and "not declared" in problems[0]


def test_registry_shapes_are_read_without_guessing():
    dict_users = {
        "users": {"zoe.reyes@northwind.example": {"groups": ["staff"]}},
        "groups": {"staff": ["company"]},
    }
    assert ni.owner_roots(dict_users, "zoe.reyes@northwind.example") == frozenset({"company"})


# --------------------------------------------------------------------------- the service session

def test_service_session_carries_the_job_snapshot():
    session = session_module.service_session(
        subject="automation+scheduled_report@oi-core-product", role="viewer",
        roots={"company"}, session_id="job-abc", deployment_id="oi-core-product")
    permit = session.permit()
    assert permit.subject == "automation+scheduled_report@oi-core-product"
    assert permit.role == "viewer"
    assert permit.roots == frozenset({"company"})
    assert session.credential_sha256 == ""


@pytest.mark.parametrize("kw", [
    dict(subject="", role="viewer", roots={"company"}),
    dict(subject="a", role="", roots={"company"}),
    dict(subject="a", role="viewer", roots=set()),
])
def test_service_session_refuses_incomplete_identity(kw):
    with pytest.raises(session_module.SessionError):
        session_module.service_session(session_id="job-abc", deployment_id="d", **kw)
