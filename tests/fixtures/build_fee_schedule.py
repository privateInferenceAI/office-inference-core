#!/usr/bin/env python3
"""Build the scanned, multi-column fee schedule that C2's parsing gate is measured against.

Why this exists: `RET-PARSE-001` verifies that "a scanned fee-schedule fixture returns correct numbers
in a table, and a scanned page returns text rather than nothing". That needs a document to verify
against, and the fixture corpus is a C2 deliverable rather than an input we can download.

Why it is authored rather than taken from the Tier 1 archive. `docs/evaluation-corpora.md` says a
question set over small-business paperwork has to be ours, and the same argument applies to the
document. A fee schedule built here can put its numbers exactly where the failure mode lives: a table
in the second column, split across a page break, with a bare price list in dense prose on the left for
the reading order to get wrong. Nothing in the public archive is shaped to trap that.

It is fictional. The vendor, the dates and the amounts belong to no real client, and the seven values
the grader checks are listed in `EXPECTED` below so the question and the answer cannot drift apart.

The output is rasterised on purpose. A text-layer PDF would let a parser succeed by copying the
characters out, which proves nothing about a scan. Every page is rendered to an image and the images
are written back as a PDF, so the only way to read this document is OCR.

Usage:
    python3 tests/fixtures/build_fee_schedule.py            # writes fee-schedule-scanned.pdf
    python3 tests/fixtures/build_fee_schedule.py --dpi 300  # smaller text, harder OCR

Requires reportlab, Pillow and poppler-utils (`pdftoppm`), none of which are parts of the stack: this
script is run by hand and its output is committed as a fixture.
"""
from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from PIL import Image

from reportlab.lib import colors
from reportlab.lib.pagesizes import LETTER
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import inch
from reportlab.platypus import (BaseDocTemplate, Frame, PageTemplate, Paragraph, Spacer, Table,
                                TableStyle)

HERE = Path(__file__).resolve().parent
OUT = HERE / "fee-schedule-scanned.pdf"

TITLE = "Meridian Facilities Services"
SUBTITLE = "Schedule of Fees and Reimbursable Rates, effective 1 July 2026"

# The values the gate checks. Seven of them, spread across prose and table, so that a parser which
# reads the page as a token stream gets the right numbers in the wrong places and is caught.
EXPECTED = {
    "mileage_per_mile_cents": 67,
    "standard_hourly_rate": 145.00,
    "after_hours_multiplier": 1.5,
    "callout_fee": 89.00,
    "filter_media_per_unit": 32.50,
    "refrigerant_per_pound": 18.75,
    "emergency_attendance_fee": 240.00,
}

LEFT_PROSE = [
    ("1. Scope and interpretation", [
        "This schedule sets out the fees payable for services performed under the master services "
        "agreement between Meridian Facilities Services and the client. Where a fee in this schedule "
        "conflicts with a fee quoted in a work order, the work order governs, provided the work order "
        "was signed by an authorised representative of both parties before the work began.",
        "Rates are stated in United States dollars and exclude applicable sales and use taxes, which "
        "are added at invoice. Travel time within the metropolitan service area is not billable. "
        "Travel beyond forty miles from the service depot is billed at the standard hourly rate, and "
        "the mileage rate in the table opposite applies only to vehicles owned or leased by the "
        "client and operated by our technicians at the client's written request.",
    ]),
    ("2. Scheduling and attendance", [
        "Work requested outside the hours of 07:00 to 17:00 on a business day is billed at the "
        "after-hours multiplier shown in the table. The multiplier applies to labour only and does "
        "not apply to parts, media or consumables, which are billed at their listed rates whatever "
        "the hour.",
        "An emergency attendance fee is payable where a technician is dispatched at the client's "
        "request outside business hours and the client is the party that classified the work as an "
        "emergency. The fee is not payable where the classification was ours, and it is credited in "
        "full against labour billed on the same visit.",
    ]),
    ("3. Parts, media and consumables", [
        "Parts are billed at cost plus fifteen percent, with the exception of filter media and "
        "refrigerant, which are billed at the fixed unit rates in the table. Consumables under five "
        "dollars per item are not itemised and are absorbed into the applicable labour rate.",
        "Refrigerant is billed per pound recovered or charged, rounded up to the nearest whole pound, "
        "and is subject to the environmental handling charge required by federal regulation. That "
        "charge is passed through at cost with no markup.",
    ]),
    ("4. Invoicing and payment", [
        "Invoices are issued on the first business day after the end of each calendar month and are "
        "payable within thirty days of the invoice date. Amounts outstanding beyond sixty days "
        "attract interest at one percent per month or the maximum permitted by law, whichever is "
        "lower.",
        "The client may dispute an invoice in writing within twenty days of receipt, stating the "
        "amount in dispute and the grounds. Undisputed amounts remain payable on the original terms "
        "and a dispute does not suspend them.",
    ]),
]

TABLE_ROWS = [
    ("Mileage, client-owned vehicle", "per mile", "0.67"),
    ("Standard labour, business hours", "per hour", "145.00"),
    ("After-hours multiplier", "of labour", "1.5x"),
    ("Emergency attendance", "per callout", "240.00"),
    ("Callout fee, scheduled visit", "per visit", "89.00"),
    ("Filter media, standard", "per unit", "32.50"),
    ("Refrigerant, R-410A", "per pound", "18.75"),
]


def styles() -> dict:
    return {
        "title": ParagraphStyle("title", fontName="Helvetica-Bold", fontSize=15, leading=18),
        "subtitle": ParagraphStyle("subtitle", fontName="Helvetica", fontSize=9.5, leading=12),
        "heading": ParagraphStyle("heading", fontName="Helvetica-Bold", fontSize=10, leading=12,
                                  spaceBefore=6, spaceAfter=3),
        "body": ParagraphStyle("body", fontName="Helvetica", fontSize=8.6, leading=11.2,
                               alignment=4),  # justified, as real schedules are
    }


def build_source(path: Path) -> None:
    style = styles()
    doc = BaseDocTemplate(str(path), pagesize=LETTER,
                          leftMargin=0.75 * inch, rightMargin=0.75 * inch,
                          topMargin=0.7 * inch, bottomMargin=0.7 * inch,
                          title="Schedule of Fees", author="Meridian Facilities Services")

    width, height = LETTER
    frame_w = width - 1.5 * inch
    frame_h = height - 1.4 * inch
    gutter = 0.28 * inch
    col_w = (frame_w - gutter) / 2

    # Two frames on each page, in reading order: left column then right column. This is the layout
    # the parser has to recover, and the reason a plain text extraction gets the order wrong.
    doc.addPageTemplates([PageTemplate(
        id="two-column",
        frames=[
            Frame(0.75 * inch, 0.7 * inch, col_w, frame_h, id="left",
                  leftPadding=0, rightPadding=0, topPadding=0, bottomPadding=0),
            Frame(0.75 * inch + col_w + gutter, 0.7 * inch, col_w, frame_h, id="right",
                  leftPadding=0, rightPadding=0, topPadding=0, bottomPadding=0),
        ],
    )])

    story = [
        Paragraph(TITLE, style["title"]),
        Paragraph(SUBTITLE, style["subtitle"]),
        Spacer(1, 8),
    ]
    for heading, paragraphs in LEFT_PROSE:
        story.append(Paragraph(heading, style["heading"]))
        for text in paragraphs:
            story.append(Paragraph(text, style["body"]))

    # The table sits in the right-hand column and is longer than the remaining column height, which
    # splits it into two blocks in the parsed output: three rows at the foot of the left column's flow
    # and four more in the right column. So one logical fee schedule arrives as two tables, which is
    # the case the chunker has to join rather than treat as two sources.
    #
    # This fixture is one page. An earlier comment here claimed the table ran onto page 2, which it
    # does not: the rasterised output is a single page, so the split is column-wise rather than across
    # a page break. A page-straddling table is a different fixture and a different case, and it is not
    # yet covered.
    story.append(Spacer(1, 6))
    story.append(Paragraph("Schedule of rates", style["heading"]))
    table = [[Paragraph(f"<b>{heading}</b>", style["body"]) for heading in ("Item", "Basis", "Rate")]]
    for item, basis, rate in TABLE_ROWS:
        table.append([Paragraph(item, style["body"]), Paragraph(basis, style["body"]),
                      Paragraph(rate, style["body"])])
    story.append(Table(table,
                       colWidths=[col_w * 0.52, col_w * 0.24, col_w * 0.24],
                       style=TableStyle([
                           ("GRID", (0, 0), (-1, -1), 0.4, colors.black),
                           ("BACKGROUND", (0, 0), (-1, 0), colors.Color(0.9, 0.9, 0.9)),
                           ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                           ("LEFTPADDING", (0, 0), (-1, -1), 3),
                           ("RIGHTPADDING", (0, 0), (-1, -1), 3),
                           ("TOPPADDING", (0, 0), (-1, -1), 2),
                           ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
                       ])))
    story.append(Spacer(1, 6))
    story.append(Paragraph(
        "Rates in this schedule are reviewed each January. The rates shown take effect on the date at "
        "the head of this page and remain in force until superseded in writing.", style["body"]))

    doc.build(story)


def rasterise(source: Path, out: Path, dpi: int) -> int:
    """Render every page to an image and write the images back as a PDF, removing the text layer.

    Pillow does the reassembly rather than `img2pdf`, one less tool to install on a box that will run
    this by hand. Grayscale PNG at `dpi`, which is the shape a desktop scanner produces.
    """
    with tempfile.TemporaryDirectory() as tmp:
        subprocess.run(["pdftoppm", "-r", str(dpi), "-gray", "-png", str(source), f"{tmp}/page"],
                       check=True)
        pages = sorted(Path(tmp).glob("page-*.png"))
        if not pages:
            raise SystemExit("pdftoppm produced no pages")
        images = [Image.open(page).convert("L") for page in pages]
        images[0].save(out, "PDF", resolution=dpi, save_all=True, append_images=images[1:])
        return len(images)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--dpi", type=int, default=200, help="rasterisation resolution")
    parser.add_argument("--out", type=Path, default=OUT)
    args = parser.parse_args(argv)

    if shutil.which("pdftoppm") is None:
        print("missing pdftoppm. On Debian or Ubuntu: apt install poppler-utils")
        return 1

    with tempfile.TemporaryDirectory() as tmp:
        source = Path(tmp) / "source.pdf"
        build_source(source)
        page_count = rasterise(source, args.out, args.dpi)

    extracted = subprocess.run(["pdftotext", str(args.out), "-"], capture_output=True, text=True)
    characters = len(extracted.stdout.strip())
    print(f"wrote {args.out} ({args.out.stat().st_size // 1024} KB, {page_count} pages, "
          f"{args.dpi} dpi)")
    print(f"extractable characters in the result: {characters}")
    if characters > 0:
        print("this document still has a text layer, so it does not test OCR")
        return 1
    print("no text layer: reading this document requires OCR, which is the point")
    print("values the grader checks:")
    for key, value in EXPECTED.items():
        print(f"  {key}: {value}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
