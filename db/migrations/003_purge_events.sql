-- 003_purge_events.sql
--
-- A retention purge is a deliberate hole in the chain, and a hole nobody explained is
-- indistinguishable from tampering. This adds the event type a tombstone uses, so a purge writes a
-- row that says what was removed and carries the chain head from before it happened.
--
-- Why the tombstone goes in before the rows come out: the row's own prev_hash is the head before the
-- purge and its row_hash is the head after, so the two hashes the requirement asks for are the row's
-- own columns rather than a copy that could drift from them.
--
-- The other six types are unchanged from 002.

alter table audit_events drop constraint if exists audit_events_event_type_check;
alter table audit_events add constraint audit_events_event_type_check
  check (event_type in ('model_call', 'tool_call', 'approval', 'access', 'config_change', 'login',
                        'purge'));

comment on constraint audit_events_event_type_check on audit_events is
  'purge is the tombstone a retention purge writes: payload names what was removed, prev_hash is the
   head before and row_hash is the head after.';

-- A purge reads rows, writes one tombstone, and deletes rows, all inside one transaction that turns
-- the maintenance setting on for its duration. This index makes the "everything older than the
-- retention window" scan an index scan rather than a table scan on a large record.
create index if not exists audit_events_purge_scan_idx on audit_events (occurred_at)
  where event_type <> 'purge';
