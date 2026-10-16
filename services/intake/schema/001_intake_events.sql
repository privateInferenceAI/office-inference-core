-- 001_intake_events.sql
--
-- One row per arrival, whether or not the content is new. `CON-INTAKE-001` and `CON-DUPE-001`.
--
-- **Why intake owns a table rather than writing into the audit chain.** The chain is the record of what the
-- system *did*: model calls, tool calls, approvals, access. An arrival is a fact about the world that the
-- system observed, and it is written before anything decides what to do with it. Folding arrivals into the
-- chain would mean an event type per source and a chain row for a message nobody acted on.
--
-- **Why the file lives beside the service.** `db/migrations/` is owned by another writer this phase, and two
-- writers in one migrations directory is a conflict with no upside. The table is intake's and the file
-- travels with the code that writes it. Moving it into `db/migrations/` later is a rename.
--
-- The columns are the fields in `provenance.COLUMNS`, and the test asserts the two lists are equal. A record
-- whose keys and whose table drift apart writes a row with a column missing, and nobody finds out until an
-- auditor asks for a field that was never stored.
--
-- `content_sha256` is the sha256 **of the file as it landed under the documents root**, which is the same
-- computation `services/ingestion/ingest.py` makes of the same file. That equality is what makes this row
-- evidence about a document rather than a note about an email.

create table if not exists intake_events (
  -- Stable and derived rather than random: the same message read twice after a crash produces the same id,
  -- so a re-poll is idempotent instead of a second row.
  intake_id           text primary key,
  deployment_id       text not null,

  -- Where it came from. A folder arrival has no mailbox and says so rather than borrowing one.
  channel             text not null,
  mailbox             text,
  folder              text,
  message_id          text,
  provider_id         text,

  -- The two clocks. `received_at` is what the mail system states and `observed_at` is when this service saw
  -- it. An auditor asks about the first, and the gap between them is the second fact.
  received_at         timestamptz,
  observed_at         timestamptz not null,

  -- The envelope sender authorised the delivery and the header sender is what a person sees. They can
  -- differ, and the difference is either forwarding or the oldest trick in invoice fraud.
  sender_from         text,
  sender_envelope     text,
  sender_reply_to     text,

  -- What the receiving mail system concluded, not what we concluded about it.
  auth_spf            text,
  auth_dkim           text,
  auth_dmarc          text,

  verdict             text not null,
  verdict_reasons     jsonb not null default '[]'::jsonb,
  allowlisted         boolean,

  content_sha256      text,
  bytes               bigint,
  relpath             text,
  filename            text,
  media_type          text,
  -- True when the ingestion worker will not read this format, so a document that will never appear in the
  -- index is a recorded fact rather than a silence.
  unreadable_by_ingest boolean,

  raw_message_sha256  text,

  -- The first row with this content, when this one is a re-send or a forward. A duplicate is recorded rather
  -- than dropped, because "it never arrived" and "it arrived twice" are different facts.
  duplicate_of        text,
  status              text not null,

  -- The signals that fired, by name. Never the matched text: a record that kept the instruction verbatim
  -- would hold an attacker's prose beside the client's data.
  content_flags       jsonb not null default '[]'::jsonb,
  requires_review     boolean not null default true,

  -- What was read to produce this row, and how the file was protected. Never the credential.
  credential_path     text,
  credential_mode     text
);

-- The dedupe scan, which is the one query on the hot path.
create index if not exists intake_events_content_idx on intake_events (content_sha256);

-- `verify-manifest` joins this to `ingest_manifest` on relpath, and that join is the requirement.
create index if not exists intake_events_relpath_idx on intake_events (relpath);

-- The volume floor counts arrivals in a window.
create index if not exists intake_events_received_idx on intake_events (received_at);
