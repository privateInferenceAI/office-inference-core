-- 017_sessions.sql
--
-- The session record, which is the authority for who is asking and what they may read.
--
-- Why a table rather than a signed token carrying the claims: SEC-ACL-002 asks that a user outside a
-- document's group cannot retrieve it by any query, and a claim inside a token that the server trusts is a
-- claim the client can be wrong about. A random identifier that resolves to a row has nothing in it to
-- tamper with. The row is where the role and the permission live.
--
-- access_roots is a SNAPSHOT taken when the session was minted, not a join. A later change to a group must
-- not change what an event recorded last week meant: the audit record has to be reconstructable as at the
-- time it was written, and a permission read live at comparison time is a permission that has since moved.
--
-- Revocation is a timestamp rather than a delete, for the same reason the audit table is append-only. A
-- session that was ended is a fact about the deployment, and a row that vanishes takes the fact with it.

create table if not exists sessions (
  session_id    text primary key,
  subject       text not null,
  actor_name    text,
  role          text not null,
  access_roots  jsonb not null,
  groups        jsonb not null default '[]'::jsonb,
  deployment_id text not null,
  issued_at     timestamptz not null default now(),
  expires_at    timestamptz not null,
  revoked_at    timestamptz,
  -- How the session was established. 'development' is the mint command, which refuses without --dev; a
  -- deployment's sessions arrive from the identity provider at C7 and are recorded as 'provider'. The
  -- column exists now because a record that cannot say how an identity was established is not evidence.
  source        text not null default 'development'
                check (source in ('development', 'provider'))
);

create index if not exists sessions_subject_idx on sessions (subject, issued_at desc);
create index if not exists sessions_expiry_idx on sessions (expires_at) where revoked_at is null;
