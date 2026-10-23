"""The task sandbox: the command, the limits, the teardown and the artifacts.

**What these tests can prove without Docker.** They prove the command the manager would run has no network, only
the task directory mounted, dropped capabilities, a read-only root and the limits; that the lifecycle creates and
removes the directory; that a timeout is typed; that the disk allowance is checked; and that artifacts are copied
out content-addressed. They cannot prove the container runtime enforces any of it. That is the live probe's job.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "services" / "workspace"))

import workspace  # noqa: E402


class FakeRunner:
    def __init__(self, *, timeout=False, returncode=0):
        self.calls = []
        self.timeout = timeout
        self.returncode = returncode

    def __call__(self, argv, timeout):
        self.calls.append((list(argv), timeout))
        if self.timeout:
            raise subprocess.TimeoutExpired(argv, timeout)
        return subprocess.CompletedProcess(argv, self.returncode, "ok", "")


def make_sandbox(tmp_path, **kwargs):
    return workspace.DockerSandbox(tmp_path / "tasks", **kwargs)


def test_the_command_has_no_network_only_the_task_mount_and_dropped_capabilities(tmp_path):
    runner = FakeRunner()
    sandbox = make_sandbox(tmp_path, runner=runner)
    directory = sandbox.create("task-1")
    argv = sandbox.command("task-1", ["python3", "-V"])
    joined = " ".join(argv)

    assert argv[:2] == ["docker", "run"]
    assert "--network" in argv and "none" in argv
    assert "--read-only" in argv
    assert "--cap-drop" in argv and "ALL" in argv
    assert "no-new-privileges" in argv
    assert f"{directory}:/workspace:rw" in argv
    assert "--user" in argv
    assert "--memory" in argv and "--cpus" in argv and "--pids-limit" in argv
    assert "/opt/oi-core" not in joined
    assert "rendered" not in joined
    assert "OI_ASSERTION" not in joined
    # No host environment is forwarded; HOME is the only container variable the manager sets.
    assert argv.count("-e") == 1 and "HOME=/workspace" in argv


def test_a_task_id_cannot_escape_the_sandbox_root(tmp_path):
    sandbox = make_sandbox(tmp_path)
    with pytest.raises(workspace.SandboxError):
        sandbox.task_dir("../other")
    with pytest.raises(workspace.SandboxError):
        sandbox.task_dir("")
    with pytest.raises(workspace.SandboxError):
        sandbox.task_dir("task/child")


def test_create_and_cleanup_remove_the_task_directory(tmp_path):
    runner = FakeRunner()
    sandbox = make_sandbox(tmp_path, runner=runner)
    directory = sandbox.create("task-1")
    (directory / "out.txt").write_text("hello")
    assert directory.exists()

    sandbox.cleanup("task-1")

    assert not directory.exists()
    assert any(call[0][:3] == ["docker", "rm", "-f"] for call in runner.calls)


def test_run_passes_the_wall_clock_budget_and_the_command_result_through(tmp_path):
    runner = FakeRunner()
    sandbox = make_sandbox(tmp_path, runner=runner)
    result = sandbox.run("task-1", ["python3", "script.py"], timeout=7)
    assert result.returncode == 0
    argv, timeout = runner.calls[-1]
    assert timeout == 7
    assert argv[-3:] == ["python3", "script.py"] or argv[-2:] == ["python3", "script.py"]


def test_exit_137_is_a_typed_resource_kill(tmp_path):
    runner = FakeRunner(returncode=137)
    sandbox = make_sandbox(tmp_path, runner=runner)
    with pytest.raises(workspace.SandboxResource):
        sandbox.run("task-1", ["python3", "-c", "allocate"])


def test_a_timeout_is_typed_and_kills_the_container(tmp_path):
    runner = FakeRunner(timeout=True)
    sandbox = make_sandbox(tmp_path, runner=runner)
    with pytest.raises(workspace.SandboxTimeout):
        sandbox.run_shell("task-1", "sleep 999", timeout=1)
    assert any(call[0][:2] == ["docker", "kill"] for call in runner.calls)


def test_a_task_over_its_disk_allowance_is_refused(tmp_path):
    runner = FakeRunner()
    sandbox = make_sandbox(tmp_path, runner=runner, limits=workspace.Limits(disk_mb=1))
    directory = sandbox.create("task-1")
    (directory / "big.bin").write_bytes(b"x" * (2 * 1024 * 1024))
    with pytest.raises(workspace.SandboxQuota):
        sandbox.run("task-1", ["true"])
    assert runner.calls == [], "the command must not run when the task is already over quota"


def test_artifacts_are_copied_out_content_addressed_and_deduplicated(tmp_path):
    sandbox = make_sandbox(tmp_path)
    directory = sandbox.create("task-1")
    (directory / "a.txt").write_text("same")
    (directory / "sub").mkdir()
    (directory / "sub" / "b.txt").write_text("same")
    (directory / "c.txt").write_text("different")

    records = sandbox.collect_artifacts("task-1", tmp_path / "artifacts")

    assert len(records) == 3
    assert len({record["sha256"] for record in records}) == 2
    stored = {path.name for path in (tmp_path / "artifacts").iterdir()}
    assert len(stored) == 2


def test_memory_sandbox_refuses_instead_of_running_on_the_api_host():
    sandbox = workspace.MemorySandbox("none here")
    with pytest.raises(workspace.SandboxError):
        sandbox.run("task-1", ["true"])
    with pytest.raises(workspace.SandboxError):
        sandbox.run_shell("task-1", "echo hi")
