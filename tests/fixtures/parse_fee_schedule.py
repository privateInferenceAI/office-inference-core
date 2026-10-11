#!/usr/bin/env python3
"""Parse the scanned fee-schedule fixture through the parser service and grade what came back.

This is the gate for `RET-PARSE-001`: a scanned fee-schedule fixture returns correct numbers in a
table, and a scanned page returns text rather than nothing. Two failures count, and they are
different:

  * the page comes back empty, which means OCR did not run or did not find anything;
  * the numbers come back attached to the wrong rows, which is worse, because it produces a
    confidently cited wrong number rather than a refusal.

The seven values graded here are listed in `tests/fixtures/build_fee_schedule.py` so the fixture and
this grader cannot drift apart. Nothing in this script needs the corpus or a question set.

Usage, from the repo root on the box:

    docker run --rm --network ai-net -v /opt/oi-core/tests/fixtures:/fixtures:ro \
      python:3.12-slim python /fixtures/parse_fee_schedule.py

Exits 0 when every graded value is present and correctly placed, 1 otherwise. It prints what it saw
either way, because a partial pass is the interesting case.
"""
from __future__ import annotations

import json
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

SERVICE = "http://parser:5001"
FIXTURE = Path(__file__).resolve().parent / "fee-schedule-scanned.pdf"

# Each number must sit next to the right label, within ROW_WINDOW characters, so a parser that puts
# 18.75 under filter media instead of refrigerant is caught rather than counted as a pass. A bare
# "is this number somewhere on the page" check would pass on a document where every row is shuffled,
# which is the failure that produces a confidently cited wrong number.
ROW_WINDOW = 120
GRADED = [
    ("mileage", "0.67", ["mileage"]),
    ("standard labour", "145.00", ["labour", "labor"]),
    ("after-hours multiplier", "1.5x", ["after-hours", "after hours"]),
    ("emergency attendance", "240.00", ["emergency"]),
    ("callout", "89.00", ["callout"]),
    ("filter media", "32.50", ["filter"]),
    ("refrigerant", "18.75", ["refrigerant"]),
]

OPTIONS = {
    "to_formats": ["json", "md"],
    # OCR on, and not forced: a page with a text layer should not be re-read through OCR, while this
    # fixture has none and must be. auto_truncate is not a Docling option; do_ocr is.
    "do_ocr": True,
    "force_ocr": False,
    "do_table_structure": True,
    "table_mode": "accurate",
    "pdf_backend": "docling_parse",
    "ocr_lang": ["en"],
    "abort_on_error": True,
}


def post_multipart(url: str, field: str, path: Path, payload: dict, timeout: int = 600) -> dict:
    """A multipart POST with the options as a JSON form field, which is the shape the API wants."""
    boundary = "----oicoreboundary"
    body = bytearray()

    def part(headers: list[str], content: bytes) -> None:
        body.extend(f"--{boundary}\r\n".encode())
        for header in headers:
            body.extend(f"{header}\r\n".encode())
        body.extend(b"\r\n")
        body.extend(content)
        body.extend(b"\r\n")

    part([f'Content-Disposition: form-data; name="{field}"; filename="{path.name}"',
          "Content-Type: application/pdf"], path.read_bytes())
    part(['Content-Disposition: form-data; name="convert_options"', "Content-Type: application/json"],
         json.dumps(payload).encode())
    body.extend(f"--{boundary}--\r\n".encode())

    request = urllib.request.Request(
        url, data=bytes(body),
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}"})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read())


def get_json(url: str, timeout: int = 600) -> dict:
    with urllib.request.urlopen(url, timeout=timeout) as response:
        return json.loads(response.read())


def collect_strings(node, out: list[str]) -> list[str]:
    """Every string anywhere in the document tree, which is enough to grade placement by proximity."""
    if isinstance(node, dict):
        for value in node.values():
            collect_strings(value, out)
    elif isinstance(node, list):
        for value in node:
            collect_strings(value, out)
    elif isinstance(node, str):
        out.append(node)
    return out


def _positions(haystack: str, needle: str) -> list[int]:
    """Every place a value occurs. "1.5" occurs inside "18.75", and "0.67" inside nothing, so the
    caller checks each occurrence rather than the first."""
    out, start = [], 0
    while True:
        found = haystack.find(needle, start)
        if found == -1:
            return out
        out.append(found)
        start = found + 1


def main() -> int:
    if not FIXTURE.exists():
        print(f"missing fixture {FIXTURE}. Run tests/fixtures/build_fee_schedule.py first.")
        return 1

    print(f"fixture {FIXTURE.name} ({FIXTURE.stat().st_size // 1024} KB)")
    started = time.perf_counter()
    try:
        task = post_multipart(f"{SERVICE}/v1/convert/file/async", "files", FIXTURE, OPTIONS)
    except urllib.error.HTTPError as exc:
        print(f"the service refused the request: HTTP {exc.code} {exc.reason}")
        print(exc.read().decode(errors="replace")[:500])
        return 1
    except urllib.error.URLError as exc:
        print(f"could not reach {SERVICE}: {exc}")
        return 1

    task_id = task.get("task_id")
    print(f"task {task_id} accepted, polling")
    status = task.get("task_status")
    deadline = time.time() + 900
    while status not in ("success", "failure") and time.time() < deadline:
        time.sleep(3)
        try:
            status = get_json(f"{SERVICE}/v1/status/poll/{task_id}").get("task_status")
        except urllib.error.HTTPError as exc:
            print(f"polling failed: HTTP {exc.code} {exc.reason}")
            return 1
    elapsed = time.perf_counter() - started
    if status != "success":
        print(f"parse did not succeed: status={status} after {elapsed:.0f}s")
        return 1

    result = get_json(f"{SERVICE}/v1/result/{task_id}")
    document = result.get("document", {})
    json_content = document.get("json_content") or {}
    md_content = document.get("md_content") or ""

    pages = json_content.get("pages") or {}
    strings = collect_strings(json_content, [])
    corpus = "\n".join(strings)
    print(f"parsed in {elapsed:.0f}s: {len(pages)} page(s), {len(corpus)} characters, "
          f"{len(md_content)} characters of markdown")
    if not corpus.strip():
        print("the page came back empty, which is the failure this gate exists to catch")
        return 1

    failures = []
    for name, value, context_words in GRADED:
        # Markdown first, because a markdown table keeps a row on one line. The JSON tree is the
        # fallback for a parser that emitted structure without rendering a table.
        where = None
        for label, haystack in (("markdown", md_content), ("json", corpus)):
            lowered = haystack.lower()
            for position in _positions(lowered, value.lower()):
                window = lowered[max(0, position - ROW_WINDOW):position + ROW_WINDOW]
                if any(word in window for word in context_words):
                    where = label
                    break
            if where:
                break
        marker = "ok  " if where else "MISS"
        print(f"  {marker} {name}: {value!r} "
              f"{'beside its label in the ' + where if where else 'NOT found beside its label'}")
        if not where:
            failures.append(name)

    # The table itself, so the citation story has something to point at rather than prose.
    tables = json_content.get("tables") or []
    print(f"tables recovered: {len(tables)}")
    for index, table in enumerate(tables):
        cells = table.get("data", {}).get("num_rows"), table.get("data", {}).get("num_cols")
        print(f"  table {index}: {cells[0]} rows x {cells[1]} cols, "
              f"page {table.get('prov', [{}])[0].get('page_no', '?')}")

    print()
    if failures:
        print(f"GATE FAILED: {len(failures)} of {len(GRADED)} graded values are missing or misplaced: "
              f"{', '.join(failures)}")
        return 1
    print(f"GATE PASSED: all {len(GRADED)} graded values present and next to the right label")
    return 0


if __name__ == "__main__":
    sys.exit(main())
