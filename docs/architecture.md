# Architecture

## The one-sentence model

One core system, two builds, and every offering is a configuration of it.

## System versus substrate

**The system** is identical everywhere: container images, service definitions, application code,
database schema, the interface, the entitlement manifest. **The substrate** is where those
containers run, where objects and secrets live, how traffic arrives, how backups happen. The
substrate is what changes; the system does not.

| Capability | Cloud implementation | Office implementation |
|---|---|---|
| Object storage | S3 | Local disk, or MinIO |
| Secrets | Secrets Manager | SOPS with age keys, or systemd credentials |
| Database | RDS or Aurora | Postgres container with WAL archiving |
| Inference | Bedrock or frontier APIs | vLLM or llama.cpp on the GPU box |
| Ingress and TLS | ALB with ACM | Caddy, internal CA or Let's Encrypt DNS challenge |
| Identity | Cognito or the client's IdP | Local accounts, optional OIDC |
| Logs and metrics | CloudWatch | Loki or files, optional shipping |
| Backups | Snapshots | `pg_dump` plus restic to disk, optionally to their S3 |
| Updates | Image rollout | Self-updater with health checks and rollback |
| Remote support | SSM Session Manager | WireGuard or Tailscale tunnel, with consent |
| Scheduling | EventBridge and ECS tasks | systemd timers |

## The three deployment profiles

| Profile | Where | What it adds |
|---|---|---|
| `cloud` | The client's cloud account | Everything, including the frontier lane |
| `office` | The client's hardware | Local inference, local documents, updater, remote support |
| `hybrid` | Both | The office profile plus the cloud leg: a frontier API path and a backup bucket |

Hybrid is composed, not separate. `deploy/office/install.sh --cloud-leg` applies
`deploy/cloud-leg/` to the client's account and flips the routing policy.

## Lanes

Every deployment runs the same lane machinery. What differs is **which lanes can exist**, because a
lane needs hardware to run on.

| Profile | Local lane | Frontier lane | What that means |
|---|---|---|---|
| **Cloud** | No, unless a GPU is added | Yes, always | Inference is a provider model in the client's own account. There is no local model because there is no local hardware |
| **Office** | Yes | Off by default, switchable on | Turning the frontier lane on is the hybrid configuration. One flag, no new deployment |
| **Hybrid** | Yes | Yes | The office build plus a cloud leg in the same account. Documents stay local; released context can leave |

- **Local lane.** Inference on hardware the client controls. Documents, records, retrieval, and
  drafting never leave the building. Requires a GPU, which is why the cloud profile does not have it.
- **Frontier lane.** Inference through a provider: Bedrock in the client's account under their
  agreement, or an API. It receives instructions and only the context the user or the policy
  explicitly releases. Every call is recorded with its payload hash.
- **Office services lane.** Distribution, monitoring, and backup. Present where an office box exists.

**Feature parity is not capability parity.** All three profiles have the same interface, workflows,
and audit, and the same lane policy engine. They do not produce the same answers: a 32B local model
is not a frontier model, which is precisely why the office build can switch the frontier lane on. Say
it that way to a client and the tier line makes sense; imply the answers are identical and the first
hard question proves otherwise.

**Moving between profiles is a deployment change.** A cloud client who later installs an office box
ends up in exactly the hybrid configuration, in the same account, under the same agreements, with
nothing to migrate. That is the "start where you are, move when the rules change" promise, and it
only holds because the lanes are configuration rather than separate products.

**Routing is by data class, not by task difficulty.** A request carries a classification from
its source, and the classification decides the lane. The gateway enforces it, and every frontier
call is written to the audit sink with the model, the user, the payload size and hash, and who
approved it.

## Build pipeline

1. One CI pipeline builds and publishes image digests.
2. `deploy/cloud` is Terraform applied to the client's account.
3. `deploy/office` is the installer: checks the GPU and drivers, installs Docker, pulls the
   pinned digests, writes config from the manifest, starts services, runs health checks, and
   configures backup, auto-update, and remote support.
4. `deploy/cloud-leg` is the small Terraform stack the hybrid adds.

Both builds run the same digests. Passing CI requires the parity suite to pass against every
profile, not just the cloud one.

## What is deliberately unresolved

The shell leaves these open on purpose, because they are base-system decisions:

1. Chat surface: Open WebUI (already in use) or LibreChat (MIT, richer agent and ACL features).
2. Orchestration on the office box: Docker Compose, or k3s.
3. Workflow engine: n8n, given its licence only covers internal business use for a paid service.
4. Identity and roles: local accounts, the client's IdP, or both.
5. How the frontier gate is enforced: redaction service, allowlist, per-request approval, or all.
6. Backup targets and the recovery promises per build.
7. Update mechanism: signed pull-based releases.
