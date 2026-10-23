# Workspace artifacts, first slice

**Date:** 2026-10-23. **Status:** the artifact path is live on the box with a local store. This is not the WS gate.

## What was built

- The workspace service collects the task's files to a content-addressed artifact root before cleanup, and
  serves them by hash after the task directory is gone.
- The API records the artifact list as a job event, exposes `GET /jobs/{id}/artifacts`, and proxies
  `GET /jobs/{id}/artifacts/{sha256}` behind the job's session rule.
- `tests/test_workspace_service.py` covers collection before cleanup, download afterward and a bad hash path.

## Live proof

Job `feb69571a70743e79f2a4ed2411dc01f` ran `workspace_brief`, wrote `report.txt`, ran `wc -c` and succeeded in
five steps.

- The artifact list returned one record: `report.txt`, 851 bytes, sha256
  `80c44fe42899f1a791d65a82b0d6a3c55a6dc53bce557adda2eed6e2d0576f31`.
- The download route returned 851 bytes. The head is the travel approval section of the policy.
- The stored artifact survived the task cleanup; no container and no task directory remained.
- The chain export holds 431 events and verifies intact.

## What this does not prove

- S3 in the client's account, retention policy and lifecycle.
- A UI listing or browser download. The API routes are the first layer.
- A separate workspace host. The store is local disk on the box host.

## Files

- `job.json`: the successful job.
- `artifacts.json`: the list route response.
- `audit-rows.txt`: the artifact and workspace tool rows.
- `containers.txt` and `task-dir.txt`: both empty/gone after cleanup.
- `audit-events.jsonl`: the 431-event chain export.
- `verify.txt`: the verifier's result.
- `export.txt`: the export summary.
- `service.log`: empty; no service errors.
- `SHA256SUMS`.

## Next

S3 and retention, the egress proxy, the UI listing, the first connector and the acceptance task.
