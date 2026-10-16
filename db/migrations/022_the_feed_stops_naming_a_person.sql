-- 022_the_feed_stops_naming_a_person.sql
--
-- **UI-FEED-001, and the sentence it is about.** `audit_feed` rendered an approval as:
--
--     'a person ' || outcome || ' an action'
--
-- which asserts something the system has not established. Real sign-in arrives at C7; today a session is a
-- bearer token that maps to a role, and the FDA's own Data Integrity guidance says a shared or group account
-- does not make an action attributable to a specific individual. The feed is the line a client reads without
-- checking, so this is not a wording preference.
--
-- **The replacement reads the record rather than the outcome.** `payload -> 'approval' -> 'identity' ->
-- 'assurance'` is the field the approval record carries, and `approvals.describe()` in
-- `services/api/approvals.py` is the sentence it assembles in Python. The view has to assemble the same
-- sentence in SQL, which is a shape defined twice, so `tests/test_workflow_feed.py` compares the two for all
-- three decisions and fails when they drift apart.
--
-- **The path in the design document was wrong, and it failed invisibly.** Section 3.4 of
-- `docs/design/c5-approval-record-identity.md` proposes `payload -> 'identity' ->> 'assurance'`. The record
-- nests identity under `approval` (`approvals.build_payload`), so `payload -> 'identity'` is always SQL null,
-- every row takes the honest branch, and a *verified* approval would render as unverified. The expression is
-- fail-safe, which is exactly why nobody would notice: the wrong version produces a defensible sentence for
-- every row, including the ones it is wrong about. This migration reads the path the record actually has.
--
-- **Unknown values resolve downward, in SQL as in Python.** The `case` tests for the three stronger values by
-- name and everything else, including a missing payload, a null, a typo and a value from a newer schema, lands
-- in the honest branch. That is the same rule `approvals.resolve_assurance` applies, and it has to hold on
-- both read paths or it does not hold.
--
-- A view can read the payload, so this needs no schema change. It needs a migration because the view is
-- defined in one, and 002 and 006 are already applied: editing an applied migration is refused by the
-- migration bookkeeping, and rewriting history would invalidate every row that hashes what the schema said.

create or replace view audit_feed as
select id, occurred_at, event_type, actor, actor_role, data_class, outcome,
       tool, model, request_id,
       case
         when event_type = 'model_call' then coalesce(model, 'a model') || ' answered'
         when event_type = 'tool_call'  then coalesce(tool, 'a tool') || ' was used'
         when event_type = 'approval' then
           -- **The word comes from the record, not from `outcome`.** `audit_events.outcome` is constrained to
           -- `ok`, `denied`, `error` and `pending`, so an approval cannot put its decision there — and the
           -- route trying to is a defect this migration found rather than caused. The decision lives in
           -- `payload -> approval -> decision`, which is where `approvals.build_payload` puts it and where
           -- `approvals.describe()` reads it, so both readers take it from the same field.
           --
           -- The three past-tense words are written out rather than derived. `initcap(decision) || 'd'` reads
           -- well for approve and deny and produces "Request_changesd" for the third decision, which is the
           -- kind of sentence that gets a record taken less seriously than it deserves.
           case coalesce(payload -> 'approval' ->> 'decision', outcome)
             when 'approve' then 'Approved'
             when 'deny' then 'Denied'
             when 'request_changes' then 'Changes requested'
             -- The same fallback `approvals.describe()` uses for a decision it does not know, so an unknown
             -- value cannot make the two readers disagree. `initcap` is Postgres; the Python is
             -- `.replace("_", " ").capitalize()`, and they agree on an identifier-shaped word.
             else initcap(replace(coalesce(payload -> 'approval' ->> 'decision', outcome, 'decided'),
                                  '_', ' '))
           end
           || ' at ' || coalesce(payload -> 'approval' -> 'part11_11_50_manifestation' ->> 'signed_at', '')
           || case
                when coalesce(payload -> 'approval' -> 'identity' ->> 'assurance', 'unverified')
                     in ('provider_verified', 'proofed', 're_attested')
                  then ' by ' || coalesce(payload -> 'approval' -> 'identity' ->> 'claimed_subject',
                                          'an unidentified person')
                       || ' (' || coalesce(payload -> 'approval' -> 'identity' ->> 'claimed_role', '') || ')'
                else ' under the '
                     || coalesce(payload -> 'approval' -> 'identity' ->> 'claimed_role', 'an unknown role')
                     || ' role. The person who decided was not identified: sign-in is not enabled on this '
                     || 'deployment, so the system cannot verify who acted.'
              end
         when event_type = 'access'     then 'a document was read'
         else event_type
       end as summary,
       payload_sha256, row_hash
from audit_events
order by id desc;

comment on view audit_feed is
  'Every event as a sentence a person reads. The approval branch renders from payload -> approval -> identity '
  'and falls back to the unverified wording for any value it does not recognise, so a new assurance level '
  'cannot make a record claim more than it has. Kept in step with approvals.describe() by '
  'tests/test_workflow_feed.py.';
