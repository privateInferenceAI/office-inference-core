-- 003_ingest_manifest.sql
--
-- What has been ingested, so a re-run only processes what changed.
--
-- Deliberately in Postgres rather than a JSON file on disk: a state file that lives in a mounted
-- directory ends up owned by whichever user the container ran as, and that is how a build breaks
-- in a way nobody can explain a week later. The database already exists and already has an owner.

create table if not exists ingest_manifest (
  doc_id     text primary key,
  sha256     text not null,
  chunks     integer not null default 0,
  pages      integer not null default 0,
  acl        text not null default 'company',
  ingested_at timestamptz not null default now()
);

comment on table ingest_manifest is
  'One row per ingested document. A change in sha256 triggers re-ingestion; a missing row triggers deletion.';
