-- 007_ingest_manifest.sql
--
-- What the ingestion worker knows about each document it has seen.
--
-- Why a table rather than a state file. Three things have to survive a restart and be visible to
-- something other than the worker: whether a document has already been ingested, so a re-run is a no-op
-- rather than a re-embed; how many times it has failed, so a corrupt file becomes a recorded
-- dead-letter instead of a document that quietly never appears; and the content hash, so a changed file
-- is re-ingested and an unchanged one is skipped without reading it.
--
-- The hash is of the file's bytes, taken before parsing. That is what makes the skip cheap: an unchanged
-- file is never parsed, never chunked and never embedded. It is also what makes the re-ingest correct,
-- because a file whose bytes changed produces different chunk ids, and the old ones are removed by the
-- reconciliation rather than accumulating beside the new ones.

create table if not exists ingest_manifest (
  -- The document's path relative to its root, which is the identity a client would recognise. Not the
  -- absolute path, because the deployment root moves and the identity should not.
  relpath           text primary key,
  -- Which root it came from: company, executive, or a future source. The access label is derived from
  -- this at ingest, which is why it is stored rather than recomputed from the path later.
  root              text not null,
  content_sha256    text not null,
  bytes             bigint not null,
  page_count        integer,
  chunk_count       integer not null default 0,
  -- Dead-lettering: a file that fails twice is not retried on every cycle, and the count is what makes
  -- that decision possible without a human remembering.
  failure_count     integer not null default 0,
  last_error        text,
  ingested_at       timestamptz,
  updated_at        timestamptz not null default now()
);

-- Reconciliation asks "which documents are gone", which is a scan of this key against the filesystem. The
-- index is on the root because that is how a cycle walks the corpus.
create index if not exists ingest_manifest_root_idx on ingest_manifest (root);

-- The worker's own record of a cycle, so "nothing changed since last run" is a claim with evidence
-- rather than an assumption from silence.
create table if not exists ingest_runs (
  id              bigserial primary key,
  started_at      timestamptz not null default now(),
  finished_at     timestamptz,
  documents_seen  integer not null default 0,
  documents_new   integer not null default 0,
  documents_changed integer not null default 0,
  documents_skipped integer not null default 0,
  documents_failed integer not null default 0,
  chunks_written  integer not null default 0,
  chunks_removed  integer not null default 0,
  note            text
);
