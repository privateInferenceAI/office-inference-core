#!/usr/bin/env python3
"""Back up a deployment's state to the encrypted backup bucket.

Backs up the Postgres database, the rendered configuration and secrets, the manifests and the documents root.
The state is small; the database dump is compressed by pg_dump and the documents tarball is gzip. The upload
uses SSE-S3 and the bucket blocks public access.

The bucket is not a substitute for a client-owned backup target. It is the development deployment's target.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tarfile
import tempfile
from datetime import datetime, timezone
from pathlib import Path

import boto3

BUCKET = "oi-core-backups-878451098165"
PREFIX = "backups"


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def run(command: list[str], *, stdout=None) -> None:
    subprocess.run(command, check=True, stdout=stdout or subprocess.DEVNULL)


def backup(root: Path, bucket: str, prefix: str, out_dir: Path | None = None) -> dict:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    work = Path(out_dir) if out_dir else Path(tempfile.mkdtemp(prefix="oi-backup-"))
    work.mkdir(parents=True, exist_ok=True)

    dump = work / "postgres.dump"
    with dump.open("wb") as handle:
        run(["docker", "exec", "postgres", "pg_dump", "-U", "oicore", "-d", "oicore", "-Fc"], stdout=handle)

    state_tar = work / "state.tar.gz"
    with tarfile.open(state_tar, "w:gz") as archive:
        for name in ("rendered", "manifests", "documents", "data/qdrant"):
            source = root / name
            if source.exists():
                archive.add(source, arcname=name)

    images = subprocess.run(["docker", "images", "--format", "{{.Repository}}:{{.Tag}} {{.ID}}"],
                            capture_output=True, text=True, check=True).stdout.strip().splitlines()
    commit = subprocess.run(["git", "-C", str(root), "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip()
    manifest = {
        "deployment": "oi-core-dev-cloud-workspace",
        "timestamp": stamp,
        "commit": commit,
        "postgres": {"file": dump.name, "sha256": sha256(dump), "bytes": dump.stat().st_size},
        "state": {"file": state_tar.name, "sha256": sha256(state_tar), "bytes": state_tar.stat().st_size},
        "images": images,
        "rpo_hours": 24,
    }
    manifest_path = work / "backup.json"
    manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")

    key = f"{prefix}/{stamp}/"
    client = boto3.client("s3", region_name="us-east-1")
    for path in (dump, state_tar, manifest_path):
        client.upload_file(str(path), bucket, key + path.name, ExtraArgs={"ServerSideEncryption": "AES256"})
    manifest["s3"] = f"s3://{bucket}/{key}"
    print(json.dumps(manifest, indent=2, sort_keys=True))
    return manifest


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="back up the deployment")
    parser.add_argument("--root", type=Path, default=Path("/opt/oi-core"))
    parser.add_argument("--bucket", default=BUCKET)
    parser.add_argument("--prefix", default=PREFIX)
    parser.add_argument("--out", type=Path)
    args = parser.parse_args(argv)
    backup(args.root, args.bucket, args.prefix, args.out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
