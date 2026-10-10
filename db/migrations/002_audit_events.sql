-- 002_audit_events.sql
--
-- The audit sink. One append-only record of every model call, tool call, approval, and access
-- event, with a hash chain so tampering is detectable rather than merely discouraged.
--
-- Why not just read LiteLLM's spend logs: those are the gateway's operational record. This table
-- is the system-wide one, it covers actions and approvals as well as model calls, and its integrity
-- can be checked by a third party from an export alone.

create extension if not exists pgcrypto;

create table if not exists schema_migrations (
  filename   text primary key,
  checksum   text not null,
  applied_at timestamptz not null default now()
);

create table if not exists audit_events (
  id             bigserial primary key,
  occurred_at    timestamptz not null default now(),
  ts_iso         text not null,
  event_type     text not null check (event_type in
                   ('model_call','tool_call','approval','access','config_change','login')),
  actor          text not null,
  actor_role     text,
  deployment_id  text not null,
  data_class     text not null default 'internal'
                   check (data_class in ('public','internal','confidential','regulated')),
  outcome        text not null default 'ok'
                   check (outcome in ('ok','denied','error','pending')),
  tool           text,
  model          text,
  request_id     text,
  approval       jsonb,
  payload_sha256 text not null,
  payload_bytes  integer,
  payload        jsonb,
  prev_hash      text,
  row_hash       text not null
);

comment on table audit_events is
  'Append-only. UPDATE and DELETE raise an exception unless oi.allow_audit_maintenance is on.';
comment on column audit_events.payload_sha256 is
  'SHA-256 of the canonical payload. Stored even when the payload itself is not kept.';
comment on column audit_events.row_hash is
  'SHA-256 over this row plus prev_hash, forming a chain that an export can be verified against.';

create index if not exists audit_events_occurred_idx on audit_events (occurred_at desc);
create index if not exists audit_events_type_idx on audit_events (event_type, occurred_at desc);
create index if not exists audit_events_actor_idx on audit_events (actor, occurred_at desc);
create unique index if not exists audit_events_model_call_request_idx
  on audit_events (request_id) where request_id is not null and event_type = 'model_call';

-- Append-only enforcement. A trigger rather than a privilege, because the table owner is also the
-- application role in a single-box deployment, and an owner can always re-grant its own rights.
-- A maintenance escape hatch exists for retention purges, and using it is a deliberate act.
create or replace function audit_events_immutable() returns trigger
language plpgsql as $$
begin
  if current_setting('oi.allow_audit_maintenance', true) = 'on' then
    return coalesce(new, old);
  end if;
  raise exception 'audit_events is append-only; % is not permitted', tg_op
    using hint = 'Set oi.allow_audit_maintenance = on for a planned retention purge.';
end $$;

drop trigger if exists audit_events_no_update on audit_events;
create trigger audit_events_no_update before update on audit_events
  for each row execute function audit_events_immutable();

drop trigger if exists audit_events_no_delete on audit_events;
create trigger audit_events_no_delete before delete on audit_events
  for each row execute function audit_events_immutable();

-- Hash chain. An advisory lock serializes inserts so two concurrent writers cannot fork the chain.
create or replace function audit_events_chain() returns trigger
language plpgsql as $$
declare
  prev_hash_value text;
  canonical text;
begin
  perform pg_advisory_xact_lock(hashtext('audit_events_chain'));

  select e.row_hash into prev_hash_value from audit_events e order by e.id desc limit 1;

  new.ts_iso := to_char(new.occurred_at at time zone 'UTC', 'YYYY-MM-DD"T"HH24:MI:SS.US"Z"');
  new.prev_hash := prev_hash_value;
  new.payload_bytes := coalesce(new.payload_bytes, octet_length(coalesce(new.payload::text, '')));

  canonical := concat_ws('|',
    new.ts_iso, new.event_type, new.actor, coalesce(new.actor_role, ''), new.deployment_id,
    new.data_class, new.outcome, coalesce(new.tool, ''), coalesce(new.model, ''),
    coalesce(new.request_id, ''), new.payload_sha256, new.payload_bytes::text,
    coalesce(new.prev_hash, ''));

  new.row_hash := encode(digest(canonical, 'sha256'), 'hex');
  return new;
end $$;

drop trigger if exists audit_events_chain on audit_events;
create trigger audit_events_chain before insert on audit_events
  for each row execute function audit_events_chain();

-- A convenience view for the Activity screen in Phase 5: the audit record in plain language.
create or replace view audit_feed as
select id, occurred_at, event_type, actor, actor_role, data_class, outcome,
       tool, model, request_id,
       case
         when event_type = 'model_call' then coalesce(model, 'a model') || ' answered'
         when event_type = 'tool_call'  then coalesce(tool, 'a tool') || ' was used'
         when event_type = 'approval'   then 'a person ' || outcome || ' an action'
         when event_type = 'access'     then 'a document was read'
         else event_type
       end as summary,
       payload_sha256, row_hash
from audit_events
order by id desc;
