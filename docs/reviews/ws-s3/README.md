# S3 artifact adapter

**Date:** 2026-10-23. **Status:** built and unit-tested. The live object-store run is blocked.

## What was built

- `services/workspace/artifact_store.py`: `S3ArtifactStore`. It uploads each local content-addressed file to
  `<prefix>/<task-id>/<sha256>` and reads it back by the same key.
- The workspace service selects the store when `WORKSPACE_S3_BUCKET` is set. The local artifact root remains
  the fallback.
- `tests/test_artifact_store.py`: 4 tests over keys, upload, download, a missing bucket and a typed upload
  failure. With the workspace service tests, 24 pass.

## Why the live run did not happen

- `docker pull minio/minio` is denied and `quay.io/minio/minio` returns 401 from this box.
- There is no S3 bucket in the client's account. Creating one is an AWS modification and needs your approval.
- The box has no `pip`, so a local S3-compatible server from `moto` is not available without installing one.

The adapter is ready to point at the client's bucket. The missing piece is the bucket and its credentials.
