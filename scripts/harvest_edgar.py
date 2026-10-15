#!/usr/bin/env python3
"""Harvest real material contracts from SEC EDGAR into a directory the worker can read.

**Why this rather than the sec-api.io dataset.** That dataset is the same documents repackaged: its
listing is public and its containers need an active subscription, which a trial token does not have
(403 Endpoint requires an active subscription). EDGAR itself is free, unmetered, and is where those
files come from in the first place. The only thing the dataset sells is the collecting, and collecting
is what this script does.

**What it gets.** Exhibit 10 is where a public company files its material contracts: employment
agreements, credit facilities, licences, merger agreements, partnership agreements. They are real, they
are long, and they have the clause structure a small business's contracts have. Almost all of them are
HTML, which is worth knowing before running it: this is a corpus for content realism, not for the
scanned-document path.

EDGAR's full-text search covers 2001 onwards, so nothing here is older than that. Set --user-agent to
something with a real contact address, because EDGAR blocks requests without one and it is the polite
thing to do with a free service.
"""
from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

SEARCH = "https://efts.sec.gov/LATEST/search-index"
ARCHIVE = "https://www.sec.gov/Archives/edgar/data"

# EDGAR asks for ten requests a second at most. Half that, because the search and the archive are
# different services and being throttled part-way through is worse than taking a little longer.
REQUEST_INTERVAL_SECONDS = 0.2

# Filing forms that carry material contracts. Narrower than the dataset's list on purpose: the point is
# contracts, and a fund's registration statement is a different kind of document.
FORMS = "8-K,10-K,10-Q,S-1,S-4,20-F,10-12B,8-K/A,10-K/A"

# **Unquoted, and that is not a detail.** A quoted "exhibit 10" is an exact-phrase match and found one
# filing in a whole month; unquoted "exhibit 10.1" found 1,713 in the same month, because the engine
# treats it as terms and a filing carries its own punctuation around the exhibit number.
PHRASES = ("exhibit 10.1", "exhibit 10.2", "exhibit 10.3", "exhibit 10.4", "exhibit 10.5")

# Each search response holds a hundred hits. EDGAR serves pages up to ten thousand results deep.
PAGE_SIZE = 100
MAX_PAGES = 20

# Suffixes worth taking. EDGAR exhibits are overwhelmingly HTML and plain text, with the occasional PDF.
# Anything else in a filing directory is an image, an XBRL instance, or a schema, and none of those are
# documents a person wrote.
WANTED = {".htm", ".html", ".txt", ".pdf", ".doc", ".docx", ".xls", ".xlsx", ".rtf", ".csv"}

_last_request = [0.0]


def _get(url: str, user_agent: str, *, binary: bool = False):
    """One throttled request. Raises on anything that is not a 200."""
    wait = REQUEST_INTERVAL_SECONDS - (time.monotonic() - _last_request[0])
    if wait > 0:
        time.sleep(wait)
    request = urllib.request.Request(url, headers={"User-Agent": user_agent,
                                                  "Accept-Encoding": "gzip, deflate"})
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            raw = response.read()
            if response.headers.get("Content-Encoding") == "gzip":
                raw = gzip.decompress(raw)
            return raw if binary else raw.decode("utf-8", "replace")
    finally:
        _last_request[0] = time.monotonic()


def search(phrase: str, start: str, end: str, user_agent: str, offset: int = 0) -> list:
    """One page of full-text results."""
    query = urllib.parse.urlencode({"q": phrase, "startdt": start, "enddt": end,
                                    "forms": FORMS, "from": offset, "size": PAGE_SIZE})
    try:
        return json.loads(_get(f"{SEARCH}?{query}", user_agent)).get("hits", {}).get("hits", [])
    except (json.JSONDecodeError, urllib.error.HTTPError, urllib.error.URLError, TimeoutError):
        return []


def document_url(hit: dict):
    """The archive URL for a search hit, or None when it names something unusable."""
    accession, _, filename = hit.get("_id", "").partition(":")
    ciks = (hit.get("_source") or {}).get("ciks") or []
    if not accession or not filename or not ciks:
        return None
    if Path(filename).suffix.lower() not in WANTED:
        return None
    return f"{ARCHIVE}/{ciks[0].lstrip('0')}/{accession.replace('-', '')}/{filename}"


def save(hit: dict, url: str, raw: bytes, out: Path, digest: str) -> dict:
    """Write one document under a name a person could read in a citation."""
    source = hit.get("_source") or {}
    company = (source.get("display_names") or ["unknown"])[0]
    company = re.sub(r"\s*\(CIK \d+\)\s*$", "", company).strip()
    slug = re.sub(r"[^a-z0-9]+", "-", company.lower()).strip("-")[:40] or "unknown"
    suffix = Path(url).suffix.lower()
    stem = Path(url).stem[:40]
    local = out / f"{slug}__{stem}{suffix}"
    if local.exists():
        local = out / f"{slug}__{stem}__{digest[:8]}{suffix}"
    local.write_bytes(raw)
    return {"file": local.name, "url": url, "sha256": digest, "bytes": len(raw),
            "company": company, "form": source.get("file_type") or "",
            "filed": source.get("file_date") or "", "accession": hit["_id"].split(":")[0]}


def harvest(out: Path, target: int, user_agent: str, windows, log=print) -> dict:
    out.mkdir(parents=True, exist_ok=True)
    manifest_path = out / "manifest.json"
    manifest = {"documents": [], "source": "SEC EDGAR full-text search",
                "phrases": list(PHRASES), "forms": FORMS}
    if manifest_path.exists():
        manifest = json.loads(manifest_path.read_text())
    seen = {d["sha256"] for d in manifest["documents"]}
    urls_seen = {d["url"] for d in manifest["documents"]}

    def enough() -> bool:
        return len(manifest["documents"]) >= target

    for start, end in windows:
        for phrase in PHRASES:
            if enough():
                break
            log(f"  {start} to {end}, {phrase!r}")
            for page in range(MAX_PAGES):
                if enough():
                    break
                hits = search(phrase, start, end, user_agent, offset=page * PAGE_SIZE)
                if not hits:
                    break
                for hit in hits:
                    if enough():
                        break
                    url = document_url(hit)
                    if not url or url in urls_seen:
                        continue
                    try:
                        raw = _get(url, user_agent, binary=True)
                    except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError) as exc:
                        log(f"    skip {url.rsplit('/', 1)[-1]}: {type(exc).__name__}")
                        continue
                    digest = hashlib.sha256(raw).hexdigest()
                    if digest in seen:
                        continue
                    manifest["documents"].append(save(hit, url, raw, out, digest))
                    seen.add(digest)
                    urls_seen.add(url)
                    if len(manifest["documents"]) % 25 == 0:
                        log(f"    {len(manifest['documents'])} documents")
                        manifest_path.write_text(json.dumps(manifest, indent=2))
                if len(hits) < PAGE_SIZE:
                    break
    manifest_path.write_text(json.dumps(manifest, indent=2))
    return manifest


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--out", type=Path, required=True, help="where the documents go")
    parser.add_argument("--target", type=int, default=300, help="how many documents to collect")
    parser.add_argument("--user-agent", required=True,
                        help="a contact address, because EDGAR blocks requests without one")
    parser.add_argument("--windows", default="2026-09-01:2026-09-30,2026-06-01:2026-06-30,"
                                            "2026-02-01:2026-02-28,2025-06-01:2025-06-30,"
                                            "2024-09-01:2024-09-30,2023-04-01:2023-04-30",
                        help="comma-separated start:end date windows to search")
    args = parser.parse_args(argv)

    windows = []
    for pair in args.windows.split(","):
        start, _, end = pair.partition(":")
        if start and end:
            windows.append((start.strip(), end.strip()))
    if not windows:
        parser.error("no date windows given")

    print(f"harvesting up to {args.target} documents from EDGAR into {args.out}")
    manifest = harvest(args.out, args.target, args.user_agent, windows)
    total = sum(d["bytes"] for d in manifest["documents"])
    suffixes = {}
    for d in manifest["documents"]:
        suffixes[Path(d["file"]).suffix] = suffixes.get(Path(d["file"]).suffix, 0) + 1
    print()
    print(f"  {len(manifest['documents'])} documents, {total / 1e6:.1f} MB")
    for suffix, count in sorted(suffixes.items(), key=lambda x: -x[1]):
        print(f"    {suffix:<7} {count}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
