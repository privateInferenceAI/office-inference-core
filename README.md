# Office Inference Core

One system. Two builds. Every product is a configuration of it.

- **Cloud build** runs in the client's cloud account.
- **Office build** runs on hardware in the client's building, with the documents and the
  inference on that box.
- **Hybrid is not a third build.** It is the office build with a cloud leg switched on. The
  documents stay in the office; heavy reasoning and agentic work can go out when the client
  turns that lane on.

Nothing in this repository is client-specific. What a client gets is a **manifest** (what is
turned on) applied to a **profile** (where things run).

## Repository map

| Path | What lives there |
|---|---|
| docs/current-deployment.md | **Start here for the current state.** Phase status, topology, endpoints, audit chain version, open rows and blockers |
| docs/runbook.md | The build log and the phase C6 to C8 commands |
| docs/operator-guide.md | The walkthrough: the order to run the stages, and what the checkpoints mean |
| `docs/manual.md` | **Start here to run it.** The operating manual: how the system works, every command with an example, and how to add a role |
| `docs/handoff.md` | **Start here to continue it.** Where the build stands, how we work, the next phase, and the traps |
| `docs/architecture.md` | The layered model: system versus substrate, profiles, and lanes |
| `docs/service-inventory.md` | Every service, tagged office, cloud, or either |
| `schemas/manifest.schema.json` | How a client's configuration is described, as the machine-readable contract |
| `docs/build-plan.md` | The system we sell and how to build it, including the coverage matrix in section 13 |
| `schemas/manifest.schema.json` | The machine-readable contract for a manifest |
| `packs/catalog.yaml` | Capability packs: what we sell, and what each one turns on |
| `profiles/` | Where things run: cloud, office, hybrid |
| `manifests/` | Example client configurations |
| `services/` | One directory per service, plus the template |
| `deploy/cloud/` | Terraform for the cloud build |
| `deploy/office/` | Installer, compose, systemd units for the office build |
| `deploy/cloud-leg/` | The small Terraform stack the hybrid adds to the client's account |
| `tests/parity/` | The suite that proves a configuration behaves |
| `scripts/` | Validation and release helpers |

## Status

C1 to C7 are built and verified. C8 is partly built.

The system runs from a manifest: Postgres, LiteLLM, ingestion, retrieval, the assistant API, the client app,
workflows and approvals, a durable agent runtime and the MCP gateway. The MCP gateway and the email connector
run in a separate client tenant behind TLS. The audit record is hash-chained at version 4, exported, anchored
and stored under Object Lock. The full suite is 1355 passed, 1 skipped.

The open rows are alarms, backup and restore, RTO, rollback, SSM-only support, cost reporting, golden
questions, fresh-deployment acceptance, load testing, and the business rows: contracts, insurance, legal
review, on-call, subprocessors and pricing. Each blocker is listed in docs/current-deployment.md.

One caveat before you trust an email: the client-tenant connector points at Mailpit, which captures mail
instead of delivering it. A production connector uses the client's SMTP or SES identity.
