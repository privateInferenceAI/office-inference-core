# Credential boundary: the MCP gateway

**Date:** 2026-10-23. **Status:** WS-CRED-001 is verified.

## What was built

`services/mcp_gateway/gateway.py` is the process that holds the connector credential.

- The API holds only `OI_MCP_URL` and `OI_MCP_TOKEN`.
- The gateway holds `CONNECTOR_URL` and `CONNECTOR_TOKEN`, resolves the declared tool, and adds the connector
  token at the boundary.
- A tool not in `rendered/mcp_tools.json` is refused before the connector sees it.
- The sandbox has neither token and no network.

This is the credential and registry slice of the gateway. Session policy, per-connector credential rotation,
data-class egress and the full Section 3 registry are not claimed by this row.

## Environment evidence

API environment keys:

```
OI_MCP_TOKEN
OI_MCP_URL
```

Gateway environment keys:

```
CONNECTOR_TOKEN
CONNECTOR_URL
MCP_GATEWAY_TOKEN
MCP_TOOLS_FILE
```

The sandbox probe found only image environment variables (`PATH`, `HOME`, Python build values), no connector,
model or cloud credential. It could not connect to the gateway on port 8093: `Network is unreachable`. The host
directory was hidden.

## Live calls

Direct gateway call: SES sent the message with message id
`010001a166e5a389-ffb2d406-4faa-4abc-a83a-1a58cc8c7b66-000000`.

Runtime job `401a8318c8f1457ab5d1e18c4f048f6a` stopped at `approval-1`, was approved, and `act-1` sent through
the gateway. The chain holds 705 events and verifies intact.

## Files

- `api-env-keys.txt`, `gateway-env-keys.txt`: names only, no values.
- `sandbox-probe.json`: the environment, host and gateway probes.
- `gateway-direct.json`: the direct gateway send.
- `job.json`, `result.json`, `audit-rows.txt`.
- `audit-events.jsonl`, `verify.txt`, `export.txt`.
- `gateway.log`: empty.
- `SHA256SUMS`.
