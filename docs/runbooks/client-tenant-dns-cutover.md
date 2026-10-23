# Client-tenant DNS and Elastic IP cutover

**Date:** 2026-10-23. **Status:** Elastic IP allocated, not associated. DNS record pending.

## Allocated address

- Allocation: eipalloc-0da2730bda34519ff
- Public IP: 34.225.62.245
- Account: badj.ai, 426063972668

The address is allocated but deliberately not associated yet. Associating it releases the tenant's current
public address, so the Caddy hostname, certificate and OI_MCP_URL must change in the same maintenance step.

## Stable name

The preferred name is mcp.badj.ai. The badj.ai DNS is hosted at Porkbun; the Route53 hosted zone in account 426
is not authoritative for public resolution. Add this record at Porkbun:

    mcp.badj.ai.  A  34.225.62.245

If a named record is not available, use the IP-derived name 34-225-62-245.sslip.io. It is stable as long as the
Elastic IP stays associated, but it is not a client-controlled name.

## Cutover steps, once the record exists

1. Confirm mcp.badj.ai resolves to 34.225.62.245.
2. Associate eipalloc-0da2730bda34519ff with tenant instance i-0b6aa00095ae40e54.
3. Change Caddyfile to mcp.badj.ai and restart Caddy. Wait for the certificate.
4. Update OI_MCP_URL in the build box rendered env to https://mcp.badj.ai.
5. Restart the API and run the SES-through-gateway test.
6. Update docs/current-deployment.md and the tenant table.

Rollback: disassociate the Elastic IP, restore the previous Caddyfile and OI_MCP_URL, restart Caddy and API. The
old DNS name returns after the old address is restored.

## What only the account owner can do

Add the Porkbun A record, or provide Porkbun API credentials. The allocation and association can be done with the
existing AWS access.
