# Audit records and the tenant refusal gap

**Date:** 2026-10-23.

## What an audit row looks like

The chain is JSON Lines, one event per line. A row carries the event, the actor, the role, the deployment, the
data class, the outcome, the model or tool, the payload hash and the chain hashes. The full export is the
authoritative history; the sample beside this file is three real rows from the dev chain.

Fields a reader can use without decoding:

- event_type: access, model_call, tool_call, approval, purge and so on
- actor and actor_role: the authenticated identity that caused the event
- data_class and egress_allowed: what content was in scope and whether it could leave
- outcome: ok, denied or error
- payload: event-specific metadata; content itself is hashed unless the deployment stores it
- prompt_sha256 and completion_sha256: version 4 covers both digests
- prev_hash and row_hash: the chain links every row to the one before it

Example row: docs/reviews/tenant-audit/sample.jsonl

The full dev export from 2026-10-23 is at /home/anthony/tenant-audit-2026-10-23.jsonl. It has 1,548 rows.

## The tenant gap

The tenant gateway has no route to the Office audit database. Its policy refusals are returned to the API, but
the Office chain could not see them. The API recorded a refused tenant email with outcome=ok and without the
refusal reason.

## What changed

The API now classifies the result it gets back from the tenant gateway:

- a successful send: outcome ok, sent true
- a local egress refusal: outcome denied, refused true, egress_allowed false
- a gateway 403: outcome denied, refused true, error carries the gateway response
- any other failure: outcome error, with the error string

The refusal is written to the Office chain by the side that owns the chain. The tenant still has no database
route, by design. A later deployment can add a signed tenant-to-Office audit route if the client requires the
gateway's own view, but the API's view of a refusal is now recorded.

## Sample denied row

The shape after the change:

    {"event_type":"tool_call","outcome":"denied","tool":"send_email",
     "payload":{"sent":false,"refused":true,"egress_allowed":false,
                "error":"gateway returned 403: ...","to":"..."},
     "chain_version":4,"prev_hash":"...","row_hash":"..."}

The exact error text is the gateway's response, truncated to 300 characters.

## Verification

- Full export: 1,548 rows, chain intact at the time of this record.
- The API code path is covered by the normal test suite.
- The tenant is frozen, so the live tenant 403 path has not been re-exercised since the change. Thawing the
  tenant and sending one refused email is the remaining live check.
