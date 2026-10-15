-- 019_the_credential_is_not_the_record.sql
--
-- **A session's credential stops being its identifier.**
--
-- Found by the C4 panel's cold reader, who opened an audit row in the app and saw a live session token printed in
-- its payload. They asked whether it was a credential, and it was: the value in the row authenticated as that
-- session. The audit screen widens with permission, so **one person's live credential was readable on another
-- person's page** — and the rows are append-only, so it could not be taken back.
--
-- The cause was a conflation rather than a leak. `mint` generated one random string and used it as BOTH the
-- session identifier and the credential handed to the caller, so every place that recorded "which session did
-- this" recorded the credential. The API passed `session.session_id` into audit payloads for its own
-- traceability, which was reasonable and became a disclosure.
--
-- **The fix separates them.** A session keeps its identifier, which is not secret and can be recorded and
-- printed freely. The credential is a separate random value, and the database stores only its SHA-256. So a row
-- that records a session id records nothing that authenticates, and a database dump holds no usable tokens.
--
-- **Existing sessions are revoked rather than migrated, and that is deliberate.** Their credentials are the
-- values already written into the chain, so there is nothing to hash and nothing to rotate: the only honest
-- move is to stop honouring them. Their rows stay, because the chain is hash-linked and rewriting it would break
-- the verification the whole record rests on. The exposure is recorded in docs/review-log.md rather than tidied
-- away, and the manual says what it was.

alter table sessions add column if not exists credential_sha256 text;

comment on column sessions.credential_sha256 is
  'SHA-256 of the credential the caller holds. **The credential itself is never stored.** A row that records a '
  'session id records nothing that authenticates, which is the property this column exists to make possible.';

comment on column sessions.session_id is
  'The session identifier. **Not a secret, and not a credential.** It is recorded in audit payloads and shown '
  'on the Activity screen on purpose, so that a person can tell which session did something. Treating it as a '
  'credential is the defect this migration fixes.';

-- **The rows that predate the split stop working.** They were minted when the identifier was the credential, so
-- the identifier cannot be trusted to be one any more, and leaving them live would leave the exposure open.
-- A revoked session is refused with the same one answer as one that never existed, which is the behaviour the
-- refusal path already documents.
update sessions
   set revoked_at = coalesce(revoked_at, now())
 where credential_sha256 is null
   and revoked_at is null;

create index if not exists sessions_credential_idx
  on sessions (credential_sha256)
  where revoked_at is null and credential_sha256 is not null;
