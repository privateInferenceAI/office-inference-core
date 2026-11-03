# Architecture

## The one-sentence model

One core system, two builds, and every offering is a configuration of it.

**Target update, 2026-10-22.** The cloud build's product is the Cloud Workspace: a workspace agent with a
task-scoped sandbox, approved web egress, artifacts and connectors. The design is
`docs/design/cloud-workspace.md`; the phase is `WS`. The office build is a separate product with its own
boundary, and its "nothing leaves the building" rule does not apply to the cloud build.

## Current implementation, 2026-10-23

The architecture above is the target shape. The current deployment implements part of it:

- Phases C1 to C7 are built and verified. C8 is partly built. The phase table is in docs/current-deployment.md.
- The build box in Office Inference account 878 runs the API, Postgres, LiteLLM, ingestion, retrieval and the
  audit tool. It publishes no port.
- The MCP gateway and the email connector run in badj.ai client tenant account 426, behind Caddy at
  https://3-85-130-96.sslip.io. The gateway listens on loopback. The API signs a short-lived session assertion
  because the tenant has no database route back to account 878.
- TLS is real at the tenant edge. It is still absent from the build box, which is reached from inside or over
  SSH.
- Secrets: the connector token is in Secrets Manager and rotates without a redeploy. The rendered env still
  holds other generated secrets and is copied by hand, not in Secrets Manager.
- Identity is still a development session token. A people-facing sign-in remains the unresolved item in the
  list below.
- Audit chain version 4 covers approval, prompt and completion digests. The audit bucket has Object Lock in
  GOVERNANCE mode for 90 days.
- Backup, restore, alarms, rollback, cost reporting, golden questions, fresh-deployment acceptance and load
  testing are not built.

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

### A lane is not a cost tier

These are two different axes, and conflating them makes the tier line sound like a hardware argument
when it is mostly a data argument.

**Cost tier.** Cheap model for easy work, expensive model for hard work. **Every profile has this.**
The office build gets its cheap tier from hardware it owns, where the marginal cost of a request is
electricity. The cloud build gets the same thing from a small provider model, and it does not need a
card to do it.

The numbers are not close. Nova Micro on Bedrock is about 0.035 USD per million input tokens and
0.14 USD per million output tokens. A published AWS example puts 22,000 light queries a month, at 800
input and 60 output tokens each, at under one dollar of inference. The same workload against a
frontier-class model at roughly 3 and 15 USD per million lands near seventy dollars. That is the
cheap tier doing its job, and it is a routing decision rather than a hardware purchase.

**Data boundary.** Whether client content reaches a provider at all. This is the axis that changes
between profiles, and it is the only thing a GPU buys.

So: **you buy local inference for the boundary, not for the price.** At small-business volumes the
rented cheap tier is far cheaper than owning a card, because the hardware is amortised against
almost nothing. The honest reasons to choose in-office are that documents cannot leave, that the
system works without a connection, and that the client wants to own it. "The monthly bills were
digging in" is true only at unusual volume, and it should be the third reason in a conversation
rather than the first.

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
3. Workflow automation: resolved 2026-11-03 by decision 0011. Our engine governs actions with approvals and
   chain rows; n8n returns as the client-owned layer for triggers, schedules and integrations.
4. Identity and roles: local accounts, the client's IdP, or both.
5. How the frontier gate is enforced: redaction service, allowlist, per-request approval, or all.
6. Backup targets and the recovery promises per build.
7. Update mechanism: signed pull-based releases.
