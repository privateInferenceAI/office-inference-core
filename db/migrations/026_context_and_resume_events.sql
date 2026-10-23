-- The record grows two event types: context_trim and resume.
--
-- The runtime writes context_trim when older messages leave the live prompt, and the startup recovery writes
-- resume when a restarted process picks up a job that was left running. The audit tool's writer accepts both,
-- but the table's event-type check was a closed list from the first migration and refused them. That refusal
-- is the correct direction for an unknown event type; this migration is where the two known types join the list.
--
-- Dropping and re-adding the constraint in one migration is deliberate: the table is append-only for normal
-- writers and this is a schema decision, not a row edit. No row is rewritten.

alter table audit_events
  drop constraint if exists audit_events_event_type_check;

alter table audit_events
  add constraint audit_events_event_type_check
  check (event_type in (
    'model_call', 'tool_call', 'approval', 'access', 'config_change', 'login',
    'purge', 'reattestation', 'context_trim', 'resume'
  ));
