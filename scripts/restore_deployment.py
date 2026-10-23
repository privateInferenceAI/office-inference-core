#!/usr/bin/env python3
"""Restore a backup produced by backup_deployment.py into a target deployment root.

The target must be a scratch deployment, not the live /opt/oi-core. The script downloads the latest backup,
extracts the rendered/manifests/documents state, and restores the Postgres dump into the target's postgres
container. Start the target stack after this, then run acceptance.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tarfile
from pathlib import Path

import boto3


def latest_prefix(bucket: str, prefix: str) -> str:
    client = boto3.client("s3", region_name="us-east-1")
    keys = [obj["Key"] for obj in client.list_objects_v2(Bucket=bucket, Prefix=prefix + "/").get("Contents", [])]
    if not keys:
        raise SystemExit("no backups found")
    newest = sorted({key.rsplit("/", 1)[0] for key in keys})[-1]
    return newest + "/"


def restore(bucket: str, prefix: str, root: Path, postgres_container: str) -> dict:
    client = boto3.client("s3", region_name="us-east-1")
    key = latest_prefix(bucket, prefix)
    work = root / ".restore"
    work.mkdir(parents=True, exist_ok=True)

    for name in ("backup.json", "postgres.dump", "state.tar.gz"):
        client.download_file(bucket, key + name, str(work / name))

    manifest = json.loads((work / "backup.json").read_text())
    with tarfile.open(work / "state.tar.gz") as archive:
        archive.extractall(root)

    with (work / "postgres.dump").open("rb") as handle:
        subprocess.run(["docker", "exec", "-i", postgres_container, "pg_restore", "-U", "oicore",
                        "-d", "oicore", "--clean", "--if-exists", "--no-owner"], stdin=handle, check=False)
    return {"backup": key, "manifest": manifest}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="restore a deployment backup")
    parser.add_argument("--bucket", default="oi-core-backups-878451098165")
    parser.add_argument("--prefix", default="backups")
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--postgres-container", default="postgres")
    args = parser.parse_args(argv)
    print(json.dumps(restore(args.bucket, args.prefix, args.root, args.postgres_container), indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
