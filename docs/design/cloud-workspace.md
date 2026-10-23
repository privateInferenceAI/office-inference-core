# The cloud workspace

**Status:** target scope, written 2026-10-22. This is the product the cloud build is for. The office build is a
different product with a different boundary; do not apply the office build's rules here.

## Why this exists

Document chat with citations is a feature. A company does not pay a build fee and a retainer for a search box.
The cloud product is a workspace where a person gives the agent a task and the agent does the work: research,
reason, use tools, write files, run code, reach approved systems and the web, and return an artifact. The person
watches, steers, and approves anything irreversible.

The office build stays the restricted product for a client who wants nothing to leave the building. The cloud
build runs in the client's AWS account on Bedrock. Its boundary is the client's account, the client's VPC and the
client's data, not "no network."

## What the user gets

- A conversation and a workspace in the client app: ask, plan, run, inspect steps, open artifacts, approve.
- A sandbox per task with a file workspace, shell and code execution. The agent can read its own inputs, write
  outputs, run a script, parse a file and check its work.
- Approved web access through an egress proxy. The client sets the allowlist and the data classes. Every fetch is
  recorded. It is off by default.
- Connectors through the MCP gateway: email, calendar, files, CRM, accounting and the rest, each with its own
  client credential and its own policy.
- Artifacts: files, drafts, reports, spreadsheets and code the agent produced, stored in the client's S3 bucket
  and shown in the UI.
- A record: every model call, tool call, sandbox command, web fetch, connector call and approval is in the same
  audit chain, addressable by task and step.

## Architecture

The cloud deployment stays one EC2 instance running Docker Compose in the client's account. The workspace adds:

| Component | What it is | Why |
|---|---|---|
| `agent` runtime | The C6 loop, queue, checkpoints, sub-agents, gate and audit | One runtime, one policy, one record |
| `workspace` | Per-task sandbox: ephemeral container, file workspace, shell/code, quotas | The agent needs an environment to do work, not only to read |
| `egress-proxy` | Allowlisted HTTP(S) proxy for web and approved APIs | The client decides what leaves the VPC, and every request is logged |
| `artifacts` | S3 bucket in the client's account, content-addressed | Outputs belong in the client's estate, not the container |
| `mcp` gateway | Connector registry, per-connector credentials, tool policy | Section 3's gateway becomes the connector path |
| client app | Workspace, artifact viewer, approval queue, activity | The product surface |
| LiteLLM + Bedrock | Model gateway and inference in the client's account | The cloud lane |

The sandbox is not the host. It gets a task-specific directory and a network namespace; it cannot read
`/opt/oi-core`, the rendered environment, the assertion key or another task's files. It is destroyed when the
task ends, and its artifacts are copied to S3 before teardown.

## Isolation and limits

- One sandbox per task. No reuse across tasks or sessions.
- CPU, memory, disk, wall-clock and token quotas, set by the definition and enforced by the runtime.
- No host mounts except the task input directory and the artifact output directory.
- No standing credentials in the sandbox. A tool that needs a credential calls the MCP gateway from the runtime,
  not from inside the sandbox.
- Egress only through the proxy. DNS and direct sockets are blocked. The proxy enforces the client's allowlist
  and records the destination, the data class and the redaction summary.
- A cancelled task is stopped and its sandbox removed. Artifacts written before cancellation are kept and
  recorded.
- A workspace task is cloud or hosted only. The office manifest refuses the workspace pack.

## The runtime seam

The C6 runtime calls tools through one interface. In Section 2 the interface is backed by the retrieval toolbox
and a test tool. In the workspace phase it is backed by:

- sandbox tools: write file, read file, list, run command, run script;
- web tools: fetch, search, through the proxy;
- connector tools: through the MCP gateway.

The runtime does not know which is which. Every call is a step, a chain row and a policy decision. Irreversible
tools stop at the C5 gate with a digest over the exact call.

## What is not in this product

- A general coding harness. The sandbox exists for the task, not as a developer environment.
- Peer teams or swarms. One runtime, one owner, read-only children at depth one.
- Unrestricted internet. The client's allowlist is the boundary.
- Multi-tenancy. One deployment per client.

## Build sequence

The workspace phase is `WS`, after C6 and before the final hardening. It is made of:

1. **Sandbox service**: ephemeral environment, file workspace, shell/code tools, quotas, teardown.
2. **Egress proxy and web tools**: allowlist, policy, audit, data-class gate.
3. **Artifact store**: S3 bucket, content-addressing, retention, UI download.
4. **Connectors**: the first real business connector through the MCP gateway, with approval for a send or post.
5. **Workspace UI**: run view, artifact viewer, approval queue, activity.
6. **Acceptance**: a research task that uses a file, a web source and code, spawns one read-only child, checkpoints,
   stops for approval on a send, resumes after a restart, produces an artifact, and verifies the chain.

## Open decisions

- Which first real connector: client SMTP or Microsoft 365 Graph `Mail.Send`. Graph reuses the intake app
  registration but needs a consent and a licensed mailbox. SMTP is simpler and client-owned.
- Which sandbox substrate on one EC2 host: Docker with gVisor, Firecracker, or a separate small instance. The
  first build can use Docker with gVisor; the isolation claim must match what is actually run.
- Whether Standard Cloud Hosted gets the workspace in our account, or only the client-account tiers.
- The web allowlist defaults and the client-facing policy form.

## Evidence and acceptance

The workspace phase is not done until a person who did not build it can open the app, start a research task, watch
the steps, open the artifact, approve a send, kill the process, restart it, and see the task resume with one
record. The register rows are `WS-*` in `docs/requirements.yaml`.
