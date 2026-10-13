#!/usr/bin/env python3
"""A damaged read caught, and a figure nobody verified disclosed when it is cited.

RET-PARSE-002's verify line has three parts and the third was proved in step 3.4b: a chunk read from a scanned
page is labelled as such while one from a digital page is not. This run checks all three against the deployed
store, so the part that already worked is not assumed to still work.

**The damaged figure is real rather than synthetic.** Step 3.4 carved defects into a cell whose truth was 145.00
and measured what came back, so the shapes used here are the shapes that measurement produced rather than ones
invented for a test.

Run from the repo root on the box:

    cd /opt/oi-core
    docker run --rm --network ai-net --user "$(id -u):$(id -g)" \\
      -v /opt/oi-core:/w -v /tmp:/probe:ro -w /w \\
      --entrypoint python oi-core/ingestion:local /tmp/figures_acceptance.py
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

sys.path.insert(0, "/w/services/retrieval")
sys.path.insert(0, "/w/services/ingestion")
sys.path.insert(0, "/w/services/eval")


def load_env(path: Path = Path("/w/rendered/env")) -> dict[str, str]:
    try:
        text = path.read_text()
    except PermissionError:
        print(f"cannot read {path}: mode 600 owned by the operator. Run as the file's owner.")
        return {}
    out: dict[str, str] = {}
    for line in text.splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, _, value = line.partition("=")
            out[key.strip()] = value.strip().strip('"').strip("'")
    return out


env = load_env()
if env and not os.environ.get("DATABASE_URL"):
    from urllib.parse import quote
    os.environ["DATABASE_URL"] = (
        f"postgresql://{quote(env['POSTGRES_USER'], safe='')}:"
        f"{quote(env['POSTGRES_PASSWORD'], safe='')}@postgres:5432/{env['POSTGRES_DB']}")

import figures  # noqa: E402
import ingest  # noqa: E402
import retriever  # noqa: E402

# The shapes step 3.4's forty trials produced when it carved defects into a cell whose truth was 145.00. The
# point of using measured shapes is that an invented one tests the regex against itself.
DAMAGED = ["2026-13-45", "02/31/2026", "1450%", "12345678901234.00", "12,34,567.00"]
INTACT = ["145.00", "1,250.00", "(1,450.00)", "145%", "32.50", "0.67"]


def main() -> int:
    services = ingest.Services()
    problems: list[str] = []

    print()
    print("=" * 96)
    print("PART ONE: a damaged figure is caught by a format or arithmetic check")
    print("=" * 96)
    print("  shapes a defective read produced in the step 3.4 measurement:")
    for token in DAMAGED:
        findings = figures.check_format(token)
        mark = "CAUGHT " if findings else "missed "
        codes = ", ".join(f.code for f in findings) or "-"
        print(f"    {mark}{token:<20}{codes}")
        if not findings:
            problems.append(f"{token} is one of the measured damaged shapes and no check caught it")

    print()
    print("  and figures that are not damaged, which must not be flagged:")
    for token in INTACT:
        findings = figures.check_format(token)
        mark = "ok    " if not findings else "FLAGGED"
        codes = ", ".join(f.code for f in findings) or "-"
        print(f"    {mark}{token:<20}{codes}")
        if findings:
            problems.append(f"{token} is a real figure and was flagged as {findings[0].code}, which is a check "
                            f"firing on a correct document")

    print()
    print("  arithmetic, where the document states the relationship:")
    rows = [["100.00"], ["45.00"]]
    for total, expected in (("145.00", True), ("155.00", False)):
        got = figures.check_arithmetic(rows, total)
        mark = "ok   " if got is expected else "WRONG"
        print(f"    {mark} 100.00 + 45.00 against a stated total of {total}: {got}")
        if got is not expected:
            problems.append(f"arithmetic on {total} returned {got} rather than {expected}")

    print()
    print("  and a document with nothing to add up returns None rather than False:")
    nothing = figures.check_arithmetic([["145.00"]], "145.00")
    print(f"    {nothing} - most documents have no relationship to check, and False would mark every figure in")
    print(f"    every prose document as unconfirmed for a reason that is not about the figure")
    if nothing is not None:
        problems.append("a document with no arithmetic relationship did not return None")

    print()
    print("=" * 96)
    print("PART THREE: a scanned page is labelled and a digital one is not")
    print("=" * 96)
    import psycopg2
    connection = psycopg2.connect(os.environ["DATABASE_URL"])
    with connection.cursor() as cur:
        cur.execute("""select doc_id, is_ocr, count(*) from chunk_index
                       group by doc_id, is_ocr order by doc_id, is_ocr""")
        rows_out = cur.fetchall()
    scanned = [r for r in rows_out if r[1] is True]
    digital = [r for r in rows_out if r[1] is False]
    for doc_id, is_ocr, count in rows_out:
        print(f"    is_ocr={str(is_ocr):<5} {count:>4} chunk(s)  {doc_id}")
    if not scanned:
        problems.append("no chunk is labelled as coming from a scanned page, so the label is not being set")
    if not digital:
        problems.append("no chunk is labelled digital, so the label cannot be told apart from a default")
    connection.close()

    print()
    print("=" * 96)
    print("PART TWO: a figure that passes every check is disclosed as machine-read when cited")
    print("=" * 96)
    result = retriever.search(services, "What is the standard labour rate per hour?", role="staff", limit=5)
    print(f"  retrieved {len(result.passages)} passage(s) for a question whose answer is a figure")
    disclosed = 0
    for rank, passage in enumerate(result.passages[:3], start=1):
        verdict = figures.assess("145.00")
        note = figures.disclosure_for(passage, verdict)
        state = "DISCLOSED" if note else "nothing to disclose"
        print(f"    [{rank}] is_ocr={str(passage.is_ocr):<5} {state:<20} {passage.doc_id} p{passage.page_start}")
        if note:
            disclosed += 1
            print(f"         {note}")
    if not any(getattr(p, "is_ocr", False) for p in result.passages):
        print("  no scanned passage was retrieved for this question, so the disclosure could not fire on the")
        print("  real path. The behaviour is checked directly below instead, and this is recorded rather than")
        print("  passed over.")

    # The behaviour checked against a passage built for it, so the mechanism is proved whether or not the
    # question happened to retrieve a scanned page.
    class Scanned:
        is_ocr = True
        doc_id = "company/irs-p15.pdf"
        page_start = 24

    unverified = figures.assess("145.00")
    note = figures.disclosure_for(Scanned(), unverified)
    print()
    print(f"  a scanned passage carrying an unverified figure: {note}")
    if not note:
        problems.append("an unverified figure from a scanned passage was not disclosed")
    confirmed = figures.assess("145.00", arithmetic_rows=[["100.00"], ["45.00"]], arithmetic_total="145.00")
    if figures.disclosure_for(Scanned(), confirmed) is not None:
        problems.append("a figure the document's own arithmetic confirms was still disclosed")
    else:
        print("  and one the document's own arithmetic confirms is not disclosed, because something did verify it")

    print()
    print("  the sentence that has to stay true: saying nothing is not a guarantee")
    print(f"    verified={unverified.verified} for a figure nothing confirmed, and it carries no caveat on a")
    print(f"    digital page because nothing recognised the text. The layer catches the malformed quarter of")
    print(f"    misreads and confirms what the document's arithmetic confirms, and everything else is one read.")

    print()
    if problems:
        print(f"FAILED with {len(problems)} problem(s):")
        for problem in problems:
            print(f"  - {problem}")
        return 1
    print("PASSED: the measured damaged shapes are caught and the intact figures are not, arithmetic confirms what")
    print("the document states, scanned and digital chunks are labelled apart, and an unverified figure from a")
    print("scanned page is disclosed when it is cited")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
