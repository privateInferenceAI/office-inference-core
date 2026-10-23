# Audit chain fork during concurrent load

**Date:** 2026-10-23. **Status:** repaired in the development database; writer fixed; verified after a 20-request
concurrent run.

## What happened

After the answer-event-loop fix, a 20-concurrent load run exposed a fork in the audit hash chain. The audit
verify command reported:

    - line 1481: prev_hash does not match the previous row
    - line 1482: prev_hash does not match the previous row

The fork was at ids 1817 and 1818. Row 1817 chained from id 1818, while id 1818 chained from id 1816. Both rows
were written at 2026-10-23T13:27:44Z during the load run.

## Cause

The audit table uses a bigserial id plus a BEFORE INSERT trigger. The trigger takes an advisory lock and computes
the hash, but PostgreSQL assigns the sequence id before the trigger runs. Two transactions can therefore receive
ids in one order and acquire the advisory lock in the other order. The lower id can commit after the higher id,
so chain order and id order disagree.

The trigger lock made each hash computation safe but did not make the id assignment and the chain order one
critical section.

## Fix

- services/audit/audit.py: insert_event takes pg_advisory_xact_lock before the INSERT. The sequence id is
  assigned after the lock, so id order and chain order agree for every writer using the function.
- cmd_purge and the backfill insert path take the same lock.
- Regression test asserts the lock statement comes before the insert statement.

## Repair

The development database rows from id 1817 through the head were re-chained in id order under the documented
maintenance flag. 37 rows were updated, preserving every event and its payload; only prev_hash and row_hash
changed. The result was then verified.

This is a development repair. A production deployment would need an incident record and a disclosed repair
procedure, not a silent rewrite.

## Verification

- After the repair: 1517-event export, chain intact.
- After deploying the writer fix and one 20-concurrent run: 1547-event export, chain intact, zero failures.
