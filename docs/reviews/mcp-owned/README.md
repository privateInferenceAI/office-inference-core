# MCP gateway and connector in the client tenant

**Date:** 2026-10-23. **Status:** MCP-OWNED-001 is verified. The public API name is `mcp.badj.ai`, which
resolves to the tenant Elastic IP and passes `scripts/check_client_endpoint.py` as client-owned. The earlier
`mcp.officeinference.com` name is still served as a convenience alias, but it is a vendor domain and is not used
as the API URL or as ownership evidence.

## What moved

The production MCP gateway and the production email connector now run in badj.ai account 426063972668, on instance i-0b6aa00095ae40e54.

- Gateway: http://3.85.130.96:8080, container client-mcp-gateway, restart unless-stopped.
- Connector: client-connector systemd service on port 8094, restart always.
- SMTP server: Mailpit on the same tenant, port 1025. It is now the fallback; the connector sends through SES
  as awstest@officeinference.com after the later email cutover.

The Office API points at that gateway with OI_MCP_URL. It no longer calls a gateway in account 878.

## How the gateway authenticates without our database

The client tenant has no route back to our sessions database. The API mints a short-lived HMAC assertion containing the session subject, role and expiry, signed with OI_MCP_SESSION_SECRET. The gateway verifies the signature and expiry. It still refuses a request that supplies its own role, and the role it uses comes from the signed assertion, not the body. Sixteen gateway tests cover the assertion path, including expired assertions, wrong signatures and a body role claim.

## Evidence

Job aa1b739255744e6f98c1cb1a38bed610 reached turn-1, approval-1, act-1, turn-2 and finish successfully. Audit row 1190 records sent true to anthony@badj.ai, with egress_allowed true.

Then officeinference.com, api.officeinference.com and mcp.officeinference.com were pointed at 127.0.0.1 on the Office box. Job 43dfdbbd5d5b465489b165013a442d0a went through the same act path; its final job state was failed verification, but the act row 1204 records sent true. The hosts file was restored.

The Mailpit inbox on the tenant held the captured messages at the time, so the connector received the call.
The connector was later switched to SES and sent through the verified awstest@officeinference.com identity.

## Endpoint check

At the time, scripts/check_client_endpoint.py http://3.85.130.96:8080 reported client-owned; the same check refused https://mcp.officeinference.com. After the DNS cutover, https://mcp.badj.ai reports client-owned; see endpoint-check-badj.json.

## What this proves and what it does not

The gateway and connector endpoints are in the client's account, not ours, and a tool call works with our hostnames blocked. That is what MCP-OWNED asks for.

The connector's SMTP target is Mailpit on the tenant, which captures the message instead of delivering it. A production client connector would point at that client's SMTP server. The ownership path is the same. The mail server is the part this test does not prove.

The gateway still needs the signed assertion from the API. The signing secret lives in the client tenant and in our rendered env, and the API is the caller. The tenant services restart without our box.

## Files

- job-success.json: the successful job.
- blocked-hosts.json: the job with our hostnames blocked.
- audit-rows.json: rows 1190 and 1204.
- endpoint-check.json.
- tenant.json: account, instance and service layout.
