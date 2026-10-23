"""The MCP credential gateway: registry, token and the connector boundary."""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "services" / "mcp_gateway"))

import gateway  # noqa: E402


def make_gateway(*, transport=None):
    tools = {"send_email": {"url": "http://connector/send", "token": "connector-secret",
                            "egress": True, "data_classes": ["internal"]}}
    return gateway.MCPGateway(tools, "gateway-token", transport=transport or (lambda tool, args: {"sent": True}))


def call(service, payload, token="gateway-token"):
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    return service.handle("POST", "/call", headers, json.dumps(payload).encode())


def test_health_is_open_and_call_requires_the_gateway_token():
    service = make_gateway()
    assert service.handle("GET", "/health", {}, b"")[0] == 200
    assert call(service, {"tool": "send_email", "arguments": {}}, token=None)[0] == 401
    assert call(service, {"tool": "send_email", "arguments": {}}, token="wrong")[0] == 401


def test_an_undeclared_tool_is_refused_before_the_connector():
    seen = []
    service = make_gateway(transport=lambda tool, args: seen.append(tool) or {"sent": True})
    status, payload = call(service, {"tool": "wire_money", "arguments": {}})
    assert status == 403
    assert "not declared" in payload["detail"]
    assert seen == []


def test_a_declared_tool_reaches_the_transport():
    seen = []
    service = make_gateway(transport=lambda tool, args: seen.append((tool, args)) or {"sent": True})
    status, payload = call(service, {"tool": "send_email", "arguments": {"to": "a@b"}})
    assert status == 200 and payload["ok"]
    assert seen[0][0]["url"].endswith("/send")
    assert seen[0][0]["token"] == "connector-secret"
    assert seen[0][1] == {"to": "a@b"}


def test_a_connector_failure_is_typed():
    def broken(tool, args):
        raise gateway.GatewayError("connector returned 500")

    service = make_gateway(transport=broken)
    status, payload = call(service, {"tool": "send_email", "arguments": {}})
    assert status == 502
    assert "connector" in payload["detail"]
