# Operating manual

For the core system behind every Office Inference deployment. Written for someone who has never seen
this project and is comfortable in a terminal.

Everything here has been run. Where a command's output matters, the manual shows what you should see.
Where something is not built yet, it says so rather than leaving you to discover it.

**About the examples.** Every command output in this manual is real, taken from a development deployment.
They were captured at different moments in that deployment's life — most of C1's in the days around
2026-10-11, and the C2 pages re-captured on 2026-10-13 after a review found five of them quoting a state
the deployment had left behind. So the event ids, counts and hashes do not agree between sections, and one
section may show a state that a later purge removed. Read the shape and the wording, not the arithmetic.
On your deployment the numbers will be your own, which is the point of a hash chain.

**Status when this was written:** phases C1 and C2 complete. The system runs, answers through a
gateway, records what it did, ships that record off the machine, and can prove the record was not
altered. It reads a client's documents: ingestion, hybrid retrieval, citation verification, access
control, egress redaction and retention are sections 13a to 13f.

Sections 1 to 25 are ordered by when they were written rather than by what a reader needs first, so
the six C2 pages sit between section 20 and the troubleshooting part rather than beside the audit
section they are numbered next to. The contents below lists them where they are.

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

**Part 3b, reading documents (phase C2)**

These six pages sit in the file after section 20, as the front matter explains. They are numbered as part
of 13 because they are operations like the rest of it.

13a. Ingesting documents
13b. Retrieval
13c. Citations and verification
13d. Access control
13e. Redaction and the egress gate
13f. Retention and deletion

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

Four offerings come out of that, and every one of them is one of three profiles:

| Offering | Whose account | Profile | Models |
|---|---|---|---|
| Standard Cloud | The client's | `cloud` | Remote |
| Compliant Cloud | The client's | `cloud`, with a compliance profile | Remote, restricted to forms the compliance profile allows |
| Standard Cloud Hosted | Ours | `cloud` | Remote |
| In-Office | None. Hardware in the client's building | `office` | Local weights only |
| A client with both | Theirs, plus their building | `hybrid` | Both lanes |

The first three share the `cloud` profile and differ by whose account they run in and what the contract
allows. `hybrid` is not a fifth offering for sale; it is the shape a client ends up with when they have
an appliance in the building and also want a frontier model for general questions, and it is why the
profile list has three entries rather than two. This manual covers the core that all of them share.

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
written immediately; the tamper-evident record gets those calls when a job copies them in, which happens
once a day as the first half of the export job, and can be run by hand at any time. And "cannot be quietly edited" is a property of the chain plus the shipped anchors,
which together make an edit detectable rather than impossible. Section 13 shows exactly what each check
proves and, more usefully, what it does not.

## 3. What runs where

| Piece | What it is | In this deployment |
|---|---|---|
| The machine | One AWS EC2 instance running Docker. No GPU: the generation models live at the provider, and embedding runs on CPU here. **Reranking does not** — this build reranks at Bedrock, and the local reranker container sits behind a bench profile because CPU reranking cannot meet the latency budget | `i-096bebd46308d1673`, an m7i.4xlarge: 16 vCPU, 64 GB RAM, 200 GB disk, Ubuntu 24.04. Check it with `nproc` and `free -g` rather than trusting this row on a deployment you did not build |
| `postgres` | The database. Holds the gateway's keys and spend, and our own audit record | A container, on the box |
| `litellm` | The gateway. Every AI call goes through it. It authenticates the caller, checks the role's budget and model permissions, calls the model, and writes down what it cost | A container, on the box |
| `audit` | The tool that writes and checks the audit record. Runs when you call it, not as a service | A container, started on demand |
| `keys` | The tool that mints per-role API keys and their ceilings | A container, started on demand |
| `retention` | The tool that deletes: a document a client asks to remove, and everything a retention period has run out on. It is the only component that unlinks a client's file | A container, started on demand |
| `eval` | The tool that runs the fixed question set against retrieval, so a change to search is measured rather than assumed | A container, started on demand |
| `ingestion` | The worker that reads documents, chunks and embeds them, and reconciles deletions. Runs as a service on a fifteen-minute cycle | A container, on the box |
| `parser` | Turns a document into structured text with page numbers, tables and OCR | A container, on the box |
| `vector` | The vector store: chunk embeddings plus the access label each chunk carries | A container, on the box |
| `embeddings` | bge-m3 through TEI, on CPU. Turns text into the vectors the store holds | A container, on the box |
| `reranker` | Reorders the fused head of a search. **Not running on this deployment**: the cloud build reranks at Bedrock, and the container here sits behind a bench profile because on CPU it cannot meet the latency budget | On Bedrock in the cloud build; a profile elsewhere |
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

**The record.** The word does two jobs in this manual, so here is the split. The gateway keeps its own
**spend log**, written as each call happens. Our **record** is the separate, tamper-evident one: a tool
called `audit` copies rows from the spend log into it, and that is the one that exports and anchors. When
this manual says "the record" without qualification, it means ours.

The gateway writes every call to its spend log, including the ones it refused. `audit` copies those rows
into our own record, which is a chain: each row contains a hash
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
| 10:00:00 | The anchor timer fires. The audit tool writes a small file holding the current head of the chain, a couple of hundred bytes, and the shipping job uploads it and reads it back | `journalctl -u oi-core-anchor.service` |
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
| The stack runs from a manifest, in the client's account, on CPU | **Built.** Reading the client's documents, sections 13a to 13f |
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

You should see seven services running, five of them healthy. Seven is the current number, and the
**assistant API is one of them**: a `ps` without it means the API is not deployed, which is a deployment that
cannot answer a question. The count has grown with each phase — six before C3 added the API — so an older copy of
this page showing a different number is a stale page rather than a broken box. Compose prints seven columns; the sample below
keeps them all, because two of them carry information the others do not. `IMAGE` is where a digest-pinned
image shows whether the box is running what the repository says it should, and `PORTS` shows the exposed
ports with no host binding, which is how you confirm from the outside that nothing is reachable:

```
NAME         IMAGE                                        COMMAND                  SERVICE      CREATED          STATUS                    PORTS
embeddings   ghcr.io/huggingface/text-embeddings-inf…     "text-embeddings-rou…"   embeddings   20 hours ago     Up 20 hours (healthy)     
ingestion    sha256:cc07ecc167dc80290ddc66ed29fde6c7…     "/bin/sh -c 'while t…"   ingestion    25 minutes ago   Up 25 minutes (healthy)   
litellm      ghcr.io/berriai/litellm@sha256:3d88bd75…     "docker/prod_entrypo…"   litellm      2 days ago       Up 2 days                 
parser       sha256:7693fc6d37a7dd055405dc2c378e39a5…     "container-entrypoin…"   parser       10 hours ago     Up 10 hours (healthy)     
postgres     postgres:16-alpine                           "docker-entrypoint.s…"   postgres     2 days ago       Up 2 days (healthy)       
vector       qdrant/qdrant@sha256:b7b0444c4c351c970b98…    "./entrypoint.sh"        vector       20 hours ago     Up 20 hours               
```

**The number matters, and so does which service is missing.** Four of them arrived with C2 and none is optional:
`vector` holds the chunk embeddings, `embeddings` turns text into them, `parser` turns a document into
text with page numbers, and `ingestion` is the worker that drives both. A `ps` showing two services is a
deployment that cannot read a client's documents at all, and it is what an older copy of this manual
showed.

Two of the six are not healthy and that is correct. `litellm` and `vector` declare no healthcheck, so
compose has nothing to report; for those two, `Up` is the whole of the information. `postgres`,
`ingestion`, `parser` and `embeddings` do declare one, and healthy means something specific in each case
— for `ingestion` it means the last cycle finished with no failures, not that the process is alive.

`ps` shows only the long-running services. `audit`, `keys`, `retention` and `eval` are behind the compose
`tools` profile, so they never appear here and `oi up -d` never starts them. They run when you call them.

Two facts about the output. A `docker compose run` prints its own preamble above whatever the tool
says, and how much of it depends on what the service depends on: the audit tool waits for postgres, so
it prints the postgres lines, while the keys tool depends on nothing and prints only its own
`Creating` and `Created` lines. Every example below has that preamble above it and it is not shown
again. And `up -d` prints
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

The supported way to reach a model is through the gateway with a role's key. The `keys` tool has
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

**Four names for one model, and which one belongs where.** A caller asks for the role name, `analyst`.
The gateway's config maps that name to a provider model: `model_name: analyst` with
`litellm_params.model: bedrock/amazon.nova-lite-v1:0`. The manifest writes it with the prefix, as
`frontier: bedrock/amazon.nova-lite-v1:0`. And the instance's policy allows the model's own ARN, which
carries the bare id, `amazon.nova-lite-v1:0`. So a `probe` printing `analyst -> analyst: 200` is the role
answering, not the model, and an `AccessDenied` is a mismatch between the prefixed id in the rendered
config and the bare ARN in the policy.

That uses the master key, which administers the gateway and can therefore call any model in it. A call
made with it bypasses the role ceilings and the model permissions, because those belong to role keys, so
use it for diagnostics and never for work. `keys --probe` is how you call as a role.

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
  Check the models available to you and try again.
```

`--as-model` is only read by `--probe`, and on its own it now exits with an error rather than doing
nothing quietly, which is what it used to do.

**Exit codes, because they are the opposite of what you might script.** A probe that gets an answer
exits 0. A probe the gateway refuses exits 1, so a monitoring script must read a 422 or a 403 as the
control working rather than as the check failing.

**Delete a key.** When a role goes away, its key has to go too, or it stays at the gateway scoped to a
model that no longer exists:

```
oi run --rm keys --revoke summarizer
```

```
revoked summarizer: deleted at the gateway and removed from /app/rendered/keys.json
  The role may still exist in the manifest. Add it back to models: and budgets.roles: to deploy it
  again, and it will get a new key rather than this one.
```

`--revoke` is the only thing in this tool that removes anything. `keys` on its own never prunes, because
it restores the manifest's policy and a missing role is not a policy change it can see.

**What the ceilings do and do not promise.** The gateway checks the budget against spend it has
already recorded, and that number lands a moment after a call returns. So a ceiling stops a runaway
loop, and it does not promise that the number is never exceeded: the call that crosses the line
completes, and in a fast burst more than one can slip through before the recorded figure catches up.
Promise a client that the loop stops, not that the number is untouchable.

## 13. The audit record, command by command

**Part 3b, reading documents (phase C2)**

These six pages are numbered as part of 13 because they are operations like the rest of it, but they sit
in the file after section 20 so that everything added by C2 is in one block rather than scattered through
pages that C1 wrote. The heading sequence in the file is not the reading order; this contents list is.

13a. Ingesting documents
13b. Retrieval
13c. Citations and verification
13d. Access control
13e. Redaction and the egress gate
13f. Retention and deletion

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
skip    006_feed_tells_the_truth.sql (already applied)
…
skip    016_retention_policy.sql (already applied)
0 migration(s) applied, 15 already present
```

On a fresh machine the same command prints `applied` for each in order and `15 migration(s) applied, 0
already present`. Migrations are ordered and checksummed, and editing one that has already been applied
is refused rather than silently accepted, so a schema change means a new file:

```
error: 005_content_modes.sql was applied but has changed since. Applied 6ed11a69cf48, now c6b031dad94a.
Write a new migration instead of editing an applied one.
```

**The count is `15 files, 15 recorded`, one row per file.** The numbering starts at `002` because `001` was
folded into the service before it shipped, and `schema_migrations` itself is created by `audit migrate` rather
than by a migration file. So the highest number is `016` while the file count is fifteen, which is the
off-by-one worth knowing about: **sixteen was never the file count.** On a fresh machine the summary line reads
`15 migration(s) applied, 0 already present`.

### Adding a migration

The section above tells you to write a new migration instead of editing an applied one. Here is how, and
it is four steps because the fourth is the one people skip.

**1. Take the next number.** List what exists and pick the next integer, zero-padded to three digits:

```
ls db/migrations/
```

```
002_audit_events.sql
…
016_retention_policy.sql
```

The next one here would be `017_`. **The number decides the order, and the order is the only ordering
mechanism**: files are sorted by name and applied in that sequence, so `017_` runs after `016_` on every
machine. Do not renumber an existing file to make room.

**2. Write the file.** Plain SQL, in `db/migrations/`. Two conventions, both of which exist in the files
already there and both of which matter more than they look:

- **Make it idempotent where you can.** `create table if not exists`, `add column if not exists`. The
  runner records what it applied and will not re-run it, so this is belt and braces rather than the
  mechanism — but it turns a half-applied migration on a machine that died mid-run into something you can
  simply run again.
- **Comment why, not what.** Read `016_retention_policy.sql` for the house style: the comments say what the
  requirement is and why the shape is what it is, including the alternatives that were rejected. A
  migration is the only record of a decision that a later reader will not find anywhere else.

**3. Apply it and read the output:**

```
oi run --rm audit migrate
```

```
applied 017_your_change.sql
1 migration(s) applied, 15 already present
```

`applied` is the word to look for. `skip` means the file was already recorded, and `error` means it was
recorded with a different checksum — which is the runner telling you that you edited a file that already
ran, not that the database is broken.

**4. Check it landed, from the database rather than the runner.** The runner records an intent; the
catalog is the fact. Both of these are read-only:

```
oi run --rm --entrypoint python audit -c "
import os, psycopg
with psycopg.connect(os.environ['DATABASE_URL']) as c, c.cursor() as cur:
    cur.execute(\"select filename, applied_at from schema_migrations order by filename desc limit 3\")
    for row in cur.fetchall(): print(row)
"
```

```
docker exec postgres psql -U oicore -d oicore -c '\d your_table'
```

**What a migration is not for.** It changes the schema, not the data in a running system. A migration that
backfills a column runs inside the same transaction as the schema change and can hold a lock on a table
the deployment is using; if a change needs a backfill, do it as a separate step with the migration and say
so in the file's comment. And a migration cannot be rolled back: there is no `down` file and no undo, so a
destructive change wants a backup taken first. Section 14 and `docs/backup-restore.md` cover that.

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

Two conventions in the samples below. The compose preamble above each output is elided, and so are the
trailing footers: `audit list` ends with a count such as `20 event(s)`, and a `psql` query ends with
`(10 rows)`. Columns are trimmed to fit the page. Nothing else is.

`--dev` is required for a hand-typed actor. Without it the command exits non-zero, because in a real
deployment the actor comes from an authenticated session rather than from whoever is typing. The actor
is an identity, not a word.

**The choices you can make:**

| Flag | Values, and what it means |
|---|---|
| `--type` | `model_call`, `tool_call`, `approval`, `access`, `config_change`, `login`. Required |
| `--actor` | Who did it. Required, and refused without `--dev` |
| `--actor-role` | Free text, recorded in the row's `actor_role` column and printed by `audit list`. **Nothing validates it and nothing reads it to make a decision** — `admin` and `staff` are conventions of ours, not values the code recognises. Do not confuse it with a retrieval role (13d) or a model role (12); it is a label for a human reading the record |
| `--dev` | Says out loud that a human typed this actor rather than a session supplying it |
| `--data-class` | `public`, `internal`, `confidential`, `regulated`. Default `internal`. It decides the content mode |
| `--outcome` | `ok`, `denied`, `error`, `pending`. Default `ok` |
| `--tool` | The tool involved, when the event is a `tool_call` |
| `--model` | The model involved, when the event is a `model_call` |
| `--request-id` | Ties a hand-written event to the gateway's call. The record is unique on it, so a later `backfill` will not create a duplicate |
| `--approval` | JSON describing an approval, for the `approval` type |
| `--payload` | JSON, or `@file` to read a file, or `-` to read standard input |
| `--prompt`, `--completion` | The text sent and returned. Both hashes are always recorded; the text is kept according to the content mode |
| `--redactions` | JSON, the kinds and counts of what redaction removed. **These four are what section 13e's record is made of**, and they are covered by the row hash from `chain_version` 2 rather than being commentary beside it |
| `--unverified-card-shapes` | How many card-shaped numbers failed the check digit. A non-zero value is a number that looked like a card and is not one |
| `--egress-allowed` | Whether the gate permitted the request to leave. The record's own account of a decision the gate made |
| `--deployment-id` | Overrides the deployment this event belongs to. Rarely needed |

**Two warnings go to standard error**, so they appear in your terminal but not in the JSON on standard
output. One says you are in development mode. The other appears if you pass `--store-payload` while the
content mode would hash: the flag wins, so the event keeps more than the deployment's policy asks for,
and the warning is there to make that a decision rather than an accident.

**The payload forms, in practice:**

```
--payload '{"document":"expense-policy.pdf"}'      # inline
--payload @/tmp/event.json                          # from a file, which has to be mounted in
echo '{"document":"x.pdf"}' | ... --payload -       # from standard input
```

The tool runs in a container whose only mounts are the migrations and the exports directory, so a host
path is invisible to it. Add the mount, and give stdin a `-T` when you are not on a terminal:

```
oi run --rm -v /tmp/event.json:/tmp/event.json audit record --dev --type access \
  --actor anthony --payload @/tmp/event.json
echo '{"document":"x.pdf"}' | oi run -T --rm audit record --dev --type access \
  --actor anthony --payload -
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
ok   /app/exports/2026-10-11/audit-events.jsonl: 23 event(s), chain intact
     note: no external anchor was supplied, so completeness cannot be shown: deleting rows from the
           end and re-exporting would still verify. Pass --anchor with the file written by the anchor job
     note: chained from row 10 (6928b2669fd9...), which is not in this file
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

`--out` defaults to `/app/exports/anchors`, which is the directory mounted back to the host, so the
file lands somewhere you can see it. Point it elsewhere under `/app/exports` if you want it in a
different place, and nowhere else: a path outside that directory is written inside the container and
disappears with it. Older builds defaulted to a bare relative path and died with a `PermissionError`,
which is why the default is explicit now.

An anchor is the only artifact that can prove rows were not deleted from the end of the chain. Check
an export against one:

```
oi run --rm audit verify /app/exports/2026-10-11/audit-events.jsonl \
  --anchor /app/exports/anchors/anchor-2026-10-11-00000023.json
```

```
ok   /app/exports/2026-10-11/audit-events.jsonl: 23 event(s), chain intact
     note: matches the anchor written 2026-10-11T04:49:16.784332+00:00 (3e7fb0f1d35d…, 23 rows)
     note: chained from row 10 (6928b2669fd9…), which is not in this file
```

That second note appears whenever an export starts part-way along the chain, which is every day except
the record's first: the file's opening row chains back to a row it does not contain. It is information
rather than a failure, and the check behind it is against the export manifest rather than the anchor.
The manifest records the hash the first row should chain from, so the comparison proves the file starts
where it says it starts. The anchor check is the line above it, and the two are different questions:
the manifest says where this file begins, and the anchor says the record still contains what existed
when the anchor was written.

With no anchor, the command says plainly that completeness cannot be shown, because deleting rows from
the end and re-exporting would still verify. That sentence is the honest one, and it is why the
shipping job in section 14 exists.

**What the anchor check proves, exactly.** It places the anchor's row against the export's **window** and
asks whether it is there. An export covers one day; an anchor records a point in the whole chain, so the
verifier compares positions rather than counts.

| The anchored row is… | What it means |
|---|---|
| **before** the export's window | The anchor is older than this file, so it says nothing about it either way |
| **inside** the window | The row must be present. A row removed from the middle, or a tail cut off ahead of it, is caught |
| **past** the window's end | **One anchor cannot decide.** Either the chain grew after the window closed, or the file lost its tail, and both look identical |

That third case is the honest limit, and it is stated rather than guessed at. The check that separates them
is the **next day's export**: its opening `first_prev_hash` must equal this file's closing `last_row_hash`,
which is section 13's manual step. Anchoring hourly keeps the gap between anchors small, which is why that
job runs as often as it does.

**What an auditor can be told from an export alone:** the chain is intact and any edit is detectable. **Not**
that rows deleted from the end have been ruled out — that needs the off-box anchor, and for the strongest
form the following day's export as well. Do not promise more than that.

An earlier version of this verifier compared the export's row count against the anchor's cumulative
`event_count`. That is a category error, invisible on a chain one day long and wrong on every deployment
older than that: it reported "the chain is shorter than it was" against files nobody had touched.

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
prev_hash is the head before the purge and row_hash is the head after, so an export starting at this
tombstone can be verified on its own.
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

One run, with its real numbers, so you know what a good one looks like:

```
anchored 23 event(s), head 3e7fb0f1d35d6f7218932be29f00aa5943555a18ad9a0bb7841158e477c5098a
  /app/exports/anchors/anchor-2026-10-11-00000023.json
  Ship this file off this machine. Kept here, it proves nothing.
shipping /evidence/anchors/anchor-2026-10-11-00000023.json (278 bytes)
  local  sha256 f9c2f120c0a5801f73d86c5a04c9cbf1c0f667dc978e0fcb838d10caa9a66584
  remote sha256 f9c2f120c0a5801f73d86c5a04c9cbf1c0f667dc978e0fcb838d10caa9a66584
  ok: s3://oi-core-audit-dev/anchors/anchor-2026-10-11-00000023.json matches the file that was sent
anchors shipped to s3://oi-core-audit-dev
```

The read-back is the point. From the sending side a complete upload and a truncated one look the same.
Fetching the object and hashing it is the difference between "we sent it" and "it is there and it
matches".

**Reading an anchor's name.** `anchor-<date>-<eight digits>.json`, where the digits are the number of
events in the chain when it was written. `00000023` means twenty-three events existed; an hour later,
with no new events, the same name comes back.

**A filename that repeats on purpose.** An anchor is named for its date and the number of events in
the chain, so an hourly run over an unchanged chain writes the same key twice, with the object's
version history holding both. That is deliberate. The
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
  s3 rm s3://oi-core-audit-dev/anchors/anchor-2026-10-11-00000023.json
```

```
delete failed: … An error occurred (AccessDenied) when calling the DeleteObject operation: Access Denied
```

## 15. The routine checks

| When | What | Command |
|---|---|---|
| After a start | Containers healthy | `oi ps` |
| After a start | Gateway answering | `docker run --rm --network ai-net curlimages/curl -sS http://litellm:4000/health/liveliness` |
| Any time | A client or their auditor wants the evidence | Export the day, hand over the JSONL and its manifest plus the anchor that covers it, and give them the two commands in section 13. There is no read-only account for an auditor today: the evidence travels as files, and they verify it on their own machine |

| Weekly | The record's chain is intact | `oi run --rm audit verify /app/exports/2026-10-11/audit-events.jsonl` (a UTC date, and export the day first) |
| Weekly | Nothing was refused unexpectedly | `oi run --rm audit list --type model_call --limit 50` |
| Weekly | Timers have been firing | `journalctl -u oi-core-anchor.service -n 20` |
| Monthly | Model lifecycle | On the box, after a `git pull`, so it reads the manifests this deployment renders from: `python3 scripts/check_model_lifecycle.py` |
| Per the contract | Retention purge | `audit purge --before <cutoff> --reason <why>`, run by hand. **Nothing schedules it**: there is no purge timer, and the retention promise in the manifest is a policy the operator keeps rather than a job the machine runs |
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
access:
  denial: counted
  egress:
    frontier_classes: [public, internal]
    redaction: "on"
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

The example above is a cloud build, so every role writes `local: ""` and no role writes a frontier name
that is missing. An office build is the other way round, and this is the whole of the difference in that
block:

```yaml
profile: office
lanes:
  frontier:
    enabled: false
models:
  front_desk: {local: qwen3-1.7b-instruct}
  analyst:    {local: qwen3-1.7b-instruct}
  reader:     {local: qwen3-1.7b-instruct}
```

The three roles name a model file that the local server serves, no role names a frontier model, and the
frontier lane is switched off rather than absent. Leaving `enabled: true` here is the mistake the
validator refuses, because an appliance that can reach a provider is not an office build.
| `lanes.frontier.enabled` | Whether remote inference exists at all. Must be false on an office build and **must be true on a cloud build**, which has no local lane to fall back on, so switching it off there leaves a deployment with no models and is refused |
| `lanes.frontier.account_owner` | `client`, or `office_inference` for our own machines and the hosted tier |
| `lanes.frontier.data_classes_allowed` | Which data classes may reach a remote model |
| `content.default`, `content.classes` | What the record keeps per data class: `hash`, `redacted`, or `full` |
| `budgets.period_days`, `budgets.roles` | The ceiling per role, in dollars, over a period in days |
| `compliance.profile` | `none`, `contractual`, `hipaa`, `glba`, `sec-17a4`, `itar-cui`, `ferpa`, `pci` |
| `compliance.audit_retention_days` | How long the record is kept. Six years is enforced for `sec-17a4` |
| `compliance.data_residency` | Required by any compliance profile other than `none` or `contractual` on a cloud or hybrid build, so the contract records where the client's data must stay. The validator checks that it is named rather than comparing it with anything, so the global-profile refusal follows from the compliance profile itself |
| `compliance.baa_with_office_inference` | Required by any compliance profile other than `none` or `contractual` on a hardware build, and it must be true. We need a signed agreement before holding that data on a client's machine |
| `lanes.frontier.redaction` | Whether prompts are redacted before leaving. One validator rule refuses `false` with `regulated` on a cloud build. **As of C2 this is superseded in practice by `access.egress.redaction`**, which is the field the egress gate reads; this one is still validated and still read by nothing at call time. The overlap is recorded rather than resolved — see the note under this table |
| `lanes.frontier.requires_approval` | A human approval step before a remote call. In the schema, read by no rule and no service; C3 owns the gate |
| `access.denial` | **What a denied user is told** when their retrieval role excludes content that would answer the question: `silent`, `denied`, or `counted`. Read by the retriever at call time, rendered to `ACCESS_DENIAL_MODE`. The trade each mode makes is the client's, and section 13d sets it out. Default `denied`; an unrecognised value is refused rather than defaulted |
| `access.data_class` | **What a request on this deployment IS**, which is what the gate compares against
 `access.egress.frontier_classes`. One of `public`, `internal`, `confidential`, `regulated`. **Absent
 means the most restrictive class**, so a deployment that forgets it does not send content off-box by
 omission. Added in C3; the field existed in the schema before this page mentioned it |
| `access.egress.frontier_classes` | **Which data classes may leave for a frontier model.** Rendered to `FRONTIER_CLASSES`. The gate refuses rather than defaulting, so a deployment that declares nothing sends nothing off-box. Confidential and regulated are absent rather than listed as false, which is what makes a refusal name what *is* permitted |
| `access.egress.redaction` | Whether the egress gate redacts before the call leaves: `"on"` or `"off"`, rendered to `EGRESS_REDACTION`. Default `on`. This is the field section 13e documents, and the one that turned the `lanes.frontier.redaction` story around |
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
the access block, and the offsite block. Section 25 lists what exists.

**One overlap is recorded here rather than resolved, because it is an architecture decision.**
`lanes.frontier.data_classes_allowed` and `access.egress.frontier_classes` carry the **same enum and the
same meaning**. Both are `[public, internal]` in this deployment. They are two places to declare one
policy, and **the gate reads the second**. The first is read by the validator's own two rules — it refuses
an empty list, and refuses `regulated` alongside `redaction: false` on a cloud build — and by nothing at
call time.

So a client's manifest can say one thing in `lanes.frontier.data_classes_allowed` and another in
`access.egress.frontier_classes`, pass validation, and be governed by the second without anyone noticing
the first disagrees. Until the overlap is resolved, **set both, and treat `access.egress` as the one that
decides.** The same applies to `lanes.frontier.redaction` against `access.egress.redaction`: set both.

**You do not have to remember to compare them, because the tools do it for you.** The validator prints a
warning beside the `ok` line when the two disagree, and the renderer writes the same warning into
`rendered/env` as `MANIFEST_DIVERGENCE`, so the divergence is discoverable on the machine rather than only
to whoever last ran the validator:

```
python3 scripts/validate_manifests.py
ok   dev-cloud.yaml  (profile=cloud, packs=['core'])
  ! dev-cloud.yaml: lanes.frontier.redaction and access.egress.redaction disagree, and access.egress is
    the one the egress gate reads: declared False, enforced 'on'. …
```

**A warning, never a failure, and the direction is stated.** A manifest that is working today must not stop
working on upgrade, so a divergence does not make a manifest invalid. What it does do is say which way the
two differ, because the two directions are not equally urgent:

- **The gate is wider than the declaration** — material leaves that the lane block does not permit. The
  warning says so, and this is the one to act on.
- **The gate is narrower** — nothing leaves that was forbidden, so it is safe and *misleading* rather than
  unsafe. Worth correcting, not worth an incident.

**What neither tool can do is decide which line is right.** The warning names both values and tells you they
disagree; a person then has to read the client's contract and correct whichever line is wrong. That is
deliberate, and it is the reason this is a warning rather than a silent rule.

**This is not hypothetical, and the failure mode is worth stating because it is silent.** Three of the
example manifests shipped with `lanes.frontier.data_classes_allowed` declared and **no `access` block at
all**. They validated. They then rendered `FRONTIER_CLASSES=` — empty — and the gate reads an empty list as
a set of permitted classes with nothing in it, so it refuses:

```
no data classes are permitted on the frontier lane. This is what an unconfigured deployment looks like,
and it refuses rather than guessing a default.
```

Every request refused, on a manifest that says three classes are allowed. The first symptom is a model call
that will not go out, and **before this round nothing errored at validation at all.** A validator rule now
refuses the shape — the bullet beginning "A frontier lane enabled with no `access.egress.frontier_classes`",
under "Residency and model eligibility" below — and the three examples declare both blocks, each with a
comment saying which one is read.

This was found by a review, not by a failure, and it is left standing on purpose. Resolving it means
either deleting a field that section 16 documents and clients may have written, or changing which field a
security control reads, and both are decisions for the owner of the product rather than for whoever
happens to be editing this page.

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
- A frontier lane enabled with an empty `data_classes_allowed`. **The reason is intent rather than egress:** this
  field is read by the validator and not by the gate, so an empty list here cannot allow anything. The rule below
  is the one that governs what actually leaves.
- **A frontier lane enabled with no `access.egress.frontier_classes`.** The gate renders that field, an absent
  block renders empty, and an empty permission set refuses every call — so this is the shape that silently stops a
  deployment sending anything while the manifest appears to permit three classes.

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

**Three fields are policy that nothing enforces yet.** `lanes.frontier.data_classes_allowed` is read
by the validator's own two rules, which refuse an empty list and refuse `regulated` alongside
`redaction: false` on a cloud build, and by nothing at call time, because nothing labels a request with
its data class. `lanes.frontier.requires_approval` is in the schema, read by no validator rule and
by no service. `lanes.frontier.redaction` is checked by one validator rule, which refuses `false`
together with `regulated` on a cloud build.

**C2 changed two of those three, and this paragraph is the correction.** As of C2 the egress gate is
built and redaction runs on the way out, both declared under `access.egress` and rendered to
`FRONTIER_CLASSES` and `EGRESS_REDACTION`; section 13e is the operating page. What that leaves is an
overlap rather than a gap: `lanes.frontier.data_classes_allowed` and `access.egress.frontier_classes`
carry the same enum, mean the same thing, and are two places to declare one policy. **The gate reads
the second.** The first is the field this table lists and it is now the one nothing reads at call
time, which is the reverse of what this paragraph said before C2 and is recorded as an open decision
rather than quietly resolved by deleting a row.

`lanes.frontier.requires_approval` remains in the schema read by no validator rule and no service.

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
role on the cheapest model, for a component that should only ever write short summaries. It takes two
edits to the manifest — one under `models:`, one under `budgets.roles:` — and then validate, render,
restart the gateway, mint the key, and probe it.

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

**A faster way to see the change took:** ask the gateway what it serves. This is the whole model list
rather than one role's answer, so it is the check to reach for when a render seems not to have landed:

```
MASTER=$(grep '^LITELLM_MASTER_KEY=' rendered/env | cut -d= -f2-)
docker run --rm --network ai-net -e MASTER="$MASTER" curlimages/curl -sS \
  -H "Authorization: Bearer $MASTER" http://litellm:4000/v1/models
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

### Removing a role

The reverse of adding one, and the order matters because a key outliving its role is a key nothing
accounts for.

1. Delete the role's entry from `models:` and from `budgets.roles:` in the manifest.
2. Validate and render, which writes a gateway config without it.
3. Restart the gateway: `oi restart litellm`. Calls naming the role now fail with a 400, because the name
   is no longer served.
4. Delete the role's key, because a key outliving its role is a credential nothing accounts for:

```
oi run --rm keys --revoke analyst
```

   That deletes it at the gateway and forgets it here. Leaving it costs nothing while the role is gone,
   and it becomes a live key again the moment someone re-adds the role name, which is the reason to
   revoke rather than leave it.
5. Record what you did: `audit record --dev --type config_change --actor <you> --actor-role admin
   --payload '{"removed_role":"analyst"}'`.

**Retiring a model** is the same shape with a different first step: pick the replacement from
`data/model-lifecycle.json`, change the `frontier` line, and follow section 18. A model is retired from
the manifests rather than from the provider's catalogue, and the lifecycle check is what tells you it is
time.

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
oi restart api
oi run --rm keys --probe analyst
```

**Both restarts, and the second one is the one people miss.** The renderer writes the config, and neither
process re-reads it. Restart only the gateway and the model genuinely changes at the provider while the API goes
on reporting the old one from the manifest it loaded at startup. That is the worst shape of failure: the record
is right and the report is wrong, and the report is the quieter of the two.

Confirm it from the provider's side rather than from ours, because the API's own answer is the thing that can be
stale:

```
docker exec postgres psql -U oicore -d oicore -c \
  'select model from "LiteLLM_SpendLogs" order by "startTime" desc limit 3'
```

That is the gateway saying what it asked the provider for. The API's answer carries both facts too: `model` is
the role the manifest names and `resolved_model` is the provider model it points at, so a response whose two
fields disagree with the spend log means a process was not restarted.

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

### Onboarding the client's corpus

**Step 6 is where a deployment stops being a stack and starts being useful, and it was missing from this
list.** Nothing is searchable until the client's documents are in the right place with the right labels,
and the six steps above never mention a document. Here is the seventh step, and it is the one that
decides whether the client's questions have answers.

**One: agree the access roots before you copy anything.** A root is a top-level folder under
`/opt/oi-core/documents`, and **its name is the access label** — there is no separate place to declare it
and no way to relabel a file afterwards without re-ingesting. Two questions for the client, and both are
contract-level rather than technical:

- **Who may see what?** The roots are folders, declared in `access.roots`, and the groups that read them are
  declared in `access.groups` — **both are manifest settings, so adding a third boundary is a manifest change
  and a re-render rather than a code change.** The retriever also carries a two-root fallback map for callers
  that supply no session permit, and it is not the authority. Roots still have to be settled before the
  corpus is laid out, because a folder that moves later has to be re-ingested rather than relabelled.
- **Is there anything one group must not see?** Whatever goes in a restricted root, the client should
  understand that a search by a role without that root returns a denial rather than an answer, and that
  `access.denial` decides whether they are told that at all. Section 13d sets out the trade.

**Two: lay the corpus out and copy it in.** The folder name is the label, subfolders are read recursively,
and every file under a root carries that root's label:

```
/opt/oi-core/documents/
  company/            <- labelled `company`, readable by the staff role
    handbook/
    invoices/
  executive/          <- labelled `executive`, readable by the executive role only
```

A file directly under `documents/` with no root folder is indexed under the label `documents`, which no
role's map contains, so **no search ever returns it**. It fails quietly. Put every file under a root.

Copy with `scp` or `rsync`, as the `ubuntu` user, and check the ownership afterwards: the worker reads the
mount as an unprivileged user inside the container, so a file the host cannot read is a failure the worker
reports rather than a file it skips.

**Three: check the formats before the client is told the corpus is loaded.** The accepted set is `.pdf`,
`.docx`, `.pptx`, `.xlsx`, `.html`, `.htm`, `.md`, `.markdown`, `.txt`, `.csv`, `.rtf`, `.odt`, `.ods`,
`.odp` and `.eml`. Everything else is ignored rather than failed, and it is not reported. In particular
`.doc` and `.xls` — anything saved by an Office older than 2007 — are ignored, and `.msg` is deliberately
outside the set because whether the parser reads it is unverified.

**`.rtf` is in the set and depends on something the parser image has to install.** Docling reads RTF by
converting it to DOCX through **LibreOffice**, which is not part of the base image; the parser service installs
`libreoffice-writer` for exactly this format. On an image built before 2026-10-13 the binary was absent, and
every RTF failed with `LibreOffice is required to convert a .rtf file to .docx` — a message the worker records
as a permanent failure and then never surfaces. **So when the client's handover contains RTF, check it is
indexed rather than assuming the suffix means it was read.** `docker exec ingestion python3 /app/ingest.py
--failures` names the reason. Count the files in the client's handover
against the number the worker reports:

```
# every file in the handover, and then only the ones the worker will look at
find /opt/oi-core/documents -type f | wc -l
find /opt/oi-core/documents -type f \
  -regextype posix-extended -iregex '.*\.(pdf|docx|pptx|xlsx|html?|md|markdown|txt|csv|rtf|odt|ods|odp|eml)' | wc -l

docker exec ingestion python3 /app/ingest.py --documents /documents
```

**Compare the second number with `seen`, not the first.** The two `find` commands are there because the
obvious check is the wrong one: the worker counts only files whose suffix is in the accepted set, and a
client's handover folder will contain a `.DS_Store`, a `.msg` export and a couple of `.doc` files that it
deliberately ignores. A first number that is higher than `seen` is the filter working, and chasing it wastes
an afternoon. The question worth asking is whether the *second* number matches:

**The `-iregex` matters.** The worker compares a lowercased suffix, so `INVOICE.PDF` is ingested, while a
case-sensitive `-regex` would not count it and the second number would read low for no real reason. That is
the kind of discrepancy the table below would then send you chasing, so the two lists are kept matching
character for character — all fifteen suffixes, and the case flag.

- **Second number higher than `seen`:** a file in an accepted format was missed. Check the worker's own
  `failed` count, then the parser's log for that filename.
- **Second number lower than `seen`:** two paths resolved to the same relative path, or a file was added
  between the two commands.
- **`failed` above zero:** those documents are dead-lettered after two attempts and will not be retried until
  their bytes change. `docker exec ingestion python3 /app/ingest.py --status` names them.

Tell the client which files were skipped and why before saying the corpus is loaded. An ignored `.doc` is a
fact they should hear from you rather than discover when a question about it has no answer.

**Four: run one question per root and read the answer.** This is the acceptance test for a corpus, and it
takes two minutes. It catches a corpus laid out under the wrong root, a parser that cannot read the
client's format, and a role map that does not match what was agreed:

```
docker compose -f deploy/office/compose/docker-compose.yml --env-file rendered/env \
  run --rm --entrypoint python -v /opt/oi-core:/w:ro -w /w eval /w/tests/fixtures/ask.py \
  -q "<a question the client says must work>" --role staff

docker compose -f deploy/office/compose/docker-compose.yml --env-file rendered/env \
  run --rm --entrypoint python -v /opt/oi-core:/w:ro -w /w eval /w/tests/fixtures/ask.py \
  -q "<a restricted question>" --role staff
```

The first should return passages from the root you expect. The second should return nothing, or return a
denial naming how many passages were withheld — and **if it returns restricted content, stop.** That is
the access filter failing, not a corpus problem, and section 13d's troubleshooting is where to go.

**Five: set the retention period before there is anything to retain.** The period lives in the database, in
`retention_policies`, and `--set-retention` is the only thing that writes it. **The manifest has no field for
the documents period** — `compliance.audit_retention_days` is the audit record's retention and a different
thing — so there is nothing to keep in agreement, and the policy is what `retention --enforce` reads:

```
oi run --rm retention --set-retention documents --days <the period the client agreed> \
  --legal-basis "<the period, in the client's or their regulator's words>"
```

`--legal-basis` is not decoration: a retention period with no stated basis is one nobody can defend to an
auditor. Section 13f covers the enforcement run and its dry run.

**What is not in this manual:** how to build the machine itself. Creating the account, the VPC, the
instance, the security group, the instance role, the bucket with its lock, and the timers is the
installer's job, and it is written down in `docs/runbook.md` (Phase 0 and Phase 1 for the cloud build,
Appendix A for the office one) and in `docs/audit-evidence.md` for the bucket's settings. Terraform
replaces all of it in C7.

The one thing that is not automatic yet is the account itself. Terraform for the VPC, the bucket, the
IAM role, and the rest is C7, and today those are created by hand.

## 20. Adding a service or a pack

**A service** is a container with a job. `services/_template/` holds the shape: a `service.yaml`
declaring what it is, and a README describing the convention. `services/audit/` is the worked example,
with a `Dockerfile`, the code, and its own README.

Four places to touch, which is the convention rather than a suggestion, because the last three are how
anything else finds the service:

1. `services/<name>/` with its code, `Dockerfile`, and `service.yaml`.
2. `deploy/office/compose/docker-compose.yml`, with a profile.
3. `packs/catalog.yaml`, in the pack that sells it.
4. `docs/service-inventory.md`, which is the list of what exists and which build each service runs in.

In the compose file, use the `tools` profile for something that runs when called, as `audit` and `keys`
do, and leave the profile off for a long-running service.

Two rules apply to anything you add. It joins `ai-net` and publishes no ports. And if it writes to the
record it writes through `audit` rather than inserting rows itself, because the tool is the record's only
writer: a component that inserted rows directly could write a row whose hashes did not match the chain,
and nothing downstream would catch it. `audit record --dev` is the deliberate exception, for a human
writing an event by hand in development, and it is why that flag exists.

**A pack** is a sellable bundle, listed in `packs/catalog.yaml` with the services it declares. Adding
one is a catalog entry plus the services it names. The manifest's `packs` list is what switches it on,
and an unknown pack id fails validation.

---

## 13a. Ingesting documents

A client's documents are not searchable until the worker has read them. `ingestion` walks the documents
directory, parses each file it has not seen before, cuts the text into chunks, embeds them, and writes the
result twice: the vectors the dense search reads, and the rows the lexical arm reads. It runs a cycle every
fifteen minutes, and it is the only component that writes the document indexes.

Each chunk carries the label of the top-level folder it was found under, so `company/handbook/policy.pdf`
is labelled `company`. The label is attached here rather than worked out during a search, because the
search filters on it. Section 13d covers what enforces it.

### Putting a document where the worker will find it

**The folder is `/opt/oi-core/documents` on the host.** That is the whole answer to "where do I put the
file", and this deployment's corpus shows what a populated one looks like:

```
$ ls /opt/oi-core/documents
company  executive

$ find /opt/oi-core/documents -type f | head -4
/opt/oi-core/documents/company/fee-schedule-2026.md
/opt/oi-core/documents/company/irs-p15.pdf
/opt/oi-core/documents/company/dispatch-review-q1.pptx
/opt/oi-core/documents/executive/executive-compensation.md
```

Four rules and nothing else:

1. **The file goes under a top-level folder**, and that folder's name is its access label. There is no
   separate place to declare the label, and no way to label a file individually. A file sitting directly
   in `documents/` with no subfolder is not unlabelled: it takes the name of the documents folder itself,
   so it is stored as `documents` and **no role's map contains that label, which means no search returns
   it.** It is indexed and unreachable, which is the quietest way to lose a document. Put files in a
   folder whose name is a root.
2. **Subfolders are read recursively.** Depth is free, and every file under a root carries that root's
   label however deep it sits. A folder inside a root does not create a new label.
3. **The extension decides whether it is read at all.** The accepted set is `.pdf`, `.docx`, `.pptx`,
   `.xlsx`, `.html`, `.htm`, `.md`, `.markdown`, `.txt`, `.csv`, `.rtf`, `.odt`, `.ods`, `.odp` and
   `.eml`. Anything else is **ignored rather than failed**, and it is not counted as a document either:
   the worker does not report it, because a client's document folder will contain a `.DS_Store` and a
   shortcut sooner or later and neither is an error. So a file in an accepted format that does not appear
   in the index is a failure to investigate, and a file in an unsupported format that does not appear is
   the filter working. `.msg` is deliberately not in the set: whether the parser reads it is unverified,
   and adding a suffix is a promise that files with it are ingested. A client with Outlook exports should
   be told that before they drop a folder of them in.
   **`.rtf` is in the set and needs LibreOffice in the parser image** — Docling converts RTF to DOCX through
   it. Without that binary the file fails as a permanent error and, until `--failures` existed, the reason
   was recorded and never shown. A suffix in this list is a promise; check the promise is kept rather than
   trusting the list.
4. **Legacy binary Office formats are not read.** `.doc` and `.xls` are outside the set, so an old
   `.doc` is ignored in exactly the way above. Modern `.docx` and `.xlsx` are read. A client whose archive
   predates 2007 needs to re-save before ingesting, and that is worth saying in the contract rather than
   discovering in the index.

To add a document, copy it in and wait for the next cycle, or run one now rather than waiting up to
fifteen minutes:

```
scp -i ~/ai-stack/ai-stack-key.pem myfile.pdf ubuntu@52.204.202.66:/opt/oi-core/documents/company/
ssh -i ~/ai-stack/ai-stack-key.pem ubuntu@52.204.202.66 \
  'cd /opt/oi-core && docker exec ingestion python3 /app/ingest.py --documents /documents'
```

The mount is **read-only** for the worker, which is deliberate: this component never writes to a client's
documents, and a mount that cannot be written to is the cheapest way to keep that true. It also means a
permissions problem on the host shows up as a failure to read rather than as a file quietly modified.

Retention mounts the same directory **read-write**, because a retention period running out destroys the
file as well as the indexes. That is the only thing in the system that deletes a client's document, and
section 13f covers it.

**Apply the schema, then start the worker:**

```
oi run --rm audit migrate
oi up -d --build ingestion
oi ps
docker logs ingestion --tail 30
```

`audit migrate` is the one path that applies migrations, and it applies them in order. `oi ps` should list
`ingestion` as `healthy`, and healthy for this container means the last cycle finished with no failures
rather than that the process is alive. There is no listening socket here, so a check that only proved the
process existed would stay green while every document failed.

**What a cycle prints, and how the two forms differ.** The worker parses every document it has not seen, so a
first run over a client's corpus takes minutes or hours.

`docker logs ingestion` shows **JSON**, because the container runs the cycle with `--json`:

```
collection documents: 1024 dimensions, Cosine, 1131 points
{
  "seen": 12,
  "new": 0,
  "changed": 0,
  "skipped": 12,
  "failed": 0,
  "chunks_written": 0,
  "chunks_removed": 0,
  "documents_removed": 0,
  "failures": []
}
```

A cycle **run by hand** prints prose instead, because the CLI defaults to the readable form. This is the
distinction that trips people up: the same cycle, the same numbers, two different shapes, and the prose form
is the one the manual can show you because only a hand run produces it:

```
$ docker exec ingestion python3 /app/ingest.py --documents /documents
collection documents: 1024 dimensions, Cosine, 1131 points

seen 12, new 0, changed 0, skipped 12, failed 0
chunks written 0, chunks removed 0, documents removed 0
```

The counts depend on the corpus and on the chunker version. On this deployment, twelve documents and 1131
points, measured from the index:

**Twelve documents, and only four of them were fetched.** `fixtures/corpus/manifest.yaml` declares both
lists and is the authority: four real filings from SEC EDGAR and the IRS under `documents`, with the licence
read at the source, and eight fixtures this project wrote under `authored_fixtures`, each naming the
`tests/fixtures/*.py` script that builds it, the control it exists to prove, and **how it arrives in a
deployed corpus — which is not the same for all eight:**

| How it arrives | How many | Which |
|---|---|---|
| `written-directly` — the builder writes into `/w/documents`, so running it installs the file | 4 | the three fee schedules, `onboarding-notes.md` |
| `copied-by-hand` — the builder writes into the repository and an operator copies it in | 1 | `executive-compensation.md`, by runbook step 3.8, "Access labels" |
| `no-install-step` — nothing in the repository places it | 3 | `callout-rate-approval.eml`, `rate-card-2026.xlsx`, `dispatch-review-q1.pptx` |

So an index holding twelve is correct rather than a sign that something was ingested twice, and **a reader
rebuilding this corpus from the repository alone will find three files missing.** That is recorded as a gap
rather than tidied: the three are the format fixtures from `RET-FORMAT-001`, and the honest statement is that
they were installed by hand during the build and the step was never written down. The same `cp` the runbook
gives for the executive fixture would place them.

`python3 fixtures/corpus/validate_corpus.py` checks that every authored fixture names a builder that exists.

| Document | Chunks | Notes |
|---|---|---|
| company/apple-10k-2024.htm | 672 | 588 table entries and 84 prose chunks |
| company/apple-ex10-1-2024.htm | 13 | pageless HTML |
| company/apple-ex10-22-2024.htm | 16 | pageless HTML |
| company/callout-rate-approval.eml | 1 | email, one chunk |
| company/dispatch-review-q1.pptx | 2 | slides |
| company/fee-schedule-2025.md | 20 | superseded, and still indexed |
| company/fee-schedule-2026-copy.md | 20 | superseded, and still indexed |
| company/fee-schedule-2026.md | 20 | the current schedule |
| company/irs-p15.pdf | 358 | 320 digital chunks plus **38 scanned**, the only OCR in the corpus |
| company/onboarding-notes.md | 1 | |
| company/rate-card-2026.xlsx | 7 | spreadsheet |
| executive/executive-compensation.md | 1 | the restricted root |
| **total** | **1131** | matching the collection's own count |

At the start of a cycle the worker also reports the collection it is writing to: its dimension, its distance
and how many points it holds. A collection whose dimension does not match what the embedding model produces
stops the run with a refusal naming both numbers, rather than writing vectors of the wrong shape. So does a
collection whose distance is not Cosine, because search results would then be ordered differently from every
measurement taken on this build.

**The second cycle is the one that matters, and both forms above are second cycles.** Over unchanged bytes it
costs one read per file and writes nothing: `skipped` equals `seen`, and `chunks written 0`. That is the whole
claim the manifest makes.

**Run a cycle now**, which is what to do after dropping a new document in the folder rather than waiting for
the schedule:

```
docker exec ingestion python3 /app/ingest.py --documents /documents
```

### The manifest

`ingest_manifest` holds one row per document, keyed by its path relative to the documents root: the sha256
of the file as ingested, its size, its page count, its chunk count, and enough about its failures to decide
whether to try it again. The hash decides everything. A file whose bytes are unchanged is skipped without
being parsed, so a cycle over a client's corpus costs one read per document rather than a parse and an embed
per document. A file whose bytes changed gets new chunk ids, because the id covers the content, and its old
chunks are removed before the new ones are written so a document that shrank cannot leave orphans behind.

Removals are reconciled from the manifest rather than from the filesystem. A document the manifest knows
about whose file has gone is removed from both indexes, and a documents mount that failed to appear cannot
empty a client's index while the container restarts. A file that was never ingested cannot cause a removal.

A document that fails twice is dead-lettered until its bytes change, which is `INGEST_MAX_FAILURES` set to 2.
The hash recorded on failure is the hash of the bytes that failed, so the check works and a replaced file
gets another chance without anyone being told to look.

**Where the failure state lives, and how to clear it:**

```
docker exec ingestion python3 /app/ingest.py --status
docker exec ingestion python3 /app/ingest.py --failures
docker exec ingestion python3 /app/ingest.py --clear-failures
docker exec ingestion python3 /app/ingest.py --clear-failures company/irs-p15.pdf
```

`--status` reads the last cycle's record from `/state/last-cycle.json` and exits non-zero when that cycle had
a failure, which is why the container's healthcheck runs it rather than checking the process. With no
argument `--clear-failures` clears every recorded failure, and with a path it clears one. The next cycle then
tries those documents again. It is for a change to the failure rules rather than a routine step.

**`--failures` is the one to reach for, and it reads a different place.** `--status` knows only about the
cycle that just ran, so a document that has been dead-lettered drops out of it: the next cycle skips the file,
succeeds, and the deployment reports healthy while a client's document is not in the index. `--failures` reads
the **manifest**, which is the only thing that remembers, and prints each failed document with its failure
count, its kind, whether it is still being retried, the error itself, and the command to try it again.

```
2 document(s) have failed at some point:

  company/terms.rtf
    failures: 2 (permanent), DEAD-LETTERED, not retried until its bytes change
    last error: RuntimeError: the parser failed on company/terms.rtf: {"task_id": "...", "task_status": "failure"}
    try it again with: docker exec ingestion python3 /app/ingest.py --clear-failures company/terms.rtf
```

**Read the error before deciding what to do, because the classification does not distinguish the two cases
that matter.** A missing system dependency and a corrupt client document both arrive as `permanent`, and their
fixes have nothing in common: a parser refusal naming a tool or a format is ours to fix, and a refusal on
content is the client's file. That is how an entire format went unparsed while the deployment looked healthy —
the binary was missing, the message said so, and nothing surfaced the message.

**Clearing a failure is required after any parser fix.** The latch is keyed on the content hash, so installing
a missing binary does not make an already dead-lettered file get retried. Without this step a correct fix
looks like it did not work.

### Re-ingestion, and `PAYLOAD_VERSION`

The skip decision is made on a file's bytes. That is right for a file that was edited and wrong for a change
to the code that derives the stored payload from those bytes. Then the bytes are identical, the manifest
still believes its old claim, and the fix never arrives. It has happened twice in this build: two exhibits
recorded as ingested with no chunks behind them, and 29 chunks of HTML left labelled machine-read after the
code that labelled them was corrected.

`PAYLOAD_VERSION` in `services/ingestion/ingest.py` names the rules a stored payload was derived under. It is
`payload-v4` today. The manifest records it per document, and a record whose value differs from the running
worker's is re-derived even though its bytes have not changed. Bump it when the meaning of a stored field
changes, and the next cycle re-derives the whole corpus once. For the fixture corpus that is about ten
minutes. For a client's, it is the price of not serving labels the code no longer produces.

It is deliberately separate from the chunker's version, which is `chunker-v1`. A payload change does not
change a chunk's text or its chunk id, and forcing a re-parse for every labelling fix would hide the
difference between the two.

A record that claims success with no chunks behind it is re-ingested without being asked, because a manifest
is a cache of a claim about the index and a record with nothing behind it is a false claim whatever wrote it.

### The collection contract

Migration 015 adds `collection_contracts`, which records what built the vectors in a collection: the model
and its revision, the pooling, whether vectors are normalised, the query and passage prefixes, the truncation
behaviour, the dimension, and the chunker version.

The failure it exists for is quiet. Change the embedding model or its pooling, and every vector already
stored came from the old contract. Similarity between a new query vector and an old passage vector is not
zero, it is meaningless, and meaningless similarity still returns results in a plausible order. So a search
checks the fingerprint before it runs and refuses when the contract has moved, naming the field rather than
reporting that two hashes differ. Re-embedding is a decision, not a repair, and nothing re-embeds
automatically.

**Check the embedding service and the collection directly:**

```
docker run --rm --network ai-net -v /opt/oi-core:/w -w /w \
  --entrypoint python ghcr.io/docling-project/docling-serve-cpu:v1.36.0 \
  /w/tests/fixtures/api_contract_probe.py
```

The probe reports which batch endpoint answers, whether a batch returns one vector per input, the
collection's vector configuration and dimension, and a point it writes that must be found by a payload filter
and then removed. Read the endpoint it found against the one the worker tries first, because a request shape
the service ignores looks like a missing feature rather than a malformed request.

**If it breaks.**

- A document the parser now reads differently is still skipped: the bytes are the same, so the hash matches
  and the manifest believes its old claim. Bump `PAYLOAD_VERSION` for a payload change, and use
  `--clear-failures` when the failure rules moved.
- The worker re-ingests everything every cycle: the manifest is in memory, which happens when `DATABASE_URL`
  is unset or Postgres is unreachable. It says so at the top of its output rather than doing it quietly.
  Check `docker exec ingestion env | grep DATABASE_URL`, and that `postgres` is healthy.
- `the parser returned no structured output`: the conversion options were not accepted. They are sent as
  separate multipart fields, and the parser's own refusal is the authority on their names: a request naming a field it does not know returns a 422 listing every field it accepts.
- Chunk counts far above the page count the run prints: OCR read a page as several regions. Compare against
  the page count before assuming the chunker is wrong.
- A cycle exits non-zero with no failure in the log: the exit code is non-zero when any document failed,
  deliberately, so a scheduled run surfaces a bad file. `--status` names it.

## 13b. Retrieval

Retrieval is what chooses the passages an answer is built from. Two searches run and their results are fused.
The dense arm embeds the question and asks the vector store for the nearest passages, filtered by the role's
permitted roots. The lexical arm looks for the exact references in the question in a Postgres table of chunk
text, under the same access clause in its own query. The fusion is reciprocal rank with `k=60` and equal
weights, so a confident first place in one arm does not simply win, and the tie is broken on chunk id so the
same question produces the same order twice.

The lexical arm exists because a similarity search cannot see an identifier. On the recorded acceptance run,
`Pub. 515`, `Form W-3SS` and `Pub. 519` were found with the arm on and missed with it off, while `Form W-4`
and `Pub. 15` were found either way as controls. What the arm fixes is phrasing rather
than rarity: the same reference is found or missed depending on how the question is worded, and the bare form
a person types, `Pub. 515` on its own, was missed every time. Plain Postgres full-text matched none of the
seven phrasings, because `plainto_tsquery` ANDs every term of a natural question and the parser splits
`Form W-4` into pieces. So references are normalised by an extractor both the ingestion worker and the
retriever share, and a small alias table closes the gap between `Pub. 515` and `Publication 515`.

Reranking runs on the fused head. The reranker scores the question against the top candidates from the fused
list, up to `RERANK_DEPTH` of them, and it can promote a passage the fusion barely kept or demote one it
ranked first. The tail keeps its order behind the head, because a reranker that saw the top of a list cannot
reorder the rest.

**`RERANK_DEPTH` is a ceiling, not a count, and the two are easily confused.** It defaults to 50 and is set
nowhere in this deployment, so the effective number is however many candidates the arms actually returned —
on the recorded run above that was 15, which is why `rerank_depth` reads 15 rather than 50. The probe prints
the number it really saw, so read `rerank_depth` from the output rather than assuming the configured ceiling
was reached. On a large corpus the ceiling is what binds; on this one it is not.

**The yardstick first:**

```
oi run --rm -v /opt/oi-core:/w eval
```

```
configuration: fused   k=10   n=10
  retrieval  100% (6/6)
  support    100% (4/4)
  support was not measurable on 2 answerable question(s): no figure in the reference answer, so it needs entailment

  category            n   retr   supp
  paraphrase          1   100%    n/a
  single-fact-prose   2   100%   100%
  table-cell          2   100%   100%
  two-document        1   100%    n/a
  unanswerable        2     0%    n/a
  unanswerable-near-miss  1     0%    n/a
  unanswerable-table  1     0%    n/a

  supported answers from documents with no real pages: 1 of 4. For those the page rule is satisfied trivially and has not been tested.

  unanswerable top scores reaching into the answerable range: True
    answerable   [0.684, 0.622, 0.599, 0.557, 0.548, 0.535]
    unanswerable [0.641, 0.582, 0.558, 0.524]
```

Two rates, and the difference is the point. Retrieval counts the document holding the answer among the
passages returned. Support counts a passage containing the answer's own value, which is much stricter. A long
document is easy to be near, so a system returning page 1 of the right filing for every question would score
well on the first rate and badly on the second.

The last two lines are the measurement a refusal threshold would be built on, and they say it would not work
at this corpus size: the strongest unanswerable question scores 0.641 and the weakest answerable one 0.535,
so no cutoff on the top score separates a question the corpus answers from one it does not. A refusal has to
come from the citation layer instead. The line about pageless documents is a coverage statement rather than a
result, and it is the one number here worth reading carefully, because the obvious gloss of it is wrong.

**The measurement is about the passage that supported the answer, not about the document the question names.**
`page_is_nominal` is read from the supporting chunk's own payload, and a document can be mixed: of the twelve
ingested, `irs-p15.pdf`, `rate-card-2026.xlsx` and `dispatch-review-q1.pptx` report real pages on every chunk,
`apple-10k-2024.htm` and all three fee schedules are mixed, and the rest are nominal throughout.

So `1 of 4` means: four answerable questions had their support measured, and **one of those four was supported
by a passage from a pageless chunk**. For that question the question set's rule — a cited page must fall inside
its chunk's range — is satisfied trivially, because 1 is inside 1..1, and the rule has not really been tested.
For the other three it was tested and passed. That is the honest ceiling on what this run proves about page
checking, and it is a stronger result than the inversion of it would be.

**Which questions are affected is not a fixed fact about the question set**, since a rerun can support the same
question from a different chunk. **Four** of the ten can draw on a pageless chunk — `Q05`, `Q06`, `Q07` and
`Q10`, with `Q05` drawing on two exhibits — and that is four questions across five question-to-document pairs.
`Q01` is not among them: it draws only on the IRS publication, whose every chunk carries a real page. So a
change to ranking is enough to move the number, which is why the harness reports it per run rather than
recording it as a property of the corpus.

**Then one line per question**, and this is the part worth reading rather than skimming. Each carries the
question's category, the rank the answering passage was found at, and — for a question the corpus cannot
answer — a paragraph saying what is missing and what an answer would have had to invent:

```
  ok  Q01  single-fact-prose doc ans  rank=2  matched the figure 184,500
  ok  Q02  single-fact-prose doc ans  rank=1  matched the figure 200,000
  ok  Q03  unanswerable               rank=None  NOT ANSWERABLE from this corpus, so this
      question's original `answerable: true` was wrong. …
  MISS Q05  two-document      doc      rank=None  the question states no reference figure, so
      support needs entailment and is not measurable at this layer. Excluded from the support
      rate rather than counted as a miss.
  …
wrote /tmp/eval-report.json
```

**Two of the ten questions come back `NOT ANSWERABLE from this corpus`, and that is the fixture having been
corrected rather than a failure to fix.** `Q03` was written as answerable and asks for a new-hire reporting
deadline that Publication 15 does not state; `Q04` asks for a withholding-table cell that lives in Pub. 15-T,
which is not in the corpus. Writing their reference answers is what showed both are unanswerable, the question
set now records them as `answerable: false`, and **the paragraph printed under each question is the note that
records why.** Reading those two paragraphs is the useful part of a run: they say what the corpus does not
contain and what an answer would have had to invent.

The rates are computed over the answerable questions only, so neither dilutes them, and both categories show
`0%` retrieval because nothing is expected to be found.

`Q08` is the one worth reading in full, because it is a near miss doing its job: the corpus does contain a
mileage figure, but it is the company's own client-facing rate of 0.67 per mile, and the question asks about
employee reimbursement. Answering `67 cents` would match a vendor billing rate to an employee question, which
is exactly the failure that question exists to catch. The question set is in `fixtures/corpus/questions.yaml`.

**`eval` takes no arguments and prints no usage.** It parses no argv at all, so `--help` is silently ignored
and a full evaluation runs instead. Its controls are environment variables — `K` (default 10),
`CONFIGURATION`, `ROLE` (default `staff`), `QUESTIONS`, `CORPUS_MANIFEST`, `REPORT_OUT` — and it **requires the
checkout mounted at `/w`**, because the harness imports the retriever and the ingestion worker from there
rather than carrying its own copy. `CORPUS_MANIFEST` is the one that matters if you point `QUESTIONS` at your
own set: it is what maps a question's document id to the file the corpus holds, and the default is the
fixture corpus:

```
# without the mount: fails, because the image is not the code
oi run --rm eval --help
ModuleNotFoundError: No module named 'ingest'
```

That failure is worth recognising rather than debugging: the eval image carries the harness and botocore, and
the mounted checkout carries everything the harness calls.

Ten questions can catch a search that is broken. They cannot support a quality claim, and the client's own
sixty arrive in C8.

**What each arm contributed.** The retriever's result carries `arms`, a count per arm. **Read it with one
caveat, because it cost a review a blocking finding:** `lexical: 0` is ambiguous. It is recorded both when the
lexical arm ran and matched nothing and when the arm could not run at all, because `lexical_search` returns an
empty list when `DATABASE_URL` is unset and the count is taken from that list. **The key is always present.**
So `lexical: 0` on a deployment whose documents contain identifiers the dense arm misses is more likely a
missing `DATABASE_URL` than an empty index, and the way to tell them apart is to check the environment rather
than the count:

```
oi run --rm --entrypoint python eval -c "import os; print('DATABASE_URL set:', bool(os.environ.get('DATABASE_URL')))"
```

The acceptance probe prints the dictionary under each verdict:

```
docker run --rm --network ai-net --user "$(id -u):$(id -g)" \
  -v /opt/oi-core:/w -w /w \
  --entrypoint python oi-core/ingestion:local \
  /w/tests/fixtures/hybrid_acceptance.py
```

The recorded run, against the deployed store on 2026-10-13, with the lexical index holding 1131 rows and 445
of them carrying identifiers:

| Reference | hybrid on | hybrid off | verdict |
|---|---|---|---|
| `Pub. 515` | found | missed | HYBRID WINS |
| `Form W-3SS` | found | missed | HYBRID WINS |
| `Pub. 519` | found | missed | HYBRID WINS |
| `Form W-4` | found | found | both, as expected |
| `Pub. 15` | found | found | both, as expected |

Three references the lexical arm is what finds, and two controls where the dense arm already did. A test built
on a phrasing the dense arm happens to handle would prove nothing, which is why the bare form is the one used.

**Reranking, and what it changed:**

```
docker run --rm --network ai-net --user "$(id -u):$(id -g)" \
  -v /opt/oi-core:/w -w /w \
  -e AWS_ACCESS_KEY_ID -e AWS_SECRET_ACCESS_KEY -e AWS_REGION=us-east-1 \
  --entrypoint python oi-core/eval:local \
  /w/tests/fixtures/rerank_acceptance.py
```

```
questions reranked: 10 of 10
per question: slowest 428 ms, average 344 ms
budget 5000 ms: WITHIN

WHAT IT CHANGED
  Q07: rank 10 -> 1
  Q01: rank 1 -> 2
  Q06: rank 1 -> 2
```

Latency is measured and comfortably inside the budget. The quality half shows no movement, and that is not
evidence that reranking does not help: both rates were already at their maximum, so there was nothing for it
to improve, and the comparison says so rather than claiming a benefit or denying one. The evidence is in the
ranks. Q07 moved from rank 10 to rank 1, and at rank 10 it was one place from being dropped. Q01 and Q06 each
moved from 1 to 2, so the reranker demotes as well as promotes. A ten-question set cannot resolve a benefit
when both rates are at their maximum, which is why the client's sixty questions are where the second half
becomes measurable.

The reranker is a paid provider model. If it cannot run, the result says `reranked: false` with a reason
rather than falling back to the fused order quietly, so a deployment answering from the fused list while
believing it reranked is not a state this code can reach.

**If it breaks.**

- No identifier hits at all: the lexical arm is not running. It returns an empty list when `DATABASE_URL` is
  unset or unreachable and prints why, and `arms` records `lexical: 0`.
- An identifier hits the wrong document: check the extractor on both the question and the chunk text. The two
  sides share one function, so a disagreement means a chunk was indexed before the normalisation changed,
  which `PAYLOAD_VERSION` handles by re-deriving the corpus.
- Fewer passages than the limit: the two stores have drifted. A chunk the lexical arm names and the vector
  store does not hold is counted in `withheld` rather than skipped, because it is a reconciliation fault
  worth seeing.
- `HTTP Error 403` naming AWS Marketplace actions: the reranker subscription has lapsed or the account
  changed. It is a paid agreement rather than a permission, and no policy change substitutes for it.
- The rates do not move after a change: check whether they were at their maximum before it. A change measured
  against a system already at 100 percent is being measured on ranks.

### Asking one question

**The assistant API is the supported way to ask a question now, and this page predates it.** What
follows is the retrieval path as a function, `search()` in `services/retrieval/retriever.py`, which is how this
stage is exercised and diagnosed. **To ask a question as a client would, use `POST /ask` — section 13g.** This
page is for the case where retrieval itself is what you are looking at. The probe below runs one
question through the whole path, and it is the way to answer "what does the system return for this?"
today. Section 25 records the gap.

```
cd /opt/oi-core
docker compose -f deploy/office/compose/docker-compose.yml --env-file rendered/env \
  run --rm --entrypoint python -v /opt/oi-core:/w:ro -w /w eval /w/tests/fixtures/ask.py \
  -q "What is the standard labour rate per hour?" --role staff
```

**Two parts of that command are load-bearing, and both were wrong in an earlier draft of this page.** The
`compose run` form supplies the environment; the `-v /opt/oi-core:/w:ro -w /w` supplies the code. Neither is
cosmetic.

**Why `compose run` and not a bare `docker run`.** The probe needs `DATABASE_URL` for the lexical arm and for
the supersession marking, and `rendered/env` does not hold it: the DSN is assembled by the compose file from
`POSTGRES_USER`, `POSTGRES_PASSWORD` and `POSTGRES_DB`, because the host and port belong to the compose network
rather than to the deployment. **A bare `docker run` with `--env-file rendered/env` therefore starts the probe
with no database at all**, and the two consequences are silent rather than loud:

- `lexical: 0` in the output, which reads exactly like an arm that ran and found nothing. It did not run.
- **No passage is marked `** NOT CURRENT`, so a claim resting on a superseded document is certified as
  supported.** Supersession is a database fact, and with no database every document looks current.

Both were found by a review comparing the two invocations, and neither shows up as an error. If you must use
`docker run`, pass the DSN explicitly with `-e DATABASE_URL="$(grep '^POSTGRES_USER=' rendered/env | cut -d= -f2-):…"`,
or accept that you are testing the dense arm alone and read the output knowing that.

**Why the `-v` and `-w` are needed even on `compose run`.** The `eval` service does not mount the checkout: its
compose definition sets `RETRIEVAL_PATH` and `INGESTION_PATH` to paths under `/w` but declares no volume, on the
assumption that whoever runs it passes one. `oi run --rm eval` does that for the plain harness invocation, and
**overriding the entrypoint drops it**, so this probe arrives at a container with no `/w` and stops with
`can't open file '/w/tests/fixtures/ask.py': [Errno 2] No such file or directory`. Mount it explicitly, as the
command above does. The same applies to every probe in this manual run with `--entrypoint`.

```
question: What is the standard labour rate per hour?
role:     staff   roots: ['company']
arms:     {'dense': 15, 'lexical': 3}
rerank:   {'reranked': True, 'rerank_depth': 15, 'rerank_ms': 251.8, 'rerank_budget_ms': 5000,
           'rerank_within_budget': True, 'rerank_model': 'cohere.rerank-v3-5:0', 'rerank_reason': None}
withheld: 0   denied: False   restricted matched: 0

[1] company/fee-schedule-2025.md  pages 1-1  acl=company  score=0.7549  ** NOT CURRENT, superseded by company/fee-schedule-2026.md
    Schedule of Fees and Reimbursable Rates
    Item | Basis | Rate
    Standard labour, business hours | per hour | 132.00
    …
```

**`lexical` is a real number here, and that is the point.** `lexical: 3` means the lexical arm ran and returned
three ids. Compare it against the sample above whenever you run this probe on a new deployment: `lexical: 0` is
ambiguous, and the ambiguity is the failure described a few lines up.

Five things that output tells you, and each one is a line you would otherwise have to guess at:

- **`role` and `roots`** are the retrieval role and the folders it may read. `--role executive` widens
  `roots` to `['company', 'executive']`, and a role that is not in the map exits 2 rather than defaulting.
- **`arms`** is what each search contributed, and **`lexical: 0` does not mean what it looks like.** The key
  is always present, and `0` is recorded both when the arm ran and matched nothing and when it never ran
  because `DATABASE_URL` was missing. There is no missing-key case to read. Check the environment rather than
  the count, as "What each arm contributed" above sets out.
- **`rerank`** is the reranker's own account of itself: the depth it saw, what it cost in milliseconds,
  whether that was inside the budget, and which model did it. `reranked: false` comes with a
  `rerank_reason` rather than a silent fallback to the fused order — the module reports that it did not
  run instead of pretending the fused order was the reranked one.
- **`restricted matched`** is the access measurement: what a second search found outside the role's roots. It
  is the number a `counted` denial reports.
- **`withheld` has two causes and they are worth telling apart, because it should be 0 on a healthy
  deployment for the first one.** It counts (a) a passage the store returned that the role may not see, which
  means the query's filter clause is not working, and (b) a chunk the lexical arm named that the vector store
  does not hold, which means the two indexes have drifted apart. **(a) is a security fault and (b) is a
  reconciliation fault.** The output does not say which, so treat a non-zero `withheld` as worth investigating
  rather than as a diagnosis: run the access probe in section 13d for (a) and the hybrid probe below for (b).
- **`score`** is the vector arm's cosine similarity, or `n/a` for a passage only the lexical arm returned.
  It is not comparable across arms, which is why a passage from the lexical arm shows `n/a` rather than a
  misleadingly small number.

**Useful variations:**

```
# see the fused order instead of the reranked one
… /w/tests/fixtures/ask.py -q "…" --no-rerank

# ask as the privileged role, and watch `restricted matched` go to 0 because nothing is restricted from it
… /w/tests/fixtures/ask.py -q "What is the chief executive officer's base salary?" --role executive

# override the configured denial mode for this one call, to see the other two
… /w/tests/fixtures/ask.py -q "…" --role staff --denial silent
… /w/tests/fixtures/ask.py -q "…" --role staff --denial counted

# check a candidate answer's citations against what was actually retrieved
… /w/tests/fixtures/ask.py -q "What is the standard labour rate?" --role staff \
    --verify "The standard labour rate is 145.00 per hour. [5]"

# machine-readable, for piping
… /w/tests/fixtures/ask.py -q "…" --json
```

**`--verify` needs a citation marker, and a claim without one is refused rather than passed.** `[n]` is the
only supported form, and it carries the same meaning it does in section 13c: this sentence rests on passage
`n` of what was retrieved. Both outcomes, run against this corpus:

```
  [supported] The standard labour rate is 145.00 per hour. [5]
all supported: True

  [REFUSED (number_not_in_source)] The standard labour rate is 999.99 per hour. [5]
      states 999.99 and the cited passage does not contain it. This is the check that catches a
      substituted digit.
refusals by reason: {'number_not_in_source': 1}
all supported: False
```

A sentence with no marker comes back `REFUSED (no_citation)` — *the sentence carries no citation marker, so
none of the checks could be run on it*. That is the check being honest rather than lenient, and it means an
answer written in prose without markers verifies as entirely unsupported. The verifier works from the
passages `ask.py` retrieved, which is why the passage numbers in your answer must match the output above it.
It is the same verifier that **Citations and verification** documents — the page the contents lists as 13c,
which shares this heading in the file — so a claim can also be refused for being `superseded` when the figures
are right.

**The exit codes are the contract**, because this is the only piece of the C2 path a script can call. There
are three of them and they key off whether anything came back, not off whether the answer was denied:

| Exit | When |
|---|---|
| `0` | passages were returned. **This includes a denied search that still returned what the role may see**, which is the common case |
| `1` | nothing was returned at all, or a json-mode run found no passages |
| `2` | the role is not one of the two the retriever knows, so nothing was searched |

**A denial is not an error and must not be treated as one.** In `counted` and `denied` modes the retriever
still returns the passages the role may see and marks the result `denied: True` with a disclosure beside
it, so a script sees exit 0 and a populated result. Exiting non-zero on `denied` would page someone every
time the access filter did its job. If you want to act on a denial, read the `denied` field from `--json`
rather than the exit code.

**A role that may see nothing is a configuration error rather than an empty result.**
`permitted_roots()` raises for a role whose permission set is empty, and `ask.py` catches that and exits 2
with the reason on stderr, so a mistyped role and a corpus that came back empty are distinguishable.

**What this probe is not.** It does not call a model. Handing these passages to one, with the citation
rules and the confidence gate attached, is what section 13c describes and what the assistant API does on
every request — sections 13g and 13h. **So this page shows the retrieval half on its own**, which is how you
look at retrieval rather than at an answer; the generation half is one `POST /ask` away and is documented
there.



The verifier is the layer that checks an answer against what it cites. Everything below it checks whether the
answer matches the source, and none of it checks whether the source is the right source or was read correctly.
Those are the two most likely wrong answers in a small business: a real number from a document somebody
replaced, and a real number from a page the scanner misread. Both pass literal matching, both pass a
comparison of numbers, and both would otherwise come out certified. So seven checks run over each sentence,
deterministic, with no model and no new dependency.

**The seven checks, in the order they run:**

| Check | Reason | What it catches |
|---|---|---|
| Citation resolves | `citation_not_found` | A citation to a passage that was never retrieved |
| Source is current | `superseded` | A real figure from a document a person replaced |
| Confidence | `low_confidence_scan` | A figure from a page the parser was unsure about |
| Numbers | `number_not_in_source` | A substituted digit |
| Identifiers | `identifier_not_in_source` | The right document and the wrong form |
| Quotes | `quote_not_in_source` | Words in quotation marks that are not in the source |
| Page | drift, reported separately | The right words with the wrong page attached |

The order is deliberate. A citation that does not resolve is checked first, because every later check reads
the passage it names and a fabricated index would make them all meaningless. Supersession is second, because
a number from a replaced document is a correct quote of the wrong source and no amount of literal matching
catches it. The literal checks come last, since they are the ones that can refuse a correct answer.

**A sentence with no marker is not a refusal.** It comes back `uncited` with the reason `no_citation`.
Transitions and direct answers have nothing wrong with them, and refusing them would refuse most of a normal
reply. Page drift is also not a reason to drop a claim: it is reported by `check_page` as a detail, because
the words are right and the place is wrong, and a person can act on that. A claim is marked rather than
dropped, so a reader can see which sentence failed and why.

**Three rules keep the false refusals down**, because a check that refuses correct answers gets turned off
and a check that is turned off catches nothing. Numbers compare with trailing zeros dropped only, so `145.00`,
`145.0` and `145` are one number, and the leading zero of `0.67` is left alone because stripping it once
refused a correct answer. Supersession fires only when every cited passage is superseded, so a claim citing
the replacement alongside the thing it replaced is supported. And the confidence gate applies to figures
rather than prose, because a digit read imperfectly is a different number and a word read imperfectly is
still the word.

**The sentence splitter is where the subtleties are.** A citation marker trails the sentence it supports, so
a splitter that cuts in the wrong place runs every check against the wrong passage. Three faults were fixed
here. A full stop, a space and a capital is what a sentence boundary looks like, and it is also `Pub. 515`,
so the splitter knows this corpus's abbreviations and treats an ambiguous stop as not a boundary, because
merging two sentences is safe and splitting wrongly is not. A single initial is not a boundary either.
Markers may sit between the full stop and the next sentence, as in `employee.[1] Form W-4`, so they are
skipped before the boundary is decided. The rule that came out of it is that the splitter must never lose a
marker, and there is a test that says so.

### The figure checks

The citation checks confirm that a number is present in the passage it cites. If OCR put the wrong number
there, they confirm the wrong number. The figure checks are the second half: a read that produces something a
number cannot be, and arithmetic the document itself states.

```
docker run --rm --network ai-net --user "$(id -u):$(id -g)" \
  -v /opt/oi-core:/w -w /w \
  --entrypoint python oi-core/ingestion:local \
  /w/tests/fixtures/figures_acceptance.py
```

```
PART ONE: a damaged figure is caught by a format or arithmetic check
  shapes a defective read produced in the step 3.4 measurement:
    CAUGHT 2026-13-45          malformed.date
    CAUGHT 02/31/2026          malformed.date
    CAUGHT 1450%               malformed.percent_range
    CAUGHT 12345678901234.00   malformed.implausible_magnitude
    CAUGHT 12,34,567.00        malformed.not_a_figure

  and figures that are not damaged, which must not be flagged:
    ok    145.00              -
    ok    1,250.00            -
    ok    (1,450.00)          -
    ok    145%                -
    ok    32.50               -
    ok    0.67                -

  arithmetic, where the document states the relationship:
    ok    100.00 + 45.00 against a stated total of 145.00: True
    ok    100.00 + 45.00 against a stated total of 155.00: False

  and a document with nothing to add up returns None rather than False:
    None - most documents have no relationship to check, and False would mark every figure in
    every prose document as unconfirmed for a reason that is not about the figure

PART THREE: a scanned page is labelled and a digital one is not
    is_ocr=False  672 chunk(s)  company/apple-10k-2024.htm
    is_ocr=False   13 chunk(s)  company/apple-ex10-1-2024.htm
    is_ocr=False   16 chunk(s)  company/apple-ex10-22-2024.htm
    is_ocr=False    1 chunk(s)  company/callout-rate-approval.eml
    is_ocr=False    2 chunk(s)  company/dispatch-review-q1.pptx
    is_ocr=False   20 chunk(s)  company/fee-schedule-2025.md
    is_ocr=False   20 chunk(s)  company/fee-schedule-2026-copy.md
    is_ocr=False   20 chunk(s)  company/fee-schedule-2026.md
    is_ocr=False  320 chunk(s)  company/irs-p15.pdf
    is_ocr=True    38 chunk(s)  company/irs-p15.pdf
    is_ocr=False    1 chunk(s)  company/onboarding-notes.md
    is_ocr=False    7 chunk(s)  company/rate-card-2026.xlsx
    is_ocr=False    1 chunk(s)  executive/executive-compensation.md

  does the lexical index carry the label? NO
    The column is absent and that does not matter: nothing builds a passage from chunk_index.
    lexical_search returns ids only, and every id the dense arm did not return has its payload
    fetched from Qdrant with with_payload true, so is_ocr reaches the passage on both arms.
    This probe printed the opposite conclusion first and the fidelity checker caught it.

PART TWO: a figure that passes every check is disclosed as machine-read when cited
  a scanned passage carrying an unverified figure: machine-read from a scanned page and not confirmed by any other check in the document
  and one the document's own arithmetic confirms is not disclosed, because something did verify it

PASSED: the measured damaged shapes are caught and the intact figures are not, arithmetic confirms what
the document states, scanned and digital chunks are labelled apart, and an unverified figure from a
scanned page is disclosed when it is cited
```

**The `PART THREE` tally is the result worth reading, and it has two parts.** The first is the split on
`company/irs-p15.pdf`: **320 chunks labelled digital and 38 labelled scanned**, from one document. That is the
label doing its job at chunk granularity rather than per file, so one figure from that publication can carry a
machine-read caveat while another from the same file does not. Every other document is wholly one or the
other.

The second is the line that says the lexical index does **not** carry the label, followed by the probe's own
correction. The column is genuinely absent from `chunk_index`, and it genuinely does not matter, for the reason
printed underneath: nothing builds a passage from that table. `lexical_search` returns ids only, and every id
the dense arm did not already return has its payload fetched from Qdrant with `with_payload` true, so `is_ocr`
reaches the passage on both arms. **The probe printed the opposite conclusion first and a review caught it**,
which is why the correction is in the output rather than in a comment — a reader who sees only the `NO` would
reasonably conclude the lexical arm loses a field the verifier needs.

`PART TWO` also records a negative result rather than glossing it: on the question it asked, no scanned passage
was retrieved, so the disclosure could not fire on the real path, and the probe says so and checks the
behaviour directly instead.

A date like the 31st of February passed a first version that only checked a day between 1 and 31, so dates
are checked against a real calendar. A negative figure in parentheses did not parse at all, because the brackets are the
sign. And the percentage line is drawn where a percentage stops being plausible as any percentage rather than
as a rate, because 145 percent is a real growth rate and flagging it would be a check that fires on correct
documents. A document with nothing to add up returns `None` rather than `False`, because returning `False`
would mark every figure in every prose document as unconfirmed for a reason that is not about the figure.

**The bound is the reason this page says what the check cannot do.** A read that produces something a number
cannot be is caught. A read that produces a different plausible number is not, and that is the common case: in
the measured sample, fourteen of fifteen wrong readings were exactly that. The check catches the malformed
quarter of misreads and confirms what the document's own arithmetic confirms. Calling it a check that catches
OCR errors would be believed, and it would be wrong.

The disclosure travels inside the citation, so it goes wherever the citation goes. Saying nothing is not a
guarantee: most figures carry no caveat because there was nothing to disclose, not because they were checked.

### What `supported` means, and what it does not

`supported` means every mechanical check passed. It is not the same as true. Entailment is Layer 1, it needs a
model, and the reply path does not have one until Phase 4, so a sentence can be supported and still not be
entailed by the passage it cites. The research is explicit that the verifier's own false-negative rate sets the ceiling on what the product can claim and that the ceiling has to be published rather than implied. **It is not published yet**, because measuring it needs entailment, which is not built and is named as open work. The one error rate this product has measured is the figure checks': fourteen of fifteen wrong readings produce a different plausible number and pass every check. That is a different number and does not stand in for this one.

Support grading needs entailment for prose, which is why the eval harness measures support by finding the
answer's own figure in a passage. A question whose reference answer carries no figure is excluded from the
rate and counted beside it. On the recorded run, two answerable questions were excluded for exactly that
reason. A rate that counted an unmeasurable question as a miss would be a measure of how many questions have
a figure in their answer, reported as a measure of retrieval quality.

**Two bounds on the confidence gate, and the second is worth knowing before you rely on it.** The gate is
document-level rather than page-level, because docling-serve does not populate the per-page confidence map
this release exposes, so a page whose digits were misread cannot be told from a page read cleanly.
Separately, the gate compares the passage's own parse score, and that score lives in the vector payload: the
gate reads `parse_score`, which the worker records per document, and refuses a claim that states a number or
an identifier and rests on a passage scored below the floor. It cannot fire on a page the parser never scored,
and it is document-level rather than per page, so a scan whose bad page the parser was confident about passes
it. The unit tests exercise it with a passage that
carries the score.

### Trying it on the deployment

```
docker run --rm --network ai-net --user "$(id -u):$(id -g)" \
  -v /opt/oi-core:/w -w /w \
  --entrypoint python oi-core/ingestion:local \
  /w/tests/fixtures/citations_acceptance.py
```

The probe retrieves passages for a question about the standard labour rate, prints each one with its score,
prints which of them a person may rely on, and then checks an answer built to contain a correct claim, an
invented citation, and a figure that appears nowhere in the corpus. It ends with `refusals by reason`, which
is the number to watch: a rising `number_not_in_source` is a generation problem and a rising
`citation_not_found` is a prompt problem.

The recorded proof is that the invented citation was rejected as `citation_not_found` and the impossible
figure was marked `number_not_in_source` while remaining in the reply rather than being dropped.

**The first run found the failure this layer exists for, by accident.** The question asked for the standard
labour rate, the retriever returned the superseded 2025 schedule as passage `[1]` with the current 2026 one at
`[2]`, and the verifier refused the claim built from `[1]` with the reason `superseded`. A real figure, from a
real document, on a real page, correct in every literal respect, refused because the document had been
replaced. Nothing below this layer would have caught it. The probe was wrong and the check was right: it had
assumed the top-ranked passage is a good source, and it now builds its claim from the first passage a person
may rely on.

**The ranking item that was open is now closed, and the closing is worth reading because it was not a change
to ranking.** When this step was first built the superseded 2025 schedule ranked first and the current 2026
one second, so a user asking the current rate got a refusal while a good answer sat one place down. That was
recorded as open rather than fixed, because boosting current documents is a search-quality change that should
be measured against the golden set. It then resolved itself on a later run, and the honest record is that
**nothing was changed to fix it**:

```
  [1] company/fee-schedule-2026.md   p1  score=0.60290456   current
  [2] company/fee-schedule-2025.md   p1  score=0.59917563   SUPERSEDED
```

The margin is 0.0037, which is not a fix, it is a coin landing the other way. So the item is closed as
*observed current* rather than as *solved*: a ranking that puts the current version first by four thousandths
is one rerun away from putting it second again. If this matters for a client, it needs the golden set and a
deliberate choice about boosting current documents, not another observation.

**Two things in that output are worth noticing for a different reason.** The superseded `2026-copy` appears at
`[3]`, so a near-duplicate of the current document is still competing with it in the result set; resolving the
duplicate pair with `--distinct` or `--supersede` is the corpus fix, and section 13d covers that. And the
higher-scoring passages are `[4]` and `[5]` at 0.7592 — the single line stating the rate — while the
lower-scoring `[1]` is the whole table. **A small chunk that contains exactly the answer can outscore the
fuller passage a person would want cited**, which is a chunking observation rather than a retrieval fault, and
it is why the verifier works from what it was given rather than from what ranked highest.

**If it breaks.**

- A correct answer is refused: read the `detail`, which names the figure and the passage. If the figure is
  written differently from the source, the normalisation is the suspect, and the fix is a rule rather than a
  loosened check.
- Everything from a scanned document is refused: the gate is firing on the document's own score rather than
  on a page's, which is the bound described above. Check the stored score before assuming the gate is wrong,
  because a genuinely low score means the parser was unsure and the refusal is correct.
- A claim is reported `uncited` that clearly has a citation: the splitter lost it or cut the sentence. `[n]`
  is the only supported form, and there is a test asserting no marker is ever dropped.
- Nothing is refused at all: the answer has no `[n]` markers, so every claim is `uncited`. The marking
  convention is the prompt's to teach, and this module cannot invent it.

## 13d. Access control

### Two kinds of role, and the words are not interchangeable

This is the point in the manual where two vocabularies meet, and the earlier draft used the same word for
both. They are different things, defined in different files, and a role of one kind has no meaning as the
other.

| | **Model role** | **Retrieval role** |
|---|---|---|
| What it is | A name a caller asks the gateway for | A name the retriever maps to document roots |
| What it decides | Which model answers, and what the caller may spend | Which folders a search may read |
| The names | `front_desk`, `analyst`, `reader`, `coder` | `staff`, `executive` |
| Where it is defined | `manifests/<client>.yaml`, under `models:` and `budgets.roles:`. Section 12 covers minting keys from it | **`services/retrieval/retriever.py`, in the `ROLES` map. There is no manifest field for it.** |
| What a caller does with it | Passes it to the gateway, usually as the model name | Passes it to `search()`, which refuses a name not in the map |
| Add one by | Adding it to the manifest and re-running `keys` | Editing the map in code and redeploying |
| If it is wrong | The gateway refuses: 403 on a model the key cannot reach | The retriever raises `KeyError` and names the known roles |

**The four model roles in this deployment**, for reference, are in section 12 with their ceilings. **The
two retrieval roles** are:

```python
# services/retrieval/retriever.py
ROLES: dict[str, frozenset[str]] = {
    "staff": frozenset({"company"}),
    "executive": frozenset({"company", "executive"}),
}
```

A role's permitted roots are the folders its search may read: `staff` sees `company`, and `executive` sees
both. **The roots come from `access.roots` in the manifest, through the session's permit**, so adding a
boundary is a manifest change and a re-render rather than a code change. The retriever carries a small
hardcoded role map as well, and it is a fallback for a caller that supplies no permit: **when a session has
resolved the access, the permit's roots are the permission and the role is not consulted at all**, so there
is no precedence between two sources and nothing to widen by passing both. A caller with no permit and an
unknown role is refused rather than defaulted.
The four model roles are not retrieval roles, and a search asked for `analyst` is refused rather than
guessed at, because a default would grant the broader of two permissions to a typo.

So a caller that wants an answer grounded in documents has to supply both: a retrieval role to decide what
may be read, and a model role to decide who answers and who pays. Section 13b's `search()` takes the first;
`keys --probe` exercises the second. Nothing in C2 wires the two together end to end, which is what C3's
assistant API adds — section 25 records that.

The filter is a clause in the search query itself, in both arms, so a restricted passage never leaves the
store. There is a second check afterwards that counts anything the store returned that the role may not see.
Both are present because they fail differently: the query clause is the requirement, and the check is what
keeps a store bug from becoming a disclosure. A role that is not in the map is refused rather than defaulted,
because a default would grant the broader of two permissions to a typo.

**A denial is a measurement, not a guess.** A staff question whose answer sits in restricted content has to
be told apart from a question the corpus cannot answer, and a similarity cutoff cannot do it at this corpus
size. So the retriever runs a second search against the complement of the role's roots and denies when the
restricted best score beats the best permitted one. Only a better match counts, because a weak restricted
match exists for almost any question, and a warning that fires constantly is one users learn to ignore.

Three things keep that second search from being a leak. It runs with `with_payload: false`, so no restricted
text enters the process and only a number crosses the boundary. Its filter is `must_not` over the role's
roots, so a folder nobody declared a role for counts as restricted rather than being silently omitted. And
the point id it sees is a uuid5 over the document id, so it does not name a file.

**What a denied user is told is the client's policy.** It is declared in the manifest as `access.denial`, and
there are three modes because the trade has three defensible answers:

| Mode | What the user is told | What it costs |
|---|---|---|
| `silent` | Nothing. The answer is built from what the role can see | An incomplete answer is indistinguishable from a complete one |
| `denied` | Documents exist that may answer this and they lack access, with no count | Reveals that restricted content exists |
| `counted` | The same, plus how many passages matched | Reveals existence and implies how many documents hold it |

The default is `denied`, and the reasoning is the product's own: a silent system returns a partial answer
with no way for a user to tell it from a complete one, which is the confidently-wrong failure this design
exists to fight. A client who weighs the leak of existence more heavily sets `silent` and gets no disclosure
at all. The dev cloud manifest declares `counted`, because a developer debugging a missing passage wants the
number and there is no client to weigh the trade. An unrecognised value is refused rather than defaulted.

The fact and the disclosure are separate, and that separation is the point. `withheld` and `denied` stay
accurate in every mode, so the record can report what happened, and only `disclosure` is governed by policy.
In silent mode it is `None` rather than an empty message, so a caller that forgets to check the mode has
nothing to render. The count in `counted` reports how many restricted passages matched the search rather than
how many were withheld: the filter works, so nothing restricted is ever returned and `withheld` is honestly
zero on every question.

**See it on the deployment:**

```
docker run --rm --network ai-net --user "$(id -u):$(id -g)" \
  -e ACCESS_DENIAL_MODE=counted \
  -v /opt/oi-core:/w -w /w \
  --entrypoint python oi-core/ingestion:local \
  /w/tests/fixtures/acl_probe.py

# and again with each policy, so the client's choice is seen rather than taken from a unit test
docker run --rm --network ai-net --user "$(id -u):$(id -g)" \
  -e ACCESS_DENIAL_MODE=silent \
  -v /opt/oi-core:/w -w /w \
  --entrypoint python oi-core/ingestion:local \
  /w/tests/fixtures/acl_probe.py
```

**The `-e ACCESS_DENIAL_MODE=…` is not optional, and leaving it out is the trap this page used to fall into.**
`DEFAULT_DENIAL_MODE` is read from the environment **at import time**, and a bare `docker run` passes no
environment at all. So the probe without `-e` prints the library's fallback:

```
denial mode: denied (configured denied)
```

That is the code's default rather than the deployment's setting, which the manifest and `rendered/env` both
declare as `counted`. The line is honest about the distinction — it says *configured denied* — but a reader
who runs the command as it was written sees the wrong mode and concludes the manifest is not being honoured.
Passing `-e` is what makes the probe show what the deployment actually does. The same caution applies to any
probe run with a bare `docker run`: the tool reads its configuration from the environment, and only the
compose file supplies it.

The probe prints what is in the index by label, the denial mode in force, and then, for each role and
question, how many passages came back, the best restricted score, whether the denial fired, and the
disclosure the user would see. In silent mode that last line says the silent policy is working.

The recorded measurement, against the deployed store on 2026-10-13, with the index holding 1130 company
points and 1 executive point:

| Question, asked by the staff role | best permitted | best restricted | denied |
|---|---|---|---|
| CEO base salary | 0.62 | 0.7766 | yes |
| Executive bonus target | 0.58 | 0.6543 | yes |
| Severance on a change of control | 0.65 | 0.4088 | no |

The executive role reports `restricted_best` as null on all three, because nothing is restricted from it, and
`denied` is false. A staff search for either restricted figure returns ten passages and contains neither.

**The third row is a false negative, recorded rather than discovered later.** The executive memorandum does
answer the severance question, the content is there, a person cannot see it, and the system does not say so.
The cause is chunking rather than the comparison: the memorandum is 1,541 characters, which is one chunk, so
its embedding averages salary, bonus, equity and severance together, and a question about any one of them
matches that average weakly while a specific passage from a long filing matches strongly. The score
comparison is a heuristic and this is the shape of its failure, a short document whose content is varied
reads as a worse match for every question than a long document's targeted passage. The alternatives are
worse, because a bare hit test would deny on most questions, so this is a recorded bound.

### The version registry and near-duplicate flags

Nothing here guesses which of two documents is current. Every document is current by default, so a corpus
that never touches the registry behaves exactly as it did before the table existed, and declaring otherwise
is one command:

```
docker exec ingestion python3 /app/ingest.py --versions
docker exec ingestion python3 /app/ingest.py --supersede company/fee-schedule-2025.md \
  --by company/fee-schedule-2026.md --note "2026 schedule in force"
docker exec ingestion python3 /app/ingest.py --scan-duplicates
docker exec ingestion python3 /app/ingest.py --distinct company/fee-schedule-2026-copy.md \
  --note "a separate policy, not a copy"
```

**`--supersede` and `--distinct` do not check that the document exists.** Neither command reads the
manifest before writing the declaration, so **both succeed silently on a path that was never ingested or
was misspelled**, and the result is worse than an error: a superseded document that does not exist will
never be cited, and the document that should have been superseded is still current. Check the path
against the manifest first, because a typo here is invisible in the output:

```
docker exec ingestion python3 /app/ingest.py --status
docker exec postgres psql -U oicore -d oicore -c \
  'select doc_id, is_current, superseded_by from document_versions order by doc_id'
```

The same caution applies to `--by`: nothing checks that the replacement exists either, so a declaration can
name a document that is not in the corpus and the refusal it produces will say `superseded` while pointing
at nothing. Use the real relative path, exactly as the manifest records it, with no leading slash.

`--versions` prints every declaration and any unresolved near-duplicate flag. A superseded passage is
deliberately not dropped from the results: it reaches the verifier marked `is_current` false, and a claim
supported only by a replaced document is refused with the reason `superseded`. Dropping it here would answer
the question from a different document with no sign that the right one existed and had been excluded, which
is a wrong answer wearing a correct one's clothes.

Near-duplicate detection compares the hash of each chunk's text, and the share is measured over the smaller
document, because a five-chunk summary of a hundred-chunk report is entirely contained in it. A pair is
recorded and never acted on, because deleting the second copy loses a document a client put in a folder
deliberately. The overlap threshold is half the smaller document, and the floor is `DUPLICATE_MIN_SHARED_CHARS`
set to 500 characters of identical text, so two documents sharing a cover line are not compared. The pair is
stored ordered with a unique constraint, so one pair is one row whichever document arrived second.

`--scan-duplicates` exists because detection otherwise runs only when a file arrives, and a corpus ingested
before the check existed is never examined. That is not a rare case: every corpus becomes an old corpus the
moment the feature ships.

**If it breaks.**

- `unknown role`: the role is not in the map and the retriever refuses rather than defaulting. A default here
  grants the broader of two permissions to a typo, which is the failure worth being loud about.
- A staff store return above zero: the query's filter clause is not reaching the store. Print the body the
  retriever builds and check that `filter.must` carries the `acl` clause, rather than checking the results,
  because the results are what a missing filter also produces.
- A denial appears when the client declared `silent`: check `grep ACCESS_DENIAL_MODE rendered/env`. A
  manifest that declares `silent` with an env file that was not re-rendered still discloses.
- `withheld` above zero on a normal query: the store returned something the role may not see. That is the
  second line rather than the first, so it firing means the first line is not.
- Fewer passages than the limit with nothing withheld: the overfetch is too low or the corpus is small. The
  store is asked for three times the limit because withheld passages consume part of the result set.
- A duplicate pair is not flagged: run `--scan-duplicates` first. If it still reports nothing, the identical
  text is under the floor.
- A pair is flagged that is genuinely two documents: resolve it with `--distinct`, which records the decision
  and clears the flag without superseding anything. The flag is the feature working, not a false positive.

### Per-user access and the session it comes from

Sections above describe the two folder roles a deployment gets without asking: `staff` reads `company`, and
`executive` reads `company` and `executive`. That is the whole model until a client needs a boundary that is
not a folder, and then it is `access.groups`, `access.users` and `access.roots` in the manifest.

**Two words, and they are not interchangeable.** A **role** says what kind of caller this is, and it comes
from the session. A **group** says what a person may read, and it comes from the manifest. A session holds
one role and any number of groups, and what it may read is the union of the two.

**Access is a union with no deny rules and no precedence.** One user in `staff` and `finance` reads both, which
is a decision rather than an omission: a model with no precedence is one that can be tested to exhaustion, and
the union is a line a reader can check by eye. Per-document grants are not built. When a client needs one, the
extension is to write group membership onto the chunk at ingest so the payload carries real labels and the
filter stays a label match inside the query.

**A session is a record, and the token carries nothing.** The client presents a random identifier that
resolves to a row in `sessions`, and the row holds the subject, the role and the document roots. There is no
role in the token to edit, no signature to forge, and no claim the server has to believe. The roots are
**snapshotted when the session is minted**, so a later change to a group cannot change what an event recorded
last week meant.

**A request that supplies its own role is refused, and the refusal is written down.** That is the acceptance
criterion for this phase, and it is why the refusal is committed to the audit chain *before* the error goes
back to the caller. A refusal nobody can find afterwards is not a control.

#### Declaring the model

```yaml
access:
  roots: [company, finance, executive]
  groups:
    staff:     {roots: [company]}
    finance:   {roots: [company, finance]}
    executive: {roots: [company, executive]}
  users:
    - subject: dana@client.example
      name: Dana Reyes
      groups: [staff]
    - subject: sam@client.example
      groups: [staff, finance]
```

The four registry fields are **all or nothing**. A manifest that declares `groups` and forgets `roots` is
refused rather than defaulted, because a renderer filling in the gap would be enforcing a model the manifest
does not state. A manifest that declares none of them keeps the two built-in roles, which is what every
manifest in this repository does.

A role may carry no roots of its own, which is the usual shape when access is written with groups: the
renderer emits one role named `viewer` for that case, and every subject's permission comes from its groups.
If you would rather write permissions beside the roles, declare `access.roles` and those are used instead.
**A subject's groups are still unioned on top of its role**, so a declared role is not a ceiling.

#### Where it goes

The renderer writes a fifth file:

```
$ python3 scripts/render_manifest.py --manifest manifests/example-standard-cloud.yaml
rendered example-standard-cloud.yaml -> /opt/oi-core/rendered
  profile      cloud
  packs        core, document-chat
  local models (none)
  gateway      4 model entries
  access       built-in: two folder roles, no users
  secrets      reused from the existing env file
  env file     /opt/oi-core/rendered/env (mode 600)
```

With a registry declared, that line reads `access       declared: 3 group(s), 2 user(s)`. The file itself is
`rendered/access.json`, and both the session tool and the retriever read it from there. It is mounted rather
than baked into an image, so re-rendering takes effect on the next command without a rebuild.

#### The commands

```
$ alias oi='docker compose -f deploy/office/compose/docker-compose.yml --env-file rendered/env'

# What this deployment is actually enforcing, which is not always what the manifest says
$ oi --profile tools run --rm session show
registry   /app/rendered/access.json
declared   yes
deployment oi-core-dev-cloud

roles   1: viewer=[]
groups  3: executive=['company', 'executive'], finance=['company', 'finance'], staff=['company']
users   2: dana@client.example[staff], sam@client.example[staff,finance]
```

```
# Establish a session. Development only: without --dev it refuses and says what replaces it
$ oi --profile tools run --rm session mint --dev --subject dana@client.example --role viewer
token      <the session id, which is the whole token>
subject    dana@client.example
role       viewer
roots      ['company']
groups     ['staff']
expires_at 2026-10-14T05:00:00+00:00
```

```
# Ask what a token holds, which is what a request does
$ oi --profile tools run --rm session verify --token <token>
subject    dana@client.example
role       viewer
groups     ['staff']
roots      ['company']
permit     {"groups": ["staff"], "role": "viewer", "roots": ["company"], "subject": "dana@client.example"}
```

```
# Rehearse the refusal. REFUSED is the pass; this command fails if the boundary is not enforced
$ oi --profile tools run --rm session forge --dev --token <token> --field role --role executive
REFUSED   refused: the request supplied 'role'. A role comes from the session and never from a
          request field. The session holds 'viewer' and the refusal has been recorded.
```

#### Reading the refusal out of the record

The refusal is in the chain, and the column a reviewer searches by holds the role the request **claimed**,
not the one the session held:

Two commands answer two different questions. `audit list` answers "what refusals happened", newest first:

```
$ oi --profile tools run --rm audit list --type access --limit 3
    id  when                          type    actor                            outcome
    64  2026-10-14T01:42:27.267199Z   access  probe-staff@officeinference.com  denied
    63  2026-10-14T01:41:53.244849Z   access  probe-staff@officeinference.com  denied
```

And `session refusals` answers "what did THIS session try", which is the question an investigation actually
starts from:

```
$ oi --profile tools run --rm session refusals --session <token>
2026-10-14 01:58:07.443005+00:00  access/denied  claimed_role='executive'  session_role='viewer'  field='role'
```

A session with nothing against it exits non-zero and says so, because a clean session and a broken query look
the same in an empty listing.

| Column | What it holds for a refusal |
|---|---|
| `actor_role` | the role the request **claimed**, because that is the value you search by |
| `outcome` | `denied` |
| `payload.supplied_field` | the field the request used, such as `role` |
| `payload.session_role` | the role the session actually held |
| `payload.session_id` | which session, so repeated attempts from one session are visible |
| `payload.roots_held` | what the session could read, so a refusal is not confused with a denial |

A legitimate denial and a refused authority claim are the same `event_type` and different in structure: a
denial sets `actor_role` to a role the session holds, and a refusal sets it to one it does not. That is what
makes the two findable apart without reading payload prose.

#### The rehearsal, end to end

```
$ python3 scripts/render_manifest.py --manifest manifests/dev-cloud.yaml
$ oi --profile tools build session
$ oi --profile tools run --rm session migrate
$ docker run --rm --network ai-net --user "$(id -u):$(id -g)" \
    -v /opt/oi-core:/w -w /w --env-file rendered/env \
    -e AUDIT_TOOL=/w/services/audit/audit.py \
    --entrypoint python oi-core/eval:local /w/tests/fixtures/access_probe.py
```

The probe prints one line per right with the wrong path it rules out beside it, and exits non-zero if any
right failed. It needs `AUDIT_TOOL` because the refusal is recorded by the audit tool, which owns the chain,
and that tool is in the mounted checkout rather than in this image. The five rights are `ACCESS` (a session cannot retrieve a passage outside its roots), `SCOPED`
(the boundary holds across both retrieval arms, not just the dense one), `FORGED` (a session cannot be minted
for a role the deployment did not grant), `REFUSED` (a request supplying a role is refused), and `RECORDED`
(the refusal is findable in the chain).

**`FORGED` comes before `REFUSED` on purpose.** If a caller can mint its own authority then the refusal path
proves nothing, because there is nothing legitimate to refuse against.

#### Extending it

- **A new boundary.** Add the name to `access.roots` and to whichever groups should read it, then re-render.
  Documents are labelled from their top-level folder, so a document under `documents/finance/` carries the
  `finance` label.
- **A new person.** Add a `subject` with its groups and re-render. Access changes without code moving, which is
  the point of the group level.
- **A role of your own.** Declare `access.roles` with the roots it carries. A subject in that role gets those
  roots plus its groups.
- **A per-document grant.** Not built. The extension that keeps the filter inside the query is to write group
  membership onto the chunk at ingest, so the payload carries real labels rather than a single folder name.

**If it breaks.**

- `unknown role`: the registry was rendered from a manifest whose roles do not include this one. Run
  `session show` and use a role it lists.
- `unknown subject`: the manifest declares users and this subject is not among them. A deployment that
  declares users stops accepting subjects it was not told about, which is the intended behaviour.
- `the registry could not be read`: the file is absent or malformed. Absent is fine and means the manifest
  declares no registry, so the built-in roles apply; malformed means a render was interrupted.
- `mint refuses ... Pass --dev`: working as intended. The mint command is a development stand-in, and the
  identity provider replaces it at C7.
- `the refusal could not be recorded`: the audit tool could not write. **Treat the deployment as having no
  working record** and fix that before anything else, because a refusal nobody can find is not a control.
- A session retrieves a passage outside its roots: check that the *lexical* arm ran. The filter is applied
  inside both arms, and a deployment with no `DATABASE_URL` silently runs the dense arm alone.

## 13e. Redaction and the egress gate

Two questions, asked in this order. May this request leave the building at all, and what must not go inside a
request that may. The gate answers the first from the manifest. The redaction answers the second. The order
is the design: redacting a regulated document and sending the remainder answers a different question than the
one the client decided.

**What may leave is a decision in the manifest, not a view held in code.** `access.egress.frontier_classes`
lists the data classes a deployment permits on the frontier lane, and the renderer writes it to
`FRONTIER_CLASSES`. The dev cloud manifest permits `public` and `internal`, the two classes a small
business can send to a provider. The office manifest renders the value empty, and that is its policy rather
than an oversight: that build has no frontier lane, and the gate reads an empty set as nothing is permitted
rather than as no opinion.

```
python3 scripts/validate_manifests.py
python3 scripts/render_manifest.py --manifest manifests/dev-cloud.yaml
grep -E 'FRONTIER_CLASSES|EGRESS_REDACTION' rendered/env
```

```
FRONTIER_CLASSES=public,internal
EGRESS_REDACTION=on
```

The renderer writes both variables for every manifest, including the empty case, so a deployment reads a
decision rather than an absence. `EGRESS_REDACTION` also defaults to on in the code, so forgetting to set it
cannot become forgetting to redact.

**The gate fails closed, and each refusal names what is wrong.** A request with no data class is refused,
because an unclassified request cannot be shown to be permitted. A class outside the four is refused, because
nothing is known about whether it may leave. An empty permission set is refused, which is what an
unconfigured deployment looks like. And a class the deployment did not permit is refused with the classes
that are permitted named in the reason. The alternative is a system that sends a client's confidential
document because a variable was misspelled, and it does so silently because the call succeeds.

**The redaction walks every string in the request**, not a list of fields. The question is typed by a person
who knows what not to type. The retrieved passages are whatever the corpus contained, and they are the part
most likely to hold a client's identifiers. Walking the whole structure means a field added later is covered
without anyone remembering to cover it.

The patterns are one combined expression with ordered alternatives, most specific first, so the pass cannot
re-match what it just wrote. Credentials come first, because a key that reaches a provider's logs is not a
privacy incident but an incident, and rotation is the only remedy: AWS key ids, private key headers, bearer
tokens, `sk-` style keys, and password assignments. Then identity and payment: Social Security numbers, EINs,
IBANs, card-shaped digit runs, email addresses, phone numbers and IPv4 addresses. A match is replaced with
its label, so a reader of the redacted text can tell a redacted email from a redacted card. The label carries
no part of the value and not even its length, because either would narrow a guess.

**The card trade is made deliberately rather than hidden.** Shape decides whether to redact, and the check
digit decides only what the reader is told. A number that passes Luhn becomes `[REDACTED-CARD]` and one that
fails becomes `[REDACTED-LONG_NUMBER]`, counted separately as `unverified_card_shapes`. A card whose digits
OCR damaged fails the check and is still a card, so the check cannot be allowed to decide. Figures are the
other direction and are tested: `145.00`, `1,500.00`, `418750` and `0.67` are not identifiers, and a pattern
that caught them would make every question about money unanswerable.

**The honest bound.** This is pattern matching. It catches identifiers that have a shape, and it does not
catch a person's name, an address, or an account number written in words. It is a reduction in what leaves
rather than a guarantee about what cannot, and presenting it as a guarantee is how a client ends up sending
something they would not have. There is a test asserting that a name survives, so the bound cannot be quietly
assumed away.

**What the audit records.** The data class, whether egress was allowed, the kinds and counts of what was
removed, and the number of card-shaped numbers that failed the check digit. The four have columns of their
own, `redaction_count`, `redactions`, `unverified_card_shapes` and `egress_allowed`. A summary is not
content, so it is stored whatever the content mode says, and the default mode stores a hash rather than the
payload. The prompt hash is taken after redaction, so the record proves what was sent instead of becoming a
comparable copy of what was removed. A hash of the original could not be read, but it could be compared,
which is enough to confirm a guess about a Social Security number.

`chain_version` 2 covers those four columns in the row hash. Rows written before that change verify under
version 1 and are not rewritten, so the untouched history still verifies while an edit to a redaction count
is now caught. A row from a writer newer than the verifier is reported as unchecked rather than as a
mismatch, because a version this verifier does not know cannot be checked.

**Prove it on the deployment:**

```
oi run --rm -v /opt/oi-core:/w -w /w --entrypoint python audit \
  /w/tests/fixtures/egress_acceptance.py
```

The probe prints the deployment's permitted classes and whether redaction is on, the request as assembled,
what would reach the frontier model, and then a class the deployment does not permit. The recorded
acceptance run carried a Social Security number in the question and one plus a card number in a retrieved
passage. What would have been sent carried neither, and the fee figure 145.00 survived because a figure is
not an identifier. A regulated request was refused with no payload at all rather than a payload with a flag
beside it.

The event was then written through the deployment's own audit CLI and read back from Postgres. It showed
`data_class=internal`, `redaction_count=3`, redactions of two Social Security numbers and one card, and
`egress_allowed` true.

**What the record holds depends on the class, and for this one it is not a hash.** `manifests/dev-cloud.yaml`
sets `internal: redacted`, so the stored payload is the redacted request itself:

```
  content mode stored the payload as: {'lane': 'frontier', 'prompt': '{"messages":[{"content":"What does
  the file say about [REDACTED-SSN]?"…}],"passages":[{"doc_id":"company/client-file.pdf","page":4,
  "text":"Account holder SSN [REDACTED-SSN], card on file [REDACTED-CARD], rate 145.00 per hour."}]}',
  'data_class': 'internal', 'redactions': {'ssn': 2, 'card': 1}, 'prompt_sha256': 'd914d097…',
  'egress_allowed': True, 'redaction_count': 3, 'unverified_card_shapes': 0}
```

So the reader can see the question and the passage as they were sent, **with the identifiers replaced in
place**. The security claim survives — no Social Security number and no card number is in that row — and it
survives for a better reason than a hash would give, because the record shows what the model was actually
asked while the thing that must not be stored is not in it. The hash of the redacted prompt is there too, so
the record can still prove the prompt was not altered afterwards.

An earlier version of this page said the payload was stored as a hash and that the run printed
`content mode stored the payload as: None`. Both were wrong: `None` is not a value this probe prints, and the
class in force is `redacted` rather than `hash`. It is corrected here because the wrong version made a
narrower claim than the system can support, and a reader who checked it would have found the record holding
more than the page promised.

The gate and the redaction run where a frontier request is assembled. **The component that assembles one is the
assistant API, built in C3 and documented at 13g**, which calls this gate on every request. The probe and the test
suite exercise it directly when you want to look at the gate on its own rather than through a question.

**If it breaks.**

- Nothing leaves and the reason says no classes are permitted: `rendered/env` has not been re-rendered since
  the manifest gained the policy, so `FRONTIER_CLASSES` is absent. Absent refuses, which is the intended
  behaviour for an unconfigured deployment and a confusing one for a configured deployment with a stale file.
- A figure is being redacted: check the patterns for a shape that is too broad. The decimal and thousands
  separators are the ones that matter, and there is a test for each.
- A real identifier survives: it has no shape this recognises. Names and addresses are the known bound, and a
  client whose documents are full of them needs a different mechanism rather than a longer pattern list.
- The record holds the original text: the hash is taken from the cleaned payload and the payload from the
  redaction. Check the content mode as well, because `--store-payload` writes content the mode would
  otherwise not keep.

## 13f. Retention and deletion

Two operations that look alike and are not. A purge is a client asking us to stop holding a copy. It clears
every store that holds the document and deliberately leaves the file, because the client still has it.
Retention is a period running out, and it destroys the file as well, because keeping invoices for seven years
means that after seven years there is no invoice. A retention job built by reusing the purge and forgetting
the file would report success while the thing the client asked to be destroyed sat in their documents
directory.

### A client-requested purge

```
oi run --rm retention --purge company/invoice-2024.pdf --reason "client asked"
oi run --rm retention --purges
```

The stores are a list in `services/retention/retention.py`: the vector index, the lexical index, the
ingestion manifest, and any audit record whose stored payload quotes the document. A test reads the
ingestion worker's write paths and asserts a purge path exists for each, so a store added in a later step
fails that test rather than failing in a client's question six months later.

The result is per store rather than a total, so a store that contributed nothing is visible instead of
looking like a store that had nothing to do. A purge that cannot reach a store reports `complete: false` and
exits non-zero, because reporting success because the reachable stores succeeded is how a client is told
their data is gone while it is not. There is no rollback, and that is worth being plain about: a purge that
fails part way leaves the document partly gone, the record says which stores confirmed, and running it again
is safe because every delete is by filter rather than by a list of ids.

**One store cannot always be cleared, and the record says so.** Content stored in the audit record cannot be
deleted. The table is append-only, and the chain covers the payload's hash and length, so clearing a payload
without updating those leaves the row failing verification and updating them makes the chain a record of what
somebody chose to leave in it. So the purge counts those rows instead of clearing them, and a non-zero count
makes the purge incomplete. A document whose text is still readable in the audit record has not been purged,
whatever the other stores reported. The mitigation is not to store it there, which is why the default content
mode is `hash`. A deployment that selects `full` is choosing to keep content it cannot later delete, and that
is a decision a client has to make knowing the consequence.

### Legal holds

```
oi run --rm retention --hold company/tax.pdf --reason "litigation in progress"
oi run --rm retention --hold --reason "litigation in progress"
oi run --rm retention --holds
oi run --rm retention --purge company/tax.pdf --reason "client asked"
```

A hold refuses a purge and changes nothing, and **the refusal is recorded in `purge_records`** — read it with
`retention --purges`, which prints the `hold` store entry with the hold's id. It is *not* written to
`audit_events`: a purge record is auditable but not tamper-evident, which is the distinction section 23's
glossary draws. If you need a refused purge in the hash-chained record, it is not there today. `--hold` with a document places a
document-scope hold. `--hold` with no document places a deployment-scope hold, which covers every document
including ones uploaded after it was placed. A hold that only covered what existed at the time would have a
gap in it, and the gap is where the document that matters ends up. The hold is checked in one query before
any store is touched, because checking it per store means a hold obeyed by the store somebody remembered and
ignored by the one they did not.

`--holds` lists what is in force. A deployment-scope hold is the one people forget they placed, and it is the
usual answer to a purge that was refused with no obvious reason.

**Releasing a hold, and the mistake this page used to make.** Hold ids are assigned by the database and are
**not** a sequence you can predict from this manual. An earlier version of the example below said
`--release-hold 1`, which on the reference deployment releases nothing at all, because the ids on that deployment had already
been used and released, and the rows shown are 3 and 4. The command exits 1 and prints:

```
hold 1 is not in force, so there was nothing to release
```

So the sequence is: **list, read the id, then release it.** Never guess the number.

```
$ oi run --rm retention --holds
  3  document    company/tax.pdf                          'litigation in progress' by retention-job at 2026-10-13 19:45:36.569158+00:00
  4  deployment  (whole deployment)                       'litigation in progress' by retention-job at 2026-10-13 19:45:40.977896+00:00

$ oi run --rm retention --release-hold 4
hold 4 released by retention-job
```

**The second thing that trips people up: `--hold` with no document is easy to type by accident and it covers
everything.** If a purge is refused and `--holds` shows a deployment-scope hold, that is the reason — for
every document, not just the one you tried. Release it by the id `--holds` printed, and check afterwards that
the list is empty rather than assuming the release worked:

```
oi run --rm retention --release-hold <the id --holds printed>
oi run --rm retention --holds
```

`--release-hold` refuses an id that is not in force, which means **releasing twice is an error rather than a
no-op** — so a script that releases defensively will report a failure on the second run. Check `--holds`
first if that matters to you.

**A hold is not deleted when released.** The row stays in `legal_holds` with a `released_at`, because "a hold
was placed and lifted" is a fact an auditor may ask about, and a row that vanished could not answer.

### Time-based retention

```
oi run --rm retention --set-retention documents --days 365 --legal-basis "tax law"
oi run --rm retention --set-retention conversations --days 90 --legal-basis "client policy"

oi run --rm retention --enforce documents --dry-run
oi run --rm retention --enforce documents
oi run --rm retention --retention-runs
```

**What the dry run prints**, and it is JSON rather than prose because the command reports a record:

```
$ oi run --rm retention --enforce documents --dry-run
{
  "cutoff": "2025-10-13T20:24:42.192625+00:00",
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

`examined 0` here is correct rather than a fault: this deployment's whole corpus was ingested today, so a
365-day period has nothing old enough to look at. **`cutoff` is the line to read** — it is the date the period
reaches back to, and a cutoff that is not what the client agreed means the policy is not what you think.

**The four scenarios in one run are proved by the acceptance probe**, which ages a fixture rather than waiting
800 days for a real document to expire, and which is why its output is labelled here rather than shown as
though the commands above produce it:

```
docker run --rm --network ai-net --user "$(id -u):$(id -g)" \
  -v /opt/oi-core:/w -w /w --env-file rendered/env \
  --entrypoint python oi-core/retention:local \
  /w/tests/fixtures/retention_policy_acceptance.py
```

Its four scenarios, and what each proves:

| Scenario | What the probe prints | What it proves |
|---|---|---|
| A period with no policy | `without a policy: retain_days=0 examined=0 destroyed=0` | **No policy destroys nothing.** An invented default would destroy a client's documents on a number they never agreed to |
| A hold beats a period | `with a hold: examined=1 destroyed=0 held=1` | The hold is checked before any store is touched, and the document is counted as held rather than as destroyed |
| A dry run | `dry run: examined=1 expired=1 destroyed=0 held=0` | The rehearsal finds the expired document, reports it, and destroys nothing |
| The run | the `retention_runs` record as JSON, then `vector index:  0`, `lexical index: 0`, `manifest:      0`, `object store:  GONE` | The document is gone from every store **and the client's own file is gone too**, which is the half a job built by reusing the purge would miss |

It then asks a question about the destroyed document and prints `asking about it returned: [...]`, which must
not name it, and ends `PASSED` or `FAILED with N problem(s)`.

**That probe writes a thirteenth file into the documents root and destroys it**, so it is not a read-only
check: run it on a deployment whose corpus is disposable, and never on a client's. It is a fixture rather than
a procedure for exactly that reason, which is also why the table above describes its output instead of quoting
a full transcript — **it has not been run against this deployment**, because that would destroy a document the
corpus needs. The behaviour it covers is the same `--enforce` path the dry run above exercises; what this adds
is the file and the aged fixture.

Age is measured from `ingested_at`, not from the file's modification time. The file's date is a fact about
the client's filesystem, and copying a folder resets every modification time, so a document whose date was
reset would be retained for another period without anybody deciding to. `ingested_at` is when the obligation
to keep it started, which is the date a retention policy is about. A document with no `ingested_at` is not
expired, because an unknown age is not the same as an old one.

Three behaviours are proved beside the destruction, each a way the mechanism could be wrong in the direction
that cannot be undone. No policy destroys nothing, because a default period invented here would delete a
client's documents on a number they never agreed to. A hold beats a period, and a held document is counted
separately from a destroyed one, because a run that skipped forty documents under a hold and a run that
destroyed forty are different outcomes. And a dry run changes nothing while still checking the hold, because
a rehearsal whose output does not match the real thing rehearses a different operation.

The stores are cleared before the file is removed, and only one of those orders is recoverable. A failure
between them leaves a file with no index, which re-ingesting fixes. The other order leaves an index pointing
at a file that is gone, which is a passage the system will cite and nobody can open.

The conversations dataset exists so the requirement's second half has something to bind to. The assistant
API that writes it is the assistant API, built in C3 and documented at 13g, so its schema is defined and
minimal rather than invented in advance.

### The end of a contract

```
oi run --rm retention --destroy-all --reason "contract terminated 2026-11-30"
```

**It removes the client's documents from disk, not only the indexes.** A teardown that emptied every index and
left the corpus sitting in their folder would be the same defect as a retention period that only hid a file, so
the run walks the documents root and removes what it finds, reporting that as its own store in the record
beside the vector index, the lexical index, the manifest, the purge history and the holds. Each store is named
individually so the client can read which of them is empty and which is not.

That is also the only place in this system where deleting by inference is right. Everywhere else, "a file with
no manifest row" is not a usable test: a document somebody uploaded and no cycle has seen yet looks exactly
like an orphan, which is why nothing deletes on that basis. Here the manifest has just been emptied on purpose
and the client has asked for their data to be gone, so every file under the root is meant to go.

| Command | The client's file |
|---|---|
| `retention --purge DOC` | **Stays.** One document during the contract, and they still have their copy |
| `retention --enforce` | **Removed.** The period ran out, so the copy we hold goes |
| `retention --destroy-all` | **Removed.** The contract ended |

The destruction record is written with the hash of its own contents, and that hash travels with the file, so
the client can show later that it is the record we produced without either side trusting the other's copy. A
mismatch means the file was edited, and it is not something to reconcile.

The audit chain itself is deliberately not destroyed, and the record says so. It says which documents existed
and that they were destroyed, which is what lets the destruction be checked by someone who does not trust us.
Destroying it would leave the client unable to show anyone that we did what we said. The events are kept and
stored payloads are not: the run counts the audit rows that still hold content and reports the destruction as
incomplete while any exist, because the table is append-only and that content cannot be removed by this run or
by any later one.

This is a different command from the audit record's own retention in section 13. `audit purge` deletes old
events from the record and leaves a tombstone. `retention` destroys documents and conversations.

**If it breaks.**

- The document still answers after a purge: check
  `docker exec postgres psql -U oicore -d oicore -c 'select doc_id, count(*) from chunk_index group by doc_id'`.
  The lexical index is the usual answer, and the fix is a store in the list rather than a larger delete in one
  place.
- `complete: false`: read the store that carries the error, fix it, and run the purge again. Nothing is
  rolled back, so the record is the only account of what was removed.
- A purge is refused and no hold is visible: check `select * from legal_holds where released_at is null`. The
  deployment-scope hold is the one that catches everything and the one people forget they placed.
- Nothing is destroyed and `retain_days` is 0: no policy is set. That is the intended state rather than a
  fault, and the command says so.
- Everything was destroyed on the first run: the period is too short, or the cutoff landed on the wrong
  column. Age comes from `ingested_at`, and a null there is not expired, so a corpus whose manifest predates
  that column is safe rather than empty.
- The file is gone and the index still holds it: the two operations ran in the wrong order, or the purge
  reported incomplete and the unlink was reached anyway. Check `--purges` for a record that says
  `complete: false`.

---

# Part 5: when something breaks

## 13g. The assistant API

**What it is for.** Every capability over one API with no interface of its own. A client application talks to
this and nothing else: it asks a question, it gets an answer with its citations, and the permission check and
the record happen inside the call rather than beside it.

**Where it runs.** A container on the internal network, port 8088, **with no published port on purpose.** The
phase this belongs to is an API with no user interface, and a client reaches it through whatever a deployment
puts in front. Publishing one here would be the debug port this project has already paid for once.

### What a request does

1. **The session resolves.** The token is a random identifier that resolves to a row holding the subject, the
   role and the document roots. No token, no answer.
2. **A request that supplies its own role is refused**, and the refusal is written to the chain before the
   error returns. The response is a 403 whose wording says the permission was denied **or the resource might
   not exist**, which is Google's AIP-211 position: a 404 instead of a 403 is worse for the operator and no
   better for secrecy.
3. **Retrieval runs with the filter inside the query**, so a passage outside the session's roots never leaves
   the store.
4. **The model is chosen from the manifest**, not from the request. A caller cannot name a model any more
   than it can name a role. This matters because the gateway's own model allowlist turned out not to be
   enforced on this path.
5. **The model call is authenticated with a short-lived signed statement** minted for this request. The API
   holds a signing key; it never holds a model credential.
6. **The answer is checked against what was retrieved.** A sentence no passage supports is marked rather than
   dropped, and counted in the response.

### Establish a session and ask

```
$ alias oi='docker compose -f deploy/office/compose/docker-compose.yml --env-file rendered/env'
$ oi --profile tools run --rm session mint --dev --subject probe-staff@officeinference.com --role viewer
token      6D6LoGQsK-qL0E9anQN2kULjH9tPjaguzbx-M5G075k
subject    probe-staff@officeinference.com
role       viewer
roots      ['company']
groups     ['staff']
expires_at 2026-10-14T09:57:56.895572+00:00
```

```
$ docker run --rm --network ai-net --entrypoint python oi-core/session:local -c "
import json, urllib.request
body = {'session': 'THE_TOKEN', 'question': 'What is the mileage rate for reimbursement?'}
req = urllib.request.Request('http://api:8088/ask', data=json.dumps(body).encode(),
                             method='POST', headers={'Content-Type': 'application/json'})
print(json.dumps(json.load(urllib.request.urlopen(req, timeout=180)), indent=2)[:900])"
{
  "answer": "The mileage rate for reimbursement is 0.67 per mile for travel in an employee's own vehicle.",
  "model": "analyst",
  "role": "viewer",
  "roots": ["company"],
  "citations": [{"index": 1, "doc_id": "company/og-fee-schedule-2026.md", "acl": "company", "page_start": 0, "page_end": 0}],
  "supported": ["The mileage rate for reimbursement is 0.67 per mile."],
  "unsupported": [],
  "denied": false,
  "disclosure": ""
}
```

### The two endpoints

| Method | Path | What it does |
|---|---|---|
| GET | `/health` | Liveness, and what the process loaded: the registry, the deployment, the default model role |
| POST | `/ask` | `{"session": "<token>", "question": "..."}` in, a cited answer out |
| POST | `/ask/stream` | the same request, answered as Server-Sent Events. See the streaming pages above |

Three, and that is deliberate. There is no fourth endpoint until a capability needs one.

### What the answer carries, and why each field is there

| Field | Why |
|---|---|
| `answer` | The model's text, with `[n]` markers referring to `citations` |
| `model` | **Which model actually answered.** Read from the manifest, so changing the manifest changes this |
| `role`, `roots` | What the session held, so a caller can see the permission it was answered under |
| `citations` | Document, access label and page for each passage the answer could cite |
| `supported` / `unsupported` | The sentences the retrieved passages do and do not back, with a reason |
| `denied`, `disclosure` | Whether passages outside the role matched, and what the client's policy says to tell the user |

**The refusal that matters is the one in the chain, not the status code.** A 403 on its own is a control
nobody can audit. Read it back the way the manual's access-control section describes:

```
$ oi --profile tools run --rm audit list --type access --limit 3
$ oi --profile tools run --rm session refusals --session <token>
```

### The credential, in one paragraph

The API signs a statement naming a role and a session, valid for five minutes and usable once, and the gateway
verifies it with the same key. The gateway accepts exactly two credentials now: a signed statement, and the
master key, which the hook recognises so that administration is not locked out. **Every virtual key the `keys`
tool mints is refused for request traffic**, which is recorded rather than tidied away.

### The rehearsal

```
$ oi --profile tools run --rm keys --provision-teams
$ oi build api && oi up -d api
$ docker run --rm --network ai-net --user "$(id -u):$(id -g)" \
    -v /opt/oi-core:/w -w /w --env-file rendered/env \
    --entrypoint python oi-core/session:local /w/tests/fixtures/api_probe.py
```

Seven rights, each printed with the wrong path it rules out: `ANSWER`, `CITED`, `SCOPED`, `FORGED`, `NO-SESS`,
`BUDGET` and `ADMIN`. The **session** image rather than the eval one, because the probe reads and writes the
gateway's own tables and that image carries the driver which reaches them.

**If it breaks.**

- `[401] kind=no_session`: the token is absent, expired or revoked. The three are one answer on purpose.
- `[403] kind=refused_authority_claim`: the request supplied its own authority. That is the control, not a bug.
- `[502] ... budget is exhausted`: the role's ceiling is spent. The message names the team, the current cost
  and the maximum. A ceiling can also be raised with `keys --set-budget`.
- `[502] ... could not be reached`: the gateway is down or the API cannot see it. Check `oi ps`.
- **Every request returns an answer with no citations**: retrieval found nothing and the model answered from
  its own knowledge, which is the failure this product exists to avoid. Check that the corpus is ingested and
  that the session's roots match a document label.
- **A budget set by hand does not take effect.** LiteLLM caches the team row, so the number in the database is
  not the number the gateway is enforcing until it restarts. Found by setting a ceiling and watching a request
  answer anyway.

### Extending it

- **Another capability** becomes another endpoint, and the phase's later steps each add one thing to this one
  rather than a service of their own.
- **A different model per role** is a manifest change: edit `models`, re-render, restart the API. The
  `OI_MODEL_<ROLE>` variables are what the API reads, and the probe's `CITED` line prints which model answered.
- **A different identity provider** replaces the verifier behind the same seam. The session record, the role
  resolution, the retriever call and the refusal path stay.
### Streaming, and the redaction that comes with it

**What it is for.** The same question answered in frames, so the first words appear while the rest is still
being written. `POST /ask` returns one JSON document; `POST /ask/stream` returns Server-Sent Events.

**The two are not the same capability and the second is not a wrapper.** Streaming opens a window that the
non-streamed path does not have: a value can reach the browser in a token before anything gets a chance to
look at the finished answer. So the streamed path redacts **before each frame is emitted**, and that is why it
is its own endpoint rather than a flag on the first one.

### What a client receives

    event: start    data: {"citations": [...], "model": "analyst", "role": "viewer", "roots": ["company"]}
    event: delta    data: {"text": "The mileage rate is "}
    event: delta    data: {"text": "0.67 per mile"}
    event: done     data: {"deltas": 36, "ttft_ms": 544, "model_first_ms": 512, "retrieval_ms": 32,
                          "redactions": {}, "total_ms": 1011}

| Event | When | What it carries |
|---|---|---|
| `start` | before the model is called | the model, the role, the roots, and the citations retrieval found |
| `delta` | as tokens arrive | a piece of the answer, **already redacted** |
| `error` | if something fails mid-stream | `kind` and a short detail. A terminal event, because the status code is spent |
| `degraded` | when the model cannot answer at all, before any delta | the retrieved passages, a typed
 reason, and the attempts made. **A client that branches only on the events above will render this as
 nothing**, which reads to a user as an empty answer |
| `done` | at the end | `deltas`, `ttft_ms`, `model_first_ms`, `retrieval_ms`, `total_ms`, and the
 redaction counts. **`ttft_ms` is the caller's wait, retrieval included**; `model_first_ms` is the
 model's own share, and the two were once conflated |

**A comment line every 15 seconds** keeps the connection alive while the model is processing the prompt. This
is not decoration: an idle connection is what intermediaries kill, and prompt processing is idle. Without it a
slow first token looks like a connection failure rather than a slow answer.

### The redaction, which is the part worth understanding

**A holdback buffer.** Each chunk is appended to a buffer, the buffer is redacted, and only the part older than
64 characters is emitted. A pattern split across two chunks is two fragments that match nothing, so it is held
until it is complete. On the last chunk the remainder is flushed, redacted.

    # what a client sees when the answer contains a reserved test number
    data: {"text": "The"}
    data: {"text": " p"}
    data: {"text": "rima"}
    data: {"text": "ry contact reference for the Acme engagement is [REDACTED-SSN] [1]."}
    data: {"deltas": 7, "ttft_ms": 301, "model_first_ms": 290, "retrieval_ms": 11, "redactions": {"ssn": 1}, "total_ms": 558}

**Sized from the pattern set, not chosen.** The longest pattern in `services/egress/redaction.py` is a
private-key header at 36 characters; an SSN is 11, a card with separators 19. 64 covers the set with headroom,
and a test fails if a longer pattern is added, because a pattern longer than the holdback is a pattern that can
straddle it.

**The holdback is unconditional, and that is a tested property rather than an implementation detail.** A design
that withholds only when it has found something leaks the prefix of a pattern that completes later, and emitted
bytes cannot be recalled. A spike against the gateway demonstrated exactly that failure mode in a different
implementation: the raw prefix went out, and the stream then failed with a 400.

**What it does not cover, said plainly.** This catches patterns with a shape: card numbers, social security
numbers, IBANs, keys, emails, phone numbers, IP addresses. A personal name or a street address has no bounded
form, so no holdback can prove it will not appear. For those the control is redacting the request before it
reaches a model, and streaming is best-effort for anything the model introduces by itself.

### Run it

    cd /opt/oi-core
    alias oi='docker compose -f deploy/office/compose/docker-compose.yml --env-file rendered/env'
    oi build api && oi up -d api

    # The canary has to be in the index for the redaction right to mean anything.
    cp fixtures/corpus/canary-pii.md documents/company/
    docker exec ingestion python3 /app/ingest.py --documents /documents --json

    docker run --rm --network ai-net --user "$(id -u):$(id -g)" \
      -v /opt/oi-core:/w -w /w --env-file rendered/env \
      --entrypoint python oi-core/session:local /w/tests/fixtures/stream_probe.py

Four rights: `END-TO-END`, `TTFT`, `INCREMENTAL` and `REDACTED`. The last one reads **the raw bytes the client
received** rather than a reassembled answer, because an answer assembled from a stream is exactly what an
audit-only control redacts.

**If it breaks.**

- **The whole answer arrives in one frame.** Something is buffering. Check the response headers for
  `X-Accel-Buffering: no` and `Incremental: ?1`, then check what sits in front of the API.
- **A long silence after the first token, then everything.** The heartbeat is not reaching the client, so
  something upstream is holding the connection.
- **A canary appears in a frame.** The holdback is too small for the pattern set, or a pattern was added that
  is longer than it. `tests/test_streaming_redaction.py` fails on that too, so check the tests first.
- **`redactions` is empty on a question whose answer should contain a pattern.** Either the canary is not
  ingested or the redactor did not run. The probe reports which.
- **The answer is truncated with an `error` event.** A mid-stream failure. The status code was already spent,
  so the error arrives as an event; look for it in the frames rather than in the HTTP status.
- **Time to first token is much worse than the non-streamed path.** The holdback is doing its job and the
  answer is short, or the heartbeat interval is shorter than the model's first-token time.

### Extending it

- **A new identifier to redact** is a new pattern in `services/egress/redaction.py`. If it is longer than the
  holdback, raise the holdback in the same change; the test fails until you do.
- **A different transport** would replace `sse-starlette`. Everything above the transport is the redactor and
  the frame shapes, and neither knows how the bytes travel.
- **A stricter guarantee for names and addresses** needs a different mechanism, not a longer holdback: redact
  the request before the model call, or stop streaming for that class of question.

## 13h. Routing, and what happens when the model will not answer

**What it is for.** Two questions with one answer each: *which model may see this*, and *which model should*.
They are decided in that order, and the order is the design.

### The gate runs first, and the triggers choose inside it

A request carries a data class, which the deployment declares in `access.data_class`. The gate compares that
against `access.egress.frontier_classes`, and if the class is not permitted then no trigger is even consulted.

**Why the order matters, and it is the sentence an auditor will ask for.** A trigger is a cost decision. The
gate is a data-boundary decision. With the gate first, the answer to *why did this content reach an external
model* is a sentence about a declared permission somebody approved. With the triggers first, the answer is a
story about a heuristic firing, which is not a control anybody can review.

**The boundary is one-way.** No trigger moves content toward a more exposed lane, ever. A user asking for the
stronger model cannot reach the frontier lane from a deployment whose data class forbids it. Escalation only
happens inside what the gate already permitted.

**A deployment that declares nothing sends nothing.** An absent `access.data_class` is treated as the most
restrictive class, so a forgotten field cannot put documents on a remote provider. That is deliberate: the
failure is a request that answers locally or not at all, rather than one that leaves the building.

### The two triggers, each a predicate over something already known

| Trigger | How it is decided | What it is not |
|---|---|---|
| `agentic_task` | the `task` field is on a configured list | not a judgement about the question |
| `user_requested_stronger` | the `stronger` field is true | not a permission to leave the permitted lane |

A third was built and removed. It escalated when retrieval returned more than one distinct document, on the
theory that several documents means an answer needs reconciling. It was measured against real retrieval and
failed: a plain single-fact question returned **six** documents while a genuine multi-document question returned
**four**, because this corpus is small and one fact is legitimately spread across several files. Every
combination of a document count from 1 to 6 with a score floor from 0.00 to 0.60 either fired on the single-fact
question or missed the synthesis one. `tests/fixtures/trigger_rules.py` prints that table.

**An unusable predicate that fires on everything is worse than no trigger**, because it escalates cost silently
and makes the audit row meaningless. Nothing here assesses difficulty, and that is a requirement rather than an
omission: a question the cheap tier answers badly must not escalate on its own judgement.

### What a request and an answer carry

    POST /ask
    {"session": "<token>", "question": "...", "task": "reconcile", "stronger": true}

Both extra fields are optional. The answer adds:

| Field | Why |
|---|---|
| `lane` | Where it was answered: `frontier`, `local`, or `none` when nothing may answer |
| `escalated` | Whether a trigger moved it to a stronger model |
| `trigger` | **Which** trigger, by name, or empty when none fired |
| `reason` | The decision in a sentence, for the record and for a person reading it |
| `notice` | What the user is told, empty when there is nothing to tell |

### The user is always told

| Situation | What the answer says |
|---|---|
| escalated, and the gate permitted it | which trigger moved it, and which model answered |
| asked for the stronger model, and the gate forbade it | that it was asked for, that this content may not reach that lane, and which model answered instead |
| asked for it, the gate forbade it, and there is no local model | that no permitted lane exists, as a refusal rather than a promise |
| not escalated | nothing, because there is nothing the user needs to know |

**Refusing silently and downgrading silently are both rejected.** A quiet downgrade is the thing the mechanical
design exists to prevent: the user believes they got the stronger model while the content never left the
building, and the only place the substitution exists is a log they cannot read.

### When the model will not answer

Three thresholds on one wall-clock deadline, anchored at the moment the client asked rather than at the moment
generation started:

| Threshold | Frontier | Local | Where the number comes from |
|---|---|---|---|
| time to first token | 1.5s | 20s | measured p50 260ms, p95 359ms on the frontier lane, so four times the observed worst |
| stall between chunks | 5s | 30s | nothing measured; a gap this long is a failure rather than a slow model |
| total | 30s | 180s | **provisional.** The longest measured answer was 2.9s and the tail is unmeasured |

**The local column is a placeholder and says so.** A llama.cpp server on CPU is compute-bound on prompt
processing, which is a different shape from a network round trip, and borrowing the frontier figure would mark
every healthy local answer as a failure. Derive the real ones from llama.cpp's own `prompt_tokens_seconds` and
`predicted_tokens_seconds` when that build is measured.

**A stream that never starts and a stream that starts then stalls are different failures**, with different
causes and different words in the record. That is why there are three thresholds and not one total cap.

**A failed model call still answers.** When generation fails, the response is the retrieved passages, cited,
with a typed reason, at HTTP 200 rather than as an error. Retrieval already did the expensive part and the
client is entitled to their own material. A question whose answer needs the model then degrades to a
document list rather than to nothing:

    {
      "kind": "degraded",
      "reason": "throttled",
      "lane": "frontier",
      "passages": [{"index": 1, "doc_id": "company/travel-policy.md", "excerpt": "..."}],
      "attempts": 3,
      "threshold": "total",
      "partial_output": false
    }

**`kind` is the field a client must branch on.** An answer and a degradation look alike otherwise, and a client
that renders one as the other shows passages as though the model had written them.

**Retries happen only before the first token.** A streamed generation is not idempotent: a retry after output
has begun is billed twice, and the client already holds text from an abandoned response. After the first byte
the only honest move is a terminal error event.

**Classification is on the provider's error type, never the status code.** A 429 covers both a quota throttle
worth waiting for and a spend cap that retrying cannot fix. An unrecognised error is treated as terminal, which
is the safe direction: a retry spent on an unknown failure is a retry spent on a request that probably cannot
succeed, and the budget it consumes is the budget the degraded answer needed.

**The gateway does not retry on its own.** `num_retries: 0` is set in the rendered config deliberately. LiteLLM
defaults it to 2 when unset, which would have made three budgeted attempts into nine upstream calls, none of
them visible to the deadline and none counted in the record.

### Run it

    cd /opt/oi-core
    alias oi='docker compose -f deploy/office/compose/docker-compose.yml --env-file rendered/env'
    oi build api && oi up -d api
    docker run --rm --network ai-net --user "$(id -u):$(id -g)" \
      -v /opt/oi-core:/w -w /w --env-file rendered/env \
      --entrypoint python oi-core/session:local /w/tests/fixtures/interface_probe.py

Seven rights, and each one is a sentence this page told you to expect. If the page and the service disagree,
that probe fails, which is the point of it.

    ok   ANSWER     [200] 6 citation(s), lane='frontier'
    ok   NO-JUDGE   escalated=False trigger='' notice=False
    ok   TRIGGERS   agentic->'agentic_task' stronger->'user_requested_stronger'
    ok   FACTS      6 row(s), asked true and false both present, every row names a lane
    ok   TOLD       notice=True kind=None
    ok   STREAM     9 frame(s), first delta at 620ms, total 503ms
    ok   DEGRADED   the last recorded degradation carries reason, attempts, threshold, partial_output, lane, ...
    PASSED: 7 right(s) held against the documented interface

### Provoking a failure on purpose, so the degraded path can be seen

**A rule that is never exercised is a rule you are trusting.** The degraded path only appears when the provider
refuses, and waiting for a provider to misbehave is not a test. Two ways to produce one, both real:

**The cheap route is the request ceiling**, not the token ceiling. From Service Quotas, read 2026-10-14:

| Model, on-demand | Requests per minute |
|---|---|
| Nova Lite | 200 |
| Nova Micro | 200 |
| Nova Pro | 25 |
| Nova Pro, latency-optimized | **1** |

A burst of several hundred small concurrent calls reaches a request ceiling in seconds, and because each call
generates one token it costs a fraction of a cent. The token arithmetic — four million tokens per minute against
a ten-thousand maximum per request, so about four hundred maximum-size requests — is true and useless here.

    cd /opt/oi-core
    run() { docker run --rm --network ai-net --user "$(id -u):$(id -g)" \
      -v /opt/oi-core:/w -w /w --env-file rendered/env "$@"; }
    run --entrypoint python oi-core/session:local /w/tests/fixtures/throttle_probe.py

It **asks the API while the burst is running**, because a throttle that has already passed proves nothing, and
it prints both the burst outcomes and the answers. Exit 0 means a real throttle was correctly classified:

    HTTP 200 kind='degraded' reason='throttled' attempts=3 threshold='first_token'
    burst outcomes: {200: 474, 429: 1991}

**Exit 1 is the one to understand.** It means the answer degraded but the reason was `unclassified` with
`attempts: 2`, and that is a defect rather than a throttle: attempt one earns the real 429, the retry presents a
signed statement the gateway has already spent, and the 401 that comes back is what gets classified. It was a
real bug and this probe is how it was found.

**The second way is to lower a team's `tpm_limit`** and restart the gateway, which produces a refusal through
our own layer rather than the provider's. It exercises the same classification path and the same degraded
payload, and it is **not** evidence of a provider throttle. Useful for a quick check; say which one you ran.
### How it breaks

- **Everything escalates.** The task list is matching too broadly. Check the audit rows counted per trigger,
  which is what the review cadence is for.
- **Nothing escalates.** The gate is refusing a class the manifest permits, or `access.data_class` is missing
  and the deployment is falling back to the most restrictive class on purpose.
- **The answer says nothing about an escalation.** A client is rendering the answer and ignoring `notice`.
- **The model did not change after a manifest edit.** The renderer wrote the old value, or the API was not
  restarted. The model is read at startup.
- **A request waits far longer than the table says.** Check the lane: a local answer legitimately has a longer
  budget, and a request answered locally was never going to finish in 1.5 seconds.
- **The count in the record does not match the number of upstream calls.** It should. If it does not, something
  below the API is retrying; `num_retries: 0` in `rendered/litellm.yaml` is the setting that stops it.

### Extending it

- **A new trigger** is a new predicate in `services/api/routing.py`, and it must be over something already
  known. Nothing that asks a model whether a question is hard, for the reason in the step's own register entry.
- **A new deployment shape** is a new entry in `tests/test_routing_contract.py`. The decision table is diffed on
  every commit, so a routing change that nobody looked at fails the build and prints what moved.
- **Different thresholds** are environment variables, and the two lanes have separate ones. Deriving the local
  numbers from the llama.cpp server's own counters is the work that is still owed.
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
| `python3 scripts/render_manifest.py --manifest <file>` | Writes `rendered/env`, `rendered/litellm.yaml`, `rendered/services.txt`, `rendered/manifest.json`, `rendered/access.json` |
| `python3 scripts/check_requirements.py` | Reports the register: verified, open, waived, by phase |
| `python3 scripts/check_model_lifecycle.py [manifest…]` | Fails if a manifest names a deprecated, past-EOL, or unknown model |
| `python3 -m venv ~/venv && ~/venv/bin/pip install -r requirements-dev.txt` | Once, on a machine that will run the tests. The box has no pytest otherwise, and Ubuntu 24.04 refuses a bare `pip install` under PEP 668 |
| `~/venv/bin/python -m pytest tests/ -q` | The test suite. Runs from a checkout, not from a container |
| `oi ps` | Container status |
| `oi logs <service>` | Container logs |
| `oi up -d` | Start everything, or reconcile a service whose *definition* changed |
| `oi restart <service>` | Restart one service so it re-reads a mounted file, such as a new model list |
| `oi --profile tools run --rm session show` | The permission model this deployment is enforcing |
| `oi --profile tools run --rm session mint --dev --subject S --role R` | Establish a session. Refuses without `--dev` |
| `oi --profile tools run --rm session verify --token T` | Resolve a token to the access it holds |
| `oi --profile tools run --rm session forge --dev --token T --role R` | Rehearse a request supplying its own role. Refused is the pass |
| `oi --profile tools run --rm session refusals --session ID` | The refusals recorded against one session, read out of the chain |
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
| `oi run --rm eval` | Run the fixed question set against retrieval. Section 13b |
| `oi run --rm retention --holds` | List the legal holds in force. Section 13f |
| `oi run --rm retention --purge DOC_ID [--dry-run]` | Delete one document from every store. Refused if a hold covers it |
| `oi run --rm retention --enforce DATASET [--dry-run]` | Destroy what a retention period has run out on. Needs a policy to exist |
| `docker compose … run --rm --entrypoint python -v /opt/oi-core:/w:ro -w /w eval /w/tests/fixtures/ask.py -q "…" --role staff` | **Ask one question against the corpus.** The whole command is in section 13b, and both the `compose run` form and the `-v` mount are required rather than cosmetic |
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
| `aws iam get-policy-version --profile oi-credits --policy-arn arn:aws:iam::878451098165:policy/oi-core-dev-bedrock-invoke --version-id v1` | Which models this machine may invoke |
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
| **`/opt/oi-core/documents/`** | **A client's documents. This is the folder you drop files into.** The ingestion worker reads it, retention deletes from it, and nothing else writes to it |
| `/opt/oi-core/documents/<root>/` | One top-level folder per access root: `company` and `executive` in this deployment. **The folder name is the access label** — a file under `documents/company/` is labelled `company`, and that label is what section 13d filters on |
| `/opt/oi-core/documents/<root>/<any/depth>/` | Subfolders are read recursively, and every file under a root carries that root's label. `documents/company/handbook/policy.pdf` is labelled `company` |
| `/opt/oi-core/data/postgres/` | The database's files |
| `/opt/oi-core/data/qdrant/` | The vector store's files |
| `/opt/oi-core/data/hf-cache/` | The embedding model's weights, downloaded once at build |
| `/opt/oi-core/data/docling-cache/` | The parser's own model cache |
| `/opt/oi-core/db/migrations/` | Schema migrations, applied by `audit migrate` |
| `/opt/oi-core/data/model-lifecycle.json` | Model status and replacements, read by the lifecycle check |
| `/etc/systemd/system/oi-core-*.{service,timer}` | The shipping schedule |

**The documents root is the one path a reader is most likely to get wrong**, so here is the whole chain
in one place. The host path is `/opt/oi-core/documents`. Compose mounts it into two containers: read-only
at `/documents` for `ingestion`, and read-write at `/documents` for `retention`, which has to be able to
unlink a file at the end of a retention period. Both mounts come from `${OI_ROOT}/documents`, so moving
the repository moves the documents with it, and there is no second copy anywhere.

### What the renderer writes into `rendered/env`

The env file is generated, mode 600, and holds both secrets and settings. The settings a reader is
most likely to want:

| Variable | What it holds |
|---|---|
| `PROFILE` | `cloud`, `office`, or `hybrid`, as rendered |
| `COMPOSE_PROFILES` | `office` on a non-cloud build, which is what starts the local model server. The renderer removes it on a cloud build, so a cloud deployment's env file does not contain it at all |
| `MODEL_ROLES` | The roles this deployment serves |
| `CONTENT_DEFAULT`, `CONTENT_MODE_<CLASS>` | The content policy, one entry per data class. The audit tool reads these |
| `AUDIT_SHIP_BUCKET`, `AUDIT_SHIP_ARTIFACTS`, `AUDIT_SHIP_REGION` | Where the shipping job sends evidence, and which artifacts may leave. Written only when the manifest declares an `offsite` block; a build without one has no evidence path off the machine |
| `MODEL_FILE`, `MODEL_CTX`, `MODEL_THREADS` | The local lane's settings: which model file the office build serves, its context window, and how many threads it may use. Written only for a build with a local lane, so a cloud deployment's env file has none of them |
| `AWS_REGION`, `AWS_DEFAULT_REGION` | The Region the containers use for the provider |
| `OI_ROOT` | Where the deployment lives, so the tools find the repository without being told |
| `HOST_UID`, `HOST_GID` | The owner the tools run as, so files they write belong to the operator |
| `GATEWAY_PORT` | The gateway's port inside the network |
| `DEPLOYMENT_ID` | Stamped into every audit row |
| `POSTGRES_USER`, `POSTGRES_PASSWORD`, `POSTGRES_DB`, `LITELLM_MASTER_KEY` | Secrets, generated once and reused on later renders. `DATABASE_URL` is assembled from these by the compose file rather than written here |

Four environment variables are set by the compose file rather than by the renderer, which matters when
you point a tool at another deployment: `LITELLM_URL` (default `http://litellm:4000`) and
`RENDERED_DIR` for the keys tool, and `MIGRATIONS_DIR` and `EXPORTS_DIR` for the audit tool.

### Settings that are read from the environment but written by nothing

There is a third category, and until now the manual named these variables in prose without saying where
they come from or what happens if you do not set them. **A service reads each one from its own environment with
a default compiled into the code, and no script writes it** — so they are set by editing the compose file, and
the defaults are what this deployment runs because nothing overrides them. **The one exception is named in the
table below:** `ACCESS_DENIAL_MODE` is rendered into `rendered/env` from the manifest's `access.denial`, which
is exactly why it is listed here as well, since a reader comparing the two needs to know the rendered value
wins.

| Variable | Default | What it decides | Read by |
|---|---|---|---|
| `RERANK_ENABLED` | `true` | Whether the reranker runs at all. Only the exact string `false` disables it | retrieval, so `eval` and the probes |
| `RERANK_DEPTH` | `50` | The **ceiling** on how many fused candidates the reranker scores. The real number is whatever the arms returned, up to this | retrieval |
| `RERANK_BUDGET_MS` | `5000` | The budget a rerank is measured against, and the HTTP timeout is derived from it (`budget/1000 + 5` seconds) | retrieval |
| `RERANK_MODEL` | `cohere.rerank-v3-5:0` | Which Bedrock reranker model is called | retrieval |
| `ACCESS_DENIAL_MODE` | `denied` | What a denied user is told: `silent`, `denied`, `counted`. **This deployment sets it to `counted`** through the renderer, because the manifest declares `access.denial` | retrieval |
| `INGEST_PARSE_TIMEOUT` | `3600` | One document's parse budget in seconds. Deliberately generous: a 59-page scan through OCR is minutes of CPU | ingestion |
| `EMBED_CONTRACT_ALLOW_CHANGE` | unset | The escape hatch for a deliberate embedding-model change. Truthy values are `1`, `true`, `yes` | ingestion |
| `EMBED_PASSAGE_PREFIX` | empty | Text prepended before embedding. **It is applied to the query as well as to passages**, because both go through the same `embed_texts` call | ingestion, retrieval |
| `EMBED_QUERY_PREFIX` | empty | **Recorded and not applied.** It is read into the service record and copied into the contract fingerprint, and nothing prepends it to anything. Setting it changes the recorded contract — so it can demand the escape hatch above and force a re-embed — while changing no vector |
| `EMBED_NORMALISE` | `true` | **Recorded and not applied, the same way.** Nothing normalises a vector; the value only enters the contract fingerprint. Only the exact string `false` turns it off | ingestion |
| `EMBED_CONTRACT_REASON` | unset | The reason recorded alongside an allowed contract change. **Set this or the change is recorded with no stated reason** | ingestion |
| `DOCUMENTS_ROOT` | `/documents` | Where the client's files are, inside the container. Both the worker's `--documents` and retention's `--documents-root` default to it, and the compose file sets it on the retention service only | ingestion, retention |

**Two of those are worth acting on rather than reading.** `EMBED_CONTRACT_ALLOW_CHANGE` is the documented
way out of the contract refusal: an operator who has decided to move the embedding model sets it with
`EMBED_CONTRACT_REASON` and re-ingests, rather than editing the check. And `RERANK_DEPTH` being unset is
why the probe in section 13b prints `rerank_depth` 15 rather than 50 — the ceiling was never reached.

**Changing any of these means editing `deploy/office/compose/docker-compose.yml` and restarting the
container**, because none of them is read from a file at run time. `oi up -d <service>` reconciles a
changed definition; `oi restart <service>` re-reads a mounted file such as a new model list. Those are
different operations and this is the first kind.

## 24. Glossary

**Anchor.** A small file recording the head of the chain at a moment, shipped off the machine. The
only artifact that can detect rows deleted from the end.

**Backfill.** Copying the gateway's own records into the audit chain.

**Chain.** The audit record's structure: each row carries a hash of the row before it, so edits are
detectable. Precisely: a row's hash covers seventeen of its columns on a row written since C2 and thirteen on
the rows before it, the last of them the previous row's hash; ordering is by id, the first row chains to
nothing, and a purge continues the chain rather than restarting it, because the tombstone is itself a row that
chains from the head before the deletion.

**The seventeen** are the original thirteen plus four that C2 added. The thirteen are the timestamp, event
type, actor, actor role, deployment, data class, outcome, tool, model, request id, payload hash, payload size
and the previous hash. `chain_version` 2 adds `redaction_count`,
the redaction summary, `unverified_card_shapes` and `egress_allowed`, which is the change section 13e
describes: those four are the ones a reader would most want to edit quietly, and editing them now fails a
verify rather than passing one. Rows written before the change keep version 1 and still verify, so the
untouched history is not rewritten to match.

**Four columns are still outside the hash**: `approval`, `prompt_sha256`, `completion_sha256` and
`occurred_at`. Editing one of those four in the database would not break the chain or fail a verify, so they
are recorded evidence rather than chained evidence. That matters most for `occurred_at`, because the retention
purge cuts on it. It is a known gap rather than a design choice, tracked as `AUD-HASH-001`, and closing it
means another versioned hash so that rows written before the change still verify.

**Content mode.** What the record keeps for a data class: `hash`, `redacted`, or `full`. The prompt and
completion hashes are recorded whatever the mode.

**Chain head.** The hash of the newest row, which is what an anchor records. The next row chains from
it, so it moves every time the record grows.

**Component.** Anything we build that asks the gateway for an answer: the assistant API, a workflow, a
connector, a document pipeline. Components are the callers in every description of a request. **The first one
exists: the assistant API, built in C3 and documented at 13g.** The rest arrive in C4 through C6, and until they
do the other callers are the `keys` probe and a curl.

**Spend log.** The gateway's own table of calls, inside its database, written as each call happens and
including the ones it refused. It is not the audit record: it is the source the audit tool copies from,
and it keeps its own retention, which is why a purge of our record does not touch it.

**Data class.** How sensitive a piece of material is: `public`, `internal`, `confidential`, `regulated`.
It decides the content mode, and under a compliance profile it is meant to decide which models may see
the material, which C2 enforces. Today a caller states it when recording an event rather than the system
inferring it.

**Deployment.** One client's installed copy of the core system, described by one manifest, with one
database, one bucket when evidence ships off the machine, and one `deployment_id` that appears in every
audit row and key alias.

**Export.** One day's events as a file, plus a small descriptor called the export manifest, which is a
different artifact from the deployment manifest despite sharing the word. The export manifest holds the
count, the first and last ids, the hash the first row chains back to, and a hash of the file itself.

**Lane.** Where inference happens. `local` means a model on the machine, `frontier` means a remote
provider. A build may have one or both.

**Manifest.** The YAML file describing a deployment. The thing a client buys.

**Pack.** A sellable bundle of services.

**Profile.** `cloud`, `office`, or `hybrid`. The difference between the builds.

**Role.** A name a caller asks for, mapped to a model, with its own key and ceiling. Four today:
`front_desk`, `analyst`, `reader`, `coder`. **Not the same thing as a retrieval role**, which is `staff`
or `executive` and decides which document folders a search may read. Section 13d has both, side by side.

**Tombstone.** The event a retention purge writes, carrying the chain head before and after, so a gap
is explained.

**Watermark.** The newest purge's cutoff. Backfill will not re-create events older than it.

## 25. Not built yet

| Phase | What it adds | State |
|---|---|---|
| C1 | The record: gateway, keys and budgets, the hash-chained audit record, export and anchoring | **Built.** Sections 11 to 14 |
| C2 | Reading documents: Docling ingestion with OCR and tables, Qdrant, embeddings and reranking, hybrid search, and the verifier that checks every citation | **Built.** Sections 13a to 13f |
| C3 | The assistant API: sessions, roles from tokens, streaming, retries, a degraded mode when the provider throttles, and routing that keeps a one-way data boundary | **Built.** Sections 13g and 13h |
| C4 | The client application: Ask, Do, Review, Activity | Not built |
| C5 | Workflows and the first connector, with an approval gate | Not built |
| C6 | The agent runtime and the MCP gateway | Not built |
| C7 | Ingress, TLS, identity, and the Terraform that creates a client's account | Not built |
| C8 | Alarms, backups, a restore drill, the sixty golden questions, vulnerability scanning, drift reporting | Not built |
| C9 | The demonstration | Not built |

**What exists today, said plainly.** The system stores a client's documents, finds the passages that
answer a question, checks every citation it makes, refuses to answer from a superseded version, filters
by role, redacts before anything leaves for a frontier model, and destroys material when a retention
period runs out. It records all of it in a tamper-evident chain and ships that record off the machine.

**What does not exist, and this is the honest gap.** There is no user interface. **The assistant API does
exist** — one endpoint in front of every capability, with sessions, streaming, a data-class gate and a
degraded answer when the model will not answer — and sections 13g and 13h document it. **Section 13b
documents the retrieval function underneath it**, which is the developer's path for looking at retrieval on
its own rather than through a question. What is missing is anything a person sits in front of: the client
application is C4 and the workflows are C5. So a client application can be written against this today, and a
person cannot yet use it without one.
