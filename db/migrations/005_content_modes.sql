-- 005_content_modes.sql
--
-- The record proves a call happened and what it cost. Whether it also proves what was *said* is a
-- policy decision a client makes per data class, and these two columns are what make that decision
-- expressible without storing the text.
--
-- A prompt hash beside a stored prompt lets a reader confirm the stored text is the text that was
-- sent. On its own, the hash proves the content without becoming a second copy of it, which is the
-- arrangement most deployments should choose and the default this system ships with.

alter table audit_events add column if not exists prompt_sha256 text;
alter table audit_events add column if not exists completion_sha256 text;

comment on column audit_events.prompt_sha256 is
  'SHA-256 of the prompt as sent. Present whatever the content mode, so the record can prove what was
   said even when it deliberately does not keep it.';
comment on column audit_events.completion_sha256 is
  'SHA-256 of the completion as returned. Same rule as the prompt hash.';
