-- 021_a_job_remembers_what_it_waits_for.sql
--
-- **The gate's two fields, and why they were missing.**
--
-- `Job.pending_action_digest` was added to the dataclass when C5 built the approval route, and it was in
-- neither the INSERT nor the SELECT in `PostgresStore`. So a job that reached the gate and came back after a
-- restart read back with an empty digest, `jobs.decide()` refused every decision with `ApprovalDrift`, and the
-- refusal was correct: the job genuinely could not say what it had been waiting about. The memory store kept
-- the value, which is why the unit tests passed and the deployment would not have.
--
-- **Why the action is stored and not only its hash.** The digest is what the decision is checked against, and
-- it is deliberately not reversible. The Review screen has to show the words behind the hash: an approval
-- screen that shows a hash and asks for a decision is asking somebody to approve something they cannot read.
-- Both columns are needed and neither substitutes for the other.
--
-- **Why `pending_action` is covered by the chain and this column is not the record.** The approval record
-- itself goes into `audit_events.payload`, which `payload_sha256` covers. These two columns are the job's own
-- state while it waits: they say what is in front of somebody right now, and the digest is cleared when the
-- decision is made. The permanent statement about what was approved is the chain row.

alter table jobs add column if not exists pending_action_digest text;

comment on column jobs.pending_action_digest is
  'SHA-256 of the exact content being approved, held only while the job is in awaiting_approval. A decision '
  'quoting a different digest is refused rather than recorded, so an approval cannot be re-pointed at content '
  'the person never saw. Cleared on approval, because the digest belongs to the act that was approved and '
  'leaving it set would let one approval be replayed against a later gate.';

alter table jobs add column if not exists pending_action jsonb;

comment on column jobs.pending_action is
  'What the job is waiting to have approved, in words, alongside the digest. The digest is what the route '
  'checks; this is what the screen renders. An approval screen showing only a hash asks somebody to approve '
  'something they cannot read, so both are stored and neither replaces the other.';
