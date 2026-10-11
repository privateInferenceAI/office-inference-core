#!/usr/bin/env python3
"""Measure reranker latency on the machine that will serve it, at the depths we might use.

Why this exists: the C2 decision put the reranker behind a measurement rather than a judgement. The
published CPU numbers came from another project's hardware, and the numbers our own research produced
came from a contended laptop at load average 27. Neither tells us what this instance does.

The output that matters is milliseconds per candidate at each depth, measured warm and on an idle box.
Depth is then chosen against the latency budget rather than guessed: at a 5 second budget, a candidate
cost of 100 ms allows 50 candidates, and a cost of 400 ms allows 12.

Usage:
    python3 scripts/bench_rerank.py --url http://127.0.0.1:8080 --depth 25 50 100
    (from a container on ai-net: --url http://reranker:80)

Runs are reported individually as well as as a median, because on a shared machine the interesting
number is often the spread.
"""
from __future__ import annotations

import argparse
import json
import random
import statistics
import time
import urllib.request

WORDS = ("the of and shall be a to party section agreement provision is for with reimbursed approved "
         "notified within days percent staff document record term clause employee vendor invoice policy "
         "amendment schedule account contract claim budget procedure audit amount").split()
QUERY = "What is the mileage reimbursement rate for personal vehicles?"


def passage(rng: random.Random) -> str:
    """A passage shaped like the documents we retrieve: prose with identifiers and dates in it."""
    out = []
    for _ in range(rng.randint(56, 70)):
        r = rng.random()
        if r < 0.08:
            out.append(f"Section {rng.randint(1, 12)}.{rng.randint(1, 9)}")
        elif r < 0.14:
            out.append(str(rng.randint(1000, 99999)))
        elif r < 0.18:
            out.append(f"{rng.randint(1, 12)}/{rng.randint(1, 28)}/{rng.randint(2020, 2026)}")
        else:
            out.append(rng.choice(WORDS))
    return " ".join(out)


def rerank(url: str, texts: list[str], timeout: int) -> float:
    body = json.dumps({"query": QUERY, "texts": texts, "raw_scores": False}).encode()
    req = urllib.request.Request(f"{url.rstrip('/')}/rerank", data=body,
                                 headers={"Content-Type": "application/json"})
    started = time.perf_counter()
    with urllib.request.urlopen(req, timeout=timeout) as response:
        response.read()
    return time.perf_counter() - started


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--url", default="http://reranker:80", help="the reranker's base URL")
    parser.add_argument("--depth", type=int, nargs="+", default=[25, 50, 100],
                        help="candidate counts to measure")
    parser.add_argument("--reps", type=int, default=3, help="repetitions per depth, median reported")
    parser.add_argument("--timeout", type=int, default=600)
    parser.add_argument("--seed", type=int, default=11, help="fixed so runs are comparable")
    args = parser.parse_args(argv)

    rng = random.Random(args.seed)
    corpus = [passage(rng) for _ in range(max(args.depth))]
    print(f"reranker {args.url}, corpus of {len(corpus)} passages, {args.reps} repetitions per depth")
    print()
    print(f"{'depth':>6}  {'median':>10}  {'ms/candidate':>13}  runs")
    results = {}
    for depth in sorted(args.depth):
        try:
            rerank(args.url, corpus[:min(depth, 5)], args.timeout)  # warm the model
        except Exception as exc:  # noqa: BLE001 - the point is to report, not to raise
            print(f"{depth:>6}  unreachable: {exc}")
            return 1
        times = [rerank(args.url, corpus[:depth], args.timeout) for _ in range(args.reps)]
        median = statistics.median(times)
        per_candidate = median / depth * 1000
        results[depth] = {"median_ms": round(median * 1000, 1),
                          "per_candidate_ms": round(per_candidate, 2),
                          "runs_ms": [round(t * 1000, 1) for t in times]}
        print(f"{depth:>6}  {median * 1000:>8.0f}ms  {per_candidate:>11.1f}ms  " +
              ", ".join(f"{t * 1000:.0f}" for t in times))
    print()
    budget = 5.0
    affordable = [d for d in sorted(results) if results[d]["median_ms"] <= budget * 1000]
    print(json.dumps(results, indent=2))
    print()
    if affordable:
        print(f"Within a {budget:.0f} second budget: depths {affordable}. "
              f"Use the largest, and re-measure under load before committing.")
    else:
        print(f"Nothing measured fits a {budget:.0f} second budget. Options: a shallower pool, a "
              f"smaller model, Bedrock's reranker on the cloud build, or a GPU.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
