## Phase C4 — Client app

The interface a business actually uses. Ask a question, and get an answer that shows where it came from.
Start work, and watch it happen. Read back what happened afterwards.

**The phase talks to the API and nothing else.** No model key, no database credential, and no knowledge of
which model answered. The app lives in its own repository, `office-inference-ui`, and the thing connecting
the two is `docs/openapi.json`, generated from this service by `scripts/fetch_openapi.py`. That file is the
contract, and `--check` fails when the running service and the committed file disagree.

### 5.0 The contract, before anything is built on it

**Goal.** A second repository can be written against this API without reading its source.

**Why this is the first step and not paperwork.** The OpenAPI document at `/openapi.json` listed three URLs
and **no response bodies at all**. Every response was an empty schema. A client built against that would have
been built against the manual's prose, and would have broken the first time a field moved.

**What was added.** `services/api/models.py` declares every payload: the answer with its citations and the
counts beside them, the degraded answer, the refusal, the four stream frames, and health. They are attached
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
OK    /w/docs/openapi.json matches http://api:8088  (3 path(s): /ask, /ask/stream, /health)
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
docker run --rm --network ai-net --user "$(id -u):$(id -g)" \
  -v /opt/oi-core:/w -w /w --env-file rendered/env \
  --entrypoint python oi-core/session:local /w/tests/fixtures/job_probe.py
```

Expected:

```
ok   CREATED     job 8f3c… state='succeeded' attempts=1 steps=1
ok   EVENTS      4 event(s), seq 1..4, replay from seq 2 returns 3
ok   RECORDED    the job and every step carry an audit row
ok   GUARDED     a role-supplied request is refused on /jobs as it is on /ask
ok   BOUNDARY    a forbidden data class does not run as a job either
ok   DENIED      a job for a task this deployment does not declare is refused, by name
PASSED: 6 right(s) held against the documented job interface
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

Built in `office-inference-ui` against `docs/openapi.json`. Ask first, then Activity, then the job view. The
test ladder is in `docs/handoff-c4.md` §5, and two rungs of it are the ones that matter: **the data-lane
indicator cannot disagree with the audit record**, and **doing a thing in the interface leaves a row**.
