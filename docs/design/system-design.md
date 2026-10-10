# System design

The map. What we are building, how the pieces talk, and where the boundaries are.

## The shape

One system, two builds, configurations sold on top. Internally there are five layers, and each one
can be replaced without touching the others.

    ┌─────────────────────────────────────────────────────────────────────┐
    │  1. Experience        client app (custom)   lab console (LibreChat) │
    ├─────────────────────────────────────────────────────────────────────┤
    │  2. Product API       assistant API: ask, tasks, approvals,         │
    │                       activity, settings. The product boundary.     │
    ├─────────────────────────────────────────────────────────────────────┤
    │  3. Capability        retrieval · agent runtime · workflows ·       │
    │                       connectors (MCP) · voice                      │
    ├─────────────────────────────────────────────────────────────────────┤
    │  4. Control           LiteLLM gateway · model router · guardrails · │
    │                       audit sink · identity                         │
    ├─────────────────────────────────────────────────────────────────────┤
    │  5. Substrate         Docker on the office box, or the client's     │
    │                       cloud account. Postgres, object store,        │
    │                       secrets, ingress, backups.                    │
    └─────────────────────────────────────────────────────────────────────┘

Layers 2 through 4 are identical in both builds. Layer 5 is the profile. Layer 1 is the same
application rendered from the client manifest.

## Why a product API exists

Without it, the interface talks directly to LibreChat, n8n, and the agent runtime, and every
future change becomes a three-way negotiation. With it, the client app talks to one service, and
everything behind that service can be swapped through a seam.

The assistant API is small on purpose:

| Endpoint | What it does | Notes |
|---|---|---|
| `POST /ask` | A question over documents | Returns the answer plus citations and the lane used |
| `POST /tasks` | Start a defined task (a workflow or an agent run) | Returns a task id |
| `GET /tasks/{id}` | Task state, steps so far, artifacts | This is what makes a long run watchable |
| `POST /tasks/{id}/approve` | Approve or deny a pending step | Records who, when, and what they saw |
| `GET /approvals` | The queue | The screen that makes autonomy tolerable |
| `GET /activity` | The audit record, filtered | Reads the audit sink, never a log file |
| `GET /settings` | What is enabled, which packs, which lanes | Reads the manifest |
| `GET /health` | Service and model health | Used by the installer and the support tunnel |

## Request paths

**A question (local lane).**

    app → assistant API → guardrails (input) → retrieval (embed, search, rerank)
        → gateway → local model → guardrails (output) → answer + citations
        → audit sink

**A question the client allowed to leave (frontier lane).**

    app → assistant API → data-class check → redaction → gateway → frontier model
        → answer → audit sink (model, payload hash, approver)

If the data class is not permitted, the call **fails closed**. That is a test in the parity suite,
not an intention.

**A task.**

    app → assistant API → runtime (workflow or agent)
        → tool calls through the MCP gateway → action
        → approval gate if the step is irreversible → the person decides
        → result + artifacts → audit sink

## Components and their jobs

| Component | Layer | Job |
|---|---|---|
| **Client app** | 1 | Ask, Do, Review, Activity, Settings. The product's face |
| **Lab console** | 1 | LibreChat, for us. Debugging, prompt work, support. Not shipped to clients |
| **Assistant API** | 2 | The single surface the client app uses. Owns lane decisions and citations |
| **Retrieval** | 3 | Docling parsing, chunking with ACLs, Qdrant, TEI embed and rerank |
| **Agent runtime** | 3 | The loop: plan, call tools, checkpoint, pause for approval |
| **Workflows** | 3 | Deterministic multi-step work with triggers and approvals |
| **MCP gateway** | 3 | One place where tools are registered, scoped, and logged |
| **Voice** | 3 | LiveKit cascade, office build, later |
| **Gateway** | 4 | LiteLLM. Models, budgets, keys, and the enforcement point |
| **Model router** | 4 | Lane selection by data class, with the gate and the audit hook |
| **Guardrails** | 4 | Input denial, output redaction, topic policy. At the gateway, never in the UI |
| **Audit sink** | 4 | Append-only record of every model call, action, and approval |
| **Identity** | 4 | Local accounts first, OIDC later. Three roles |
| **Substrate** | 5 | Postgres, object store, secrets, ingress, backups, updates |

## Security boundaries

1. **The box.** Only ingress is reachable. Nothing else publishes a port.
2. **The data class.** Every service declares the highest class it handles, and the router
   consults it. Regulated never reaches a lane that is not permitted to carry it.
3. **The tool.** Every connector has its own credentials and its own scope list per client.
4. **The approval.** Irreversible actions stop for a person, and the approval is recorded.
5. **The account.** In cloud and hybrid, we hold a revocable role in the client's account, never
   standing credentials.
6. **The audit.** Append-only, with a signed daily export the client can keep.

## Deployment shapes

| | Office | Cloud | Hybrid |
|---|---|---|---|
| Where documents live | The box | The client's cloud account | The box |
| Where inference runs | Local GPUs | Frontier APIs, in their account | Local, plus frontier when allowed |
| Ingress | Caddy on the LAN, plus a consented tunnel | ALB with TLS | Both |
| Backup | Local disk, restic. Optional S3 | Snapshots, S3 with Object Lock | Local, plus their bucket |
| Updates | Signed pull with rollback | Image rollout | Signed pull, plus the cloud leg's Terraform |
| Who pays the cloud bill | Nobody, or them for the optional bucket | Them, directly | Them, directly |

## What we are not building

- Not a multi-tenant platform. One deployment per client. That is what makes compliance and the
  "you can fire us" promise simple.
- Not a browser-first agent. Browser work is assistive, and the recipe runner handles the routine.
- Not our own model. Models are configuration.
- Not our own chat engine. We use LibreChat for the lab console and build only the client app.
