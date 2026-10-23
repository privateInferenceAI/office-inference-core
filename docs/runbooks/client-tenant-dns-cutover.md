# Client-tenant DNS and Elastic IP cutover

**Date:** 2026-10-23. **Status:** completed. The only public API name is mcp.badj.ai.

## Address

- Allocation: eipalloc-0da2730bda34519ff
- Public IP: 34.225.62.245
- Association: eipassoc-06186707a373a4e5b
- Tenant instance: i-0b6aa00095ae40e54
- Account: badj.ai, 426063972668
- Previous address 3.85.130.96 was released.

## Name and certificate

- mcp.badj.ai, A record at Porkbun to 34.225.62.245
- Caddy certificate from Let's Encrypt
- OI_MCP_URL=https://mcp.badj.ai
- scripts/check_client_endpoint.py reports client-owned
- HTTP returns 308 to HTTPS

The earlier mcp.officeinference.com record was deleted from DNS and its Caddy site block was removed. It is not a
public name and must not be used in client-facing material.

## Evidence

- Both badj.ai DNS and the tenant Elastic IP resolve to 34.225.62.245.
- https://mcp.badj.ai/health returns ok.
- Gateway SES send returned message id 010001a168e1b255-02048d9b-ae8a-4ee6-a767-e3d752175a35-000000,
  sent true, transport ses.
- API answer after the switch returned with citations.

## Ownership

badj.ai is not one of the vendor suffixes in scripts/check_client_endpoint.py, and the check reports the endpoint as
client-owned. The tenant account is badj.ai account 426. If badj.ai is not controlled by the client, the account
separation is still real but the domain claim is not.

## Rollback

Disassociate the Elastic IP, restore the previous Caddyfile and OI_MCP_URL, restart Caddy and the API. The old
3.85.130.96 address is released. A rollback endpoint needs a separate DNS name if this one is retired.
