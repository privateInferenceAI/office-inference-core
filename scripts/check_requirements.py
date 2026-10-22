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
# **The register uses two statuses the checker did not know.** C6 added `retired` for a capability that was
# removed and is kept as history, and `experimental` for one that is built but not admitted to a deployment.
# A checker that refuses the statuses its own register uses is a check that fails on a correct file; both are
# named here so the register can describe the states the product actually has.
STATUSES = {"open", "verified", "waived", "retired", "experimental"}
TIERS = {"cloud", "office", "hybrid", "hosted"}


def main() -> int:
    path = Path(sys.argv[1] if len(sys.argv) > 1 else "docs/requirements.yaml")
    if not path.exists():
        print(f"FAIL: no register at {path}")
        return 1

    data = yaml.safe_load(path.read_text()) or {}
    items = data.get("items") or []
    problems: list[str] = []

    # **A duplicate key is a row that quietly lost half of itself.** `yaml.safe_load` keeps the last
    # occurrence and says nothing. Three C5 rows carried a `note` holding their verification evidence and a
    # second `note` that overwrote it, **PyYAML reported no problem, and this script printed "register is
    # well formed"** — a check passing on a record that had lost content, which is the failure this file
    # exists to prevent.
    #
    # The parsed mapping cannot show what was dropped, so the document is walked with a loader that refuses a
    # repeated key and names the line.
    class _Strict(yaml.SafeLoader):
        pass

    def _no_duplicates(loader, node, deep=False):
        seen = set()
        for key_node, _ in node.value:
            key = loader.construct_object(key_node, deep=deep)
            if key in seen:
                raise yaml.constructor.ConstructorError(
                    None, None,
                    f"duplicate key {key!r} in one mapping; the later value silently replaces the earlier "
                    f"one, which is how three rows lost their evidence notes",
                    key_node.start_mark)
            seen.add(key)
        return yaml.SafeLoader.construct_mapping(loader, node, deep=deep)

    _Strict.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, _no_duplicates)
    # **`YAMLError`, not `ConstructorError`.** PyYAML marks up a failing constructor into a `MarkedYAMLError`
    # on the way out, so catching the narrower class let the refusal escape as a traceback instead of a
    # finding — the check fired and the report crashed. A check whose failure mode is a crash tells a reader
    # nothing about the register.
    try:
        yaml.load(path.read_text(), Loader=_Strict)
    except yaml.YAMLError as exc:
        mark = getattr(exc, "problem_mark", None)
        where = f" (line {mark.line + 1})" if mark is not None else ""
        problems.append(f"{getattr(exc, 'problem', None) or exc}{where}")

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
