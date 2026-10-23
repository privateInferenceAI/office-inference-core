# Authenticated session, never a caller-supplied role

**Date:** 2026-10-23. **Status:** SEC-AUTH-001 is verified.

## Evidence

No token, `POST /ask`:

```json
{"error":"the session token resolves to no live session. It was never issued, it expired, or it was revoked; the three are one answer on purpose, because a refusal that says which is a refusal that confirms a token once existed.","kind":"no_session"}
```

Refused with 401 and recorded as audit row 1218:

```json
{"stage":"session","endpoint":"/ask","refusal":"no_session"}
```

Valid session with a forged `role: executive`:

```json
{"error":"Permission denied on this resource (or it might not exist). A role comes from an authenticated session and never from a request field.","kind":"refused_authority_claim"}
```

Refused with 403 and recorded as audit row 1219, which names the session role `viewer` and the attempted role
`executive`. The request also wrote row 1220 for the authority-claim refusal, because the request carried two
forms of the same claim.

The no-session row was the one this test found missing. The API returned 401 without writing anything, so a
review looking for unauthenticated attempts would have found none. The `/ask` refusal path now records the
attempt before it returns.

## Files

- `responses.json`: the two responses.
- `audit-rows.json`: rows 1218, 1219 and 1220.
