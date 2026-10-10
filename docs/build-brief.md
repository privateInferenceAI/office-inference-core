# Build brief

**This document is the only context the build session gets.** If something isn't here, it doesn't
exist for that session. Read it in full before the first command.

Written 2026-10-10, from the merged design passes and two adversarial audits. Verified against the
repository, the live AWS APIs, and the accounts.

---

## 0. How this work happens

**Anthony types every command.** The agent writes code, opens pull requests, and explains. It does not
run stack commands on the build box, and it never commits to `main`.

**The agent may run read-only AWS discovery directly** through the AWS MCP tools, because that saves
pasting console output. Anything that creates, modifies, or deletes a resource is described first and
approved explicitly.

**Branch, commands, expected output.** Every handoff states the branch, the exact commands, and what
the output should look like. The terminal loop has to be copy-pasteable.

**A phase ends when its exit criteria pass, not when its code is written.** Written code that has not
run is a claim.

**Nothing on a branch is visible from `main`.** Check out what you need. Two sessions have already
lost time to an unmerged branch being invisible.

---

## 1. What we are building

Office Inference sells AI systems to small businesses. One core system, two builds, four offerings. The
difference between the offerings is configuration, not architecture.

| Offering | Runs in | Inference | Note |
|---|---|---|---|
| **Standard Cloud** | The client's AWS account | Bedrock, two cost tiers | No compliance claim |
| **Compliant Cloud** | The client's AWS account | Bedrock, compliance-grade services | Same architecture, four controls on |
| **Standard Cloud Hosted** | Our account | Bedrock | Prepaid annual, ten seats minimum |
| **In-Office** | The client's hardware | Open weights locally | GPU. Nothing leaves the building |
| **Hybrid** | Both | Both | A configuration, not a product |

**The client owns the AWS account and pays AWS directly.** We charge a build fee plus an optional
retainer, and never pass through cloud costs. A revocable role in their account, never standing
credentials.

**We are building the cloud deployment first, on CPU, with no GPU.** Reasons: it is the cheapest place
to find problems, it produces the demo, and the office build reuses every component and swaps the
substrate. GPUs exist for local inference and local audio only.

---

## 2. Commercial frame

| Offering | Build | Monthly | Year-one margin |
|---|---|---|---|
| Standard Cloud | 12,000 | 750 | 40 percent |
| Compliant Cloud | 35,000 | 1,800 | 22 percent, soft. Go to 45,000 or narrow to one framework |
| In-Office entry | 14,000 plus hardware | 900 | 12 percent, soft. Go to 18,000 or drop it |
| In-Office 4-card | 25,000 plus hardware | 1,200 | 25 percent |
| Hosted | 2,500 setup | 600 / 1,100 / 2,400 for 5 / 10 / 25 seats | 21 / 50 / 72 percent |

**Never charge for tokens.** On a routed deployment inference is 3 to 17 percent of the running cost,
so a usage line item is theatre and invites the client to audit the one number that does not matter.

**Cost to run a client deployment:** 125 to 170 USD a month for a 5-person practice, 312 to 526 for a
20-person firm, 659 to 740 for a 50-person firm. A compliant-ready 10-user shape with all the endpoints
is around 635.

**Packaging changes still to apply to `packs/catalog.yaml`:** bundle document-chat,
workflow-automation, and offsite-backup into the base with an `included_in` key; delete the
frontier-lane pack and fold it into core with `requires_profile: [cloud, hybrid]`; add a connectors
pack priced per connector (1,500 to 3,500 to build, 100 to 250 a month to maintain); require a
compliance profile for compliance-pack; hold voice until the voice phase ships.

---

## 3. Where things stand today

### What exists on `main`, verified

| Present | Detail |
|---|---|
| `scripts/render_manifest.py` | Renders a manifest into `rendered/{env, litellm.yaml, services.txt, manifest.json}`. Now emits Bedrock entries for the frontier lane, which it previously did not |
| `scripts/validate_manifests.py` | Validates manifests and the pack catalog. Now enforces the residency rules (no `global.*` outside none and contractual, Fable refused for regulated profiles, residency required for remote inference) and the six-year SEC retention floor |
| `services/audit/` | Append-only audit sink with a hash chain, an export, an `anchor` command, and `verify --anchor`. Payload content is hashed and checked |
| `tests/test_audit_verify.py` | Regression tests replaying four attacks that previously defeated the verifier |
| `deploy/office/compose/docker-compose.yml` | Four services: postgres, llamacpp, litellm, audit |
| `docs/build-plan.md` | The merged design. Section 13 maps every readiness item to a phase. Section 12 records every correction |
| `docs/production-readiness.md` | The 49-item gap list plus ten added |
| `docs/runbook.md` | Phases 0 to 2, actually built and run |
| `manifests/`, `profiles/`, `packs/`, `schemas/` | Config surface |

### What does not exist, verified

| Missing | Consequence |
|---|---|
| **`services/ingestion/`** | PR #7 was closed rather than merged. Document ingestion, Qdrant, TEI, and the migration `003_ingest_manifest.sql` are **not on main**. Phase 3 must be rebuilt, with Docling this time |
| **Qdrant and TEI in compose** | Same reason. Retrieval does not exist on `main` |
| **`deploy/*/terraform/`** | Files exist with zero resources |
| **`deploy/office/install.sh`** | 69 lines, 13 TODOs |
| **The cloud deployment** | Nothing of it exists. That is what C0 starts |
| **`profiles/cloud.yaml`** | Still says `compute: managed-containers`. The decision is one EC2 instance running Docker Compose, same as the office build |
| **The pack changes** | Section 2's list is unapplied, so a cloud manifest has no inference lane without the renderer fix |
| **`tests/parity/`** | A README only. No golden questions exist |

### The box and the accounts

| Thing | Value |
|---|---|
| Development instance | `i-0887564c9fe06b0b5`, t3.large, Ubuntu 24.04, 30 GB gp3, **currently running** |
| Address | Elastic IP `52.204.202.66` (`eipalloc-0f0560d02bcb32517`) |
| Account | **878451098165 (Office Inference)**, holds the credits. Profile `oi-credits` |
| Credits | 5,000 USD AWS Activate, expires **2028-10-31**. October spend in that account: **0.01 USD** |
| GPU account | 426063972668 (Novus AI Labs), 48 vCPU G quota, holds a stopped g6e.2xlarge. Profile `default` |
| Management | 637551066720, billing only. Profile `oi-mgmt` |
| Repo on the box | `/opt/oi-core`, run compose from `deploy/office/compose/docker-compose.yml` with `--env-file rendered/env` |

**A standing rule already broken once:** stop the instance when you finish. A running t3.large costs
about 2 USD a day, and the elastic IP bills whether or not the instance runs.

---

## 4. The target for C0

**Launch `m7i.xlarge`: 4 vCPU, 16 GB RAM, no GPU, Ubuntu 24.04, 30 GB gp3.** About 0.2016 USD an
hour, roughly 1.61 USD for an eight-hour build day.

A t3.large has 2 vCPU and 8 GB, which was enough for the gateway and the audit sink and will stall the
moment Docling meets a two-column scanned fee schedule. If ingestion proves too slow, move to
`m7i.4xlarge` (16 vCPU, 64 GB, about 588 USD a month), which is also the production shape for a
10-user deployment.

Everything else is identical to the office build: same compose file, same images, same manifest. The
cloud difference is the profile, not the software.

---

## 5. Architecture

    Client browser
        |  HTTPS
    ALB (ACM certificate)
        |
    EC2 instance, private subnet, one Auto Scaling group of size one
        docker compose:
          caddy / alb-target   ingress routing inside the box
          app                  the client interface (Ask, Do, Review, Activity)
          api                  the assistant API: sessions, roles, citations, streaming
          litellm              the model gateway: per-role keys, budgets, spend
          router               role and residency policy
          qdrant               vectors, access labels in the payload
          tei-embed            bge-m3
          tei-rerank           bge-reranker-v2-m3
          docs                 ingestion and retrieval tools
          agents               the agent runtime
          mcp                  the MCP gateway with vetted connectors
          n8n or replacement   workflows
          audit                the audit sink
        |                 |
    RDS Postgres      Bedrock (this account)     S3 (documents, exports, backups)

**Decisions already argued, with the reason:**

- **One EC2 instance running Docker Compose, not ECS and not EKS.** Single tenant, 5 to 30 users.
  Fargate at 16 vCPU is about 568 USD a month and cannot hold a Qdrant volume. EKS adds a control
  plane that costs about 438 a month if a Kubernetes version drifts. Identical compose on both builds
  removes a class of "works differently in the cloud" bugs. The cost is no zero-downtime deploy.
- **RDS for PostgreSQL, single-AZ, as the cloud default** (about 47 USD a month at db.t4g.medium).
  Automated backups, point-in-time recovery, patching, and a compliance story an examiner can read.
  Postgres in compose stays the office default and the cloud fallback.
- **Retrieval stays self-hosted, not a managed knowledge base.** A managed one would run retrieval
  outside the audit sink and strand the access filter and citation verifier in a service the office
  build cannot have.
- **No NAT gateway. Nine interface endpoints, about 65 USD a month**, so the retrieval path has no
  route to the internet at all. The only egress is to Bedrock, through an endpoint.
- **Compliance is not a second architecture.** Same Terraform with four things switched on:
  customer-managed KMS keys, VPC endpoints, Object Lock on the audit and document buckets, and the
  retention and eligibility rules enforced by the validator.
- **Terraform owns the account** (VPC, subnets, endpoints, security groups, ALB, ACM, Cognito, RDS,
  S3, KMS, IAM, CloudWatch, budgets). **The installer owns the instance** (pull images by digest,
  render config from the manifest, start compose, health check, register with SSM).

---

## 6. Models and routing

**Roles, never model names, in the product.** Verified against `bedrock:ListFoundationModels` in
us-east-1 on 2026-10-10. The catalog holds 124 models across 18 providers.

| Role | Cheap tier | Frontier tier |
|---|---|---|
| `front_desk` | `amazon.nova-micro-v1:0` | `anthropic.claude-haiku-5-5` |
| `analyst` | `amazon.nova-lite-v1:0` | `anthropic.claude-sonnet-5` |
| `reader` | `amazon.nova-pro-v1:0` | `anthropic.claude-sonnet-5` |
| `coder` | `deepseek.v3.2` | `openai.gpt-6-astra` |

Prices per million tokens, in-region and global where they differ: Nova Micro 0.035 / 0.14. Nova Lite
0.06 / 0.24. Haiku 5.5 **1.00 / 5.00**. Sonnet 5 2.20 / 11.00 in-region, 2.00 / 10.00 global. There is
**no batch discount** on Sonnet 5, Sonnet 5.5, Haiku 5.5, or Opus 5.5.

**The cheap tier is a cost decision and a latency decision.** Nova Micro is about thirty times cheaper
on input than Haiku 5.5, so routing light work to it saves real money, and it answers faster, which
matters for voice and the front desk.

**The three residency rules, enforced in `validate_manifests.py`:**

1. **No `global.*` inference profile** unless the compliance profile is `none` or `contractual`. A
   global profile can route a prompt to any Region AWS operates in.
2. **A geographic profile is not single-Region either.** A `us.*` profile from us-east-1 can route
   across us-east-1, us-east-2, and us-west-2. Fine for most compliance work, and it belongs in the
   service agreement in plain words.
3. **A single-Region requirement disqualifies every inference-profile-only model**, which removes
   Claude, GPT, Grok, and Kimi K3. It leaves **58 ON_DEMAND models**, including
   `mistral.mistral-large-3-675b-instruct`, `qwen.qwen3-vl-235b-a22b`, `nvidia.nemotron-super-3-120b`,
   `openai.gpt-oss-120b-1:0`, `moonshotai.kimi-k2.5`, `zai.glm-5`, ON_DEMAND embeddings
   (`cohere.embed-v4:0`, `amazon.titan-embed-text-v2:0`), and a reranker (`cohere.rerank-3-5:0`).
   Residency costs some capability, not most of it.

**Retention rules to verify per model card and then encode:** Haiku 5.5, Sonnet 5, and Sonnet 5.5 are
reported to carry no retention requirement. Fable 5 and 5.1 permit only `aws_review` or
`provider_data_share`, which means instructing AWS to retain inputs and outputs for human review, and
that is incompatible with Compliant Cloud. The validator refuses them for regulated profiles.

**Routing is mechanical.** Cheap tier first. Escalate on exactly three triggers: the task is on the
agentic pack's list, the request needs synthesis across more than one document, or the user asks for
the stronger model. **No model's own judgement may move content between lanes.**

**Open risk:** Nova Micro is past its stated end-of-life floor. Model deprecation is unowned and
unpriced. Assign it before C0 so a provider sunset does not become an incident.

---

## 7. Ingestion and retrieval

    watch folder -> Docling (layout, tables, OCR) -> page-aware chunks
                 -> bge-m3 embeddings via TEI -> Qdrant with access labels in the payload
                 -> hybrid search: dense plus keyword, fused
                 -> bge-reranker-v2-m3 -> citation assembly -> verifier -> answer

**Docling is required, not optional.** It brings OCR, table structure, and reading order. Reasoning:
a scanned document that yields nothing is an obvious gap, but a fee schedule read as a token stream
produces a confidently cited **wrong number**, which is the failure that loses a client. Layout,
table, and OCR models are CPU work; the reference instance has the cores for it.

**Hybrid search is required.** Vector search alone is weak on exact identifiers: procedure codes,
statute sections, invoice numbers, account numbers, names. Qdrant prefetch plus reciprocal rank
fusion, then rerank 50 to 100 candidates down to a handful.

**Citation verification is the product, in three checks:** every cited index exists in the retrieved
set; the cited page matches the passage's page; the sentence is entailed by the passage. Unsupported
sentences are marked amber rather than deleted, and the support rate is recorded per answer so a
client can be told a true number.

**Chunking** is page-aware and structure-aware where the parser provides it, so a table is not split
mid-row. Every chunk carries document id, page, access label, and content hash.

**Bulk indexing is a separate job.** A million pages is roughly 150 USD on a temporary c7i.8xlarge for
a few hours, against about nine days of pegged CPU on the production instance.

---

## 8. Trust

### The audit sink

Built, and hardened after an audit broke the original verifier four ways.

- **The chain**: each row carries `prev_hash` and `row_hash`, with a trigger enforcing append-only.
- **The anchor**: `audit.py anchor` writes the chain head to a file meant to leave the machine
  (the client's S3 bucket under Object Lock, or a second channel). `verify --anchor` compares an
  export against it. **This is the only check that detects a deleted tail.** Without an anchor, the
  command says plainly that completeness cannot be shown.
- **Payload integrity**: a stored payload is hashed and compared to `payload_sha256`, so content
  cannot be rewritten while the hashes still verify.
- **Tombstones**: a retention purge must write an event carrying the hash before and after, so a gap
  is always visible. `oi.allow_audit_maintenance` is a session setting anyone with database access
  can flip, and today it leaves no trace. This is not yet fixed.
- **Content mode per data class**: hash, redacted, or full, plus prompt and completion hashes, chosen
  by the client and recorded in the manifest. Not yet built.
- **Retention**: `audit_retention_days` exists; the validator enforces six years for `sec-17a4`.

### Identity and keys

**Roles come from an authenticated session, never a parameter.** The ALB validates a Cognito token,
the assistant API maps it to a role, and the tooling refuses a hand-typed actor outside an explicit
development mode. Today the audit CLI takes `--actor` as free text and the runbook demonstrates a
hand-typed admin. No client has seen that, and it stops before one does.

**Per-role virtual keys with hard budgets**, one set per deployment, a hard cap rather than an alert.
The LiteLLM master key exists for administration and appears nowhere else. Today the tools use it.

### Refusals

Four named reasons, each written to the same chain as a success and each carrying its retrieval trace:
`no_source`, `boundary`, `policy`, `insufficient_evidence`.

### The demo

Ask a question, show the cited page, then ask something the role may not read and show the denial in
the audit log. Hand over real JSONL, and tamper with a copy in front of them. The export is the only
artifact they take away, and it has to come from a deployment configured for the demo rather than a
scratch box carrying test events.

---

## 9. Operations

**Five alarms page. Everything else is a digest.**

| Alarm | Why |
|---|---|
| Chain head moved backwards | The record was altered |
| Sixty minutes with no audit event in business hours | Silently down, or silently unrecorded |
| Disk at ninety percent | The next write fails |
| Database down five minutes | Everything is down |
| Spend at cap | A runaway loop is spending the client's money |

**Backups:** RDS automated with point-in-time recovery; S3 versioning and Object Lock for documents
and exports; a restore drill in the acceptance test.

**Recovery objectives are not established.** There is no RTO until a restore drill produces one, and
no availability target until a month of measured uptime exists. Do not put either in a contract before
then. An earlier draft promised five minutes and one hour; the design pass estimated eight hours and
said neither had been demonstrated.

**Object Lock mode is undecided and permanent.** Compliance mode cannot be shortened or removed by
anyone, including root, until the retention expires. At 2,190 days that is six years the bucket cannot
be deleted. Use **Governance** unless a regime demands Compliance, disclose it in the contract, and
price the storage.

**Updates:** images digest-pinned with the version recorded beside the pin, signed releases, rollback,
a maintenance window the client picks, and a scheduled CVE watch. The LiteLLM advisory is why: we
pinned an image, recorded no version, and could not answer the first question a security review asks.

**Client boxes use SSM only, never SSH keys.**

**Cost reporting has a trap.** Claude on Bedrock bills through AWS Marketplace and appears in Cost
Explorer under the model provider, not under Amazon Bedrock. A report filtered on service equals
Bedrock shows a Claude deployment as **zero**. Read the provider line and the record type.

---

## 10. The quality gate

**Sixty golden questions per deployment**, forty general and twenty specific to the client's work.
Thresholds: 92 percent retrieval, 85 percent answer support.

**Five results block a release regardless of averages:** an access-control leak; a frontier-bound
request that leaves without redaction or carrying a data class the policy forbids; an invented answer
to a question the documents do not cover; a numeric mismatch against the source; a successful prompt
injection from document content.

Run the set on every model swap, every chunking change, and every parser upgrade. This is what makes
"models are configuration" true rather than a slogan.

---

## 11. Build sequence

| Phase | Work | Exit criteria |
|---|---|---|
| **C0** | Reference instance (m7i.xlarge, no GPU), repo, compose up, manifest rendered, CI running lint and the tests | The stack runs healthy on CPU, `test_audit_verify.py` passes, and `validate_manifests.py` passes |
| **C1** | Per-role virtual keys with hard budgets; the anchor job on a timer; the purge tombstone | A key-scoped model call is recorded, anchored, and verifiable with `--anchor`; a purge leaves a visible tombstone |
| **C2** | Docling ingestion (OCR, tables, layout), Qdrant, TEI, hybrid search, reranking, the three-check citation verifier, document versioning, duplicate detection, PII redaction | A cited answer from a scanned, table-bearing document; a recorded refusal; a superseded document not cited; redaction proven by a test |
| **C3** | Assistant API: sessions, roles from tokens, streaming, timeouts and retries, degraded mode when Bedrock throttles | Every capability over one API with no UI; a forged role refused and recorded |
| **C4** | Client app: Ask, Do, Review, Activity | Someone who has never seen it can ask a question and approve an action |
| **C5** | Workflows and the first connectors | Invoice intake with an approval gate |
| **C6** | Agent runtime and MCP gateway | An agent task with a sub-agent and a human checkpoint |
| **C7** | Ingress, TLS, Cognito, Terraform into a client account | A deployment created from code in a client account, no hand-typed steps |
| **C8** | Alarms, backups, restore drill, golden questions, vulnerability scanning, drift detection, capacity reporting | The acceptance test passes on a fresh deployment |
| **C9** | Demo rehearsal, twelve minutes, eight scenes | The demo |

C0 to C4 produce the cloud demo. The rest makes it sellable and supportable.

---

## 12. Open items and blockers

The full register is `docs/requirements.yaml`, with a checker at `scripts/check_requirements.py` that
fails when an item has no phase or no verification. **The build is not done while items are open.**

**Thirteen items are open with no design yet:**

1. **No retention or deletion requirement for documents and conversations.** The manifest carries one
   retention number and it covers the audit log. The only deletion path is the ingestion
   reconciliation loop. Legal hold is assigned to block a purge job that does not exist.
2. **Nothing defines data destruction at client termination, or proves it.**
3. **No answer for a compromised, suspended, or unfunded client AWS account.**
4. **24x7 30-minute SEV1 acknowledgement rests on one person.** No deputy, no client break-glass,
   nothing for illness.
5. **The hosted tier holds client data with the least defined exit**, and an open liability question.
6. **Model deprecation is unowned and unpriced**, and Nova Micro is past its stated EOL floor.
7. **The maintenance escape hatch is unauthenticated and unrecorded.**
8. **Content mode is not built**, so the record proves a call happened but not what was said.
9. **Per-user and per-document permissions do not exist.** Folder-based ACLs with two roles will not
   survive a serious evaluation.
10. **No incident response runbook** and no notification clocks.
11. **No offboarding package** for a client employee or a client.
12. **Onboarding checklist, support terms, subprocessor register, and insurance do not exist.**
13. **Four runbook verification steps cannot fail.** One passes without Docker installed, one uses
    `psql --set` (a client variable) against a server setting, one quotes a stale manifest count, and
    one fails on a working system.

**Blockers that need Anthony, not research:**

| # | Blocker |
|---|---|
| 1 | **n8n's Sustainable Use License.** It does not clearly permit deploying n8n into a client's building as part of a paid build. Get it in writing or replace it with Windmill, Temporal, or small services we own |
| 2 | **Anthropic models and the AWS BAA.** Does Bedrock's Anthropic access sit inside the AWS BAA for PHI, or need a separate agreement? Ask AWS in writing before the first medical proposal. If the answer is no, the medical lineup is the 58 ON_DEMAND models |
| 3 | **Legal citations need counsel.** The HIPAA, SEC 17a-4, FERPA, and CMMC references came from secondary sources because the primary sites block automated reads |
| 4 | **Two pricing soft spots:** Compliant Cloud at 22 percent year-one margin and In-Office entry at 12 percent |

---

## 13. Traps already paid for

Each of these cost time or credibility once. They are here so they cost nothing again.

| Trap | What happens |
|---|---|
| A missing bind-mount source directory | Docker creates it as `root`, and a later `git pull` fails with permission denied |
| Pulling an image by digest | Stores no tag, so `docker run` by tag fails afterwards |
| `docker inspect <name>` | Inspects the container, not the image |
| Cost Explorer | Lags by up to a day, and credits appear as a separate record type rather than reducing usage |
| Claude on Bedrock | Bills through AWS Marketplace, under the model provider. A `service = Bedrock` filter reports zero |
| Bedrock model ids | Must be exact (`moonshotai.kimi-k3`, not `moonshot.kimi-k3`), and most frontier models are inference-profile only |
| A validator rule that is claimed but not written | Two documents said residency was enforced; it was not |
| A check whose two sides come from one source | Cannot fail. The audit verifier's "anchor" was this, and it manufactured confidence |
| A renderer that knows one lane | A cloud manifest rendered an **empty** model list |
| `psql --set` | Sets a client variable, not a server setting. The escape hatch demo could never have worked |
| Qwen3 reasoning models | Return empty content with `finish_reason: length` unless `max_tokens` is raised |
| Stopped instances | EBS volumes and elastic IPs keep billing |
| Unmerged branches | Invisible from `main`. Check out what you need |
| Asserting from memory | Wrong model list, wrong price, wrong retention floor, all in one merge. Verify against the API or the primary text |

---

## 14. Reference map

| Path | What is in it |
|---|---|
| `docs/build-plan.md` | The merged design: product, architecture, models, trust, operations, phases, coverage matrix, corrections |
| `docs/production-readiness.md` | The 49-item gap list plus ten added, with status |
| `docs/requirements.yaml` | The register: every item with a phase, a verification, and a status |
| `docs/runbook.md` | Phases 0 to 2 as actually built, with commands |
| `docs/architecture.md` | Layers, profiles, lanes |
| `docs/decisions/` | ADRs: service implementations, agent topology |
| `docs/service-inventory.md` | Every component, what it is, where it came from |
| `schemas/manifest.schema.json` | The configuration contract |
| `~/projects/oi-design/` | Four raw design passes (cloud architecture, capability parity, reliability, economics), two audits (completeness, verifiability), and the verified-facts file. Read these for the reasoning behind a decision |
| `~/projects/office-inference-research/` | Competitor teardown, open-source survey, hardware roadmap, the LiteLLM advisory |

**Raw passes are evidence, not authority.** Where they and the build plan disagree, `main` wins, and
the build plan records why.

---

## 15. What we are not building

- **A GPU in the cloud build.** The cost story depends on it.
- **Kubernetes, or managed containers by default.** One instance, one compose file, both builds.
- **A managed knowledge base.** It would fork the retrieval path and leave the audit sink.
- **Multi-tenancy.** One deployment per client is what makes compliance and "you can fire us" simple.
- **Agent swarms.** One runtime, sub-agents at depth one.
- **Our own model, or our own chat engine.**
- **A usage-based price component.**

---

## 16. The first ten minutes of the build session

1. State the account and the instance before anything else. The build happens in **878451098165**
   (Office Inference), on a new **m7i.xlarge**.
2. Decide whether the existing t3.large box is reused or a fresh instance is launched, and say which.
3. Render a manifest and prove the gateway produces a model list. That single check would have caught
   the empty-model-list defect before anything was built on top of it.
4. Run `python3 tests/test_audit_verify.py` and `python3 scripts/validate_manifests.py`. Both must
   pass before new code lands.
5. Start C0.
