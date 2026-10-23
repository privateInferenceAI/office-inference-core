# Client-tenant endpoint cutover

**Date:** 2026-10-23. **Status:** complete.

## Address and name

- Elastic IP allocation: eipalloc-0da2730bda34519ff
- Address: 34.225.62.245
- Association: eipassoc-06186707a373a4e5b
- Instance: i-0b6aa00095ae40e54, badj.ai account 426063972668
- Public name: mcp.officeinference.com
- DNS: A record at Namecheap to 34.225.62.245

## Cutover evidence

- Let's Encrypt certificate obtained for mcp.officeinference.com.
- https://mcp.officeinference.com/health returned status ok.
- http://mcp.officeinference.com/health returned 308 to HTTPS.
- Caddy reverse-proxies to 127.0.0.1:8080.
- The build box rendered env now has OI_MCP_URL=https://mcp.officeinference.com.
- A gateway send through the new name returned HTTP 200:

    {"ok":true,"result":{"message_id":"010001a168d26007-bb5a825f-ca4d-4db6-a715-e71d9ec82e74-000000",
     "sent":true,"subject":"new stable endpoint","to":"awstest@officeinference.com","transport":"ses"},
     "tool":"send_email"}

## Ownership

The endpoint is on Office Inference's domain. scripts/check_client_endpoint.py refuses it as a vendor domain, so
the MCP-OWNED evidence applies to the earlier client-domain endpoint and to the tenant account, not to this host
name. The tenant infrastructure is still in the badj account. A true client-owned endpoint needs a client-domain
A record to 34.225.62.245.

## Rollback

Disassociate the Elastic IP, change Caddy and OI_MCP_URL back to the previous endpoint, restart Caddy and the API.
The old 3.85.130.96 address was released when the Elastic IP was associated.
