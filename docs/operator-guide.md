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

**If the box is small, stop and resize it.** This project has a rule about that, in `AGENTS.md`: ask what a
competent production team would deploy, and let that decide. A four-vCPU box will work for the fixtures and
fall over on the first scanned document, and the failure will look like a parser bug rather than a
hardware limit.

## The shape of the thing

Five containers run all the time. Four tools run when you call them. Knowing which is which makes the
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

**Checkpoint.** `git -C /opt/oi-core log --oneline -1` prints a commit. If that fails, nothing later will
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
is C3. What exists is a probe that runs one question through the whole path, and it is how you answer
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
docker run --rm --network ai-net --user "$(id -u):$(id -g)" \
  -v /opt/oi-core:/w -w /w \
  --entrypoint python oi-core/ingestion:local \
  /w/tests/fixtures/citations_acceptance.py
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
the code**, which is a real limitation rather than a convention: per-user and per-group permissions are C3.

There is a second vocabulary in this system that shares the word "role" and means something else entirely.
The model roles — `analyst`, `front_desk` and so on — decide which model answers and what the caller may
spend. They are in the manifest and they have nothing to do with document access. A search asked for
`analyst` is refused rather than guessed at, because a default would grant the broader of two permissions
to a typo.

## 8a. Prove the filter, don't assume it

```
docker run --rm --network ai-net --user "$(id -u):$(id -g)" \
  -e ACCESS_DENIAL_MODE=counted \
  -v /opt/oi-core:/w -w /w \
  --entrypoint python oi-core/ingestion:local \
  /w/tests/fixtures/acl_probe.py
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
