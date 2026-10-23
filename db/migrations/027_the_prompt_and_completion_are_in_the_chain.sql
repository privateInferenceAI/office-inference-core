-- 027_the_prompt_and_completion_are_in_the_chain.sql
--
-- Bring audit_events.prompt_sha256 and completion_sha256 inside the hash chain as version 4.
--
-- AUD-HASH-001 asks that editing the approval, prompt hash or completion hash column break the chain. Version 3
-- covered the approval digest but not the other two, so an operator could rewrite which prompt or completion a
-- model call used and every row would still verify. That is the same shape as AUD-APPROVAL-001.
--
-- **Why version 4 and not a rewrite.** Existing rows were hashed with version 3 and must keep verifying. The
-- formula is versioned; rows written from now on carry 4 and include two more digests. Nothing is rewritten.
--
-- The chain history is now: 1 = thirteen fields, 2 = redaction summary, 3 = approval digest, 4 = prompt and
-- completion digests.

drop function if exists audit_row_canonical(
  smallint, text, text, text, text, text, text, text, text, text, text, text,
  integer, text, integer, jsonb, integer, boolean, text);

create or replace function audit_row_canonical(
  p_chain_version smallint,
  p_ts_iso text, p_event_type text, p_actor text, p_actor_role text, p_deployment_id text,
  p_data_class text, p_outcome text, p_tool text, p_model text, p_request_id text,
  p_payload_sha256 text, p_payload_bytes integer, p_prev_hash text,
  p_redaction_count integer, p_redactions jsonb, p_unverified_card_shapes integer,
  p_egress_allowed boolean, p_approval_sha256 text,
  p_prompt_sha256 text, p_completion_sha256 text
) returns text
language plpgsql immutable as $$
declare
  base text;
  summary text;
  version_two text;
  version_three text;
begin
  base := concat_ws('|',
    p_ts_iso, p_event_type, p_actor, coalesce(p_actor_role, ''), p_deployment_id,
    p_data_class, p_outcome, coalesce(p_tool, ''), coalesce(p_model, ''),
    coalesce(p_request_id, ''), p_payload_sha256, p_payload_bytes::text,
    coalesce(p_prev_hash, ''));

  if p_chain_version < 2 then
    return base;
  end if;

  select coalesce(string_agg(key || '=' || value, ',' order by key), '')
    into summary
    from jsonb_each_text(coalesce(p_redactions, '{}'::jsonb));

  version_two := base || '|' || concat_ws('|',
    p_redaction_count::text,
    summary,
    p_unverified_card_shapes::text,
    coalesce(p_egress_allowed::text, ''));

  if p_chain_version < 3 then
    return version_two;
  end if;

  version_three := version_two || '|' || coalesce(p_approval_sha256, '');

  if p_chain_version < 4 then
    return version_three;
  end if;

  -- coalesce, not a bare argument to concat_ws. concat_ws skips nulls and their separators, so a row with no
  -- prompt would lose the field and could never match a verifier that appends '|'. Null is written empty on
  -- both sides.
  return version_three || '|' || coalesce(p_prompt_sha256, '') || '|' || coalesce(p_completion_sha256, '');
end $$;

comment on function audit_row_canonical is
  'The canonical string a row''s hash is computed from, for a given chain version. Must stay in step with '
  'row_canonical() in services/audit/audit.py. Version 4 adds prompt and completion digests.';

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

  if new.approval is not null and new.approval_sha256 is null then
    raise exception 'an event with an approval must carry approval_sha256, or the approval would sit outside the chain'
      using hint = 'Compute sha256 of the canonical JSON of the approval in the writer; services/audit/audit.py does.';
  end if;

  new.chain_version := 4;

  new.row_hash := encode(digest(audit_row_canonical(
    new.chain_version,
    new.ts_iso, new.event_type, new.actor, new.actor_role, new.deployment_id,
    new.data_class, new.outcome, new.tool, new.model, new.request_id,
    new.payload_sha256, new.payload_bytes, new.prev_hash,
    new.redaction_count, new.redactions, new.unverified_card_shapes, new.egress_allowed,
    new.approval_sha256, new.prompt_sha256, new.completion_sha256
  ), 'sha256'), 'hex');
  return new;
end $$;

drop trigger if exists audit_events_chain on audit_events;
create trigger audit_events_chain before insert on audit_events
  for each row execute function audit_events_chain();

create or replace function audit_events_version_guard() returns trigger
language plpgsql as $$
begin
  if new.chain_version > 4 then
    raise exception 'chain version % has no canonical form in this deployment, so its hash cannot be checked',
      new.chain_version;
  end if;
  return new;
end $$;

drop trigger if exists audit_events_version_guard on audit_events;
create trigger audit_events_version_guard before insert on audit_events
  for each row execute function audit_events_version_guard();
