# Prompt, completion and approval hashes are in the chain

**Date:** 2026-10-23. **Status:** AUD-HASH-001 is verified.

## The gap

Chain version 3 hashed the approval digest. It did not hash `prompt_sha256` or `completion_sha256`, so an
operator could rewrite which prompt or completion a model call used and every row would still verify. The
export also omitted both columns, which hid the gap from an export-and-verify check.

## The fix

Migration `027_the_prompt_and_completion_are_in_the_chain.sql` adds chain version 4:

```
version 3 + "|" + prompt_sha256 + "|" + completion_sha256
```

The trigger writes version 4 for new rows. Old rows keep versions 1, 2 or 3 and keep verifying. The verifier
understands version 4, and the export now includes both columns.

## Evidence

A test row was recorded with approval, prompt and completion hashes (row 1250, chain version 4). The full
export of 914 rows verifies intact. Then each of the three columns was set to 64 zeroes in a copy of the export:

| Edited column | Result |
|---|---|
| approval_sha256 | FAIL, row_hash does not match the row contents |
| prompt_sha256 | FAIL, row_hash does not match the row contents |
| completion_sha256 | FAIL, row_hash does not match the row contents |

The unedited export verifies. The three tampered exports do not.

## Files

- `verify-clean.txt`: the intact verification.
- `tamper-results.json`: the three failures.
- `row.json`: row 1250's hash fields.
- `027_the_prompt_and_completion_are_in_the_chain.sql`: the migration.
