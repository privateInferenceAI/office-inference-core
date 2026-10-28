#!/usr/bin/env python3
"""Fail when a manifest names a model that is deprecated or past its end of life.

The table in `data/model-lifecycle.json` is the source of truth, and it is refreshed deliberately
rather than queried here. That is what lets this run in CI with no credentials, and what makes it give
the same answer on every machine. Refreshing is a documented step with an owner, in
`docs/model-lifecycle.md`.

Two things fail the check: a model the table marks as anything other than ACTIVE or with an end-of-life
date in the past, and a model the table does not know at all. The second is deliberate. A new model
appearing in a manifest should be a decision someone made, not something that slipped past because
nobody had written its lifecycle down.

Usage:
    python3 scripts/check_model_lifecycle.py [manifest ...]
    (no arguments checks every file in manifests/)
"""
from __future__ import annotations

from manifest_fields import fallback_chain, split_fallback  # noqa: E402

import json
import re
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(Path(__file__).resolve().parent))
TABLE = ROOT / "data" / "model-lifecycle.json"

# Geographic inference-profile prefixes. A profile like us.anthropic.claude-sonnet-5 names the model
# anthropic.claude-sonnet-5 with a routing scope, so the lifecycle belongs to the model underneath.
PROFILE_PREFIXES = ("us.", "eu.", "apac.", "global.")


def model_id(frontier: str) -> str:
    identifier = frontier.split("/", 1)[1] if frontier.startswith("bedrock/") else frontier
    # **A litellm route can name the Bedrock Converse API explicitly.** `bedrock/converse/<model>` selects
    # the API, not a different model: the lifecycle belongs to the model underneath, so the route segment is
    # removed before the geographic profile prefix. Without this the canonical deployment's Kimi route is
    # reported as an unknown model even though its entry is in the table.
    if identifier.startswith("converse/"):
        identifier = identifier.split("/", 1)[1]
    for prefix in PROFILE_PREFIXES:
        if identifier.startswith(prefix):
            return identifier[len(prefix):]
    return identifier


def load_table(path: Path) -> dict:
    try:
        return json.loads(path.read_text())
    except FileNotFoundError:
        raise SystemExit(f"no lifecycle table at {path}. See docs/model-lifecycle.md") from None
    except json.JSONDecodeError as exc:
        raise SystemExit(f"{path} is not valid JSON: {exc}") from None


def check(manifest: dict, table: dict, today: date) -> list[str]:
    problems = []
    # **The fallback is checked exactly like the primary.** It is the model that answers when the primary
    # fails, so a fallback that is deprecated is a recovery path that stops working on a date — the worst
    # possible time to discover it. Both fields are read here so a manifest cannot carry one unaccounted
    # remote model past the table.
    for role, entry in sorted((manifest.get("models") or {}).items()):
        candidates = [(f"models.{role}", (entry or {}).get("frontier") or "")]
        for index, item in enumerate(fallback_chain(entry)):
            candidates.append((f"models.{role}.fallback[{index}]", split_fallback(item)[0]))
        for label, frontier in candidates:
            if not frontier:
                continue
            identifier = model_id(frontier)
            known = (table.get("models") or {}).get(identifier)
            if known is None:
                problems.append(
                    f"{label}: {identifier} is not in {TABLE.name}. Add it with its lifecycle "
                    "status, or the next deprecation will be discovered by a client")
                continue
            status = str(known.get("status", "")).upper()
            if status != "ACTIVE":
                problems.append(
                    f"{label}: {identifier} is {status}, and the manifest still names it. "
                    f"Replacement on file: {known.get('replacement', 'none recorded')}")
            eol = known.get("eol_date")
            if eol:
                try:
                    eol_date = date.fromisoformat(eol)
                except ValueError:
                    problems.append(f"{identifier}: eol_date {eol!r} is not an ISO date")
                    continue
                if eol_date < today:
                    problems.append(
                        f"{label}: {identifier} passed its end-of-life date on {eol}. "
                        f"Replacement on file: {known.get('replacement', 'none recorded')}")
    return problems


def main(argv: list[str]) -> int:
    paths = [Path(a) for a in argv[1:]] or sorted((ROOT / "manifests").glob("*.yaml"))
    if not paths:
        print("no manifests found", file=sys.stderr)
        return 2
    try:
        import yaml
    except ImportError:
        print("PyYAML is required: pip install pyyaml", file=sys.stderr)
        return 2

    table = load_table(TABLE)
    today = date.today()
    failed, checked = 0, 0
    for path in paths:
        manifest = yaml.safe_load(path.read_text()) or {}
        problems = check(manifest, table, today)
        # Both remote fields count, so the number printed beside the pass is the number checked: a
        # fallback that was not counted would make the line understate what the manifest sends to a provider.
        named = [model_id((entry or {}).get("frontier", ""))
                 for entry in (manifest.get("models") or {}).values() if (entry or {}).get("frontier")]
        named += [model_id(split_fallback(item)[0])
                  for entry in (manifest.get("models") or {}).values()
                  for item in fallback_chain(entry)]
        checked += len(named)
        if problems:
            failed += 1
            print(f"FAIL {path.name}")
            for problem in problems:
                print(f"  - {problem}")
        else:
            print(f"ok   {path.name}  ({len(named)} remote model(s), all ACTIVE)")

    print()
    print(f"{len(paths) - failed}/{len(paths)} manifests clean; {checked} remote model reference(s) "
          f"checked against a table verified {table.get('verified_on', 'at an unknown time')}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
