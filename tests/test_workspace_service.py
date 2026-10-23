"""The workspace service: auth, the task path, the files and the response caps."""
from __future__ import annotations

import base64
import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "services" / "workspace"))

import service as service_module  # noqa: E402
import workspace  # noqa: E402


class FakeRunner:
    def __init__(self, stdout="ok", stderr=""):
        self.calls = []
        self.stdout, self.stderr = stdout, stderr

    def __call__(self, argv, timeout):
        self.calls.append((list(argv), timeout))
        return subprocess.CompletedProcess(argv, 0, self.stdout, self.stderr)


def make_service(tmp_path, *, max_body=service_module.MAX_BODY):
    runner = FakeRunner("hello", "")
    sandbox = workspace.DockerSandbox(tmp_path / "tasks", runner=runner)
    return service_module.WorkspaceService(sandbox, "secret-token", max_body=max_body), sandbox, runner


def call(service, path, body=None, *, token="secret-token", method="POST"):
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    return service.handle(method, path, headers, json.dumps(body or {}).encode())


def test_health_is_available_without_a_token(tmp_path):
    service, _, _ = make_service(tmp_path)
    assert service.handle("GET", "/health", {}, b"") == (200, {"status": "ok"})


def test_every_task_route_requires_the_bearer_token(tmp_path):
    service, _, _ = make_service(tmp_path)
    assert call(service, "/list", {"task_id": "t"}, token=None)[0] == 401
    assert call(service, "/list", {"task_id": "t"}, token="wrong")[0] == 401


def test_write_read_and_list_stay_inside_the_task(tmp_path):
    service, sandbox, _ = make_service(tmp_path)
    content = base64.b64encode(b"hello workspace").decode()
    status, written = call(service, "/write", {"task_id": "t1", "path": "notes/a.txt", "content": content})
    assert status == 200
    assert written["bytes"] == 15

    status, read = call(service, "/read", {"task_id": "t1", "path": "notes/a.txt"})
    assert status == 200
    assert base64.b64decode(read["content"]) == b"hello workspace"

    status, listed = call(service, "/list", {"task_id": "t1"})
    assert status == 200
    assert listed["files"] == [{"path": "notes/a.txt", "bytes": 15}]


def test_a_path_that_tries_to_leave_the_task_is_refused(tmp_path):
    service, _, _ = make_service(tmp_path)
    content = base64.b64encode(b"x").decode()
    assert call(service, "/write", {"task_id": "t1", "path": "../escape", "content": content})[0] == 400
    assert call(service, "/write", {"task_id": "t1", "path": "/etc/passwd", "content": content})[0] == 400
    assert not (tmp_path / "escape").exists()


def test_run_returns_the_command_result_and_the_shell_route_uses_a_script(tmp_path):
    service, _, runner = make_service(tmp_path)
    status, result = call(service, "/run", {"task_id": "t1", "argv": ["python3", "-V"], "timeout": 9})
    assert status == 200 and result["returncode"] == 0 and result["stdout"] == "hello"
    assert runner.calls[-1][1] == 9

    status, result = call(service, "/shell", {"task_id": "t1", "script": "echo hi", "timeout": 4})
    assert status == 200
    assert runner.calls[-1][0][-3:] == ["/bin/sh", "-lc", "echo hi"]


def test_cleanup_removes_the_task_directory(tmp_path):
    service, sandbox, _ = make_service(tmp_path)
    sandbox.create("t1")
    (sandbox.task_dir("t1") / "x").write_text("x")
    status, _ = call(service, "/cleanup", {"task_id": "t1"})
    assert status == 200
    assert not sandbox.task_dir("t1").exists()


def test_a_body_over_the_cap_is_refused_before_it_is_parsed(tmp_path):
    service, _, _ = make_service(tmp_path, max_body=8)
    status, payload = call(service, "/list", {"task_id": "a-longer-body"})
    assert status == 413


def test_an_unknown_route_is_a_404(tmp_path):
    service, _, _ = make_service(tmp_path)
    assert call(service, "/delete_everything", {"task_id": "t1"})[0] == 404
