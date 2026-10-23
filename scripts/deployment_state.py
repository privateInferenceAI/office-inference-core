#!/usr/bin/env python3
"""Report the deployment's manifest and image pins so drift is detectable.

The report is a JSON document with the manifest hash and one image reference per compose service. Run it on
the box, keep the expected copy in the repository, and compare. A pin changed on the box but not in the
repository shows as a different image reference.

Usage:
    python3 deployment_state.py --compose /opt/oi-core/deploy/office/compose/docker-compose.yml \
        --manifest /opt/oi-core/rendered/manifest.json --out state.json
    python3 deployment_state.py ... --compare expected.json
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

import yaml


def state(compose_path: Path, manifest_path: Path) -> dict:
    compose = yaml.safe_load(compose_path.read_text()) or {}
    images = {}
    for name, service in (compose.get("services") or {}).items():
        if isinstance(service, dict) and service.get("image"):
            images[name] = str(service["image"])
    digest = hashlib.sha256(manifest_path.read_bytes()).hexdigest()
    return {"manifest_sha256": digest, "images": dict(sorted(images.items()))}


def differences(expected: dict, actual: dict) -> list[str]:
    found = []
    if expected.get("manifest_sha256") != actual.get("manifest_sha256"):
        found.append("manifest_sha256 differs")
    expected_images = expected.get("images") or {}
    actual_images = actual.get("images") or {}
    for name in sorted(set(expected_images) | set(actual_images)):
        if expected_images.get(name) != actual_images.get(name):
            found.append(f"{name}: expected {expected_images.get(name)!r}, found {actual_images.get(name)!r}")
    return found


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="report deployment state")
    parser.add_argument("--compose", required=True, type=Path)
    parser.add_argument("--manifest", required=True, type=Path)
    parser.add_argument("--out", type=Path)
    parser.add_argument("--compare", type=Path)
    args = parser.parse_args(argv)

    actual = state(args.compose, args.manifest)
    if args.out:
        args.out.write_text(json.dumps(actual, indent=2, sort_keys=True) + "\n")
    if args.compare:
        expected = json.loads(args.compare.read_text())
        problems = differences(expected, actual)
        if problems:
            for problem in problems:
                print(problem)
            return 1
        print("no drift")
        return 0
    print(json.dumps(actual, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
