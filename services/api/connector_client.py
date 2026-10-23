"""The API's client for the email connector. The sandbox never sees this token or the SMTP credential."""
from __future__ import annotations

import json
import urllib.error
import urllib.request


class ConnectorClientError(Exception):
    """The connector refused or failed a send."""


class ConnectorClient:
    def __init__(self, base_url: str, token: str, *, timeout: int = 30):
        self.base_url = (base_url or "").rstrip("/")
        self.token = token or ""
        self.timeout = timeout

    def send_email(self, to: str, subject: str, body: str) -> dict:
        if not self.base_url or not self.token:
            raise ConnectorClientError("connector URL and token are required")
        request = urllib.request.Request(
            self.base_url + "/send",
            data=json.dumps({"to": to, "subject": subject, "body": body}).encode(), method="POST",
            headers={"Content-Type": "application/json", "Authorization": "Bearer " + self.token})
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                return json.loads(response.read() or b"{}")
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode(errors="replace")[:300]
            raise ConnectorClientError(f"connector returned {exc.code}: {detail}") from exc
        except Exception as exc:  # noqa: BLE001
            raise ConnectorClientError(f"connector unreachable: {type(exc).__name__}") from exc
