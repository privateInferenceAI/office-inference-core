# Client-tenant DNS and Elastic IP cutover

**Date:** 2026-10-23. **Status:** completed on mcp.officeinference.com.

## Allocated and associated address

- Allocation: eipalloc-0da2730bda34519ff
- Public IP: 34.225.62.245
- Association: eipassoc-06186707a373a4e5b
- Tenant instance: i-0b6aa00095ae40e54
- Account: badj.ai, 426063972668

The Elastic IP is associated. The previous address 3.85.130.96 was released.

## Name

The public name is mcp.officeinference.com, added at Namecheap as an A record to 34.225.62.245. Caddy obtained a
Let's Encrypt certificate for it, HTTP redirects to HTTPS, and the API uses OI_MCP_URL=https://mcp.officeinference.com.

mcp.badj.ai was the original proposal but its public DNS at Porkbun did not resolve to the Elastic IP. It is not
used.

## Ownership tradeoff

The hostname is on Office Inference's domain, not the client's. The MCP-OWNED evidence in
docs/reviews/mcp-owned/ was gathered when the endpoint was the client-domain sslip name. The current name is an
Office Inference alias, and scripts/check_client_endpoint.py refuses it for that reason. The tenant account is
still badj.ai, but the domain check does not pass.

For a true client-owned endpoint, add a client-domain A record to 34.225.62.245 and change the Caddyfile and
OI_MCP_URL. For this development deployment, the Office alias is accepted.

## Rollback

Disassociate the Elastic IP, restore the previous Caddyfile and OI_MCP_URL, restart Caddy and API. The tenant has
no old address to return to once the EIP is associated, so rollback must use another address or the EIP again.

## Evidence

- HTTPS health: ok
- HTTP: 308 to HTTPS
- Let's Encrypt certificate obtained for mcp.officeinference.com
- Gateway SES send after cutover: message id 010001a168d26007-bb5a825f-ca4d-4db6-a715-e71d9ec82e74-000000,
  sent true, transport ses
