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


