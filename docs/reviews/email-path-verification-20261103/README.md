# Email path verification, 2026-11-03

Phase E's email checks, and a gap found and fixed in the audit chain.

## Finding: a blocked message left no audit row

The connector owns the recipient allowlist and is the last boundary before a message leaves. Calling the gateway
with a non-allowlisted recipient was refused correctly by the connector, but the gateway turned that refusal into
an error without recording it. The chain could not show that a send was attempted and blocked at the last
boundary.

## Fix

`services/mcp_gateway/gateway.py` now records outcome `denied` with `refusal=connector` and the connector detail
before re-raising. A test asserts the row is present and carries the refused recipient. All 17 gateway tests
pass. Merged in product PR #367, release commit `234ac9a`. The live gateway image was rebuilt from that release;
the previous image is tagged `oi-core/mcp-gateway:pre-connector-refusal-audit` for rollback.

## Live verification

| Check | Evidence |
|---|---|
| Non-allowlisted recipient refused | connector returned 403, error names the allowlist |
| Refusal now in the chain | audit event 1321, access, actor `anthony@officeinference.com`, outcome `denied`, detail names the refused recipient |
| Allowed governed send | audit events 1368 and 1369, access and tool_call, `send_email`, outcome `ok`; part of the workspace acceptance run |
| Product acceptance after the fix | rc 0 in 57 seconds: research brief, workspace brief with approval and email, artifact upload and download, staff boundary denial, invoice approval |
| SES identity | `anthony@officeinference.com`, verified; sending enabled |
| SES limits | sandbox: 200 per 24 hours, one per second, one verified identity |

The SES quota counter still read 9 for the last 24 hours at the time of the check. AWS counters can lag, so this
record does not claim independent inbox delivery. It claims the connector accepted the send, the gateway recorded
it, and SES accepted it. The owner can confirm the message arrived: subject "Task Brief: workspace checks,
calculation, fetched title, mileage rate".

## What remains for email in Phase E

- Decide sandbox versus production access before staff send to addresses other than the verified identity.
- Confirm who may approve an email send; the record names the person.
- Verify each recipient address if the sandbox stays.
