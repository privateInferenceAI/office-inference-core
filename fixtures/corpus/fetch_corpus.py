#!/usr/bin/env python3
"""Fetch the fixture corpus and record what was actually downloaded.

The corpus is four real business documents rather than a downloaded dataset, for the reason
`docs/evaluation-corpora.md` gives: two of its named sources are distributed as `datasets`-library
loaders, which need the Hub client and access to upstream archives. SEC EDGAR and the IRS serve files
directly, so a client box can fetch them with no credentials and nothing to install.

What this records and why. The manifest carries the licence, read at the source. This writes a lockfile
alongside the bytes with the URL, the fetch time, the size and the sha256, because a corpus whose
contents can change under it is not a fixture: the same question set applied to different bytes is a
different test. The lockfile is what makes a run reproducible, and it is the file to diff when a result
moves and nobody changed the code.

Refuses to overwrite a file whose hash does not match the lockfile. That is deliberate: if a source has
changed, that is a finding about the corpus rather than a silent refresh.

Usage, from the repo root:

    python3 fixtures/corpus/fetch_corpus.py              # fetch anything missing
    python3 fixtures/corpus/fetch_corpus.py --verify     # check bytes against the lockfile, fetch nothing
    python3 fixtures/corpus/fetch_corpus.py --force      # refetch, and report hashes that moved
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

try:
    import yaml
except ImportError:  # the corpus manifest is YAML, and the box has PyYAML for the renderer
    print("this needs PyYAML, which the box already has for scripts/render_manifest.py")
    raise SystemExit(1)

HERE = Path(__file__).resolve().parent
MANIFEST = HERE / "manifest.yaml"
LOCKFILE = HERE / "corpus.lock.json"


def sha256_of(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def load_lock() -> dict:
    if LOCKFILE.exists():
        return json.loads(LOCKFILE.read_text())
    return {"corpus_version": None, "documents": {}}


def fetch(url: str, user_agent: str, timeout: int = 120) -> bytes:
    request = urllib.request.Request(url, headers={"User-Agent": user_agent})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return response.read()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--verify", action="store_true", help="check bytes against the lockfile, fetch nothing")
    parser.add_argument("--force", action="store_true", help="refetch everything and report moved hashes")
    args = parser.parse_args(argv)

    manifest = yaml.safe_load(MANIFEST.read_text())
    user_agent = manifest["user_agent"]
    delay = float(manifest.get("request_delay_seconds", 0.5))
    lock = load_lock()
    problems: list[str] = []
    fetched = verified = 0

    for document in manifest["documents"]:
        target = HERE / document["path"]
        recorded = (lock.get("documents") or {}).get(document["id"])

        if args.verify:
            if not target.exists():
                problems.append(f"{document['id']}: missing {document['path']}")
                continue
            actual = sha256_of(target)
            if recorded and recorded.get("sha256") != actual:
                problems.append(
                    f"{document['id']}: hash moved. lockfile {recorded['sha256'][:16]}, "
                    f"on disk {actual[:16]}")
                continue
            print(f"  ok    {document['id']:<22} {target.stat().st_size:>9} bytes  {actual[:16]}")
            verified += 1
            continue

        if target.exists() and not args.force:
            actual = sha256_of(target)
            if recorded and recorded.get("sha256") != actual:
                problems.append(
                    f"{document['id']}: refusing to use {document['path']}, its hash does not match the "
                    f"lockfile. Re-run with --force to refetch and record the new bytes.")
                continue
            print(f"  have  {document['id']:<22} {target.stat().st_size:>9} bytes")
            verified += 1
            continue

        print(f"  fetch {document['id']:<22} {document['url'][:72]}")
        try:
            payload = fetch(document["url"], user_agent)
        except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError) as exc:
            problems.append(f"{document['id']}: fetch failed: {exc}")
            continue
        if recorded and args.force and recorded.get("sha256"):
            moved = recorded["sha256"] != hashlib.sha256(payload).hexdigest()
            if moved:
                print(f"        NOTE: the bytes at this URL have changed since "
                      f"{recorded.get('fetched', 'the last fetch')}")
        target.write_bytes(payload)
        digest = hashlib.sha256(payload).hexdigest()
        lock.setdefault("documents", {})[document["id"]] = {
            "path": document["path"],
            "url": document["url"],
            "sha256": digest,
            "bytes": len(payload),
            "content_type": None,
            "fetched": time.strftime("%Y-%m-%d"),
            "source": document["source"],
            "licence": " ".join(str(document["licence"]).split()),
        }
        print(f"        {len(payload):>9} bytes  sha256 {digest[:16]}")
        fetched += 1
        time.sleep(delay)

    # The lockfile is only rewritten when something was actually fetched or verified against it. The first
    # version wrote it on every run, so a plain run against files that were already present left a tracked
    # file modified and the next `git pull` refused. Re-verifying present files changes nothing, so it
    # should write nothing.
    lock["corpus_version"] = manifest.get("corpus_version")
    if not args.verify and fetched:
        LOCKFILE.write_text(json.dumps(lock, indent=2, sort_keys=True) + "\n")

    print()
    print(f"fetched {fetched}, already present or verified {verified}, "
          f"documents in the corpus {len(manifest['documents'])}")
    if problems:
        print()
        print(f"{len(problems)} problems:")
        for problem in problems:
            print(f"  - {problem}")
        return 1
    print(f"lockfile: {LOCKFILE.relative_to(HERE.parents[1])}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
