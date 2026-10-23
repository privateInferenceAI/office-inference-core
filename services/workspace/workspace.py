"""A task-scoped workspace sandbox.

**One container per task.** The container is not the host: only the task directory is mounted, there is no
network, the root filesystem is read-only, capabilities are dropped, and memory, CPU, pid and wall-clock limits
are set. The task directory is removed on cleanup, after its artifacts have been copied out.

**The Docker socket is not held by the API.** This module is intended for the workspace service on a dedicated
host. The API and the runtime call the workspace service, not Docker. Keeping that boundary is the reason the
sandbox exists at all.

The module is deliberately testable without Docker: the command is built by `command()`, and the process
runner is injected. The live deployment uses the default runner.
"""
from __future__ import annotations

import hashlib
import os
import re
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional

DEFAULT_IMAGE = "oi-core/workspace:local"
_TASK_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")


class SandboxError(Exception):
    """The sandbox could not be created, run or cleaned up."""


class SandboxTimeout(SandboxError):
    """The command exceeded its wall-clock budget."""


class SandboxQuota(SandboxError):
    """The task wrote more than its disk allowance."""


@dataclass(frozen=True)
class Limits:
    """Per-task ceilings. They are passed to Docker and checked after the command for disk."""

    cpus: float = 1.0
    memory_mb: int = 512
    pids: int = 128
    seconds: int = 120
    disk_mb: int = 256
    tmp_mb: int = 64
    nofile: int = 1024


class DockerSandbox:
    """The lifecycle of one task's container and directory."""

    def __init__(self, root: str | Path, *, image: str = DEFAULT_IMAGE, docker: str = "docker",
                 limits: Optional[Limits] = None, uid: Optional[int] = None,
                 gid: Optional[int] = None,
                 runner: Optional[Callable[[list[str], int], subprocess.CompletedProcess]] = None):
        self.root = Path(root).resolve()
        self.image = image
        self.docker = docker
        self.limits = limits or Limits()
        self.uid = os.getuid() if uid is None else uid
        self.gid = os.getgid() if gid is None else gid
        self._runner = runner or self._default_runner
        self.root.mkdir(parents=True, exist_ok=True)
        os.chmod(self.root, 0o700)

    # ---- paths

    def task_dir(self, task_id: str) -> Path:
        if not _TASK_ID.match(task_id or ""):
            raise SandboxError(f"invalid task id: {task_id!r}")
        path = (self.root / task_id).resolve()
        if self.root not in path.parents:
            raise SandboxError("the task directory escaped the sandbox root")
        return path

    def create(self, task_id: str) -> Path:
        path = self.task_dir(task_id)
        path.mkdir(mode=0o700, parents=False, exist_ok=True)
        return path

    # ---- the container

    def container_name(self, task_id: str) -> str:
        return "oi-ws-" + task_id

    def command(self, task_id: str, argv: list[str]) -> list[str]:
        """Build the Docker command for one task. No host environment and no extra mounts."""
        if not argv:
            raise SandboxError("a sandbox command needs an argv")
        directory = self.task_dir(task_id)
        limits = self.limits
        return [
            self.docker, "run", "--rm", "--name", self.container_name(task_id),
            "--network", "none",
            "--memory", f"{limits.memory_mb}m",
            "--memory-swap", f"{limits.memory_mb}m",
            "--cpus", str(limits.cpus),
            "--pids-limit", str(limits.pids),
            "--ulimit", f"nofile={limits.nofile}:{limits.nofile}",
            "--cap-drop", "ALL",
            "--security-opt", "no-new-privileges",
            "--user", f"{self.uid}:{self.gid}",
            "--read-only",
            "--tmpfs", f"/tmp:rw,noexec,nosuid,size={limits.tmp_mb}m",
            "--workdir", "/workspace",
            "-e", "HOME=/workspace",
            "-v", f"{directory}:/workspace:rw",
            self.image,
            *argv,
        ]

    def run(self, task_id: str, argv: list[str], *, timeout: Optional[int] = None) -> subprocess.CompletedProcess:
        """Run one command, bounded by the task's wall clock and disk allowance."""
        directory = self.create(task_id)
        before = self.size_bytes(task_id)
        if before > self.limits.disk_mb * 1024 * 1024:
            raise SandboxQuota(f"the task already uses {before} bytes")
        budget = min(int(timeout or self.limits.seconds), self.limits.seconds)
        try:
            result = self._runner(self.command(task_id, argv), budget)
        except subprocess.TimeoutExpired as exc:
            self._kill(task_id)
            raise SandboxTimeout(f"the command exceeded {budget} seconds") from exc
        after = self.size_bytes(task_id)
        if after > self.limits.disk_mb * 1024 * 1024:
            raise SandboxQuota(f"the task wrote {after} bytes, over its {self.limits.disk_mb} MB allowance")
        return result

    def run_shell(self, task_id: str, script: str, *, timeout: Optional[int] = None) -> subprocess.CompletedProcess:
        if not script or "\x00" in script:
            raise SandboxError("a shell command needs a non-empty script without NUL bytes")
        return self.run(task_id, ["/bin/sh", "-lc", script], timeout=timeout)

    def _kill(self, task_id: str) -> None:
        try:
            self._runner([self.docker, "kill", self.container_name(task_id)], 10)
        except Exception:  # noqa: BLE001 - the container may already be gone
            pass

    @staticmethod
    def _default_runner(argv: list[str], timeout: int) -> subprocess.CompletedProcess:
        return subprocess.run(argv, capture_output=True, text=True, timeout=timeout, check=False)

    # ---- disk and teardown

    def size_bytes(self, task_id: str) -> int:
        total = 0
        path = self.task_dir(task_id)
        if not path.exists():
            return 0
        for entry in path.rglob("*"):
            try:
                if entry.is_file() and not entry.is_symlink():
                    total += entry.stat().st_size
            except OSError:
                continue
        return total

    def cleanup(self, task_id: str) -> None:
        """Remove the container, then the task directory. Idempotent."""
        try:
            self._runner([self.docker, "rm", "-f", self.container_name(task_id)], 10)
        except Exception:  # noqa: BLE001 - no container is a normal cleanup
            pass
        path = self.task_dir(task_id)
        if path.exists():
            shutil.rmtree(path, ignore_errors=False)

    # ---- artifacts

    def collect_artifacts(self, task_id: str, destination: str | Path) -> list[dict]:
        """Copy the task's files out under content-addressed names, and return what was copied."""
        source = self.task_dir(task_id)
        target = Path(destination)
        target.mkdir(parents=True, exist_ok=True)
        records = []
        if not source.exists():
            return records
        for entry in sorted(source.rglob("*")):
            if not entry.is_file() or entry.is_symlink():
                continue
            data = entry.read_bytes()
            digest = hashlib.sha256(data).hexdigest()
            stored = target / digest
            if not stored.exists():
                with tempfile.NamedTemporaryFile(dir=target, delete=False) as handle:
                    handle.write(data)
                    temp = Path(handle.name)
                os.replace(temp, stored)
            records.append({"path": str(entry.relative_to(source)), "sha256": digest,
                            "bytes": len(data)})
        return records


class MemorySandbox:
    """A stand-in used by tests and by a deployment with no workspace host.

    It refuses to run code. A caller that asks for it gets a typed refusal rather than a hidden shell on the
    API host.
    """

    def __init__(self, reason: str = "this deployment has no workspace sandbox"):
        self.reason = reason

    def run(self, task_id: str, argv: list[str], *, timeout: Optional[int] = None):
        raise SandboxError(self.reason)

    def run_shell(self, task_id: str, script: str, *, timeout: Optional[int] = None):
        raise SandboxError(self.reason)
