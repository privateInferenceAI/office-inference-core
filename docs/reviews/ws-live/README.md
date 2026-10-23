# Live workspace job

**Date:** 2026-10-23. **Status:** the runtime's workspace tools are live-tested. This is not the WS gate.

## Deployment

- The workspace service ran on the box host at `0.0.0.0:8090`, root `/opt/oi-core/data/workspaces`, image
  `oi-core/workspace:local`.
- The API container reached it at `http://host.docker.internal:8090` through the Docker bridge gateway, with
  `OI_WORKSPACE_TOKEN`.
- The rendered definitions included `workspace_brief`.

## Job

Job `5a5e153d06aa4a028c616269bdd4068d` ran the `workspace_brief` definition with the task: write
`note.txt` containing `workspace live`, run `cat note.txt`, and check the expense policy with a citation.

- State: `succeeded`, four steps, 12.4 seconds.
- Audit rows: `workspace_write` at 720 and `workspace_run` at 725, each addressed by job and step.
- The chain export holds 395 events and verifies intact.
- No `oi-ws-5a5e...` container remained.
- The task directory was gone after the job.

## What this proves

- The API can call the workspace service with the internal token.
- The runtime routes the workspace tools and records each call in the chain.
- The sandbox executes the work and the task is cleaned up after a terminal job.

## What it does not prove

- Disk and memory quota refusal on a live task.
- Approved web egress, artifact storage, workspace UI and the first connector.
- The service running as a container on a separate workspace host; this run used the host process and the bridge
  gateway.

## Files

- `job.json`: the successful job.
- `audit-rows.txt`: the workspace tool rows and surrounding calls.
- `containers.txt`: empty, no task container remained.
- `task-dir.txt`: the task directory was gone.
- `audit-events.jsonl`: the 395-event chain export.
- `verify.txt`: the verifier's result.
- `export.txt`: the export summary.
- `service.log`: empty; the service logged no errors.
- `SHA256SUMS`.
