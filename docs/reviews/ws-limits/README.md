# Workspace limits, live probe

**Date:** 2026-10-23. **Status:** WS-LIMITS-001 is verified.

The task image was `oi-core/workspace:local`, with limits of 0.5 CPU, 32 MB memory, 32 pids, 2 seconds and a
1 MB disk allowance.

## Sandbox ceilings

- **Wall clock:** `sleep 10` with a 2 second budget raised the typed `SandboxTimeout`.
- **Memory:** a 128 MB allocation under a 32 MB limit was killed, and exit 137 is now the typed
  `SandboxResource` with the likely memory cause in the detail.
- **Disk:** a 2 MB file under a 1 MB allowance raised the typed `SandboxQuota`.
- **Recovery:** a task started after the kill returned `recovered` with exit 0.
- **Aftermath:** no task directories were left behind.

## Token budget

The workspace definition was set to a one-token `max_tokens` and a job was submitted. Job
`b266f088bb594052891f3e60fe483d18` stopped at `turn-1` with failure kind `token_budget` and
`outcome_reason` `token_budget`. The definition was restored after the run.

The parent token total comes from each model call's usage. When a provider omits usage, the runtime estimates
from content. The running total is on the model audit rows.

## What remains

CPU is a Docker throttle, not a kill with a typed cause. The limit is set and enforced as a ceiling. If the row
needs a typed CPU event specifically, that is a separate build.

## Files

- `limits.json`: the first probe.
- `limits-typed.json`: the probe with typed memory and a recovery task.
- `token-budget.json`: the failed-over-budget job.
- `SHA256SUMS`.
