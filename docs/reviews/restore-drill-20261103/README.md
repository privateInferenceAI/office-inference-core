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
| Backup | fresh backup at the start of the drill, 4.4 MB, checksums verified before restore |
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

All eleven checks passed. Total elapsed: 24 seconds.

| Step | Time | Result |
|---|---|---|
| backup | 3.4s | 4.4 MB, checksums verified |
| create scratch databases | 0.4s | |
| restore three databases | 9.6s | |
| compare counts | 1.1s | eleven tables equal |
| known answer | 0.05s | contains 0.67 |
| audit chain on restored database | 0.5s | 933 events, chain intact |
| n8n data directory | 0.01s | 7 files, config mode 600 with encryption key |
| n8n workflows on restored database | 2.9s | five workflows, both templates present |
| scratch API against restored database | 5.2s | status 200, cited answer, 12 citations |
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
- Full drill including backup, checks, scratch API and cleanup: 24 seconds.
- RPO: the time since the last backup. Backups are currently run on demand; there is no timer yet, so the
  practical RPO for business data is "since the last manual backup". Scheduled backups are still a C8 item and
  become important before real company documents go in.

## What this drill does not prove

- It restores into the same Postgres instance and the same host. A host loss, a disk loss or a corrupted
  Postgres install also loses the scratch path. The backup directory is on the same box, so "the box is gone"
  is not covered until the backup is copied to durable off-box storage.
- The vector store is not restored. It is derived from the documents and can be re-imported; the drill does not
  measure a Qdrant rebuild time.
- The audit bucket and its Object Lock are not part of this drill.
- Restore time is measured on an idle box with a synthetic corpus. It is a floor, not a promise.
- No scheduled backup and no off-box copy exist yet. Both belong in the onboarding checklist before the first
  real document is ingested.

## Evidence

`evidence/` holds the script output: timings, counts, the known answer, chain verification, n8n workflow list,
the scratch API answer, and cleanup confirmation. Run the drill again with
`bash scripts/restore-drill.sh` on the product box; evidence lands in `eval-out/restore-drill-<timestamp>/`.
