"""The egress service: the only path from a task to an approved URL.

**The sandbox has no network.** A workspace definition that needs the web gets a runtime tool, and that tool
calls this service. The service holds the allowlist and the token; the sandbox holds neither. A URL that is not on
the deployment's list is refused before any bytes leave the box.

The service is deliberately one verb: fetch a URL. It does not proxy arbitrary traffic, it does not tunnel, and
it does not keep a browser session. The response is text with a size cap; a binary download is not this tool.
"""
from __future__ import annotations

import argparse
import json
import os
import secrets
import sys
import urllib.error
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

MAX_BODY = 64 * 1024
MAX_BYTES = 2 * 1024 * 1024
MAX_TEXT = 64 * 1024


class EgressError(Exception):
    """The request is not allowed or the fetch failed."""


class EgressService:
    def __init__(self, allowlist, token: str, *, timeout: int = 15,
                 max_bytes: int = MAX_BYTES, max_text: int = MAX_TEXT,
                 opener=None):
        if not token:
            raise ValueError("the egress service refuses to start without a token")
        self.allowlist = {entry.strip().lower() for entry in allowlist if entry.strip()}
        self.token = token
        self.timeout = timeout
        self.max_bytes = max_bytes
        self.max_text = max_text
        self._opener = opener or urllib.request.urlopen

    def handle(self, method: str, path: str, headers: dict, body: bytes):
        try:
            if path == "/health" and method == "GET":
                return 200, {"status": "ok", "allowlist": sorted(self.allowlist)}
            if not self._authorised(headers):
                return 401, {"error": "unauthorised"}
            if method != "POST":
                return 405, {"error": "method not allowed"}
            if len(body) > MAX_BODY:
                return 413, {"error": "request too large"}
            try:
                payload = json.loads(body or b"{}")
            except (ValueError, TypeError):
                return 400, {"error": "body must be JSON"}
            if not isinstance(payload, dict):
                return 400, {"error": "body must be a JSON object"}
            if path != "/fetch":
                return 404, {"error": "not found"}
            return 200, self.fetch(str(payload.get("url") or ""))
        except EgressError as exc:
            return 403, {"error": "egress", "detail": str(exc)}
        except Exception as exc:  # noqa: BLE001
            return 502, {"error": "fetch failed", "detail": type(exc).__name__}

    def _authorised(self, headers: dict) -> bool:
        for key, value in headers.items():
            if key.lower() == "authorization":
                supplied = str(value)
                if supplied.startswith("Bearer "):
                    return secrets.compare_digest(supplied[7:], self.token)
        return False

    def _allowed(self, host: str) -> bool:
        host = host.lower()
        if host in self.allowlist:
            return True
        for entry in self.allowlist:
            if entry.startswith("*.") and host.endswith(entry[1:]) and host != entry[2:]:
                return True
        return False

    def _validate(self, url: str) -> str:
        parsed = urllib.parse.urlparse(url)
        if parsed.scheme != "https":
            raise EgressError("only https URLs are allowed")
        if parsed.username or parsed.password:
            raise EgressError("credentials in a URL are not allowed")
        if parsed.port not in (None, 443):
            raise EgressError("only the default https port is allowed")
        host = (parsed.hostname or "").lower()
        if not host or not self._allowed(host):
            raise EgressError(f"host {host!r} is not on the egress allowlist")
        return url

    def fetch(self, url: str) -> dict:
        url = self._validate(url)
        request = urllib.request.Request(url, headers={"User-Agent": "oi-workspace-egress/1"})
        try:
            with self._opener(request, timeout=self.timeout) as response:
                data = response.read(self.max_bytes + 1)
                status = getattr(response, "status", 200)
                content_type = response.headers.get("Content-Type", "")
                final_url = response.geturl()
        except urllib.error.HTTPError as exc:
            raise EgressError(f"the URL returned HTTP {exc.code}") from exc
        except Exception as exc:  # noqa: BLE001
            raise EgressError(f"the fetch failed: {type(exc).__name__}") from exc
        if len(data) > self.max_bytes:
            raise EgressError(f"the response is larger than {self.max_bytes} bytes")
        text = data.decode("utf-8", errors="replace")
        return {"url": url, "final_url": final_url, "status": status,
                "content_type": content_type, "bytes": len(data),
                "text": text[:self.max_text],
                "truncated": len(text) > self.max_text}


class _Handler(BaseHTTPRequestHandler):
    service: EgressService = None

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


def serve(allowlist, token: str, host: str = "0.0.0.0", port: int = 8091):
    _Handler.service = EgressService(allowlist, token)
    ThreadingHTTPServer((host, port), _Handler).serve_forever()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="allowlisted egress fetch service")
    parser.add_argument("--host", default=os.environ.get("EGRESS_HOST", "0.0.0.0"))
    parser.add_argument("--port", type=int, default=int(os.environ.get("EGRESS_PORT", "8091")))
    args = parser.parse_args(argv)
    token = os.environ.get("EGRESS_TOKEN", "")
    if not token:
        print("EGRESS_TOKEN is required; refusing to start an unauthenticated egress service", file=sys.stderr)
        return 2
    allowlist = [entry for entry in os.environ.get("EGRESS_ALLOWLIST", "").split(",") if entry.strip()]
    if not allowlist:
        print("EGRESS_ALLOWLIST is empty; refusing to start with no allowed host", file=sys.stderr)
        return 2
    serve(allowlist, token, args.host, args.port)
    return 0


if __name__ == "__main__":
    sys.exit(main())
