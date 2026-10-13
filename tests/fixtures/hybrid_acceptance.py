#!/usr/bin/env python3
"""The acceptance test for hybrid search, on the deployed store: hybrid on against hybrid off.

RET-HYBRID-001 asks for a question containing an exact reference that succeeds with the lexical arm and fails
without it. That is a claim about the deployed store rather than about the code, because the lexical arm
returned nothing at all for all seven identifiers before it existed, and only the store can show that has
changed.

Hybrid off is simulated by pointing the lexical arm at a function that returns nothing, which is what the
module does when it cannot reach Postgres. Using the real code path rather than a flag means the comparison
exercises the same fusion, the same filters and the same passage construction that a deployment does.

Run from the repo root on the box:

    docker run --rm --network ai-net -v /opt/oi-core:/w:ro -v /tmp:/probe:ro -w /w \\
      --entrypoint python ghcr.io/docling-project/docling-serve-cpu:v1.36.0 /probe/hybrid_acceptance.py
"""
from __future__ import annotations

import json
import sys
import urllib.request

sys.path.insert(0, "/w/services/retrieval")
sys.path.insert(0, "/w/services/ingestion")

import ingest  # noqa: E402
import retriever  # noqa: E402

# The two the vector arm missed entirely, and two it found, so the test shows the arm helping where it should
# and not disturbing what already worked.
CASES = [
    ("Pub. 515", "What does Pub. 515 cover?", True),
    ("Form W-3SS", "When must Form W-3SS be filed?", True),
    ("Pub. 15", "What does Pub. 15 say about withholding?", False),
    ("Form W-4", "How long must I keep a Form W-4?", False),
]


def count_lexical_rows(services: ingest.Services) -> int:
    import psycopg2
    with psycopg2.connect(services.database_url) as connection:
        with connection.cursor() as cur:
            cur.execute("select count(*), count(*) filter (where identifiers <> '{}') from chunk_index")
            total, with_ids = cur.fetchone()
    print(f"chunk_index: {total} rows, {with_ids} carrying identifiers")
    return total


def main() -> int:
    services = ingest.Services()
    problems: list[str] = []
    if not services.database_url:
        print("DATABASE_URL is not set, so the lexical arm cannot run. Nothing below would mean anything.")
        return 1
    if count_lexical_rows(services) == 0:
        print("chunk_index is empty. Re-ingest before running this, or every case below fails for that reason.")
        return 1

    print()
    print(f"{'reference':<13}{'hybrid on':>11}{'hybrid off':>12}  verdict")
    for token, question, must_need_lexical in CASES:
        on = retriever.search(services, question, role="staff", limit=10)
        on_hit = any(token.lower() in p.text.lower() for p in on.passages)

        original = retriever.lexical_search
        try:
            retriever.lexical_search = lambda *a, **k: []
            off = retriever.search(services, question, role="staff", limit=10)
        finally:
            retriever.lexical_search = original
        off_hit = any(token.lower() in p.text.lower() for p in off.passages)

        if must_need_lexical:
            verdict = "HYBRID WINS" if (on_hit and not off_hit) else (
                "no difference" if on_hit == off_hit else "unexpected")
            if not (on_hit and not off_hit):
                problems.append(f"{token}: expected hybrid on to find it and hybrid off to miss it, got "
                                f"on={on_hit} off={off_hit}")
        else:
            verdict = "both find it, as expected" if on_hit else "REGRESSION: on misses what off found"
            if not on_hit:
                problems.append(f"{token}: hybrid on should still find what the vector arm found")
        print(f"{token:<13}{str(on_hit):>11}{str(off_hit):>12}  {verdict}")
        print(f"    arms on: {on.arms}")

    print()
    if problems:
        print(f"FAILED with {len(problems)} problem(s):")
        for problem in problems:
            print(f"  - {problem}")
        return 1
    print("PASSED: the lexical arm finds references the vector arm cannot, and the fusion keeps what it found")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
