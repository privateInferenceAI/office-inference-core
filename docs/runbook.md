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
passes. Later phases stay as outlines until we reach them, so nothing here goes stale.

**Status, 2026-10-11:** **C0 complete.** Phases 0, 1, and 2 built, run, and verified on the cloud
reference instance. The chain holds the first live model call, the day is exported with a manifest,
the anchor ships on a timer to a bucket the box cannot delete from, and per-role keys with hard
budgets are written. C1 still owes the purge tombstone, the per-data-class content modes, the audit
CLI's refusal of hand-typed actors, and a decision on model deprecation. Phases 3 to 11 outlined.

## The environment we are building on

| | |
|---|---|
| Account | Office Inference, `878451098165` (holds the Activate credits) |
| Instance | `i-096bebd46308d1673`, m7i.4xlarge, Ubuntu 24.04, 200 GB gp3 encrypted. 16 vCPU, 61 GB RAM, no GPU |
| Elastic IP | `52.204.202.66` (stable across stops; moved from the retired t3.large) |
| SSH key | `ai-stack-key`, private key at `~/ai-stack/ai-stack-key.pem` on the workstation |
| SSH allowed from | 47.164.45.109 only (security group `sg-09d9bfffbd28014c9`) |
| Instance role | `oi-core-dev-instance`: `AmazonSSMManagedInstanceCore` for keyless access, plus `oi-core-dev-bedrock-invoke`, which allows exactly the four models the dev manifest names |
| Inference | Bedrock, us-east-1. No model runs on this box |
| Region | us-east-1 |
| State | **stopped** when not in use |

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
      --instance-ids i-096bebd46308d1673
    aws ec2 wait instance-running --profile oi-credits --region us-east-1 \
      --instance-ids i-096bebd46308d1673
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

    aws ssm start-session --profile oi-credits --region us-east-1 --target i-096bebd46308d1673

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
| Delete refused | `AccessDenied`, naming `assumed-role/oi-core-dev-instance/i-096bebd46308d1673` and `no identity-based policy allows the s3:DeleteObject action` |
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
    python3 -m pytest tests/test_chunker.py -q
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
the runbook now names `/v1/capabilities` as the authority on option names and the option set is asserted by
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
once. That costs a re-parse of everything, which for this corpus is about ten minutes and for a client's is
the price of not serving labels the code no longer produces.

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

<<<<<<< Updated upstream
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
=======
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

**A limitation the third row exposes, recorded rather than discovered later.** The severity question is not
denied, because the restricted chunk scored 0.4088 against a permitted best of 0.65. The executive
memorandum does answer it, so that is a **false negative**: the content is there, a person cannot see it, and
the system does not say so.

The cause is chunking rather than the comparison. The memorandum is 1,541 characters, which is one chunk, so
its embedding is an average of salary, bonus, equity and severance together. A question about any one of them
matches that average weakly, while a specific passage from the 10-K matches strongly. **The score comparison
is a heuristic and this is the shape of its failure**: a short document whose content is varied reads as a
worse match for every question than a long document's targeted passage. The alternatives are worse — a bare
hit test would deny on most questions and train users to ignore the warning — so this is recorded as a
bound, not fixed.
>>>>>>> Stashed changes

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
  fallback is silent by design and the symptom is a disclosure rather than an error.
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
    docker compose -f deploy/office/compose/docker-compose.yml --profile tools run --rm render
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

