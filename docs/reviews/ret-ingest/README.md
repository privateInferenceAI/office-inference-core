# Ingest cycle lock: RET-INGEST-002

**Date:** 2026-10-23. **Status:** verified.

## Problem

The scheduled ingestion loop and an operator's manual docker exec were separate processes with no shared state.
A manual backfill could run at the same time as a scheduled cycle, both writing chunks and both driving the
embedding service.

## Fix

- Both paths take an exclusive flock on /state/ingest.lock for the whole cycle.
- The lock file lives in the state volume both processes mount.
- A manual run holds the lock for its cycle.
- The scheduled loop runs with INGEST_SCHEDULED=1. If the lock is held it prints
  "another ingest cycle is running; skipping this scheduled cycle" and exits 0, so the shell loop sleeps until
  the next interval.
- A second manual run is refused with exit 1 rather than running beside the first.
- The embedding settings remain one document at a time with the token-budgeted batch, so a bulk load does not
  run multiple parser/embedding windows.

## Test

Held the lock for 25 seconds from a separate process, then tried each path:

    $ docker exec ingestion env INGEST_SCHEDULED=1 python /app/ingest.py --documents /documents --json
    another ingest cycle is running; skipping this scheduled cycle
    exit 0

    $ docker exec ingestion python /app/ingest.py --documents /documents --json
    another ingest cycle is running; refusing to start a second manual cycle
    exit 1

After the holder released the lock, a normal manual cycle completed with 80 seen, 0 new, 80 skipped, 0 failed.

The medical source was then ingested through the same path: 81 seen, 1 new, 4 chunks, 0 failed.

## Files

- services/ingestion/ingest.py
- deploy/office/compose/docker-compose.yml (INGEST_SCHEDULED=1 on the scheduled invocation)
- Lock path: /state/ingest.lock
