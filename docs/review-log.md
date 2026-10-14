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

**What round 1 found, and what changed.** Filled in when the reviewers report.

