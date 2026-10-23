# Client-tenant endpoint cutover

**Date:** 2026-10-23. **Status:** complete. Public API name: mcp.badj.ai.

## Evidence

- Elastic IP eipalloc-0da2730bda34519ff, address 34.225.62.245, associated with i-0b6aa00095ae40e54.
- mcp.badj.ai A record at Porkbun resolves to 34.225.62.245.
- scripts/check_client_endpoint.py https://mcp.badj.ai returns client-owned.
- Let's Encrypt certificate obtained for mcp.badj.ai.
- https://mcp.badj.ai/health returned status ok.
- http://mcp.badj.ai/health returned 308 to HTTPS.
- OI_MCP_URL is https://mcp.badj.ai.
- Gateway send through mcp.badj.ai returned HTTP 200:

    {"ok":true,"result":{"message_id":"010001a168e1b255-02048d9b-ae8a-4ee6-a767-e3d752175a35-000000",
     "sent":true,"subject":"badj client endpoint","to":"awstest@officeinference.com","transport":"ses"},
     "tool":"send_email"}

- API /ask after the switch returned a cited answer.

## Removed alias

mcp.officeinference.com was deleted from DNS and removed from Caddy. It is not a public endpoint.

## Rollback

Disassociate the Elastic IP, restore the previous Caddyfile and OI_MCP_URL, restart Caddy and the API. The old
3.85.130.96 address is released; a rollback needs a separate DNS name if the endpoint is retired.
