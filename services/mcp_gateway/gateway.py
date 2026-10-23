"""The MCP gateway: registry, session, policy and connector credentials.

**The API holds this gateway's token. The gateway holds the connector's credential.** The sandbox holds neither
and has no network. A tool that needs a credential is called by the runtime into this gateway, which validates
the session, checks the deployment's tool policy, and adds the connector credential at the boundary.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import secrets
import stat
import subprocess
import sys
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

AUTHORITY_FIELDS = ("role", "subject", "actor", "groups", "roots")
MAX_BODY = 64 * 1024


class GatewayError(Exception):
    """The gateway could not complete a connector call."""


class SessionRefused(GatewayError):
    """The presented session is absent, expired, revoked or unreadable."""


class MCPGateway:
    def __init__(self, tools: dict, token: str, *, session_resolver=None, audit=None,
                 transport=None, credential_loader=None):
        if not token:
            raise ValueError("the gateway refuses to start without a token")
        self.tools = tools
        self.token = token
        self._session_resolver = session_resolver or self._session_from_db
        self._audit = audit or self._audit_tool
        self._transport = transport or self._post
        self._credential_loader = credential_loader or self._read_credential

    def handle(self, method: str, path: str, headers: dict, body: bytes):
        try:
            if path == "/health" and method == "GET":
                return 200, {"status": "ok"}
            if not self._authorised(headers):
                return 401, {"error": "unauthorised"}
            if method == "GET" and path == "/tools":
                return 200, {"tools": sorted(self.tools)}
            if method != "POST":
                return 405, {"error": "method not allowed"}
            if len(body) > MAX_BODY:
                return 413, {"error": "request too large"}
            payload = json.loads(body or b"{}")
            if not isinstance(payload, dict):
                return 400, {"error": "body must be a JSON object"}
            if path != "/call":
                return 404, {"error": "not found"}
            return self._call(payload)
        except GatewayError as exc:
            return 502, {"error": "connector", "detail": str(exc)}
        except Exception as exc:  # noqa: BLE001
            return 500, {"error": "internal", "detail": type(exc).__name__}

    def _call(self, payload: dict):
        claimed = next((field for field in AUTHORITY_FIELDS if payload.get(field) is not None), None)
        if claimed:
            self._record("denied", "none", None, {"refusal": "authority_claim", "field": claimed})
            return 403, {"error": "refused_authority_claim",
                         "detail": "a request may not supply its own authority"}
        token = str(payload.get("session") or payload.get("session_sha256") or "")
        if not token:
            self._record("denied", "none", None, {"refusal": "no_session"})
            return 401, {"error": "no_session"}
        try:
            session = self._session_resolver(token)
        except SessionRefused as exc:
            self._record("denied", "none", None, {"refusal": "invalid_session", "detail": str(exc)})
            return 401, {"error": "invalid_session"}
        name = str(payload.get("tool") or "")
        tool = self.tools.get(name)
        if tool is None:
            self._record("denied", session["role"], session["subject"],
                         {"refusal": "not_declared", "tool": name})
            return 403, {"error": "not_declared",
                         "detail": f"tool {name!r} is not declared for this deployment"}
        allowed_roles = tool.get("allowed_roles") or []
        if allowed_roles and session["role"] not in allowed_roles:
            self._record("denied", session["role"], session["subject"],
                         {"refusal": "role_policy", "tool": name, "required": allowed_roles})
            return 403, {"error": "role_policy",
                         "detail": f"role {session['role']!r} does not hold {name!r}"}
        data_class = str(payload.get("data_class") or tool.get("data_class") or "internal")
        allowed_classes = tool.get("data_classes") or []
        if allowed_classes and data_class not in allowed_classes:
            self._record("denied", session["role"], session["subject"],
                         {"refusal": "data_class", "tool": name, "data_class": data_class})
            return 403, {"error": "data_class",
                         "detail": f"the tool does not accept {data_class!r} data"}
        if tool.get("egress") and data_class in ("confidential", "regulated") and not payload.get("egress_approved"):
            self._record("denied", session["role"], session["subject"],
                         {"refusal": "egress_gate", "tool": name, "data_class": data_class})
            return 403, {"error": "egress_gate",
                         "detail": "the frontier lane has not approved this egress"}
        arguments = payload.get("arguments") or {}
        if not isinstance(arguments, dict):
            return 400, {"error": "arguments must be a JSON object"}
        credential = self._credential_loader(tool)
        if credential is None:
            return 503, {"error": "credential_unavailable"}
        result = self._transport(tool, arguments, credential)
        self._record("ok", session["role"], session["subject"],
                     {"tool": name, "data_class": data_class})
        return 200, {"ok": True, "tool": name, "result": result}

    def _authorised(self, headers: dict) -> bool:
        for key, value in headers.items():
            if key.lower() == "authorization":
                supplied = str(value)
                if supplied.startswith("Bearer "):
                    return secrets.compare_digest(supplied[7:], self.token)
        return False

    def _record(self, outcome: str, role: str, subject: str | None, payload: dict):
        try:
            self._audit({"outcome": outcome, "role": role or "none", "subject": subject or "unknown",
                         "payload": payload})
        except Exception:  # noqa: BLE001
            pass

    def _session_from_db(self, token: str) -> dict:
        database_url = os.environ.get("DATABASE_URL", "")
        if not database_url:
            raise SessionRefused("the gateway has no database to read the session from")
        import psycopg
        value = token.strip()
        if len(value) == 64 and all(c in "0123456789abcdef" for c in value):
            digest = value  # a job holds the hash, never the credential
        else:
            digest = hashlib.sha256(value.encode()).hexdigest()
        try:
            with psycopg.connect(database_url) as conn:
                with conn.cursor() as cur:
                    cur.execute("""select subject, role, expires_at, revoked_at
                                   from sessions where credential_sha256 = %s""", (digest,))
                    row = cur.fetchone()
        except Exception as exc:  # noqa: BLE001
            raise SessionRefused(f"the session lookup failed: {type(exc).__name__}") from exc
        if row is None:
            raise SessionRefused("the token resolves to no live session")
        subject, role, expires_at, revoked_at = row
        if revoked_at is not None:
            raise SessionRefused("the session was revoked")
        if expires_at is not None:
            from datetime import datetime, timezone
            if expires_at <= datetime.now(timezone.utc):
                raise SessionRefused("the session has expired")
        return {"subject": subject, "role": role}

    def _audit_tool(self, event: dict):
        tool = os.environ.get("AUDIT_TOOL", "")
        if not tool or not os.path.exists(tool):
            return
        command = [sys.executable, tool, "record", "--type", "access",
                   "--actor", event["subject"], "--actor-role", event["role"],
                   "--outcome", event["outcome"], "--data-class", "internal",
                   "--request-id", f"mcp-{secrets.token_hex(8)}",
                   "--payload", json.dumps(event["payload"], sort_keys=True)]
        if os.environ.get("AUDIT_DEV", "") == "1":
            command.append("--dev")
        subprocess.run(command, capture_output=True, text=True, env=dict(os.environ), timeout=20)

    def _read_credential(self, tool: dict):
        path = tool.get("credential_file", "")
        if not path:
            return tool.get("token")
        try:
            mode = stat.S_IMODE(os.stat(path).st_mode)
            if mode & 0o077:
                raise GatewayError(f"credential file {path} is readable by another account")
            with open(path) as handle:
                return handle.read().strip()
        except FileNotFoundError:
            return None

    def _post(self, tool: dict, arguments: dict, credential: str):
        request = urllib.request.Request(
            tool["url"], data=json.dumps(arguments).encode(), method="POST",
            headers={"Content-Type": "application/json", "Authorization": "Bearer " + credential})
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
        if length < 0 or length > MAX_BODY:
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
    return {name: dict(entry, url=connector_url + entry["path"])
            for name, entry in (raw.get("tools") or {}).items()}


def serve(tools: dict, token: str, host: str = "0.0.0.0", port: int = 8093):
    _Handler.service = MCPGateway(tools, token)
    ThreadingHTTPServer((host, port), _Handler).serve_forever()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="MCP gateway")
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
