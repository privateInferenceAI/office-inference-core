# Operating manual

For the core system behind every Office Inference deployment. Written for someone who has never seen
this project and is comfortable in a terminal.

Everything here has been run. Where a command's output matters, the manual shows what you should see.
Where something is not built yet, it says so rather than leaving you to discover it.

**About the examples.** Every command output in this manual is real, taken from a development
deployment on 2026-10-11. They were captured at different moments in that deployment's life, so the
event ids, counts and hashes do not agree between sections, and one section may show a state that a later
purge removed. Read the shape and the wording, not the arithmetic. On your deployment the numbers will be
your own, which is the point of a hash chain.

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

Four offerings come out of that, and they are three configurations of one thing:

| Offering | Whose account | Profile | Models |
|---|---|---|---|
| Standard Cloud | The client's | `cloud` | Remote |
| Compliant Cloud | The client's | `cloud`, with a compliance profile | Remote, restricted to forms the compliance profile allows |
| Standard Cloud Hosted | Ours | `cloud` | Remote |
| In-Office | None. Hardware in the client's building | `office` | Local weights only |

`hybrid` is the third profile and not a fifth offering: it is a build with both lanes, which is what a
client buys when they want documents handled locally and general questions answered by a frontier model.
This manual covers the core that all four share.

## 2. The problem it solves

A small business wants to use AI on its own material. Three things stop them.

They cannot tell what the system did. "The AI answered" is not an answer their auditor accepts.
They cannot control what it costs when something goes wrong, because a loop of AI calls spends money
the way a leak spends water. And they cannot always let their data go to a third party, for legal
reasons or because their client would object.

This system answers all three. Every AI call goes through one gateway that knows which role asked,
what that role may spend, and which model it may reach. The gateway writes down every call and every
refusal as it happens, including the refusals, and a tool copies that record into one that cannot be
quietly edited. That record leaves the machine on a schedule, so a client can check it without trusting
us.

Two qualifications belong next to that promise rather than nine sections later. The gateway's own log is
written immediately; the tamper-evident record gets the day's calls when a job copies them in, which
happens on a timer. And "cannot be quietly edited" is a property of the chain plus the shipped anchors,
which together make an edit detectable rather than impossible. Section 13 shows exactly what each check
proves and, more usefully, what it does not.

## 3. What runs where

| Piece | What it is | In this deployment |
|---|---|---|
| The machine | One AWS EC2 instance running Docker. No GPU, because the models live at the provider | `i-096bebd46308d1673`, an m7i.4xlarge: 16 cores, 64 GB RAM, 200 GB disk, Ubuntu 24.04 |
| `postgres` | The database. Holds the gateway's keys and spend, and our own audit record | A container, on the box |
| `litellm` | The gateway. Every AI call goes through it. It authenticates the caller, checks the role's budget and model permissions, calls the model, and writes down what it cost | A container, on the box |
| `audit` | The tool that writes and checks the audit record. Runs when you call it, not as a service | A container, started on demand |
| `keys` | The tool that mints per-role API keys and their ceilings | A container, started on demand |
| `llamacpp` | A local model server, used by the office build only | A container, switched off in the cloud build by a compose profile |
| The models | Frontier AI models, reached through the provider's API | Amazon Bedrock, us-east-1: four models, Nova and DeepSeek. The Claude models appear in the lifecycle table and the example manifests, not in this deployment |
| The bucket | Where the evidence goes, so the machine cannot erase it | `oi-core-audit-dev`, with Object Lock |

Three terms in that table carry weight, so here they are plainly.

**A deployment** is one client's installed copy of the core system: one manifest, one database, one
bucket, one `deployment_id` that appears in every audit row and in every key alias. This manual's
example deployment is a development one in our own account. "This deployment" throughout means that
one, and the steps for a second client in section 19 are the steps for making another.

**Object Lock** is an S3 setting that stops objects being deleted or overwritten for a retention period,
enforced by the storage service rather than by the account using it. It can only be switched on when a
bucket is created, which is why `docs/audit-evidence.md` treats it as a decision rather than a setting.
It is what makes the shipped evidence worth something: the machine uploads, and cannot take back.

**A data class** is how sensitive a piece of material is: `public`, `internal`, `confidential`, or
`regulated`. It decides what the record may keep for it, and under a compliance profile it also decides
which models may see it. Nothing labels a request with a class yet, so today a class is a decision the
caller makes when it records an event rather than something the system infers.

No container publishes a port. The containers talk to each other over a private Docker network called
`ai-net`, and nothing of ours is reachable from outside the machine.

The machine itself listens on one port, 22, for SSH, and the security group allows it from a single
address. That is the whole inbound surface: no gateway port, no database port, no dashboard. The state
to aim for is that the only way in is SSH or a Systems Manager session, and the only thing listening is
the door you came through.

## 4. The two paths: a request, and its record

**A request.** Something wants an answer. It authenticates with a key, and the key belongs to a role.
Four are configured, and the names describe the job rather than the model:

| Role | What it is for | Model in this deployment |
|---|---|---|
| `front_desk` | The first thing a question meets. Cheap and fast, for triage and short answers | `amazon.nova-micro-v1:0` |
| `analyst` | The workhorse. Reasoning over retrieved material, where most answers come from | `amazon.nova-lite-v1:0` |
| `reader` | Long documents. Summarising material too large for the others | `amazon.nova-pro-v1:0` |
| `coder` | Code and structured output | `deepseek.v3.2` |

The gateway checks that the key may call the model that role maps to, checks that the role is inside its
spending ceiling, calls the model, and returns the answer. The caller never names a model. It names a
role, which is what lets you change which model answers without touching the caller.

**On a hybrid build the names change.** The local entry keeps the plain role name and the remote one is
suffixed, so a hybrid deployment answers `analyst` from the local model and `analyst-frontier` from the
provider. A cloud build has one entry per role under the plain name, because there is no local lane to
distinguish. Anything naming a model has to name the right one for the build it runs in.

**The record.** The gateway writes every call to its own spend log, including the ones it refused. A
tool called `audit` copies those rows into our own record, which is a chain: each row contains a hash
of the row before it, so any edit or deletion breaks the chain in a way a reader can detect. Each day
that record exports as a file with a manifest, and every hour a small file recording the head of the
chain is shipped to a bucket the machine can write to but not delete from. That last part is what
makes the record evidence rather than a log file: the machine cannot cover its tracks without the
missing piece being visible somewhere it does not control.

### One request, end to end, in time order

Two paths described separately are easy to nod along to and hard to hold in your head. Here is one
request and the twelve hours after it, which is the whole system in one pass.

| Time | What happens | What you could look at |
|---|---|---|
| 09:14:02 | A component asks the gateway for the `analyst` role, presenting its key. It never names a model | Nothing yet |
| 09:14:02 | The gateway matches the key to its alias, `oi-core-dev-cloud:analyst`, which gives it the deployment and the role. It checks the key may call the analyst model, and that the role is inside its ceiling | The key and its limits: `oi run --rm keys` |
| 09:14:02 | The gateway calls the provider with the deployment's credentials, which come from the machine's instance role. No key is stored on disk for this | The policy that allows it, on the instance role |
| 09:14:03 | The provider answers. The gateway returns the answer and writes a row to its own spend log: model, tokens, cost, status, and the key's alias | The spend log, in Postgres |
| 09:20:00 | Nothing runs. The call sits in the gateway's log and nowhere else | |
| 10:00:00 | The anchor timer fires. The audit tool hashes the current head of the chain, writes a 276-byte file, and the shipping job uploads it and reads it back | `journalctl -u oi-core-anchor.service` |
| 11:00:00 | The anchor fires again. The chain has not changed, so it writes the same filename, and the bucket keeps both versions | `aws s3api list-object-versions` |
| 15:41:07 | A component asks for a model its key is not scoped to. The gateway refuses before the provider is ever called, and writes that refusal to the spend log with the reason | The refusal, with its error code |
| 00:00:00 | The export timer fires. The job runs `backfill`, which copies every spend-log row from the last day into the chain, including the refusal, and marks the refusal `denied` rather than as a call that succeeded. Then it exports the day that just ended, and ships the file and its manifest | `journalctl -u oi-core-export.service` |
| 00:00:20 | The bucket holds the day's file, its manifest, and the anchors written through the day. The machine can add to it and read it, and cannot delete from it | `aws s3api list-object-versions --bucket oi-core-audit-dev` |
| Sometime later | The client's auditor runs two commands. One checks every row in the file against the row before it. The other checks the file against an anchor written while the day was happening, which is what catches rows deleted from the end | The output of `audit verify`, or a failure naming the line |

The timeline's numbers are illustrative and come from one development run. Later sections quote other
runs, so counts differ between them on purpose.

Three things about that timeline are worth noticing, because they are the design.

The gateway is the only thing that talks to a model, so there is exactly one place where permission,
cost, and recording are decided. A refusal happens there, before any money is spent, and it is recorded
there too.

The record is written by the tool rather than by the caller, so a component that forgets to log
something cannot erase it from the record: the gateway's own log is the source, and the tool copies
from it.

The evidence leaves the machine on a schedule rather than when someone remembers. The machine does not
hold the credentials to delete it afterwards, which is what turns a log into evidence.

## 5. The manifest is the contract

A client's deployment is described by one YAML file called a manifest. It names the client, the
profile (cloud, office, or hybrid), the roles and their models, the spending ceilings, what content
the record may keep, where evidence ships, and any compliance profile.

Nothing is hand-configured on a server. The renderer reads the manifest and writes four files: an
environment file holding secrets and settings, the gateway's model list, the list of services the packs
add up to, and a normalized copy of the manifest. Everything downstream reads those, so changing a deployment means changing the manifest and
re-rendering, not editing files in place.

A validator refuses a manifest that would break a promise. It knows, for example, that an office build
cannot use remote models, that a regulated deployment may not route a prompt globally, and that a
build with remote models must declare spending ceilings.

A word on `schemas/manifest.schema.json`, because it is easy to overrate. It is the machine-readable
contract and the reference for what a field means, and **nothing enforces it yet**. The gate is the
hand-written Python in `scripts/validate_manifests.py`, which implements about twenty rules and is the
authoritative list in section 16. A field that the schema forbids but no Python rule mentions will pass
validation today. Enforcing the schema is a tracked gap rather than a done thing, and the manual says
so rather than implying otherwise.

## 6. What exists today, and what does not

| Working now | Not built yet |
|---|---|
| The stack runs from a manifest, in the client's account, on CPU | Reading the client's documents. Ingestion and retrieval are C2 |
| Four roles on the provider's models, through one gateway | A user interface of any kind. C4 |
| Per-role keys with model permissions and hard spending ceilings | Per-user permissions. Today roles are all there is |
| An append-only audit record with a hash chain | Alarms that page someone, and a restore drill. C8 |
| Daily export, hourly anchor, shipped to a bucket with Object Lock | Loading the sixty golden questions that judge answer quality. C8 |
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

**One prerequisite on your workstation:** the AWS CLI needs the Session Manager plugin, which is a
separate download from the CLI itself. Without it the command fails with `SessionManagerPlugin is not
found`, which reads like a permissions problem and is not. Install it once per workstation, following
the AWS instructions for your platform.

**How to check the agent, and how not to.** On Ubuntu the agent is installed as a snap, so
`systemctl status amazon-ssm-agent` says `Unit amazon-ssm-agent.service could not be found` and a
`dpkg -l` search finds nothing, while the agent is running perfectly well. That combination has
already fooled one careful reader of an earlier draft. Look at the snap instead, or ask AWS:

```
snap list amazon-ssm-agent
sudo snap services amazon-ssm-agent
aws ssm describe-instance-information --profile oi-credits --region us-east-1 \
  --filters Key=InstanceIds,Values=i-096bebd46308d1673 \
  --query 'InstanceInformationList[].{id:InstanceId,ping:PingStatus,agent:AgentVersion}'
```

`PingStatus: Online` is the answer that matters. A `not-found` from `systemctl` is a property of how
the package ships, not a fault.

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

You should see `postgres` healthy and `litellm` running. Compose prints seven columns; the middle ones
are trimmed here for width, and `PORTS` shows the exposed ports without a host binding, which is the
thing to look at when you want to know whether anything is reachable:

```
NAME       IMAGE                    COMMAND     SERVICE    CREATED      STATUS
litellm    ghcr.io/berriai/litellm…            litellm    4 hours ago  Up 4 hours
postgres   postgres:16-alpine                   postgres   4 hours ago  Up 4 hours (healthy)
```

`ps` shows only the two long-running services. `audit` and `keys` are behind the compose `tools`
profile, so they never appear here and `oi up -d` never starts them. They run when you call them.

Two facts about the output: a `docker compose run` prints its own preamble (`Container postgres
Running`, `Container oi-core-audit-run-<id> Creating`), so every example in this manual that shows a
tool's output has those lines above it, and they are not shown again below. And `up -d` prints
`Container <name> Running` when it decides nothing needs doing, which is not the same as having
restarted anything.

Then ask the gateway whether it is alive, from a throwaway container on the same private network,
because no container publishes a port:

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

**The alias exists only in the shell where you typed it.** In `ssh host 'command'`, in a script, or in
a cron entry, write the compose command in full, because a non-interactive bash does not expand
aliases and you will get `oi: command not found`. That failure looks like the tool is missing rather
than the alias.

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

`--as-model` is only read by `--probe`, and on its own it now exits with an error rather than doing
nothing quietly, which is what it used to do.

**Exit codes, because they are the opposite of what you might script.** A probe that gets an answer
exits 0. A probe the gateway refuses exits 1, so a monitoring script must read a 422 or a 403 as the
control working rather than as the check failing.

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

**The choices you can make:**

| Flag | Values, and what it means |
|---|---|
| `--type` | `model_call`, `tool_call`, `approval`, `access`, `config_change`, `login`. Required |
| `--actor` | Who did it. Required, and refused without `--dev` |
| `--actor-role` | Their role, for a human: `admin`, `staff`, or a role name |
| `--dev` | Says out loud that a human typed this actor rather than a session supplying it |
| `--data-class` | `public`, `internal`, `confidential`, `regulated`. Default `internal`. It decides the content mode |
| `--outcome` | `ok`, `denied`, `error`, `pending`. Default `ok` |
| `--tool` | The tool involved, when the event is a `tool_call` |
| `--model` | The model involved, when the event is a `model_call` |
| `--request-id` | Ties a hand-written event to the gateway's call. The record is unique on it, so a later `backfill` will not create a duplicate |
| `--approval` | JSON describing an approval, for the `approval` type |
| `--payload` | JSON, or `@file` to read a file, or `-` to read standard input |
| `--prompt`, `--completion` | The text sent and returned. Both hashes are always recorded; the text is kept according to the content mode |
| `--deployment-id` | Overrides the deployment this event belongs to. Rarely needed |

**Two warnings go to standard error**, so they appear in your terminal but not in the JSON on standard
output. One says you are in development mode. The other appears if you pass `--store-payload` while the
content mode would hash: the flag wins, so the event keeps more than the deployment's policy asks for,
and the warning is there to make that a decision rather than an accident.

**The payload forms, in practice:**

```
--payload '{"document":"expense-policy.pdf"}'      # inline
--payload @/tmp/event.json                          # from a file
echo '{"document":"x.pdf"}' | ... --payload -       # from standard input
```

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

**How the copied rows read.** A backfilled call is attributed to `gateway-key:` followed by eight
characters of the key's hash, and the role comes from the key's alias, so a refusal reads as the role
that asked rather than as an anonymous key. A call the gateway refused is recorded as `denied` when the
gateway answered 4xx and `error` for anything else, with the provider's error code and class, the
normalized error, the budget figures when the refusal was about money, and the message hashed rather
than stored, because error strings are the field most likely to quote a prompt back.

**What a refusal looks like in the record.** This is the row a budget refusal produced, read back with
`audit list --type model_call`:

```
    id  when                         type           actor                   class          outcome  detail
    14  2026-10-11T03:40:29.633000Z  model_call     gateway-key:50b578d6    internal       denied   bedrock/deepseek.v3.2
```

`denied` rather than `ok` is the whole point. The gateway refused the call before spending anything, and
the record says so, attributed to the role whose key asked. The provider's error code, its class, the
normalized error, the budget figures, and a hash of the message are in the row's payload:

```
docker exec postgres psql -U oicore -d oicore -c \
  "select jsonb_pretty(payload) from audit_events where id = 14;"
```

**A view built for reading.** `audit_feed` renders each event as a plain-language line, which is what a
future Activity screen will use. Reading it directly is the quickest way to see what happened without
decoding JSON:

```
docker exec postgres psql -U oicore -d oicore -c "select id, summary from audit_feed order by id desc limit 10;"
```

```
 id |                 summary
----+-------------------------------------------
 34 | bedrock/amazon.nova-lite-v1:0 was refused
 33 | bedrock/amazon.nova-micro-v1:0 answered
 32 | bedrock/amazon.nova-pro-v1:0 was refused
 31 | bedrock/amazon.nova-lite-v1:0 answered
```

The view is thirteen columns wide, so ask for the two you want to read. A refused call reads as
refused rather than answered, which it did not before migration 006.

**What the content modes look like side by side.** Three events recorded against the same policy, one per
class, and what the record kept for each:

```
docker exec postgres psql -U oicore -d oicore -c "
  select id, data_class, payload is null as no_content, left(prompt_sha256, 12) as prompt_hash,
         coalesce(payload ->> 'prompt', '(none)') as stored_prompt
  from audit_events where event_type = 'model_call' and prompt_sha256 is not null
  order by id limit 3;"
```

```
 id | data_class | no_content | prompt_hash  |                        stored_prompt
----+------------+------------+--------------+-------------------------------------------------------------
 15 | public     | f          | a20d5f4b90a6 | what is the mileage rate?
 16 | internal   | f          | 7a098dd2ab2f | email the client on [REDACTED-EMAIL] about [REDACTED-PHONE]
 17 | regulated  | t          | d9cb8186a1b2 | (none)
```

`no_content` and `stored_prompt` are aliases this query invents, not columns; the table has `payload`
and `prompt_sha256`, and a null payload is what hash mode leaves behind.

`public` kept the prompt and its hash, `internal` kept it with the identifiers masked, and `regulated`
kept only the hash. That third row is what `hash` mode means in practice: the record can prove what was
said, if someone later produces the text, without holding it.

### Exporting a day

```
oi run --rm audit export --date 2026-10-11
```

```
exported 23 event(s) for 2026-10-11
  /app/exports/2026-10-11/audit-events.jsonl
  /app/exports/2026-10-11/manifest.json
  last row hash 3e7fb0f1d35d6f7218932be29f00aa5943555a18ad9a0bb7841158e477c5098a
```

On the host that is `/opt/oi-core/exports/2026-10-11/`. The JSONL file holds one event per line. The
manifest holds the count, the first and last ids, the hash the first row chains back to, and the hash
of the file itself. Without `--date` you get today in UTC.

**An export is a snapshot of the record, not a view of it.** The record only holds the gateway's calls
after a `backfill`, so exporting without one ships whatever the last backfill happened to catch. That
is how the first export this project shipped came to hold two rows of a twelve-row day, and the file
verified cleanly, because nothing was wrong with it: it was a faithful export of a record nobody had
refreshed.

The shipping job now runs `backfill` before every export for exactly that reason. If you export by
hand, run `backfill` first.

**Re-exporting a date replaces that day's file on this machine**, because the day keeps growing as calls
happen. The copy in the bucket does not change, because uploads there are versioned and locked. So when
someone asks what the record said, the shipped version is the frozen one and the local file is the
working copy. Export once at the end of a day, and know which of the two you are handing over.

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
anchored 23 event(s), head 3e7fb0f1d35d…
  /app/exports/anchors/anchor-2026-10-11-00000023.json
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
ok   /app/exports/2026-10-11/audit-events.jsonl: 23 event(s), chain intact
     note: matches the anchor written 2026-10-11T04:49:16.784332+00:00 (3e7fb0f1d35d…, 23 rows)
     note: chained from row 10 (6928b2669fd9…), which is not in this file
```

That second note appears when the export's first row chains back to a row the file does not contain,
which is the ordinary case once a purge has removed the beginning of the chain. It is information, not
a failure: the check compares the first row's `prev_hash` against the anchor, not against nothing.

With no anchor, the command says plainly that completeness cannot be shown, because deleting rows from
the end and re-exporting would still verify. That sentence is the honest one, and it is why the
shipping job in section 14 exists.

**What the anchor check proves, exactly.** It catches an export that is missing the row the anchor
recorded, and an export with fewer rows than the anchor's count. It does not prove that nothing
written *after* the anchored row has gone missing, so the accurate claim is "the chain still contains
the state that existed when this anchor was written", not "nothing is missing". Anchor often enough
that the gap between two anchors is small, which is why the job runs hourly.

### A retention purge

A retention window is a date in the past, so the exact command depends on what the record holds. Two
forms, and both are dry runs:

```
# everything older than 90 days, which is what a scheduled purge would do
oi run --rm audit purge --before "$(date -u -d '90 days ago' +%Y-%m-%d)" --reason "retention: 90 days" --dry-run

# a specific moment, which is easier to reason about while learning
oi run --rm audit purge --before 2026-10-11T03:00 --reason "retention: 90 days" --dry-run
```

```
would purge 10 event(s) before 2026-10-11T03:00 (ids 1..10)
  the tombstone would chain from 98ea3b7877e3d2c9a71cf505b93533ec79f4bab10a631c3794c711407a3e8499
```

**When the window contains nothing, that is a success, not an error**, and it says so:

```
nothing to purge before 2026-10-11T03:00: the chain head is 7d106dcb560d…
```

Everything in the examples below is from one run on 2026-10-11, and the ids and hashes are that run's.
On your box they will differ, which is the point of a chain.

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

`--actor` records who or what ran the purge, and defaults to `retention-job`, which is what an
unattended run should say. Give it a person's name when a person is doing it.

A purge is a deliberate hole in the chain. The tombstone is what makes the hole explained instead of
suspicious, and it carries both hashes: `prev_hash` is the head before the purge and `row_hash` is the
head after. The tombstone is written first, inside the same transaction as the deletion, so either both
happen or neither does.

**What the record looks like afterwards**, which is worth seeing before running a destructive command.
The surviving rows and the tombstone, with the hashes that tie them together:

```
 id | event_type |          occurred_at          |     prev     |     hash
----+------------+-------------------------------+--------------+--------------
 11 | access     | 2026-10-11 03:23:21.203369+00 | 6928b2669fd9 | 98ea3b7877e3
 13 | purge      | 2026-10-11 03:33:36.544593+00 | 98ea3b7877e3 | 6df2d45eab2d
```

Row 11 chains to a purged row that is no longer in the table, and row 13 chains to row 11. The chain is
intact across the gap, and the tombstone is the row that explains why the ids jump from 11 to 13. Note
that ids can have gaps even without a purge, because a failed insert consumes one.

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

The numbers below come from one run. Sizes and hashes differ per anchor, and two anchors of the same
chain differ by a byte or two depending on how many digits the timestamps carry, so read the shape
rather than the values.

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

**Reading an anchor's name.** `anchor-<date>-<eight digits>.json`, where the digits are the number of
events in the chain when it was written. `00000006` means six events existed; an hour later, with no new
events, the same name comes back.

**A filename that repeats on purpose.** An anchor is named for its date and the number of events in
the chain, so an hourly run over an unchanged chain writes the same key: `anchor-2026-10-11-00000006.json`
at 03:00 and again at 04:00, with the object's version history holding both. That is deliberate. The
filename is a fingerprint of the chain's state, so two identical names mean the chain did not change,
and `aws s3 ls` showing one object is not a lost upload. To see the versions:

```
aws s3api list-object-versions --bucket oi-core-audit-dev --prefix anchors/ \
  --profile oi-credits --query 'Versions[].{key:Key,written:LastModified}' --output table
```

**Which day it ships.** The job runs at midnight and exports the day that just ended, in UTC. The
first version of it took the current date, which at midnight is the day that is starting, so it would
have shipped an empty file every night.

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
| Weekly | The record's chain is intact | `oi run --rm audit verify /app/exports/2026-10-11/audit-events.jsonl` (a UTC date, and export the day first) |
| Weekly | Nothing was refused unexpectedly | `oi run --rm audit list --type model_call --limit 50` |
| Weekly | Timers have been firing | `journalctl -u oi-core-anchor.service -n 20` |
| Monthly | Model lifecycle | On the box, after a `git pull`, so it reads the manifests this deployment renders from: `python3 scripts/check_model_lifecycle.py` |
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
| `packs` | Which sellable bundles are switched on. Every sellable pack declares `requires: [core]` in the catalog, and the validator follows those edges, so `core` is implied rather than listed. A pack list that never reaches `core` is refused, because it would leave the deployment with no gateway |
| `models.<role>.frontier` | The model a role reaches when inference is remote. The `bedrock/` prefix is required |
| `models.<role>.local` | The model file a role reaches when inference is local, used by the office build |
| `lanes.frontier.enabled` | Whether remote inference exists at all. Must be false on an office build |
| `lanes.frontier.account_owner` | `client`, or `office_inference` for our own machines and the hosted tier |
| `lanes.frontier.data_classes_allowed` | Which data classes may reach a remote model |
| `content.default`, `content.classes` | What the record keeps per data class: `hash`, `redacted`, or `full` |
| `budgets.period_days`, `budgets.roles` | The ceiling per role, in dollars, over a period in days |
| `compliance.profile` | `none`, `contractual`, `hipaa`, `glba`, `sec-17a4`, `itar-cui`, `ferpa`, `pci` |
| `compliance.audit_retention_days` | How long the record is kept. Six years is enforced for `sec-17a4` |
| `compliance.data_residency` | Required by any compliance profile other than `none` or `contractual` on a cloud or hybrid build, so the contract records where the client's data must stay. The validator checks that it is named rather than comparing it with anything, so the global-profile refusal follows from the compliance profile itself |
| `compliance.baa_with_office_inference` | Required by any compliance profile other than `none` or `contractual` on a hardware build, and it must be true. We need a signed agreement before holding that data on a client's machine |
| `lanes.frontier.redaction` | Whether prompts are redacted before leaving. One validator rule refuses `false` with `regulated` on a cloud build, and nothing redacts yet |
| `lanes.frontier.requires_approval` | A human approval step before a remote call. In the schema, read by no rule and no service; C3 owns the gate |
| `lanes.frontier.provider` | `bedrock` (the schema also allows `api`). Required when the lane is on, and omitted entirely by a build with no remote lane |
| `offsite.provider`, `offsite.region`, `offsite.account_owner` | Where evidence ships: `s3`, the Region, and whose account the bucket is in |
| `offsite` | Where evidence ships, and which artifacts may leave: `anchors`, `exports`, `backups`. `backups` is accepted today and nothing consumes it until C8 |
| `support.response_window`, `support.remote_access`, `support.drift_reporting` | What the client is promised about support, recorded in the contract. No service reads them |
| `compliance.baa_with_provider`, `compliance.worm_storage` | Recorded intent for agreements and write-once storage. Described in the schema and read by nothing |
| `models.<role>.fallback` | Intended as a second model for a role, for when the first is unavailable. It renders nothing and nothing routes to it |
| `integrations`, `interface` | Reserved and unimplemented |

Those last four rows matter when you write a manifest for a client: a field can be in the schema, pass
validation, and still do nothing, because the schema describes the contract we intend while the services
implement what exists. The fields that change behaviour today are the ones named in the sections above:
which models a role uses, whether a lane is on, the budgets, the content policy, the compliance profile,
and the offsite block. Section 25 lists what exists.

**What the validator refuses.** The script is the authoritative list; this is what it covers, grouped
by what the rule protects.

*Promises to the client:*

- An office build with a frontier lane, since an AWS account is not what makes inference remote.
- A remote model with no spending ceiling, because a remote call costs money and an uncapped role is
  how a loop empties an account.
- `offsite.ship` naming anything but `anchors`, `exports`, or `backups`. Documents and prompts can never
  leave the building, and the list is how that stays true.
- An unknown pack id, so a purchase cannot quietly under-deploy.
- A missing `client.name` or `client.deployment_id`, or an id with characters outside letters, digits,
  and hyphens.
- A missing `models.analyst`, `models.front_desk`, or `models.reader`. Every role entry must also
  carry a `local` key, written as an empty string when the build has no local lane. The key is required
  and the value may be empty, which says "this deployment has no local model for this role" rather than
  leaving a reader to guess whether it was forgotten.

*Residency and model eligibility:*

- A global inference profile under any compliance profile other than `none` or `contractual`, because
  it can route a prompt to any Region. An *inference profile* is the provider's name for a routing
  layer in front of a model: a Region-scoped one such as `us.anthropic.claude-sonnet-5` keeps requests
  inside a geography, and a `global.` one may serve them from anywhere. A model offered only through a
  profile cannot be pinned to one Region, which is what disqualifies it for a client whose contract
  names one. The lifecycle table records which of our models this affects.
- Fable models. Their provider terms permit only retention modes that let the provider keep inputs and
  outputs and have people review them, which is incompatible with a promise that a client's material is
  not held by a third party. The validator refuses them outright rather than warning.
- The `frontier-lane` pack on the office profile.
- A compliance profile on a cloud or hybrid build with no `compliance.data_residency`, since residency
  decides which model forms are allowed.
- `regulated` in `data_classes_allowed` on a cloud build with `redaction: false`.
- A frontier lane enabled with an empty `data_classes_allowed`, which would allow everything.

*Agreements and accountability:*

- A compliance profile other than `none` or `contractual` on a hardware build without
  `compliance.baa_with_office_inference`, because we need a signed agreement to hold that data.
- `account_owner: office_inference` under any compliance profile. The hosted tier is standard-only, so
  a compliance claim belongs in the client's own account.
- `itar-cui` on anything but the office profile, and `itar-cui` with any offsite destination at all.
- A `sec-17a4` deployment with under six years of retention.

*Shape of the data:*

- An unknown role in `budgets.roles`, an unknown data class or mode in the content policy, a budget
  that is not positive, a `period_days` that is not a positive whole number.
- An unknown `offsite.provider`, a bucket name that is not a valid S3 name, or an offsite block with no
  `ship` list.
- An unknown compliance profile name.

**Three fields are policy that nothing enforces yet.** `lanes.frontier.data_classes_allowed` is
rendered into the environment and read by no service at call time, because nothing labels a request
with its data class. `lanes.frontier.requires_approval` is in the schema, read by no validator rule and
by no service. `lanes.frontier.redaction` is checked by one validator rule, which refuses `false`
together with `regulated` on a cloud build, and **nothing redacts anything on the way out**; the
redaction service is sold in the compliance pack and is not built. What redacts today is the record's
own content mode, which decides what is kept rather than what is sent.

All three become controls in C2 and C3. Until then, treat them as the client's intent recorded in the
contract rather than something that will stop a call.

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

`--root` sets the deployment root the outputs are written for, and it is what lets you render for
another machine from this one. `--out` overrides the output directory.

The three tools built on `argparse` answer `--help`, which is the fastest way to see their options. The
other three scripts do not, and will try to treat `--help` as a filename:

```
python3 scripts/render_manifest.py --help
oi run --rm audit --help
oi run --rm keys --help
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

A role is a name that maps to a model, with its own key and ceiling. Roles are a naming convention
rather than a closed set, so a deployment can add as many as it needs. This example adds a `summarizer`
role on the cheapest model, for a component that should only ever write short summaries. It takes four
edits and five commands.

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

**If the model is one this deployment has not used before, two more things are needed**, and both fail
late rather than at validation:

- An entry in `data/model-lifecycle.json`, or `check_model_lifecycle.py` fails and names the model. The
  format and the refresh procedure are in `docs/model-lifecycle.md`.
- The model in the instance role's Bedrock policy. That policy allows exactly four model ARNs today, so
  a new one answers `AccessDenied` at call time and nothing before that catches it. Section 18 shows
  the check.

**3. Decide what the record keeps for it.** It inherits `content.default`, which is `hash`. If you want
something else for its events, that is a per-data-class decision rather than per-role, so leave it
alone unless the client asked otherwise.

**4. Validate and render:**

```
python3 scripts/validate_manifests.py
python3 scripts/render_manifest.py --manifest manifests/dev-cloud.yaml
```

The render now reports `gateway 5 model entries`.

**5. Restart the gateway** so it reads the new model list. The file is mounted rather than baked in,
and the gateway reads it once at startup:

```
oi restart litellm
```

`oi up -d litellm` is **not** the command here, and this is worth understanding rather than
memorising. Compose recreates a container when the service definition changes, and a mounted file
changing underneath a running container is not a change it can see. So `up -d` prints
`Container litellm Running` and does nothing, which looks like success. `restart` restarts the process,
which re-reads the file. LiteLLM does not watch its own config, which was verified by appending a model
to a mounted config in a throwaway container and watching the model list stay unchanged.

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
oi restart litellm
oi run --rm keys --probe analyst
```

Nothing in the application changes, because applications ask for the analyst role rather than for a
model. Two rules apply. If the client's contract names a single region, an inference-profile-only
model is disqualified and the validator will say so. And a model swap means rerunning the golden
questions before a client sees the new answers, which is C8's work rather than something to skip.

If the model is unknown to `data/model-lifecycle.json`, the lifecycle check fails and names it. Add it
with its status and a replacement, following `docs/model-lifecycle.md`.

Then confirm the instance may actually invoke it:

```
aws iam get-policy-version --profile oi-credits \
  --policy-arn arn:aws:iam::878451098165:policy/oi-core-dev-bedrock-invoke \
  --version-id v1 --query 'PolicyVersion.Document.Statement[].Resource'
```

`get-role-policy` returns `NoSuchEntity` for this, because the policy is attached to the role rather
than written into it, and the error reads like the policy does not exist.

If the new model's ARN is not in that list, add it, or the first call fails with `AccessDenied` and the
gateway will look like the problem.

One ordering note, which sounds worse than it is. The renderer rewrites `rendered/litellm.yaml` while
the gateway has that file mounted read-only. The running container keeps the old contents, because a
process that has already read its config does not see the file change, and a render during traffic
disturbs nothing. What it does mean is that the change is not live until you restart, so a render on its
own can look like it did nothing.

## 19. Standing up a second deployment for a new client

The same repository serves every client. A new deployment is a new manifest, a new account, and a new
bucket.

1. Copy `manifests/dev-cloud.yaml` to `manifests/<client>.yaml`.
2. Change `client.name` and `client.deployment_id`. The id is used in every audit row and in every key
   alias, so it must be unique. **Nothing enforces that for you**: no validator rule compares ids across
   manifests, and nothing checks the id against a deployment already running. Two clients sharing an id
   would produce a record that cannot be separated afterwards, so this is an operator's job until C7
   puts the deployments under one piece of infrastructure.
3. Set the models, ceilings, content policy, and compliance profile the client bought.
4. Point `offsite.bucket` at a bucket in the client's account, with Object Lock enabled at creation,
   because that cannot be turned on afterwards.
5. Validate and render on the new machine, then mint the keys there.
6. Ship the evidence, by installing the timers. The unit files hard-code `/opt/oi-core` and the
   `ubuntu` user, so they work as they are only at that path with that user:

```
sudo cp deploy/cloud/systemd/oi-core-* /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now oi-core-anchor.timer oi-core-export.timer
systemctl list-timers 'oi-core-*'
```

Expect two timers with a NEXT time within the hour and within the day. Both are `Persistent`, so a run
missed while the machine was stopped happens once at the next start.

The one thing that is not automatic yet is the account itself. Terraform for the VPC, the bucket, the
IAM role, and the rest is C7, and today those are created by hand. `docs/audit-evidence.md` records
what the bucket needs and why.

## 20. Adding a service or a pack

**A service** is a container with a job. `services/_template/` holds the shape: a `service.yaml`
declaring what it is, and a README describing the convention. `services/audit/` is the worked example,
with a `Dockerfile`, the code, and its own README.

Four places to touch, which is the convention rather than a suggestion, because the last three are how
anything else finds the service:

1. `services/<name>/` with its code, `Dockerfile`, and `service.yaml`.
2. `deploy/office/compose/docker-compose.yml`, with a profile.
3. `packs/catalog.yaml`, in the pack that sells it.
4. `docs/service-inventory.md`, which is the list of what exists and which build each service runs in. Use `tools` for something that runs when
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

**The gateway is not answering.** Read the logs, then check the two things that tell a fault from
startup noise:

```
docker logs litellm --tail 40
docker inspect litellm --format 'restarts={{.RestartCount}} started={{.State.StartedAt}}'
docker run --rm --network ai-net curlimages/curl -sS http://litellm:4000/health/liveliness
```

**Do not read `Database migration cannot proceed. Cannot automatically baseline this database` as the
fault.** On this box it prints thirteen times while a newly created container starts, then the service
comes up and answers, and it does not appear again on ordinary restarts. It has misled one careful
reader of this manual already. It becomes the answer only alongside a rising `RestartCount` and a
liveliness check that stays silent a minute after the container starts.

If that is what you have, something created tables in the gateway's own database that its migration tool
does not recognise. That happened once, through a Postgres first-boot mount running our migrations into
the gateway's schema. Check what is there:

```
docker exec postgres psql -U oicore -d oicore -c '\dt'
```

You are looking for our tables, `audit_events` and `schema_migrations`, inside the database the gateway
owns. They belong to us and should live only in ours.

**A curl prints nothing at all.** `-s` hides connection errors. Use `-sS`, or drop `-s`, and you get
`curl: (7) Failed to connect` instead of silence.

**`AccessDenied` on a model call.** The role's policy and the model id disagree. Compare the id in
`rendered/litellm.yaml` with the policy attached to the instance role.

**`Unable to locate credentials` from the gateway.** The container cannot reach the instance role
through the metadata service, because a request from inside a container is one hop further than the
default limit allows. Confirm it, then raise it:

Run these from a workstation, not from the box: the instance carries no AWS CLI and no credentials for
one, which is deliberate, and an operator mid-incident should not discover that with a
`command not found`.

```
aws ec2 describe-instances --profile oi-credits --region us-east-1 \
  --instance-ids i-096bebd46308d1673 \
  --query 'Reservations[].Instances[].MetadataOptions.HttpPutResponseHopLimit'

aws ec2 modify-instance-metadata-options --profile oi-credits --region us-east-1 \
  --instance-id i-096bebd46308d1673 --http-put-response-hop-limit 2 --http-tokens required
```

The value should read 2, and it does on this instance. If the query returns 1 and the gateway says it
cannot find credentials, that is the cause rather than a guess.

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

**`verify --anchor` fails, saying the anchored row is missing from this export.** Two ordinary causes
before you suspect tampering.

The export predates the anchor. Anchors are written hourly and the export is written daily, so pairing
today's anchor with this morning's export asks whether a file contains rows written after it. The
message names the row it wanted, which is the clue. Export the day again and re-verify.

The anchor belongs to a different chain than the export. Nothing inside either artifact records which
one came first, so compare the anchors by name, where the date and the event count are visible, and fall
back to file modification times. If in doubt, export the day again and verify against the newest anchor:
that pairing is the one that can pass.

Expected after a retention purge: the anchored rows are genuinely gone and the tombstone is the artifact
that explains it. Any other time, with a fresh export and a matching anchor, treat it as a real
finding.

**The timer did not fire while the machine was stopped.** It fires once at the next start, because both
timers are `Persistent`. If it did not fire at all, check `systemctl list-timers 'oi-core-*'` and
`journalctl -u oi-core-anchor.service`.

**Containers suddenly point at an empty database, or exports land in the wrong place.** Check
`OI_ROOT` in `rendered/env` before anything else. Every container path derives from it: the database
files, the exports, the migrations, the rendered configuration. Rendering with a different root, or
running compose with an env file that names one, starts a database against an empty directory. It looks
like data loss and is a path change. Confirm where postgres is actually mounting from:

```
docker inspect postgres --format '{{range .Mounts}}{{.Source}} -> {{.Destination}}{{"\n"}}{{end}}'
```

**The night's work is stale on the machine.** `git pull` on the box, then rebuild the tools. The
repository is the source of truth for code, and `main` is what runs.

```
cd /opt/oi-core
git pull
oi build audit keys
```

Expect the build to finish in seconds when only Python changed, and the next tool run to reflect it. If
a change seems not to have taken, check `git log --oneline -1` on the box against `origin/main` before
looking anywhere else.

Two limits on that check, both of which have burned time here. It cannot see a fix that lives on an
unmerged branch, so a box can be level with `main` and still lack the change you are testing. Run
`git log --oneline -1 origin/main` and confirm the commit you expect is actually in `main`, not just
pushed. And `main` is what runs on a client machine, so nothing reaches a box until it is merged and
pulled, however green the branch is.

**A script works locally and not on the box.** If it reads a file, check the file is there. The lifecycle
table is the example: `data/model-lifecycle.json` is tracked in the repository, and `.gitignore` excludes
`data/*` with a single negation for that file, which has already swallowed it once. On a box that has
not pulled it, `check_model_lifecycle.py` exits with `no lifecycle table at …`.

Two related traps. The box can sit several merges behind without anything looking wrong, so check
`git log --oneline -1` on the box against `origin/main` when a fix seems not to have taken. And the
manual you are reading lives in the repository rather than on the machine, so a fresh box will not have
it until the branch that added it is merged and pulled.

**If `git pull` refuses because the box has local changes**, look at them before discarding anything. A
change can be a real fix made on the box, and it can also be work that is already upstream, applied by
hand rather than merged, which is what happened here and left the checkout unable to pull:

```
git status --short
git stash push -u -m "work applied by hand, check whether it is already upstream"
git pull
git status
git stash show -p stash@{0} | head -40        # anything not already upstream?
git stash drop                                 # only once you are sure
```

---

# Part 6: reference

## 22. The commands, in one table

Two tables: the tools in this repository, then the surrounding commands from the host, AWS and git that
the manual also asks you to type.

### The tools

| Command | What it does |
|---|---|
| `python3 scripts/validate_manifests.py [manifest…]` | Checks manifests and the pack catalog, including residency and budget rules |
| `python3 scripts/render_manifest.py --manifest <file>` | Writes `rendered/env`, `rendered/litellm.yaml`, `rendered/services.txt`, `rendered/manifest.json` |
| `python3 scripts/check_requirements.py` | Reports the register: verified, open, waived, by phase |
| `python3 scripts/check_model_lifecycle.py [manifest…]` | Fails if a manifest names a deprecated, past-EOL, or unknown model |
| `python3 -m pytest tests/ -q` | The test suite |
| `oi ps` | Container status |
| `oi logs <service>` | Container logs |
| `oi up -d` | Start everything, or reconcile a service whose *definition* changed |
| `oi restart <service>` | Restart one service so it re-reads a mounted file, such as a new model list |
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

### The host, the provider, and git

| Command | What it does |
|---|---|
| `ssh -i <key> ubuntu@52.204.202.66` | Get a shell on the machine |
| `aws ssm start-session --profile oi-credits --region us-east-1 --target i-096bebd46308d1673` | A shell with no key and no open port. Needs the Session Manager plugin locally |
| `aws ec2 stop-instances` / `start-instances --instance-ids i-096bebd46308d1673` | Stop paying for the machine, or bring it back. The address survives |
| `aws s3api list-object-versions --bucket oi-core-audit-dev --profile oi-credits` | Every version of every piece of evidence, which is where a repeated anchor filename shows its history |
| `aws iam get-policy-version --policy-arn arn:aws:iam::878451098165:policy/oi-core-dev-bedrock-invoke --version-id v1` | Which models this machine may invoke |
| `aws ec2 describe-instances … --query '…MetadataOptions…'` | The metadata hop limit, when the gateway cannot find credentials |
| `git log --oneline -1`, `git pull`, `git status --short` | What the box is running, and getting it current |
| `git stash push -u`, `git stash show -p`, `git stash drop` | Setting aside local changes so a pull can proceed |
| `docker logs <service> --tail 40` | A container's logs |
| `docker exec postgres psql -U oicore -d oicore -c '<sql>'` | Read the record directly, including the `audit_feed` view |
| `docker inspect <service> --format '…'` | Where a container mounts from, and how often it has restarted |
| `systemctl list-timers 'oi-core-*'`, `journalctl -u <unit>`, `systemctl status <unit>` | The shipping schedule and its history |

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
| `/opt/oi-core/data/model-lifecycle.json` | Model status and replacements, read by the lifecycle check |
| `/etc/systemd/system/oi-core-*.{service,timer}` | The shipping schedule |

### What the renderer writes into `rendered/env`

The env file is generated, mode 600, and holds both secrets and settings. The settings a reader is
most likely to want:

| Variable | What it holds |
|---|---|
| `PROFILE` | `cloud`, `office`, or `hybrid`, as rendered |
| `COMPOSE_PROFILES` | `office` on a non-cloud build, which is what starts the local model server. Absent on cloud |
| `MODEL_ROLES` | The roles this deployment serves |
| `CONTENT_DEFAULT`, `CONTENT_MODE_<CLASS>` | The content policy, one entry per data class. The audit tool reads these |
| `AUDIT_SHIP_BUCKET`, `AUDIT_SHIP_ARTIFACTS`, `AUDIT_SHIP_REGION` | Where the shipping job sends evidence, and which artifacts may leave. Empty means everything stays on the box |
| `AWS_REGION`, `AWS_DEFAULT_REGION` | The Region the containers use for the provider |
| `OI_ROOT` | Where the deployment lives, so the tools find the repository without being told |
| `HOST_UID`, `HOST_GID` | The owner the tools run as, so files they write belong to the operator |
| `GATEWAY_PORT` | The gateway's port inside the network |
| `DEPLOYMENT_ID` | Stamped into every audit row |
| `POSTGRES_USER`, `POSTGRES_PASSWORD`, `POSTGRES_DB`, `LITELLM_MASTER_KEY`, `DATABASE_URL` | Secrets. Generated once and reused on later renders |

Two environment variables are read by the tools but not written by the renderer, which matters when you
point a tool at another deployment: `LITELLM_URL` (default `http://litellm:4000`) and `RENDERED_DIR`.

## 24. Glossary

**Anchor.** A small file recording the head of the chain at a moment, shipped off the machine. The
only artifact that can detect rows deleted from the end.

**Backfill.** Copying the gateway's own records into the audit chain.

**Chain.** The audit record's structure: each row carries a hash of the row before it, so edits are
detectable. Precisely: a row's hash covers thirteen of its columns plus the previous row's hash,
ordering is by id, the first row chains to nothing, and a purge continues the chain rather than
restarting it, because the tombstone is itself a row that chains from the head before the deletion.

The columns the hash covers are the timestamp, event type, actor, actor role, deployment, data class,
outcome, tool, model, request id, payload hash, payload size and the previous hash. **Three columns are
outside it**: `approval`, `prompt_sha256` and `completion_sha256`. Editing one of those in the database
would not break the chain or fail a verify, so today they are recorded evidence rather than chained
evidence. That is a known gap rather than a design choice, tracked as `AUD-HASH-001`, and closing it
means a versioned hash so that rows written before the change still verify.

**Content mode.** What the record keeps for a data class: `hash`, `redacted`, or `full`. The prompt and
completion hashes are recorded whatever the mode.

**Data class.** How sensitive a piece of material is: `public`, `internal`, `confidential`, `regulated`.
It decides the content mode, and under a compliance profile it is meant to decide which models may see
the material, which C2 enforces. Today a caller states it when recording an event rather than the system
inferring it.

**Deployment.** One client's installed copy of the core system, described by one manifest, with one
database, one bucket when evidence ships off the machine, and one `deployment_id` that appears in every
audit row and key alias. The word
this manual is organised around, and until this entry, the one it never defined.

**Export.** One day's events as a file, plus a small descriptor called the export manifest, which is a
different artifact from the deployment manifest despite sharing the word. The export manifest holds the
count, the first and last ids, the hash the first row chains back to, and a hash of the file itself.

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
