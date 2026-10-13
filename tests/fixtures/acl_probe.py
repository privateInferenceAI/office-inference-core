#!/usr/bin/env python3
"""Does the access filter work against the real vector store, or only against a stub?

The unit tests assert that the filter is a clause in the query. That is the requirement, and it is not the
same as the store honouring it: a clause with a misspelled key, or a payload whose label was written under a
different name, produces a query that looks right and returns everything. So this asks the deployed store,
with the real index, and checks both halves — that a restricted role receives nothing, and that a permitted
role receives what it should.

It also greps the returned text for the fixture's restricted figures. A count can be right while a figure
still reaches a caller through some field the count does not cover, and a grep is the check that does not
care which field it was.

Run from the repo root on the box:

    docker run --rm --network ai-net -v /opt/oi-core:/w:ro -w /w \\
      --entrypoint python ghcr.io/docling-project/docling-serve-cpu:v1.36.0 \\
      /w/tests/fixtures/acl_probe.py
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, "/w/services/retrieval")
sys.path.insert(0, "/w/services/ingestion")

import ingest  # noqa: E402
import retriever  # noqa: E402

# Figures that exist only in the restricted fixture, so a leak is found by searching for one.
RESTRICTED_FIGURES = ("418,750", "62.5 percent")

QUESTIONS = [
    "What is the chief executive officer's base salary?",
    "What is the executive bonus target?",
    "What is the severance on a change of control?",
]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--denial", choices=[m.value for m in retriever.DenialMode], default=None,
                        help="override the configured denial mode, so both behaviours can be seen on the "
                             "box rather than taken from a unit test")
    args = parser.parse_args()

    services = ingest.Services()
    problems: list[str] = []

    print("=== what is in the index, by label ===")
    for root in ("company", "executive"):
        body = {"filter": {"must": [{"key": "acl", "match": {"value": root}}]}, "exact": True}
        import urllib.request
        request = urllib.request.Request(
            f"{services.vector_url}/collections/{services.collection}/points/count",
            data=json.dumps(body).encode(), method="POST",
            headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(request, timeout=60) as response:
            count = json.loads(response.read())["result"]["count"]
        print(f"  {root:<12} {count} points")
        if root == "executive" and count == 0:
            problems.append(
                "no executive points are indexed, so every test below would pass against a retriever with "
                "no filter at all. Install the fixture and re-ingest.")

    print(f"denial mode: {args.denial or retriever.denial_mode().value} "
          f"(configured {retriever.denial_mode().value})")
    print()
    for role in ("staff", "executive"):
        print(f"=== role: {role} ===")
        for question in QUESTIONS:
            result = retriever.search(services, question, role=role, limit=5, denial=args.denial)
            texts = [p.text for p in result.passages]
            leaked = [figure for figure in RESTRICTED_FIGURES
                      if any(figure in text for text in texts)]
            print(f"  {question[:50]:<52} passages {len(result.passages):>2}  "
                  f"denied {str(result.denied):<5} withheld {result.withheld:>2}  "
                  f"restricted_best {result.restricted_best_score}")
            for passage in result.passages[:2]:
                print(f"      {passage.doc_id} p{passage.page_start} {passage.text[:54]!r}")
            if result.disclosure is not None:
                print(f"      DISCLOSURE[{result.disclosure.mode}]: {result.disclosure.message}")
            elif result.denied:
                print(f"      DISCLOSURE: none, which is the silent policy working")
            # Only the role that may NOT see the figures is checked for receiving them. The first version of
            # this probe checked every role, so the executive role was reported as leaking what it is
            # permitted to read, and the run ended in FAILED with the permissions actually correct.
            if role == "staff" and leaked:
                problems.append(f"staff received restricted figures {leaked} for {question!r}")
            if role == "staff" and any(p.acl != "company" for p in result.passages):
                problems.append(f"a staff query returned a passage outside company for {question!r}")
            if role == "executive" and not result.passages:
                problems.append(f"an executive query returned nothing for {question!r}")
        print()

    print("=== the direct test: does a staff query reach the restricted document at all? ===")
    for figure in RESTRICTED_FIGURES:
        result = retriever.search(services, figure, role="staff", limit=10)
        found = any(figure in p.text for p in result.passages)
        print(f"  staff search for {figure!r}: {len(result.passages)} passages, "
              f"figure present {found}")
        if found:
            problems.append(f"a staff search for {figure!r} returned the restricted figure")

    print()
    if problems:
        print(f"FAILED with {len(problems)} problem(s):")
        for problem in problems:
            print(f"  - {problem}")
        return 1
    print("PASSED: a staff role reaches no restricted passage by any of these questions, and an executive "
          "role reaches them")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
