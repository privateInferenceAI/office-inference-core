"""The API client for the MCP gateway. The API holds the gateway token, never the connector token."""
from __future__ import annotations

import json
import urllib.error
import urllib.request


class MCPClientError(Exception):
    """The gateway refused or failed a tool call."""


class MCPClient:
    def __init__(self, base_url: str, token: str, *, timeout: int = 30):
        self.base_url = (base_url or "").rstrip("/")
        self.token = token or ""
        self.timeout = timeout

    def call(self, tool: str, arguments: dict, *, session_sha256: str = "",
             data_class: str = "internal", egress_approved: bool = False) -> dict:
        if not self.base_url or not self.token:
            raise MCPClientError("MCP gateway URL and token are required")
        request = urllib.request.Request(
            self.base_url + "/call",
            data=json.dumps({"tool": tool, "arguments": arguments,
                             "session_sha256": session_sha256, "data_class": data_class,
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
