# S3 artifact store, live

**Date:** 2026-10-23. **Status:** WS-ARTIFACT-001 is verified.

## The bucket

Created in the credits account **878451098165**, region **us-east-1**:

- Bucket: `oi-core-workspace-artifacts-878451098165`.
- Public access fully blocked.
- SSE-S3 encryption (AES256).
- Versioning enabled.
- Lifecycle: objects expire after 365 days; incomplete multipart uploads abort after 7 days.
- Instance role `oi-core-dev-instance` has an inline policy for `ListBucket` and
  `GetObject`/`PutObject`/`DeleteObject` on the artifact prefix only.

The box's instance role wrote and read a probe object before the job ran.

## The store

- `services/workspace/artifact_store.py`: `S3ArtifactStore` uploads each content-addressed file to
  `<prefix>/<task-id>/<sha256>` and reads it back by the same key.
- The workspace service selects it when `WORKSPACE_S3_BUCKET` is set.
- `tests/test_artifact_store.py`: 4 tests; the service tests cover the S3 branch.

## The live job

Job `220b4329143f4db2a9f5ae89b8278355` ran `workspace_brief` and wrote `report.txt`.

- The artifact record carries `store: s3` and key
  `artifacts/220b4329143f4db2a9f5ae89b8278355/b64506b8...`.
- The API download returned 932 bytes with the travel approval section at the head.
- S3 holds one object under the job prefix, 932 bytes, SSE-S3.
- The chain holds 513 events and verifies intact.
- No task container and no task directory remained.

## Files

- `bucket.json`: the bucket settings and role policy.
- `objects.json`: the S3 object listing and head.
- `job.json`, `artifacts.json`, `audit-rows.txt`.
- `audit-events.jsonl`, `verify.txt`, `export.txt`.
- `containers.txt`, `task-dir.txt`.
- `c6-s3-live-result.json`.
- `SHA256SUMS`.
