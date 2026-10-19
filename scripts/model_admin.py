#!/usr/bin/env python3
"""List, check and change the models a deployment names, without hand-editing the gateway.

**A client we do not service still has to be able to change a model.** Models are deprecated on the
provider's schedule, and the deployment has to outlive the choice made at installation. This script is
the supported procedure as a tool: it lists the models the lifecycle table knows and what a deployment
currently names, checks a manifest the way CI does, and rewrites one role's model and fallback chain
only after the whole candidate manifest has passed the validator and the lifecycle check.

It never calls AWS and it never restarts anything. Applying a change is render plus restart, which the
runbook's model-swap section spells out; the script prints the commands at the end.

Usage:
    python3 scripts/model_admin.py list
    python3 scripts/model_admin.py list --manifest manifests/dev-cloud-claude.yaml
    python3 scripts/model_admin.py check manifests/dev-cloud-claude.yaml
    python3 scripts/model_admin.py set manifests/dev-cloud-claude.yaml --role analyst \\
        --model bedrock/us.anthropic.claude-haiku-4-5-20251001-v1:0 \\
        --fallback bedrock/us.anthropic.claude-haiku-4-5-20251001-v1:0@us-east-2 \\
        --fallback bedrock/amazon.nova-pro-v1:0
    python3 scripts/model_admin.py set ... --dry-run

The models block is rewritten, and nothing else in the manifest is touched. Comments inside that block
are regenerated rather than preserved, so put notes outside it or in the deployment's evidence folder.
"""
from __future__ import annotations

import argparse
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(Path(__file__).resolve().parent))

import yaml  # noqa: E402

from check_model_lifecycle import TABLE, check as lifecycle_check, load_table, model_id  # noqa: E402
from manifest_fields import fallback_chain, is_region, split_fallback  # noqa: E402
from validate_manifests import check as manifest_check, load_catalog_packs  # noqa: E402

NEXT_STEPS = """Next, render the manifest and recreate the two processes that read it:

  python3 scripts/render_manifest.py --manifest {manifest} --out <deployment-root>/rendered
  cd <deployment-root> && docker compose -f deploy/office/compose/docker-compose.yml \\
      --env-file rendered/env up -d --force-recreate litellm api

**Restart both.** A gateway restart alone changes the model at Bedrock while the API keeps reporting
the old one from the manifest it loaded at startup; the record and the report then disagree."""


def load(path) -> dict:
    return yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}


def problems_for(manifest: dict, path) -> list[str]:
    """The same two gates CI runs: the manifest rules and the lifecycle table."""
    return (manifest_check(manifest, load_catalog_packs(), Path(path))
            + lifecycle_check(manifest, load_table(TABLE), date.today()))


def fallback_value(chain: list[str]) -> object:
    """The manifest spelling for a chain: a single id as a string, more as a list."""
    cleaned = [item for item in chain if item and item.strip()]
    if not cleaned:
        return ""
    return cleaned[0] if len(cleaned) == 1 else cleaned


def check_fallback_entry(value: str) -> list[str]:
    """Validate one entry before it reaches the manifest, so the error names the argument."""
    model, region = split_fallback(value)
    problems = []
    if not model.startswith("bedrock/"):
        problems.append(f"fallback {value!r} must name a Bedrock model as bedrock/<id>")
    if region and not is_region(region):
        problems.append(f"fallback {value!r} names a region that is not an AWS Region code")
    return problems


def models_span(text: str) -> tuple[str, str]:
    """Split the manifest text around its top-level models block."""
    lines = text.splitlines(keepends=True)
    start = None
    for index, line in enumerate(lines):
        if not line[:1].isspace() and line.startswith("models:"):
            start = index
            break
    if start is None:
        raise SystemExit("the manifest has no top-level models: block")
    end = len(lines)
    for index in range(start + 1, len(lines)):
        line = lines[index]
        if line.strip() and not line[:1].isspace() and not line.startswith("#") and ":" in line:
            end = index
            break
    return "".join(lines[:start]), "".join(lines[end:])


def write_models(path, manifest: dict) -> None:
    """Rewrite the models block, keeping the rest of the file byte for byte."""
    head, tail = models_span(Path(path).read_text(encoding="utf-8"))
    block = yaml.safe_dump({"models": manifest["models"]}, sort_keys=False, width=100,
                           default_flow_style=False)
    Path(path).write_text(head + block + ("\n" if tail else "") + tail, encoding="utf-8")


def command_list(args) -> int:
    table = load_table(TABLE)
    manifest = load(args.manifest) if args.manifest else {}
    named: dict[str, list[str]] = {}
    for role, entry in (manifest.get("models") or {}).items():
        for item in [(entry or {}).get("frontier") or ""] + fallback_chain(entry):
            if item:
                named.setdefault(model_id(item), []).append(role)
    print(f"{'model':52} {'provider':10} {'status':9} {'roles'}")
    for identifier, entry in sorted((table.get("models") or {}).items()):
        status = str((entry or {}).get("status", "?"))
        roles = ", ".join(sorted(set(named.get(identifier, []))))
        replacement = (entry or {}).get("replacement") or ""
        marker = "" if status == "ACTIVE" else f"  (replacement: {replacement})"
        print(f"{identifier:52} {(entry or {}).get('provider', '?'):10} {status:9} {roles}{marker}")
    return 0


def command_check(args) -> int:
    manifest = load(args.manifest)
    problems = problems_for(manifest, args.manifest)
    if problems:
        print(f"FAIL {args.manifest}")
        for problem in problems:
            print(f"  - {problem}")
        return 1
    print(f"ok   {args.manifest}  (validator and lifecycle table agree)")
    return 0


def command_set(args) -> int:
    path = Path(args.manifest)
    manifest = load(path)
    models = manifest.get("models") or {}
    if args.role not in models:
        print(f"FAIL {path}: no role {args.role!r}; the manifest declares {sorted(models)}")
        return 1
    problems = [problem for value in args.fallback for problem in check_fallback_entry(value)]
    if problems:
        for problem in problems:
            print(f"  - {problem}")
        return 1
    entry = dict(models.get(args.role) or {})
    before = (entry.get("frontier") or "", fallback_chain(entry))
    entry["frontier"] = args.model.strip()
    fallback = fallback_value([item.strip() for item in args.fallback])
    if fallback:
        entry["fallback"] = fallback
    else:
        entry.pop("fallback", None)
    models[args.role] = entry
    manifest["models"] = models
    problems = problems_for(manifest, path)
    if problems:
        print(f"FAIL {path}: the change would leave the manifest invalid, so it was not written")
        for problem in problems:
            print(f"  - {problem}")
        return 1
    if args.dry_run:
        print(f"dry run: would set models.{args.role} to frontier {entry['frontier']!r}, "
              f"fallback {fallback!r}")
        return 0
    write_models(path, manifest)
    print(f"wrote {path}")
    print(f"  models.{args.role}.frontier: {before[0]!r} -> {entry['frontier']!r}")
    print(f"  models.{args.role}.fallback: {before[1]!r} -> {fallback!r}")
    print()
    print(NEXT_STEPS.format(manifest=args.manifest))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="List, check and change the models a manifest names.")
    sub = parser.add_subparsers(dest="command", required=True)
    listing = sub.add_parser("list", help="the lifecycle table, and optionally what a deployment names")
    listing.add_argument("--manifest", default="")
    listing.set_defaults(func=command_list)
    checking = sub.add_parser("check", help="run the validator and the lifecycle check on a manifest")
    checking.add_argument("manifest")
    checking.set_defaults(func=command_check)
    setting = sub.add_parser("set", help="change one role's model and fallback chain")
    setting.add_argument("manifest")
    setting.add_argument("--role", required=True)
    setting.add_argument("--model", required=True)
    setting.add_argument("--fallback", action="append", default=[],
                         help="a fallback entry in order, repeatable; <model> or <model>@<region>")
    setting.add_argument("--dry-run", action="store_true")
    setting.set_defaults(func=command_set)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
