# Backup and recovery

This clause names the promise and the target. The drill records the measured number; the contract names the
ceiling.

## Backup

- Target: the deployment's encrypted S3 backup bucket, versioned and blocked from public access.
- Contents: a nightly Postgres dump, the rendered configuration, manifests, documents and the Qdrant vector data.
- Schedule: daily at 02:00 UTC, through the systemd timer oi-backup.timer.
- Retention: the bucket keeps object versions; the deployment owner decides the lifecycle period.
- Secrets: the rendered configuration and the database dump contain secrets, so the bucket is encrypted and
  private. The benchmark is not a client-owned backup until the bucket is in the client's account.

## Recovery promise

- RPO: 24 hours.
- RTO: 4 hours.

The RPO is the maximum age of the newest backup at the moment of loss. The RTO is measured from the decision to
restore to a working system that answers a cited question and verifies its audit chain.

## Last drill

2026-10-23, on scratch instance i-096bebd46308d1673:

- Backup used: s3://oi-core-backups-878451098165/backups/20261023T122439Z/
- Restore start: 2026-10-23T12:24:55Z.
- Cited answer: 2026-10-23T12:25:43Z. State restore to working system: 48 seconds.
- Full drill from starting the scratch instance at 12:10:01Z: 15 minutes 42 seconds.
- Audit chain: 964 events, chain intact.
- Qdrant: 24,296 points restored; the restored API answered with citations.
- Both measured numbers are inside the 4-hour RTO.

The drill depends on the target having the container images and compose definition. The backup restores state,
not the runtime base. A complete client recovery also provisions the base image or reinstalls the stack.

## Change rule

Changing the bucket, the RPO or the RTO is a contract change and a backup configuration change together. A
deployment that does not match this clause is not installable under this contract.
