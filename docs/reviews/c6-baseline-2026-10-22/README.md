# C6 baseline: the accepted fresh install after a stop and start, 2026-10-22

**Date:** 2026-10-22, 19:13 to 19:30 UTC. **Instance:** `i-02aba2feef08f8e7b`, m7i.4xlarge, us-east-1b,
200 GB gp3, account 878451098165. **Code:** `795b9a6`, archive sha256 `28c24862…b70c`.
**Deployment:** `oi-core-fresh-2026-10-22`, profile cloud, packs `[core]`. **Corpus:** 80 documents, 24,296
chunks. **Model:** Claude Sonnet 4.6 through Bedrock. **Result:** the durable state, the four probes, the
corpus-2 smoke and the code identity reproduce. The startup race, the gateway-to-audit gap and the shared quota
have limits recorded below.

## Why

C6 will be built on the accepted fresh install, not on the older development or corpus boxes. The install was
accepted on 2026-10-22 at 10:54 UTC. This check answers one question before any code changes: after a stop and a
start, what still matches the record, and what does not.

This is a baseline, not a measurement. It runs the recorded acceptance checks once. It does not rerun the
five-run answer suite. A five-run suite is a measurement, and C6 does not change the answer path. The suite runs
again only if a C6 change touches that path.

The first pass of this record claimed the box was a clean starting line. An independent review found that the
first bundle did not tie the running code to the commit, did not reconcile the gateway spend log against the
audit chain, and overstated the cold-start result. This version adds that evidence and records the limits.

## What was run

| Check | Expected | Observed |
|---|---|---|
| AWS identity | account 878451098165, assumed role DSHAgentRole | as expected |
| Instance | stopped, then started for the check, then stopped | started at 19:13:39 UTC; three starts and three stops during the session |
| Instance facts | m7i.4xlarge, us-east-1b, 200 GB gp3, profile `oi-core-dev-instance` | as expected, from `DescribeInstances` |
| Public IP | no Elastic IP, new address on every start | 18.234.126.59, 44.212.75.211, 18.212.74.87 |
| SSM | instance profile, agent online | Online, agent 3.3.4793.0 |
| Services | 8 containers: postgres, embeddings, parser, ingestion, vector, litellm, api, app | 8 running, healthchecks healthy |
| Migrations | 23, last `024_the_row_knows_its_table.sql` | 23, same last file |
| Index | 80 documents, 24,296 chunks | 80, 24,296 |
| Dead letters | 0 at `failure_count >= 2` | 0 |
| Data-set chunks | 0 | 0 |
| Manifest rows | 80 | 80 |
| Qdrant | collection green, 24,296 points, 1024 dimensions, cosine | green, 24,296, 1024, cosine |
| Code identity | S3 archive and box code are `795b9a6` | S3 hash matches; 495 tracked code files, 0 hash differences |
| Four probes | 24 rights held, every exit 0 | 24 held, every exit 0 |
| Corpus-2 check | 5 of 5 pass | 5 of 5 pass in 35.8 s |
| Model window | Sonnet 4.6, no fallback | 69 Sonnet rows, 2 forced-degradation rows, 0 fallback true |
| Gateway spend log | Sonnet only, no errors | 94 successful Sonnet completions, no other model |
| Gateway against audit | explained or recorded | 25 successful completions unaccounted by row; reconciled by window |
| Audit chain | export and verify | 157 events, chain intact |
| Quota | no throttle in the baseline window | no 429 or fallback; 554 account-wide throttles earlier in the day |

`counts.txt` in the raw bundle was written before the last probe run; `final-counts.txt` has the closing
count of 157 audit events and 71 model-call rows.

## Findings

**The accepted state is intact.** The durable data re-verified exactly: 80 documents, 24,296 chunks, 0 dead
letters, 0 data-set chunks, 80 manifest rows, 23 migrations, and a green Qdrant collection at 24,296 points. The
corpus-2 question file on the box is byte-identical to the one in the repository, sha256
`bc489cdf…c31ec`. The rendered gateway still names `bedrock/us.anthropic.claude-sonnet-4-6` for the analyst,
and the rendered environment still sets `OI_ANSWER_PROFILE=accuracy`.

**The code is 795b9a6.** The acceptance record's archive hash `28c24862…b70c` matches the S3 object
`fresh-install/oi-core-795b9a6.tar.gz` today. That archive holds 496 files and its tree is identical to
`git archive 795b9a6`, with zero differences. The box's `/opt/oi-core` has 495 tracked code files and zero
hash differences against the same tree. The two non-code differences are the runtime
`data/model-lifecycle.json` and the install-specific `manifests/fresh-install-2026-10-22.yaml`. The images
were built on the box at 09:17 UTC, before acceptance. The evidence is `verification/code-identity.txt`.

**Ingestion still loses the startup race.** On each of three starts, the first ingestion cycle failed with
`cannot use the vector store: Connection refused` and `cycle exited non-zero` while the container healthcheck
reported healthy. A manual restart after the stack was up produced a clean cycle: seen 80, new 0, skipped 80,
failed 0, no failures. The worker was not observed recovering on its own. This matches the fresh-install
runbook's failure table. The operational rule is: after every start, read the ingestion log and restart the worker
if its last cycle failed. Do not treat the container healthcheck as proof that the cycle ran. No data was lost and
no re-ingest was needed. The timestamped log is `verification/boot-ingestion-timestamps.txt`.

**The four probes pass on the live box.** The probe run is a fresh run, not the accepted one:

| Probe | Rights held | Exit |
|---|---|---|
| `interface_probe` | ANSWER, NO-JUDGE, TRIGGERS, FACTS, LANE-AGREE, TOLD, STREAM, DEGRADED | 0 |
| `activity_probe` | READ, SCOPED, ACTOR, FILTERED, BYREQUEST, BYJOB, CAPPED, RECORDED | 0 |
| `access_probe` | ACCESS, SCOPED, REFUSED, RECORDED | 0 |
| `stream_probe` | END-TO-END, TTFT, INCREMENTAL, REDACTED | 0 |

The access probe prints a fifth line, `skip FORGED`, because no declared role can claim a group beyond its own
grant on this deployment. The probe counts four rights and so does this table. The `STREAM` line in the probe
output reports a first delta of 1376 ms against a total of 1151 ms. That is an inconsistency in the probe's own
timing fields, not a failed right. The probe exited 0 and printed `PASSED: 4 right(s)`.

**The corpus-2 smoke check is 5 of 5.** The run used the recorded four question ids, with `SYN_A01` answered once
per role:

| Question | Role | Result | Source document |
|---|---|---|---|
| `PUB_N01` | staff | PASS | `company/second-public/company/target-corp/10-K_2026-03-11_annual-report.htm` |
| `PUB_P01` | staff | PASS | `company/second-public/company/intuit-inc/EX-10_2026-01-12_credit-agreement.htm` |
| `PUB_L01` | staff | PASS | `company/second-public/company/caterpillar-inc/EX-10_2026-09-01_credit-agreement.htm` |
| `SYN_A01` | staff | PASS | `company/second-synthetic/org-chart.md`, the CEO salary is refused |
| `SYN_A01` | executive | PASS | `executive/second-synthetic/executive-compensation-2025.md`, the salary is 385,000 |

Every category in the JSON summary has `runs` equal to `passed`. This rerun took 35.8 seconds. The accepted run
took 33.5 seconds.

**No fallback happened.** Every audited model-call row names Sonnet 4.6 with `fallback_used=false`. The two rows
with no resolved model are the forced degradations recorded by the accepted install, not fallbacks. The gateway
spend log has 94 successful Sonnet completions and no other model.

The gateway spend log and the audit chain do not have the same row count. The reconciliation by window is:

| Window | Gateway completions | Audit model_call rows |
|---|---|---|
| Before the chain starts, 09:22 and 10:49:18 | 2 | 0 |
| Acceptance, 10:49:20 to 10:55 | 61 | 44 (42 successful Sonnet, 2 degraded) |
| Baseline, after 19:15 | 31 | 27 |
| Total | 94 | 71 (69 successful Sonnet, 2 degraded) |

The 25-successful-call difference is the two calls before the chain starts, 19 tool-loop completions in the
acceptance window, and 4 in the baseline window. The audit chain records one `model_call` row per answer, and the
tool loop makes more than one completion per answer. This is a window-level reconciliation, not a row-level match.
It is not a fallback. The evidence is `verification/spend-audit-note.txt` and `verification/spend-rows.txt`.

**The audit chain is not yet a complete record of provider use.** This is the C6 design input, not a quota or
fallback finding. The gateway spend log is the provider record: 94 completions. The audit chain is the product
record: 71 rows, one per answer. A task runtime multiplies completions further, through sub-tasks, tool steps and
checkpoints. If the runtime inherits per-answer granularity, the distance between gateway activity and the chain
grows with every task, and the chain stops being a credible record of model use, token spend and model identity.

AUD-MODEL-001 is the requirement that closes this for the runtime: one chain row per completion, carrying the
task, step, role, model, token count and attribution facts, so the chain reconciles against the gateway spend
table. The existing answer-level `model_call` row is part of the API contract and the measurement instrument. It
is not redefined. The runtime adds per-completion coverage alongside it, and the answer path's record stays as it
is.

**The day-to-date model count is not the acceptance count.** The accepted window recorded 42 Sonnet calls and 2
degraded rows. Today's total after this baseline is 69 Sonnet rows and 2 degraded rows. The baseline added 27
Sonnet rows. The two numbers answer different questions, and the cutoff is in
`verification/spend-audit-reconciliation.txt`.

**The audit chain verifies, with the usual limit.** The export holds 157 events, ids 1 to 157, chain intact,
last row hash `86332637…d3ea`. No external anchor was supplied, so completeness cannot be shown. Deleting rows
from the tail and re-exporting would still verify. The anchor job is C8 work and this baseline did not install
one.

**Quota is account-wide and was already constrained today.** The account's `Cross-Model Max Tokens Per Day` is
150,000,000 and non-adjustable. CloudWatch for Sonnet 4.6 shows 554 `InvocationThrottles` between 00:00 and
03:59 UTC today, before this baseline, with 1,993,272 input tokens and 22,492 output tokens account-wide. The
acceptance and baseline windows had no throttle and no fallback. This deployment's share is 94 calls and 568,542
tokens. The allowance is shared, and the fallback guard stays mandatory for any suite run. The evidence is
`verification/quota-summary.txt`.

## What this does not prove

- It is not a five-run answer measurement. It is one pass of the acceptance checks.
- It does not reproduce the acceptance timings or the 84-minute ingestion. The index was already built.
- It does not prove the box survives a cold start without the ingestion restart. It shows the race on three starts
  and the manual fix.
- It does not prove row-level audit coverage of the tool loop. The reconciliation is by window.
- It does not prove quota headroom. The account was throttled earlier today, before the baseline.
- It does not show anything about C6, which does not exist yet.
- The public IP changes on every start, and `/tmp` is not durable. The evidence bundle was copied off the box
  before the stop. `/opt/oi-core/evidence/fresh-install` is still on the root volume.

## Evidence

Workstation archive root: `/home/anthony/oi-eval-runs/c6-baseline-2026-10-22/`

| Item | Path or hash |
|---|---|
| Complete bundle | `c6-baseline-2026-10-22-v2.tar.gz`, sha256 `5d6d0164…4178` |
| Bundle index | `README.md` |
| Reproduction commands | `commands.txt` |
| File checksums | `SHA256SUMS` (relative paths, verified with `sha256sum -c`) |
| Code identity | `verification/code-identity.txt`, `box-code.sha256`, `git-code.sha256`, `s3-code.sha256` |
| Audit export | `c6-baseline/audit-export/audit-events.jsonl`, sha256 `0f61d1aa…de8a` |
| Corpus-2 JSON | `c6-baseline/corpus2-check.json`, sha256 `4cf8aae2…11d9` |
| Probe transcripts | `c6-baseline/probes.txt` |
| Boot race | `verification/boot-ingestion-timestamps.txt`, `c6-baseline/ingestion-cycle.txt` |
| Gateway against audit | `verification/spend-audit-note.txt`, `spend-rows.txt`, `spend-audit-reconciliation.txt` |
| Quota and throttles | `verification/quota-summary.txt`, `aws-cloudwatch-bedrock-today.json` |
| Instance facts | `verification/instance-state.txt` |
| Audit verify | `c6-baseline/audit-verify-final.txt` |

The preceding archive `c6-baseline-2026-10-22.tar.gz` (sha256 `0c89f26c…c91b`) is the first pass and is
superseded by the v2 bundle. It remains on disk for comparison. No credentials are present in either bundle. The
audit export contains prompt text and the synthetic salaries 385,000 and 372,000, which are test content.

The box is stopped, not terminated. The accepted install evidence remains under `/opt/oi-core/evidence/fresh-install`.

## Next

The C6 section plan goes to the owner for approval before any C6 code is written. Section 1, closing the eight
open C5 approval rows, is the recommended first section because AG-GATE reuses that gate and the approval record.
