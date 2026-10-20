# Answer quality, one page

Office Inference core stack, measured 2026-10-19 to 2026-10-20 on our acceptance corpus: 539 documents,
100,478 indexed passages. The current configuration answered **53 of 53 expected outcomes in each of five repeat
runs** of our live acceptance set, and **84.2 of 85 on average** across five runs of a wider, harder set.

## What the numbers mean

Each expected outcome is scored through the same API a client calls: the right value must be in the answer, the
document carrying it must be cited, and the answer must not be a fallback. Refusal questions pass only if the
system declines; access questions pass only if staff are denied and the executive session answers. Five repeat
runs per configuration; the range is shown, not a single number.

## Results

| Measurement | Result |
|---|---|
| Live acceptance set, 5 runs | 53, 53, 53, 53, 53 of 53 |
| Wider set, 5 runs | 84, 84, 85, 84, 84 of 85 (mean 84.2) |
| Aggregation, date arithmetic, multi-document reasoning | 100 percent on the wider set |
| Access boundaries, both roles | 80 of 80 role-runs |
| Refusal and policy-absence questions | 70 of 70 |
| Answer citations | Document and page on every answer; figures checked against retrieved text |

**Search is measured, not assumed.** Enabling the structured row query raised the score from 43.0 to 46.6 of 52,
exact-number questions from 4.8 to 7.4 of 8, and a large-table pinpoint probe from 0 to 4 of 5 runs, and removed
seven unsupported-number refusals. Every numeric claim is verified against the retrieved passages before an answer
is returned.

**Reliability.** In five runs with the primary model provider failed on purpose, all 265 answers came through the
fallback model in a second region, with no quality loss and no degraded answers. With healthy traffic, 0 of 492
model calls failed over.

**Controls.** Access filtering held in every run, including documents the earlier test set never touched. A PII
canary returns masked in every run. A deterministic honesty check refuses questions about the company's own
policies when no retrieved passage states them, in the three phrasings we measured.

**Speed and cost.** Median response 2.8 to 3.4 seconds, 95th percentile 11.8 to 13.6 seconds, about 1.2 to 1.3 US
cents of provider spend per answer at 2.5 to 3 provider calls per answer. Infrastructure is quoted separately.

## What this is not

One corpus, one deployment, one model, five-run samples. The questions were written from the indexed text and
reviewed, not an independent benchmark. One known limit is needle retrieval in very long documents: one question
about a headcount in a long annual report was missed in four of five runs. The next proof is the same
instrument on your documents, not ours.

Full method, evidence and limits: docs/sales/answer-quality-2026-10-20.md.
