# Fresh install acceptance, 2026-10-22

**Date:** 2026-10-22. **Instance:** `i-02aba2feef08f8e7b`, m7i.4xlarge, us-east-1b, 200 GB gp3, account
878451098165, profile `oi-core-dev-instance`. **Code:** commit `795b9a6`, archive sha256 `28c24862…b70c`.
**UI:** `oic-ui` commit `6d28c3c`. **Corpus:** 80 documents, 24,296 chunks. **Model:** Claude Sonnet 4.6 through
Bedrock, no fallback. **Result:** accepted. The procedure is `docs/runbooks/fresh-install-agent.md`.

## Why

The install path had only ever been exercised on the development box, which carries years of accumulated state.
This run starts from a bare Ubuntu instance and a pinned archive, so it answers one question: can somebody who
did not build this deployment reproduce it from the documented assets.

## What was installed

| Component | Version or identity | Evidence |
|---|---|---|
| Code | `795b9a6`, 3,606,299 bytes | `code-archive.sha256` |
| UI | `oic-ui` `6d28c3c`, 148,271 bytes | `ui-archive.sha256` |
| Corpus | `second-corpus-2026-10-22.tar.gz`, 21,613,548 bytes, 80 files | `corpus-archive.sha256` |
| Docker | 29.9.2, Compose v5.6.1 | host prep log |
| LiteLLM | 1.104.2 | `litellm-version.txt` |
| Migrations | 23, last `024_the_row_knows_its_table.sql` | `schema-migrations-count.txt` |
| Gateway models | analyst `bedrock/us.anthropic.claude-sonnet-4-6`, fallback Haiku 4.5 then Nova Pro | `litellm-models.txt` |
| Manifest | `oi-core-fresh-2026-10-22`, client `Office Inference Fresh Install` | `rendered/manifest.json` |

## Timeline

All times UTC, from `acceptance-start.txt`, the progress monitor, and the launch timestamp.

| Step | Time | Duration |
|---|---|---|
| Launch | 09:13:21 | |
| SSH answers | 09:13:40 | 19 s |
| Host packages and Docker | 09:14:15 | 35 s |
| Archives verified, manifest rendered, validation clean | 09:14:55 | 1 s |
| Image build (10 images) | 09:15:47 to 09:18:54 | 3 min 07 s |
| First `up` fails on missing `ai-net` | 09:19:01 | |
| `ai-net` created, stack starts | 09:19:37 to 09:19:57 | |
| Database reset, gateway started first | 09:21:04 to 09:21:28 | 24 s |
| Remaining services, API applies 23 migrations | 09:21:35 to 09:22:00 | 25 s |
| Ingestion restarted after the startup race | 09:22:08, container at 09:22:18 | |
| Teams provisioned | 09:22:40 | |
| Ingestion reaches 80 documents and 24,296 chunks | 10:46:47 | 84 min 29 s |
| Acceptance script | 10:49:02 to 10:54:42 | 5 min 40 s |
| Clean probe rerun and corpus-2 rerun | 10:52 to 10:55 | 3 min |

Launch to accepted: **1 hour 41 minutes**. Ingestion is 83 percent of it.

## Corpus

Ingestion ran one cycle from 09:22:18 and finished at 10:46. The worker's own summary:

```
{
  "seen": 80, "new": 80, "changed": 0, "skipped": 0, "failed": 0,
  "chunks_written": 24296, "chunks_removed": 0, "documents_removed": 0,
  "unreadable": 0, "failures": []
}
```

Counts through the deployment's Postgres:

| Query | Result | Baseline |
|---|---|---|
| `select count(distinct doc_id), count(*) from chunk_index` | `80 | 24296` | 80 | 24296, matches |
| `select count(*) from ingest_manifest where failure_count >= 2` | `0` | none dead-lettered |
| `select count(*) from chunk_index where kind = 'dataset'` | `0` | no file classified as a dataset |
| `select count(*) from ingest_manifest` | `80` | one manifest row per document |

The baseline matched exactly, so no investigation was needed. The monitor recorded 71 documents and 23,698 chunks
at 10:45:47 and 80 and 24,296 at 10:46:47, which is where the completion timestamp comes from. Timings from the
worker: parse 1,166,826 ms, chunk 162,292 ms, embed 3,679,708 ms, write 17,737 ms across 80 documents.

## Acceptance

**Gateway configuration.** `rendered/litellm.yaml` maps `analyst` to `bedrock/us.anthropic.claude-sonnet-4-6`
with `analyst-fallback` on `us.anthropic.claude-haiku-4-5-20251001-v1:0` and `analyst-fallback-2` on
`amazon.nova-pro-v1:0`, exactly as the tracked accuracy manifest declares.

**Smoke question.** `ask_client.py` asked "What is the mileage rate for reimbursement?" through the API and
returned:

```
resolved_model   bedrock/us.anthropic.claude-sonnet-4-6
configured_model bedrock/us.anthropic.claude-sonnet-4-6
model_group      analyst
fallback_used    False
```

**The audit chain agrees.** Across the whole acceptance window, 42 successful `model_call` rows, every one
`bedrock/us.anthropic.claude-sonnet-4-6` with `fallback_used=false`:

```
                resolved                | fallback | count
----------------------------------------+----------+-------
 bedrock/us.anthropic.claude-sonnet-4-6 | false    |    42
```

**The four probes.** Run exactly as `docs/runbook.md` section 6.7 specifies, after one forced degradation so the
DEGRADED right had a real row to read.

| Probe | Result | Rights |
|---|---|---|
| `interface_probe` | `PASSED: 8 right(s)` | ANSWER, NO-JUDGE, TRIGGERS, TOLD, FACTS, LANE-AGREE, DEGRADED, STREAM |
| `activity_probe` | `PASSED: 8 right(s)` | READ, SCOPED, ACTOR, FILTERED, BYREQUEST, BYJOB, CAPPED, RECORDED |
| `access_probe` | `PASSED: 4 right(s)` | ACCESS, SCOPED, REFUSED, RECORDED |
| `stream_probe` | `PASSED: 4 right(s)` | END-TO-END, TTFT, INCREMENTAL, REDACTED |

All four exited 0. 24 rights in total. The transcripts are `probe-interface.txt`, `probe-activity.txt`,
`probe-access.txt`, `probe-stream.txt`.

**The corpus-2 check.** Five results from four IDs, because `SYN_A01` is an access question and runs once per
role:

```
  PUB_N01 staff     PASS docs=['company/second-public/company/target-corp/10-K_2026-03-11_annual-report.htm', ...]
  PUB_P01 staff     PASS docs=['company/second-public/company/intuit-inc/EX-10_2026-01-12_credit-agreement.htm', ...]
  PUB_L01 staff     PASS docs=['company/second-public/company/caterpillar-inc/EX-10_2026-09-01_credit-agreement.htm', ...]
  SYN_A01 staff     PASS docs=['company/second-synthetic/org-chart.md', ...]
  SYN_A01 executive PASS docs=['executive/second-synthetic/executive-compensation-2025.md', ...]
```

Summary: 5 runs, 5 passed, 1.0 on every category. Duration 33.5 s. Full JSON in `corpus2-check.json`. The staff
run refused the CEO salary and the executive run found 385,000, which is the access boundary the question exists
to test.

## Model and fallback status

Sonnet 4.6 answered every acceptance call. No fallback was used in any row. Bedrock did not return a
`ThrottlingException` at any point. Acceptance is complete, not partial.

## What went wrong, and the smallest fix

Six failures, all recorded here because the runbook now carries them.

1. **`network ai-net declared as external, but could not be found`.** A fresh host has no `ai-net`. Fix:
   `docker network create ai-net`, then `up` again. Costs 36 seconds.
2. **The build failed on `path "/opt/oic-ui" not found`.** The `app` service builds from a second checkout that
   the code archive does not carry. Fix: `git archive` of `oic-ui` at `6d28c3c`, extracted to `/opt/oic-ui`.
   That archive is now a staged asset with a recorded checksum.
3. **LiteLLM restart-looped with `Database migration cannot proceed`.** The API applied the 23 audit migrations
   at startup before Prisma baselined the database, and Prisma refuses a schema it did not create. Fix: stop the
   stack, remove `data/postgres`, start `postgres litellm` first, wait for `/health/liveliness`, then start the
   rest. The compose comment predicted this exact failure.
4. **Ingestion failed its first two cycles.** `relation "chunk_index" does not exist` because it started before the
   API migrated, and `could not measure the embedding dimension` because bge-m3 was still loading. Fix:
   `docker restart ingestion` once, after the API logged `api ready` and embeddings reported healthy.
5. **The forced degradation did not take the first time.** `OI_MAX_TOKENS=100000` on the compose command line did
   not reach the container, because the api service does not list that variable in its `environment:` block, so
   the recreate succeeded and changed nothing. Fix: stop the gateway (`docker stop litellm`), ask one question,
   start it again. The API recorded `stage=degraded` with 12 passages, reason `unclassified`. An earlier
   cold-start attempt had already left a `timeout` degradation row, so `interface_probe` passed DEGRADED on the
   rerun.
6. **The suite printed 5 of 5 and then exited 1.** The eval image runs as an unprivileged user and could not
   write `--json-out` into the operator-owned evidence directory. Fix: mount a directory it can write
   (`chmod 777 /tmp/oi-evidence`), write there, copy out. The scoring was never affected.

One more, smaller: the first `stream_probe` run reported REDACTED as unproven immediately after an API
recreation. The same probe on a warm stack passed with `redactions {'phone': 1}`. The transcript kept is the
passing one; the earlier miss is recorded here rather than discarded.

## What this proves, and what it does not

- It proves the pinned archive installs and runs on a clean host with the documented assets.
- It proves corpus 2 ingests to the expected counts and that retrieval, access control, streaming, redaction and
  the audit chain work against that corpus.
- It proves Sonnet 4.6 answers through the deployment with no fallback across 42 recorded calls.
- It does not measure answer quality across runs. Five runs of one configuration is the comparison rule, and this
  is one run of five questions.
- The degradation used for the DEGRADED right was forced by removing the provider, not by a real Bedrock 429.
  Its reason is `unclassified`, which is what the taxonomy says for a failure it does not recognise.

## Evidence

Copied to `/home/anthony/oi-eval-runs/fresh-install-2026-10-22/` (44 files, 212 KB) before the instance was
stopped. The same files are under `/opt/oi-core/evidence/fresh-install/` on the instance's root volume.

| File | What it holds |
|---|---|
| `counts.txt` | the three count queries and the manifest row count |
| `ingestion-summary.txt` | the worker's closing JSON |
| `ingestion-progress.log` | a count every minute from 09:23 to 10:46 |
| `smoke-answer.json` | the full answer with model facts and citations |
| `audit-answer-window.txt` | 42 Sonnet calls, fallback false |
| `audit-smoke.txt` | the newest model_call rows |
| `degradation.txt` | the two degraded rows, with reason and threshold |
| `probe-*.txt` | the four probe transcripts and their exit codes |
| `corpus2-check.json` | the five scored answers with retrieval evidence |
| `compose-ps.txt`, `keys-provision-teams.txt` | the running stack and the four team aliases |
| `code-archive.sha256`, `ui-archive.sha256`, `corpus-archive.sha256` | asset verification |
