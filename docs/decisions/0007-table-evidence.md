# 0007. Render table column context with table evidence

**Date:** 2026-10-21. **Status:** accepted. **Owner context:** corpus-2 answer quality work continues.

## Why

The indexed table row is exactly the row: "Total revenues | 4,934,098 | 4,136,543 | 8,947,032 | 8,183,283".
It carries no caption, no column header and no period. When five passages from one filing all arrive as whole-table
blobs, the model cannot tell which of the four numbers belongs to the three months ended June 30, 2026. Measured:
PUB_N04 (C.H. Robinson total revenues) answered 896,604, which is the Global Forwarding column of a concatenated
table, not the consolidated total.

An offline probe over the corpus showed the fix is the column header, not a new retriever. It measured
period-resolvability of returned rows at 3 of 10 without enrichment and 7 of 10 with the nearest header block and
section caption prepended. Document title and heading path made results slightly worse, because they inject
"report" and "annual" into every chunk of every filing. The same probe confirmed the product already has the
matcher, query_rows; the gap is that the model does not call it for confident wrong answers.

## Decision

Render table context with table evidence, at answer time, without reindexing.

- For a table_row passage, read the rows above it in the same Qdrant table_id (the lexical chunk_index does not
  store table_id or row_index), find the nearest preceding header-looking row, include up to one contiguous
  header row above it, and prepend a caption when a section title sits directly above the header.
- For a whole-table blob, find the header row and caption in the blob's own lines.
- Cache the derived prefix per table and row. The Qdrant call is bounded and failure returns no prefix, which is
  the old behaviour.
- Do not prepend the document title or heading path at index time.
- Do not centre the already-retrieved seed evidence on the question. The first combined measurement did, and it
  moved snippet windows past carrying lines on PUB_N01, SYN_A02 and U01. Search results keep the centred snippet
  from decision 0006's excerpt work; the seed render keeps the start slice.

## Measurement, 2026-10-21

Five serial runs per phase, same-day controls on main.

| Comparison | Control | This change |
|---|---|---|
| Corpus-2 live set (37) | 29, 30, 29, 29, 27 (mean 29.2) | 31, 32, 31, 34, 31 (mean 31.8) |
| Corpus-1 live suite (53) | 53, 53, 53, 53, 52 (mean 52.8) | 53, 52, 53, 53, 52 (mean 52.6) |

Corpus-2 gains 2.6 role-runs per full pass, with the best run at 34 of 37. The gain is not owned by one question:
PUB_N04 fails in all five runs of the accepted variant and passes in the best run; SYN_A02's executive answer, the
Target net-sales question, the visitor-badge probe and the Caterpillar termination date each move by two or three
runs between the control and this change. That is the honest shape of a table-evidence change on a question set
where the model's reading varies run to run. Corpus-1 is flat within the five-run spread: one extra X02 failure in
one run, no systematic question.

Rejected on the way: seeding the centred snippet into the seed evidence (regressed the three questions above), and
prepending document title or heading path (the offline ablation showed it cost a question).

## Consequences

- The lexical arm still cannot see table identity. Rows that were never emitted as rows (Duke Energy's 600, the
  6,010-line ledger) and long prose definitions (Caterpillar's Current Termination Date) are untouched; those are
  the reindex and sidecar work.
- The Qdrant sibling scan is one request per table passage, about 2 seconds timeout, cached per row. A slow vector
  store degrades to the old evidence, not to an error.
- Ties between rows that hold the right number in different tables remain; the enrichment adds period, not entity.
