# The system we sell, and how to build it: cloud first

Merged 2026-10-10 from four independent design passes, checked against live AWS APIs where a claim
could be checked. This is meant to be built from, not discussed. Where the passes disagreed, the
decision is made here and the argument is given.

**Cloud first, on purpose.** One CPU instance, Bedrock for inference, no GPU. Ironing out problems on
cheap hardware produces the cloud demo and the cloud product. The office build reuses every component
and swaps the substrate.

---

## 1. The product, in one page

| Offering | Runs in | Inference | Notes |
|---|---|---|---|
| **Standard Cloud** | The client's AWS account | Bedrock, two cost tiers | No compliance claim |
| **Compliant Cloud** | The client's AWS account | Bedrock, compliance-grade services | Same architecture, four controls on |
| **Standard Cloud Hosted** | Our account | Bedrock | Prepaid annual. Refuse below 10 seats |
| **In-Office** | Client hardware | Open weights locally | GPU. Nothing leaves the building |
| **Hybrid** | Both | Both | A configuration, not a product |

**What we charge for: the system, never the tokens.** At 4 to 110 USD a month of inference on a
properly routed deployment, a usage line item is theatre, and it invites the client to audit the one
number that does not matter.

| Offering | Build | Monthly | Year-one margin |
|---|---|---|---|
| Standard Cloud | 12,000 | 750 | 40 percent |
| Compliant Cloud | 35,000 | 1,800 | **22 percent, soft.** Go to 45,000 or narrow to one framework |
| In-Office entry | 14,000 plus hardware | 900 | **12 percent, soft.** Go to 18,000 or drop the entry build |
| In-Office 4-card | 25,000 plus hardware | 1,200 | 25 percent |
| Hosted | 2,500 setup | 600 / 1,100 / 2,400 for 5 / 10 / 25 seats | 21 / 50 / 72 percent |

Market anchors, for the first negotiation: a scoped AI pilot runs 5,000 to 15,000, a multi-workflow
build 20,000 to 80,000, agency retainers 2,000 to 15,000 a month, AI consulting 150 to 350 an hour.

**The retainer is priced below market on purpose.** 750 sits under the 2,000 floor. Keep it as an
introductory rate, write into the first contract that it moves to 1,250 after the third client, and
say so out loud rather than surprising anyone.

**Packaging changes to `packs/catalog.yaml`:**

- Bundle **document-chat**, **workflow-automation**, and **offsite-backup** into the base. Add an
  `included_in` key so the renderer can still price them.
- **Delete the frontier-lane pack.** In a cloud build it is the only lane, so selling it separately is
  selling the engine as an option. Fold it into core with `requires_profile: [cloud, hybrid]`.
- **Add a connectors pack**, priced per connector: the first two included with agentic-desk, then
  1,500 to 3,500 to build and 100 to 250 a month to maintain. This is the only line that grows without
  growing Anthony's hours.
- `compliance-pack` requires a compliance profile in the schema.
- Hold **voice** back until the voice phase ships.

---

## 2. Architecture

    Client browser
        |  HTTPS
    ALB  ->  EC2 instance (private subnet, one Auto Scaling group of size one)
                 docker compose:
                   caddy/alb-target  ingress
                   app               client interface
                   api               assistant API (sessions, roles, citations)
                   litellm           model gateway, per-role virtual keys
                   router            role and residency policy
                   qdrant            vectors
                   tei-embed         bge-m3
                   tei-rerank        bge-reranker-v2-m3
                   docs              ingestion and retrieval tools
                   agents            agent runtime
                   mcp               MCP gateway, vetted connectors
                   n8n or replacement workflows
                   audit             audit sink, the CLI
        |                |
    RDS Postgres     Bedrock (in this account)     S3 (documents, exports, backups)

| Component | Built from | Why |
|---|---|---|
| Ingress | ALB plus ACM, Caddy inside for routing | TLS terminates at the edge, nothing else listens |
| Interface | the client app from `docs/design/frontend.md` | Talks only to the assistant API, so everything behind it can change |
| Assistant API | Python service | Owns sessions, the role derived from them, citations, and lane decisions. This is the product boundary |
| Model gateway | LiteLLM | Keys, budgets, spend, and the audit hook in one place |
| Router | our policy service | Chooses by role and residency. Not by guessing |
| Vector store | Qdrant | Access labels live in the payload, so the permission filter runs inside the search |
| Embeddings and reranking | Text Embeddings Inference | bge-m3 and bge-reranker-v2-m3. On the office build the same images run with a CUDA tag |
| Ingestion | Docling plus our pipeline | OCR, tables, and layout. See section 4 |
| Agent runtime | OpenAI Agents SDK | MIT, MCP, sessions, human-in-the-loop, and a LiteLLM provider so it drives either lane |
| Workflows | n8n, or a replacement | See the licence blocker in section 10 |
| MCP gateway | ContextForge | Central tool registry, per-connector credentials, tool-level policy |
| Audit | our sink | The differentiator. Section 5 |
| Identity | Cognito, roles from the validated token | Today the role is a command-line flag. That ends here |

### The decisions that were argued

**Compute: one EC2 instance running Docker Compose, not ECS, not EKS.** Both architecture passes
converged on this independently. The deployment is single-tenant with 5 to 30 users. Compose on both
sides means the cloud and office builds differ in substrate, not in orchestration, which removes a
whole class of "works differently in the cloud" bugs. Managed containers become an upgrade for a
client who needs them, not the default. What we give up is stated plainly: a compose restart drops
connections, so there is no zero-downtime deploy. At this size that is an acceptable trade, and the
ALB keeps the outage to seconds.

**Database: RDS for PostgreSQL, single-AZ, as the default in the cloud.** The two passes disagreed,
and this is the decision. Pass 02 argued for Postgres in compose to keep both builds identical. Pass
01 argued RDS. RDS wins for three reasons: automated backups and point-in-time recovery, patching, and
a compliance story that a client's examiner can read without taking our word for it. It costs about 47
USD a month at db.t4g.medium against a roughly 212 USD instance, on a bill the client pays. The parity
argument is real but small, because the application reaches Postgres through a URL and the behavioral
difference is operational rather than structural. Compose Postgres stays the office default, and stays
available in the cloud for a cost-sensitive deployment.

**Compliance is not a second architecture.** It is the same Terraform with four things switched on:
customer-managed KMS keys, VPC endpoints so no data path crosses the internet, Object Lock on the
audit and document buckets, and the retention and eligibility rules of section 3 enforced by the
validator.

**Infrastructure as code.** Terraform owns the account: VPC, subnets, endpoints, security groups,
ALB, ACM, Cognito, RDS, S3, KMS, IAM, CloudWatch, budgets. The installer owns the instance: pull the
images by digest, render config from the manifest, start compose, run health checks, register with
SSM. A client deployment is created, updated, and destroyed by those two things and nothing typed by
hand.

---

## 3. Models and routing

**Roles, never model names, in the product.** Each role resolves to two models: the cheap tier and the
frontier tier. Verified IDs, from `bedrock:ListFoundationModels` in us-east-1 on 2026-10-10:

| Role | Cheap tier | Frontier tier | Notes |
|---|---|---|---|
| `front_desk` | `amazon.nova-micro-v1:0` | `anthropic.claude-haiku-5-5` | Greetings, routing, classification, short answers, voice |
| `analyst` | `amazon.nova-lite-v1:0` | `anthropic.claude-sonnet-5` | Document answers, drafting, agent runs |
| `reader` | `amazon.nova-pro-v1:0` | `anthropic.claude-sonnet-5` | Document reading, vision on the office build |
| `coder` | `deepseek.v3.2` | `openai.gpt-6-astra` | Scripts and structured work. Optional pack |

**The three residency rules, which are validator code and not advice.** Nearly every frontier model is
`INFERENCE_PROFILE` only and cannot be called on demand in a single Region. Only the Nova family and
DeepSeek v3.2 support `ON_DEMAND` among those checked.

1. **Refuse `global.*` profiles when `compliance.profile` is anything but `none` or `contractual`.**
   Cross-region inference means the computation may happen in another Region, even though data at rest
   stays in the source Region. **A geographic profile is not single-Region either:** a `us.*` profile
   used from us-east-1 can route across us-east-1, us-east-2, and us-west-2. That is three states.
   Acceptable for most compliance work, and not acceptable for a client with a state-specific
   commitment, so it belongs in the service agreement in plain words rather than in a footnote.
2. **A client requiring single-Region processing disqualifies every inference-profile-only model**,
   which removes Claude, GPT, Grok, and Kimi K3. It does not empty the shelf. A live
   `ListFoundationModels` in us-east-1 returns **58 ON_DEMAND models**, among them
   `mistral.mistral-large-3-675b-instruct`, `qwen.qwen3-vl-235b-a22b`, `nvidia.nemotron-super-3-120b`,
   `openai.gpt-oss-120b-1:0`, `moonshotai.kimi-k2.5`, and `zai.glm-5`, plus ON_DEMAND embeddings
   (`cohere.embed-v4:0`, `amazon.titan-embed-text-v2:0`) and a reranker (`cohere.rerank-v3-5:0`).

   **An earlier version of this plan said the lineup "becomes the Nova family and DeepSeek", which was
   wrong.** It came from checking a handful of ids and generalising to the catalog, the exact mistake
   this document exists to prevent. Residency costs some capability rather than most of it, and a
   client asking for single-Region processing should be told what they keep.
3. **Cross-check the `model_form` field against `compliance.data_residency`.** The schema already
   carries `data_residency` and nothing reads it. Now something does.

**Residency costs capability, not money.** A single-Region-only lineup prices at about 34 USD a month
for a 50-person firm against 110 for routed global profiles, because DeepSeek is 0.62 and 1.85 per
million against Sonnet's 2.00 and 10.00. In-Region frontier pricing is only a 9 percent premium over
global. So residency is a quality decision with a small price tag, and the manifest should carry the
delta so the client chooses it knowingly.

**Routing policy.** By role first, then by residency, then by escalation: start on the cheap tier, and
escalate to the frontier tier on three mechanical triggers: the task belongs to the agentic pack's
list, the request needs synthesis across more than one document, or the user explicitly asks for the
stronger model. **Nothing about a model's own judgement may move content between lanes.** An earlier
draft of this plan let the cheap tier escalate itself "for lack of evidence", which hands the data
boundary to a model's discretion. Every escalation writes an audit event with its trigger.

**The cheap tier is a cost decision and a latency decision.** Nova Micro runs at 0.035 and 0.14 per
million tokens. Haiku 5.5 runs at 1.00 and 5.00. That is about thirty times the input price and
thirty-five times the output price, so routing light work to the cheap tier saves real money, and it
also answers faster, which matters for voice and the front desk.

An earlier version of this section quoted Haiku 5.5 at 0.11 and 0.10 per million and concluded the
cheap tier was "not a savings one". The price was wrong by roughly nine times and the conclusion
followed from it. **There is no batch discount** on Sonnet 5, Sonnet
5.5, Haiku 5.5, or Opus 5.5, so batch work cannot be assumed cheaper either.

**Tokens are not the cost, the instance is.** A 20-person firm pays roughly 26 USD a month for routed
Bedrock, 212 for the instance, and 26 for the database. Inference is 3 to 17 percent of the bill.
Consequences: do not optimise routing for cost, optimise it for latency and quality. Moving down one
instance size saves about 148 USD. Sending everything to the cheap model saves about 24.

**Two more rules for the validator, from the retention question:**

- Refuse Fable models when `compliance.profile` is `hipaa` or `glba` if the reported retention
  behaviour holds: Fable 5 and 5.1 retain all traffic for 30 days with possible human review, while
  Sonnet 5, Sonnet 5.5, and Haiku 5.5 carry no retention requirement. Verify per model card, then
  encode it.
- Negotiate the account's data retention mode explicitly, so the provider enforces the boundary rather
  than our configuration being the only thing standing between a prompt and a log.
---

## 4. Ingestion and retrieval

    watch folder -> Docling (layout, tables, OCR) -> page-aware chunks
                 -> bge-m3 embeddings (TEI) -> Qdrant, access labels in the payload
                 -> hybrid search: dense plus keyword, fused
                 -> bge-reranker-v2-m3 -> citation assembly -> verifier -> answer

**Docling goes back in.** It was cut so a two-vCPU development box could keep moving. The reference
instance has 16 vCPU and 64 GB, and the target is client hardware and capable instances. OCR, table
structure, and reading order are the three things whose absence loses deals, and tables are the
dangerous one: a fee schedule read as a token stream produces a confidently cited wrong number.

**Retrieval stays ours, not a managed knowledge base.** The alternative, Bedrock Managed Knowledge
Bases, would run retrieval outside our audit sink and strand the access filter and the citation
verifier in a service the office build cannot have. One code path across both builds is worth more than
the saved plumbing.

**Hybrid search is required, not a refinement.** Vector search alone is weak on exact identifiers:
procedure codes, statute sections, invoice numbers, account numbers, patient names. Legal, medical, and
accounting documents are full of them. Implement with Qdrant prefetch plus reciprocal rank fusion, then
rerank 50 to 100 candidates down to the handful that reach the model.

**Citation verification is the product.** Three checks, in order of cost:

1. **Every citation index exists in the retrieved set.** Cheap, and it catches an invented `[7]`.
2. **The cited page matches the passage's page.** Catches an off-by-one in assembly.
3. **The sentence is entailed by the passage.** The expensive check, and the one that matters. Where a
   sentence is not supported, mark it in amber rather than deleting it, because a person needs to see
   what the model tried to say.

Record the support rate per answer, so a client can be told something true and specific: "98.4 percent
of September's sentences were supported by their citations." Published benchmarks put 50 to 90 percent
of cited model answers as not fully supported by their sources, which makes this a differentiator that
can be measured rather than claimed.

**Chunking.** Page-aware, and structure-aware where the parser provides it, so a table is not split
mid-row. Every chunk carries document id, page, access label, and content hash.

**Bulk indexing is a separate job.** A million pages is roughly 150 USD on a temporary c7i.8xlarge for
a few hours, against about nine days of pegged CPU on the production instance. Do it once, on purpose,
and terminate the instance afterwards.

---

## 5. Trust

### The audit sink

Built and working: append-only, hash-chained, with a JSONL export and a verifier. Three things are
missing, and the first is a correctness bug rather than a gap.

1. **An external anchor.** Verification recomputes the chain inside a file and compares it to the
   manifest beside it. Delete the tail, re-export, and both are consistent again. Confirmed against our
   own verifier rather than inferred. Fix: a signed checkpoint every five minutes, and a daily signed
   anchor shipped to the client's S3 bucket under Object Lock. The anchor is what turns the record into
   evidence instead of a file we control.
2. **A tombstone for the maintenance escape hatch.** Today `oi.allow_audit_maintenance` is a session
   setting anyone with database access can flip, and flipping it leaves no trace. A purge must write an
   event carrying the hash before and after, so a gap is always visible.
3. **Content mode per data class.** The record proves a call happened; it cannot show what was said.
   Store prompt and completion hashes, plus a per-data-class mode of hash, redacted, or full, chosen by
   the client and recorded in the manifest. Some clients want full auditability, some want nothing but
   a hash. It is their call, in writing.

### Identity and keys

**Roles come from the session, never a parameter.** The ALB validates a Cognito token, the assistant API
maps it to a role, and the tooling refuses a hand-typed actor outside an explicit development mode.
Today the audit CLI takes `--actor` as free text and the runbook demonstrates a hand-typed admin. No
client has seen that, and it stops before one does.

**Per-role virtual keys with budgets**, one set per deployment, enforced by the gateway rather than
alerted on. **Corrected 2026-10-14: not a hard cap.** Admission is checked against recorded spend, so
concurrent requests can pass before any is counted (open LiteLLM issues #34732, #35567). It stops a
runaway; it does not make the number unexceedable. The ceilings that hold are the egress gate and a
per-request max_tokens. The master key exists for administration and appears nowhere else.

### The refusal path

Four named reasons, each written to the same hash chain as a success, each carrying its retrieval trace
so a person can inspect why: `no_source`, `boundary`, `policy`, `insufficient_evidence`. A refusal
that is recorded and explainable is worth more in a sales conversation than an answer.

### The demo

Ask a question, show the cited page, then ask something the role may not read and show the denial in
the audit log. Then hand over real JSONL, and tamper with a copy in front of them. Nothing else in the
meeting lands as hard, and the export is the only artifact they take away.

---

## 6. Operations

**Five alarms page. Everything else is a digest.**

| Alarm | Why it pages |
|---|---|
| Chain head moved backwards | The record was altered |
| Sixty minutes with no audit event in business hours | The system is silently down, or silently unrecorded |
| Disk at ninety percent | The next write fails |
| Database down five minutes | Everything is down |
| Spend at cap | A runaway loop is burning the client's money |

**Backups and recovery.** RDS automated backups with point-in-time recovery; S3 versioning and Object
Lock for documents and exports; a restore drill in the acceptance test.

**Recovery objectives are not established yet, and the plan previously asserted numbers it had not
earned.** An earlier draft promised five minutes of data loss and one hour to recover for the cloud,
while the reliability pass, working from the same architecture, estimated eight hours and noted that
neither figure had been demonstrated. Both were guesses. There is no RTO until a restore drill
produces one, so: run the drill, record what it actually took, and only then put a number in a
contract. An availability target does not exist either, and should not appear in an agreement before
a month of measured uptime exists.

**Object Lock mode is a decision with a permanent consequence, and it is not yet made.** Compliance
mode cannot be shortened or removed by anyone, including the account root, until the retention period
expires. At 2,190 days, the figure in the example manifest, that is six years in which the bucket
cannot be deleted at all. Use **Governance** mode with a retention the client agrees to, unless their
regime demands Compliance, disclose the difference in the contract, and price the storage. A six-year
immutable audit bucket is a real line item, not a default.

**Updates.** Images digest-pinned with the version recorded beside the pin, signed releases, rollback, a
maintenance window the client picks, and a scheduled CVE watch. The LiteLLM advisory is why: we pinned
an image, recorded no version, and could not answer the first question a security review asks.

**Client boxes use SSM only, never SSH keys.** No key material in a client's hands, and every session
logged in their own account.

**No NAT gateway, nine interface endpoints, about 65 USD a month**, so the retrieval path has no route
to the internet at all. The only egress is to Bedrock, through an endpoint.

**Cost reporting has a trap.** Claude on Bedrock bills through AWS Marketplace and appears in Cost
Explorer under the model provider rather than under Amazon Bedrock. A report filtered on service equals
Bedrock shows a Claude deployment as zero. Read the provider line and the record type.

---

## 7. The quality gate

**Sixty golden questions per deployment**, forty general and twenty specific to the client's work, with
thresholds of 92 percent retrieval and 85 percent answer support.

**Four results block a release regardless of the averages:**

- an access-control leak, where a restricted passage reaches the model or the answer
- **a frontier-bound request that leaves without redaction, or carrying a data class the policy does
  not permit.** The single boundary control that had no release-blocking test
- an invented answer to a question the documents do not cover
- a numeric mismatch against the source
- a successful prompt injection from document content

Run the set on every model swap, every chunking change, and every parser upgrade. This is what makes
"models are configuration" a fact rather than a slogan, and the only way to promise a client that a
change did not make their answers worse.

---

## 8. Build sequence, cloud first

| Phase | Work | Ends with |
|---|---|---|
| **C0** | Reference instance (16 vCPU, 64 GB, no GPU), repo, compose up, manifest rendered | The stack running and healthy on CPU |
| **C1** | Gateway with per-role keys and budgets; audit sink with the anchor job | A key-scoped model call recorded in a chain with an external anchor |
| **C2** | Docling ingestion, hybrid search, reranking, citation verifier | A cited answer and a recorded refusal |
| **C3** | Assistant API: sessions, roles from tokens, streaming | Every capability over one API, no UI |
| **C4** | Client app: Ask, Do, Review, Activity | Someone who has never seen it can ask and approve |
| **C5** | Workflows and the first connectors | Invoice intake with an approval gate |
| **C6** | Agent runtime and MCP gateway | An agent task with a sub-agent and a checkpoint |
| **C7** | Ingress, TLS, Cognito, Terraform into a client account | A deployment created from code in a client account |
| **C8** | Ops: alarms, backups, restore drill, golden questions, CI | The acceptance test passes on a fresh deployment |
| **C9** | Demo rehearsal, twelve minutes, eight scenes | The demo |

C0 to C4 produce the cloud demo. Everything after makes it sellable and supportable.

---

## 9. Bill of materials

**Two passes sized this differently, and the difference is worth reconciling during C0 rather than
averaging here.** The architecture pass sizes a 10-user, 50,000-document deployment at about 635 USD a
month including nine interface endpoints, ALB, RDS, S3, KMS, Cognito, and CloudWatch. The economics
pass, assuming a leaner service set, puts a 20-person firm at about 312 USD. The gap is mostly endpoints
and managed services rather than compute. Treat 312 as the floor with no endpoints and 635 as the
compliant-ready shape.

| Deployment | Monthly run cost | Notes |
|---|---|---|
| Demo box, m7i.xlarge | about 1.61 USD per eight-hour demo day | A t3.large will stall on stage the moment Docling meets a scanned fee schedule |
| 5-person practice | 125 to 170 | |
| 20-person firm | 312 to 526 | |
| 50-person firm | 659 to 740 | |
| Compliant-ready, 10 users | about 635 | Endpoints, customer-managed keys, Object Lock |

The 5,000 USD of Activate credits covers the demo box for years. Do not optimise it.

---

## 10. Blockers and decisions that are not research

| # | Blocker | Why it stops things |
|---|---|---|
| 1 | **n8n's Sustainable Use License** | It does not clearly permit deploying n8n into a client's building as part of a paid build. Get it in writing or replace it with Windmill, Temporal, or small services we own. Blocks the workflow pack |
| 2 | **Anthropic models and the AWS BAA** | Whether Bedrock's Anthropic access sits inside the AWS BAA for PHI or needs a separate agreement. Ask AWS in writing before the first medical proposal. If the answer is no, the medical lineup becomes Nova plus DeepSeek |
| 3 | **Legal citations need counsel** | The HIPAA, SEC 17a-4, FERPA, and CMMC references came from secondary sources because the primary sites block automated reads. Do not put them in a client document unread by a lawyer |
| 4 | **Compliant Cloud pricing is soft** | 22 percent year-one margin at 35,000. Go to 45,000, or narrow to one compliance framework for the first client |
| 5 | **In-Office entry pricing is soft** | 12 percent at 14,000. Go to 18,000, or drop the entry build |
| 6 | **Per-model retention terms** | The zero-retention claim rests on Sonnet 5, 5.5, and Haiku 5.5 needing none. Verify per model card, then encode it. Fable 5 and 5.1 permit only `aws_review` or `provider_data_share`, which means instructing AWS to retain inputs and outputs for human review, and that is incompatible with Compliant Cloud |

---

## 11. What we are not building

- **A GPU in the cloud build.** The cost story depends on it.
- **Kubernetes, or managed containers by default.** One instance, one compose file, both builds.
- **A managed knowledge base.** It would fork the retrieval path and leave the audit sink.
- **Multi-tenancy.** One deployment per client is what makes compliance and "you can fire us" simple.
- **Agent swarms.** One runtime, sub-agents at depth one.
- **Our own model, or our own chat engine.**
- **A usage-based price component.** It is theatre at these volumes and invites the client to audit the
  one number that does not matter.

---

## 12. Corrections made while merging

Recorded because each would otherwise have propagated into the build.

1. **A spend figure was attributed to the wrong account.** The economics pass reported that Office
   Inference spent 30.14 USD in October on a stopped instance. Checked against Cost Explorer: Office
   Inference spent **0.01 USD**, and 30.14 is **Novus AI Labs**, a different account, which is the
   existing run rate after the cleanup. The instance in the credits account was running, not stopped.
   The conclusion, that the demo box should not be optimised, survives. The number did not.
2. **Model IDs were wrong in several places.** `claude-sonnet` and `claude-haiku` do not exist. The
   verified list is in `oi-design/05-verified-facts.md`.
3. **The database choice was contested and is decided here.** Compose Postgres keeps both builds
   identical; RDS buys point-in-time recovery, patching, and a compliance story. RDS is the cloud
   default; compose stays the office default and the cloud fallback.
4. **A geographic inference profile is not single-Region.** It routes within a geography, which for
   `us.*` means three states. That belongs in the service agreement.
5. **There is no batch discount** on Sonnet 5, Sonnet 5.5, Haiku 5.5, or Opus 5.5, and the cheap tier is
   no longer primarily a cost decision.
6. **The cheap in-Region option is narrower than it looks.** Nova 2 Lite is inference-profile only, so a
   single-Region requirement leaves the Nova v1 family and DeepSeek v3.2.
7. **The audit chain could not prove its own completeness**, and the maintenance escape hatch was
   unrecorded. Fixed in section 5 and in code.
8. **The verifier had three defects, found by an audit that broke it four ways.** It never checked a
   stored payload against its own hash, so content could be rewritten while every hash still verified.
   Its "anchor" check compared the manifest against the very row the manifest was written from, so it
   could never fail. And an empty export verified as intact. All three are fixed, with a regression
   test at `tests/test_audit_verify.py` that replays the four attacks.
9. **The single-Region claim was wrong** and was costing capability. Corrected in section 3 against the
   live catalog.
10. **Haiku 5.5's price was wrong by nine times**, and it carried a conclusion about the cheap tier.
    Corrected in section 3.
11. **The SEC retention floor was one year.** 17 CFR 240.17a-4(a) requires six. The validator now
    enforces 2,190 days for a broker-dealer profile.
12. **Twenty-one was the wrong count** of unassigned items. It was twenty-nine rows.
---

## 13. Coverage: every readiness item, and where it lands

The readiness list has 49 numbered items plus ten added by the reliability pass. This section maps
every one of them, because "we will get to it" is how things get left out. Status means: **covered**
(designed in this plan), **partial** (named but not designed), **office** (belongs to the office build,
which reuses this plan), **not needed** (deliberate), or **gap** (nothing yet, now assigned).

### Inference and serving

| # | Item | Where it lands | Status |
|---|---|---|---|
| 2.1 | vLLM on the GPU tier | Office build | office |
| 2.2 | TEI CUDA images | Office build, same interface | office |
| 2.3 | Model roles read from the manifest | C3, assistant API reads roles, not names | covered |
| 2.4 | Quantization strategy, measured | Office build, gated by section 7 | office |
| 2.5 | Context and KV budgeting | Office build | office |
| 2.6 | Timeouts, retries, queueing | **C3**, plus Bedrock throttling handling | gap, assigned |
| 2.7 | Streaming responses | C3 | covered |
| 2.8 | Fallback path when the provider is unavailable | **C3**, degraded mode with a recorded reason | gap, assigned |

### Documents and retrieval

| # | Item | Where it lands | Status |
|---|---|---|---|
| 3.1 | Docling with OCR, tables, layout | C2 | covered |
| 3.2 | Vision reader for drawings and images | Office build; cloud reads text-native only until the pack exists | office |
| 3.3 | Hybrid search | C2 | covered |
| 3.4 | Section-aware chunking | C2 | covered |
| 3.5 | Rerank window sizing | C2, 50 to 100 candidates | covered |
| 3.6 | Citation verification | C2 and section 7's quality gate | covered |
| 3.7 | Document versioning and supersession | **C2**, supersession metadata and current-version preference | gap, assigned |
| 3.8 | Duplicate detection | **C2**, near-duplicate flag at ingest | gap, assigned |
| 3.9 | Per-user and per-document permissions | C3 sessions plus group grants; folder ACLs are not enough for a serious evaluation | partial |
| 3.10 | More formats (email, spreadsheets, slides) | **C2**, format list extended and tested per client | gap, assigned |
| 3.11 | Large-corpus behaviour, index parameters | **C0** decision with the bulk-index job, measured once | gap, assigned |

### Security and identity

| # | Item | Where it lands | Status |
|---|---|---|---|
| 4.1 | Authentication, roles from a session | C7 Cognito, C3 enforcement | covered |
| 4.2 | Scoped virtual keys and hard budgets | C1 | covered |
| 4.3 | Secrets management and rotation | **C7**, Secrets Manager with a rotation procedure | gap, assigned |
| 4.4 | TLS everywhere a client touches | C7 | covered |
| 4.5 | PII redaction on outbound frontier calls | **C2**, a redaction step before anything leaves, recorded per call | gap, assigned |
| 4.6 | Audit retention and purge | C1 tombstone; retention period set per compliance profile | covered |
| 4.7 | Offsite audit copy | C1, daily anchor to the client's Object Lock bucket | covered |
| 4.8 | Vulnerability management, image scanning, SBOM | **C8** | gap, assigned |
| 4.9 | Penetration test and threat model | **C8**, before the first regulated client | gap, assigned |
| 4.10 | Incident response and breach notification | **C8**, with the notification clocks written down | gap, assigned |

### Operations

| # | Item | Where it lands | Status |
|---|---|---|---|
| 5.1 | Backups and a tested restore | C8, restore drill in the acceptance test | covered |
| 5.2 | Monitoring, the five alarms | C8 | covered |
| 5.3 | Log aggregation and retention | **C8**, CloudWatch with a retention period per client | gap, assigned |
| 5.4 | Signed updates with rollback | C8 | covered |
| 5.5 | Drift detection | **C8**, the box reports its manifest and digests | gap, assigned |
| 5.6 | Support access with consent, SSM only | C8 | covered |
| 5.7 | Cost monitoring and budgets | C8, plus the Marketplace line trap in section 6 | covered |
| 5.8 | Capacity and usage reporting per client | **C8** | gap, assigned |

### Quality and engineering

| # | Item | Where it lands | Status |
|---|---|---|---|
| 6.1 | Golden questions and the parity suite | C8, section 7 | covered |
| 6.2 | Unit and integration tests | **C1 onward**, every service ships tests | gap, assigned |
| 6.3 | CI: lint, test, validate manifests, build images | **C0**, then extended per phase | gap, assigned |
| 6.4 | Acceptance test per deployment | C8 | covered |
| 6.5 | Load testing | **C8**, concurrent users with ingestion running | gap, assigned |
| 6.6 | Dependency policy and update cadence | C8 alongside vulnerability management | partial |

### Commercial and legal

| # | Item | Where it lands | Status |
|---|---|---|---|
| 7.1 | Model and component licensing | Blockers 1 and 2 | partial |
| 7.2 | Client agreements, data clauses, BAA | Blocker 2, plus a template before the first client | partial |
| 7.3 | Support terms, hours, response window | **Before the first proposal**, written into the retainer | gap, assigned |
| 7.4 | Onboarding checklist | **Before the first client**: documents, accounts, contacts, network | gap, assigned |
| 7.5 | Offboarding and handover package | **Before the first client**: what they keep, how it runs without us | gap, assigned |
| 7.6 | Insurance: errors and omissions, cyber | **Before the first regulated client** | gap, assigned |

### Added by the reliability pass

| # | Item | Where it lands | Status |
|---|---|---|---|
| 12.1 | Clock and timezone correctness, NTP, UTC in the audit | C0, verified by a test | gap, assigned |
| 12.2 | Data residency enforced rather than documented | `scripts/validate_manifests.py`: global profiles refused outside none and contractual, Fable refused for regulated profiles, residency required for remote inference | covered, in code |
| 12.3 | Legal hold | **C1**, blocks a purge for a matter under litigation | gap, assigned |
| 12.4 | Pilot-light decision for the office box | Office build | office |
| 12.5 | Quarterly access review | **C8**, an hour, and it answers half of every questionnaire | gap, assigned |
| 12.6 | Subprocessor register | **Before the first client** | gap, assigned |
| 12.7 | Model outage drill | **C8**, exercise the throttling path | gap, assigned |
| 12.8 | Third-party breach duties in writing | **C8** with incident response | gap, assigned |
| 12.9 | Error budget reporting, monthly availability per client | **C8** | gap, assigned |
| 12.10 | A named security contact at the client and at ours | **Onboarding checklist** | gap, assigned |

### What this audit found

Twenty-nine rows were unassigned before this section. The original text said twenty-one, and the
count was wrong. Four of them would have been genuinely embarrassing to discover late: **PII redaction before a frontier call**, **document
versioning**, **incident response with notification clocks**, and **secrets rotation**. They are now
assigned to phases rather than remembered.

Three items remain deliberately office-only, because they need a GPU: vLLM, TEI CUDA, and KV
budgeting. Two more are office-only by design: the vision reader and the pilot-light decision.

**Nothing on the list is unassigned.** If that changes, it changes here.


