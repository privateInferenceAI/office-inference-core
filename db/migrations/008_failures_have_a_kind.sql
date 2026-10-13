-- 008_failures_have_a_kind.sql
--
-- A failure is either about the document or about the moment, and the manifest could not tell them apart.
--
-- The first real corpus run dead-lettered a 60-page PDF after two 504s from the parser. The document was
-- fine. The stage behind it is slow, something in front of it gave up, and the retry exhausted. Counting
-- that against the file means a permanently unreadable corpus that no retry fixes and no log explains,
-- because the entry says the document failed rather than that the service did.
--
-- The count stays, because a document that fails to parse twice for its own reasons should stop being
-- retried. The kind is what decides which failures count: a transient one records itself so the next cycle
-- tries again, and leaves the count where it was.

alter table ingest_manifest add column if not exists failure_kind text;

comment on column ingest_manifest.failure_kind is
  'transient or permanent. Transient means a gateway error, a timeout, or a dropped connection: it says '
  'something about the moment rather than the document, so it does not count towards failure_count and '
  'the document stays retryable.';

-- The worker asks for this on every cycle, and a dead-lettered document is found by its kind.
create index if not exists ingest_manifest_failure_kind_idx
  on ingest_manifest (failure_kind) where failure_kind is not null;
