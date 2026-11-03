# Company documents: intake and provenance

How the company's documents get from the owner to the deployment without becoming a second uncontrolled copy.
This is the transport and bookkeeping step in Phase E. Building and serving the corpus is
`docs/runbooks/company-corpus-onboarding.md`.

## Before anything moves

- [ ] The owner confirms they keep their own copy. The deployment is never the only copy.
- [ ] The pilot decision is made: this box with both corpora, or a separate business deployment.
- [ ] The document set is agreed: which folders, who may see them, and which are executive-only.
- [ ] The backup schedule is running and the latest backup has been restored once. Adding documents grows the
      only copy if backups are not working.

## Transport

Use one of these, in this order of preference:

1. **Owner uploads to a staging area on the product box over SSH.** The owner uses their own key or a temporary
   key the operator creates for the transfer and removes afterwards. Destination:
   `/home/ubuntu/company-staging/<date>/`.
2. **Operator copies from a source the owner controls** with `scp` or `rsync` over an encrypted connection.
3. **A signed link the owner controls**, downloaded to the staging area and checksum-verified.

Do not send company documents through chat, email attachments, or a shared consumer drive. Do not put them in the
repository.

## Layout and provenance

The registry groups are `staff` and `exec`. The corpus folders must match:

```
company-staging/<date>/
  company/        documents the staff group may read
  executive/      documents only the exec group may read
  provenance.csv  one row per file
```

`provenance.csv` columns:

```
relative_path,source,owner,data_class,retention_note,sha256
company/travel-and-expense-policy.pdf,shared drive,Treasury,internal,seven years,<hash>
```

- `relative_path` is what the ingest layout and citations will show.
- `source` says where the file came from, not where it sits now.
- `data_class` must be one the deployment declares (`public`, `internal`, `confidential`, `regulated`).
- `sha256` is computed at intake. A file that changes after the hash is a new document, not an edit to hide.

Compute the hashes and check them before ingestion:

```bash
cd /home/ubuntu/company-staging/<date>
find company executive -type f -print0 | sort -z | xargs -0 sha256sum > SHA256SUMS
sha256sum -c SHA256SUMS
```

## Permissions and cleanup

- Staging files are readable by the operator account only until they are placed in the deployment's document
  root; set mode 700 on the staging directory and 600 on files.
- After the bundle or ingestion run succeeds and the counts are recorded, remove the staging copy. The serving
  copy and the backup are the working copies; the owner holds the original.
- Record the staging path, the date, the file count and the checksum file hash in the onboarding record.

## Intake acceptance

Do not switch the served collection until all of these are true:

- [ ] File count and per-file checksums match `provenance.csv` and `SHA256SUMS`.
- [ ] The ingestion run has zero failures. A refused document is a worklist item, not a warning to ignore.
- [ ] Document and chunk counts are recorded for the company collection.
- [ ] Three known questions return answers with citations to the expected company documents.
- [ ] An executive-only question with a staff account is denied with a disclosure.
- [ ] An executive account can answer the executive question.
- [ ] A restore drill with the new corpus passes, and the evidence is copied off the box.
