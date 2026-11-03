# Pilot support and incident path

The written support agreement for the pilot. Fill the contact and hours rows before the first business day; the
technical procedure below is already the one the deployment uses.

## Contacts to fill

| Role | Name | How to reach | Hours |
|---|---|---|---|
| Business owner (customer) |  |  |  |
| Technical contact (operator) | Anthony Stachowitz |  |  |
| After-hours escalation |  |  |  |
| Second responder |  |  |  |

Support hours for the pilot: `<fill in>`. Response expectation: `<fill in>`. These are commitments: write a
number the operator can keep, even if it is one business day.

## Severity

- **Critical:** the service is down for everyone, or content or audit evidence is exposed. Respond immediately,
  notify the customer within 72 hours of confirmed exposure, follow `docs/runbooks/incident-response.md`.
- **High:** a working area is broken for one person or approvals cannot be made. Workaround first, root cause
  next.
- **Normal:** a wrong answer, a missing document, a question about use. Handle in business hours.

## What to collect from a report

Time in ET and UTC, the signed-in email, the screen (Ask, Do, Review, Activity), the exact question or task,
what the person expected, what happened, and a screenshot if the browser shows something. Do not ask for the
password or the session token.

## First triage on the box

```bash
cd /opt/oi-product
bash scripts/verify/step-01-services.sh
bash scripts/verify/step-02-answer.sh
bash scripts/verify/step-03-audit.sh
docker ps --format '{{.Names}} {{.Status}}'
docker logs api --since 1h 2>&1 | tail -50
docker logs caddy --since 1h 2>&1 | tail -50
journalctl -u oi-product-backup -n 50 --no-pager
```

If the issue is login, check the edge and Cognito before changing anything inside the deployment. If it is
content access, check the person's group in `rendered/access.json` before changing a document.

## Evidence and preservation

- Audit export and verify: `oi run --rm audit export --date "$(date -u +%F)"` then
  `oi run --rm audit verify "/app/exports/$(date -u +%F)/audit-events.jsonl"`.
- Backups: `ls /home/ubuntu/backups/` and the S3 prefix under
  `s3://oi-core-backups-878451098165/backups/product-oi-core-product-20261103/`.
- Do not edit the audit chain and do not delete logs while investigating. Copy evidence before remediation.

## Routine

- After any restart: the five probes and two app questions.
- Weekly: Phase B twice, backup sanity check, and a look at the open findings.
- Monthly: backup prefixes and local retention, edge certificate renewal check, support agreement review.

## Boundaries

Support does not access a customer's content to answer a question unless the person asks for that specific
investigation. The operator path is admin-only. Customer passwords are never requested; they are reset.
