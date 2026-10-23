# Workspace limits, live probe

**Date:** 2026-10-23. **Status:** the wall-clock, disk and memory ceilings were exercised against a real
container. This is not the WS gate.

The task image was `oi-core/workspace:local`, with limits of 0.5 CPU, 32 MB memory, 32 pids, 2 seconds and a
1 MB disk allowance.

- **Wall clock:** `sleep 10` with a 2 second budget raised the typed `SandboxTimeout` and killed the
  container.
- **Memory:** a 128 MB allocation under a 32 MB limit exited 137 (SIGKILL). The container enforced the limit; the
  manager does not yet turn 137 into a typed memory cause.
- **Disk:** a 2 MB file under a 1 MB allowance was refused before the next command with `SandboxQuota`.
- **Aftermath:** no task directories were left behind.

## What remains for WS-LIMITS

- A typed cause for the memory kill, rather than exit 137.
- A live parent token budget stop. The child token budget is live-tested; the parent budget is not.
- A task that runs after the killed task on the same service, to show the deployment stayed healthy.

## Files

- `limits.json`: the probe output.
- `SHA256SUMS`.
