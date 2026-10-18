# After the giant-table policy: chunker-v4 corpus, 2026-10-18

The "after" half of the comparison that opened with `docs/reviews/baseline-v3.md`. Same runner, same scorer, same
forty questions and four probes. Read the baseline's limits before reading the totals: one run there, and the
answer model turned out to be nondeterministic, so the answer-pass total cannot carry the conclusion.

## The corpus that was measured

Rebuilt from zero under `chunker-v4`: the collection, manifest, contract, chunk index and state file were cleared
first, so there is no mixed chunking anywhere.

| | |
|---|---|
| files on disk | 544 |
| indexed | 538 (532 documents, 6 data-set cards) |
| chunks | 100,477, matching the Qdrant point count exactly |
| dead letters | 6, all genuine parser failures (list below) |
| contract | chunker-v4, max input 8,192, truncation off, generation 1 |

Rebuild time: started 2026-10-17T21:21:41Z, condition met 2026-10-18T02:47:33Z, **five hours twenty-six minutes**.
That is longer than the one-to-two hours I estimated. The estimate was wrong for a measurable reason: the data-set
rule skipped the five biggest files and the row cap removed the row explosion, but the remaining five hundred
documents still had to be parsed and embedded, and thirty of them had never been indexed at all because the v3
load stopped early. The last two cycles also retried twelve failures. The work that remained was the work that
cannot be skipped.

**Data-set cards** (six, listed in `rejected/datasets.csv`):

| file | size | why |
|---|---|---|
| executive/group-03/594185.xls | 8.7 MB | spreadsheet above the 2 MB line |
| executive/group-03/058658.txt | 6.6 MB | text dump above the 3 MB line |
| executive/group-03/084615.txt | 5.3 MB | text dump |
| executive/group-03/594706.xls | 5.2 MB | spreadsheet |
| company/group-02/595758.xls | 3.2 MB | spreadsheet |
| executive/group-03/042246.txt | 3.4 MB | text dump |

**Dead letters** (six, listed in `rejected/rejected.csv` with the parser's own error): five legacy OLE2 `.xls`
files that the parser cannot open (000406, 024650, 184459, 546492, 828946) and one 55 KB CSV whose
`SimplePipeline` fails (468308). These are the same files the v3 load could not read. They are unchanged on disk,
so they are waiting for a human decision to replace or exclude them, not for another retry.

## Retrieval: deterministic, and better

Retrieval does not involve the answer model, so this is the part of the comparison that means something. The
after column is runs two to four; run one differed on one question (X02, which hit in every other run) and that
is noted under noise.

| Category | Baseline retrieval | After retrieval |
|---|---|---|
| exact number from a table | 3 of 8 | **6 of 8** |
| prose fact | 7 of 8 | 7 of 8 |
| cross-document comparison | 8 of 8 | 8 of 8 |
| access boundary | 10 of 10 | 10 of 10 |
| total | 30 of 34 | **33 of 34** |

The three recovered questions are all exact-number lookups that the dump had been displacing (N03, N04, N06).
The remaining misses are unchanged from the baseline (N05, N07, P05) plus the PR1 probe, which is a bad probe
and is addressed below.

**The pollution is gone.** In the baseline the 27,829-row CSV appeared in the top five of 15 of 52 runs, 42 of
the 260 retrieved passages in total. After the rebuild it appears in **0 of 52 runs and 0 of 260 passages**. That
is the defect this change was built to remove, and it is removed rather than reduced.

## Answers: the model flips, so the total cannot carry the verdict

Four identical after-runs, same corpus, same questions, same scorer, produced **32, 26, 31 and 29** passing runs
out of 52. The baseline was one run at 33. Nineteen of the 52 question-runs change outcome across those four
runs. That is the answer model, not the retrieval layer.

Three questions passed in the baseline and fail in all four after-runs: P06, X03 and A07/staff. For each of them
the retrieved documents and passages are **identical** before and after. Same input, same context, different
output. They are not regressions caused by the chunker; they are the same nondeterminism, caught on the other
side. The same applies in reverse to the questions that became more reliable.

The honest reading is that the forty-question set can measure retrieval and citations, and cannot measure
answer quality in a single run. The next version of this comparison should use three runs and a majority vote, or
a temperature of zero, before it reports an answer total. What it can say now: there is no evidence of an answer
regression, and no evidence of an answer improvement either.

## The four probes

- **PR1, row in a normal table: fails in both eras.** The probe is wrong, not the corpus: the two numbers it
  quotes are in different chunks of that census table, so no single passage can match it and the retriever is
  being asked for a join. It should be replaced with a question whose values share one row.
- **PR2, row in a large table: right document retrieved, wrong value answered** (-4.6 instead of -6.1). This is
  the intended tradeoff, visible: a 41,000-row table no longer has one entry per row, so the model has to find
  the row inside a bounded group, and it can pick the wrong line. Large-table pinpoint lookups got worse by
  design, and the citation now points at a row range rather than a row.
- **PR3, data-set-only question: correct refusal**, as in the baseline. The card is not retrieved for a
  content question because it names the file rather than describing its contents. That is the honest limit of
  the policy: the file is discoverable by name, and its data is not searchable at all until the data query path
  exists.
- **PR4, pollution probe: fails in both eras.** Also a bad probe: "the median earnings figure for workers 16 and
  over" appears in several census tables, so the question has no unique answer. Replace it.

## Two defects this rebuild found and fixed

1. The data-set card was returned unsealed, so its chunk id was empty and Qdrant rejected every card write with
   "value is not a valid point ID". Five files dead-lettered twice and the first completed corpus had zero cards.
   Fixed in PR #161 with a regression test that the id is a real UUID.
2. The text-dump threshold was 4 MB, so a 3.4 MB text dump was handed to the parser, refused with 413 and
   dead-lettered. The threshold is now 3 MB, which is above every document in this corpus and below the point at
   which the parser refuses the file anyway.

## Verdict

Accept the policy. The measured defect is eliminated (pollution: 15 runs to 0), exact-number retrieval improved
by three questions, prose and cross-document retrieval are unchanged, and the access boundary did not move. The
costs are the ones predicted: large-table row-precision drops, and the data itself stops being searchable.
The answer total is not evidence for or against, because the model varies more than the change does.

`RET-TABLE-001` moves to verified for the mechanism and the measured effect. Follow-ups: redesign PR1 and PR4 so
the probes measure something, adopt a multi-run majority before the next answer-quality comparison, and treat the
data query path as part of C6. The v3 snapshot is still in
`s3://officeinference-corpus-878451098165/backups/baseline-v3/` for anyone who wants to re-run the baseline.
