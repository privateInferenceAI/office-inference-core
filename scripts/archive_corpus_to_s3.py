#!/usr/bin/env python3
"""Upload the corpus groups to S3, verify each one, and only then delete it locally.

**Verify before delete, in that order and never the reverse.** The point of the exercise is to get 116 GB off
a laptop that is 80% full, so deleting is the whole idea; deleting first and uploading second is how a silent
failure costs a corpus. Each group is uploaded, its keys and sizes are read back from S3, compared against
what is on disk, and only an exact match permits the local files to go.

**Verification is per file, not per group.** A count and a total would agree after an upload that dropped one
file and duplicated another. The check is that the set of names is identical and every name carries the same
number of bytes, which is cheap because S3 lists a thousand keys a page.

**S3 versioning is on**, so a file deleted here after a mistaken match is recoverable rather than gone.

    python3 scripts/archive_corpus_to_s3.py --groups ~/corpus-groups --bucket BUCKET --profile oi-credits
    python3 scripts/archive_corpus_to_s3.py ... --dry-run
    python3 scripts/archive_corpus_to_s3.py ... --only group-01
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path


def run(cmd: list[str], **kw) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True, **kw)


def remote_inventory(bucket: str, prefix: str, profile: str) -> dict[str, int]:
    """Every key under a prefix, with its size. The listing is authoritative, not our memory of the upload."""
    found: dict[str, int] = {}
    token = None
    while True:
        cmd = ["aws", "s3api", "list-objects-v2", "--bucket", bucket, "--prefix", prefix,
               "--profile", profile, "--output", "json", "--max-items", "1000"]
        if token:
            cmd += ["--starting-token", token]
        result = run(cmd)
        if result.returncode != 0:
            raise RuntimeError(f"listing {prefix} failed: {result.stderr.strip()[:300]}")
        payload = json.loads(result.stdout or "{}")
        for item in payload.get("Contents", []):
            # Key relative to the prefix, which is the file name as it sits on disk.
            found[item["Key"][len(prefix):].lstrip("/")] = item["Size"]
        token = payload.get("NextToken")
        if not token:
            break
    return found


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--groups", type=Path, required=True)
    parser.add_argument("--bucket", required=True)
    parser.add_argument("--prefix", default="corpus")
    parser.add_argument("--profile", default="oi-credits")
    parser.add_argument("--only", action="append", help="one group by name; repeatable")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--keep-local", action="store_true", help="upload and verify but delete nothing")
    args = parser.parse_args(argv)

    folders = sorted(d for d in args.groups.iterdir() if d.is_dir())
    if args.only:
        wanted = set(args.only)
        folders = [d for d in folders if d.name in wanted]
    if not folders:
        parser.error(f"no group folders under {args.groups}")

    total_bytes = sum(f.stat().st_size for d in folders for f in d.iterdir() if f.is_file())
    print(f"  {len(folders)} folders, {total_bytes/1e9:.2f} GB")
    print(f"  s3://{args.bucket}/{args.prefix}/<group>/")
    print()

    receipts = []
    for folder in folders:
        files = sorted((f for f in folder.iterdir() if f.is_file()), key=lambda f: f.name)
        local = {f.name: f.stat().st_size for f in files}
        size = sum(local.values())
        prefix = f"{args.prefix}/{folder.name}/"
        print(f"  {folder.name}: {len(files):,} files, {size/1e9:.2f} GB", flush=True)

        if args.dry_run:
            print("    dry run, nothing uploaded")
            continue

        started = time.monotonic()
        uploaded = run(["aws", "s3", "sync", str(folder), f"s3://{args.bucket}/{prefix}",
                        "--profile", args.profile, "--only-show-errors"])
        elapsed = time.monotonic() - started
        if uploaded.returncode != 0:
            print(f"    ** upload failed: {uploaded.stderr.strip()[:300]}", file=sys.stderr)
            return 1
        rate = size / elapsed / 1e6 if elapsed else 0
        print(f"    uploaded in {elapsed/60:.1f} min at {rate:.1f} MB/s", flush=True)

        remote = remote_inventory(args.bucket, prefix, args.profile)
        missing = sorted(set(local) - set(remote))
        extra = sorted(set(remote) - set(local))
        mismatched = sorted(n for n in set(local) & set(remote) if local[n] != remote[n])

        if missing or extra or mismatched:
            print(f"    ** NOT VERIFIED: {len(missing)} missing, {len(extra)} unexpected, "
                  f"{len(mismatched)} size mismatches", file=sys.stderr)
            for n in (missing + extra + mismatched)[:5]:
                print(f"       {n}: local {local.get(n)} remote {remote.get(n)}", file=sys.stderr)
            print("    the local files are untouched. Nothing is deleted without an exact match.",
                  file=sys.stderr)
            return 1

        print(f"    verified: {len(remote):,} keys, every size identical", flush=True)
        receipts.append({"group": folder.name, "files": len(local), "bytes": size,
                         "s3_prefix": f"s3://{args.bucket}/{prefix}", "verified": True})

        if args.keep_local:
            print("    --keep-local, so the files stay", flush=True)
            continue
        for f in files:
            f.unlink()
        folder.rmdir()
        print(f"    local files deleted, {folder.name} is now empty", flush=True)

    (args.groups / "s3-receipt.json").write_text(json.dumps(
        {"bucket": args.bucket, "prefix": args.prefix, "groups": receipts}, indent=2) + "\n")
    print(f"\n  {len(receipts)} group(s) uploaded, verified and cleared")
    return 0


if __name__ == "__main__":
    sys.exit(main())
