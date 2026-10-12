#!/usr/bin/env python3
"""Chunk the scanned fee schedule for real and show what the chunker made of it.

This is step 3.5's verification. The unit tests pin the rules against synthetic documents; this runs
them against the parser's actual output for the fixture, which is the only thing that can catch a rule
that holds on a document I invented and not on one Docling produced.

What it proves, or does not:

  * one logical schedule, emitted by the parser as two tables, comes back as ONE table chunk;
  * every fee row is present, including the ones in the fragment that carried no header;
  * every row entry carries its column names, so a row retrieved alone says what its number is;
  * no fee appears in a page chunk as well, which would give one figure two different citations;
  * every chunk's page range is a range, and the ids are stable across two runs.

Usage, from the repo root on the box:

    docker run --rm --network ai-net -v /opt/oi-core:/w -w /w \\
      --entrypoint python ghcr.io/docling-project/docling-serve-cpu:v1.36.0 \\
      /w/tests/fixtures/chunk_fee_schedule.py
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

# The seven values the fixture's generator also prints, so the question and the answer cannot drift.
EXPECTED = {
    "Mileage, client-owned vehicle": "0.67",
    "Standard labour, business hours": "145.00",
    "After-hours multiplier": "1.5x",
    "Emergency attendance": "240.00",
    "Callout fee, scheduled visit": "89.00",
    "Filter media, standard": "32.50",
    "Refrigerant, R-410A": "18.75",
}


def parse() -> dict:
    boundary = "----chunkprobe"
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
        payload = json.loads(response.read())
    return (payload.get("document") or {}).get("json_content") or {}


def main() -> int:
    document = parse()
    chunks = chunker.chunk_document(document, "oi-core-fixture", "fixture-sha")
    print(f"{len(chunks)} chunks from the parsed fixture")
    for chunk in chunks:
        print(f"  {chunk.kind:<10} p{chunk.page_start}-{chunk.page_end} "
              f"{chunk.token_estimate if hasattr(chunk, 'token_estimate') else ''} "
              f"{chunk.text[:66]!r}")

    failures: list[str] = []

    tables = [c for c in chunks if c.kind == "table"]
    if len(tables) != 1:
        failures.append(f"expected the two parser tables to join into one, got {len(tables)}")
    rows = [c for c in chunks if c.kind == "table_row"]

    joined = " ".join(r.text for r in rows)
    for item, rate in EXPECTED.items():
        if f"Item: {item}" not in joined:
            failures.append(f"{item!r} is missing from the row entries")
        elif rate not in joined:
            failures.append(f"{rate!r} is missing from the row entries")
        else:
            match = next(r.text for r in rows if f"Item: {item}" in r.text)
            if f"Rate: {rate}" not in match:
                failures.append(f"{item!r} does not carry its own rate: {match!r}")

    # The duplication check, scoped to what can actually be duplicated. Prose that names a rate on
    # purpose is not a fault: the fixture's own text says the mileage rate applies to client vehicles.
    # What must not happen is a table ROW's content turning up inside a page chunk, because that is the
    # same figures in two retrievable places with two different citations, and one of them will be wrong.
    prose = " ".join(c.text for c in chunks if c.kind == "prose").lower()
    for item in EXPECTED:
        if item.lower() in prose:
            failures.append(f"the fee row {item!r} appears in a page chunk as well as in the table")

    for chunk in chunks:
        if chunk.page_start > chunk.page_end:
            failures.append(f"{chunk.chunk_id} has page_start after page_end")
        if chunk.kind == "table" and chunk.page_start == chunk.page_end:
            pass  # a one-page table is a range of one, which is correct
    again = chunker.chunk_document(document, "oi-core-fixture", "fixture-sha")
    if [c.chunk_id for c in chunks] != [c.chunk_id for c in again]:
        failures.append("chunk ids are not stable across two runs of the same input")

    print()
    if failures:
        print(f"FAILED with {len(failures)} problems:")
        for problem in failures:
            print(f"  - {problem}")
        return 1
    print(f"PASSED: {len(rows)} row entries covering all {len(EXPECTED)} fees, "
          f"one joined table, no fee duplicated into a page chunk, stable ids")
    return 0


if __name__ == "__main__":
    sys.exit(main())
