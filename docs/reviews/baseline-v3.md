# Baseline measurement, chunker-v3 corpus, 2026-10-17

The "before" half of the before-and-after measurement for the giant-table policy change. Frozen, reproducible, and
taken with the same runner the "after" run uses, so the comparison is like for like.

## The corpus that was measured

Ingestion was stopped at 20:11 UTC with the index in this state, and snapshotted before anything else happened:

| | |
|---|---|
| documents indexed | 508 of 544 on disk |
| chunks | 196,751, matching the Qdrant point count exactly |
| unprocessed | 36 files, all multi-megabyte data dumps |
| contract | chunker-v3, max input 8,192, truncation off |

Artifacts: `/opt/oi-core/backups/baseline-v3/` on the box, and the same three objects in
`s3://officeinference-corpus-878451098165/backups/baseline-v3/`. The Qdrant snapshot is 1,030,080,000 bytes and its
sha256 was verified against the checksum Qdrant reported: `1f0ac5934f26949278e77856f2eac33bc070d4823a162603d9011b3afc23a195`.
The manifest dump is 17 MB.

## Method

`tests/fixtures/data_suite_run.py`, run in the eval image against the frozen index, 52 runs in 102 seconds:

- the forty questions in `docs/eval/data-questions.yaml`, plus four targeted probes;
- each question through the retriever as `ask.py` calls it, recording documents and chunk kinds;
- each answer through the assistant API as a client asks it, recording the answer, its status and its citations;
- access questions run twice, as a staff session and as an executive session.

Scoring is deliberately narrow and identical for both runs. An answer question passes when the expected value is
in the answer and a listed document is cited. A question that expects a refusal passes when the service declined,
by status or in plain words ("the passages do not contain", "cannot answer"), cited nothing forbidden and produced
no withheld value. The raw JSON behind every row is `docs/reviews/baseline-v3.json`.

## Results

| Category | Runs | Passed | Retrieval hit |
|---|---|---|---|
| exact number from a table | 8 | 1 | 3 of 8 |
| prose fact | 8 | 6 | 7 of 8 |
| unanswerable | 8 | 6 | n/a |
| cross-document comparison | 8 | 5 | 8 of 8 |
| access boundary | 16 | 14 | 10 of 10 |
| probe: row in normal table | 1 | 0 | 0 of 1 |
| probe: row in large table | 1 | 0 | 1 of 1 |
| probe: dataset-only question | 1 | 1 | n/a |
| probe: pollution | 1 | 0 | 1 of 1 |
| **total** | **52** | **33** | |

## What the baseline shows

**1. One row dump is dominating retrieval.** `company/group-02/481953.csv`, a 27,829-row numeric dump indexed under
v3 as 28,000-odd table-row chunks, appears in the top five of **42 of the 52 runs**. Its rows are near-identical
numeric strings, so they match almost any numeric question and displace the documents that hold the answer. This
is the pollution the row cap is meant to remove, measured rather than argued.

**2. Exact-number questions are the weak category, and the two causes are separable.** Retrieval found the right
document for only 3 of 8, and only 1 of 8 answers passed. The probe on a normal-sized table (PR1) misses because
the dump outranks it. When the correct document is retrieved, the model still often declines or restates the question
instead of emitting the number (N01, N08).

**3. Prose facts work.** Seven of eight retrieve the right document and six answer correctly. The one miss (P05) is
a telephone number inside a `.doc`, which is a retrieval weakness worth its own look but not part of this change.

**4. Cross-document retrieval is solved; the arithmetic is not.** All eight comparisons retrieve both operand
documents, and five answers pass. The failures are the model hedging or computing without citing, not the retriever.

**5. Honesty mostly holds, with one fabricated answer.** Six of eight unanswerable questions were correctly
declined. The exception is U06: asked what a solar farm maintenance agreement requires, the model answered with the
obligations from a NASA property-use agreement, complete with citations. That is a confident invented answer and
the most serious single failure in the baseline.

**6. The access boundary holds.** No staff run cited an executive document or contained an executive value; staff
refusals are plain-language declines. The two access-category failures are answer completeness on permitted
questions (A07's shotgun clause), not leaks.

**7. The probes behave as expected for this design.** PR2 retrieves the 41,000-row coordinate document and the model
still cannot find the row inside the passages; PR3 declines the dataset-only question honestly; PR4 retrieves the
census document and answers from the wrong table, again because of dump pollution.

## Limits of this measurement

One run, so answer variance is real. The answer half is a model, so this is a system measurement, not a
chunker-only one; what the after-run can attribute to the policy change is the retrieval and citation behaviour,
and the answer differences that follow from it. The scorer is strict about values and lenient about refusal
wording, and it is the same scorer on both sides.

## What the after-run has to show

- the giant dumps no longer appear in the top five of ordinary questions;
- exact-number retrieval and pass rate go up, and PR1 in particular finds its document;
- PR3 still declines, but names the dataset rather than pretending it does not exist;
- prose and cross-document categories do not regress;
- no new leak and no fabricated answer.
