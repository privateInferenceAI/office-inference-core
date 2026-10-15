# Operator guide: the whole system, start to finish

For someone who has never seen this project and is comfortable in a terminal. It walks one deployment
from nothing to a working corpus, then through every tool the system has, and it tells you at each stage
how to check that the stage worked.

**This is not the manual.** `docs/manual.md` is the reference: every command, every field, every message.
This guide is the walkthrough — the order to do things in, why each step exists, and what it means when
it breaks. Where the manual says *what*, this says *why* and *in what order*.

**This is not the runbook.** `docs/runbook.md` is the build log: what was done on each date, with the
measurements recorded as they were taken. It is history. This guide is a procedure, and it is written to
be run again on a different machine.

## How to use it

Each stage ends with a **checkpoint** — one command and the output that means the stage worked. Do not
move on until the checkpoint passes. The order is not a suggestion: the stages build on each other, and
a failure caught at stage 4 takes a minute to fix while the same failure caught at stage 9 looks like a
retrieval bug.

Where a stage can destroy something, it says so before the command, not after.

## What you need before you start

| You need | Why | How to check |
|---|---|---|
| A machine running Ubuntu 24.04 with **no GPU** | The cloud build does its inference at a provider and its embedding on CPU. The office build is a different target with different hardware | `lsb_release -d` |
| **16 vCPU, 64 GB RAM, 200 GB disk** | 16 vCPU is what the parser wants; 64 GB because the parser's layout models are the largest thing in the pipeline; 200 GB because a first ingestion of a real corpus and the vector store need room | `nproc`, `free -g`, `df -h /` |
| **Docker and the compose plugin** | Everything runs in containers | `docker compose version` |
| **An AWS account** with Bedrock model access | The frontier lane calls Amazon Bedrock. Four models: three Nova and one DeepSeek in the reference deployment | The manual's section 3 lists them |
| **The repository** at `/opt/oi-core` | The path is load-bearing: the systemd unit files hard-code it and the compose file resolves mounts relative to it | `ls /opt/oi-core` |
| **A free hour** | The first ingestion of a real corpus takes minutes, and the model downloads take longer | — |
| **A subject the manifest declares** | A session names a subject, and a made-up address is refused rather than defaulted. `rendered/access.json` lists the declared ones, and Stage 13h is where you use it | `oi --profile tools run --rm session mint --dev --subject <yours> --role viewer` |

**If the box is small, stop and resize it.** This project has a rule about that, in `AGENTS.md`: ask what a
competent production team would deploy, and let that decide. A four-vCPU box will work for the fixtures and
fall over on the first scanned document, and the failure will look like a parser bug rather than a
hardware limit.

## The shape of the thing

Six containers run all the time, and the assistant API is one of them. Four tools run when you call them. Knowing which is which makes the
rest of this guide readable.

```
                    ┌──────────────────────────────────────────┐
   your question ──▶ │  retrieval (a function, not a service)   │
                    └───────────────┬──────────────────────────┘
                                    │
        ┌───────────────────────────┼───────────────────────────┐
        ▼                           ▼                           ▼
   ┌─────────┐                ┌───────────┐               ┌──────────┐
   │ vector  │                │ embeddings│               │ postgres │
   │ Qdrant  │                │ bge-m3    │               │ lexical  │
   │ chunks  │                │ on CPU    │               │ + record │
   └────▲────┘                └─────▲─────┘               └────▲─────┘
        │                           │                          │
        │      ┌────────────────────┴──────────────────┐       │
        └──────┤ ingestion worker (every 15 minutes)   ├───────┘
               └────────────────────┬──────────────────┘
                                    │
                             ┌──────▼──────┐
                             │   parser    │  documents → text with page numbers
                             │  Docling    │  OCR, tables, reading order
                             └─────────────┘
```

Long-running services, and what each is for:

| Container | Its job | If it is down |
|---|---|---|
| `postgres` | The audit record, the lexical index, the manifest, the version registry, retention policy | Nothing works. Every tool waits for it |
| `litellm` | The gateway. Authenticates the caller, enforces the budget and the model permission, writes down what the call cost | No model call gets out |
| `ingestion` | Walks the documents root, parses what changed, writes vectors and lexical rows | New documents never become searchable |
| `parser` | Turns a document into structured text with page numbers, tables and OCR | Ingestion fails on every document |
| `vector` | Holds the chunk vectors and, in each payload, the access label | Retrieval returns nothing |
| `embeddings` | Turns text into vectors, on CPU | Ingestion fails; retrieval fails |

Tools, which run only when called:

| Tool | What it does | Where it is documented |
|---|---|---|
| `audit` | Migrations, the record, export, anchor, verify, purge of old events | manual sections 13 and 14 |
| `keys` | Mints per-role API keys and their budgets | manual section 12 |
| `retention` | Deletes: one document on request, or everything a period has run out on | manual section 13f |
| `eval` | Runs the fixed question set against retrieval | manual section 13b |

**Four names for one thing, and this trips everybody.** Throughout the system the token `qwen3-14b` (in an
office build) or `amazon.nova-lite-v1:0` (in a cloud build) appears in four different shapes: the role name
a caller asks for (`analyst`), the model name the gateway maps it to, the manifest's form with a provider
prefix (`bedrock/amazon.nova-lite-v1:0`), and the bare model id in the IAM policy. An `AccessDenied` is
almost always a mismatch between the last two.

---

# Stage 0: get the repository onto the machine

**Goal:** the code is at `/opt/oi-core` and you can run it.

If this machine was built by `pathb.sh` or by hand, this stage is done. If you are bringing up a second
deployment, or restoring one, this is where you start.

```
sudo mkdir -p /opt/oi-core
sudo chown "$USER":"$USER" /opt/oi-core
git clone <the repository> /opt/oi-core
cd /opt/oi-core
```

**Why the top level of `/opt/oi-core` and not a subdirectory.** Three things assume it. The compose file
resolves service build contexts relative to itself, so moving the checkout breaks the paths. The systemd
unit files hard-code `/opt/oi-core`. And `rendered/env` is read from that directory by every tool, so a
checkout elsewhere needs `oi` redefined everywhere it appears.

**Two things the machine needs that are easy to assume are already there, and on a fresh Ubuntu 24.04 are
not.** Neither one announces itself: the guide's later commands fail in ways that look like the system's
fault rather than the machine's.

```
# 1. The compose shorthand every later stage uses. A shell alias, so it exists only in the shell you typed
#    it in — ssh host 'command', a script, and docker run do not have it.
alias oi='docker compose -f deploy/office/compose/docker-compose.yml --env-file rendered/env'

# 2. A Python environment, once, for the tests. Ubuntu 24.04 refuses a bare pip install under PEP 668,
#    and this box ships with no pip, no pytest and no venv.
sudo apt-get install -y python3-venv python3-pip
python3 -m venv ~/venv
~/venv/bin/pip install -r requirements-dev.txt
```

**Use `~/venv/bin/python -m pytest`, not a bare `python3`.** And note what happens if you skip the install:
**the test suite imports its optional dependencies in a way that skips cleanly when they are absent**, so
`pytest` passes with fewer checks than it should. A green suite on a machine with no `docling_core` and no
`psycopg` is not the same suite.

**Checkpoint.** `git -C /opt/oi-core log --oneline -1` prints a commit, `oi ps` lists containers, and
`~/venv/bin/python -m pytest tests/ -q` ends with a passing count. If the first fails, nothing later will
work and the cause is a git or permissions problem, not this system.

**How this breaks.**

- `Permission denied` on the clone: the deploy key is not installed for `root` or for the user you are.
- `fatal: not a git repository` from later commands: you are in the wrong directory. Every command in this
  guide runs from `/opt/oi-core` unless it says otherwise.

---

# Stage 1: the manifest, which is the contract

**Goal:** a file that says what this deployment is, which everything else is generated from.

A manifest is what a client buys. The models their roles use, the ceilings on their spend, what the record
keeps, and where evidence ships all come from one YAML file. Nothing in this system is configured by
editing a running box.

```
cp manifests/dev-cloud.yaml manifests/my-client.yaml
$EDITOR manifests/my-client.yaml
```

Five fields to get right, and each one has a consequence that is invisible until later:

- **`client.deployment_id`** appears in every audit row and prefixes every key alias. **Nothing enforces
  that it is unique** — no validator compares ids across manifests and nothing checks against a running
  deployment. Two clients sharing an id produce a record that cannot be separated afterwards.
- **`models.<role>.frontier`** needs the `bedrock/` prefix. The bare id belongs in the IAM policy, and a
  mismatch between the two produces an `AccessDenied` that reads like a gateway fault.
- **`packs`** decides which services exist. A pack list that never reaches `core` is refused, because it
  would leave the deployment with no gateway.
- **`access.egress.frontier_classes`** is what may leave for a frontier model. The field next to it,
  `lanes.frontier.data_classes_allowed`, declares the same thing and is read by the validator rather than
  by the gate. **Set both to the same list** — the validator warns when they disagree, and the warning
  tells you which direction the difference runs.
- **`budgets.roles`** is a hard ceiling per role per period. A remote call costs money and an uncapped role
  is how a loop empties an account.

**A field can be in the schema, pass validation, and do nothing.** The schema describes the contract we
intend; the services implement what exists. The manifest's field table in manual section 16 marks which
rows are read by nothing, and reading that column before quoting a client a feature is worth the two
minutes.

**Checkpoint.**

```
python3 scripts/validate_manifests.py manifests/my-client.yaml
```

Expect `ok   my-client.yaml  (profile=cloud, packs=[...])`. A `!` line after it is a warning, not a
failure: it means two declarations of one policy disagree. Read it and fix the manifest, because a warning
that stays becomes a warning nobody reads.

**How this breaks.**

- `FAIL` with a list: the list is the whole diagnosis. Each line names a rule and, usually, the field.
- `unknown pack ids`: the pack is not in `packs/catalog.yaml`, which means it is not sellable yet.
- `frontier lane enabled with no data classes allowed`: the lane is on and nothing may leave it, which is
  a deployment that will refuse every model call.
- `offsite.bucket ... is not a valid S3 bucket name`: S3 names are lower-case, no underscores, and this
  is caught here rather than at the first export.

---

# Stage 2: render, which turns the manifest into files

**Goal:** the files the deployment actually reads, generated from the manifest rather than typed.

```
python3 scripts/check_model_lifecycle.py
python3 scripts/render_manifest.py --manifest manifests/my-client.yaml
```

```
rendered dev-cloud.yaml -> /opt/oi-core/rendered
  profile      cloud
  packs        core
  local models (none)
  gateway      4 model entries
  secrets      reused from the existing env file
  env file     /opt/oi-core/rendered/env (mode 600)
```

Four files come out, and each has one job:

| File | What reads it |
|---|---|
| `rendered/env` | Every tool and every service. Mode 600, holds the generated secrets **and** the settings |
| `rendered/litellm.yaml` | The gateway's model list. It is mounted read-only into `litellm` |

**`check_model_lifecycle.py` runs before the render, and it is not decoration.** It checks every remote model
a manifest names against `data/model-lifecycle.json`, which records each model's status and what replaces it.
A model that is not in the table fails here and names itself. It checks all six manifests and about fourteen
remote references in a second, so there is no reason to skip it.
| `rendered/keys.json` | The per-role API keys, mode 600, written by `keys` rather than by the renderer |
| `rendered/manifest.json` | The manifest as the system sees it, for a tool that wants the parsed form |

**Two things in that output are worth reading, not skimming.** `gateway 4 model entries` is the model list
the gateway will serve, and `(none)` there means the deployment has nothing to answer with. And
`secrets reused` means the renderer did not regenerate the database password, which would lock the stack
out of its own data. On a first render it says `generated` instead, which is correct once and wrong after.

**Why rendering is a separate command from validating.** Validating answers "is this contract coherent".
Rendering answers "what files does it produce". You will render many times and validate after each change,
and keeping them separate means a render cannot silently accept a manifest that validation would refuse.

**Checkpoint.**

```
grep -E 'FRONTIER_CLASSES|EGRESS_REDACTION|ACCESS_DENIAL_MODE' rendered/env
```

Expect the three values your manifest declared. `FRONTIER_CLASSES=` empty on a cloud build means every
frontier call will be refused — that is the single most common configuration failure in this system, and
it produces a refusal that names an unconfigured deployment rather than the field you forgot.

**How this breaks.**

- `env file ... (mode 600)` and then a tool cannot read it: you are running the tool as a different user.
  Inside a container that needs `--user "$(id -u):$(id -g)"`.
- The render succeeds and the gateway still serves the old models: **a running container keeps the config
  it read at start.** Rendering does not reload anything. `oi restart litellm` is the reload.
- Secrets changed when they should not have: you deleted `rendered/env` or rendered into a different
  `--out`. The database password in the new file will not match the one Postgres was initialized with.

---

# Stage 3: bring the stack up

**Goal:** six containers running, five of them healthy, and nothing listening from outside.

Migrations first, because the tools need the schema and the workers need the tables. Then the stack.

```
cd /opt/oi-core
alias oi='docker compose -f deploy/office/compose/docker-compose.yml --env-file rendered/env'

oi run --rm audit migrate
oi up -d
oi ps
```

**The alias only exists in the shell where you typed it.** In `ssh host 'command'`, in a script, or in a
cron entry it does not expand and you get `oi: command not found`, which reads like a missing tool rather
than a missing alias. Write the compose command in full in anything non-interactive.

**What a first run does, and how long it takes.** `postgres` initialises its data directory. `parser`
downloads its layout and TableLayout models — the largest thing in the pipeline, and the slowest first
start. `embeddings` downloads bge-m3. On a 200 GB box over a good connection that is minutes; on a slow one
it is tens of minutes, and the containers look stuck because their logs are quiet while a model downloads.

**Checkpoint.**

```
oi ps
```

Expect **six** services. Four report `healthy`: `postgres`, `parser`, `ingestion`, `embeddings`. Two do
not: `litellm` and `vector` declare no healthcheck, so for those `Up` is the whole of the information.
That is correct rather than a fault, and a `ps` showing two services is a deployment that has not started
the document pipeline at all.

Then confirm from the outside that nothing is reachable:

```
docker run --rm --network ai-net curlimages/curl -sS http://litellm:4000/health/liveliness
```

Expect `"I'm alive!"`. Use `-sS`, not `-s`: bare `-s` hides connection errors, so a gateway that is down
prints nothing at all and looks like a hang.

**How this breaks.**

- `oi ps` shows a container restarting in a loop: read its logs. `docker logs <service> --tail 40`. A
  parser that cannot download its models restarts silently.
- Everything is up and `grep` on the LLM fails: the instance has no AWS credentials. The instance role is
  what makes this keyless; without it the gateway cannot reach Bedrock.
- A container is up and the healthcheck says unhealthy: for `ingestion` that means its last cycle had a
  failure, not that the process died. The distinction is deliberate — see stage 4.
- `oi up -d` prints `Container X Running` and you expected a restart: `up -d` reconciles, and "Running"
  means it decided nothing needed doing.

---

# Stage 4: give it something to read

**Goal:** a corpus on the machine, laid out so the access labels are right the first time.

Up to here the system is a stack that cannot read anything. This stage puts documents where the ingestion
worker will find them, and it is the stage with the most ways to go quietly wrong.

## 4a. Generate or obtain a corpus

Two sources, and a working corpus usually needs both.

**Generated**, which covers every format and the failure paths:

```
pip install "faker-file[docx,xlsx,pptx,odt,ods,odp,pdf]>=0.19,<0.20"
python3 fixtures/generate_corpus.py --out /tmp/client-corpus
```

```
writing Meridian Facilities Group's corpus to /tmp/client-corpus
  authored documents: 13
  faker-file documents: 13
  total files: 27
  suffixes covered: 15/15  (complete)
  read /tmp/client-corpus/PLACEMENT.md before copying anything anywhere
```

Read `/tmp/client-corpus/PLACEMENT.md` before you copy anything. It names each document's purpose and
which root it belongs under.

**Documents by the thousand, when the question is retrieval rather than formats.** This is still part of 4a, and it is a paragraph rather than a heading because **the guide has no third heading level anywhere** — twenty-three stages and eighty-one subsections, two levels. A single H3 introduced for one topic is how a document acquires a structure nobody can predict, which is the same defect as a stage numbered unlike its neighbours.

**Twenty-six documents prove the pipeline and cannot test retrieval.** A hundred questions against a corpus
that small are answered by finding one of a handful of plausible documents. Add `--volume N` for the other
question: *does it find the right document when there are thousands?*

```
python3 fixtures/generate_corpus.py --out /tmp/big --volume 10000
```

It writes N more documents **into `company/` and `executive/`**, named `vol-*`, on twenty ordinary topics of a
facilities company — boilers and fire alarm panels, waste transfer notes, access control changes. The sentences
are written and the topics carry vocabulary, because the first version filled paragraphs from `Faker.paragraph()`
and produced "Energy both office. Degree street cold." A corpus like that exercises the plumbing and gives
retrieval nothing to find.

**They go in the real roots, and that is not tidiness.** The first version wrote them to `volume/company/` and
`volume/executive/`, which reads like better organisation and would have been a silent disaster: **the access
label is the first path component**, so all ten thousand would have carried the label `volume` while this
deployment declares two roots. Indexed, unreachable by every role, and nothing reporting it. It is the same trap
4b describes for an operator adding a folder, arriving through the script instead.

**Measured, on the m7i.4xlarge deployment, 2026-10-15:**

| Step | Corpus | Time | Rate |
|---|---|---|---|
| Generate | 10,000 documents, 40 MB | 21.8 s | 460 documents/sec |
| Ingest, first 1,000 | 1,000 new of 1,026 seen | 14 m 21 s | 70 documents/min |
| Ingest, the remaining 9,000 | 9,001 new | about 2 h | 72 documents/min |

**What the arithmetic says about where the time goes, and a number I got wrong first.** I measured the
embedding service at 33 chunks per second, concluded that ten thousand documents were minutes of embedding, and
wrote that here. **It was wrong, and the ingest is what proved it.** That measurement used hundred-character
test strings; the chunks this corpus actually produces are around two thousand characters, and embedding cost
scales with length. Measured again at the real size, on the same box and while the ingest was running:

| Chunk size | Chunks per second | Thousand tokens per second |
|---|---|---|
| 200 characters | 5.5 | 0.27 |
| 2,000 characters | **2.7** | 1.33 |

**The embedding service saturates the machine.** During the ingest it sits at 1,574 percent CPU, which is
fifteen of the sixteen cores, while every other container is under 14 percent. It is the largest single
consumer by a wide margin.

**Then I made a second mistake on top of the first, and it is worth recording because it is the more common
one.** From the gap between the service's rate and the worker's, I concluded that "roughly two thirds of the
critical path is per-file work". **No measurement supports that.** The benchmarks above were run *while the
ingest was running*, so they measure the marginal capacity left over after the worker had taken its share, not
the worker's own cost. Decomposing a pipeline with numbers taken from a run that competes with it is not a
measurement.

**What is actually known:** the worker achieves about 0.8 chunks per second; the embedding service does 2.0 at
a batch of eight and 0.37 at a batch of one, under the same contention; and the worker sends one or two chunks
per request because it embeds per document. **The batch-size effect is the solid finding and it is large.**

**What is not known, and how to settle it:** how the worker's second splits between the parse round trip, the
embedding call, and the two writes. That needs a run on an idle box with per-phase timings, which is a small
instrumentation change and the right thing to do before choosing between more parallel workers and a faster
embedding service.

**A warning about benchmarks of this service.** A rate measured on short strings does not describe this load,
and the error is in the flattering direction. If you measure it, measure it at the chunk size you will actually
send.

**The fix was concurrency, not a faster machine, and it was measured.** The worker prepared one document at a
time, so while it waited on the parser the embedder was idle and the other way round. Neither service was
saturated: the parser used **about one core of sixteen**, and the box had fifteen idle throughout. The cost was
never capacity.

Documents are now prepared across a pool of `INGEST_CONCURRENCY` — four by default — and written on the main
thread in a single stream. Writes are two percent of the clock and are the part that touches shared state, so
serialising them costs nothing and keeps the manifest's bookkeeping out of a race. A failure stays attached to
its own document: each document's work is its own unit, so one bad file dead-letters itself and not its
neighbours.

**The full 10,026-document corpus, measured twice on the same box:**

| | One at a time | Four at a time |
|---|---|---|
| Wall clock | 2 h 48 m | **1 h 36 m** |
| Rate | ~59 documents/min | ~104 documents/min |
| parse | 44% of the clock | **6%** |
| embed | 53% | **93%** |

**Parsing did not get faster; it stopped being on the critical path.** It now runs on cores that were idle
while the worker waited on the embedding service. **Embedding is the remaining bottleneck at 93 percent**, and
per chunk it runs at roughly 1,430 ms against the 490 ms the service manages at a batch of eight, because a
per-document request carries one or two chunks. Batching across documents is the next lever and nobody has
built it.

**And there is no degradation to worry about.** Across ten buckets of a full run, per-document time went from
2,947 ms to 3,147 ms, a drift of **1.07x**. What looks like a run slowing down is the chunk count varying
between documents — buckets averaging one chunk take about 1,400 ms and buckets averaging two take about
3,100 ms. The rate moves between 68 and 138 documents/min for that reason and not because the index is
growing. **A short sample cannot tell you this**, which is why the full run was the right call.

**For scale, overnight is enough.** Ten thousand documents in an hour and a half means a corpus ten times that
size finishes inside a working night, on the box that is already deployed, with no second machine and no GPU.
That was the decision taken, and it is recorded here so the next person does not reopen it without new
numbers.

**Two operational notes.** Set `INGEST_EMBED_BATCH` no higher than the service's advertised limit of 32; a
batch of 64 answers `422 batch size 64 > maximum allowed batch size 32`, and a burst of 32 concurrent requests
answers `429 Model is overloaded`, so the worker's default of 16 is the right side of both.

**Do not point a normal run at a subset of the corpus, and this is here because it happened.** The worker
reconciles deletions from the manifest: a document the manifest knows about whose file is not under the root
being walked gets removed. That is correct when the root is the deployment's own `documents/` folder. It is
destructive when the root is a subset — **a measurement run against 300 sample documents removed the other
9,700**, emptying the lexical table and the manifest for a corpus that had taken three and a half hours to
build. The vectors survived only because that run also used a separate collection, which was luck rather than
design.

**The worker refuses to remove anything when the root is not its own now**, and says so. If you genuinely need
a run over a subset, note that a separate collection is not enough on its own: `chunk_index` and
`ingest_manifest` are shared, so a partial run needs its own database as well. The supported way to get a
measurement out of the worker is the timing line described above, which comes from a real run and needs no
subset at all.

**And an ingest takes as long as it takes: do not wrap it in a `timeout`.** One run of this corpus was killed
at fifty minutes by a `timeout 3000` around the command, forty per cent of the way in. The worker is
incremental, so the next run resumed rather than restarted and nothing was lost, but the only reason that is
true is the content-hash manifest. Run it detached and poll the row count.

**Real or fetched**, for documents whose awkwardness you did not choose. The fixture corpus already in the
repository is four filings from SEC EDGAR and the IRS, and `fixtures/corpus/manifest.yaml` records where
each came from with the licence read at the source. **If you add anything, add its licence the same way.**
A document whose licence was assumed rather than read is a document you may have to remove later, and this
project has been caught by that twice.

## 4b. The one rule that matters

**The top-level folder name is the access label.** Not a filing convention, not a hint — it is the value
stored on every chunk and the value the search filters on.

```
/opt/oi-core/documents/            <- the documents root on the host
  company/                         <- labeled "company"
    handbook/
      policy.pdf                   <- labeled "company", despite being two levels down
  executive/                       <- labeled "executive"
```

Four consequences, and only the first is obvious:

1. **A file under `company/` is readable by the `staff` retrieval role.** A file under `executive/` is
   readable only by `executive`.
2. **Subfolders are free and do not create labels.** Depth costs nothing and every file under a root
   carries that root's label.
3. **A folder named anything else makes its contents unreachable.** A file under `acme/` is labelled
   `acme`, no role's map contains `acme`, and **no search returns it.** It is ingested, it is paid for, and
   it is invisible. Adding a root is a change to the `ROLES` map in
   `services/retrieval/retriever.py`, not a setting.
4. **A file loose in the root takes the root's own name as its label** — `documents` — which no role's map
   contains either. Same outcome, same silence.

The generated corpus contains one file that demonstrates this on purpose:
`loose/read-me-first.md`. Copy it into `/opt/oi-core/documents/` itself, not into a folder, once the rest
is working. Watching it fail to appear in results is faster than reading the four points above.

**Why it is built this way.** The alternative — a separate configuration file mapping documents to
labels — is a second place for the mapping to drift from the files, and the failure that produces is a
document labelled one thing in the config and another in the index. Deriving the label from the folder
makes the layout the single source of truth. The cost is that renaming a folder silently relabels
everything under it, which is why re-ingestion exists.

## 4c. Copy the documents in

One class at a time, so a failure is attributable. Start with the plain documents, because they exercise
the whole path with nothing else that can go wrong.

```
cd /opt/oi-core
sudo mkdir -p documents/company documents/executive
sudo chown -R "$USER":"$USER" documents

# the plain markdown first
cp /tmp/client-corpus/company/travel-expenses-policy.md documents/company/
cp /tmp/client-corpus/company/onboarding-notes.md      documents/company/
cp /tmp/client-corpus/company/fee-schedule-2026.md     documents/company/
cp /tmp/client-corpus/executive/executive-compensation.md documents/executive/
```

**On ownership and the containers.** The worker reads the mount as an unprivileged user inside the
container. A file the host can read and the container cannot produces a failure the worker reports, which
is the good direction; the bad direction is a file written as `root` that nothing can read and no error
mentions. Give the tree to yourself and let the mounts do the rest.

Do not copy the whole corpus yet. Stages 5 to 8 add to it deliberately, one class at a time, and by the
end you will have used every document for something.

**Checkpoint.**

```
find /opt/oi-core/documents -type f | sort
```

Expect four files, two under `company` and one under `executive`. Nothing under a folder called anything
else, and nothing loose in the root yet.

**How this breaks.**

- Files are in place and the worker reports `seen 0`: the path is wrong. `--documents /documents` inside
  the container maps to `${OI_ROOT}/documents` on the host, and `OI_ROOT` is in `rendered/env`.
- A document lands in the index and cannot be found by any role: its root is not one of the labels in the
  role map. This is the failure with no error message.
- `Permission denied` from `cp`: you are not the owner of the tree. Check with `ls -ld documents`.

---

# Stage 5: ingestion, and the two cycles that matter

**Goal:** every document parsed, chunked, embedded, and written to both indexes. Then prove that a second
pass costs nothing.

```
oi up -d --build ingestion
docker logs ingestion --tail 30
```

**The worker runs on a fifteen-minute cycle** and it is the only component that writes the document
indexes. It is not a service you query; it wakes up, compares the documents root against a manifest of
what it has already seen, and does work only on what changed.

**What the log looks like, and why it looks like that.** The container runs the cycle with `--json`, so
`docker logs` holds JSON objects:

```
collection documents: 1024 dimensions, Cosine, 1131 points
{
  "seen": 12,
  "new": 0,
  "changed": 0,
  "skipped": 12,
  ...
}
```

A cycle **run by hand** prints prose instead, because the CLI defaults to the readable form. Same cycle,
same numbers, two shapes. Watch for the prose form when you expect JSON and you will think the worker
changed behaviour:

```
docker exec ingestion python3 /app/ingest.py --documents /documents
```

```
collection documents: 1024 dimensions, Cosine, 1131 points

seen 4, new 4, changed 0, skipped 0, failed 0
chunks written 61, chunks removed 0, documents removed 0
```

**The absolute numbers are yours, not these.** `seen` is how many files the worker looked at; `chunks
written` depends on the corpus and the chunker. What is *not* yours is the relationship between the fields:
on a first cycle `new` equals `seen` and `skipped` is zero, and on a second cycle that inverts. Those
relationships are what the checkpoints below test.

**Why `seen` and not a file count.** The worker counts only files whose suffix it accepts, and it ignores
the rest **without reporting them**, because a client's folder will contain a `.DS_Store` and a shortcut
and neither is an error. That is why the guide tells you to compare the right two numbers in 5b rather
than a file count against `seen`.

## 5a. The first cycle

Run it now rather than waiting for the schedule:

```
docker exec ingestion python3 /app/ingest.py --documents /documents
```

Expect `new 4`, and a `chunks written` count in the tens. The first cycle parses every document, so it is
the slow one; on a real corpus of scanned PDFs it is minutes to hours, and the healthcheck's
`start_period` is an hour for that reason.

**Checkpoint.**

```
docker exec ingestion python3 /app/ingest.py --status
```

```
last cycle at 2026-10-13T22:00:07.439054+00:00: {"seen": 12, "new": 0, "changed": 0, "skipped": 12,
"failed": 0, "chunks_written": 0, "chunks_removed": 0}
```

**Read the JSON, not the shape of the line.** It is a timestamp followed by the cycle record, not a JSON
document on its own, so piping it into a JSON parser fails. What you are looking for is `"failed": 0`.

**`--status` is what the container's healthcheck runs**, which is worth understanding: it reads the last
cycle's record rather than checking that the process is alive, and **it exits non-zero when that cycle had
a failure**. `ingestion` has no listening socket, so a healthcheck that only proved the process existed
would stay green while every document failed. This is the same design decision appearing twice: the
container reports unhealthy for a *bad cycle*, not for a dead process.

**How this breaks.**

- `collection documents: ... 0 points` on every cycle: the collection was never created. `ensure_collection`
  runs before the first document and reports the failure once rather than per document.
- A refusal naming two dimensions: the embedding contract has moved. The collection was built with one
  model and the worker is now using another, and similarity against the old vectors is meaningless rather
  than zero. Do not force past it; read manual section 13a's contract section.
- `failed` above zero with every document listed: the parser is down or unreachable. `docker logs parser`.
- Nothing happens at all and the log is empty: the worker is waiting out its interval. Run the cycle by
  hand as above.

## 5b. The second cycle, which is the actual claim

```
docker exec ingestion python3 /app/ingest.py --documents /documents
```

```
seen 4, new 0, changed 0, skipped 4, failed 0
chunks written 0, chunks removed 0, documents removed 0
```

**This is the whole performance claim of the ingestion design.** Over unchanged bytes the worker reads
each file, hashes it, matches the hash against the manifest, and writes nothing. A cycle over a client's
corpus costs one read per document rather than a parse and an embed per document, which is what makes a
fifteen-minute interval affordable.

**Checkpoint.**

```
find /opt/oi-core/documents -type f \
  -regextype posix-extended -iregex '.*\.(pdf|docx|pptx|xlsx|html?|md|markdown|txt|csv|rtf|odt|ods|odp|eml)' | wc -l
```

Compare that number with `seen`. **Compare the second number, not a plain `find | wc -l`.** A plain count
includes the `.DS_Store` and the `.msg` export the worker deliberately ignores, so it will disagree, and
chasing that disagreement wastes an afternoon. The `-iregex` matters too: the worker lowercases the suffix,
so `INVOICE.PDF` is ingested while a case-sensitive regex would not count it.

**How this breaks.**

- The regex count is higher than `seen`: a file in an accepted format was missed. Check `failed` in the
  status output, then the parser's log for that filename.
- The regex count is lower than `seen`: two paths resolved to the same relative path, or a file was added
  between the two commands.
- `failed` is above zero: those documents are dead-lettered after two attempts and **will not be retried
  until their bytes change**. That is deliberate — see stage 5c.

## 5c. The failure path, on purpose

A corpus of valid documents can never exercise dead-lettering, which is why the generated corpus contains
a file the parser refuses.

```
cp /tmp/client-corpus/company/dead-letter-fixture.txt documents/company/
docker exec ingestion python3 /app/ingest.py --documents /documents   # cycle 1: fails
docker exec ingestion python3 /app/ingest.py --documents /documents   # cycle 2: fails again
docker exec ingestion python3 /app/ingest.py --documents /documents   # cycle 3: not retried
docker exec ingestion python3 /app/ingest.py --status; echo "exit=$?"
```

**The sequence is the test.** `INGEST_MAX_FAILURES` is 2, so the first `--status` after two failures **exits
non-zero**, which is what the container's healthcheck reads: a worker whose last cycle failed reports
unhealthy rather than alive. After that the file is dead-lettered and a cycle no longer attempts it, so the
status goes back to healthy while the document stays unindexed. That is the designed behaviour and it is
easy to mistake for a fix.

```
docker exec ingestion python3 /app/ingest.py --clear-failures company/dead-letter-fixture.txt
```

`--clear-failures` is the documented way to make it try again, and it exists for a change to the failure
rules rather than a routine step. Changing the file's bytes also clears it, because the recorded failure
hash is the hash of the bytes that failed.

**Its output is not a check, and it is worth knowing that before you trust it.**

```
cleared failures on 1 document(s) matching 'company/onboarding-notes.md'. The next cycle will try them again.
```

That line prints for a document with no recorded failure at all — run it against a healthy document and it
still says it cleared one. It reports that the path matched the manifest, not that a failure was cleared,
so **do not use the count as evidence that the dead-letter state changed.** The evidence is in the next
cycle: a document that is re-attempted is a document that was cleared, and one that is still `skipped` was
not.

**Checkpoint.** After clearing, run a cycle and watch what happens to that file. It should be **attempted
and fail again**, because it is still unparseable. A file that stays `skipped` was never cleared, whatever
`--clear-failures` said.

**How this breaks.**

- Nothing ever dead-letters: check the suffix is in the accepted set. A file the worker ignores never
  reaches the parser and never fails.
- `--clear-failures` reports zero: the path does not match the manifest's. Paths are relative to the
  documents root, with no leading slash: `company/dead-letter-fixture.txt`.
- The healthcheck flaps and you cannot tell why: read `--status`. It names the failing documents.

## 5d. Re-ingestion, and when it is the answer

Three things make the worker redo a document it has already seen, and they are different in kind:

| Trigger | What it means |
|---|---|
| The bytes changed | The client edited the file. Chunk ids change because the id covers the content, and old chunks are removed before new ones are written |
| `PAYLOAD_VERSION` changed | **We** changed what a stored field means. The bytes are identical and the stored payload is stale |
| The record claims success with no chunks behind it | A false claim, re-ingested without being asked |

The second is the one to reach for when stored data looks wrong after a code change, and it is a code
edit rather than a command. `PAYLOAD_VERSION` is `payload-v4` today, and bumping it re-derives the whole
corpus on the next cycle. For the fixture corpus that is about ten minutes; for a client's it is the price
of not serving labels the code no longer produces.

**Do not reach for `PAYLOAD_VERSION` when the collection contract has moved.** Those are different faults
with different fixes: the contract is the model and its preprocessing, and a mismatch there is refused
rather than repaired, because re-embedding is a decision rather than a repair.

---

# Stage 6: retrieval, and asking a question

**Goal:** get a real answer out of the corpus, and understand what each line of the output is telling you.

**There is no ask endpoint.** The retrieval path is a function, `search()` in
`services/retrieval/retriever.py`, and the API that would expose it — sessions, caller identity, streaming —
is the assistant API, built in C3 and walked through at Stage 13h. What remains here is the probe that runs one question through the whole path, and it is how you answer
"what does the system return for this" today.

```
cd /opt/oi-core
docker compose -f deploy/office/compose/docker-compose.yml --env-file rendered/env \
  run --rm --entrypoint python -v /opt/oi-core:/w:ro -w /w eval /w/tests/fixtures/ask.py \
  -q "What is the standard labour rate per hour?" --role staff
```

**Every part of that command is load-bearing, and two of them were wrong in an earlier draft of the
manual.**

- **`compose run` rather than a bare `docker run`** supplies `DATABASE_URL`. It is not in `rendered/env`;
  the compose file assembles it from `POSTGRES_USER`, `POSTGRES_PASSWORD` and `POSTGRES_DB`, because the
  host and port belong to the compose network rather than to the deployment. **A bare `docker run` with
  `--env-file rendered/env` starts the probe with no database at all**, and its two consequences are
  silent: the lexical arm reports `0`, which looks like an arm that found nothing, and **no passage is
  marked `NOT CURRENT`, so a claim resting on a superseded document is certified as supported.**
- **`-v /opt/oi-core:/w:ro -w /w`** supplies the code. The `eval` service declares no volume, on the
  assumption that the caller passes one; overriding the entrypoint drops the implicit one, and without
  this the probe stops with `can't open file '/w/tests/fixtures/ask.py'`.

**Checkpoint.**

```
arms:     {'dense': 15, 'lexical': 3}
```

`lexical` must be a number above zero. **`lexical: 0` is ambiguous** — it is recorded both when the arm
ran and matched nothing and when it never ran for want of a database, and the key is always present. If
you see zero, check the environment before you believe the index is empty.

**Reading the rest of the output.** Five lines, each answering a question you would otherwise guess at:

- **`role` and `roots`** — the retrieval role and the folders it may read. `--role executive` widens `roots`
  to `['company', 'executive']`. A role that is not in the map exits 2 rather than defaulting, because a
  default would grant the broader of two permissions to a typo.
- **`rerank`** — the reranker's own account of itself: the depth it saw, what it cost in milliseconds,
  whether that was inside the budget, and which model did it. `reranked: false` comes with a
  `rerank_reason` rather than a silent fallback to the fused order, which is the difference between
  reporting that a control did not run and pretending the fused order was the reranked one.
- **`withheld`** — has two causes and the output does not say which. A passage the store returned that the
  role may not see is a **security fault**: the query's filter is not working. A chunk the lexical arm
  named that the vector store does not hold is a **reconciliation fault**: the two indexes have drifted.
  Non-zero means investigate, not diagnose.
- **`restricted matched`** — what a second search found outside the role's roots. This is the number a
  `counted` denial reports.
- **`score`** — the vector arm's cosine similarity, or `n/a` for a passage only the lexical arm returned.
  Not comparable across arms, which is why it prints `n/a` rather than a misleadingly small number.

**How this breaks.**

- `ModuleNotFoundError: No module named 'ingest'`: you dropped the `-v` mount.
- `KeyError: unknown role`: the retrieval role is not one the retriever knows. It knows `staff` and
  `executive`; the model roles in your manifest are a different vocabulary entirely.
- Everything returns `n/a` for scores and `lexical` is the only arm with hits: embedding is down.
  `docker logs embeddings`.
- Passages come back from the wrong document: that is a corpus or ranking question rather than a fault.
  Stage 6a is how you tell the difference.

## 6a. Reading the arms, and the one line that lies

Before the citations stage, one diagnostic deserves its own section because it is the most misread line in
the tool's output.

```
arms:     {'dense': 15, 'lexical': 6}
```

`dense` is how many the vector arm returned. `lexical` is how many the lexical arm returned. **`lexical: 0`
does not mean "the lexical arm found nothing" and it does not mean "the lexical arm did not run" — it means
one of those two, and the line cannot tell you which.**

The key is always present. It is set from the length of a list, and that list is empty both when the arm ran
and matched nothing and when it never ran at all because `DATABASE_URL` was missing. There is no
missing-key case to read.

So when `lexical` is zero and you expected hits, check the environment rather than the index:

```
docker compose -f deploy/office/compose/docker-compose.yml --env-file rendered/env \
  run --rm --entrypoint python eval -c "import os; print('DATABASE_URL set:', bool(os.environ.get('DATABASE_URL')))"
```

`False` means the arm is not running and the count is meaningless. `True` means it ran and the corpus
genuinely holds nothing exact for that question, which is a different investigation.

**Why this matters enough to have its own section.** A reviewer reading an earlier draft was told `lexical:
0` meant the arm was skipped, and the same page in another place said it meant the arm ran and found
nothing. Both readings cannot be right, and a reader who acts on the wrong one either chases a healthy
index or trusts a broken one.

---

# Stage 7: citations, and the one refusal that saves you

**Goal:** understand what the verifier refuses, and see a real document be refused for a real reason.

Every citation a model produces is checked before a reader sees it. The check is mechanical — it does not
know whether a sentence is true, it knows whether the cited passage contains the figure and whether the
document may be relied on. What it catches is the failure this whole product exists to fight: a wrong
number with a real citation, which looks exactly like a right one.

## 7a. The five outcomes

```
bash scripts/probe.sh citations_acceptance.py
```

The probe builds an answer containing four kinds of sentence and prints a verdict for each. The four
verdicts are the whole vocabulary you will meet:

| Verdict | What it means |
|---|---|
| `ok` | Every mechanical check passed. **This is not the same as true** — see 7c |
| `number_not_in_source` | The sentence states a figure the cited passage does not contain. This is the check that catches a substituted digit |
| `citation_not_found` | The sentence cites `[9]` and only five passages were retrieved, so it cites something that was never in front of the model |
| `no_citation` | The sentence carries no `[n]` marker, so none of the checks could be run on it |
| `superseded` | Every passage it cites is from a document a person has declared replaced |

**A markerless sentence is refused, not passed.** That is deliberate and it surprises people: an answer
written as clean prose with no `[n]` markers verifies as entirely unsupported. The verifier is not being
harsh, it is refusing to certify what it cannot check.

**Checkpoint.** `refusals by reason` shows at least one `number_not_in_source` and at least one
`citation_not_found`. If both counters are zero, the probe is not exercising the verifier and something
upstream has changed.

## 7b. Supersession, which is the refusal worth seeing

This is the one that requires no model at all, and it is the reason the version registry exists. Do it by
hand, because watching the marker appear is the point.

**First, look before you declare anything.** The generated corpus contains a 2025 schedule and a 2026 one.
Neither is marked, so the system treats both as usable:

```
docker compose -f deploy/office/compose/docker-compose.yml --env-file rendered/env \
  run --rm --entrypoint python -v /opt/oi-core:/w:ro -w /w eval /w/tests/fixtures/ask.py \
  -q "What is the standard labour rate per hour?" --role staff
```

Both schedules come back, with no marker on either. **Nothing is wrong**: a document nobody has ruled on is
usable, which is the permissive default and the right one, because the alternative refuses answers over a
table nobody filled in.

**Now declare the replacement.**

```
docker exec ingestion python3 /app/ingest.py --supersede company/og-fee-schedule-2025.md \
  --by company/og-fee-schedule-2026.md --note "the 2026 schedule is in force"
```

```
company/og-fee-schedule-2025.md is now superseded by company/og-fee-schedule-2026.md. An answer whose
only support is this document will be refused rather than answered.
```

**Run the same question again:**

```
[1] company/og-fee-schedule-2025.md  pages 1-1  acl=company  score=0.6367  ** NOT CURRENT, superseded by company/og-fee-schedule-2026.md
[2] company/og-fee-schedule-2026.md  pages 1-1  acl=company  score=0.6385
```

**The passage is still returned, and that is the design.** Dropping it would answer the question from a
different document with no sign that the right one existed and had been excluded — a wrong answer wearing a
correct one's clothes. The passage carries the fact instead, and the verifier acts on it:

```
--verify "The standard labour rate is 132.00 per hour. [1]"
  [REFUSED (superseded)] The standard labour rate is 132.00 per hour. [1]
      every passage it cites is from a document a person has declared replaced, so the figure may be
      real and out of date

--verify "The standard labour rate is 145.00 per hour. [2]"
  [supported] The standard labour rate is 145.00 per hour. [2]
```

**Checkpoint.** The first claim is refused with the reason `superseded` and the second is supported. The
figures are both real, both from real documents, on real pages, and neither is invented. The difference is
that one document has been replaced and a person said so.

**How this breaks.**

- The marker never appears: the declaration did not reach the registry. `--versions` lists what is
  declared, and `--status` confirms the database is reachable.
- `superseded by None`: the replacement name was not recorded. That was a real bug — `search()` read the
  registry's boolean and never its `superseded_by` column — and it is fixed. If you see it, the retrieval
  code is older than this guide.
- A claim is refused when you are sure the document is current: check the *cited passage's* document, not
  the document the question names. A question can be answered from a document you did not expect.

## 7c. What `supported` does not mean

**`supported` means every mechanical check passed. It does not mean the sentence is true.** Entailment —
deciding whether a passage actually implies a claim, rather than merely containing its numbers — needs a
model and is not built here. It arrives with the answer layer.

Two consequences worth stating plainly, because they bound what you can promise a client:

- **The figure checks have a ceiling.** They catch the malformed quarter of misreads: impossible dates,
  impossible percentages, implausible magnitudes, malformed numbers. A digit substituted in a plausible
  way — `184,500` becoming `184,900` — passes every check unless another document contradicts it.
- **The verifier's false-negative rate is not published.** The research says it must be, and that is an
  obligation rather than a fact about today.

So the honest sentence to a client is not "every answer is verified". It is **"every citation is checked
mechanically, a figure that is not in the source is refused, and a claim resting on a replaced document is
refused. Nothing here tells you a sentence is true."**

---

# Stage 8: access control, and the four ways it can look right

**Goal:** be confident that a role cannot reach a document outside its roots, and know how the system tells
a user so.

Two retrieval roles exist, `staff` and `executive`, and they map to folders. `staff` sees `company`;
`executive` sees both. **There is no manifest field for that map and no way to add a role without editing
the code**, which is a real limitation rather than a convention: per-user and per-group permissions are built and walked through at Stage 8, above.

There is a second vocabulary in this system that shares the word "role" and means something else entirely.
The model roles — `analyst`, `front_desk` and so on — decide which model answers and what the caller may
spend. They are in the manifest and they have nothing to do with document access. A search asked for
`analyst` is refused rather than guessed at, because a default would grant the broader of two permissions
to a typo.

## 8a. Prove the filter, don't assume it

```
bash scripts/probe.sh acl_probe.py
```

```
=== what is in the index, by label ===
  company      1245 points
  executive    10 points
denial mode: counted (configured counted)
```

**The `-e ACCESS_DENIAL_MODE=…` is not optional, and leaving it out is a trap.** The mode is read from the
environment **at import time**, and a bare `docker run` passes no environment at all. So the probe without
`-e` prints `denial mode: denied (configured denied)` — the *library default* rather than your deployment's
setting, which your manifest and `rendered/env` both declare as `counted`. The line is honest about the
distinction, but a reader who runs the command as written in an older draft concludes the manifest is not
being honoured. That is what happened here.

The probe then walks each role and question and prints whether the denial fired plus the disclosure a user
would see. The two figures to read are `withheld` and `restricted_best`:

- **`withheld: 0`** on every question is the filter working. It counts passages the store returned that the
  role may not see, and the query's filter clause means it should never be non-zero. **A non-zero `withheld`
  is a security fault** — the first line of defence failed and only the second caught it.
- **`restricted_best`** is the best score found among passages outside the role's roots. It is what makes
  "the answer may be incomplete" a measured statement rather than a guess.

## 8b. The direct test, which is the one that convinces people

The probe ends by searching for the restricted figures themselves:

```
=== the direct test: does a staff query reach the restricted document at all? ===
  staff search for '418,750': 10 passages, figure present False
  staff search for '62.5 percent': 10 passages, figure present False

PASSED: a staff role reaches no restricted passage by any of these questions, and an executive role reaches them
```

**A grep rather than a count, on purpose.** A count can be right while a figure still reaches a caller
through some field the count does not cover. Looking for the string does not care which field it was in.

**Checkpoint.** `figure present False` for both, and an executive role receiving its own document. If the
figure appears, stop and treat it as a disclosure: the filter is not doing its job and nothing else on the
page matters until it is fixed.

## 8c. What a denied user is told, and why it is a client's decision

The system always knows it withheld something. What it *says* is policy, declared in the manifest as
`access.denial`, and there are three defensible answers:

| Mode | What the user sees | What it costs |
|---|---|---|
| `silent` | Nothing. The answer is built from what the role can see | **An incomplete answer is indistinguishable from a complete one** |
| `denied` | That documents exist which answer this more directly, with no count | Reveals that restricted content exists |
| `counted` | The same, plus how many passages matched | Reveals existence and implies how many documents hold it |

```
-e ACCESS_DENIAL_MODE=silent
  DISCLOSURE: none, which is the silent policy working
```

**The default is `denied`, and the reasoning is the product's own.** A silent system returns a partial
answer with no way for a user to tell it from a complete one, which is precisely the confidently-wrong
failure this design exists to fight. But the trade is the client's to make: a firm that weighs the leak of
*existence* more heavily sets `silent` and gets no disclosure at all.

**This is a contract conversation, not a configuration detail.** Ask the client which they want, and record
the answer. "We told you nothing on purpose" is a defensible position; "we did not think about it" is not.

**Checkpoint.** Run the probe with `-e ACCESS_DENIAL_MODE=silent` and see the disclosure lines become
`none`. Then put it back. If silent still discloses, the environment is not reaching the process and the
mode in `rendered/env` is stale.

**How this breaks.**

- A denial appears when the client declared `silent`: `grep ACCESS_DENIAL_MODE rendered/env`. A manifest
  that declares silent with an env file that was never re-rendered still discloses.
- `withheld` above zero on an ordinary query: the store returned something the role may not see. Check the
  query body the retriever builds and confirm `filter.must` carries the `acl` clause.
- Fewer passages than the limit with nothing withheld: the overfetch is too low or the corpus is small.
- `restricted_best` is `None` for every question as `executive`: correct. Nothing is restricted from that
  role, so there is no second search to run.

---

# Stage 9: redaction, and what actually leaves the building

**Goal:** see an identifier removed on the way out, and understand the two decisions that govern it.

Everything so far has been about what the system may *read*. This stage is about what it may *send*, which
is a different question with a different control.

## 9a. The two gates

A frontier call has to pass two checks that are easy to confuse:

| Control | The question it answers | Where it is declared |
|---|---|---|
| The **egress gate** | *May material of this data class leave at all?* | `access.egress.frontier_classes` |
| **Redaction** | *What must be stripped from it before it goes?* | `access.egress.redaction` |

They fail differently and both fail closed. The gate refuses outright; redaction lets the call through with
the identifiers replaced. A deployment can permit a class and still redact inside it.

**The gate refuses rather than defaulting.** A manifest that declares nothing sends nothing off-box, and
the refusal names what *is* permitted rather than saying "denied". That is deliberate: a refusal a person
cannot act on is one they route around.

## 9b. Watch an identifier being replaced

```
docker compose -f deploy/office/compose/docker-compose.yml --env-file rendered/env \
  run --rm --entrypoint python -v /opt/oi-core:/w -w /w audit \
  /w/tests/fixtures/egress_acceptance.py
```

The probe prints the deployment's permitted classes and whether redaction is on, the request as assembled,
what would reach the frontier model, and then a class the deployment does not permit. The generated corpus
contains a document written for exactly this: `company/pii-new-starter.md` carries an SSN and a
card-shaped number in reserved test ranges.

**What to look for.** The assembled request contains the identifiers. The version that would be sent does
not. A figure such as `145.00` survives, because a figure is not an identifier — **and that is the whole
point of the pattern list being narrow.** A redactor that stripped every number would remove the answers
along with the identifiers, and the deployment would be useless in a way that looks safe.

**Checkpoint.** The probe reports redactions of the expected kinds and a count above zero, and the regulated
class is refused with no payload at all rather than a payload with a flag beside it.

## 9c. What the record keeps, which depends on the class

This is the part people find surprising, and it is worth reading twice.

```
grep CONTENT_MODE rendered/env
```

Four modes decide what the **audit record** keeps, per data class, and they are independent of whether the
call was redacted:

| Mode | What the record keeps |
|---|---|
| `hash` | A hash of the payload and nothing readable |
| `redacted` | The payload with identifiers replaced in place |
| `full` | Everything |
| `prompt` | The prompt, not the response |

**The default is `hash`, and a deployment will usually set `internal: redacted`.** That means for ordinary
internal material the record holds the actual request, with the identifiers replaced — so the record shows
what the model was asked while the thing that must not be stored is not in it.

An earlier draft of the manual said the payload was stored as a hash and would never contain text. For a
`redacted` class that is false, and it is false in the direction that matters: a reader who believed it
would promise a client something the record does not do. **The honest statement is that the content mode
decides, and you should check which mode is in force for each class before describing the record to
anyone.**

**Checkpoint.**

```
grep -E 'CONTENT_MODE|CONTENT_DEFAULT' rendered/env
```

Read the four lines and be able to say what each means for a client's material. If `CONTENT_MODE_INTERNAL`
is `full`, the record holds internal material verbatim and cannot later be purged — the audit table is
append-only and the chain covers the payload, so content stored there is content you keep.

## 9d. What cannot be purged, and why that is a design decision

The audit table is append-only. That is what makes the record worth something, and it has a consequence
that belongs in a client conversation rather than a troubleshooting section:

**Content stored in the audit record cannot be deleted later.** The purge path counts rows that quote a
document and reports them; it does not clear them, because clearing a payload without updating its hash
makes the row fail verification, and updating the hash makes the chain a record of what somebody chose to
leave in it.

So the mitigation is not to store it in the first place, which is what the `hash` and `redacted` modes are
for. A deployment that selects `full` is choosing to keep content it cannot delete, and should be told so
before it does.

---

# Stage 10: retention, holds, and the one operation that destroys

**Goal:** destroy a document on purpose, having first proved you can rehearse it.

**This is the only stage in this guide that deletes something irreversibly.** Everything before it can be
re-run. Read the whole stage before running any of it, and use a throwaway document the first time.

There are two deletion operations and they are different in kind:

| Command | What it does | Does it touch the client's file? |
|---|---|---|
| `--purge DOC_ID` | Deletes one document's chunks and index entries **on request** | **No.** The client asked us to stop holding a copy; they still have theirs |
| `--enforce DATASET` | Deletes everything a retention **period has run out on** | **Yes.** "Keep invoices for seven years" means after seven years there is no invoice |

Confusing the two is the mistake this stage exists to prevent. A job built by reusing the purge would
report success while the thing the client asked to be destroyed sat in their documents directory.

## 10a. Rehearse first, always

```
docker compose -f deploy/office/compose/docker-compose.yml --env-file rendered/env \
  run --rm retention --enforce documents --dry-run
```

```
{
  "cutoff": "2025-10-13T22:24:42.192625+00:00",
  "dataset": "documents",
  "destroyed": 0,
  "dry_run": true,
  "examined": 0,
  "expired": 0,
  "failed": 0,
  "failures": [],
  "held": 0,
  "retain_days": 365
}

destroyed 0, held 0, failed 0 (dry run, nothing was changed)
```

**`examined 0` is usually correct rather than a fault.** A deployment whose corpus was ingested recently
has nothing a 365-day period can reach. The line to read is `cutoff`: that is the date the period reaches
back to, and a cutoff that is not what the client agreed means the policy is not what you think.

**The rehearsal reports what a real run would destroy, per store, under `would_delete` rather than
`deleted`**, so a machine reading the record cannot mistake one for the other.

**Checkpoint.** The dry run exits 0, reports counts, and changes nothing. Prove the nothing: the file's
hash, the point count for that document, its `chunk_index` rows and its `ingest_manifest` row are all
identical afterwards, and `purge_records` has not grown.

## 10b. Then destroy a throwaway, and watch all four scenarios

Build a document whose only purpose is to be destroyed, age it past the cutoff, and walk the four
scenarios in order. This is the sequence the acceptance fixture automates, and doing it by hand once is
worth it.

```
cd /opt/oi-core
printf "# Disposal Fixture\n\nReference OG-DISPOSAL-4488.\n" > documents/company/og-to-be-destroyed.md
docker exec ingestion python3 /app/ingest.py --documents /documents
docker exec postgres psql -U oicore -d oicore -c \
  "update ingest_manifest set ingested_at = now() - interval '800 days' where relpath = 'company/og-to-be-destroyed.md'"
```

**The UPDATE is the fixture, and it is honest about what it is.** Age is measured from `ingested_at`, and
the alternative to aging a row is waiting 800 days or setting a retention period of minutes, which is a
policy nobody would ever set. What the run proves is the mechanism, not the arithmetic.

**Scenario 1: no policy destroys nothing.**

```
delete from retention_policies where dataset = 'documents';
-- then:
NO POLICY for documents, so nothing was destroyed. Set one with --set-retention.
```

An absent policy is not a default. Destroying on an invented number is the one mistake here that cannot be
undone, so a deployment with no policy destroys nothing and says so.

**Scenario 2: a hold beats a period.**

```
retention --set-retention documents --days 365 --legal-basis "the client agreed to 365 days"
retention --hold company/og-to-be-destroyed.md --reason "litigation in progress"
retention --enforce documents --dry-run
```

```
"destroyed": 0, "examined": 1, "expired": 1, "held": 1
destroyed 0, held 1, failed 0
```

**`expired: 1` and `destroyed: 0` together are the whole point.** The document is past its period and a
hold stopped it. The hold is checked in one query before any store is touched, because checking it per
store means a hold obeyed by the store somebody remembered and ignored by the one they did not.

**Scenario 3: a dry run changes nothing.** Release the hold and rehearse again. `examined 1`, `expired 1`,
`destroyed 0`, and the file is still on disk with its indexes intact.

**Scenario 4: the run.** Drop `--dry-run`:

```
docker compose -f deploy/office/compose/docker-compose.yml --env-file rendered/env \
  run --rm retention --enforce documents
```

```
"destroyed": 1, "examined": 1, "expired": 1, "failed": 0
destroyed 1, held 0, failed 0
```

**Checkpoint.** The document is gone from **all three stores and the filesystem**:

```
docker exec postgres psql -U oicore -d oicore -tAc \
  "select (select count(*) from chunk_index where doc_id='company/og-to-be-destroyed.md'),
          (select count(*) from ingest_manifest where relpath='company/og-to-be-destroyed.md')"
ls documents/company/og-to-be-destroyed.md
```

`0|0` and `No such file or directory`. Anything else and the destruction is partial — see 10d.

## 10c. What happens when a destruction fails part way, which it will

**This is the most important thing in the stage, and it was learned by doing it wrong.**

The order of operations inside a real run is: clear the vector store, clear the lexical index, clear the
manifest, and **then** unlink the client's file. A failure after the first three leaves **a file with no
index**, and the run reports:

```
INCOMPLETE: 1 document(s) failed. The run is recorded and re-running is safe.
```

**"Re-running is safe" is true about the stores and misleading about the file.** Fix the cause and run
again, and the second run reports:

```
"destroyed": 0, "examined": 0, "expired": 0, "failed": 0
```

Nothing failed and nothing happened, because **the document is no longer in the manifest and therefore no
longer eligible to be destroyed** — the thing that would have deleted the file has forgotten the file
exists. **The client's document is now unreachable by the system and still sitting in their folder.**

So when a run reports `failed`, do not simply fix and re-run. First establish which stores still hold the
document, then either re-ingest it and destroy it again properly, or remove the file by hand and record
that you did. This guide cannot give you a command that finishes a half-done destruction, because there
isn't one — which is itself worth knowing before you schedule this to run unattended at three in the
morning.

## 10d. If the destruction does not complete

Work down this list; each cause presents differently.

| Symptom | Cause |
|---|---|
| `destroyed: 0, examined: 0` when you expected work | Nothing is past the cutoff. Check `cutoff` and the document's `ingested_at` |
| `destroyed: 0, examined: 1, held: 1` | A hold. `--holds` lists them, including a deployment-scope hold that covers everything |
| `failed: 1` with a `PermissionError` | The container cannot unlink. It runs as the operator for exactly this reason; check `HOST_UID` in `rendered/env` matches the owner of the documents tree |
| `failed: 1` with a `TypeError` about arguments | The purge callback is not being bound with the store locations. That was a real defect and it is fixed; if you see it, the image is older than this guide |
| `INCOMPLETE` and `re-running is safe` | Read 10c before re-running |
| A document vanished from the index and the file is still there | A previous run destroyed the stores and failed on the file. Read 10c |

## 10e. Holds, and the one that covers everything

```
retention --hold company/tax.pdf --reason "litigation in progress"
retention --hold --reason "litigation in progress"      # the whole deployment
retention --holds
retention --release-hold <the id --holds printed>
```

**`--hold` with no document covers every document, including ones added later.** A hold that only covered
what existed at the time would have a gap in it, and the gap is where the document that matters ends up.
It is also the hold people forget they placed, and it is the usual answer to a purge that was refused with
no obvious reason.

**Release by the id `--holds` prints, never a number you expect.** Hold ids are assigned by the database
and are not predictable from this guide. An earlier version of the manual said `--release-hold 1`, which
releases nothing at all — the command exits 1 and prints that the hold is not in force.

**A released hold is not deleted.** The row stays with a `released_at`, because "a hold was placed and
lifted" is a fact an auditor may ask about.

## 10f. The purge, which leaves the file alone

```
retention --purge company/invoice-2024.pdf --reason "client asked" --dry-run
retention --purge company/invoice-2024.pdf --reason "client asked"
```

`--purge` clears the indexes and the manifest and **leaves the client's file exactly where it is.** The
client asked us to stop holding a copy; they still have theirs. The distinction from `--enforce` is the
whole reason both commands exist.

**Its dry run is a rehearsal like `--enforce`'s**, and until recently it was not: `--dry-run` was accepted,
documented, and ignored on this path, so the rehearsal destroyed the document for real. If you are reading
a version of the manual or the code older than that fix, do not run it.

---

# Stage 11: the audit record, and what it can and cannot prove

**Goal:** read the record, export a day, and understand exactly what a client's auditor can and cannot
conclude from it.

The record is the product's central claim. Every model call, every denial, every key minted, every export
and every purge is written to one append-only table, and each row carries a hash of the row before it. An
edit anywhere breaks every hash after it, which is what makes the record evidence rather than a log.

## 11a. Reading it

```
docker compose -f deploy/office/compose/docker-compose.yml --env-file rendered/env \
  run --rm audit list --limit 3
```

```
    id  when                         type           actor                   class          outcome  detail
    61  2026-10-13T22:39:43.340114Z  model_call     egress-acceptance       internal       ok       anthropic.claude-sonnet-4-5
    60  2026-10-13T21:10:48.985000Z  model_call     gateway-key:50b578d6    internal       ok       bedrock/deepseek.v3.2
    59  2026-10-13T21:10:47.555000Z  model_call     gateway-key:50b578d6    internal       denied   bedrock/deepseek.v3.2
```

`--type` filters, `--limit` bounds it, and the columns are the ones to quote to a client: when, what kind
of event, who, at what data class, with what outcome.

**Two conventions worth knowing before you read a sample.** A `docker compose run` prints its own preamble
above the tool's output — the `Container postgres …` lines — and every example in this guide omits it. And
the record's ids are not contiguous with the events you can see, because a purge leaves a tombstone rather
than a gap.

**Checkpoint.** `audit list` exits 0 and prints rows. If it prints nothing on a deployment you have been
using, the events are going somewhere else — check `DEPLOYMENT_ID` in `rendered/env`, because every row
carries it and a mismatch means you are reading a different deployment's id than the one writing.

## 11b. Export a day, and verify it without an anchor

```
D=$(date -u +%Y-%m-%d)
docker compose -f deploy/office/compose/docker-compose.yml --env-file rendered/env \
  run --rm audit export --date "$D"
```

```
exported 13 event(s) for 2026-10-13
  /app/exports/2026-10-13/audit-events.jsonl
  /app/exports/2026-10-13/manifest.json
  last row hash 1953fcb2e870519983f57f33ff82e9a6548cfd998b40ccf5d77f9586cf7f9c97
```

Two files, and the manifest is what makes the JSONL checkable: it records the file's own hash, its row
count and its final row hash.

**Note the path.** The export lands in `/app/exports` **inside the container**, which is
`${OI_ROOT}/exports` on the host. That distinction bites when you verify: the documented command passes
`/app/exports/...`, not `/w/exports/...`, because the audit image already mounts the exports directory
rather than the whole checkout.

```
docker compose -f deploy/office/compose/docker-compose.yml --env-file rendered/env \
  run --rm audit verify /app/exports/$D/audit-events.jsonl
```

```
ok   /app/exports/2026-10-13/audit-events.jsonl: 13 event(s), chain intact
     note: no external anchor was supplied, so completeness cannot be shown: deleting rows from the
           end and re-exporting would still verify. Pass --anchor with the file written by the anchor job
     note: chained from row 47 (72a888eda49a...), which is not in this file
```

**Read both notes, because they bound the claim.** The first says what this check does *not* prove: every
row links to the one before it, so an edit is caught, but rows deleted from the **end** leave a shorter
chain that verifies perfectly. That is what the anchor is for. The second note is the same fact from the
other side: this export starts mid-chain, so its first row points at a row the file does not contain.

**Checkpoint.** `chain intact` and an exit code of 0. Then tamper with a **copy** and watch it fail:

```
cp -r exports/$D /tmp/tamper
sed -i 's/"actor":"[^"]*"/"actor":"not-anthony"/' /tmp/tamper/audit-events.jsonl
docker run --rm -v /tmp/tamper:/app/exports \
  -v /opt/oi-core/db/migrations:/app/migrations:ro \
  --network ai-net --env-file rendered/env \
  oi-core-audit:latest verify /app/exports/audit-events.jsonl
```

```
FAIL /app/exports/audit-events.jsonl
  - line 1: row_hash does not match the row contents
  - line 2: row_hash does not match the row contents
```

**It fails from line 1 rather than at the edited row**, because changing a row breaks that row's hash and
every hash after it. One edited field turns a valid file into a file that is wrong from the first link
onward, which is the property the chain exists to provide.

**Run it against a copy, not the export.** The audit image mounts `${OI_ROOT}/exports` at `/app/exports`,
so `-v /tmp/tamper:/app/exports` replaces that mount for this one container and nothing touches the real
export.

**How this breaks.**

- `no such file`: the path is the container's. Exports are at `/app/exports`, not `/w/exports`.
- A verification failure on a file you did not touch: compare it with the manifest. A file that was
  re-exported after the manifest was written will not match it.

## 11c. The anchor, and the honest problem with it

```
docker compose -f deploy/office/compose/docker-compose.yml --env-file rendered/env \
  run --rm audit anchor
```

```
anchored 49 event(s), head 1953fcb2e870519983f57f33ff82e9a6548cfd998b40ccf5d77f9586cf7f9c97
  /app/exports/anchors/anchor-2026-10-13-00000049.json
  Ship this file off this machine. Kept here, it proves nothing.
```

**The last line is the whole design.** An anchor kept beside the database it describes can be rewritten by
whoever rewrote the database. It only proves something once it is somewhere the database owner does not
control, which is what the shipping job is for.

**Now the part you need to know before you promise it to a client.** In principle, verifying a day's export
against an anchor is what catches a deleted tail. In practice, **the two do not fit together, and this
guide will not pretend they do.**

An **export** holds one day: `export --date D` filters the chain to that date. An **anchor** records the
head of the **whole chain** and its total row count.

```
export of 2026-10-13 : 13 rows, the day's events, ending at row 49 of 49
anchor at the same time: 49 events, head = row 49
```

Run them against each other on a deployment more than a day old and the anchor check fails on a file
nobody has touched:

```
FAIL /app/exports/2026-10-13/audit-events.jsonl
  - this export holds 13 rows and the anchor records 49: the chain is shorter than it was
```

**That message accuses the client's file of having lost rows.** The file is intact. The check is comparing
a slice of the chain against the whole of it.

And the failing case is worse than the noisy one. If the anchor was written *after* the last event of the
day you are checking — an hourly anchor against a day whose traffic stopped at lunchtime — then the
anchored row is not in the export at all, and the check reports:

```
the anchored row 1953fcb2e870... is missing from this export, so rows that existed when the anchor was
written are gone
```

**Which is the language of tampering, applied to an untampered file.**

**So what is the honest position, today?**

| Claim | Holds? |
|---|---|
| Every row links to the one before it, so an edit is caught | **Yes.** Verified in this stage |
| Rows deleted from the end are caught | **Only if the anchor's head happens to be in the export.** Not in general |
| Verify-with-anchor works for a daily export | **No.** It reports a false failure on a chain older than a day |

The unit tests for this pass, because each one builds a single-day chain where the export *is* the whole
chain and the two line up. No test builds a multi-day chain, so the shape the deployment actually produces
is the one shape that is untested. That is the same pattern as the retention failures in stage 10: the code
was tested, and the gap between the test's world and the deployed world was not.

**This is recorded as an open defect rather than worked around here.** For an auditor today, the defensible
statement is the first row of that table and nothing more: the chain is intact and an edit is detectable.
Completeness against a deleted tail is not something this deployment can currently demonstrate, and saying
otherwise to a client would be promising a control that returns a false accusation.

**Checkpoint.** You can state which of those three claims you are making. If you need the third, it is not
available yet.

## 11d. Purging old events, which is a different retention

The record has its own retention, separate from documents:

```
oi run --rm audit purge --before 2026-01-01 --reason "retention: 90 days" --dry-run
```

A purge of the record writes a **tombstone** first: a row carrying the chain head before the deletion and
the head after, so the gap is explained rather than looking like tampering. The check in 11b reports the
tombstone and continues the chain across it.

**Never run this without `--dry-run` on a deployment whose record you have not exported.** The rows are
gone afterwards, and the tombstone records that they went rather than what they said.

---

# Stage 12: backup and restore, which do not exist yet

**Goal:** know what protects this deployment's data today, and what does not.

**This stage is short because the feature is not built.** There is no backup script in this repository, no
`services/backup/`, and no restore procedure. `OPS-BACKUP-001` in `docs/requirements.yaml` reads *"Backups
restore, proven by a drill"* and its status is **open**, phase C8.

That is not an oversight in the guide. It is the state of the system, and a guide that skipped the stage
would leave an operator assuming something exists that does not.

## 12a. What is protected today, and by what

| Data | Where it lives | What protects it today |
|---|---|---|
| The audit record | `postgres`, volume under `data/postgres` | The bucket it ships to, with Object Lock — **the record, not the database** |
| Documents | `documents/` on the host | Nothing. The client's own copies are the backup, which is a real answer for a purge and not one for a disk failure |
| The vector store | `data/qdrant/` | Nothing. It is derived data and can be rebuilt by re-ingesting, at the cost of the parse time |
| The manifest and the rendered env | `manifests/`, `rendered/env` | Git for the manifest; **`rendered/env` is gitignored and holds the generated secrets** |
| API keys | `rendered/keys.json` | Nothing |
| Retention policy, holds, version registry | `postgres` | Nothing |

**The one that should worry you is `rendered/env`.** It holds the Postgres password and the LiteLLM master
key, it is generated once, and it is deliberately not in git. Lose it and you cannot authenticate to your
own database or gateway; the data is intact and unreachable. **Copy it somewhere safe by hand, now**, and
treat that as the minimum viable backup until C8.

## 12b. What an operator can do today, and what it does not cover

Two things are available, and neither is a backup:

```
# the vector store is derived, so re-ingesting rebuilds it
docker exec ingestion python3 /app/ingest.py --documents /documents

# the record can be exported and shipped, which is what the bucket is for
oi run --rm audit export --date "$(date -u +%Y-%m-%d)"
```

The first is a slow rebuild rather than a restore, and it depends on the documents still being on disk —
which is exactly what a disk failure takes away. The second protects the *record* off-machine, which is the
compliance claim, and does nothing for the documents or the keys.

**A truthful sentence to a client today:** *the audit record ships continuously to write-once storage in your
account, so the evidence survives this machine; the documents and the configuration do not yet have a
backup procedure, and that arrives with C8.*

## 12c. What a restore will have to cover when it is built

Recorded here so the operator knows what to ask for, and so whoever builds it has the list:

- `postgres` — the record, the manifest, the lexical index, the version registry, holds and policy. A
  `pg_dump` and a matching `rendered/env`, or the dump is unusable
- `documents/` — the client's files, which the system does not own and should not be the only copy of
- `data/qdrant/` — derivable, so a restore may legitimately choose to re-ingest instead, and the choice
  should be deliberate because re-ingesting a scanned corpus takes hours
- `rendered/env` and `rendered/keys.json` — small, secret, and the difference between a restorable
  deployment and an unreachable one
- `manifests/` — in git already
- The **restore drill** the requirement asks for: a restore that produces a working system, with the
  elapsed time recorded as the RTO. A backup nobody has restored is a hypothesis

**Checkpoint.** You can answer two questions: where is `rendered/env` copied to, and who knows. If the
answer to the second is "nobody", that is the finding.

---

# Stage 13: the acceptance probes, in one run

**Goal:** one pass that exercises every control, so a new deployment can be judged in twenty minutes
rather than by reading.

Each probe is a script under `tests/fixtures/` that talks to the real services. **They are not unit tests**:
unit tests prove the code does what it was written to do, and these prove the *deployment* behaves. That
distinction is the reason this project was caught shipping a purge rehearsal that destroyed documents.

Run them in this order. Each prints `PASSED` or `FAILED with N problem(s)` and nothing in between is worth
ignoring.

## 13a. The four that need only the checkout

```
cd /opt/oi-core
run() { docker run --rm --network ai-net --user "$(id -u):$(id -g)" \
  -v /opt/oi-core:/w -w /w --env-file rendered/env "$@"; }

run --entrypoint python oi-core/ingestion:local /w/tests/fixtures/hybrid_acceptance.py
run --entrypoint python oi-core/ingestion:local /w/tests/fixtures/figures_acceptance.py
run --entrypoint python oi-core/ingestion:local /w/tests/fixtures/api_contract_probe.py
run --entrypoint python oi-core/ingestion:local /w/tests/fixtures/citations_acceptance.py
```

| Probe | What its `PASSED` line tells you |
|---|---|
| `hybrid_acceptance` | A bare reference is found by the lexical arm and missed without it, and fusion keeps what the vector arm found. **The last line prints `arms on:`** — on a larger corpus those counts grow, which is how you know the corpus is being searched rather than a fixture |
| `figures_acceptance` | Damaged figures are caught, intact ones are not, arithmetic confirms what the document states, and scanned and digital chunks are labelled apart |
| `api_contract_probe` | The services answer what the contract says they answer. **Its last line — "Findings are what the services answered, not what a document said they would" — is the point of the whole suite** |
| `citations_acceptance` | An invented citation is rejected, an unsupported sentence is marked and kept, and a correct sentence survives |
| `interface_probe` | **Every claim the assistant API's pages make, as seven rights.** If the manual and the service disagree, this fails, which is the point of it. Run it after any change to the API, the routing, or the budget |

And the one that needs the reranker's paid subscription rather than just the checkout:

```
run --entrypoint python oi-core/eval:local /w/tests/fixtures/rerank_acceptance.py
```

It prints the rank changes the reranker produced and its latency against the budget, and it says plainly
that ten questions cannot resolve a small effect. **A `403` naming AWS Marketplace here means the
subscription lapsed** — it is an agreement rather than a permission, and no IAM change substitutes for it.

## 13b. The ACL probe, which needs the denial mode passed explicitly

```
run -e ACCESS_DENIAL_MODE=counted --entrypoint python oi-core/ingestion:local /w/tests/fixtures/acl_probe.py
```

Without `-e`, it prints the library default rather than your deployment's mode. Stage 8 explains why.

## 13c. The retention probe, which needs more than the others

**This one wrote a file into a client's corpus and destroyed it, and it needs the eval image rather than
the retention image.** That is not obvious from any of its invocation lines, and it cost this guide two
failed attempts. Every requirement is load-bearing:

```
docker compose -f deploy/office/compose/docker-compose.yml --env-file rendered/env \
  run --rm --user "$(id -u):$(id -g)" \
  -e DOCUMENTS_ROOT=/documents -e VECTOR_URL=http://vector:6333 -e COLLECTION=documents \
  --entrypoint python \
  -v /opt/oi-core:/w -v /opt/oi-core/documents:/documents -w /w \
  eval /w/tests/fixtures/retention_policy_acceptance.py
```

| Part | Why |
|---|---|
| **the `eval` image** | The probe imports `psycopg2` and spawns `/app/ingest.py`. The retention image ships psycopg 3 and has no `ingest.py`, so it fails with `ModuleNotFoundError` |
| `--user "$(id -u):$(id -g)"` | It writes into the documents tree, which belongs to the operator |
| `-e DOCUMENTS_ROOT=/documents` | Or it writes to `/documents` and then inspects `/w/documents`, finds its own fixture missing, and reports five failures that all follow from that |
| `-v /opt/oi-core/documents:/documents` | The file half of the test |
| **not `:ro`** | The eval service mounts the checkout read-only by convention. The probe must write, so this mount cannot be read-only, and the write lands **on your real corpus** |
| `-e VECTOR_URL` and `-e COLLECTION` | The compose `eval` service does not set them; the probe's purge needs them to reach the store |

```
  vector index:  0
  lexical index: 0
  manifest:      0
  object store:  GONE
  PASSED: a period with no policy destroyed nothing, a hold beat the period, a dry run changed nothing,
  and the real run left the document gone from the vector index, the lexical index, the manifest and the
  client's own files
```

**`object store: GONE` is the assertion nothing else in this suite makes.** It is the only proof that a
retention period destroys the client's own file rather than merely hiding it.

**Checkpoint.** All five print `PASSED`, and the probe leaves no fixture behind. Confirm:

```
ls documents/company/retention-policy-fixture.md     # must not exist
docker exec postgres psql -U oicore -d oicore -tAc "select count(*) from retention_policies"
```

**A warning about running this on a client's deployment.** The probe writes a document into the corpus and
destroys it. On a deployment whose corpus is not disposable, run it on a copy, or accept that for a few
seconds a document that is not the client's exists in their folder and then is destroyed. Its `DOC_ID` is
`company/retention-policy-fixture.md`, a name no client will collide with.

## 13d. What a full pass means, and what it does not

**Five `PASSED` lines mean:** retrieval works at both arms and fuses them, the figure checks catch what they
claim to, the access filter holds, citations are refused when they are invented or superseded, and a
retention period destroys a document everywhere including the client's file.

**They do not mean the answers are true.** No probe here evaluates whether a model's prose is correct, and
stage 7c explains why the checks cannot. The suite proves the controls around the model, not the model.

---

# Stage 13h: the assistant API, which is the first thing a client actually touches

**Goal:** go from "the pieces work" to "a client application can ask a question and get a cited, permitted,
redacted answer", and know what happens when the model will not answer.

Everything before this stage exercised components. Retrieval was a function, the ACL was a filtered query, the
gateway was a curl. **C3 puts one interface in front of all of it**, and after this stage the way to ask a
question is `POST /ask` rather than a probe.

## 13h.1. Why there is an API at all, and why it has no port

Every capability the system has is reachable through this one interface, and it has no user interface of its
own. **The port is unpublished on purpose.** A phase that builds an API should not also publish a debug port,
and this project has already paid for that mistake once in another repository. A client reaches the API
through whatever the deployment puts in front of it, which is C7's work.

    oi ps api
    docker port api          # prints nothing, and that is the check

**Checkpoint.** `docker port api` prints an empty line. If it prints a mapping, something published it.

Every command below runs in a container on the deployment's own network, because the API publishes no port.
Define this once and the rest of the stage reads cleanly:

```
cd /opt/oi-core
alias oi='docker compose -f deploy/office/compose/docker-compose.yml --env-file rendered/env'
run() { docker run --rm --network ai-net --user "$(id -u):$(id -g)" \
  -v /opt/oi-core:/w -w /w --env-file rendered/env "$@"; }
ask() { run --entrypoint python oi-core/session:local /w/tests/fixtures/ask_client.py "$@"; }
```
## 13h.2. A session, which is what a request carries instead of a password

The caller does not hold a model credential and does not name a role. It holds a **session token**, which
resolves to a row holding the subject, the role and the document roots. The API holds one signing key and mints
a short-lived statement for each model call, so a leaked session token is worth one question rather than the
deployment's account.

    oi --profile tools run --rm session mint --dev \
      --subject probe-staff@officeinference.com --role viewer

It prints `token`, `subject`, `role`, `roots`, `groups` and `expires_at`. **The subject must be one the manifest
declares** — a made-up address is refused rather than defaulted, because a default here would invent an
identity. The generator's message lists the declared subjects, which is where to look when it refuses.

**Checkpoint.** The command prints a token and `roots ['company']` for a staff subject. A subject you invented
is refused, and that refusal is correct.

## 13h.3. Ask a question, and read the answer

    ask --question "What is the mileage rate for reimbursement?"

Five fields matter more than the rest, and two of them exist to stop a lie:

| Field | What it tells you |
|---|---|
| `answer` | The model's text, with `[n]` markers pointing at `citations` |
| `citations` | Document, access label and page for every passage the answer may cite |
| `model` and `resolved_model` | **The role, and the model that actually ran.** A role is a name in the manifest; the model is what answered. A client written against `model` alone cannot tell that a manifest swap took effect |
| `notice` | What the user should be told. Empty when there is nothing to say, which is the common case |
| `lane`, `escalated`, `trigger` | Where it was answered, whether a trigger moved it to a stronger model, and **which** trigger |

**Checkpoint.** A 200 with six citations, `lane: frontier`, `escalated: false`, and an empty `notice`.

## 13h.4. The three ways a request can be refused, and they are different

| What you did | What comes back | Why it is not the same as the others |
|---|---|---|
| No session, or a made-up token | 403 `refused_authority_claim` or a session error | there is nobody to answer |
| Put `"role": "executive"` in the body | 403 `refused_authority_claim`, **and a row in the chain** | a role comes from an authenticated session and never from a request field. The refusal is recorded before the error returns, which is what makes it a control rather than a message |
| Ask something no permitted passage covers | 200, an answer that says so, `denied: true` | this is not a refusal. The system answered honestly about what it could see |

Check the second one in the record rather than in the response, because that is the part that matters:

    oi --profile tools run --rm session refusals --session <the token>

**Checkpoint.** The forged-role request returns a 403 whose wording says the permission was denied **or the
resource might not exist**, and `session refusals` shows `claimed_role='executive' session_role='viewer'`.

## 13h.5. Streaming, and the redaction that has to come with it

    ask --question "What is the mileage rate?" --stream

The same request as Server-Sent Events. **Redaction on a stream is not the same problem as redaction on a
finished answer**, because a value can reach the client in a token before anything has looked at the whole
response. So the streamer holds back a window of text, redacts it, and emits only what is settled.

    event: start     the model, the role, the roots, and the citations retrieval found
    event: delta     a piece of the answer, already redacted
    event: degraded  when the model cannot answer at all, before any delta
    event: error     if something fails mid-stream. Terminal, because the status code is spent
    event: done      deltas, ttft_ms, model_first_ms, retrieval_ms, total_ms, redactions

**`degraded` is the one a client forgets to handle**, and forgetting it renders an empty answer where the
system meant to hand over the retrieved passages.

**Checkpoint.** `stream_probe` reports four rights, including `REDACTED` with the canary absent from every
frame. **Run it alone** — it is load-sensitive and will fail spuriously if other probes are running.

## 13h.6. When the model will not answer, which is the part that was hard to build

Three thresholds on one deadline anchored at the moment the client asked: first token 1.5s, stall 5s, total 30s,
with 1.2s held back so a degraded answer still fits. Retries happen **only before the first token**, because a
streamed generation is not idempotent and a retry after output has begun is billed twice.

When the model cannot answer, the response is a 200 carrying the retrieved passages and a typed reason. **A
client must branch on `kind`**, because a degraded answer and a real answer look alike otherwise:

    { "kind": "degraded", "reason": "throttled", "lane": "frontier",
      "passages": [ ... ], "attempts": 3, "threshold": "first_token" }

Proving this needs a real failure, and the honest way to produce one is a real provider throttle:

    # A burst of small concurrent calls exhausts the provider's REQUESTS-per-minute ceiling, which is far
    # cheaper to reach than the token ceiling. Ask the API while it runs.
    run --entrypoint python oi-core/session:local /w/tests/fixtures/throttle_probe.py

**Checkpoint.** While the burst runs, `/ask` returns `kind: degraded` with `reason: throttled`, `attempts: 3`,
and all three thresholds named. The audit row agrees. **`reason: unclassified` with `attempts: 2` means the
retry path failed to authenticate**, which is a defect rather than a throttle.

## 13h.7. Routing, and the boundary it keeps

Two questions with one answer each: **which model may see this**, and **which model should**. They are decided
in that order, and the order is the design.

| Trigger | How it is decided |
|---|---|
| `agentic_task` | the `task` field is on a configured list |
| `user_requested_stronger` | the `stronger` field is true |

A third was built and removed. It escalated when retrieval returned more than one document, and measurement
killed it: a plain single-fact question returned **six** documents while a genuine multi-document question
returned **four**, and no combination of count and score floor separated them. An unusable predicate that fires
on everything is worse than none, because it escalates cost silently and makes the record meaningless.

**Nothing assesses difficulty.** A question the cheap tier answers badly must not escalate on its own judgement,
and there is nothing in the path that could form one.

**Checkpoint.** A plain question returns `escalated: false` with an empty `trigger`. Then:

    ask --question "..." --task reconcile
    ask --question "..." --stronger

Each returns `escalated: true` naming **its own** trigger, and a `notice` telling the user what happened.

## 13h.8. The one-way boundary, proved rather than asserted

The gate runs before any trigger, and it decides whether content may reach a remote model at all. **A
deployment that declares no permitted classes sends nothing off-box**, and a deployment that declares some
still refuses anything outside them.

On a deployment with a local model, an escalation the gate refuses is answered locally **and announced**. On a
cloud deployment there is no local lane, so it is refused outright and the refusal is recorded.

**Checkpoint.** With a manifest whose `access.egress.frontier_classes` is empty, a `--stronger` request is
**not** escalated, and the response says why. The row in the chain carries `escalation_requested: true` beside
`lane: local`, which is the pair that proves what was asked for was not what ran.

## 13h.9. The interface test, which is the one to run after any change

    run --entrypoint python oi-core/session:local /w/tests/fixtures/interface_probe.py

Seven rights, each one a sentence the manual tells you to expect. **If the manual and the service disagree,
this fails**, which is the point of it.

    ok   ANSWER     [200] 6 citation(s), lane='frontier'
    ok   NO-JUDGE   escalated=False trigger='' notice=False
    ok   TRIGGERS   agentic->'agentic_task' stronger->'user_requested_stronger'
    ok   FACTS      the row carries asked and answered separately
    ok   TOLD       an escalation the gate refuses is announced
    ok   STREAM     a first token well before the last
    ok   DEGRADED   the last recorded degradation carries its reason, attempts and threshold

**Checkpoint.** `PASSED: 7 right(s) held against the documented interface`.

## 13h.10. Changing which model answers, which takes two restarts

    # edit models.analyst in the manifest
    python3 scripts/validate_manifests.py
    python3 scripts/render_manifest.py --manifest manifests/dev-cloud.yaml
    oi restart litellm
    oi restart api          # <-- the one people miss

**Both restarts, and the second is the one that gets forgotten.** Neither process re-reads the rendered config.
Restart only the gateway and the model genuinely changes at the provider while the API goes on reporting the old
one from the manifest it loaded at startup — the worst shape of failure, because the record is right and the
report is wrong.

Confirm it from the provider's side rather than from ours:

    docker exec postgres psql -U oicore -d oicore -c \
      'select model from "LiteLLM_SpendLogs" order by "startTime" desc limit 3'

**Checkpoint.** The spend log names the new model, and the answer's `resolved_model` agrees with it. **A swap
before the golden questions exist is unmeasured for quality** — that is C8's work, and this checkpoint proves the
mechanism rather than the answer.
## 13h.11. What C3 added, in the order it matters

| What | Why it exists | Where it is proved |
|---|---|---|
| **One interface for every capability** | A client application talks to this and nothing else. No published port: the phase that builds an API does not also publish a debug port | 13h.1 |
| **Sessions instead of credentials** | The caller holds a token that resolves to a subject, a role and a set of roots. The API holds one signing key and mints a short-lived statement per model call | 13h.2 |
| **A refusal that is recorded** | A role supplied in the request body is refused **and written to the chain before the error returns.** That ordering is what makes it a control rather than a message | 13h.4 |
| **Streaming, with redaction before the frame** | A value can reach a browser in a token before anything sees the finished answer, so the streamer holds a window back, redacts it, and emits only what is settled | 13h.5 |
| **A degraded answer instead of an error** | When the model will not answer, the client gets the retrieved passages and a typed reason at HTTP 200. Retrieval already did the expensive part | 13h.6 |
| **A data boundary that guards every remote request** | The gate runs before any trigger, and a class the manifest does not permit is refused rather than sent | 13h.8 |
| **Two escalation triggers, both mechanical** | A task list and a request field. Nothing assesses difficulty, because a question the cheap tier answers badly must not escalate on its own judgement | 13h.7 |

**One thing C3 did not finish, and you should know before promising it.** The request-side redaction module exists
and is proved by its own acceptance probe, and **the API does not call it yet.** The stream-side redaction is in
the API and runs on every frame. The control on the way out today is the data-class gate. A client asking about
confidential material is protected by the gate rather than by request redaction, and the manual says so in
section 13e rather than leaving it to be found.

**And one thing that is proved but not enforced end to end.** The gateway accepts the identity's `tpm_limit` and
`rpm_limit` now, because a limit nobody passed to the enforcer was a comment rather than a ceiling. Whether the
gateway applies them when the identity comes from a custom auth hook is **unproven** — lowering a team's limit
did not produce a refusal. Treat a declared rate limit as documentation until somebody proves it bites.
# Stage 13i: the client app, and the first thing you will be tempted to fake

**Goal:** the app running on the deployment, showing a real answer from a real document, and leaving a row in the
chain that you can find afterwards.

**Why this stage is last in the phase and first in the demo.** Everything before it is machinery a client never
sees. This is the product. It is also the stage where the temptation appears: the screens can be made to look
correct without being connected to anything, and a screenshot cannot tell the difference. So this stage ends with
three checks that a screenshot cannot pass.

## 13i.1. Put the app's checkout where the build can see it

The app is a separate repository and the build needs both trees. `OI_UI_ROOT` names the app's checkout; a
deployment that lays the two out differently sets it differently rather than failing with a missing file.

```
sudo mkdir -p /opt/oic-ui && sudo chown "$USER":"$USER" /opt/oic-ui
git clone https://github.com/privateInferenceAI/oic-ui.git /opt/oic-ui

cd /opt/oi-core
cp /opt/oic-ui/docs/openapi.json /tmp/contract-copy.json
```

**Checkpoint.** The contract in the app's checkout matches the one this deployment serves. Run the app's own
checker rather than comparing files by eye:

```
docker run --rm --network ai-net -v /opt/oic-ui:/w:ro -w /w --entrypoint bash \
  oi-core/session:local /w/scripts/check-contract.sh --live http://api:8088
```

It prints the paths and exits non-zero when they differ. **A copy nobody compares drifts**, and the drift shows
up as a screen that renders nothing.

## 13i.2. Build it, and note what the build does not contain

```
cd /opt/oi-core
export OI_UI_ROOT=/opt/oic-ui
alias oi='docker compose -f deploy/office/compose/docker-compose.yml --env-file rendered/env'
oi build app && oi up -d app
```

**Checkpoint, and it is about what is absent.** The app's runtime has exactly two variables of its own, and
neither is a secret:

```
docker inspect app --format '{{range .Config.Env}}{{println .}}{{end}}' \
  | grep -vE '^(PATH|LANG|GPG_KEY|PYTHON_VERSION|PYTHON_SHA256|APP_PORT|OI_API_URL)='
```

**Expect no output.** Anything printed is a variable nobody meant to set, and it is the thing to look at.

**The first version of this checkpoint was a grep for `KEY|PASSWORD|TOKEN` and it was wrong**, which is worth
recording because it is the kind of wrong that teaches the wrong lesson. It matched `GPG_KEY`, the signing key
of the Python base image, and reported one finding on a correct deployment. A check whose failure is normal is a
check people learn to ignore. The allowlist above has no false positives on this image, and it catches a
variable nobody intended rather than only the ones somebody thought to name.

## 13i.3. Look at it with your own eyes

```
# on the box: publish nothing, relay to the loopback temporarily
docker run -d --name app-relay --network ai-net -p 127.0.0.1:8090:8090 alpine/socat \
  -d -d TCP-LISTEN:8090,fork,reuseaddr TCP:app:8080

# from your machine
# **In a loop, because one session is not enough.** `AWS-StartPortForwardingSession` ends
# its session after a connection and a browser opens several at once, so a single
# invocation dies on the first page load and every later request is refused by a port
# nothing is listening on. It looks like the server stopped, and the server is fine.
while true; do
  aws --profile oi-credits ssm start-session --target <instance-id> \
    --document-name AWS-StartPortForwardingSession \
    --parameters '{"portNumber":["8090"],"localPortNumber":["18080"]}'
  echo "tunnel dropped; reconnecting"
  sleep 2
done
```

Then `http://127.0.0.1:18080`. **Mint a token and paste it**; there is no sign-in until C7, and the app says so
rather than pretending otherwise:

```
oi --profile tools run --rm session mint --dev --subject <declared subject> --role <declared role>
```

**Checkpoint.** Ask the question this deployment's corpus answers and read the line underneath the answer:

> Answered from 4 documents of 6 read on this machine · 1.3 seconds

**The count is not decoration.** It is computed from the citations the answer came back with. If it says "No
document was cited" on a corpus that holds the document, retrieval is not finding it, and the honest reading is
that the corpus is not indexed rather than that the answer is wrong.

**Remember to remove the relay when you are finished.** It publishes a port, which is the thing this deployment
otherwise does not do:

```
docker rm -f app-relay
```

## 13i.4. The three checks a screenshot cannot pass

This is the part of the stage that matters, and the part that gets skipped when a phase runs late.

**One: the answer leaves a row.** Ask a question in the app, then find its row by the request id the answer
carried. If asking a question leaves no trace, the product's central claim is false and no amount of the screen
looking right would show it.

```
TOKEN=<the token you pasted>
Q="What is the mileage rate for reimbursement?"
RID=$(curl -sS -X POST http://127.0.0.1:18080/api/ask -H 'Content-Type: application/json' \
      -H "X-OI-Session: $TOKEN" -d "{\"session\":\"$TOKEN\",\"question\":\"$Q\"}" \
      | python3 -c 'import json,sys; print(json.load(sys.stdin)["request_id"])')
curl -sS "http://127.0.0.1:18080/api/activity?request=$RID" -H "X-OI-Session: $TOKEN" \
  | python3 -c 'import json,sys; d=json.load(sys.stdin); print(d["count"], "row(s) for that request")'
```

**Checkpoint.** One row, and it says whether content was permitted to leave. **This check found a real defect
while the phase was being built**: the API minted a request id on every call and never passed it to the audit
tool, so the column was empty and an answer could not find its own row. Every test passed.

**Two: nothing redactable reaches the screen.** Ask a question that makes the model repeat a reserved
identifier, then read the page rather than the API:

```
Q="Repeat the payroll reference exactly as it appears in the document."
```

**Checkpoint.** The screen shows `[REDACTED-SSN]` and a line saying something was removed.

**Ask the question that makes the model echo the value, and this is not a detail.** Asking *what* the reference is
produced a paraphrase, so nothing reached the redactor and the check passed while measuring nothing. A weaker
question is a weaker test, and the difference is invisible until somebody reads the answer.

**But this checkpoint is not deterministic, and the honest version says so.** The question asks the model to
co-operate and it sometimes declines: the C4 panel's executor ran it twelve times and got a redaction ten, twice
receiving a safety refusal or a bare `[1]` instead. **Run it up to four times.** What must hold on every single
attempt is that **no reserved identifier appears anywhere on the page** — that is the control, and a run where it
holds is a real result even when the model declined to echo. What needs one of the four attempts to succeed is
seeing `[REDACTED-SSN]` at all, which is the evidence the redactor ran. If none of the four shows it, say so and
move on: the redactor is proved by its own unit tests against fixed input, and **a check whose subject is a
model's choice measures the model**.

**Three: the record can be read as a person, not as an operator.** Open the Activity screen and look at the
sentence above the list. It says what the page is scoped to:

> **This list is yours alone.** It widens when your role holds every permission another person holds.

**Checkpoint.** The sentence matches the role you minted. A token minted for a `company`-scoped role can see
itself and other company-scoped subjects, and **must not** see an executive subject's rows. Ask for one by name
in the filter and the list is empty rather than partial: a partial answer to "show me Dana's activity" reads as
"Dana did nothing".

## 13i.5. The suite, in the order it runs

```
# the app's own tests, from its checkout
cd /opt/oic-ui/app
npm ci
npx vitest run                                  # 57: components, and the contract
export OI_API_URL=http://127.0.0.1:18080 OI_TOKEN=$TOKEN
npx vitest run tests/integration.test.ts        # 12: the app's client code, live
npx playwright test                             # 10: a real browser, real flows

# the core's probes, from the deployment
cd /opt/oi-core
bash scripts/probe.sh activity_probe.py
bash scripts/probe.sh job_probe.py
bash tests/fixtures/gate_check_run.sh
bash tests/fixtures/gate_check_run.sh --jobs
```

**Checkpoint.** Every one passes, and you have watched one of them fail. Pick any probe here, remove the control
it tests, and confirm it goes red before you trust a green run. **A probe that cannot fail is not a probe**, and
this project has shipped one of those before.

**Two of these are not deterministic, and the guide should say so rather than promise a clean run.** The
integration suite's PII test and this stage's check two both ask the model to repeat a reserved identifier, and
**the model is not obliged to co-operate**: the C4 panel's executor ran the question twelve times and the
redactor fired ten, because twice the model refused on safety grounds or returned a bare `[1]`. Nothing reached
the redactor, so nothing was redacted, and the assertion on the counts failed.

**The fix was to separate two claims that had been conflated.** The control — that no reserved identifier
reaches the client — is asserted on every attempt and holds unconditionally. The evidence — that the redactor
fired — is sought over four attempts, and if the model never echoes, the failure names the model rather than
pretending the redactor is broken. The redactor's own correctness is proved by twenty-four unit tests with fixed
inputs, because **a check whose subject is a model's choice measures the model**.

## 13i.6. Open a citation, because that is where the product stops asking to be believed

Ask a question and click a source chip. **The chip has been a span since C4 and it is a button now**, and the
difference is the whole claim: a citation you cannot open asks a person to trust the answer, and this product's
position is that it does not have to.

**What you get is the text the model answered from**, not the file as it sits on disk. That is deliberate: a
PDF's layout, a spreadsheet's rendering and an OCR'd page are not what the model read, and showing a person
something subtly different from the evidence would be worse than showing nothing.

**Checkpoint, and it is the one worth doing by hand.** Ask the mileage question, open the first chip, and find
`0.67 per mile` in the document. If the figure the answer gave is not in the passage the chip opens, the
citation is decoration.

Three things to look for while it is open:

- **The part the answer used is marked**, so you are not reading a page looking for a sentence.
- **If the document has more chunks than one response carries, the panel says so.** A view that quietly showed
  the first two hundred chunks would read as the whole document.
- **A document holding a reserved identifier reports the removal and shows the marker**, because the redactor
  runs on this path too. `company/pii-new-starter.md` is the file to try.

**And check the refusal, which is the security-relevant half.** Open a chip, then request an executive document
from a company session:

```
docker run --rm --network ai-net curlimages/curl -sS -o /dev/null -w '%{http_code}\n' \
  -H "X-OI-Session: <a company-scoped token>" \
  http://api:8088/documents/executive/executive-compensation.md
```

**It answers `404`, the same as a document that does not exist, and that is the point.** A `403` would tell a
caller the document exists. The probe asserts the two are indistinguishable rather than checking each on its
own, because two separate assertions both pass while the responses differ.

## 13i.7. If it breaks, and what each symptom means

- **The page loads and everything says the service is unreachable.** The proxy is not forwarding. The app must
  be on `ai-net` and `OI_API_URL` must name the API's base *including* `/api`. Getting that wrong is a 404 that a
  browser reports as the API being down.
- **`/` returns 404 with the app running.** The bundle is not in the image. `npm run build` writes `app/dist`,
  and the image copies that directory specifically.
- **Every question refuses with `no_session`.** The token expired or was never minted. Sessions last eight hours
  by default.
- **Do is missing from the navigation.** Correct, if the manifest declares no tasks. Check `GET /tasks`.
- **The answer says "No document was cited" for something you know is in the corpus.** The document is not
  indexed, or its access label is one no role can read. **A folder under `documents/` whose name is not a
  declared root is indexed and unreachable**, and nothing reports it. That is the easiest way to lose an
  afternoon.

## 13i.8. What C4 added, in the order it matters

| What | Why it exists | Where it is proved |
|---|---|---|
| **A contract a second repository builds against** | `/openapi.json` listed three URLs and no response bodies. A client built on that is built on prose | `scripts/fetch_openapi.py --check`, against the running service |
| **`GET /activity`** | The chain's only reader was a CLI behind an SSH session. The screen that makes the product auditable had no endpoint | `activity_probe.py`, eight rights |
| **Jobs, with a replayable event log** | Work that can be watched, stopped and found afterwards. Progress is a numbered table, not a socket, so a client that closed the tab catches up | `job_probe.py`, eight rights |
| **The task registry in the manifest** | Six task names were hardcoded in Python, so no client could discover, label or describe them | `GET /tasks`, and the app's Do screen |
| **The app itself** | Ask, Do, Activity. It holds no model key and no database credential | the browser suite, ten tests |

# Stage 14: onboarding a second client's corpus

**Goal:** add a corpus to a deployment that is already holding one, without disturbing it.

This is the procedure manual section 19 describes and, until this guide was written, nothing had ever run
it. It is also where the access-label rule turns from a theoretical problem into a decision.

## 14a. Decide the roots before you copy anything

**This is the step people skip and then redo.** A root is a top-level folder under `documents/`, and its
name is the label. Two questions, both of which are the client's to answer:

1. **Who may see what?** Two roots exist in code today: `company` and `executive`. **Adding a third is a
   code change** to the `ROLES` map in `services/retrieval/retriever.py` and a redeploy, not a setting.
2. **Is there anything one group must not see?** Whatever goes in a restricted root, the client should
   understand that a search by a role without it returns a denial rather than an answer, and that
   `access.denial` decides whether they are told.

**A root named anything else is a folder nobody can read.** If the client's organisation implies
`finance/` or `hr/`, you have three options and only one of them is free: file those documents under
`executive/` and accept that only the executive role sees them; add the root to the code; or use C3's
per-user permissions. Decide before the corpus is laid out, because relabelling afterwards means
re-ingesting.

## 14b. One deployment holds one corpus, and this is a limitation

A second deployment is a second manifest, a second database and a second bucket. **Loading a second
client's documents into this deployment's `company/` folder puts them in the same search as the first
client's**, with no boundary between them: the ACL filters by folder, not by client.

So the honest options are:

- **A second deployment** for a second client, which is the shape section 19 describes and what the
  `deployment_id` in the manifest is for

**Two different things are called a role, and this stage is about only one of them.** The roots under
`documents/` map to *retrieval* roles in the `ROLES` map in `services/retrieval/retriever.py`, which is a
**code change**. The roles in `models:` are *model* roles — a manifest change, covered in stage 14b. Keep them
apart when you plan, because one is an edit and the other is a redeploy.
- **One shared corpus** when the two bodies of documents genuinely are one pool, which is the case for a
  single firm's departments and is what the fixture corpus looks like

**Never mix two clients' material in one root.** The failure is silent: every question returns a blend, and
the ACL cannot tell you because there is nothing to filter on.

## 14c. The procedure

```
cd /opt/oi-core

# 1. the documents, under roots that already exist
cp /tmp/newclient/policy.md      documents/company/
cp /tmp/newclient/board-pack.pdf documents/executive/

# 2. ingest, and read the counts rather than the exit code
docker exec ingestion python3 /app/ingest.py --documents /documents

# 3. set the retention period BEFORE there is anything to retain
docker compose -f deploy/office/compose/docker-compose.yml --env-file rendered/env \
  run --rm retention --set-retention documents --days 365 \
  --legal-basis "<the period, in the client's or their regulator's words>"

# 4. ask one question per root
docker compose -f deploy/office/compose/docker-compose.yml --env-file rendered/env \
  run --rm --entrypoint python -v /opt/oi-core:/w:ro -w /w eval /w/tests/fixtures/ask.py \
  -q "<a question the client says must work>" --role staff
```

**Compare the right two numbers** before telling anyone it is loaded — stage 5b has the regex, and the
point is that a plain file count includes the `.DS_Store` and the `.msg` the worker ignores on purpose.

**`--legal-basis` is not decoration.** A retention period with no stated basis is one nobody can defend to
an auditor.

**Checkpoint.** `seen` matches the count of accepted-suffix files, the question returns passages from the
root you expect, and a restricted question returns a denial rather than content. If restricted content comes
back, **stop** — that is the access filter failing, and stage 8d is where to go.

## 14d. What this stage cannot yet prove

**Two clients in one deployment, isolated from each other, is not possible.** There is one database, one
vector collection and one role map. The unit of isolation is the deployment, and a second client means a
second deployment. That is a real architectural limit and worth saying to a client who asks whether they can
share.

---

---

# Stage 14b: adding a role, and the two steps that fail late

**Goal:** give a piece of work its own model, ceiling, and key, and know which steps fail *after* everything
looks finished.

Manual section 17 is the procedure. This stage is the operator's version: the order, and the two places a
new role breaks without warning at validation time.

## 14b.1. When a role is the right answer

A role is a permission boundary. Add one when the work needs something an existing role cannot give it:

| Add a role when | Do not add one when |
|---|---|
| it needs a **different model** — agentic work against an email writer | it is a label you might use later |
| it needs a **different ceiling** that a person owns | the ceiling would match an existing role's |
| the **record** has to separate it, for billing or for an audit question | separation only matters "in case" |
| it needs **narrower model access** — a key that cannot reach the expensive model | it would hold the same permissions as one that exists |

**The manual's own framing is the rule:** *"If two components should have different ceilings, give them
different roles, even if they point at the same model."*

**A role nothing calls is indistinguishable from a role nothing wired up**, which is why this stage argues for
few rather than many. Declare a catalogue of plausible roles and you cannot tell "no email has been sent" from
"the email component is pointed at the wrong key".

## 14b.2. The order

```
cd /opt/oi-core
$EDITOR manifests/<client>.yaml      # 1. the role under models:, its ceiling under budgets.roles:
python3 scripts/validate_manifests.py <client>.yaml    # 2.
python3 scripts/check_model_lifecycle.py               # 3. only if the model is new to this deployment
python3 scripts/render_manifest.py --manifest manifests/<client>.yaml   # 4.
oi restart litellm                                      # 5. required, and not the command you would guess
oi run --rm keys                                        # 6.
oi run --rm keys --probe <role>                         # 7.
```

`budgets.roles.<role>` is **not optional** while remote inference is on. A role with no ceiling fails
validation, which is the good kind of failure.

## 14b.3. Step 5, which looks like it worked and did not

```
oi restart litellm        # correct: restarts the process, which re-reads the mounted config
oi up -d litellm          # WRONG: prints "Container litellm Running" and does nothing
```

**A mounted file changing underneath a running container is not a change Compose can see.** So `up -d` finds
nothing to do, reports success, and the model list is unchanged. LiteLLM reads its config once at startup and
does not watch the file. This is worth understanding rather than memorising: it is a property of mounts and
Compose, not a LiteLLM quirk, and it applies to any service here that reads a mounted file once.

**The check that settles it**, because it asks the gateway what it actually serves rather than what you
rendered:

```
MASTER=$(grep '^LITELLM_MASTER_KEY=' rendered/env | cut -d= -f2-)
docker run --rm --network ai-net -e MASTER="$MASTER" curlimages/curl -sS \
  -H "Authorization: Bearer $MASTER" http://litellm:4000/v1/models
```

## 14b.4. The two steps that fail late, which is the point of this stage

Both of these pass validation, pass the render, pass the restart, and then fail **when a call is made**. A
role that hits either one looks correctly configured until someone uses it.

**One: the model is not in `data/model-lifecycle.json`.** `check_model_lifecycle.py` fails and names it. The
format and the refresh procedure are in `docs/model-lifecycle.md`. Adding a model without its lifecycle entry
means nothing knows when it is deprecated or what replaces it.

**Two: the model's ARN is not in the instance role's Bedrock policy.** The policy allows a fixed list, so a
new model answers `AccessDenied` at call time and nothing before that catches it. Check the list:

```
aws iam get-policy-version --profile oi-credits \
  --policy-arn arn:aws:iam::878451098165:policy/oi-core-dev-bedrock-invoke \
  --version-id v1 --query 'PolicyVersion.Document.Statement[].Resource'
```

**`get-role-policy` returns `NoSuchEntity` for this policy**, because it is attached to the role rather than
written into it. That error reads like the policy does not exist, and it means you asked the wrong question.

**Checkpoint.** The probe in step 7 passes, **and** the model appears in the gateway's own list from 14b.3.
A passing probe on the old model list means the restart did not take.

## 14b.5. Changing which model a role uses

The same order, minus the key mint if the role already has one:

```
$EDITOR manifests/<client>.yaml      # change the one line under models.<role>
python3 scripts/validate_manifests.py <client>.yaml
python3 scripts/check_model_lifecycle.py
python3 scripts/render_manifest.py --manifest manifests/<client>.yaml
oi restart litellm
oi run --rm keys --probe <role>
```

**Nothing in the application changes**, because applications ask for a role rather than for a model. That is
the whole reason roles exist as a layer.

**Two rules, and the first is a contract term rather than a preference.** If the client's contract names a
single region, an inference-profile-only model is disqualified, and the validator says so rather than leaving
it to the first call. And a model swap means **rerunning the golden questions before the client sees the new
answers** — a different model gives different answers to the same question, and that is C8's work rather than
something to skip because the probe passed.

## 14b.6. Taking a role away

```
oi run --rm keys --revoke <role>
```

Then remove it from `models:` and `budgets.roles:`, validate and render. **Revoking the key and leaving the
manifest entry is a half-done removal**, and the mint path says so: *"The role may still exist in the
manifest. Add it back to models: and budgets.roles:"*. A role in the manifest with no key refuses every call,
which is safe, and reports as a role rather than as a deployment fault, which is confusing.

**A role is not the same thing as an access root.** Stage 14 covers the roots under `documents/`, and those
are a **code change** to the `ROLES` map in `services/retrieval/retriever.py`. This stage is about model
roles, which are a manifest change. Different mechanisms, and the words collide.

---

# Stage 15: the end of a contract

**Goal:** destroy everything and leave the client evidence that it happened.

`DATA-EXIT-001` is the requirement: termination destroys the client's data and produces evidence. It was
verified against the deployed store on 2026-10-13, and this stage describes what it does.

**This guide does not run it, and neither should you until the contract has actually ended.** It is the one
command in the system that empties everything, and on this deployment it would take the corpus with it.

## 15a. What it destroys, and the two things it does not

```
oi run --rm retention --destroy-all --reason "contract ended 2026-10-13"
```

| Store | What happens |
|---|---|
| The vector index | **The collection is dropped** — every embedding, every passage, every access label |
| The lexical index | `chunk_index` cleared: passage text, search terms, references |
| The ingestion manifest | Cleared: what was ingested and each document's hash |
| The purge history | Cleared |
| The legal holds | Cleared |
| The audit record's stored payloads | **Not cleared. Counted, and the run reports incomplete if any remain** |
| The audit chain | **Not destroyed, deliberately** |
| **The client's files** | **Not touched** — see below |

**The audit chain survives on purpose.** It records that the documents existed and that they were destroyed,
and it is the thing that proves the destruction happened. Destroying it would leave the client unable to show
anyone that we did what we said.

**The audit payload count is why a teardown can report incomplete.** If any audit row still holds content,
the run says so, because a record telling a client their data is gone while content sits in the record is the
worst kind of false claim. The fix is at deployment time, not here: a `hash` content mode never stores
content, so there is nothing to leave behind.

**`destroy-all` does not delete the client's documents from disk.** Their folder is theirs; deleting the
copies the system holds is what this does. If the contract requires the corpus removed as well, that is a
separate, deliberate act, and it is not this command.

## 15b. The destruction record

The run writes a record the client keeps, and **its hash recomputes from its own contents** — which is the
check they can run without trusting us. It names every store with the verb that is true of it: `deleted`
where rows went, `still_stored` where content remains, so the record cannot describe a partial destruction as
a complete one.

It also carries the chain head, so the deletion sits at a known point in the audit chain rather than being
asserted.

**Checkpoint, for a deployment whose contract has ended.** The record names every store, the hash recomputes
to itself, `still_stored` is zero or explained, and the collection is gone:

```
docker run --rm --network ai-net curlimages/curl -sS http://vector:6333/collections
```

## 15c. Stopping the machine, which is not teardown

The installation and the contract are different things, and the difference is where the money is:

```
# the instance, from wherever you administer AWS
aws ec2 stop-instances --profile oi-credits --region us-east-1 --instance-ids <the instance>
```

**Stopping costs nothing and destroys nothing.** The address survives a stop, the data survives on the
volume, and the containers restart themselves. This is what you do at the end of a working day.

A teardown is a contract event: it destroys data and produces evidence. **Never stop the instance expecting
data to be gone**, and never run `--destroy-all` expecting to save money.

**A standing rule worth repeating:** stop the instance when you finish. An m7i.4xlarge runs at roughly
0.81 USD an hour, which is about 20 dollars a day, and it is easy to leave running for a week.

---

# Stage 16: when something is wrong

**Goal:** get from a symptom to a cause without reading the whole manual.

Every entry here is a symptom, the one command that discriminates, and what each answer means. Work down;
the causes are ordered by how often they are the answer.

## The stack will not come up

```
oi ps                                    # what is actually running
docker logs <service> --tail 40          # the one that is not
```

| Symptom | Cause |
|---|---|
| A container restarting in a loop with no log output | It is downloading a model. `parser` and `embeddings` do this on first start and are silent while it happens |
| `postgres` unhealthy | Its data directory was initialised with a different password than `rendered/env` holds. The two must match |
| `oi ps` shows fewer services than you expect | A pack you removed took its services with it. `rendered/services.txt` is what the manifest declared |
| Everything up, nothing reachable | No container publishes a port. Use `docker run --rm --network ai-net curlimages/curl` to talk to them |

## A model call fails

```
oi run --rm keys --probe analyst
```

| Symptom | Cause |
|---|---|
| `403` naming a model the key cannot reach | The probe is asking for a model outside the key's scope. That is the control working |
| `422` budget exceeded | The ceiling, working. `keys --set-budget ROLE AMOUNT` is the emergency brake |
| `AccessDenied` from the provider | The IAM policy allows a bare model id and the rendered config names a prefixed one, or the reverse. Stage 1 has the four names |
| Nothing at all, and it hangs | The instance has no AWS credentials. The instance role is what makes this keyless |

## A document will not ingest

```
docker exec ingestion python3 /app/ingest.py --status
```

| Symptom | Cause |
|---|---|
| `seen 0` | The path is wrong. `--documents /documents` is the container's view of `${OI_ROOT}/documents` |
| `seen` lower than your file count | Unsupported suffixes are ignored silently, by design. Stage 5b has the regex that compares the right two numbers |
| `failed` above zero | Dead-lettered after two attempts. It is not retried until its bytes change, and `--clear-failures`'s own output is not evidence that it worked — stage 5c |
| `failed` with a parser refusal on a format in the accepted list | **`.rtf` is accepted by the worker and refused by the parser.** It dead-letters, which is the designed response to an unreadable file and a misleading one for a supported format |
| A document is ingested and no search finds it | Its top-level folder is not a root in the role map. It is indexed and unreachable, and nothing reports it. Stage 4b |

## A question returns the wrong thing

```
docker compose … run --rm --entrypoint python -v /opt/oi-core:/w:ro -w /w eval \
  /w/tests/fixtures/ask.py -q "…" --role staff
```

| Symptom | Cause |
|---|---|
| `lexical: 0` | **Ambiguous.** Either the arm found nothing or it never ran. Check `DATABASE_URL` before believing the index is empty — stage 6a |
| Passages from a document you did not expect | The corpus, not the code. That document answers the question too |
| No `NOT CURRENT` marker on a document you superseded | Either the declaration did not land (`--versions`) or the probe ran without a database — stage 7b |
| Fewer passages than the limit, `withheld: 0` | The overfetch is low or the corpus is small |
| `withheld` above zero | Two causes and the output does not say which: a store returning out-of-role content is a **security** fault, index drift is a reconciliation fault — stage 6 |

## A denial, or a lack of one

```
docker run … -e ACCESS_DENIAL_MODE=counted … /w/tests/fixtures/acl_probe.py
```

| Symptom | Cause |
|---|---|
| The probe prints `configured denied` when your manifest says `counted` | You did not pass `-e`, so it is reading the library default. Stage 8a |
| No denial when content is restricted | Either nothing restricted matched, or the mode is `silent`. Both are legitimate; check `restricted_matched` to tell them apart |
| A denial names a count of zero | The count is `restricted_matched`, not `withheld`. `withheld` is honestly zero on every question by construction |

## Redaction

| Symptom | Cause |
|---|---|
| Nothing leaves, and the reason says no classes are permitted | `FRONTIER_CLASSES` is empty. Re-render, or declare `access.egress.frontier_classes` — stage 9 |
| A figure was redacted | The pattern list is too broad. Decimal and thousands separators are the ones to check |
| A real identifier survives | It has no shape the pattern list recognises. Names and addresses are the known bound |

## Retention

| Symptom | Cause |
|---|---|
| `destroyed 0, examined 0` | Nothing is past the cutoff. Check `cutoff` and the document's `ingested_at` |
| `destroyed 0, examined 1, held 1` | A hold. `--holds`, including a deployment-scope one |
| `failed: 1` with `PermissionError` | The container cannot unlink. It runs as the operator for that reason — stage 10 |
| `INCOMPLETE` and the run says re-running is safe | **Read stage 10c before re-running.** It is true about the stores and misleading about the file |
| A document is gone from search and the file is still on disk | A previous run destroyed the stores and failed on the file. There is no command that finishes it |

## The verification commands disagree

| Symptom | Cause |
|---|---|
| `audit verify --anchor` fails on a file nobody touched | **Known defect.** A daily export is a slice of the chain and the anchor is the whole of it. Stage 11c |
| `--clear-failures` says it cleared one and the document stays skipped | Its count is not evidence. Stage 5c |
| `find | wc -l` disagrees with `seen` | Use the suffix-restricted regex. A plain count includes what the worker ignores — stage 5b |

---

# Appendix: the commands, by what they touch

Ordered by how much damage a mistake does, not alphabetically. Everything in the first two groups is safe
to run.

**Read-only, always safe**

```
oi ps                                    docker logs <service> --tail 40
oi run --rm audit list [--type T]        oi run --rm audit verify <file>
oi run --rm audit export --date D        oi run --rm retention --holds
oi run --rm retention --purges           oi run --rm retention --retention-runs
docker exec ingestion python3 /app/ingest.py --status
docker exec ingestion python3 /app/ingest.py --versions
docker exec postgres psql -U oicore -d oicore -c '<sql>'
```

**Rehearsals and read-mostly**

```
oi run --rm audit migrate                # applies migrations; additive
oi run --rm audit export                  # writes files under exports/
oi run --rm retention --enforce D --dry-run      # reports, destroys nothing
oi run --rm retention --purge DOC --dry-run      # reports, destroys nothing
oi run --rm audit purge --before D --dry-run     # reports, destroys nothing
docker exec ingestion python3 /app/ingest.py --documents /documents
```

**Changes state**

```
oi run --rm keys                          # mints or updates role keys
oi run --rm keys --set-budget ROLE AMOUNT
oi run --rm retention --set-retention D --days N --legal-basis "..."
oi run --rm retention --hold DOC --reason "..."
oi run --rm retention --release-hold ID   # exits 1 if the hold is not in force
docker exec ingestion python3 /app/ingest.py --supersede A --by B --note "..."
docker exec ingestion python3 /app/ingest.py --distinct DOC --note "..."
oi restart <service>                      # a container re-reads its mounted config
```

**Destroys data**

```
oi run --rm retention --purge DOC --reason "..."        # one document's indexes; leaves the file
oi run --rm retention --enforce D                       # everything past the period, INCLUDING the file
oi run --rm audit purge --before D --reason "..."       # old audit rows, with a tombstone
oi run --rm retention --destroy-all --reason "..."      # every index, at the end of a contract
```

**The three things to do before anything in that last group:** run the `--dry-run` form, know which document
or date it will reach, and check `--holds` in case a hold is already covering it.

---

# Appendix B: what changed in October 2026, and what it cost to find out

Four defects were found by running this guide against a live deployment after three review rounds and two
independent panels had passed the documentation. They are recorded together because they share a shape, and
the shape is worth recognising the next time.

**The shape: the rehearsal tested a path the real command does not take, or the test's world was not the
deployed world.**

| Defect | How it hid |
|---|---|
| `retention --enforce` raised `TypeError` on every document | `--dry-run` returns before the call that failed, so every rehearsal passed |
| Then it could not unlink, running as uid 10002 against the operator's tree | Nothing reached the unlink until the first bug was fixed. One defect behind another |
| A run that failed on the unlink left a file with no index, and re-running found nothing to do | The `INCOMPLETE` message said re-running was safe. It is safe for the stores and wrong about the file |
| `audit verify --anchor` failed on untampered files | Every test built a **single-day** chain, where the export *is* the whole chain. No test built the multi-day chain a deployment produces |
| `.rtf` dead-lettered although it is in the accepted list | The generator's "RTF" fixture was plain text with an `.rtf` extension, so the format looked covered |

## The four fixes

**Retention could never destroy anything.** `policy.enforce` calls its purge callback with four arguments
because it decides whether to destroy rather than where; `retention.purge` takes six. The branch passed the
bare function. See stage 10.

**Retention could not unlink a client's file.** The container ran as the image's uid 10002 against a documents
tree owned by uid 1000 with mode `drwxr-xr-x`. Unlinking needs write on the **directory**. `audit`, `keys` and
`eval` all declared the operator's uid; retention, the one tool that writes to the host filesystem, did not.
See stage 10.

**A half-done destruction cannot be finished, and this is a design hazard rather than a bug.** The order is:
clear the vector store, clear the lexical index, clear the manifest, then unlink the file. A failure after the
first three leaves a file with no index and no manifest row, so a re-run reports `examined 0` and does nothing.
**There is no command that completes it.** See stage 10c. Anthony's fix, when it lands, is to remove the files
after the stores are cleared.

**The anchor check compared a slice against a whole.** An export is a window over the chain; an anchor is a
point in it. The verifier now places the anchored row against the window rather than comparing row counts, and
where one anchor genuinely cannot decide, it says so instead of choosing a verdict. See stage 11c.

**The RTF format was never parsed.** Docling supports RTF and needs LibreOffice, which was not installed. A
missing system binary and a corrupt client document both arrive as `permanent` failures and both collapsed
into a one-line count, so a supported format looked like a broken file. See stage 5 and `--failures` in
stage 16.

## Two commands that exist because of these

```
docker exec ingestion python3 /app/ingest.py --failures
```

Every document that has failed, with the reason and whether it is still being retried. **It reads the
manifest, not the last cycle**, because a dead-lettered document drops out of `--status`: the next cycle skips
it, succeeds, and the deployment reports healthy while a file is silently not in the index.

```
docker exec ingestion python3 /app/ingest.py --clear-failures <relpath>
```

Clears the dead-letter latch on one document. **This is needed after any fix to the parser**, because the latch
is keyed on the content hash: installing a missing binary does not make an already dead-lettered file get
retried. That is exactly how the RTF fix would have appeared not to work.

## What to take from it

**Running the guide is what found these, not reading it.** Every one of them survived unit tests, a
documentation review and two panels, because each test exercised the code and none exercised the deployment.
The guide's checkpoints are the part that earns its keep.

**A check that reports a problem is worth less than a check that reports the right problem.** The anchor
verifier accused intact files of tampering, and the retention job reported `INCOMPLETE` while telling the
operator to re-run. A false accusation costs an afternoon; a false acquittal costs the client's evidence.
