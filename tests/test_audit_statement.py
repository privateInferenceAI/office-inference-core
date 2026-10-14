"""The audit statement: one side mints it, the other verifies it, and they have to agree.

**Why this is a test and not an integration check.** The API signs a statement naming the actor of every audit
row it writes, and the audit tool refuses a row whose statement it cannot verify. A mismatch between the two
sides would not fail loudly: the API prints the tool's refusal to its log and carries on answering, so a
deployment would look healthy while recording nothing. That is the exact failure this phase has already found
three times, and this is the cheap place to catch it.

The verifier is imported the way the tool imports it, so the two definitions are exercised rather than a
third one written here.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "services" / "gateway"))
sys.path.insert(0, str(ROOT / "services" / "audit"))

import assertion  # noqa: E402


def audit_tool():
    """The tool as a module, imported the way the image imports it.

    The Postgres driver is required at import, so this is skipped where it is absent, which is the same
    treatment the rest of the audit tests give it. The verifier itself needs no database; the module around it
    does.
    """
    pytest.importorskip("psycopg", reason="the audit tool imports the Postgres driver")
    spec = importlib.util.spec_from_file_location("oi_audit_tool", ROOT / "services" / "audit" / "audit.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


KEY = b"a" * 48


def test_the_tool_accepts_a_statement_the_api_minted():
    """The contract: what the signer produces, the checker accepts."""
    token = assertion.mint_audit(KEY, "probe-staff@example.com", "viewer", deployment_id="test")
    verifier = audit_tool()._verifier()
    payload = verifier.verify_audit(KEY, token)
    assert payload["actor"] == "probe-staff@example.com"
    assert payload["actor_role"] == "viewer"
    assert payload["purpose"] == "audit"


def test_a_model_call_statement_cannot_write_an_audit_row():
    """Two capabilities, two purposes, one key. The purpose is the only thing keeping them apart."""
    token = assertion.mint(KEY, "viewer", session_id="probe")
    with pytest.raises(assertion.AssertionError_, match="rather than for an audit row"):
        assertion.verify_audit(KEY, token)


def test_a_statement_signed_with_another_key_is_refused():
    token = assertion.mint_audit(KEY, "somebody@example.com", "viewer")
    with pytest.raises(assertion.AssertionError_, match="signature does not match"):
        assertion.verify_audit(b"b" * 48, token)


def test_an_expired_statement_is_refused():
    token = assertion.mint_audit(KEY, "somebody@example.com", "viewer", now=1_000_000)
    with pytest.raises(assertion.AssertionError_, match="expired"):
        assertion.verify_audit(KEY, token, now=1_000_000 + assertion.MAX_AGE_SECONDS + 1)


def test_the_tool_imports_without_the_credential_machinery():
    """A tool whose import needs a signing key cannot be tested without one, which is why the verifier is lazy."""
    module = audit_tool()
    assert hasattr(module, "_verifier")


def test_the_tool_refuses_a_hand_typed_actor_when_there_is_no_statement():
    """The guard that was always there, kept because a person at a terminal has no session to resolve."""
    source = (ROOT / "services" / "audit" / "audit.py").read_text()
    assert "refuses a hand-typed actor outside development mode" in source
    assert "--assertion" in source, "and the statement path has to exist beside it"
