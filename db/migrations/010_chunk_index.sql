-- 010_chunk_index.sql
--
-- The lexical arm's copy of every chunk, so a reference a vector search cannot see is still findable.
--
-- Why a second store rather than Qdrant's sparse vectors. The architecture chose Postgres full-text, and the
-- measurement that opened step 3.9 shows why an identifier needs more than a text-search parser: Postgres
-- splits `Form W-4` into `'w' & '-4'` and `Form W-3SS` into `'w-3ss' & 'w' & '3ss'`, and `plainto_tsquery`
-- over a natural question ANDs every term, so "What does Pub. 515 cover?" requires the words pub, 515 AND
-- cover to appear together and matches nothing at all. Seven identifier questions were tried against the
-- deployed vector arm and against plain full-text; full-text matched zero of them.
--
-- So this table carries two ways to match, and they answer different questions:
--
--   tsv          word matching for prose, stemmed, which is what full-text is good at
--   identifiers  exact references, normalised by services/retrieval/identifiers.py, which is what full-text
--                is bad at
--
-- The identifiers are normalised in application code rather than by a text-search configuration, because the
-- normalisation has to agree on both sides of the search and a parser's behaviour is not a contract. `W-4`
-- becomes `w4` here and `w4` in the query, whatever Postgres thinks about hyphens.

create table if not exists chunk_index (
  -- The same id Qdrant holds, so a hit from either arm names the same chunk and the fusion can tell when the
  -- two arms agree. This is what makes reciprocal rank fusion possible at all.
  chunk_id          text primary key,
  doc_id            text not null,
  -- The access label, carried here for the same reason it is carried in Qdrant: the filter belongs in the
  -- query. A lexical arm without this column would be a second path to content the first path protects.
  acl               text not null,
  text              text not null,
  page_start        integer not null,
  page_end          integer not null,
  kind              text not null,
  chunker_version   text not null,
  source_sha256     text not null,
  -- Normalised references found in this chunk, empty when it has none. An array rather than a string so the
  -- match is `&&`, which is an exact token comparison and cannot match a substring by accident.
  identifiers       text[] not null default '{}',
  -- Stemmed words for prose matching. Generated rather than written by the worker, so the two stores cannot
  -- disagree about it, and stored rather than computed per query because an index needs a column.
  tsv               tsvector generated always as (to_tsvector('english', text)) stored
);

create index if not exists chunk_index_tsv_idx on chunk_index using gin (tsv);
create index if not exists chunk_index_identifiers_idx on chunk_index using gin (identifiers);
create index if not exists chunk_index_acl_idx on chunk_index (acl);

comment on table chunk_index is
  'The lexical arm of hybrid search. Written by the ingestion worker alongside Qdrant, and read by the '
  'retriever with the same access label applied as a filter in the query.';
comment on column chunk_index.identifiers is
  'Normalised exact references, from services/retrieval/identifiers.py. Matched with && so a token either '
  'matches or does not, which is the property a text-search parser cannot provide for W-4.';
