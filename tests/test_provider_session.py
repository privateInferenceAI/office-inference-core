"""The provider session exchange: an edge-authenticated identity becomes a session.

**Why this file exists.** The customer path puts a login at the edge and keeps the API unpublished. The
contract between them is one endpoint and one shared secret. These tests cover the refusals as carefully as
the success, because this endpoint is the place where a missing check hands out a session to anyone who can
reach the API with a header.

The tests use the memory session store and a registry built the same way the renderer builds one. The real
Cognito login is exercised by Phase D on the deployment.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
for extra in (ROOT / "services", ROOT / "services" / "api", ROOT / "services" / "retrieval",
              ROOT / "services" / "session"):
    if str(extra) not in sys.path:
        sys.path.insert(0, str(extra))

pytest.importorskip("fastapi")
from fastapi.testclient import TestClient  # noqa: E402

import access as access_module  # noqa: E402
import approvals  # noqa: E402
import session as session_module  # noqa: E402

EDGE_SECRET = "edge-secret-for-tests"


def access_document():
    return {
        "declared": True,
        "deployment_id": "oi-provider-test",
        "groups": {"exec": ["company", "executive"], "staff": ["company"]},
        "registry_version": 1,
        "roles": {"viewer": []},
        "roots": ["company", "executive"],
        "users": [
            {"groups": ["staff"], "name": "Zoe Reyes", "subject": "zoe.reyes@northwind.example"},
            {"groups": ["exec"], "name": "Adam Hale", "subject": "adam.hale@northwind.example"},
        ],
    }


@pytest.fixture()
def provider(monkeypatch, tmp_path, api_app):
    # The api_app fixture installs the service import paths and stubs the SSE transport when it is absent.
    import app as app_module

    path = tmp_path / "access.json"
    path.write_text(json.dumps(access_document()))
    registry = access_module.load(str(path))
    store = session_module.MemoryStore()
    calls = []

    def fake_audit(event, actor, role, outcome, payload, **extra):
        calls.append({"event": event, "actor": actor, "role": role, "outcome": outcome, "payload": payload,
                      **extra})

    app_module.STATE["registry"] = registry
    app_module.STATE["store"] = store
    monkeypatch.setattr(app_module, "audit", fake_audit)
    monkeypatch.setenv("OI_EDGE_SECRET", EDGE_SECRET)
    monkeypatch.setenv("OI_PROVIDER_ROLE", "viewer")
    monkeypatch.setenv("OI_PROVIDER_SESSION_TTL_HOURS", "8")
    return app_module, TestClient(app_module.app), store, calls


def request_session(client, *, secret=EDGE_SECRET, email="zoe.reyes@northwind.example", subject="cognito-sub-1"):
    headers = {}
    if secret is not None:
        headers["X-OI-Edge-Secret"] = secret
    if email is not None:
        headers["X-OI-Provider-Email"] = email
    if subject is not None:
        headers["X-OI-Provider-Subject"] = subject
    return client.post("/sessions/provider", headers=headers)


def test_a_declared_identity_gets_a_provider_session(provider):
    _, client, store, calls = provider
    response = request_session(client)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["source"] == "provider"
    assert body["subject"] == "zoe.reyes@northwind.example"
    assert body["roots"] == ["company"]
    assert body["groups"] == ["staff"]
    assert body["token"]
    resolved = session_module.resolve_token(store, body["token"])
    assert resolved.source == "provider"
    assert resolved.subject == "zoe.reyes@northwind.example"
    assert any(call["payload"].get("stage") == "provider_session" and call["outcome"] == "ok" for call in calls)


def test_a_wrong_edge_secret_is_refused_and_recorded(provider):
    _, client, store, calls = provider
    response = request_session(client, secret="not-the-edge")
    assert response.status_code == 401
    assert response.json()["kind"] == "not_the_edge"
    assert not store.rows
    refused = [call for call in calls if call["payload"].get("stage") == "provider_session"
               and call["outcome"] == "denied"]
    assert refused and refused[0]["payload"]["reason"] == "edge_secret"


def test_a_missing_edge_secret_is_refused(provider):
    _, client, store, _ = provider
    response = request_session(client, secret=None)
    assert response.status_code == 401
    assert not store.rows


def test_an_identity_without_an_email_is_refused(provider):
    _, client, store, _ = provider
    response = request_session(client, email=None)
    assert response.status_code == 400
    assert response.json()["kind"] == "no_identity"
    assert not store.rows


def test_an_unknown_identity_is_refused_and_recorded(provider):
    _, client, store, calls = provider
    response = request_session(client, email="stranger@example.com")
    assert response.status_code == 403
    assert response.json()["kind"] == "unknown_identity"
    assert not store.rows
    refused = [call for call in calls if call["payload"].get("stage") == "provider_session"
               and call["outcome"] == "denied"]
    assert refused and refused[0]["payload"]["reason"] == "unknown_subject"


def test_the_body_cannot_supply_identity_or_authority(provider):
    """The response is built from the registry; a body claiming a role or subject changes nothing."""
    _, client, store, _ = provider
    response = client.post("/sessions/provider", headers={
        "X-OI-Edge-Secret": EDGE_SECRET,
        "X-OI-Provider-Email": "zoe.reyes@northwind.example",
    }, json={"subject": "adam.hale@northwind.example", "role": "executive"})
    assert response.status_code == 200
    body = response.json()
    assert body["subject"] == "zoe.reyes@northwind.example"
    assert body["role"] == "viewer"


def test_without_an_edge_secret_configured_the_route_refuses(provider, monkeypatch):
    app_module, client, store, _ = provider
    monkeypatch.delenv("OI_EDGE_SECRET")
    response = request_session(client, secret="anything")
    assert response.status_code == 503
    assert response.json()["kind"] == "not_configured"
    assert not store.rows


def test_the_email_match_is_case_insensitive(provider):
    _, client, _, _ = provider
    response = request_session(client, email="ZOE.REYES@NORTHWIND.EXAMPLE")
    assert response.status_code == 200
    assert response.json()["subject"] == "zoe.reyes@northwind.example"


def test_an_executive_identity_gets_the_executive_roots(provider):
    _, client, _, _ = provider
    response = request_session(client, email="adam.hale@northwind.example")
    assert response.status_code == 200
    assert set(response.json()["roots"]) == {"company", "executive"}


# --------------------------------------------------------------------------- the record a provider makes


def provider_session(subject="zoe.reyes@northwind.example", role="viewer"):
    from datetime import datetime, timedelta, timezone
    return session_module.Session(
        session_id="provider-session-1", subject=subject, role=role,
        roots=frozenset({"company"}), groups=("staff",), deployment_id="oi-provider-test",
        expires_at=datetime.now(timezone.utc) + timedelta(hours=1), source="provider")


def test_a_provider_session_records_provider_verified_assurance():
    identity = approvals.identity_for(provider_session())
    assert identity["assurance"] == approvals.ASSURANCE_PROVIDER_VERIFIED
    assert identity["basis"] == approvals.BASIS_PROVIDER
    assert identity["claimed_subject"] == "zoe.reyes@northwind.example"


def test_a_provider_approval_manifestation_carries_the_name():
    identity = approvals.identity_for(provider_session())
    manifestation = approvals.part11_manifestation("approve", "sha256:" + "a" * 64,
                                                   "2026-11-03T12:00:00+00:00", identity=identity)
    assert manifestation["printed_name"] == "zoe.reyes@northwind.example"
    assert manifestation["name_absent_reason"] is None
    assert manifestation["name_basis"] == approvals.BASIS_PROVIDER


def test_a_development_session_still_records_unverified():
    from datetime import datetime, timedelta, timezone
    development = session_module.Session(
        session_id="dev-session-1", subject="probe-staff@officeinference.com", role="viewer",
        roots=frozenset({"company"}), groups=("staff",), deployment_id="oi-provider-test",
        expires_at=datetime.now(timezone.utc) + timedelta(hours=1))
    identity = approvals.identity_for(development)
    assert identity["assurance"] == approvals.ASSURANCE_UNVERIFIED
    assert identity["basis"] == approvals.BASIS_SESSION_TOKEN
