# Handoff

Where the build stands, how we work, and what is next. Written for whoever picks this up next, human
or agent, with no access to the conversation that produced it.

Everything else is already written down, and this file points at it rather than repeating it:

| Document | What it holds |
|---|---|
| `docs/manual.md` | How the system works and every command with a real example. The operating manual |
| `docs/runbook.md` | The build log, phase by phase, in the order things were built, with the reasoning |
| `docs/build-brief.md` | The plan: phases C0 to C9, the offerings, the boxes, the sequence of record |
| `docs/requirements.yaml` | The register: 66 requirements, each with a verification and a status |
| `docs/audit-evidence.md` | The record's design: what each check proves and what it does not |
| `docs/model-lifecycle.md` | Who owns model deprecation, how it is detected, what a swap costs |

## Where it stands

**C0 and C1 are complete. C2 is next and has not started.** The register reads 14 verified, 46 open,
6 waived.

What exists and runs in the client's account: one EC2 instance, Postgres and the LiteLLM gateway,
four roles on Bedrock, per-role keys with hard ceilings and model permissions, an append-only
hash-chained record, a daily export and an hourly anchor shipped to an S3 bucket the machine cannot
delete from, and CI that tests all of it.

What does not exist yet: anything that reads a document. The system answers from a model's general
knowledge today. That is C2, and it is the phase that makes the product answer from a client's own
material.

## How we work

The build is a teaching exercise as much as a delivery. The division is: **the agent writes code and
documentation on branches with pull requests; Anthony types every command and pastes the output.**

Per step, the agent gives: the goal in one line, the commands to paste, what the output should look
like, what just happened in plain English, a check, and the two or three things to try if it breaks.
When Anthony says "continue", the step did what was described and the next one follows. When output
surprises him or errors, he pastes it.

House rules that have earned their place:

- Never commit to `main`. Branch first, open a pull request, and check that CI is green before saying
  it is ready. Anthony merges pull requests himself, with one exception: **during an end-of-phase
  review round the agent may merge its own pull requests**, so that a reviewer executing on the box is
  testing the merged code rather than a branch. The agent reports which ones it merged.
- Anthony merges quickly, sometimes while the agent is still working, so **check whether a pull request
  is still open before pushing to its branch**, and open a fresh one if it is not. This has cost time
  twice.
- A phase is finished when someone who did not write it can run it from the documentation. The review
  panel and the three-round cap are in `AGENTS.md` under "What finished means".
- Write down every new mechanism in `docs/`, and record every register change in
  `docs/requirements.yaml` at the same time as the code.
- Verify on the box before calling something done. Three of the most useful findings in C1 came from
  running a command, not from reading it.
- Digest-pin images with the version in a comment. Nothing publishes a port. Secrets never in git.

## The machine

| Thing | Value |
|---|---|
| Instance | `i-096bebd46308d1673`, m7i.4xlarge, us-east-1, Ubuntu 24.04, 200 GB gp3 |
| Address | `52.204.202.66`, an Elastic IP, so it survives a stop |
| Account | `878451098165` (Office Inference), AWS profile `oi-credits` |
| Key | `ssh -i ~/ai-stack/ai-stack-key.pem ubuntu@52.204.202.66` |
| Repo on the box | `/opt/oi-core`, on `main` |
| Bucket | `oi-core-audit-dev`, Object Lock GOVERNANCE, 90 days |
| Cost | about 0.81 an hour while running. **Stop it when finished**: `aws ec2 stop-instances --profile oi-credits --region us-east-1 --instance-ids i-096bebd46308d1673` |

A git stash exists on the box holding work that was applied by hand and is now upstream. It is a
safety net and can be dropped.

## Traps that cost time in C1

Each of these is in the manual's troubleshooting section, and each was learned the hard way:

- **`oi up -d <service>` does not reload a mounted file.** Compose recreates a container when its
  definition changes, and a file changing underneath one is invisible to it. It prints
  `Container litellm Running` and does nothing. Use `oi restart <service>`.
- **`OI_ROOT` decides where the database, exports and migrations live.** Pointing compose at an env
  file that names a different root starts Postgres against an empty directory, which looks exactly
  like data loss.
- **The snapshot tools run from images.** A change to `services/audit/audit.py` does nothing until
  `oi build audit keys`.
- **The gateway writes its spend rows asynchronously.** A backfill in the same second as a call can
  miss it; the next run picks it up, because the pass is idempotent.
- **Migrations are checksummed.** Editing an applied migration is refused. A schema change is a new
  file.
- **`set local` only works inside a transaction**, and the audit tool's connections are in autocommit
  mode. That combination once wrote a tombstone for a purge that never happened.
- **The `oi` alias exists only in an interactive shell.** Over `ssh host 'command'` write the compose
  command in full.
- **`data/*` is gitignored** with a single negation for the lifecycle table, which silently swallowed
  that file once. Check `git ls-files` when a script cannot find a file that exists.
- **`services/_template/service.yaml` is the declaration** every service carries, and a new service is
  registered in four places: its directory, the compose file, the pack catalog, and
  `docs/service-inventory.md`.

## What is next: C2

The brief's acceptance for C2: **a cited answer from a scanned, table-bearing document; a recorded
refusal; a superseded document not cited; and redaction proven by a test.** Twelve register items:

| Area | Items |
|---|---|
| Ingestion | `RET-PARSE-001` Docling for scans, tables and reading order; `RET-FORMAT-001` email, spreadsheets, slides; `RET-DUPE-001` near-duplicate detection |
| Retrieval | `RET-HYBRID-001` hybrid search for exact identifiers; `RET-CITE-001` the three-check citation verifier; `RET-VERSION-001` a superseded document is not cited |
| Security | `SEC-ACL-001` role filtering inside the search; `SEC-REDACT-001` redaction before anything frontier-bound leaves; `SEC-INJECT-001` document content cannot change the lane or the answer's provenance |
| Data lifecycle | `DATA-RETAIN-001` retention for documents and conversations; `DATA-PURGE-001` a client-requested purge everywhere; `DATA-HOLD-001` legal hold blocks a purge |

The first thing to do is not code. It is to write the C2 section of `docs/runbook.md` the way C1's
Phase 2 was written: the sequence, in order, with what each step proves, and then build it a step at a
time. Pick the first item from the table above, not the whole phase.

Two notes that will save time:

- The new containers (Docling, Qdrant, the two TEI services) follow the same rules as the existing
  ones: on `ai-net`, no published ports, digest-pinned images, and a `service.yaml` plus a README.
- `SEC-REDACT-001` is the real redaction engine, and the content modes built in C1 are a different
  thing: those decide what the *record* keeps. Do not confuse them, and do not let the record's
  pattern-based redactor stand in for the engine that has to run before a frontier call.

## Deliberately open

| Item | Why it is open |
|---|---|
| `AUD-HASH-001` | Four columns are outside the row hash, `occurred_at` among them. Closing it needs a versioned hash so existing rows still verify |
| Terraform (C7) | The account is built by hand today, which is fine for one development box and not for a client |
| Backups (C8) | `offsite.ship` accepts `backups` and nothing ships one yet. There is no restore drill |
| The office build | Appendix A of the runbook. The same components with local weights, and nothing has run on real office hardware |
