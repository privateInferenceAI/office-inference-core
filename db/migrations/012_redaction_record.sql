-- 012_redaction_record.sql
--
-- What was removed from a request before it left, recorded as metadata rather than as content.
--
-- The requirement is that PII is redacted before any frontier-bound request leaves AND that the redaction is
-- recorded. The first half worked and the second half did not, and the reason is worth writing down.
--
-- The redaction summary was being put in the audit payload, and the default content mode is `hash`, which
-- stores a hash of the payload and not the payload. So on the arrangement the system ships with and recommends,
-- the record proved that a call happened and proved nothing about the redaction: an operator asking "was
-- anything removed from this request" got a null. The probe found it on the first run.
--
-- The fix is that a redaction summary is not content. It says how many things of which kind were removed and
-- never what they were, so storing it is not storing the client's data, and it must not be governed by a
-- setting about whether to store content.

alter table audit_events add column if not exists redaction_count integer not null default 0;

alter table audit_events add column if not exists redactions jsonb;

alter table audit_events add column if not exists unverified_card_shapes integer not null default 0;

-- Whether the request was permitted to leave at all, which is a different fact from the outcome. A denied
-- request has outcome 'denied' and egress_allowed false; a request that never attempted egress has this null.
alter table audit_events add column if not exists egress_allowed boolean;

comment on column audit_events.redaction_count is
  'How many identifiers were replaced before this request left. Metadata about the request, not content, so it
   is recorded whatever the content mode: a deployment on the default hash mode would otherwise have no record
   that anything was redacted.';
comment on column audit_events.redactions is
  'Counts by kind, as {"ssn": 2, "card": 1}. Kinds and counts only, never values: a record that keeps what it
   redacted has redacted nothing.';
comment on column audit_events.unverified_card_shapes is
  'Long numbers that failed the card check digit and were redacted anyway. Counted separately because "a card
   was removed" and "something shaped like a card was removed" are different claims and only one is certain.';
comment on column audit_events.egress_allowed is
  'Whether the data-class gate permitted this request to leave for a frontier model. Null where no egress was
   attempted, false where it was refused.';
