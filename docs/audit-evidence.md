# Audit evidence: what leaves the building, and why

This explains the part of the system that sends audit artifacts off the machine that wrote them. It
covers what goes in the bucket, what must never go in it, why the anchor has to leave at all, how the
permissions are arranged so the box cannot erase what it wrote, and how the three build shapes differ.

Written 2026-10-11, after building the bucket and before building the job that fills it.

---

## 1. Three things people confuse

The audit story has three separate pieces, and mixing them up causes most of the confusion.

| Piece | What it is | Where it lives | Who can change it |
|---|---|---|---|
| **The record** | The `audit_events` table: one row per event, hash-chained, append-only by trigger | Postgres, on the box | Nobody through the app. A trigger refuses updates and deletes |
| **The export** | One day's rows as JSON lines, plus a manifest | A file on the box, then offsite | Anyone with file access, so it is only trustworthy when checked against an anchor |
| **The anchor** | The chain head at a moment in time: the last row's hash and the event count | A small file, sent offsite immediately | Nobody, once it is in the bucket. That is the point |

The record is the live source. The export is a copy you can hand over. The anchor is the thing that
makes the copy checkable by someone who does not trust us.

---

## 2. What is in the record, and what is deliberately not

Each row holds the actor, their role, the event type, the data class, the outcome, the tool or model
involved, the request id, the payload hash and size, a hash of the row, and the previous row's hash.

A model call row adds the model that answered, the token counts, the spend, and a fingerprint of the
API key that made the call. The fingerprint is eight hex characters of a SHA-256 over the key, so two
keys can be told apart in the record without the record holding either key.

**What is not in the row by default:** the document, the prompt, and the answer. The row carries
`payload_sha256`, a hash of the payload, instead of the payload itself. That is the rule from Phase 2:
the record has to prove what was sent without becoming a second copy of the client's documents. A
record that quietly accumulates copies of client material is a liability, not evidence.

`AUD-MODE-001` is where a client may opt into more, per data class: hash only, redacted, or full
content, with prompt and completion hashes recorded. That is a policy decision the client makes and
pays for, and it is not built yet.

---

## 3. What goes in the bucket

Two prefixes, and nothing else:

| Prefix | Contents | Size |
|---|---|---|
| `anchors/` | One small JSON file per run: chain head hash, event count, timestamp | About 300 bytes each |
| `exports/` | One directory per day: `audit-events.jsonl` and `manifest.json` | About 650 bytes per event, so under 1 MB a day for a ten-person firm |

**Never in this bucket:** documents, prompt text, model answers, database dumps, or application
backups. Documents belong in the document store and its own bucket. Backups are a separate concern.
If a client opts into a content mode that stores prompt text, that text lands in the audit row in
Postgres and therefore in the export, and the contract has to say so before it is switched on.

**Why it has to leave the box.** A record that exists only on the machine it describes can be
rewritten by whoever controls that machine. Rows can be deleted from the end of the chain, the
manifest regenerated to match, and every check would pass, because the file would be internally
consistent. The only thing that catches a deleted tail is a hash published earlier, somewhere the box
cannot reach. That is the whole reason for Object Lock and for withholding delete permission from the
box's own role.

An anchor kept beside the database it describes proves nothing. The tool says so in its own output.

---

## 4. The bucket

Configuration, and the reason for each piece:

| Setting | Value | Why |
|---|---|---|
| Object Lock | Enabled **at creation** | It cannot be added to an existing bucket. A bucket created without it has to be replaced, so this is a creation-time decision and it is permanent |
| Default retention | **GOVERNANCE**, days from `compliance.audit_retention_days` | Governance can still be bypassed by an administrator with the permission. Compliance cannot be shortened or removed by anyone, including root, until it expires |
| Versioning | Enabled | Object Lock requires it |
| Public access | All four block settings on | Evidence is not public material |
| Encryption | SSE-S3 for a standard build, a customer-managed KMS key when a compliance profile is on | Key custody is part of what a compliance profile buys |
| Lifecycle | STANDARD_IA at 90 days, GLACIER_IR at 365, **no expiration** | Cost control without ever deleting. An expiration rule would fight the retention period and is refused for locked objects |
| Name | `<client>-audit-<deployment_id>` in the client's account | The client owns the account. A reference deployment in our own account uses `oi-core-audit-dev` |

**Governance or Compliance.** Compliance mode is a promise nobody can break: not us, not the client,
not AWS support, until the retention period expires. At 2,190 days that is six years in which the
bucket cannot be deleted, which is the wrong default for a deployment that might be torn down. Use
Governance unless a regime demands otherwise, disclose which one is in use in the contract, and price
the storage.

**Retention comes from the manifest.** `compliance.audit_retention_days` is 90 in the development
manifest and 2,190 for a broker-dealer. The validator already enforces a six-year floor for
`sec-17a4`, so the bucket and the manifest cannot drift apart without a test failing.

---

## 5. The permissions, and why each absence matters

The box's role can write evidence and read it back. It cannot destroy or rewrite it.

| Action on an object under `anchors/` or `exports/` | Decision | Why it is arranged that way |
|---|---|---|
| `s3:PutObject` | allowed | The job has to ship |
| `s3:GetObject` | allowed | So the job can read back what it sent and compare hashes |
| `s3:DeleteObject` | denied | A box that can delete its own evidence is not evidence |
| `s3:DeleteObjectVersion` | denied | Versioning would otherwise be a way around the first denial |
| `s3:BypassGovernanceRetention` | denied | This is the permission that makes Governance mode removable |
| `s3:PutObjectRetention` | denied | Otherwise the box could shorten the retention and then delete |
| `s3:PutBucketObjectLockConfiguration` | denied | Otherwise it could turn the lock off at the bucket level |

This was verified with `SimulatePrincipalPolicy` when the policy was written. **A simulation is not a
test.** The real test runs after the job is deployed: ship an anchor, confirm it is in the bucket, then
try to delete it from the box and watch the denial.

**The honest limit.** In GOVERNANCE mode an account administrator who holds
`s3:BypassGovernanceRetention` can still remove an object, and that includes us. The control being
built here is that *the box* cannot, and that the attempt is visible in CloudTrail. A client who needs
more than that needs COMPLIANCE mode and a contract that says so.

---

## 6. The schedule

| Job | Frequency | What it does |
|---|---|---|
| Anchor | Hourly | Writes the chain head, uploads it to `anchors/`, reads it back, compares the hash |
| Export | Daily | Writes the day's JSONL and manifest, uploads both to `exports/<date>/`, reads them back, compares the hashes |

Hourly for the anchor because it is tiny, and because it caps the window in which rows could be
deleted and the head rewritten before a copy lands somewhere the box cannot reach. Daily for the
export because that is the size of a day's events.

The read-back is what makes this evidence rather than a claim. Uploading proves something was sent.
Fetching the object back and confirming its hash matches the file that was sent proves it arrived
intact. Without it, a truncated upload and a successful one look the same.

Both jobs run on a **systemd timer**: a `.timer` unit says when, a `.service` unit says what,
`systemctl list-timers` shows the schedule, and `journalctl -u` shows every run with its output. That
is the operational story a support conversation needs. `Persistent=true` on both, because the box is
stopped when nobody is working, and a missed run should happen at the next start rather than never.

**Why the anchor key repeats, and why that is deliberate.** The filename is the date plus the event
count, so an hourly anchor over an unchanged chain uploads the *same key* every time. S3 versioning
then records each write as its own version with a server-side timestamp, which is a better record than
unique filenames would be: the version list is an ordered history of every anchor written, timestamped
by AWS rather than by us.

Two consequences worth knowing before you look:

- `aws s3 ls` shows one object per key. `aws s3api list-object-versions` shows the sequence. An
  examiner wants the second one.
- Each version carries its own retention clock, so the bucket grows by about 300 bytes an hour instead
  of being overwritten.

An anchor whose head hash equals the previous run's is the healthy case: nothing changed in between. A
head hash that moves backwards, or that changes when no events were written, is the signal worth
alarming on, and it is one of the five alarms in the brief.

---

## 7. The three build shapes

This is the part that changed on 2026-10-11, when it turned out an office build can have its own AWS
account without being a hybrid deployment.

### Cloud build

Runs in the client's AWS account. Inference is Bedrock. The box is an EC2 instance with an instance
role, so credentials arrive through the instance metadata service and no key material exists on disk.
The bucket is created by Terraform in the client's account at C7, and the manifest records its name.
Shipping is always on.

### Office build, no AWS account

Runs on the client's hardware in their building with no cloud account at all. There is nowhere offsite
to send anything, so the anchor goes to a **second local channel**: a NAS, a second machine, a disk the
client rotates off site.

Say plainly what that is worth. A second disk in the same box, or a share on the same network, is weak
evidence: whoever can rewrite the database can usually reach it too. It is better than nothing, and it
is not the same as an anchor the box cannot touch. A client with a compliance claim should be told
which of the two they are buying.

### Office build with an AWS account

The same appliance, plus an account that exists only to hold evidence. This is a legitimate shape and
arguably a better one than the cloud build for the anchor, because "nothing leaves the building" makes
a deleted tail easier to hide and an offsite anchor harder to argue with.

What changes:

- The job ships the anchor, and optionally the export, to an Object-Locked bucket in the client's
  account. Which artifacts leave is declared in the manifest, never assumed.
- The permissions are the same shape as the cloud build: put and read back, never delete.
- **Credentials are the interesting difference.** A client's own hardware has no instance role, so the
  options are a long-lived access key sitting on their server, or IAM Roles Anywhere: a certificate on
  the appliance exchanged for short-lived AWS credentials. Roles Anywhere is the one to use, and it is
  a C7 decision to formalize.
- The job reads credentials from the AWS SDK's default chain, so the same image works on both
  substrates. The substrate decides how credentials appear, not the code.

**What never changes.** Inference stays local, documents stay local, and prompts stay local. The only
thing that crosses the network is the evidence trail the client asked to be able to prove. An office
build that ships its audit anchors is still an office build.

---

## 8. The manifest field

The bucket name is configuration, not code, because a client's bucket is created in their account by
Terraform and nothing should be hand-typed on a server. The intended shape:

```yaml
offsite:
  provider: s3
  account_owner: client
  bucket: acme-audit
  region: us-east-1
  ship: [anchors]            # anchors, exports, backups. Nothing else is accepted.
```

Rules the validator enforces, or will:

1. **`ship` is an allowlist.** Only `anchors`, `exports`, and `backups` are accepted. `documents` and
   `prompts` are refused, so "nothing leaves the building" cannot be quietly turned off by editing a
   list.
2. **`itar-cui` refuses offsite shipping entirely**, because that profile's whole point is that no
   third party, including AWS, can reach the material.
3. **A compliance profile requires the retention floor**, so a hipaa deployment cannot ship to a
   bucket with a 30-day lock.
4. **The office profile may ship evidence and nothing else.** Inference stays local, and the frontier
   lane stays off. An earlier draft of this document proposed refusing offsite destinations on the
   office profile outright. That was wrong, and this section is what replaced it.

---

## 9. What is not built yet

| Item | What it is |
|---|---|
| `AUD-ANCHOR-001` | The anchor job on a timer. The bucket exists; nothing fills it yet |
| `AUD-EXPORT-001` | The daily export shipped offsite |
| `AUD-MODE-001` | Per-data-class content modes, with prompt and completion hashes |
| `AUD-PURGE-001` | A tombstone so a purge leaves a visible gap rather than an invisible one |
| C7 | Terraform that creates the bucket in a client's account, and the Roles Anywhere decision for office appliances |

---

## 10. Checking it yourself

The bucket configuration, in the account that holds it:

    aws s3api get-object-lock-configuration --bucket <bucket> --profile <profile>
    aws s3api get-public-access-block --bucket <bucket> --profile <profile>
    aws s3api get-bucket-lifecycle-configuration --bucket <bucket> --profile <profile>

What the box is allowed to do, after it tries:

    # ship something, then try to remove it. The first should succeed and the second should not.
    aws s3 cp anchor.json s3://<bucket>/anchors/
    aws s3 rm s3://<bucket>/anchors/anchor.json

And the check that needs no trust in us at all: take any day's export, recompute each row's hash from
its own contents, and confirm it matches the `row_hash` in the file. The SQL for that is in the runbook
at 2.3.
