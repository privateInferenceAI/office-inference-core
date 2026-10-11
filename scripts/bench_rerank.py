#!/usr/bin/env python3
"""Measure reranker latency on the machine that will serve it, at the depths we might use.

Why this exists: the C2 decision put the reranker behind a measurement rather than a judgement. The
published CPU numbers came from another project's hardware, and the numbers our own research produced
came from a contended laptop at load average 27. Neither tells us what this instance does.

The output that matters is milliseconds per candidate at each depth, measured warm and on an idle box.
Depth is then chosen against the latency budget rather than guessed: at a 5 second budget, a candidate
cost of 100 ms allows 50 candidates, and a cost of 400 ms allows 12.

One request per depth does not work, and the service says so rather than degrading. Measured on the
reference instance 2026-10-11 against TEI cpu-1.9 serving bge-reranker-v2-m3: a call carrying more
than 32 texts is refused with HTTP 422 before any work happens, and a call needing more infer permits
than the service has is refused with HTTP 429 immediately rather than queued. Permits are consumed per
candidate, so the first depth this script measured failed at ten candidates against a service
configured with `--max-concurrent-requests=8`. It therefore sends candidates in chunks of `--chunk`,
defaulting to the 8 that lets one call coexist with another, and retries a 429 with a short backoff.
The per-candidate figure is the whole depth's wall time divided by its candidates, so it carries the
per-call overhead with it: at 100 candidates in chunks of 8 that overhead is paid thirteen times.

Timing note: these passages are shorter than what we retrieve, so read the number as a floor rather
than as the cost of a real query. A chunk in this system is 384 to 512 tokens, and no measurement of
the reranker at that size exists yet. The difference is large enough to move the chosen depth, so
`--words 420` belongs in the answer before the depth is committed.

Usage:
    python3 scripts/bench_rerank.py --depth 25 50 100
    python3 scripts/bench_rerank.py --words 420          # passages the size we really retrieve

Run it from a container joined to the network the reranker is on, because the service publishes no
port. From the host:

    docker run --rm --network ai-net -v /opt/oi-core/scripts:/app/scripts:ro \\
      python:3.12-slim python /app/scripts/bench_rerank.py --depth 25 50 100

`reranker` resolves only inside that network. Naming the host in `--url` will not work.

Runs are reported individually as well as as a median, because on a shared machine the interesting
number is often the spread.
"""
from __future__ import annotations

import argparse
import json
import random
import statistics
import time
import urllib.error
import urllib.request

WORDS = ("the of and shall be a to party section agreement provision is for with reimbursed approved "
         "notified within days percent staff document record term clause employee vendor invoice policy "
         "amendment schedule account contract claim budget procedure audit amount").split()
QUERY = "What is the mileage reimbursement rate for personal vehicles?"

# The service's `max_client_batch_size`, read from its /info. A call above it is refused with 422.
MAX_TEXTS_PER_CALL = 32
# A 429 means no permit was free. The service does not queue, so the wait is ours to take.
RETRIES = 4
RETRY_DELAY_SECONDS = 0.5


def passage(rng: random.Random, words: int | None = None) -> str:
    """A passage shaped like the documents we retrieve: prose with identifiers and dates in it.

    `words` sets the length. The default is the short synthetic passage this benchmark shipped with,
    kept so a run stays comparable with the numbers already recorded; pass the real chunk size to
    measure the shape a query actually reranks.
    """
    out = []
    for _ in range(words if words is not None else rng.randint(56, 70)):
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


def post(url: str, texts: list[str], timeout: int) -> float:
    """One request. Returns its wall time, waiting excluded so a retry does not flatter the number."""
    body = json.dumps({"query": QUERY, "texts": texts, "raw_scores": False}).encode()
    req = urllib.request.Request(f"{url.rstrip('/')}/rerank", data=body,
                                 headers={"Content-Type": "application/json"})
    delay = RETRY_DELAY_SECONDS
    for attempt in range(RETRIES + 1):
        started = time.perf_counter()
        try:
            with urllib.request.urlopen(req, timeout=timeout) as response:
                response.read()
            return time.perf_counter() - started
        except urllib.error.HTTPError as exc:
            if exc.code != 429 or attempt == RETRIES:
                raise
            time.sleep(delay)
            delay *= 2
    raise AssertionError("unreachable")


def rerank(url: str, texts: list[str], timeout: int, chunk: int) -> float:
    """Rerank every text, in calls the service will accept, and return the total wall time."""
    return sum(post(url, texts[i:i + chunk], timeout) for i in range(0, len(texts), chunk))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--url", default="http://reranker:80", help="the reranker's base URL")
    parser.add_argument("--depth", type=int, nargs="+", default=[25, 50, 100],
                        help="candidate counts to measure")
    parser.add_argument("--reps", type=int, default=3, help="repetitions per depth, median reported")
    parser.add_argument("--chunk", type=int, default=8,
                        help=f"texts per call, at most {MAX_TEXTS_PER_CALL}")
    parser.add_argument("--words", type=int, default=None,
                        help="words per passage; the default is the original 56 to 70, and 384 to "
                             "512 is the size of a real chunk")
    parser.add_argument("--timeout", type=int, default=600)
    parser.add_argument("--seed", type=int, default=11, help="fixed so runs are comparable")
    args = parser.parse_args(argv)

    if not 1 <= args.chunk <= MAX_TEXTS_PER_CALL:
        parser.error(f"--chunk must be between 1 and {MAX_TEXTS_PER_CALL}, the service's limit")

    rng = random.Random(args.seed)
    corpus = [passage(rng, args.words) for _ in range(max(args.depth))]
    shape = f"{args.words} words each" if args.words else "56 to 70 words each (the original shape)"
    print(f"reranker {args.url}, corpus of {len(corpus)} passages ({shape}), "
          f"{args.reps} repetitions per depth, {args.chunk} per call")
    print()
    print(f"{'depth':>6}  {'median':>10}  {'ms/candidate':>13}  {'calls':>5}  runs")
    results = {}
    for depth in sorted(args.depth):
        try:
            rerank(args.url, corpus[:min(depth, args.chunk)], args.timeout, args.chunk)  # warm up
        except Exception as exc:  # noqa: BLE001 - the point is to report, not to raise
            print(f"{depth:>6}  unreachable: {exc}")
            return 1
        times = [rerank(args.url, corpus[:depth], args.timeout, args.chunk) for _ in range(args.reps)]
        median = statistics.median(times)
        per_candidate = median / depth * 1000
        calls = -(-depth // args.chunk)
        results[depth] = {"median_ms": round(median * 1000, 1),
                          "per_candidate_ms": round(per_candidate, 2),
                          "calls": calls,
                          "runs_ms": [round(t * 1000, 1) for t in times]}
        print(f"{depth:>6}  {median * 1000:>8.0f}ms  {per_candidate:>11.1f}ms  {calls:>5}  " +
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
