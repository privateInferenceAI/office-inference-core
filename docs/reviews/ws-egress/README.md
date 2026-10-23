# Allowlisted egress, live

**Date:** 2026-10-23. **Status:** the web fetch path is live. This is not the WS gate.

## What was built

- `services/egress/proxy.py`: a token-guarded fetch service with an allowlist. It accepts https on the default
  port only, rejects credentials in the URL, supports wildcard subdomains and caps response bytes.
- `services/api/egress_client.py`: the API's client. The sandbox never sees the token.
- The `web_fetch` runtime tool, only for a definition with `web: true`.
- The API audits each fetch with the URL, status and bytes, by job and step.
- `tests/test_egress_proxy.py`: 7 tests. The full targeted set is 187.

## Live proof

The egress service ran on the box at `0.0.0.0:8091` with allowlist `example.com`. The API reached it at
`http://host.docker.internal:8091`.

Job `f418380cef994d47ab42f2c2aad69244` fetched `https://example.com` and wrote `page.txt` with the title.

- State: `succeeded`, five steps.
- Audit row 771 is the `web_fetch` row: URL `https://example.com`, status 200, 577 bytes.
- The artifact `page.txt` is 14 bytes and contains `Example Domain`.
- The chain export holds 450 events and verifies intact.
- No container or task directory remained.

## What this does not prove

- A live refusal of a URL off the allowlist through a full job. The unit tests cover the refusal.
- A client-facing egress policy editor. The allowlist is a deployment environment value.
- A separate egress host or a proxy that streams binaries.

## Files

- `job.json`: the successful job.
- `artifacts.json`: the artifact list.
- `artifact-page.txt`: the stored `page.txt` bytes.
- `audit-rows.txt`: the rows, including the `web_fetch` row.
- `audit-events.jsonl`: the 450-event chain export.
- `verify.txt`: the verifier's result.
- `containers.txt` and `task-dir.txt`: empty/gone after cleanup.
- `egress.log`: empty; no service errors.
- `SHA256SUMS`.

## Next

The S3 artifact store and retention, the workspace UI, the first connector and the acceptance task.
