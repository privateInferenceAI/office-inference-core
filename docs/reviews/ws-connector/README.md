# Email connector and the combined audit run

**Date:** 2026-10-23. **Status:** WS-AUDIT-001 is verified. WS-CONNECTOR-001 stays open because the send went
to a local mail sink rather than a client's mail server.

## What was built

- `services/connectors/email_connector.py`: a token-guarded SMTP connector with a recipient allowlist, a
  one-line header check and one verb, send.
- `services/api/connector_client.py`: the API's client. The sandbox never sees the connector token or the SMTP
  credential.
- `send_email` is offered only to a connector definition and is declared irreversible. The existing approval
  gate holds the call before it runs; the act step asks the deployment's connector callback to send it.
- Tests: 4 connector tests, plus the existing tool and runtime tests.

## Single send

Job `124da301356a43769f13a6c74639da2d` reached `approval-1` with the pending `send_email` call and digest
`sha256:a2cdf1e0...`. The approval resumed it, `act-1` sent, and the job succeeded.

The mail sink received one message from `agent@officeinference.com` to `probe@example.com` with subject
`Workspace connector test`. Audit row 795 is the approval; row 798 is the send row, addressed to `act-1`.

## Combined audit run

Job `68f5a0fa70134821b30c40292afe676c` did all four actions in one workspace task.

| Action | Audit row |
|---|---|
| fetch `https://example.com` | 807 |
| write `page.txt` | 811 |
| run `wc -c page.txt` | 815 |
| approval | 821 |
| send the email | 824 |

The job succeeded in eight steps, the artifact `page.txt` is 14 bytes, the mail sink holds two messages and the
495-event chain verifies intact.

## What remains for WS-CONNECTOR

The send went to Mailpit on the box. A client's SMTP server with a username and password, and a real mailbox
round trip, is the remaining proof. The connector already reads `SMTP_USERNAME`, `SMTP_PASSWORD` and
`SMTP_USE_TLS`; none was exercised.

## Files

- `job.json`, `artifacts.json`, `audit-rows.txt`, `mailpit.json`: the combined run.
- `single-send/`: the first gate-and-send run.
- `audit-events.jsonl`: the 495-event chain export.
- `verify.txt`, `export.txt`.
- `c6-connector-live-result.json`, `c6-audit-live-result.json`.
- `SHA256SUMS`.
