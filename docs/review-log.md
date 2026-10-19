# The review log

Every review round, what it covered, and what changed something. A round that found nothing still gets a line,
because a silent round and an unrun round look identical afterwards.

## C3 panel, round 1 — started 2026-10-14

**Scope.** The whole of C3, reviewed once at the end of the phase. Three agents that did not write the work.

| Reviewer | Entry point | Access |
|---|---|---|
| executor | `docs/manual.md`, sections 9 to 19 | the live box and the repo |
| cold reader | `docs/manual.md` only | **no source, no runbook** |
| fidelity checker | `docs/manual.md` against the source | the repo |

**The rules, from `docs/handoff-c3.md` section 5.2.**

1. **They get the manual and nothing else.** Not the code, not the diff, not a list of what changed, not the
   runbook. If a reviewer needs the source to work out how to run something, that is itself the finding.
2. **Every round re-tests from the manual, including rounds that only verify fixes.** A round is not
   *did you change the line I complained about*, it is *run the documented command and see what happens now*.
3. **Each reviewer states a verdict** — a yes or a no on whether a competent stranger could run this from the
   documentation alone — so stopping is a decision rather than a feeling.
4. **Cap three rounds.** Stop when a round returns only cosmetics.

**Arranged so that fixes are possible between rounds.** Each reviewer was told to report partway and stop,
rather than run to completion, so the coordinating agent can fix what they find and send them back. A reviewer
that finishes everything in one pass breaks that loop.

**Baseline before round 1**, taken so that anything a reviewer breaks is distinguishable from what was already
there:

    595 passed
    6/6 manifests valid; 8 packs known
    api_probe          PASSED: 7 right(s) held against the running service
    interface_probe    PASSED: 7 right(s) held against the documented interface
    stream_probe       PASSED: 4 right(s) held against the streaming service
    access_probe       PASSED: 4 right(s) held against the live stack
    acl_probe          PASSED
    hybrid_acceptance  PASSED
    citations_acceptance PASSED
    injection_acceptance PASSED
    egress_acceptance  PASSED
    7 services running

**What round 1 found.** All three reviewers returned **NO** on the verdict question, and between them they found
five defects that would have shipped. Two were severe and both were in the service rather than the page.

### The two severe ones

**The stream redaction leaked, and it leaked every time.** `_drain` sliced a safe prefix out of the raw buffer,
redacted that slice alone, and advanced the buffer by the slice's length. Redacting a slice without its
predecessors meant a pattern straddling the boundary had nothing to match against, and advancing by the slice
length discarded the held tail so the next chunk could not complete it either. With random chunking a canary
walked out in the clear in **50 trials out of 50**. A single feed never leaked, which is exactly why the unit
tests passed: they fed whole answers in one call, where the final drain flushes everything through one
redaction.

Found by the **fidelity checker**, who fed the module a canary through its own tests rather than reading it.

**Every retry failed authentication.** The signed assertion was minted once, before the retry loop, and a
statement is spent by its first presentation. Attempt one earned the real throttle, the retry replayed a spent
assertion, and the 401 that came back classified as `unclassified` — so a genuine Bedrock throttle was recorded
as an unrecognised failure and the documented retry could never have succeeded. `MAX_ATTEMPTS` was three and the
third attempt was unreachable.

Found by the **executor**, who provoked a real Bedrock throttle with 486 `ThrottlingException`s and read the
reason the deployment had recorded.

### What else changed

| Finding | Fix |
|---|---|
| The manual documented the assistant API as running and as not built | C3 marked built, and four other pages that denied its existence corrected |
| `keys --set-budget`, the documented emergency brake, wrote a virtual key that nothing consults | it writes the team row the auth hook reads, and says the gateway must restart |
| The egress gate is bypassed on any deployment declaring a local model | **not fixed.** Recorded; see below |
| Escalation does not change the model on this deployment | **not fixed.** Recorded; see below |
| The `/ask` deadline is anchored after retrieval, not at the client's ask | **not fixed.** Recorded |
| Section 18's model swap needed two restarts, not one | corrected, with the provider-side check |
| Section 18's verification command returns 401 and exits 0 | replaced with the `resolved_model` check |
| The SSE `done` frame carries `ttft_ms`, not the documented `first_token_ms` | **not yet fixed** |
| An undocumented `degraded` SSE event | **not yet fixed** |
| Section 9 said six services; there are seven | corrected |
| `access.data_class` missing from the field reference | **not yet fixed** |

### The evidence that closed `INF-TIMEOUT-001`

The executor's burst produced more than a degraded answer. After the retry fix, a real provider throttle is
classified correctly and all three attempts run:

    reason    | attempts |  matched   |  threshold  | partial
    ---------- | -------- | ---------- | ----------- | -------
     throttled | 3        | throttling | first_token | false
     unclassified | 2     |            |             | false

The `unclassified | 2` row is the old code, kept because it is the contrast that makes the fix legible. The
`throttled | 3` rows are a genuine Bedrock `ThrottlingException` reaching our classifier, with every retry
attempted and the first-token threshold named.

### Deliberately not fixed in round 1, and why

**The gate bypass is real and needs a design decision, not a patch.** `local_lane_available()` is a
deployment-wide fact — does any role declare a local model — and the gate returns permitted immediately for it,
so on any deployment with a local model the triggers run unconditionally. That contradicts the one-way boundary
the whole step rests on. The fix is probably that the gate should be consulted for the frontier lane regardless,
and laneness is about where the model runs rather than whether the boundary applies. That wants a decision.

**Escalation not changing the model** is also a design gap: the renderer names a frontier entry `<role>-frontier`
on a deployment that also has a local entry, and nothing asks for it. On this cloud-only deployment both lanes
resolve to the same model, so the notice says "the stronger model answered" while the same model answers. That
is a false claim in a user-facing message and it deserves a proper fix rather than a wording change.

**Round 2 will re-test from the manual**, including everything fixed here.

---

## C3 panel, round 2 — 2026-10-14

**Verdict: NO from all three, again.** The head verdict did not move, and the reason is the most useful thing
this panel produced: **the same class of defect was on more pages than round 1 found, and one of them was in the
service rather than the page.**

### What round 2 found that round 1 had not

**The data boundary guarded only escalations.** `routing.route()` consulted the gate when a trigger wanted the
frontier lane, and returned the default lane untouched when none fired. On a deployment whose ordinary model is
remote, a request whose class the manifest did not permit **went to the provider anyway**, with
`escalated: false` and no notice. The page promised a boundary and the code drew one around a door nobody had to
use. Fixed: the gate is consulted on every request that would reach a remote model, and a refused class returns
`lane: none`.

**A refusal that was not one.** `lane: 'none'` fell through the model guard, because the guard only handled the
`local` case, so the remote model answered while the notice claimed no answer could be produced. That is the
worst combination available: a refusal that is not a refusal, with a message saying it was. Found by reading the
guard and asking what the other value did.

**The model-permission drill proved nothing, and that was my defect from hours earlier.** `keys --probe
--as-model reader` minted the assertion for *the model being requested*, so the gateway resolved that role's
team and permitted it: 200 for every combination. The drill is the one documented way to prove the model ACL and
it had been inverted. Fixed: the assertion names the role, and the drill now returns the 403 the page always
documented.

**The emergency brake could not run at all.** `keys --set-budget` needs a database, the keys service had none,
and the tool's own advice pointed at `rendered/env`, where the operator already was. Fixed with a `DATABASE_URL`
in the service.

### The documentation class, which was live on five pages

| Finding | Fix |
|---|---|
| The `ps` sample showed six services and no API, three paragraphs under a caption saying seven with the API | the sample shows seven |
| Section 25 said the API was built in a table and did not exist twelve lines later | the prose says it exists and names what is missing: a user interface |
| Three more pages said C3 would add the API | corrected |
| Section 19 said adding an access root needs a code change; section 13d said it is a manifest setting | **13d was right** and 19 was wrong: the permit's roots are the permission and the code map is only a fallback for a caller with no permit |
| Section 16 named the wrong three scripts as answering `--help`, proved backwards by running it | the right three are named, with the failures shown |
| A migration count of fifteen, with a paragraph insisting sixteen was never the count | sixteen, and the paragraph now says why a count in a manual goes stale |
| 13g's field table called `model` the model that answered | it is the role; `resolved_model` is the model, and 13g now says so |
| The record's model column, the contents list, the endpoint heading, the chapter list | corrected |
| `trigger_rules.py` died with `ModuleNotFoundError` when run as the page implies | it resolves its own paths and states what it needs |

### One thing deliberately not fixed, and disclosed in the page instead

**The request-side redaction module is not wired into the API.** `services/egress/egress.py` is imported by
nothing but its own tests, and `_generate()` posts the raw question and raw passages. The stream-side redaction
is in the API and runs on every frame; the request-side one is a module the acceptance probe exercises directly.
**That is now stated in section 13e rather than implied away**, because a boundary that covers less than a reader
assumes is worse than one whose shape they know. Wiring it in is real work and belongs to the next phase.

### What round 2 confirmed as already correct

`api_probe` 7 of 7, `stream_probe` 4 of 4 alone, `interface_probe` 7 of 7, `access_probe` 4 of 4, `audit`
migrate, list and verify, the `audit_feed` view, `keys --provision-teams`, `session mint`, the `/ask` field set,
the thresholds 1.5/5/30 and 20/30/180, `num_retries: 0`, and the holdback with its boundary walk-back.

**Round 3 re-tests from the manual**, including everything above.

---

## C3 panel, round 3 — 2026-10-14, the final round under the three-round cap

### The verdicts, and they disagreed

| Reviewer | Verdict | What it means |
|---|---|---|
| **cold reader** | **YES** | *A competent stranger can run this from the documentation alone.* The API is real, running and answering; the contradiction class is substantively gone |
| **fidelity checker** | **NO** | Two documented drills behaved differently from the page, and the manual contradicted its own disclosure |
| **executor** | **YES** | *A competent stranger can run this deployment from the manual alone.* Every command the manual names, it ran; nothing blocked a task. Its first measurements were contaminated by a runaway container of mine and it withdrew them after a clean re-run |

**Two of three returned YES, and the third's findings were all fixed afterwards.** That is the panel's outcome, and it is stated with the caveat below rather than as a clean sweep.

**The disagreement is the honest result and it is worth stating plainly.** The cold reader answers *can a stranger run this*, and after three rounds it says yes. The fidelity checker answers *does the page match the code*, and it found the page still ahead of the code in two operational drills. Both are right about different questions.

### The executor's round-3 findings, fixed after it reported

**The boundary fix had no rehearsal in the manual.** `gate_check.py` proves it, and the manual named every other
probe it relies on and not this one — so the sentence in 13e was a claim a reader could not check from the page.
It is named there now, with the note that it exists because the sentence was false when it was written.

**Three commands ran from the wrong machine.** Section 14's `s3api list-object-versions`, section 18's
`iam get-policy-version` and the row in section 22 all present themselves as host commands with a workstation
profile; the box has no AWS CLI and fails with `aws: command not found`, which reads like a broken deployment
rather than a command in the wrong place. All three say where they run now, and section 22 carries the note once.

**The `ps` sample finally shows the api row.** The caption was corrected to seven services two rounds ago and the
sample below it kept listing six, because the replacement silently failed to match: the sample uses a single
Unicode ellipsis character and the pattern used three dots. **A silent no-op edit is worse than a wrong one**,
and it took a reviewer reading the sample to find it.

### What round 3 found, and what was fixed

**The emergency brake drilled wrong in both directions.** `keys --set-budget` writes the team row and the gateway caches that row, so the cap is not in force until a restart — the page showed it biting immediately. Worse, the documented release (`oi run --rm keys`) rewrites the *virtual keys*, which is not the row the assertion path enforces: an operator could cap a role and then be unable to release it with the command the page gave them. Both directions touch the team row now, and the page shows the restart and the release it takes.

**A drill that silently did nothing.** Section 13h offered lowering a team's `tpm_limit` as a way to provoke a refusal through our own layer. Tested at last: the calls keep answering. The identity carries the limit and **whether the gateway enforces it on that path is unproven**, which is now what the page says instead of stating the opposite as fact.

**A summary that promised a control its own detail denied.** Section 25 said the system redacts before anything leaves for a frontier model; section 13e discloses that the request-side redactor is not wired into the API. The code agrees with 13e. Section 25 now says what actually protects the boundary, which is the gate.

**Two latent code defects, neither visible on this deployment.**

The degraded **audit row** hardcoded `lane="frontier"` and `data_class="internal"` on the streaming path, so a local-lane deployment would have written a false residency claim while the response payload said local. Fixed: the record carries the lane that answered and the class the deployment declared.

The **lane was a deployment-wide fact**. `local_lane_available()` answers whether *any* role has a local model, and the routing used that as the default lane for every request. On a hybrid manifest where the analyst role is frontier-only and other roles are local, a plain request went to a remote model with the gate never consulted and the record saying `local`. No shipped manifest has that shape, which is why it was latent. The lane now follows the model the role actually points at.

### The three-round cap

**Round 3 was the third round, which is the cap.** The rule for stopping is a round that returns only cosmetics, and this one did not: it returned two operational defects and two latent code defects, all now fixed and verified.

**What that leaves is a re-test that the cap does not allow.** The cold reader's YES stands on the manual as it was when it read it, and every finding from the other two reviewers has since been fixed and verified by hand — the brake drill end to end, the throttle path, the four probes on a quiet box, 602 unit tests. **No fourth round re-read the manual after those fixes**, and saying the panel passed would be claiming a verification that did not happen.

### The state at the close of round 3

    602 passed
    6/6 manifests valid; 8 packs known
    api_probe          PASSED: 7 right(s)
    stream_probe       PASSED: 4 right(s)
    interface_probe    PASSED: 7 right(s)
    access_probe       PASSED: 4 right(s)
    gate_check         permitted class answers; a forbidden one is refused with lane='none'
    7 services running

### The runaway container, and whose fault it was

The executor's round-3 measurements were contaminated by a `docker run` burst of **mine**, left behind by an aborted probe run and still driving roughly 1,500 spend rows a minute twelve minutes later. It throttled the provider, so every provider-dependent probe failed in the false-failure mode the panel brief warned about.

The executor did the right thing: it did not kill a container outside its authorised set, it identified the cause, and it offered both options. **The lesson is mine.** A probe that bursts a shared provider should clean up after itself, and an aborted run leaves more than a mess — it leaves a deployment that lies to the next reviewer.

### Fixed after round 2 reported, from the same round's evidence

**A regression in the degraded record, caught by the round-2 executor's last measurement.** Rows written after
the retry work read `reason: 'unclassified'` with an empty `errors` list: the provider error was being swallowed
again. Two causes, both mine and both in the same function.

The loop's fall-through returned a **two-tuple where the caller unpacked three**, and it fell back to a bare
`unclassified` when the loop broke before recording anything — so a real throttle could still be recorded as an
unrecognised failure, which is precisely the defect the retry fix had removed one layer down. It now returns the
three values the caller expects, and never discards a classification it already made. When no attempt was made
at all, it says `timeout`, which is true, rather than `unclassified`, which reads as a failure nobody understands.

The handler then built the audit row with **`errors=[]` and `lane="frontier"` hardcoded**, so the attempts the
loop had carefully collected were thrown away at the last step and a local-lane deployment would have written a
false residency claim. `_generate` hands its attempts back now, and the row carries them and the lane that
answered.

Verified by provoking a real throttle afterwards:

    reason    | attempts |   lane   | errors
    ---------- | -------- | -------- | ------
     throttled | 3        | frontier | [{"reason": "throttled", "status": 429, "attempt": 1, "matched": "throttling"...

**The lesson is the one this phase keeps repeating.** The retry fix was correct and I verified it by reading it.
The record still lost the data, one function away, and only a measurement of what the record actually contained
found it.

### Documentation corrected from the same round

Section 9 counted six services and two healthy; the box has seven and three without a healthcheck. The corpus
point count was a recorded output from a smaller corpus. Section 18 gained the note that the next documented
command fails with `Connection refused` for tens of seconds after a restart, which reads like a configuration
problem rather than an impatient one.




---

## C3 panel, verification pass — round 1 of 3, started 2026-10-14

**Why a second pass rather than closing the register.** The first panel closed with two of three reviewers passing
and the third's findings fixed but not re-read. The choice was to close the five C3 items on that evidence, or to
buy one more look. **A fresh round 1 is worth more than a fourth round of the old panel**: the later rounds were
re-tests against my own fixes, and every one of those fixes has been hand-verified. What proves the manual sound
is a reviewer arriving with no memory of the previous findings and finding nothing structural.

**The stopping rule is unchanged and it is the honest one: stop when a round returns only cosmetics.** Three
previous rounds each returned something substantive, and a 4,400-line manual reviewed by three agents will always
yield *something*. So the reviewers were told plainly that a clean round is a real result, that they should not
manufacture a finding to seem useful, and that **a reviewer who always finds something is a reviewer whose
findings stop being read.**

**Two changes to how the panel was run, both from what went wrong last time.**

The box is on `main` and in sync, confirmed before launch. The previous executor round tested a tree that was 78
commits behind and said so in its report, which is a waste of a reviewer.

**Reviewers must clean up after themselves.** A burst container of mine ran for twelve minutes, throttled the
provider, and made the next reviewer's measurements fail in a way that looked like a product defect. The executor
is now told to use `docker run --rm`, to stop anything long-lived, and to say in its report what it started and
that it stopped it.

**State at launch**, so anything a reviewer breaks is distinguishable from what was already there:

    main at f0cbf38, in sync with origin, working tree clean
    a plain question answers: lane='frontier', escalated=False, kind=None
    no stray containers
    provider spend in the preceding minute: 0 rows

**What round 1 found.** Two of three passed and one found the worst defect of the whole phase.

| Reviewer | Verdict |
|---|---|
| **cold reader** | **NO** — *"the second of the two documented API endpoints returns a Social Security number to the caller in plaintext"* |
| **fidelity checker** | **YES** — *"every operational path I followed from the page ran and produced the documented shape"*, with description and stale-number defects |
| **executor** | interim, everything it reached passing |

### The defect that decided the round, and it was mine

**`POST /ask` returned the PII canary unredacted.** The streaming endpoint redacted from the start; the JSON endpoint
never did. So the same question returned this:

    POST /ask         -> "...the Acme engagement is 219-09-9999, as indicated in the first passage [1]."
    POST /ask/stream  -> REDACTED-SSN

**The canary fixture says in its own first line** that if the value ever appears in a response, the deployment is
not safe to show a client. It appeared in a response from the documented API, and 13g tells the reader that
streaming exists *because* it redacts, listing both endpoints as equals. A reader would believe redaction was a
property of the API.

**How it happened is worth recording.** The streaming path was built with a redactor because a value can reach a
browser mid-token and cannot be recalled. The JSON path was built earlier, returns one finished document, and
nobody asked where its redaction was — **including me, for four rounds, while writing probes that tested the
streaming path's redaction and never the other one's.** The panel found it by asking the question over the
endpoint nobody had tested and reading the answer.

Fixed: the answer is redacted before anything downstream sees it, the citation checks run against the text the
client actually receives, and the response carries a `redactions` field so a client can tell. Four structural
tests assert it against the source, so it cannot come back quietly.

### The other half of that finding: the record could not say whether egress was permitted

13e promises that the data class, the egress verdict and the redaction counts "have columns of their own". They
have had the columns all along and **the audit tool has had the flags all along**: the API never passed them.
Twelve of 7,691 model-call rows carried `egress_allowed`; the twelve were the acceptance probe's own writes.

    id    | stage      | data_class | egress_allowed | redaction_count | has_hash
    ------+------------+------------+----------------+-----------------+---------
    7814  | ask        | internal   | t              | 1               | t        <- after
    7812  | ask        | internal   |                | 0               | f        <- before

**A control whose evidence is a blank column is a control an auditor cannot check**, and this is the second time
in this phase that a declared control turned out to have no enforcer on the path that mattered.

### What else round 1 found, all fixed

| Finding | Fix |
|---|---|
| 13e's boundary-probe command said `run ...`, a shell function defined 600 lines later. `run: command not found` | the whole `docker run` invocation is spelled out, with a note on why an alias from another section is not a command |
| The holdback was described as a fixed 64-character window | it is a walk-back from that window, and the description now says which part is the guarantee |
| "The longest pattern is a private-key header at 36 characters" | it is the phone pattern at 59; the page now gives the measured list and says `longest_pattern()` computes it |
| "if the class is not permitted then no trigger is even consulted" | the trigger is evaluated first and the gate decides; a refused trigger is announced, not silent |
| The migration count said 15 eleven lines before it said 16 | 16 |
| The renderer "writes four files" and section 22 listed five | six, named, in both places |
| 13a's corpus counts said 1131 while the same section's own output said 1261 | 1261, keeping the dated historical run at its recorded figure |

**Two findings were recorded rather than fixed**, because both are disclosures that are already accurate: the
request-side redaction module is not wired into the API, and the rate-limit drill does not produce a refusal.

---

## C3 panel, verification pass — round 2, 2026-10-14

**Verdicts: two YES and one NO.** The cold reader and the executor both closed at yes. The fidelity checker found
the worst defect of the phase and was right to hold the line.

| Reviewer | Verdict | Deciding finding |
|---|---|---|
| cold reader | **YES** | the canary was redacted on both paths and the record carried the verdict; verified by running, not reading |
| executor | **YES** | about forty documented commands ran verbatim; every acceptance probe passed on the current build |
| fidelity checker | **NO** | **`/ask/stream` had no egress gate at all** |

### The boundary bypass, and it is the same shape as the last one

**`/ask` had a `lane == "none"` guard and `/ask/stream` did not.** The streaming handler computed the routing
decision and never read it again except to pass the lane into a frame. A class the manifest forbade was refused
with a 403 on one endpoint and **answered by the remote model on the other**, while the audit row recorded
`egress_allowed: false` — a false record of a call that left the building.

    under a manifest whose class may not leave:
      JSON   /ask         -> 403 no_permitted_lane
      STREAM /ask/stream  -> 200, full answer from the remote model
      audit row           -> egress_allowed = false

**This is the redaction defect again, one layer over.** A control implemented on the path somebody was looking
at, absent on its sibling. The redaction was missing on the JSON path while the stream redacted; the gate was
missing on the stream while the JSON refused. **Neither was visible to a probe, because the probes tested the
path they were written for.** The reviewer found both by taking the same request to the other endpoint.

Fixed: the stream refuses before the response starts, which is the one moment a stream can still say no, and
records `data_class` and `egress_allowed` when it does. Verified by reproducing the check — and the first
attempt was invalid, because the temp API fell back to the current manifest and both endpoints would have
passed for the wrong reason.

    after the fix, with the temp API confirmed to see data_class='regulated':
      JSON   -> 403      STREAM -> 403
      rows   -> 7895-7898  access denied  data_class=regulated  egress_allowed=f

### Also found and fixed

| Finding | Fix |
|---|---|
| The recorded prompt hash was of the **raw** question, so it could confirm a guess about a value that had been redacted | the API passes the redacted question, and the record hashes what it is given |
| The refusal path never passed its data class to the column, so the row said `internal` while the payload said `regulated` | it passes both, and the verdict |
| 13a said `healthy` is what to look for after a corpus load, with a dead-lettered document proving otherwise | the page says healthy is not evidence the corpus loaded, and to count with `--status` **and** `--failures` |
| 13d's session commands used subjects this deployment does not declare, so copy-pasting failed | the commands use a declared subject, and the page says why the illustrative manifest differs |
| The corpus table's rows summed to 1,131 against a stated total of 1,261 | the total explains that the table names only the declared twelve, and the box has forty |
| 13h's heading said the gate runs first while its body said the trigger is evaluated first | the heading says the gate decides, which is what the code and the prose now say |
| 13g named `OI_MODEL_<ROLE>` where the variable is keyed by API role and renders once | corrected, with the reason a reader would not find `OI_MODEL_SUMMARIZER` |
| Section 16 printed a manifest that omitted the access registry and said `redaction: false` where the file says `true`, plus a validator warning this deployment does not print | the sample is the file the box runs, and the warning has its own labelled example |
| Section 23's environment table omitted the eleven budget and stream variables 13h calls environment variables | all eleven listed with their defaults |
| A workstation note said three commands when it lists four | four |

**And the cold reader retracted one.** It reported in round 1 that 13b's `ModuleNotFoundError` demonstration did
not reproduce; on re-running it, it does, exactly as the page says. **The page was right and the reviewer was
not** — worth recording, because a panel whose findings are never wrong is a panel nobody should trust.

### State at the close of round 2

    611 passed
    6/6 manifests valid; 8 packs known
    api_probe 7/7   stream_probe 4/4   interface_probe 7/7   access_probe 4/4
    the canary redacted on /ask and /ask/stream
    a forbidden class refused on both, with the class and the verdict in the record
    7 services, no stray containers
### Round 2 goes back to the cold reader

Its NO rested entirely on the canary leak, which is fixed and verified — `POST /ask` now returns
`[REDACTED-SSN]`, and the record carries the verdict. **Round 2 asks it to re-run that exact question and see
what happens now**, and to re-walk the pages it found the other defects on.

---

## C3 panel, verification pass — round 3, 2026-10-14, the final round

**All three verdicts: YES.**

| Reviewer | Verdict |
|---|---|
| cold reader | **YES** — *"the stream path now refuses a forbidden class with the same 403 and the same wording as the JSON path, and the refusal is recorded"* |
| executor | **YES** — *"about forty documented commands ran verbatim; the only things I found this round are cosmetic"* |
| fidelity checker | **YES** — *"with that one table row added, I would have nothing left to report"* |

### The finding that mattered, and it was about my own probe

The cold reader found that **`gate_check.py` never touched `/ask/stream`.** It called `routing.route()` in
process, posted once to `/ask`, and printed the same three lines whatever the service did. **It would have
printed them with the streaming guard removed.** So the command the manual tells readers to run *"to check the
claim rather than take it"* could not have caught the bypass it was written for, and neither could it have caught
the redaction leak.

**That is the phase's real lesson, said precisely: the probes were written per feature and the defects were per
path.** Redaction existed on the stream and not the JSON path. The gate existed on the JSON path and not the
stream. Each probe passed because each tested the endpoint its author had in mind.

### The probe is rebuilt, and it has been proved able to fail

`tests/fixtures/gate_check_run.sh` builds a manifest copy with a class that may not leave, starts a second API
container on it, **waits until that container confirms it loaded the copy**, and runs a probe that asks both
endpoints. It fails if one refuses and the other answers, and it fails if the refusal does not name the class, so
a container that quietly fell back to the real manifest fails rather than passing for the wrong reason.

**The proof it works, run on the box:**

    guard present  -> /ask 403 · /ask/stream 403 · permitted path 200   PASSED, exit 0
    guard removed  -> /ask 403 · /ask/stream 200 "event: start"        FAILED, exit 1
                     "the boundary holds on one endpoint and not on /ask/stream"
    guard restored -> PASSED, exit 0

**A probe that fails when the control is removed and passes when it is there.** That is what this one should
have been from the start, and the reviewer who noticed it was the one who had already been caught by the same
blindness.

### Also fixed in round 3

| Finding | Fix |
|---|---|
| The endpoint table promised a stream is "answered as Server-Sent Events" and nothing said a refusal is a JSON 403 before the first event | the table says so, and why a client handling only `error` and `message` will not see it |
| The answer-field table omitted `redactions` | listed, with why it exists |
| A stray `|'s own count |` fragment in the corpus total row, from my own edit | removed |
| 13h's rationale closed by arguing from the order the new heading abandoned | it argues from the verdict now |
| Two stale paragraphs in 13e describing the old bare-probe invocation and its output | the section says only what is true of the runner |

### What round 3 cost, and one thing that went wrong

**The regression test timed out mid-run and left the streaming guard disabled on the box**, with the API rebuilt
from the disabled source. The next reviewer would have measured a deployment whose boundary was open. It was
caught because the executor reported that the tree had uncommitted changes it had not made, which prompted a
check. **A test that disables a control must restore it even when the test is killed**, and this one did not.

Two other things went wrong while rebuilding the probe, both recorded because they are the kind that waste an
afternoon: the second container was given the repo at `/w` when the API mounts only `rendered/` at `/app/rendered`,
and it was started without `DATABASE_URL`, which lives in the compose service rather than `rendered/env`, so every
request came back `401 no_session` and looked like a session bug.

### State at the close of the verification pass

    611 passed
    6/6 manifests valid; 8 packs known
    api_probe 7/7   stream_probe 4/4   interface_probe 7/7   access_probe 4/4
    the boundary probe passes with the guard present and fails with it removed
    the canary redacted on /ask and /ask/stream
    7 services, no probe containers left behind

**The five C3 register items have their evidence.** Whether to close them is a decision above this log.

---

# C4 panel — round 1, 2026-10-14

**Three reviewers, each asked a different question, each re-testing from the manual rather than from a diff.**
Every one was told plainly that a clean round is a real result and that a finding manufactured to look useful is
worse than none, because a reviewer who always finds something is a reviewer whose findings stop being read.

| Reviewer | What it did | The question it answered |
|---|---|---|
| **executor** | used the app from the manual and the operator guide | does it work as written |
| **cold reader** | the manual and the app, **never the source** | would a stranger understand it |
| **fidelity checker** | the manual's claims against the source and the running system | does the page describe what the code does |

**What was different about this round.** C4 has a screen, so the panel was given a running app rather than a set
of probes: the deployed container, reachable through a port forward, with a session token that works. The cold
reader was told not to open a `.py` or `.ts` file, and that opening one to understand a word in the manual was
itself the finding.

**What the round was given**, so a later reader can tell what was on the table:

- the app, deployed and running, at 1366x768
- `docs/manual.md` section 13i and `docs/operator-guide.md` stage 13i
- `docs/runbook.md` phase C4, with the probe outputs pasted from real runs
- a corpus of 26 documents the deployment actually answers from, not the six benchmark files that were there


## Round 1, what each reviewer reported

**The executor failed to finish and left no report.** Re-run after the fixes below, because a panel of two is not
the panel this phase is supposed to have.

**The cold reader returned `NO` with thirteen comprehension failures.** It used the app as its own instructions
allow and never opened a source file, which is the point of the role. The two that mattered most:

- **The receipt contradicted the answer above it.** "Answered from 6 documents on this machine" printed under
  "I cannot answer the question based on the given passages". The count was the retrieved set, which is six
  whatever the question. The line manual 13i calls the point of the product was describing the search.
- **The session token was printed in the audit payload**, and the reader asked whether it was a credential. It
  was. That is the finding of the phase and it has its own section below.

The rest were real and are fixed: sources listed the retrieved set with duplicates while the receipt claimed the
cited set; the access label was hover-only on a product whose front desk has a tablet; markdown asterisks reached
the screen on the streaming path and not the JSON one; an expired session made the shell claim the deployment
declares no tasks while the header said all systems ready; all four tasks asked for "the two versions of the
travel policy"; the signed-in person was labelled "Operator"; and a machine token appeared after every
unbacked sentence.

**The fidelity checker returned `NO` with six wrong claims**, all documentation except one. It also caught the
deployment itself: the api image was a commit stale, which nothing else had noticed because every test ran
against the source.

### The finding that decided the round, and it was a live credential in a read-only record

**A session token was readable in the audit payload, and one person's token was readable on another person's
page.** Confirmed rather than assumed: a session id taken from a row authenticated as that session, HTTP 200.

The cause was a conflation. `mint` generated one random string and used it as both the session identifier and
the credential, so every place that recorded which session did something recorded something that authenticated.
Two columns held it in plain: `sessions.session_id`, the lookup key, and `jobs.session_id`, the credential the
caller presented, kept so a job could run after its request ended.

**The rows were not rewritten.** They are append-only and hash-linked, and a record that can be edited to hide
something is not evidence. The exposure is recorded here instead. Migrations 019 and 020 separate the two, store
only a SHA-256, revoke the sessions whose credentials are already in the chain, and truncate the job rows that
held one.

**Two things this says about the panel that found it.** A reviewer whose instructions were to read the manual
and use the app, and who was told not to open the source, found a credential disclosure that 715 passing tests
and three probes did not. And it found it by asking a question — "is this value a credential?" — rather than by
knowing where to look.

## What the round cost, three times over

**Three edits of mine did not match the text they were replacing, and each one reported nothing.** A
replacement that finds nothing looks exactly like a replacement that worked. The third was
`resolve_token`, whose hashing I had written, committed, pushed, and rebuilt an image for while the line still
resolved the raw token. What found it was a fresh token authenticating as nothing against the live service,
which read as the security fix being broken rather than as an edit that missed. `docs/runbook.md` already warns
about this shape; it had not been applied to editing.

The guard is a test that reads the line, and a habit of reading a file back after a scripted change rather than
trusting the call.

**And one NameError on the refusal path.** `check_boundary` was given a `request_id` parameter and its body used
the name; the test written for it asserted the string appeared in the source, and it appeared in `prepare`,
which passed it to nothing. The boundary probe found it by reporting that the service was not enforcing the
class it had been given, which looked like a setup problem. The replacement calls the function instead of
reading it.

## State at the close of round 1

    727 passed, 2 skipped                       (was 715)
    job_probe 7/7 + 1 skipped, 8/8 with the gate runner
    activity_probe 8/8
    api_probe 7/7
    gate_check 0 (refused on both endpoints) and gate_check --jobs 8/8
    a session id from an audit row: HTTP 401
    a credential minted after the fix: HTTP 200
    app: 200 for / and for /api/health, 57 app tests, 12 integration, 10 browser
    contract check: OK, 9 paths

**What round 1 did not prove.** An undeclared subject refused at mint time, the `degraded` and `no_source`
screens, Pause, Resume and Cancel on a running job, and a task refused for having no permitted lane. The cold
reader named all four as untested and made no claim about them, which is the right way to leave a gap.

---

# C4 panel — round 2, 2026-10-14

**What triggered it.** The fidelity checker's six findings were fixed, and fixing them meant running every
documented command for the first time in one sitting. That is when the pattern showed: **four documented
commands could not work as written**, and each had been copied into more than one document.

**The four, and they are one defect wearing four hats.** A long invocation copied between documents, where one
clause is wrong and nothing runs it.

| The command | What happened when it was run as printed |
|---|---|
| `check-contract.sh --live http://api:8088` | name-resolution traceback: `api` resolves inside the network, not on the host |
| the probe `docker run` line | `access_probe` reported "the refusal could not be recorded", because the session image has no `/app/audit.py` — the compose service mounts it |
| the same line, in the manual | mounted the repo at `/w` for some probes and not others, so one died on `ModuleNotFoundError` |
| the API base as `/api` | every call 404s; the failure stayed hidden because the deployed image predated the change |

**The fix is one script and twenty-two replacements.** `bash scripts/probe.sh <probe.py>` takes the compose
service the deployment uses, so the mounts and variables come from one place and a probe cannot drift from the
service it tests. It is shorter than the line it replaced, which is the only kind of fix that survives being
copied.

### Running all six probes found three defects that reading them had not

**`interface_probe`'s TRIGGERS right was testing a task that stopped existing.** It sent `reconcile`, a name
hardcoded in the API until C4 moved the task list into the manifest. The manifest declares four others. The
right kept passing after the move, because its assertion was that a trigger named itself and nothing checked
the task was real. It failed honestly once the router began refusing an undeclared task. It reads `GET /tasks`
now and sends the first id the deployment declares.

**Its STREAM right counted frames.** `frames > 3` is a statement about how the model chunked its answer, and a
short reply arrives in three. The right exists to prove the answer arrives in pieces with the first before the
last, so it asserts that. The count and the arrival times are `stream_probe`'s INCREMENTAL right, which
measures them properly.

**`stream_probe`'s canary named a document that had been deleted.** The corpus was replaced this week with a
generated one and `canary-pii.md` went with it. The right reported that it was proving nothing, which is the
honest failure and also why a canary should never be a document name. It matches reserved ranges now, and the
question pushes the model to repeat the value verbatim — measured, not assumed, because asking *what* the
reference is produces a paraphrase and the check then passes while measuring nothing.

### What this round says about the phase

**Every one of these was found by executing a documented command rather than by reading it.** The panel's whole
premise is that a page is a claim and a run is a measurement, and this round is the strongest evidence for it
that the project has: four commands that could not work, three probe defects, and a credential disclosure, none
of which any test caught because no test ran the thing a person would run.

### State at the close of round 2

    api_probe 7/7          access_probe 4/4        activity_probe 8/8
    job_probe 7/7 + 1 skipped, 8/8 with the gate runner
    interface_probe 7/7    stream_probe 4/4
    gate_check exit 0      gate_check --jobs exit 0
    contract check: OK, 9 paths
    727 passed, 2 skipped

**Still not tested, and named so nobody assumes otherwise:** the `degraded` and `no_source` screens rendered in
the app, Pause, Resume and Cancel on a running job, a task refused for having no permitted lane, and an
undeclared subject refused at mint time. The executor re-run was asked to start with those.

---

# C4 panel — round 3, 2026-10-14, the executor re-run

**The executor's first run failed and left no report.** It was re-run after rounds 1 and 2, with the brief telling
it to work in small steps and send a partial report rather than none. It delivered, and it did the two things the
brief asked for: it started with the four areas nobody had covered, and it re-tested every documented step rather
than trusting the changelog.

**Verdict: `NO`, six disagreements.** Five were pages that had not moved when the thing they describe did. One
was a product decision.

### The four uncovered areas, three of them now covered

| Area | Result |
|---|---|
| an undeclared subject refused at mint | **verified** — names the subject and lists the two it knows; an unknown role lists the one it knows |
| a task refused for having no permitted lane | **verified** — `403 no_permitted_lane` on `/ask` **and** `/jobs`, and **no job row was created** |
| the `degraded` screen | **not reachable** — 40 concurrent requests, a 204 KB question and a minute-long stream produced zero degraded frames |
| the `no_source` screen | **not reachable** — the retriever has no score threshold, so three unrelated questions all returned ordinary answers in which the model declined in prose |

**What that leaves, stated rather than glossed.** Both shapes have component tests against fixed payloads, so
**their rendering is verified and their live trigger is not.** The manual now says exactly that, in the section
that lists them, instead of implying both have been seen.

### The five stale pages

**The tunnel connected to nothing.** Manual 13i told a reader to forward SSM to instance port 8080. The app
publishes no host port, by design, so the tunnel attached to nothing and the browser reported a dead connection.
Verified both halves afterwards: host port 8090 answers 200 through the relay, and `docker port app` prints
nothing. The relay is now described as review scaffolding with the command to remove it, because a published port
is the thing this deployment otherwise does not do.

**Three quotes had gone stale.** The Activity scope sentence, the `LEFT THE BUILDING` tag that renders as `sent to
the model`, and the receipt example, which said "Answered from 2 documents" where the live line reads "Answered
from 4 documents of 6 read". Each was a deliberate rewording whose reasoning is in the code comment beside it,
and the pages were not moved with the code.

**And Pause, Resume and Cancel cannot appear on this build.** The manual stated them as a present feature. A C4
job runs one step inside the request that started it, so the window is milliseconds: the executor drove a
nine-second job, polled every 300 milliseconds, and saw them zero times. The manual says so now, and says the API
actions work — a terminal job answers `409` naming the state — and that they are the seam C6 plugs into.

### The one where the product needed a decision

**The PII test was intermittent by construction.** It asserted that asking the model to repeat a reserved
identifier produces redaction counts, and the model is not obliged to repeat it. Twelve runs: ten redactions, and
twice a safety refusal or a bare `[1]`, so nothing reached the redactor and the assertion failed. The executor
measured the rate rather than reporting a single failure, which is what made the cause visible.

**Two claims were conflated and are now separate.** The control — no reserved identifier reaches the client — is
asserted on every attempt and holds unconditionally. The evidence — that the redactor fired — is sought over four
attempts, and a run where the model never echoes fails with a message naming the model. The redactor's own
correctness is twenty-four unit tests against fixed input, because **a check whose subject is a model's choice
measures the model**. Three consecutive clean runs after the change.

### Also found by the executor, and fixed

**A 204 KB question was an unhandled `500`.** The traceback was `OSError: [Errno 7] Argument list too long`: the
prompt travelled to the audit tool as a command-line argument, so **the request that most needed a record was the
one that could not have one**. Three faults, three fixes: the prompt goes on stdin; an oversized question is
refused with `413 question_too_long` before the session resolves, because a size refusal is not a permission
refusal; and a record that cannot be written now raises instead of printing to stderr and answering anyway.

### What round 3 says about the rounds before it

**Every finding in this round was in a document, and every one was found by running the document.** Two panels
had read those pages and passed them. Neither had executed them in sequence, which is the only way a tunnel to a
port nobody listens on, a test count that moved, and controls that cannot render are visible.

### The lane agreement check the phase's plan named and nobody had written

**Found while closing this round, not by a reviewer.** The plan lists "the lane/audit agreement assertion" as one
of the ladder's rungs and it did not exist. `FACTS` proved that audit rows carry a lane; nothing compared a row
to the answer it belongs to, so a response claiming `local` while the call went to a provider would have passed
every right in the probe. It exists now as `LANE-AGREE`, and it checks three things rather than one, because
agreement alone is satisfiable by two matching lies: the lanes match, the lane is named at all, and the recorded
egress verdict is the one that lane implies.

**Proved able to fail** by making the writer record a lane the answer does not claim: the right reported
`answer lane='local', row lane='frontier', egress_allowed=True` and the probe exited 1. Restored, rebuilt, and
the tree verified clean.

## State at the close of round 3

    api_probe 7/7          access_probe 4/4        activity_probe 8/8
    job_probe 7/7 + 1 skipped, 8/8 with the gate runner
    interface_probe 8/8 (LANE-AGREE added)         stream_probe 4/4
    gate_check exit 0      gate_check --jobs exit 0
    contract check: OK, 9 paths
    730 passed, 2 skipped  (was 727)
    app: 57 unit, 12 integration (three clean runs), 10 browser
    a 204 KB question: 413 question_too_long
    a session id from an audit row: 401

**What remains unverified, and it is one thing.** The `degraded` and `no_source` screens have never been rendered
by this deployment, because nothing here produces those shapes. Their rendering is covered by component tests and
their trigger is not. That is the honest limit of this phase's evidence.

---

# C4 panel — round 4, 2026-10-14, closing the executor's findings

**Two regressions, both introduced by the fixes for round 3, and both worth more than the fixes.**

### The gate check was passing on a deployment that could not record a refusal

Round 3 made a failed audit write raise instead of printing to stderr and carrying on. That turned
`gate_check_run.sh` red, and the traceback was the finding: **the temporary container it spins up had no audit
tool**, so every refusal it produced was unrecorded. Two mounts were missing, both of which the compose service
has — `services/audit/audit.py` and the statement verifier it looks for beside itself.

**That probe exists to prove a refusal is a control rather than a message, and it was measuring a deployment
where the record could not be written at all.** It reported `PASSED` in that state for as long as it existed.
What found it was making a silent failure loud: **the change that broke this probe is the change that proved it
was hollow.** It passes now with a working record, which is a stronger result than the one it used to report.

### The contract lagged the service by the status code I had just added

Adding a `413` to `/ask` is a contract change and the committed document did not have it, so the checker
reported nine paths whose contents differ. Regenerated from the running service rather than hand-edited, then
copied into the app's checkout, because the app builds against that copy.

**This one is ordinary and that is the point.** A response added to an endpoint and not to the contract is the
most predictable drift there is, and the project has a check for it that failed exactly as designed. Worth
recording because it is the first time in this phase that a check caught something before a reviewer did.

### State at the close of round 4

    api_probe 7/7          access_probe 4/4        activity_probe 8/8
    job_probe 7/7 + 1 skipped, 8/8 with the gate runner
    interface_probe 8/8    stream_probe 4/4
    gate_check exit 0      gate_check --jobs exit 0      (both with a working audit tool)
    contract check: OK, 9 paths, 22 models
    730 passed, 2 skipped
    app: 69 unit and component, 12 integration live, 10 browser against the deployed app
    a 204 KB question -> 413 question_too_long
    a session id taken from an audit row -> 401
    deployed: api and app running, no restarts, contract matching

**The one thing this phase never saw, stated once more so it is not mistaken for coverage:** the `degraded` and
`no_source` shapes have never been produced by this deployment. Their rendering has component tests; their
trigger has nothing. The manual says so in the section that lists them.

---

# C4, closing turn — the citation that could not be opened

**Not a panel round. Work asked for directly**, and it found the same class of defect the panels kept finding:
something documented, in place, and one step short of true.

### The finding: the product's central claim stopped at the last step

**The Ask screen has shown a chip per source since C4 and the chips were spans.** A person could see that an
answer rested on the travel policy and could not read the travel policy. There was even a comment in the code
explaining that opening the source was a real feature for a later phase, which was true and was also the reason
the product asked to be believed at the one moment it had promised not to.

`GET /documents/{doc_id}` closes it. Three properties had to be deliberate, and the first is the one that would
have gone unnoticed:

| Property | How | What it rules out |
|---|---|---|
| the label filters **in the query** | `chunk_index.acl` is in the where clause | the third path to content where a missing filter hides. Every test against a permitted document passes either way |
| every chunk is redacted on the way out | the function that redacts an answer | treating "they may read the file" as a reason to put an SSN on a shared screen |
| a forbidden document is **not found** | `404`, identical to a missing one | using the endpoint to learn that a document exists |

**Proven on the deployment, not asserted:** a company session reading an executive document gets `404` with the
same body as a document that does not exist; the document holding reserved values comes back with
`{'card': 1, 'ssn': 1}` redacted and no raw shape in any chunk; and the figure the answer quoted, `0.67`, is in
the text the read returns, which is the assertion that makes the citation evidence rather than decoration.

### The defect found while building it, and it is the phase's recurring shape

**The panel was transparent.** The first screenshot showed the page header painted over it and looked like a
stacking-order bug. Measuring before fixing said otherwise: `document.elementFromPoint` at the collision
returned the panel's own head, with no trapping ancestor anywhere up the tree, so the DOM order was right.

`background: var(--surface)` and there is no `--surface` in that stylesheet. **An undefined custom property is
not an error**: the declaration is dropped, the element has no background, and the header was showing through
the dialog rather than over it. A second typo, `--fs-body` for `--fs-base`, fell back to the inherited size and
looked fine.

**This is the same shape as a scripted edit that matches nothing**, which cost this phase three silent failures:
the code says one thing, the result is another, and nothing tells you. So the fix came with the four-line check
that would have caught it — every `var(--x)` in the stylesheet must be defined — and it was proved to fail by
putting the typo back. 27 defined, 29 used, now 27 and 27.

### Also in this turn

**The corpus generator was never missing.** It is `fixtures/generate_corpus.py`, declared in
`requirements-dev.txt`, deterministic from a fixed seed, and a fresh run produces **26 files byte-identical to
the deployment's**. The earlier claim that it could not be found was wrong: it was searched for under `scripts/`
and by corpus filename, and it lives in `fixtures/` and is named after what it does. The registry's rule about
reading files back applies to searching for them too.

**Both repositories were audited for anything dangling**, which found two things in the app repository and both
were mine: `app/tsconfig.tsbuildinfo` was **tracked** — a build cache rewritten on every typecheck, making every
diff noisy — and an empty `node_modules/.vite/vitest` tree sat at the repository root from a command run in the
wrong directory. Both were invisible to `git status`, one because it was ignored and one because it was
committed, which is the argument for auditing what is tracked and ignored rather than what is dirty.

**And the operator guide's C4 stage was numbered unlike anything else in the document.** Bare `## 1.`, `## 2.`,
`## 3.` appear nowhere else; the guide uses `## Na.` for stages 4 to 15 and `## Nx.N.` for the two stages
inserted into a sequence, 13h and 14b. Stage 13i uses `## 13i.N.` now, matching its neighbours, and the runbook
had two unnumbered subsections that are numbered too. The manual was already consistent, which was checked
rather than assumed.

## State at the close

    730 passed, 2 skipped
    api_probe 8/8 (DOCUMENT added)     access_probe 4/4     activity_probe 8/8
    job_probe 7/7 + 1 skipped         interface_probe 8/8  stream_probe 4/4
    contract: 10 paths, 22 models, matching
    app: 65 unit and component, 12 integration live, 10 browser

---

# C4, closing — the ingest, measured, and what it cost to find out

**Not a panel round. A question the owner asked**: how long does a large corpus take, can it be made faster,
and is a second machine or a GPU needed. It ended with a 1.75x speed-up from a code change, a decision not to
buy hardware, and two defects — one of which I caused.

## The measurements, in the order they were taken

**The corpus.** 10,000 generated documents alongside the 26 coverage fixtures. Generation: 21.8 seconds for
40 MB. Ingestion of the first 1,000: 14m21s. The rest, serially: about 2h48m for the whole 10,026.

**The split, from the instrumented worker.** Parsing 44 to 47 percent, embedding 50 to 53, writing 2. And the
parser container at **one core of sixteen** — which is what turned the problem from capacity into structure.

**The change.** Documents are prepared across a pool of four and written on the main thread. Writes are two
percent of the clock and are what touches shared state, so serialising them is free and keeps the manifest out
of a race.

**The result.** 2h48m to **1h36m**. Parsing fell from 44 percent of the clock to 6 without getting faster: it
now runs on the fifteen cores that were idle while the worker waited on the embedding service. Embedding is
93 percent, at roughly 1,430 ms per chunk against the 490 ms the service manages at a batch of eight.

**No degradation.** Per-document time across ten buckets: 2,947 ms to 3,147 ms, a drift of 1.07x. The rate
swinging between 68 and 138 documents per minute is the chunk count varying between documents. **A
300-document sample would have shown neither the flat curve nor the source of the swings**, which is why the
owner's insistence on a full run was right and my first instinct to sample was wrong.

## The two defects

**One I caused.** A measurement run pointed at 300 sample documents reconciled away the other 9,700, because
the worker removes every document the manifest knows about whose file is not under the root it is walking. The
vectors survived only because that run happened to use a separate collection — luck, not design. The repair
was a re-ingest rather than a hand-written rebuild, on the grounds that a second implementation of what the
manifest should say is the defect this codebase rejects everywhere else. The worker now refuses to remove
anything when the root is not its own.

**One that was latent and concurrency made reachable.** `embed_texts` tries several endpoints and wraps its
last failure in a `RuntimeError`, and `is_transient` classified by exception type. So a `429 Too Many Requests`
inside a wrapper was read as a permanent fault of the document. A concurrent run provoked 56 of them and
**dead-lettered four healthy documents**. The wrapper chains its cause now and the classifier walks the chain.
Worth recording for the ordering as much as the fix: the speed-up arrived with a fault only a busy machine
could show.

## The decision

**No GPU, no second machine.** Ten thousand documents in an hour and a half means a corpus ten times that size
finishes inside a working night on the box already deployed. The owner's words were that large groups will be
done overnight anyway, which is the right frame: this is a batch job, and a batch job that finishes before
morning needs no hardware. Recorded in the operator guide so it is not reopened without new numbers.

**What nobody has built, and what it is worth.** Batching embeddings across documents. Per-document requests
carry one or two chunks and run at a third of the service's batch-of-eight efficiency, so the measurement says
roughly 3x is available on the 93 percent. It is not needed for the decision above, and it is the obvious next
lever if a client's onboarding ever becomes interactive rather than overnight.

## State at the close

    13,307 chunks · 10,025 documents · 0 documents carrying a failure
    a cycle that changes nothing: seen 10,026, skipped 10,026, failed 0
    api_probe 8/8   access_probe 4/4   activity_probe 8/8
    job_probe 7/7 + 1 skipped          interface_probe 8/8   stream_probe 4/4
    gate_check exit 0                  gate_check --jobs exit 0
    contract: 10 paths, matching       744 tests

## C5 panel, round 1 — 2026-10-16

**Scope.** C5 only: `docs/runbook.md` 6.0 to 6.6, `docs/manual.md` 13j to 13m, `docs/operator-guide.md` stage
13m.0 to 13m.6. Three reviewers that did not write the work: an executor on the live box, a cold reader with no
source and no git history, and a fidelity checker reading the claims against the source.

**Verdicts. All three said no, and all three said the same thing.**

> The behaviour C5 describes works. What does not work is the documentation of how to check it.

**What the executor proved before it said no.** The whole HTTP path: `POST /jobs` reached the gate with four
step rows and a digest, `docker restart api` left the job waiting with the digest intact, a wrong digest was
refused with `409 approval_drift` and both hashes, a denial with no reason with `400 bad_decision`, an approval
quoting the digest finished the run with six steps and one row each, and `audit verify` reported the chain
intact. **It found no defect in the engine, the gate, the approval record, the intake connector or the two
screens.** Everything it found was in the pages.

**Thirty documentation findings.** The three that mattered most:

- **No runnable command started an `invoice_intake` job.** The manual gave the text to send and never said where
  to send it, so the phase's own walkthrough could not be started by a reader.
- **The operator guide contradicted the runbook.** Stage 13m.6 said the engine was not wired into the HTTP
  routes yet, while runbook 6.2 pasted it working. Same date, opposite claims.
- **A client-facing sentence was quoted wrong in three of four places.** The system prints one sentence, and
  three pages had their own version of it, each presented as the sentence.

**Where the findings landed.** Fixed on `main` in `d52152a`, before round two ran.

**Provenance, stated because it is a gap.** This entry is written from `docs/handover-c5.md` section 7, which is
the only surviving account of the round. The reviewers' own reports were not filed under `docs/reviews/`, and
the requirement in `AGENTS.md` that every round is recorded here was not met at the time. Round two is filed
properly, and so is this paragraph.

**One register correction came out of the handover itself.** It named the wrong eight open C5 rows: it said they
were the intake connector's tenant gap, and the register showed them to be the approval set. The register is
authoritative, and the handover was corrected at `7fcd3b5`.

## C5 panel, round 2 — 2026-10-16

**Scope and rules as round one**, run against `a6b0643` in this repository and `bcffdae` in `oic-ui`, on the live
deployment. The three reports are filed under `docs/reviews/`, and the merged findings table with each finding's
disposition is `docs/reviews/c5-round-2.md`.

**Verdicts. Executor no, cold reader yes with a blocking finding, fidelity checker no.** They converged: the
behaviour works and the pages' instructions for checking it did not.

**The correction round one could not execute, executed.** Round one's "start a job" command had no execution
behind it, deliberately, because running it writes to the deployment's chain. The executor ran it. Its output
matches the page line for line, including the digest `sha256:5b480b869b4bbb02f…`, which is content-addressed and
therefore identical on rerun. So C5's own walkthrough is now cleared by execution rather than by the route table,
the registry and the contract.

**Twenty-five findings, merged from three reports, and none of them a defect in the C5 behaviour.** No defect was
found in the engine, the gate, the approval record, the intake connector or the two screens. What the round found
was documentation, plus two app-repository defects in the tests that prove the documentation.

- **Four pages now say where the app rungs run.** The box has no Node toolchain, and the pages had been telling a
  reader to run `npm` in `/opt/oic-ui/app` and, eleven lines later, that the checkout is on the deployment and
  nowhere else. The three commands exit 127 there.
- **The app repository's suite is green again, at 161 passed.** `bcffdae` refreshed the contract copy and left the
  test file's hard-coded path list and its three-field allowance behind; the test's own comment said to delete the
  allowance when the fields landed. It was deleted rather than widened, and the manual's second Review rule
  (*approve is never the default*) got the test it had been claiming, proved by mutation: adding `autoFocus` to
  the control makes it fail.
- **The extraction-pattern claims were corrected in three places**, and the correction is sharper than the
  finding: an unmatched reference is not an empty field, because `extract_fields` leaves it empty and
  `draft_action` substitutes the message's own id, so the entry a person approves carries sixteen hexadecimal
  characters where an invoice number would be.
- **The Part 11 block in runbook 6.0 was wrong in two values** — `approved` where the record writes `approve`, and
  `staff` where the role is `viewer`. Both were found by reading a real row rather than the code.
- **Counts and provenance:** the queue is scoped by deployment as well as by access roots; three of fourteen chain
  tests compare the two sentences and not four; the view's fallback differs from `describe()` on multi-word input;
  two Playwright pastes are trimmed and now say so; the acceptance run's stderr line is shown and explained; the
  demo's scratch directory is removed first.
- **The api image normalizes its own file modes**, and this deployment's checkout was normalized to the index.
  `gate_check_run.sh` was dying with `PermissionError` because the image had inherited a restrictive umask from
  the working tree, and the deployed api runs as root, which hid it.

**Two findings were not fixed as code, and both say why in `docs/reviews/c5-round-2.md`.** A denied job leaves its
gate step row `running` while the job is terminal: documented in guide 13m.3 and runbook 6.2, with the difference
from cancellation named, because closing the row is a change to `jobs.decide()` that belongs beside C6's second
caller of the gate. And the deployment's `documents` collection holds zero points with `documents/` empty, so
every question answers `no_source`; that is what the boundary runner now trips over, and restoring the corpus from
S3 is a deployment decision rather than a page fix.

**Also closed while this round ran.** C6's register rows were written (25 rows, `phase: C6`), so a reader of the
register alone can now see the phase is planned.

## C5 panel, round 3 — 2026-10-16, the final round under the three-round cap

**Verdicts. Executor yes, cold reader yes with no blocking finding, fidelity checker no with five page-side
findings.** The three reports are filed under `docs/reviews/`, and the merged table is
`docs/reviews/c5-round-3.md`.

**Round two's corrections held under re-execution.** Every command in scope was run again from the pages: the
start-a-job command prints the digest the page prints, the restart-and-approve sequence finishes over HTTP with
one row per step, the feed sentence is the honest one in both databases, the probes report their rights, the
intake acceptance is `10 PASS, 0 FAIL` with the stderr line the page now shows, and the app's counts are the
counts the pages print.

**Seventeen findings, one of them code.** `services/api/answer.py` audited its `no_source` branch — and its
retrieval-error branch — without the `request_id` every other audited path on that route passes, so an answer
that said it had nothing carried an id that could not find its own row. `activity_probe.py`'s `BYREQUEST` right is
what caught it, and it now holds. The rest were page-side and are fixed: a wrong line pointer in `cord-check`'s
output, `watch-folder`'s output described as one line per file when it is one per scan, a suite count one behind
the suite, and a Microsoft retention figure that named the wrong store and the wrong number — Entra keeps 30 days
on P1/P2 and 7 on Free, and the 180-day default an operator is likely to be thinking of belongs to the Microsoft
365 Unified Audit Log in Purview. Ten more came from the cold reader, all of them small and none of them
blocking: a configuration file the intake page never said to create, a job id with no source, a test command that
hid its own skip reason without `-rs`, `oi` defined twice with a contradiction between the pages, an identity
example carrying a group where a role goes, a silent failure that is not silent, a commit check printed twice and
naming no commit, a register row named once and never explained, two `fetch` key lists that differed, and the
phrase "round two" used where a reader has no round one.

**Two items stay open, and neither is a page edit.** A denied job leaves its gate step row `running` while the job
is terminal; it is documented in guide 13m.3 and runbook 6.2, and closing the row is a change to `jobs.decide()`
that belongs beside C6's second caller of the gate. And the deployment's `documents` collection holds zero points
with `documents/` empty, which is why the boundary runner still exits 1: the corpus is archived in
`officeinference-corpus-878451098165` and `disk-ingest-documents-878451098165`, and restoring it is the owner's
call rather than a documentation fix.

**State at the close.** core and app clean and pushed; ten images current; api rebuilt; `activity_probe.py` 8
rights, `workflow_probe.py` 13, `job_probe.py` 7 with 1 skipped by box state; 896 passed and 22 skipped on the
workstation, 884 and 23 on the box with every skip named; the register at 145 items with C5 at 34 and C6 at 25.

**The cap is reached and the panel stops here.** Each round found real defects and each round's findings were
fixed and re-tested; a fourth round would be re-measuring fixes to a round that already passed.

## Retrieval chunking, defect investigation — 2026-10-16

**How it started.** Restoring a real corpus to the development deployment — `group-01` and `group-02` from the S3
store, 341 documents — and asking one question end to end.

**What it found, none of it the chunk size.** Five layers: the estimator is the wrong unit (`words × 1.35`; low by
2.8x and 3.2x on measured table samples, 15x on a 40-row CSV, and 1 token for an unbroken 756 KB run); the prose
splitter has no terminal rung; the table path emitted the whole serialised table with no size check, and the
builder confirmed with `git log -S 'fits'` that "a table that fits is never split" was written as prose and never
as code; the answer path had no cap and two prompt builders; and the verifier would have checked claims against the
full passage once trimming began, while the reranker already scored only the first 8,000 characters. The
consequence: **73 of 341 documents carried chunks past the recorded 4,096-token embedding limit and were embedded
as their first tokens**, `auto_truncate` being true, and one prompt assembled at 228,326 estimated tokens was
refused by the provider. Every API answer degraded.

Found on the way and fixed separately: the gateway auth hook handed psycopg a `DATABASE_URL` that LiteLLM had
rewritten with Prisma's pool parameters (`963a1ef`). And the two-writer ingest hazard is `RET-INGEST-002`.

**The process, because the shape worked.** Six research agents, one angle each (the evidence, production systems,
oversized elements, the answer side, this architecture, the 2025–2026 frontier); a synthesis; two adversarial
reviewers — evidence: yes with fixes; client reality: yes with changes, eleven attacks; then the agent who built
the chunker confirmed the diagnosis, answered the eight questions, and approved three repairs.

**The decision, owner, 2026-10-16.** Repairs, not a redesign. (1) The post-condition is a measurement, not a number:
every embedded text is measured with the embedding model's tokenizer and must fit
`min(contract.max_input_length, budget)` minus a margin, on every emission path. (2) The citation unit is separated
from the embedding unit: a whole table stays the citable object while bounded text is what gets embedded. (3)
`auto_truncate` goes off, or ingestion refuses to run while it is true. Plus a hard cut behind the sentence split,
the invariant as a test using the two files that broke it, `CHUNKER_VERSION` to v3, and a re-ingest into a new
collection generation. **What must not weaken:** page ranges, zero or near-zero overlap, the deterministic prefix,
atomic tables as citation units, sentence-preferred splitting.

The in-flight 29,092-point load was scrapped as a plausible-looking artifact, and the ingestion container is
stopped. `RET-CHUNK-001` is opened, because a design rule that is not a register row has no test that fails when it
stops being true. The research, the synthesis and the defect report are in `docs/research/chunking/`.


## Retrieval chunking, the repair at work — 2026-10-17

The three repairs landed and the corpus was rebuilt at chunker-v3. What the rebuild found that the tests could not
is the part worth keeping.

**A rebuild is a better test than a unit test, and it found four things.** The embedding batch was sized by chunk
count, and sixteen measured chunks could still cross the service's token cap, so the service refused the request
and the worker read that as "no endpoint answered": that alone failed 245 documents. The tokenizer batch had the
same shape but worse, because a byte budget let hundreds of short spreadsheet chunks into one request. The
character cut, the last rung of the fallback order, sized its slices from an average characters-per-token ratio and
never measured the slice, which produced 43 chunks of 513 to 578 tokens against a 512 budget on a real document
whose density changed inside the window. And the running API image still carried `chunker-v2`, so the read-side
contract check refused every search on the v3 collection, which is exactly the hazard the client-reality review
predicted; rebuilding the API fixed it.

**The fixes, in the same order:** embedding requests bounded by the measured token counts and halved on a refusal;
tokenizer requests capped by text count as well as bytes; the character cut measures each slice and shrinks it;
a 429 is retried with growing backoff instead of failing the document, because load shedding is a pace rather than
a fault; and the compose default for ingestion concurrency is two, because each request now sits near the batch
token cap and a window of four fills the queue. The deeper pacing fix stays `RET-INGEST-002`.

**Verified on the rebuilt index:** the boundary gate check passes (forbidden class refused on both endpoints, the
permitted path answers), the job probe holds eight rights, the activity probe holds eight, staff asking the
executive compensation question is denied and an executive session gets the memorandum first, no chunk over 512
measured tokens exists in the index, the 756 KB unbroken run is 789 bounded chunks, and tables that did not fit are
row groups carrying their identity and row range. `RET-CHUNK-001` is verified, the register row that would have
caught this had it existed.


## The corpus rebuild, and the verification that came with it — 2026-10-17

After the chunker repair (chunker-v3, measured post-condition, truncation off) the corpus was rebuilt from the S3
groups: 544 documents, 478 indexed by the end of the pass, 70,749 chunks, matching the Qdrant point count exactly.
The invariant was checked against the whole collection rather than a sample: zero points over 512 measured tokens;
the 756 KB unbroken run is 789 bounded chunks; 205 tables that did not fit are row groups carrying their row
ranges; 169 executive documents are indexed. The contract records chunker-v3, 8,192 maximum input, truncation off.

The rebuild found what the tests could not, again. The last 66 documents, all large, cycled between 429 and re-parse
because two documents in flight each sent a near-cap embedding request and the service shed one; that is
`RET-INGEST-002` in production, and the load now runs as a single writer (PR #154) with one 429 line instead of
dozens. Ten files dead-lettered on parser 400/413 and parser task failures; the 3.3 MB text file refused with
`HTTP 413 Payload Too Large` is the one worth a follow-up, because a text file that size should be parseable.

Verified on the rebuilt corpus: the boundary gate check passes on both endpoints with a real answer on the
permitted path, the job probe holds eight rights, the activity probe holds eight, the access canaries behave, and
the new forty-question data set retrieves its expected documents for the sample checked. `RET-CHUNK-001` stays
verified; `RET-INGEST-002` stays open with this as its evidence.

## 2026-10-18: chunker-v4 rebuilt corpus, and the before-and-after

The v3 index was frozen and snapshotted first (checksum-verified, copied to S3 with the manifest dump), the
baseline suite was run and committed (PR #159), then the giant-table policy was built (PR #160) and the corpus
rebuilt from zero: 538 documents, 100,477 chunks, six data-set cards, six dead letters, contract chunker-v4. The
dump that appeared in 15 of 52 baseline runs appears in none of the after runs, and exact-number retrieval rose
from 3 of 8 to 6 of 8.

The rebuild found two defects the tests had not. The data-set card was returned unsealed, so its chunk id was
empty and Qdrant rejected every card write; five files dead-lettered twice before that was seen and fixed in PR
#161. And the text-dump threshold at 4 MB handed a 3.4 MB dump to a parser that refuses it; the line is now 3 MB.
The rebuild took five and a half hours against my estimate of one to two; the remaining work was genuine parse
and embed of documents that had never been indexed, which no policy removes.

The answer half of the suite is not usable as a single-run total: four identical runs scored 32, 26, 31 and 29
against a baseline of 33, nineteen question-runs flip, and the three baseline passes that fail after retrieved
byte-identical passages. Where the model varies more than the change, the comparison has to be retrieval and
citations, and that is what the report concludes. `RET-TABLE-001` is verified; the probe redesign and the
multi-run majority are logged as follow-ups in `docs/reviews/after-v4.md`.

## 2026-10-18: what was actually wrong with the answers, and the fix that measured

Retrieval was not the problem: the right document was found 33 of 37 times, but the chunk holding the value only
11 of 19, and 18 of the 32 failing question-runs had the value in context and still failed. The model had no way to
look beyond the five passages it was handed. Three changes followed, each measured on the same forty questions.

The answer contract (temperature zero, value-first, numbers must exist in the evidence) changed nothing on its
own. The frontier comparison found the real wording defect: "using only the numbered passages" made both Claude
models refuse every cross-document question, 0 of 8, and Nova had been hiding it by ignoring the instruction. The
multi-passage rule recovered those and Haiku single-shot reached 35.3 of 52.

The tool loop is what moved it: search_documents and read_passages, a numbered evidence list, bounded steps and
calls, and a fallback to the single-shot path on any failure. Nova Lite with tools scored 37.7; Claude Haiku with
tools scored 44, 44, 42, average 43.3 of 52, with exact-number answers at 5 to 7 of 8 and cross-document at 7 to 8
of 8. The cost is about three times the model calls per answer, inside the caps.

Anthropic access was enabled in the credits account: the use-case form was submitted, and the instance policy now
carries the Claude inference profiles and their three regions (policy versions v4 and v5). The deployment runs
Haiku with the loop on; rollback is the flag or the manifest.

Remaining: the large-table pinpoint probe still fails, which is the row-cap tradeoff and the reason Phase B adds a
lookup_value tool. Tool use on the weaker model slightly hurt honesty (two unanswerable questions answered from
adjacent facts); Haiku held 7 of 7. Prompt tightening on both is next.

## 2026-10-18, evening: Phases B and C, and the budget that was pretending to be an outage

Phase B added lookup_value, the two-figure intersection, the ambiguity guard, the automatic lookup before a
refusal, and the honesty rule. Phase C added opt-in tool streaming with progress frames, manual section 13n,
runbook 6.7, and this acceptance run. Each phase was measured before the next started, three suite runs each.

Final configuration (Claude Haiku, six steps, ten calls, lookup off): 41, 40, 39 of 52. The loop is worth about
five points on this configuration and eight at its best observed (44, 44, 42 before lookup and the canary document
changed); single-shot scored 35 to 36. The differences between loop configurations are inside run-to-run variance,
which is wide, so the report states the range rather than a single number. Lookup did not pay off on this corpus:
duplicated census figures made it an anchoring risk, the ambiguity guard correctly refuses that, and the
large-table pinpoint probe still answers the wrong row. It stays implemented and switchable, default off.

All four acceptance probes pass on the final configuration: interface 8, activity 8, access 4, stream 4. The stream
canary had to be restored to the corpus first; without it the REDACTED right was proving nothing, and it said so
rather than passing.

The acceptance run also found a real budget defect: 21 of 52 answers degraded with numbers that could not be right.
The hook passed the team's remaining budget as the identity budget while the gateway charges spend to the
identity, so the same spend was subtracted twice and the budget fired at half its value; and every role shared one
identity name, so four roles shared one spend counter. Fixed in PR #173. The development ceiling of 10 USD had also
been spent by the day's measurement and is raised to 100 on the owner's instruction.

## 2026-10-19, overnight: five runs settle the loop question, and correct a method failure

Ran five suite runs for each of three frozen configurations: Phase A's commit, current main with the loop on, and
current main with the loop off. Phase A 37, 43, 40, 44, 42 (mean 41.2). Main 33, 45, 44, 36, 43 (mean 40.2).
Single-shot 36, 33, 34, 35, 37 (mean 35.0). The loop is worth five to six points; the two loop configurations are
one point apart with overlapping ranges and a per-question split of 11 wins, 7 wins, 34 ties.

The correction matters more than the numbers. On 2026-10-18 the phase report treated a Phase A triple of 44, 44, 42
as a peak, concluded the final configuration had regressed, and shipped two prompt changes in response. The
overnight experiment shows the triple was the top of a distribution centred at 41.2, and the prompt changes were
noise chasing. Five runs per configuration, frozen corpus, one scorer: that is the method from now on, and the
phase report is superseded by docs/reviews/experiment-three-configs-2026-10-19.md.

Exact-number questions are the loop's real contribution: 5.4 to 6.0 of 8 against 0.8 single-shot. The honesty
categories are where single-shot is nominally ahead, which is the trade to watch. Deployment left running current
main with the loop on; next work is the structured query tool.

## 2026-10-19, afternoon: the row query, and the hand-off brought current

A second session built the structured row query from the hand-off's first open item: `query_rows`, one to four
literal strings, exact rows back with document, page and ordinal, numbered into the citation evidence, bounded and
access-filtered, audited, off by default behind OI_TOOL_QUERY. It measured 46.6 of 52 over five runs against a
same-day control of 43.0 with non-overlapping ranges; exact-number questions went from 4.8 to 7.4 of 8 and the
large-table probe from 0 to 4 of 5. The same session found and fixed two infrastructure defects the hand-off left
behind (the suite script could not write its output directory as the container user; the audit tool column was
never populated), and fixed a tool defect where a full evidence namespace swallowed a queried row.

The hand-off was brought current: the milestone, its numbers, the honest control-drift caveat, the concentrated
remaining failures (27 failing runs of 260, of which 22 sit on four questions and 5 are provider blips), the
acceptance-probe evidence for the live build, and the revised next-work list. The remaining work is not prompt
tuning: diagnose A06, P05, P06 and U08, add provider fallback, redesign the two bad probes, and widen the
evaluation set before claiming more.
