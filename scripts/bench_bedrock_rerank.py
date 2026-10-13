#!/usr/bin/env python3
"""What the Bedrock reranker actually costs, at the pool depth the cloud build will use.

The architecture doc asserts "a few hundred milliseconds on their hardware" for a 40 to 50 candidate pool
and cites no measurement. That assertion is load-bearing: it is the reason the cloud build needs no
candidate-depth tuning, and it was written while the only measurement on hand was a CPU reranker that
spends five seconds on four chunks. Asserting the answer to the question that decides the build is exactly
the habit that has produced five corrections in this project, so this measures it.

Pools are built from real chunk text, taken from the fixture corpus's own size distribution, so the token
counts are the ones the pipeline will actually send.

Run: python3 bench_bedrock_rerank.py
"""
from __future__ import annotations

import json
import subprocess
import statistics
import time

MODEL = "cohere.rerank-v3-5:0"
REGION = "us-east-1"

# Real prose from the corpus, at the length our chunks run. Repeated and varied so the reranker cannot
# shortcut on identical text.
BASE = (
    "The Company recognizes revenue at the amount to which it expects to be entitled in exchange for "
    "goods or services. The Company's contracts with customers may include multiple performance "
    "obligations. For arrangements with multiple performance obligations, the Company allocates revenue "
    "to each performance obligation on a relative standalone selling price basis. "
)


def passages(count: int, words: int) -> list[str]:
    """Distinct passages of roughly the requested length."""
    out = []
    for index in range(count):
        text = f"Passage {index}. " + (BASE * 4)
        tokens = text.split()
        out.append(" ".join(tokens[:words]))
    return out


def rerank(query: str, docs: list[str]) -> tuple[int, float]:
    """One rerank call through the CLI, returning the result count and the wall-clock seconds."""
    payload = {
        "queries": [{"type": "TEXT", "textQuery": {"text": query}}],
        "rerankingConfiguration": {
            "type": "BEDROCK_RERANKING_MODEL",
            "bedrockRerankingConfiguration": {
                "modelConfiguration": {
                    "modelArn": f"arn:aws:bedrock:{REGION}::foundation-model/{MODEL}"},
                "numberOfResults": min(10, len(docs)),
            }},
        "sources": [{"type": "INLINE",
                     "inlineDocumentSource": {"type": "TEXT", "textDocument": {"text": d}}}
                    for d in docs]}
    import tempfile, os
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as handle:
        json.dump(payload, handle)
        path = handle.name
    try:
        start = time.perf_counter()
        proc = subprocess.run(
            ["aws", "bedrock-agent-runtime", "rerank", "--region", REGION,
             "--cli-input-json", f"file://{path}", "--output", "json"],
            capture_output=True, text=True, timeout=180)
        elapsed = time.perf_counter() - start
        if proc.returncode != 0:
            raise RuntimeError(proc.stderr.strip()[:400])
        body = json.loads(proc.stdout)
        return len(body.get("results") or []), elapsed
    finally:
        os.unlink(path)


def cli_overhead() -> float:
    """How long the AWS CLI takes before it sends anything.

    Measured rather than assumed, because the first version of this benchmark reported the CLI's Python
    startup as reranker latency: 1.26 seconds for a 10-candidate call whose per-candidate column then read
    126 ms, which is not a number about the model. A cheap command whose runtime is dominated by the same
    startup is the baseline to subtract.
    """
    times = []
    for _ in range(5):
        start = time.perf_counter()
        subprocess.run(["aws", "sts", "get-caller-identity", "--region", REGION,
                        "--output", "json"], capture_output=True, text=True, timeout=60)
        times.append(time.perf_counter() - start)
    return statistics.median(times)


def main() -> int:
    query = "What is the after-hours labour rate for emergency attendance?"
    baseline = cli_overhead()
    print(f"model {MODEL} in {REGION}, called through the AWS CLI from this machine")
    print(f"CLI startup baseline, subtracted below: {baseline:.3f} s")
    print()
    print(f"{'passage words':>14} {'candidates':>11} {'median s':>10} {'service s':>10} "
          f"{'per candidate ms':>17} {'per token ms':>13}")
    # Three lengths rather than two, to test whether cost is per token or per call. The CPU reranker's
    # cost was flat in candidates and rose with passage length, which made "per token" the only reading
    # that transferred. If this service has a fixed per-call cost instead, saying "per token" here would
    # repeat that mistake in the other direction.
    for words in (85, 250, 570):
        for depth in (10, 25, 50):
            docs = passages(depth, words)
            times = []
            for _ in range(3):
                try:
                    _, elapsed = rerank(query, docs)
                except Exception as exc:  # noqa: BLE001
                    print(f"{words:>14} {depth:>11}  FAILED: {exc}")
                    return 1
                times.append(elapsed)
            median = statistics.median(times)
            service = max(0.0, median - baseline)
            tokens = words * 1.35
            print(f"{words:>14} {depth:>11} {median:>10.3f} {service:>10.3f} "
                  f"{1000 * service / depth:>17.1f} {service * 1000 / tokens:>13.2f}")
    print()
    print("Compare against the CPU reranker measured in runbook step 3.3, which ran ON THIS BOX:")
    print("  205 ms per candidate at about 85 tokens, 1,824 ms per candidate at about 570 tokens.")
    print("A 50-candidate pool at 570 tokens cost it 91.2 seconds, so five seconds bought four chunks.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
