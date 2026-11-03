# Restore drill, product deployment, 2026-11-03

Phase C of `docs/runbooks/product-verification.md` on the live product deployment. The question this answers:
if the box and its data are lost, does a backup produce a working system, and how long does it take.

## Environment

| Item | Value |
|---|---|
| Deployment | `oi-core-product-20261103` on `i-0a3fca3c1141cbcad` |
| Release branch head | the merge of PR #345 |
| Postgres | 16, in the deployment's `postgres` container |
| Databases | `oicore` 37 MB, `litellm` 13 MB, `n8n` 18 MB |
| Backup | fresh backup at the start of the drill, 25 MB (21 MB of it documents), checksums verified before restore |
| Script | `scripts/restore-drill.sh` in `oi-canonical-stack` |

## Procedure

The script does the following, in order, and stops nothing in production:

1. Run `scripts/agentic-n8n-backup.sh` to a new directory and verify `SHA256SUMS`.
2. Create scratch databases `oicore_restore`, `litellm_restore`, `n8n_restore`.
3. `pg_restore` the three dumps into the scratch databases.
4. Compare row counts in eleven tables between live and restored.
5. Read a known succeeded answer from the restored job table and require the mileage rate in it.
6. Export and verify the audit chain against the scratch `oicore_restore`.
7. Extract `data/n8n` to a scratch path and check the config file with the encryption key survived.
8. Run the n8n CLI against `n8n_restore` and require both templates in the list.
9. Start a scratch API container against `oicore_restore`, mint a session, ask the mileage question, and require
   a cited answer. This is the step that proves the restored data works through the application, not only in SQL.
10. Drop the scratch databases, remove the scratch directories and the scratch container, and confirm production
   is still healthy.

## Results

All twelve checks passed. Total elapsed: 27 seconds.

| Step | Time | Result |
|---|---|---|
| backup | 4.6s | 25 MB, checksums verified |
| create scratch databases | 0.4s | |
| restore three databases | 9.8s | |
| compare counts | 1.1s | eleven tables equal |
| known answer | 0.05s | contains 0.67 |
| audit chain on restored database | 0.5s | 933 events, chain intact |
| documents archive | 0.5s | 80 files, identical content hash |
| n8n data directory | 0.01s | 7 files, config mode 600 with encryption key |
| n8n workflows on restored database | 2.9s | five workflows, both templates present |
| scratch API against restored database | 5.8s | status 200, cited answer, 12 citations |
| cleanup | 0.4s | zero scratch databases, no scratch container, production healthy |

Counts matched exactly: `jobs` 64, `job_events` 642, `job_steps` 237, `audit_events` 933, `sessions` 50,
`chunk_index` 10576, `LiteLLM_SpendLogs` 191, `LiteLLM_VerificationToken` 0, `workflow_entity` 5,
`credentials_entity` 0, `user` 1.

The scratch API answered: "The travel and expense policy reimburses personal vehicle mileage at 0.67 USD per
mile. The policy notes this covers fuel, wear and insurance, and that mileage and fuel cannot both be claimed
for the same trip." Twelve citations were attached. This is the customer-visible behaviour after a restore,
not only a row count.

## Measured recovery numbers

- Restore of the three databases: about 10 seconds.
- Full drill including backup, checks, scratch API and cleanup: 27 seconds.
- RPO: 24 hours. Backups run daily at 03:00 Eastern through `oi-product-backup.timer`, with a persistent
  timer, and are copied to `s3://oi-core-backups-878451098165/backups/product-oi-core-product-20261103/`.
  See `docs/reviews/product-backup-20261103/README.md`.

## Off-box restore, 2026-11-03

The local drill proved the archive; it did not prove the copy in the bucket. The off-box restore closes that.

- The fresh backup was made with row counts in its manifest. The same counts are now part of every backup.
- The nine objects were downloaded from
  `s3://oi-core-backups-878451098165/backups/product-oi-core-product-20261103/20261103T225539Z/` to a scratch
  directory on the box, checksums verified.
- The drill ran with `RESTORE_DRILL_EXISTING=1` so it did not create a new local backup. It restored the three
  databases, compared eleven table counts against the manifest rather than the live database, verified the
  chain, restored the documents and n8n data, ran the n8n CLI and a scratch API, and cleaned up.
- Result: 11 checks passed in 21 seconds. The counts matched the manifest exactly: 80 jobs, 811 job events, 300
  steps, 1,182 audit events, 68 sessions, 10,576 chunks, 245 spend rows, 5 workflows, 1 user.
- The first attempt failed for the right reason: it compared the restored copy with the live database, which
  had moved on since the backup. The count snapshot in the manifest is the fix.
- Downloaded copies were removed from the box after the drill. Evidence is in
  `evidence/offbox-summary.txt`, `offbox-counts.txt`, `offbox-timings.txt`, `offbox-chain-verify.txt` and
  `offbox-scratch-api-answer.txt`.

## Post-launch run, 2026-11-03 23:47Z

A fresh backup was taken after the customer edge, gateway audit fix and healthcheck change, then the local drill
ran against the new state: 12 checks passed in 26 seconds. The backup is 26 MB, nine objects in S3 under
`backups/product-oi-core-product-20261103/20261103T234724Z/`, and the manifest carries the 11 table counts. This
proves the current deployment, not only the pre-launch one.

## What this drill does not prove

- This drill restores from the local backup directory on the box. An off-box copy now exists and is
  uploaded daily, but restoring from the S3 copy has not been tested. A host loss is covered by the upload
  only after that second drill.
- The vector store is not restored. It is derived from the documents and can be re-imported; the drill does not
  measure a Qdrant rebuild time.
- The audit bucket and its Object Lock are not part of this drill.
- Restore time is measured on an idle box with a synthetic corpus. It is a floor, not a promise.
- The bucket is in the same account and writable by the product box role. That protects against host or
  disk loss, not account compromise, and it is not a client-owned backup target.

## Evidence

`evidence/` holds the script output: timings, counts, the known answer, chain verification, n8n workflow list,
the documents hash, the scratch API answer, and cleanup confirmation. Run the drill again with
`bash scripts/restore-drill.sh` on the product box; evidence lands in `eval-out/restore-drill-<timestamp>/`.
