# Phase E readiness, 2026-11-03

What the deployment looks like on the eve of company onboarding, checked against the Phase E checklist. The
machine-side items pass. The items that need the owner are listed at the end, and nothing here is presented as
finished.

## Checked

| Item | State |
|---|---|
| Customer URL and certificate | Live at `https://app.officeinference.com`, Let's Encrypt certificate valid to 2027-02-01 |
| Owner login | Browser walkthrough passed; owner account is the only Cognito account |
| Cognito accounts | One: `anthony@officeinference.com`, confirmed |
| Rendered access registry | Five identities: owner, two demo identities (`adam.hale@`, `zoe.reyes@`) and two probes (`probe-exec@`, `probe-staff@`). The demo and probe identities have no Cognito login, so they cannot sign in, but they should be removed before the business uses the deployment |
| Email sending | SES sending enabled; `anthony@officeinference.com` verified; connector recipient allowlist configured. A non-allowlisted recipient is refused, and since the gateway fix (PR #367) the refusal is recorded in the chain; an allowed governed send is recorded as ok. Record: `docs/reviews/email-path-verification-20261103/` |
| SES sandbox | Account is in the sandbox: 200 messages per 24 hours, 1 per second, 9 sent in the last 24 hours. A new recipient must be verified first, or SES production access requested |
| Backups | `oi-product-backup.timer` active; next run 2026-11-04 08:00 UTC; latest local and S3 backup 2026-11-03T22:55Z, 9 objects, 26,227,904 bytes, 11 table counts in the manifest |
| Restore drills | Local drill 12 checks in 27 seconds; off-box S3 drill 11 checks in 21 seconds |
| Corpus | Synthetic only: 80 documents, 10,576 chunks, one Qdrant collection. Company corpus not loaded |
| Ingestion rehearsal | Three sample documents in, two ingested with 63 chunks, one refused for an oversized chunk. The canonical bundle path is the recommended serving route |
| Health | Five smoke probes pass; API, app and n8n healthy; host connector and egress services active |
| Audit chain | 1,308 events, chain verification included in the passing audit probe |
| Disk | 172 GB free on the 193 GB root volume |
| Support documents | `docs/runbooks/incident-response.md` exists; the deployment contact section is blank. `docs/runbooks/pilot-support.md` is the template to fill |

## Needed from the owner before documents go in

1. Staff accounts: names, emails, and group (`staff` for company documents, `exec` for the executive root too).
2. Company documents: where they are and how to get them to the box.
3. Email: whether to request SES production access or stay in the sandbox and verify each recipient; who may
   approve an email send.
4. Support: contact names, hours, and response expectations for the pilot.
5. The pilot decision: keep this box with the synthetic corpus for checks and add the company corpus, or give the
   business its own deployment.
6. Confirmation that the demo and probe identities may be removed from the rendered registry.

## Evidence

- Phase D acceptance and browser walkthrough: `docs/reviews/customer-edge-live-20261103/`.
- Customer UI work flow: `docs/reviews/customer-ui-flow-20261103/`.
- Backups and restore: `docs/reviews/product-backup-20261103/`, `docs/reviews/restore-drill-20261103/`.
- Ingestion rehearsal: `docs/reviews/company-ingestion-rehearsal-20261103/`.
- Runbooks: `company-onboarding.md`, `company-corpus-onboarding.md`, `pilot-users.md`, `pilot-support.md`.
