# Client-tenant DNS and Elastic IP cutover

**Date:** 2026-10-23. **Status:** completed. The public API name is mcp.badj.ai.

## Address

- Allocation: eipalloc-0da2730bda34519ff
- Public IP: 34.225.62.245
- Association: eipassoc-06186707a373a4e5b
- Tenant instance: i-0b6aa00095ae40e54
- Account: badj.ai, 426063972668
- Previous address 3.85.130.96 was released.

## Names and certificates

Primary API name:

- mcp.badj.ai, A record at Porkbun to 34.225.62.245
- Caddy certificate obtained from Let's Encrypt
- OI_MCP_URL=https://mcp.badj.ai
- scripts/check_client_endpoint.py reports client-owned
- HTTP returns 308 to HTTPS
- Gateway SES send returned message id 010001a168e1b255-02048d9b-ae8a-4ee6-a767-e3d752175a35-000000

Compatibility alias:

- mcp.officeinference.com still resolves to the Elastic IP and is still served by Caddy.
- It is on Office Inference's domain, so check_client_endpoint refuses it as a vendor domain.
- It is not the API URL and must not be used as ownership evidence.

## Cutover evidence

- Both names return health ok.
- Both certificates were obtained.
- The API was recreated with OI_MCP_URL=https://mcp.badj.ai.
- Gateway send through mcp.badj.ai returned sent true and transport ses.

## Ownership

badj.ai is not one of the vendor suffixes in scripts/check_client_endpoint.py, and the check reports the endpoint as
client-owned. The tenant account is badj.ai account 426. The officeinference.com alias remains vendor-owned.

## Rollback

Disassociate the Elastic IP, restore the previous Caddyfile and OI_MCP_URL, restart Caddy and the API. The previous
3.85.130.96 address is released. The officeinference.com alias remains available as a fallback name while the
tenant is in this account.
