-- 014_retention.sql
--
-- Purging a client's document, holding one against a purge, and destroying everything at the end.
--
-- Three requirements, and the first one is where the work is: "a client-requested purge deletes content
-- everywhere it is stored, including the vector index". EVERYWHERE is the requirement. A purge that clears the
-- vector store and leaves the lexical index still answers the question that was supposed to be forgotten, with
-- a correct citation to a document that no longer exists. That is the failure this schema has to make
-- impossible rather than unlikely, which is why the purge record names every store and what each one reported.

create table if not exists legal_holds (
  id           bigserial primary key,
  -- The document, or the whole deployment when doc_id is null. A hold on everything is what a litigation hold
  -- usually is, and modelling it as a row per document would mean the hold silently fails to cover a document
  -- added after it was placed.
  doc_id       text,
  scope        text not null default 'document' check (scope in ('document', 'deployment')),
  reason       text not null,
  placed_by    text not null,
  placed_at    timestamptz not null default now(),
  released_at  timestamptz,
  released_by  text,
  constraint legal_holds_scope_matches check (
    (scope = 'document' and doc_id is not null) or (scope = 'deployment' and doc_id is null))
);

create index if not exists legal_holds_active_idx on legal_holds (doc_id) where released_at is null;
create index if not exists legal_holds_deployment_idx on legal_holds (scope) where released_at is null;

comment on table legal_holds is
  'Documents a purge may not touch. A deployment-scope hold covers every document including ones added after '
  'it was placed, because a hold that only covered what existed at the time is a hold with a gap in it.';

create table if not exists purge_records (
  id             bigserial primary key,
  doc_id         text not null,
  reason         text not null,
  actor          text not null,
  purged_at      timestamptz not null default now(),
  -- What each store reported, as {"vector": {"deleted": 20}, "lexical": {"deleted": 20}, ...}. Recorded per
  -- store rather than as a total, because a total that says 40 cannot show that one store contributed nothing,
  -- and a store contributing nothing is the failure this table exists to make visible.
  stores         jsonb not null,
  -- Whether every store confirmed. A purge that could not reach one of them is not a purge, and the row has to
  -- say so rather than record success.
  complete       boolean not null default false,
  -- What the audit chain looked like around this, so the deletion is provable rather than merely asserted.
  chain_head     text
);

comment on table purge_records is
  'One row per purged document, naming what every store reported. Kept after the content is gone, because the '
  'record of a deletion is not the thing deleted.';

comment on column purge_records.stores is
  'Per-store results. A store reporting zero is as important as one reporting a deletion: it is how a store '
  'that was never wired into the purge becomes visible instead of looking like a store that had nothing.';

-- Destruction at the end of a contract. Separate from purge_records because it is a different event: a purge
-- is one document on a client's instruction and this is every document on termination, and a client reading
-- their own record should be able to tell the two apart.
create table if not exists destruction_records (
  id            bigserial primary key,
  requested_by  text not null,
  reason        text not null,
  destroyed_at  timestamptz not null default now(),
  stores        jsonb not null,
  complete      boolean not null default false,
  -- The hash of the record's own contents, so the client can keep it and check it later without us.
  record_sha256 text not null,
  chain_head    text
);
