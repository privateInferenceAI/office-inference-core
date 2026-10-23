-- 025_the_approval_is_in_the_chain.sql
--
-- Bring audit_events.approval inside the hash chain as version 3, without invalidating a single row written
-- under version 1 or 2.
--
-- The gap this closes is AUD-APPROVAL-001, and it is the same shape as the one 013 closed for the four
-- redaction columns. `approval` is a jsonb column with an approval event type and it was not among the fields
-- the canonical string covered, so a value written there could be edited by hand while every chain check passed
-- and reported the record intact. The defect was found on 2026-10-16, before any writer existed; C5 is the first
-- writer, so the fix lands before the first row rather than after it.
--
-- **Why the JSON is hashed rather than cast into the canonical string.** The obvious fix is to append
-- `coalesce(new.approval::text, '')` and it does not work. Postgres renders jsonb with a space after every
-- colon and comma, drops duplicate keys, and does not preserve the key order a caller wrote, while the verifier
-- is Python and hashes `canonical_json`, which sorts keys and separates without spaces. Matching the two
-- textually would mean reproducing one language's serialiser inside the other, and the day either changed,
-- every row would fail verification at once -- 013's comment says exactly this about the redaction summary. A
-- digest is the one form both languages produce exactly from the same value, so the column holds
-- sha256(canonical_json(approval)) and the canonical string covers the digest. Postgres never computes it:
-- hashing JSON in SQL would hash a different byte string than the verifier does, which is the drift this whole
-- design exists to avoid. The writer is the only place that can produce it, and the trigger refuses a row that
-- arrived without one.
--
-- **Why a version and not an edit.** Identical to 013: a row's stored hash was computed from a canonical string
-- and verification recomputes that string. Change the formula and every row written under the old one fails,
-- which is indistinguishable from tampering, and the first real break would be lost in the noise. So the
-- formula is versioned, each row records which version produced its hash, and nothing is rewritten. Rows 1..N
-- keep 1 or 2 and keep verifying.
--
-- The chain's history is now: 1 = the original thirteen fields, 2 = the redaction summary, 3 = the approval
-- digest.

alter table audit_events add column if not exists approval_sha256 text;

comment on column audit_events.approval_sha256 is
  'SHA-256 of canonical_json(approval), computed by the writer and covered by chain version 3. Postgres cannot '
  'compute it: jsonb::text and the verifier''s canonical_json serialise the same value differently, so a digest '
  'both languages reproduce from the value is the only exact comparison. Null exactly when approval is null.';

-- The eighteen-argument form from 013 is dropped rather than left beside the new one. `create or replace` with a
-- changed argument list creates an OVERLOAD, so keeping it would leave two functions named audit_row_canonical
-- whose only difference is whether an approval is covered, and a caller passing eighteen arguments would
-- silently get the one that cannot see it. plpgsql bodies are not parsed at creation, so dropping this does not
-- disturb the trigger function that is about to be replaced.
drop function if exists audit_row_canonical(
  smallint, text, text, text, text, text, text, text, text, text, text, text,
  integer, text, integer, jsonb, integer, boolean);

-- The canonical string, in one place, so the trigger and the verifier cannot drift. plpgsql rather than SQL
-- because it has to branch, and a function rather than an inline expression because two callers need it.
create or replace function audit_row_canonical(
  p_chain_version smallint,
  p_ts_iso text, p_event_type text, p_actor text, p_actor_role text, p_deployment_id text,
  p_data_class text, p_outcome text, p_tool text, p_model text, p_request_id text,
  p_payload_sha256 text, p_payload_bytes integer, p_prev_hash text,
  p_redaction_count integer, p_redactions jsonb, p_unverified_card_shapes integer,
  p_egress_allowed boolean, p_approval_sha256 text
) returns text
language plpgsql immutable as $$
declare
  base text;
  summary text;
  version_two text;
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

  version_two := base || '|' || concat_ws('|',
    p_redaction_count::text,
    summary,
    p_unverified_card_shapes::text,
    coalesce(p_egress_allowed::text, ''));

  if p_chain_version < 3 then
    return version_two;
  end if;

  -- coalesce, not a bare argument to concat_ws. **That distinction is the whole difference between a null and an
  -- empty field here**: concat_ws SKIPS null arguments and with them the separator, so a row with no approval
  -- would lose the last field entirely and its hash could never match a verifier that appends '|'. The two
  -- languages must agree on null, so null is written as the empty string on both sides.
  return version_two || '|' || coalesce(p_approval_sha256, '');
end $$;

comment on function audit_row_canonical is
  'The canonical string a row''s hash is computed from, for a given chain version. Must stay in step with '
  'row_canonical() in services/audit/audit.py, which is the verifier''s copy. Version 3 adds the approval digest.';

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

  -- An approval with no digest beside it would be a field outside the chain, which is the defect this migration
  -- closes, so the insert is refused rather than written with a hole in it. The trigger cannot fill the digest
  -- in: it would have to hash jsonb::text, which is a different byte string from the one the verifier hashes,
  -- and a wrong digest is worse than a null because it fails verification silently years later. A row that
  -- carries an approval must therefore come from a writer that can compute the digest.
  if new.approval is not null and new.approval_sha256 is null then
    raise exception 'an event with an approval must carry approval_sha256, or the approval would sit outside the chain'
      using hint = 'Compute sha256 of the canonical JSON of the approval in the writer; services/audit/audit.py does.';
  end if;

  -- New rows carry the newest formula. The column has a default of 1 for the rows that already exist; this
  -- overrides it here rather than changing the default, because a higher default would silently relabel old rows
  -- if anyone ever backfilled one.
  new.chain_version := 3;

  new.row_hash := encode(digest(audit_row_canonical(
    new.chain_version,
    new.ts_iso, new.event_type, new.actor, new.actor_role, new.deployment_id,
    new.data_class, new.outcome, new.tool, new.model, new.request_id,
    new.payload_sha256, new.payload_bytes, new.prev_hash,
    new.redaction_count, new.redactions, new.unverified_card_shapes, new.egress_allowed,
    new.approval_sha256
  ), 'sha256'), 'hex');
  return new;
end $$;

drop trigger if exists audit_events_chain on audit_events;
create trigger audit_events_chain before insert on audit_events
  for each row execute function audit_events_chain();

-- A row's stored version must match what its hash was computed with, so a row claiming version 3 while holding
-- a version 2 hash cannot be smuggled in. This is a check rather than a constraint because it needs the
-- canonical function, and it fires on insert only: existing rows are not re-examined, which is the point.
create or replace function audit_events_version_guard() returns trigger
language plpgsql as $$
begin
  if new.chain_version > 3 then
    raise exception 'chain version % has no canonical form in this deployment, so its hash cannot be checked',
      new.chain_version;
  end if;
  return new;
end $$;

drop trigger if exists audit_events_version_guard on audit_events;
create trigger audit_events_version_guard before insert on audit_events
  for each row execute function audit_events_version_guard();

-- **A re-attestation is not an approval.** It is a statement about an earlier event, appended rather than
-- replacing it, so it gets its own type. That is a DDL change and it does not touch a single existing row, which
-- is what makes it safe under the append-only trigger (design section 5.3).
alter table audit_events drop constraint if exists audit_events_event_type_check;
alter table audit_events add constraint audit_events_event_type_check
  check (event_type in ('model_call', 'tool_call', 'approval', 'access', 'config_change', 'login',
                        'purge', 'reattestation'));

comment on constraint audit_events_event_type_check on audit_events is
  'purge is the tombstone a retention purge writes: payload names what was removed, prev_hash is the head before '
  'and row_hash is the head after. reattestation is a statement about an earlier event, appended rather than '
  'replacing it: payload carries supersedes_event_id and supersedes_row_hash, original_assurance stays what the '
  'original says, and limits records what the new statement does not establish. audit_feed renders it by name '
  'until a sentence is designed for it, which is honest rather than wrong.';
