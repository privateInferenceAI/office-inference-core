#!/usr/bin/env python3
"""Reranking measured: does it improve retrieval and support, and what does it cost.

RET-RERANK-001's verify line asks for both halves, and this is the run that answers them on the deployed store
with the real harness rather than with an opinion.

The comparison is the one the requirement names: the same ten questions, reranked and not. Latency is reported
beside the rates, because a quality gain that costs more than the budget is a trade and a trade nobody can see
is not one anybody agreed to.

Run from the repo root on the box, from a container that has AWS credentials:

    cd /opt/oi-core
    docker run --rm --network ai-net --user "$(id -u):$(id -g)" \\
      -v /opt/oi-core:/w -v /tmp:/probe:ro -w /w \\
      -e AWS_ACCESS_KEY_ID -e AWS_SECRET_ACCESS_KEY -e AWS_REGION=us-east-1 \\
      --entrypoint python oi-core/ingestion:local /tmp/rerank_acceptance.py
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

sys.path.insert(0, "/w/services/retrieval")
sys.path.insert(0, "/w/services/ingestion")
sys.path.insert(0, "/w/services/eval")


def load_env(path: Path = Path("/w/rendered/env")) -> dict[str, str]:
    try:
        text = path.read_text()
    except PermissionError:
        print(f"cannot read {path}: mode 600 owned by the operator. Run as the file's owner.")
        return {}
    out: dict[str, str] = {}
    for line in text.splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, _, value = line.partition("=")
            out[key.strip()] = value.strip().strip('"').strip("'")
    return out


env = load_env()
if env and not os.environ.get("DATABASE_URL"):
    from urllib.parse import quote
    os.environ["DATABASE_URL"] = (
        f"postgresql://{quote(env['POSTGRES_USER'], safe='')}:"
        f"{quote(env['POSTGRES_PASSWORD'], safe='')}@postgres:5432/{env['POSTGRES_DB']}")

import harness  # noqa: E402
import ingest  # noqa: E402
import rerank  # noqa: E402
import retriever  # noqa: E402

K = 10


def main() -> int:
    services = ingest.Services()
    questions = harness.load_questions("/w/fixtures/corpus/questions.yaml")
    manifest = harness.load_document_paths("/w/fixtures/corpus/manifest.yaml")
    problems: list[str] = []

    print()
    print(f"reranker: {rerank.MODEL_ID} in {rerank.REGION}, depth {rerank.RERANK_DEPTH}, "
          f"budget {rerank.RERANK_BUDGET_MS} ms")

    if not rerank.available():
        print("  no reranker is configured on this deployment, so there is nothing to measure")
        return 1

    def without(question: str):
        return retriever.search(services, question, role="staff", limit=K, rerank_passages=False).passages

    def with_rerank(question: str):
        result = retriever.search(services, question, role="staff", limit=K, rerank_passages=True)
        # The per-question record is the only place the latency is visible, so it is collected here rather than
        # measured around the whole run: a total would average a fast question against a slow one and report a
        # number that describes neither.
        latencies.append(result.rerank.get("rerank_ms") or 0.0)
        ran.append(bool(result.rerank.get("reranked")))
        reasons.append(result.rerank.get("rerank_reason"))
        return result.passages

    latencies: list[float] = []
    ran: list[bool] = []
    reasons: list[str | None] = []

    print()
    print("=" * 96)
    print("WITHOUT reranking: the fused order")
    print("=" * 96)
    before = harness.run(questions, without, k=K, configuration="fused, no rerank",
                         document_paths=manifest)
    print(before.render())

    print()
    print("=" * 96)
    print("WITH reranking")
    print("=" * 96)
    after = harness.run(questions, with_rerank, k=K, configuration="fused, reranked",
                        document_paths=manifest)
    print(after.render())

    print()
    print("=" * 96)
    print("THE COMPARISON, on the same questions")
    print("=" * 96)
    print(harness.compare(before, after))

    print()
    print("=" * 96)
    print("THE BUDGET")
    print("=" * 96)
    if not any(ran):
        problems.append("the reranker never ran, so nothing about it was measured. "
                        f"{next((r for r in reasons if r), 'no reason given')}")
        print(f"  the reranker did not run on any question: {next((r for r in reasons if r), '')}")
    else:
        calls = [value for value in latencies if value]
        slowest, average = max(calls), sum(calls) / len(calls)
        print(f"  questions reranked: {sum(ran)} of {len(ran)}")
        print(f"  per question: slowest {slowest:.0f} ms, average {average:.0f} ms")
        print(f"  budget {rerank.RERANK_BUDGET_MS} ms: "
              f"{'WITHIN' if slowest <= rerank.RERANK_BUDGET_MS else 'EXCEEDED'}")
        if slowest > rerank.RERANK_BUDGET_MS:
            problems.append(f"the slowest question took {slowest:.0f} ms against a budget of "
                            f"{rerank.RERANK_BUDGET_MS} ms")

    print()
    print("=" * 96)
    print("WHAT IT CHANGED, question by question")
    print("=" * 96)
    moved = 0
    for before_outcome in before.outcomes:
        after_outcome = next(o for o in after.outcomes if o.question_id == before_outcome.question_id)
        same = (before_outcome.supported == after_outcome.supported
                and before_outcome.rank == after_outcome.rank)
        if not same:
            moved += 1
            print(f"  {before_outcome.question_id}: rank {before_outcome.rank} -> {after_outcome.rank}, "
                  f"supported {before_outcome.supported} -> {after_outcome.supported}")
    if not moved:
        print("  nothing moved on this question set")

    print()
    print("A rate at ten questions is not a quality claim, and a reranker's benefit is exactly the kind of small")
    print("effect ten questions cannot resolve. What this run can show is the direction and the cost.")

    print()
    if problems:
        print(f"FAILED with {len(problems)} problem(s):")
        for problem in problems:
            print(f"  - {problem}")
        return 1
    print("PASSED: reranking ran on the fused head, was measured against the same questions without it, and its")
    print("latency is reported against the budget")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
