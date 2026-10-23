# Runbook

Build log and operating manual for the core system. Commands you can paste, in order, with the
reasoning recorded at the point each component is added.

**Which build this follows: the cloud build.** The box runs the gateway, the database, and the audit
sink. Every generation call goes to Bedrock, cheap tier or frontier tier. There is no model on the box
and no GPU on the box. The office build runs open weights on the client's own hardware; its record is
Appendix A, and those are not steps in this build. On 2026-10-10 this document did not say which build
it followed, and an hour was spent building the wrong lane on the right box.

**The sequence of record** is `docs/build-brief.md` section 11, phases C0 to C9. The phases here cover
the same work in more detail and are not numbered alike, so read the brief for order and this for
commands.

**How to use this:** work one phase at a time. A phase is not finished until its verification step
passes. C6, C7 and the built part of C8 are written as built sections below. Future phases stay as outlines so
nothing here pretends to be current when it is not.

**Status, 2026-10-23:** **C0 to C7 complete. C8 partly built.** C6 added the durable agent runtime and the full
MCP gateway, and the gateway and connector now run in badj.ai client tenant account 426 behind TLS. C7 added
authenticated-session refusals in the chain, a least-privilege support role that cannot read documents, secret
rotation from Secrets Manager, TLS with a closed plaintext port, and Object Lock matched to the contract. C8 has
four verified rows: audit chain version 4, scheduled image scanning, deployment-state drift reporting, and the
incident tabletop. The rest of C8 and the business rows are open.

The authoritative current snapshot is docs/current-deployment.md. Section Phase C6 to C8 below has the commands.

The build box is in Office Inference account 878. The client tenant is badj.ai account 426, instance
i-0b6aa00095ae40e54, public name https://3-85-130-96.sslip.io. The tenant runs the MCP gateway, the email
connector, Caddy and Mailpit. The connector sends through SES as awstest@officeinference.com to that verified
address. Mailpit remains a capture fallback.

## The environment we are building on

| | |
|---|---|
| Account | Office Inference, `878451098165` (holds the Activate credits) |
| Instance | `i-02aba2feef08f8e7b`, m7i.4xlarge, Ubuntu 24.04, 200 GB gp3 encrypted. 16 vCPU, 61 GB RAM, no GPU |
| Elastic IP | `52.204.204.133` (stable across stops; moved from the retired t3.large) |
| SSH key | `ai-stack-key`, private key at `~/ai-stack/ai-stack-key.pem` on the workstation |
| SSH allowed from | 47.164.45.109 only (security group `sg-09d9bfffbd28014c9`) |
| Instance role | `oi-core-dev-instance`: `AmazonSSMManagedInstanceCore` for keyless access, plus `oi-core-dev-bedrock-invoke`, which allows exactly the four models the dev manifest names |
| Inference | Bedrock, us-east-1. No model runs on this box |
| Client tenant | badj.ai account 426063972668, instance i-0b6aa00095ae40e54, public name https://3-85-130-96.sslip.io |
| Tenant services | MCP gateway loopback 8080, connector systemd port 8094, Caddy 80 and 443, Mailpit 1025 fallback, SES identity awstest@officeinference.com |
| Region | us-east-1 |
| State | Build box: stopped when not in use. Client tenant: running while it serves the gateway |

Why this shape: an Elastic IP so the address survives stops; a keyless SSM role so access does not
depend on a key file; and a security group that publishes nothing but SSH from one address. That is
the same posture a client box gets, minus the credit card.

The box is CPU-only because generation happens at Bedrock, not because a small model runs here. It is
sized at 16 vCPU and 64 GB for the components that do run on it later: Docling's layout, table, and OCR
models, and Qdrant serving vectors from RAM.

---

## Phase 0 — Reference machine

**Goal.** A clean host with Docker, a private container network, the repository, and a passing
manifest validation. No GPU, because nothing on this box generates text: the cloud build's inference
is Bedrock. It is an m7i.4xlarge rather than something smaller because later phases put Docling and
Qdrant on it.

### 0.1 Start the instance

From the workstation:

    aws ec2 start-instances --profile oi-credits --region us-east-1 \
      --instance-ids i-02aba2feef08f8e7b
    aws ec2 wait instance-running --profile oi-credits --region us-east-1 \
      --instance-ids i-02aba2feef08f8e7b
    aws ec2 describe-addresses --profile oi-credits --region us-east-1 \
      --allocation-ids eipalloc-0f0560d02bcb32517 --query 'Addresses[0].PublicIp' --output text

Expected output: the instance id, then `52.204.204.133`.

**Why:** every session starts the same way, and stopping the box between sessions is what keeps the
credits lasting. The Elastic IP means the address never changes, so nothing in this runbook needs
editing between sessions.

### 0.2 Connect

    ssh -i ~/ai-stack/ai-stack-key.pem ubuntu@52.204.204.133

If SSH complains about key permissions:

    chmod 600 ~/ai-stack/ai-stack-key.pem

Alternative, no key and no open port:

    aws ssm start-session --profile oi-credits --region us-east-1 --target i-02aba2feef08f8e7b

**Why:** SSH is convenient during the build. Session Manager is the pattern a client box will use,
because it needs no inbound port and no key material in the client's hands, and every session is
logged in their account. Both work here, so you can practise the one clients will live with.

### 0.3 Record the host

    . /etc/os-release && echo "$PRETTY_NAME"
    nproc
    free -g | head -2
    df -h / | tail -1

Expected: Ubuntu 24.04.x LTS, 16 CPUs, 64 GB RAM (about 61 usable), 200 GB disk with most of it free.

**These numbers changed on 2026-10-10.** Phases 0 to 2 below were first run on the t3.large, which had
2 CPUs, 8 GB, and a 30 GB disk. The reference instance is now an m7i.4xlarge, because Docling wants
cores for OCR and layout work and Qdrant serves vectors from RAM. Where a step says "2 CPUs", read it
as history rather than as the machine you are on.

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
      ubuntu@52.204.204.133:/opt/oi-core/

**Option B, clone on the box** (needs GitHub auth on the server):

    sudo mkdir -p /opt/oi-core && sudo chown "$USER":"$USER" /opt/oi-core
    gh auth login          # device code flow
    gh repo clone privateInferenceAI/office-inference-core /opt/oi-core

**Why Option A is listed first:** the repository is private, and cloning it on the server means
storing a token there. Client boxes should never hold our repository credentials, so the habit
starts here. Use Option B when you want the box to pull its own updates.

**And install `oi`, which every command after this one assumes.** The wrapper is in the repository at
`deploy/office/oi` and the rest of this book uses its short form throughout — `oi build`, `oi run`, `oi ps`,
`oi up`, `oi restart`.

```
sudo ln -sf /opt/oi-core/deploy/office/oi /usr/local/bin/oi
oi
```

Expected: the usage text, listing the verbs.

**Why a symlink and not a copy.** The wrapper resolves its own location to find the compose file and the
rendered environment, so a symlink follows the repository through every pull and a copy drifts the moment one
happens.

**Why this step is not optional.** For most of this build the documentation referred to `oi` more than a
hundred times and the command did not exist: not on the box, not in the repository, not as an alias. Every one
of those references failed with `command not found`, including the one in the update procedure:

```
cd /opt/oi-core
git pull
oi build audit keys
```

**That is how a deployment came to be running an image from before a fix that `main` already had.** A teardown
on it cleared every index, wrote a destruction record, and left 10,033 client documents on disk, because the
code that removes them was in the repository and not in the image, and the step that rebuilds the image could
not be run. A documented command that cannot run is worse than no documentation: it turns a missing step into
a step somebody believes they performed.


### 0.9 Validate the manifests

    sudo apt -y install python3-yaml
    cd /opt/oi-core
    python3 scripts/validate_manifests.py

Expected:

    ok   example-compliant-cloud.yaml  (profile=cloud, packs=[...])
    ok   example-hybrid.yaml  (profile=hybrid, packs=[...])
    ok   example-in-office.yaml  (profile=office, packs=[...])
    ok   example-standard-cloud.yaml  (profile=cloud, packs=[...])

    6/6 manifests valid; 8 packs known

**The count moves.** It was 4/4 when this section was written, and 6/6 on 2026-10-10 after
`dev-office.yaml` and `dev-cloud.yaml` were added. What matters is that every manifest passes, not
the number; a manifest appearing here is a new deployment shape, and a manifest failing is the
validator doing its job.

**Why:** the manifest is what a client buys, and the validator is what refuses to install a
configuration that breaks a promise, such as turning the frontier lane on in a build with no cloud
leg. It runs at install time on the client's box, so we run it here first, on ours.

### 0.10 Verification, which ends the phase

    docker ps -a --format '{{.Names}}' | grep -E 'postgres|litellm' || echo clean

Expected: `clean`.

Nothing of ours is running yet, and that is correct. Phase 0 built the floor, not the house.

---

## Phase 1 — Data and gateway (cloud build)

**Goal.** Configuration rendered from the manifest, Postgres, the gateway, and one model call that
travels the whole path and lands in the database as a spend row. **Every generation call goes to
Bedrock**, because this is the cloud build.

**What starts, and what deliberately does not.** Four services are defined and two of them run:

| Service | Runs? | What it is |
|---|---|---|
| `postgres` | yes, healthy | The gateway's keys and spend, and later the audit trail. Becomes RDS in a client account |
| `litellm` | yes | The single control point. Four roles, all of them Bedrock model ids |
| `audit` | on demand | Defined under the `tools` profile: it runs when called and exits, because a resident process would be a container for something that runs for a second |
| `llamacpp` | **no** | The local lane, which belongs to the office build. The renderer leaves its compose profile off, so a cloud deployment never starts it |

That last row is the whole shape of the cloud build. There is no model on this box and no GPU on this
box: generation happens at Bedrock, and the box runs the gateway, the database, and the record.

### 1.1 Render the manifest into configuration

    cd /opt/oi-core
    python3 scripts/render_manifest.py --manifest manifests/dev-cloud.yaml
    python3 scripts/render_manifest.py --manifest manifests/dev-cloud.yaml   # rerun on purpose
    sed -E 's/(PASSWORD|MASTER_KEY|AWS_SECRET|AWS_ACCESS).*/\1=redacted/' rendered/env

Expected, the second run reporting reused secrets:

    rendered dev-cloud.yaml -> /opt/oi-core/rendered
      profile      cloud
      packs        core
      local models (none)
      gateway      4 model entries
      secrets      generated
      env file     /opt/oi-core/rendered/env (mode 600)

**Why the rerun is part of the step:** secrets are generated once and reused. Regenerating the
database password would lock the stack out of its own data directory, because Postgres initialized
with the old one.

**Why `local models (none)` is correct here and not a defect.** `dev-cloud.yaml` names a Bedrock model
for every role and has no local lane. The office build's `dev-office.yaml` is the opposite: local
models, no Bedrock. Same renderer, same compose file, different configuration, and that is the claim
this whole design rests on. If a cloud render ever reports a local model, the wrong manifest was
passed.

**The gateway list is the thing to check, not the file's existence.** Four entries, one per role, each
`bedrock/`-prefixed:

    grep -E 'model_name|model:' rendered/litellm.yaml

### 1.2 Confirm which containers this build starts

    docker compose -f deploy/office/compose/docker-compose.yml --env-file rendered/env config --services

Expected: exactly two lines, `postgres` and `litellm`. If `llamacpp` appears, `rendered/env` came from
an office render and the wrong lane is about to start.

**Why this check exists as its own step.** Compose refuses a project whose service depends on one that
an inactive profile excludes, so `litellm` has no dependency on `llamacpp`. That was verified with
`docker compose up --dry-run`, which failed with:

    service "litellm" depends on undefined service "llamacpp": invalid compose project

One compose file can serve both builds only because the local lane sits behind a profile and the
gateway boots without it. The gateway does not need a model server to start; it needs one per request,
and only on the office build.

### 1.3 Start the stack

    cd /opt/oi-core
    docker compose -f deploy/office/compose/docker-compose.yml --env-file rendered/env up -d
    docker compose -f deploy/office/compose/docker-compose.yml --env-file rendered/env ps

Expected: `postgres` healthy, `litellm` running. First start takes a minute or two while images pull,
Postgres initializes, and the gateway runs its migrations.

Note what is not published: no host ports at all. Everything is reachable on `ai-net` only, which is
why the checks below run a throwaway container on that network.

### 1.4 Verify the gateway

    docker exec litellm python -c "from importlib.metadata import version; print('litellm', version('litellm'))"
    docker run --rm --network ai-net curlimages/curl -s http://litellm:4000/health/liveliness
    docker images --digests | grep litellm

Expected: a version of **1.104.x**, `"I'm alive!"`, and a DIGEST column reading `sha256:3d88bd75...`,
matching the pin in the compose file. The TAG column will say `<none>`, which is correct: pulled by
digest, so no tag was ever stored.

**Why the version check is a step and not a nicety:** LiteLLM's CVEs are version-bound.
CVE-2026-59822 affects everything before 1.84.0 and sits in CISA's KEV catalog. A pin records intent;
the running version reports reality, and only one of those answers a security review.

Three dead ends worth knowing, because each looks right and fails:

- **`docker inspect litellm` inspects the container, not the image.** Containers have no `RepoDigests`
  field.
- **`docker image inspect ghcr.io/berriai/litellm` fails with "No such image".** Omitting a tag makes
  Docker assume `latest`, and a digest pull stores no tag. Use `docker images --digests`, or inspect
  by ID.
- **This image ships no `org.opencontainers.image.version` label**, so a label check returns empty. Not
  every publisher sets one.

### 1.5 Make a call through the gateway, and make it reach Bedrock

    KEY=$(grep '^LITELLM_MASTER_KEY=' rendered/env | cut -d= -f2-)
    docker run --rm --network ai-net -e KEY="$KEY" curlimages/curl -s \
      -X POST http://litellm:4000/v1/chat/completions \
      -H "Authorization: Bearer $KEY" -H 'Content-Type: application/json' \
      -d '{"model":"analyst","messages":[{"role":"user","content":"In one sentence, what is a gateway?"}],"max_tokens":60}'

Expected: JSON with an answer, and the `model` field naming a Nova model. A second or two, not the
seventy a CPU model took.

**Why this is the phase's real test.** One request exercises authentication, role routing, the AWS
credential chain, the Bedrock call, and the spend record. The model name `analyst` is a role, not a
model: that indirection is what lets the tier change without touching anything upstream.

**How the container gets AWS credentials.** The instance carries the role `oi-core-dev-instance`, and
the gateway picks credentials up from the instance metadata service. Two things make that work, and
both are configuration rather than luck: the metadata hop limit is 2, which is what lets a container
reach IMDS at all, and the role carries a policy naming exactly the four models in the manifest. No
key material exists on the box or in the env file.

**If it fails, the error names the cause:**

| Error | Cause |
|---|---|
| `AccessDeniedException` | The model id and the role's policy disagree. Compare the id in `rendered/litellm.yaml` against the policy's resource list |
| `Unable to locate credentials` | IMDS is unreachable from the container, usually the hop limit, or the instance has no role |
| `ValidationException: on-demand throughput isn't supported` | That model is inference-profile only, which is the case for Claude. It needs a `us.` profile, and it is why the dev manifest uses `ON_DEMAND` models |
| `ResourceNotFoundException` | The model id is not available in this Region, or not spelled exactly |

### 1.6 Find the spend row

    docker exec postgres psql -U oicore -d oicore -c '\dt' | tail -12
    docker exec postgres psql -U oicore -d oicore -c 'select count(*) from "LiteLLM_SpendLogs";'
    docker exec postgres psql -U oicore -d oicore -c \
      'select request_id, model, total_tokens, "startTime" from "LiteLLM_SpendLogs" order by "startTime" desc limit 3;'

Expected: your call, with the model, the token count, and a timestamp.

**Why this matters more than it looks:** the record exists from the first call, without us writing a
line of logging code. Every call is recorded with which key made it, which model answered, how many
tokens it cost, and when. Phase 2 adds our own append-only sink for actions and approvals; the
model-call record starts here.

### 1.7 Verification, which ends the phase

All four must pass:

1. `health/liveliness` returns alive.
2. `config --services` lists `postgres` and `litellm`, and no local model server.
3. A chat completion returns an answer, and the response names a Bedrock model.
4. That call appears in `LiteLLM_SpendLogs`.

### 1.8 C0 closed, 2026-10-11

C0's exit criteria come from `docs/build-brief.md` section 11. What satisfied each one:

| Criterion | Evidence |
|---|---|
| The stack runs healthy on CPU | `postgres` healthy, `litellm` running with `restarts=0`, `"I'm alive!"` from `health/liveliness` |
| `test_audit_verify.py` passes | `all four attacks are now caught`, exit 0, run on the box |
| `validate_manifests.py` passes | `6/6 manifests valid; 8 packs known` |
| Repo, compose, manifest rendered, CI | `main` at the C0 merges, two containers up from a rendered `dev-cloud.yaml`, CI green on every pull request |

**The first cloud call, for the record.** `analyst` resolved to `bedrock/amazon.nova-lite-v1:0` and
answered in about a second: 41 tokens, 9 prompt and 32 completion, recorded in `LiteLLM_SpendLogs`
under `chatcmpl-2152501b-e2a5-4dfa-8919-d544a4ae410a`. The response body named the role and the spend
row named the model, which is the indirection doing its job in public.

**Two defects surfaced during C0, both fixed with tests that lock them:**

1. **The validator refused our own hosted tier.** It demanded `account_owner: client` while the schema
   allowed `office_inference`, so no development manifest could validate and the hosted offering could
   not be expressed at all. Pull request #14.
2. **The Postgres entrypoint created tables in the gateway's schema.** Mounting `db/migrations` at
   `/docker-entrypoint-initdb.d` ran our audit migration against a virgin data directory in the
   database LiteLLM keeps its Prisma schema in, and Prisma refused to baseline. The gateway
   restart-looped eight times. Pull request #16.

Both were fresh-install bugs. Neither would have appeared on a box that had been built incrementally,
which is exactly why the reference instance gets built from scratch.

**Cost so far.** Two containers and one stopped instance: about USD 3.65 a month for the elastic
address, and the instance hours only while it runs.

### If it breaks

- **litellm restarting in a loop, `status=running` but nothing answers on :4000:** read the logs
  before anything else, because the message names the cause. If it says *"Database migration cannot
  proceed. Cannot automatically baseline this database: its schema has not been verified to match this
  build"*, the database holds tables Prisma did not create. That happened on 2026-10-10 and the cause
  was ours: `db/migrations` was mounted into the Postgres entrypoint, which runs those files against a
  virgin data directory and created `audit_events` in the gateway's own database. Check with
  `docker exec postgres psql -U oicore -d oicore -c '\dt'`. If you see our tables and no
  `_prisma_migrations`, that is the collision. The mount is gone from the compose file; a volume that
  already has the bad state needs the two tables dropped, or the volume wiped.
- **`llamacpp` appears in `config --services`:** `rendered/env` is from an office render. Re-render
  `dev-cloud.yaml`; the renderer removes `COMPOSE_PROFILES` for a cloud manifest.
- **litellm unhealthy:** check that postgres is healthy first, then `docker logs litellm | tail -30`.
  A bad password in `DATABASE_URL` shows up as an authentication error.
- **curl cannot resolve `litellm`:** the container has to be on the same network. `--network ai-net`
  is not optional, and omitting it is the most common version of this mistake.
- **curl prints nothing at all:** `-s` hides connection errors. Use `-sS`, or drop `-s`, and you get
  `curl: (7) Failed to connect to litellm:4000` instead of silence. An empty answer from a silent
  curl looks like a broken model and is usually a container that is not listening.

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

**That reasoning was right, and the shortcut it described was still in the compose file.** Postgres
mounted `db/migrations` at `/docker-entrypoint-initdb.d` as well, so a fresh install got our tables
without this step. On 2026-10-10 that broke the gateway: the entrypoint created `audit_events` in the
same database LiteLLM keeps its Prisma schema in, and Prisma refused to baseline a schema it had not
created. The mount is gone. `audit migrate`, right here, is the only path, and the order for a fresh
install is now: bring the stack up so the gateway migrates its own tables, then run this step.

### 2.2 Record an event

    docker compose -f deploy/office/compose/docker-compose.yml --env-file rendered/env run --rm audit \
      record --dev --type access --actor anthony --actor-role admin --data-class internal \
      --payload '{"document":"expense-policy.pdf","pages":3}'

Expected: JSON with an id, a timestamp, a `row_hash`, and a `payload_sha256`.

    docker compose -f deploy/office/compose/docker-compose.yml --env-file rendered/env \
      run --rm audit list

**Why `--dev` is on that command.** An actor is an identity, not a word someone types. Outside
development mode `record` refuses a hand-typed actor and exits non-zero, because a deployment records
events through the assistant API with the identity from an authenticated session. The flag exists so
the developer can work on a box that holds no client data, it prints a warning every time it is used,
and C3 is where it stops being needed.

**Why the payload is hashed and not stored by default:** the record needs to prove *what* was sent
without becoming a second copy of the client's documents. `--store-payload` keeps the content, but
that is a policy decision a client makes, not a default we choose for them.

### 2.3 Prove it is append-only

    docker exec postgres psql -U oicore -d oicore -c \
      'update audit_events set actor = '"'"'someone else'"'"' where id = 1;'
    docker exec postgres psql -U oicore -d oicore -c 'delete from audit_events where id = 1;'

Expected: both raise `audit_events is append-only; UPDATE is not permitted`, and the same with `DELETE`,
each with a hint naming the maintenance setting. The row is still there afterwards, unchanged.

**The escape hatch, corrected on 2026-10-11.** This step used to end with

    ... set actor = 'maintenance' where id = 1;' --set=oi.allow_audit_maintenance=on

which cannot work. `psql --set` defines a **client** variable, and the trigger reads
`current_setting('oi.allow_audit_maintenance', true)`, which is a **server** setting. The variable
never reaches the server, so the trigger sees nothing and the update is refused exactly as before. The
command looked like a passing check while proving nothing, which is the failure mode this runbook
exists to catch. The brief counts it among four verification steps that cannot fail.

The form that works is a transaction, which is what a retention purge will run:

    docker exec postgres psql -U oicore -d oicore -c \
      "begin; set local oi.allow_audit_maintenance = 'on'; \
       update audit_events set actor = 'maintenance' where id = 1; commit;"

**Know what that costs before running it.** The chain trigger fires on insert only, so an update that
slips through the hatch leaves the row's `row_hash` describing contents that are no longer there. The
alteration is detectable afterwards, and nothing records that the hatch was used. That gap is
`AUD-PURGE-001` in the register, and C1 closes it with a tombstone. Put the row back afterwards and the
hash matches again, because the contents return to what it was computed from:

    docker exec postgres psql -U oicore -d oicore -c \
      "begin; set local oi.allow_audit_maintenance = 'on'; \
       update audit_events set actor = 'anthony' where id = 1; commit;"

Verify it either way, because this is the check a third party would run:

    docker exec -i postgres psql -U oicore -d oicore <<'SQL'
    select id, actor,
           row_hash = encode(digest(concat_ws('|',
             ts_iso, event_type, actor, coalesce(actor_role, ''), deployment_id,
             data_class, outcome, coalesce(tool, ''), coalesce(model, ''),
             coalesce(request_id, ''), payload_sha256, payload_bytes::text,
             coalesce(prev_hash, '')), 'sha256'), 'hex') as hash_matches_contents
    from audit_events order by id;
    SQL

Expected: `t` after a restore. While the row still says `maintenance`, it reports `f`, which is the
hash chain doing the only job it can do after the fact.

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

### 2.7 Phase 2 run on this deployment, 2026-10-11

All six passed, against real data rather than the synthetic rows in `tests/test_audit_verify.py`.
What exists on the box as a result:

| Artifact | What it is |
|---|---|
| `audit_events` | Two rows: the hand-recorded access event, and the model call backfilled from the gateway |
| `schema_migrations` | `002_audit_events.sql`, checksum `2d96020ef5e0` |
| `exports/2026-10-11/audit-events.jsonl` | The day, one canonical JSON object per line |
| `exports/2026-10-11/manifest.json` | Event count, first and last ids, the hash the first row chains back to, and the file's own sha256 |
| `exports/anchors/anchor-2026-10-11-00000002.json` | The chain head at 01:05: `768f01c8…`, two events |

The manifest and the anchor agree on the head hash, and they were produced by two separate commands
two minutes apart, which is what makes them a cross-check rather than a restatement.

**The backfilled row is worth reading once.** Its actor is `gateway-key:05315140`, a fingerprint of
the API key rather than the key, so calls can be told apart without the record holding a credential.
Its payload *is* stored, unlike the hand-recorded event's, because what it holds is metadata: model,
token counts, spend, provider.

**Three things surfaced while running this phase, all now written down:**

1. **The escape-hatch command could never work** (`psql --set` sets a client variable, the trigger
   reads a server setting). Corrected in 2.3, with what the hatch costs recorded beside it. Fixed in
   pull request #18.
2. **`audit anchor`'s default output path is inside the container.** The default is `anchors` relative
   to `/app`, which is not a mounted directory, so the one artifact that must survive a container exit
   is discarded by default. Use `--out /app/exports/anchors`. The anchor job in C1 should settle this
   by defining the path rather than leaving it to a flag.
3. **`backfill`'s skip counter can never be anything but zero**, because its query only selects rows
   that are not already in the chain. The idempotence is real and the message is noise. Cosmetic.

**What is not done, and belongs to C1:** nothing ships the anchor off this machine on a schedule, a
purge still leaves no tombstone, the record proves a call happened but not what was said, and the
daily export does not leave the box. Each is a named item in `docs/requirements.yaml`.

**Where these artifacts go next, and what may leave the building, is `docs/audit-evidence.md`.** That
document covers the bucket, the retention mode, the permissions that stop the box erasing what it
wrote, and the three build shapes, including an office appliance that has its own AWS account without
being a hybrid deployment.

### 2.8 Ship the evidence off the box

**Run on 2026-10-11**, and what it produced:

| Evidence | Result |
|---|---|
| Anchor shipped | `anchors/anchor-2026-10-11-00000002.json`, 276 bytes, hashes matched on both sides |
| Export shipped | `exports/2026-10-11/audit-events.jsonl` (1,299 bytes) and `manifest.json` (405 bytes) |
| Retention applied | All four objects locked `GOVERNANCE` until **2027-01-09**, from the bucket default |
| Delete refused | `AccessDenied`, naming `assumed-role/oi-core-dev-instance/i-02aba2feef08f8e7b` and `no identity-based policy allows the s3:DeleteObject action` |
| Deleted tail caught | An export with its last row removed failed verification against the shipped anchor: `the anchored row 768f01c89c08... is missing from this export` |

The delete attempt is what this whole slice exists for. The same call succeeded from a workstation
credential an hour earlier. Same bucket, same object, two identities, two answers.

**On "delete it as root":** root on the box gets the same denial, because EC2 credentials come from the
instance role through the metadata service and do not vary by local user. The denial names the role
rather than a user for exactly that reason.

The job is two containers and nothing installed on the host. The **audit tool** writes the anchor or
the export, because it owns the chain and knows how to produce them. The **ship image** delivers the
file and reads it back to compare hashes, because the audit tool is not allowed a cloud dependency:
the office appliance runs that same tool with no bucket in sight.

Install the timers:

    cd /opt/oi-core
    sudo cp deploy/cloud/systemd/oi-core-*.service deploy/cloud/systemd/oi-core-*.timer /etc/systemd/system/
    sudo systemctl daemon-reload
    sudo systemctl enable --now oi-core-anchor.timer oi-core-export.timer
    systemctl list-timers 'oi-core-*'

Run one by hand first, which is also how you debug it:

    ./deploy/cloud/ship/oi-core-ship.sh anchors
    ./deploy/cloud/ship/oi-core-ship.sh exports

Expected, for the anchor:

    anchored 2 event(s), head 768f01c8...
      /app/exports/anchors/anchor-2026-10-11-00000002.json
      Ship this file off this machine. Kept here, it proves nothing.
    shipping /evidence/anchors/anchor-2026-10-11-00000002.json (276 bytes)
      local  sha256 ...
      remote sha256 ...
      ok: s3://oi-core-audit-dev/anchors/anchor-... matches the file that was sent
    anchors shipped to s3://oi-core-audit-dev

Then see what the timers have been doing:

    journalctl -u oi-core-anchor.service -n 40
    systemctl status oi-core-anchor.timer

**Why the read-back is inside the script.** From the sending side a complete upload and a truncated one
look identical: both are silent and both exit zero. Fetching the object and hashing it is the
difference between "we sent it" and "it is there and it matches".

**Why `Persistent=true` on both timers.** The box is stopped when nobody is working, so a scheduled run
gets missed. Persistent means a missed run happens once, after the box starts, rather than silently
never happening.

**If it breaks:** `no offsite destination in the manifest` means the job correctly found nothing to do.
`AccessDenied` on the upload means the role's policy and the bucket name disagree. `AccessDenied` on a
*delete* is the system working, and section 5 of `docs/audit-evidence.md` explains why the box is not
allowed to do it.

### 2.9 Per-role keys with hard budgets

**Run on 2026-10-11.** Both restrictions held on the box, and each refusal is harder than the phrase
"hard budget" promises on its own. The retention purge and the classification fix below landed the
same night, so this section is the whole of C1's audit-record work in one place:

| Test | Result |
|---|---|
| `--probe coder` with the role capped below its spend | **422** `Budget has been exceeded! Key=oi-core-dev-cloud:coder Current cost: 2.104e-05, Max budget: 1e-06` |
| `--probe coder` again | **422**, the same |
| Restore the manifest's ceiling, then probe | **200**, answered by coder, 13 tokens |
| `--probe analyst --as-model reader` | **403** `The requested model 'reader' is not available for this API key` |

**Rebuild the tool images after a code change.** The tools run from images, and `docker compose run`
uses the image it already has. A change to `services/audit/audit.py` or `services/gateway/keys.py`
does nothing until:

    docker compose -f deploy/office/compose/docker-compose.yml --env-file rendered/env build audit keys

That cost an hour on 2026-10-11: a backfill ran the old code and printed one line where two were
expected, which looked like a data problem and was a stale image.

**What the ceiling promises, and what it does not.** The gateway checks the budget against *recorded*
spend, and the spend write lands a moment after the call returns. So a cap stops a runaway loop, and it
does not promise zero calls past the line: the call that crosses completes, and in a fast burst more
than one can slip through before the recorded number catches up. Measured here: the first drill let
three calls through, and a later drill against a key that already carried spend refused both
immediately. Write the client-facing promise as "the loop is stopped", not "the number is never
exceeded".

**Refusals reach the chain through the spend log, not the error log.** `LiteLLM_ErrorLogs` stays empty
through budget and authorization refusals, because both are decided before a request is routed. The
spend log holds them anyway: `status = 'failure'`, a zero-token usage object, the reason under
`metadata.error_information` with its code, class, and budget numbers, and `user_api_key_alias` naming
the role that asked. `audit backfill` reads all of that and records `denied` for a 4xx and `error` for
anything else.

An earlier version of the backfill ignored `status` and recorded refusals as successful calls with no
tokens. Keep that in mind when adding a source to the record: a row saying "a model answered" about a
call the gateway refused is worse than a missing row, because nothing about it looks wrong.

Every component that calls a model uses a key for the **role** it is acting as, and each key carries
two restrictions: it may call one model, and it has a ceiling in dollars. The master key mints those
keys and appears nowhere else, which is what makes it an administration credential instead of a
shared secret.

The ceilings come from the manifest:

    budgets:
      period_days: 30
      roles:
        front_desk: 2
        analyst: 10

A role with no budget is a validation failure whenever the frontier lane is on, because a remote call
costs money and an uncapped role is how a loop empties an account.

Mint or update them:

    cd /opt/oi-core
    docker compose -f deploy/office/compose/docker-compose.yml --env-file rendered/env run --rm keys

Expected:

    keys for oi-core-dev-cloud:
      analyst    minted   models=[analyst]  10.0 USD per 30d
      coder      minted   models=[coder]  5.0 USD per 30d
      front_desk minted   models=[front_desk]  2.0 USD per 30d
      reader     minted   models=[reader]  10.0 USD per 30d
      secrets in /app/rendered/keys.json (mode 600) and never printed

Running it a second time reports `updated` rather than `minted`, and the secrets do not change,
because components already hold them. The gateway's database is the source of truth for the policy;
`rendered/keys.json` is the source of truth for the secrets.

**The drill, which is what the requirement actually asks for.** A ceiling that reports is not a
ceiling, so narrow one and prove the refusal:

    docker compose -f deploy/office/compose/docker-compose.yml --env-file rendered/env \
      run --rm keys --set-budget coder 0.000001
    docker compose -f deploy/office/compose/docker-compose.yml --env-file rendered/env \
      run --rm keys --probe coder

Expected: the probe exits non-zero and prints the gateway's reason, `budget exceeded for this key`.
Then put it back, which also demonstrates idempotence:

    docker compose -f deploy/office/compose/docker-compose.yml --env-file rendered/env run --rm keys
    docker compose -f deploy/office/compose/docker-compose.yml --env-file rendered/env \
      run --rm keys --probe coder

**The model ACL.** A key may call its own model and nothing else:

    docker compose -f deploy/office/compose/docker-compose.yml --env-file rendered/env \
      run --rm keys --probe analyst --as-model reader

Expected: a refusal. The analyst key is scoped to `analyst`, so asking for `reader` is denied at the
gateway rather than by convention.

**`--set-budget` is not only a drill.** It is the emergency brake: when something is looping, capping
the role stops it now instead of at the next deploy. The next plain run restores the manifest's value.

**The master key check** (`SEC-KEY-002`). Exactly one service has it in its environment, and it is
the gateway itself:

    docker compose -f deploy/office/compose/docker-compose.yml --env-file rendered/env config \
      | python3 -c "import sys, yaml; d = yaml.safe_load(sys.stdin); print([s for s, v in d['services'].items() if 'LITELLM_MASTER_KEY' in (v.get('environment') or {})])"

Expected: `['litellm']`. The key tool reads the master key from the rendered env *file* through its
mount rather than from an environment variable, which is why it does not appear in its own
environment and why the answer stays a one-item list.

**If it breaks:** `cannot reach the gateway` means the tool is not on the `ai-net` network, which is
the only way to reach a gateway that publishes no port. `no rendered manifest` means the renderer has
not run. A refusal on a probe for the key's *own* role means the ceiling is doing its job, not that
the key is broken.

### 2.10 Retention purge, and the tombstone that explains it

**Written and tested against a scripted cursor; not yet run on this deployment.** The heading gets its
marker when a real purge has left a real tombstone.

A retention purge is a deliberate hole in the chain. Without a tombstone the hole is indistinguishable
from tampering: rows are simply missing, and the only artifact that could tell the difference is an
anchor, which would report the loss without knowing why.

    cd /opt/oi-core
    docker compose -f deploy/office/compose/docker-compose.yml --env-file rendered/env \
      run --rm audit purge --before 2026-10-01 --reason "retention: 90 days" --dry-run

Expected, on a record with nothing that old: `nothing to purge before 2026-10-01`, and the chain head.

Then for real, without `--dry-run`:

    docker compose -f deploy/office/compose/docker-compose.yml --env-file rendered/env \
      run --rm audit purge --before 2026-10-01 --reason "retention: 90 days"

Expected:

    purged 4 event(s) before 2026-10-01
    {
      "tombstone_id": 7,
      "ts_iso": "2026-10-11T...",
      "head_before": "...",
      "head_after": "...",
      "deleted_count": 4,
      ...
    }

**Why the tombstone is written before the rows are deleted.** Its `prev_hash` is then the chain head
from before the purge and its `row_hash` is the head after, so the two hashes the record needs are the
row's own columns rather than a copy that could drift from them. Reverse the order and the tombstone
chains from the surviving rows, and the hash from before the gap is lost with the rows it described.

**The whole thing runs with the maintenance setting on**, inside one transaction, because the
append-only trigger refuses both the insert and the delete. That setting is the escape hatch 2.3
demonstrates by hand; this is the one operation that uses it, and it is why the tombstone matters: the
hatch leaves no trace except through what it writes.

**Reading the tombstone afterwards:**

    docker compose -f deploy/office/compose/docker-compose.yml --env-file rendered/env \
      run --rm audit list --type purge

**A purge is not undone by the next backfill.** The gateway's spend logs are not subject to our
retention window, so without a watermark the next `audit backfill` would re-create every model call the
purge had just removed. The watermark is the newest tombstone's cutoff, and both backfill queries
respect it.

**What a purge does to anchors written before it.** An anchor from before the purge records a head hash
whose row is now gone, so `verify --anchor` against it will report the missing row. That is the honest
outcome rather than a bug: the rows are gone, and the tombstone is the artifact that says why. A client
under a retention obligation should be told that a purge invalidates earlier anchors and that the
tombstone is what carries the explanation forward.

**If it breaks:** `audit_events is append-only; DELETE is not permitted` means the maintenance setting
did not reach the transaction. On 2026-10-11 that happened for real, and the damage was worse than a
failed command: the connection is in autocommit mode, so `SET LOCAL` outside a transaction block was
ignored, the tombstone inserted anyway because nothing blocks an insert, and the delete was refused
afterwards. The result was a tombstone claiming ten purged events with all ten still present.

Two changes came out of it. The purge now runs its insert and its delete inside one explicit
transaction, so either both happen or neither does. And migration 004 adds a trigger that refuses a
`purge` row unless the maintenance setting is on, so a bare tombstone cannot be written at all, by this
tool or any other.

That is also why the tests assert the transaction block is opened rather than only that the `SET LOCAL`
statement was issued. The first version of those tests passed while the command was broken, because
they checked the words and not the effect.

## Phase 3 — Documents

C2. The phase that makes this system answer from a client's own material instead of from a model's
general knowledge. It is the largest phase so far, and it is built one step at a time.

**The acceptance test, from the brief:** a cited answer from a scanned, table-bearing document; a
recorded refusal; a superseded document not cited; and redaction proven by a test.

**The pieces, and what each is for.** Docling parses documents, including scans and tables, into text
with page numbers. An ingestion worker turns that text into chunks, embeds them, and writes them to
Qdrant with the access labels the search filters on. Two TEI services serve the embedding model and
the reranker on the box, so no document content leaves for either. Retrieval is hybrid: a vector arm
for meaning and a lexical arm for the exact identifiers that vectors miss. Every answer carries
citations, and every citation is checked three ways before a client sees it.

**What runs where.** Four new containers, all on `ai-net`, none publishing a port: `vector` (Qdrant),
`embeddings` and `reranker` (TEI), and `ingestion` (the worker). They are the services the
`document-chat` pack declares, which is why a manifest that lists that pack already asks for them.

**The steps, and what each proves.**

| Step | Builds | Proves |
|---|---|---|
| 3.1 | The vector store | A point round-trips: written, indexed, and found by a query |
| 3.2 | Embeddings on the box | Text becomes a 1024-dimension vector, deterministically, without a network call |
| 3.3 | The reranker | A pair's order changes by relevance, which is what makes top-10 worth reading |
| 3.4 | The parser | A scanned, table-bearing PDF becomes structured text with page numbers and cell boxes |
| 3.4b | The confidence question | A confidently wrong read is caught by redundancy or disclosed, not trusted |
| 3.5 | The chunker | A page becomes chunks that keep the page intact as a citation unit, and a table the parser split in two is joined before it is chunked |
| 3.6 | The fixture corpus | Real business documents with their licences read at the source, and a hand-written question set |
| 3.7 | The ingestion worker | A document on disk becomes chunks in the vector store once, and a re-run costs one read per file |
| 3.8 | Access labels | A role cannot retrieve a chunk its label excludes, because the filter is inside the search |
| 3.9 | Hybrid search | An exact identifier that the vector arm misses is found by the lexical arm and surfaces |
| 3.10 | Versioning and duplicates | A superseded document is not cited while its replacement exists; a near-duplicate is flagged at ingest |
| 3.11 | Citations, three checks | A citation that is fabricated, on the wrong page, or unsupported by its chunk is refused |
| 3.12 | Redaction before the frontier | A test shows the outbound payload carries no PII, and the record says what was redacted |
| 3.13 | Injection resistance | A document containing instructions cannot change the lane or the answer's provenance |
| 3.14 | Document retention | A client-requested purge removes content everywhere; a legal hold blocks it |
| 3.15 | Verification | The brief's acceptance test, run end to end, with the outputs recorded here |

**The table grew during the build, and the numbering follows the sections below rather than the plan.**
Four steps were added once measurement showed they were separate jobs: the parser (3.4) because
extraction is where a confidently wrong read is born; the confidence question (3.4b) because it is a
decision about what to do with a bad read rather than a parsing task; the chunker (3.5) because the
citation unit is decided there and nowhere else; and the fixture corpus (3.6) because the evaluation
policy requires a corpus and questions before anything can be measured. Step 3.7 absorbs what the plan
called ingestion.

**Order matters.** 3.1 to 3.3 build the floor and have nothing to do with documents yet; they are
proved with hand-made input, which keeps a retrieval bug from hiding inside a parsing bug. 3.4 is the
first step that needs a real document, and 3.4b is the first that asks what happens when a real
document is read wrongly. 3.7 comes before 3.8 because a filter bolted on after the search returns
results is the mistake this phase exists to avoid, and because the label is attached at ingest, so
there has to be an ingest before there is a label.

**Two things not to confuse.** The content modes from C1 decide what the *record* keeps; 3.9 is about
what leaves the building for a frontier model, and the record's pattern-based redactor is not a
substitute for it. And access labels are per chunk, decided at ingest, not per document.

### 3.1 The vector store

**Goal.** Qdrant running on the box, on the private network, holding its data where the rest of the
deployment lives, with a collection whose shape matches the embedding model we are going to use.

**Commands.**

    cd /opt/oi-core
    git pull
    oi up -d vector
    oi ps

**Expected.** `vector` appears alongside `postgres` and `litellm`, and its state is `Up`.

**What just happened.** The manifest's `document-chat` pack declares a service called `vector`, and
that name is now a container. It is on `ai-net` like everything else, and publishes no port, so the
only way to reach it is from another container on that network. Its storage is a bind mount under
`/opt/oi-core/data/qdrant`, which is the same root the database uses, so the retention and backup
story stays in one place.

**Verify.** From a container on the network, ask Qdrant for its readiness and its version:

    docker run --rm --network ai-net curlimages/curl -sS http://vector:6333/readyz
    docker run --rm --network ai-net curlimages/curl -sS http://vector:6333/

Then create the collection, which is the shape every later step depends on: 1024 dimensions because
the embedding model produces 1024, and cosine distance because that is how these vectors compare.

    docker run --rm --network ai-net curlimages/curl -sS -X PUT http://vector:6333/collections/documents \
      -H 'Content-Type: application/json' \
      -d '{"vectors": {"size": 1024, "distance": "Cosine"}}'

And prove a point round-trips, with a hand-made vector so nothing here depends on a model:

    docker run --rm --network ai-net curlimages/curl -sS -X PUT \
      http://vector:6333/collections/documents/points \
      -H 'Content-Type: application/json' \
      -d '{"points":[{"id":1,"vector":[0.1,0.2,0.3],"payload":{"text":"a probe"}}]}'

The vector above is three numbers where the collection expects 1024, so this last one is *supposed* to
be refused with a message naming the size. That refusal is the proof the collection has the shape we
asked for. Creating a 1024-long vector by hand is not worth typing; step 3.2 produces real ones.

**If it breaks.**

- `vector` starts and immediately exits: check `docker logs vector`. The usual cause is a storage
  directory the container cannot write, which on this box means `/opt/oi-core/data/qdrant` is owned by
  root rather than by uid 1000.
- A connection refused from the curl container: the service is on `ai-net` and the curl container has
  to join that network. Forgetting `--network ai-net` is the common mistake, and the error reads like
  the service is down.

### 3.2 Embeddings on the box

**Goal.** Text becomes a vector the collection can hold, deterministically, from a service on the private
network that makes no call out of the building.

**Commands.**

    cd /opt/oi-core
    docker run --rm --network ai-net curlimages/curl -sS http://embeddings:80/info

    docker run --rm --network ai-net -v /opt/oi-core:/w:ro -v /tmp/embed_verify.py:/e.py:ro -w /w \
      --entrypoint python ghcr.io/docling-project/docling-serve-cpu:v1.36.0 /e.py

**Expected.** The service reports `BAAI/bge-m3`, and the measurement reports 1024 dimensions, two calls on
the same text being identical, a unit L2 norm, and a batch whose vectors match the same texts embedded one
at a time.

**What just happened, measured 2026-10-13.**

| | |
|---|---|
| model | `BAAI/bge-m3`, float32 |
| dimensions | **1024**, which is what the collection is built for |
| L2 norm | 1.0000, so the vectors are normalised and cosine is a dot product |
| determinism | two calls on the same text are byte-identical |
| batch order | a batch of three matches three solo calls, each to its own text |
| discrimination | 0.41 cosine against an unrelated sentence, so equal vectors do not stand for anything |

**Why each of those matters rather than being a formality.** The dimension is what the collection's shape
depends on, and a model change that altered it would be rejected by Qdrant at write time rather than
silently producing a second kind of vector. Determinism is what makes a re-ingest idempotent: the same bytes
give the same vector, so a re-run updates a point rather than creating a second one that ranks alongside it.
Order is what lets the worker zip vectors back onto chunks by position, which is the one assumption in the
ingestion path that nothing else checks. And the normalisation is why the collection uses cosine.

**What this step does not measure, and it is a real gap.** bge-m3's throughput on this box. The
28-hours-for-50,000-pages figure in the architecture is arithmetic, not measurement, and it could be wrong by
three times either way. It gates any client-facing throughput or pricing promise, and it is recorded in
`docs/search-architecture.md` under what is still unverified rather than being estimated here.

**If it breaks.**

- `no embedding endpoint answered`: the walk in `embed_texts` tries `/embed` then `/v1/embeddings`. Both
  work on this service; `/embeddings` is the older shape and answers 422. A 422 on all of them means the
  request body is wrong rather than the endpoint missing.
- Dimensions not 1024: the model the container was started with is not the one the collection expects.
  `docker logs embeddings | grep model` names what it loaded.
- The first call takes a minute: the warmup, bounded by `MAX_WARMUP_SEQUENCE_LENGTH` and
  `MAX_WARMUP_BATCH_SIZE` in the compose file, which is what turns a ten-minute first boot into twenty
  seconds.

### 3.3 The reranker, measured

**Goal.** Answer one question with a number rather than an opinion: **can a reranker on this box serve
this pipeline at all?** Not how many candidates fit a budget, which is what this step originally asked and
which turned out to be the wrong question: the box being measured was never going to serve the cloud build,
and the cloud build is the one being built.

The measurement still settled the build. It established the negative by a wide enough margin that there
was no depth worth tuning to, which is why the cloud build reranks at Bedrock and the CPU reranker ships
in no build. That is a useful result reached by asking a badly framed question, and the framing is recorded
here rather than quietly improved, because the same mistake is easy to make again: **measure the thing you
are going to run, not the thing sitting in front of you.**

**Commands.** The service first, then the measurement. The reranker is behind the `bench` profile, so
it does not start with the stack:

    cd /opt/oi-core
    git pull
    docker compose -f deploy/office/compose/docker-compose.yml --env-file rendered/env \
      --profile bench up -d reranker

    docker run --rm --network ai-net \
      -v /opt/oi-core/scripts:/app/scripts:ro \
      python:3.12-slim python /app/scripts/bench_rerank.py --depth 25 50 100
    docker run --rm --network ai-net \
      -v /opt/oi-core/scripts:/app/scripts:ro \
      python:3.12-slim python /app/scripts/bench_rerank.py --depth 25 50 --words 420

The first start downloads 2.2 GB into `data/hf-cache`, shared with `embeddings`, and takes about two
and a half minutes, of which 33 seconds is the download and the rest the bounded warmup.

**The measurements, 2026-10-11, idle box, load average 0.00, 16 vCPU, nothing else running.**

| Passage length | Candidates | Median | Per candidate | Per token |
|---|---|---|---|---|
| 56 to 70 words (about 85 tokens) | 25 | 4,897 ms | 196 ms | 2.3 ms |
| 56 to 70 words | 50 | 10,237 ms | 205 ms | 2.4 ms |
| 56 to 70 words | 100 | 19,764 ms | 198 ms | 2.3 ms |
| 420 words (about 570 tokens) | 25 | 45,185 ms | 1,807 ms | 3.2 ms |
| 420 words | 50 | 91,202 ms | 1,824 ms | 3.2 ms |

**Read it per token, not per candidate.** That is the only reading that transfers. Cost is flat in the
number of candidates, so there are no economies of scale in depth: doubling the pool doubles the time.
Cost rises with passage length at roughly 2.5 to 3 ms per token. A five-second budget therefore buys
about 1,700 to 2,000 tokens of candidate text, which is three or four of our 500-token chunks.

The earlier estimate that put a 50-candidate pool at the borderline was sized on the short passages.
Against real chunks it was wrong by about eighteen times on the number that matters.

**The decision, recorded 2026-10-11.**

- **Cloud build: Bedrock `cohere.rerank-v3-5:0`.** No reranker on the box at all. `us-east-1`,
  ON_DEMAND, ACTIVE since 2024-12-01. One call per query, 1 to 1,000 sources, returning an index and a
  relevance score.

**Bedrock measured, 2026-10-13.** The paragraph above used to say "a few hundred milliseconds on their
hardware" with no measurement behind it, which is the habit this project has already paid for five times
over. Measured through the AWS CLI with the CLI's own startup subtracted as a baseline, three runs each:

| Passage words | Candidates | Service time | Per candidate |
|---|---|---|---|
| 85 | 10 / 25 / 50 | 0.161 / 0.218 / 0.262 s | 16.1 / 8.7 / 5.2 ms |
| 250 | 10 / 25 / 50 | 0.216 / 0.286 / 0.389 s | 21.6 / 11.4 / 7.8 ms |
| 570 | 10 / 25 / 50 | 0.227 / 0.234 / 0.342 s | 22.7 / 9.3 / 6.8 ms |

**Read it per call, not per token, and not too finely.** This is the opposite of the local reranker, whose
cost was flat in candidates and rose with passage length, which made per-token the only reading that
transferred there. Here a 10-candidate call and a 50-candidate call at the same length differ by about
0.1 seconds, and the run-to-run spread on a repeated shape (0.342 against 0.366 s at 570 words and 50
candidates) is as large as some of the differences between rows. So the defensible statement is that
**a 50-candidate pool at our chunk size returns in about a third of a second**, and the per-token column
should not be read as a cost model.

That is roughly a thirtyfold improvement over 91.2 seconds for the same pool on this box. It is the number
that settles the cloud build's pool depth: 40 to 50 candidates is affordable, so depth is not a tuning
problem for us, and the five-second budget is not a cloud constraint.

The benchmark is `scripts/bench_bedrock_rerank.py`, which reports both the raw wall clock and the baseline-
subtracted service time, because the first version of it reported the AWS CLI's Python startup as reranker
latency.
- **The office build is not decided here.** Its reranking belongs to that build, on hardware that does
  not exist yet, and it will arrive with its own problems. What this measurement establishes is only
  the negative: a CPU reranker cannot serve it at our chunk size, so whatever that build does, it is
  not this without a GPU.
- **The CPU reranker stays in the repo behind the `bench` profile.** It produced this number on an
  instance with no GPU, which is the only way we could have learned that the CPU path fails. It starts
  in no build by accident: `docker compose config --services` lists no `reranker` for either manifest,
  and `--profile bench` brings it back. `embeddings` did not move, because `bge-m3` is the dense
  retriever and belongs in the default build.

The shape on the cloud build:

    query -> dense (Qdrant, top 500) + lexical (Postgres full-text, top 500)
          -> RRF fusion in our code -> top 40 to 50
          -> rerank at Bedrock -> top 8 to 10
          -> generate -> verify -> answer or refuse

Retrieval decides recall and the reranker only orders. Nothing in the reranker can surface a passage
that retrieval missed, which is why the identifier work stays in the lexical arm.

**Three constraints that come with it.**

- **The reranked passages leave the box on the cloud build.** Apply the data-classification gate: only
  chunks whose class permits a frontier call are sent. For a query whose candidates are all regulated
  without permission, skip the reranker, use the fused order, and record that no reranker ran.
  Degrading is allowed; claiming we reranked when we did not is not.
- **A new IAM permission is a prerequisite.** `oi-core-dev-bedrock-invoke` allows `bedrock:InvokeModel`
  and `bedrock:InvokeModelWithResponseStream` on four model ARNs and nothing else, so the rerank path
  needs `bedrock:Rerank` on the rerank model's ARN before it can work at all.
- **It must degrade rather than fail.** Throttling, or a Region where the model is unavailable, falls
  back to the fused order and records that in the audit row.

**What the numbers are not.** Both runs used synthetic passages built from a word list, so they say
nothing about whether reranking improves retrieval on real documents. That question belongs to the
golden set, and it is a separate item from the depth.

**If it breaks.**

- `HTTP 422` on a call: the payload is above the 32-text client limit and no inference happened. Chunk
  the call. Retrying a 422 is pointless, and the benchmark deliberately does not.
- `HTTP 429` and the run fails inside 30 ms: permits were unavailable. Confirm what the service has
  with `docker run --rm --network ai-net curlimages/curl -sS http://reranker:80/info` and compare
  `max_concurrent_requests` with the pool being sent. A pool of N candidates needs N permits.
- `No such file or directory` for `/app/scripts/bench_rerank.py`, or an empty `/app`: the `-v` mount
  did not land. The path inside the container is one directory below the mount point, and a bind mount
  that silently fails looks identical to a missing file.

### 3.4 The parser, and the scanned fee schedule

**Goal.** A scanned, two-column, table-bearing document becomes structured text with page numbers and
its numbers attached to the right rows. This is the gate the rest of C2 sits behind: if the parser
mangles a page, no chunker, embedding or verifier downstream recovers it.

**Commands.** The image is 4.4 GB and carries its own weights, so pull it before starting anything:

    cd /opt/oi-core
    git pull
    docker compose -f deploy/office/compose/docker-compose.yml --env-file rendered/env up -d parser

Wait for it, about two to five minutes on first start:

    for i in $(seq 1 40); do
      s=$(docker inspect parser --format '{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}' 2>/dev/null)
      echo "$s"
      [ "$s" = "healthy" ] && break
      sleep 15
    done

**Expected.** `healthy`, and:

    docker run --rm --network ai-net curlimages/curl -sS http://parser:5001/health

**What just happened.** Docling Serve runs as its own container on `ai-net`, publishing nothing, with
its weights cached under `/opt/oi-core/data/docling-cache` so a rebuild does not re-download them. It
is separate from the ingestion worker because the layout, table and OCR models are the heaviest thing
in the pipeline and a worker that restarts on every document change should not reload them.

Everything a request could ask for that reaches outside the box is switched off in the compose file:
remote services, third-party plugins, the browser UI, and the API documentation. CORS is narrowed from
its `*` default. The parser is the one stage that ever sees a document in its original form, so its
surface is worth keeping small.

**Verify.** The gate. The fixture is authored, not downloaded, because the corpus policy says a
question set over small-business paperwork has to be ours and the same argument applies to the
document: a fee schedule built here can put its numbers exactly where the failure mode lives, which is
a table in the second column with a price list in dense prose beside it.

    cd /opt/oi-core
    ls -l tests/fixtures/fee-schedule-scanned.pdf
    docker run --rm --network ai-net -v /opt/oi-core/tests/fixtures:/fixtures:ro \
      python:3.12-slim python /fixtures/parse_fee_schedule.py

**Expected.** Seven `ok` lines and `GATE PASSED`. The fixture's own generator prints the same seven
values, so the question and the answer cannot drift apart.

The fixture has no text layer on purpose, and the generator refuses to write one that does. A parse
that succeeds by copying characters out of the PDF has proved nothing about OCR, which is why the last
line of the generator's output is a count of extractable characters and it must be zero.

**The result, 2026-10-11 and 2026-10-12.** Run on the reference instance, parser container, CPU,
nothing else running:

| | |
|---|---|
| Parse time | 9 seconds for the one-page scan, OCR included; `processing_time` 4.98s |
| Text recovered | 3,321 characters of markdown from a page with no text layer |
| Graded values | 7 of 7 present and beside the right label |
| Reading order | Correct. The two columns came back in order, left prose before the right-hand table |
| Structured output | One page, two tables, 12 cells each, per-cell bounding boxes, all on page 1 |
| Confidence | Document level: OCR 0.989, mean 0.939 (excellent), low 0.894 (good) |

Nine seconds a page on CPU is inside the range the research predicted, 3.1 seconds for a text page and
13 where OCR runs, and it puts a 5,000-page client corpus at about twelve hours of parsing. That is a
batch job, not a problem, and it is the number any throughput or pricing promise has to be built on.

**Three things this run exposed. One was mine, one is chunker work, one is a real gap.**

*The structured output was never missing.* The first run reported none, and the cause was the request:
the options were sent nested under a single `convert_options` field, which the service ignored in
silence. It fell back to markdown only and answered `status: success`. Sent as top-level form fields,
`json_content` arrives with everything the architecture needs. Worth remembering as a shape: a
successful status on a request the service quietly reinterpreted, whose symptom is indistinguishable
from the feature not existing.

*The split table is joinable.* One logical fee schedule arrives as two tables because it is longer than
its column. Both carry `prov` with `page_no: 1` and adjacent bounding boxes, table 0 at x 53 to 297 and
table 1 at x 315 to 559, with table 1's cells at the top of the page and table 0's lower down. Two
columns, one page, adjacent. So the split is recoverable from provenance and the joining belongs to the
chunker in step 3.5. The architecture already says never split a table; what this adds is that a table
can arrive pre-split by the parser, so the join has to happen before the split rule applies.

*The OCR confidence is document-level only, and that is a real gap.* The number exists and it is good:
OCR 0.989 on a page nobody could read without OCR. But Layer 0's confidence gate is specified to refuse
a claim from a poorly-read page, and Docling's own source says what is available:

    class ConfidenceScores(BaseModel):
        """JSON-safe snapshot of docling confidence scores. ...
        Document-level only. A per-page breakdown can be added later as an
        optional ``pages`` field without breaking this shape."""

Per-page confidence is computed inside Docling, where it is stored in the `pages` field of its own
`ConfidenceReport`, and it does not survive into the service's wire schema. So the gate as written
cannot be built on this output. A document-level average cannot say which page was read badly, and on a
multi-page scan the low score that matters is on one page.

Two other fields in that report are `None`, and neither is a fault. `table_score` is documented "not yet
implemented", and `parse_score` is the 10th percentile of *digital* text cells, which a pure scan has
none of. Neither should be read as a zero.

One small correction to the fixture itself: it is one page, and a comment in its generator claimed the
table ran onto a second page. It does not. The split is column-wise. A page-straddling table is a
different fixture and is not yet covered.

### 3.4b What a confidently wrong read is, and what the profession does about it

**Goal.** Settle what to do when OCR reads a number wrongly and reports high confidence. The gate above
proves the parser works; this is about the one failure the parser cannot see in itself.

**The measurement, 2026-10-12.** Forty trials carving defects into the cell whose truth is `145.00`.
Fifteen readings changed the value and the confidence score flagged **one**. Fourteen wrong numbers
passed at confidence 61 to 93, among them `145.60`, `146.00` and `145.08`. Then two engines, the
parser's own RapidOCR and Tesseract:

| | |
|---|---|
| rapidocr wrong | 4 of 40 |
| tesseract wrong | 15 of 40 |
| tesseract caught, of rapidocr's 4 failures | 2 |
| still unflagged with both engines reading | 2 |
| both engines wrong and agreeing | 2 |

The two failures that beat both engines were both **truncated readings**, `14600` and `1450` for
`14500`, which a digit-count check catches for nothing.

**What that settles.** Confidence is a certainty-of-reading signal, not a correctness one, and
calibration cannot change that because rescaling is monotone. The engine question is settled too:
RapidOCR, which the pipeline already runs, is the strong reader here, and Tesseract earns its place as a
checker rather than an equal.

**Four research angles, in `docs/research/`.** Open-source stacks: no project catches this, and every
confidence gate in that landscape deletes text below 0.5, which for a number is worse than nothing.
Commercial vendors: none claims to catch it, and two say in print that their score is not a probability.
Verification practice: the cheque industry has never trusted one reading at any confidence, and requires
two, while accounting reconciles against a bank feed rather than proofreading. The literature: Tesseract's
value is an affine compression of an unnormalised score, the result matching this measurement was
published in 2009, and the pattern prescribed is redundancy the defect cannot corrupt twice.

**The decision, recorded in `docs/search-architecture.md`.** Do it the way the profession does it, and
not by putting eyes on every document. Six controls in order: format, range and checksum checks on every
extracted figure; cross-field arithmetic where a document has the relationships, line items to subtotal
and subtotal plus tax to total; a second independent reading of any cited figure as an escalation gate
and never a vote; human review of what those flag, one field with the source fragment beside it; the
override recorded as machine value against human value; and disclosure where a figure passed no check
and had no second source.

**The queue is the one number still unknown.** It decides whether the review surface is a database query
or a product with triage and bulk actions, and it depends on the client's actual paper rather than on
synthetic damage. That is a fixture-corpus question, and it does not block the chunker.

**What the plan gets wrong if this is skipped.** `RET-PARSE-001` closes on the gate and looks like the
end of parsing. It is not: the gate proves the parser reads a clean scan correctly, and says nothing
about the failure that reaches a client as a wrong number with a real citation.

**If it breaks.**

- `docker compose` reports no such service: the compose file on the box is older than the pull. Check
  `git log --oneline -1` and `grep -n "parser:" deploy/office/compose/docker-compose.yml`.
- **`unhealthy`, the probe logging `Connection refused`, and the app log ending in
  `FileNotFoundError: RapidOCR artifacts not found or incomplete in artifacts_path`.** This is the
  failure the first attempt at this step hit, and the cause is a setting that reads like a cache and
  is not one. `DOCLING_SERVE_ARTIFACTS_PATH` means *load the weights from here and never download
  them*, so pointing it at an empty directory tells the service its weights are missing while
  forbidding it from fetching them. The log says so twice, in
  `artifacts_path is set to a valid directory. No model weights will be downloaded at runtime` and then
  in the missing-file list. The image already carries every weight it needs, which is why the pull is
  2 GB rather than 8, so the fix is to leave the variable unset. Do not "fill the cache" instead: that
  is work the image has already done.
- The container never leaves `starting`: it is loading layout, table and OCR weights, which takes
  minutes on first run. `docker logs parser --tail 20` will show a model being fetched or loaded. With
  the artifacts path unset there should be nothing to fetch, so a download appearing here means a
  model the image does not carry, and that is worth reading carefully rather than waiting out.
- `healthy` but the gate reports zero characters: OCR did not run. Confirm with
  `docker run --rm --network ai-net curlimages/curl -sS http://parser:5001/health`, then re-run and
  watch `docker logs parser` for the OCR stage. A scan read as a blank page is the exact failure this
  step exists to catch, so treat it as a finding rather than a hiccup.
- A graded value reported as `MISS`: the page parsed but the number is not beside its label. Read the
  output rather than re-running it. A number attached to the wrong row is worse than a missing one,
  because it produces a confidently cited wrong figure, so this is a stop-and-look.

### 3.5 The chunker, and the table the parser split in two

**Goal.** A parsed document becomes retrievable chunks, with the page kept intact as the citation unit
and a table kept whole even when the parser handed it over in pieces.

**Commands.** The rules are unit-tested, so the box only has to prove they hold on real parser output:

    cd /opt/oi-core
    ~/venv/bin/python -m pytest tests/test_chunker.py -q
    docker run --rm --network ai-net -v /opt/oi-core:/w -w /w \
      --entrypoint python ghcr.io/docling-project/docling-serve-cpu:v1.36.0 \
      /w/tests/fixtures/chunk_fee_schedule.py

**Expected.** Eleven unit tests pass, and the fixture run ends with `PASSED` naming seven row entries
covering all seven fees, one joined table, no fee duplicated into a page chunk, and stable ids.

**What just happened.** The chunker is `services/ingestion/chunker.py`, and it is a module rather than a
step inside a worker because it is the only place where a decision made today, about what a citation
points at, becomes permanent. A chunk that spans two pages or carries half a fee schedule cannot be
repaired by the retriever or the verifier afterwards.

The rules come from the architecture's stage 2: page-bounded and structure-aware, 384 to 512 tokens,
overlap of zero to 32 tokens and zero preferred, the page stored as a **range** rather than a single
page, and a table that fits never split.

Two of those rules were written against an assumption this step had to correct, both found by measuring
the parser in 3.4.

*A table arrives already split.* The parser emits one table per column region, so the fixture's single
fee schedule comes back as two tables whose cell extents are x 54 to 297 and x 316 to 558 on one page.
"Never split a table" is meaningless until the fragments are one table again, so joining happens first
and the grouping is by page and horizontal adjacency, using the widest cell extent in each fragment
because a fragment's own box can be drawn tighter than its cells.

*A continuation fragment has no header row.* The second fragment starts at its own first data row, so
its rows would serialise as bare values with no column names, and a row retrieved alone has to say what
its number is. The header is found by repetition: it is the top row shared by the most fragments, and a
fragment whose top row is that row is treated as carrying it. The rest have their header skipped, and
only those.

**The collection is established by the worker, not by remembering step 3.1.** On the first real run the
collection did not exist, because the `vector` container was new and its data directory with it, and every
document failed with a 404 from Qdrant while the ingestion container reported healthy. A dependency that
lives in a runbook is one the next person does not know about, so `ingest.py` now asks the embedding
service how many dimensions it produces and creates the collection at that size, with cosine distance,
when it is missing.

It refuses to reconcile a mismatch rather than repairing one. A collection built at 768 dimensions against
a model producing 1024 means either the model changed or the collection did, and both are decisions:
recreating it deletes every chunk in it, and using it rejects every write. The error names both numbers and
stops, because the alternative is a silent choice between two bad outcomes.

This makes step 3.1's manual collection creation a diagnostic rather than a prerequisite. Running it
against an existing collection is a no-op; running it against a fresh store is how you check the store
before the worker gets there.

**The result, 2026-10-13.** The parser swap deployed and verified on the reference instance. The corpus
re-ingested to 1059 chunks with no failures, and a second cycle skipped all four and wrote nothing.

| Document | Chunks | `is_ocr` | Pages labelled OCR |
|---|---|---|---|
| irs-p15.pdf | 358 | **38 true, 320 false** | **1 and 8** — the two the parser read off an image |
| apple-ex10-1-2024.htm | 13 | 13 false | none |
| apple-ex10-22-2024.htm | 16 | 16 false | none |
| apple-10k-2024.htm | 400 | 400 false | none |

The publication is the one that matters: 2 of its 59 pages were read off an image, and 38 of its 358
chunks say so while the other 320 say they were not. Before this work the document-level answer was "not
OCR" in full, so a figure recognised from page 1's scan was presented as text that was already there.
`ocr_page_count` travels with each chunk as well, so a passage that is partly machine-read can be described
that way rather than with a yes or no.

**The parser is now `services/parser`, not `docling-serve`.** Step 3.4 chose docling-serve and this step
replaced its HTTP layer while keeping its image, weights and request shape. The reason is one field.

`docling-serve` computes Docling's per-page confidence report and serialises only the document-level
rollup. Its own code says so:

    class ConfidenceScores(BaseModel):
        """JSON-safe snapshot of docling confidence scores.
        Document-level only. A per-page breakdown can be added later as an optional ``pages`` field..."""

That breakdown is what tells the pipeline which pages were read off an image. Measured on the fixture
corpus's 59-page publication, the in-process report names pages 1 and 8 as OCR'd, with page 1 carrying the
weakest OCR score on the document at 0.7284. The served rollup cannot express any of that: it reports
`low_score 0.9326` while page 59 scores 0.6739, because a 5th-percentile average over 59 pages is not moved
by one bad page.

**The wrapper adds one Python file to the same image**, so nothing doubles: same base digest, same weights,
same 5001, same request shape, and the ingestion worker talks to either service without a code change. What
comes back is different.

Three things it does differently, and each is a decision:

* **It returns `confidence_pages`.** A page with an `ocr_score` was read off an image; a page with null was
  not touched by OCR. The worker uses it to set `is_ocr` per chunk rather than per document, so a figure
  from a scanned page of a mixed filing is labelled as machine-read and a figure from a digital page is not.
* **It refuses an option it does not understand**, naming the ones it accepts. `docling-serve` ignores an
  unknown field while answering success, which is how `do_ocr`, `do_table_structure` and `table_mode` all
  looked like they worked. Sending `do_ocr=true` to the new service returns a 422 listing the real names.
* **It offers no route that fetches a URL, no plugin loader, no UI and no published schema.** The five
  variables that switched those off in docling-serve were about a surface this service does not have.

**The fixture, and why it took two.** The existing scanned fixture is one page, and a one-page scan cannot
test whether the pipeline knows WHICH pages were read off an image: a wholly scanned document and a wholly
digital one are both labelled correctly by a document-level answer. The failure only exists in a mixture.
`tests/fixtures/build_mixed_scan.py` authors four pages with scanned and digital interleaved, pages 2 and 4
as image-only pages with no text layer, and it refuses to write a fixture whose pages do not have the shape
it claims. Verified: the service reports an `ocr_score` for pages 2 and 4 and null for pages 1 and 3, in 11
seconds.

**What the numbers are not.** The token count is estimated from word count rather than measured with the
embedding model's tokenizer, and the estimate is named in the module so it can be replaced. It bounds a
chunk size and must never be used to assert one. Step 3.6's embedding contract is where the real
tokenizer belongs.

**Why a ceiling at all, since it costs two things.** Anthony asked, 2026-10-12, and the answer is
engineering rather than quality. Three constraints. The embedding model takes at most 8,192 tokens, and a
chunk past that cannot be embedded at all. The reranker costs 2.5 to 3 ms per token of candidate text,
measured in 3.3, so an 800-token chunk spends two and a half seconds of a five-second budget before any
other chunk is scored. And the prompt has to hold several chunks at once, so an unbounded one crowds out
the question. What the ceiling is not is a measured optimum: the chunking research says there is no
universal size, that published optima span 200 to 1,024 tokens, and that the page-versus-token accuracy
difference is noise. The architecture row now says so instead of stating 384 to 512 as though it were
settled.

The ceiling costs something real on our own fixture. Page 1 parses to about 825 tokens, so the prose is
split where the page boundary rule alone would have kept it whole. That is the right trade: a chunk that
cannot be embedded, or that eats half the reranking budget, is worse than a citation that points at part
of a page.

**The bug the ceiling exposed.** The first version tested only whether adding the NEXT passage would
overflow, so a passage already over the budget went in whole and the chunk came out past the ceiling.
On a dense page that is precisely the outcome the ceiling exists to prevent. It is fixed, and the test
for it uses a page of 800-token prose, which is what a real schedule's dense page looks like.

**And the ceiling itself was only an estimate until chunker-v3.** The estimate is whitespace words × 1.35,
which measured 2.8x to 3.2x low on table text and returns one token for a run with no whitespace; the table path
had no size check at all. On a real 341-document corpus that produced a 756 KB prose chunk and a 186 KB table
chunk, 73 documents were embedded as prefixes because truncation was on, and one assembled prompt was refused by
the provider for size. **The repair is not a bigger number.** Every emitted chunk is measured with the served
model's tokenizer after its heading prefix is in place, and split by a line/sentence/clause/character fallback if
it is over; an oversized table becomes row groups that keep its `table_id`, total row count and each group's row
range; and the embedding service runs with truncation off, so an oversized input fails loudly instead of becoming
a prefix. `RET-CHUNK-001` is the row that keeps it true.

**And a table has a size at which it stops being a document table.** At chunker-v4, a table over 5,000 rows emits
no per-row entries and at most 200 bounded row groups plus a marker naming the omitted rows; a spreadsheet over
2 MB or a text dump over 4 MB is classified as a data set before parsing and contributes one card instead of
thousands of row chunks. This came from the 2026-10-17 corpus, where one 27,829-line dump appeared in 42 of 52
baseline retrievals and displaced the documents holding the answers. The rule and its thresholds are in
`services/ingestion/chunker.py`; `RET-TABLE-001` is the row, and the data suite is what measures the effect.

**If it breaks.**

- A test failing on row labelling: the column names are being paired with the wrong cells. This has now
  happened three ways, and only the third survived review, so treat it as the live risk in this module.
  Pairing names positionally without sorting the cells by column first gives `per callout: Item`. Looking
  the name up by the cell's own column index gives a rotated row, because a continuation fragment does
  not share the first fragment's column numbering. And taking row zero as the header gives a data row's
  text as the column names. Sorted position is the reliable key; column index is only a sort key.
- `PASSED` not printed and two table chunks instead of one: the join is not firing. Print the cell
  extents and check the horizontal gap against the tolerance in `join_column_splits`. On the fixture the
  gap is 19 points, so a tolerance below that will not join them.
- A fee reported as appearing in a page chunk as well as the table: the body-children walk is not
  excluding table references, and the same figures now sit in two retrievable places.

### 3.5b The heading is content, or it is nowhere

**Goal.** A fact that exists only in a heading is findable. A scanned invoice is retrievable by its own
invoice number and its supplier's name.

**Why this is a step of its own and not a note in 3.5.** Step 3.5 built the chunker and its tests pass. The
defect is not in the table rules it was checked against, it is in what the prose path does with a line
docling calls a heading, and the tests could not see it because they asked about tables.

**What was wrong.** `page_texts` treats an item labelled `title` or `section_header` as structure. It appends
the text to a `headings` breadcrumb and does not let it become a content item:

```
if label in ("section_header", "title"):
    headings = headings[: max(0, level - 1)] + [text]
    continue          # <- the text never becomes content
```

The breadcrumb then travels into the vector payload's `headings` field, and **neither retrieval arm reads
that field.** The embedding is of `chunk.text`; the lexical index is of `payload["text"]`. So every heading in
every document was preserved in the one place nothing queries.

**How it was found, and why nothing did find it earlier.** It surfaced while proving that scanned images now
upload. OCR returned the invoice correctly and the answer still said the document was not there:

```
chunk_index rows matching 'HALLORAN'                 0
chunk_index rows matching '2026-0417'                0
chunk_index rows matching '4,218.50'  (in the body)  4

Qdrant payload: headings: ['HALLORAN MECHANICAL LTD INVOICE 2026-0417']
                text:     'Site: Lake Kayla pumping station ...'
```

The invoice number and the supplier were in the payload and unsearchable. **The document was indexed, the OCR
was perfect, and the invoice could not be found by its own number.**

**Scale, because a scan is the small case.** A generated record in the corpus has one title and four section
headers out of fourteen text items. A contract has sixty section headers out of a hundred and twenty-one.
Half of the structure of a contract was unsearchable, and no page said so.

**The fix, one place.** `emit()` in `_group_prose`, which is where a chunk's text is assembled:

```
body = "\n\n".join(buffer)
prefix = " > ".join(headings).strip()
return current_page, (f"{prefix}\n{body}" if prefix else body), first_bbox, headings
```

One prefix per chunk, and it is the path in force where the chunk opens. A chunk that runs past a new section
belongs to the section it began in, which is also what its citation breadcrumb says.

**The table path already did exactly this**, which is what makes it an inconsistency rather than a choice.
`serialise_table` prepends the same `" > ".join(headings)` and its docstring records why: the research
measured a deterministic heading prefix plus caption beating generated keywords on page accuracy, 67.38
against 46.10. Prose never got it.

**Two version constants, and both are load-bearing.** The chunking rules changed, so `CHUNKER_VERSION` goes to
`chunker-v2` and every chunk id changes with it. What a payload means changed, so `PAYLOAD_VERSION` goes to
`payload-v5` and every document is re-derived on the next cycle rather than skipped as unchanged. **A fix
without the second one reaches nothing**: the manifest would see identical bytes and skip every file.

**Do this on a fresh corpus, or accept the re-embed.** The text of every chunk changes, so every vector
changes. On the reference instance a full re-ingest of 10,026 documents is about 1h36m at four documents in
flight. Applying it before a first ingest costs nothing extra; applying it afterwards costs that.

**Verify.**

```
~/venv/bin/python -m pytest tests/test_chunker.py -q -k "heading or title_is_in_the_chunk_text"
bash scripts/probe.sh interface_probe.py
```

Then ask for a value that exists only in a heading. On the fixture corpus that is the scanned invoice:
`What is the total due on invoice 2026-0417 from Halloran Mechanical?` It must come back citing the scan. If
it does not, check that the re-ingest happened rather than a skip: a cycle that reports `skipped 10026` has
not re-derived anything.

**If it breaks.**

- Every heading unsearchable and `chunk_index` has no rows matching the title: the payload version did not
  bump, so the worker skipped every file as unchanged. Clear the manifest or bump the version.
- A citation showing two section names in its breadcrumb: the prefix is being applied per item rather than per
  chunk. It belongs in `emit()`, which runs once per chunk.
- An empty first line on every chunk: the prefix is being added when `headings` is empty. It is skipped when
  the path is empty, and `test_a_document_with_no_headings_is_unchanged` is what holds that.


### 3.5c The deployed image is built from the source it claims

**Goal.** No service is running code older than the source it was built from.

**Why this is a step.** On 2026-10-16 a teardown cleared every index, reported its stores by name, and
**left all 10,033 client documents on disk.** The code that removes them had been in `main` since 13 October.
The image was built before it and never rebuilt, and nothing noticed: `git status` was clean, `git rev-list
HEAD..origin/main` said zero, and every command ran without complaint. A client asking for their data
destroyed would have received a signed destruction record and kept their documents.

**The gap is not that bug, it is that a pull without a rebuild is invisible.** The box also reported itself
current while being five commits behind, because its `origin/main` ref was three days stale and nothing had
fetched. So the check does both: fetch, then compare.

```
cd /opt/oi-core
git fetch origin
git rev-list --count HEAD..origin/main      # the number that lies without a fetch
bash scripts/check_images_current.sh        # exits non-zero on any stale image
```

**Expected output.** `HEAD` matching `origin/main`, `actually behind 0`, and one line per service ending
`All 10 images are at least as new as their source.` Ten, not five: the script passes `--profile tools`, and
its first version did not — it checked five, reported all current, and said nothing about `retention`, which
was the one that had broken.

**It is a tripwire, not a proof.** It compares each image's build time against the last commit touching that
service's source. A rebuild with no source change is harmless and unflagged; an image built from a dirty
working tree is flagged current when it is not.

**If it breaks.**

- `STALE` on a service you have just changed: rebuild. `docker compose -f deploy/office/compose/docker-compose.yml
  --env-file rendered/env --profile tools build <service>` then `up -d <service>`.
- `STALE` on a service you have not changed: the image predates a commit that did change it. Rebuild and
  expect behaviour to move, because it will.
- A service reported `NOT BUILT`: the image name in the resolved compose file has no local image. Build it.
- Every image current and the deployment still behaving like old code: check `git rev-parse HEAD` against
  `origin/main` by hand. A fetch that did not run makes the whole comparison meaningless.

### 3.6 The fixture corpus and the question set

**Goal.** Real business documents to measure against, with the licence for each read at the source, and a
hand-written question set over them. C2's decisions need a yardstick, and this is it.

**Commands.**

    cd /opt/oi-core
    python3 fixtures/corpus/fetch_corpus.py
    python3 fixtures/corpus/fetch_corpus.py --verify
    python3 fixtures/corpus/validate_corpus.py

**Expected.** Four documents fetched with a sha256 each, then `ok` for all four on the verify pass, then
`both files agree with each other and with the lockfile`.

    fetch apple-10k-2024         https://www.sec.gov/Archives/...
          1503891 bytes  sha256 812f766e3cd4f3c5
    ...
    fetched 4, already present or verified 0, documents in the corpus 4

**What just happened.** The corpus is four public documents fetched directly over HTTP: a 10-K, two
contract exhibits from the same filing, and an IRS publication. `docs/evaluation-corpora.md` had already
decided the licence policy and named the sources; what this step adds is the actual bytes and the record
of which bytes.

**Why files from EDGAR and the IRS rather than one of the named datasets.** Two of that document's named
sources, DocLayNet and the CUAD QA form, are distributed as `datasets`-library loaders rather than as
files. Fetching them needs the Hub client and access to upstream archives, which a client box may not
have. SEC EDGAR and the IRS serve files directly, so this corpus fetches with the standard library and
nothing to install. The named datasets are not rejected, they are deferred, and the manifest says which
case each would cover that this one does not.

**The lockfile is the part that makes this a fixture.** `corpus.lock.json` records the URL, the fetch
date, the size and the sha256 for each document. A corpus whose bytes can change under it is not a
fixture: the same questions applied to different bytes are a different test. So the fetcher refuses to
use a file whose hash does not match, and `--force` refetches while reporting that the source moved.
When a result moves and nobody changed the code, the lockfile is the first file to diff.

**What the corpus covers, and the two cases it does not.**

| Case | Covered by |
|---|---|
| long document, dozens of real tables | the 10-K |
| contract clauses with legal numbering | the two EX-10 exhibits |
| dense policy prose with rate tables | the IRS publication |
| near-duplicate documents | the two exhibits, which share boilerplate |
| **a scanned document with no text layer** | **not here.** EDGAR and the IRS serve text and HTML |
| **a spreadsheet, an email thread, slide decks** | **not here.** Both need a source whose licence is read first |

The scanned case is proved separately, in step 3.4, against a fixture this project authored. The two are
complementary and neither substitutes for the other: 3.4 proves a scan can be read, this proves real
documents can be retrieved from and cited.

**The question set is written, and its answers are not.** Ten questions, eight answerable and two that
must produce a refusal. The six requirements from `docs/evaluation-corpora.md` are represented
deliberately: paraphrase rather than quotation, unanswerable questions, a required page number, a
table-cell answer, a two-document answer, and coverage of the smallest document rather than only the
largest.

Every `reference_answer` is null, and that is deliberate rather than unfinished. A reference answer is a
claim that a specific fact appears on a specific page, and the only honest way to write one is to read
the parsed text. This corpus's own manifest caught that mistake once already, where a licence was
described before it was read and the description was wrong. So the questions are mine and the answers get
filled in from the parsed pages, in the ingestion step, and the validator refuses to let the set claim
completeness while they are missing.

**If it breaks.**

- A fetch fails with 403 from `sec.gov`: the User-Agent is missing or does not identify a contact. SEC's
  access terms require a declared agent and cap requests at 10 per second, which is what
  `request_delay_seconds` in the manifest is for.
- `refusing to use ... its hash does not match the lockfile`: a source has changed the bytes at a URL
  that looked permanent. That is a finding about the corpus, not a hiccup. Re-run with `--force` to
  accept the new bytes and see what moved, and record why in the manifest.
- The validator reports a question drawing on a document that is not in the manifest: the two files have
  drifted. That is what the check is for, and CI runs it on every pull request.

### 3.7 The ingestion worker

**Goal.** A document on disk becomes chunks in the vector store, once, and a second run costs one read
per file rather than a re-embed. This is the step that makes the fixture corpus an actual test.

**Commands.** First the contract probe, because this worker makes three HTTP calls and a wrong request
shape looks like a missing feature rather than a malformed request. That has already cost this project one
round trip on the parser.

    cd /opt/oi-core
    git pull
    docker run --rm --network ai-net -v /opt/oi-core:/w -w /w \
      --entrypoint python ghcr.io/docling-project/docling-serve-cpu:v1.36.0 \
      /w/tests/fixtures/api_contract_probe.py

Then the migration, the container, and the cycle:

    docker compose -f deploy/office/compose/docker-compose.yml --env-file rendered/env \
      --profile tools run --rm audit migrate
    oi up -d --build ingestion
    oi ps
    docker logs ingestion --tail 30

**Expected, from the probe.** The embedding service reports which endpoint answers and whether a batch
returns one vector per input, the collection's vector config and dimension, and a written point that is
found by a payload filter and then removed. Read the endpoint it found against what `embed_texts` tries
first.

**Expected, from the run.** The migration applies `007_ingest_manifest.sql`. Then `ingestion` appears in
`oi ps` and reaches `healthy` — which for this container means the last cycle finished with no failures,
not that the process is alive. The first cycle reads the four corpus documents, which takes minutes because the
10-K and the IRS publication go through the parser:

    ingesting apple-10k-2024.htm  (new, 1503891 bytes)
      214 chunks, 42 pages
    ingesting irs-p15.pdf  (new, 1713916 bytes)
      ...

    collection documents: 1024 dimensions, Cosine, 0 points     <- first run, created or reported
    seen 4, new 4, changed 0, skipped 0, failed 0
    chunks written 1183, chunks removed 0, documents removed 0

Run it a second time and the whole cycle should be `skipped 4` with nothing written:

    docker exec ingestion python3 /app/ingest.py --documents /documents

**What just happened.** The worker walked the documents directory, and for each file hashed the bytes
before doing anything else. That hash is the whole design. A file whose bytes are unchanged is skipped
without being parsed, so a cycle over a client's corpus costs one read per document rather than a parse
and an embed per document. A file whose bytes changed gets new chunk ids, because the id covers the
content, and its old chunks are removed before the new ones are written so a document that shrank cannot
leave orphans behind.

**Four decisions, each of which has a test that fails if it reverts.**

*Skip on the hash.* The manifest holds the sha256 of the file as ingested, not a modification time. A
file that was touched but not edited costs nothing.

*Delete then write, when a document changed.* The order matters. Writings first would leave two versions
of the same policy in the index, both citable, and the wrong one would look exactly as confident as the
right one.

*Reconcile removals from the manifest, not the filesystem.* A document the manifest knows about whose file
has gone is removed from the index. A file that was never ingested cannot cause a removal, and a
documents mount that failed to appear cannot empty a client's index while the container restarts.

*Dead-letter after two failures, until the bytes change.* The first version recorded the previous good
hash on failure, and an unreadable file has no previous good hash, so it was re-parsed every cycle for the
life of the deployment. Worse, its failure stopped being visible because it was always there. The hash
recorded on failure is now the hash of the bytes that failed, so the check works and a replaced file gets
another chance without anyone being told.

**The healthcheck reads the last cycle, not the process.** This worker has no listening socket by design,
so "is it up" is not a question it can answer. A healthcheck that only proved the process existed would
stay green while every document failed. The cycle writes its outcome to `/state/last-cycle.json` and
`ingest.py --status` reads it back, exiting non-zero when the last cycle had a failure.

**What the label is, and where it comes from.** Each chunk carries `acl` set to the top-level folder it
was found under, so `company/handbook/policy.pdf` becomes `company`. It is attached at ingest rather than
derived at query time because the search filters on it, and a filter that reads the filesystem is not a
filter. Step 3.8 is what enforces it.

**What is not done here.** The chunks are written with no version column yet, so a superseded document is
removed rather than superseded; that is step 3.10. The parser's per-page confidence is not stored, and
step 3.4b records why it cannot be read from this service's output anyway.

**The result, 2026-10-13.** Run on the reference instance against the four corpus documents, with the
collection deleted first so the run is from zero rather than from whatever survived a restart.

| Document | Chunks | Notes |
|---|---|---|
| apple-10k-2024.htm | 672 | 588 table entries and **84 prose chunks** |
| apple-ex10-1-2024.htm | 13 | pageless HTML |
| apple-ex10-22-2024.htm | 16 | pageless HTML |
| irs-p15.pdf | 358 | 59 pages, text layer, no OCR |
| **total** | **1059** | |

The second cycle printed `seen 4, new 0, changed 0, skipped 4, failed 0, chunks written 0`. That is the
step's whole claim: the same bytes cost one read per file and nothing else.

**Three bugs the run exposed, each fixed and each with a test.**

*The synchronous convert endpoint has a 120-second cap and does not abort the work.* Its handler carries
`# TODO: abort task!`, so every 504 left the parse running while the client was told it had failed. My
three retries therefore orphaned three parses and started a fourth: the parser accumulated over eight hours
of CPU time and the box's load average reached 30 on sixteen cores. Restarting the parser dropped it to
3.46, which is how the orphans were confirmed. The worker now submits to the asynchronous endpoint and
polls, which separates whether the parser accepted the work from how long the work takes.

*Two of the four conversion options did not exist.* `do_ocr`, `do_table_structure` and `table_mode` are not
options of docling-serve 1.36; the stages take `ocr_preset` and `table_structure_preset`, and an unknown
field is ignored while the service answers success. This is the same trap step 3.4 recorded, which is why
the runbook names the parser's own 422 as the authority on option names, because it serves no capabilities
endpoint, and the option set is asserted by
a test.

*`is_ocr` was a constant.* The code searched each item's provenance for an `origin` field that this release
does not emit, so the fallback answered True and an HTML contract was labelled as machine-read text. The
signal that exists is `parse_score`, null when a document has no digital text at all. It is now read from
the document-level confidence report.

**A correction to step 3.5's numbers, which is not a regression.** The 10-K produced 588 chunks before this
work and 672 now, on the same bytes. The old chunker was silently dropping every word of the 10-K's
narrative: HTML has no pages, so each text item failed the page-number check, and only the table cells
survived because table fragments do not consult that check. The 588 were 587 table rows and one table with
no prose at all. Running the old and new chunkers on the same parse confirms it:

    old (d5f2b9a)    588 chunks  {'table': 1, 'table_row': 587}
    new (main)       672 chunks  {'table': 1, 'table_row': 587, 'prose': 84}

**Retrieval works on what was stored.** The four questions from the fixture set that draw on this corpus
each found their document, and the table-row design earned its place: the net sales question returned
`Total net sales | 391,035 | 383,285 | 394,328` as a row entry at 0.646, which is a figure a reader can
check against the page it cites.

**The review-queue number, deferred three times.**

| | top score |
|---|---|
| questions that found their document | 0.621 to 0.673 |
| unanswerable questions | 0.523, 0.524 |

The two unanswerable questions, which this corpus cannot answer at all, each returned ten passages. That is
what a nearest-neighbour search does: it has no notion of nothing. Worse for a threshold approach, the
best wrong answer scores 0.524 and the worst right answer 0.621, so the gap is under a tenth of a point
across four documents. **A similarity cutoff is not a usable answerability gate at this corpus size**, and
step 3.8's citation checks are the mechanism that has to carry it instead.

Two of the eight answerable questions did not retrieve their document. Both draw on the IRS publication and
both ask for a specific figure in a specific form, which is the case the reference answers have to settle:
the questions are mine and the answers were left null on purpose, so until the parsed pages are read there
is no way to tell a retrieval miss from a question the corpus cannot answer.

**A code change has to reach the documents it was written for.** The skip decision is made on a file's
bytes, which is right for a file that was edited and wrong for a change to the code that derives the stored
payload from those bytes. Then the bytes are identical, the manifest still believes its old claim, and the
fix never arrives. It happened twice in this step. The first time was caught by a symptom: two exhibits were
recorded as ingested with no chunks, so the worker learned to distrust that shape. The second time was
`is_ocr`, where all 29 chunks of the two HTML exhibits stayed labelled machine-read after the code that
labelled them was corrected, and the only sign was a per-page count that disagreed with the label.

The mechanism is now explicit. `PAYLOAD_VERSION` in `services/ingestion/ingest.py` names the rules a stored
payload was derived under, the manifest records it per document, and a record whose value differs from the
running worker's is re-derived even though its bytes have not changed. It is deliberately separate from the
chunker's version, because a payload change does not change a chunk's text or its chunk id, and forcing a
re-parse for every labelling fix would hide the difference between the two.

**Bump it when the meaning of a payload field changes**, and the next cycle re-derives the whole corpus
once. **Measured: 10,026 documents in 1h36m at four documents in flight.** For a client's corpus it is the
price of not serving content the code no longer produces.

**A rules change needs the other constant too.** A payload bump alone is right when a stored field's meaning
moves and the chunk text does not. When the chunking rules themselves change, the text changes with them,
`CHUNKER_VERSION` moves to `chunker-v2`, and a payload bump that arrived without it would reach nothing at
all: the manifest would see identical bytes and skip every file. Step 3.5b is that case.

**Repairing a record written under an older rule.** A manifest row records a decision that was made by
whatever code wrote it, and code changes. The first corpus run left two contract exhibits recorded as
ingested with zero chunks behind them, and dead-lettered a 60-page PDF over two gateway timeouts. Neither
row could know that the rules had moved, so both kept the documents out of the index while every fix to
the chunker and the failure policy passed them by.

The cycle now re-ingests a record that claims success with no chunks behind it, because a manifest is a
cache of a claim about the index and a record with nothing behind it is a false claim whatever wrote it.
For the failures, the repair is explicit:

    docker exec ingestion python3 /app/ingest.py --clear-failures
    docker exec ingestion python3 /app/ingest.py --clear-failures company/irs-p15.pdf

With no argument it clears every recorded failure; with a path it clears one. The next cycle then tries
those documents again. It is needed when a failure rule changes rather than routinely, and the
alternative is telling an operator to delete rows by hand from a table they have no reason to know about.

**If it breaks.**

- A document that was fixed on disk is still skipped: the change is in the code rather than the bytes, so
  the hash still matches and the manifest still believes its old claim. That is what the two repairs above
  are for.
- The worker re-ingests everything every cycle: the manifest is in memory, which happens when
  `DATABASE_URL` is unset or the database is unreachable. It says so at the top of its output rather than
  doing it quietly. Check `docker exec ingestion env | grep DATABASE_URL` and that `postgres` is healthy.
- `the parser returned no structured output`: the same failure step 3.4 spent a round trip on. The options
  are sent as separate multipart fields and an empty `json_content` means they were not accepted.
- Chunk counts far higher than expected: the parser found more documents to split than the file has
  pages, which usually means OCR read a page as several regions. Compare against the page count the run
  prints.
- A cycle exits non-zero but the log shows no failure: the exit code is non-zero when any document failed,
  deliberately, so a scheduled run surfaces a bad file. `--status` names it.

### 3.8 Access labels

**Goal.** A role cannot retrieve a chunk its label excludes, and the exclusion happens inside the search
rather than after it. The requirement is `SEC-ACL-001`, and its verify line is the test this step ships.

**Commands.**

    cd /opt/oi-core
    git pull
    python3 tests/fixtures/build_executive_fixture.py
    mkdir -p documents/executive
    cp fixtures/corpus/executive-compensation.md documents/executive/
    ls -l documents/executive/

    docker exec ingestion python3 /app/ingest.py --documents /documents

Then the proof. This is a probe rather than a unit test because the thing being proved is what the STORE
returns when the query carries a filter, and a stub cannot answer that:

    docker run --rm --network ai-net -v /opt/oi-core:/w:ro -w /w \
      --entrypoint python ghcr.io/docling-project/docling-serve-cpu:v1.36.0 \
      /w/tests/fixtures/acl_probe.py

    # And again with each policy, so the client's choice is seen rather than taken from a unit test:
    docker run --rm --network ai-net -v /opt/oi-core:/w:ro -w /w \
      --entrypoint python ghcr.io/docling-project/docling-serve-cpu:v1.36.0 \
      /w/tests/fixtures/acl_probe.py --denial silent

**Expected.** The ingest picks up the fifth document:

    seen 5, new 1, changed 0, skipped 4, failed 0
    chunks written 3, chunks removed 0, documents removed 0

And the probe reports, for each role, what the store returned and what the retriever passed on:

    role=staff      store returned 0 passages, withheld 0, denied False
    role=executive  store returned 3 passages, withheld 0, denied False
    staff query for 418,750: no passage contains the figure

**What just happened, and why the order matters.** The build order says this regression test ships *before*
the feature, and the reason is in the sentence it comes from: a filter that runs after the search returns
results is the mistake this phase exists to avoid. A test written afterwards describes whatever the code
happens to do, and the thing under test here is *where the filter runs*, which a test written afterwards
cannot see.

So the test was written against a module that did not exist. Ten tests, all failing on a placeholder that
returned nothing, and then the module that makes them pass. The evidence for the failing half is in the pull
request, because a test that has never failed is a test nobody has seen work.

**The permission model, and why it is this small.** Two roots, `company` and `executive`, matching how a
client's folder tree is organised, and two roles permitted one or both. Per-user and per-group permissions
are `SEC-ACL-002`, a later phase. A model with two roles can be read in full and tested exhaustively, and
getting the inside-the-search part right matters more than having more roles.

**The denial had to be rebuilt, because the first version never fired.** The deployed probe reported
`withheld 0` and `denied False` on every question, which looked like the filter working and was the feature
being dead code.

The first version inferred a denial from passages the store returned that the role could not see. The filter
is in the query and the store honours it, so nothing restricted comes back, so that count stayed at zero and
`denied` was never true. Every unit test passed, because their stub returned restricted passages to prove the
second line worked — and the second line only fires when the store misbehaves. The client's policy on what a
denied user is told would never have applied to anything.

The requirement is that a staff query for executive content returns *a denial*, and that needs knowing
whether restricted content **would have answered**. So the retriever now runs a second search against the
complement of the role's roots and compares scores.

**Only a better match counts.** A weak restricted match exists for almost any question, because every corpus
has something loosely related to everything, and denying on one would put a warning on most answers. A
warning that fires constantly is one users learn to ignore, which costs the disclosure its only job. So the
denial fires when the restricted best score **beats** the best permitted one, and a worse restricted match is
recorded for the record and not shown.

**What keeps that second search from being a leak.** It runs with `with_payload: false`, so no restricted
text enters this process: only a number crosses the boundary. Only the score is compared, never the content.
And the filter is expressed as `must_not` over the role's roots rather than a list of known-restricted ones,
so a folder nobody declared a role for counts as restricted rather than being silently omitted. The point id
Qdrant returns anyway is a uuid5 over the document id, so it does not name a file.

It runs in every mode, including silent, because the record has to report that a partial answer was
returned. That is precisely what an auditor asks about, and a silent organisation is the one most likely to
be asked.

**The result, 2026-10-13.** Run on the reference instance against the deployed store, with the restricted
fixture installed. The index holds 1059 company points and 1 executive point.

| Question, asked by the staff role | best permitted | best restricted | denied |
|---|---|---|---|
| CEO base salary | 0.62 | **0.7766** | **yes** |
| Executive bonus target | 0.58 | **0.6543** | **yes** |
| Severance on a change of control | 0.65 | 0.4088 | no |

The executive role returns `restricted_best None` on all three, because nothing is restricted from it, and
`denied False`. A staff search for either restricted figure returns ten passages and contains neither.

Run again with `--denial silent`, the same three rows report `denied True` and print `DISCLOSURE: none`,
which is the two fields doing their separate jobs: the fact stays accurate for the record and the disclosure
follows the client's policy.

**A limitation the third row exposes, recorded rather than discovered later.** The severance question is not
denied, because the restricted chunk scored 0.4088 against a permitted best of 0.65. The executive
memorandum does answer it, so that is a **false negative**: the content exists, a person cannot see it, and
the system does not say so.

The cause is chunking rather than the comparison. The memorandum is 1,541 characters, which is one chunk, so
its embedding is an average of salary, bonus, equity and severance together. A question about any one of them
matches that average weakly, while a targeted passage from the 10-K matches strongly. **The score comparison
is a heuristic and this is the shape of its failure**: a short document whose content is varied reads as a
worse match for every question than a long document's specific passage. The alternatives are worse, because a
bare hit test would deny on most questions and train users to ignore the warning, so this is a recorded bound
rather than a fixed defect.

**What a denied user is told is the client's policy, not ours.** Anthony's decision, 2026-10-13: this
should be something a client turns on or off rather than a behaviour we choose for them. So it is declared
in the manifest under `access.denial`, and there are three modes because the trade has three defensible
answers.

| Mode | What the user is told | What it costs |
|---|---|---|
| `silent` | Nothing. The answer is built from what the role can see | An incomplete answer is indistinguishable from a complete one |
| `denied` | Documents exist that may answer this and they lack access, with no count | Reveals that restricted content EXISTS |
| `counted` | The same, plus how many passages were withheld | Reveals existence and implies how many documents hold it |

**The default is `denied`**, and the reasoning is the product's own: a silent system returns a partial
answer with no way for a user to tell it from a complete one, which is the confidently-wrong failure this
whole design exists to fight. A client who weighs the leak of existence more heavily sets `silent` and gets
no disclosure at all. The dev cloud manifest declares `counted`, because a developer debugging a missing
passage wants the number and there is no client to weigh the trade.

**The fact and the disclosure are separate, and that separation is the point.** `withheld` and `denied` stay
accurate in every mode, so the record and any audit trail can report what happened; only `disclosure` is
governed by policy, and it is `None` in silent mode rather than an empty message. Collapsing the two would
make silent mean "we withheld nothing", which is a lie the record would then carry — and the record is the
artifact a client's auditor reads.

`SEC-ACL-003` records the trade, and the schema description says it belongs in the contract under a
compliance profile, the same way the content modes do.

**The fixture is restricted content, and it had to come first.** The corpus is four documents, all under
`company/`. Before this step there was nothing restricted to leak, so a permission test would have passed
against a retriever with no filter at all — the failure mode a permissions test is most prone to. The
fixture is a synthetic executive compensation memorandum carrying two figures that appear nowhere else in
the corpus, `418,750` and `62.5`, so a leak is found by grepping for a figure rather than only by comparing
counts.

**If it breaks.**

- `unknown role`: the role is not in `ROLES`, and the retriever refuses rather than defaulting. A default
  here grants the broader of two permissions to a typo, which is the failure worth being loud about.
- The probe reports a staff store return above zero: the query's filter clause is not reaching Qdrant.
  Print the body the retriever builds and check that `filter.must` carries the `acl` clause, rather than
  checking the results, because the results are what a missing filter also produces.
- A denial appears when the client declared `silent`: the renderer did not write `ACCESS_DENIAL_MODE`, so
  the retriever fell back to its default. Check `grep ACCESS_DENIAL_MODE rendered/env`, because the
  fallback is `denied`, which is the mode the product defaults to: a deployment that lost the variable
  discloses rather than hides, and the symptom is a disclosure where none was configured.
- `withheld` above zero on a normal query: the store returned something the role may not see. That is a
  second line rather than the first, so it working is good news and it firing means the first line is not.
- Fewer passages than the limit with nothing withheld: overfetch is too low, or the corpus is small. The
  store is asked for three times the limit because withheld passages consume part of the result set.

### 3.9 Hybrid search

**Goal.** An exact reference a vector search cannot see is found by the lexical arm. `RET-HYBRID-001`, and its
verify line asks for a question where hybrid on succeeds and hybrid off fails.

**The measurement first, because the arm is the largest piece of this step.** Seven questions built from
identifiers that genuinely appear in the corpus were put to the deployed vector arm and to plain Postgres
full-text, before anything was built.

| Identifier | Chunks mentioning it | Vector found it | Plain full-text |
|---|---|---|---|
| Form W-3SS | 2 | **missed** | **missed** |
| Pub. 515 | 14 | **missed** | **missed** |
| Pub. 519 | 15 | found, not first | **missed** |
| Form W-3 | 10 | found | **missed** |
| Pub. 15 | 53 | found | **missed** |
| Form W-2 | 36 | found | **missed** |
| Form W-4 | 32 | found | **missed** |

**That table was read wrongly the first time, and the correction matters.** It has one question per
reference, and it was taken to mean the vector arm misses rare references. Two probes then disagreed about
`Pub. 515`, which turned out to be because they asked different questions: *"What does Pub. 515 cover?"* is
found at rank 7, and *"What is in Pub. 515?"* is not found at all.

Asking each reference five ways gives the real finding:

| Reference | bare form | "tell me about ..." | best phrasing | spread |
|---|---|---|---|---|
| `Pub. 515` | **missed** | **missed** | #5 | #5 to #75 |
| `Form W-3SS` | **missed** | **missed** | #0 | #0 to #23 |
| `Pub. 519` | **missed** | **missed** | #3 | #3 to #85 |

**The spread within one reference is as wide as the spread between references.** So what the lexical arm
fixes is *phrasing*, not rarity: the vector arm finds a reference only when the words around it happen to
point at the right passage, and it found `Form W-3SS` at rank 0 for one question and not in 200 for another.

**And the bare reference is missed every single time**, which is the form a person uses. Someone looking
something up types the thing they want, not a sentence about it. That case is what the arm exists for, and it
is a stronger reason to build it than the rarity claim was.

**And plain full-text matched none of the seven.** Two faults, both read from the database rather than
guessed:

    What does Pub. 515 cover?        ->  'pub' & '515' & 'cover'      matches nothing
    When must Form W-3SS be filed?   ->  'must'&'form'&'w-3ss'&'w'&'3ss'&'file'
    How long must I keep a Form W-4? ->  'long'&'must'&'keep'&'form'&'w'&'-4'

`plainto_tsquery` ANDs every term of a natural question, so a chunk saying "Pub. 515" without the word "cover"
cannot match. And Postgres splits a reference unpredictably: `Form W-4` becomes `'w' & '-4'`, `Form W-3SS`
becomes three tokens. Stemming is right for prose and wrong for a reference, and no configuration of a
text-search parser makes `W-4` stable.

**So the arm normalises references itself.** `services/ingestion/identifiers.py` extracts them with a pattern
that requires a leading capital and reduces them to letters and digits, so `Form W-4`, `W-4` and `w4` all
become `w4` on both sides of the search. It lives beside the worker rather than in the retriever because the
ingestion image copies a fixed file list and a module both images need has to sit inside one build context.
The dependency already ran that way, since the retriever imports the worker for its Services record.

Two things the measurement forced, and both are in the module's tests. A pattern without the leading capital
pulled `is67` out of "the mileage rate is 67 cents per mile", which would have matched ordinary sentences on a
numeral. And normalisation alone could not match `Pub. 515` to a document saying `Publication 515`, so a small
alias table expands the abbreviation. That is the same corpus-mined glossary the architecture calls for on the
query side.

**The acceptance test, verified on the deployed store.** `chunk_index: 1060 rows, 435 carrying identifiers`.
Hybrid on against hybrid off, through the real code path with the lexical arm pointed at an empty result:

| Reference | hybrid on | hybrid off | verdict |
|---|---|---|---|
| `Pub. 515` | found | **missed** | **HYBRID WINS** |
| `Form W-3SS` | found | **missed** | **HYBRID WINS** |
| `Pub. 519` | found | **missed** | **HYBRID WINS** |
| `Form W-4` | found | found | both, as expected |
| `Pub. 15` | found | found | both, as expected |

Three references the lexical arm is what finds, and two controls where the vector arm already did, so the test
shows the arm helping where it should rather than disturbing what worked. The bare form is deliberate: a test
built on a phrasing the vector arm happens to handle would prove nothing.

**The store.** `010_chunk_index.sql` adds a table with the chunk text, the access label, a generated `tsvector`
for prose, and a normalised `identifiers text[]` with a GIN index. The worker writes it beside the vectors, and
clears a document's rows when the document changes or goes, because a row no vector point matches is a row the
fusion would still rank.

**The fusion is ours**, `k=60`, equal weights, tie-broken on chunk id. The architecture keeps it out of the
database for determinism, and a citation that moves between two identical runs is a citation nobody can check.

**The leak this step could have reintroduced.** A lexical arm is a second copy of every document. Without the
access clause in its query it becomes a second path to content the vector path protects, and the failure is
silent: the answer is correct, cited correctly, and shown to someone who should not see it. So `acl` is a
clause in the lexical query exactly as it is in the vector one, and there is a test asserting the clause is
there rather than asserting the results look right.

**If it breaks.**

- No identifier hits at all: the lexical arm is not running. It returns an empty list when `DATABASE_URL` is
  unset or unreachable and prints why, and `result.arms` records `lexical: 0`, which is how a caller tells
  "found nothing" from "did not run".
- An identifier hits the wrong document: check `identifiers.extract` on both the question and the chunk text.
  The two sides share one function, so a disagreement means a chunk was indexed before the normalisation
  changed, which `PAYLOAD_VERSION` handles by re-deriving the corpus.
- A question with no reference in it returns nothing from the lexical arm: expected, and the dense arm answers.
- Fewer passages than the limit: the two stores have drifted. A chunk the lexical arm names and the vector
  store does not hold is counted in `withheld` rather than skipped, because that is a reconciliation fault
  worth seeing.

### 3.10 Versioning and duplicates

**Goal.** A superseded document is not cited while its replacement exists, and the same document under two
filenames is flagged rather than indexed twice. `RET-VERSION-001` and `RET-DUPE-001`.

**Commands.**

    cd /opt/oi-core
    git pull
    docker compose -f deploy/office/compose/docker-compose.yml --env-file rendered/env \
      --profile tools run --rm audit migrate
    docker compose -f deploy/office/compose/docker-compose.yml --env-file rendered/env up -d --build ingestion
    docker logs ingestion --tail 20

    docker exec ingestion python3 /app/ingest.py --versions

**Expected.** The migration applies `011_document_versions.sql`. `--versions` prints the declarations and any
unresolved near-duplicate flags.

**The design is the research's, and it is deliberately not clever.** A document version registry with an
explicit `is_current` flag, set at ingest and reviewable by a human. Nothing here guesses which of two
documents is current. A guess that is wrong is invisible; a flag that is unresolved is not.

Three pieces.

*A registry.* `document_versions` holds `is_current`, the replacement, the date, and a note. Every document
is current by default, so a corpus that never touches this behaves exactly as it did before the table existed.
Declaring is one command:

    docker exec ingestion python3 /app/ingest.py --supersede company/fee-2025.pdf \
      --by company/fee-2026.pdf --note "2026 schedule in force"

*Near-duplicate detection that flags rather than acts.* The signature is a hash of each chunk's **text**. The
share is over the **smaller** document: a five-chunk summary of a hundred-chunk report is entirely contained in
it, and an overlap over the larger would read as 5 percent while one over the smaller reads as 100. Neither is
"these are the same document", which is why the pair is recorded and a person decides.

**Two faults, both found by running it rather than reading it.** The first version of this compared chunk ids
and could never have fired. An id is
`uuid5(doc_id, source_sha256, chunker_version, ordinal, content_hash)`, so it contains the document it belongs
to: two byte-identical files produce ids that all differ, the overlap is structurally zero, and an exact copy
in the corpus is reported as no duplicate at all. Its unit test passed because the fixture supplied shared ids
by hand, which is a fixture agreeing with itself rather than modelling the data. The test now derives ids the
way the chunker does and compares text hashes, so a regression to id comparison fails it.

    docker exec ingestion python3 /app/ingest.py --scan-duplicates
    docker exec ingestion python3 /app/ingest.py --versions
    docker exec ingestion python3 /app/ingest.py --distinct company/policy-copy.pdf \
      --note "a separate policy, not a copy"

**`--scan-duplicates` exists because detection otherwise runs only when a file arrives.** A corpus ingested
before the check existed is never examined: both copies are already indexed, every cycle skips them, and the
comparison never runs. That is exactly what the first deployed test found, and it is not a rare case, because
every corpus becomes an old corpus the moment the feature ships.

**Two things this deliberately does not do.** It does not delete the second copy, because losing a document a
client put in a folder deliberately is worse than keeping two. And it does not drop a superseded passage from
the results: the research is explicit that an answer supported only by a non-current document is **refused with
the reason `superseded`**, which means the passage has to reach the verifier carrying the fact. Dropping it
here would answer the question from a different document with no sign that the right one existed and had been
excluded, which is a wrong answer wearing a correct one's clothes.

So a passage carries `is_current` and the result carries the list of superseded documents among its passages.
Step 3.11 is what refuses on it.

**If it breaks.**

- `--versions` prints nothing: no document has a declaration, which is the default state and not a fault.
  Every document is current until somebody says otherwise.
- A duplicate pair is not flagged: run `--scan-duplicates` first, because detection normally runs only when a
  file arrives and an existing corpus is never examined. If it still reports nothing, the identical text is
  under the floor: `DUPLICATE_MIN_SHARED_CHARS` is 500, so two documents sharing a cover line or a form header
  are deliberately not compared. Check with
  `select doc_id, count(*), sum(length(text)) from chunk_index group by doc_id`.
- A pair is flagged that is genuinely two documents: resolve it with `--distinct`, which records the decision
  and clears the flag without superseding anything. The flag is the feature working, not a false positive: the
  system cannot tell two policies apart from one policy stored twice, and it is not supposed to try.
- A one-page document exported twice is not flagged: the floor is measured in characters, not chunks, so a
  single-page fee schedule clears it and a shared cover line does not. If it is under 500 characters of
  identical text it will not be reported, which is the intended behaviour and the number to change if a
  client's documents are smaller than that.
- The same pair appears twice: it cannot, because the pair is stored ordered with a unique constraint. If it
  does, the ordering in `near_duplicates` has been changed.

### 3.11 Citations

**Goal.** Every citation is verified against the retrieved set, the page, and entailment. `RET-CITE-001`, whose
verify line asks that an invented citation index is rejected and an unsupported sentence is marked rather than
silently kept.

**What this step is, in the research's words.** The mechanical layer "covers fabricated citations, page drift,
missing quotes, numeric substitution, identifier substitution, superseded sources and low-confidence scans",
and the literal identifier assertion inside it is "the cheapest item in the entire architecture and the highest
value". That is seven deterministic checks, no model, no new dependency.

**Why it exists at all is one level up from retrieval.** Every layer below this one checks whether the answer
matches the source. None checks whether the source is the right source or was read correctly. Those are the two
most likely wrong answers in a small business, a real number from a superseded document and a real number from
a misread scan, and **both pass the mechanical layer, both pass entailment, and both come out certified.**

**The seven checks, in the order they run.**

| Check | Reason | What it catches |
|---|---|---|
| Citation resolves | `citation_not_found` | A citation to a passage that was never retrieved |
| Source is current | `superseded` | A real figure from a document a person replaced |
| Confidence | `low_confidence_scan` | A figure from a page the parser was unsure about |
| Numbers | `number_not_in_source` | A substituted digit |
| Identifiers | `identifier_not_in_source` | The right document and the wrong form |
| Quotes | `quote_not_in_source` | Words in quotation marks that are not in the source |
| Page | drift, reported separately | The right words with the wrong page attached |

The order is deliberate. A citation that does not resolve is checked first, because every later check reads the
passage it names and a fabricated index would make them all meaningless. Supersession is second, because a
number from a replaced document is a correct quote of the wrong source and no amount of literal matching
catches it. The literal checks come last, since they are the ones that can be false-refused.

**The false refusal is the thing to guard.** A check that refuses correct answers gets turned off, and a check
that is turned off catches nothing. So the answer is marked, not dropped, and there are as many tests for
answers that must pass as for answers that must be refused. Four rules follow from that:

- **Trailing zeros only.** `145.00`, `145.0` and `145` are one number. `0.67` is the mileage rate and the first
  version stripped its leading zero to `.67`, which matched nothing and refused a correct answer.
- **Supersession fires on ONLY.** A claim citing the replacement alongside the thing it replaced is supported,
  because the current source is there. Refusing on any superseded citation would refuse a correct answer.
- **The confidence gate applies to figures only.** A digit read imperfectly is a different number; a word read
  imperfectly is still the word. Refusing every sentence from a scanned document would refuse whole filings.
- **A sentence with no citation is `uncited`, not refused.** Transitions and direct answers have nothing wrong
  with them, and refusing them would refuse most of a normal reply.

**The sentence splitter, which is where the subtleties are.** A marker trails the sentence it supports, and
getting that wrong runs every check against the wrong passage. Three faults, each hidden behind the last:

- The first version cut `Pub. 515 covers withholding` into `Pub.` and the rest, because a period, a space and a
  capital is exactly what a sentence boundary looks like. The fragment was then reported uncited and a correct
  answer was marked unsupported. The splitter now knows the abbreviations this corpus uses, and anything
  ambiguous is **not** a boundary, because merging two sentences is safe and splitting wrongly is not.
- The second matched the marker as part of the separator and swallowed it, so every claim came out with no
  citation and every claim was reported uncited.
- The third required whitespace directly after the period, so `employee.[1] Form W-4` never split at all.

The rule that came out of it: **the splitter must never lose a marker**, and there is a test that says so.

**The honest bound, and it is a real one.** The confidence gate is **document-level, not page-level**, because
docling-serve does not populate the per-page confidence map this release exposes. So a page whose digits were
misread cannot be told from a page read cleanly, and the gate refuses on a low-scoring document rather than on
a low-scoring page. That is coarser than the architecture asks for. Docling's per-page confidence is named in
the research as the Wave 4 item to verify, and until it is populated this check is the document-level version.

**The first run found something by accident, and it is the best evidence this step has.** The probe asked for
the standard labour rate, and the retriever returned the **superseded 2025 schedule as passage [1]**, with the
current 2026 one at [2]. The verifier then refused the claim built from [1] with the reason `superseded`.

That is the whole chain working on a real question with no contrived fixture: a real figure, from a real
document, on a real page, correct in every literal respect, and **refused because the document had been
replaced.** Nothing below this layer would have caught it.

**The probe was wrong and the check was right.** It built its "correct" claim from passage [1] on the
assumption that the top-ranked passage is a good source, which is the same class of mistake as the fixtures in
3.9 and 3.10: a test that encodes an assumption about the data instead of the property being tested. It now
builds the claim from the first passage a person may rely on and prints the currency of every passage it
retrieved.

**Open, and it is a ranking question rather than a correctness one.** Both versions were retrieved, and the
superseded one ranked first. The answer is refused rather than wrong, which is the designed behaviour, but a
user asked about the current rate and got a refusal when a good answer was in the result set two places down.
Whether current documents should be boosted in ranking is a search-quality change that should be measured
against the golden set rather than guessed at here, so it is recorded as open rather than fixed.

**Entailment is not here.** It is Layer 1 and it needs a model, and the reply path does not have one until
Phase 4. So `supported` means every mechanical check passed, which is **not the same as true**. The research is
explicit that the verifier's own false-negative rate sets the ceiling on what the product can claim, and that
the ceiling must be published rather than implied.

**If it breaks.**

- A correct answer is being refused: read the `detail`, which names the figure and the passage. If the figure
  is written differently from the source, the normalisation is the suspect, and the fix is a rule rather than a
  loosened check.
- Everything from a scanned document is refused: the confidence gate is firing on the document's own score.
  Check `select distinct payload->>'parse_score' from ...` before assuming the gate is wrong, because a
  genuinely low score means the parser was unsure and the refusal is correct.
- A claim is reported `uncited` that clearly has a citation: the splitter lost it or cut the sentence. The
  marker is `[n]` with no other form, and there is a test asserting no marker is ever dropped.
- Nothing is refused at all: the answer has no `[n]` markers, so every claim is `uncited`. The marking
  convention is the prompt's to teach and this module cannot invent it.

### 3.12 Redaction

**Goal.** PII is redacted before any frontier-bound request leaves, and the redaction is recorded.
`SEC-REDACT-001`, whose verify line asks that a question containing an SSN reaches Bedrock with the pattern
replaced and that the audit shows the data class.

**The audit module named this gap in Phase 0.** Its four patterns are documented as "not a compliance
guarantee", with a note that "SEC-REDACT-001 in C2 owns the real engine, which has to run before anything
leaves for a frontier model rather than only on the way into the record". This step is that engine.

**Two jobs, and the order between them is the design.**

*The gate asks whether the request may leave.* `services/egress/lane.py` reads `FRONTIER_CLASSES` from the
rendered environment, which comes from `access.egress.frontier_classes` in the manifest. A class the client has
not permitted is refused **before redaction is attempted**, because redacting a regulated document and sending
the remainder answers a different question than the one the client decided.

*The redaction asks what must not leave inside a request that may.* `services/egress/redaction.py` replaces the
identifiers a business document carries and counts what it replaced.

**It fails closed, and the direction is the whole point.** An unset permission set, an unknown class, a lane
nobody configured: every one refuses. The alternative is a system that sends a client's confidential document
to a provider because a variable was misspelled, and it does so silently because the call succeeds.

**Three properties, each from a way this could succeed while doing nothing.**

*It runs on the bytes that are sent.* `prepare()` takes the assembled request and returns what to send. There is
no path that returns the original alongside it, because a function that returns both invites a caller to send
the wrong one and the mistake is invisible since both are valid requests.

*It walks every string, not a list of fields.* The question is typed by a person who knows what not to type; the
**retrieved passages are whatever the corpus contained, and they are the part most likely to hold a client's
identifiers.** A redactor that covers the question and skips the passages reports success on every request and
leaves the actual risk untouched. Walking the whole structure means a field added in 2027 is covered without
anyone remembering to cover it.

*One pass, ordered alternatives.* Redacting in a loop over patterns re-scans the text it just wrote, so a value
replaced with a label can be re-matched by a later pattern. A single combined pattern cannot re-match its own
output. The alternative ordering is deliberate too: credentials first, because a key that reaches a provider's
logs is not a privacy incident but an incident, and rotation is the only remedy.

**The false-positive trade, made deliberately rather than hidden.** A 16-digit invoice number is not a card, and
redacting it costs a legitimate question its reference. A card number reaching a frontier model is a payment
incident. So **shape decides whether to redact** and the check digit decides only what the reader is told:
`[REDACTED-CARD]` for a number that passes Luhn, `[REDACTED-LONG_NUMBER]` for one that does not, counted
separately as `unverified_card_shapes`. A card whose digits OCR damaged fails the check and is still a card, so
the check cannot be allowed to decide.

Figures are the other direction and are tested: `145.00`, `1,500.00`, `418750` and `0.67` are not identifiers,
and a pattern that catches them makes every answer about money unanswerable.

**The honest bound.** This is pattern matching. It catches identifiers that have a shape and it **does not catch
a person's name**, an address, or an account number written in words. It is a reduction in what leaves, not a
guarantee about what cannot, and presenting it as a guarantee is how a client ends up sending something they
would not have. There is a test asserting a name survives, so the bound cannot be quietly assumed away.

**The summary is recorded whatever the content mode, and the first version was not.** The redaction summary was
being put in the audit payload, and the default content mode is `hash`, which stores a hash of the payload and
not the payload. So on the arrangement this system ships with and recommends, **the record proved that a call
happened and proved nothing about the redaction**: an operator asking "was anything removed from this request"
got a null. The probe found it on its first run.

A redaction summary is not content. It says how many things of which kind were removed and never what they
were, so storing it is not storing the client's data, and it must not be governed by a setting about whether to
store content. Migration `012_redaction_record.sql` gives it four columns of its own: `redaction_count`,
`redactions`, `unverified_card_shapes` and `egress_allowed`.

**Open, and it is a tamper-evidence question rather than a correctness one.** The audit chain's canonical string
covers twelve fields and **not the four new ones**, so a row whose `redactions` value was edited by hand would
still verify. That is a real gap in a mechanism whose whole purpose is detecting exactly that. It is recorded
rather than fixed here because extending a hash chain's canonical form invalidates every existing row's hash:
the formula is what the stored hashes were computed with, so a change needs a version column and a verifier that
reads the version per row, and doing that hastily at the end of another step is how a working security property
gets broken. It is the first item for 3.14, which owns the audit record's lifecycle.

**The record proves what was sent, not what was typed.** The hash is taken **after** redaction, because a hash
of the original is a second copy of the thing being removed: it cannot be read, but it can be compared, which is
enough to confirm a guess about a Social Security number. The audit column already says it holds "SHA-256 of the
prompt as sent". The record carries the data class, the kinds and counts of what was removed, and never a value.

**Commands.**

    cd /opt/oi-core
    git pull
    # Re-render, because the egress policy is new and rendered/env is a generated file.
    python3 scripts/render_manifest.py --manifest manifests/dev-cloud.yaml
    grep -E 'FRONTIER_CLASSES|EGRESS_REDACTION' rendered/env

**Expected.** `dev-cloud` renders `FRONTIER_CLASSES=public,internal` and `EGRESS_REDACTION=on`. `dev-office`
renders `FRONTIER_CLASSES=` empty, which is its policy and not an oversight: that build has no frontier lane,
and the gate reads an empty set as "nothing is permitted" rather than as "no opinion".

**If it breaks.**

- Nothing leaves and the reason says no classes are permitted: `rendered/env` has not been re-rendered since
  the manifest gained the policy, so `FRONTIER_CLASSES` is absent. Absent refuses, which is the intended
  behaviour for an unconfigured deployment and a confusing one for a configured deployment whose env is stale.
- A figure is being redacted: check `PATTERNS` for a shape that is too broad. The decimal and thousands
  separators are the ones that matter, and there is a test for each.
- A real identifier survives: it has no shape this recognises. Names and addresses are the known bound, and a
  client whose documents are full of them needs a different mechanism rather than a longer pattern list.
- The record holds the original text: the hash is taken from `cleaned` and the payload from the redaction.
  Check the content mode as well, because `--store-payload` writes content the mode would otherwise not keep.

### 3.12b The chain covers the redaction summary

**A fix to 3.12, done before moving on because it is a hole in a mechanism whose purpose is detecting exactly
this.** Migration 012 added four columns and the chain's canonical string was not changed to include them, so a
row whose `redactions` value was edited by hand still verified. In a table that exists to detect that, a field
the chain does not cover is a field that can be rewritten while every check passes and reports the record
intact.

**Why this is a version rather than an edit.** A row's stored hash was computed from a canonical string, and
verification recomputes that string and compares. Change the formula and every row written under the old one
fails, which is **indistinguishable from tampering**: the verifier reports a chain break on rows nobody touched,
and the first real break is lost in the noise. On the development instance that would have been rows 1 to 50.

So the formula is versioned. `chain_version` is a column, version 1 is the original thirteen fields, version 2
adds the four, new rows are version 2, and **nothing is rewritten**.

**Commands.**

    cd /opt/oi-core
    git pull
    docker compose -f deploy/office/compose/docker-compose.yml --env-file rendered/env \
      --profile tools run --rm audit migrate
    docker compose -f deploy/office/compose/docker-compose.yml --env-file rendered/env build audit
    docker compose -f deploy/office/compose/docker-compose.yml --env-file rendered/env \
      --profile tools run --rm audit export
    docker compose -f deploy/office/compose/docker-compose.yml --env-file rendered/env \
      --profile tools run --rm audit verify exports/2026-10-13/audit-events.jsonl

**Expected.** `1 migration(s) applied`. The export then carries every row with its own `chain_version`, and
`verify` reports no problems: the rows written before the change still verify under version 1 and the rows
written after it verify under version 2, in one file. The export before this change had 50 rows at version 1.

**The result, 2026-10-13, on the reference instance.** 38 rows at version 1 and 1 at version 2 after the fix.
The export of the day holds rows 48 and 50 at version 1 and row 51 at version 2 **in one file**, and `verify`
reports `chain intact`. No row was rewritten.

**And the positive test, because a verifier that passes everything has proved nothing.** A copy of that export
with the version 2 row's redaction summary erased, its count zeroed and its egress decision cleared:

    FAIL /tampered/audit-events.jsonl
      - line 3: row_hash does not match the row contents

Before this change that edit left the hash valid and the verifier reported the record intact. **Both directions
are now shown: the untouched history still verifies, and the edit that used to be invisible is caught.**

**What to look for, and it is the whole point.** `select chain_version, count(*) from audit_events group by 1`
must show version 1 rows beside version 2 rows, and `verify` must pass on the file holding both. **A fix that
made the whole history verify by rewriting it would have destroyed the property the chain exists for.**

**The summary is flattened rather than serialised as JSON**, to sorted `key=value` pairs. The writer is Postgres
and the verifier is Python, and `jsonb::text` puts a space after every colon and comma, so matching the two
textually would mean reproducing one language's serialiser in the other, and the day either changed every row
would fail at once. Sorted pairs are a form both produce exactly.

**A row from a newer writer is reported as unchecked**, not as a mismatch. A version this verifier does not know
cannot be checked, and calling that tampering would be a false finding in the one report where a false finding
does the most damage.

**If it breaks.**

- Every row fails verification after the migration: the canonical formula in `audit_row_canonical` and
  `row_canonical` in `audit.py` have drifted. They are two copies of one formula and there is a test asserting
  the field order, the version branch and the separator match.
- A row reports an unknown chain version: the writer is newer than the verifier. Update the verifier rather than
  treating it as a break, which is what the message says.
- The old rows fail and the new ones pass: the version branch is not being read per row. Check that the export
  carries `chain_version`, because a verifier that cannot see it reads every row as version 1.

### 3.13 Injection resistance

**Goal.** Prompt injection from document content cannot change the lane or the answer's boundary.
`SEC-INJECT-001`, whose verify line asks that a document containing an instruction to reveal other documents
produces a refusal.

**Four controls, and only one of them is a detector.** The order matters because three of them are structural
and hold regardless of what a passage says, while the fourth recognises attempts and is a signal.

*The lane is decided before any content is assembled.* It is a property of the request, read from the manifest,
and **there is no code path from retrieved text to `data_class`.** That absence is the control: not a check that
recognises an attempt, but no route for one to take. There is a test asserting the absence, because a behaviour
test would not notice the day somebody added a route.

*The boundary is the access filter at retrieval.* Retrieval has already happened and was already filtered by the
role's permitted roots, and it is **not re-run because a passage asked for something.** So an injection in a
passage a role can see cannot reach another passage: the only material it can reach is material already in front
of it. That limits the damage to what the role was allowed to see, which is what the requirement asks for, and
it holds whatever the model does.

*The content is data and is presented as data.* `assemble` puts retrieved text inside `<<<DOCUMENT ... DOCUMENT>>>`
after the instruction, never concatenated into it. This is the difference between a document that says "ignore
your instructions" and a document that **is** one, and it is decided by where the text sits rather than by what
it says. It therefore holds for a passage no detector would flag, which is the case no detector can help with.

*And the detector, which is a signal.* A passage carrying text aimed at the assistant rather than at a reader is
quarantined: excluded from the request, refused, and recorded. The signals are weighted, the threshold is four,
and the weakest signal is three, so **naming the assistant once is not enough.**

**The detector is shaped around its false positives, because that is what gets one turned off.** A policy
document telling employees not to share passwords is imperative, contains "must not" and "do not" and
"confidential", and is exactly the kind of document a client has. The signals aim at **second-person address to
an assistant** and at **attempts to change its behaviour**, not at imperative language, and there are tests
asserting that a fee schedule, an employee policy and a document about assistants all pass untouched.

**Two bounds, and the deployed run found the second.**

*Detection is not a guarantee.* A passage written to look like ordinary prose while steering the model is not
caught by any pattern, and the controls that hold in that case are the structural three, which do not depend on
recognising anything.

*And quarantine is per passage, which on a short document is the whole document.* The installed fixture is **one
chunk of 964 characters**, below the chunker's 384-token minimum, so the instruction and the invoices and the
shared-drive paragraph are the same passage and quarantining it took all of them. **The answer is safe and the
document is unusable**, which is the right direction to fail in and still a real loss. The honest fixes are a
chunker that splits on headings or a quarantine that removes the instruction rather than dropping the passage,
and neither is in this step.

**A probe defect worth recording, because it was a false pass.** The check for whether the legitimate content
survived read `"shared drive" in assembled.user`, and the user turn **ends with the question**, which asks about
the shared drive. So the assertion was satisfied by the input rather than the output: it passed on a run where
the whole document had been quarantined, and printed that the ordinary sections were still available. It now
tests the document block with the question appended rather than the whole turn, using terms that appear in the
document and not in the question. **An assertion that can be satisfied by the question is not testing the
assembly.**

**The refusal names the passage by the index the citations will use**, so a reviewer reading "passage 2 was
quarantined" can find passage 2, and the record carries the signal names, the score and a 120-character excerpt
and never the passage. **A record holding the content would be a copy of what the quarantine kept out.**

**If it breaks.**

- A short document is quarantined whole and its usable content is lost: that is the bound above, not a
  misconfiguration. Check the chunk count with
  `select doc_id, count(*) from chunk_index where doc_id = '...'`; one chunk means one passage means no partial
  quarantine.
- A legitimate document is quarantined: read the signal names in the record. If they are `assistant_address` or
  `override_behaviour` on ordinary prose, the pattern is too broad; if they are `restricted_target`, the document
  legitimately names a restricted folder and the pattern needs the second-person context it currently lacks.
- Nothing is quarantined and the passage came back: the text is prose that steers without instructing. **This is
  the known bound**, and the structural three are what is holding. Do not answer by adding patterns to catch it.
- The refusal appears and the passage was still sent: `assemble` builds one string and returns it. Check that the
  caller is using `assembled.user` rather than the passages it passed in.

### 3.14 Retention

**Goal.** Three requirements and one of them is the work: `DATA-PURGE-001`, a client-requested purge deletes
content everywhere it is stored including the vector index; `DATA-HOLD-001`, a legal hold blocks a purge;
`DATA-EXIT-001`, termination destroys client data and produces evidence that it happened.

**`EVERYWHERE` is the requirement.** The vector index is named because it is the obvious one. The one that gets
forgotten is whichever store was added most recently, and in this build that is **the lexical index from step
3.9** — a second copy of every document, added six steps after the first. A purge that clears the vector store
and not that one still answers the question it was purged for, **with a correct citation to a document that no
longer exists.** That is worse than an error, because the answer looks right.

**So the stores are a list, and the list is checked against the writers.** `STORES` in
`services/retention/retention.py` enumerates them, and a test reads the ingestion worker's write paths and
asserts a purge store exists for each. **A new store added in a later step without a purge path fails that test
rather than failing in a client's question six months later.**

**A store that reports zero is recorded, not skipped.** The result is per store rather than a total, so a store
that was never wired in is visible instead of looking like a store that had nothing to do. A total of forty
cannot show that one of three stores contributed nothing, and **"did nothing" is the shape of the bug**.

**A purge that cannot reach a store is not a purge.** The record says `complete: false` and the command exits
non-zero. Reporting success because the reachable stores succeeded is how a client is told their data is gone
while it is not.

**The hold covers documents added later.** A deployment-scope hold has no document list, so a document uploaded
after the hold was placed is covered by it. **A hold that only covered what existed at the time is a hold with a
gap in it, and the gap is where the document that matters ends up.** The hold is checked in **one query** before
any store is touched, because checking it per store means a hold obeyed by the store somebody remembered and
ignored by the one they did not, which from the client's side is the same as no hold at all.

**The destruction record is hashed, and the hash is of the record.** The client keeps the file; the hash is what
lets them show later that it is the one we produced, and neither side has to trust the other's copy.

**A conflict between two requirements, found by running the teardown, and the resolution.** `DATA-PURGE-001`
says delete the content everywhere it is stored. **Content stored in the audit record cannot be deleted**, for
two reasons that both come from the same property. The table is append-only: an UPDATE is refused by a trigger
unless `oi.allow_audit_maintenance = on`, and that hatch exists for the planned purge of whole rows rather than
for rewriting them. And opening it would not help, because **the chain covers `payload_sha256` and
`payload_bytes`**: clearing a payload without updating those leaves the row failing verification, and updating
them makes the chain a record of what somebody chose to leave in it.

So the purge **counts** rather than clears, and **a non-zero count makes the purge incomplete**. A document
whose text is still readable in the audit record has not been purged, whatever the other stores reported, and a
purge that said otherwise would be the exact failure this requirement exists to prevent. The record carries the
count and a note saying why it cannot be zero.

**The mitigation is not to store it there, which is why the default content mode is `hash`.** A deployment that
selects `full` is choosing to keep content it cannot later delete, and that is a decision a client has to make
knowing the consequence. **This is the one place where the audit guarantee and the deletion guarantee genuinely
conflict, and the resolution is to make the conflict visible rather than to resolve it silently in either
direction.**

**The audit chain is deliberately not destroyed**, and the record says so. It says which documents existed and
that they were destroyed, which is what lets the destruction be checked by someone who does not trust us.
Destroying it would leave the client unable to show anyone that we did what we said. The stored payloads are
cleared; the events are kept.

**Commands.**

    cd /opt/oi-core
    git pull
    docker compose -f deploy/office/compose/docker-compose.yml --env-file rendered/env \
      --profile tools run --rm audit migrate
    docker compose -f deploy/office/compose/docker-compose.yml --env-file rendered/env \
      --profile tools run --rm retention --holds

    # A document, with a reason that goes in the record.
    docker compose -f deploy/office/compose/docker-compose.yml --env-file rendered/env \
      --profile tools run --rm retention --purge company/invoice-2024.pdf --reason "client asked"

    # A hold, then the hold refusing the purge, then the release.
    ... retention --hold company/tax.pdf --reason "litigation in progress"
    ... retention --purge company/tax.pdf --reason "client asked"     # refused, exits non-zero
    ... retention --holds
    ... retention --release-hold 1

    # The end of a contract.
    ... retention --destroy-all --reason "contract terminated 2026-11-30"

**Expected on the acceptance run.** A hold refuses the purge and **changes nothing**; after the release the
purge clears the vector index, the lexical index and the manifest; and **the same question asked before and
after returns the document the first time and not the second.** That last pair is the requirement: a check that
only asked afterwards could pass on a corpus that never held the document.

**If it breaks.**

- The document still answers after a purge: ask `select doc_id, count(*) from chunk_index group by doc_id`.
  The lexical arm is the usual answer, and the fix is a store in `STORES` rather than a larger delete in one
  place.
- `complete: false`: read the store that carries the error. Nothing is deleted from the stores that succeeded
  until the failed one is fixed? **No such rollback exists**, and that is worth being plain about: a purge that
  fails part way leaves the document partly gone. The record says which stores confirmed, and the fix is to run
  it again once the store is reachable, which is safe because every delete is by filter rather than by a list of
  ids.
- A purge is refused and no hold is visible: check `select * from legal_holds where released_at is null`. The
  deployment-scope hold is the one that catches everything and is the one people forget they placed.
- The destruction record fails its own hash: the record travels with the hash, so a mismatch means the file was
  edited. Do not try to reconcile it; the hash is the point.

### 3.15 The yardstick, and what it says

**Three requirements closed together, and the order matters: RET-EVAL-001 is the harness the other two are
measured with.**

    cd /opt/oi-core
    docker compose -f deploy/office/compose/docker-compose.yml --env-file rendered/env \
      --profile tools build eval
    docker compose -f deploy/office/compose/docker-compose.yml --env-file rendered/env \
      --profile tools run --rm -v /opt/oi-core:/w eval

**The result, against the deployed store on 2026-10-13.**

    configuration: fused   k=10   n=10
      retrieval  100% (6/6)
      support    100% (4/4)
      support was not measurable on 2 answerable question(s): no figure in the reference answer

      category                  n   retr    supp
      paraphrase                1   100%     n/a
      single-fact-prose         2   100%    100%
      table-cell                2   100%    100%
      two-document              1   100%     n/a
      unanswerable              2     0%     n/a

      supported answers from documents with no real pages: 1 of 4
      unanswerable top scores reaching into the answerable range: True
        answerable   [0.684, 0.659, 0.655, 0.641, 0.633, 0.621]
        unanswerable [0.673, 0.607, 0.558, 0.524]

**Two rates, and the difference is the point.** Retrieval counts the document holding the answer among the
passages returned. Support counts a passage containing the answer's own value, which is much stricter: **a long
document is easy to be near**, and a system returning page 1 of the right filing for every question would score
well on the first and badly on the second.

**Writing the reference answers changed three of the ten questions.** The question set's own header says the only
honest way to write one is to read the parsed text and check, and doing it found that Q03 and Q04 were marked
answerable and are not:

- **Q03** asks for the deadline for reporting a new hire. Publication 15 gives the obligation, the addressee and
  the definition of a new employee, and **no deadline**, because the deadline is state law.
- **Q04** asks for a weekly withholding figure. Page 4 says verbatim that the Percentage Method and Wage Bracket
  tables are in **Publication 15-T**, which this corpus does not contain. **A corpus-selection failure, not a
  chunker failure.**
- **Q07** was re-scoped rather than flipped: the cover page states a share count as of October 2024 and the
  balance sheet states a different one in thousands at fiscal year end, so the answer gives both and says which
  is which rather than silently picking one.

**Q08 is a trap worth knowing about.** The corpus **does** contain a `0.67 per mile` mileage rate, in all three
fee schedules. It is what the company **charges a client**. There is no employee reimbursement policy anywhere, so
answering "67 cents per mile" is exactly the failure the question was written to catch: **a plausible number from
the wrong document is harder to refuse than no number at all.**

**Four structural findings from the same reading.**

- **Six of the ten questions draw on HTML filings whose every chunk reports page 1.** The rule that a cited page
  must fall inside the chunk's range is **satisfied trivially** for them. The harness counts supported answers
  from pageless documents and says the rule has not been tested on those.
- **Q02's answer sentence straddles the page 29/30 boundary**: page 29 ends "you must withhold a" and page 30
  opens with the threshold.
- **The unanswerable questions' top scores overlap the answerable ones**, 0.673 against a weakest answerable
  0.621. **No confidence threshold separates them**, so a refusal has to come from somewhere other than the top
  score. That is the measurement a refusal would be built on and the answer is that this one will not work.
- **The employer withholding threshold Publication 15 states cannot distinguish a single filer from a married
  one**, which Q02's premise assumed it could.

**Three faults found by running it, all of them the harness rather than the retriever.** Worth listing because
each one produced a number that looked like a measurement:

1. **Support required every figure in a paragraph to appear in one chunk.** The answers explain what they found,
   so Q06's names the sales figure, the fiscal year and the year-end date, and no single chunk of a financial
   table holds all three. **Q06 and Q07 were scored as misses while the retriever had put the right table in
   front of the model.**
2. **The question set named a publication where the corpus holds a filename.** `irs-p15-2026` against
   `company/irs-p15.pdf`, and `apple-10k-2024` only escaped because the filename contains the id. My first fix
   changed the question set and **the corpus validator refused it**, which settled that the manifest ids are the
   contract and the filenames are how the corpus was assembled.
3. **The fallback for an answer with no figure counted shared vocabulary** and reported 60 percent of it as
   support. That number was arbitrary and the mechanism is weak in a way no number fixes: a paraphrase question
   and its source legitimately share almost no wording. It made **Q05 grade on `10.19`, an exhibit reference
   number, and Q10 on `31`**, and the support rate read 100 percent. **The figure is now stated by whoever writes
   the question**, and a question without one is excluded from the rate and counted beside it.

**A rate at ten questions is not a quality claim.** The research says the same about its own gate at sixty, where
the interval around 55/60 is 0.80 to 0.95. Ten questions can catch a search that is broken, which is what this
does, and the client's own sixty arrive in C8 under `QLT-GOLDEN-001`.

**If it breaks.**

- Both rates read 0 percent and every question reports the same error: read one detail line. A mechanism failing
  closed and a corpus that answers nothing produce identical summaries, and only the detail tells them apart.
  This happened twice in this step.
- A category shows `n/a` for support: nothing in it could be measured. That is a coverage statement, not a
  failure, and the count of unmeasurable questions is printed above the table.
- A figure question misses: check the figure is stated on the question and that it appears in the corpus at all.
  `select doc_id, page_start from chunk_index where text ilike '%391,035%'`.

### 3.16 The reranker, verified

**`RET-RERANK-001` proved on 2026-10-13, and its two halves landed differently.**

    questions reranked: 10 of 10
    per question: slowest 428 ms, average 344 ms
    budget 5000 ms: WITHIN

    WHAT IT CHANGED
      Q07: rank 10 -> 1
      Q01: rank 1 -> 2
      Q06: rank 1 -> 2

**Latency is measured and comfortably inside the budget**, and it agrees with the third-of-a-second figure the
architecture records for a fifty-candidate pool, which was measured separately through the AWS API before the
reranker was wired in.

**The quality half is at ceiling and therefore shows no movement.** Retrieval and support were already 100 percent
without the reranker, so the comparison reports `+0%` and there is nothing for it to improve. **Reporting that as
evidence that reranking does not help would be the same mistake this phase has spent its time removing**, so the
summary says "same" rather than claiming a benefit or denying one.

**The evidence that it works is in the ranks rather than the rates.** Q07 moved **from rank 10 to rank 1**, which
is precisely the case the requirement is about: a passage the fusion barely kept inside the window became the top
passage, and **at rank 10 it was one place from being dropped**. Q01 and Q06 each moved from 1 to 2, so the
reranker demotes as well as promotes.

**A ten-question set cannot resolve a reranker's benefit when both rates are at their maximum.** The client's
sixty questions in C8 under `QLT-GOLDEN-001` are where the second half becomes measurable, and that is recorded
as the reason rather than as a gap.

**It was blocked and then unblocked, and the block is worth keeping.** The model needs a **paid AWS Marketplace
subscription**, 0.002 dollars per search unit, which is one query against up to 100 documents and therefore **one
unit per question** at our depth of 50. The first attempt failed with a 403 whose body named the missing actions
precisely:

    Model access is denied due to IAM user or service role is not authorized to perform the required AWS
    Marketplace actions (aws-marketplace:ViewSubscriptions, aws-marketplace:Subscribe)

**The mechanism under test reported its own absence rather than falling back to the fused order**, which is why
the cause was findable at all: a reranker that silently answered from the fused list would have produced a
comparison showing no difference between two identical runs.

**The IAM change and its state.** `oi-core-dev-bedrock-invoke` listed four text models and the reranker was
simply absent from the list. `cohere.rerank-v3-5:0` was added to it, and the two marketplace actions the error
named were added in a second statement whose Sid says why they are there. A separate policy was created first and
then removed in favour of one list, because two policies saying the same thing is two places for it to drift.
**The role carries the same three policies it carried before the work.**

**If it breaks.**

- `HTTP Error 403` with the marketplace message: the subscription has lapsed or the account changed. It is a
  paid agreement, not a permission, and no policy change substitutes for it.
- `reranked: false` on every question with a reason: read the reason. The mechanism reports rather than falls
  back, so the reason is the diagnosis and not a guess at one.
- The rates do not move: check whether they were at ceiling before the change. A reranker measured against a
  system already at 100 percent is being measured on ranks, and the comparison prints the rank movements for
  exactly that reason.

### 3.17 Time-based retention

**Goal.** `DATA-RETAIN-001`: retention and deletion defined for documents and conversations, not only the audit
log. The verify line names three places a document has to be gone from, and the run checks all three.

    cd /opt/oi-core
    docker compose -f deploy/office/compose/docker-compose.yml --env-file rendered/env \
      --profile tools run --rm audit migrate
    docker compose -f deploy/office/compose/docker-compose.yml --env-file rendered/env \
      --profile tools build retention

    # A period, with the reason it exists.
    ... retention --set-retention documents --days 365 --legal-basis "tax law"
    ... retention --set-retention conversations --days 90 --legal-basis "client policy"

    # What it would do, then what it does.
    ... retention --enforce documents --dry-run
    ... retention --enforce documents
    ... retention --retention-runs

**The result, 2026-10-13, on a fixture aged 800 days.**

    NO POLICY MEANS NOTHING IS DESTROYED
      retain_days=0 examined=0 destroyed=0
    A HOLD BEATS A PERIOD
      examined=1 destroyed=0 held=1
    A DRY RUN CHANGES NOTHING
      examined=1 expired=1 destroyed=0
    THE RUN
      destroyed=1 failed=0

    DATA-RETAIN-001: gone from the vector index, the object store and the answer
      vector index:  0
      lexical index: 0
      manifest:      0
      object store:  GONE
      asking about it returned no passage from it

**The object store is the half a job built by reusing the purge would miss.** `DATA-PURGE-001` is a client asking
us to stop holding a copy, and it **deliberately leaves the file**: the client still has their document.
Retention is a period running out, and *"keep invoices for seven years"* means that after seven years there is no
invoice. **A run that cleared the indexes and left the file would report success while the thing the client asked
to be destroyed sat in their documents directory**, and nothing would surface it because the report would say the
run succeeded.

**Three behaviours proved beside the destruction**, each a way the mechanism could be wrong in the direction that
cannot be undone:

- **No policy destroys nothing.** A default period invented here would delete a client's documents on a number
  they never agreed to.
- **A hold beats a period.** The period says the client agreed to lose the document by now; the hold says a court
  has told them not to. Counted separately from a destruction, because a run that skipped forty documents under a
  hold and a run that destroyed forty are different outcomes.
- **A dry run changes nothing**, and still checks the hold, because a rehearsal whose output does not match the
  real thing rehearses a different operation.

**Age is measured from `ingested_at`, not from the file's mtime.** The file's date is a fact about the client's
filesystem: **copying a folder resets every mtime**, so a document whose date was reset would be retained for
another period without anybody deciding to. `ingested_at` is when the obligation to keep it started, which is the
date a retention policy is about. A document with no `ingested_at` is not expired, because an unknown age is not
the same as an old one.

**The stores are cleared before the file is removed**, and only one of those orders is recoverable. A failure
between them leaves a file with no index, which re-ingesting fixes. The other order leaves **an index pointing at
a file that is gone**: a passage the system will cite and nobody can open.

**Conversations are defined but minimal.** The table exists so the requirement's second half has something to
bind to, and the assistant API that writes it arrives in Phase 4. Inventing its schema now would be inventing
decisions about a component that does not exist.

**If it breaks.**

- Nothing is destroyed and `retain_days` is 0: no policy is set. That is the intended state and not a fault, and
  the command says so rather than exiting silently.
- Documents are held that nobody expected: `retention --holds`. A **deployment-scope** hold covers every document
  including ones added after it was placed, and it is the one people forget they placed.
- The file is gone and the index still holds it: the two operations ran in the wrong order, or the purge
  reported incomplete and the unlink was reached anyway. Check `retention --purges` for a document whose record
  says `complete: false`.
- Everything was destroyed on the first run: the period is too short, or the cutoff landed on the wrong column.
  Age comes from `ingested_at` and a null there is not expired, so a corpus whose manifest predates that column
  is safe rather than empty.

### 3.18 Catching a confidently wrong read

**The last requirement of Phase 3, and the failure it answers is the worst this product has.** OCR misreads a
character; the wrong number is faithful to the document **as parsed**, so retrieval finds it, the model cites it,
and the citation layer confirms the number is present in the cited passage **because OCR is what put it there.**
Every check passes and the client receives a confident wrong figure with a real citation.

**Why confidence is not the instrument.** Step 3.4 carved defects into a cell whose truth was 145.00, forty
trials: **fifteen wrong readings, and the parser flagged one of them.** Fourteen wrong numbers passed at
**confidence 61 to 93.**

**Three parts, all verified on the deployed store.**

    PART ONE: a damaged figure is caught
      CAUGHT 2026-13-45          malformed.date
      CAUGHT 02/31/2026          malformed.date
      CAUGHT 1450%               malformed.percent_range
      CAUGHT 12345678901234.00   malformed.implausible_magnitude
      CAUGHT 12,34,567.00        malformed.not_a_figure
      ok     145.00 1,250.00 (1,450.00) 145% 32.50 0.67   - none flagged

      100.00 + 45.00 against 145.00: True
      100.00 + 45.00 against 155.00: False
      no relationship to check: None

    PART THREE: is_ocr is set per chunk, true for the scanned publication

    PART TWO: an unverified figure from a scanned passage is disclosed as machine-read
      "machine-read from a scanned page and not confirmed by any other check in the document"

**The ceiling is stated where it is implemented.** A read that produces something a number cannot be is caught. A
read that **produces a different plausible number is not**, and step 3.4 measured fourteen of fifteen wrong
readings as exactly that. **A check described as catching OCR errors would be believed, and this one catches a
quarter of them.**

**`None` rather than `False` for a document with nothing to add up.** Most documents have no such relationship,
and returning `False` would mark every figure in every prose document as unconfirmed for a reason that is not
about the figure.

**The percentage line is drawn where a percentage stops being plausible as ANY percentage**, not where it stops
being plausible as a rate. **145 percent is a growth rate and a real figure**, so flagging it would be a check
that fires on correct documents — the failure mode that gets a check turned off.

**The disclosure travels inside the citation**, so it goes wherever the citation goes, and **saying nothing is
not a guarantee**: most figures carry no caveat because there was nothing to disclose, not because they were
checked.

**Three faults were found writing it, all of them the parser being wrong about numbers.** 31 February passed a
`1 <= day <= 31` range, so dates are checked against a real calendar. `(1,250.00)` did not parse at all because
**the parentheses are the sign** and were left in place, so a statement's negative figure read as not a number.
And a four-digit percentage fell through the three-digit *shape* to "not a figure" — true and useless, since the
digits are fine and the value is what is wrong.

**Open, found by the acceptance run.** The per-chunk `is_ocr` label lives in the vector payload and **not in
`chunk_index`**, so **a passage the lexical arm returns alone carries no scan label** and the disclosure cannot
fire on it. The lexical arm is a second path to the same content, and a disclosure silently not firing on one arm
is the shape of failure this step exists to prevent. It needs the label adding to that table and the worker
writing it.

## Phase C3 — Assistant API

The single surface the client app talks to: ask, tasks, approvals, activity, settings. This is the phase the
hand-off at `docs/handoff-c3.md` describes. Its two decisions are recorded in that document at §5.4 and §5.5.

**The step order matters, and this is why.** The route from a question to an answer already exists and is
proven, ending in a function call that takes a role as an argument. What does not exist is anything that can
say who is asking. Every later step in this phase — streaming, the timeout budget, the routing triggers — puts
a decision on top of that answer, so the session boundary is built first and the API is built around it.

### 4.1 The access model, the session, and the forged-role refusal

**Goal.** `SEC-ACL-002`: *a user outside a document's group cannot retrieve it by any query.*

The requirement's title says "per-user and per-group permissions". The verify line says something narrower and
harder: not that the mechanism exists, but that it holds against every query. So the step is two things at
once. **The permission model gets a third level**, so a document boundary can be narrower than the folder
tree. **And the role stops being an argument**, because today it is one:

    def search(services, question, role: str, limit=10, denial=None, rerank_passages=True) -> Result:

That signature is safe while the only caller is a probe on the same box. The moment an API sits in front of it,
whoever can reach the API can claim `executive` by typing it, and read everything. So the role has to arrive
from a session, and a request that tries to set its own has to be **refused and recorded**, which is the
phase's own acceptance sentence.

**The vocabulary, three levels, and each one earns its place.**

| Level | What it is | Why it exists |
|---|---|---|
| **root** | An access boundary, and the value stored in a chunk's `acl` label | It is the label `SEC-ACL-001` already filters on, so this level renames what is there rather than adding a mechanism |
| **group** | A named union of roots | It is how a person's access actually changes over time: a job changes, a group changes, and no code moves |
| **user** | A subject, with the groups it belongs to | It is the thing the identity provider will authenticate, and in C3 it is the thing a session is minted for |

**The shape, in the manifest's `access` block:**

    access:
      roots: [company, executive, finance]
      groups:
        staff:      {roots: [company]}
        finance:    {roots: [company, finance]}
        executive:  {roots: [company, executive]}
      users:
        - subject: dana@client.example
          name: Dana Reyes
          groups: [staff]
        - subject: anthony@client.example
          groups: [executive]

**Access is a union and there are no deny rules.** A user in `staff` and `finance` sees `company` and
`finance`, with no precedence table and no exceptions. That is a decision rather than an omission: a
permission model with no precedence is one that can be tested to exhaustion, and the union is a single line of
code that a reader can check by eye. Per-document grants are **not** built here. When a client needs one, the
extension that keeps the filter inside the query is to write group membership onto the chunk at ingest, so the
payload carries real labels and the filter stays a label match.

**The two folder roles stay as the built-in default.** A deployment that declares nothing keeps `staff` and
`executive` reading `company` and `executive`, which is what every manifest on the box does today, so no
existing deployment changes behaviour when this lands. Declaring `roots`, `groups` or `users` replaces that
default wholesale rather than merging with it, and the validator refuses a manifest that declares some of the
three and not the rest.

**The session record is the authority, not the token.** The token carries a session id and nothing else, so
there is no claim inside it to tamper with. What it resolves to is a row:

    sessions(session_id, subject, role, roots jsonb, issued_at, expires_at, revoked_at)

`roots` is **snapshotted when the session starts**, not looked up per request. The reason is the audit record:
a later change to a group must not change what an event recorded last week meant.

**A refusal is evidence, and the evidence is written before the client is told no.** Three properties, each of
which has been wrong in a system somewhere:

- **The guard runs before anything touches the database.** A refusal that half-wrote a session would be worse
  than the request it refused. The same order as `audit.py record`, which refuses a hand-typed actor before it
  opens a connection.
- **The record is committed before the 403 goes out.** If the response is written first and the commit
  second, a crash in between leaves an access decision with no trace.
- **The refused role is recorded as a role, not as a string in a message.** The event is
  `event_type='access'`, `outcome='denied'`, and `actor_role` carries the role that was **claimed**,
  because that is the claim the row has to be findable by. The session's real role goes in the payload beside
  the name of the field it was supplied in:

      actor_role: executive          -- the role the request claimed
      payload: {supplied_field: "role", attempted_role: "executive",
                session_role: "staff", session_id: "..."}

**Why that shape and not a new event type.** `event_type` is a check constraint with six allowed values and
`access` is one of them, so a refused access decision is already representable and needs no migration. It is
also the same type a legitimate denial uses, which is why the structural difference matters: a refused
authority claim sets `actor_role` to something the session does not hold, and a legitimate denial sets it to
the role the session does hold. A reviewer can tell them apart in one query without reading payload prose.

**The development mint command follows the guard already in the tree.**

    ... session mint --subject dana@client.example --role staff --ttl 8h

It refuses unless `--dev` is passed, it says in its refusal what will replace it, and it mints a session for a
subject the manifest's `access.users` declares. A subject the manifest does not know is refused, because
otherwise the command is a way to invent an identity.

**The probe, and what makes it falsifiable.** Four rights, run through the same interface a request takes:

| Right | Act | What is asserted |
|---|---|---|
| `ACCESS` | mint a session for `staff`, ask the question the `finance` document answers | the answer carries no passage from `finance` |
| `REFUSED` | send the same request with `role: executive` in the body | refused, and the refusal is in the chain |
| `RECORDED` | read the audit row the refusal wrote | `outcome='denied'`, `actor_role='executive'`, payload names the field |
| `FORGED` | mint a session claiming a role the manifest does not grant that subject | refused at mint time, and refused again at the API |

**A right with no wrong path beside it is not a test.** Each of the four is paired with the failing case it
exists to catch, and the ledger prints both. The forged session is what makes `REFUSED` mean anything: with
only production auth there is no forgery to refuse, which is why this phase builds the stand-in, and why its
verification cannot wait for C7.

**The seams, named here so C7 inherits them rather than rediscovering them.** C7 changes three things:
the verifier module in `services/session/session.py`, the mint command, which it deletes, and the manifest
field that names the verifier. It keeps four: the session record and its schema, the role and roots
resolution, the retriever call, and the refusal path with its audit event.

**The refusal path is deliberately not on that list.** Production auth does not stop a client from trying to
assert its own role — an ALB that rejects a bad token never sees the body, and the API is what sees a valid
session asking for something it does not hold. That path is what this phase is for, and it stays.

**Run it.**

    cd /opt/oi-core
    alias oi='docker compose -f deploy/office/compose/docker-compose.yml --env-file rendered/env'

    # Render the registry, then bring the session tool up.
    python3 scripts/render_manifest.py --manifest manifests/dev-cloud.yaml
    oi --profile tools build session

    # A session for a subject the manifest declares, and one it does not.
    oi --profile tools run --rm session mint --dev --subject dana@client.example --role staff
    oi --profile tools run --rm session mint --dev --subject nobody@client.example --role staff

    # The four rights, and the wrong path beside each.
    bash scripts/probe.sh access_probe.py

    # The refusal, read back out of the chain rather than out of the probe's own report.
    oi --profile tools run --rm audit list --type access | head

**The result, 2026-10-14, on the live stack.**

    THE PERMISSION MODEL THIS DEPLOYMENT IS ENFORCING
      registry   /w/rendered/access.json
      declared   yes
      roles   1: viewer=[]
      groups  2: exec=['company', 'executive'], staff=['company']
      users   2: probe-exec@officeinference.com[exec], probe-staff@officeinference.com[staff]

    ok   ACCESS    as 'viewer' over roots ['company'], the executive question returned acls=['company']
    ok   SCOPED    10 chunk(s) are labelled executive; the inside session reached 4 document(s), 2 of them
                    beyond the other session's reach, and the outside answer overlapped those by 0
    skip FORGED    every declared role stays inside the groups that grant it, so no role exists for this
                    subject to claim beyond its grant
    ok   REFUSED   a request supplying role='executive' while holding 'viewer' was refused
    ok   RECORDED  the chain holds 1 refusal(s) for this session: 2026-10-14 01:41:53 access/denied
                    actor_role='executive' payload.session_role='viewer'

    PASSED: 4 right(s) held against the live stack

**The refused role is stored as the claimed role.** `actor_role='executive'` is what the request asked for,
and `payload.session_role='viewer'` is what it held. A reviewer searching the chain by role finds the attempt,
and a refusal is told apart from a legitimate denial structurally rather than by reading prose.

**FORGED is skipped, and that is the rule working rather than a gap.** A subject's role may not reach past
what its groups grant, so in a registry where every declared role stays inside its groups there is no role to
forge. The rule arrived during this step: without it a role was a way around the groups, and a deployment
that declared a role carrying `executive` would have handed that root to every subject who claimed it.

**Four defects this step found by running rather than reading**, all in the probe and one in the boundary:

- The declared root `finance` matched no document, so the crossing question crossed nothing and the right
  passed on an index that could not have failed. The roots are now the two the corpus has.
- The role could exceed the groups. Fixed in `access.py`, with five tests.
- `DATABASE_URL` was assembled by compose for a service and absent for a bare `docker run`, so the refusal
  writer exited without writing while every other part of the run looked healthy.
- `AUDIT_TOOL` was captured at import time, so a caller that set it afterwards recorded through a missing
  path.
**What proves it.** The probe prints one line per right and its wrong path beside it, and ends with a count.
`access_probe.py` exits non-zero on any failure, so the operator does not have to read the ledger to know.

**How it breaks.** The three ways this fails on a real box, because each one has a shape that looks like
something else:

- **The registry is not rendered.** The session tool reads `rendered/access.json` and refuses to start
  without it. The message names the render command, because the alternative is a traceback that reads like a
  code fault.
- **The session expired between minting and asking.** The refusal is the same shape as a forged one. The
  probe prints the session's `expires_at` on every line so an expired session is visible as an expired
  session rather than as a permission failure.
- **The audit row is missing while the refusal worked.** The response is written before the commit. The probe
  fails the `RECORDED` right in that case rather than passing on the 403 alone, because a refusal nobody can
  find afterwards is not a control.


### 4.2 The assistant API, and the credential it calls with

**Goal.** There is no API today. This step builds one: an authenticated session asks a question, the answer
comes back with its citations, and the model call is made with a credential the gateway issued rather than one
the API typed. All five of the phase's requirements land on top of this, so this step is deliberately the
plainest version of it: no streaming, no timeout budget, no routing beyond reading the manifest.

**Why the credential is part of this step and not a later one.** The API cannot answer anything without
calling a model, and how it authenticates that call decides what the authorization story looks like for the
rest of the phase. It was settled by a spike before this step was written; the finding and the evidence are in
`docs/design/c3-assistant-api-decisions.md` section 7a.

#### The shape

| Piece | What it does |
|---|---|
| the API service | **FastAPI on uvicorn**, one process, no published port, on `ai-net`. A service rather than a tool because it listens. |
| session resolution | the token resolves to a session record through `services/session`, which refuses a request that supplies its own role and records the refusal |
| the permit | the session's roots go to the retriever, which filters inside the query, as `4.1` built |
| the credential | the API signs a short-lived assertion per request; the gateway validates it through `custom_auth` and enforces the role's budget as a **team** |
| the answer | retrieval, then generation, then citation assembly, returned as one JSON document |

#### The credential path, which the spike decided

The API holds **one** secret: a signing key. It does not hold a model credential, and it cannot read one.

    rendered/assertion.key    mode 600, one line, minted by the renderer like every other secret

Per request the API signs `role` and a nonce with that key and sends two headers. The gateway's
`custom_auth` hook verifies the signature, resolves the identity, and returns a `UserAPIKeyAuth` whose
`team_id` is the role. Budget enforcement then happens against that team.

**Three findings from the spike that this step depends on, each proved by running it:**

1. **A team must exist before an assertion naming it works.** A `team_id` the database does not know returns a
   404 telling you to create it. So provisioning moves into the `keys` tool, which reads
   `budgets.roles` from the manifest and creates one team per role with that ceiling.
2. **A team budget refuses hard.** Over the ceiling the gateway returned `budget_exceeded` at HTTP 422,
   naming the spend and the maximum. This is **stronger than the key budgets we had**, which admission-check
   against recorded spend and can be passed by concurrent requests. The manual and the schema say so.
3. **The master key must be exempted or the deployment locks itself out.** With `custom_auth` enabled the
   master key goes through the same hook. A hook that only understands role assertions refuses it, and every
   management endpoint, including `/team/new`, becomes unreachable. The hook receives `api_key`, so it
   compares it against `LITELLM_MASTER_KEY` and returns `proxy_admin`. Without that line this step would
   brick budget administration.

#### The endpoints

| Method | Path | What it does |
|---|---|---|
| GET | `/health` | liveness, and what it has loaded |
| POST | `/ask` | a question in, a cited answer out |

Two, and that is the point of the step. The phase's later steps add streaming to the second one and a routing
decision behind it; there is no third endpoint until a capability needs one.

**A request that supplies its own role is refused with the refusal already in the chain**, which `4.1` built
and this step wires in. The response is a 403 whose wording says the permission was denied **or the resource
might not exist**, which is Google's AIP-211 position: a 404 instead of a 403 is worse for the operator and no
better for secrecy.

#### Run it

    cd /opt/oi-core
    alias oi='docker compose -f deploy/office/compose/docker-compose.yml --env-file rendered/env'

    # The registry and the signing key, then the teams the assertions name.
    python3 scripts/render_manifest.py --manifest manifests/dev-cloud.yaml
    oi --profile tools run --rm keys --provision-teams
    oi --profile tools build api
    oi up -d api

    # A session, then a question through the API.
    oi --profile tools run --rm session mint --dev --subject probe-staff@officeinference.com --role viewer
    bash scripts/probe.sh api_probe.py

**The result, 2026-10-14, on the live deployment.** Six rights held and one measured, from the probe as written:

    THE SERVICE
      http://api:8088  status=200  {"status":"ok","deployment_id":"oi-core-dev-cloud"}
      role held: 'viewer'

    ok   ANSWER   [200] The mileage rate for reimbursement is 0.67 per mile for travel in an employee's own
    ok   CITED    6 citation(s) from the retrieved set, model='analyst', unsupported=0
    ok   SCOPED   [200] roots=['company'] answer='The base salary of the Chief Executive Officer is not'
    ok   FORGED   [403] kind=refused_authority_claim and 2 denial row(s) in the chain
    ok   NO-SESS  [401] kind=no_session
    ok   BUDGET   the ceiling the hook reads is 10.0 with 0 spent, so the remainder it returns is 10.0
    ok   ADMIN    [200] [{"team_alias":"oi-core-dev-cloud:analyst","team_id":"analyst"...

    PASSED: 7 right(s) held against the running service

And the budget refusal, captured by hand because a probe inside the network cannot restart the gateway:

    $ set the analyst team's spend to 25 against its ceiling of 10, then restart the gateway
    $ ask the mileage question
    502 {"error": "the role's budget is exhausted at the gateway: Budget has been exceeded! Team=analyst
         Current cost: 25.0, Max budget: 10.0", "kind": "generation_failed"}

**Four defects this step found by running it, and three were controls reporting success while doing nothing.**
None was visible to reading the code, and that is the pattern worth keeping:

1. **Every audit write the API made was refused, and it is fixed properly rather than exempted.** The audit
   tool refuses a hand-typed actor, and its guard fires before the database is touched. The first probe run
   passed its FORGED right **on a denial row written by an earlier run rather than by the request under test**,
   which surfaced only because the probe counts rows instead of trusting a 403. The API now signs a statement
   naming the actor it resolved from the session, the tool verifies it and takes the actor from inside, and the
   development flag is gone from the deployment. Three smaller defects came out of that work: `verify()`
   demanded a role an audit payload has no reason to carry, `--actor` stayed required at the parser, and the
   API mounted the tool without the verifier beside it.
2. **The model allowlist is not enforced on the signed-statement path.** A `viewer` statement reached `coder`,
   with both common-check flags on. The API therefore chooses the model from the manifest and a caller cannot
   name one, which is where that enforcement lives now.
3. **The budget did not enforce at all.** Spend 99 against a ceiling of 10, and the gateway answered. The
   spike had looked like proof that team budgets work; what it enforced was a `max_budget` on the identity it
   returned, and the team row was decorative. The hook now reads the ceiling and the spend and returns the
   remainder, and the refusal above is the result.
4. **The gateway caches the team row**, so a spend written straight into the database is not what it enforces
   until a restart. Harmless in operation, because the gateway writes the spend itself, and a trap for
   anybody testing a ceiling by hand.

**`--provision-teams` is new in this step.** The gateway refuses an assertion naming a team that does not
exist, so teams are created from `budgets.roles` as part of a render rather than as a repair.
#### What proves it

The probe prints one line per right with the wrong path beside it, as `access_probe.py` does:

| # | Right | Act | What is asserted |
|---|---|---|---|
| 1 | `ANSWER` | ask a question the corpus answers, with a valid session | the answer carries a citation whose page and document exist in the retrieved set |
| 2 | `SCOPED` | ask the executive question as a staff session | no passage outside the session's roots, and the content is reachable by a session that holds them |
| 3 | `FORGED` | send the request with a `role` field | refused, and the refusal is in the chain |
| 4 | `NO-KEY` | call the API with no session token | refused before any model is called |
| 5 | `BUDGET` | set a role's team ceiling below its spend, then ask | refused by the gateway with `budget_exceeded`, and the API reports it as a refusal rather than an answer |
| 6 | `ADMIN` | call `/team/list` with the master key | answers, proving the exemption in finding 3 is in place |

**Right 6 exists because of how the spike failed first.** Without it, a later change to the hook could lock
administration out and nothing in the deployment would say so until somebody needed to change a budget.

#### How it breaks

- **Every request returns 401 with a message from the hook.** The signing key the API loaded is not the one the
  gateway is verifying. Both come from `rendered/`, so a half-finished render is the cause.
- **A request returns 404 mentioning a team.** The teams were never provisioned, or the manifest's role list
  changed and nobody re-ran `--provision-teams`.
- **Every request returns 422 `budget_exceeded` on a fresh deployment.** The team was created with the
  manifest's ceiling and the period has not rolled over. Check `budgets.period_days` before assuming a bug.
- **The API answers but the answer has no citations.** Generation ran and citation assembly did not. The
  citations are the product, so this is a failure and not a degradation.
- **`/team/list` returns 401 with the master key.** The exemption is missing or the environment does not
  carry `LITELLM_MASTER_KEY` into the hook. This is the lockout from finding 3 and it stops everything.
### 4.3 Streaming, and the redaction that has to come with it

**Goal.** `INF-STREAM-001`: the first token arrives before the last is generated. And, because streaming opens it,
the response path gets the redaction it does not have today.

**Why redaction is in this step rather than a later one.** Research into the streaming design found two things.
The first is that `post_call` guardrails on a streaming path are audit-only in every gateway that has them,
with live bypasses in the one this deployment uses: one where raw SSE bytes stream through while the guardrail
logs `success`, and one where a PII guardrail **fails open** on an analyzer error. The second is that this
deployment's model *responses* are not redacted at all, today, streaming or not. `redact_request` runs on the
way out to a frontier model and nothing redacts what comes back. So the exposure is not created by streaming;
streaming makes it arrive sooner.

**A correction to an argument that was made while deciding this.** Input redaction was said to cover the
response, on the grounds that a model cannot echo what it never saw. That is wrong: a model can emit a
valid-shaped SSN that appears in no document, because it learned the shape. Input redaction is necessary and
not sufficient, and output redaction needs its own justification.

#### The mechanism, and why this one

**A holdback buffer.** Redact the raw accumulated buffer on every chunk, emit only the prefix older than the
holdback, back off to a UTF-8 boundary, and flush at the end. The invariant that matters comes from a
published implementation's design record: **re-redact the raw buffer, never re-redact an earlier marker**,
because a marker like `sk-***` breaks the pattern's own character class and the continuation of a
boundary-split secret stops matching.

| Mechanism | Guarantees no raw pattern reaches the browser? |
|---|---|
| Redact the request before the model call | **yes**, and it is already built for the frontier lane |
| Holdback buffer on the response | **yes**, if the holdback is at least the longest pattern |
| Redact each delta as it arrives | no. A published measurement found 40 of 86 two-way splits leaking |
| Redact the assembled response after delivery | no. That is logging, not a control |
| Block on a stream after tokens have gone | impossible. Bytes written cannot be recalled |

**The holdback is sized from the pattern set, not chosen.** The longest pattern in `services/egress/redaction.py`
is a private-key header at 36 characters; an SSN is 11, a card with separators 19, an IBAN up to 34. **64
characters** covers the set with headroom, which is about 16 tokens and roughly 0.3 to 0.5 seconds of added
time to first token on the local model. Full buffering, which is what one large provider does, costs the entire
generation instead.

**The honest limit, said in the manual rather than discovered later.** This covers patterns with a shape. A
personal name or an address has no bounded form, so no finite holdback can prove it cannot appear. For those
the control is the request-side redaction, and streaming is best-effort for anything the model introduces.

#### The transport

**Server-Sent Events through `sse-starlette`**, not FastAPI's own SSE: the native support is months old while
`sse-starlette` already carries `ping`, `send_timeout` and slow-client handling. Uvicorn implements write flow
control, so a slow client really does pause the send rather than buffering without limit.

Four things go in from the start because each is invisible to a load test and obvious in production:

- **`Incremental: ?1`** (RFC 10036) and **`X-Accel-Buffering: no`**. A proxy that buffers turns a fast origin
  into a slow client. One published measurement: 0.41s at the origin, 3.4s at the browser, and no synthetic
  test caught it because synthetic traffic keeps the stream busy.
- **A heartbeat comment every 15 seconds, including while the model is processing the prompt.** This is not
  decoration. An idle connection is what intermediaries kill, and CPU prompt processing is exactly an idle
  connection. Without it a slow local first token surfaces as a connection failure rather than a slow answer.
- **A terminal error event**, because the status code is spent once the first byte goes out. A mid-stream
  failure cannot become a 502.
- **Token counts from our own deltas**, never the gateway's usage numbers: a provider sends usage in the final
  chunk, so a client that disconnects early loses it.

#### Run it

    cd /opt/oi-core
    alias oi='docker compose -f deploy/office/compose/docker-compose.yml --env-file rendered/env'
    oi build api && oi up -d api
    bash scripts/probe.sh stream_probe.py

**The result, 2026-10-14, on the live deployment.** Four rights held, and the honest one is `REDACTED`:

    ok   END-TO-END  9 frame(s), 6 citation(s), answer starts 'The mileage rate for reimbursement is 0.67'
    ok   TTFT        first token at 336ms of 1542ms total, 104 frame(s)
    ok   INCREMENTAL 104 frame(s) at 37 distinct arrival times, spread 1.54s, X-Accel-Buffering='no'
    ok   REDACTED    canary in any frame: no; the stream reported redactions {'ssn': 1}

    PASSED: 4 right(s) held against the streaming service

**The canary is a document, not an assertion.** `fixtures/corpus/canary-pii.md` carries two reserved social
security numbers that belong to nobody, and the probe asks a question that retrieves it. The model repeats the
number, and a client reading raw frames receives:

    data: {"text": "The"}
    data: {"text": " p"}
    data: {"text": "rima"}
    data: {"text": "ry contact reference for the Acme engagement is [REDACTED-SSN] [1]."}
    data: {"deltas": 7, "first_token_ms": 301, "redactions": {"ssn": 1}, "total_ms": 558}

**The value was in the model's output and not in anything the client received.** Frames are small enough that
a split is the normal case rather than the interesting one, which is why the redactor holds back unconditionally
and why the probe reads raw bytes rather than a reassembled answer.

**A spike on the gateway settled the question this step was designed around, and corrected part of the
research.** The legacy `post_call` hook does run on a stream and cannot affect the client: it fires after the
iterator ends. But `post_call` is a mode rather than a mechanism, and an `apply_guardrail` guardrail under
`incremental_diff` **can** change streamed bytes. Two findings from that spike are now tests: the default
`block_only` silently drops a rewrite, and withholding only on the rounds that rewrite leaks the raw prefix and
then fails with a 400, because emitted bytes cannot be retracted. Full evidence in the design note at 7e.
#### What proves it

| # | Right | Act | What is asserted |
|---|---|---|---|
| 1 | `TTFT` | stream a long answer, timestamp every frame | the first token is materially before the last, and a non-streaming implementation fails this |
| 2 | `INCREMENTAL` | read the frames as they arrive | the bytes arrive in more than one frame, with the first well before the last |
| 3 | `REDACTED` | ask a question whose answer contains a canary pattern | the pattern appears in **no frame the client received** |
| 4 | `BOUNDARY` | feed the transformer a pattern split across chunks | the split pattern is caught, which per-delta redaction does not |
| 5 | `END-TO-END` | ask a normal question | the answer still arrives, cited, with the same content as the non-streaming path |

**Right 3 is the one that matters and it is the one that can be faked.** The canary is a real value the corpus
contains, the probe reads the raw frames rather than a parsed answer, and the assertion is over every byte the
client received. A test that checked the assembled reply would pass on exactly the design being replaced.

#### How it breaks

- **The whole answer arrives in one frame.** A proxy is buffering. Check the response headers for
  `X-Accel-Buffering: no` and `Incremental: ?1`, then check what sits in front of the API.
- **The first token arrives, then a long silence, then everything.** The heartbeat is not running during prompt
  processing, so something upstream is holding the connection.
- **A pattern reaches the client.** Check the holdback first: a pattern longer than the holdback is a pattern
  that can straddle it. Then check that the transformer is re-redacting the raw buffer rather than an earlier
  marker.
- **The response is cut short with no error.** A mid-stream failure emitted a terminal error event that
  nothing logged. The event is there; look for it.
- **Time to first token is much worse than the non-streaming path.** The holdback is too large for the pattern
  set, or the heartbeat interval is shorter than the model's first-token time.
### 4.4 Bounded time, a retry that is safe, and an answer when the provider will not

**Goal.** `INF-TIMEOUT-001`: *with the provider throttling, requests return a clear degraded answer within the
timeout, and it is recorded.*

**The numbers come from a measurement, not from a preference.** Nine streamed requests on this deployment gave
the figures below, and the derivation is in `docs/design/c3-assistant-api-decisions.md` section 7f. **They
describe the frontier lane**, which is what this deployment answers from: the office build runs a local
llama.cpp server where prompt processing is compute-bound on CPU, and it will need its own measurement.

| Threshold | Value | Where it comes from |
|---|---|---|
| time to first token | 1.5s | measured p50 260ms, p95 359ms, so four times the observed worst |
| stall between chunks | 5s | nothing measured; a gap this long is a failure rather than a slow model |
| total | 30s | **provisional.** The longest measured answer was 2.9s, and the shape of the tail is unknown |
| delivery reserve | 1.2s | enough to build the degraded answer, stream it, and write the record |

**The deadline is measured from the client's arrival, not from when the model starts**, which is a defect this
step's measurement found rather than assumed: the first version reported time to first token from inside the
stream generator, understating what a person waits by the whole of retrieval.

**One boundary is not a number.** A stream that never starts and a stream that starts and then stalls are
different failures, with different fixes and different words in the record. The stall threshold exists so the
second one is not reported as the first.

#### The rules, each of which is a decision

**Retry only before the first token.** A streamed generation is not idempotent. A retry after output has begun
is billed twice, and the client already holds text from a response that was abandoned. If the failure arrives
after the first byte, the only honest move is a terminal error event.

**Classify on the error type, never on the status.** On Bedrock a 429 covers both `ThrottlingException` (our
quota, worth waiting for) and `ModelNotReadyException` (not ours to wait out). Anthropic returns a spend-cap 429
with no `Retry-After` and documentation saying retrying fails until access resumes. Retrying on status alone
spends the entire deadline on a request that cannot succeed.

**Every wait comes out of one budget.** Three attempts with per-attempt timeouts is three times the wait. The
deadline is wall-clock from arrival, and a retry only happens if what remains can hold it.

**No circuit breaker, and the reason is concrete.** With one provider there is nothing to break away from, and
an open breaker turns a five-second throttle into a sixty-second outage.

**No cross-lane fallback.** Degradation may only move data toward a less exposed lane. A reliability fallback
that sends a question from the local lane to the frontier lane is a data-class decision wearing a reliability
costume, and a document that was never permitted to leave the building just left. A fallback that would
increase exposure is refused and recorded as a refusal.

#### The degraded answer

**The retrieved passages, cited, with a typed reason.** Retrieval and generation are already separate halves, so
this is honest and still delivers the client's own data. A "try again" message throws away work already done,
and a cheaper model's answer changes quality silently under the same branding.

    {
      "kind": "degraded",
      "reason": "throttled",
      "detail": "the provider is throttling and the answer could not be generated within 30s",
      "passages": [{"index": 1, "doc_id": "company/travel-policy.md", "page_start": 1,
                     "excerpt": "The mileage rate is 0.67 per mile ..."}],
      "attempts": 3,
      "threshold": "total"
    }

**A deployment whose data class forbids the frontier lane meets this path routinely rather than in an
emergency.** That is a client's declared choice, so the degraded answer has to read like a feature: it says
what it is, gives the passages that answer the question, and does not pretend the answer is complete.

#### The record, which is the half that makes it a control

| Field | Why an examiner needs it |
|---|---|
| `lane` and `data_class` | makes the residency claim provable from the chain alone |
| `requested_model`, `resolved_model` | a single model column conflates what was asked for with what ran |
| `attempts` and each error type | a throttle and a hard failure are different events |
| `threshold` that tripped | first token, stall or total: three different faults |
| `deadline_remaining_ms` at surrender | how close the call came to finishing |
| `partial_output` | whether the client already held text, which is the retry-safety fact |
| `taxonomy_version` | the chain is append-only, so a classification needs its rule set |

**Never recorded:** prompt or passage text, credentials, raw provider error strings, or anything derived from a
role supplied in a request. Counts and identifiers only.

#### Provoking a real throttle, which is not something you can wait for

**There is no supported way to force a provider error through the gateway.** The three `mock_testing_*`
flags were stripped from proxy requests in LiteLLM 1.85.0, and the one documented mock facility mocks success.

**The admission-arithmetic method does not work, and this was measured after being recommended.** The idea was
that AWS deducts `input + max_tokens` from the per-model quota at admission, so an oversized `max_tokens` would
produce a genuine throttle. **Bedrock validates `max_tokens` against the model's own limit first and returns a
400:**

    max_tokens=4200000 -> 400 BadRequestError: The maximum tokens you requested exceeds the model limit of
                              10000. Try again with a maximum tokens value that is lower than 10000.

So the oversized value never reaches the quota check, and Nova Lite's limit is 10,000 rather than the 4,000,000
tokens per minute its quota allows. The finding is worth keeping because it was the cheapest-looking method and
looked correct on paper.

**What a real throttle needs: load, against a quota small enough to reach.** The quotas were read from Service
Quotas and they vary by two orders of magnitude. Nova Lite's on-demand quota is 4,000,000 tokens per minute,
which would take about four hundred maximum-size requests. But the **latency-optimized** on-demand quota for
Nova Pro V1 is **40,000 tokens per minute**, and a single request may ask for up to 10,000, so roughly four
concurrent requests exhaust it. That is the route to a genuine throttle: the same model, a different service
tier, and a ceiling four requests away rather than four hundred.

**Attempted on 2026-10-14 and not reached, with the arithmetic to show why.** Concurrency is the only honest
route and the ceiling is too high for this box:

    concurrency  8, max_tokens  9000  -> 8 of 8 answered 200
    concurrency 12, max_tokens  9000  -> 12 of 12 answered 200 in 2.8s
    concurrency 40, max_tokens 19000  -> 40 of 40 rejected 400, the model ceiling is 10000

Forty maximum-size requests is roughly 360,000 tokens against Nova Lite's 4,000,000 per minute. **Reaching the
quota needs about four hundred of them in one minute**, which a development box cannot produce and which would
burn credit for evidence rather than for a product. The latency-optimized Nova Pro tier at 40,000 would take
about four, and selecting that tier is the unverified step: LiteLLM passing the wrong field there would silently
use the standard quota and look like a bug in our budget.

**So a real provider throttle is not provoked, and this step does not claim it is.** What is verified for
`INF-TIMEOUT-001` is the whole path *except* the provider's own 429: the deadline, the three thresholds, the
classification of a terminal refusal, the retry rules, the degraded answer with its passages, and the audit row.
The one thing missing is a real `ThrottlingException` body arriving from Bedrock, and the classification of that
body was unit-tested against the shape AWS documents.

**A real defect found while trying.** The team row has carried `tpm_limit` and `rpm_limit` columns all along and
the auth hook never read them, so **a rate limit declared on a team was silently unenforced**. The hook passes
them now. Whether the gateway enforces the identity's limits when the identity arrives from a custom auth hook
is still unproven: setting a team's `tpm_limit` to 100 did not produce a 429, which means either the gateway
ignores it on this path or the limit is not consumed as expected. A limit that is not passed to the component
that enforces it is a comment, and this is the second time in this phase that a declaration turned out to have
no enforcer.


    # The real throttle, once per release, with the provider's own request id in the record.
    OI_MAX_TOKENS=100000 oi restart api       # far above the per-model TPM quota
    (ask a question)
    oi restart api                            # back to the normal ceiling

**For every commit, a stub rather than a product.** One small service that speaks the provider's error shapes,
reached through `AWS_ENDPOINT_URL_BEDROCK_RUNTIME`, which AWS documents as highest precedence in endpoint
resolution. It covers 429 with and without `Retry-After`, 503, 529, a malformed chunk and a mid-stream error.
**It is a test and not evidence of a real throttle**, and the runbook will say which is which.

**The local lane has no throttle and this step will not pretend otherwise.** llama.cpp does not meter anyone.
When its slots are full it defers. Its only error is a 503 while the model loads. So local saturation is a
deadline problem and local 503 is the hard-failure case, and manufacturing a 429 from it would put a fiction in
this document.

#### Run it

    cd /opt/oi-core
    alias oi='docker compose -f deploy/office/compose/docker-compose.yml --env-file rendered/env'
    oi build api && oi up -d api
    bash scripts/probe.sh degrade_probe.py

**The result, 2026-10-14, on the live deployment.** A terminal provider failure was forced by putting the
role's spend over its ceiling, and the answer that came back was the degraded one:

    status 200
    kind: degraded | reason: budget | attempts: 1 | threshold: None
    passages: 6
    detail: this role's budget ceiling has been reached, so no model call was made

**And the chain recorded it**, from the row rather than from the response:

     event_type | outcome | reason | threshold | attempts | partial
    ------------+---------+--------+-----------+----------+---------
     model_call | denied  | budget |           | 1        | false

**Three things about that are worth naming.** The answer is a 200 with cited passages, not an error: retrieval
had already done the expensive part and the client is entitled to their own material. `attempts` is 1, because a
budget refusal is terminal and waiting would spend the budget the degraded answer needed. And `threshold` is
empty rather than a word, because no deadline tripped: a budget refusal is not a timeout, and blurring the two
would waste the three thresholds this step exists to distinguish.

**A defect the first run of this test found.** The threshold field was carrying the catch-all word `surrender`
for any failure, which reads as though a deadline had tripped. It carries None now, and the reason field says
what actually happened. Found by reading the row rather than the response, which is the habit this project keeps
having to relearn.

**The real throttle has not been provoked yet.** It is the next thing this step owes: a request whose input plus
`max_tokens` exceeds the per-model tokens-per-minute quota produces a genuine `ThrottlingException` from
Bedrock, which is the requirement's own words rather than a stand-in. The budget path proved above exercises
the same code, and it is not the same evidence.
#### What proves it

| # | Right | Act | What is asserted |
|---|---|---|---|
| 1 | `THROTTLE` | provoke a real throttle by admission arithmetic | a `kind: degraded` answer with `reason: throttled`, inside the timeout, not a hang |
| 2 | `RECORDED` | read the row the degraded answer wrote | attempts, error type, threshold and `partial_output` are all present |
| 3 | `NO-RETRY-AFTER-TOKEN` | fail the provider after the first frame | a terminal error event, and **no second upstream call** |
| 4 | `STALL` | a stub that sends two tokens then stops | the stall threshold fires rather than the total cap |
| 5 | `NO-CROSS-LANE` | ask with a data class that forbids the frontier lane | refused and recorded as a refusal, not silently upgraded |
| 6 | `INFRA` | a stub speaking 503 | the hard-failure path, distinguished from the throttle in the record |

**Right 3 is the one that cannot be reasoned into existence.** It needs a provider that fails after the first
token, which the stub exists for, and its assertion is about an absence: the upstream is called once.

#### How it breaks

- **A request hangs past the total cap.** The deadline is enforced around the upstream call rather than inside
  the generator. A generator that never yields cannot be interrupted from outside.
- **A degraded answer says nothing useful.** It is being built without the retrieved passages, which is the
  whole reason it is honest.
- **Every request degrades after a quota change.** The tokens-per-minute ceiling was hit by ordinary traffic,
  not by the probe: check the per-model quota and `OI_MAX_TOKENS` before assuming a bug in the budget.
- **The record says `throttled` for a hard failure.** The classification fell back to the status code. The
  taxonomy is keyed on the error type for exactly this reason.
- **A retry happens after the first token.** The partial output has already been emitted, so the client holds
  text from an abandoned generation and the second call is billed. This is the failure the ordering exists for.
### 4.5 Routing, and the one-way boundary it keeps

**Goal.** `INF-ROUTE-001` and `INF-ROLE-001`: routing escalates on the three mechanical triggers only, and the
model a role uses is read from the manifest rather than assumed.

#### The order is the design

**The data-class gate comes first, and the triggers choose a model only inside the lane it permitted.** That
reordering is the whole of this step. A trigger is a cost decision; the gate is a data-boundary decision, and
the boundary has to be settled before anything asks how hard the question is.

The reason it matters is the answer to a question an examiner will ask: *why did this content reach an external
model?* With the gate first, the answer is one sentence about a declared, reviewable permission. With the
triggers first, the answer is a story about a heuristic firing, which is not a control anybody can audit.

**The boundary is one-way.** No trigger may move content toward a more exposed lane. A user asking for the
stronger model cannot reach the frontier lane from a deployment whose data class forbids it, and the same
applies to the task list and to synthesis. Escalation is only ever *within* what the gate allowed.

#### The three triggers, each a predicate over facts already held

| # | Trigger | How it is decided | What it is not |
|---|---|---|---|
| 1 | the task is on the agentic pack's list | a lookup in a configured list | not a judgement about the question |
| 2 | synthesis across more than one document | an integer: distinct `doc_id` above the floor, with a relevance threshold under it | not an assessment of difficulty |
| 3 | the user asks for the stronger model | a request field | not a permission to leave the permitted lane |

**Trigger 2 is a proxy and the record says so.** Distinct `doc_id` count has false positives, when a question
incidentally matches two documents that need no reconciling, and false negatives, when one long contract needs
synthesis internally. It stays because determinism is worth more here than accuracy, and the audit row labels
it a proxy rather than a measured property.

#### What the user is told, always

**A user is never left guessing what happened.** Decided 2026-10-14, and the reasoning is in the design note at
7h:

| Situation | What the answer says |
|---|---|
| escalated, and the gate permitted it | which model answered, and which trigger moved it |
| asked for the stronger model, and the gate forbade it | that it was asked for, that this content may not reach that lane, and which model answered instead |
| not escalated | nothing, because there is nothing the user needs to know |

**Refusing was the alternative and it is worse.** It is equally honest and less useful: the user asked a
reasonable question and would get nothing, and they could not tell that apart from a permission problem.
Announcing does the same work while still delivering an answer.

#### The record carries two facts, not one

**`escalation_requested` and `lane_answered` are separate fields.** A single field conflates what was asked for
with what ran, which is the distinction the audit row already draws between requested and resolved model. An
examiner reading the row can see that a user asked for the frontier lane and that the gate refused it, which is
the fact a residency claim turns on.

#### `INF-ROLE-001` is proved here, and only for the mechanism

Change `models.analyst` in the manifest, validate, render, restart, and ask the API which model it used. **The
answer must change.** That tests which model was called, not whether the answer improved.

`tests/parity/` holds a README and no questions, and the brief says so. Golden questions are C8's, at sixty per
deployment. So this step tests the mechanism and **not** answer quality:

- assert the model that was called changed, from the gateway's own view
- ask the same question before and after the swap and record both answers side by side in `docs/runbook.md`, so the difference is visible rather than asserted
- state in the manual that answering *is the new model as good* is C8's, and that a swap before then is unmeasured

**Do not close `INF-ROLE-001` by claiming the swap is safe for answers.** It is safe for the mechanism, which is
what its criteria ask.

#### Run it

    cd /opt/oi-core
    alias oi='docker compose -f deploy/office/compose/docker-compose.yml --env-file rendered/env'
    oi build api && oi up -d api
    bash scripts/probe.sh route_probe.py

**The result, 2026-10-14, on the live deployment.** All four cases behave, and the streaming path that had no
retry now has one:

    plain     (200, 'frontier', False, 'answer')
    agentic   (200, 'frontier', True,  'answer')
    stronger  (200, 'frontier', True,  'answer')
    streaming (8 frames, ttft 318ms, total 584ms)

    the routing decision table is unchanged: 20 decisions
    587 passed
    6/6 manifests valid; 8 packs known
    PASSED: 7 right(s) held against the running service
    PASSED: 4 right(s) held against the streaming service

**A plain question escalates nothing**, which is the requirement's own words and the thing the first attempt
got wrong. The record carries both facts, so a refused escalation is never confused with a permitted one.

#### Four calibration defects the research found in this code, fixed here

**The gateway was retrying behind our back.** LiteLLM's `DEFAULT_MAX_RETRIES = 2` applies when `num_retries`
is unset, and it was unset. So our three counted attempts could become **nine** upstream calls, none of them
visible to the deadline and none counted in the record. The rendered config sets `num_retries: 0` now, with the
reasoning in the file: a retry that ignores the caller's deadline is an unaccounted one, and AWS states that
retries "can make the overload worse instead of better".

**The lane did not decide the thresholds.** The office build's CPU lane was inheriting a first-token budget of
1.5s measured against Bedrock, which would mark healthy local answers as failures. `Budget.for_lane` gives the
local lane 20s/30s/180s, and the numbers are **placeholders until the office build is measured**, which the code
says rather than implying they are derived.

**The degraded payload said `frontier` while a local model served.** It takes the lane now, and the budget that
decides the thresholds is the same one that builds the payload, so they cannot disagree.

**The streaming path forfeited a safe retry.** The identical 429 retried three times on the non-streaming path
and degraded immediately on the streaming path, which is the one real clients use. It retries now, which is safe
because no answer byte has been emitted; the start frame carries citations rather than text. After the first
delta the rule is unchanged, and the field agrees: LiteLLM is the only system that attempts mid-stream recovery
and it does so by replaying the prefix as an assistant prefill, which re-bills it.

**Two runtime defects of mine, found by exercising the paths rather than by the unit suite.** A `passages`
argument the signature no longer took, and a `decision` referenced outside its scope. Both would have been
instant failures for a client and neither was visible to 587 passing tests, because the tests do not run the
handlers.
**The role swap, 2026-10-14, on the live deployment.** `INF-ROLE-001` asks that the model a role uses comes
from the manifest. The proof changes the manifest and watches the called model change, and the evidence is the
gateway's own record rather than our report of it.

    BEFORE  role='analyst' resolved_model='bedrock/amazon.nova-lite-v1:0'
    SWAP    manifest: analyst frontier -> bedrock/amazon.nova-micro-v1:0
    AFTER   role='analyst' resolved_model='bedrock/amazon.nova-micro-v1:0'

**And from LiteLLM's spend log**, which is the gateway saying what it asked Bedrock for rather than the API
saying what it believes:

    bedrock/amazon.nova-micro-v1:0
    bedrock/amazon.nova-micro-v1:0

The two answers, side by side, because the difference should be visible rather than asserted:

    nova-lite:  "The mileage rate for reimbursement is 0.67 per mile for travel in an employee's own vehicle [1][2][3][4]."
    nova-micro: "The mileage rate for reimbursement is **0.67 per mile** for travel in an employee's own vehicle [1][2][3][4]. Travel in a client-owned vehicle is bill..."

**What this proves and what it does not.** It proves the mechanism: which model runs is read from the manifest,
and changing the manifest changes it. It does **not** say the new model is as good. Golden questions are `C8`'s,
at sixty per deployment, and `tests/parity/` holds a README and no questions. A swap before then is unmeasured
for quality, and the manual says so rather than implying otherwise.

**Two things the swap exposed.**

**The answer reported the role, not the model.** It returned `model: 'analyst'` before and after, so a client
could not tell that anything had changed and the swap this entry asks for would have been invisible. The answer
carries `resolved_model` now, beside the role, because a role is a name in a manifest and a model is what ran.

**A failover is reported the same way.** The gateway sets the routing decision on the response
(`x-litellm-model-group`, `x-litellm-model-name`, `x-litellm-attempted-fallbacks`), and the API
reads them. The answer adds `configured_model`, `model_group` and `fallback_used`; the
`model_call` row's `model` column is the model that answered and its payload keeps the role as
`model_role`. Before this, an answer served by the fallback credited the primary, which is the one fact an
auditor would have been misled by.

**Both processes must be restarted, and the failure is silent.** `render_manifest.py` writes the config and
neither process re-reads it: restart only the gateway and the model genuinely changes at Bedrock while the API
goes on reporting the old one from the manifest it loaded at startup. That is the worst shape of failure, because
the record and the report disagree and the quieter one is wrong. The sequence is render, restart the gateway,
**restart the API**, and verify with the spend log rather than with the answer.

**The swap was reverted**, and the manifest is back to `nova-lite` in the commit rather than left changed.
#### What proves it

| # | Right | Act | What is asserted |
|---|---|---|---|
| 1 | `NO-JUDGEMENT` | ask a question the cheap model answers badly | it does **not** escalate, and no trigger is named |
| 2 | `TRIGGERS` | one request per trigger | each escalates, each naming its own trigger in the record |
| 3 | `ONE-WAY` | ask for the stronger model with a data class that forbids the lane | the lane does not change, the answer says so, and the attempt is recorded |
| 4 | `ROLE` | change `models.analyst`, render, restart | the model the gateway reports having called changes |

**Right 1 is the one the requirement's own words are aimed at**: *a question the cheap tier answers poorly does
not escalate on its own judgement.* It is asserted as an absence, which is why the record's `trigger` field
being empty is the evidence rather than the answer being bad.

#### How it breaks

- **Everything escalates.** The agentic list is matching too broadly, or the `doc_id` floor is at zero so an
  incidental lexical match counts as synthesis.
- **Nothing ever escalates.** The gate is refusing the frontier lane for a data class the manifest does permit.
  Check `access.egress.frontier_classes` before the triggers.
- **A user's escalation request silently does nothing.** The gate refused it and the answer did not say so. That
  is the case the announcement exists for.
- **The model did not change after a manifest edit.** The renderer wrote the old map, or the API was not
  restarted. `OI_MODEL_<ROLE>` is read at startup.
- **The answer quality changed and nobody measured it.** Expected: golden questions are C8's. The manual says so
  rather than implying the swap was validated.
## Phase C4 — Client app

**Probes run through `bash scripts/probe.sh <probe.py>`, from the deployment root.** The long `docker run`
form it replaces was wrong for one probe in a way that mattered: the session image has no `/app/audit.py`,
because the compose service mounts it, so `access_probe` reported that a refusal could not be recorded and the
deployment looked like it had no working audit chain. The runner takes the same service the deployment uses,
so the mounts and variables come from one place. It is also shorter than the line it replaces, which is the
only kind of fix that survives being copied.


The interface a business actually uses. Ask a question, and get an answer that shows where it came from.
Start work, and watch it happen. Read back what happened afterwards.

**The phase talks to the API and nothing else.** No model key, no database credential, and no knowledge of
which model answered. The app lives in its own repository, `oic-ui`, and the thing connecting
the two is `docs/openapi.json`, generated from this service by `scripts/fetch_openapi.py`. That file is the
contract, and `--check` fails when the running service and the committed file disagree.

### 5.0 The contract, before anything is built on it

**Goal.** A second repository can be written against this API without reading its source.

**Why this is the first step and not paperwork.** The OpenAPI document at `/openapi.json` listed three URLs
and **no response bodies at all**. Every response was an empty schema. A client built against that would have
been built against the manual's prose, and would have broken the first time a field moved.

**What was added.** `services/api/models.py` declares every payload: the answer with its citations and the
counts beside them, the degraded answer, the refusal, the five stream frames, and health. They are attached
with `responses={...}` and deliberately **not** `response_model`, so the wire format is still decided by the
code that has been tested against a live deployment, and this change is documentation rather than behaviour.

**Run it:**

```
cd /opt/oi-core
docker run --rm --network ai-net --user "$(id -u):$(id -g)" \
  -v /opt/oi-core:/w -w /w --entrypoint python oi-core/session:local \
  /w/scripts/fetch_openapi.py --check
```

Expected, and this is the output that proves it:

```
OK    /w/docs/openapi.json matches http://api:8088  (9 path(s): /activity, /ask, /ask/stream, /health, /jobs, /jobs/{job_id}, /jobs/{job_id}/events, /jobs/{job_id}/{action}, /tasks)
```

**The check refuses to publish a bad contract**, and each refusal is a test. It names a missing path, a path
nobody declared, a `kind` field absent from either response shape, a status code the app has to handle, a
refusal with no wording, and a streaming event missing the fields the JSON path carries.

**What it does not check, said plainly.** That the fields hold the right *values*. It is a shape check. The
values are what the probes and the panel are for.

### 5.1 A job, so work can be watched rather than waited for

**Goal.** A piece of work is a thing with an id that can be watched, stopped, and found afterwards.

**Why a job is not a longer answer.** The requirement behind this step is a sentence a client said out loud:
*work on this for thirty minutes, let it fail four times, and on the fifth failure change the approach.* That
needs a durable record, one row per attempt with its typed failure, an escalation rule, progress that can be
watched while it runs, and somewhere to stop it. `POST /ask` has none of that. It is one round trip that ends
when the answer does.

**The shape, and every field is one the interface needs.**

| Method | Path | What it does |
|---|---|---|
| POST | `/jobs` | `{"session": "<token>", "task": "<task id>", "input": "..."}` in, a job id and its state out |
| GET | `/jobs/{id}` | the job: state, attempts, steps, budget spent |
| GET | `/jobs/{id}/events` | its progress as Server-Sent Events. **Replayable**, with `?since=<seq>` |
| POST | `/jobs/{id}/pause` | stop after the current step |
| POST | `/jobs/{id}/resume` | continue |
| POST | `/jobs/{id}/cancel` | stop, and keep what happened |

**A job is a resource and the events are a log, which is the decision that matters.** Progress is not a
stream a client must be present for. It is a numbered list that a client can read from the beginning, or
resume from where it stopped, or read tomorrow. **A client that closes the tab and comes back has to be able
to catch up**, and a design where the only copy of "what happened" was pushed down a socket cannot do that.

**Replay is why `seq` exists.** Every event carries one, monotonically increasing per job. `?since=7` returns
everything after seven. That makes the stream resumable, and it makes the log readable without a stream at
all.

**The state machine, and it is deliberately small:**

```
queued ──▶ running ──▶ succeeded
             │  ▲
             │  └── paused
             ├────▶ failed
             ├────▶ awaiting_approval   (C5 and C6 put work here; C4 defines the state)
             └────▶ cancelled
```

**C4 ships a one-step executor, and that is honest rather than a stub.** The job is created, one step runs,
and it finishes. A job with one step that completes **is a job**: the record is real, the events are real,
the screen is real, and the audit rows are real. C6 adds tools, a real loop and approval gates underneath
without the interface moving. **What C4 does not do is pretend.** The Do screen shows the task menu the
deployment declares, and a deployment with no agentic pack has no tasks to show.

**Every job route carries the same guards as `/ask`, and this is the part C3 paid for.** Session
resolution, the refusal of a caller-supplied role, the data boundary, the redaction, and the audit row all
apply to a job because a job is another way to ask a model something. C3 shipped two defects of exactly this
shape: a redaction on the streaming path and not the JSON one, and a data boundary on the JSON path and not
the streaming one. Each passed every probe, because each probe tested the endpoint its author had in mind.
**So every control is asserted on both job paths, and the probes exist in pairs.**

**Run it:**

```
cd /opt/oi-core
bash scripts/probe.sh job_probe.py
```

Expected:

```
ok   CREATED     job 8f3c… state='succeeded' attempts=1 steps=1
ok   EVENTS      4 event(s), seq 1..4, replay from seq 2 returns 3
ok   RECORDED    the job and every step carry an audit row
ok   GUARDED     a role-supplied request is refused on /jobs as it is on /ask
ok   BOUNDARY    a forbidden data class does not run as a job either
ok   DENIED      a job for a task this deployment does not declare is refused, by name
PASSED: 7 right(s) held against the documented job interface, 1 skipped
```

**If it breaks.**

- **`no such task`**: the deployment does not declare the task. The task list is the manifest's, and a
  deployment with no agentic pack declares none. That refusal is the control, not a fault.
- **A job that never leaves `queued`**: nothing is executing it. Check that the executor started with the
  service, and read the row rather than the screen.
- **Events with a gap in `seq`**: two writers. Every event is numbered inside the same transaction that
  writes it, so a gap means something else is writing to the log.
- **`?since=` returns nothing after a restart**: the events are in the database rather than in memory. If
  they are gone, they were kept in the wrong place.

### 5.1b The activity read API, so the record can be read without a shell

**Goal.** A client can read the audit chain over the API, and cannot read a row outside its own permission.

**Why this is an endpoint rather than "use the audit CLI".** The chain is the product's central claim, and
until now the only way to read it was `audit list` on the box. That is an operator tool behind an SSH
session, not an interface, and the client app lives in its own repository and reaches this service over HTTP
and nothing else. **Activity is the screen that makes the product auditable to a client**, and it had no
endpoint at all.

**The scoping rule, which is the part worth arguing about.** The audit table has no per-row permission
column, and inventing one would be a second permission model maintained beside the real one. So the rule
reads from the model that already exists:

| Who | What they see |
|---|---|
| any session | its own rows |
| a session whose roots include another subject's roots | that subject's rows too |

**The direction matters and it is the safe one.** A session with `company` sees itself and every other
`company`-scoped subject. A session with `company` and `executive` sees everyone. **Nobody sees a row whose
actor holds a root they do not hold**, so the executive rows stay invisible to a company-scoped role, which
is the same boundary `SEC-ACL-001` enforces on retrieval. The stored `access_roots` on each session is a
snapshot, so the comparison is against the permission in force when the row was written rather than the
permission today.

**A subject with no session record is not visible to a narrower role.** The rule is a subset test, not a
guess, and an unknown actor falls to the narrower side.

**What a row carries.** The record's own columns, and the payload, because an activity screen that cannot
show the request, the model, the documents read and whether content left is a table of timestamps. **The
payload is already the content-mode-governed form**: `content.classes` decides whether a prompt is stored as
a hash, redacted, or in full, and this endpoint reads what was stored rather than deciding for itself. It
does not widen what a row contains.

### 5.1b.i The endpoint, and what each parameter filters

| Method | Path | What it does |
|---|---|---|
| GET | `/activity` | the chain, newest first, filtered and scoped to the session |

| Parameter | What it filters |
|---|---|
| `limit` | how many rows, default 50, capped at 500 |
| `since` | rows after this id, for paging and for catching up |
| `type` | `model_call`, `tool_call`, `approval`, `access`, `config_change`, `login` |
| `actor` | one subject, if the session may see it |
| `outcome` | `ok`, `denied`, `error`, `pending` |
| `job` | the `job_id` a row names, which is how a job's whole history is read back |
| `request` | the `request_id` an answer carries, which is how the screen ties an answer to its row |

**A `404` is not used for a row outside the scope.** The filter simply does not match it, so a caller cannot
learn that a row exists by asking for it and reading the difference between "no" and "not there". That is the
same position the 403 wording already takes.

### 5.1b.ii Run it, and read the output

```
cd /opt/oi-core
bash scripts/probe.sh activity_probe.py
```

Expected:

```
ok   READ       12 row(s) returned, newest first, ids descending
ok   SCOPED     a company-scoped session cannot see an executive subject's row
ok   FILTERED   type=model_call returns only model calls; outcome=denied only denials
ok   BYREQUEST  the row for a request_id is returned by asking for that request_id
ok   BYJOB      a job's rows are all returned by asking for that job
ok   CAPPED     limit=500 is the ceiling and a larger number is refused rather than honoured
PASSED: 8 right(s) held against the documented activity interface
```

**If it breaks.**

- **An empty list on a deployment that has rows**: the session's deployment id does not match, or the actor
  comparison is failing because the session's roots snapshot is empty.
- **Rows from another deployment**: the query must filter on `deployment_id`. Two deployments can share a
  database, and a chain that mixes them is not evidence about either.
- **A row with no payload**: the payload column is nullable and older rows may not have one. That is not an
  error, and a screen should render the columns it has.
- **`limit` above the cap silently honoured**: the cap exists so one request cannot pull the whole chain.
  Refusing rather than trimming is deliberate: a caller that asked for 5,000 and got 500 has been lied to
  about what it read.

### 5.1c The outputs, as they were on 2026-10-14

**Every claim above is copied from a run, not composed.** The deployment is `oi-core-dev-cloud` on the `m7i.4xlarge`
reference instance, and the API was rebuilt from the commit that carries these steps.

**The job interface**, `job_probe.py`, eight rights:

```
ok   TASKS      4 task(s) declared, agentic=True, all labelled
ok   REFUSED    [403] kind=no_such_task
ok   GUARDED    [403] kind=refused_authority_claim, 1 audit row(s) for /jobs
ok   CREATED    [200] job 7a0bf4fe… state='succeeded' attempts=1 steps=1
ok   EVENTS     5 event(s), seq 1..5, replay from seq 2 returns 3, last=done
ok   RECORDED   3 row(s) name the job, 1 name an attempt, the chain grew by 4
ok   OTHER      GET /jobs/{id} as another subject -> 404, events -> 404
ok   BOUNDARY   /ask -> 403, /jobs -> 403, both naming 'regulated'
PASSED: 8 right(s) held against the documented job interface
```

**The activity interface**, `activity_probe.py`, eight rights:

```
deployment: oi-core-dev-cloud
ok   READ       10 row(s), ids [9080]..[9071], newest first=True, every row carries actor, at, id, outcome, payload, type
ok   SCOPED     a ['company'] session asking for probe-exec@officeinference.com (['company', 'executive']) -> 200, 0 row(s), restricted=True
ok   ACTOR      actor=nobody@officeinference.com -> 200, 0 row(s)
ok   FILTERED   type=model_call -> 5 row(s), all model calls=True; type=not_a_type -> 400 bad_request
ok   BYREQUEST  request_id 2e5a9bcd… -> 1 row(s)
ok   BYJOB      job b9e007ab… -> 3 row(s) naming it
ok   CAPPED     limit=500 -> 200, limit=5000 -> 400 limit is capped at 500
ok   RECORDED   20 activity read(s) recorded in the chain
PASSED: 8 right(s) held against the documented activity interface
```

**The data boundary, on both paths, with the job path added:**

```
PASSED: refused on both endpoints for a class that may not leave, and the permitted path still answers
PASSED: 8 right(s) held against the documented job interface
```

**The app's own ladder**, three rungs:

| Rung | Command | Result |
|---|---|---|
| unit and component | `npx vitest run` in `oic-ui/app` | 47 passed |
| integration against the live API | `OI_API_URL=… OI_TOKEN=… npx vitest run tests/integration.test.ts` | 12 passed |
| end to end in headless Chromium | `npx playwright test` | 10 passed in 20.6s |

**What the E2E run proves that nothing below it can.** One test asks a question in the browser, then reads the
chain through the API by the request id the answer carried, and fails if the row is not there. Another asserts
that no social-security shape appears anywhere in the DOM after asking the canary question. A third asserts the
Activity screen states its own scope. **If asking a question in the app left no row, the product's central claim
would be false, and no amount of the screen looking right would show it.**

### 5.1d Three defects this step found, and the shape they share

**A control on the path somebody was looking at, for the third time in this project.** `/ask` refused a
forbidden data class with a 403 and `/jobs` answered 200 with a failed job row, so work no lane was permitted
to do had a record and a cheerful status code. The fix is ordering rather than a missing check: the boundary is
consulted before the job exists.

**A successful attempt wrote no row of its own.** The runner wrote one row when a job started and one when it
finished, and a row per attempt only when an attempt failed. A job that worked first time therefore left two
rows that both said "job", and nothing in the chain recorded that a step had run. This is the C3 lesson in its
general form, and it is the second time this project has paid for it: **the columns existed, the flags existed,
and nobody connected them.**

**The request id never reached the audit tool.** `audit()` accepted a `request_id` keyword and never passed the
flag on the command line, so the column stayed empty on every model call and the id an answer carries could not
find its own row. The probe's BYREQUEST right is what found it, and it reported zero rows, which looks exactly
like "the row was never written".

**All three were found by running something, and none by reading.** The pattern in every case is a value that
existed, a place that could hold it, and no connection between them, which is why the tests written for them
assert the connection rather than the presence.

### 5.1e The app, as a service on the deployment

**Goal.** The client application runs on the deployment, reaches the API over the internal network, and is
reached the way the API is reached: through whatever the deployment puts in front, with nothing published.

**Why the app is a build artifact here and a development server there.** In `oic-ui` the app runs under Vite,
which serves the sources and proxies `/api` to the API. On a deployment there is no Vite: the app is a static
bundle of three files, and the API is reached directly. **The thing that makes both work is that the app calls
a relative `/api`**, so the question is only who answers it. That is why the bundle is served by the same little
proxy the workbench uses, and why that proxy is now part of the product rather than a scratch tool.

**What it talks to, and what it must not.** The assistant API, over `ai-net`, and nothing else. It holds no
model key, no database credential, and no knowledge of which model answered. Those are the constraints that let
the model tier, the retrieval stack and the runtime change underneath without a rewrite here, so they are
checked rather than intended: `docker compose config` shows the app's environment is two variables and neither
is a secret.

**Build it.**

```
cd /opt/oi-core
# 1. the contract this build is against, copied from the core's own generator
cp docs/openapi.json oic-ui/docs/openapi.json
# 2. the bundle
cd oic-ui/app && npm ci && npm run build && cd ../..
# 3. the image, which is the bundle and the proxy
docker compose -f deploy/office/compose/docker-compose.yml --env-file rendered/env build app
docker compose -f deploy/office/compose/docker-compose.yml --env-file rendered/env up -d app
```

**Prove it.**

```
docker compose -f deploy/office/compose/docker-compose.yml --env-file rendered/env ps app
docker run --rm --network ai-net curlimages/curl -sS http://app:8080/ | head -5
docker run --rm --network ai-net curlimages/curl -sS http://app:8080/api/health
```

Expected, and the three lines each prove something different:

```
app     running
<!DOCTYPE html>                      the bundle is served
{... "deployment_id": "oi-core-…"}   and /api is forwarded to the service
```

**If it breaks.**

- **The page loads and every call says the service is unreachable.** The proxy is not forwarding. Check that
  `OI_API_URL` names the compose service and that the app is on `ai-net`; a name that does not resolve answers
  nothing, and the browser reports it as the API being down.
- **`/` returns 404.** The bundle is not in the image. `npm run build` writes to `app/dist`, and the image
  copies that directory specifically: a build that failed earlier leaves the previous `dist` in place if one
  exists, which is why the build step prints its output size.
- **The app answers but every question refuses with `no_session`.** There is no sign-in yet, so a token has to
  be minted and pasted. That is C7's work and the manual says so at the point a person meets it.
- **A blank page with no error.** The bundle was built against a different contract than the API serves. Run
  **Run it inside the network, because `api` resolves nowhere else** — the line as first written was executed
  from the host and died with a name-resolution traceback, which the C4 panel's fidelity checker found by doing
  what the page said:

  ```
  docker run --rm --network ai-net -v "$PWD/oic-ui:/w:ro" -w /w --entrypoint bash \
    oi-core/session:local /w/scripts/check-contract.sh --live http://api:8088
  ```

  It names the paths that moved. **A checkpoint that cannot pass where the page says to run it is not a
  checkpoint**, and this one had been in three documents that way.

### 5.1f The app deployed, as it was on 2026-10-14

**Built and started on the deployment, first try.** The bundle is built in the image and served by the proxy:

```
$ oi build app && oi up -d app
app restarts=0 state=running

$ docker run --rm --network ai-net curlimages/curl -sS -o /dev/null -w '%{http_code}\n' http://app:8080/
200
$ docker run --rm --network ai-net curlimages/curl -sS http://app:8080/api/health
{"status":"ok","deployment_id":"oi-core-dev-cloud","registry_declared":true,"default_model_role":"analyst"}
```

**The constraint, checked rather than asserted.** The app's runtime holds exactly two variables of its own and
no credential of any kind:

```
$ docker inspect app --format '{{range .Config.Env}}{{println .}}{{end}}' \
    | grep -vE '^(PATH|LANG|GPG_KEY|PYTHON_VERSION|PYTHON_SHA256|APP_PORT|OI_API_URL)='
(nothing)
```

**Seen, not just measured.** All three screens at 1366x768 through an SSM port forward, with a real answer from
a real document. Two things came out of looking that no assertion would have produced: the Activity row's actor
chip rendered as a pressable block because its class name collided with the rail's profile card, and the sources
chips sat tight against the answer above them.

**And the corpus was replaced.** The deployment had six documents, four of which were benchmark files from a C2
test rather than anything a small business would hold. It now carries the corpus generator's own set: 26
documents under `company` and `executive`, 124 points, **with one deliberate failure fixture failing on purpose
and the other not.**

**The second fixture stopped being a failure and nothing said so.** `dead-letter-fixture.txt` is indexed with one
chunk and zero failures, because the file on the box is the generator's own text and the parser accepts it. The
sentence claiming both failed was true when it was written and was never re-measured, and the operator guide's
Stage 5c depends on a file the parser refuses. **A fixture that stopped failing is worse than no fixture**,
because the check using it keeps passing while measuring nothing. Recorded rather than quietly reworded. The generator's travel policy answers the mileage question the manual uses as its example, and its
new-starter record carries the reserved identifiers the redaction check needs.

**One thing this found in the tests themselves, which is the most useful part of the exercise.** Swapping the
corpus removed the source of a question two tests were built on, and both redaction tests kept passing: one had
its real assertion inside an `if`, and the other accepted "No document was cited" as a valid outcome. A test
whose subject can vanish measures nothing, and it reports green while doing it.

### 5.1g The document read, so a citation leads somewhere

**Why.** The Ask screen has shown a chip per source since 5.1 and the chips were spans. A person could see that
an answer rested on the travel policy and could not read the travel policy, so the product stopped one step
short of the thing it claims: an answer that carries its evidence.

**Where the text comes from.** `chunk_index`, which the ingestion worker writes alongside the vector store. The
document as **indexed**, not as the file looks on disk: a PDF's layout, a spreadsheet's rendering and an OCR'd
page are not what the model read, and showing something subtly different from the evidence is worse than
showing nothing.

Three properties, and each is asserted rather than intended:

| Property | How it is done | What it rules out |
|---|---|---|
| **The label filters in the query** | `chunk_index.acl` is in the where clause | a third path to content where a missing filter hides. Every test written against a permitted document passes either way |
| **Every chunk is redacted on the way out** | the same function that redacts an answer | "they may read the file" being treated as a reason to put an SSN on a shared screen |
| **A forbidden document is not found** | `404` with the same wording as a missing one | using this endpoint to learn that a document exists |

**Run it.**

```
# as the app does it: open the first source chip, or by hand
docker run --rm --network ai-net curlimages/curl -sS \
  -H "X-OI-Session: <a company token>" \
  http://api:8088/documents/company/travel-expenses-policy.md
```

**Expected.** `doc_id`, `acl`, the page extent, the chunks with their pages, `truncated`, and the redaction
counts. The output below is from the deployment:

```
doc_id:     company/travel-expenses-policy.md
acl:        company
pages:      1 to 1
chunks:     1 of 1   truncated: False
redactions: {}
first 160 chars: Applies to all employees. Effective 1 January 2026. Supersedes the 2024 revision.
                 Mileage is reimbursed at 0.67 per mile for travel in an employee's own vehi…
```

**And the two refusals, which must be identical:**

```
company session -> executive/executive-compensation.md   HTTP 404
company session -> company/no-such-document.md           HTTP 404
```

**Both are 404 and the probe asserts they are indistinguishable**, rather than asserting each one on its own.
Two separate assertions both pass while the responses differ, which is how a 403 for the real document would
survive a test suite.

**The redaction on this path, on the document that carries reserved values:**

```
company/pii-new-starter.md -> redactions {'card': 1, 'ssn': 1}, no raw shape in any chunk
```

**Rights.** `api_probe.py` DOCUMENT, which fetches the document a citation names, checks the figure the answer
quoted is present in the text the read returns, checks no identifier shape comes back, and checks the two
refusals match.

### 5.1h The corpus at scale, and what a bigger one costs

**Twenty-six documents prove the pipeline and cannot test retrieval.** C4 closed with the deployment carrying
**10,026 documents**, and the numbers are in the operator guide's stage 4a because that is where an operator
choosing a corpus size will look. The short version:

| Step | Corpus | Time | Rate |
|---|---|---|---|
| Generate | 10,000 documents, 40 MB | 21.8 s | 460 documents/sec |
| Ingest, first 1,000 | 1,000 new of 1,026 seen | 14 m 21 s | 70 documents/min |
| Ingest, the remaining 9,000 | 9,001 new | about 2 h | 72 documents/min |

**The cost is not where I first said it was.** The first measurement of the embedding service used
hundred-character strings and reported 33 chunks/sec, which led to a line in this runbook claiming the corpus
was minutes of embedding and that the cost was all per-file overhead. **The ingest disproved it.** At the
two-thousand-character chunks the corpus actually produces, the same service does about **2.7 chunks/sec**, and
it runs at 1,574 percent CPU during an ingest, saturating fifteen of the sixteen cores.

**Batch size is the solid finding.** Measured under the same contention: one chunk per request gives 0.37
chunks/sec, eight gives 2.0, and sixteen and thirty-two are rejected with `429 Model is overloaded`. The worker
embeds per document and these documents average 1.28 chunks, so it sends requests of one or two — the worst end
of that table, on every document.

**And a claim in this runbook that had to be withdrawn.** It said per-file work was "roughly two thirds of the
critical path". The benchmarks behind that were run while the ingest was running, so they measure the capacity
left over rather than the worker's own cost. **Decomposing a pipeline with numbers taken from a run that
competes with it is not a measurement.** The split needs per-phase timings on an idle box.

**And retrieval was checked rather than assumed.** With 1,400 documents indexed, *"What is recorded about the
boiler at Lake Kayla?"* returned `company/vol-maintenance-00000.md`, the document that contains it. The
mileage question still returns the authored travel policy and the figure `0.67`. Adding a thousand documents
did not move the fixture answers, which is what the volume generator is designed for: no travel policy and no
second document carrying reserved identifiers.

### 5.2 The task registry, so the interface can show what this deployment can do

**Goal.** The tasks a client can start come from their manifest rather than from our source code.

**Why.** The routing trigger consults a list of six task names that are **hardcoded in Python**:

```
summarise_across_documents, reconcile, compare, draft, extract_table, multi_step
```

They are used for one thing, which is a real cost decision: a request carrying one of them asks for the
stronger model. The problem is everything else about them. A client cannot discover them. Nothing labels
them in the client's words. Nothing says what they touch or whether they need approval. And the manifest,
which is what defines the deployment, knows nothing about them.

So the Do menu would have to be written into the app, which breaks the rule that the app is a function of the
manifest. **The list moves into the manifest**, each entry carrying the id the API matches on, a label and a
description in a client's words, the model role that handles it, and what it touches. The API reads the same
list, so the affordance and the enforcement cannot drift apart.

### 5.3 The application

**Goal.** A person who has never seen the system can ask a question and understand what happened.

Built in `oic-ui` against `docs/openapi.json`. Ask first, then Activity, then the job view. The
test ladder is in `docs/handoff-c4.md` §5, and two rungs of it are the ones that matter: **the data-lane
indicator cannot disagree with the audit record**, and **doing a thing in the interface leaves a row**.

## Running the probes: which image, and why it matters

**Every probe needs an entrypoint that carries its dependencies, and getting it wrong looks like a product
failure rather than a harness one.** A C2 acceptance probe run under the ingestion image reported *the audit
event could not be recorded*, which reads as the sink refusing a write; the actual cause was that the image has
no Postgres driver and the audit tool could not import one. The tool names the wrong image now, but the table is
cheaper than the error.

| Probe | Entrypoint | Why that one |
|---|---|---|
| `api_probe`, `stream_probe`, `interface_probe`, `throttle_probe` | `oi-core/session:local` | they mint a session against Postgres |
| `access_probe` | `oi-core/eval:local` | carries the eval extras and the registry |
| `acl_probe`, `hybrid_acceptance`, `citations_acceptance`, `injection_acceptance` | `oi-core/ingestion:local` | they read the vector store through the ingestion services |
| `egress_acceptance` | **`oi-core/session:local` or `oi-core/eval:local`** | it writes through the audit CLI, which needs a Postgres driver |

    cd /opt/oi-core
    alias oi='docker compose -f deploy/office/compose/docker-compose.yml --env-file rendered/env'

    # A probe that mints a session:
    bash scripts/probe.sh interface_probe.py

    # A probe that reads the vector store:
    bash scripts/probe.sh acl_probe.py

**A probe that passes under one image and fails under another is a harness problem until proven otherwise.**

**And `stream_probe` is load-sensitive, so run it on its own.** It has passed all four rights three times in a
row when run by itself and failed once when several probes ran back to back, and the substantive properties held
on every attempt: the canary was redacted, the frames arrived incrementally, and `X-Accel-Buffering` was `no`.
Frame counts vary run to run (9 to 44 on the same question) because the provider's chunking varies, and one of
its rights is a timing assertion. **A probe that fails under load is weak evidence**, so when it fails, re-run it
alone before believing it, and treat a repeatable failure as real.
Check the import before believing the failure.
## Phase C5 — Workflows and the first connector

**Invoice intake end to end, with an approval gate.** Two screens that exist and do nothing get an engine
underneath them: Do shows a task menu with nothing to run, and Review was left out of C4 on purpose because
an approval queue with nothing in it teaches a client the feature is broken.

**The register is the specification.** Thirty-four rows, `phase: C5`, in `docs/requirements.yaml`. This phase
is not finished while any of them is open.

**Five research reports are the input rather than background reading**, in `docs/research/` and
`docs/design/c5-approval-record-identity.md`. Three findings in them changed the plan and none is obvious:
the workflow engine is ours rather than adopted, and the reason is transactional audit rather than licence;
a shared mailbox cannot be used at all, which changes what a client buys; and an approval cannot claim a
verified identity, which changes what the interface may say.

### 6.0 The contract, before anything is built on it

**Goal.** A second repository, and three agents, can be written against this contract without reading its
source. And no route can record an approval without also recording what the system does not know.

**Why this is the first step, again.** C4 wrote the same step for the same reason and it paid for itself: the
OpenAPI document listed three URLs and **no response bodies at all**, so a client built against it would have
been built against the manual's prose. The approval route has a sharper version of that problem. **It is the
one place in the product where a wrong record is worse than a missing one**, because a client reads it and
believes it.

**What already exists.** `POST /jobs/{id}/{action}` takes pause, resume and cancel. `awaiting_approval` has
been in the state machine since C4 and is reachable from `running`. Nothing has ever reached it, which is why
C5 is its first writer and why two defects are still cheap to fix (`AUD-APPROVAL-001`, `UI-FEED-001`). Both
are rows in `docs/requirements.yaml` rather than things a reader is expected to have read about elsewhere, and
6.2 is where each one is explained and closed.

**What is added, and why it is a route of its own rather than a fourth action.**

| Method | Path | What it does |
|---|---|---|
| POST | `/jobs/{id}/approvals` | a decision on a job that is waiting for one |

Pause and cancel are **controls on the work**. An approval is an **authorization of it**, and it carries a
body, a reason, and a record that outlives the job. Folding it into `{action}` would put a path segment where
a payload belongs, and the payload is the part that has to be right.

**The request.**

```
POST /jobs/{id}/approvals
{"session": "<token>", "decision": "approve", "reason": "", "note": "", "action_digest": "sha256:..."}
```

**Three decisions, and one of them requires a reason.**

| decision | reason |
|---|---|
| `approve` | not required |
| `deny` | **required**, and a denial without one is refused rather than stored blank |
| `request_changes` | **required**, for the same reason |

**`action_digest` is what makes the approval an approval rather than a click.** It is the hash of the exact
content being approved, and a decision whose digest does not match what is waiting is refused. Without it the
record says somebody approved something, and the link to what can be re-pointed afterwards.

**The record, and where it goes.** Into `payload`, **not** into `audit_events.approval`. That column is a
jsonb field with an approval event type and it is not among the eighteen fields the canonical function takes,
so a value written there can be edited by hand while every chain check passes. It is the identical defect
migration 013 was written to close for the redaction columns. `payload_sha256` already covers `payload`, so
this needs no schema change and no new chain version.

**And the record carries what the system does not know**, which is the half that is easy to omit:

```
"identity": {
  "assurance": "unverified",
  "basis": "session_token",
  "claimed_subject": "probe-staff@officeinference.com",
  "claimed_role": "viewer"
},
"part11_11_50_manifestation": {
  "printed_name": null,
  "name_absent_reason": "sign-in is not enabled on this deployment, so the system cannot verify who acted",
  "signed_at": "2026-10-16T16:20:00Z",
  "meaning": "approve the action described by action_digest"
}
```

**Two values in that block are the ones the system writes and not a past tense of them.** The meaning is the
decision verb the caller sent, so an approval reads `approve the action described by action_digest`, and the
role is a session role: `viewer` is the one this deployment declares, while `staff` is a group in the access
registry. A copy that said `approved` and `staff` was wrong twice in one block, and both were found by reading
a real row.

**Two rules make the assurance field work, and both are the kind that fail quietly.**

- **Unknown values resolve downward.** A value the reader does not recognise becomes `unverified`, never
  something stronger. The four are `unverified`, `provider_verified`, `proofed` and `re_attested`; only the
  first is reachable today.
- **Assurance never rises on read.** The display path reads assurance **from the record** and never joins to
  the live session. This is the argument `017_sessions.sql` already makes for `access_roots`: a permission read
  live at comparison time is a permission that has since moved. Identity read live is an identity that was not
  verified when the act happened.

**The fields are named `claimed_` rather than `subject_` on purpose.** The caveat lives in the field name,
where an interface cannot drop it by choosing not to render a paragraph.

**What no sentence may say.** Not *"Approved by Dana at 2:14pm"*, and not *"A person approved an action"*
either. The second is the line `audit_feed` rendered until migration 022 and it asserts something the system
does not know. The sentence `approvals.describe()` prints, and therefore the sentence the feed renders and the
one a client reads, is:

```
Approved at 2026-10-16T18:18:25+00:00 under the viewer role. The person who decided was not identified:
sign-in is not enabled on this deployment, so the system cannot verify who acted.
```

Every word is load-bearing. **Decided** rather than approved, because one sentence renders all three
decisions. The **role**, because that is what the record establishes. The reason **as
`approvals.UNVERIFIED_REASON` holds it**, so the sentence cannot drift from the record it describes.
`tests/test_workflow_chain.py` compares the view's sentence against `describe()` for all three decisions,
because a sentence defined twice is a sentence that drifts.

**And the contract cannot see route order, which is how this route stayed dead through every check.**
`docs/openapi.json` lists both `/jobs/{job_id}/{action}` and `/jobs/{job_id}/approvals` and passes, because a
schema is generated from the route table and does not know which entry wins a match. Starlette matches in
declaration order, and the wildcard was declared first, so `approvals` arrived as an action and was correctly
refused with `bad_action`. **The route had never run, for any decision, and every check was green.** Only a
request carrying a valid session can tell the two apart, and 6.2 is that request.

**Verify.**

```
cd /opt/oi-core
bash scripts/probe.sh job_probe.py

# The contract check has to run ON the docker network, because it fetches http://api:8088 and that name
# resolves only there. `python3 scripts/fetch_openapi.py --check` on the host fails with a name resolution
# error, which reads like a broken service and is a broken command.
docker run --rm --network ai-net --user "$(id -u):$(id -g)" \
  -v /opt/oi-core:/w -w /w --entrypoint python oi-core/session:local \
  /w/scripts/fetch_openapi.py --check

~/venv/bin/python -m pytest tests/ -q
```

Expected: `job_probe.py` reports its rights and none regresses; the contract check prints
`OK … (12 path(s): …)` with `/jobs/{job_id}/approvals` among them and exits 0; and the suite passes with the
database-backed files skipped, which is a skipped right and not a held one. **`~/venv/bin/python` and not a
bare `python3`**: the system Python on the reference machine has no pytest, so the bare form fails with
`No module named pytest` before it runs a single test.

**What this step cannot yet prove, said plainly.** The probe drives the C4 one-step executor, and **that
executor never reaches the gate**, because reaching it needs a multi-step run and the engine is the next
piece. So the approval route is verified here for its shape, its refusals and its presence in the contract,
and its end-to-end path is verified by the workflow piece. A page that claimed otherwise would be describing
a test that does not exist.

**Both commands in this step were wrong when it was first written, and following it is what found them.**
It said `jobs_probe.py` and the file is `job_probe.py`. Then it said `python3 scripts/fetch_openapi.py
--check`, which cannot resolve `api` from the host, when the correct form was already written out in step
5.0 four hundred lines above. Neither error is visible by reading. This project has now hit a documented
command that cannot run five times, and every one was found by somebody running it.

**If it breaks.**

- **A denial stored with no reason**: the route is treating `reason` as optional for all three decisions. Only
  `approve` may omit it.
- **`verify_chain` reports a break on rows nobody touched**: the approval went into the `approval` column,
  which is outside the canonical string, so the row hash covers something the row does not. Move it to
  `payload`.
- **The screen says a person approved**: something is rendering `claimed_subject` without the basis. The
  record is honest and the display is not, and the display is what the client reads.
- **An old job's approval changes when a session changes**: assurance is being read live instead of from the
  record. It must be read from the record.


### 6.1 The engine, and the transaction the state change shares with its record

**Goal.** A job with more than one step runs to a terminal state, and every step it takes has a row in the
chain written **in the same transaction as the state change**. Kill the process between the two writes and
neither is present.

**Why the engine is ours rather than adopted, said once and precisely.** Every off-the-shelf engine sits
outside our transaction and reports what happened afterwards, by webhook or by callback. That is not a
limitation of any particular product; it is what a separate process can do. A crash between the business
write and the audit write then leaves a gap in the one artifact a client may ask to verify, and no amount of
configuration closes it. The requirement is the transaction, so the engine has to be inside it.

**What already exists, and is not rewritten.** `services/api/jobs.py` holds the state machine, the event log,
the budgets, `TERMINAL` and both stores. C5 adds a loop that has more than one step; it does not replace the
machine underneath it.

**What C4 could not do.** `jobs.run()` writes the state with `store.put(job)` and then writes the chain row
through `audit(...)`, which shells out to `services/audit/audit.py`. That tool opens its own connection, so
those are two transactions in two processes. **The engine piece was blocked on that, and nobody had noticed
until this step was written down.** `cmd_record` now writes its row through `insert_event(cur, ...)`, which
takes a caller's cursor, and `cmd_record` calls it, so there is still one INSERT statement and still one
definition of the hash.

**What is added.**

| File | What it is |
|---|---|
| `services/workflow/engine.py` | the loop: position, gate, budgets, cancellation, one transaction per unit |
| `services/workflow/plans.py` | the plans this deployment can run, and the action a gate holds |
| `services/workflow/chain.py` | the call into the one audit writer, on the engine's cursor |

**The one idea, and it is four lines.**

```
with store.conn.transaction():          # psycopg3 begins a real transaction even in autocommit mode
    store.put(job)                      # the state change
    store.append(job_id, EV_STEP, ...)  # the event, numbered in the same transaction
    chain.record(store.conn.cursor(), ...)  # the chain row, same eighteen columns
```

Commit at the end. A `SIGKILL` anywhere inside leaves the connection for Postgres to reap, and it rolls back
all three writes. `store.put` and `store.append` are the same functions the rest of the system uses, so the
job row is written in one place. Nothing about the chain moves: the hash is computed by the
`audit_events_chain` trigger from `audit_row_canonical`, and that stays the single definition.

**Where the position lives.** Nowhere new. A step that has run has a `job_steps` row with its name and
`state = 'succeeded'`, so the engine's position is the first plan step without one. A resumed run therefore
cannot repeat work it already recorded, and there is no cursor table to fall out of step with the work.

**Verify.**

```
cd /opt/oi-core
bash scripts/probe.sh workflow_probe.py
~/venv/bin/python -m pytest tests/test_workflow_engine.py -q
```

Expected: the probe reports `RUNS`, `RECORDED` and `ATOMIC` held. `ATOMIC` is the one that matters: it starts
a job in a child process, kills that process with `SIGKILL` at the moment between the state write and the
chain write, and then reads the database. The step's row is absent, the job's state is the one before the
step, and the chain has no row for it. Either both are present or neither is.

**Run it, as it was on 2026-10-16.** On the box, through `scripts/probe.sh`, which is the command above:

```
ok   RUNS        state='awaiting_approval', 4 step row(s), 3 succeeded before the gate
ok   GATED       state='awaiting_approval', digest=sha256:5b480b869b4…, 0 row(s) after the gate
ok   SHOWSACTION read from the job row: 'Post USD 4,210.00 to accounts payable for accounts@ferguson.example'
ok   RESUMED     state='succeeded', post-gate rows={'post_entry': 1, 'close_out': 1}
ok   DENIED      state='failed', reason='denied_at_the_gate', 0 row(s) after the gate
ok   CANCELLED   state='cancelled', slow row=('slow', 'failed', 'cancelled_mid_step'), 0 row(s) after the stop
ok   RECORDED    6 step row(s), 13 chain row(s) naming the job, 6 distinct step(s)
ok   TYPED       state='failed', refusing step=('refuse', 'failed', 'unreadable_invoice'), 0 row(s) after it
ok   BOUNDED     state='failed' after 0.52s (deadline 0.5s), kind='step_timeout', 0 row(s) after it
ok   IDEMPOTENT  state='succeeded', far side applied 1 time(s) over 2 call(s), 2 attempt row(s), 1 ledger row(s)
ok   ATOMIC      child exit=-9 (killed=True), job row='queued', 0 step row(s), 0 chain row(s)
ok   BOTHPRESENT control run: 4 step row(s), 8 chain row(s)
ok   CHAIN       ok   /tmp/workflow-probe-export-1w3334cd/audit-events.jsonl: 108 event(s), chain intact

PASSED: 13 right(s) held against the documented workflow engine
```

**One line moved on the second run, and it moved because somebody fixed something.** The first run at
`2b1cd6a` reported `SHOWSACTION read from the gate event`, because `pending_action` was written on the job and
dropped by the store: it had no column and `PostgresStore.get` did not read it. With migration 021 applied and
the store carrying it, the same right now reads the action **from the job row**, which is the path a browser
takes. The line above is the second run and the evidence moved with the claim.

`ATOMIC`'s line is the whole piece: the child was killed (`exit=-9`), and the job row is still `queued` with
no step rows and no chain rows. `BOTHPRESENT` is the control, so that line cannot be green on an engine that
writes nothing at all.

**`CHAIN`'s event count grows with every run, and a reader comparing two pages should know why.** The probe
exports the whole day's chain rather than the rows its own job wrote, so 108 is the number of events in that
day at that moment: every probe, every test run and every model call since midnight adds to it. The number to
read is `chain intact`, not the count.

```
$ ~/venv/bin/python -m pytest tests/test_workflow_engine.py -q
23 passed in 0.65s
```

### The approval route, end to end

The engine probe uses the store directly. The HTTP route is the surface a person uses, and it has its own probe:

```
cd /opt/oi-core
bash scripts/probe.sh approval_probe.py
```

Expected: seven rights held. `GATED` and `LISTED` prove the invoice job stops and appears in the queue;
`APPROVED` proves the decision resumes the plan and records one transactional and one external effect;
`DENIED` proves nothing after the gate runs; `REFUSED` proves a missing session and a forged role are both
refused and recorded; `HONEST` proves the record and the feed do not invent a person; `CHAIN` proves the
export still verifies after the decisions.

```
ok   GATED     state='awaiting_approval', digest=sha256:5b480b869b4…, 0 step(s) after the gate
ok   LISTED    [200] 1 waiting
ok   APPROVED  [200] state='succeeded', post_entry rows=1, effect ledger rows=transactional 1 / external 1
ok   DENIED    [200] state='failed', post-gate rows=0
ok   REFUSED   no-session=401, forged-role=403, refusal rows=1, authority rows=1
ok   HONEST    assurance='unverified', basis='session_token', summary='Approved at … under the viewer role. The person who decided was not identified…'
ok   CHAIN     ok   …/audit-events.jsonl: 334 event(s), chain intact

PASSED: 7 right(s) held against the documented approval route
```

The refusal counts grow with every run; that is the chain keeping them, not a duplicate. The first interface
probe run after an API restart can fail its `STREAM` right with two frames and no done frame. The separate
stream probe and a warm rerun both pass. Record both runs when it happens; do not call the first one a pass.

**And the right can fail, which is the part worth checking.** A test that cannot fail proves nothing, and
`ATOMIC` is the one this whole piece exists for, so it was mutated on purpose: `PostgresUnit.__enter__` was
changed to skip the transaction, which is the smallest possible version of the mistake — every write still
goes through the same functions, they simply commit one at a time.

```
AS BUILT   ok   ATOMIC  child exit=-9 (killed=True), job row='queued',  0 step row(s), 0 chain row(s)
MUTATED    FAIL ATOMIC  child exit=-9 (killed=True), job row='running', 0 step row(s), 0 chain row(s)
```

**That is the defect stated in one line.** With the transaction, the kill leaves nothing: the job is still
`queued` and the chain is empty. Without it, the job row survives as `running` while the chain has no record of
it — a job that moved and nothing that says who moved it, which is exactly the gap an adopted engine cannot
close. The mutation was reverted before the run above; the `ok` line is the same run this step pastes.

**What this step cannot prove, said plainly.** The probe kills a *runner* process, not the API service. That
is the stronger test of durability, because resume depends on the row and not on the process, but it is not
the same sentence as "restart the service" and the runbook will not pretend it is. The service-level restart
belongs to step 6.2, **and on 2026-10-16 it was done**: the api image was rebuilt, the service was restarted
while a job waited at the gate, and the job finished after an approval over HTTP. That took four fixes to
paths that had never run, and 6.2 names all four.

**This step found that WF-ENGINE-001 was not achievable as written.** The register row asked for a property
that the deployed architecture could not have: with the chain written through a subprocess, no transaction
spanned both writes at any level of care. `services/audit/audit.py` now has `insert_event(cur, ...)`, which
takes a caller's cursor, and `cmd_record` calls it, so the CLI and the engine still write through one INSERT
statement. The row is unchanged and the code moved, which is the direction that is allowed.

**And the first version of this step named a command that does not exist.** It said
`python3 -m pytest tests/test_workflow_failure.py -q` in 6.3 and `tests/test_workflow_gate.py` and
`tests/test_workflow_feed.py` in 6.2. None of the three files was ever written; the tests live in
`test_workflow_engine.py` (no database needed) and `test_workflow_chain.py` (a database needed, and it says
so). Following this page literally is what found them, which is the same way every other wrong command in this
runbook was found.

**If it breaks.**

- **`ATOMIC` reports the chain row present and the state unchanged**: the state write committed before the
  kill, so it is not in the same transaction. Look for a `store.put()` outside the `with` block.
- **`RECORDED` counts fewer rows than steps**: a path writes the state without the chain row. That is the
  defect the row exists for, not a flake.
- **The probe reports `ATOMIC` skipped**: it found no database, and a skipped right is not a held one.
- **`verify_chain` breaks on rows nobody touched**: something wrote the chain with its own canonical string.
  There is one definition and it is in the database.


### 6.2 The gate, the resume across a restart, and the sentence the feed may write

**Goal.** A workflow stops at an approval gate, survives the process that was running it, and finishes after
somebody approves it. The steps after the gate each have exactly one row. And the chain's own rendering of an
approval stops asserting a person nobody identified.

**Why the gate is a step and not a fourth action.** `awaiting_approval` has been in the machine since C4 and
this is its first writer. The gate is reached from `running`, never from `queued`: a job that has never run
cannot be waiting to approve something it has not produced. The machine refuses that transition and it is
right to.

**What the gate holds.** The engine computes the digest over the drafted action's content and calls
`jobs.request_approval`, which writes the digest before the transition. There is no window in which the state
says waiting and the digest is empty. The action itself goes into the gate's event, so the Review screen has
the words behind the hash rather than only the hash.

**The resume, which is the part that has to survive.** The job row carries `pending_action_digest`, and
without it in the store a job that came back after a restart would have an empty digest and every approval
would be refused as drift. **That was the state of the tree when this step was written**: the field was on
the dataclass and in neither the INSERT nor the SELECT. Migration `021` adds the column; the store writes it
on insert *and on update*, because an approval clears it and a column written only on insert never changes.

**A resumed run must not repeat what it already did.** The position comes from the succeeded `job_steps` rows,
so a step that completed before the gate is not run again. The register asks for "exactly one row" per
post-gate step, and that is what the probe counts.

**`UI-FEED-001`, which lands here because this is the first approval anyone can read.** `audit_feed` rendered
an approval as `'a person ' || outcome || ' an action'`, which asserts a person the system has not
identified. Migration `022` replaces the view's approval branch with one that reads the record and falls back
to the honest sentence for any value it does not recognise. **The design document's suggested expression
reads `payload -> 'identity'` and the record nests identity at `payload -> 'approval' -> 'identity'**, so
that expression would never find it and would silently take the honest branch for every row, including
verified ones. Fail-safe, invisible, and wrong. Migration `022` reads the path the record actually has, and a
test compares the view's sentence against `approvals.describe()` for all three decisions so the two cannot
drift apart in silence.

**Verify.**

```
cd /opt/oi-core
bash scripts/probe.sh workflow_probe.py
docker exec postgres psql -U oicore -d oicore -tAc \
  "select summary from audit_feed where event_type = 'approval' order by id desc limit 3"
# **`-rs` is not decoration.** Without it a skip prints as one `s` and both the reason and the command to
# start a database are hidden, so fourteen unheld rights read as fourteen fine ones. Round three's cold reader
# recorded exactly that, and three of these tests are the ones holding the feed sentence to `describe()`.
OI_TEST_DATABASE_URL=<a database of your own> ~/venv/bin/python -m pytest tests/test_workflow_chain.py -q -rs
```

**And the sequence the register actually writes, which is five commands and one restart.** The engine is
wired into the API and the manifest declares the task, so this is the whole thing from outside:

```
# 1. rebuild, because the API runs from an image that mounts none of its own source
cd /opt/oi-core && oi build api && oi up -d api

# 2. a session for a subject the registry declares. rendered/access.json lists them.
TOKEN=$(oi run --rm session mint --dev --subject probe-staff@officeinference.com --role viewer --json \
  | python3 -c 'import json,sys; print(json.load(sys.stdin)["token"])')

# 3. start the workflow. The input is a message rather than an invoice description: the plan reads the text
#    with four patterns, the vendor is the one word after "from", and an ellipsis in that position is
#    captured as the vendor. The request runs on ai-net because the API publishes no port.
JOB=$(docker run --rm --network ai-net curlimages/curl -sS -X POST http://api:8088/jobs \
  -H 'Content-Type: application/json' \
  -d "{\"session\":\"$TOKEN\",\"task\":\"invoice_intake\",\"input\":\"INV-2026-0417 from accounts@ferguson.example Total 4210.00 due 2026-11-15\"}" \
  | python3 -c 'import json,sys; print(json.load(sys.stdin)["job_id"])')
#    -> 200, state=awaiting_approval, four step rows, a pending_action and a digest

# 4. restart the service while it waits, and read the row rather than the response
docker restart api
docker exec postgres psql -U oicore -d oicore -tAc \
  "select state, pending_action_digest from jobs where job_id = '$JOB'"
#    -> awaiting_approval | sha256:5b480b869b4bbb02f…

# 5. approve, quoting the digest the job is holding
DIGEST=$(docker exec postgres psql -U oicore -d oicore -tAc \
  "select pending_action_digest from jobs where job_id = '$JOB'" | tr -d '[:space:]')
docker run --rm --network ai-net curlimages/curl -sS -X POST \
  "http://api:8088/jobs/$JOB/approvals" -H 'Content-Type: application/json' \
  -d "{\"session\":\"$TOKEN\",\"decision\":\"approve\",\"action_digest\":\"$DIGEST\"}"
#    -> 200, state=succeeded, six steps, each exactly one row
```

Expected: the job survives the restart with its digest intact, and the approval route — not the probe —
finishes the run. `$TOKEN` and `$JOB` are what the later commands in this step use.

**Two things in that sequence are not obvious and both cost somebody an afternoon.** `POST /jobs` needs a
*session in the body*, not only in the header, and the subject has to be one the deployment's registry
declares, because a subject is refused rather than defaulted. And the API mounts only `rendered/`, `audit.py`
and `assertion.py`, so an `app.py` change is invisible until `oi build api` runs — a restart alone reloads the
old image and looks exactly like a fix that did not work.

Expected: `GATED` and `RESUMED` held, the approval summaries say the person was not identified and never name
the claimed subject, and the post-gate steps have one row each.

**The last command needs its own database, and it says so rather than skipping quietly.** It writes rows into
an append-only chain, so it takes `OI_TEST_DATABASE_URL` and never `DATABASE_URL`: a deployment's own variable
would put test rows into a client's record, which is the one table that cannot be tidied afterwards. Without
the variable the tests **skip with the reason and the command to start one**, which is a skipped right and not
a held one.

**Run it, as it was on 2026-10-16.** The feed, read out of the view after the chain tests ran — **in the test
database `OI_TEST_DATABASE_URL` points at, not on the deployment** — so the block shows all three decisions,
including the verified branch this deployment cannot produce. When this step was written the deployment had no
approval row; the sequence above is what produces them, and its feed renders the honest sentence. Three rows, one per decision, and the middle one is the
case where the record says the identity was verified:

```
$ psql -tAc "select summary from audit_feed where event_type = 'approval' order by id desc limit 3"
Approved at 2026-10-16T16:20:00+00:00 under the staff role. The person who decided was not identified: sign-in is not enabled on this deployment, so the system cannot verify who acted.
Approved at 2026-10-16T16:20:00+00:00 by dana (staff)
Changes requested at 2026-10-16T16:20:00+00:00 under the staff role. The person who decided was not identified: sign-in is not enabled on this deployment, so the system cannot verify who acted.

$ OI_TEST_DATABASE_URL=… ~/venv/bin/python -m pytest tests/test_workflow_chain.py -q
14 passed in 0.56s
```

The two English sentences are produced in two languages: the view assembles one in SQL and
`approvals.describe()` assembles the other in Python. **Three of those fourteen tests compare them, one per
decision, from a single parametrised test**, because a sentence defined twice is a sentence that drifts. The
comparison is the only thing holding them together, and it is why the view's fallback branch is written to
match `describe()`'s rather than being independently reasonable. **The fallback matches on the three decisions
the route accepts and not beyond them**: the view uses Postgres `initcap`, which capitalises every word, where
`describe()` uses Python's `capitalize`, which capitalises the first. A hand-written row carrying
`request_changes_v2` renders `Request Changes V2` from the view and `Request changes v2` from the API. The
route refuses any decision outside the three, so that difference needs a row written by hand to reach.

**This step's first version named two test files that do not exist** — `tests/test_workflow_gate.py` and
`tests/test_workflow_feed.py`. The tests are in `test_workflow_chain.py`. Found by following the page.

**This step also found a defect in `services/api/app.py` that no probe could have caught.** `decide_job`
passes the decision as the audit row's `outcome`, and `audit_events.outcome` is constrained to
`ok`, `denied`, `error` and `pending`. So `POST /jobs/{id}/approvals` raises `CheckViolation` inside
`audit()`, after `jobs.decide()` has already committed — a job that resumed with no record of who approved it.
The fix is the route's, and it is to pass `outcome="ok"` and let the decision live in
`payload -> approval -> decision`, which is where `build_payload` puts it and where both the view and
`describe()` read it. 6.0 said this route had never run end to end; this is what was waiting there.

**What this step could not prove when it was written, and proved on 2026-10-16.**

The probe restarts the *runner*, not the API service container. Restarting the runner is the stronger test of
durability, because resume depends on the row and not on the process, but it is not the sentence the register
writes, and the runbook will not swap one for the other.

**Then the service-level restart was done, and this is what it looked like.** The engine was wired into
`services/api/app.py`, the manifest declared `invoice_intake` with `needs_approval: true`, the api image was
rebuilt and the service restarted — after which a workflow ran over HTTP for the first time in this phase:

```
$ POST /jobs  {"task": "invoice_intake", "input": "INV-2026-0417 from accounts@ferguson.example total 4210.00 USD due 2026-11-15"}
POST /jobs -> HTTP 200
  {"attempts": 4, "job_id": "178e4523057540b3bb87d569852c93c9", "state": "awaiting_approval", "terminal": false}
  steps: [('read_message', 'succeeded'), ('extract_fields', 'succeeded'), ('draft_action', 'succeeded'), ('approval', 'running')]
  digest: sha256:5b480b869b4bbb02f…
  pending_action: {"body": "{…\"amount_minor\": 421000…}", "kind": "ledger.post" ...}

$ docker restart api
--- after docker restart api ---
awaiting_approval | digest=sha256:5b480b869b4bbb0

$ POST /jobs/178e4523…/approvals  {"decision": "approve", "action_digest": "sha256:5b480b869b4bbb02f…"}
POST /jobs/{id}/approvals -> HTTP 200
  {"attempts": 6, "job_id": "178e4523057540b3bb87d569852c93c9", "state": "succeeded", "terminal": true}
  steps: [('read_message', 'succeeded'), ('extract_fields', 'succeeded'), ('draft_action', 'succeeded'), ('approval', 'succeeded'), ('post_entry', 'succeeded'), ('close_out', 'succeeded')]
```

**Two of those lines are a labelled transcript rather than one command's output.** The restart line is
`awaiting_approval | digest=sha256:5b480b869b4bbb0`, and the query behind it is `psql -tAc`, which prints
`awaiting_approval|sha256:5b480b869b4bbb02f12ed…` with no spaces and no `digest=` label. The value is the
digest the job is holding.

And the record it left, read out of the database afterwards:

```
job row     : succeeded | reason=(none) | digest=(cleared)
step rows   : 1 read_message … 6 close_out, one row each, all succeeded
chain       : 14 row(s) naming the job, 7 distinct step(s)
approval row: event_type=approval | outcome=ok | decision=approve | assurance=unverified
              | name=probe-staff@officeinference.com
feed        : Approved at 2026-10-16T18:18:25+00:00 under the viewer role. The person who decided was not
              identified: sign-in is not enabled on this deployment, so the system cannot verify who acted.
verify      : ok /check/audit-events.jsonl: 176 event(s), chain intact
```

**The last two lines are UI-FEED-001 fixed and rendering live.** The sentence names no person, says which role
the decision is attributable to, and says what the system does not know.

**Three defects sat on this path, and none of them could be found without running it.** 6.0 wrote that the
route had never run end to end; this is what was waiting there.

- **The decision was written into `outcome`.** `audit_events.outcome` is constrained to `ok`, `denied`,
  `error` and `pending`, and the route passed `approve`. The insert raised `CheckViolation` *after*
  `jobs.decide()` had committed, so the client got a 500 and the job resumed with no record of who approved
  it. The fix passes `ok` and leaves the decision in `payload -> approval -> decision`, where both readers
  already look for it.
- **The approvals route was shadowed by the wildcard.** `/jobs/{job_id}/{action}` was declared *before*
  `/jobs/{job_id}/approvals`, and Starlette matches in declaration order, so `approvals` arrived as an action
  and was correctly refused with `bad_action`. **The route had never run, for any decision.** A contract check
  cannot see this: `docs/openapi.json` lists both paths and passes, because a schema is generated from the
  route table and does not know which entry wins a match. Only a request with a valid session can tell them
  apart.
- **`PostgresStore.get()` returned no steps at all.** Its steps list was built from a cursor whose result set a
  later `execute` — the one computing `events_through` — had replaced, so every job read back from the durable
  store had no history. The engine derives its position from those rows, so the symptom was
  `the step row for read_message attempt 1 is missing`: a sentence about the engine, from a defect in the
  store. It sent two people to look at the wrong file, and it is why `POST /jobs` answered 500.

**And a fourth, found by reading the job row rather than the traceback.** A workflow paused and then resumed
to completion carried `outcome_reason = "paused_by_operator"` on a `succeeded` job. `jobs.transition` only
ever sets that field, and it is documented as the reason a job ended *in the case that it did not succeed*.
The engine now clears it on success, and `test_a_job_that_succeeded_carries_no_reason_for_having_ended` fails
without that line.

**All four are the same shape: a path that had never been executed.** The suite is green through every one of
them, because the memory store takes a different branch and the contract check reads a schema rather than a
match. `tests/test_store_parity.py` is the answer to the store half of that shape.

**A known gap, and it is a decision rather than an omission.** `jobs.result` has no column, so a job
that succeeds **after a resume** reads back from `GET /jobs/{id}` with an empty result. The answer is in the
`done` event, which is readable and is the record; the job row is not. The owner's decision on 2026-10-16 was
not to add the column, on the grounds that it is a schema change for a screen detail. It is written here so
that a client seeing an empty result finds the explanation rather than a mystery.

**If it breaks.**

- **Every approval after a restart is refused as `approval_drift`**: `pending_action_digest` is not surviving
  the store round trip. Check the column exists and that the `on conflict do update set` carries it.
- **A post-gate step has two rows**: the position is being taken from something other than the succeeded step
  rows, or a step that already succeeded was run again.
- **The feed names the person**: the approval branch is reading `claimed_subject` without the assurance, or
  reading the wrong path. Both fail the same way, and the second looks like it works.
- **The feed says an identified person approved when nobody did**: the assurance path resolved upward. An
  unknown value must land in the honest branch, never in the other one.
- **A finished job has a step row still `running`**: on a denial the gate's own row is left as it was, which is
  recorded in guide 13m.3. It is not work in progress, and the job's state is what says whether anything is
  happening.


### 6.3 A typed cause, a bounded step, and an effect that happens once

**Goal.** Three failure properties, each with a test that fails if it stops being true: a failed step is
recorded with a typed cause and **nothing after it runs**; a step that hangs is stopped and the job reaches a
terminal state in bounded time; and a retried step does not repeat an effect it already had.

**Why "nothing after it runs" is the harder half.** A test that asserts only the failure passes on a workflow
that ran to the end and failed at the last hurdle. The probe asserts the *absence* of a row for every step
after the failing one, which is a different assertion and the one the register asks for.

**Why the timeout is enforced twice.** A step callable receives a deadline and is expected to honour it, and
the engine also enforces it from outside, because a callable that blocks on a socket that never answers does
not run its own timeout. The engine runs the step in a worker thread and stops waiting at the deadline, so
the *job* reaches a terminal state within the bound whether or not the step cooperated. **What that cannot do
is free the blocked thread**, and the runbook will not claim otherwise: a step abandoned at its deadline keeps
its socket until the far side closes it or the process exits. The failure is typed `step_timeout` either way,
and the record says the step was stopped rather than that the work was undone.

**Why an effect needs a key rather than a rule.** Exactly-once is a property of a transaction. An effect
outside our transaction cannot be made exactly-once by us; what we can do is give it a key that is **stable
across attempts** and record it, so the far side can dedupe and the record can show how many times the effect
was applied rather than how many times the step ran. An engine that keys the effect by the attempt number
duplicates it on the first retry, and that is the defect this row is for.

The probe tests it by failing a step *after* its external effect and before its state write, then letting it
retry. The far side counts applications, not calls, and the count is one.

**And the transactional case is structural.** An effect the engine writes itself goes into
`workflow_effects` in the same transaction as the step's state change, so a kill between the two rolls back
both and the retry applies it once. That case needs no key discipline because it has no window.

**Verify.**

```
cd /opt/oi-core
bash scripts/probe.sh workflow_probe.py
~/venv/bin/python -m pytest tests/test_workflow_engine.py -q
```

Expected: `TYPED`, `BOUNDED` and `IDEMPOTENT` held. `TYPED` reports the failing step's kind with no later row;
`BOUNDED` reports the elapsed time against the bound; `IDEMPOTENT` reports the far side's application count
as one against a call count of two.

**Run it, as it was on 2026-10-16.** The three lines below are from the box run above; the fourth is the
suite, which needs no database because these three properties are properties of the loop:

```
ok   TYPED       state='failed', refusing step=('refuse', 'failed', 'unreadable_invoice'), 0 row(s) after it
ok   BOUNDED     state='failed' after 0.52s (deadline 0.5s), kind='step_timeout', 0 row(s) after it
ok   IDEMPOTENT  state='succeeded', far side applied 1 time(s) over 2 call(s), 2 attempt row(s), 1 ledger row(s)

$ ~/venv/bin/python -m pytest tests/test_workflow_engine.py -q
23 passed in 0.65s
```

`IDEMPOTENT`'s line is the one to read twice. **Two calls, one application.** The step ran twice, the far side
saw the same key both times, and it counted one. If the engine derived the key from the attempt number, that
line would say two applications, the job would still be `succeeded`, and nothing else in this runbook would
notice. `tests/test_workflow_engine.py::test_the_effect_key_is_derived_from_the_job_and_the_step_and_never_from_the_attempt`
is what makes it visible without a database.

**And the first version of this step named a test file that does not exist** —
`tests/test_workflow_failure.py`. These three properties are tested in `test_workflow_engine.py`. That is the
second wrong command this page had when it was written, and both were found the same way.

**What this step cannot prove, said plainly.** The timeout test uses a real listening socket that accepts a
connection and never answers, which is the shape the register names. It does not test a provider that stops
sending mid-stream, which is a different failure with its own handling in the request path and its own probe.

**If it breaks.**

- **A later step has a row after a failure**: the loop is continuing past the failure. The job's terminal
  state hides it, because a job that failed at step five still ends `failed`.
- **The job never reaches terminal**: the watchdog is not bounding the step, or the worker thread holds the
  unit's transaction open. A transaction held open by an abandoned thread is the failure mode to look for.
- **The far side applied the effect twice**: the key is derived from the attempt rather than from the job and
  the step. It must be the same key on every attempt.
- **The effect row and the state change disagree after a kill**: the effect was written outside the unit's
  transaction. Inside it, a kill cannot separate them.


### 6.4 The email intake connector

**Goal.** An invoice arrives in the client's own AP mailbox and becomes a document in the system with a
provenance row naming the sender, the message identifier, the arrival time and the sha256 of its bytes. That
hash and the hash the ingestion worker records for the same file are the same number. A second copy of the
same invoice does not run the workflow twice. Nothing on the path belongs to Office Inference, and cutting
every cord to us leaves it working.

**Why the mailbox is a licensed regular mailbox, and why that is a purchase the client has to be told
about.** Three findings in `docs/research/email-intake/handover-owned-email-intake.md` decide this step.

**A shared mailbox cannot be used at all.** Microsoft's shared-mailbox documentation says sign-in on the
associated user account should be blocked and kept blocked, and that every shared mailbox has a
system-generated password nobody knows. Delegated OAuth needs one interactive sign-in to mint the refresh
token, so a shared mailbox is not a slower path, it is no path. The failure it forces is the one to avoid:
the moment intake sits on a shared mailbox you are pushed to application permission, and application
`Mail.Read` reads every mailbox in the tenant.

**Delegated permission is the only scope that is right without PowerShell.** Delegated `Mail.Read` reads the
signed-in user's mail and nothing else. It needs no Exchange RBAC grant, no management scope and no cmdlet.
Application permission is the one that needs scoping, and the scoping mechanism is PowerShell only, which is
why choosing delegated removes the problem rather than solving it. It also removes the trap in the other
mechanism: Exchange RBAC for Applications is **additive** to the Entra grant, so an administrator who
completes three of four steps has built something that looks scoped and is not.

**The app registration cannot be created anywhere but the client's tenant.** There is no supported move of an
app registration between tenants. A service principal in the client's tenant pointing at an application
object in ours is the disqualified design: the client cannot rotate the secret, cannot change the redirect
URI, and deleting their service principal does not stop our app object existing.

So the client needs one licensed regular mailbox, and the onboarding checklist says so before the quote
rather than after it, because it is a line item they are buying.

**Why this connector reads a mailbox instead of owning an address.** The C5 channel research recommended an
address we host, on the evidence that email is the largest automatable arrival channel and the only one with
no stored credential. That recommendation is superseded for this build by `BIZ-INDEPENDENT-001`: an address
we host is infrastructure the client depends on us for, and the requirement is that they can cut every cord
and keep working. Reading their own mailbox over delegated permission keeps the data in their tenant and
gives us a credential that reaches exactly one mailbox.

**What is built.** One new service, `services/intake/`. It imports nothing from `services/api/` or
`services/workflow/`, and the seam to the workflow is the deposited document and its hash rather than a
job. That is deliberate: the intake piece was built in parallel with the workflow piece, and a seam of one
file plus one hash is the one that survives two authors.

| Module | What it owns |
|---|---|
| `intake.py` | The CLI. `check-config`, `cord-check`, `migrate`, `fetch`, `watch`, `watch-folder`, `enumerate-reach`, `monitor`, `verify-manifest`, `status` |
| `config.py` | Reads and validates the client's YAML. Refuses an application permission, a shared mailbox, an alert recipient on a domain we own, and any host that is not Microsoft's |
| `net.py` | The only place an HTTP request is made. Holds the host allowlist and refuses anything else, and refuses any Graph method other than GET |
| `secretstore.py` | Reads the credential from the client's secret store at the point of use. Refuses a group- or world-readable file, caches nothing |
| `graph.py` | The refresh-token grant, the message list, the attachments. Classifies `AADSTS` codes into a sentence a client can act on |
| `sender.py` | Envelope sender against header sender, the `Authentication-Results` header, the supplier allowlist |
| `content.py` | Instruction detection in a body or a subject, and the rule that a body is data |
| `provenance.py` | The record, the deposit, the dedupe by content hash, and the journal |
| `folder.py` | The polling folder connector and its settle check |
| `monitor.py` | The heartbeat, the volume floor, the alert classification, the recipient check |
| `reach.py` | Enumerating what the credential can actually reach |
| `schema/001_intake_events.sql` | Intake's own table, beside the service rather than in `db/migrations/` (see below) |

**Why the migration lives beside the service.** `db/migrations/` is owned by the workflow piece this phase,
and two writers in one migrations directory is a conflict with no upside. The table is intake's and the file
travels with the code that writes it. Moving it into `db/migrations/` later is a rename, and the note is here
so the next person does not read the placement as a decision about ownership.

**The record.** Every arrival writes one row, whether or not it is a duplicate. The row has thirty-one keys and
`build_record` in `services/intake/provenance.py` is the definition of all of them; the table below is the ones
a reader asks about, with the column names a query has to use.

| Field | What it is, and why |
|---|---|
| `intake_id` | Stable id, the primary key |
| `received_at` | Arrival time as the mail system states it, not as we noticed it |
| `observed_at` | When intake saw it. The two differ and an auditor asks about the first |
| `channel` | `graph` or `folder` |
| `mailbox`, `folder` | Which mailbox, which mail folder. One credential reads one mailbox and the record says which |
| `message_id` | The RFC 5322 `Message-ID`. The identifier a mail administrator can search for |
| `provider_id` | Graph's immutable message id, when the channel has one |
| `sender_from`, `sender_envelope`, `sender_reply_to` | Header `From`, the envelope sender and `Reply-To`, as three columns. The envelope sender is the one that authorised the delivery and the header one is the one a person sees, so both are recorded because they can differ |
| `auth_spf`, `auth_dkim`, `auth_dmarc` | The three results parsed out of `Authentication-Results`, one column each |
| `verdict` | `trusted` or `marked`. Marked means the sender could not be verified |
| `content_sha256`, `bytes`, `relpath` | The hash of the deposited bytes, the size, and where it landed under the documents root |
| `raw_message_sha256` | The hash of the message as received, so the envelope can be proved later without keeping it |
| `duplicate_of` | The earlier `intake_id` when this content has been seen before, otherwise null |
| `content_flags` | Instruction attempts and the risk markers that fired, by name |
| `requires_review` | Always true. Intake never approves anything |

**Intake has no approval capability at all.** `requires_review` is a constant rather than a decision, and
`auto_approved` is not a field. A component that can approve its own intake is a component that can be
talked into approving by the thing it just read, which is what `CON-INJECT-001` is about.

**Where the deposit lands.** `<documents_root>/<root>/intake/<YYYY-MM-DD>/<message-sha8>/<filename>`. The
first path component is the access root, so an invoice deposited under `company` is readable by everyone
with the `company` label, which is the label a client expects an invoice to carry. The file is written under
a `.part` name and renamed into place, because the ingestion worker walks the same tree and `.part` is
already in its list of suffixes it will not read. A half-written PDF never becomes a document.

**These commands need a configuration that this step does not create.** `/etc/oi-intake/intake.yaml` and the
credential beside it come from manual 13l's install section, which is the page a deployment is set up from; on
a box without them the first command refuses with `no-config: no configuration at …` and exits 1, which is the
guard working rather than a broken deployment. Manual 13l.7 has the five commands that need neither a tenant
nor a configuration, and they are the ones to run first on a box with no client.

**Commands.**

    cd /opt/oi-core

    # What the configuration must satisfy, before anything is fetched.
    ~/venv/bin/python services/intake/intake.py check-config --config /etc/oi-intake/intake.yaml

    # Cut every cord and see whether anything still resolves to us.
    ~/venv/bin/python services/intake/intake.py cord-check --config /etc/oi-intake/intake.yaml

    # Intake's own table. Idempotent, like the audit sink's migrate.
    # **In a container on ai-net, not from the host.** compose assembles DATABASE_URL for containers and
    # the renderer does not write it, so `--dsn "$DATABASE_URL"` in a host shell passes an empty string and
    # psycopg refuses it. `eval` is the image on this box carrying a YAML parser and the Postgres driver;
    # intake has its own Dockerfile and no compose service yet.
    oi run --rm --entrypoint sh -v /opt/oi-core:/w:ro -w /w eval -c \
      'python /w/services/intake/intake.py migrate --dsn "$DATABASE_URL"'

    # One poll. Reads, deposits, records, and exits. This is what the timer runs. Needs the client's four
    # paths mounted: the config and credential under /etc/oi-intake, intake.documents_root, intake.state_dir.
    oi run --rm --entrypoint sh -v /opt/oi-core:/w:ro -w /w \
      -v /etc/oi-intake:/etc/oi-intake:ro \
      -v <intake.documents_root>:<intake.documents_root> \
      -v <intake.state_dir>:<intake.state_dir> \
      eval -c 'python /w/services/intake/intake.py fetch --config /etc/oi-intake/intake.yaml'

    # The daemon: poll on an interval, write a heartbeat on every success. The same poll in a loop; the
    # timer runs `fetch`.
    ~/venv/bin/python services/intake/intake.py watch --config /etc/oi-intake/intake.yaml

    # The second channel: a shared folder, polled, with a settle check.
    ~/venv/bin/python services/intake/intake.py watch-folder --config /etc/oi-intake/intake.yaml

    # What the credential can actually reach, rather than what it was granted.
    ~/venv/bin/python services/intake/intake.py enumerate-reach --config /etc/oi-intake/intake.yaml

    # The heartbeat and the volume floor, evaluated now.
    ~/venv/bin/python services/intake/intake.py monitor --config /etc/oi-intake/intake.yaml

    # The recorded hash against the ingest manifest's hash for the same file. Same container, same reason
    # as `migrate`.
    oi run --rm --entrypoint sh -v /opt/oi-core:/w:ro -w /w \
      -v /etc/oi-intake:/etc/oi-intake:ro eval -c \
      'python /w/services/intake/intake.py verify-manifest --config /etc/oi-intake/intake.yaml --dsn "$DATABASE_URL"'

    # Everything above, without a database and without a tenant.
    ~/venv/bin/python tests/fixtures/intake_acceptance.py

**Expected.** `check-config` prints `ok` and the five components with their owner. `cord-check` prints
`no cord` and exits 0. **`fetch` prints one JSON object for the poll, not a line per message**, with these
keys: `channel`; `messages_seen` and `no_attachment`, which count messages; `attachments`, `deposited`,
`duplicates`, `already` and `marked`, which count attachments; and `last_error`, which is null on a clean poll
and otherwise the classified failure the heartbeat recorded. `deposited` writes a row and deposits a file,
`duplicates` writes a row with `duplicate_of` and deposits nothing, `already` writes **no row at all** (the
same attachment on a message read on an earlier poll), and `marked` is a count of a sender verdict on the
deposited rows rather than a fourth outcome. `enumerate-reach` prints a probe table where exactly one row is
`reached`. `monitor` prints `no alert` when the heartbeat is fresh and the volume is inside the floor. The
acceptance probe prints ten `PASS` lines, one per register row, and exits 0.

**Verify.** The ten rows this step owns are CON-OWNED-001, CON-LEAST-001, CON-INTAKE-001, CON-DUPE-001,
CON-INJECT-001, CON-SENDER-001, CON-FOLDER-001, CON-SILENT-001, SEC-CON-001 and SEC-INTAKE-002. The test
command and its output are below.

    ~/venv/bin/python -m pytest tests/ -q
    ~/venv/bin/python tests/fixtures/intake_acceptance.py

**If it breaks.**

- **`check-config` refuses the permission as `application`**: this is the guard working, not a bug. Application
  `Mail.Read` is refused because its RBAC scope is additive and a leftover Entra grant makes it a no-op. Change
  the app registration to delegated `Mail.Read`.
- **A shared mailbox is refused with "cannot sign in"**: also the guard. The client needs a licensed regular
  mailbox, and `CON-OWNED-001` carries the note explaining why this is a purchase rather than a setting.
- **`fetch` prints `AADSTS65001`**: consent was revoked. The token request failed cleanly and the alert says
  re-approval, not the code.
- **`fetch` returns zero messages and no error**: this is the failure the connector cannot see. Go to 6.4a.
- **The hash on the record and the hash in `ingest_manifest` differ**: the deposit was rewritten after the
  record was written, or the record was written before the rename. The record is written after `os.replace`
  and the bytes are hashed from the file itself.
- **`verify-manifest` reports a relpath that will never be ingested**: the attachment's suffix is not one the
  ingestion worker accepts. Intake records `unreadable_by_ingest` on the row rather than reporting a deposit
  that never becomes a document.

**One line of this run goes to stderr and the paste below is stdout.** It reads
`mail failure: Authentication to ap@acmetool.example is working but the mailbox cannot be read. Check that its
licence is still assigned, because a mailbox without one returns exactly this.` That is `CON-SILENT-001`'s
pulled-licence scenario reporting itself through `intake.py`, it is the feature working rather than a failure,
and the run still ends `10 PASS, 0 FAIL` and exits 0. `2>&1` puts it in the block if you want to see it where it
happens, and a reader who meets it without this paragraph will debug a credential that is fine.

**As it ran, on 2026-10-16.** Recorded on the deployment at `/opt/oi-core`, following manual section 13l.7 word
for word. There is no client tenant and no database on this machine, so what follows is the acceptance run
against the simulated tenant and the five commands that need neither. **`~/venv/bin/python`, not a bare
`python3`**: the system Python on the reference machine has no pytest, and the acceptance script's own
dependencies come from `requirements-dev.txt`.

```
$ ~/venv/bin/python tests/fixtures/intake_acceptance.py

C5-C intake acceptance
  started 2026-10-16T18:59:40+00:00
  **the tenant is simulated.** tests/fixtures/intake_env.py implements the delegated-permission
  semantics from Microsoft's documentation. No Microsoft tenant is contacted, so the rows below
  are proved against the contract rather than against the service. See the runbook for which
  part of each row needs a real tenant.

PASS  CON-OWNED-001   five components owned by {client}; 6 request(s) to graph.microsoft.com,login.microsoftonline.com; a host we control refused before a socket
PASS  CON-LEAST-001   6 probe(s), reached: own_mailbox; granted scopes Mail.Read,offline_access
PASS  CON-INTAKE-001  4 row(s) with sender, message id, arrival time and a 64-character hash; 4 verified equal to the ingestion worker's own sha256_of of the deposited file
PASS  CON-DUPE-001    the same bytes twice: 1 deposited, 1 recorded as a duplicate pointing at in-b35b9d3c8623b28d7e6a; 1 copy on disk, no second job notice
PASS  CON-INJECT-001  signals act_as_system,bank_details_changed,conceal,exfiltrate_document,ignore_previous; 0 write request(s) to the mail API; requested actions {()}; the instruction text is not in the row
PASS  CON-SENDER-001  known supplier trusted; unknown sender marked; envelope bounce@mailer.example vs header billing@supplier.example -> marked; DMARC recorded on all three; every row requires review
PASS  CON-FOLDER-001  a second process copied a file into the share; the connector took it without restarting (1 row); 0 inotify descriptor(s) held while it ran
PASS  CON-SILENT-001  revoked scope: no error logged (True), alerts volume_floor; pulled licence: exit 2, kind mailbox_not_readable, alerts mailbox_not_readable,volume_floor; diverted mail: exit 0, no error logged (True), alerts volume_floor
PASS  SEC-CON-001     0 key(s) in the service; a rotation took effect on the next poll with no restart (not-a-real-secret -> the-new-secret); the value is not in the record; mode 0644 refused; the api container declares no access to the store
PASS  SEC-INTAKE-002  a second mailbox, the directory, the profile and another service refused; a write method refused in code; and the enumeration caught a tenant-wide credential (reached own_mailbox,second_mailbox,directory,profile,other_service)

10 PASS, 0 FAIL
```

```
$ ~/venv/bin/python -m pytest tests/test_intake_*.py -q
90 passed in 19.85s

$ ~/venv/bin/python -m pytest tests/ -q
884 passed, 23 skipped in 32.23s
```

**The 23 skips are not intake's, and none of them is a silent pass.** Fourteen are
`tests/test_workflow_chain.py` and five are `tests/test_store_parity.py`, both of which need a real Postgres
because the property they check is a property of a transaction. Two are `tests/test_audit_statement.py`, which
needs the audit tool's Postgres driver. One is `tests/test_format_coverage.py`, which runs inside the parser
image because it needs docling. One is `tests/test_manifest_schema.py`, which skips without jsonschema, and the
deployment's venv does not carry it. Every intake test runs on a machine with no database and no tenant, which
is the point of the simulated one.

**And the ten rows can fail.** Each control was broken deliberately, one at a time, and the test for its row
was run against the broken build. Ten of the eleven mutations were caught by the intended assertion; the
eleventh, adding a watcher import, failed at collection instead, so it was proved separately by adding a
module that imports `inotify` and watching the assertion fire. Two mutations were caught by more than one
test, which is the belt and braces on the rows that carry two properties.

#### 6.4.a The two failures that leave a working credential

**Goal.** The connector notices the two ways intake stops that produce no error at all: a break that leaves
the credential working and the queue empty, and a poll that never happens because the connector went away.

**Why a heartbeat rather than error logging, and why polling rather than a filesystem watch.** Both are the
same argument about a signal that is absent rather than wrong.

**Error logging cannot see a silent failure.** A revoked scope and a mail rule diverting invoices both leave
a credential that authenticates and a queue that is empty, and both look exactly like a quiet week. Error
logging reports nothing in either case, which is why every "we log exceptions" answer fails this requirement.
**The pulled licence is the half-way case and is worth separating**: it answers 403 with a healthy token, so
the classifier does name it and the volume floor catches it as well. A reader who took all three for silent
would read the classifier firing as a defect. The connector writes a heartbeat on every successful poll, and a separate
check alerts when the last success is older than the interval allows. That catches the failures that produce
no error, because the signal is the absence of a success rather than the presence of a failure.

**A volume floor catches the third one.** A licence pulled from the mailbox, or a rule that quietly diverts
invoices to a subfolder, both leave a connector that polls fine and reads nothing. No invoice for longer than
the quiet period is a fault rather than a slow month, and seven days at an 80 percent decline is the second
signal. The floor is configuration, because a plumber in February and a landscaper in February have
different rhythms.

**The folder connector must poll.** `inotify(7)` says it plainly: it "does not catch remote events that occur
on network filesystems", and applications "must fall back to polling the filesystem to catch such events".
A scanner writing into a share, or a person dragging a file onto a mapped drive, produces no local event on
the machine doing the reading. An inotify connector therefore works on the developer's laptop and misses
every real arrival, and the test that writes a file locally and watches a local path passes on it. The
connector polls, and it decides a file is finished by seeing the same size and mtime on two consecutive
scans rather than by hearing about a write.

**The alert goes to the client's address.** If alerts route only to Office Inference, monitoring quietly
reintroduces the dependency the design exists to remove. Recipients are configuration, and
`check-config` refuses any recipient whose domain is not the client's, so the rule is enforced rather than
remembered.

**Commands.** Covered by 6.4. The two that matter here:

    ~/venv/bin/python services/intake/intake.py monitor --config /etc/oi-intake/intake.yaml
    ~/venv/bin/python services/intake/intake.py watch-folder --config /etc/oi-intake/intake.yaml

**Expected.** `monitor` prints `no alert` on a healthy deployment, and one line per alert naming the failure
in a sentence rather than a code. On a stale heartbeat it prints `stale_heartbeat` with the age in minutes
and the address the alert was sent to. On a volume floor breach it prints `volume_floor` with the count and
the window. `watch-folder` prints **one JSON object per scan**, so a scan that takes nothing still prints a
line and two of the three lines in the paste below describe no take. A file that is still being written is
named in none of them, which is the half that matters: the settle check shows up as an absence.

**Verify.** `CON-SILENT-001` is proved by three simulated failures in
`tests/test_intake_silent.py`, each of which leaves the credential working and the queue empty.
`CON-FOLDER-001` is proved by `tests/test_intake_folder.py`, which starts the connector as its own process,
writes a file from a second process, and asserts the connector holds no inotify file descriptor while it
runs.

**If it breaks.**

- **`monitor` says `no alert` while the queue is empty**: the quiet period is longer than the outage. That is
  the configuration being wrong rather than the check, and it is worth checking against the client's real
  invoice rhythm.
- **An alert names Office Inference as the recipient**: `check-config` should have refused it. The recipient
  list is read from the client's configuration file, so the fix is in that file rather than in the code.
- **`watch-folder` picks up a file that is still being written**: the settle check is comparing one scan
  rather than two, or it is comparing mtime alone on a filesystem whose mtime granularity is a second.
- **`watch-folder` misses a file copied in with an old timestamp**: it is using mtime as a watermark. It must
  decide by content hash, because `rsync` and a scanner both preserve or reset mtime and neither is a fact
  about arrival.

**As it ran, on 2026-10-16.** The folder channel is the one that needs neither a tenant nor a database, so
this is the whole thing running for real: three scans, a file that arrives during the second, and the settle
check visible in the output.

```
$ ~/venv/bin/python services/intake/intake.py watch-folder --config /tmp/oi-demo/intake.yaml --iterations 3 --poll-seconds 1
{"duplicates": [], "scanned": 1, "skipped": [], "taken": [], "unreadable": 0}
{"duplicates": [], "scanned": 1, "skipped": [], "taken": [{"bytes": 84, "content_sha256": "1ccf744751ce302df4a1219520d9546f14cd45f1b32bf1dea4f2b4be0491be54", "intake_id": "in-86fdb4a2dc4bdb9f2daf", "relpath": "demo-invoice.pdf"}], "unreadable": 0}
{"duplicates": [], "scanned": 1, "skipped": [{"content_sha256": "1ccf744751ce302df4a1219520d9546f14cd45f1b32bf1dea4f2b4be0491be54", "path": "/tmp/oi-demo/share/demo-invoice.pdf", "reason": "already taken"}], "taken": [], "unreadable": 0}
```

**Read the three lines in order, because they are the settle check.** The first scan sees the file and waits.
The second takes it, because size and mtime agreed with the first. The third finds it already taken by content
hash and leaves it where it is.

The silent failures, from the acceptance run above, one line per scenario (wrapped here to fit the page,
verbatim apart from the wrap):

```
PASS  CON-SILENT-001  revoked scope: no error logged (True), alerts volume_floor;
                      pulled licence: exit 2, kind mailbox_not_readable, alerts mailbox_not_readable,volume_floor;
                      diverted mail: exit 0, no error logged (True), alerts volume_floor
```

**`no error logged (True)` is the finding.** Two of the three scenarios left the credential working, the read
succeeding and nothing in the log, and the volume floor caught both.

#### 6.4.b Cutting the cord

**Goal.** Prove, on a running deployment, that nothing in intake resolves to infrastructure Office Inference
operates.

**Why this is a step of its own.** `BIZ-INDEPENDENT-001` says the client may take the system over completely
and we are out of the picture, and its test is that you can cut every cord to us and the acceptance still
passes. Two versions of this design failed it before a line was written: one put the client's invoice address
on infrastructure we would operate, and one put the OAuth application registration in our identity tenant.
Both would have passed an offboarding rehearsal and both are rebuilds rather than edits once a client has
paid. The constraint is cheap here and expensive later, so it is checked by a command that runs on any
deployment at any time.

**The five components, and the owner each must name.**

| Component | Where it lives | What the check refuses |
|---|---|---|
| The intake address | The client's Microsoft 365 tenant | Any address whose domain is not in `client.domains` |
| The mailbox | The client's Exchange Online | A shared mailbox, or a mailbox with no licence |
| The app registration | The client's Microsoft Entra ID | A `tenant_id` that differs from the deployment's tenant, or `registered_by` anyone but the client |
| The consent grant | The client's Microsoft Entra ID | A grant recorded against another tenant |
| The credential | The client's own secret store | A path outside the client's estate, or a file another account can read |

**And the network.** Every request intake makes goes through one function in `net.py`, which holds the host
allowlist. There are exactly two entries, `login.microsoftonline.com` and `graph.microsoft.com`, and the
function refuses anything else before a socket is opened. That makes "does anything phone home" a property
of one function rather than a claim about the whole codebase, and it is why the cord check can be a command
rather than an audit.

**Commands.**

    ~/venv/bin/python services/intake/intake.py cord-check --config /etc/oi-intake/intake.yaml
    ~/venv/bin/python services/intake/intake.py enumerate-reach --config /etc/oi-intake/intake.yaml

**Expected.** `cord-check` prints the five components with `client` beside each and then `no cord` and exits
0. `enumerate-reach` prints one row per probe and there are six: the client's own mailbox `reached`, and then
`refused` for a second mailbox, the directory, the signed-in user's profile, a different Microsoft service, and
any other host, which the allowlist refuses before a socket is opened rather than the tenant refusing after it.

**Verify.** `CON-OWNED-001` and `SEC-CON-001` are proved in `tests/test_intake_ownership.py`, including a
negative control that moves each of the five components to us in turn and asserts the check fails.
`CON-LEAST-001` and `SEC-INTAKE-002` are proved in `tests/test_intake_reach.py`, which asserts what the
credential reached rather than the permission it was granted.

**If it breaks.**

- **`cord-check` refuses a component we did not expect**: read which one and why. Every refusal names the
  component and the value, and the fix is in the client's configuration or in their tenant rather than in
  the code.
- **`enumerate-reach` shows a second mailbox reached**: the app registration has an application permission,
  or a delegated permission was granted tenant-wide by a grant that is not the one the deployment recorded.
  Both are the failure `CON-LEAST-001` exists for, and the enumeration is why it was caught rather than
  assumed.
- **A request to a host that is not Microsoft's is made**: it went around `net.py`. That is the only way to
  reach a third host, and the test that asserts the allowlist is what keeps it that way.

**As it ran, on 2026-10-16.** Cord-check against the example configuration, on the development checkout. It
needs no tenant and no database, and it is the command `BIZ-INDEPENDENT-001` turns on.

```
$ ~/venv/bin/python services/intake/intake.py cord-check --config /tmp/oi-demo/intake.yaml
ok   Acme Tool & Die (acme-2026)
     ok   intake address     client   ap@acmetool.example
     ok   mailbox            client   ap@acmetool.example (regular_user, exchange_online)
     ok   app registration   client   AP Invoice Intake in tenant 00000000-0000-0000-0000-000000000000
     ok   consent grant      client   granted by client in tenant 00000000-0000-0000-0000-000000000000
     ok   credential         client   /tmp/oi-demo/credential.json
     ok   host allowlist      login.microsoftonline.com, graph.microsoft.com
     ok   a host we control is refused before a socket is opened
     ok   no licence server, update check or telemetry in the configuration
no cord
```

**The two lines after the five components are the ones that matter**, which are the seventh and eighth of the
block: the fourth and fifth are two of the components, and this sentence said otherwise until round three's
fidelity checker counted them against the paste above. The host allowlist is asserted rather than described, and
then exercised: the command makes a request to a host we control and reports the
refusal. A control proved by refusing something is a control; a control described in a paragraph is a
paragraph.

`enumerate-reach` needs the client's mailbox, so it was run inside the acceptance probe rather than here. Its
result, from the same run, wrapped to fit the page:

```
PASS  SEC-INTAKE-002  a second mailbox, the directory, the profile and another service refused; a write method
                      refused in code; and the enumeration caught a tenant-wide credential
                      (reached own_mailbox,second_mailbox,directory,profile,other_service)
PASS  CON-LEAST-001   6 probe(s), reached: own_mailbox; granted scopes Mail.Read,offline_access
```

**The words after "and" are the half worth having.** A tenant-wide credential was handed to the same
enumeration and it was caught, which is what makes the one-mailbox result on the left evidence rather than a
line that would print the same thing whatever the credential could reach.

## Phase C6 — Agent runtime and MCP gateway, as built

C6 is built and verified. The gate and evidence are in docs/reviews/ws-gate/ and the MCP gateway review is in
docs/reviews/mcp-gateway/. The client-tenant evidence is in docs/reviews/mcp-owned/.

### C6.1 The durable agent runtime

The runtime is one agent loop with tools, an attempt and time budget, a child-token budget, context trimming and
startup recovery. Every step is persisted before it runs, so a restart resumes rather than repeats. The approval
gate holds a tool call, writes the pending action to the job row, and records the approval as a chain row. The
runtime records only the held tool call in the approved delta, which is the fix that avoided Bedrock rejecting the
turn after approval with a missing toolResult block.

The runtime is covered by tests/test_agent_runtime.py and the acceptance run in docs/reviews/ws-acceptance/.

### C6.2 The MCP gateway

The gateway is services/mcp_gateway/gateway.py. It loads a tool registry from the manifest, resolves a session,
refuses a caller-supplied role, enforces the tool's allowed roles and data classes, applies the egress gate,
reads the connector credential from a mode-600 file on every call. Refusals on the build box are written to the chain. The tenant gateway has no audit database route, so its refusals return to the API but are not in the Office chain; that is a known gap.

The registry is rendered from the manifest's mcp.tools block into rendered/mcp_tools.json. An undeclared tool is
refused by name. The credential is never in the API environment, the gateway image or the repository.

    # the gateway tests, from a full repository checkout with the dev dependencies
    # (the deployed /opt/oi-core tree omits tests and has no pytest)
    python3 -m venv ~/venv
    ~/venv/bin/pip install -r requirements-dev.txt
    ~/venv/bin/python -m pytest tests/test_mcp_gateway.py -q

    # live refusal evidence from 2026-10-23
    # no session: row 1070, invalid session: row 1071, authority claim: row 1072,
    # undeclared tool: row 1073, data class: row 1074, role policy: row 1090,
    # expired session: row 1091

### C6.3 The client tenant

The production gateway and connector are in badj.ai account 426, instance i-0b6aa00095ae40e54. The gateway
listens on loopback 8080, the connector is the systemd unit client-connector on loopback 8094, Caddy owns public
80 and 443. The connector sends through SES as awstest@officeinference.com; Mailpit on loopback 1025 is the
capture fallback.

    # tenant health
    curl -s https://3-85-130-96.sslip.io/health

    # restart-safe checks
    ssh -i ~/ai-stack/ai-stack-key.pem ubuntu@3.85.130.96 'systemctl is-active client-connector; docker ps --format "{{.Names}} {{.Status}}"'

The connector sends through SES to the verified awstest@officeinference.com identity. The badj SES account is
still in the sandbox, so arbitrary recipients need production access. Mailpit remains a capture fallback.

### C6.4 Signed session assertions

The tenant has no database route back to account 878. The API mints a short-lived HMAC assertion containing the
session subject, role and expiry, signed with OI_MCP_SESSION_SECRET on the API and MCP_SESSION_SECRET on the tenant gateway. The gateway verifies the signature and
expiry. It still refuses a request that supplies its own role.

The assertion is an identity claim, not a permission claim: the tool's role policy and the data-class policy are
still enforced after it. The secret is in the mode-600 rendered env on the build box and in the gateway
environment on the tenant. Rotate it with the same procedure as any shared secret and update both sides.

### C6.5 The egress gate on a tool call

send_email consults the manifest's access.egress.frontier_classes before the gateway. Denied: the job is
refused before it exists, the connector sees nothing, and the audit row says egress_allowed false. Allowed: the
body is redacted when access.egress.redaction is on, the gateway is called with egress_approved true, and the
audit row records data_class, egress_allowed and the redaction counts.

    # lane-off evidence: regulated job refused before creation, docs/reviews/mcp-egress/lane-off.json
    # lane-on evidence: job b36f84f12f7d428fa205c844afe319e0 succeeded, audit row 1148 sent true

The first lane-on attempt succeeded while the send was denied because the tool gate read the env and the job
boundary read the manifest. The gate now reads the manifest first. That is why the audit row exists at all.

## Phase C7 — Security, as built

C7 is built and verified. Evidence is in docs/reviews/sec-auth/, sec-least/, sec-secret/, sec-tls/ and aud-lock/.

### C7.1 Authenticated session, not a caller role

    # no token -> 401, row 1218
    # valid session plus role: executive -> 403, row 1219

The no-session refusal used to return without writing anything. The /ask path now records the attempt before it
returns. The forged-role path records the session role and the attempted role.

### C7.2 Least-privilege support role

The role office-inference-support in account 426 trusted only Office Inference's DSHAgentRole, allowed SSM and no
S3. Assuming it and reading a real object in disk-ingest-documents-878451098165 returned AccessDenied. The role
and policy were then deleted, and AssumeRole returned AccessDenied. Access is revocable from the client account.

### C7.3 Secret rotation from Secrets Manager

The connector token is in Secrets Manager in account 426. Rotation does not rebuild an image:

    ssh -i ~/ai-stack/ai-stack-key.pem ubuntu@3.85.130.96 'cd /home/ubuntu/client && sudo AWS_DEFAULT_REGION=us-east-1 python3 rotate_connector_secret.py       --secret-id oi-core/connector-token       --token-file /home/ubuntu/client/connector.token       --env-file /home/ubuntu/client/connector.env --service client-connector'

The gateway reads the new file on the next call. The connector restarts through systemd. Old token 401, new token
200, and job 4795a9935e1744a481410971e496e40b sent true on row 1228.

### C7.4 TLS

Caddy terminates TLS for https://3-85-130-96.sslip.io. The gateway listens on loopback only. Security group
ingress is 22, 80 and 443; port 8080 is closed externally. HTTP returns 308 to HTTPS. Job
57309daac87244d9acea35f4f65dbffb went through the HTTPS gateway; its final state was failed verification, but
act row 1242 records sent true.

    curl -sS https://3-85-130-96.sslip.io/health
    curl -sS -I http://3-85-130-96.sslip.io/health

### C7.5 Object Lock and the contract

The audit bucket oi-core-audit-dev has Object Lock enabled with default GOVERNANCE mode for 90 days. The
retention clause in docs/contracts/audit-evidence-retention.md names the same mode and period, and limits bypass
to the account administrator. AUD-LOCK-001 is a consistency check; counsel review is BIZ-LEGAL-001 and
BIZ-CONTRACT-001.

## Phase C8 — Operations, partly built

Six C8 rows are verified: audit chain version 4, image scanning, drift, the incident tabletop, backup and the
restore/RTO drill. The load test failed its latency target and stays open. The rest are open and need a decision
or an external party; docs/current-deployment.md names the blocker for each.

### C8.1 Audit chain version 4

Chain version 4 covers the approval digest, the prompt digest and the completion digest. Migration
027_the_prompt_and_completion_are_in_the_chain.sql added it without rewriting older rows. The export carries
prompt_sha256 and completion_sha256.

    # clean export
    docker exec api python /app/audit.py export --out /tmp/chain
    docker exec api python /app/audit.py verify /tmp/chain/audit-events.jsonl

    # For a version 4 row, editing approval_sha256, prompt_sha256 or completion_sha256
    # makes the verifier fail with "row_hash does not match the row contents".
    # Version 3 rows cover only the approval digest.

### C8.2 Image scanning

scripts/scan_images.sh scans every deployment image with Trivy and writes one JSON report per image plus a
summary. A systemd timer, oi-image-scan.timer, runs it weekly. LiteLLM 1.104.2 is recorded beside its compose pin
and confirmed in the running container. The current report includes critical findings in litellm, parser and
postgres; they are recorded, not waived.

    cd /opt/oi-core
    sudo bash scripts/scan_images.sh /var/log/oi-image-scan
    systemctl list-timers oi-image-scan.timer --no-pager

### C8.3 Drift reporting

scripts/deployment_state.py reports the manifest hash and every compose image pin. Keep the expected state with
the release and compare:

    cd /opt/oi-core
    python3 scripts/deployment_state.py --compose deploy/office/compose/docker-compose.yml       --manifest rendered/manifest.json --compare deploy/office/expected-state.json

Changing a pin on the box makes the compare exit 1 with the differing service. The manual's section 26 has the
operator form.

### C8.4 Incident response

docs/runbooks/incident-response.md is the runbook. docs/reviews/ops-incident/tabletop.md is the public-artifact
tabletop: containment before evidence export, first client notice at 70 minutes against a 72-hour clock, second
update at 24 hours. Two gaps are recorded: no acceptance probe for public-access settings, and no owning job id
on the artifact manifest.

### C8.5 Still open

    OPS-ALARM-001      needs a notification destination and a confirmed subscription
    OPS-UPDATE-001     needs release signing and a rollback target
    OPS-SUPPORT-001    needs approval to remove SSH and use SSM only
    OPS-COST-001       needs the report shape and Marketplace line access
    QLT-GOLDEN-001     needs a vertical and sixty client-approved questions
    QLT-ACCEPT-001     needs a fresh deployment
    QLT-LOAD-001       needs a concurrency target and latency budget
    BIZ-*              counsel, insurance, contracts, on-call, subprocessors, pricing

## Future phases

### Phase 9 — Office appliance  *(outline)*

Backup, restore drill, signed updates with rollback, drift reporting, support tunnel. The drift reporting and the
SSM role exist. The backup and restore drill now exist; the signed update and rollback do not.

### Phase 10 — Voice  *(outline)*

LiveKit cascade: speech to text, model, text to speech, with the transcript audited.

### Phase 11 — Flagship demo  *(outline)*

The denial appeal agent end to end.


---


### 6.7 The answer step searches: enabling, measuring and rolling back the tool loop

The answer step is one path: retrieval seeds it, the model may search again and read around a hit, and the
answer is verified against the evidence it gathered. It is not enabled by a flag; the deployment chooses an
answer profile, which sets the evidence budget.

**Set the profile on a deployment.**

    cd /opt/oi-core
    grep -q '^OI_ANSWER_PROFILE=' rendered/env && sed -i 's/^OI_ANSWER_PROFILE=.*/OI_ANSWER_PROFILE=accuracy/' rendered/env || echo 'OI_ANSWER_PROFILE=accuracy' >> rendered/env
    docker compose -f deploy/office/compose/docker-compose.yml --env-file rendered/env up -d --force-recreate api
    docker exec api printenv OI_ANSWER_PROFILE        # expect: accuracy
    docker logs api 2>&1 | grep 'tool-loop'           # one line per answer after the next question

**Choose the model.** The loop is provider-agnostic, but tool calling quality is not. The measured best is Claude
Haiku 4.5 through Bedrock:

    # manifests/dev-cloud.yaml: analyst.frontier: bedrock/us.anthropic.claude-haiku-4-5-20251001-v1:0
    python3 scripts/render_manifest.py --manifest manifests/dev-cloud.yaml --out rendered
    docker compose -f deploy/office/compose/docker-compose.yml --env-file rendered/env up -d --force-recreate litellm api

Prerequisites for Claude on Bedrock in this account, all verified on 2026-10-18: the Anthropic use-case form is
submitted, and `oi-core-dev-bedrock-invoke` (v5) allows the Claude inference profiles and the foundation-model
ARNs in us-east-1, us-east-2 and us-west-2. Check the resolved model on a real answer:

    docker compose -f deploy/office/compose/docker-compose.yml --env-file rendered/env \
      run -T --rm --no-deps --entrypoint python -v /opt/oi-core:/w -w /w \
      -e OI_API_URL=http://api:8088 -e ACCESS_REGISTRY=/w/rendered/access.json \
      eval /w/tests/fixtures/ask_client.py -q "What is the mileage rate?" --json | grep resolved_model

**Measure it.** Same forty questions, five runs minimum, same scorer, from the deployment root:

    scripts/run_answer_suite.sh 5 /tmp/oic-suite

Five is the minimum because five identical runs of one configuration have spanned 33 to 45 of 52. Read the range,
not one number. Expected with the measured configuration (six steps, ten calls, lookup off): 40 to 42 mean, with
individual runs from 33 to 45. Single-shot Nova is a mean of 29; single-shot Claude Haiku a mean of 35. Check the
analyst team ceiling first: a budget-exhausted lane reports every answer as degraded with reason budget, and the
gateway numbers in the error are the way to tell that apart from an outage. The per-question evidence behind the
recorded five-run experiment is in `docs/reviews/experiment-answers-2026-10-19/`.

**The row query (the answer step's third tool).** `query_rows` returns the exact table rows a question
quotes instead of the passage around them, because the measured failure on exact-number questions was reading the
neighbouring line of a forty-row group. It is always offered; there is no flag to set.

Ask a question whose answer is a row and confirm the call reached the audit chain. Every tool call is a
`tool_call` row; the row query carries its own counts, so a refusal or a wrong row can be checked later:

    docker exec -i postgres psql \
      -U "$(grep -E '^POSTGRES_USER=' rendered/env | cut -d= -f2)" \
      -d "$(grep -E '^POSTGRES_DB=' rendered/env | cut -d= -f2)" -c "select id, outcome, \
      payload->>'candidate_chunks' as chunks, payload->>'rows_matched' as matched, \
      payload->>'rows_returned' as shown from audit_events where event_type='tool_call' \
      and tool='query_rows' order by id desc limit 5;"

The bounds are constants in `services/api/tool_loop.py`: 400 chunks scanned, 8 rows returned (4 by default), 600
characters a row, 6,000 characters a response. The query is one parameterised SELECT with the session's access
roots in its where clause, and an unscoped match that spans more than three documents is refused with examples
rather than anchored on the first. Nothing in the corpus, the vector store or the audit chain changes when the
profile changes. The five-run comparison of on against off is
`docs/reviews/query-tool-2026-10-19.md`.

**Test the streaming opt-in.** The old frames are unchanged unless the client asks for tools:

    ... ask_client.py -q "In the census table reporting a median household income of 59,473 dollars, what is
    the mean household income?" --stream --tools

Expect `start`, one or more `progress` frames, `sources`, `delta`, `done`. Without `--tools` the stream is the
single-shot stream it has always been.

**Run the acceptance probes** (each prints a ledger and exits non-zero if a right failed):

    for probe in interface_probe activity_probe; do
      docker run --rm --network ai-net --user "$(id -u):$(id -g)" -v /opt/oi-core:/w -w /w \
        --env-file rendered/env --entrypoint python oi-core/session:local /w/tests/fixtures/$probe.py
    done
    docker run --rm --network ai-net --user "$(id -u):$(id -g)" -v /opt/oi-core:/w -w /w \
      --env-file rendered/env -e AUDIT_TOOL=/w/services/audit/audit.py --entrypoint python oi-core/eval:local \
      /w/tests/fixtures/access_probe.py
    docker run --rm --network ai-net --user "$(id -u):$(id -g)" -v /opt/oi-core:/w -w /w \
      --env-file rendered/env --entrypoint python oi-core/session:local /w/tests/fixtures/stream_probe.py

**The fallback model (provider reliability).** A role may declare a second remote model the gateway fails over
to when the primary deployment fails, so a transient Bedrock failure answers from a second deployment instead of
degrading the question. It is a manifest field:

    # manifests/dev-cloud.yaml
    models:
      analyst:
        local: ""
        frontier: bedrock/amazon.nova-lite-v1:0
        fallback: bedrock/amazon.nova-pro-v1:0

Rendering it produces two things, and both are required: a second model group `analyst-fallback` and a
`litellm_settings.fallbacks` entry mapping `analyst` to it. The gateway does the failover once, so the
question path, the stream and every tool-loop call get it without a second implementation. **The retry count
stays zero**: the gateway makes one attempt per model and then moves on, so a provider that is already struggling
is not asked three times.

**Cloud builds only, and the validator enforces it.** On an office or hybrid build the primary can be the local
lane, and failing over to a remote model would send a client's content off the box on an undeclared egress path.
The lifecycle check reads the fallback too, because a deprecated fallback would stop working exactly when it is
needed.

**A chain, and the development deployment's is tracked.** The field also takes a list, tried in order. An
entry may carry an `@<region>` suffix: the same model, invoked through that Region's endpoint, which is the
cheapest recovery because the model, the answer quality and the price stay the same. The development
deployment's manifest is tracked, `manifests/dev-cloud-claude.yaml`, and it declares:

    models:
      analyst:
        frontier: bedrock/us.anthropic.claude-haiku-4-5-20251001-v1:0
        fallback:
          - bedrock/us.anthropic.claude-haiku-4-5-20251001-v1:0@us-east-2   # same model, other Region
          - bedrock/amazon.nova-pro-v1:0                                    # different model

The rendered gateway config gets `analyst-fallback` and `analyst-fallback-2` in that order.
**Hand-editing `rendered/litellm.yaml` for models is over**: the deployment renders from its tracked manifest, so
a re-render cannot silently revert the model or drop the chain. Render and recreate both processes:

    python3 scripts/render_manifest.py --manifest manifests/dev-cloud-claude.yaml --root /opt/oi-core
    docker compose -f deploy/office/compose/docker-compose.yml --env-file rendered/env \
      up -d --force-recreate litellm api

**Installation: the client's instance role must allow both Regions.** Both Claude entries are inference-profile
only, and a geographic profile is resolved from the Region the call is made in. The role needs the profile ARN in
the home Region and in the fallback Region, plus the foundation-model ARNs the profile routes to.
`oi-core-dev-bedrock-invoke` v6 is the shape to copy. A client installation that skips this step has a
second fallback entry that fails with AccessDenied at the moment the first one is needed.

**Measure it by failing the primary on purpose, not by waiting for a provider blip.** The fault stub in
`tests/faultinject/` speaks the provider's failure shapes, and it takes a default scenario from
`OI_STUB_FAULT` because a request that arrives through the gateway carries no per-request header:

    cd /opt/oi-core
    docker run -d --name oi-fault-stub --network ai-net -e OI_STUB_FAULT=unavailable \
      -v /opt/oi-core/tests/faultinject:/app:ro -w /app --entrypoint uvicorn \
      python:3.12-slim fault_stub:app --host 0.0.0.0 --port 8099
    cp rendered/litellm.yaml /tmp/litellm.yaml.before-fault
    # point only the primary at the stub: add  api_base: http://oi-fault-stub:8099  under analyst's
    # litellm_params, then:
    docker compose -f deploy/office/compose/docker-compose.yml --env-file rendered/env \
      up -d --force-recreate litellm
    scripts/run_answer_suite.sh 5 /tmp/oic-fault-chain          # every answer should come via the first entry
    # to exercise the second entry, also point analyst-fallback at the stub, then repeat
    # restore and recreate the gateway before doing anything else
    cp /tmp/litellm.yaml.before-fault rendered/litellm.yaml
    docker compose -f deploy/office/compose/docker-compose.yml --env-file rendered/env \
      up -d --force-recreate litellm
    docker rm -f oi-fault-stub

A run with the primary broken and the chain in place should produce no `provider_unavailable` degradations
and a score comparable to the healthy baseline: the first entry answers with the same model. Pointing the first
entry at the stub as well should still answer, from the second entry's different model. A run with no fallback
produces degraded answers with that reason, which is the control.
`docs/reviews/provider-fallback-2026-10-19.md` holds the measured comparison.

**Roll back.** Flag off for the loop, or the manifest for the model, then recreate only the containers that read
that configuration. Nothing in the corpus, the vector store or the audit chain changes either way. A fallback is
removed by deleting its model group and its `fallbacks` entry; the primary is untouched.

**Changing a model after installation.** Models are deprecated on the provider's schedule, and the client may
run the deployment without us. `scripts/model_admin.py` is the supported procedure:

    python3 scripts/model_admin.py list --manifest manifests/<deployment>.yaml
    python3 scripts/model_admin.py check manifests/<deployment>.yaml
    python3 scripts/model_admin.py set manifests/<deployment>.yaml --role analyst \
        --model bedrock/... --fallback bedrock/...@us-east-2
    # then render and recreate both processes, as above

It lists the models the lifecycle table knows and their status, refuses a change that would leave the manifest
invalid, writes only the models block, and never calls AWS. The table `data/model-lifecycle.json` is what makes
the check meaningful: refresh it from the provider API first (`docs/model-lifecycle.md`), or the tool will refuse
the new model as unknown. **Restart both processes.** The API reports the model from the manifest it loaded at
startup, so a gateway-only restart leaves the record disagreeing with the request.

**The honesty gate (C6).** A question phrased as an attribute of the company is refused when no retrieved passage
contains the subject phrase. It is a deterministic check in the answer paths, not a prompt rule: `finish` replaces
the JSON answer, the tool stream replaces it before emitting, and the single-shot stream refuses before calling the
model. It is part of the answer path, not a switch; the audit row carries `honesty_gate`.
`docs/reviews/honesty-gate-2026-10-19.md` holds the two rejected prompt iterations and the measurement.

**What to watch.** About 1.6 times the suite time (178 to 188 seconds against 110 single-shot) and two to three
times the model calls per answer. The development analyst ceiling is 100 USD for a 30-day period; a day of suite
runs costs about 0.4 USD per run. The step and call caps bound it; a question that hits the cap returns the single-shot answer rather
The large-table row cap still means rows beyond a data dump's indexed range are not reachable by the row
query; the second-corpus measurement records that limit and the marker chunk says it in the corpus.

## Standing rules

1. One phase at a time. Verify before moving on.
2. Every component gets its reasoning recorded here when it is added, not later.
3. Every image is digest-pinned with its version in a comment.
4. Nothing publishes a port except ingress. On the tenant, that is Caddy on 80 and 443; the gateway stays on loopback.
5. Stop the build box when you stop working. The credits are generous, not infinite.
6. The client tenant is not a build box. Leave it running while it serves the gateway, and do not expose 8080.
7. Mailpit is a capture fallback. The current connector sends through SES to awstest@officeinference.com; arbitrary recipients need SES production access.

---

## Appendix A — The office lane, as built on the retired t3.large

**Not this build.** This is the record of the local lane: the office build runs it, the cloud build
does not. It was built first, on the t3.large, before the cloud build existed, and it stays in this
document because the office offering is real work with real numbers in it.

Why it is fenced off: on 2026-10-10 these steps were followed on the cloud reference instance by
mistake. That rendered the office manifest and downloaded a 1.1 GB model the cloud build has no use
for. The lane is now switched off by configuration rather than by memory, but this record has to say
which build it belongs to. It did not, and that cost an hour.

The measurement worth keeping is in A.5: 1.7B Q4 on 2 vCPU predicted 7.2 tokens per second. That is
the argument for the GPU in the office offering, and it is not an argument about the cloud build at
all.

### The goal, as it was set at the time

**Goal.** Configuration rendered from the manifest, Postgres, a model server, and the gateway
running, with one model call travelling the whole path and landing in the database as an audit row.
The model server is llama.cpp on this box, and that single container is the difference between the two
builds.

### A.1 Render the manifest into configuration

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

**The cloud build renders a different manifest.** `manifests/dev-office.yaml` declares the office
profile, whose inference is the local lane: llama.cpp on this box, serving the GGUF from 1.2.
`manifests/dev-cloud.yaml` declares the cloud profile, where every role resolves to Bedrock and
there is no local lane at all. The same compose file serves both, because the renderer sets
`COMPOSE_PROFILES` only for a build that has local containers; a cloud render starts Postgres and
the gateway and leaves llama.cpp out. The cloud smoke test is C0 work and gets its own section once
it has run.

### A.2 Fetch the development model

    mkdir -p /opt/oi-core/models
    curl -L --fail -o /opt/oi-core/models/Qwen3-1.7B-Q4_K_M.gguf \
      https://huggingface.co/unsloth/Qwen3-1.7B-GGUF/resolve/main/Qwen3-1.7B-Q4_K_M.gguf
    ls -lh /opt/oi-core/models/

Expected: about 1.1 GB.

**Why:** this box has no GPU, so the local lane runs a 1.7B CPU model. It exists to prove the path,
not to be good, and every role points at it for now. When the GPU arrives, vLLM replaces llama.cpp
and the roles point at real models. Nothing in front of it changes, because the gateway is the only
thing that knows a model exists.

### A.2b Know this before you start: Docker creates missing mount paths as root

The Postgres service mounts `${OI_ROOT}/db/migrations`. **If that path does not exist, the Docker daemon
creates it, owned by `root`, not by you.** When this lane was built, the repository had no `db/`
directory yet, so the first `up -d` created it as root. `db/migrations` is tracked in git now, so that
particular directory arrives with the clone; `data/`, `models/`, and `exports/` are the ones to create
by hand, and `exports` matters most because the audit tool runs as your user and cannot write into a
root-owned directory.

The symptom appears later, and it looks unrelated: `git pull` in Phase 2 fails with
`unable to create file db/migrations/002_audit_events.sql: Permission denied`.

Check and fix before it bites:

    ls -ld /opt/oi-core/db /opt/oi-core/db/migrations 2>/dev/null
    sudo chown -R "$USER":"$USER" /opt/oi-core/db

`documents/` joins that list at step 3.7, and it bites the same way. The ingestion worker mounts
`${OI_ROOT}/documents` read-only, and Docker creates a missing bind path as root, so a first `up -d`
before the directory exists leaves a root-owned `documents/` that the operator cannot copy anything into:

    mkdir: cannot create directory 'documents/company': Permission denied

The fix is to create it as the operator, before compose ever sees it, and to put the corpus inside:

    cd /opt/oi-core
    mkdir -p documents/company
    python3 fixtures/corpus/fetch_corpus.py
    cp fixtures/corpus/*.htm fixtures/corpus/*.pdf documents/company/

If it has already gone wrong, the repair is the same as for `db/`:

    sudo chown -R "$USER":"$USER" /opt/oi-core/documents

**Do not chown the whole tree.** `data/` and `exports/` are root-owned on purpose, because Docker made
them for volumes.

**If a pull is interrupted halfway**, git leaves what it already wrote: tracked files modified and new
files untracked. Recover with:

    rm -rf services/audit          # or whatever the pull listed as untracked
    git checkout -- .
    git pull

Deleting those untracked files is safe: they are partial copies of what the pull is about to write
from the same commit.

### A.3 Start the stack

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

### A.4 Verify the gateway and the model

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

### A.5 Make a call through the gateway

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

### A.6 Find the audit row

    docker exec postgres psql -U oicore -d oicore -c '\dt' | tail -12
    docker exec postgres psql -U oicore -d oicore -c 'select count(*) from "LiteLLM_SpendLogs";'
    docker exec postgres psql -U oicore -d oicore -c \
      'select request_id, model, total_tokens, "startTime" from "LiteLLM_SpendLogs" order by "startTime" desc limit 3;'

Expected: your call, with the model, the token count, and a timestamp.

**Why this matters more than it looks:** the audit trail exists from the first call, without us
writing a line of logging code. Every model call is recorded with which key made it, which model
answered, how many tokens it cost, and when. Phase 2 adds our own append-only sink for actions and
approvals, but the model-call record starts here.

### A.7 Verification, which ends the phase

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

### 6.5 The live view, where the work is the progress indicator

**Goal.** Somebody who starts a job watches it in sentences, and a job that has stopped moving looks stopped
rather than busy. The screen is the record read back, so it cannot show anything that did not happen.

**Why the step list *is* the progress indicator and not a panel beside a spinner.** Luke Wroblewski shipped
agentic interfaces at Bench and at ChatDB and wrote down what failed. At Bench the team built several levels of
progressive disclosure with tool calls collapsed by default and his verdict was "it was too much", with the note
that showing less process by default "became even more important as the number of tools grew". At ChatDB the
working column collapses to a summary and a link once the result arrives. The principle he draws out of that is
this screen's entire design: "the agent's work should serve as a detailed progress indicator, instead of forcing
people to watch a spinner while things work"
(`docs/research/live-updates-transport-and-agentic-ux.md` §4.1).

So there is no spinner here. When the step list stops changing, the screen has stopped claiming progress, which
is what makes a stalled job visible instead of merely slow.

**Two levels of disclosure, and the second is one expand away.** Nielsen's rule is more restrictive than it
looks: "designs that go beyond 2 disclosure levels typically have low usability because users often get lost
when moving between the levels." The line between the two is drawn by consequence rather than by technical
depth: something belongs at level one if a person would need it to decide whether to trust the run or to stop
it.

| Level | What is on it | Why there |
|---|---|---|
| One, always visible | The step running now as a sentence in the client's language. The steps already done, with when the record says they happened. What happens at the gate, stated before the gate arrives. The stop control | These are the four things a person acts on |
| Two, one expand | The ordered event list with its sequence numbers, per-step timings, retries and typed failures | An auditor and a support call want it; nobody needs it to decide |
| Neither | Raw tool arguments and model reasoning. Any percentage | The firehose the brief rules out, and a percentage over an unknown duration that will stick or move backwards |

Nielsen is explicit that anything slower than ten seconds needs "a clearly signposted way for the user to
interrupt the operation", which is why the stop control is at level one and not in a menu. `POST
/jobs/{id}/cancel` already exists.

**A long wait is not a defect, and the interface must not imply that it is.** A CHI 2026 controlled experiment
(n=240, between-subjects) varied time-to-first-token at 2, 9 and 20 seconds with the streaming rate held fixed,
and the participants who got the 2 second answer rated the output **less thoughtful and less useful** than the
ones who waited. The qualitative data says why: a longer wait read as deliberation. So this screen never counts
down, never animates a bar, and states the work rather than the speed. The caution from the same page applies
and is worth keeping: that study measured a chat answer, not a monitored job, so applying it here is an
inference and not a replication, and the finding is not a licence to add latency.

**A heartbeat does not fix a stalled job, and this is the part that is easy to get wrong.** The route sends
`ping=15` to keep the connection open through a proxy, and a keep-alive comment is invisible to a reader by
design: the comment line is ignored before it reaches any handler. A screen that treats a ping as progress
therefore shows a live-looking job forever while nothing is happening. So the screen stamps the arrival of the
last **real** event, from the event log rather than from the socket, and after **45 seconds** without one it
stops claiming progress and says when the job last reported. Forty-five is three heartbeats: long enough that a
slow step does not flicker into stalled on a healthy run, short enough that nobody is left watching a dead one.

**Render the screen first and attach the stream second.** The connection is an optimisation over the record and
never the only copy of progress, which is the difference between a refresh that returns to the same run and a
tab that has to stay open:

1. `GET /jobs/{job_id}` returns the job with its steps, state and result. Draw the complete screen from that
   alone, before any stream exists.
2. Take the cursor from the same response, which publishes `events_through`.
3. Attach `GET /jobs/{job_id}/events?since=<cursor>`, or from the start when the response carries no cursor.
4. Key the view on the event's `seq`. Replay re-delivers frames, so duplicate delivery is normal here rather
   than an error, and a view that appended blindly would show a step twice.
5. Close the stream on a terminal state. The job says `terminal` rather than leaving the screen to infer it, and
   a stream left open on a finished job holds a per-host connection slot for nothing.

**What is built.** The live view is `app/src/screens/JobView.tsx` in the app repository, with the step and event
sentences in `app/src/jobSentences.ts` so the wording is a function of the record rather than a template inline
in a component. `app/src/api.ts` gains `jobEvents()`, which is the only place an `EventSource` is constructed,
and the session travels as the `?session=` query parameter because a stock `EventSource` cannot set a header.
That is why the route must not log its full URL.

**Verify, from a workstation.**

**These three commands need Node, and the deployment does not carry it.** `node`, `npm` and `npx` all answer
`command not found` on the box, which the second review round's executor confirmed by running each of them
there (`docs/reviews/c5-round-2.md`). Run them in a clone of the app on a workstation, with `npm ci` once
first.

```
cd <oic-ui>/app
npm run typecheck
npm test -- --run

# The browser rung. Playwright starts the dev server itself and the tests answer /api from the shapes the
# contract publishes, so this needs no deployment and no token. It is the rung that catches a screen which
# renders but does nothing, which component tests cannot see.
npx playwright test e2e/live.spec.ts
```

**The app is a separate repository and `/opt/oic-ui` is its checkout on the deployment** (guide 13i.1), which is
where the compose file builds the bundle from; it is not on the core's machine at any other path. The screens in
this step are the ones built from `bcffdae` or later, and the deployment's checkout is where to check that:
`git -C /opt/oic-ui log --oneline -1`, run on the box. That check is on the deployment and the three commands
above are not.

Expected: the unit suite passes with the live-view tests named, and the browser rung reports the refresh case,
where the page is reloaded mid-run and the steps already recorded come back, none missing and none invented.

**Run, and the output pasted here.** Six commands, on 2026-10-16, from a checkout of the app with no deployment
and no session token.

```
$ npm run typecheck
> tsc -b --noEmit
```

Exit 0. A type error here is a field the app believes exists, which is the class of mistake no runtime test
catches until the screen renders nothing.

```
$ npm test -- --run
 ✓ tests/contract.test.ts (28 tests) 12ms
 ✓ tests/jobSentences.test.ts (37 tests) 37ms
 ✓ tests/approvalSentences.test.ts (28 tests) 13ms
 ✓ tests/AnswerView.test.tsx (33 tests) 53ms
 ✓ tests/DocumentPanel.test.tsx (7 tests) 310ms
 ↓ tests/integration.test.ts (12 tests | 12 skipped)
 ✓ tests/review.test.tsx (16 tests) 795ms
 ✓ tests/live.test.tsx (12 tests) 1959ms

 Test Files  7 passed | 1 skipped (8)
      Tests  161 passed | 12 skipped (173)
```

**The counts moved by one in the second review round, and the reason is worth the line.** The suite was red for a day:
`bcffdae` refreshed the app's copy of the contract while `tests/contract.test.ts` still held the eleven-path
list and the three-field allowance the copy had outgrown. Fixing that restored 160, and the manual's second
rule for the Review screen (*approve is never the default*) had no test until the same round, which is the
161st. **A pasted suite is a claim about a commit**, and this one now names 161 at the commit it was taken on.

The twelve skips are the integration rung, which needs a live deployment and says so with the two commands that
would make it run. A skip that printed nothing would be the failure this suite has already paid for once.

```
$ npx playwright test e2e/live.spec.ts
Running 4 tests using 1 worker
  ✓  1 › a refresh mid-run comes back to the same job with the recorded steps (983ms)
  ✓  2 › shows a step as it arrives, in a sentence, while the job is still running (508ms)
  ✓  3 › offers the two levels and no more (503ms)
  ✓  4 › a job that stops reporting looks stalled, within the shipped window (47.5s)

  4 passed (53.0s)
```

**The four lines are trimmed for the page.** Playwright prints `✓  1 [chromium] › e2e/live.spec.ts:83:3 › the
live view › a refresh mid-run comes back to the same job`, so the project, the file, the line and the describe
block are dropped here. The names, the counts and the durations are what the command printed.

The fourth line is the one worth looking at: it waits out the real 45 second window rather than a shortened one,
so the number it proves is the number that ships. Running both specs together is thirteen tests and just under a
minute, because one of them is fifty of those seconds.

**What this step cannot prove, said plainly.** A live deployment's job can reach the gate, and 6.2 records that
run: the engine is wired into `services/api/app.py` and `POST /jobs` drives the six-step plan. What this step
still cannot prove is the *screen* against a live gate: the live view's acceptance is the browser rung, whose
responses are the contract's shapes against a stubbed deployment, and the refresh path is exercised through the
app's own client rather than through a worker killed on a box. Two rows of the register are therefore about the
mechanism and not about the deployment: the screen renders what the record says, and the record is proved
elsewhere.

**If it breaks.**

- **A refreshed tab comes back to a blank job**: the screen is being drawn from the stream instead of from
  `GET /jobs/{id}`, or the job id is not in the URL. A view that only exists while a socket is open is the
  defect the ordering above prevents.
- **Steps appear twice after a reconnect**: the view is appending instead of keying on `seq`. Replay is a
  feature and duplicate delivery is its normal consequence.
- **The screen says a dead job is working**: the ping is being counted as progress. Only a real event with a
  sequence number may reset the staleness clock.
- **An event arrives and the screen does not change**: check the event name against what the route sends.
  `EV_STEP`, `EV_PROGRESS`, `EV_NOTICE` and `EV_DONE` are four different things and only some of them move the
  step list. On the wire they are the lowercase names `step`, `progress`, `notice` and `done`: the constants are
  `EV_`-prefixed in `services/api/jobs.py`, and an SSE frame carries `event: step`, so a filter on the constant
  name matches nothing.
- **`EventSource` never connects and the console shows a 401**: the session is being sent as a header. A stock
  `EventSource` cannot set one, so on this route the session is a query parameter.

### 6.6 The Review screen, and what a decision is allowed to look like

**Goal.** A pending approval shows what will happen, why, and the exact content, *before* a decision is offered.
Bulk approval exists only for items the system can show are identical, and it states the count. An empty queue
says so rather than looking broken. And no sentence on any screen names a person the record cannot identify.

**Why the default assumption is a rubber stamp.** The closest large-scale measurement of human approval
behaviour is a study of merged pull requests: **46% were approved with no comment at all** and 65% with three
words or fewer, and in 8.4% of merges several reviewers approved without leaving a single comment. That is a
population of professional engineers reviewing their peers' work. A practice manager approving an agent's work
is in a weaker position on both counts: less able to judge the artefact, and less able to be held responsible
for it. The design therefore assumes a presented approval will often be a gesture, and the only defence is
making the decision genuinely easy to evaluate (`docs/research/live-updates-transport-and-agentic-ux.md` §5.1).

**What "easy to evaluate" means here, in three rules.**

- **Show the action, not the conclusion.** The gate states the specific thing in concrete terms: which
  recipient, which subject, which body, and what changes if it is approved. An engineer approving a diff sees
  the diff.
- **Approve is never the default.** No key binding, no focus, no Enter-to-confirm. A queue a person can clear by
  holding a key is a queue that gets cleared that way.
- **A decision quotes the content it is about.** The screen sends `action_digest` back with the decision. If the
  content moved while it was being read the route answers `409 approval_drift`, and the screen re-reads the job
  and asks again in its new form. It does not report a generic failure, because "something went wrong" leaves a
  person believing they approved the thing they read.

**Bulk approval is a stated rule over enumerated items, never a checkbox column.** The research's construction
is the one built: *"approve these 5 invoices, all matching their purchase order"*, with the rule and the list
captured in the record. That is auditable in a way that five checkbox clicks are not, and stating the qualifying
condition is what forces a person to read it. The screen offers the bulk action only when the **digest of every
pending item is equal**, which is the only equality the system can *show*, and the control names the count. A
queue of items that differ in any way offers no bulk action at all, and that absence is the assertion the test
makes, because the presence of the action on the identical case is the easy half.

**The identity rule, which is the one that fails quietly.** An approval recorded on this deployment is
attributable to a session holding a role and not to an identified individual: the basis is a bearer token and
the workstation is often shared. So:

- **The screen reads assurance from the record and never from the live session.** A permission read live at
  comparison time is a permission that has since moved; identity read live is an identity that was not verified
  when the act happened.
- **An unknown assurance value resolves downward to `unverified`.** A typo, an old row or a value written by a
  later version must never render as a verified identity.
- **No sentence names a person while the basis is unverified.** Not the claimed subject, and not "a person
  approved an action" either. The banner above the Approve control says what the record will say before anybody
  clicks it: *"Identity not verified. This approval will be recorded against a role, not a person."* The
  sentence a reader gets afterwards is the one `approvals.describe()` prints, in the app's own words in
  `approvalSentences.ts`: *"Approved at 2026-10-16T18:18:25+00:00 under the viewer role. The person who decided
  was not identified: sign-in is not enabled on this deployment, so the system cannot verify who acted."*
  **The words are identical in both renderers and the timestamp is not.** The feed and `describe()` print the
  recorded ISO value; the app passes the same field through `toLocaleString()`, so the same approval reads
  `Approved at 10/16/2026, 2:18:25 PM under the viewer role.` on screen. A page that quotes one and calls it
  the other is wrong about which renderer it is describing, and round three's executor found this page doing
  exactly that.
- **`claimed_subject` never sits where a verified name would sit.** The field is named `claimed_` on purpose,
  where an interface cannot drop the caveat by choosing not to render a paragraph.

**The queue, and where it comes from.** `GET /approvals` returns the jobs waiting at a gate that this session
may decide on, newest first, scoped **by access roots**: a job appears if the roots it carries are a subset of
the roots the session holds, which is the same comparison the retrieval boundary makes everywhere else. Roots
rather than a role and rather than the subject, because the subject answers *who asked* and this question is
*who may decide*. **It does not decide who the right approver is**: every session holding the roots sees every
gate in them, so separation of duties is a policy this deployment does not yet express. And **the app does not
call the route yet**: `loadQueue()` in `app/src/queue.ts` reads the jobs this browser started and filters them
on `awaiting_approval`, and the screen states that scope above the list rather than offering a browser's own
history as the role's queue. A queue derived from the jobs one browser started cannot show an approval to the
person whose job it is to approve them, which is the whole point of the screen; that is why the route exists
and why the screen has a single switch point waiting for it.

**What is built.** `app/src/screens/Review.tsx` in the app repository, with every sentence it can produce for an
approval in `app/src/approvalSentences.ts`. That module is separate from the component for the same reason
`services/api/approvals.py` is separate from the route: the wording is the part that lies if it drifts, and a
test can read a module that a test cannot read out of JSX.

**Verify.**

**From a workstation, for the same reason as 6.5: the box carries no Node.** `npm ci` once in a clone of the
app, then:

```
cd /opt/oic-ui/app
npm test -- --run

# The browser rung, against a stubbed deployment: five identical approvals, a mixed queue, and an empty one.
npx playwright test e2e/review.spec.ts

# The app's copy of the contract against the core's, because the app is built against the copy. Run from
# the core's checkout, where the two repositories are both on the box; the script's default path assumes
# they are siblings and on the deployment they are not.
cd /opt/oi-core && bash /opt/oic-ui/scripts/check-contract.sh /opt/oi-core/docs/openapi.json
```

Expected: the review tests name the four rows; the browser rung asserts the full body is in the document before
the approve control is enabled, that the mixed queue offers no bulk action, and that the empty queue renders a
sentence rather than a blank panel or a spinner. **And the contract check passes**, because the app's copy of
the contract caught up with the core's: the lines below are what it prints, and the paragraph after them says
what the check looked like while it failed.

**Run, and the output pasted here.** Same session as 6.5, no deployment and no token.

```
$ npx playwright test e2e/review.spec.ts
Running 9 tests using 1 worker
  ✓  1 › shows what will happen, why, and the exact content before a decision is offered (783ms)
  ✓  2 › offers no bulk action on a queue of five different items (623ms)
  ✓  3 › offers one stated rule naming five when five items hold the same content (639ms)
  ✓  4 › refuses to offer a decision when the record carries only a digest (597ms)
  ✓  5 › an empty queue says nothing is waiting rather than looking broken (563ms)
  ✓  6 › denying records the reason, and the queue reflects it with no manual refresh (752ms)
  ✓  7 › asking for changes records the reason, and the queue reflects it with no manual refresh (746ms)
  ✓  8 › a decision leaves the queue without a manual refresh (588ms)
  ✓  9 › where the queue comes from › a job started in this browser reaches the live view and then the queue (872ms)
  9 passed (8.4s)
```

The second line is UI-REVIEW-003's half that matters and it is an assertion of absence. Lines six to eight are
UI-REVIEW-002's three controls, one test each, and each asserts that the job leaves `awaiting_approval` and that
the queue reflects the decision with no manual refresh. The ninth is the one that found the limit of the queue
described above: a job started in this browser is visible to it and to nobody else, which is why the screen
states its scope and why `GET /approvals` is the route this screen is built for.

```
$ bash /opt/oic-ui/scripts/check-contract.sh /opt/oi-core/docs/openapi.json
OK    the app's contract matches /opt/oi-core/docs/openapi.json
      12 path(s): /activity, /approvals, /ask, /ask/stream, /documents/{doc_id}, /health, /jobs,
      /jobs/{job_id}, /jobs/{job_id}/approvals, /jobs/{job_id}/events, /jobs/{job_id}/{action}, /tasks
      25 model(s)
```

**The check failed for a day, and the fix took two commits in the other repository.** The core's
`docs/openapi.json` published `/approvals`, `/documents/{doc_id}` and the three job fields `Job.to_dict()` has
always sent (`pending_action`, `pending_action_digest`, `events_through`) while the app's copy was last
regenerated before them, and the app compiles against its own copy. `oic-ui` regenerated that copy at `bcffdae`
and that commit changed one file. The allowance list in `tests/contract.test.ts` stayed behind, so the app's own
suite went red on it, and the second round's fidelity checker is what found that. The fix is in the app repository: the
list was deleted rather than widened, which is what its own comment said to do when the fields landed. The check
above is the one that says the two copies agree.

**The paragraph this replaces had it backwards, and that is worth recording.** It said the three job fields
were absent from the published document and that the gap could not be closed from here. They were absent from
the *committed file* while the service had been serving them, which `scripts/fetch_openapi.py --check` caught
the moment it was run: the file had 11 paths against the service's 12. Regenerated and verified on 2026-10-16
at twelve paths and twenty-five schemas.

**The twelve paths are not ten, and the whole-set comparison is what keeps it that way.** `tests/contract.test.ts`
compares the whole path set rather than a subset, so a route added on the service side and not on the client
side fails the app's own suite rather than being discovered by a client that calls it.

**What this step cannot prove, said plainly.** A live deployment's job reaches the gate and waits there, and
6.2 records that run, so the remaining gap is narrower than it was: **the screen's own acceptance is still the
browser rung**, whose responses are the contract's shapes against a stubbed deployment rather than a live gate.
The `409 approval_drift` path is driven by a stub that answers 409, not by a real draft rewritten mid-read; the
service's own half of drift is proved in `tests/test_workflow_chain.py`, which needs a database. And **the queue
on screen is the jobs this browser started**, because `loadQueue()` reads them and does not call
`GET /approvals`. That route exists, answers, and is scoped by access roots, so the switch is a change in the
app's repository rather than a wait on the service; the screen states its scope above the list until then.

**If it breaks.**

- **An approval is offered with no content above it**: the screen is falling back to the digest. Refusing the
  decision is the correct outcome, so the bug is that a control was rendered at all.
- **The screen shows a name**: `claimed_subject` is being rendered, or `approvalSentences.ts` is not the only
  place the sentence is built. The record is honest and the display is not, and the display is what a client
  reads.
- **A bulk action appears on a mixed queue**: the comparison is on the count or on the task rather than on the
  digest. Equal digests are the one equality the system can demonstrate.
- **A 409 arrives and the screen says "something went wrong"**: the drift branch is missing. Re-read the job,
  re-render, and ask again; the person's decision was about content that no longer exists.
- **An empty queue renders a blank panel**: the loading state and the empty state are the same branch. Three
  states, not two, which is the same defect the shell had when it reported "no tasks declared" for a session it
  could not read.

