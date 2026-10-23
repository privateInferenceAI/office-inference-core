"""The API client for the MCP gateway. The API holds the gateway token, never the connector token."""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import time
import urllib.error
import urllib.request


def session_assertion(subject: str, role: str, secret: str = "") -> str:
    """A short-lived signed identity for a client-tenant gateway, never a caller-supplied role."""
    secret = secret or os.environ.get("OI_MCP_SESSION_SECRET", "")
    if not secret:
        return ""
    payload = {"sub": subject, "role": role, "exp": int(time.time()) + 60}
    body = base64.urlsafe_b64encode(json.dumps(payload, sort_keys=True).encode()).decode().rstrip("=")
    return body + "." + hmac.new(secret.encode(), body.encode(), hashlib.sha256).hexdigest()


class MCPClientError(Exception):
    """The gateway refused or failed a tool call."""


class MCPClient:
    def __init__(self, base_url: str, token: str, *, timeout: int = 30):
        self.base_url = (base_url or "").rstrip("/")
        self.token = token or ""
        self.timeout = timeout

    def call(self, tool: str, arguments: dict, *, session_sha256: str = "",
             session_assertion: str = "", data_class: str = "internal",
             egress_approved: bool = False) -> dict:
        if not self.base_url or not self.token:
            raise MCPClientError("MCP gateway URL and token are required")
        request = urllib.request.Request(
            self.base_url + "/call",
            data=json.dumps({"tool": tool, "arguments": arguments,
                             "session_sha256": session_sha256,
                             "session_assertion": session_assertion,
                             "data_class": data_class,
                             "egress_approved": egress_approved}).encode(), method="POST",
            headers={"Content-Type": "application/json", "Authorization": "Bearer " + self.token})
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                return json.loads(response.read() or b"{}")
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode(errors="replace")[:300]
            raise MCPClientError(f"gateway returned {exc.code}: {detail}") from exc
        except Exception as exc:  # noqa: BLE001
            raise MCPClientError(f"gateway unreachable: {type(exc).__name__}") from exc
