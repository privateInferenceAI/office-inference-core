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

## Phase 2 — Audit sink  *(outline)*

Append-only record of every model call, action, and approval, with a signed daily export.

## Phase 3 — Documents  *(outline)*

Docling ingestion, chunking with access labels, Qdrant, TEI embeddings and reranker. Ends with a
cited answer and a logged refusal.

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
