"""The API's client for the workspace service.

**The API never sees Docker.** It sends a task id and a tool request to the workspace host, with an internal
bearer token. This client is the only place that knows the service URL and token, so tests can replace it and a
deployment can point at a different host without changing the runtime.
"""
from __future__ import annotations

import base64
import json
import urllib.error
import urllib.request


class WorkspaceClientError(Exception):
    """The workspace service refused or failed a request."""


class WorkspaceClient:
    def __init__(self, base_url: str, token: str, *, timeout: int = 120):
        self.base_url = (base_url or "").rstrip("/")
        self.token = token or ""
        self.timeout = timeout

    def _call(self, path: str, body: dict | None = None, *, method: str = "POST") -> dict:
        if not self.base_url or not self.token:
            raise WorkspaceClientError("workspace URL and token are required")
        data = json.dumps(body).encode() if body is not None else None
        request = urllib.request.Request(
            self.base_url + path, data=data, method=method,
            headers={"Content-Type": "application/json",
                     "Authorization": "Bearer " + self.token})
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                return json.loads(response.read() or b"{}")
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode(errors="replace")[:300]
            raise WorkspaceClientError(f"workspace service returned {exc.code}: {detail}") from exc
        except Exception as exc:  # noqa: BLE001
            raise WorkspaceClientError(f"workspace service unreachable: {type(exc).__name__}") from exc

    def write(self, task_id: str, path: str, content: str) -> dict:
        return self._call("/write", {"task_id": task_id, "path": path,
                                     "content": base64.b64encode(content.encode()).decode()})

    def read(self, task_id: str, path: str, max_bytes: int = 64 * 1024) -> dict:
        result = self._call("/read", {"task_id": task_id, "path": path, "max_bytes": max_bytes})
        result["text"] = base64.b64decode(result.get("content") or "").decode(errors="replace")
        return result

    def list(self, task_id: str) -> dict:
        return self._call("/list", {"task_id": task_id})

    def run(self, task_id: str, command: str, timeout: int = 60) -> dict:
        return self._call("/shell", {"task_id": task_id, "script": command, "timeout": timeout})

    def cleanup(self, task_id: str) -> dict:
        return self._call("/cleanup", {"task_id": task_id})
