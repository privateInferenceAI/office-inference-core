# MCP gateway

**Date:** 2026-10-23. **Status:** MCP-REGISTRY-001, MCP-CREDS-001, MCP-POLICY-001 and SEC-MCP-001 are
verified. MCP-EGRESS-001 and MCP-OWNED-001 remain open.

## What is built

`services/mcp_gateway/gateway.py` is the process between the API and a connector. It:

- reads the deployment's tool registry from `rendered/mcp_tools.json`, written from the manifest's `mcp.tools`
  block;
- resolves the caller's session from the sessions table, accepting a raw token or a job's credential hash;
- refuses a request that supplies its own role or authority;
- enforces the tool's roles, data classes and the egress gate before the connector is reached;
- reads the connector credential from a mode-600 file on each call, so rotation needs no redeploy;
- records every refusal through the audit tool and the API records every successful send.

The API holds only `OI_MCP_URL` and `OI_MCP_TOKEN`. The gateway's environment has no `CONNECTOR_TOKEN`; the
credential is the mounted file. The sandbox has neither.

## Evidence

Registry:

- `registry.json`: the rendered tool set; the manifest declares the same one tool.
- `probe.json`: `GET /tools` returns `["send_email"]`; an undeclared `wire_money` is refused.
- Refusal row 1073 records the undeclared tool by name.

Sessions:

- no session: 401, refusal row 1070.
- no live session: 401, row 1071.
- expired session: 401, row 1091.
- authority supplied in the body: 403, row 1072.

Policy:

- role without the tool: 403, row 1090. The gateway refuses before the connector; the unit tests assert the
  transport was never called.
- data class the tool does not accept: 403, row 1074.

Credentials:

- connector token file mode 600; a 644 file is refused.
- rotation test: old value returned connector 401; the new value sent successfully with SES message id
  `010001a166f0ccf9...`; no redeploy.
- gateway container env keys contain `CONNECTOR_URL` but no `CONNECTOR_TOKEN`.

Runtime:

- job `12479c11cca74fb9981d97b1e9f0c5ed` stopped at `approval-1`, was approved, and `act-1` sent through the
  gateway. Audit row 1083 records `sent: true` to the verified recipient.
- The chain holds 754 events and verifies intact.

## Tests

`tests/test_mcp_gateway.py`: 12 tests over registry, session, authority, role, data class, egress gate,
credential rotation, file mode and connector failure.

## Still open

- `MCP-EGRESS-001`: the frontier-lane data-class gate and its redaction summary are not wired into the existing
  egress lane.
- `MCP-OWNED-001`: the ownership/cord-check test against a gateway we operate is not built.

## Files

- `registry.json`, `probe.json`, `result.json`, `job.json`.
- `refusals-and-job-rows.txt`, `audit-events.jsonl`, `verify.txt`, `export.txt`.
- `c6-role-result.json`, `c6-expiry-result.json`, `c6-rotation-result.json`.
- `api-env-keys.txt`, `gateway-container-env-keys.txt`, `credential-file-mode.txt`.
- `SHA256SUMS`.
