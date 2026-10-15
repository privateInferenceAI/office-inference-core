-- 020_a_job_holds_a_hash_too.sql
--
-- **The second place a credential was kept as a column value.**
--
-- Migration 019 stopped the credential being the session identifier. This one deals with what the jobs table
-- was doing with the same value: `jobs.session_id` held the credential the caller presented, in plain, because a
-- job has to run after the request that started it has ended and the only thing available at creation time was
-- the credential. So a job row was a working credential, and a database read was an impersonation.
--
-- **A job keeps the hash and resolves the session when it runs.** That is the same shape as 019: the value that
-- authenticates is never stored, and the value that is stored cannot authenticate. A job whose session has
-- expired or been revoked then fails with `no_session`, which is the honest outcome and the one the state
-- machine already has a terminal path for.
--
-- Measured before it was fixed: every row in this table held a live credential for a declared subject, and one
-- of them was still valid at the time it was read. The rows are truncated rather than migrated, because a
-- credential cannot be un-stored by adding a column, and a job that cannot name the session it ran under is not
-- a record worth keeping in the shape it is in.

alter table jobs add column if not exists credential_sha256 text;

comment on column jobs.credential_sha256 is
  'SHA-256 of the credential that created the job. **The credential itself is never stored.** A job resolves '
  'the session by this hash when it runs, so a job whose session has expired fails honestly rather than running '
  'under a stored credential.';

comment on column jobs.session_id is
  'Unused. It held the credential before migration 020 and is kept only so the column has not silently '
  'disappeared from a table a reader may have notes about. A future migration may drop it.';

-- **The rows that predate this stop being readable as credentials.** Truncated rather than updated, because the
-- plain value is in the row and adding a column does not remove it. The audit chain carries the record of what
-- those jobs did, which is where a record belongs, and it is hash-linked so it stays verifiable.
delete from job_events;
delete from job_steps;
delete from jobs;

-- And the sessions those credentials belong to. The same reasoning as 019, applied to the sessions a job was
-- created under: their credentials were stored in plain in this table, so they are spent.
update sessions
   set revoked_at = coalesce(revoked_at, now())
 where credential_sha256 is null
   and revoked_at is null;
