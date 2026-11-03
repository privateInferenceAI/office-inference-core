# Client-owned n8n automation

n8n is the client's own workflow automation and integration layer. The governed action layer stays in the
Office Inference API: a workflow starts work through a signed per-workflow integration, and every job carries
the integration's task, role, document roots and named owner.

## Licence and commercial guardrails

- One isolated n8n instance per client, in the client's deployment.
- The official n8n image is pulled at install, pinned by digest. It is not white-labelled.
- The client owns the instance and its credentials.
- n8n is never resold, never white-labelled, and never payment-gated. Access to n8n is not a subscription
  switch.
- n8n is distributed under the Sustainable Use Licence; counsel confirmation of the arrangement is tracked as
  `BIZ-LICENSE-001`. The architecture and deployment work is billed as a one-time build; the ongoing service
  is a managed infrastructure and support retainer, not a software licence fee.

## What runs

| Item | Value |
|---|---|
| Image | `docker.n8n.io/n8nio/n8n:2.46.4`, pinned digest set by prepare in `N8N_IMAGE` |
| Database | `n8n` in the deployment's Postgres, separate from `oicore` and `litellm` |
| Listen | `127.0.0.1:5678` on the product box, loopback only until C7 ingress |
| Data | `${OI_ROOT}/data/n8n`, encryption key in `rendered/env` |
| Templates | `deploy/n8n/workflows/scheduled-governed-report.json`, `deploy/n8n/workflows/invoice-intake-trigger.json` |
| Integration registry | `rendered/n8n_integrations.json`, mode 600, rendered by the provisioning script |

## Workflows as shipped

**Scheduled governed report.** A schedule trigger fires every five minutes and starts a
`summarise_across_documents` job through the `scheduled_report` integration. The job runs under the
integration's role and roots, the API calls the model, and the result is persisted on the job and returned
to the workflow. Acceptance produced a cited answer for the mileage and receipt rules.

**Invoice intake trigger.** An invoice arrives on the webhook `POST /webhook/invoice-intake` and starts an
`invoice_intake` job through the `invoice_trigger` integration. The job stops at the approval gate with the
drafted posting. A person approves it in the product Review screen. The workflow does not approve anything.

## Integration authentication

Each registry entry has its own secret. The API accepts either:

- HMAC-SHA256 over `<timestamp>.<raw body>` in `X-OI-Timestamp` and `X-OI-Signature`, five-minute window, or
- `Authorization: Bearer <integration secret>`.

The templates use the bearer form because n8n's task runner disallows the Node crypto module. The bearer is
the same secret as the HMAC key, the endpoint is on the internal `ai-net` only, and the HMAC path stays for
callers that can sign. Task, role, roots and owner always come from the rendered registry; the caller only
sends `input`, so a workflow cannot widen its own access.

Provisioning refuses an entry whose declared roots are not a subset of the owner's roots. The check is
`n8n_integrations.owner_problems`, used by the provisioning script and the tests.

## Setup, in order

```bash
# On the product box, from /opt/oi-product:
bash scripts/agentic-product-prepare.sh   # resolves and pins the n8n image, generates the n8n secrets once
bash scripts/agentic-product-start.sh     # creates the n8n database and starts n8n after the API
bash scripts/agentic-n8n-provision.sh     # renders the registry, owner account, imports and activates templates
bash scripts/agentic-n8n-acceptance.sh    # runs the checks and writes evidence under eval-out/
```

`N8N_OWNER_EMAIL` defaults to `anthony@officeinference.com`. The owner password is generated on first run and
written to `rendered/n8n-owner.env` (mode 600); it is never committed. Changing role or roots in the registry
means re-running provisioning so the API reloads the registry.

## Operating

- UI: `ssh -L 5678:127.0.0.1:5678 -i <key> ubuntu@<product-eip>`, then `http://127.0.0.1:5678`. Log in with
  the owner credentials in `rendered/n8n-owner.env`.
- Health: `docker inspect -f '{{.State.Health.Status}}' n8n`.
- Backup: `bash scripts/agentic-n8n-backup.sh [dest]` dumps `oicore`, `litellm`, `n8n`, `rendered/` and
  `data/n8n`. Restoring the database without `data/n8n` leaves stored credentials unreadable, so keep both.
- Upgrade: change `N8N_VERSION` in prepare, re-run it, then rebuild/recreate n8n and re-import and republish
  the templates. Record the new digest in the deployment record.

## n8n 2.x behaviour this deployment depends on

- CLI imports create draft versions. A workflow must be published (`n8n publish:workflow --id`) and activated
  through the REST API before its triggers register, and n8n must be restarted after activation.
- Webhook nodes need a `webhookId`; without it the workflow activates but the webhook is never registered.
- The templates carry fixed workflow ids so a re-import updates the workflow instead of creating a duplicate.
- Full user management is on. Provisioning creates the owner through `/rest/owner/setup` and logs in through
  `/rest/login`.

## What n8n does not get

It does not get a LiteLLM virtual key. The gateway authenticates model calls with an assertion minted by the
API, so there is no long-lived model credential to hand a workflow. Model work happens inside governed jobs.
An earlier draft passed a virtual key to n8n; that check was removed and the key revoked.

## Verification and first use

- `docs/runbooks/product-verification.md` is the process for checking the whole deployment: verification
  walkthrough, full acceptance run twice, restore drill, customer access path, company onboarding. The n8n
  acceptance below is one step inside it, not the whole check.
- `docs/runbooks/product-walkthrough.md` is the first-use guide for the person using the system: Ask, Do,
  Review, Activity, workspace artifacts and the automation layer, with operator-only steps labelled.
- `oi-canonical-stack/scripts/verify/` holds the per-step probes.

## Evidence

`docs/reviews/n8n-integration-20261103/` holds the acceptance record and raw outputs: the invoice webhook job
at the gate, the scheduled report job with its cited answer, negative signature and stale-timestamp 401s, the
provisioning refusal for an executive workflow owned by a staff member, the n8n executions, and the audit
chain verification.
