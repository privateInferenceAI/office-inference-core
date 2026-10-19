#!/usr/bin/env python3
"""Compare the five-run control and row-query suites, from the frozen result files in this directory.

**Why this exists.** The claim this PR makes is a five-run comparison, and a mean in a document is not
checkable. This reads the ten committed result files and prints the totals, the range, the per-category table and
the per-question pass counts, so a reader can recompute every number in the report without a deployment.

    python3 docs/reviews/query-tool-2026-10-19/compare.py
"""
from __future__ import annotations

import collections
import json
import pathlib
import sys

HERE = pathlib.Path(__file__).resolve().parent
CONFIGS = (("control", "query off"), ("rowquery", "query on"))


def runs(config: str) -> list[dict]:
    return [json.loads(path.read_text()) for path in sorted((HERE / "raw" / config).glob("run-*.json"))]


def total(run: dict) -> int:
    return sum(1 for record in run["results"] if record["score"]["pass"])


def main() -> int:
    data = {config: runs(config) for config, _ in CONFIGS}
    for config, label in CONFIGS:
        if len(data[config]) != 5:
            print(f"FAIL: {label} has {len(data[config])} runs, and five are the minimum for a claim")
            return 1
    print("== totals ==")
    for config, label in CONFIGS:
        totals = [total(run) for run in data[config]]
        degraded = [sum(1 for record in run["results"]
                        if (record.get("answer") or {}).get("kind") == "degraded")
                    for run in data[config]]
        mean = sum(totals) / len(totals)
        print(f"{label:<10} {totals}  mean {mean:.1f}  range {min(totals)}-{max(totals)}  "
              f"degraded {degraded}")
    print()
    print("== by category (mean passes in five runs) ==")
    categories = sorted({record["category"] for run in data["control"] for record in run["results"]})
    header = f"{'category':<20}" + "".join(f"{label:>12}" for _, label in CONFIGS)
    print(header)
    for category in categories:
        cells = []
        for config, _ in CONFIGS:
            passed = sum(1 for run in data[config] for record in run["results"]
                         if record["category"] == category and record["score"]["pass"])
            shown = sum(1 for run in data[config] for record in run["results"]
                        if record["category"] == category)
            cells.append(f"{passed / 5:.1f}/{shown // 5}")
        print(f"{category:<20}" + "".join(f"{cell:>12}" for cell in cells))
    print()
    print("== per question (passes of five runs, staff role only) ==")
    ids = []
    seen = set()
    for run in data["control"]:
        for record in run["results"]:
            if record["id"] not in seen:
                seen.add(record["id"])
                ids.append(record["id"])
    for question_id in ids:
        cells = []
        for config, _ in CONFIGS:
            passed = sum(1 for run in data[config] for record in run["results"]
                         if record["id"] == question_id and record["role"] == "staff"
                         and record["score"]["pass"])
            cells.append(str(passed))
        marker = "  <-- differs" if cells[0] != cells[1] else ""
        print(f"{question_id:<5} off {cells[0]}  on {cells[1]}{marker}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
