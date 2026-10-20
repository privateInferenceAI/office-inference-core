# The wider evaluation set, measured for the first time

**Date:** 2026-10-19 night. **Deployment:** `oi-core-dev-cloud`, Claude Haiku 4.5, tool loop on, query on.
**Instrument:** `docs/eval/data-questions-wider.yaml`, 72 questions plus the five probes.

## What this is

The live forty are frozen so old and new runs stay comparable. The wider set is the first attempt at questions the
current instrument cannot measure: aggregation over several rows (8), date arithmetic (4), multi-hop across
documents (4), untouched-document coverage (10), and six policy-absence questions that keep the honesty gate's
phrasing boundary measured. Every question was validated mechanically against the index: documents exist, expected
values appear in the named documents or are proven by indexed operands through a `derivation` block, absent
patterns match zero chunks.

This is a **baseline**, not a pass mark. The questions are harder on purpose, and a first run is supposed to find
where the system actually stops. The useful output is the per-category picture and a reading of each failure as a
product limit or a question defect.

## How it was measured

Five runs through the assistant API on the deployed configuration, exactly the live suite's runner with the file
swapped in (`DATA_SUITE_QUESTIONS=/w/docs/eval/data-questions-wider.yaml`). The five probes run too, so the
mechanism checks stay in the same measurement. Per run: 85 role-runs (16 access-boundary, 64 single-role, 5
probes).

## Results

## Results

Five runs, 85 role-runs each: **84, 84, 85, 84, 84 of 85 (mean 84.2, range 84 to 85)**.

| Category | Passed | What it says |
|---|---|---|
| aggregation (8) | 40/40 | multi-row arithmetic across tables works |
| date_arithmetic (4) | 20/20 | dates and intervals over indexed text work |
| multi_hop (4) | 20/20 | two-document reasoning works, including the retrieval |
| coverage (10) | 46/50 | one long-document needle miss in four runs |
| unanswerable (14) | 70/70 | includes the six phrasing probes and U08: the honesty gate holds |
| access_boundary (16) | 80/80 | the ACL still holds on documents the old set never touched |
| cross_document, exact_number, prose_fact, all five probes | 100% | unchanged from the live suite |

**The single failure is CAND_UNTOUCHED_07**, the Apple 10-K full-time-equivalent count (164,000). The carrying
chunk exists in the index as prose on page 1; retrieval returned financial-table chunks from the same document,
and the loop did not reach the headcount paragraph in four of five runs. Run 3 found it and scored 85 of 85. That
is a real long-document needle-retrieval limit, not a question defect: the evidence was verified when the question
was authored, and the document is a single very long HTML file where many chunks look alike. A candidate fix
(heading-aware retrieval or a document-scoped read) is product work, not an instrument change.

## Cost and latency

From the same runs, via scripts/acceptance_panel.py: **p50 2.8 s, p95 13.6 s**, provider spend 5.02 USD for
1,080 calls, **0.0118 USD per answer at 2.54 provider calls per answer**. Two calls went through the Nova Pro
fallback during the five runs, so the chain was exercised by ordinary traffic and the answers it carried passed.

## What the first measurement says

- **The wider set did not expose the weaknesses it was built to expose.** Aggregation, date arithmetic and
  multi-hop all passed 100 percent. That is a good generality result for the tool loop and the row query, and it
  means the wider set is now close to this deployment's ceiling too.
- **The one real gap is retrieval of a needle in a long, uniformly-formatted document.** One question found it.
  This is the first evidence that the next instrument needs harder shapes (multi-table joins, long-document
  needles, ambiguous questions), not more of the same difficulty.
- **The honesty gate and the probes held**: 70 of 70 refusal questions passed across five runs.
- The live forty stay frozen; this set is the comparison point for the next capability claim, not a replacement.


## What to look for

- **Aggregation and arithmetic** are the new capability claims. The answer verifier flags a figure that appears
  nowhere in the evidence and may send computed answers back; if those questions fail with refusals rather than
  wrong numbers, that is a product and instrument interaction worth a decision, not a question defect.
- **Multi-hop** failures can be retrieval misses, and the evidence column in the run record shows which document
  was missing.
- **Coverage questions** use documents no earlier question touched, so a failure is a retrieval or parsing limit
  that the old instrument hid.
- **The six policy-absence questions** must refuse. A failure there is a gate regression, not a hard question.

## Caveats

- One corpus, one deployment, one model.
- The 26 authored questions were reviewed mechanically and by the agent that wrote them, not by a domain expert.
- p50/p95 latency and cost for this run set are available from the same records with
  `scripts/acceptance_panel.py`.
