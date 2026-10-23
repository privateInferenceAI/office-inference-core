# Backup restore drill

**Date:** 2026-10-23. **Status:** OPS-BACKUP-001 and OPS-RTO-001 verified.

## What was done

- Created encrypted, versioned, private S3 bucket oi-core-backups-878451098165.
- Added scripts/backup_deployment.py and scripts/restore_deployment.py.
- Installed oi-backup.timer, daily at 02:00 UTC.
- Backed up Postgres, rendered configuration, manifests, documents and Qdrant.
- Restored the backup on scratch instance i-096bebd46308d1673 at 52.204.202.66.
- Started the restored stack from the restored database, configuration and vector store.
- The restored API answered the mileage question with citations and the restored audit chain verified.

## Measurements

- Backup: s3://oi-core-backups-878451098165/backups/20261023T122439Z/
  - postgres.dump sha256 056b832c85ac75125334d3c240a65659bc5bb1d1cafa3afe0c60007ba63e21fe
  - state.tar.gz sha256 81289b8ced73b5124f16e77a9761353f7f60124de7202e5039906da2d82acdd6
- Restore start: 2026-10-23T12:24:55Z.
- Cited answer: 2026-10-23T12:25:43Z.
- State restore to working system: 48 seconds.
- Full drill from scratch instance start at 12:10:01Z: 15 minutes 42 seconds.
- Audit chain: 964 events, intact.
- Qdrant: 24,296 points restored.
- RPO: 24 hours. RTO: 4 hours. Both measured numbers are inside the promise.

## Gaps found and fixed

- The first backup restored the database and documents but not Qdrant, so the restored system had no vectors and
  the answer request failed. The backup now includes data/qdrant; the second restore succeeded.
- The scratch instance had old images, so the API image was rebuilt from the current checkout before the restore
  test. The backup restores state, not the runtime base.

## Still open

The backup bucket is in Office Inference's account, not the client's. A client deployment needs the same bucket
policy in the client account. This is recorded as part of BIZ-INDEPENDENT-001.

## Files

- scripts/backup_deployment.py
- scripts/restore_deployment.py
- docs/contracts/backup-and-recovery.md
- rto.json
