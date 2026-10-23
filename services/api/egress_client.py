"""The API's client for the egress service. The sandbox never sees this token."""
from __future__ import annotations

import json
import urllib.error
import urllib.request


class EgressClientError(Exception):
    """The egress service refused or failed a fetch."""


class EgressClient:
    def __init__(self, base_url: str, token: str, *, timeout: int = 30):
        self.base_url = (base_url or "").rstrip("/")
        self.token = token or ""
        self.timeout = timeout

    def fetch(self, url: str) -> dict:
        if not self.base_url or not self.token:
            raise EgressClientError("egress URL and token are required")
        request = urllib.request.Request(
            self.base_url + "/fetch", data=json.dumps({"url": url}).encode(), method="POST",
            headers={"Content-Type": "application/json", "Authorization": "Bearer " + self.token})
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                return json.loads(response.read() or b"{}")
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode(errors="replace")[:300]
            raise EgressClientError(f"egress service returned {exc.code}: {detail}") from exc
        except Exception as exc:  # noqa: BLE001
            raise EgressClientError(f"egress service unreachable: {type(exc).__name__}") from exc
