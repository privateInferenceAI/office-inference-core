# Company ingestion rehearsal, 2026-11-03

A rehearsal of the Phase E document path, run on the product box. The goal was to answer one question before
the company's real documents arrive: does the deployment's ingestion path work, and what does a bad document
look like.

## Setup

- Scratch database `company_ingest_test`, restored from the latest local `oicore` backup.
- Three synthetic policy documents copied to a stand-in company folder: travel and expense, records retention,
  IT acceptable use.
- One-off ingestion worker container (`oi-core/ingestion:local`) on the deployment network, writing to a scratch
  Qdrant collection `company-rehearsal`, with the real parser, embeddings and vector services.
- The live collection `documents-mixed-v2-20261028` was not written to. The scratch database and collection were
  removed afterwards.

## Result

The worker ran in 13 seconds.

| Measure | Value |
|---|---|
| Documents read | 3 |
| New documents ingested | 2 |
| Failed | 1 |
| Chunks written | 63 |
| Manifest rows | 3 added |
| Qdrant points | 63, green |

The failure was explicit and stopped that document:

`records-retention-policy.md: RuntimeError: the embedding service refused a single chunk. With truncation off
that means the chunk is past the model's input limit.`

The other two documents ingested normally. The live collection and its 10,576 points were untouched.

## What this means

The ingestion path works end to end: parser, chunker, embeddings, Qdrant and the Postgres manifest. The
rehearsal also shows the failure mode that matters for a business: one document with an oversized chunk fails
as a whole, is listed with the reason, and the run exits non-zero. That is the right behaviour. It also means
the raw ingestion path can refuse a real document, and onboarding cannot declare a corpus ready while a
document is in the failure list.

Two implications for onboarding:

1. The canonical bundle path from `oi-corpus-builder` is the supported serving path: it enforces the measured
   512-token chunk ceiling before import, which is the invariant that failed here. The rehearsal used the raw
   ingestion worker as a reachability and failure test, not as the recommended production path.
2. Whichever path is used, the onboarding gate is a clean run: zero failures, documented counts, and a cited
   answer from a company document before the deployment switches its served collection.

## Evidence

`rehearsal.txt` is the run log, `ingest.json` is the worker summary, `ingest.stderr` is the error stream and
`qdrant.json` is the collection check.
