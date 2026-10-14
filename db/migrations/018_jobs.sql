-- 018_jobs.sql
--
-- A job: work with an identity, a state, and a history that outlives the connection that started it.
--
-- Why this is not a longer answer. POST /ask is one round trip that ends when the answer does. The
-- requirement here is a sentence with several parts to it: work on this for thirty minutes, let it fail four
-- times, and on the fifth failure change the approach. That needs a record that survives a restart, one row
-- per attempt with its typed failure, an escalation rule, progress that can be watched while it runs, and
-- somewhere to stop it. None of those can live in a request.
--
-- Why the job carries a SNAPSHOT of the role and roots rather than a join to the session. The same reason
-- 017 gives for its own snapshot. A job run against last week's permission has to be readable as at the time
-- it ran. A permission read live at display time is a permission that has since moved, and the record would
-- then describe a decision that was never made.
--
-- Why the budget is stored rather than held in memory. A job that outlives its process is the whole point of
-- durability, and a budget that resets on restart is a budget that can be spent twice.

create table if not exists jobs (
  job_id         text primary key,
  deployment_id  text not null,
  -- Who asked. The subject and the role as they were when the job was created.
  subject        text not null,
  actor_role     text not null,
  access_roots   jsonb not null,
  session_id     text,
  -- What was asked for. `task` is the identifier the manifest declares; `input` is the person's words.
  task           text not null,
  input          text not null,
  -- The machine. Small on purpose: a client has to be able to draw every state and know what may follow.
  state          text not null default 'queued'
                   check (state in ('queued','running','paused','awaiting_approval','succeeded','failed','cancelled')),
  -- Why it ended, in the case that it did not succeed. A typed reason rather than prose, because a client
  -- branches on it and an operator counts by it.
  outcome_reason text,
  -- The ceiling in force, so a reader can tell a job that finished from one that was stopped at its limit.
  time_budget_s  integer not null default 1800,
  attempt_budget integer not null default 5,
  created_at     timestamptz not null default now(),
  started_at     timestamptz,
  finished_at    timestamptz,
  -- The data class the job ran under, recorded rather than inferred. An auditor asking "did this leave the
  -- building" needs the class that was in force at the time, not the one the manifest declares today.
  data_class     text not null default 'internal'
);

-- One row per attempt. This is the table that makes "let it fail four times" visible rather than a claim.
create table if not exists job_steps (
  step_id       text primary key,
  job_id        text not null references jobs (job_id) on delete cascade,
  attempt       integer not null,
  -- What this attempt did. A name, so a client can label it and an operator can count by it.
  name          text not null,
  state         text not null default 'running'
                  check (state in ('running','succeeded','failed')),
  -- **The typed failure, never a raw provider string.** A validation error can echo input fragments and
  -- this table is readable by anyone who can read the job.
  failure_kind  text,
  failure_detail text,
  -- Whether trying again could help. The decision, recorded at the moment it was made, because that is the
  -- question anybody reading this table afterwards will ask.
  retryable     boolean,
  lane          text,
  model         text,
  started_at    timestamptz not null default now(),
  finished_at   timestamptz,
  duration_ms   integer,
  unique (job_id, attempt)
);

create index if not exists job_steps_job_idx on job_steps (job_id, attempt);

-- The event log, and the reason it is a table rather than a socket.
--
-- A client that closes the tab and comes back has to be able to catch up. If the only copy of "what happened"
-- was pushed down a connection, then closing the tab loses it. `seq` is monotonic per job and assigned in the
-- same transaction that writes the row, so ?since=<seq> is a resumable read and a gap means something else is
-- writing.
create table if not exists job_events (
  job_id     text not null references jobs (job_id) on delete cascade,
  seq        integer not null,
  -- The frame name, matching the stream: progress, step, notice, degraded, done, error.
  event      text not null,
  data       jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default now(),
  primary key (job_id, seq)
);

create index if not exists jobs_state_idx on jobs (state, created_at desc);
create index if not exists jobs_subject_idx on jobs (subject, created_at desc);
