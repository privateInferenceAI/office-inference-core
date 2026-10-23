# Workspace sandbox

**Date:** 2026-10-23. **Status:** WS-SANDBOX-001 is verified.

## What was built

- `services/workspace/workspace.py`: one container per task. Network none, only the task directory mounted,
  read-only root, dropped capabilities, host uid, memory, CPU, pid, wall-clock, CPU-time and disk ceilings.
- `services/workspace/Dockerfile`: the task image with Python, bash, coreutils, jq and `/usr/bin/time`.
- Teardown removes the container and the task directory. Artifacts are copied out content-addressed before
  teardown.

## Live proof

Task `live-task-1` wrote and read `/workspace/out.txt`, could not see `/opt/oi-core`, could not reach the
network, ran as uid 1000, and left no directory or container.

The CPU-time and isolation probe:

```
cpu:       typed resource kill, "the task used 3.21 CPU seconds, over its 0.20 allowance"
isolation: OWN: .oi-cpu, a.txt
           HOST: HIDDEN
           OTHER: HIDDEN
leftover:  []
```

The memory, disk and wall-clock limits are typed in `docs/reviews/ws-limits/limits-typed.json`. The CPU ceiling
is now a typed CPU-time stop rather than only a Docker throttle.

## Tests

`tests/test_workspace_sandbox.py` covers the command, the path guard, cleanup, timeout, memory exit 137, CPU
time, disk quota, artifacts and the no-sandbox fallback.

## Files

- `c6-ws-live-result.json`: the first container proof.
- `containers.txt`, `c6-ws-image.txt`: the image and the empty container list.
- `isolation-cpu.json`: the CPU and isolation probe.
- `SHA256SUMS`.
