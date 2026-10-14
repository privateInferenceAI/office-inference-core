# Handover: building C4, the client app

**Read this before the first line of code.** It is what the C3 handoff was: what the phase is, how work happens
here, how it gets tested, and what will go wrong if nobody says it first. Everything in it comes from the four
phases already built, and section 9 is the part that cost the most to learn.

---

## 0. What this phase is

**Ask, Do, Review, Activity.** A person who has never seen the system asks a question, gets an answer with its
sources, approves or denies an action, and can find out what happened afterwards. The acceptance sentence from the
build plan is the whole test: **someone who has never seen it can ask a question and approve an action.**

It talks to the assistant API and nothing else. No model keys, no database, no knowledge of which model answered.
That constraint is what lets the model tier, the retrieval stack and the runtime change underneath without a
rewrite, and it is worth defending when the first tempting shortcut appears.

**This is not the operator console.** An operator edits manifests and reads audit records, and the C3 handoff
listed screens for that work. Those are a different product for a different person and they are not in this
phase. If a task in C4 starts to look like the manifest editor, it belongs somewhere else.

---

## 1. The requirements

**The register has no C4 rows.** C1, C2, C3, C7 and C8 have items; C4 has none, so the first job is to write
them. Propose between five and eight, each with a test that could fail, and put them in `docs/requirements.yaml`
before building rather than after. A requirement written at the end describes what happened.

The four screens and the two indicators are the natural set. Whatever the wording, each one needs a way to be
wrong: *the answer carries its citations*, not *the Ask screen works*.

---

## 2. What is already decided

`docs/design/frontend.md` is the design and it is prescriptive. Six principles, four screens plus Settings, two
permanent indicators, and the build-or-adopt call. **Do not re-litigate it.** The decisions that matter:

| Decision | What it says |
|---|---|
| Build or adopt | **Build** the client app as a small TypeScript app. **Adopt** LibreChat for the lab console. Both talk to the same API, so this is not a choice between them |
| Principles | Show your work. Few verbs, big targets. Nothing happens without a receipt. Say where data went. Plain words. **The app is a function of the manifest**, so a client without the agentic pack never sees Do |
| The two indicators | Data lane (*stayed on this machine* / *left the building, approved by X at time*), and system state (*local models ready* / *indexing N documents* / *frontier lane off*). Both permanent, neither decorative |
| Office reality | 1366x768, tablet at the front desk, keyboard-first, large default text, no dark patterns |

**That document is dated 9 October and predates C2 and C3.** Where it assumes an API shape, the API is the
authority. Its *intent* is reliable; its field names may not be.

---

## 3. What the API already gives you, and the three gaps

Most of this phase is a client over endpoints that exist. Mapping the screens to them now saves discovering the
holes later.

| Screen | Already there | Missing |
|---|---|---|
| **Ask** | `POST /ask` and `POST /ask/stream` with citations, `supported`/`unsupported`, `denied` and `disclosure`, `redactions`, `lane`, `model` and `resolved_model` | nothing structural |
| **Activity** | the audit chain, with filtered listing through the audit CLI | **no read API.** Activity is the screen that makes the product auditable to a client, and it has no endpoint |
| **Review** | the concept of a refusal is recorded and readable | **nothing to review.** C5 workflows and C6 agents generate approvals, and neither exists |
| **Do** | packs are declared in the manifest, so visibility is knowable | **nothing to do.** The agentic pack is C6 |

**Two of the four screens have nothing behind them yet**, and that is a decision to make rather than a surprise
to hit. The honest options are to build Do and Review as visible placeholders that say what they will do, or to
scope C4 to Ask and Activity and add the other two when their dependencies land. **I would take the second and
say so plainly**, because a Review screen with nothing to review teaches a client that the feature is broken.

**A third gap, and it is not on a screen.** *"Left the building, approved by Dana at 2:14pm"* needs an identity.
C3's sessions are a development stand-in behind a seam, and real identity arrives at C7 with Cognito. Until then
the indicator has to say something true about who is signed in, and the wording matters more than the mechanism:
a client will read that sentence and believe it.

---

## 4. How work happens here

Four activities, in this order, every time. They are not a suggestion; they are why the last four phases are
navigable by somebody who was not there.

**1. Write the runbook step before the code.** `docs/runbook.md` is the specification. The step says what the
thing does, the exact commands, and the output that proves it. Writing it first is what stops the build from
being whatever was easiest.

**2. Build it.**

**3. Write the manual page.** `docs/manual.md` says *what* and *how*. The operator guide says *why and in what
order*, and it is the document a person actually follows.

**4. Follow your own page.** Run every command you wrote, in order, on the real deployment, as though you had
never seen it. **Fix whichever is wrong, the page or the code.** This is the activity that keeps finding things,
and it is the one that gets skipped when a phase is running late.

**Then the panel, at the end of the phase.** Section 5.

---

## 5. How C4 gets tested

**A UI breaks the probe idea.** C3's tests were command-line probes against a live service, and there is no
equivalent for a screen. The honest replacement is a ladder, and each rung catches something the one below it
cannot.

| Rung | What it is | What it catches |
|---|---|---|
| Unit and component | pure functions, state, formatting, the redaction of a citation chip | the pieces |
| Integration against the live API | the app's own client code driving a real deployment | the wiring, which is where C3 failed most |
| **End-to-end in a headless browser** | Playwright driving real flows against the deployed app | what a person actually does |
| **The data-lane indicator, asserted** | a test that the indicator cannot disagree with the audit record | the honesty claim, which is the product |
| **Post-action audit verification** | do a thing in the UI, then assert the chain recorded it | a UI action that leaves no trace |
| Accessibility | a keyboard-only path, 1366x768, measured text size | the office reality the design names |
| **The panel, three agents** | executor, cold reader, fidelity checker | what none of the above can see |

**Two of those rungs are the ones that matter most and are easiest to skip.** The lane indicator is a promise
about where data went, and a promise no test checks is a decoration. The post-action check is the same idea from
the other side: if clicking Approve leaves no row in the chain, the product's central claim is false and no
screenshot will show it.

**The panel's cold-reader question changes for a UI, and this is the interesting part.** In C3 it was *could a
competent stranger run this from the documentation alone.* For C4 it becomes **could a competent stranger use
this without being told anything, and would they understand what happened.** A different test, and the one that
decides whether the product is clear. Run the panel at the end of the phase, in up to three rounds:

| Reviewer | What it does | The question it answers |
|---|---|---|
| **executor** | uses the app from the manual and the guide, reports where reality differs | does it work as written |
| **cold reader** | the manual and the app, and nothing else. Never the source | would a stranger understand it |
| **fidelity checker** | the manual's claims against the source and the running system | does the page describe what the code does |

**Every round re-tests from the manual.** Not from the diff, not from a changelog. The rule is *run the
documented thing and see what happens now.*

**Each reviewer states a verdict.** Yes or no. **And tell them plainly that a clean round is a real result and
that they should not manufacture a finding to seem useful.** A reviewer who always finds something is a reviewer
whose findings stop being read. In C3 the cold reader retracted one of its own findings on re-testing, which is
the behaviour you want and only happens if being wrong is allowed.

---

## 6. Known gaps, to handle rather than discover

**Do and Review have no engine behind them.** C5 and C6. Decide the scope in section 3 and write the decision
down.

**"Approved by Dana at 2:14pm" needs an identity C4 does not have.** Real identity is C7. Say something true in
the meantime and revisit the wording when Cognito lands.

**The frontend design predates the API.** Its intent holds; its field names may not.

**The request-side redaction is still not wired into the API.** `services/egress/egress.py` exists, the probes
exercise it directly, and the API does not call it. **The response side is wired on both endpoints now**, which
is the half a client sees. Recorded rather than fixed, and it should stay visible in the manual until it is done.

**The budget cap is not a hard cap, and the wording in the manifest is deliberate.** LiteLLM checks admission
against recorded spend, so concurrent requests can pass before any is counted. It stops a runaway; it does not
make a number unexceedable. **Do not let a screen imply otherwise.**

---

## 7. Where things are

| Path | What |
|---|---|
| `docs/manual.md` | the operating manual. What and how |
| `docs/operator-guide.md` | why and in what order, stage by stage, with checkpoints |
| `docs/runbook.md` | every step as it was run, with real output. The specification |
| `docs/review-log.md` | every panel round, its verdicts and what was fixed |
| `docs/requirements.yaml` | the register. Phase status lives here |
| `docs/design/frontend.md` | the client app's design |
| `docs/design/c3-assistant-api-decisions.md` | what C3 decided and why, including the measurements |
| `services/api/` | the assistant API. The client app's only dependency |
| `tests/fixtures/` | the probes. Read `gate_check_run.sh` before writing a new one |

---

## 8. The commands

```
# the deployment, from the repo on the box
docker compose -f deploy/office/compose/docker-compose.yml --env-file rendered/env ps

# the tests
~/venv/bin/python -m pytest tests/ -q

# the probes, each after a pause so the provider stays calm
docker run --rm --network ai-net --user "$(id -u):$(id -g)" -v /opt/oi-core:/w -w /w \
  --env-file rendered/env --entrypoint python oi-core/session:local /w/tests/fixtures/api_probe.py

# the boundary check, which needs its runner because it tests a deployment
# whose class may NOT leave
bash tests/fixtures/gate_check_run.sh
```

---

## 9. What C3 cost to learn, so C4 does not pay it again

**Probe each path, not each feature.** This is the big one. C3 shipped two defects of the same shape and neither
was visible to a probe. Redaction was implemented on the streaming endpoint and missing on the JSON one: the
canary came back in clear over `POST /ask` while the stream redacted it. The data boundary was implemented on
the JSON endpoint and missing on the streaming one: a forbidden class was refused on `/ask` and answered by the
remote model on `/ask/stream`, with an audit row saying `egress_allowed: false` while the content left. **Both
passed every probe, because each probe tested the endpoint its author had in mind.** When C4 adds a second way
to do something, test both. When it adds a field, check it appears on every response that should carry it.

**A probe that cannot fail is not a probe.** `gate_check.py` called the routing function in process and posted
once to `/ask`. It printed the same three lines whatever the service did, and the manual told readers to run it
"to check the claim rather than take it". It was rebuilt to stand up a deployment with a forbidden class and ask
both endpoints, and then **proved able to fail**: with the guard removed it exits 1 and names the endpoint that
answered. Do that to every probe you write. Remove the control, watch the probe fail, put it back.

**A silent no-op edit is worse than a wrong one.** A sample in the manual kept listing six services for two
rounds because the replacement never matched: the file uses a single Unicode ellipsis and the pattern used three
dots. Nothing reported an error. **Check that your edit changed something, every time.** Do not trust a
replacement that did not raise.

**A test that disables a control must restore it even when the test is killed.** A regression run timed out
mid-way and left the streaming guard disabled on the box, with the API rebuilt from the disabled source. The
next reviewer would have measured a deployment whose boundary was open. It was caught only because another
reviewer reported uncommitted changes it had not made.

**Confirm a tester is testing what you think.** Twice, a check "passed" for the wrong reason: a temp API that had
fallen back to the real manifest, and a temp container started without `DATABASE_URL` so every request returned
`401` and looked like a session bug. **Assert the precondition before trusting the result.**

**Reviewers must clean up after themselves, and so must you.** A burst container ran for twelve minutes, throttled
the provider, and made the next reviewer's measurements look like product defects. Write it into the brief you
give a reviewer: use `--rm`, stop what you start, and say what you started.

**Keep the checkout current before a review round.** One C3 round was spent partly on a tree 78 commits behind.
The reviewer said so, which was useful, and it was still a wasted round.

**A control's evidence has to be in the record.** For most of C3, the API never passed the data class or the
egress verdict to the audit tool, so 7,679 of 7,691 model-call rows said nothing about whether content was
permitted to leave. The columns existed. The flags existed. Nobody connected them. **When you add a control, add
its evidence in the same commit, and read the row back to prove it is there.**

---

## 10. The register, and what is still owed

**The five C3 items have their evidence:** `SEC-ACL-002`, `INF-ROLE-001`, `INF-STREAM-001`,
`INF-TIMEOUT-001`, `INF-ROUTE-001`. All three panel reviewers passed the phase, and every finding is fixed and
re-tested. Whether to mark them closed is a decision for Anthony, not for the agent who built them.

**Still owed beyond C4, carried forward rather than lost:**

- the request-side redaction wired into the API
- proof that the gateway enforces the identity rate limits it is configured with
- a `fallback_served` audit fact, and error-typed fallbacks with a cooldown
- a review cadence for the agentic trigger list
- the audit writer: spool locally, refuse past a bound

**Do not let a green suite mean more than it does.** C3 ended with 611 passing tests, four probes passing, and a
live deployment that returned a Social Security number in clear on one of its two endpoints. The tests were
honest; they were just aimed at the wrong half.
