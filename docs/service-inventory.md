# Service inventory

Every service in the core, with its location tag and the pack that turns it on. The tag is what
lets one build be assembled two ways.

Location tags: **office** (runs on the client's hardware), **cloud** (runs in the client's cloud
account), **either** (both builds run it, the implementation differs).

## Core services

These are on in every build. The *Core* pack is implicit and not sold separately.

| Service | Location | Purpose | Office implementation | Cloud implementation |
|---|---|---|---|---|
| `gateway` | either | Single model control point, budgets, spend, audit | LiteLLM container | LiteLLM container |
| `interface` | either | The chat and approval surface | Same image | Same image |
| `database` | either | Application state, audit records, spend | Postgres container, WAL archiving | RDS or Aurora |
| `object-store` | either | Documents and exports | Local disk or MinIO | S3 |
| `secrets` | either | Credential storage | SOPS with age, or systemd credentials | Secrets Manager |
| `ingress` | either | TLS termination and routing | Caddy | ALB with ACM |
| `audit-sink` | either | Append-only record of every model call and action | Postgres table plus signed daily export | Postgres plus S3 Object Lock |
| `observability` | either | Health, metrics, alerts | Loki and Uptime Kuma, or files plus email | CloudWatch |

## Document pipeline

| Service | Location | Purpose | Pack |
|---|---|---|---|
| `ingestion` | either | Chunk, embed, reconcile deletions | Document Chat |
| `parser` | either | Documents become structured text with page numbers: layout, tables, OCR | Document Chat |
| `vector` | either | Qdrant collection per client | Document Chat |
| `embeddings` | either | bge-m3 through TEI | Document Chat |
| `reranker` | either | Reranking reads: Bedrock on the cloud build, local on the office build. The CPU implementation is a bench instrument | Document Chat |

## Inference

| Service | Location | Purpose | Pack |
|---|---|---|---|
| `inference-local` | office | vLLM or llama.cpp serving open weights | Core on office builds |
| `inference-frontier` | cloud | Bedrock or a frontier API | Core on cloud builds |
| `model-router` | either | Lane selection by data class, with the gate and the audit hook | Frontier Lane |

## Capability services

| Service | Location | Purpose | Pack |
|---|---|---|---|
| `workflow` | either | Multi-step execution, approval gates. Our engine; the state change and chain row are one transaction | Workflow Automation |
| `agent-runtime` | either | The agent loop: dynamic plan, tools, sessions, checkpoints, human-in-the-loop | Agentic Desk |
| `mcp-gateway` | either | Central tool registry, per-client credential injection, tool-level policy | Agentic Desk |
| `connectors` | either | One adapter per integration, MCP servers where they exist | per integration |
| `workspace-sandbox` | cloud | Per-task sandbox: files, shell, code, quotas, teardown | Cloud Workspace (WS) |
| `egress-proxy` | cloud | Allowlisted web and API egress, with audit | Cloud Workspace (WS) |
| `artifact-store` | cloud | S3 artifacts in the client's account | Cloud Workspace (WS) |
| `voice` | office | LiveKit cascade: ASR, then model, then TTS | Voice |
| `redaction` | either | Strips identifiers on the way out when the frontier lane is used | Compliance Pack |

## Operational services

| Service | Location | Purpose | Pack |
|---|---|---|---|
| `backup` | either | Scheduled dumps and restic snapshots, restore drills | Core; Offsite Backup adds a remote target |
| `updater` | office | Signed releases, health check, rollback | Core on office builds |
| `remote-support` | office | Consented tunnel for support access | Core on office builds |
| `drift-reporter` | office | Reports manifest and image digests on a schedule | Core on office builds |

## Still to place

These are known needs with no decided implementation. Each becomes a service directory when
it is built.

- **WORM record storage** for clients under SEC 17a-4 style rules: S3 Object Lock in cloud, an
  append-only store on the office box.
- **Legal hold** on documents and conversations.
- **Data residency** controls if a client's contract names a state or country.
- **Local mail relay** for notifications where the office build must not depend on a cloud
  mail service.
