# Load test: 20 concurrent users

**Date:** 2026-10-23. **Status:** QLT-LOAD-001 stays open. The target was not met.

## Test

- Tool: scripts/load_test.py, run inside the API container against http://127.0.0.1:8088.
- Concurrency: 20.
- Requests: 20.
- Question: the mileage-rate question.
- Ingestion: the scheduled worker was running.
- Target: p95 at or below 15 seconds, zero failures.

## Result

    {"concurrency":20,"requests":20,"failures":0,"p50_s":41.585,"p95_s":80.823,"max_s":85.571,"ok":false}

Zero failures. The p95 is 80.8 seconds, 5.4 times the target. The maximum is 85.6 seconds and the median is
41.6 seconds.

## Reading

The run proves the API returns correct answers under 20 concurrent requests, which is a useful capacity result.
It does not prove the 15-second latency promise.

Likely contributors, in order:

1. Every request makes a model call plus a retrieval pass; at 20 concurrent requests they queue behind the same
   API, retrieval and model budget.
2. The tool loop can add a second model turn, which roughly doubles the slow tail.
3. There is no response cache and no admission queue, so the slowest requests dominate p95.
4. The API is one process on a CPU box; the concurrency limit is a deployment setting, not a measured one.

## What to do before closing the row

- Decide whether 20 concurrent users means 20 in flight or 20 active sessions with a few requests per minute.
  The current test models 20 in flight.
- Measure at 1, 5 and 10 in flight to find the knee.
- Add an admission queue or a timeout that returns a degraded answer instead of letting p95 grow.
- Consider a smaller model for the load test and compare, because model latency is the dominant term.
- Re-run after the change and record the new p95.

## Files

- docs/reviews/ops-load/load.json
- scripts/load_test.py
