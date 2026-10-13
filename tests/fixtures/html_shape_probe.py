#!/usr/bin/env python3
"""Why two HTML exhibits produced zero chunks while a 90-page 10-K produced 588.

The worker parsed both EX-10 exhibits, reported success, wrote nothing, and said `0 chunks`. A document
that ingests to nothing is worse than one that fails, because it is retrievable-looking and empty.

This dumps the shape of the parser's structured output for one of them, so the reason is read rather
than guessed. The candidates are a body that references no text items, text items whose provenance the
chunker skips, or content the parser put somewhere the chunker does not walk.

Run from the repo root on the box:

    docker run --rm --network ai-net -v /opt/oi-core:/w -w /w \\
      --entrypoint python ghcr.io/docling-project/docling-serve-cpu:v1.36.0 \\
      /w/tests/fixtures/html_shape_probe.py
"""
from __future__ import annotations

import json
import sys
import urllib.request
from collections import Counter
from pathlib import Path

sys.path.insert(0, "/w/services/ingestion")
import chunker  # noqa: E402

PARSER = "http://parser:5001"
TARGET = Path("/w/documents/company/apple-ex10-1-2024.htm")


def parse() -> dict:
    boundary = "----shape"
    body = bytearray()

    def part(headers, content):
        body.extend(f"--{boundary}\r\n".encode())
        for header in headers:
            body.extend(f"{header}\r\n".encode())
        body.extend(b"\r\n")
        body.extend(content)
        body.extend(b"\r\n")

    part(['Content-Disposition: form-data; name="files"; filename="ex.htm"',
          "Content-Type: text/html"], TARGET.read_bytes())
    part(['Content-Disposition: form-data; name="to_formats"'], b"json")
    body.extend(f"--{boundary}--\r\n".encode())
    request = urllib.request.Request(
        PARSER + "/v1/convert/file", data=bytes(body),
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}"})
    with urllib.request.urlopen(request, timeout=900) as response:
        return (json.loads(response.read()).get("document") or {}).get("json_content") or {}


def main() -> int:
    print(f"parsing {TARGET.name} ({TARGET.stat().st_size} bytes)")
    document = parse()

    print()
    print("=== top-level keys and their sizes ===")
    for key, value in sorted(document.items()):
        if isinstance(value, list):
            print(f"  {key:<14} {len(value)} entries")
        elif isinstance(value, dict):
            print(f"  {key:<14} {len(value)} keys")
        else:
            print(f"  {key:<14} {value!r}")

    print()
    print("=== body ===")
    body = document.get("body") or {}
    children = body.get("children") or []
    print(f"  body children: {len(children)}")
    labels = Counter()
    for child in children[:400]:
        ref = child.get("$ref") or ""
        kind = ref.split("/")[1] if ref.startswith("#/") else "?"
        labels[kind] += 1
    print(f"  child reference kinds: {dict(labels)}")
    print(f"  first 8 refs: {[c.get('$ref') for c in children[:8]]}")

    print()
    print("=== text items, and whether the chunker can reach them ===")
    texts = document.get("texts") or []
    print(f"  texts: {len(texts)}")
    if texts:
        print(f"  labels: {dict(Counter(t.get('label') for t in texts))}")
        with_prov = sum(1 for t in texts if (t.get("prov") or []))
        print(f"  with provenance: {with_prov} of {len(texts)}")
        print(f"  first text: {texts[0].get('self_ref')} "
              f"{str(texts[0].get('text'))[:60]!r}")

    print()
    print("=== what the chunker's own walk produces ===")
    order = chunker._walk_order(body, document)
    print(f"  _walk_order returns {len(order)} refs")
    print(f"  first 8: {order[:8]}")
    prose = chunker.page_texts(document)
    print(f"  page_texts returns {len(prose)} items")
    chunks = chunker.chunk_document(document, "probe", "sha")
    print(f"  chunk_document returns {len(chunks)} chunks")

    print()
    if not chunks and texts:
        print("DIAGNOSIS: the document holds text the chunker's walk does not reach.")
        print("The body's children are the traversal, so a text item not referenced from there is")
        print("invisible however much content it has.")
    elif chunks:
        print(f"DIAGNOSIS: the chunker reaches this document now ({len(chunks)} chunks).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
