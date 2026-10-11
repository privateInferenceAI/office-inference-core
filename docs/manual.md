# Operating manual

For the core system behind every Office Inference deployment. Written for someone who has never seen
this project and is comfortable in a terminal.

Everything here has been run. Where a command's output matters, the manual shows what you should see.
Where something is not built yet, it says so rather than leaving you to discover it.

**Status when this was written:** phase C1 complete. The system runs, answers through a gateway,
records what it did, ships that record off the machine, and can prove the record was not altered. It
cannot read a client's documents yet. That is phase C2 and it is next.

---

## Contents

**Part 1, understanding the system**
1. What this is
2. The problem it solves
3. What runs where
4. The two paths: a request, and its record
5. The manifest is the contract
6. What exists today, and what does not

**Part 2, getting in**
7. Access
8. Starting and stopping the machine
9. Checking that the stack is healthy

**Part 3, everyday operations**
10. A shorthand for the compose command
11. Making a model call
12. Keys and budgets
13. The audit record, command by command
14. Shipping evidence off the machine
15. The routine checks

**Part 4, changing things**
16. The manifest, field by field
17. Adding a role
18. Changing which model a role uses
19. Standing up a second deployment for a new client
20. Adding a service or a pack

**Part 5, when something breaks**
21. Troubleshooting

**Part 6, reference**
22. Every command in one table
23. What lives where on the machine
24. Glossary
25. Not built yet

---

# Part 1: understanding the system

## 1. What this is

One core system, sold two ways. The cloud build runs in the client's own AWS account and sends AI
requests to models there. The office build runs on hardware inside the client's building and uses
models stored on that hardware, with no cloud account at all. The components are the same. What
differs is configuration, which is the whole commercial claim: one thing to build, maintain, and
support, assembled differently per client.

Four offerings come out of that: Standard Cloud and Compliant Cloud in the client's account, a hosted
tier in ours, and In-Office on the client's own machine. This manual covers the core, which is what
all four share.

## 2. The problem it solves

A small business wants to use AI on its own material. Three things stop them.

They cannot tell what the system did. "The AI answered" is not an answer their auditor accepts.
They cannot control what it costs when something goes wrong, because a loop of AI calls spends money
the way a leak spends water. And they cannot always let their data go to a third party, for legal
reasons or because their client would object.

This system answers all three. Every AI call goes through one gateway that knows which role asked,
what that role may spend, and which model it may reach. Every call and every refusal lands in a record
that cannot be quietly edited, and that record leaves the machine on a schedule so a client can check
it without trusting us.

## 3. What runs where

| Piece | What it is | In this deployment |
|---|---|---|
| The machine | One AWS EC2 instance running Docker. No GPU, because the models live at the provider | `i-096bebd46308d1673`, an m7i.4xlarge: 16 cores, 64 GB RAM, 200 GB disk, Ubuntu 24.04 |
| `postgres` | The database. Holds the gateway's keys and spend, and our own audit record | A container, on the box |
| `litellm` | The gateway. Every AI call goes through it. It authenticates the caller, checks the role's budget and model permissions, calls the model, and writes down what it cost | A container, on the box |
| `audit` | The tool that writes and checks the audit record. Runs when you call it, not as a service | A container, started on demand |
| `keys` | The tool that mints per-role API keys and their ceilings | A container, started on demand |
| `llamacpp` | A local model server, used by the office build only | A container, switched off in the cloud build by a compose profile |
| The models | Frontier AI models, reached through the provider's API | Amazon Bedrock, us-east-1: Nova and Claude and DeepSeek models |
| The bucket | Where the evidence goes, so the machine cannot erase it | `oi-core-audit-dev`, with Object Lock |

Nothing publishes a port. The containers talk to each other over a private Docker network called
`ai-net`, and the only way in is SSH or a Systems Manager session. That is deliberate: a machine with
no listening ports is a machine that cannot be attacked over the network.

## 4. The two paths: a request, and its record

**A request.** Something wants an answer. It authenticates with a key, and the key belongs to a role:
`front_desk`, `analyst`, `reader`, or `coder`. The gateway checks that the key may call the model that
role maps to, checks that the role is inside its spending ceiling, calls the model, and returns the
answer. The caller never names a model. It names a role, which is what lets you change which model
answers without touching the caller.

**The record.** The gateway writes every call to its own spend log, including the ones it refused. A
tool called `audit` copies those rows into our own record, which is a chain: each row contains a hash
of the row before it, so any edit or deletion breaks the chain in a way a reader can detect. Each day
that record exports as a file with a manifest, and every hour a small file recording the head of the
chain is shipped to a bucket the machine can write to but not delete from. That last part is what
makes the record evidence rather than a log file: the machine cannot cover its tracks without the
missing piece being visible somewhere it does not control.

## 5. The manifest is the contract

A client's deployment is described by one YAML file called a manifest. It names the client, the
profile (cloud, office, or hybrid), the roles and their models, the spending ceilings, what content
the record may keep, where evidence ships, and any compliance profile.

Nothing is hand-configured on a server. The renderer reads the manifest and writes three files: an
environment file holding secrets and settings, the gateway's model list, and a normalized copy of the
manifest. Everything downstream reads those, so changing a deployment means changing the manifest and
re-rendering, not editing files in place.

A validator refuses a manifest that would break a promise. It knows, for example, that an office build
cannot use remote models, that a regulated deployment may not route a prompt globally, and that a
build with remote models must declare spending ceilings.

## 6. What exists today, and what does not

| Working now | Not built yet |
|---|---|
| The stack runs from a manifest, in the client's account, on CPU | Reading the client's documents. Ingestion and retrieval are C2 |
| Four roles on the provider's models, through one gateway | A user interface of any kind. C4 |
| Per-role keys with model permissions and hard spending ceilings | Per-user permissions. Today roles are all there is |
| An append-only audit record with a hash chain | Alarms that page someone, and a restore drill. C8 |
| Daily export, hourly anchor, shipped to a bucket with Object Lock | Loading the seventy questions that judge answer quality. C8 |
| Evidence that survives a deleted-tail check | Voice, agents, workflows. C5, C6 |
| CI that tests, validates manifests, and catches deprecated models | |

---

# Part 2: getting in

## 7. Access

Two ways in, and the second one needs no key and no open port.

**SSH**, from a workstation holding the private key:

```
ssh -i ~/ai-stack/ai-stack-key.pem ubuntu@52.204.202.66
```

The security group allows port 22 from one address only. If your address changes, the connection times
out rather than being refused, and the fix is to update the security group.

**A Systems Manager session**, which is the pattern client machines use because it needs no inbound
port and no key material:

```
aws ssm start-session --profile oi-credits --region us-east-1 --target i-096bebd46308d1673
```

Both land you in the same shell as the `ubuntu` user, who can run Docker without `sudo`.

**What is on the machine.** The repository is at `/opt/oi-core`. Everything the deployment produces
lives under it: `rendered/` for generated configuration and secrets, `exports/` for the audit
artifacts, `data/` for the database. The Docker containers are separate from all of that.

## 8. Starting and stopping the machine

The machine bills by the hour whether or not you are using it, about 0.81 US dollars, so it is stopped
between sessions.

```
# from a workstation
aws ec2 stop-instances  --profile oi-credits --region us-east-1 --instance-ids i-096bebd46308d1673
aws ec2 start-instances --profile oi-credits --region us-east-1 --instance-ids i-096bebd46308d1673
```

The address does not change across a stop, so the SSH command above keeps working. The containers are
configured to restart by themselves, so after a start you only need to check them.

## 9. Checking that the stack is healthy

```
cd /opt/oi-core
docker compose -f deploy/office/compose/docker-compose.yml --env-file rendered/env ps
```

You should see `postgres` healthy and `litellm` running:

```
NAME       IMAGE                    SERVICE    STATUS
litellm    ghcr.io/berriai/litellm… litellm    Up 4 hours
postgres   postgres:16-alpine       postgres   Up 4 hours (healthy)
```

Then ask the gateway whether it is alive, from a throwaway container on the same private network,
because nothing is published to the host:

```
docker run --rm --network ai-net curlimages/curl -sS http://litellm:4000/health/liveliness
```

Expected: `"I'm alive!"`. Note `-sS` rather than `-s`: bare `-s` hides connection errors, so a
gateway that is down prints nothing at all and looks like a hang.

---

# Part 3: everyday operations

## 10. A shorthand for the compose command

The compose command is long. Set an alias once per shell and the rest of this manual is readable:

```
cd /opt/oi-core
alias oi='docker compose -f deploy/office/compose/docker-compose.yml --env-file rendered/env'
```

Everything below uses `oi`. Without the alias, write the whole thing out.

## 11. Making a model call

The only supported way to reach a model is through the gateway, with a role's key. The `keys` tool has
a probe that does exactly that and reports what came back:

```
oi run --rm keys --probe analyst
```

```
analyst -> analyst: 200
  answered by analyst, 13 tokens
```

That is a real call through the gateway to the provider, billed at fractions of a cent. To see the
same thing by hand, which is useful when you want the raw response:

```
KEY=$(grep '^LITELLM_MASTER_KEY=' rendered/env | cut -d= -f2-)
docker run --rm --network ai-net -e KEY="$KEY" curlimages/curl -sS \
  -X POST http://litellm:4000/v1/chat/completions \
  -H "Authorization: Bearer $KEY" -H 'Content-Type: application/json' \
  -d '{"model":"analyst","messages":[{"role":"user","content":"In one sentence, what is a gateway?"}],"max_tokens":60}'
```

That uses the master key, which is for administration. Use a role key in anything that is not a test.
The `keys` tool is the supported way to call as a role.

## 12. Keys and budgets

`litellm` needs a key to accept a call. `keys` mints one per role from the manifest, with two
restrictions attached: the key may call one model, and it has a ceiling in dollars per period.

**Mint or update every role's key:**

```
oi run --rm keys
```

```
keys for oi-core-dev-cloud:
  analyst    updated  models=[analyst]  10.0 USD per 30d
  coder      updated  models=[coder]  5.0 USD per 30d
  front_desk updated  models=[front_desk]  2.0 USD per 30d
  reader     updated  models=[reader]  10.0 USD per 30d
  secrets in /app/rendered/keys.json (mode 600) and never printed
```

The first run says `minted` and the second says `updated`, with the secrets unchanged. That is
deliberate: components already hold those keys, so re-running restores the manifest's policy without
breaking anything. The secrets live in `rendered/keys.json`, readable only by the owner, and the tool
never prints them.

**Cap a role right now**, which is the emergency brake when something is looping:

```
oi run --rm keys --set-budget coder 0.000001
```

```
coder capped at 0.000001 USD. A plain run of this tool restores the manifest's value.
```

**See the ceiling hold:**

```
oi run --rm keys --probe coder
```

```
coder -> coder: 422
  Budget has been exceeded! Key=oi-core-dev-cloud:coder (sk-...VcRQ) Current cost: 3.156e-05, Max budget: 1e-06
```

**Put it back:**

```
oi run --rm keys
oi run --rm keys --probe coder
```

**Prove the model permission**, by asking for a model the key is not scoped to:

```
oi run --rm keys --probe analyst --as-model reader
```

```
analyst -> reader: 403
  The requested model 'reader' is not available for this API key, or the model name is invalid.
```

**What the ceilings do and do not promise.** The gateway checks the budget against spend it has
already recorded, and that number lands a moment after a call returns. So a ceiling stops a runaway
loop, and it does not promise that the number is never exceeded: the call that crosses the line
completes, and in a fast burst more than one can slip through before the recorded figure catches up.
Promise a client that the loop stops, not that the number is untouchable.

## 13. The audit record, command by command

The `audit` tool writes and checks one append-only record. Every command below is run the same way:

```
oi run --rm audit <command>
```

### Applying schema changes

```
oi run --rm audit migrate
```

```
skip    002_audit_events.sql (already applied)
skip    003_purge_events.sql (already applied)
skip    004_purge_needs_the_hatch.sql (already applied)
skip    005_content_modes.sql (already applied)
0 migration(s) applied, 4 already present
```

Migrations are ordered and checksummed. Editing a migration that has already been applied is refused
rather than silently accepted, so a schema change means a new file.

### Writing an event by hand

```
oi run --rm audit record --dev --type access --actor anthony --actor-role admin \
  --data-class internal --payload '{"document":"expense-policy.pdf","pages":3}'
```

```json
{
  "id": 18,
  "ts_iso": "2026-10-11T04:20:00.000000Z",
  "row_hash": "…",
  "payload_sha256": "…",
  "content_mode": "redacted",
  "prompt_sha256": null,
  "completion_sha256": null
}
```

`--dev` is required for a hand-typed actor. Without it the command exits non-zero, because in a real
deployment the actor comes from an authenticated session rather than from whoever is typing. The actor
is an identity, not a word.

**The event types:** `model_call`, `tool_call`, `approval`, `access`, `config_change`, `login`.
**The outcomes:** `ok`, `denied`, `error`, `pending`.
**The data classes:** `public`, `internal`, `confidential`, `regulated`.

**Recording a model call with its prompt and completion:**

```
oi run --rm audit record --dev --type model_call --actor anthony --actor-role admin \
  --data-class public --model bedrock/amazon.nova-lite-v1:0 \
  --prompt "what is the mileage rate?" --completion "67 cents"
```

The prompt and completion hashes are always recorded. Whether the text itself is kept depends on the
data class and the manifest's content policy, described in section 16.

### Reading the record

```
oi run --rm audit list
```

```
    id  when                         type           actor                   class          outcome  detail
    18  2026-10-11T04:20:00.000000Z  access         anthony                 internal       ok
    17  2026-10-11T03:55:08.995751Z  model_call     anthony                 regulated      ok       bedrock/deepseek.v3.2
    16  2026-10-11T03:55:01.203403Z  model_call     anthony                 internal       ok       bedrock/amazon.nova-lite-v1:0
```

Filter and limit it:

```
oi run --rm audit list --type model_call --limit 5
oi run --rm audit list --type purge
```

### Copying the gateway's calls into the record

```
oi run --rm audit backfill
```

```
backfilled 1 successful call(s), 1 refused or failed
```

This reads the gateway's spend log, which holds every call including the refused ones, and copies
anything not already in the record. It is idempotent: `request_id` is the key, so running it twice
changes nothing the second time. Two things are worth knowing. The gateway writes its spend rows a
moment after a call returns, so a backfill run in the same second can miss the newest row and the next
run picks it up. And a retention purge sets a watermark, so calls older than the newest purge are
deliberately not copied back.

### Exporting a day

```
oi run --rm audit export --date 2026-10-11
```

```
exported 6 event(s) for 2026-10-11
  /app/exports/2026-10-11/audit-events.jsonl
  /app/exports/2026-10-11/manifest.json
  last row hash 6df2d45eab2d…
```

On the host that is `/opt/oi-core/exports/2026-10-11/`. The JSONL file holds one event per line. The
manifest holds the count, the first and last ids, the hash the first row chains back to, and the hash
of the file itself. Without `--date` you get today in UTC.

### Checking an export

```
oi run --rm audit verify /app/exports/2026-10-11/audit-events.jsonl
```

```
ok   /app/exports/2026-10-11/audit-events.jsonl: 6 event(s), chain intact
```

To see it catch something, edit a copy and check that instead:

```
cp -r exports/2026-10-11 /tmp/tamper
sed -i 's/"actor":"anthony"/"actor":"not-anthony"/' /tmp/tamper/audit-events.jsonl
oi run --rm -v /tmp/tamper:/app/exports audit verify /app/exports/audit-events.jsonl
```

```
FAIL /app/exports/audit-events.jsonl
  - line 1: row_hash does not match the row contents
  - the file hash does not match the manifest
```

Two independent checks caught it: the row's own hash, and the manifest's hash of the whole file.

### Anchoring the chain head

```
oi run --rm audit anchor --out /app/exports/anchors
```

```
anchored 6 event(s), head 6df2d45eab2d…
  /app/exports/anchors/anchor-2026-10-11-00000006.json
  Ship this file off this machine. Kept here, it proves nothing.
```

Use `--out` under `/app/exports`, because that directory is the one mounted back to the host. The
default writes inside the container and the file disappears when the container exits.

An anchor is the only artifact that can prove rows were not deleted from the end of the chain. Check
an export against one:

```
oi run --rm audit verify /app/exports/2026-10-11/audit-events.jsonl \
  --anchor /app/exports/anchors/anchor-2026-10-11-00000006.json
```

```
ok   /app/exports/2026-10-11/audit-events.jsonl: 6 event(s), chain intact
     note: matches the anchor written 2026-10-11T04:00:07+00:00 (6df2d45eab2d…, 6 rows)
```

With no anchor, the command says plainly that completeness cannot be shown, because deleting rows from
the end and re-exporting would still verify. That sentence is the honest one, and it is why the
shipping job in section 14 exists.

### A retention purge

```
oi run --rm audit purge --before 2026-10-11T03:00 --reason "retention: 90 days" --dry-run
```

```
would purge 10 event(s) before 2026-10-11T03:00 (ids 1..10)
  the tombstone would chain from 98ea3b7877e3d2c9a71cf505b93533ec79f4bab10a631c3794c711407a3e8499
```

Then for real, without `--dry-run`:

```
purged 10 event(s) before 2026-10-11T03:00
{
  "tombstone_id": 13,
  "ts_iso": "2026-10-11T03:33:36.544593Z",
  "head_before": "98ea3b7877e3…",
  "head_after": "6df2d45eab2d…",
  "deleted_count": 10,
  "first_id": 1,
  "last_id": 10,
  "reason": "retention: 90 days"
}
```

A purge is a deliberate hole in the chain. The tombstone is what makes the hole explained instead of
suspicious, and it carries both hashes: `prev_hash` is the head before the purge and `row_hash` is the
head after. The tombstone is written first, inside the same transaction as the deletion, so either both
happen or neither does.

Two consequences to know. A purge invalidates anchors written before it, because the anchored rows are
genuinely gone, and the tombstone is the artifact that says why. And the rows it removes are not
restored by the next backfill, because the purge sets a watermark.

## 14. Shipping evidence off the machine

The job ships two things: the chain anchor every hour, and the day's export once a day. It runs on
systemd timers, and it uses two containers: the audit tool writes the artifact, and a small courier
image uploads it and then reads it back to compare hashes.

**Run it by hand, which is also how you debug it:**

```
cd /opt/oi-core
./deploy/cloud/ship/oi-core-ship.sh anchors
./deploy/cloud/ship/oi-core-ship.sh exports
```

```
anchored 6 event(s), head 6df2d45eab2d…
shipping /evidence/anchors/anchor-2026-10-11-00000006.json (276 bytes)
  local  sha256 e19d146a56ae47e8a9312269809af1f928f84e60415b7fa43f41713d21a2c9c9
  remote sha256 e19d146a56ae47e8a9312269809af1f928f84e60415b7fa43f41713d21a2c9c9
  ok: s3://oi-core-audit-dev/anchors/anchor-2026-10-11-00000006.json matches the file that was sent
anchors shipped to s3://oi-core-audit-dev
```

The read-back is the point. From the sending side a complete upload and a truncated one look the same.
Fetching the object and hashing it is the difference between "we sent it" and "it is there and it
matches".

**The timers:**

```
systemctl list-timers 'oi-core-*'
journalctl -u oi-core-anchor.service -n 40
systemctl status oi-core-anchor.timer
```

Both timers are `Persistent`, so a run missed while the machine was stopped happens once at the next
start rather than never.

**What the machine is allowed to do with the bucket.** It can upload and read back. It cannot delete an
object, delete a version, shorten a retention period, or change the bucket's lock configuration. That
is the control that makes the evidence worth something, and it is worth testing once so you have seen
it:

```
docker run --rm --entrypoint aws -e AWS_REGION=us-east-1 oi-core-ship:local \
  s3 rm s3://oi-core-audit-dev/anchors/anchor-2026-10-11-00000006.json
```

```
delete failed: … An error occurred (AccessDenied) when calling the DeleteObject operation: Access Denied
```

## 15. The routine checks

| When | What | Command |
|---|---|---|
| After a start | Containers healthy | `oi ps` |
| After a start | Gateway answering | `docker run --rm --network ai-net curlimages/curl -sS http://litellm:4000/health/liveliness` |
| Weekly | The record's chain is intact | `oi run --rm audit verify /app/exports/<today>/audit-events.jsonl` |
| Weekly | Nothing was refused unexpectedly | `oi run --rm audit list --type model_call --limit 50` |
| Weekly | Timers have been firing | `journalctl -u oi-core-anchor.service -n 20` |
| Monthly | Model lifecycle and vulnerabilities | `python3 scripts/check_model_lifecycle.py` |
| Monthly | What the deployment is spending | The provider's cost report, not the gateway's |

---

# Part 4: changing things

## 16. The manifest, field by field

A manifest is what a client buys. Everything else is generated from it. Here is the file this
deployment runs, with what each part does.

```yaml
manifest_version: 1
client:
  name: Office Inference Dev
  deployment_id: oi-core-dev-cloud
profile: cloud
packs: [core]
models:
  front_desk: {local: "", frontier: bedrock/amazon.nova-micro-v1:0}
  analyst:    {local: "", frontier: bedrock/amazon.nova-lite-v1:0}
  reader:     {local: "", frontier: bedrock/amazon.nova-pro-v1:0}
  coder:      {local: "", frontier: bedrock/deepseek.v3.2}
lanes:
  frontier:
    enabled: true
    provider: bedrock
    account_owner: office_inference
    data_classes_allowed: [public, internal]
    requires_approval: false
    redaction: false
content:
  default: hash
  classes:
    public: full
    internal: redacted
budgets:
  period_days: 30
  roles:
    front_desk: 2
    analyst: 10
    reader: 10
    coder: 5
compliance:
  profile: none
  audit_retention_days: 90
offsite:
  provider: s3
  account_owner: office_inference
  bucket: oi-core-audit-dev
  region: us-east-1
  ship: [anchors, exports]
support:
  response_window: business hours, 4 hours
```

| Field | What it decides |
|---|---|
| `client.name`, `client.deployment_id` | Who this is for. The id appears in every audit row, and it prefixes the key aliases |
| `profile` | `cloud`, `office`, or `hybrid`. It decides whether the local model server starts, and which residency rules apply |
| `packs` | Which sellable bundles are switched on. `core` is implicit and always present |
| `models.<role>.frontier` | The model a role reaches when inference is remote. The `bedrock/` prefix is required |
| `models.<role>.local` | The model file a role reaches when inference is local, used by the office build |
| `lanes.frontier.enabled` | Whether remote inference exists at all. Must be false on an office build |
| `lanes.frontier.account_owner` | `client`, or `office_inference` for our own machines and the hosted tier |
| `lanes.frontier.data_classes_allowed` | Which data classes may reach a remote model |
| `content.default`, `content.classes` | What the record keeps per data class: `hash`, `redacted`, or `full` |
| `budgets.period_days`, `budgets.roles` | The ceiling per role, in dollars, over a period in days |
| `compliance.profile` | `none`, `contractual`, `hipaa`, `glba`, `sec-17a4`, `itar-cui`, `ferpa`, `pci` |
| `compliance.audit_retention_days` | How long the record is kept. Six years is enforced for `sec-17a4` |
| `offsite` | Where evidence ships, and which artifacts may leave: `anchors`, `exports`, `backups` |

**What the validator refuses**, all of it deliberately:

- A global inference profile under a compliance profile, because it can route a prompt to any region.
- Fable models, whose retention terms require a human review step.
- An office build with a frontier lane, since the account is not what makes inference remote.
- A remote model with no spending ceiling.
- An unknown pack, an unknown role in a budget, an unknown data class in a content policy.
- `offsite.ship` naming anything but anchors, exports, or backups. Documents and prompts can never
  leave the building, and the list is how that stays true.
- `itar-cui` with any offsite destination at all.
- A `sec-17a4` deployment with under six years of retention.

Run it before you render anything:

```
python3 scripts/validate_manifests.py
```

```
ok   dev-cloud.yaml  (profile=cloud, packs=['core'])
ok   dev-office.yaml  (profile=office, packs=['core'])
…
6/6 manifests valid; 8 packs known
```

Then render, which turns the manifest into the files the deployment reads:

```
python3 scripts/render_manifest.py --manifest manifests/dev-cloud.yaml
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

Two things to watch in that output. `gateway 4 model entries` is the model list the gateway will
serve, and a `(none)` there would mean the deployment has nothing to answer with. And `secrets reused`
means the renderer did not regenerate the database password, which would lock the stack out of its own
data.

## 17. Adding a role

A role is a name that maps to a model, with its own key and ceiling. Adding one takes four edits and
three commands. This example adds a `summarizer` role on the cheapest model, for a component that
should only ever write short summaries.

**1. Add the role and its model.** In `manifests/dev-cloud.yaml`, under `models:`:

```yaml
  summarizer:
    local: ""
    frontier: bedrock/amazon.nova-micro-v1:0
```

**2. Give it a ceiling.** Under `budgets.roles:`:

```yaml
    summarizer: 1
```

A role with no ceiling fails validation whenever remote inference is on, so this is not optional.

**3. Decide what the record keeps for it.** It inherits `content.default`, which is `hash`. If you want
something else for its events, that is a per-data-class decision rather than per-role, so leave it
alone unless the client asked otherwise.

**4. Validate and render:**

```
python3 scripts/validate_manifests.py
python3 scripts/render_manifest.py --manifest manifests/dev-cloud.yaml
```

The render now reports `gateway 5 model entries`.

**5. Restart the gateway** so it reads the new model list. The file is mounted rather than baked in, so
the container needs a restart to pick it up:

```
oi up -d litellm
```

**6. Mint the key:**

```
oi run --rm keys
```

```
keys for oi-core-dev-cloud:
  analyst    updated  models=[analyst]  10.0 USD per 30d
  coder      updated  models=[coder]  5.0 USD per 30d
  front_desk updated  models=[front_desk]  2.0 USD per 30d
  reader     updated  models=[reader]  10.0 USD per 30d
  summarizer minted   models=[summarizer]  1.0 USD per 30d
```

**7. Prove it works and that it is scoped:**

```
oi run --rm keys --probe summarizer
oi run --rm keys --probe summarizer --as-model analyst
```

The first answers. The second returns 403, because the summarizer key may not call the analyst model.

**Roles are the unit of permission.** There is no separate key per component today: a component uses
the key for the role it is acting as. If two components should have different ceilings, give them
different roles, even if they point at the same model. That costs one line each and keeps the record
readable, because every event names the role that asked.

## 18. Changing which model a role uses

Change the line, render, restart, and check that the new model is not deprecated:

```yaml
  analyst:
    local: ""
    frontier: bedrock/amazon.nova-pro-v1:0
```

```
python3 scripts/validate_manifests.py
python3 scripts/check_model_lifecycle.py
python3 scripts/render_manifest.py --manifest manifests/dev-cloud.yaml
oi up -d litellm
oi run --rm keys --probe analyst
```

Nothing in the application changes, because applications ask for the analyst role rather than for a
model. Two rules apply. If the client's contract names a single region, an inference-profile-only
model is disqualified and the validator will say so. And a model swap means rerunning the golden
questions before a client sees the new answers, which is C8's work rather than something to skip.

If the model is unknown to `data/model-lifecycle.json`, the lifecycle check fails and names it. Add it
with its status and a replacement, following `docs/model-lifecycle.md`.

## 19. Standing up a second deployment for a new client

The same repository serves every client. A new deployment is a new manifest, a new account, and a new
bucket.

1. Copy `manifests/dev-cloud.yaml` to `manifests/<client>.yaml`.
2. Change `client.name` and `client.deployment_id`. The id must be unique and is used in every audit
   row.
3. Set the models, ceilings, content policy, and compliance profile the client bought.
4. Point `offsite.bucket` at a bucket in the client's account, with Object Lock enabled at creation,
   because that cannot be turned on afterwards.
5. Validate and render on the new machine, then mint the keys there.
6. Ship the evidence: install the timers from `deploy/cloud/systemd/`.

The one thing that is not automatic yet is the account itself. Terraform for the VPC, the bucket, the
IAM role, and the rest is C7, and today those are created by hand. `docs/audit-evidence.md` records
what the bucket needs and why.

## 20. Adding a service or a pack

**A service** is a container with a job. `services/_template/` holds the shape, and `services/audit/`
is the worked example: a `Dockerfile`, the code, and a short README. Then add it to
`deploy/office/compose/docker-compose.yml` with a profile. Use `tools` for something that runs when
called, as `audit` and `keys` do, and leave the profile off for a long-running service.

Two rules apply to anything you add. It joins `ai-net` and publishes no ports, and if it writes to the
record it writes through `audit` rather than inserting rows itself, because the chain is meant to have
one writer.

**A pack** is a sellable bundle, listed in `packs/catalog.yaml` with the services it declares. Adding
one is a catalog entry plus the services it names. The manifest's `packs` list is what switches it on,
and an unknown pack id fails validation.

---

# Part 5: when something breaks

## 21. Troubleshooting

Every item below is something that actually happened, with what it looked like and what fixed it.

**A tool ignores your code change.** The tools run from images, and `docker compose run` uses the image
it already has. A change to `services/audit/audit.py` does nothing until:

```
oi build audit keys
```

**The gateway restarts in a loop and nothing answers on port 4000.** Read the logs before anything
else:

```
docker logs litellm --tail 40
```

If the message is `Database migration cannot proceed. Cannot automatically baseline this database`,
something created tables in the gateway's own database that its migration tool does not recognise. That
happened once, through a Postgres first-boot mount running our migrations into the gateway's schema.
Check what is there:

```
docker exec postgres psql -U oicore -d oicore -c '\dt'
```

**A curl prints nothing at all.** `-s` hides connection errors. Use `-sS`, or drop `-s`, and you get
`curl: (7) Failed to connect` instead of silence.

**`AccessDenied` on a model call.** The role's policy and the model id disagree. Compare the id in
`rendered/litellm.yaml` with the policy attached to the instance role.

**`Unable to locate credentials` from the gateway.** The container cannot reach the instance role
through the metadata service. The instance's metadata hop limit must be 2 for a container to reach it.

**`403 The requested model … is not available for this API key`.** The model permission working. That
key is scoped to another role.

**`422 Budget has been exceeded`.** The ceiling working. Raise the ceiling in the manifest and
re-render, or cap and restore with `keys --set-budget`.

**A call is missing from the record.** Run `backfill` again. The gateway writes its spend rows a moment
after a call returns, so a backfill in the same second can miss the newest one, and the pass is
idempotent.

**Rows you purged came back.** They cannot, unless the watermark is missing. `backfill` reads the
newest tombstone's cutoff and skips anything older, so a chain without a tombstone is the only way that
happens.

**`audit_events is append-only; DELETE is not permitted`.** The maintenance setting did not reach the
transaction. The purge command handles this itself; if you see it from your own SQL, you need one
transaction: `begin; set local oi.allow_audit_maintenance = 'on'; …; commit;`

**A tombstone with no purge behind it.** Impossible since migration 004, which refuses a `purge` row
unless the maintenance setting is on. Before that it was possible, and it happened.

**`verify --anchor` fails on an old anchor.** Expected after a retention purge: the anchored rows are
gone and the tombstone is the artifact that explains it. Any other time, treat it as a real finding.

**The timer did not fire while the machine was stopped.** It fires once at the next start, because both
timers are `Persistent`. If it did not fire at all, check `systemctl list-timers 'oi-core-*'` and
`journalctl -u oi-core-anchor.service`.

**The night's work is stale on the machine.** `git pull` on the box, then rebuild the tools. The
repository is the source of truth for code, and `main` is what runs.

---

# Part 6: reference

## 22. Every command in one table

| Command | What it does |
|---|---|
| `python3 scripts/validate_manifests.py [manifest…]` | Checks manifests and the pack catalog, including residency and budget rules |
| `python3 scripts/render_manifest.py --manifest <file>` | Writes `rendered/env`, `rendered/litellm.yaml`, `rendered/services.txt`, `rendered/manifest.json` |
| `python3 scripts/check_requirements.py` | Reports the register: verified, open, waived, by phase |
| `python3 scripts/check_model_lifecycle.py [manifest…]` | Fails if a manifest names a deprecated, past-EOL, or unknown model |
| `python3 -m pytest tests/ -q` | The test suite |
| `oi ps` | Container status |
| `oi logs <service>` | Container logs |
| `oi up -d [service]` | Start, or restart one service after its config changed |
| `oi run --rm audit migrate` | Apply database migrations |
| `oi run --rm audit record …` | Write one event by hand. Needs `--dev` |
| `oi run --rm audit list [--type T] [--limit N]` | Read the record |
| `oi run --rm audit backfill [--limit N]` | Copy the gateway's calls and refusals into the record |
| `oi run --rm audit export [--date D] [--out DIR]` | Write a day's JSONL and manifest |
| `oi run --rm audit verify <file> [--anchor F]` | Check an export, optionally against an anchor |
| `oi run --rm audit anchor [--out DIR]` | Write the chain head to a file |
| `oi run --rm audit purge --before D [--reason R] [--dry-run]` | Delete events past retention, leaving a tombstone |
| `oi run --rm keys` | Mint or update every role's key from the manifest |
| `oi run --rm keys --set-budget ROLE AMOUNT` | Cap one role now |
| `oi run --rm keys --probe ROLE [--as-model M]` | One small call with that role's key |
| `./deploy/cloud/ship/oi-core-ship.sh anchors\|exports` | Ship an artifact and prove it arrived |
| `systemctl list-timers 'oi-core-*'` | The shipping schedule |
| `journalctl -u oi-core-anchor.service -n 40` | What the anchor job did |

## 23. What lives where on the machine

| Path | What is in it |
|---|---|
| `/opt/oi-core` | The repository |
| `/opt/oi-core/rendered/env` | Secrets and settings, mode 600, never committed |
| `/opt/oi-core/rendered/keys.json` | Per-role API keys, mode 600 |
| `/opt/oi-core/rendered/litellm.yaml` | The gateway's model list |
| `/opt/oi-core/exports/<date>/` | A day's JSONL and manifest |
| `/opt/oi-core/exports/anchors/` | Anchors written on this machine |
| `/opt/oi-core/data/postgres/` | The database's files |
| `/opt/oi-core/db/migrations/` | Schema migrations, applied by `audit migrate` |
| `/etc/systemd/system/oi-core-*.{service,timer}` | The shipping schedule |

## 24. Glossary

**Anchor.** A small file recording the head of the chain at a moment, shipped off the machine. The
only artifact that can detect rows deleted from the end.

**Backfill.** Copying the gateway's own records into the audit chain.

**Chain.** The audit record's structure: each row hashes the row before it, so edits are detectable.

**Content mode.** What the record keeps for a data class: `hash`, `redacted`, or `full`. The prompt and
completion hashes are recorded whatever the mode.

**Export.** One day's events as a file, with a manifest describing it.

**Lane.** Where inference happens. `local` means a model on the machine, `frontier` means a remote
provider. A build may have one or both.

**Manifest.** The YAML file describing a deployment. The thing a client buys.

**Pack.** A sellable bundle of services.

**Profile.** `cloud`, `office`, or `hybrid`. The difference between the builds.

**Role.** A name a caller asks for, mapped to a model, with its own key and ceiling. Four today:
`front_desk`, `analyst`, `reader`, `coder`.

**Tombstone.** The event a retention purge writes, carrying the chain head before and after, so a gap
is explained.

**Watermark.** The newest purge's cutoff. Backfill will not re-create events older than it.

## 25. Not built yet

| Phase | What it adds |
|---|---|
| C2 | Reading documents: Docling ingestion with OCR and tables, Qdrant, embeddings and reranking, hybrid search, and the verifier that checks every citation |
| C3 | The assistant API: sessions, roles from tokens, streaming, retries, a degraded mode when the provider throttles |
| C4 | The client application: Ask, Do, Review, Activity |
| C5 | Workflows and the first connector, with an approval gate |
| C6 | The agent runtime and the MCP gateway |
| C7 | Ingress, TLS, identity, and the Terraform that creates a client's account |
| C8 | Alarms, backups, a restore drill, the sixty golden questions, vulnerability scanning, drift reporting |
| C9 | The demonstration |

Until C2, this system answers from a model's general knowledge and cannot read a client's files. Until
C4 there is no screen for a client to look at. What exists is the foundation and the evidence, and the
next phase builds the part clients actually ask for first.
