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
and an anchor records the head. C1 is the work that remains in the audit story: the anchor on a timer
and shipped off the machine, the purge tombstone, the per-data-class content modes, and the daily
export to the client's bucket. Phases 3 to 11 outlined.

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

