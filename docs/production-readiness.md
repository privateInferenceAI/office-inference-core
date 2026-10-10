# Production readiness

What has to be true before this system can be sold, deployed, and supported. Written 2026-10-10.

**Why this document exists.** Decisions made against a development box's limits quietly become
product limits. This is the list that catches those, and the place where "we will get to it" is
either written down with a reason or removed on purpose.

**Status key:** done · partial · missing

---

## 1. Hardware: what each deployment tier actually needs

The GPU question has a short answer. **The only genuine GPU consumer is local model inference.** If
inference runs in the cloud provider's account, the whole stack is CPU, and that changes the cost of
the cloud tiers by an order of magnitude.

| Component | Bound by | Detail |
|---|---|---|
| Local LLM inference | **GPU, VRAM** | The one true GPU hog |
| Vision document reader | GPU | Second model on the same card |
| Voice ASR and TTS | GPU, small | 4 to 8 GB for quality worth shipping |
| Embeddings, bge-m3 | GPU preferred, CPU viable | 568M parameters, tens of ms per chunk on CPU |
| Reranker, bge-reranker-v2-m3 | GPU preferred, CPU viable | Same size class |
| Docling: layout, tables, OCR | **CPU and RAM** | Torch on CPU. Slow, no GPU required |
| Qdrant | **RAM** | Vectors are served from memory |
| Postgres, LiteLLM, app, n8n | CPU, modest | |

### Reference configurations

| Deployment | Instance | Why |
|---|---|---|
| **Development and demo (now)** | `g6e.4xlarge`: 16 vCPU, 128 GB RAM, 1 x L40S 48 GB | One card runs a 32B-class model at FP8 plus embeddings and reranking. 16 vCPU keeps Docling, OCR, Qdrant, and Postgres from starving. 128 GB holds a real index. About 4.50 per hour, covered by credits |
| Minimum viable dev | `g6e.2xlarge`: 8 vCPU, 64 GB, 1 x L40S | Existing box. Fine for inference, cramped for ingestion |
| Large-model dev, or a 4-card demo | `g6e.12xlarge`: 48 vCPU, 192 GB, 4 x L40S 192 GB | Runs a 235B-class model or several models at once. Uses the full 48 vCPU GPU quota, so one at a time |
| **Client cloud deployment** | **No GPU.** 8 to 16 vCPU, 32 to 64 GB RAM | Inference is Bedrock in their account. Add a small GPU only if they want local embeddings or local OCR |
| **Client office deployment** | 1 x 48 GB GPU entry, 1 x 96 GB recommended, 4 x 96 GB for a 235B analyst | This is the price of the in-office promise |

**Consequence for pricing.** The cloud tiers carry no GPU cost. Their infrastructure is a modest
instance plus tokens. The office tier is where hardware appears in the quote, and it should be priced
as the premium product it is.

### Why a demo box has a GPU when the cloud product does not

This looks inconsistent until you name which product is being demonstrated.

- **The in-office demo needs the GPU**, because the product's entire claim is that the model runs in
  the building. There is no way to demonstrate that claim on a machine that has to reach out for
  inference.
- **The cloud demo needs no GPU at all**, because inference is Bedrock in the client's account. Running
  that demo on a cheap CPU instance is not a compromise, it is the demonstration: this is what your
  bill looks like, and there is no hardware to buy.
- **One GPU box can host both demos.** It runs the office configuration with local models, and it runs
  the cloud configuration with the GPU sitting idle. During a cloud demo the idle card is worth
  mentioning out loud, because "we are demonstrating this on a machine that does not need a GPU" is a
  stronger statement than any slide.

**Which lanes exist per profile** is a design question rather than a hardware one, and the matrix is
in `docs/architecture.md`, under Lanes. The short version: office and hybrid deployments can run both
lanes, a pure cloud deployment runs the frontier lane only, and adding an office box to a cloud client
produces the hybrid configuration in the same account with nothing to migrate.

**No GPU does not mean a small instance.** Ingestion, the vector store, and the database are real CPU
and RAM work in both tiers. A cloud deployment still wants 8 to 16 vCPU and 32 to 64 GB of RAM for
Docling, Qdrant, Postgres, and the application. What it does not want is a card.

### When the cloud tier does want a GPU, optionally

**Cost tiering is not on this list.** A cheap model for easy work, with the expensive model reserved
for hard work, comes from Bedrock small models and needs no card at all. Nova Micro runs at roughly
0.035 and 0.14 USD per million tokens, so a workload of tens of thousands of light queries a month
costs about a dollar. A cloud deployment that routes well spends far less than one that sends every
request to a frontier model, and neither needs a GPU. See the Lanes section in `docs/architecture.md`.

| Situation | Why | Sold as |
|---|---|---|
| Heavy scanning volume | Docling's layout, table, and OCR models run far faster on a GPU. Tens of thousands of pages is a different problem from hundreds | An add-on to the build |
| Embeddings must not go to the model provider | Bedrock embeddings keep the text inside the client's own account under their agreement, which is usually acceptable. If they refuse even that, local TEI needs a small card to be quick | An add-on to the build |
| A local model for classification or redaction | Some clients want a cheap local model in front of the frontier call | An add-on to the build |

None of these are requirements. All of them are reasons a cloud quote might include a small GPU, and
saying so up front is better than discovering it mid-project.

### Voice: what it needs, by profile

Voice is a cascade of three stages: speech to text, then the model, then text to speech. Each stage
can run on hardware the client controls or as a provider service, and that choice is the whole
question.

| Stage | Office build, local | Cloud build, provider | Cloud build, local audio |
|---|---|---|---|
| Speech to text | Whisper-class on the GPU | Transcribe, or the provider's equivalent | Whisper-class on a GPU |
| The reply | Local 8B-class model on the GPU | Bedrock, cheap model for the front desk | Bedrock |
| Text to speech | Piper or Kokoro, CPU is fine | Polly, or the provider's equivalent | Local, CPU is fine |

**"Local audio" means the speech stages run on the client's hardware, so the audio never leaves.**
That is a boundary choice, the same one the text has. In a cloud deployment, sending audio to
Transcribe crosses no boundary that the text has not already crossed, in the same account under the
same agreement.

**Yes, conversational voice needs a GPU, but only on the office build.** The reason is latency, and
the budget is unforgiving:

| Figure | Conversational | Noticeable | Broken |
|---|---|---|---|
| Time to first spoken reply | under 1 second | 1 to 2 seconds | over 2.5 seconds |

What that means in practice, as typical figures rather than measured ones:

- **Speech to text.** Whisper-class on a GPU transcribes a five-second utterance in a few hundred
  milliseconds. The same model on eight vCPUs runs at roughly real time or worse, so a five-second
  question takes seconds to become text.
- **The reply.** A local 8B model on a 48 GB card generates a short spoken answer in a few hundred
  milliseconds. The same model on CPU runs at single-digit tokens per second, so a thirty-token reply
  takes four to six seconds. That alone ends the conversation.
- **A Bedrock cheap model** answers in a few hundred milliseconds, which is why the cloud build has
  conversational voice without any card at all.
- **Text to speech.** Piper and Kokoro are built for CPU and start speaking in tens of milliseconds.
  Premium cloud voices sound better and cost per character.

**On one 48 GB card, the whole voice stack fits comfortably:** an 8B front-desk model at FP8 around
8 GB, Whisper-class speech recognition around 3 GB, and local speech synthesis around 1 to 2 GB. Voice
is not the reason to buy a bigger card. It is the reason the front-desk model has to stay **resident**
rather than being swapped in on demand, because a cold load is several seconds of silence.

**What actually makes conversation feel good is software, not VRAM.** Streaming at every stage, so
partial speech becomes an early reply instead of waiting for the full sentence. Endpointing, so the
system knows when somebody stopped talking. Barge-in, so a person can interrupt. Products in this
space fail on endpointing far more often than on model quality. Streaming is currently item 2.7 on
this list and is not built.

---

## 2. Inference and serving

| # | Item | Status | What it means |
|---|---|---|---|
| 2.1 | **vLLM on the GPU tier** | missing | llama.cpp is the CPU development path. Production local inference needs vLLM for batching, paged attention, and tensor parallelism. Until then the demo path and the shipping path differ |
| 2.2 | **TEI CUDA images** | missing | Embeddings and reranking currently run the CPU build. Same HTTP interface, different tag, selected by profile |
| 2.3 | Model roles read from the manifest | partial | `ask.py` hardcodes the analyst role. Roles are the product's model-swap seam and must come from configuration |
| 2.4 | Quantization strategy, measured | missing | FP8 versus 4-bit is a quality decision, and it needs the parity suite to be a measured one |
| 2.5 | Context and KV budgeting | partial | Long-context agent runs need an explicit KV budget per model role |
| 2.6 | Timeouts, retries, queueing | missing | A slow model must not become a hung request |
| 2.7 | Streaming responses | missing | Server-sent events. A 30-second silent wait is unusable in an interface |
| 2.8 | Fallback path | missing | Local model unavailable, frontier lane permitted: what happens, and is it recorded |

## 3. Documents and retrieval

| # | Item | Status | What it means |
|---|---|---|---|
| 3.1 | **Docling with OCR, tables, and layout** | missing | Reverses the cut made for a 2 vCPU box. Scanned documents, fee schedules, statements, and two-column pages are common in the target verticals, and tables are the dangerous case: a token stream can produce a confidently cited wrong number |
| 3.2 | Vision reader for drawings and images | missing | The `reader` role exists for this. Machine shops and scanned exhibits need it |
| 3.3 | **Hybrid search** | missing | Vector search alone is weak on exact identifiers: procedure codes, statute sections, names, account numbers. Legal, medical, and accounting are full of them. Merge sparse or full-text results before reranking |
| 3.4 | Section-aware chunking | partial | Fixed character windows can split a table mid-row. Chunk on structure where the parser provides it |
| 3.5 | Rerank window sizing | partial | 20 candidates is thin. Production reranks 50 to 100 down to a handful |
| 3.6 | **Citation verification** | missing | The model is instructed to cite; nothing checks that `[7]` exists or that the passage supports the sentence. "Show your work" is the trust story, so it has to be enforced, not requested. Cheap first step: reject citations outside the retrieved set |
| 3.7 | **Document versioning** | missing | Upload two versions of a policy and both stay indexed. The model can cite the superseded one, confidently. Needs supersession metadata and a current-version preference |
| 3.8 | Duplicate detection | missing | The same document under two names produces two answers and wastes a rerank slot |
| 3.9 | Per-user and per-document permissions | missing | Access is folder-based with two roles. Groups and per-document grants come up in every serious evaluation |
| 3.10 | More formats | partial | PDF, Word, html, text, csv today. Email, spreadsheets with real table parsing, and slides will appear |
| 3.11 | Large-corpus behaviour | missing | Index parameters, quantization, and sharding are unmeasured past a few thousand chunks |

## 4. Security and identity

| # | Item | Status | What it means |
|---|---|---|---|
| 4.1 | **Authentication** | missing | The role is a command-line flag today. Anyone who can run the tool can claim to be an admin. The role must come from an authenticated session, and the retrieval layer must refuse a caller-supplied role |
| 4.2 | **Scoped virtual keys and budgets** | missing | The tools use the LiteLLM master key. The key hierarchy was designed and then bypassed. Per-role keys with budgets, and no master key outside administration |
| 4.3 | Secrets management and rotation | partial | A mode-600 env file generated by the renderer. Needs SOPS or Secrets Manager, plus a rotation procedure |
| 4.4 | TLS | missing | Nothing is encrypted in transit today, including the interface. Fine on a laptop, not on a client network |
| 4.5 | PII redaction on outbound frontier calls | missing | Designed in the lane policy, not built. The frontier lane is the one place data leaves, so the redaction step is the control that makes it defensible |
| 4.6 | Audit retention and purge | partial | The sink is append-only with a verified export. Retention enforcement and the planned purge are not implemented |
| 4.7 | Offsite audit copy | missing | The export exists; nothing ships it anywhere durable |
| 4.8 | Vulnerability management | partial | We learned the LiteLLM lesson the hard way. Needs scheduled image scanning, an SBOM, and a CVE watch that is a task rather than a good intention |
| 4.9 | Penetration test and threat model | missing | Once there is an HTTP surface, before the first regulated client |
| 4.10 | Incident response and breach notification | missing | HIPAA gives 60 days for notification; the runbook and the clock both need to exist before they are needed |

## 5. Operations

| # | Item | Status | What it means |
|---|---|---|---|
| 5.1 | Backups and a tested restore | missing | Backups that have never been restored are a belief, not a control. Needs a restore drill on the calendar |
| 5.2 | Monitoring and alerting | missing | Health, disk, GPU, queue depth, error rate, and the alert that fires at 3am |
| 5.3 | Log aggregation and retention | missing | Container logs to disk is not a log strategy |
| 5.4 | Signed updates with rollback | missing | The office box is an appliance. It must update itself and be able to undo a bad one |
| 5.5 | Drift detection | missing | The box reports its manifest and image digests, so "what is running at that client" is answerable |
| 5.6 | Support access with consent | partial | SSM works and is the right pattern. It needs a logged, consented process rather than an ad-hoc session |
| 5.7 | Cost monitoring and budgets | missing | Per-account budget alarms, and credit-expiry visibility |
| 5.8 | Capacity and usage reporting | missing | Per-client token and storage reporting, both for billing and for the conversation about growth |

## 6. Quality and engineering

| # | Item | Status | What it means |
|---|---|---|---|
| 6.1 | **Golden questions and the parity suite** | missing | No retrieval or answer quality is measured. Without it, a model swap, a chunking change, or a parser upgrade is faith. This is what makes "models are config" true rather than aspirational |
| 6.2 | Unit and integration tests | missing | Chunking, ACL filtering, citation assembly, and the audit chain all deserve tests that run without a GPU |
| 6.3 | CI | missing | Lint, test, validate the manifests, build the images, on every push |
| 6.4 | Acceptance test per deployment | missing | A short, runnable check that a given deployment meets what was sold: the cited answer works, the denial is logged, the backup restores |
| 6.5 | Load testing | missing | Concurrent users, a large corpus, and an ingestion running at the same time |
| 6.6 | Dependency policy | partial | Images are digest-pinned with versions recorded, after learning that lesson. Needs an update cadence |

## 7. Commercial and legal

| # | Item | Status | What it means |
|---|---|---|---|
| 7.1 | Model and component licensing | partial | n8n's licence likely needs Enterprise for a paid hosted service. Model licences vary, and Kimi K3's is revenue-tiered. Recheck before shipping any new model |
| 7.2 | Client agreements | missing | Service agreement with the data and suitability clauses, plus a BAA template for regulated work |
| 7.3 | Support terms | missing | Hours, response window, what counts as a change versus operations |
| 7.4 | Onboarding checklist | missing | Exactly what we need from a client: documents, accounts, network access, a named contact |
| 7.5 | Offboarding and handover package | missing | What they keep if they leave, and how the system keeps running without us |
| 7.6 | Insurance | missing | Errors and omissions, plus cyber, before the first regulated client |

---

## 8. Deliberately not needed

Written down so nobody builds it by reflex.

- **A GPU for the cloud tiers.** Inference is Bedrock in the client's account.
- **Kubernetes.** One host per deployment, plus a tool container. Revisit only when there are several
  services that need independent scaling.
- **A multi-tenant platform.** One deployment per client is what makes compliance and "you can fire
  us" simple. Multi-tenancy is a hosted-tier question, and the hosted tier is prepaid and optional.
- **Our own model.** Models are configuration.
- **Our own chat engine.** A lab console and a small client app, nothing more.
- **Agent swarms.** One agent with sub-tasks, as decided in ADR 0002.

---

## 9. Order of work

| Step | Work | Unlocks |
|---|---|---|
| 1 | Reference deployment on a GPU instance; vLLM and TEI CUDA behind a GPU profile | A demo that matches what ships |
| 2 | Docling with OCR and tables | Scanned and structured documents, the two gaps that lose deals |
| 3 | Hybrid search and citation verification | Answers people trust, and the ability to prove a citation |
| 4 | Authentication and scoped keys | The access-control claim becomes true |
| 5 | Golden questions and CI | Quality measured rather than believed |
| 6 | Backups, monitoring, signed updates | Deployable to a client without heroics |
| 7 | Agreements, onboarding, offboarding | Sellable and supportable |
