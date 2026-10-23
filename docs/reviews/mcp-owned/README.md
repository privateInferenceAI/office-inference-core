# Client-tenant ownership proof

**Status:** evidence gathered, MCP-OWNED-001 stays open until the production gateway and connector move to the client tenant.

## What was tested

The client tenant is badj.ai account **426063972668**, instance `i-0b6aa00095ae40e54` (t3a.xlarge), public IP `52.91.69.1`.

On that instance:

- a Postgres sessions table with a viewer session row;
- the MCP gateway from `services/mcp_gateway/gateway.py`;
- a small echo connector on port 8094 that returns `client_account: 426063972668`.

The Office box at `52.204.204.133` called the client gateway at `http://52.91.69.1:8080/call`. The call completed and the connector response named account 426.

## Resolver block

`officeinference.com`, `api.officeinference.com` and `mcp.officeinference.com` were pointed at 127.0.0.1 in the Office box's `/etc/hosts`. The same call to the client tenant still completed. The hosts file was restored.

## Endpoint check

`scripts/check_client_endpoint.py` refuses a gateway or connector endpoint whose hostname is on a domain we operate.

- `http://52.91.69.1:8080`: client-owned.
- `https://mcp.officeinference.com`: refused.
- Tests: `tests/test_client_endpoint.py`, 3 passing.

## Why the row stays open

The production API, MCP gateway and connector for the Office deployment still run in account 878. This proof shows the client tenant can host the boundary and answer the Office box, but it is not yet the deployment's production path. MCP-OWNED-001 says every C6 component must be in the client's account or tenant, so closing it requires moving the production gateways, not only running a test fixture there.

## Files

- `mcp-owned-instance.json`: tenant instance.
- `mcp-owned-call.json`: cross-account call.
- `mcp-owned-blocked.json`: call with vendor hostnames blocked.
- `mcp-owned-check.json`: endpoint check pass and refusal.
