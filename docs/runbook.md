# Runbook

Build log and operating manual for the core system. Commands you can paste, in order, with the
reasoning recorded at the point each component is added.

**How to use this:** work one phase at a time. A phase is not finished until its verification step
passes. Later phases stay as outlines until we reach them, so nothing here goes stale.

**Status:** Phase 0 written and ready. Phases 1 to 11 outlined.

## The environment we are building on

| | |
|---|---|
| Account | Office Inference, `878451098165` (holds the Activate credits) |
| Instance | `i-0887564c9fe06b0b5`, t3.large, Ubuntu 24.04, 30 GB gp3 encrypted |
| Elastic IP | `52.204.202.66` (stable across stops) |
| SSH key | `ai-stack-key`, private key at `~/ai-stack/ai-stack-key.pem` on the workstation |
| SSH allowed from | 47.164.45.109 only (security group `sg-09d9bfffbd28014c9`) |
| Instance role | `oi-core-dev-instance` (SSM, for keyless access) |
| Region | us-east-1 |
| State | **stopped** when not in use |

Why this shape: an Elastic IP so the address survives stops; a keyless SSM role so access does not
depend on a key file; and a security group that publishes nothing but SSH from one address. That is
the same posture a client box gets, minus the credit card.

---

## Phase 0 — Reference machine

**Goal.** A clean host with Docker, a private container network, the repository, and a passing
manifest validation. No GPU needed, so this can run on the smallest box.

### 0.1 Start the instance

From the workstation:

    aws ec2 start-instances --profile oi-credits --region us-east-1 \
      --instance-ids i-0887564c9fe06b0b5
    aws ec2 wait instance-running --profile oi-credits --region us-east-1 \
      --instance-ids i-0887564c9fe06b0b5
    aws ec2 describe-addresses --profile oi-credits --region us-east-1 \
      --allocation-ids eipalloc-0f0560d02bcb32517 --query 'Addresses[0].PublicIp' --output text

Expected output: the instance id, then `52.204.202.66`.

**Why:** every session starts the same way, and stopping the box between sessions is what keeps the
credits lasting. The Elastic IP means the address never changes, so nothing in this runbook needs
editing between sessions.

### 0.2 Connect

    ssh -i ~/ai-stack/ai-stack-key.pem ubuntu@52.204.202.66

If SSH complains about key permissions:

    chmod 600 ~/ai-stack/ai-stack-key.pem

Alternative, no key and no open port:

    aws ssm start-session --profile oi-credits --region us-east-1 --target i-0887564c9fe06b0b5

**Why:** SSH is convenient during the build. Session Manager is the pattern a client box will use,
because it needs no inbound port and no key material in the client's hands, and every session is
logged in their account. Both work here, so you can practise the one clients will live with.

### 0.3 Record the host

    . /etc/os-release && echo "$PRETTY_NAME"
    nproc
    free -g | head -2
    df -h / | tail -1

Expected: Ubuntu 24.04.x LTS, 2 CPUs, 8 GB RAM (about 7 usable), 30 GB disk with most of it free.

**Why:** the runbook doubles as the record of what the system was built on. When a client asks six
months from now, the answer should not be a guess.

### 0.4 Update the system

    sudo apt update && sudo apt -y upgrade

**Why:** a fresh image still carries pending security updates. Patching before installing anything
is the cheapest hardening step there is, and it happens before any service is listening.

### 0.5 Docker

    curl -fsSL https://get.docker.com | sudo sh
    sudo usermod -aG docker "$USER"
    newgrp docker
    docker run --rm hello-world
    docker --version

Expected: the hello-world message, then a version like `29.x`.

**Why Docker:** every component we ship is a container, so one build runs identically on a client's
hardware and in a client's cloud account. This is the substrate half of "one system, two builds."
The office box and the cloud account run the same images; only the profile differs.

**The pinning lesson:** images are pinned by digest, **with the version written in a comment beside
the pin**, and the version is verified after any upgrade. We learned this the hard way on the
earlier stack, where a digest-pinned LiteLLM had no version recorded and nobody could answer the
first question a security review asks.

### 0.6 The private network

    docker network create ai-net
    docker network ls | grep ai-net

Expected: `ai-net` listed as a bridge network.

**Why:** services talk to each other over a dedicated bridge, and nothing publishes a port to the
host except ingress. That is the first security boundary in the design. The name matches the
earlier builds so the documentation and the muscle memory carry over.

### 0.7 Firewall

    sudo ufw default deny incoming
    sudo ufw default allow outgoing
    sudo ufw allow OpenSSH
    sudo ufw enable
    sudo ufw status verbose

Expected: status active, with only OpenSSH allowed in.

**Why:** the security group already limits who can reach the box, but defence in depth means the
host also refuses what it was not asked to accept. Two independent controls, one at the network edge
and one on the machine.

**Know the limitation:** Docker writes its own iptables rules and can bypass UFW when a container
publishes a port. So the real control is not publishing ports in the first place. Compose files in
this repo publish nothing except ingress, and that is a rule we enforce by review.

### 0.8 Put the repository on the box

Two ways. The first keeps credentials off the server entirely:

**Option A, copy from the workstation** (run this on the workstation, not the box):

    rsync -av --exclude '.git' ~/projects/office-inference-core/ \
      ubuntu@52.204.202.66:/opt/oi-core/

**Option B, clone on the box** (needs GitHub auth on the server):

    sudo mkdir -p /opt/oi-core && sudo chown "$USER":"$USER" /opt/oi-core
    gh auth login          # device code flow
    gh repo clone privateInferenceAI/office-inference-core /opt/oi-core

**Why Option A is listed first:** the repository is private, and cloning it on the server means
storing a token there. Client boxes should never hold our repository credentials, so the habit
starts here. Use Option B when you want the box to pull its own updates.

### 0.9 Validate the manifests

    sudo apt -y install python3-yaml
    cd /opt/oi-core
    python3 scripts/validate_manifests.py

Expected:

    ok   example-compliant-cloud.yaml  (profile=cloud, packs=[...])
    ok   example-hybrid.yaml  (profile=hybrid, packs=[...])
    ok   example-in-office.yaml  (profile=office, packs=[...])
    ok   example-standard-cloud.yaml  (profile=cloud, packs=[...])

    4/4 manifests valid; 8 packs known

**Why:** the manifest is what a client buys, and the validator is what refuses to install a
configuration that breaks a promise, such as turning the frontier lane on in a build with no cloud
leg. It runs at install time on the client's box, so we run it here first, on ours.

### 0.10 Verification, which ends the phase

    docker ps -a --format '{{.Names}}' | grep -E 'postgres|litellm' || echo clean

Expected: `clean`.

Nothing of ours is running yet, and that is correct. Phase 0 built the floor, not the house.

---

## Phase 1 — Data and gateway

**Goal.** Configuration rendered from the manifest, Postgres, a model server, and the gateway
running, with one model call travelling the whole path and landing in the database as an audit row.

### 1.1 Render the manifest into configuration

    cd /opt/oi-core
    python3 scripts/render_manifest.py --manifest manifests/dev-office.yaml
    python3 scripts/render_manifest.py --manifest manifests/dev-office.yaml   # rerun on purpose
    sed -E 's/(PASSWORD|MASTER_KEY)=.*/\1=redacted/' rendered/env

Expected: the second run reports **secrets reused from the existing env file**, and `rendered/`
holds `env` (mode 600), `litellm.yaml`, `services.txt`, and `manifest.json`.

**Why:** the manifest is what a client buys, and this turns it into what the deployment reads, so
nobody hand-edits configuration on a server. Secrets are generated once and reused, because
regenerating the database password would lock the stack out of its own data. That is why the rerun
is part of the step rather than an afterthought.

### 1.2 Fetch the development model

    mkdir -p /opt/oi-core/models
    curl -L --fail -o /opt/oi-core/models/Qwen3-1.7B-Q4_K_M.gguf \
      https://huggingface.co/unsloth/Qwen3-1.7B-GGUF/resolve/main/Qwen3-1.7B-Q4_K_M.gguf
    ls -lh /opt/oi-core/models/

Expected: about 1.1 GB.

**Why:** this box has no GPU, so the local lane runs a 1.7B CPU model. It exists to prove the path,
not to be good, and every role points at it for now. When the GPU arrives, vLLM replaces llama.cpp
and the roles point at real models. Nothing in front of it changes, because the gateway is the only
thing that knows a model exists.

### 1.2b Know this before you start: Docker creates missing mount paths as root

The Postgres service mounts `${OI_ROOT}/db/migrations`. **If that path does not exist, the Docker daemon
creates it, owned by `root`, not by you.** In Phase 1 the repository has no `db/` directory yet (it
arrives in Phase 2), so after the first `up -d` the directory exists and is not yours.

The symptom appears later, and it looks unrelated: `git pull` in Phase 2 fails with
`unable to create file db/migrations/002_audit_events.sql: Permission denied`.

Check and fix before it bites:

    ls -ld /opt/oi-core/db /opt/oi-core/db/migrations 2>/dev/null
    sudo chown -R "$USER":"$USER" /opt/oi-core/db

**Do not chown the whole tree.** `data/` and `exports/` are root-owned on purpose, because Docker made
them for volumes.

**If a pull is interrupted halfway**, git leaves what it already wrote: tracked files modified and new
files untracked. Recover with:

    rm -rf services/audit          # or whatever the pull listed as untracked
    git checkout -- .
    git pull

Deleting those untracked files is safe: they are partial copies of what the pull is about to write
from the same commit.

### 1.3 Start the stack

    cd /opt/oi-core
    docker compose -f deploy/office/compose/docker-compose.yml --env-file rendered/env up -d
    docker compose -f deploy/office/compose/docker-compose.yml --env-file rendered/env ps

Expected: `postgres` healthy, `llamacpp` and `litellm` running. First start takes a minute or two
while images pull, Postgres initializes, and the gateway runs its migrations.

**Why these three, in this order:**

- **Postgres** because the gateway needs somewhere to keep keys, spend, and the audit trail. It runs
  as a container here and becomes RDS in the cloud build, with the same schema and the same queries.
- **llama.cpp** because it is the only way to get a local model answering on a CPU box.
- **LiteLLM** because it is the single control point: authentication, per-role routing, budgets,
  spend, and logging live in one place. It is also the seam that makes a model swap a one-line
  configuration change rather than a project.

Note what is not published: no host ports at all. Everything is reachable on `ai-net` only, which is
why the checks below run a throwaway container on that network.

### 1.4 Verify the gateway and the model

    docker exec litellm python -c "from importlib.metadata import version; print('litellm', version('litellm'))"
    docker run --rm --network ai-net curlimages/curl -s http://litellm:4000/health/liveliness
    docker run --rm --network ai-net curlimages/curl -s http://llamacpp:8080/v1/models

Expected: a version of **1.104.x**, `"I'm alive!"`, and one model listed by llama.cpp.

**Why the version check is a step and not a nicety:** LiteLLM's CVEs are version-bound.
CVE-2026-59822 affects everything before 1.84.0 and sits in CISA's KEV catalog. We verify the running
version rather than trusting the pin, because a pin records intent and the version reports reality.

**Cross-check the running digest.** This is the check that actually matters:

    docker images --digests | grep litellm

Expected: a line whose DIGEST column reads `sha256:3d88bd75...`, matching the pin in
`deploy/office/compose/docker-compose.yml`. The TAG column will say `<none>`, and that is correct:
we pulled by digest, so no tag was ever stored.

Three things the first run taught us, all worth knowing because each one is a command that looks
right and fails:

- **`docker inspect litellm` inspects the container, not the image.** Containers have no
  `RepoDigests` field, so the request errored even though the image was fine.
- **`docker image inspect ghcr.io/berriai/litellm` fails with "No such image: ...:latest".** Omitting a
  tag makes Docker assume `latest`, and a digest pull stores no tag. To inspect by name you must give
  the digest: `docker image inspect ghcr.io/berriai/litellm@sha256:3d88bd75...`, or inspect by ID:
  `docker image inspect $(docker inspect --format '{{.Image}}' litellm)`.
- **This image ships no `org.opencontainers.image.version` label**, so a label check returns empty. Not
  every publisher sets one, which is why the digest is the comparison that counts.

`docker images --digests` sidesteps all three: it lists what is on disk with the digest Docker
recorded when it pulled, and it needs neither a tag nor a container.

**Why `importlib.metadata` instead of `litellm.__version__`:** LiteLLM 1.104 loads attributes lazily and
does not export `__version__`, so the obvious command raises `AttributeError`. The package metadata is
the reliable source, and the digest comparison is what actually proves the running build is the one we
pinned.

### 1.5 Make a call through the gateway

    KEY=$(grep '^LITELLM_MASTER_KEY=' rendered/env | cut -d= -f2-)
    docker run --rm --network ai-net -e KEY="$KEY" curlimages/curl -s \
      -X POST http://litellm:4000/v1/chat/completions \
      -H "Authorization: Bearer $KEY" -H 'Content-Type: application/json' \
      -d '{"model":"analyst","messages":[{"role":"user","content":"In one sentence, what is a gateway?"}],"max_tokens":60}'

Expected: JSON containing an answer. On two vCPUs, expect a few seconds.

**Why:** one request exercises the entire path, client to gateway to model and back, with the
gateway doing authentication, role routing, and logging. The model name `analyst` is a role, not a
model, which is the indirection that lets the model tier change without touching anything upstream.

**What a good response looks like, and the thinking-token trap.** The first successful call may come
back with an **empty `content`**, a populated `reasoning_content`, and `"finish_reason": "length"`.
That is not a failure. Qwen3 is a reasoning model: it spends tokens thinking before it answers, and a
60-token budget gets consumed entirely by the thinking phase.

Two ways to see an actual answer:

    # give it room: thinking still happens, but there is budget left to answer
    #   ... "max_tokens": 400 ...

    # or turn thinking off for this request, which llama.cpp honours per request
    #   ... "chat_template_kwargs": {"enable_thinking": false} ...

Why this matters beyond a smoke test: **thinking tokens are billed tokens and they are slow tokens.**
A front desk answering "when are you open" should not spend 300 tokens reasoning first. That is the
argument for the role indirection doing real work: a small non-thinking model serves the front desk
while the analyst keeps its reasoning, and both run through the same gateway.

**Measured on 2026-10-10, for the record:** 1.7B Q4 on 2 vCPU gave 7.2 predicted tokens per second and
21 tokens per second of prompt processing. A 500-token answer would take about 70 seconds. Fine for
proving a path; unusable for a client, and the clearest possible argument for the GPU.

### 1.6 Find the audit row

    docker exec postgres psql -U oicore -d oicore -c '\dt' | tail -12
    docker exec postgres psql -U oicore -d oicore -c 'select count(*) from "LiteLLM_SpendLogs";'
    docker exec postgres psql -U oicore -d oicore -c \
      'select request_id, model, total_tokens, "startTime" from "LiteLLM_SpendLogs" order by "startTime" desc limit 3;'

Expected: your call, with the model, the token count, and a timestamp.

**Why this matters more than it looks:** the audit trail exists from the first call, without us
writing a line of logging code. Every model call is recorded with which key made it, which model
answered, how many tokens it cost, and when. Phase 2 adds our own append-only sink for actions and
approvals, but the model-call record starts here.

### 1.7 Verification, which ends the phase

All four of these must pass:

1. `health/liveliness` returns alive.
2. llama.cpp lists a model.
3. A chat completion returns an answer through the gateway.
4. That call appears in `LiteLLM_SpendLogs`.

### If it breaks

- **llamacpp restarting in a loop:** the file name in `rendered/env` (`MODEL_FILE`) must match what is
  in `models/`. Check `docker logs llamacpp`.
- **litellm unhealthy:** check that postgres is healthy first, then `docker logs litellm | tail -30`.
  A bad password in `DATABASE_URL` shows up as an authentication error.
- **curl cannot resolve `litellm`:** the container has to be on the same network. `--network ai-net`
  is not optional, and omitting it is the most common version of this mistake.

## Phase 2 — Audit sink

**Goal.** An append-only record with a hash chain, which every later component writes to, which
cannot be edited or deleted through the application, and which exports as a file a third party can
verify without trusting us.

**Why this is the phase that matters commercially.** A log file is a claim. A hash-chained record
with a verifiable export is evidence. When a regulated client's examiner asks "prove that nothing
left the building in September", the answer is an export they can check themselves.

### 2.1 Apply the migration

    cd /opt/oi-core
    docker compose -f deploy/office/compose/docker-compose.yml --env-file rendered/env \
      run --rm audit migrate

Expected: `applied 002_audit_events.sql`, then a count.

**Why not just let the container initialize the database:** the Postgres entrypoint only runs files
in `docker-entrypoint-initdb.d` when the data directory is empty. Fresh installs get them for free,
but every later change needs an ordered, recorded migration. `schema_migrations` stores each file's
checksum, and editing an applied migration is refused rather than silently ignored.

### 2.2 Record an event

    docker compose -f deploy/office/compose/docker-compose.yml --env-file rendered/env run --rm audit \
      record --type access --actor anthony --actor-role admin --data-class internal \
      --payload '{"document":"expense-policy.pdf","pages":3}'

Expected: JSON with an id, a timestamp, a `row_hash`, and a `payload_sha256`.

    docker compose -f deploy/office/compose/docker-compose.yml --env-file rendered/env \
      run --rm audit list

**Why the payload is hashed and not stored by default:** the record needs to prove *what* was sent
without becoming a second copy of the client's documents. `--store-payload` keeps the content, but
that is a policy decision a client makes, not a default we choose for them.

### 2.3 Prove it is append-only

    docker exec postgres psql -U oicore -d oicore -c \
      'update audit_events set actor = '"'"'someone else'"'"' where id = 1;'
    docker exec postgres psql -U oicore -d oicore -c 'delete from audit_events where id = 1;'
    docker exec postgres psql -U oicore -d oicore -c \
      'update audit_events set actor = '"'"'maintenance'"'"' where id = 1;' --set=oi.allow_audit_maintenance=on

Expected: the first two raise `audit_events is append-only`. The third is the escape hatch, and it
demonstrates that a retention purge is possible but has to be asked for explicitly. The purge in
Phase 9 will run inside a transaction that sets the flag, not by dropping the trigger.

**Why a trigger rather than revoked privileges:** in a single-box deployment the application connects
as the table owner, and an owner can always re-grant its own rights. A trigger stops accidents, which
is the realistic threat. The hash chain is what stops deliberate edits from going unnoticed.

### 2.4 Bring the gateway's calls into the audit record

    docker compose -f deploy/office/compose/docker-compose.yml --env-file rendered/env \
      run --rm audit backfill
    docker compose -f deploy/office/compose/docker-compose.yml --env-file rendered/env \
      run --rm audit list --type model_call

Expected: the model call from Phase 1, now recorded in our own table with its token counts and the
hash of the payload. Running it twice inserts nothing the second time.

**Why both tables exist:** LiteLLM's spend logs are the gateway's operational record, which is what
you want when debugging routing or cost. `audit_events` is the system-wide record: model calls beside
tool calls and approvals, in one chain, exportable as one artifact.

### 2.5 Export the day, then try to break it

Create the exports directory as yourself first. The audit tool runs as your user on purpose, so that
the files it produces belong to you rather than to root:

    mkdir -p exports
    docker compose -f deploy/office/compose/docker-compose.yml --env-file rendered/env \
      run --rm audit export --date "$(date -u +%F)"
    ls -l exports/$(date -u +%F)/
    docker compose -f deploy/office/compose/docker-compose.yml --env-file rendered/env \
      run --rm audit verify /app/exports/$(date -u +%F)/audit-events.jsonl

Expected: `ok ... chain intact`, plus a note like:

    note: chained from row 12 (anchor 4f0a9c21d3b8...), which is not in this file

That note is normal and important. The chain is ordered by **recording** time, not by occurrence, so a
day's export almost always starts mid-chain. The manifest records the hash the first row chains back
to, which is what lets a single day's file be checked on its own. Without that anchor, an honest
export would look tampered because its first line references a row the file does not contain.

To check continuity *between* exports, compare one manifest's `last_row_hash` against the next one's
`first_prev_hash`. That is the chain of custody, and it is what an examiner actually asks for.

Now break it on purpose, because an integrity check that has never failed is not a check:

    cd /opt/oi-core
    cp -r exports/$(date -u +%F) /tmp/tamper
    sed -i 's/"actor":"anthony"/"actor":"not-anthony"/' /tmp/tamper/audit-events.jsonl
    docker compose -f deploy/office/compose/docker-compose.yml --env-file rendered/env \
      run --rm -v /tmp/tamper:/app/exports audit verify /app/exports/audit-events.jsonl

Expected: `FAIL`, with `row_hash does not match the row contents` and `the file hash does not match the
manifest`. Try deleting a line instead of editing one and it also reports `prev_hash does not match the
previous row` and an event count mismatch.

**Work on a copy, and learn from why:** an in-place `sed` on an export that is owned by root fails
silently if you are not watching stderr, and the verification then correctly reports the *untouched*
file as intact. That is exactly the false confidence this step exists to prevent, and it is the reason
the audit tool runs as your user.

Then confirm the real export is still intact:

    docker compose -f deploy/office/compose/docker-compose.yml --env-file rendered/env \
      run --rm audit verify /app/exports/$(date -u +%F)/audit-events.jsonl

**Why this is the whole point of the phase:** the export is what you hand over. A client, an auditor,
or a lawyer can recompute the chain and see that nothing was altered, without trusting our database
or our word.

### 2.6 Verification, which ends the phase

1. `migrate` applies the migration and refuses to run twice.
2. An event records and appears in `list`.
3. `update` and `delete` are refused.
4. `backfill` brings the gateway's call in, and a second run changes nothing.
5. `export` writes JSONL plus a manifest.
6. `verify` passes on the untouched file and fails on the edited one.

## Phase 3 — Documents

**Goal.** Ask a question across files and get an answer with a citation you can check: document, page,
and the passage behind it. Plus a refusal that is recorded when someone asks for something they may
not read.

**Why this phase is the whole product in miniature.** Everything else is plumbing around it. If a
citation is wrong, or if a restricted passage can reach a model, nothing built later matters.

### 3.1 Start the document services

    cd /opt/oi-core
    docker compose -f deploy/office/compose/docker-compose.yml --env-file rendered/env up -d qdrant embeddings reranker
    docker compose -f deploy/office/compose/docker-compose.yml --env-file rendered/env ps

**Expect a long first start.** The two model servers download bge-m3 and bge-reranker-v2-m3, about
4.5 GB together, into `data/hf`. Watch them:

    docker compose -f deploy/office/compose/docker-compose.yml --env-file rendered/env logs -f embeddings

You are waiting for a line about the model being ready and listening on port 80.

**Why three services rather than embedding inside the application:**

- **Qdrant** because a vector store answers "which passages are near this question", and because
  access labels live in the payload, so the filter that enforces permissions runs in the same query
  that finds the passages. Filtering after the search would mean retrieving a restricted passage and
  then deciding not to show it, which is one bug away from leaking it.
- **Embeddings, through Text Embeddings Inference**, because loading torch inside every tool that
  needs a vector would make each tool a gigabyte and a half. TEI keeps that cost in one container, and
  the GPU tier swaps the image tag and keeps the same HTTP interface.
- **A reranker** because vector search finds passages that share vocabulary with the question, which
  is not the same as passages that answer it. Reranking is what makes a citation worth clicking.

**Memory, honestly:** bge-m3 and the reranker want roughly 2 GB each alongside llama.cpp, Postgres, and
the gateway. On an 8 GB box that is tight. If something gets killed, stop llama.cpp while ingesting:

    docker compose -f deploy/office/compose/docker-compose.yml --env-file rendered/env stop llamacpp

### 3.2 Apply the migration

    docker compose -f deploy/office/compose/docker-compose.yml --env-file rendered/env run --rm audit migrate

Expected: `applied 003_ingest_manifest.sql`.

**Why a table rather than a state file on disk:** a JSON file in a mounted directory ends up owned by
whichever user the container ran as, which is the trap that broke a `git pull` earlier in this build.
The database already exists, already has an owner, and survives container replacement.

### 3.3 Put documents in place

    mkdir -p documents/company documents/executive
    python3 tests/fixtures/make_sample_pdf.py > documents/company/vendor-agreement.pdf
    ls -R documents

Expected: three markdown files already in the repo, plus a three-page PDF you just generated.

**Why generate a PDF:** markdown can only ever cite page 1. The fixture has a different fact on each
page, so a citation to page 3 is either right or it is visibly wrong.

**The folder is the permission.** `documents/company/` is readable by every role and
`documents/executive/` only by admin. Adding a restricted class of documents means creating a folder
and adding the label to `ROLE_ACLS` in `services/ingestion/common.py`.

### 3.4 Ingest

    docker compose -f deploy/office/compose/docker-compose.yml --env-file rendered/env --profile tools build docs
    docker compose -f deploy/office/compose/docker-compose.yml --env-file rendered/env run --rm docs ingest.py

Expected, roughly:

      add company/expense-policy.md: 1 page(s), 2 chunk(s), acl=company
      add company/vendor-agreement.pdf: 3 page(s), 4 chunk(s), acl=company
      ...
    added 4, changed 0, removed 0, skipped 0, failed 0
    points in company_docs: 8

**Then run it again, which is the point:**

    docker compose -f deploy/office/compose/docker-compose.yml --env-file rendered/env run --rm docs ingest.py

Expected: `added 0 ... skipped 4`. A run with nothing to do costs nothing, which is what makes it safe
to put on a timer. Delete a document and re-run, and its points are removed rather than lingering as
an answer nobody can find the source of.

### 3.5 Retrieve, without a model

    docker compose -f deploy/office/compose/docker-compose.yml --env-file rendered/env run --rm docs ask.py \
      --role staff --question "how long do we have to report a data breach?" --retrieve-only

Expected: the vendor agreement, page 3, near the top. Nothing is sent to a model, so this is the step
to debug retrieval on.

### 3.6 Ask, and check the citation

    docker compose -f deploy/office/compose/docker-compose.yml --env-file rendered/env run --rm docs ask.py \
      --role staff --question "how long do we have to report a data breach?" --max-tokens 800

Expected: an answer citing the passage, then a Sources list naming the document and page.

**If the answer is empty**, the model spent the budget thinking. Qwen3 reasons before it answers, so
raise `--max-tokens`. This is the same behaviour Phase 1 documented, now with a bill attached.

**Why citations are structured this way:** the chunk carries its page because chunks never cross a
page boundary, and the prompt numbers each passage so the model can point at one. A citation you can
open and check is the difference between a tool people trust and a tool they quietly stop using.

### 3.7 Ask for something you may not read

    docker compose -f deploy/office/compose/docker-compose.yml --env-file rendered/env run --rm docs ask.py \
      --role staff --question "what is the partner draw?" --doc executive/comp-memo.md

Expected: `denied: executive/comp-memo.md is not readable by role 'staff'`, exit code 3.

Then confirm the refusal is on the record:

    docker compose -f deploy/office/compose/docker-compose.yml --env-file rendered/env run --rm audit list --limit 10

Expected: an `access` event with outcome `denied`.

**Why this matters more than the answer:** the restricted document was never retrieved, so it never
reached a model. The denial is a filter that ran, not an apology printed afterwards.

### 3.7b See the gap: a document we cannot read yet

    python3 tests/fixtures/make_scanned_pdf.py > documents/company/scanned-contract.pdf
    docker compose -f deploy/office/compose/docker-compose.yml --env-file rendered/env run --rm docs ingest.py

Expected:

    FAIL company/scanned-contract.pdf: no extractable text; likely a scan or an image-only PDF,
    which needs OCR (not enabled yet)
    added 0, changed 0, removed 0, skipped 4, failed 1

The command exits non-zero, and the failure is written to the audit log as an `error`.

**Why this test exists.** Everything else in this phase reads text-native documents, so it would pass
whether or not we could handle a scan. A document the system silently ignores is indistinguishable
from a document it has never been told about, and that is how a client stops trusting an answer. The
fixture stays in the repository as the regression test for the day OCR arrives.

**What we gave up by not using Docling, stated plainly:**

| Capability | Now | Consequence |
|---|---|---|
| OCR for scans | Nothing; fails loudly | Scanned contracts and faxes are not searchable until OCR is added |
| Table structure | Text with no rows or columns | A fee schedule or statement becomes a jumble of numbers, which can produce a confidently cited wrong answer. The most dangerous of the three |
| Reading order and layout | The PDF's internal order | Two-column documents can interleave, so a citation is garbled |

The cheap middle path, when we need it: `pdfplumber` (pure Python, no torch) recovers word
coordinates and tables, and `tesseract` through `pytesseract` adds OCR for roughly 50 MB instead of
2 GB. Full Docling remains the target on the GPU box, and the vision reader role in the manifest is
the intended tool for drawings and images.

### 3.8 Verification, which ends the phase

1. A question returns an answer with a document and page citation.
2. The cited page actually contains the fact (open the PDF and look).
3. A document outside the role is refused, and the refusal appears in the audit log.
4. A scanned document fails loudly with a reason rather than being skipped in silence.
4. Re-running ingestion changes nothing.
5. Deleting a document removes its points on the next run.

## Phase 4 — Assistant API  *(outline)*

The single surface the client app talks to: ask, tasks, approvals, activity, settings.

## Phase 5 — Client app  *(outline)*

Ask, Do, Review, Activity, Settings, with citations as first-class objects and the lane indicator.

## Phase 6 — Workflows and one connector  *(outline)*

n8n for deterministic work with an approval gate, plus the first MCP connector.

## Phase 7 — Agent runtime and MCP gateway  *(outline)*

The agent loop with tools, sessions, and human-in-the-loop.

## Phase 8 — Frontier lane  *(outline)*

Routing by data class, redaction, approvals, and a test proving nothing leaves when the lane is off.

## Phase 9 — Office appliance  *(outline)*

Backup, restore drill, signed updates with rollback, drift reporting, support tunnel.

## Phase 10 — Voice  *(outline)*

LiveKit cascade: speech to text, model, text to speech, with the transcript audited.

## Phase 11 — Flagship demo  *(outline)*

The denial appeal agent end to end.

---

## Standing rules

1. One phase at a time. Verify before moving on.
2. Every component gets its reasoning recorded here when it is added, not later.
3. Every image is digest-pinned with its version in a comment.
4. Nothing publishes a port except ingress.
5. Stop the instance when you stop working. The credits are generous, not infinite.
