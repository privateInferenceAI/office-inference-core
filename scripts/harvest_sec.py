#!/usr/bin/env python3
"""Build a corpus of real material contracts, using the sec-api MCP to find them and EDGAR to fetch them.

**The shape of this follows from how each side is metered.** The sec-api trial is billed by bandwidth, not
by call: a metadata search costs about 33 KB and returns a hundred filing URLs with company, ticker, form
and date, while fetching a document's bytes through it costs about 330 KB for one file. EDGAR serves the
same bytes for nothing and has no quota. So the MCP is used to *find* documents and EDGAR to *fetch* them,
which turns a limited trial into a corpus of whatever size is useful.

`--pdf N` spends the trial on the one thing EDGAR cannot give: real PDFs. `filing-to-pdf` renders a filing
through Chromium and returns the result, so those are genuine PDF documents with a text layer — worth
having, because almost every exhibit on EDGAR is HTML, and **not** a substitute for a scanned document.
A Chromium render needs no OCR, so the scanned path stays untested by this.

Set --user-agent to a real contact address. EDGAR blocks requests without one.
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

MCP = "https://api.sec-api.io/mcp"
REQUEST_INTERVAL_SECONDS = 0.2
WANTED = {".htm", ".html", ".txt", ".pdf", ".doc", ".docx"}

_last = [0.0]


def _throttle() -> None:
    wait = REQUEST_INTERVAL_SECONDS - (time.monotonic() - _last[0])
    if wait > 0:
        time.sleep(wait)
    _last[0] = time.monotonic()


def http(url: str, user_agent: str, *, binary: bool = False, payload: dict | None = None):
    """One throttled request."""
    _throttle()
    data = json.dumps(payload).encode() if payload is not None else None
    headers = {"User-Agent": user_agent, "Accept-Encoding": "gzip, deflate"}
    if data:
        headers["Content-Type"] = "application/json"
        headers["Accept"] = "application/json, text/event-stream"
    request = urllib.request.Request(url, data=data, headers=headers,
                                     method="POST" if data else "GET")
    with urllib.request.urlopen(request, timeout=180) as response:
        raw = response.read()
        if response.headers.get("Content-Encoding") == "gzip":
            raw = gzip.decompress(raw)
        return raw if binary else raw.decode("utf-8", "replace")


def mcp_call(token: str, tool: str, arguments: dict, user_agent: str):
    """One MCP tool call, unwrapped to whatever the tool returned."""
    body = {"jsonrpc": "2.0", "id": 1, "method": "tools/call",
            "params": {"name": tool, "arguments": arguments}}
    url = f"{MCP}?apiKey={urllib.parse.quote(token)}"
    raw = http(url, user_agent, payload=body)
    result = (json.loads(raw).get("result") or {})
    if result.get("isError"):
        raise RuntimeError(f"{tool} failed: {json.dumps(result)[:300]}")
    text = "".join(c.get("text") or "" for c in (result.get("content") or []))
    return text


def find(token: str, user_agent: str, windows, pages: int, log=print) -> list[dict]:
    """Search for exhibits and return the filings, deduplicated by URL."""
    found: dict[str, dict] = {}
    for start, end in windows:
        for exhibit in ("10.1", "10.2", "10.3", "10.4", "10.5", "10.6"):
            for page in range(1, pages + 1):
                query = {"query": f'"exhibit {exhibit}"', "formTypes": ["8-K", "10-K", "10-Q", "S-1"],
                         "startDate": start, "endDate": end, "page": page}
                try:
                    text = mcp_call(token, "full-text-search", query, user_agent)
                    payload = json.loads(text)
                except (RuntimeError, json.JSONDecodeError, urllib.error.HTTPError,
                        urllib.error.URLError, TimeoutError) as exc:
                    log(f"    {exhibit} {start} p{page}: {type(exc).__name__}")
                    continue
                filings = payload.get("filings") or []
                fresh = 0
                for f in filings:
                    if f.get("filingUrl") and f["filingUrl"] not in found:
                        found[f["filingUrl"]] = f
                        fresh += 1
                log(f"    exhibit {exhibit} {start} p{page}: {len(filings)} results, {fresh} new")
                if len(filings) < 100:
                    break
    return list(found.values())


def slugify(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", (name or "unknown").lower()).strip("-")[:40] or "unknown"


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--token", required=True, help="the sec-api key")
    parser.add_argument("--user-agent", required=True)
    parser.add_argument("--target", type=int, default=400, help="how many documents to fetch")
    parser.add_argument("--pdf", type=int, default=0, help="how many to fetch as PDF through the MCP")
    parser.add_argument("--search-pages", type=int, default=3, help="pages per search, 100 results each")
    parser.add_argument("--windows", default="2026-09-01:2026-09-30,2026-05-01:2026-05-31,"
                                            "2025-10-01:2025-10-31,2025-03-01:2025-03-31")
    args = parser.parse_args(argv)

    windows = []
    for pair in args.windows.split(","):
        a, _, b = pair.partition(":")
        if a and b:
            windows.append((a.strip(), b.strip()))

    args.out.mkdir(parents=True, exist_ok=True)
    log_path = args.out / "manifest.json"
    manifest = {"source": "sec-api MCP search, documents fetched from SEC EDGAR",
                "documents": []}
    if log_path.exists():
        manifest = json.loads(log_path.read_text())
    have = {d["url"] for d in manifest["documents"]}

    print("searching via the MCP")
    filings = find(args.token, args.user_agent, windows, args.search_pages)
    print(f"  {len(filings)} distinct filings found, {len(have)} already downloaded")

    todo = [f for f in filings if f["filingUrl"] not in have][:args.target]
    print(f"fetching {len(todo)} documents from EDGAR")
    for i, f in enumerate(todo, 1):
        url = f["filingUrl"]
        suffix = Path(urllib.parse.urlparse(url).path).suffix.lower()
        if suffix not in WANTED:
            continue
        company = re.sub(r"\s*\(Filer\)\s*$", "", f.get("companyNameLong") or "unknown").strip()
        name = f"{slugify(company)}__{(f.get('type') or 'ex').replace('.', '_')}__{f.get('filedAt') or ''}"
        try:
            raw = http(url, args.user_agent, binary=True)
        except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError) as exc:
            print(f"    skip {url.rsplit('/', 1)[-1]}: {type(exc).__name__}")
            continue
        digest = hashlib.sha256(raw).hexdigest()
        local = args.out / f"{name}{suffix}"
        if local.exists():
            local = args.out / f"{name}__{digest[:8]}{suffix}"
        local.write_bytes(raw)
        manifest["documents"].append({
            "file": local.name, "url": url, "sha256": digest, "bytes": len(raw),
            "company": company, "ticker": f.get("ticker") or "", "form": f.get("formType") or "",
            "exhibit": f.get("type") or "", "filed": f.get("filedAt") or "",
            "accession": f.get("accessionNo") or "", "format": suffix.lstrip("."),
        })
        if i % 25 == 0:
            print(f"    {len(manifest['documents'])} documents")
            log_path.write_text(json.dumps(manifest, indent=2))

    if args.pdf:
        print(f"fetching {args.pdf} as PDF through the MCP, which is the only thing EDGAR cannot give")
        done = 0
        for f in filings:
            if done >= args.pdf:
                break
            stem = f"{slugify(re.sub(r' *[(]Filer[)] *$', '', f.get('companyNameLong') or 'x'))}" \
                   f"__{(f.get('type') or 'ex').replace('.', '_')}__{f.get('filedAt') or ''}"
            local = args.out / f"{stem}.pdf"
            if local.exists():
                continue
            try:
                text = mcp_call(args.token, "filing-to-pdf", {"url": f["filingUrl"]}, args.user_agent)
            except (RuntimeError, urllib.error.HTTPError, urllib.error.URLError, TimeoutError) as exc:
                print(f"    pdf skip: {type(exc).__name__}")
                continue
            raw = text.encode("latin-1", "ignore")
            if not raw.startswith(b"%PDF"):
                continue
            local.write_bytes(raw)
            manifest["documents"].append({
                "file": local.name, "url": f["filingUrl"],
                "sha256": hashlib.sha256(raw).hexdigest(), "bytes": len(raw),
                "company": re.sub(r"\s*\(Filer\)\s*$", "", f.get("companyNameLong") or ""),
                "ticker": f.get("ticker") or "", "form": f.get("formType") or "",
                "exhibit": f.get("type") or "", "filed": f.get("filedAt") or "",
                "accession": f.get("accessionNo") or "", "format": "pdf",
            })
            done += 1
            print(f"    pdf {done}/{args.pdf}")

    log_path.write_text(json.dumps(manifest, indent=2))
    counts: dict[str, int] = {}
    for d in manifest["documents"]:
        counts[d["format"]] = counts.get(d["format"], 0) + 1
    total = sum(d["bytes"] for d in manifest["documents"])
    print()
    print(f"  {len(manifest['documents'])} documents, {total / 1e6:.1f} MB")
    for fmt, n in sorted(counts.items(), key=lambda x: -x[1]):
        print(f"    {fmt:<6} {n}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
