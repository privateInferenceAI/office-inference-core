#!/usr/bin/env python3
"""Check the requirements register.

The register exists because a coverage matrix once tracked whether an item had a home without
checking whether the home had contents. This script is what makes that impossible to repeat: an
item with no phase, no verification, an unknown status, or a waiver without a reason fails here.

Run: python3 scripts/check_requirements.py [path]
Exit 1 on any violation.
"""
import sys
from collections import Counter
from pathlib import Path

try:
    import yaml
except ImportError:
    print("PyYAML is required: pip install pyyaml", file=sys.stderr)
    raise SystemExit(2)

CLOUD_PHASES = {f"C{n}" for n in range(10)}
OTHER_PHASES = {"OFFICE", "DEPLOY", "BIZ", "NEVER"}
PHASES = CLOUD_PHASES | OTHER_PHASES
STATUSES = {"open", "verified", "waived"}
TIERS = {"cloud", "office", "hybrid", "hosted"}


def main() -> int:
    path = Path(sys.argv[1] if len(sys.argv) > 1 else "docs/requirements.yaml")
    if not path.exists():
        print(f"FAIL: no register at {path}")
        return 1

    data = yaml.safe_load(path.read_text()) or {}
    items = data.get("items") or []
    problems: list[str] = []
    seen: set[str] = set()

    for index, item in enumerate(items, start=1):
        where = item.get("id") or f"item {index}"
        if not item.get("id"):
            problems.append(f"{where}: no id")
        elif item["id"] in seen:
            problems.append(f"{where}: duplicate id")
        else:
            seen.add(item["id"])

        for field in ("title", "phase", "status"):
            if not item.get(field):
                problems.append(f"{where}: no {field}")

        if item.get("phase") and item["phase"] not in PHASES:
            problems.append(f"{where}: unknown phase {item['phase']!r}")

        if item.get("status") and item["status"] not in STATUSES:
            problems.append(f"{where}: unknown status {item['status']!r}")

        # A requirement with no test that would fail is a wish, not a requirement. The single
        # exception is something we have decided not to build, where there is nothing to verify.
        has_verify = bool((item.get("verify") or "").strip())
        if not has_verify and item.get("phase") != "NEVER":
            problems.append(f"{where}: no verification. Write the test that would fail, or waive it")

        if item.get("status") == "waived" and not (item.get("waiver_reason") or "").strip():
            problems.append(f"{where}: waived without a reason")

        if item.get("phase") == "NEVER" and item.get("status") != "waived":
            problems.append(f"{where}: phase NEVER must have status waived")

        for tier in item.get("tier") or []:
            if tier not in TIERS:
                problems.append(f"{where}: unknown tier {tier!r}")

    by_status = Counter(i.get("status") for i in items)
    by_phase = Counter(i.get("phase") for i in items)
    open_items = [i for i in items if i.get("status") == "open"]

    print(f"{len(items)} requirement(s) in {path}")
    for status in ("verified", "open", "waived"):
        if by_status[status]:
            print(f"  {status:<9} {by_status[status]}")
    print("  by phase: " + ", ".join(f"{p}={by_phase[p]}" for p in sorted(by_phase)))

    if open_items:
        blocking = [i["id"] for i in open_items if i.get("phase") in CLOUD_PHASES]
        print(f"\n  {len(blocking)} open item(s) belong to the cloud build:")
        for item in open_items:
            if item.get("phase") in CLOUD_PHASES:
                print(f"    {item['phase']:<4} {item['id']:<20} {item['title'][:70]}")

    if problems:
        print()
        for problem in problems:
            print(f"FAIL: {problem}")
        return 1

    print("\nregister is well formed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
