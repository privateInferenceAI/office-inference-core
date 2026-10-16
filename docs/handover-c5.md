# Handover: Office Inference Core, and how to work on it

**Written 2026-10-16 by the agent who built C5.** Read this whole document before you touch
anything. It is long on purpose. Anthony asked for it to be long, because a handover that
leaves you guessing means the two of you spend your first hour getting acquainted instead of
working.

---

## 0. Read this first

You are picking up a real product with a real client promise, not a demo. The work through C4
is deployed. C5 is built, verified, and documented, and it has not yet passed its second
review panel.

**Three things about how this project works, before anything technical.**

**First, the product standard is a sentence.** C5 was framed for me like this, and it governs
every decision:

> This is a compliance product. A permission, a redaction, an audit record or a data boundary
> that reads as enforced.

That sentence is a test. When you write a permission, ask whether it *reads* as enforced or
whether it merely exists. When you write an audit record, ask whether a client reading it would
believe it. The whole C5 approval design came out of taking that sentence literally, and it is
why the approval record says "the person who decided was not identified" instead of naming
somebody.

**Second, the governing constraint.** Verbatim from Anthony:

> FOR NOW WE ARE ONLY BUILDING THE CLOUD VERSION. ALL DECISIONS ARE BASED ON CLOUD ARCHITECTURE
> NOT LIMITATIONS OF AN OFFICE SYSTEM.

There is also a newer, harder constraint that came from him during C5 and that now sits in the
register as `BIZ-INDEPENDENT-001`:

> Nothing the client depends on may live on infrastructure Office Inference operates after
> handover.

He said it like this: *"we may have clients that buy this system from us and once we set it up,
they want to take care of everything themselves, we are out of the picture completely."* That
killed an entire email intake design mid-build. When you design anything, ask whose
infrastructure it runs on after we leave.

**Third, and this is the one people get wrong: verify, do not recall.** Every defect worth
finding in this project was found by running something. None was found by reading. If you
believe a page, run it. If you believe a check passes, run the check. If you believe a file is
committed, ask git. I was wrong about my own work at least six times in one day, and every time
the thing that caught it was a command whose output I did not expect.

---

## 1. Who Anthony is, and how he works with you

He is the owner and the decision-maker. He is not a passive approver and he does not want to be
one.

**He works with you as a co-pilot.** He rides along, asks hard questions, and expects you to
push back with evidence. He noticed things in this session that I had missed, including the
whole reason the email design had to change. When he says something that contradicts your plan,
he is usually right and you should find out why before defending the plan.

**The operating model he agreed to, in his words.** I proposed three options and he picked the
second:

> **Owner with a threshold.** I decide the small things and bring you anything that changes
> scope, price, or a client-facing promise.

He then added something that changed how I worked for the rest of the day:

> I like to send out research agents when I need to make a big decision so that we are building
> this the way professional stacks are created. I dont want to cut corners. If there is a big
> decision you can send out the agents before you talk to me so we have good information in
> order to make the correct decision.

**So the pattern is: on a big decision, dispatch research first, then bring him findings and a
recommendation rather than an open question.** I did this four times in C5 and it changed three
of the four answers. The approval-identity research is the clearest example: I would have asked
him "how should we record approvals before we have real sign-in", and instead I brought him
21 CFR Part 11 and a field design.

**What he cannot stand.** Over-explaining. Hype. Being asked a question you could have answered
yourself with a command. He said early on: *"be concise, dont paste large files into chat when a
summary does."* And he means it.

**On context.** He asked me directly, mid-session, how much context I had left. The honest
answer then was "plenty". The honest answer at the end was "not much, and the last three fixes
each needed a retry". **Tell him the truth about this.** He plans around it. He explicitly
asked whether to keep working with me or start fresh, and that question is a good sign: he is
managing the resource, not ignoring it.

**Two preferences worth memorising.**

Never use the dollar sign character in a reply. The Web GUI renders paired dollar signs as math
delimiters and the surrounding text turns to vertical gibberish. Write `USD 5,000` or `5,000
dollars`.

Match his writing style in anything you write for him. Full rules are in
`~/.dsh/AGENTS.md` and in `ai-stack/Writing-Style-Guide.md`. The short version: plain, direct,
first person, contractions. Short paragraphs. Specifics over adjectives, numbers over claims.
No em-dash chains, no rule-of-three lists, no quotable-epigram sentences, no hype adjectives,
no exclamation marks. Before you finish, read it aloud and swap any word he would not say.

---

## 2. The product

**Office Inference** is an AI systems shop that builds a self-hosted stack for small businesses,
five to fifty people. Four offerings: Standard Cloud and Compliant Cloud (both in the client's
own cloud account), Standard Cloud Hosted (our account), and In-Office (hardware in their
building). One core stack, four deployments. The difference between the cloud tiers is legal,
not physical.

**The repository is `office-inference-core`**, private, at
`github.com/privateInferenceAI/office-inference-core`, cloned at
`/home/anthony/projects/office-inference-core`.

**The stack** is Docker Compose: a FastAPI assistant API, LiteLLM as the model gateway, Qdrant
for vectors, docling for parsing, Postgres for state, a React app for the interface, and now a
workflow engine and an email intake connector.

**The phases.** C0 reference machine, C1 gateway and audit, C2 documents and retrieval, C3
assistant API, C4 client app, **C5 workflows and the first connector**, C6 agent runtime and MCP
gateway, C7 ingress and identity, C8 operations, C9 the demonstration.

**One trap that catches everybody, and it caught me:** anything prefixed `oi` in this project is
either a command or a phase, and they are different things. `oi` is a shell wrapper. C-phases are
build stages. The register uses `phase: C5`. The runbook uses `### 6.x` for C5's steps, because
the runbook numbers by the *build sequence* and not by the C-phase. So C5 lives in runbook
section 6, and the manual calls its pages 13j through 13m.

---

## 3. Where everything lives

```
/home/anthony/projects/office-inference-core     the core repository
/home/anthony/projects/oic-ui                    the client app, a separate repository
/home/anthony/projects/office-inference-research research reports that are not product docs
/home/anthony/projects/office-inference-admin    business admin. LOCAL AND SENSITIVE. Never push.
/home/anthony/corpus-groups                      the C5 test corpus. Mostly archived to S3 now.
/home/anthony/corpora-logs-excluded              9,976 log files, deliberately excluded
```

**The deployment**, and everything about it is in `~/.dsh/AGENTS.md`:

```
instance     i-096bebd46308d1673   m7i.4xlarge, 16 vCPU, 64 GB, no GPU
IP           52.204.202.66
key          /home/anthony/ai-stack/ai-stack-key.pem
path         /opt/oi-core
cost         roughly 0.81 USD an hour. Stop it when you are finished.
account      878451098165, the Office Inference credits account
```

**Access is one line:**

```
ssh -i /home/anthony/ai-stack/ai-stack-key.pem ubuntu@52.204.202.66
```

**The single most useful thing on the box is `oi`**, the wrapper I wrote during C5. It knows the
compose file, the rendered environment, and which verbs need the `tools` profile:

```
oi build api          build a service
oi up -d api          start it
oi restart api        bounce it
oi ps                 what is running
oi run --rm audit …   run a tool
oi --profile tools run --rm session mint …    mint a session
```

It is installed as a symlink at `/usr/local/bin/oi`, pointing into the repository, so it follows
every pull. **Before C5 it did not exist**, and the documentation referenced it more than a
hundred times. Every one of those commands failed with `command not found`, including the update
procedure. That is worth understanding as a pattern; see section 8.

---

## 4. How this project builds a thing

**Four activities, in this order, and none of them skipped.** This is the house method and it is
not negotiable:

1. **Write the runbook step first.** Before the code. What this does, why it exists, the
   commands, the expected output, how to verify it, and what it means when it breaks.
2. **Build it.**
3. **Write the manual page**, and the operator-guide stage.
4. **Follow your own page literally.** Paste the real output into the runbook step.

**Activity four is where all the value is.** In one C5 step I wrote three commands that could not
run as written:

```
jobs_probe.py              the file is job_probe.py
python3 fetch_openapi.py   cannot resolve `api` from the host, and the correct form
                           was already written out four hundred lines above in step 5.0
--write                    the flag is --out
```

None of the three is visible by reading. Each failure also *looked like something else*: a
missing file, then a name resolution error that reads like a broken service, then an
unrecognised argument. That is the whole argument for activity four. **A page that has been
followed is a different artifact from a page that has been written.**

**A related rule that has teeth:** the register is the specification, and it gets written before
the build too. `docs/requirements.yaml`. Each row has an `id`, a `title`, a `tier`, a `phase`, a
`status`, and a `verify` field. **The `verify` field is the test that would FAIL if the
requirement were not met.** A requirement you cannot write a failing test for is a wish, and
`scripts/check_requirements.py` says so.

**The register's real purpose is that a claim reads as enforced.** When you add a row, ask what
command would prove it false. Then make that command exist.

---

## 5. C4, briefly, and what I did there

C4 is the client application: Ask, Do, Review, Activity. It was mostly built before this session
and I finished and corrected parts of it.

**What C4 got right and why it matters for C5.** It shipped a state machine with
`awaiting_approval` in it and **nothing that reached that state**. The comment in `jobs.py` says
why: the interface has to render the state, and a state added later changes the shape of the
screen. That decision is the reason C5 was cheap. C5 is the first writer for a state that was
defined two phases earlier.

**Two scope decisions C4 made, both recorded in `docs/handoff-c4.md`:**

Do was built with a real one-step executor rather than a placeholder, because the interface for
the client's own example ("work on this for thirty minutes, fail four times, change approach on
the fifth") is the product and retrofitting it later rewrites the interaction layer.

Review was deliberately NOT built, because approvals need C5 and a queue with nothing in it
teaches a client the feature is broken.

**What I did in C4.** I fixed the operator guide's C4 stage, and I found and fixed several
defects. The most instructive is the retention image. A teardown on the deployment cleared every
index, wrote a destruction record with a hash, and **left all 10,033 client documents on disk**.
The code that removes them had been in `main` since 13 October. The image was built before it and
never rebuilt. I found this because the documentation said one thing and the code did another.
`docs/operator-guide.md` said `destroy-all` leaves client files. The code deleted them. The page
was written 47 minutes before the behaviour changed and survived twenty later edits.

**The lesson, and it is the biggest one in this document:** a deployment running code nobody can
point at is the failure mode this project keeps producing. See section 8.

I also built, during C4 and C5, the thing that catches it: `scripts/check_images_current.sh`.
It compares each image's build time against the last commit touching its source, and each running
container's image id against its tag. It exits non-zero. Run it after any pull.

---

## 6. C5, in full

**The phase: workflows and the first connector. Exit criterion: invoice intake end to end, with
an approval gate.** Thirty-four register rows, all `phase: C5`.

### 6.1 The five decisions, and why

**Decision 1: the workflow engine is ours, not adopted.**

Research ranked owned code first, then Kestra, Temporal, n8n, Activepieces. Windmill is a flat no
because its licence requires a commercial agreement for anything you sell.

**The reason is not licensing. It is the audit row.** Our audit table is in Postgres. Code we own
writes the audit row **in the same transaction as the state change**. Every off-the-shelf engine
sits outside that transaction and reports by webhook, so a crash between the business write and
the audit write leaves a gap in the one artifact a client may ask to verify.

That requirement produced the single most interesting defect of the phase, in section 6.5.

**Decision 2: email intake, with everything in the client's estate.**

The obvious design was an address we host (`invoices@client.officeinference.com`). Anthony killed
it: the client takes over completely, so the address cannot be ours. Then the same objection
applied one level down, because an OAuth app registration in *our* identity tenant has the same
problem.

The design that survives:

```
the address             the client's, unchanged
the mailbox             theirs, and a LICENSED REGULAR user, not a shared mailbox
the app registration    theirs, in their Entra tenant
the consent             theirs, from their admin
the credential          theirs, on their box
the polling daemon      theirs
```

**A shared mailbox cannot be used at all**, and this is the finding that shaped everything.
Microsoft keeps sign-in blocked on shared mailboxes with a system-generated password, and
delegated OAuth needs one interactive sign-in. So a shared mailbox forces you onto application
permissions, and Graph's `Mail.Read` application permission reads **every mailbox in the
tenant**. Almost every small business's AP inbox is a shared mailbox, so **the client has to buy
a licensed seat**, and that belongs in the onboarding checklist.

The reasoning is in `docs/research/email-intake/handover-owned-email-intake.md`.

**Decision 3: approvals are recorded honestly, and the record says what the system does not
know.**

Real sign-in arrives in C7. Until then a session is a bearer token, and a role is not a person.
21 CFR 11.100(b) makes identity verification a **precondition** to an electronic signature
existing, and the FDA's 2003 enforcement discretion **kept** the signature rules when it relaxed
everything else. Section 11.50 wants a printed name, a timestamp, and the meaning. C5 can do two
of three honestly and no wording fixes the third.

The record carries an `unverified` identity marker, names the role rather than the person, and
marks `printed_name` null **with the reason beside it**. A null with a reason tells the reader
the record looked. An absent field invites them to assume it does not carry names at all.

**The limit, stated plainly: you can never upgrade a historical approval's assurance.** The token
was a bearer credential and the workstation is often shared. Every C5-era approval stays
unverified permanently. C7 makes that boundary visible and dated; it does not make old approvals
verified. Full reasoning in `docs/design/c5-approval-record-identity.md`.

**Decision 4: keep SSE.** The transport was already correct. Three small changes went in: emit
the SSE `id:` field so the browser sends `Last-Event-ID` on reconnect, pin the heartbeat
explicitly, and do not log full URLs for that route because the session travels as a query
parameter. That last one is what makes a stock browser `EventSource` work at all.

**Decision 5: the groups are a sample, not a partition.** For the test corpus. Nineteen divisions
of 39 million chunks leaves a smallest group of a million, which is four days of embedding. You
can have groups that cover everything or groups that are testable, not both. The corpus is in S3,
grouped, with a manifest per group.

### 6.2 What was built

```
services/workflow/       the engine. engine.py, plans.py, chain.py
services/intake/         the connector. twelve modules and a schema
services/api/approvals.py    the approval record, and the four rules it enforces
services/api/app.py      the routes: POST /jobs/{id}/approvals and GET /approvals
db/migrations/021-023    pending_action, the feed sentence, the effects ledger
oic-ui/app/src/          Review.tsx, JobView.tsx, queue.ts, the SSE client
```

**The plan** is `invoice_intake`, six steps: read the message, extract four fields, draft the
entry, **approval gate**, post the entry, close out.

**The four extraction patterns**, and this cost me an hour so it will cost you five minutes:

```python
_AMOUNT    = r"\btotal\s+([0-9][0-9,]*(?:\.[0-9]{1,2})?)"   IGNORECASE
_VENDOR    = r"\bfrom\s+([^\s,;]+)"                          IGNORECASE
_DUE       = r"\bdue\s+([0-9]{4}-[0-9]{2}-[0-9]{2})"        IGNORECASE
_REFERENCE = r"\b([A-Z]{2,}-[0-9]{4}-[0-9]{3,})\b"           case sensitive
```

**The plan reads a message, not an invoice description.** Total and vendor are required. Text
that does not match fails as `unreadable_invoice`, and **that refusal is the feature working**:
an invoice whose total could not be read is not an invoice for zero. This is documented now in
manual 13j and guide 13m.0, because three of my own test runs failed on it while I looked for a
bug in the extractor.

### 6.3 The defects, and the shape they share

**Four defects on the HTTP path, and every one was invisible to a green test suite.** None was a
bug in any unit of the system. All four were in the seam between units, and the seam had never
been run.

**One: `outcome` was the decision.** The approval route passed `approve`/`deny`/`request_changes`
to `audit_events.outcome`, which is constrained to `('ok','denied','error','pending')`. That
raised a `CheckViolation` **after** `jobs.decide()` had committed the state change: a resumed job
with no record of who approved it, and a 500 to the client. The fix passes `ok`, which is the
outcome of the *recording*, and leaves the decision in `payload`, where `build_payload` puts it
and `describe()` reads it.

**Two: `Jsonb` used and not imported.** `PostgresStore.put()` raised `NameError` on every durable
write, and **the entire 896-test suite was green on it**, because every test uses `MemoryStore`.

**Three: route shadowing.** `@app.post("/jobs/{job_id}/{action}")` was declared **before**
`@app.post("/jobs/{job_id}/approvals")`, and Starlette matches routes in declaration order. The
wildcard captured `/approvals` as `action="approvals"` and the handler correctly refused an action
it did not know. **The approvals route had never run once, for any decision.**

**And nothing could see it.** `docs/openapi.json` listed both paths. The contract check passed.
Every assertion passed, because a schema is generated from the route table and does not know
which entry wins a match. Every earlier probe got a 401 for a missing session, which is what the
wildcard returns too. **Only a request with a valid session tells them apart, and nothing had
ever sent one.** That is now a documented limit of the contract check: it proves a path exists,
not that it is reachable.

**Four: the job was not in the store.** `_run_or_resume` persisted the job only in the one-step
branch, so the engine was handed a job that existed in memory and nowhere else. It refused in its
own words: *"job … is not in the store, so it cannot be driven."*

**And a fifth, found while fixing the third:** my `events_through` query ate the steps result set.
`cur.fetchall()` reads the result set of the **most recent** `execute`, and I put a second query
between the steps read and the fetch. So `PostgresStore.get()` returned an empty steps list every
time, and a job came back from the database with no history. **The impact was worse than the
500**: with steps always empty the engine's position is always step zero, so a resumed job would
have re-run the whole plan and duplicated its effects, except a uniqueness constraint stopped it.

**Then a sixth, found by reading a row rather than a traceback:** a job that succeeded after a
resume carried `outcome_reason: paused_by_operator`, describing a state it had left.

### 6.4 The shape behind all of it

**The durable path and the in-memory path disagree, and the test suite cannot see it.** That is
the pattern. Six occurrences in one phase:

```
pending_action_digest     on the dataclass, not in the store
Jsonb                     used, not imported
events_through            the fetchall that ate the result set
the stale response        the handler's object versus the store's row
outcome_reason            never cleared on resume
the psycopg stub          a fake driver shadowing the real one
```

**What caught them was a probe against a real Postgres that starts at a job row, not a plan.**
`workflow-builder` put it exactly right: the fix is "run the durable path in CI", not "write more
unit tests".

**There is now `tests/test_store_parity.py`**, which writes one job through both stores with
steps, a digest, a pending action and events, and compares them field for field. It fails on the
commit that introduced the `events_through` bug, and it would have failed on `Jsonb` and on
`pending_action_digest`.

### 6.5 The register, and a defect in the register itself

**26 of 34 C5 rows are verified, 8 are open.** The open ones belong to the intake connector's
tenant gap, below.

**Two of the verified rows carry their limit in the note, not only their result.**
`CON-OWNED-001` and `CON-LEAST-001` are proved against a stand-in tenant that implements the
documented delegated semantics, **not against Microsoft**. Saying so is the difference between a
register and a brochure, and the next reader needs to know which rows a real tenant would still
have to confirm.

**And the register had a defect the panel found.** Three rows carried **two `note:` keys**. YAML
keeps the last and drops the earlier one without a word, and the dropped one held the
verification evidence: *"Verified 2026-10-16, runbook step 6.1"* in each case. So the row said
`status: verified` and the sentence explaining what proved it was gone.

**And `scripts/check_requirements.py` printed "register is well formed".** A check passing on a
record that lost content is the exact failure this register exists to prevent.

Both halves are merged now, and **the checker refuses a repeated key** by walking the document
with a loader that raises. Getting that check right took two attempts. Catching
`ConstructorError` let the refusal escape as a traceback, because PyYAML marks it up into a
`MarkedYAMLError` on the way out. And my first placement put the block before `problems` was
declared, so the report died with `UnboundLocalError` instead of printing the finding. **A check
whose failure mode is a crash tells a reader nothing**, which is the same fault as the check that
passed.

---

## 7. The panel, and where C5 stands

**The panel is three agents, up to three rounds, each stating yes or no.**

```
executor          runs every documented command and compares output to the page
cold reader       reads the pages as a newcomer, no source, no git history
fidelity checker  checks every claim against the code and the deployment
```

**Three rules that make it work, and you must pass them on verbatim:**

**Test from the manual, not from the diff.** Not from a changelog, not from what the code looks
like it does. The rule is: run the documented thing and see what happens now. A reviewer working
from the changelog finds the changes they already know about.

**State a verdict: yes or no.**

**A clean round is a real result. Do not manufacture a finding to seem useful.** A reviewer who
always finds something is a reviewer whose findings stop being read. In C3 the cold reader
retracted one of its own findings on re-testing, which is the behaviour you want and it only
happens if being wrong is allowed.

**Also tell each reviewer plainly what it cannot have** (no Microsoft tenant, no SMB share) so it
reports the gap rather than implying coverage.

### Round one, 2026-10-16: all three said NO

**And all three said the same thing.**

> The behaviour C5 describes works. What does not work is the documentation of how to check it.

**The executor verified the entire HTTP workflow end to end** and found **no defect in the
engine, the gate, the approval record, the intake connector, or the two screens.** What it
found was nine documented commands that fail or cannot run as written.

```
POST /jobs                -> 200, awaiting_approval, 4 steps, digest
docker restart api        ->  same state, digest intact
wrong digest              ->  409 approval_drift with both digests
deny with no reason       ->  400 bad_decision
approve with the digest   ->  200, succeeded, six steps, one row each
audit verify              ->  chain intact, 298 events
tampered copy             ->  FAIL, so the green is meaningful
```

**Thirty documentation findings.** The fixes are on main now. The three that mattered most:

**No runnable command started an `invoice_intake` job.** The manual gave the text to send and
never said where to send it. I had written that section that afternoon and congratulated myself
on closing the gap, and left out the one thing that makes it usable.

**The operator guide contradicted the runbook.** Stage 13m.6 said *"The engine is not wired into
the HTTP routes yet... a workflow cannot be started over HTTP"*, written before I wired it, while
runbook 6.2 pasted it working. Same date, opposite claims.

**A client-facing sentence was quoted wrong in three of four places.** The system prints
`Approved at <iso> under the <role> role. The person who decided was not identified: sign-in is
not enabled on this deployment, so the system cannot verify who acted.` Three pages had their own
version, each presented as *the* sentence.

### What round two should do

**Run the same three roles against the corrected pages.** The pages changed substantially:
hundreds of lines in the manual alone. The point of round two is to confirm the corrections
landed rather than assuming they did.

**Point the fidelity checker at these specifically**, because they are the ones most likely to
have been corrected wrongly:

- Every pasted output block in runbook 6.0 through 6.6. Several were stale before the round and
  the fixer re-ran some. A stale paste is a finding: a reader cannot tell it from a current one.
- The count lines. The workstation and the box disagree legitimately (896/22 here, 884/23 there,
  because `jsonschema` is installed here). The pages now name the deployment run and list each
  skip. Check that reconciles.
- The route-order paragraph in 6.0, and whether it is in the right step.
- The extraction patterns and their case sensitivity. `_REFERENCE` is case sensitive and the other
  three are not.
- The approval sentence, in all four places it appears.
- Whether the new "start a job" command actually runs, because **`docs-fixer` verified it by the
  route, the registry and the contract rather than by running it** — deliberately, since running
  it writes to the deployment's chain. **That is the one correction in the whole round with no
  execution behind it.** Have the executor run it.

### The state as I hand over

```
office-inference-core    main, pushed, clean. 896 passed, 22 skipped. Register well formed.
oic-ui                   main, pushed, clean. 160 passed, 12 skipped. Contract check OK.
the box                  at an older commit. Pull and rebuild before testing.
the box's DB             has probe rows and test jobs in it. The chain is append-only by design.
                         A disposable postgres container oi-c5-testpg may still be running on 55432.
```

**The box needs a pull and a rebuild before round two**, and the app needs pulling there too. Run
`bash scripts/check_images_current.sh` afterward; it will tell you if anything is stale.

---

## 8. The traps, in the order they will bite you

**A deployment running code nobody can point at.** This is the project's signature failure and it
has cost real damage. A teardown cleared every index and left 10,033 client documents on disk
while writing a destruction record, because the image was built before the fix and never rebuilt.
`git pull` without a rebuild is invisible: `git status` is clean, the count says zero, every
command runs.

**And the box lies about being current.** `git rev-list HEAD..origin/main` only means something
after a `git fetch`. The box's `origin/main` ref was three days stale, so it reported being zero
commits behind while it was five behind. **Fetch first, then count, then pull, then rebuild, then
verify.** In that order.

**A documented command that cannot run.** This project has now hit it six times. `oi` did not
exist while the documentation referenced it more than a hundred times, including the update
procedure. `jobs_probe.py` was written for a file called `job_probe.py`. A command in the manual's
update procedure could not resolve a hostname. **Every one was found by running it.** When you
write a page, follow it.

**The durable path is not the tested path.** Six occurrences in one phase. If a store has two
implementations, only one of them is exercised by unit tests, and the one that is not is the one
that runs in production.

**A check that answers a different question than the one that matters.** The contract check
verifies a path exists, not that it is reachable. The image check verified images, not
containers, and reported everything current while every container ran an older build. The
register checker verified structure, not that a row had kept its content. **When you write a
check, ask what it cannot see, and write that down in the check.**

**Reporting success on partial work.** A grouping script reported moving 236,519 of 238,095 files
and said nothing about the shortfall. A `git add -A` swept up a teammate's in-progress work into
my commit. **Count what you moved, and compare it to what you started with.** The grouping tool
now asserts it.

**A subagent writing into the wrong repository.** Subagents inherit the session working directory,
which is `ai-stack`, a *different product*. Two research reports were filed there by accident.
**Every brief you write for a subagent needs an absolute repository path and an explicit
statement that the working directory is not the target.** The check afterward is two commands:
confirm the files landed where claimed, and confirm the other repositories are unchanged.

**Fixing the instance instead of the shape.** When `retention` was stale I rebuilt it. When a
second check had the same gap I wrote a test. When six store defects appeared I wrote a parity
test. **The instance is cheaper and the shape is what lasts.** Ask which one you are doing, and
say so.

---

## 9. How to be useful here

This is the part Anthony asked for most explicitly, so let me be concrete.

**Run things.** Do not recall. Do not assume. Your recollection of what a file says is a
hypothesis and the file is the evidence. I was wrong about my own recent work at least six
times in one day, and the commands caught every one.

**Say what you did not do, loudly.** The single most valuable sentence in any teammate's report
this session was `workflow-builder`'s: *"The HTTP path for a workflow has never run."* That was
true, it was uncomfortable, and it was the reason four defects were found in twenty minutes
rather than in front of a client. Every agent that reported its gaps honestly made the work
better. Report yours first, before the things that went well.

**Distinguish a decision from evidence.** When Anthony closes a blocker, record *how* it closed.
I wrote the n8n licence line as "closed by the owner's decision, not by a grant, and if a client
asks for the grant it does not exist". Six months from now nobody will remember which kind of
closure that was.

**Put the limit in the note, not just the result.** A register row that says "verified" and omits
"against a stand-in tenant, not against Microsoft" is a row that will mislead the first person
who relies on it.

**Push back with evidence.** Anthony wants it. When `workflow-builder` found that a requirement I
had written was unachievable as specified, the right move was to say so, not to satisfy the
letter of it. He did, and the requirement changed.

**Prefer the smaller, honest fix.** When a test can only pass against a fake, the test is a test
of the fake. When I found the `psycopg` stub collision I tried the obvious fix, it broke the
other half, and **I reverted and wrote down three candidate fixes with their costs** rather than
leave a half-fix in the tree. Leaving it green with a known, documented collision was better than
leaving it red with a guess.

**Ask before you guess on a big decision, and research before you ask.** Four research agents
changed three of four answers in C5. The approval-identity one is the clearest: the question
"how do we record approvals without sign-in" has a regulatory answer, and finding it took one
research pass and produced a design I would never have reached.

**Do not narrate your confidence. Show your work.** Numbers, commands, output. The tone that
works with Anthony is plain and specific, and it is the tone of somebody who checked.

**And keep the humour dry.** He wrote *"lol"* once and *"have fun!"* once. Match that energy by
being easy to work with, not by being jokey.

---

## 10. If you read nothing else

```
The product works. C5 is built, verified on a real deployment, and documented.
Round one of the panel said NO, and all thirty findings were documentation.
Round two has not run. That is the job.

The three rules for the panel:
  test from the manual, not the diff
  state a verdict
  a clean round is a real result, so do not manufacture a finding

The four activities, in order, none skipped:
  runbook step, build, manual page, follow your own page

And the one that catches the most:
  verify, do not recall
```

**Everything is pushed. The register is current. The board is accurate. Start with `git fetch`
and `git log --oneline -15` to see what the last day looked like, then read
`docs/runbook.md` section 6, then run the panel.**

Good luck. It is a good system and it is in decent shape.
