"""The MCP gateway: the only process that holds connector credentials.

**The API holds this gateway's token. The gateway holds the connector's token.** The sandbox holds neither and
has no network. A tool that needs a credential is called by the runtime into this gateway, which resolves the
declared connector and adds the credential at the boundary.

This is the credential and registry slice of the MCP gateway, not the full Section 3 gateway. A tool that is not
in the deployment's registry is refused by name before a connector sees it. Session and data-class policy are
the next layer, named in the handover rather than pretended here.
"""
from __future__ import annotations

import argparse
import json
import os
import secrets
import sys
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


class GatewayError(Exception):
    """The gateway refused the call or a connector failed it."""


class MCPGateway:
    def __init__(self, tools: dict, token: str, *, transport=None):
        if not token:
            raise ValueError("the gateway refuses to start without a token")
        self.tools = tools
        self.token = token
        self._transport = transport or self._post

    def handle(self, method: str, path: str, headers: dict, body: bytes):
        try:
            if path == "/health" and method == "GET":
                return 200, {"status": "ok"}
            if not self._authorised(headers):
                return 401, {"error": "unauthorised"}
            if method != "POST":
                return 405, {"error": "method not allowed"}
            payload = json.loads(body or b"{}")
            if not isinstance(payload, dict):
                return 400, {"error": "body must be a JSON object"}
            if path != "/call":
                return 404, {"error": "not found"}
            name = str(payload.get("tool") or "")
            tool = self.tools.get(name)
            if tool is None:
                return 403, {"error": "not_declared",
                             "detail": f"tool {name!r} is not declared for this deployment"}
            arguments = payload.get("arguments") or {}
            if not isinstance(arguments, dict):
                return 400, {"error": "arguments must be a JSON object"}
            result = self._transport(tool, arguments)
            return 200, {"ok": True, "tool": name, "result": result}
        except GatewayError as exc:
            return 502, {"error": "connector", "detail": str(exc)}
        except Exception as exc:  # noqa: BLE001
            return 500, {"error": "internal", "detail": type(exc).__name__}

    def _authorised(self, headers: dict) -> bool:
        for key, value in headers.items():
            if key.lower() == "authorization":
                supplied = str(value)
                if supplied.startswith("Bearer "):
                    return secrets.compare_digest(supplied[7:], self.token)
        return False

    def _post(self, tool: dict, arguments: dict):
        url = tool["url"]
        token = tool.get("token") or ""
        request = urllib.request.Request(
            url, data=json.dumps(arguments).encode(), method="POST",
            headers={"Content-Type": "application/json", "Authorization": "Bearer " + token})
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                return json.loads(response.read() or b"{}")
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode(errors="replace")[:300]
            raise GatewayError(f"connector returned {exc.code}: {detail}") from exc
        except Exception as exc:  # noqa: BLE001
            raise GatewayError(f"connector unreachable: {type(exc).__name__}") from exc


class _Handler(BaseHTTPRequestHandler):
    service: MCPGateway = None

    def do_GET(self):  # noqa: N802
        self._handle("GET")

    def do_POST(self):  # noqa: N802
        self._handle("POST")

    def _handle(self, method: str):
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            length = -1
        if length < 0 or length > 64 * 1024:
            self._send(413, {"error": "request too large"})
            return
        body = self.rfile.read(length) if length else b""
        status, payload = self.service.handle(method, self.path, dict(self.headers), body)
        self._send(status, payload)

    def _send(self, status: int, payload: dict):
        data = json.dumps(payload, sort_keys=True).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *args, **kwargs):
        return


def registry_from_env() -> dict:
    path = os.environ.get("MCP_TOOLS_FILE", "")
    if not path:
        raise SystemExit("MCP_TOOLS_FILE is required")
    with open(path) as handle:
        raw = json.load(handle)
    connector_url = os.environ.get("CONNECTOR_URL", "").rstrip("/")
    connector_token = os.environ.get("CONNECTOR_TOKEN", "")
    tools = {}
    for name, entry in (raw.get("tools") or {}).items():
        tools[name] = {"url": connector_url + entry["path"], "token": connector_token,
                       "egress": bool(entry.get("egress")), "data_classes": entry.get("data_classes", [])}
    return tools


def serve(tools: dict, token: str, host: str = "0.0.0.0", port: int = 8093):
    _Handler.service = MCPGateway(tools, token)
    ThreadingHTTPServer((host, port), _Handler).serve_forever()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="MCP credential gateway")
    parser.add_argument("--host", default=os.environ.get("MCP_HOST", "0.0.0.0"))
    parser.add_argument("--port", type=int, default=int(os.environ.get("MCP_PORT", "8093")))
    args = parser.parse_args(argv)
    token = os.environ.get("MCP_GATEWAY_TOKEN", "")
    if not token:
        print("MCP_GATEWAY_TOKEN is required", file=sys.stderr)
        return 2
    serve(registry_from_env(), token, args.host, args.port)
    return 0


if __name__ == "__main__":
    sys.exit(main())
