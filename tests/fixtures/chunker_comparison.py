#!/usr/bin/env python3
"""Docling's own chunkers against the one written here, on the same real document.

Anthony's question, 2026-10-12: if we are using an app, why build a chunker from scratch, and would
the app's own chunker not do. That is a better question than an argument, so this answers it with
output. Docling ships four chunkers, and one of them is literally called PageChunker and yields one
chunk per page, which is the shape our architecture asks for.

So the honest test is: run both over the fixture's real parse and look at what each produces. If
Docling's is good enough, most of our module should be deleted, and this script is how we find that out
rather than guessing.

What to look for in the output:

  * chunk COUNT and size, which is about fitting the embedding budget;
  * whether a table stays whole, or arrives as rows mixed into a page's prose;
  * whether each chunk can say which PAGE it came from, and where on the page;
  * whether a fee row can be retrieved on its own with its column names.

Run from the repo root on the box:

    docker run --rm --network ai-net -v /opt/oi-core:/w -w /w \\
      --entrypoint python ghcr.io/docling-project/docling-serve-cpu:v1.36.0 \\
      /w/tests/fixtures/chunker_comparison.py
"""
from __future__ import annotations

import json
import sys
import urllib.request
from pathlib import Path

sys.path.insert(0, "/w/services/ingestion")

import chunker  # noqa: E402

PARSER = "http://parser:5001"
FIXTURE = Path("/w/tests/fixtures/fee-schedule-scanned.pdf")


def parse() -> dict:
    boundary = "----cmp"
    body = bytearray()

    def part(headers: list[str], content: bytes) -> None:
        body.extend(f"--{boundary}\r\n".encode())
        for header in headers:
            body.extend(f"{header}\r\n".encode())
        body.extend(b"\r\n")
        body.extend(content)
        body.extend(b"\r\n")

    part(['Content-Disposition: form-data; name="files"; filename="fee.pdf"',
          "Content-Type: application/pdf"], FIXTURE.read_bytes())
    part(['Content-Disposition: form-data; name="to_formats"'], b"json")
    body.extend(f"--{boundary}--\r\n".encode())
    request = urllib.request.Request(
        PARSER + "/v1/convert/file", data=bytes(body),
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}"})
    with urllib.request.urlopen(request, timeout=900) as response:
        return (json.loads(response.read()).get("document") or {}).get("json_content") or {}


def report_ours(document: dict) -> None:
    chunks = chunker.chunk_document(document, "fixture", "sha")
    print("=== ours (services/ingestion/chunker.py) ===")
    print(f"chunks: {len(chunks)}")
    for c in chunks:
        tokens = c.token_count
        print(f"  {c.kind:<10} p{c.page_start}-{c.page_end} tokens~{tokens:<5} {c.text[:60]!r}")
    tables = [c for c in chunks if c.kind == "table"]
    rows = [c for c in chunks if c.kind == "table_row"]
    print(f"  table chunks: {len(tables)}   row entries: {len(rows)}")
    print(f"  a row retrieved alone reads: {rows[0].text[:70]!r}" if rows else "  no row entries")


def report_theirs(document: dict) -> None:
    print()
    print("=== docling's own chunkers, same document ===")
    try:
        from docling_core.types import DoclingDocument
        from docling_core.transforms.chunker.page_chunker import PageChunker
        from docling_core.transforms.chunker.hierarchical_chunker import HierarchicalChunker
    except Exception as exc:  # noqa: BLE001 - the point is to report
        print(f"  could not import docling's chunkers: {exc}")
        return

    try:
        dl_doc = DoclingDocument.model_validate(document)
    except Exception as exc:  # noqa: BLE001
        print(f"  the parsed JSON does not validate as a DoclingDocument: {exc}")
        print("  (that itself is worth knowing: our chunker works on the raw JSON, theirs needs the model)")
        return

    for name, chunker_ in (("PageChunker", PageChunker()),
                           ("HierarchicalChunker", HierarchicalChunker())):
        try:
            chunks = list(chunker_.chunk(dl_doc))
        except Exception as exc:  # noqa: BLE001
            print(f"  {name}: failed: {exc}")
            continue
        print(f"  {name}: {len(chunks)} chunks")
        for index, chunk in enumerate(chunks):
            text = getattr(chunk, "text", "")
            items = getattr(getattr(chunk, "meta", None), "doc_items", None) or []
            pages = sorted({prov.page_no for item in items
                            for prov in (getattr(item, "prov", None) or [])})
            print(f"    [{index}] {len(text)} chars, pages {pages or 'not on the chunk'}, "
                  f"{len(items)} doc items")
            print(f"        {text[:90]!r}")


def main() -> int:
    document = parse()
    print(f"parsed fixture: {len(document.get('texts') or [])} texts, "
          f"{len(document.get('tables') or [])} tables, "
          f"{len(document.get('pages') or {})} pages")
    print()
    report_ours(document)
    report_theirs(document)
    print()
    print("The question this answers: if Docling's PageChunker already gives page-bounded chunks,")
    print("what does ours add that is worth keeping, and what is it reimplementing?")
    return 0


if __name__ == "__main__":
    sys.exit(main())
