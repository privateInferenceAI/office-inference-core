# Company onboarding: prepare the product deployment for real work

This is Phase E of `docs/runbooks/product-verification.md`. It is the step between a deployment that passes
acceptance and a deployment a business uses every day. It assumes Phase D has passed: a customer URL with TLS,
a working login, and no operator paths exposed.

Nothing in this checklist is optional before real documents go in. The order matters because a failed restore
after the documents are in is a different problem from reading this list beforehand.

## 0. Confirm the deployment choice

Three decisions from decision 0012 must be settled before this list runs:

- The pilot deployment: this product box, or a separate deployment for the business.
- The customer hostname and who controls DNS.
- The identity account and the initial accounts.

If this box is the pilot, the synthetic acceptance corpus stays for runbook checks and the company corpus is
separate. If the business gets its own deployment, this checklist runs on that one and the pilot stays for
tests.

## 1. Accounts and roles

- [ ] Create the owner account in Cognito. Anthony is the first owner; the provisioning script creates it.
- [ ] Decide who else needs an account. A small business usually starts with the owner and one other person.
- [ ] Add each person to the rendered access registry with a group: `staff` for company documents, `exec` for
      the executive root as well. The groups decide document access, not a role typed into a request.
- [ ] Sign in as each person through the customer URL and confirm they see what their group allows and nothing
      more.
- [ ] Confirm Activity shows the provider subject, and an approval made by an owner records
      `provider_verified` assurance and the asserted name.
- [ ] Remove the minted-session operator path from daily use. It stays for support and diagnostics, labelled
      admin-only in the runbooks.

## 2. Documents

- [ ] Follow `docs/runbooks/company-corpus-onboarding.md`. Put the company documents in one folder per
      access group, build or ingest them as a separate corpus, and verify before switching the served
      collection. Nothing else should be mixed into the synthetic folders.
- [ ] Choose a collection name for the company corpus and render the manifest against it. Do not reuse the
      synthetic collection name for real documents.
- [ ] Run the import and record the document and chunk counts.
- [ ] Ask three questions whose answers are known and confirm the citations point at the right documents.
- [ ] Ask a question only an executive document answers with a staff account and confirm the denial and the
      disclosure.
- [ ] Confirm the person keeps their own copy of the documents. The deployment is not the only copy.

## 3. Email

- [ ] Confirm the SES identity and the connector recipient allowlist in `rendered/host-services.env`.
- [ ] Send a test message from an approved job to the approved recipient and confirm delivery.
- [ ] Confirm a recipient outside the allowlist is refused, and that the refusal is in the chain.
- [ ] Decide who may approve an email send. Approval is a person, and the record names them.

## 4. Backups

- [ ] Confirm `oi-product-backup.timer` is enabled and has run at least once at the expected time.
- [ ] Confirm the latest backup exists locally and under
      `s3://oi-core-backups-878451098165/backups/product-oi-core-product-20261103/`.
- [ ] Run `scripts/restore-drill.sh` on the deployment with the company corpus and record the numbers.
- [ ] Decide the retention the business needs and confirm the script's seven local and thirty remote days
      match it. A client-owned backup target is still a gap for the first client.
- [ ] Confirm a database-only restore would not be used: the n8n data directory and the documents archive are
      both required.

## 5. Support and incident path

- [ ] Fill in the deployment section of `docs/runbooks/incident-response.md`: who is called, how, and what
      evidence is collected.
- [ ] Confirm the audit export and chain verification commands are known and have been run once.
- [ ] Decide the support cadence and response expectation for the pilot. A one-person business can be honest
      about this in writing.
- [ ] Record where logs live: `docker logs` for services, `journalctl` for the host services and backup, and
      the evidence directories under `eval-out/`.
- [ ] Keep the operator runbook current. Admin-only steps stay labelled.

## 6. Acceptance through the customer path

- [ ] Run Phase V of the product verification runbook using only the browser and the customer URL.
- [ ] Run Phase B twice, with the company corpus and the company accounts. Record the baseline.
- [ ] Run the restore drill once more after the corpus is final.
- [ ] Save the evidence off the box before it is stopped, upgraded or resized.

## 7. Daily use

- After every restart: the five smoke probes and two questions in the app.
- Weekly: Phase B and a backup sanity check.
- Before every upgrade: Phase B twice, Phase C, and a signed release with a rollback path.
- Monthly: check the S3 backup prefixes and the local retention, rotate the edge secret when it is due, and
  review the open findings list.

## Open items that belong to onboarding, not to this document

- Replace the product box's development instance role with a scoped role.
- A client-owned backup target with Object Lock.
- Scheduled audit export verification with an anchor.
- Written support terms for the pilot.

Those are tracked in the requirement rows and decision 0012. They do not block starting with the owner's own
documents, but they block calling the deployment a client deployment.
