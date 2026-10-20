# The exact-phrase lane: diagnosis, implementation and live checks

**Date:** 2026-10-20. **Branch:** feat/exact-phrase-lane (PR #206). **Status:** implemented and live-tested; the
five-run acceptance measurement is not yet run, so the tool stays default off.

## Diagnosis, from the run records and the installed indexes

Each failing needle was traced to the carrying chunk in the index and to its rank in retrieval for the question.

| Needle | Carrying chunk in top 8? | First result | Cause |
|---|---|---|---|
| Caterpillar Current Termination Date (corpus 2) | no; 12 chunks state the phrase, none in the eight | the same document, a signature page | recall: the document ranked first but the carrying chunk did not |
| Northwind visitor badge (corpus 2) | no | employee handbook badge section, contractor 30-day badge row | recall plus a competing related rule: retrieval offered near matches |
| Apple full-time-equivalent count (corpus 1 wider set) | yes, rank 1 | the carrying chunk | excerpt reading: the 700-character snippet was truncated before the figure, and four of five runs concluded the answer was absent |

The split matters: the first two need a literal phrase lane, the third needs the matching words in the excerpt the
model reads. One mechanism does both, because a match-centred excerpt shows the searched words.

## What was built

A sixth tool, search_exact, behind OI_TOOL_EXACT (default off): a literal, comma-insensitive phrase match inside
the session's access roots, one parameterised SELECT, at most six passages, six reserved evidence slots, and an
excerpt centred on the match. It is additive: search_documents returns exactly what it returned before.

## Live checks

On the second-corpus index with the flag on:

- Caterpillar Current Termination Date: **passes**, answering August 27, 2029, and the audit shows a search_exact
  call.
- Apple full-time-equivalent count: **passes**, answering 164,000 with a citation. It did not need the new tool in
  that run; the measurement will show whether the tool changes its rate.
- Northwind visitor badge: **still fails**. The model calls search_exact but then answers from the handbook's
  end-of-visit rule instead of the facilities manual's 24-hour maximum. The question is a deliberate
  related-rule trap; one prompt-guidance sentence was added and the case is still open. This is the question the
  measurement has to decide: either the tool needs better selection guidance, or the question's expected value
  should be the handbook rule and the probe is measuring the wrong thing.

## Acceptance, not yet run

1. A needle set of about ten questions across both corpora, each with its carrying chunk identified.
2. Five runs with the flag off and five on, per corpus, plus the needle set.
3. Minimal gate per decision 0004: needle set improves, neither corpus aggregate drops, control probes hold.

Everything above is repeated from run records and live checks, not estimated.
