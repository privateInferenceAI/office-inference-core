#!/usr/bin/env python3
"""A small concurrency test for the assistant API.

Sends the same known question through /ask at a fixed concurrency and records p50, p95 and max latency plus the
error count. Run it from inside the API container because the API has no public port.
"""
from __future__ import annotations

import argparse
import json
import statistics
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed

QUESTION = "What is the mileage rate for reimbursement? Answer in one sentence."


def ask(url: str, token: str, timeout: int) -> tuple[float, bool, str]:
    started = time.monotonic()
    request = urllib.request.Request(
        url + "/ask",
        data=json.dumps({"session": token, "question": QUESTION}).encode(),
        headers={"Content-Type": "application/json"},
        method="POST")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            body = json.loads(response.read() or b"{}")
        ok = response.status == 200 and body.get("kind") is None and bool(body.get("answer"))
        return time.monotonic() - started, ok, str(body.get("kind") or "")
    except Exception as exc:  # noqa: BLE001
        return time.monotonic() - started, False, type(exc).__name__


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="assistant API concurrency test")
    parser.add_argument("--url", default="http://127.0.0.1:8088")
    parser.add_argument("--token", required=True)
    parser.add_argument("--concurrency", type=int, default=20)
    parser.add_argument("--requests", type=int, default=20)
    parser.add_argument("--timeout", type=int, default=180)
    args = parser.parse_args(argv)

    results = []
    with ThreadPoolExecutor(max_workers=args.concurrency) as pool:
        futures = [pool.submit(ask, args.url, args.token, args.timeout) for _ in range(args.requests)]
        for future in as_completed(futures):
            results.append(future.result())

    latencies = sorted(result[0] for result in results)
    failures = sum(1 for result in results if not result[1])
    def percentile(p: float) -> float:
        index = min(len(latencies) - 1, int(round((p / 100) * (len(latencies) - 1))))
        return latencies[index]
    report = {
        "concurrency": args.concurrency,
        "requests": args.requests,
        "failures": failures,
        "p50_s": round(statistics.median(latencies), 3),
        "p95_s": round(percentile(95), 3),
        "max_s": round(max(latencies), 3),
        "ok": failures == 0 and percentile(95) <= 15.0,
    }
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
