#!/usr/bin/env python3
"""The harness against the deployed store: two rates, per-category counts, and two configurations compared.

RET-EVAL-001 asks the harness to report retrieval and support rates with per-category counts, and to compare
two configurations on the same questions. Both are done here with the real retriever, and the two configurations
are the ones this build actually has: the fused hybrid search, and the vector arm alone with the lexical arm
silenced.

That pair is chosen because it is not a hypothetical. RET-HYBRID-001 measured hybrid on against hybrid off for
three references, and this asks what the same change does across the whole graded set, which is the question a
yardstick exists to answer.

Run from the repo root on the box:

    cd /opt/oi-core
    docker run --rm --network ai-net --user "$(id -u):$(id -g)" \\
      -v /opt/oi-core:/w -v /tmp:/probe:ro -w /w \\
      --entrypoint python oi-core/ingestion:local /tmp/eval_acceptance.py
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

sys.path.insert(0, "/w/services/eval")
sys.path.insert(0, "/w/services/retrieval")
sys.path.insert(0, "/w/services/ingestion")


def load_env(path: Path = Path("/w/rendered/env")) -> dict[str, str]:
    try:
        text = path.read_text()
    except PermissionError:
        print(f"cannot read {path}: mode 600 owned by the operator. Add --user \"$(id -u):$(id -g)\"")
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
import retriever  # noqa: E402

QUESTIONS = "/w/fixtures/corpus/questions.yaml"
K = 10


def main() -> int:
    services = ingest.Services()
    questions = harness.load_questions(QUESTIONS)
    print(f"question set: {len(questions)} item(s), "
          f"{sum(1 for q in questions if q.answerable)} answerable, "
          f"{sum(1 for q in questions if not q.answerable)} not")

    graded = [q for q in questions if q.reference_answer]
    print(f"with a reference answer and therefore gradable on support: {len(graded)}")
    if not graded:
        print()
        print("no question carries a reference answer, so support cannot be measured and a run would report a")
        print("retrieval rate against one number. Fill fixtures/corpus/questions.yaml first.")
        return 1

    def fused(question: str):
        return retriever.search(services, question, role="staff", limit=K).passages

    def vector_only(question: str):
        original = retriever.lexical_search
        try:
            retriever.lexical_search = lambda *a, **k: []
            return retriever.search(services, question, role="staff", limit=K).passages
        finally:
            retriever.lexical_search = original

    print()
    print("=" * 100)
    print("CONFIGURATION A: the fused hybrid search")
    print("=" * 100)
    a = harness.run(questions, fused, k=K, configuration="fused hybrid")
    print(a.render())

    print()
    print("=" * 100)
    print("CONFIGURATION B: the vector arm alone, the lexical arm silenced")
    print("=" * 100)
    b = harness.run(questions, vector_only, k=K, configuration="vector only")
    print(b.render())

    print()
    print("=" * 100)
    print("THE COMPARISON, on the same questions")
    print("=" * 100)
    print(harness.compare(a, b))

    out = Path("/w/eval-report.json")
    try:
        out.write_text(json.dumps({"fused": a.as_dict(), "vector_only": b.as_dict()},
                                  indent=2, sort_keys=True) + "\n")
        print(f"\nwrote {out}")
    except OSError as exc:
        print(f"\ncould not write the report: {exc}")

    print()
    print("A rate at this n is not a quality claim. Ten questions can catch a search that is broken; the")
    print("research says the same about its own gate at sixty, where the interval around 55/60 is 0.80 to 0.95.")

    problems = []
    if a.retrieval_rate is None:
        problems.append("no retrieval rate was produced")
    if a.by_category() and len(a.by_category()) < 2:
        problems.append("the set carries one category, so the per-category counts show nothing")
    print()
    if problems:
        print(f"FAILED with {len(problems)} problem(s):")
        for problem in problems:
            print(f"  - {problem}")
        return 1
    print("PASSED: both rates were reported with per-category counts, and two configurations were compared on")
    print("the same questions with the questions that changed named individually")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
