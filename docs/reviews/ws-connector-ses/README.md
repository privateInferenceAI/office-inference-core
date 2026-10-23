# Real SES connector, live

**Date:** 2026-10-23. **Status:** WS-CONNECTOR-001 is verified.

## What changed

The connector now has an SES transport. It sends through the SES API using the box's instance role, so no SMTP
username or password is stored in the connector, the API or the sandbox. The SMTP transport remains for a client
mail server.

- IAM role `oi-core-dev-instance` has an inline `oi-core-dev-ses-send` policy for `ses:SendEmail` and
  `ses:SendRawEmail` on the verified identity `anthony@officeinference.com`.
- The temporary IAM user `oi-core-ses-smtp` and its two access keys were deleted after the SES transport worked.
- `tests/test_email_connector.py` covers the SES transport with a fake client; 5 connector tests pass.

## Live sends

Direct connector send:

```
sent: true
to: anthony@officeinference.com
message_id: 010001a166d7a380-465896f6-94e2-46b7-8ef9-b04297e2cc0b-000000
transport: ses
```

Runtime job `0bc496610eb543898359360591e62491`:

- State: `succeeded`.
- Steps: `turn-1`, `approval-1`, `act-1`, `turn-2`, `finish`.
- The gate held the `send_email` call and its digest.
- Audit row 854 is the approval; row 857 is the send to `anthony@officeinference.com` with `sent: true`.
- The chain holds 527 events and verifies intact.

The email was delivered to the verified mailbox.

## Files

- `direct-send.json`: the direct SES send.
- `job.json`, `result.json`: the approved runtime job.
- `audit-rows.txt`: the approval and send rows.
- `audit-events.jsonl`, `verify.txt`, `export.txt`.
- `connector.log`: empty.
- `SHA256SUMS`.
