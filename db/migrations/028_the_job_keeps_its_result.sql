-- The job keeps its result.
--
-- **Why a column.** A job's result lived only in the process that ran it. The in-memory object held it, so
-- the immediate HTTP response looked right, and a later read from the durable store returned a succeeded job
-- with no result at all. The n8n integration exposed it: a workflow that starts a governed job and polls it
-- got `state: succeeded, result: {}`. A record that says work happened and cannot say what it produced is
-- half a record.
--
-- jsonb rather than text, because the result is produced by our code and read by our code; a schema that
-- refuses a shape is a second place where the contract is decided.

alter table jobs add column if not exists result jsonb;

comment on column jobs.result is
  'The job''s result, written when a terminal state is reached. Empty for a job that produced none.';
