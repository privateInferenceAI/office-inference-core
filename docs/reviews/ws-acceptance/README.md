# WS acceptance run

**Date:** 2026-10-23. **Status:** WS-ACCEPT-001 is verified.

## The run

Job `2a983a879115486c8bac089f2ffd9f95` used the `workspace_brief` definition with the read-only child.

- It wrote `input.txt`, read it back, fetched `https://example.com`, ran a Python calculation and called
  `delegate_research`. The child rows carry span `child-1`.
- The API process was restarted while `turn-2` was running. Recovery resumed the job and wrote resume row 998.
  The interrupted attempt is recorded as a failed attempt; no succeeded step was repeated.
- The job stopped at `approval-3`. The approval was granted with the pending digest, and `act-3` sent through
  the SES connector to the verified mailbox.
- The job then wrote `acceptance.txt` and finished successfully.
- Artifacts: `acceptance.txt` (671 bytes) and `input.txt` (16 bytes), both content-addressed in
  `oi-core-workspace-artifacts-878451098165`.
- The chain holds 691 events and verifies intact.

## The runtime bug this found

The first attempts failed after the approved send with a Bedrock error: "Expected toolResult blocks". The gate
was recording every tool call from the turn but only returning the result for the approved one. The gate now
records only the held call; the other calls are dropped and the model can reissue them after the decision.

## What was exercised

Workspace write and read, code execution, approved web fetch, child helper, process restart and resume, approval
gate, real SES send, S3 artifacts and chain verification.

## Files

- `job.json`, `result.json`, `artifacts.json`.
- `audit-rows.txt`, `audit-events.jsonl`, `verify.txt`, `export.txt`.
- `objects.json`: the two S3 objects.
- `result-before-child.json`: the earlier run that exposed the gate bug.
- `SHA256SUMS`.
