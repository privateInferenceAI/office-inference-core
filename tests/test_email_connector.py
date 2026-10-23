"""The email connector: token, allowlist and one send."""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "services" / "connectors"))

import email_connector  # noqa: E402


class FakeSMTP:
    def __init__(self, *args, **kwargs):
        self.args, self.kwargs = args, kwargs
        self.sent = []
        self.quit_called = False

    def send_message(self, message):
        self.sent.append(message)

    def quit(self):
        self.quit_called = True


def make_connector(*, allowed=("probe@example.com",)):
    fake = FakeSMTP()
    connector = email_connector.EmailConnector(
        host="smtp.test", port=2525, sender="agent@officeinference.com", token="secret",
        allowed_recipients=allowed, smtp_factory=lambda *a, **k: fake)
    return connector, fake


def call(connector, payload, token="secret"):
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    return connector.handle("POST", "/send", headers, json.dumps(payload).encode())


def test_health_is_open_and_send_requires_the_token():
    connector, _ = make_connector()
    assert connector.handle("GET", "/health", {}, b"") == (200, {"status": "ok"})
    assert call(connector, {"to": "probe@example.com", "subject": "s", "body": "b"}, token=None)[0] == 401
    assert call(connector, {"to": "probe@example.com", "subject": "s", "body": "b"}, token="wrong")[0] == 401


def test_an_allowed_send_reaches_the_smtp_server():
    connector, fake = make_connector()
    status, result = call(connector, {"to": "probe@example.com", "subject": "Status", "body": "All good"})
    assert status == 200 and result["sent"]
    assert len(fake.sent) == 1
    message = fake.sent[0]
    assert message["To"] == "probe@example.com"
    assert message["Subject"] == "Status"
    assert fake.quit_called


def test_an_off_list_recipient_is_refused_before_smtp():
    connector, fake = make_connector()
    status, result = call(connector, {"to": "someone@else.example", "subject": "s", "body": "b"})
    assert status == 403
    assert "allowlist" in result["detail"]
    assert fake.sent == []


def test_missing_fields_and_header_injection_are_refused():
    connector, fake = make_connector()
    assert call(connector, {"to": "probe@example.com", "subject": "", "body": "b"})[0] == 403
    assert call(connector, {"to": "probe@example.com", "subject": "ok\r\nBcc: x@y", "body": "b"})[0] == 403
    assert fake.sent == []
