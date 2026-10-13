-- 016_retention_policy.sql
--
-- Time-based retention: a period per dataset, and the record of a run that enforced it.
--
-- The two requirements this sits beside are different in kind and the difference is the point. `DATA-PURGE-001`
-- is a client asking for one document to be removed, and it deliberately LEAVES THE FILE: the client still has
-- the document, they asked us to stop holding a copy. `DATA-RETAIN-001` is a period running out, and it
-- destroys the file as well, because "keep invoices for seven years" means after seven years there is no
-- invoice. A retention job built by reusing the purge and forgetting the file would report success while the
-- thing the client asked to be destroyed sat in their documents directory.

create table if not exists retention_policies (
  -- Which body of material the period applies to. Two, because the requirement names two: the documents a
  -- client ingested, and the conversations they had with the system. They are kept differently and destroyed
  -- differently and a policy that could not tell them apart could only be right about one.
  dataset      text primary key check (dataset in ('documents', 'conversations')),
  retain_days  integer not null check (retain_days > 0),
  -- Why this period, in the client's words or their regulator's. A retention period with no stated basis is
  -- one nobody can defend to an auditor, and the number is the kind that gets changed without one.
  legal_basis  text,
  updated_at   timestamptz not null default now()
);

comment on table retention_policies is
  'How long each dataset is kept. Absent means no policy, and no policy means nothing is destroyed on a '
  'schedule: a default period invented here would destroy a client''s documents on a number they never agreed '
  'to.';

-- A run is recorded whether or not it destroyed anything, because the absence of a run and a run that found
-- nothing are different facts and only one of them means retention is working.
create table if not exists retention_runs (
  id           bigserial primary key,
  dataset      text not null,
  started_at   timestamptz not null default now(),
  finished_at  timestamptz,
  -- The period in force when this ran. Stored rather than joined, because the policy can change and the record
  -- of what happened has to say what the rule was at the time.
  retain_days  integer not null,
  cutoff       timestamptz not null,
  examined     integer not null default 0,
  expired      integer not null default 0,
  destroyed    integer not null default 0,
  -- Skipped because a hold covers them. Counted separately from destroyed because a run that skipped forty
  -- documents under a hold and a run that found forty documents and destroyed them are different outcomes.
  held         integer not null default 0,
  failed       integer not null default 0,
  dry_run      boolean not null default false,
  actor        text not null default 'retention-job',
  failures     jsonb
);

create index if not exists retention_runs_dataset_idx on retention_runs (dataset, started_at desc);

comment on column retention_runs.held is
  'Documents skipped because a legal hold covers them. A hold beats a retention period: the period says the '
  'client agreed to lose the document by now and the hold says a court has told them not to.';

-- Conversations, so the requirement has something to bind to on both halves.
--
-- Minimal on purpose. The assistant API that writes these arrives in Phase 4, and inventing its schema now
-- would be inventing decisions about a component that does not exist. What is here is what retention needs to
-- be defined at all: an identity, when it happened, and the text.
create table if not exists conversations (
  id           bigserial primary key,
  session_id   text not null,
  actor        text not null,
  actor_role   text,
  asked_at     timestamptz not null default now(),
  question     text,
  answer       text,
  -- Which documents the answer cited, so a purge can find the conversations that quote a document rather than
  -- only the document itself.
  citations    jsonb,
  data_class   text not null default 'internal'
);

create index if not exists conversations_asked_idx on conversations (asked_at);
create index if not exists conversations_session_idx on conversations (session_id);

comment on table conversations is
  'Questions and answers, kept so retention can be defined for them. Written by the assistant API in Phase 4; '
  'this table exists now because a retention policy for a dataset with no table is a policy that has never been '
  'tested against anything.';
