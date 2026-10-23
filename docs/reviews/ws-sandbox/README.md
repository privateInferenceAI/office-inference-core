# Workspace sandbox, first slice

**Date:** 2026-10-23. **Status:** the sandbox manager and live container proof are built. This is not the WS gate.

## What was built

- `docs/decisions/0010-workspace-sandbox.md`: the decision to run one container per task on a dedicated
  workspace host, with no Docker socket in the API.
- `services/workspace/workspace.py`: the sandbox manager. It builds the Docker command, mounts only the task
  directory, sets network none, a read-only root, dropped capabilities, the host user id, memory, CPU, pid and
  wall-clock limits, checks disk before and after, copies artifacts out content-addressed, and removes the
  container and directory.
- `services/workspace/Dockerfile`: the small task image with Python, bash, coreutils and jq.
- `tests/test_workspace_sandbox.py`: 8 tests over the command, the path guard, cleanup, timeout, disk quota,
  artifacts and the no-sandbox fallback.

## Live proof on the box

Image: `oi-core/workspace:local`, id `cfa6f585631b`, 179 MB.

The task ran with limits of 1 CPU, 256 MB, 64 pids, 30 seconds and 10 MB disk.

- The task wrote and read `/workspace/out.txt` and printed its content.
- `/opt/oi-core` was not visible inside the container (`HOST_HIDDEN`).
- The process ran as uid 1000, not root.
- `/etc/passwd` from the task image was visible, which is expected: the image is the environment.
- A network connection returned `Network is unreachable`.
- The artifact was copied out as `f98287e4...` and its text matched.
- After cleanup, the task directory was gone and no `oi-ws-live-task-1` container remained.

## What this does not prove

- A workspace service on a separate host. The manager was run from the box host, not from the API.
- The API and runtime calling the sandbox as a tool.
- The egress proxy, S3 artifacts, workspace UI and first connector.
- gVisor or microVM isolation. The current substrate is a Docker container.

## Next

Build the workspace service that wraps this manager and runs on the dedicated host, then connect the runtime's
workspace tools to it. Then the egress proxy, artifact store, UI and first connector.
