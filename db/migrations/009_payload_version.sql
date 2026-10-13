-- 009_payload_version.sql
--
-- A record of the rules a stored payload was derived under.
--
-- The skip decision is made on a file's bytes, which is right for a file that was edited and wrong for a
-- change to the code that derives the payload from it. Then the bytes match, the manifest still believes
-- its old claim, and the new logic never reaches the documents it was written for. Twice in the ingestion
-- step: a fix to how empty documents are refused, and a fix to is_ocr, where all 29 chunks of two HTML
-- exhibits stayed labelled machine-read after the code that labelled them was corrected.
--
-- The column makes the mismatch detectable so the worker can re-derive those documents once, and the
-- version is deliberately separate from the chunker's: a payload change does not change a chunk's text or
-- its id, and forcing a re-parse for a labelling fix would hide the difference between the two.

alter table ingest_manifest add column if not exists payload_version text;

comment on column ingest_manifest.payload_version is
  'The payload rules in force when this document was ingested. A record whose value differs from the '
  'running worker''s is re-derived even though its bytes have not changed.';
