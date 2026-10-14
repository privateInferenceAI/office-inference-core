# Handover: building C3, the assistant API

Written to start a fresh session with no access to the conversation that produced it. Read this first, then
`AGENTS.md`, then `docs/build-brief.md` section 6 for the architecture C3 serves.

**C3 has not been started.** Nothing below is built. This document is the plan and the process, not a report.

---

## 0. What this phase is

From `docs/build-brief.md`:

> **C3** — Assistant API: sessions, roles from tokens, streaming, timeouts and retries, degraded mode when
> Bedrock throttles. *Every capability over one API with no UI; a forged role refused and recorded.*

**There is no assistant API service today.** `services/` holds audit, egress, eval, gateway, ingestion,
parser, retention and retrieval. C3 builds the layer above them: one API that reaches every capability, with
a UI deliberately absent.

---

## 1. The five requirements

All five are **open** in `docs/requirements.yaml`. Their verification criteria are quoted verbatim because
they are what "done" means, and closing an item without meeting its stated criteria is the failure C2 made.

| ID | Title | Verification criteria | Tiers |
|---|---|---|---|
| **SEC-ACL-002** | Per-user and per-group document permissions beyond two folder roles | *a user outside a document's group cannot retrieve it by any query* | cloud, hosted |
| **INF-ROLE-001** | Model roles are read from the manifest, not hardcoded | *changing the analyst role in the manifest changes the model the API calls* | all three |
| **INF-STREAM-001** | Responses stream | *the first token arrives before the last is generated* | cloud, hosted |
| **INF-TIMEOUT-001** | Timeouts, retries, and queueing are bounded, and throttling degrades rather than hanging | *with the provider throttling, requests return a clear degraded answer within the timeout, and it is recorded* | all three |
| **INF-ROUTE-001** | Routing escalates on the three mechanical triggers only | *a question the cheap tier answers poorly does not escalate on its own judgement* | all three |

---

## 2. The thing that unifies SEC-ACL-002 and INF-ROLE-001

**Read this before designing anything. It is the spine of the phase.**

`services/retrieval/retriever.py` takes the role as a **parameter**:

```python
def search(services, question, role: str, limit=10, denial=None, rerank_passages=True) -> Result:
    ...
    if role not in ROLES:
        raise KeyError(f"unknown role {role!r}. Known roles: {sorted(ROLES)}. ...")
```

and `ROLES` is a hardcoded two-entry map:

```python
ROLES = {"staff": frozenset({"company"}), "executive": frozenset({"company", "executive"})}
```

**That is safe today and unsafe the moment C3 exists.** The only caller now is an internal CLI probe. Once an
API sits in front of it, whoever calls the API can claim `executive` by asserting it, and read everything.

So SEC-ACL-002 is not merely "add groups". It is: **the role must come from an authenticated session, and a
claimed role must be refused and recorded.** The phase's own acceptance sentence says exactly that — *a
forged role refused and recorded* — and INF-ROLE-001 is the same boundary one layer up, where the *model*
role must come from the manifest rather than being assumed.

**The three pieces of the trust boundary, in order:**

1. A session is established, and it carries a role.
2. Retrieval takes the role **from the session**, never from a request field.
3. A request that tries to set or override its role is **refused and the refusal is written to the audit
   record** — not just a 403. The record is the evidence, and a refusal nobody can find later is not a
   control.

---

## 3. Routing: the three mechanical triggers

From `docs/build-brief.md` section 6, quoted because the wording is load-bearing:

> **Routing is mechanical.** Cheap tier first. Escalate on exactly three triggers: the task is on the
> agentic pack's list, the request needs synthesis across more than one document, or the user asks for
> the stronger model. **No model's own judgement may move content between lanes.**

**Every escalation writes an audit event naming its trigger.** An earlier draft of the plan let the cheap
tier escalate itself "for lack of evidence", and that was rejected because it hands the data boundary to a
model's discretion. INF-ROUTE-001's verification is aimed directly at that: *a question the cheap tier
answers poorly does not escalate on its own judgement.*

Routing order is **role first, then residency, then escalation**.

The cost reason, so the design is not mistaken for fussiness: Nova Micro is 0.035 and 0.14 per million
tokens; Haiku 5.5 is 1.00 and 5.00. Roughly thirty times the input price.

---

## 4. The build process

`AGENTS.md` defines what finished means, and the order is not a summary of what usually happens. **C2 did
step 1 and jumped to step 5**, marking 18 requirements verified with no operating page for anything it
built. The panel could not run because there was nothing to read.

### 4.1 Each step is three activities, not one

The failure to avoid is treating the manual as a report written afterwards. It is written **as part of the
build**, and then used.

| # | Activity | What it means |
|---|---|---|
| 1 | **Write the step** in `docs/runbook.md` | What the operator does, before it is built. This is the specification |
| 2 | **Build it** | The code |
| 3 | **Write the operating page** in `docs/manual.md` | The same procedure for a reader with no context: what it is for, every command with a real example and its real output, and how to extend it |
| 4 | **Follow your own page** | Open the page you just wrote and do what it says. Fix the code or the manual until they agree |

**Activity 4 is the one that gets skipped, and it is the one that finds things.** See section 5.1 for what
it means precisely.

Write the page before finishing the code and it acts as a specification: it forces the interface to be
decided before the implementation. Write it afterwards and you get documentation of what you built rather
than documentation of what is true.

### 4.2 The loop with Anthony

**The agent builds. Anthony approves, merges, and runs the tests.**

1. The agent works on a feature branch and **opens a PR**. Never commits to `main`.
2. **Anthony reviews, approves and merges.** He pulls it and **runs the tests in his own terminal.**
3. **The agent supplies the commands to run as each piece lands** — copy-pasteable, with what to look for in
   the output. He wants to be involved and learning, so the point is not to make the loop fast.
4. When something fails, he pastes the output back and the agent diagnoses it.

**Exception, only when he says so:** he may hand over the whole loop for a stated stretch of work — commit,
push, merge, deploy, test — or give the agent full access to the box. **That is opt-in per instruction and
never assumed.** It reverts when the instruction ends, and it is not a standing permission.

So every hand-back to Anthony states: **the branch, the exact commands, and what output to paste back.**

### 4.3 Build for the cloud stack. Never for the office box.

**This is a standing rule and it is easy to break without noticing.**

> **We are building the cloud stack.** Never downgrade a component, swap a service, or weaken a setting
> because it might have to run on a less powerful office machine. **The office build is a separate project,
> built later.** Where a choice would differ, the cloud stack decides and the office build adapts.

`AGENTS.md` has a neighbouring rule — *"Never let our own test instance decide. If the right component needs
more machine, the machine is what changes"* — but that one is about our development box. This one is about
the office product, and it is a different failure.

**The temptation is invisible when it happens.** Choosing the smaller model, skipping a GPU-dependent path,
avoiding a service that needs more RAM, or splitting a process to fit a smaller machine all look like
prudence in the moment. Each is scope creep on the office build's behalf, and each degrades the product
being sold.

Size for the cloud reference instance in `docs/build-brief.md` section 5. Use managed services where they are
right. Do not trade a capability for portability.

**If a component is wrong for the office build, that is a finding for the office build's phase.** Record it
and move on; do not change this one.

### 4.4 At the end of the phase, not before

1. **The three-agent panel** runs, from the manual only. See section 5.2.
2. **Fix everything they find, then have the fixes re-tested.** Cap three rounds. Stop when a round returns
   only cosmetics.
3. **Every reviewer states a verdict** — a yes or a no on "could a competent stranger run this from the
   documentation alone" — so stopping is a decision rather than a feeling.
4. **Close the register items in `docs/requirements.yaml` at the same time**, with the evidence. Never
   before. Do not close one until its operating page exists and the panel has passed it.
5. **Record every round in `docs/review-log.md`**, naming what was covered and what changed something.

Within a phase, review only what changed. The full panel runs once, at the end.

---

## 5. The testing process

Four layers, in this order, because each catches what the one below cannot.

### 5.1 Layer 3, which is the one C2 skipped

**The rule: run it through the interface the manual names, from the image the manual names.**

This means: after building, **follow your own manual page rather than testing from the code.** The
distinction is the direction you read from. An agent testing what it built naturally tests it the way it
built it — calling the function, from the checkout, holding the objects it just made. The rule says: open the
page, do what it says, and see whether that works.

Three parts:

| Part | Meaning |
|---|---|
| **The interface** | If the page says `oi run --rm retention --enforce`, run that — not the Python function it wraps. The wrapper is where the bugs were |
| **The image** | `oi run --rm <tool>` starts the **built image** with its own entrypoint and environment. The checkout has different files, sometimes a different working directory, and variables compose assembles that a bare `docker run` does not supply |
| **The words** | The command as written, with its flags and its path arguments. `--dry-run` on the line. `/app/exports/...` and not `/w/exports/...` |

**What C2 got wrong, as three concrete pairs:**

| The probe tested | The manual told the reader to do |
|---|---|
| `policy.enforce(dry_run=True)`, as a Python call | `retention --enforce documents --dry-run` |
| run from the mounted checkout | `oi run --rm retention ...`, from the image |
| passed a `Passage` carrying `parse_score` | whatever the retriever actually returns |

**Every probe passed and every documented command was broken.** The safety rehearsal destroyed documents for
real, a command in the manual failed with "can't open file", and a field the documentation promised was
never populated.

**The test for whether this layer was done:** a fresh session, given only `docs/manual.md`, pastes the
command and it works. If it works for you because you know the entrypoint, you tested your knowledge.

**This is also what the operator guide is for.** `docs/operator-guide.md` is the end-to-end walkthrough with
a checkpoint at every stage, and its stages were what found the retention defects, the anchor defect and the
RTF gap — all four invisible to the unit tests and to two review panels. **C3 adds a stage for the API**;
the guide currently goes from stage 14b to stage 15 with nothing between.

### 5.2 Layer 4, the panel

Three agents that did not write the work:

- **executor** — runs every command on the live box and reports where reality differs
- **cold reader** — reads the manual with no access to the code and reports what a new operator would get
  wrong
- **fidelity checker** — when code changed, reads the manual's claims against the source

**Two rules that change how this runs:**

> **They get the manual and nothing else.** Not the code, not the diff, not a list of what changed, not the
> runbook. `docs/manual.md` is their only entry point.
>
> **If a reviewer needs the source to work out how to run something, that is itself the finding.**

> **Every round re-tests from the manual, including rounds that only verify fixes.** A round is not "check
> whether the fix landed in the source". It is "run the documented command again and see what happens now".

**The second rule is a correction to how this went last time.** After the fixes, the re-check was scoped to
the corrections — which answers *"did you change the line I complained about?"* rather than *"does the
command work now?"* A fix can be present in the code and still not fix the experience of the person
following the manual. Round 4 of the last phase found three corrections that were wrong in exactly that way.

### 5.3 What each C3 requirement needs

| Requirement | The test that proves it |
|---|---|
| **SEC-ACL-002** | Forge a session claiming `executive`. It must be **refused and recorded** — and the refusal must be findable in the audit record, not only a 403 |
| **INF-ROLE-001** | Change `models.analyst` in the manifest, validate, render, restart, and ask the API which model it used. **The answer must change.** This tests which model was called, not whether the answer improved |
| **INF-STREAM-001** | Assert time-to-first-token is materially less than total time, on a response long enough for the difference to be measurable. A response that arrives whole must fail |
| **INF-TIMEOUT-001** | **Inject** throttling. The answer must be degraded, clear, and **recorded, within the timeout** — not a hang |
| **INF-ROUTE-001** | A question the cheap tier answers badly must **not** escalate. Then each of the three triggers must escalate, each with its own audit event naming the trigger |

**Throttling will not happen by waiting for it.** INF-TIMEOUT-001 needs a deliberate fault-injection
mechanism, and none exists. Build one, or the criterion cannot be met honestly.

**Rerun the existing probes too.** They are in `tests/fixtures/` — `hybrid_acceptance.py`,
`figures_acceptance.py`, `api_contract_probe.py`, `citations_acceptance.py`, `acl_probe.py`,
`retention_policy_acceptance.py`, `rerank_acceptance.py`, and `ask.py`. C3 inserts a layer that everything
else calls through, so their passing is part of C3's evidence, not just C2's.

### 5.4 Golden questions: deferred to C8, with the reason

**They do not exist and C3 is not the phase to build them.** `tests/parity/` is a README with no questions,
and `docs/build-brief.md` says so: *"A README only. No golden questions exist."* They are assigned to C8, at
sixty per deployment — forty general and twenty specific to the client's work.

**The rule applied:** if C1 and C2 ran them, C3 would too. They did not, so this waits for C8 as scheduled.

**What C3 does instead, so nothing is left uncovered.** INF-ROLE-001 changes a model by design, and the
manual says a model swap means rerunning the golden questions before a client sees the new answers. Without
a question set, C3 tests the mechanism and **not** answer quality:

- assert the **model that was called** changed, from the gateway's own view
- ask the same question before and after the swap, and **record both answers side by side** in
  `docs/runbook.md` so the difference is visible rather than asserted
- state in the manual that answering *"is the new model as good"* is C8's, and that a swap before then is
  unmeasured

**Do not close INF-ROLE-001 by claiming the swap is safe for answers.** It is safe for the mechanism, which
is what its criteria ask, and the quality claim is C8's.

---

## 6. Five known gaps to handle, not discover

1. **The operator guide has no API stage.** Stages run 14b then 15. C3's acceptance is "every capability
   over one API", so the guide needs a stage that calls everything through it, with checkpoints.
2. **Throttling must be provoked.** No fault-injection mechanism exists for INF-TIMEOUT-001.
3. **Streaming needs a timing assertion.** "First token before last" is a timing claim, not a correctness
   one, and a non-streaming implementation must fail the test.
4. **The five requirements have no notes.** `docs/requirements.yaml` gives each a title and criteria and
   nothing else. There is no prior thinking to inherit, so the design is yours.
5. **`docs/design/build-sequence.md` is stale and misleading.** It uses the old 0–11 phase numbering, not
   the current C0–C8 model, and it stops partway through Phase 1. Anyone reading it for C3 will be misled.
   Either update it or add a line at the top saying it is superseded by `docs/build-brief.md`.

---

## 7. Where things are

| What | Where |
|---|---|
| The requirement register | `docs/requirements.yaml` |
| The phase list, with C3's one-line scope | `docs/build-brief.md` section 6 |
| The process, and what finished means | `AGENTS.md` |
| The operating page, and the panel's entry point | `docs/manual.md` |
| The build log, step by step | `docs/runbook.md` |
| The end-to-end walkthrough with checkpoints | `docs/operator-guide.md` |
| Every round of every review | `docs/review-log.md` |
| The previous hand-over, worth reading for the failure it describes | `docs/handoff-c2-review.md` |
| Acceptance probes | `tests/fixtures/` |
| The deployed box | m7i.4xlarge `i-096bebd46308d1673`, us-east-1b, Elastic IP 52.204.202.66, repo at `/opt/oi-core` |

**Deploying a change, on the box:** `git pull`, then rebuild the service you changed, then restart it.
`oi up -d <service>` does **not** pick up a changed mounted file — a mounted file changing underneath a
running container is not a change Compose can see. Use `oi restart <service>` for that.

---

---

## 8. The commands Anthony runs

He runs these himself, in his terminal, on the box. Paste the output back when something is wrong.

**First, once per shell, and once per machine.**

```
cd /opt/oi-core
alias oi='docker compose -f deploy/office/compose/docker-compose.yml --env-file rendered/env'
```

**`oi` is a shell alias, not a program.** It works in an interactive shell and **does not exist** in
`ssh host 'command'`, in a script, or in `docker run`. Write the whole compose line out in those places. This
hand-off and `docs/manual.md` both use the shorthand, and the manual says the same thing at section 10.

**And the box needs a Python environment before any test can run.** It ships without `pip`, `pytest` or
`venv`, and Ubuntu 24.04 refuses a bare `pip install` under PEP 668. Three commands, once:

```
sudo apt-get install -y python3-venv python3-pip
python3 -m venv ~/venv
~/venv/bin/pip install -r requirements-dev.txt
```

Then use `~/venv/bin/python -m pytest` rather than a bare `python3`. **Test imports skip cleanly when a
dependency is absent**, so the suite passes without them and a missing package does not announce itself.

**After pulling a change, the short loop:**

```
cd /opt/oi-core
git pull
~/venv/bin/python -m pytest tests/ -q            # unit tests
python3 scripts/validate_manifests.py            # every manifest still valid
python3 scripts/check_model_lifecycle.py         # no model is deprecated or unknown
```

`pytest -q` ends with a count. **484 passing** is where this phase starts; the number only goes up, and a
count that goes **down** means a test was deleted rather than satisfied.

**Then the service that changed**, e.g. for the API service C3 adds:

```
oi build api && oi up -d api && oi ps
```

**Then the probes**, on the live stack. Each prints `PASSED` or `FAILED with N problem(s)` and nothing in
between is worth ignoring.

The `run()` helper below defines the compose line explicitly, which is why it works where the `oi` shorthand
would not:

```
run() { docker run --rm --network ai-net --user "$(id -u):$(id -g)" \
  -v /opt/oi-core:/w -w /w --env-file rendered/env "$@"; }

run --entrypoint python oi-core/ingestion:local /w/tests/fixtures/hybrid_acceptance.py
run --entrypoint python oi-core/ingestion:local /w/tests/fixtures/figures_acceptance.py
run --entrypoint python oi-core/ingestion:local /w/tests/fixtures/api_contract_probe.py
run --entrypoint python oi-core/ingestion:local /w/tests/fixtures/citations_acceptance.py
run -e ACCESS_DENIAL_MODE=counted --entrypoint python oi-core/ingestion:local /w/tests/fixtures/acl_probe.py
```

`docs/operator-guide.md` stage 13 has all of them including the two that need different arguments — the
retention probe needs the `eval` image and a writable documents mount, and the rerank probe needs the
reranker running. **Use the guide's invocations rather than improvising**; its copies are the ones that work.

**And what C3 adds**, once it exists: an API probe that forges a role (must be refused and recorded), a
streaming assertion (time-to-first-token, not correctness), and a throttling injection. None of those exist
yet — building them is part of the phase.

**When a document will not ingest:**

```
docker exec ingestion python3 /app/ingest.py --status     # the last cycle
docker exec ingestion python3 /app/ingest.py --failures   # every failure, with the reason
```

`--status` knows only the last cycle, so a dead-lettered document disappears from it and the deployment
reports healthy while a file is unindexed. **`--failures` is the one to read**, because it reads the
manifest. After any fix to the parser, `--clear-failures <relpath>` is required: the latch is keyed on the
content hash, so without it a correct fix looks like it did nothing.

---

## 9. The frontend, when it comes

**Not now, and not in C3.** The phase's acceptance says "no UI" on purpose: the API has to be right before
anything is built on top of it. But it is the eventual destination, so the record is here.

**The scope is the operations a person regularly needs, not the whole system.** The operator guide is
already very nearly that list, because every stage is a procedure with a checkpoint. The ones that want a
screen are the **file edits** — the work a non-technical operator currently does in a text editor and a
terminal:

| Stage | What a person does today | Wants a UI |
|---|---|---|
| 1 | edits the manifest — models, roles, budgets, egress | **yes**, this is the main one |
| 2 | renders, and restarts what needs it | **yes** |
| 14b | adds a role, changes a role's model | **yes** |
| 15 | ends a contract and reads the destruction record | **yes** |
| 4 | lays out the corpus and names the access roots | **yes**, eventually — it is where mistakes are silent |
| 3, 5–13, 16 | bringing up, ingesting, asking, checking, troubleshooting | mixed; much of it is diagnosis and may never want a screen |

**It is a frontend over the API C3 builds, which is why the order is right.** Every operation above should
become an API call first and a screen second. A UI that shells out to `docker compose` would reproduce the
current problem in a new place.
