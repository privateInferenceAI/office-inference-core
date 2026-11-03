# Off-box backups and timer, product deployment, 2026-11-03

The restore drill in `docs/reviews/restore-drill-20261103/` proved that a local backup restores. It also made
two gaps visible: the backup sat on the same box as the deployment, and nothing ran it on a schedule. Both are
fixed.

## What runs now

`oi-product-backup.service` and `oi-product-backup.timer` run at 03:00 Eastern (08:00 UTC) every day on the
product box. The service:

1. Runs `agentic-n8n-backup.sh`, which dumps `oicore`, `litellm` and `n8n`, archives `rendered/`, `documents/`
   and `data/n8n`, and exports the n8n workflows.
2. Writes `backup-manifest.json` with the deployment id, image pins and the sha256 of every file.
3. Uploads every file to `s3://oi-core-backups-878451098165/backups/product-oi-core-product-20261103/<stamp>/`
   with SSE-S3. The bucket is versioned, encrypted, and public access is blocked.
4. Prunes local backups beyond seven and remote prefixes beyond thirty days.

The bucket and the instance-role permission already existed from the C8 work, so no new AWS resource was
created. The product box role already allowed `s3:PutObject`, `GetObject`, `DeleteObject` and `ListBucket` under
`backups/*` in that bucket.

## First run

| Item | Value |
|---|---|
| Local backup | `/home/ubuntu/backups/product-backup-20261103T223022Z`, 25 MB |
| Remote copy | `s3://oi-core-backups-878451098165/backups/product-oi-core-product-20261103/20261103T223022Z/` |
| Objects | 9, including the three dumps, documents, rendered, n8n data, workflows and manifest |
| Encryption | SSE-S3 AES256, confirmed on the uploaded objects |
| Run time | 6 seconds |
| Timer | enabled, next run 2026-11-04 08:01 UTC |

`documents.tar.gz` is 21 MB of the 25 MB. The vector store is deliberately not backed up: it is derived from
the documents, and the restore drill rebuilds the application without it. The drill now also extracts the
documents archive to a scratch path and compares the file count and the content hash of the whole tree; both
match at 80 files and `2b8cffb3...`.

## Restore drill after the change

`scripts/restore-drill.sh` was extended with the documents check and re-run: 12 checks passed in 27 seconds,
against a 25 MB backup. Database restore remains about 10 seconds. The scratch API still answers with the cited
mileage answer. See `docs/reviews/restore-drill-20261103/`.

## Recovery posture

- RPO is now 24 hours: a daily backup, timer-persistent so a missed run fires when the box comes back.
- Local retention is seven days; the bucket retains thirty days of prefixes through the script.
- Backup contents include secrets: `rendered/` and the n8n data. The bucket is encrypted and public access is
  blocked, but the backup is only as safe as the instance role that can write and delete it.

## Off-box restore, 2026-11-03

The bucket copy was restored and tested, not only uploaded. The backup manifest now records eleven table counts
so an off-box restore has expectations of its own; comparing a restored copy with the live database is wrong,
because the live one has moved on. The restore drill downloaded the nine objects, verified checksums, and
passed 11 checks in 21 seconds with the counts matching the manifest. The downloaded copies were removed from
the box afterwards. Full numbers are in `docs/reviews/restore-drill-20261103/README.md`.

## Findings and gaps

1. **The product box still uses the development instance role.** `oi-core-dev-instance` carries the release
   signer, SES send, DeepSeek key read, workspace artifact and backup permissions. That is broader than a
   production deployment should have. It is recorded in decision 0012 as a hardening item for the customer
   access phase, not something this backup work changed.
2. **Same account, same credentials.** The backup is off the box but in the same account and readable by the
   same role. It protects against host or disk loss, not against account compromise or an operator mistake.
3. **No Object Lock on the backup bucket.** Versioning and SSE are on; a deletion by the role is not prevented.
   Object Lock with a retention window belongs with the client-owned backup work.
4. **The backup manifest and bucket are not a client-owned target.** For a client deployment, the target is
   theirs and the credentials are scoped to it.

## Next actions

- Before real company documents go in: confirm the timer fires on the next day and the second backup prunes
  nothing (only one prefix exists now).
- Before the first client: a client-owned bucket or an Object Lock retention window, and a scoped instance role
  for the product box.

## Evidence

The run output is in the systemd journal and the objects are listed above. Local evidence from the drill run is
in `.scratch/product-readiness/restore-drill/` on the operator machine and in
`docs/reviews/restore-drill-20261103/evidence/`.
