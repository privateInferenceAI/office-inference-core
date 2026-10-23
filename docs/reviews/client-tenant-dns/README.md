# Client-tenant endpoint cutover

**Date:** 2026-10-23. **Status:** complete. Public API name: mcp.badj.ai.

## Address

- Elastic IP allocation: eipalloc-0da2730bda34519ff
- Address: 34.225.62.245
- Association: eipassoc-06186707a373a4e5b
- Instance: i-0b6aa00095ae40e54, badj.ai account 426063972668
- DNS: mcp.badj.ai A record at Porkbun

## Evidence

- dig +short mcp.badj.ai A returns 34.225.62.245.
- scripts/check_client_endpoint.py https://mcp.badj.ai returns client-owned.
- Let's Encrypt certificate obtained for mcp.badj.ai.
- https://mcp.badj.ai/health returned status ok.
- http://mcp.badj.ai/health returned 308 to HTTPS.
- OI_MCP_URL is https://mcp.badj.ai.
- Gateway send through mcp.badj.ai returned HTTP 200:

    {"ok":true,"result":{"message_id":"010001a168e1b255-02048d9b-ae8a-4ee6-a767-e3d752175a35-000000",
     "sent":true,"subject":"badj client endpoint","to":"awstest@officeinference.com","transport":"ses"},
     "tool":"send_email"}

## Compatibility alias

mcp.officeinference.com still resolves and is still served by Caddy. It is an Office Inference domain, so the
ownership check refuses it. It is not the API URL and is not used as ownership evidence.

## Ownership

badj.ai is a client-tenant domain and is not in the vendor suffix list. The tenant account remains badj.ai account
426. The officeinference.com alias is vendor-owned.

## Rollback

Disassociate the Elastic IP, change Caddy and OI_MCP_URL back, restart Caddy and the API. The old 3.85.130.96
address is released; the officeinference.com alias is available as a temporary fallback.
