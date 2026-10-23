# Load test: 20 concurrent users

**Date:** 2026-10-23. **Status:** QLT-LOAD-001 verified. Five after-runs, zero failures.

**Audit-chain note:** an earlier concurrent run exposed a chain fork caused by id assignment racing the
advisory lock. The writer now takes the lock before the insert. See docs/reviews/audit-chain-fork/. The final
20-concurrent run exported 1547 events with an intact chain.

## Test

- Tool: scripts/load_test.py, run inside the API container against http://127.0.0.1:8088.
- Concurrency: 20.
- Requests: 20 per run.
- Question: the mileage-rate question.
- Target: p95 at or below 15 seconds, zero failed or degraded answers.

## Before

    {"concurrency":20,"requests":20,"failures":0,"p50_s":41.585,"p95_s":80.823,"max_s":85.571,"ok":false}

Two defects produced this:

1. The handler was async but ran retrieval, the multi-step model loop and the audit subprocess synchronously
   on the event loop. One request blocked every other request, so 20 callers were served one after another.
2. A real Bedrock 429 says "Too many requests, please wait before trying again." The classifier only knew the
   underscore form, so it treated the throttle as an unrecognised terminal failure and skipped the retry.

## Fix

- The blocking pipeline runs through starlette.concurrency.run_in_threadpool: prepare and retrieval, the tool
  loop and single-shot generation, the degraded path and finish.
- The classifier recognises the provider's sentence form, and the HTTP status is included in the text it
  classifies.

The pipeline contract and the answer path are unchanged.

## After, five runs

| Run | p50 | p95 | Max | Failures |
|---|---:|---:|---:|---:|
| 1 | 5.666 | 7.759 | 9.783 | 0 |
| 2 | 4.849 | 7.340 | 7.368 | 0 |
| 3 | 4.760 | 7.182 | 7.546 | 0 |
| 4 | 4.717 | 6.664 | 7.028 | 0 |
| 5 | 3.367 | 7.243 | 7.588 | 0 |

Worst p95 across the five runs: 7.759 seconds. That is 7.2 seconds inside the 15-second target. The worst maximum
is 9.783 seconds. Zero failures and zero degraded answers across 100 requests.

## Caveats

This is one question, 20 requests in flight, on one API process, with the production Bedrock model and the live
corpus. It does not measure sustained throughput, many different questions, streaming, or provider throttling at
higher concurrency. Those are future capacity tests, not part of this row.

## Files

- docs/reviews/ops-load/load.json, the worst after-run
- docs/reviews/ops-load/runs.json, all five after-runs
- docs/reviews/ops-load/before.json, the before result
- scripts/load_test.py
