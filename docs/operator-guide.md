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
