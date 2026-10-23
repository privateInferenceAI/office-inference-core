# WS phase gate report

**Date:** 2026-10-23. **Status:** all ten WS rows are verified on the cloud development deployment.
This is a phase gate for the workspace build, not a claim that the full C6 MCP section is done.

## Rows

| Row | Evidence |
|---|---|
| WS-SANDBOX-001 | `docs/reviews/ws-sandbox/`: container per task, host and other task hidden, typed CPU, memory, disk and wall-clock stops, no leftovers. |
| WS-WEB-001 | `docs/reviews/ws-egress/`: allowlisted https fetch through the egress service, row 771, artifact title. |
| WS-ARTIFACT-001 | `docs/reviews/ws-s3/`: bucket `oi-core-workspace-artifacts-878451098165`, SSE-S3, versioning, 365-day lifecycle, job download. |
| WS-UI-001 | `docs/reviews/ws-ui/`: `oic-ui#1` artifact panel; UI tests 162 passed, 12 skipped. |
| WS-CONNECTOR-001 | `docs/reviews/ws-connector-ses/`: real SES send, approval row 854, send row 857. |
| WS-CRED-001 | `docs/reviews/ws-cred/`: API holds only the gateway token, gateway holds the connector token, sandbox holds neither. |
| WS-LIMITS-001 | `docs/reviews/ws-limits/`: typed memory, disk, wall-clock and token stops; recovery task. |
| WS-AUDIT-001 | `docs/reviews/ws-connector/`: one job with workspace write, run, web fetch and connector send; rows 807, 811, 815, 824; chain intact. |
| WS-ACCEPT-001 | `docs/reviews/ws-acceptance/`: read/write, fetch, code, child-1, resume row 998, approval, SES send, two S3 artifacts, 691-event chain. |
| WS-PROFILE-001 | `docs/reviews/ws-profile/`: workspace pack is cloud-only and refused on office; 10/10 manifests valid. |

## Deployment state

- Box `i-02aba2feef08f8e7b`, account 878451098165, deployment `oi-core-dev-cloud-workspace`.
- Workspace service on 8090, egress 8091, connector 8092, MCP gateway 8093.
- S3 bucket in the client account; SES identity `anthony@officeinference.com` verified; instance role has
  artifact read/write and SES send.
- Full suite: **1336 passed, 1 skipped**.

## Known gaps after this gate

- The full Section 3 MCP gateway is not done. The credential slice is built; registry/session policy, per-connector
  credential rotation, data-class egress, ownership checks and SEC-MCP remain open.
- The workspace service runs on the same box as the API. A separate workspace host and its security group are the
  infrastructure part of the profile.
- The SES account is still in the sandbox with one verified recipient. Production sending access and a client
  domain identity are not done.
- The browser e2e suite was not run against the box; component tests and live API evidence cover the views.
- Artifact retention is a fixed 365-day lifecycle, not a per-client policy.

## Next

C6 Section 3: the full MCP gateway, then the provider layer (Section 4), then close-out and handover.
