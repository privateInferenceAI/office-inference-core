# 0008. Write table context into the index

**Date:** 2026-10-21. **Status:** accepted for corpus 2; corpus-1 reindex pending.

## Decision

When the chunker emits a table row, it writes the nearest preceding header block and section caption into the
row's own text ("Table: ... Columns: ..."), and the lexical table gains `table_id`, `row_index`, `row_start`,
`row_end`, `table_rows` and `headings`. The manifest records the chunker version so an unchanged document is
re-derived after a chunker change. No document title or heading path is added: the offline ablation showed those
made retrieval worse.

## Why

The render-time prefix (decision 0007) gave the model columns only for passages it rendered. The lexical arm and
`query_rows` still read bare rows. The index is where a deterministic fact belongs.

## Evidence

Corpus-2 reindex: 80 documents, 24,296 chunks, 0 failures. Five serial runs after: 32, 32, 33, 33, 30 (mean 32.0)
against a 31.8 control on the same answer-path code with the old index. PUB_N04 went from five failures to one,
and PUB_L02 from five to zero. PUB_P01, PUB_L01, the badge probe and SYN_A02 executive are unchanged because they
are not table-row problems.

The gain is small. It is kept because the index now carries the table identity that the approved dataset sidecar
and a future per-table reader need, and because the row text no longer depends on a render-time lookup that a
slow vector store could skip.

## Consequences

- Corpus-2 reindexing costs about 1.5 hours at `INGEST_CONCURRENCY=1`; large SEC and OSHA filings dominate.
- Corpus 1 is far larger. A start was stopped after 18 of 539 documents and its index is mixed v4/v5. Do not use
  corpus 1 for comparisons until a complete reindex is run. The deferred-work note is on the box.
- The chunker version is part of the collection contract fingerprint. A chunker change flips the contract and
  requires both the ingestion and API images rebuilt, or the API refuses to start on a migration checksum.
- Rows never emitted, and the ledger rows above the table-chunk cap, are not fixed by this decision. The first
  was resolved by re-derivation; the second needs the dataset sidecar.
