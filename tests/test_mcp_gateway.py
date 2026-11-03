"""The MCP gateway: session, registry, role policy, data class and credentials."""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "services" / "mcp_gateway"))

import gateway  # noqa: E402


class Recorder:
    def __init__(self):
        self.events = []
        self.calls = []

    def audit(self, event):
        self.events.append(event)

    def transport(self, tool, arguments, credential):
        self.calls.append((tool, arguments, credential))
        return {"sent": True}


def make_gateway(tmp_path, *, role="viewer"):
    rec = Recorder()
    credential = tmp_path / "connector.token"
    credential.write_text("connector-secret")
    os.chmod(credential, 0o600)
    tools = {"send_email": {
        "url": "http://connector/send", "credential_file": str(credential),
        "allowed_roles": ["viewer", "admin"], "data_classes": ["internal", "regulated"],
        "egress": True,
    }}
    service = gateway.MCPGateway(
        tools, "gateway-token", session_resolver=lambda token: {"subject": "dana", "role": role},
        audit=rec.audit, transport=rec.transport)
    return service, rec, credential


def call(service, payload, token="gateway-token"):
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    return service.handle("POST", "/call", headers, json.dumps(payload).encode())


def test_health_and_tool_list_are_open_and_call_needs_the_gateway_token(tmp_path):
    service, _, _ = make_gateway(tmp_path)
    assert service.handle("GET", "/health", {}, b"")[0] == 200
    assert service.handle("GET", "/tools", {"Authorization": "Bearer gateway-token"}, b"")[1]["tools"] == ["send_email"]
    assert call(service, {"tool": "send_email", "session": "valid"}, token=None)[0] == 401


def test_no_session_is_refused_and_recorded(tmp_path):
    service, rec, _ = make_gateway(tmp_path)
    status, payload = call(service, {"tool": "send_email"})
    assert status == 401 and payload["error"] == "no_session"
    assert rec.events[-1]["payload"]["refusal"] == "no_session"
    assert rec.calls == []


def test_invalid_session_is_refused(tmp_path):
    rec = Recorder()
    service = gateway.MCPGateway(
        {"send_email": {"url": "http://x", "credential_file": "/nonexistent"}}, "gateway-token",
        session_resolver=lambda token: (_ for _ in ()).throw(gateway.SessionRefused("expired")),
        audit=rec.audit, transport=rec.transport)
    status, payload = call(service, {"tool": "send_email", "session": "bad"})
    assert status == 401 and payload["error"] == "invalid_session"
    assert rec.events[-1]["payload"]["refusal"] == "invalid_session"


def test_a_body_supplied_authority_is_refused(tmp_path):
    service, rec, _ = make_gateway(tmp_path)
    status, payload = call(service, {"tool": "send_email", "session": "valid", "role": "admin"})
    assert status == 403 and payload["error"] == "refused_authority_claim"
    assert rec.events[-1]["payload"]["refusal"] == "authority_claim"


def test_an_undeclared_tool_is_refused_before_the_connector(tmp_path):
    service, rec, _ = make_gateway(tmp_path)
    status, payload = call(service, {"tool": "wire_money", "session": "valid"})
    assert status == 403 and payload["error"] == "not_declared"
    assert rec.calls == []
    assert rec.events[-1]["payload"]["refusal"] == "not_declared"


def test_a_role_without_the_policy_is_refused_before_the_connector(tmp_path):
    service, rec, _ = make_gateway(tmp_path, role="contractor")
    status, payload = call(service, {"tool": "send_email", "session": "valid"})
    assert status == 403 and payload["error"] == "role_policy"
    assert rec.calls == []
    assert rec.events[-1]["payload"]["refusal"] == "role_policy"


def test_a_data_class_the_tool_does_not_accept_is_refused(tmp_path):
    service, rec, _ = make_gateway(tmp_path)
    status, payload = call(service, {"tool": "send_email", "session": "valid", "data_class": "confidential"})
    assert status == 403 and payload["error"] == "data_class"
    assert rec.calls == []


def test_regulated_egress_needs_the_frontier_approval(tmp_path):
    service, rec, _ = make_gateway(tmp_path)
    status, payload = call(service, {"tool": "send_email", "session": "valid", "data_class": "regulated"})
    assert status == 403 and payload["error"] == "egress_gate"
    assert rec.calls == []
    status, _ = call(service, {"tool": "send_email", "session": "valid", "data_class": "regulated",
                               "egress_approved": True, "arguments": {"to": "a@b"}})
    assert status == 200
    assert rec.calls[-1][2] == "connector-secret"


def test_a_declared_tool_gets_the_credential_and_reaches_the_transport(tmp_path):
    service, rec, _ = make_gateway(tmp_path)
    status, payload = call(service, {"tool": "send_email", "session": "valid",
                                     "arguments": {"to": "a@b", "subject": "s", "body": "b"}})
    assert status == 200 and payload["ok"]
    assert rec.calls[0][2] == "connector-secret"
    assert rec.events[-1]["payload"]["tool"] == "send_email"


def test_the_credential_file_is_read_each_call_so_rotation_needs_no_redeploy(tmp_path):
    service, rec, credential = make_gateway(tmp_path)
    call(service, {"tool": "send_email", "session": "valid", "arguments": {}})
    credential.write_text("rotated-secret")
    call(service, {"tool": "send_email", "session": "valid", "arguments": {}})
    assert [entry[2] for entry in rec.calls] == ["connector-secret", "rotated-secret"]


def test_a_world_readable_credential_file_is_refused(tmp_path):
    service, _, credential = make_gateway(tmp_path)
    os.chmod(credential, 0o644)
    status, payload = call(service, {"tool": "send_email", "session": "valid", "arguments": {}})
    assert status == 502
    assert "readable by another account" in payload["detail"]


def test_a_connector_failure_is_typed(tmp_path):
    def broken(tool, arguments, credential):
        raise gateway.GatewayError("connector returned 500")

    service = gateway.MCPGateway({"send_email": {"url": "x", "token": "t"}}, "gateway-token",
                                 session_resolver=lambda token: {"subject": "dana", "role": "viewer"},
                                 audit=Recorder().audit, transport=broken)
    status, payload = call(service, {"tool": "send_email", "session": "valid", "arguments": {}})
    assert status == 502 and "connector" in payload["detail"]


def _assertion(secret: str, *, role: str = "viewer", subject: str = "dana", exp=None) -> str:
    import base64, hashlib, hmac, time
    payload = {"sub": subject, "role": role, "exp": int(time.time()) + 60 if exp is None else exp}
    body = base64.urlsafe_b64encode(json.dumps(payload, sort_keys=True).encode()).decode().rstrip("=")
    return body + "." + hmac.new(secret.encode(), body.encode(), hashlib.sha256).hexdigest()


def _assertion_gateway(tmp_path, secret="assertion-secret"):
    os.environ["MCP_SESSION_SECRET"] = secret
    try:
        rec = Recorder()
        credential = tmp_path / "connector.token"
        credential.write_text("connector-secret")
        os.chmod(credential, 0o600)
        tools = {"send_email": {"url": "http://connector/send", "credential_file": str(credential),
                                "allowed_roles": ["viewer"], "data_classes": ["internal"], "egress": True}}
        service = gateway.MCPGateway(tools, "gateway-token", audit=rec.audit, transport=rec.transport)
    finally:
        os.environ.pop("MCP_SESSION_SECRET", None)
    return service, rec


def test_a_signed_session_assertion_carries_identity_without_a_database(tmp_path):
    service, rec = _assertion_gateway(tmp_path)
    token = _assertion("assertion-secret")
    status, payload = call(service, {"tool": "send_email", "session_assertion": token,
                                     "arguments": {"to": "client@badj.ai"}})
    assert status == 200 and payload["result"]["sent"] is True
    assert rec.events[-1]["subject"] == "dana"


def test_an_expired_assertion_is_refused(tmp_path):
    service, rec = _assertion_gateway(tmp_path)
    status, payload = call(service, {"tool": "send_email", "session_assertion": _assertion("assertion-secret", exp=1)})
    assert status == 401 and payload["error"] == "invalid_session"
    assert rec.events[-1]["payload"]["refusal"] == "invalid_session"


def test_an_assertion_with_the_wrong_signature_is_refused(tmp_path):
    service, _ = _assertion_gateway(tmp_path)
    status, payload = call(service, {"tool": "send_email", "session_assertion": _assertion("wrong-secret")})
    assert status == 401 and payload["error"] == "invalid_session"


def test_a_body_role_is_refused_even_with_a_valid_assertion(tmp_path):
    service, rec = _assertion_gateway(tmp_path)
    token = _assertion("assertion-secret")
    status, payload = call(service, {"tool": "send_email", "session_assertion": token, "role": "admin"})
    assert status == 403 and payload["error"] == "refused_authority_claim"

def test_connector_refusal_is_recorded_in_the_audit_chain(tmp_path):
    """A message blocked at the connector must still appear in the chain.

    **The connector is the last boundary before a message leaves.** When it refuses a recipient, the caller
    gets an error; if the gateway does not record that error, the chain cannot show that a send was attempted
    and stopped. This is the allowlist case from the Phase E email checks.
    """
    service, recorder, _ = make_gateway(tmp_path)

    def refuse(tool, arguments, credential):
        raise gateway.GatewayError(
            "connector returned 403: recipient 'outside@example.com' is not on the connector allowlist")

    service._transport = refuse
    status, body = call(service, {
        "tool": "send_email", "session": "dana",
        "arguments": {"to": "outside@example.com", "subject": "test", "body": "blocked"},
        "data_class": "internal", "egress_approved": True,
    })
    assert status == 502
    assert body["error"] == "connector"
    refusals = [event for event in recorder.events
                if event["outcome"] == "denied" and (event["payload"] or {}).get("refusal") == "connector"]
    assert refusals, recorder.events
    assert "outside@example.com" in str(refusals[-1]["payload"]["detail"])

