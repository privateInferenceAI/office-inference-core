# 0001. Service implementations

Date: 2026-10-09
Status: accepted, with named open risks. The `agent-runtime` row is superseded by 0009, which keeps the C5 engine and writes the loop as a dynamic plan.

## Context

The service inventory says what exists and where it runs. It deliberately does not say what each
service is built from. This record fills that gap so the choice is a decision rather than an
accident, and so a future swap knows what it is replacing and why.

## Decisions

| Service | Chosen | Licence | Why this, and what it replaces |
|---|---|---|---|
| `gateway` | **LiteLLM** | MIT | Already proven in the office build. One control point for models, budgets, spend, and audit. Must be patched: see the LiteLLM advisory |
| `interface` | **LibreChat** | MIT | Agents, MCP with per-object ACLs, SSO, and memories in something product-shaped. Replaces Open WebUI, whose MCP support is admin-gated and whose branding clause caps at 50 users |
| `database` | **Postgres 16** | PostgreSQL | Already in use. Holds keys, spend, audit, and application state |
| `vector` | **Qdrant** | Apache-2.0 | Already in use, one collection per client, simple to back up |
| `embeddings` | **TEI with bge-m3** | Apache-2.0 | Already in use, multilingual, runs on modest GPUs |
| `reranker` | **TEI with bge-reranker-v2-m3** | Apache-2.0 | Already in use, big quality gain for citation accuracy |
| `ingestion` | **Docling** for parsing, custom pipeline around it | MIT | Handles scanned documents and complex layouts better than the current extractors. Keeps our manifest, chunking, and deletion reconciliation |
| `workflow` | **Our workflow engine (C5)**, plus a client-owned n8n instance | n/a, Sustainable Use Licence for n8n | Governed steps need the state change and the chain row in one transaction, so the engine is ours. n8n returns as the client-owned automation layer for triggers, schedules and integrations. **Superseded by 0011, 2026-11-03** |
| `agent-runtime` | **OpenAI Agents SDK** | MIT | Ships a sandbox, MCP on multiple transports, sessions, guardrails, human-in-the-loop, and a LiteLLM provider, so it drives local models through our existing gateway. Replaces nothing because we have nothing yet |
| `mcp-gateway` | **IBM ContextForge** | Apache-2.0 | Central auth, per-key tool filtering, and audit, which are two of the worst gaps in the open-source stack. Alternative: Docker MCP Gateway |
| `audit-sink` | **Ours**, Postgres plus signed daily export | n/a | No off-the-shelf component produces one append-only record across models, tools, and workflows. This is also a differentiator, so it is worth owning |
| `memory` | **pgvector in Postgres** | PostgreSQL | Add mem0 or Graphiti only when a client complaint justifies it |
| `observability` | **Langfuse** self-hosted plus our audit sink | MIT core, enterprise add-ons | Traces for debugging, while the audit sink stays the compliance record. Verify self-hosting terms |
| `sandbox` | **Docker with gVisor** on the office box, **Lambda MicroVMs** in the cloud build | n/a | Isolation for agent code execution without a new platform |
| `voice` | **LiveKit agents**, cascade (ASR, model, TTS) | Apache-2.0 | Auditable at the gateway, and it is the architecture already locked for the friend's build |
| `secrets` | **SOPS with age** (office), **Secrets Manager** (cloud) | n/a | Matches the substrate with no shared control plane |
| `ingress` | **Caddy** (office), **ALB with ACM** (cloud) | Apache-2.0 | Caddy gets automatic TLS with almost no configuration |
| `object-store` | **Local disk plus restic** (office), **S3** (cloud) | n/a | Object Lock in the cloud covers WORM requirements |
| `updater` | **Signed pull-based releases** using cosign | n/a | The box checks a signed manifest and can roll back |

## Consequences

- The office build carries more services than a cloud build, because it does the work a cloud
  provider otherwise does: backups, updates, monitoring, and recovery.
- The n8n licence risk is handled by decision 0011: client-owned isolated instances, install-time pull of
  the official image, no resale and no white-labelling. Counsel confirmation is tracked as BIZ-LICENSE-001.
  Langfuse self-hosting terms still need verification before shipping at scale.
- Every choice above can be revisited through a seam, and the parity suite is what makes the
  revisit cheap.
