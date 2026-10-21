# Corpus-2 reindex with table context in the index, 2026-10-21

**What changed.** Chunker-v5 writes `Table: ... Columns: ...` into every table row, `PAYLOAD_VERSION` moved to v6,
migration 024 added table identity and row metadata to `chunk_index`, and `ingest_manifest` records the chunker
version so a code change re-derives the corpus. The collection contract fingerprint includes the chunker version,
so the reindex needed the contract-change flag and both images rebuilt.

**Procedure actually run on the corpus-2 box.**
1. `pg_dump` of the whole database and Qdrant collection snapshot taken before the change, both copied off the box
   to `/home/anthony/oi-eval-runs/backups`.
2. `audit migrate` after rebuilding the audit image. The first attempt failed on an apostrophe inside a SQL
   comment; PR #218 fixed it before the migration had been applied anywhere.
3. `docker compose run -d --name oi2-reindex -e EMBED_CONTRACT_ALLOW_CHANGE=1 ... --entrypoint python3 ingestion
   /app/ingest.py --documents /documents`.
4. Result: **80 documents seen, 80 changed, 0 skipped, 0 failed, 24,296 chunks written**. The previous index held
   24,295.
5. The API image was rebuilt after the migration fix. A first restart crash-looped because the API image still
   carried the original migration and the checksum guard saw it changed; rebuilding resolved it.
6. Five serial corpus-2 runs, contract generation current.

**Result.** Control on the same answer-path code with the old index: 31, 32, 31, 34, 31 (mean **31.8 of 37**).
After the reindex: 32, 32, 33, 33, 30 (mean **32.0 of 37**).

The aggregate change is within one question. The per-question picture is more useful: PUB_N04 (C.H. Robinson
total revenues) went from failing in every control run to passing 4 of 5, and PUB_L02 (Duke first mortgage bond,
600) went from failing 5 of 5 to passing 5 of 5. The remaining deterministic failures are unchanged and not
table-row problems: PUB_P01 (prose contract clause), PUB_L01 (definition below the retrieval cut), SC_PROBE_NEEDLE
(excerpt truncation and a competing rule), SYN_A02 executive (board minutes reading).

**Corpus-1 is deferred and must not be measured against its current index.** The corpus-1 reindex was started on
the build box and stopped after 18 of 539 documents: the observed rate implied about a day, not the five hours the
older ingest logs suggested. The index is mixed chunker-v4/v5 and the collection contract generation is 2. The
deferred-work note is at `/opt/oi-core/eval-runs/CORPUS1-REINDEX-DEFERRED.txt` on that box. A complete corpus-1
reindex is needed before any corpus-1 comparison.

**Evidence.** Raw runs at `/home/anthony/oi-eval-runs/reindex-corpus2` on the laptop, and the reindex log in that
archive. Snapshots and dumps in `/home/anthony/oi-eval-runs/backups`.


## Related experiment: query_rows section-row matching (PR #219)

A separate change let `query_rows` complete a match using the section row immediately above a data row, aimed at
the Duke bond question. Measured on the reindexed corpus-2 index, five serial runs: 29, 32, 32, 33, 33 (mean 31.8)
against a 32.0 baseline on the same code and index. PUB_L02 passes 4 of 5 in the experiment, but it already passes
5 of 5 on the reindexed baseline, so the rule bought nothing. Closed without merge and recorded here as a
measured negative.
