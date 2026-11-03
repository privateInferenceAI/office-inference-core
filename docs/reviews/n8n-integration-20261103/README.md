# n8n integration acceptance, 2026-11-03

The client-owned n8n automation layer running on the product deployment, with both templates live, the
per-workflow integration identities validated against their owner's roots, and the governed jobs visible in
the audit chain.

## Environment

| Item | Value |
|---|---|
| Product box | `i-0a3fca3c1141cbcad`, EIP 100.60.208.224 |
| Deployment id | `oi-core-product-20261103` |
| n8n | 2.46.4, `docker.n8n.io/n8nio/n8n@sha256:4384b9ff20a78bc1708c35ce7864f84f724611de7801fb36d715998db7c000f1` |
| n8n database | `n8n` in the deployment Postgres, separate from `oicore` and `litellm` |
| Listen | `127.0.0.1:5678` only |
| Analyst model | `bedrock/converse/us.moonshotai.kimi-k3` |
| Integration owners | `zoe.reyes@northwind.example` (staff, company root) |
| Code | `feat/n8n-integration`, PR #340; deployment scripts in `oi-canonical-stack` |

## Checks

| Check | Result | Evidence |
|---|---|---|
| n8n runs the pinned image, healthy, own database | pass | `evidence/n8n-version.txt`, `evidence/n8n-digest.txt`, `evidence/containers.txt` |
| Registry renders both workflows; owner roots validate | pass | `evidence/registry-redacted.json` |
| Invoice webhook starts a governed job that stops at the approval gate | pass, job `ff2feac1…` | `evidence/jobs.txt`, `evidence/audit-rows.txt` |
| Audit rows name the automation actor, integration and owner | pass, 14 rows for the two templates | `evidence/audit-rows.txt` |
| Scheduled workflow fires, job succeeds with a cited answer | pass, execution 6, job `7c824c93…` | `evidence/scheduled-job.txt` |
| n8n execution succeeds for the scheduled run | pass | `evidence/scheduled-job.txt` |
| Negative authentication: wrong signature and stale timestamp | pass, both 401 and no job | `evidence/negative-auth.txt` |
| Provisioning refuses a workflow with roots its owner lacks | pass, executive root refused for staff owner | `evidence/provisioning-refusal.txt` |
| Audit chain verifies | pass, 283 events, chain intact | `evidence/chain-verify.txt` |

Chain at verification: last row hash `72a3949348199147502923e4c17e63eed8b0da4172ff1dee9db9a2a54dbee274`,
283 events. No external anchor was supplied to the export, so the verification shows internal consistency, not
completeness; the anchor job remains the C8 item.

## Deviations from the scope, and why

1. **Authentication includes a bearer alternative.** n8n's task runner disallows the Node crypto module, so a
   workflow cannot compute the HMAC inside n8n. The API accepts the per-workflow secret as
   `Authorization: Bearer …`; HMAC stays the first choice when the headers are present. The bearer is the same
   secret, the endpoint is internal-only, and the task, role, roots and owner still come from the registry.
2. **No direct n8n-to-LiteLLM call.** The gateway authenticates model calls with an API-signed assertion, so
   there is no long-lived model key to give a workflow. The scope check "n8n HTTP node reaches LiteLLM and a
   spend row appears" is replaced by the governed-job path: the workflow starts a job, the API calls the
   model, and the spend and the tool call are recorded against the automation principal. The placeholder
   virtual key created during the build was revoked.
3. **n8n 2.x deployment behaviour.** Imports create drafts; publish, activate and restart are all required,
   and webhook nodes need a `webhookId`. The scripts and the runbook encode this.
4. **Job results are now persisted.** Polling exposed that a completed job lost its result. Migration 028 and
   the job store persist it, which is what lets the scheduled workflow receive its answer.

## Scripted acceptance

The live build was first exercised by hand. The checks were then encoded in
`scripts/agentic-n8n-acceptance.sh` and run on the same box: 7 passed, 0 failed, evidence under
`evidence/scripted-run/` (`summary.txt` plus one log per check). The script exits non-zero if any check fails.

The deployment backup `scripts/agentic-n8n-backup.sh` was also run: it dumped `oicore`, `litellm` and `n8n`,
archived `rendered/` and `data/n8n`, and wrote checksums (4.1 MB output directory on the box). Restore was not
drilled.

## What was not run in this pass

- A fresh end-to-end run from a clean box using only the updated scripts. The acceptance and backup scripts
  were exercised on the live box; the prepare/start/provision sequence has not been replayed from scratch.
- UI login through the tunnel was not part of the automated acceptance. The owner account was created and
  logged in through the n8n REST API, which is the same user management path the UI uses.
- The chain export has no external anchor; completeness is a C8 item.

## Raw outputs

`evidence/` holds the raw command outputs from the product box on 2026-11-03. Secrets are redacted in
`registry-redacted.json`; everything else is as produced.
