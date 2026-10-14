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
