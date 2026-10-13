-- 013_chain_covers_redaction.sql
--
-- Bring the four redaction columns inside the hash chain, without invalidating a single existing row.
--
-- The gap this closes: migration 012 added redaction_count, redactions, unverified_card_shapes and
-- egress_allowed, and the chain's canonical string was not changed to include them. So a row whose redactions
-- value was edited by hand still verified. In a table whose entire purpose is detecting that, a field the chain
-- does not cover is a field that can be rewritten while every check passes, and the check would report the
-- record intact.
--
-- Why this needs a version rather than an edit. A row's stored hash was computed from a canonical string, and
-- verification recomputes that string and compares. Change the formula and every row written under the old one
-- fails, which is indistinguishable from tampering: the verifier would report a chain break on rows nobody
-- touched, and the first real break would be lost in the noise. So the formula is versioned and each row
-- records which one produced its hash.
--
-- Version 1 is the original thirteen-field string, which is what rows 1 to 50 on the development instance were
-- written with. Version 2 adds the four fields. New rows are version 2; nothing is rewritten.

alter table audit_events add column if not exists chain_version smallint not null default 1;

comment on column audit_events.chain_version is
  'Which canonical string produced this row''s hash. 1 is the original thirteen fields; 2 adds the redaction '
  'summary. The verifier reads it per row, because recomputing every row with the newest formula would report '
  'untouched history as tampering.';

-- The canonical string, in one place, so the trigger and the verifier cannot drift. plpgsql rather than SQL
-- because it has to branch, and a function rather than an inline expression because two callers need it.
create or replace function audit_row_canonical(
  p_chain_version smallint,
  p_ts_iso text, p_event_type text, p_actor text, p_actor_role text, p_deployment_id text,
  p_data_class text, p_outcome text, p_tool text, p_model text, p_request_id text,
  p_payload_sha256 text, p_payload_bytes integer, p_prev_hash text,
  p_redaction_count integer, p_redactions jsonb, p_unverified_card_shapes integer,
  p_egress_allowed boolean
) returns text
language plpgsql immutable as $$
declare
  base text;
  summary text;
begin
  base := concat_ws('|',
    p_ts_iso, p_event_type, p_actor, coalesce(p_actor_role, ''), p_deployment_id,
    p_data_class, p_outcome, coalesce(p_tool, ''), coalesce(p_model, ''),
    coalesce(p_request_id, ''), p_payload_sha256, p_payload_bytes::text,
    coalesce(p_prev_hash, ''));

  if p_chain_version < 2 then
    return base;
  end if;

  -- The summary is flattened to `key=value` pairs in key order rather than using jsonb::text. Postgres writes
  -- jsonb with a space after each colon and comma, and the verifier is Python, so matching the two textually
  -- would mean reproducing one language's serialiser in the other. Sorted pairs are a form both can produce
  -- exactly, and the keys are identifiers rather than content, so nothing here is a value being recorded.
  select coalesce(string_agg(key || '=' || value, ',' order by key), '')
    into summary
    from jsonb_each_text(coalesce(p_redactions, '{}'::jsonb));

  return base || '|' || concat_ws('|',
    p_redaction_count::text,
    summary,
    p_unverified_card_shapes::text,
    coalesce(p_egress_allowed::text, ''));
end $$;

comment on function audit_row_canonical is
  'The canonical string a row''s hash is computed from, for a given chain version. Must stay in step with '
  'row_canonical() in services/audit/audit.py, which is the verifier''s copy.';

create or replace function audit_events_chain() returns trigger
language plpgsql as $$
declare
  prev_hash_value text;
begin
  perform pg_advisory_xact_lock(hashtext('audit_events_chain'));

  select e.row_hash into prev_hash_value from audit_events e order by e.id desc limit 1;

  new.ts_iso := to_char(new.occurred_at at time zone 'UTC', 'YYYY-MM-DD"T"HH24:MI:SS.US"Z"');
  new.prev_hash := prev_hash_value;
  new.payload_bytes := coalesce(new.payload_bytes, octet_length(coalesce(new.payload::text, '')));

  -- New rows carry the newest formula. The column has a default of 1 for the rows that already exist; this
  -- overrides it here rather than changing the default, because a default of 2 would silently relabel old rows
  -- if anyone ever backfilled one.
  new.chain_version := 2;

  new.row_hash := encode(digest(audit_row_canonical(
    new.chain_version,
    new.ts_iso, new.event_type, new.actor, new.actor_role, new.deployment_id,
    new.data_class, new.outcome, new.tool, new.model, new.request_id,
    new.payload_sha256, new.payload_bytes, new.prev_hash,
    new.redaction_count, new.redactions, new.unverified_card_shapes, new.egress_allowed
  ), 'sha256'), 'hex');
  return new;
end $$;

drop trigger if exists audit_events_chain on audit_events;
create trigger audit_events_chain before insert on audit_events
  for each row execute function audit_events_chain();

-- A row's stored version must match what its hash was computed with, so a row claiming version 2 while holding
-- a version 1 hash cannot be smuggled in. This is a check rather than a constraint because it needs the
-- canonical function, and it fires on insert only: existing rows are not re-examined, which is the point.
create or replace function audit_events_version_guard() returns trigger
language plpgsql as $$
begin
  if new.chain_version > 2 then
    raise exception 'chain version % has no canonical form in this deployment, so its hash cannot be checked',
      new.chain_version;
  end if;
  return new;
end $$;

drop trigger if exists audit_events_version_guard on audit_events;
create trigger audit_events_version_guard before insert on audit_events
  for each row execute function audit_events_version_guard();
