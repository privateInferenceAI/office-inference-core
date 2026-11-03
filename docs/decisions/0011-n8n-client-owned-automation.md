# 0011. n8n returns as the client-owned automation layer

- Status: **accepted by the owner, 2026-11-03**
- Phase: product packaging and C5 workflow scope
- Supersedes: the `workflow` row of `0001-service-implementations.md`, and the build-plan line that
  recorded n8n as "rejected"

## Context

`0001-service-implementations.md` chose n8n for the workflow row because it was the fastest path to
approvals, schedules and triggers. Two later findings moved the product away from it:

1. The Sustainable Use Licence restricts distributing or providing the software to others for payment, so
   it looked incompatible with a paid client build.
2. The governed-workflow requirement: a workflow's state change and its audit chain row have to commit in
   one transaction. n8n's execution log and our chain are two separate records, so n8n alone cannot carry
   the audit promise.

C5 therefore shipped its own workflow engine, `services/workflow`, and the product deployment built on
2026-11-03 does not include n8n. That solved the transaction and licence questions, and it left a real
capability gap: no connector breadth, no schedules or webhooks, and no visual builder.

The owner's product decision on 2026-11-03 is that n8n is included in the stack as a client-owned
automation layer, not licensed or resold by Office Inference.

## Decision

Two workflow layers, with different jobs.

**Our engine remains the governed action layer.** Any step that needs an approval, a chain row, a data
class decision or a model budget runs through our API, workflow engine or agent runtime. The transaction
property from the C5 build is not weakened.

**n8n is the client-owned automation and integration layer.** It is deployed into the client's isolated
environment and used by the client for their own internal business operations, under the free Sustainable
Use Licence and the conditions below.

- The client owns and runs an isolated instance on their own hardware or private cloud. One client per
  instance. No shared multi-tenant n8n.
- The install pulls the official `n8nio/n8n` image from the source during deployment, at a pinned digest.
- The client's own credentials live in their n8n instance. n8n is not white-labelled, wrapped in a
  proprietary UI, or presented as our engine. It keeps its own branding and its own address.
- We do not sell a software licence to n8n, and we never gate access to the client's n8n instance on any
  payment. Fees cover architecture, deployment, workflow templates, and optionally a managed
  infrastructure and support retainer.
- If the client stops paying, they keep a working n8n instance. What stops is our management and support.

## Integration points

1. **Model access through our gateway.** n8n uses a LiteLLM virtual key scoped to the deployment, so model
   spend and routes are governed and visible. No provider key is pasted into n8n.
2. **Governed jobs through a signed webhook.** A scheduled or triggered n8n flow can create a job on our
   API. That job runs in our engine with approvals and chain rows, and n8n consumes the result.
3. **Client-side actions stay in n8n.** Connector actions that operate on the client's own tools are
   logged by n8n, not by our chain. The product documentation says this plainly.
4. **Optional record bridge.** An n8n flow may post execution summaries to an API endpoint that writes
   `tool_call` rows with a data class we assign. This is a mirror, not a transaction guarantee, and it is
   presented as an add-on.

## Commercial guardrails the decision depends on

- Do not sell, resell, sublicense or host n8n for multiple clients from one instance.
- Do not disable n8n access for non-payment.
- Do not hide n8n's branding or present it as our software.
- Pull the image from the official source as part of the client's own deployment.
- Describe the one-time fee as system architecture, deployment and workflow templates, and the recurring
  fee as managed infrastructure and support.

These are conditions of the decision, not recommendations. If any of them cannot hold for a particular
deal, the decision does not apply to that deal and the option becomes n8n Enterprise or a different engine.

## Consequences

- The product regains connector breadth, scheduling, webhooks and a visual builder without reopening the
  audit transaction gap, because governed work stays in our engine.
- The audit claim gets narrower and more precise: the chain covers work executed by our API and engine.
  n8n-native actions are the client's own record unless the optional bridge is enabled.
- The deployment gains a second application runtime, its database, its encryption key, its update cadence
  and its own authentication requirement. C7 identity work applies to both.
- The licence position is a commercial reading, not a legal grant. `BIZ-LICENSE-001` stays open until
  counsel confirms the contract language for a paid deployment where the client owns the instance.

## Alternatives considered and rejected

- **Our engine only.** Keeps one record and no licence question, but leaves the product without the
  integration breadth an SMB compares us against. Rejected as the product's only automation path.
- **Embed n8n behind a proprietary UI.** Risks the licence prohibition on providing the software as part
  of a paid service and on concealing branding. Rejected outright.
- **Hosted multi-tenant n8n.** Clearly outside the licence. Rejected.
- **Buy n8n Enterprise and resell it.** A clean licence path if a client demands a fully managed
  subscription, but a different commercial model. Kept as an option for that case.

## Open items

- Counsel's written confirmation of the deployment and billing language (`BIZ-LICENSE-001`).
- Add n8n back to the product deployment: compose service, its own database on the shared Postgres, an
  encryption key, a pinned digest, ingress and auth, backup and update steps.
- Workflow template library as our IP: invoice intake, scheduled report, contract review and email, lead
  follow-up.
- An integration acceptance test: schedule in n8n, governed job in our API, result returned, record shown.
- Update the catalogue, and this repository's other references that still describe n8n as rejected.
