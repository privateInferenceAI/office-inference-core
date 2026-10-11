-- 004_purge_needs_the_hatch.sql
--
-- A tombstone claims that rows were removed on purpose. If one can be written without the maintenance
-- setting switched on, the record can hold a purge claim for a purge that never happened, which is
-- exactly what occurred on 2026-10-11: the first version of `audit purge` ran with autocommit on, so
-- SET LOCAL was ignored, the tombstone inserted (nothing blocks an insert), and the delete was refused
-- by the trigger it was supposed to bypass.
--
-- The 003 constraint lets a tombstone exist. This one makes it require the same deliberate act that
-- the deletion does, so a bare tombstone is refused rather than merely discouraged.

create or replace function audit_purge_requires_the_hatch() returns trigger
language plpgsql as $$
begin
  if current_setting('oi.allow_audit_maintenance', true) = 'on' then
    return new;
  end if;
  raise exception 'a purge tombstone requires the maintenance setting'
    using hint = 'Run the purge inside a transaction that sets oi.allow_audit_maintenance = on.';
end $$;

drop trigger if exists audit_events_purge_needs_hatch on audit_events;
create trigger audit_events_purge_needs_hatch before insert on audit_events
  for each row when (new.event_type = 'purge')
  execute function audit_purge_requires_the_hatch();

comment on function audit_purge_requires_the_hatch() is
  'A tombstone is a claim that rows were deliberately removed, so it needs the same hatch the
   removal needs. Without this, the record can hold a purge claim for a purge that never ran.';
