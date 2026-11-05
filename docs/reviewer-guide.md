# Where to look: a reviewer's guide

This is the shortest path for someone reviewing what was built: the Office Inference product deployment, its
customer access path, and the evidence behind the claims. Read the repository that matches the question you are
asking.

## The short version

The deployed product is one self-hosted stack: API, client app, LiteLLM gateway, Postgres, Qdrant, embeddings,
document parser, MCP gateway, agent runtime, workspace sandbox, SES connector, n8n and an audit hash chain. A
customer signs in through Cognito at `https://app.officeinference.com`, gains a provider session at the edge,
and uses Ask, Do, Review and Activity. Operator tools (SSH tunnel, minted sessions, n8n login) are admin-only and
labelled as such.

The synthetic corpus is in place. Company documents, staff accounts and the support agreement are the remaining
Phase E steps. The deployment boxes are currently stopped; the site comes back when they start.

## Repositories

| Repository | Visibility | What it is | Look first at |
|---|---|---|---|
| `privateInferenceAI/office-inference-core` | private | Product code, manifests, decisions, runbooks and the evidence records from this work | branch `release/canonical-v2-20261028`: `docs/runbooks/`, `docs/reviews/`, `services/`, `deploy/product/`. Default `main`: architecture and product definition |
| `privateInferenceAI/oic-ui` | private | The client app. It talks only to the API | `README.md`, `app/src/App.tsx`, `app/src/api.ts` |
| `privateInferenceAI/oi-canonical-stack` | private | Deployment scripts and verification for the product box, and the canonical stack history | `scripts/verify/README.md`, `scripts/verify/customer-path-acceptance.py`, `scripts/restore-drill.sh`, `scripts/product-backup.py`, `scripts/cognito-provision.py`, `deploy/agentic-product/` |
| `privateInferenceAI/oi-corpus-builder` | private | Builds the validated corpus bundle the serving stack imports | `README.md` and `docs/format/oi-doc-v0.md`. The active work is on a `step-6*` branch, not `main` |
| `privateInferenceAI/ai-stack` | public | The earlier office/local stack. Useful context, not the deployment described here | `README.md` and `docs/` only |

Access is by GitHub collaborator or organisation team. Nothing in these repositories contains a secret, a
customer document or a key. The business administration folder on the owner's machine is local and must not be
shared.

## Reading order for a technical review

1. `office-inference-core` main `README.md`, then release branch
   `docs/decisions/0012-customer-access-path.md` on main.
2. Release branch `docs/runbooks/product-verification.md`: the worked process, phases V, B, C, D, E. This is the
   document that answers "how did you check it".
3. Release branch `docs/runbooks/product-walkthrough.md`: what a customer actually does.
4. Evidence, in this order:
   - `docs/reviews/customer-edge-live-20261103/README.md`: real hostname, certificate, owner login, browser
     walkthrough, wrong-password and staff-boundary refusals.
   - `docs/reviews/customer-ui-flow-20261103/README.md`: Do, Review and Activity in the live app.
   - `docs/reviews/email-path-verification-20261103/README.md`: allowlist refusal, the audit-chain fix and the
     governed email send.
   - `docs/reviews/restore-drill-20261103/README.md`: local and off-box S3 restore drills.
   - `docs/reviews/product-backup-20261103/README.md`: daily timer, S3 copy, manifest counts.
   - `docs/reviews/phase-e-readiness-20261103/README.md`: exactly what is ready and what still needs the owner.
5. Code, in the order a request travels: `services/api/` (sessions, approvals, jobs, agent runtime), then
   `services/mcp_gateway/`, then `deploy/product/` (compose overlays, edge Caddyfile). The client side is the
   `oic-ui` app.
6. Deployment and verification automation in `oi-canonical-stack`: start with `scripts/verify/README.md` and the
   five `step-0*` probes, then `customer-path-acceptance.py`, `restore-drill.sh` and `product-backup.py`.

## Reading order for an operations or business review

Read `docs/runbooks/product-walkthrough.md`, then the customer-edge and phase-e readiness records above. The
runbooks named there cover company onboarding, document intake, pilot users, pilot support and SES production
access. Skip the code.

## What is proven, and what is not

Proven on the live deployment before it was stopped:

- Customer TLS and login on the real hostname; provider session; browser walkthrough; sign-out.
- The customer work flow: task, approval gate, approval, Activity record.
- Staff boundary denial for executive content; wrong-password refusal; operator ports closed.
- Five independent smoke probes, the stepwise acceptance process, local and off-box restore drills, daily backup
  timer and S3 copy.
- The audit chain records approvals, provider identity and connector refusals.

Not proven or not done:

- Company documents, staff accounts and the support agreement. Phase E waits on the owner.
- SES is in the sandbox: one verified identity, 200 messages per 24 hours.
- The rendered registry still contains demo and probe identities awaiting removal.
- A scoped IAM role for the product box, a client-owned backup target with Object Lock, and scheduled audit
  export anchoring are open items before this is sold as a client deployment.

## If you want a live walkthrough

The deployment boxes are stopped to save cost. Starting the product box and the workspace host, then following
`docs/runbooks/product-walkthrough.md`, is the intended demo path. The acceptance scripts in
`oi-canonical-stack/scripts/verify/` are what to run after the stack is up.
