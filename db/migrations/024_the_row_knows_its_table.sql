-- 024_the_row_knows_its_table.sql
--
-- **A table row in the lexical index could not say which table it came from, or which period its figures
-- belong to.**
--
-- Corpus 2 is 80 documents and 24,295 chunks. A row in it reads as
-- `Total revenues | 4,934,098 | 4,136,543 | 8,947,032 | 8,183,283`: four numbers, and the labels that
-- tell them apart sit in the header rows above. The row text was bare because `chunk_index` stored ten
-- columns and dropped the rest of the payload, so `table_id`, `row_index`, `row_start`, `row_end`,
-- `table_rows` and `headings` existed only in Qdrant, where a full-text query cannot see them.
--
-- The fix that worked at render time prepends the nearest preceding header block to a row
-- ("Columns: Three Months Ended June 30, | Six Months Ended June 30, / 2026 | 2025 | 2026 | 2025."). That
-- costs a lookup per row and only helps the path that does the lookup. These columns, and the chunker
-- that writes the same block into the row's own text, put the fact in the index instead.
--
-- **Why this file and not an edit to 010.** `audit migrate` records each file's checksum and refuses a
-- migration that has changed since it was applied, so the create-table definition in 010 is frozen in
-- place. A fresh database runs 010 and then this file, and ends with the same column set as a database
-- that has been running since before it. Every statement is `add column if not exists`, so this file is
-- safe to run twice and safe on a database the ingestion worker has already brought up to date.

alter table chunk_index add column if not exists table_id text;
alter table chunk_index add column if not exists row_index integer;
alter table chunk_index add column if not exists row_start integer;
alter table chunk_index add column if not exists row_end integer;
alter table chunk_index add column if not exists table_rows integer;
alter table chunk_index add column if not exists headings jsonb;

comment on column chunk_index.table_id is
  'The table a row belongs to, named the way the chunker names it: "<doc_id>:table:<parser ref>". Null for '
  'prose and data-set chunks. A row query that matches a figure can name the schedule it came from without '
  'reading a second store.';

comment on column chunk_index.row_index is
  'Which row of its table this chunk is, for a table_row chunk. Null for a whole-table chunk, which carries '
  'a row range instead, and for everything that is not a table.';

comment on column chunk_index.row_start is
  'The first row this chunk covers. Equal to row_index on a table_row chunk; a range on a table chunk that '
  'was too large to embed as one piece; null on a table chunk that fits.';

comment on column chunk_index.row_end is
  'The last row this chunk covers, paired with row_start.';

comment on column chunk_index.table_rows is
  'How many rows the table holds, so a citation can say row 4 of 231 rather than an index with no scale.';

comment on column chunk_index.headings is
  'The heading path in force where this chunk sits, as JSON. Stored as metadata and deliberately NOT folded '
  'into the row text: measured on corpus 2, the document title and heading path made retrieval worse.';

-- **The chunker version, because the skip decision is on the file's bytes.** A chunker change alters every
-- chunk's text and id and touches no byte of any document, so without this column a corpus chunked under
-- the old rules is skipped forever after a bump. The worker writes it on success and on failure, and
-- re-derives any document whose recorded version differs from the running one.
alter table ingest_manifest add column if not exists chunker_version text;

comment on column ingest_manifest.chunker_version is
  'The chunker rules in force when this document was last derived. A record whose value differs from the '
  'running chunker''s is re-derived even though its bytes have not changed.';
