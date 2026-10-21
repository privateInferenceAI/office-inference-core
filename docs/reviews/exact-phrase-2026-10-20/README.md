# The exact-phrase fallback: diagnosis, rejected designs, and measurement

**Date:** 2026-10-20. **Branch:** feat/exact-phrase-lane (PR #206). **Status:** merged behind a default-off flag. The
fallback is **not enabled on any deployment**; the corpus-1 live gate could not be certified because that suite was
unstable during the measurement window with the feature off, as recorded below.

## Diagnosis

Each failing needle was traced to its carrying chunk in the installed index and to that chunk's rank in retrieval:

| Needle | Carrying chunk in top 8? | Cause |
|---|---|---|
| Caterpillar Current Termination Date (corpus 2) | no; 12 chunks state the phrase | recall: the right document ranked first, the carrying chunk did not |
| Northwind visitor badge (corpus 2) | no; related badge rules returned instead | recall plus a competing related rule |
| Apple full-time-equivalent count (corpus 1) | yes, rank 1 | excerpt reading: the 700-character snippet was truncated before the figure |

The first two need a literal phrase lane; the third needs the searched words inside the excerpt the model reads. A
match-centred exact search does both.

## Designs measured and rejected

1. **A sixth model-facing tool.** Measured: Caterpillar fixed, but the Target table question (PUB_N01) broke in 5 of
   5 runs because the model reached for the new tool instead of the row query. Rejected.
2. **An exact mode on search_documents.** Measured: Target restored, Caterpillar broke 5 of 5; a prompt sentence
   did not stabilise the choice. Rejected.
3. **A deterministic fallback, shipped.** The tool list and prompt stay exactly as measured before. When the loop is
   about to refuse, or reaches within two steps of its cap without a row-query call, it takes the rarest literal
   phrase from the question (capitalised runs or content n-grams, 1 to 16 matching chunks), searches it literally
   inside the access roots, adds up to ten match-centred excerpts, and lets the model answer once more. Exactly one
   retry. It does not fire when the row query was used, so anchor-number table questions keep their existing path.

## Measurement, final design

Ten runs per comparison, same deployment, only the flag changing.

**Needles.** Corpus-2 target set (6 questions): off **23 of 30** (5, 5, 5, 4, 4), on **30 of 30**. Caterpillar went
0 of 5 to 5 of 5; the visitor-badge probe went 3 of 5 to 5 of 5. Corpus-1 coverage needle set (10 questions): off
**44 of 50**, on **45 of 50**; the Apple headcount went 0 of 5 to 5 of 5, and one other coverage question
(CAND_UNTOUCHED_10) went from 2 of 5 to 0 of 5, a genuine per-question regression recorded under the minimal gate.

**Live suites.** Corpus-2 full set, five runs: off 31.8 of 37 (earlier same-configuration measurement), on
31, 32, 33, 30, 33 = **31.8 of 37**. No aggregate change; the needle gains are inside this set. Corpus-1 live suite:
the control itself measured 48 and 49 of 53 with the flag **off**, against 53 of 53 measured earlier the same day,
and the two anchor-number questions N06 and N07 fail with the flag off as well. That is instrument instability, not
an effect of this change, but it means the corpus-1 aggregate gate cannot be certified from this window.

## Decision

- Merge with `OI_TOOL_EXACT` default **off**. No deployment enables it.
- The corpus-2 gate is met (needles improve, aggregate unchanged). The corpus-1 needle set improves, but the
  corpus-1 live gate stays open until a clean same-day five-run control reproduces.
- The visitor-badge probe wording was clarified as an instrument fix (it asks for the maximum number of hours).
- Next: re-run the corpus-1 live control in a stable window; if it reproduces, enable the flag on the development
  deployment and keep the corpus-2 comparison as the acceptance evidence.

## Where the numbers come from

- Raw runs: /tmp/dev-live, /tmp/exact-measure on the two instances.
- Question sets: docs/eval/data-questions.yaml, docs/eval/data-questions-wider.yaml,
  docs/eval/corpora/second-corpus/questions.yaml.
- Dispatches behind this record: the branch history of PR #206.


## Late update, 2026-10-20: the real cause of the unstable live suite

The corpus-1 instability that blocked the gate was **measurement contention**, not the exact fallback. Two series
of five runs on an otherwise idle box scored 53, 53, 53, 53, 53 with the flag off, and the same configuration had
scored 48 and 49 while two evaluation suites ran at once. The flaky questions were the anchor-number table
questions N03, N06 and N07, whose failure mode is the tool loop reaching its step cap and the caller discarding the
evidence the loop had gathered.

Two follow-ups are in flight as their own units:

1. **Measurement hygiene, merged (#210).** A host flock, a Postgres advisory lock, load and memory admission gates,
   and a run-context file now stop two suites from sharing a deployment and record the conditions of every run.
2. **Loop-cap rescue, PR pending (#211).** When the loop is about to exit at the step or call cap, it makes one
   final no-tools call using the evidence it already gathered, verified by the same answer contract. Unit tests
   pass; its five-run acceptance is the next measurement, now enforceable under the new hygiene rules.

The exact fallback stays default off. The cue-restricted version measured 50, 53, 53 on three live-on runs before
the run was stopped for time; that is not a certification, and no deployment enables the flag.


## Acceptance of the loop-cap rescue, 2026-10-21

The rescue in decision 0006 was accepted after five serial runs per phase: corpus-1 control 53, 53, 53, 53, 53 and
rescue 53, 53, 53, 53, 52, with the single difference an A04 executive question that fails 5 of 5 when run alone on
both builds; corpus-2 control 29.4 and rescue 29.2, flat within the five-run spread. The first attempt was
invalidated by the shared Bedrock daily token quota when a suite ran on each deployment at once, which is now rule
8 in decision 0005. The exact-phrase flag stays default off; the rescue is the answer-path improvement that landed.
