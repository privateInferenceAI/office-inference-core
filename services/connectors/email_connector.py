"""The first business connector: send one email through the client's SMTP server.

**The sandbox never holds this credential.** The connector service runs beside the deployment, reads its SMTP
credentials from its own environment, and accepts a send only from the runtime with the internal token. The
runtime's approval gate holds the call before it reaches this service.

The service is deliberately one verb. It does not read a mailbox, it does not use a browser, and it does not
accept attachments yet.
"""
from __future__ import annotations

import argparse
import json
import os
import secrets
import smtplib
import sys
import uuid
from email.message import EmailMessage
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

MAX_BODY = 64 * 1024


class ConnectorError(Exception):
    """The send is not allowed or the SMTP server refused it."""


class EmailConnector:
    def __init__(self, *, host: str, port: int, sender: str, token: str,
                 allowed_recipients=(), username: str = "", password: str = "",
                 use_tls: bool = False, timeout: int = 10, smtp_factory=None):
        if not token:
            raise ValueError("the connector refuses to start without a token")
        if not host or not sender:
            raise ValueError("the connector needs an SMTP host and a sender")
        self.host, self.port, self.sender = host, int(port), sender
        self.token = token
        self.allowed = {entry.strip().lower() for entry in allowed_recipients if entry.strip()}
        self.username, self.password = username, password
        self.use_tls = use_tls
        self.timeout = timeout
        self._smtp_factory = smtp_factory or smtplib.SMTP

    def handle(self, method: str, path: str, headers: dict, body: bytes):
        try:
            if path == "/health" and method == "GET":
                return 200, {"status": "ok"}
            if not self._authorised(headers):
                return 401, {"error": "unauthorised"}
            if method != "POST":
                return 405, {"error": "method not allowed"}
            if len(body) > MAX_BODY:
                return 413, {"error": "request too large"}
            payload = json.loads(body or b"{}")
            if not isinstance(payload, dict):
                return 400, {"error": "body must be a JSON object"}
            if path != "/send":
                return 404, {"error": "not found"}
            return 200, self.send(str(payload.get("to") or ""), str(payload.get("subject") or ""),
                                  str(payload.get("body") or ""))
        except ConnectorError as exc:
            return 403, {"error": "connector", "detail": str(exc)}
        except Exception as exc:  # noqa: BLE001
            return 502, {"error": "send failed", "detail": type(exc).__name__}

    def _authorised(self, headers: dict) -> bool:
        for key, value in headers.items():
            if key.lower() == "authorization":
                supplied = str(value)
                if supplied.startswith("Bearer "):
                    return secrets.compare_digest(supplied[7:], self.token)
        return False

    def _validated(self, to: str, subject: str, body: str):
        if not to or not subject or not body:
            raise ConnectorError("to, subject and body are required")
        if "\n" in to or "\r" in to or "\n" in subject or "\r" in subject:
            raise ConnectorError("headline fields must be one line")
        if self.allowed and to.strip().lower() not in self.allowed:
            raise ConnectorError(f"recipient {to!r} is not on the connector allowlist")
        return to.strip(), subject.strip(), body

    def send(self, to: str, subject: str, body: str) -> dict:
        to, subject, body = self._validated(to, subject, body)
        message = EmailMessage()
        message["From"] = self.sender
        message["To"] = to
        message["Subject"] = subject
        message["Message-ID"] = f"<{uuid.uuid4()}@officeinference.local>"
        message.set_content(body)
        client = self._smtp_factory(self.host, self.port, timeout=self.timeout)
        try:
            if self.use_tls:
                client.starttls()
            if self.username:
                client.login(self.username, self.password)
            client.send_message(message)
        finally:
            try:
                client.quit()
            except Exception:  # noqa: BLE001 - the send result is what matters
                pass
        return {"sent": True, "to": to, "subject": subject,
                "message_id": message["Message-ID"]}


class _Handler(BaseHTTPRequestHandler):
    service: EmailConnector = None

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


def serve(connector: EmailConnector, host: str = "0.0.0.0", port: int = 8092):
    _Handler.service = connector
    ThreadingHTTPServer((host, port), _Handler).serve_forever()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="SMTP send connector")
    parser.add_argument("--host", default=os.environ.get("CONNECTOR_HOST_BIND", "0.0.0.0"))
    parser.add_argument("--port", type=int, default=int(os.environ.get("CONNECTOR_PORT", "8092")))
    args = parser.parse_args(argv)
    token = os.environ.get("CONNECTOR_TOKEN", "")
    if not token:
        print("CONNECTOR_TOKEN is required; refusing to start an unauthenticated connector", file=sys.stderr)
        return 2
    connector = EmailConnector(
        host=os.environ.get("SMTP_HOST", ""),
        port=int(os.environ.get("SMTP_PORT", "25")),
        sender=os.environ.get("SMTP_FROM", "agent@officeinference.com"),
        token=token,
        allowed_recipients=os.environ.get("CONNECTOR_ALLOWED_RECIPIENTS", "").split(","),
        username=os.environ.get("SMTP_USERNAME", ""),
        password=os.environ.get("SMTP_PASSWORD", ""),
        use_tls=os.environ.get("SMTP_USE_TLS", "false").lower() == "true")
    serve(connector, args.host, args.port)
    return 0


if __name__ == "__main__":
    sys.exit(main())
