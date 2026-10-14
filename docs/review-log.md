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



