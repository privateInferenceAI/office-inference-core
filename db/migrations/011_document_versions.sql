-- 011_document_versions.sql
--
-- Which document is current, and which documents look like the same document under another name.
--
-- The failure this exists for is named in the research as one of the two most likely wrong answers in a small
-- business: a real number from a superseded document. It passes every mechanical check, it passes entailment,
-- and it comes out certified, because a 2025 fee schedule is a real document with the fee on a real page.
--
-- The research prescribes the answer rather than leaving it to be designed: a version registry with an
-- explicit is_current flag, set at ingest and reviewable by a human, with a claim supported only by a
-- non-current document refused with the reason `superseded`. Nothing here guesses which of two documents is
-- current. A guess that is wrong is worse than a flag that is unresolved, because the guess is invisible and
-- the flag is not.

create table if not exists document_versions (
  -- The document's path relative to the documents root, the same identity the manifest and the chunks use.
  doc_id          text primary key,
  -- Whether an answer may rely on this document. Defaults to true so a corpus with no declarations behaves
  -- exactly as it did before this table existed: every document is current until somebody says otherwise.
  is_current      boolean not null default true,
  -- The document that replaces this one, when a human has said so.
  superseded_by   text,
  superseded_at   timestamptz,
  -- Why, in the operator's words. A registry entry without a reason is one nobody can review later.
  note            text,
  updated_at      timestamptz not null default now()
);

create index if not exists document_versions_current_idx on document_versions (is_current);

comment on table document_versions is
  'Which documents may be relied on. Set by a human through ingest.py --supersede, never inferred: two '
  'documents with similar content are flagged for review, and the flag is resolved by a declaration.';

-- A near-duplicate found at ingest, recorded rather than acted on.
--
-- The requirement is that the same document under two filenames is FLAGGED rather than indexed twice. Acting
-- automatically is the tempting version and it is wrong in both directions: deleting the second copy loses a
-- document a client put there deliberately, and keeping both silently means an answer cites whichever the
-- retriever happened to rank first. So the pair is recorded and a person decides.
create table if not exists duplicate_flags (
  id              bigserial primary key,
  -- Ordered so a pair is recorded once whichever document arrived second. The smaller path is always
  -- `doc_id` and the larger `other_doc_id`, which makes the uniqueness constraint mean what it says.
  doc_id          text not null,
  other_doc_id    text not null,
  -- The share of the smaller document's chunks that both documents have, so a two-page extract of a
  -- hundred-page report reads as a small overlap rather than a complete one.
  chunk_overlap   numeric(5, 4) not null,
  detected_at     timestamptz not null default now(),
  -- Resolved when a person has said which is current, or that they are genuinely two documents.
  resolved_at     timestamptz,
  resolution      text,
  constraint duplicate_flags_pair_unique unique (doc_id, other_doc_id)
);

create index if not exists duplicate_flags_unresolved_idx
  on duplicate_flags (detected_at) where resolved_at is null;

comment on table duplicate_flags is
  'Near-duplicate pairs found at ingest, for a human to resolve. Resolution is a document_versions '
  'declaration when one replaces the other, or a note when they are genuinely two documents.';
