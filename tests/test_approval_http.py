"""The approval route over HTTP, with a real session and a memory job store.

**Why this file exists.** `tests/test_approvals.py` tests the record and the state machine. The HTTP route was
the piece that had never run: the C5 panel found that `POST /jobs/{id}/approvals` had been shadowed by the
wildcard route, so every request hit the wrong handler and returned a refusal that looked like an
authentication failure. A contract check proves a path exists in the schema, not that a request reaches it.
These tests send requests.

A memory store is deliberate here. The route's decision and record are one transaction only on a durable store;
the transaction test is in `tests/test_approval_durable.py`, which needs Postgres. This file tests status codes,
the record shape, the authority guard and the refusal rows.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
for extra in (ROOT / "services", ROOT / "services" / "api", ROOT / "services" / "gateway"):
    if str(extra) not in sys.path:
        sys.path.insert(0, str(extra))

pytest.importorskip("fastapi")
from fastapi.testclient import TestClient  # noqa: E402

import jobs  # noqa: E402


class FakeSession:
    def __init__(self, subject="dana", role="staff", session_id="sess-1"):
        self.subject, self.role = subject, role
        self.session_id = session_id
        self.assurance = None

    def permit(self):
        return {"company"}


def at_gate(store, subject="dana", task="test_task"):
    job = jobs.new_job(deployment_id="unknown", subject=subject, actor_role="staff", roots={"company"},
                       credential_sha256="a" * 64, task=task, input="send the email")
    store.put(job)
    jobs.transition(job, jobs.RUNNING)
    store.put(job)
    jobs.request_approval(job, store, "send the email to ferguson@example.com")
    return job


@pytest.fixture()
def route(api_app, monkeypatch):
    """The app object, a memory store, and a recorder in place of the audit subprocess."""
    import app as app_module

    store = jobs.MemoryStore()
    app_module.STATE["jobs"] = store
    app_module.STATE["manifest"] = {}
    calls = []

    def fake_audit(event, actor, role, outcome, payload, **extra):
        calls.append({"event": event, "actor": actor, "role": role, "outcome": outcome,
                      "payload": payload, **extra})

    def fake_resolve(token):
        if token == "good":
            return FakeSession()
        if token == "executive":
            return FakeSession(subject="eve", role="executive")
        raise app_module.session_module.SessionError("no session")

    monkeypatch.setattr(app_module, "audit", fake_audit)
    monkeypatch.setattr(app_module, "_resolve", fake_resolve)
    return app_module, TestClient(app_module.app), store, calls


def post(route, job_id, body):
    _, client, _, _ = route
    return client.post(f"/jobs/{job_id}/approvals", json=body)


def test_approve_reaches_the_route_and_moves_the_job(route):
    app_module, _, store, calls = route
    job = at_gate(store)
    response = post(route, job.job_id, {"session": "good", "decision": "approve",
                                        "action_digest": job.pending_action_digest})
    assert response.status_code == 200, response.text
    assert response.json()["state"] == jobs.RUNNING
    assert store.get(job.job_id).state == jobs.RUNNING
    assert calls and calls[0]["event"] == "approval"
    assert calls[0]["payload"]["approval"]["decision"] == "approve"
    assert calls[0]["payload"]["approval"]["identity"]["assurance"] == "unverified"
    assert calls[0]["payload"]["approval"]["identity"]["session_id"] == "sess-1"


def test_deny_without_a_reason_is_refused_before_the_record(route):
    _, _, store, _ = route
    job = at_gate(store)
    response = post(route, job.job_id, {"session": "good", "decision": "deny",
                                        "action_digest": job.pending_action_digest})
    assert response.status_code == 400
    assert "reason" in response.text
    assert store.get(job.job_id).state == jobs.AWAITING_APPROVAL


def test_deny_with_a_reason_fails_the_job_and_refuses_the_change_request_reason(route):
    _, _, store, calls = route
    job = at_gate(store)
    response = post(route, job.job_id, {"session": "good", "decision": "deny", "reason": "wrong vendor",
                                        "action_digest": job.pending_action_digest})
    assert response.status_code == 200
    assert response.json()["state"] == jobs.FAILED
    assert response.json()["outcome_reason"] == "denied_at_the_gate"
    assert calls[0]["payload"]["approval"]["reason"] == "wrong vendor"


def test_a_decision_with_a_drifted_digest_is_409_and_does_not_move_the_job(route):
    _, _, store, _ = route
    job = at_gate(store)
    response = post(route, job.job_id, {"session": "good", "decision": "approve",
                                        "action_digest": "sha256:" + "0" * 64})
    assert response.status_code == 409
    assert response.json()["kind"] == "approval_drift"
    assert store.get(job.job_id).state == jobs.AWAITING_APPROVAL


def test_a_decision_on_a_job_that_is_not_waiting_is_409(route):
    _, _, store, _ = route
    job = jobs.new_job(deployment_id="unknown", subject="dana", actor_role="staff", roots={"company"},
                       credential_sha256="a" * 64, task="test_task", input="x")
    store.put(job)
    response = post(route, job.job_id, {"session": "good", "decision": "approve",
                                        "action_digest": jobs.action_digest("x")})
    assert response.status_code == 409
    assert response.json()["kind"] == "transition_refused"


def test_no_session_is_refused_and_recorded(route):
    _, _, store, calls = route
    job = at_gate(store)
    response = post(route, job.job_id, {"decision": "approve",
                                        "action_digest": job.pending_action_digest})
    assert response.status_code == 401
    assert calls and calls[0]["event"] == "access"
    assert calls[0]["payload"]["stage"] == "approval_refused"
    assert calls[0]["payload"]["status"] == 401


def test_an_expired_session_is_refused_and_recorded(route):
    _, _, store, calls = route
    job = at_gate(store)
    response = post(route, job.job_id, {"session": "expired", "decision": "approve",
                                        "action_digest": job.pending_action_digest})
    assert response.status_code == 401
    assert calls and calls[0]["payload"]["stage"] == "approval_refused"


def test_a_caller_supplied_role_is_403_and_recorded(route):
    _, _, store, calls = route
    job = at_gate(store)
    response = post(route, job.job_id, {"session": "good", "role": "executive", "decision": "approve",
                                        "action_digest": job.pending_action_digest})
    assert response.status_code == 403
    assert response.json()["kind"] == "refused_authority_claim"
    assert calls and calls[0]["event"] == "access"
    assert calls[0]["payload"]["supplied_field"] == "role"
    assert store.get(job.job_id).state == jobs.AWAITING_APPROVAL


def test_a_change_request_is_distinguishable_from_a_deny(route):
    _, _, store, calls = route
    job = at_gate(store)
    response = post(route, job.job_id, {"session": "good", "decision": "request_changes",
                                        "reason": "the total is wrong",
                                        "action_digest": job.pending_action_digest})
    assert response.status_code == 200
    assert response.json()["outcome_reason"] == "changes_requested_at_the_gate"
    assert calls[0]["payload"]["approval"]["decision"] == "request_changes"
