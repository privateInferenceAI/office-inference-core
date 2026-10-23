"""The workspace service: an internal HTTP API over the task sandbox.

**Why a service and not a module in the API.** The sandbox manager needs the Docker socket. The API holds the
model signing key and the client data path. Those are two different privileges and they do not belong in one
container. This service runs on the workspace host, holds the socket, and the API reaches it with an internal
bearer token.

The service is deliberately small: run, shell, write, read, list and cleanup. It does not decide whether a task
may use the workspace; the runtime's definition and the approval gate do. It does not store artifacts; the
artifact copy-out is a separate deployment step.
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import re
import secrets
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import workspace  # noqa: E402

MAX_BODY = 2 * 1024 * 1024
MAX_OUTPUT = 64 * 1024


class WorkspaceService:
    """The request handler, with no HTTP server in it so the API can be tested directly."""

    def __init__(self, sandbox, token: str, *, max_body: int = MAX_BODY,
                 max_output: int = MAX_OUTPUT, artifact_root: str | Path | None = None,
                 max_artifact_bytes: int = 2 * 1024 * 1024):
        if not token:
            raise ValueError("the workspace service refuses to start without a token")
        self.sandbox = sandbox
        self.token = token
        self.max_body = max_body
        self.max_output = max_output
        self.artifact_root = Path(artifact_root or (Path(sandbox.root).parent / "artifacts")).resolve()
        self.artifact_root.mkdir(parents=True, exist_ok=True)
        self.max_artifact_bytes = max_artifact_bytes

    def handle(self, method: str, path: str, headers: dict, body: bytes):
        try:
            if path == "/health" and method == "GET":
                return 200, {"status": "ok"}
            if not self._authorised(headers):
                return 401, {"error": "unauthorised"}
            if method != "POST":
                return 405, {"error": "method not allowed"}
            if len(body) > self.max_body:
                return 413, {"error": "request too large"}
            try:
                payload = json.loads(body or b"{}")
            except (ValueError, TypeError):
                return 400, {"error": "body must be JSON"}
            if not isinstance(payload, dict):
                return 400, {"error": "body must be a JSON object"}
            if path == "/run":
                return self._run(payload, shell=False)
            if path == "/shell":
                return self._run(payload, shell=True)
            if path == "/write":
                return self._write(payload)
            if path == "/read":
                return self._read(payload)
            if path == "/list":
                return self._list(payload)
            if path == "/cleanup":
                return self._cleanup(payload)
            if path == "/artifacts":
                return self._artifacts(payload)
            if path == "/artifact":
                return self._artifact(payload)
            return 404, {"error": "not found"}
        except workspace.SandboxTimeout as exc:
            return 504, {"error": "timeout", "detail": str(exc)}
        except workspace.SandboxQuota as exc:
            return 413, {"error": "quota", "detail": str(exc)}
        except workspace.SandboxError as exc:
            return 400, {"error": "sandbox", "detail": str(exc)}
        except Exception as exc:  # noqa: BLE001 - the service must answer, not crash
            return 500, {"error": "internal", "detail": type(exc).__name__}

    # ---- auth and helpers

    def _authorised(self, headers: dict) -> bool:
        supplied = ""
        for key, value in headers.items():
            if key.lower() == "authorization":
                supplied = str(value)
                break
        if not supplied.startswith("Bearer "):
            return False
        return secrets.compare_digest(supplied[7:], self.token)

    def _task(self, payload: dict) -> str:
        task_id = str(payload.get("task_id") or "")
        if not task_id:
            raise workspace.SandboxError("task_id is required")
        self.sandbox.create(task_id)
        return task_id

    def _relative(self, task_id: str, path: str) -> Path:
        """A task-relative path that cannot leave the task directory."""
        value = str(path or "")
        if not value or value.startswith("/") or ".." in Path(value).parts:
            raise workspace.SandboxError("path must stay inside the task directory")
        base = self.sandbox.task_dir(task_id)
        target = (base / value).resolve()
        if base not in target.parents and target != base:
            raise workspace.SandboxError("path escaped the task directory")
        return target

    def _truncate(self, text: str) -> str:
        if len(text) <= self.max_output:
            return text
        return text[:self.max_output] + "\n[output truncated by the workspace service]"

    def _run(self, payload: dict, *, shell: bool):
        task_id = self._task(payload)
        timeout = payload.get("timeout")
        if timeout is not None:
            timeout = int(timeout)
            if timeout < 1 or timeout > 600:
                return 400, {"error": "timeout must be between 1 and 600 seconds"}
        if shell:
            script = str(payload.get("script") or "")
            if not script:
                return 400, {"error": "script is required"}
            result = self.sandbox.run_shell(task_id, script, timeout=timeout)
        else:
            argv = payload.get("argv")
            if not isinstance(argv, list) or not argv or not all(isinstance(x, str) for x in argv):
                return 400, {"error": "argv must be a non-empty list of strings"}
            result = self.sandbox.run(task_id, argv, timeout=timeout)
        return 200, {"returncode": result.returncode,
                     "stdout": self._truncate(result.stdout or ""),
                     "stderr": self._truncate(result.stderr or "")}

    def _write(self, payload: dict):
        task_id = self._task(payload)
        target = self._relative(task_id, str(payload.get("path") or ""))
        try:
            content = base64.b64decode(str(payload.get("content") or ""), validate=True)
        except Exception:  # noqa: BLE001
            return 400, {"error": "content must be base64"}
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)
        return 200, {"path": str(target.relative_to(self.sandbox.task_dir(task_id))),
                     "sha256": hashlib.sha256(content).hexdigest(), "bytes": len(content)}

    def _read(self, payload: dict):
        task_id = self._task(payload)
        target = self._relative(task_id, str(payload.get("path") or ""))
        if not target.is_file() or target.is_symlink():
            return 404, {"error": "no such file"}
        limit = int(payload.get("max_bytes") or MAX_OUTPUT)
        limit = max(1, min(limit, MAX_OUTPUT))
        data = target.read_bytes()
        truncated = len(data) > limit
        return 200, {"path": str(target.relative_to(self.sandbox.task_dir(task_id))),
                     "content": base64.b64encode(data[:limit]).decode(),
                     "bytes": len(data), "truncated": truncated}

    def _list(self, payload: dict):
        task_id = self._task(payload)
        base = self.sandbox.task_dir(task_id)
        records = []
        total = 0
        if base.exists():
            for entry in sorted(base.rglob("*")):
                if entry.is_file() and not entry.is_symlink():
                    size = entry.stat().st_size
                    total += size
                    records.append({"path": str(entry.relative_to(base)), "bytes": size})
        return 200, {"files": records, "count": len(records), "bytes": total}

    def _cleanup(self, payload: dict):
        task_id = self._task(payload)
        self.sandbox.cleanup(task_id)
        return 200, {"cleaned": True, "task_id": task_id}

    def _artifacts(self, payload: dict):
        """Copy the task's files to the artifact store before the task directory is removed."""
        task_id = self._task(payload)
        destination = self.artifact_root / task_id
        records = self.sandbox.collect_artifacts(task_id, destination)
        return 200, {"task_id": task_id, "artifacts": records}

    def _artifact(self, payload: dict):
        """Read one stored artifact by its content hash."""
        task_id = str(payload.get("task_id") or "")
        self.sandbox.task_dir(task_id)  # validate without creating a directory after cleanup
        sha = str(payload.get("sha256") or "")
        if not re.fullmatch(r"[0-9a-f]{64}", sha):
            return 400, {"error": "sha256 must be 64 lowercase hex characters"}
        target = (self.artifact_root / task_id / sha).resolve()
        base = (self.artifact_root / task_id).resolve()
        if base not in target.parents:
            return 400, {"error": "artifact escaped its task directory"}
        if not target.is_file() or target.is_symlink():
            return 404, {"error": "no such artifact"}
        data = target.read_bytes()
        if len(data) > self.max_artifact_bytes:
            return 413, {"error": "artifact too large for one download"}
        return 200, {"task_id": task_id, "sha256": sha, "bytes": len(data),
                     "content": base64.b64encode(data).decode()}


class _Handler(BaseHTTPRequestHandler):
    service: WorkspaceService = None

    def do_GET(self):  # noqa: N802
        self._handle("GET")

    def do_POST(self):  # noqa: N802
        self._handle("POST")

    def _handle(self, method: str):
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            length = -1
        if length < 0 or length > self.service.max_body:
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

    def log_message(self, *args, **kwargs):  # noqa: D401 - quiet by design
        return


def serve(root: str, token: str, host: str = "0.0.0.0", port: int = 8090,
          image: str = workspace.DEFAULT_IMAGE):
    sandbox = workspace.DockerSandbox(root, image=image)
    _Handler.service = WorkspaceService(sandbox, token)
    server = ThreadingHTTPServer((host, port), _Handler)
    server.serve_forever()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="workspace sandbox service")
    parser.add_argument("--root", default=os.environ.get("WORKSPACE_ROOT", "/var/lib/oi-workspace"))
    parser.add_argument("--host", default=os.environ.get("WORKSPACE_HOST", "0.0.0.0"))
    parser.add_argument("--port", type=int, default=int(os.environ.get("WORKSPACE_PORT", "8090")))
    parser.add_argument("--image", default=os.environ.get("WORKSPACE_IMAGE", workspace.DEFAULT_IMAGE))
    args = parser.parse_args(argv)
    token = os.environ.get("WORKSPACE_TOKEN", "")
    if not token:
        print("WORKSPACE_TOKEN is required; refusing to start an unauthenticated workspace service",
              file=sys.stderr)
        return 2
    serve(args.root, token, args.host, args.port, args.image)
    return 0


if __name__ == "__main__":
    sys.exit(main())
