# A support role in the client account cannot read documents

**Date:** 2026-10-23. **Status:** SEC-LEAST-001 is verified.

## What was tested

A role named `office-inference-support` was created in badj.ai account 426063972668. Its trust policy allowed
only `arn:aws:iam::878451098165:role/DSHAgentRole` to assume it. Its inline policy allowed SSM session actions
and nothing else. No S3 action was attached.

The Office Inference role assumed it successfully. It then tried to read a real object in the documents bucket:

```
s3://disk-ingest-documents-878451098165/ingest/C/BadjAI/documents/ApplicationInfo.pdf
```

S3 returned AccessDenied:

```
An error occurred (AccessDenied) ... is not authorized to perform: s3:GetObject on resource:
"arn:aws:s3:::disk-ingest-documents-878451098165/ingest/C/BadjAI/documents/ApplicationInfo.pdf"
because no identity-based policy allows the s3:GetObject action
```

## Revocation

The role and its inline policy were deleted from account 426. The next AssumeRole attempt from Office Inference
returned AccessDenied, so the access is revocable by deleting the role rather than by changing our own account.

An earlier delete ran against the wrong profile (account 878) and did not remove the role. The evidence above
is from the corrected deletion in account 426.

## Files

- `assume-and-read.txt`: the successful assumption and the denied read.
- `after-revoke.txt`: the denied assumption after deletion.
