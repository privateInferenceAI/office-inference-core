-- 015_collection_contract.sql
--
-- What built the vectors in a collection, recorded so a change is a decision rather than a silent degradation.
--
-- The failure this exists for is quiet. Swap the embedding model, or change its pooling, or start prefixing
-- queries differently, and every vector already in the collection was produced by the old contract. Similarity
-- between a query vector from the new model and a passage vector from the old one is not zero, it is meaningless,
-- and meaningless similarity still returns results in a plausible order. The store stays healthy, the searches
-- return passages, and the only symptom is that the answers are worse, which nothing measures.
--
-- The dimension check in `ensure_collection` catches one field of this contract and was written when dimension
-- was the only one anybody had changed. It is the field that fails loudly, which is why it is the one that got
-- caught: a mismatch there rejects every write. The others fail silently.

create table if not exists collection_contracts (
  collection   text primary key,
  -- Every field that changes what a vector means. Stored whole rather than as a fingerprint alone, so a refusal
  -- can name the field that changed instead of reporting that two hashes differ.
  contract     jsonb not null,
  -- SHA-256 over the contract's canonical form. The comparison is on this, because comparing twelve fields one
  -- at a time is a list somebody forgets to extend.
  fingerprint  text not null,
  recorded_at  timestamptz not null default now(),
  -- Which generation this is. A contract change that is deliberate starts a new generation, and the old row is
  -- kept so the history of what built the current vectors is readable.
  generation   integer not null default 1
);

create table if not exists collection_contract_history (
  id           bigserial primary key,
  collection   text not null,
  contract     jsonb not null,
  fingerprint  text not null,
  generation   integer not null,
  recorded_at  timestamptz not null default now(),
  -- Why the contract changed, in the operator's words. A generation boundary without a reason is one nobody can
  -- review later, and the whole point of the boundary is that somebody decided.
  reason       text
);

comment on table collection_contracts is
  'The contract the vectors in a collection were built under. Any field in it changing makes existing vectors '
  'meaningless without making them absent, so the read path refuses rather than answering from them.';

comment on column collection_contracts.fingerprint is
  'SHA-256 over the canonical contract. The comparison is on this rather than field by field, because a '
  'field-by-field check is a list that stops covering the contract the first time somebody adds a field.';
