-- 023_an_effect_is_written_once.sql
--
-- **WF-IDEMPOTENT-001, and the only form of exactly-once this system can honestly offer.**
--
-- A retried step must not repeat an effect it already had. Whether that is possible depends entirely on where
-- the effect happens, and the two answers are different enough that the record has to distinguish them:
--
--   * **An effect the engine writes** is inside the step's transaction, so the effect and the state change
--     commit together or not at all, and a retry after a crash finds the effect already present. There is no
--     window to close because there is no window. This is `mode = 'transactional'`.
--   * **An effect outside our transaction** cannot be made exactly-once by us, and an engine that claims
--     otherwise is claiming a property of somebody else's system. What we can do is hand the far side a key
--     that is **stable across attempts** and record what it reported, so a retry is deduplicable and the
--     record shows how many times the effect was applied rather than how many times the step ran. This is
--     `mode = 'external'`.
--
-- **The primary key is the mechanism, not decoration.** `(job_id, effect_key)` with `on conflict do nothing`
-- and `returning` is what turns "apply this effect" into an operation that can be attempted any number of
-- times and applied at most once. An engine that keys the effect by the attempt number writes a new row on
-- every retry and duplicates the effect on the first failure — that is the defect this row exists for, and it
-- looks like working code.
--
-- **`effect_key` is derived from the job and the step, never from the attempt.** It is
-- `sha256(job_id + '|' + step_name)` in `services/workflow/engine.py`, so it survives a retry, a resume and a
-- restart of the process. A key that changed between attempts would protect nothing.
--
-- **What this table is not.** It is not an outbox and it does not retry anything. A crash between an external
-- effect and the row that records it leaves an effect that happened and no local evidence of it, and the
-- engine says so in those words (`effect_unknown`) rather than re-running the step and hoping. An honest gap
-- that a person can see is worth more than a silent duplicate.

create table if not exists workflow_effects (
  job_id      text not null references jobs (job_id) on delete cascade,
  effect_key  text not null,
  -- The plan step that caused it. A name rather than the step row's id, because the interesting question
  -- afterwards is "which piece of work did this" and that survives the row being retried.
  step_name   text not null,
  mode        text not null check (mode in ('transactional', 'external')),
  -- `applied` when this write is the one that applied it, `already_applied` when the far side reported the
  -- key had been seen. The distinction is the whole point: two attempts, one application.
  state       text not null check (state in ('applied', 'already_applied')),
  detail      jsonb not null default '{}'::jsonb,
  recorded_at timestamptz not null default now(),
  primary key (job_id, effect_key)
);

create index if not exists workflow_effects_step_idx on workflow_effects (job_id, step_name);

comment on table workflow_effects is
  'The effects a workflow step had, keyed so that a retry, a resume or a restart cannot apply one twice. '
  'Transactional effects are written in the same transaction as the step state change; external effects are '
  'keyed so the far side can deduplicate. A crash between an external effect and this row leaves no local '
  'evidence, and the engine records that as effect_unknown rather than repeating the effect.';
