#!/usr/bin/env python3
"""One file of each format RET-FORMAT-001 names, built rather than downloaded.

The corpus is public filings and tax publications, which contain no spreadsheet, no slide deck and no email
message. So the three formats that requirement names have never been through the pipeline, and a format that has
never been through it is a format whose handling is an assumption.

Each fixture carries one fact that appears nowhere else in the corpus, so "is it answerable" is a question about
this file rather than about a near-match in another one. The facts are the kind a small business actually keeps
in these formats: a rate in a spreadsheet, a decision in a deck, an approval in an email.

Run: python3 tests/fixtures/build_format_fixtures.py
"""
from __future__ import annotations

import email.message
from pathlib import Path

OUT = Path(__file__).resolve().parents[2] / "fixtures" / "corpus" / "formats"
OUT.mkdir(parents=True, exist_ok=True)

# A figure that appears in no other document in the corpus, so a passage carrying it can only have come from
# the spreadsheet. 8,475.50 is not a rate in any of the other fixtures.
XLSX_RATE = "8,475.50"


def build_xlsx() -> Path:
    """A rate card, with the rate in a cell rather than in prose.

    This is the case the architecture's whole-table rule is about: the value lives in one cell and its meaning
    lives in the row and column headers, so a chunker that keeps only the cell's text produces a number with no
    unit and a citation that cannot be checked.
    """
    from openpyxl import Workbook
    book = Workbook()
    sheet = book.active
    sheet.title = "Rate card"
    sheet.append(["Meridian Facilities", "Rate card 2026"])
    sheet.append([])
    sheet.append(["Service", "Unit", "Rate", "Notes"])
    sheet.append(["Emergency callout", "per visit", XLSX_RATE, "Outside contracted hours"])
    sheet.append(["Planned maintenance", "per visit", "1,240.00", "Scheduled a week ahead"])
    sheet.append(["Filter media", "per unit", "32.50", "Standard grade"])
    sheet.append([])
    sheet.append(["Approved by", "R. Okafor", "2026-01-15"])
    path = OUT / "rate-card-2026.xlsx"
    book.save(path)
    return path


def build_pptx() -> Path:
    """A decision deck, with the decision in the body of one slide.

    Slides are mostly titles with a few lines, which is the shape that produces chunks too small to retrieve
    well and headings that look like content. The fact sits in a bullet, which is where a slide's content
    actually lives.
    """
    from pptx import Presentation
    from pptx.util import Inches
    deck = Presentation()
    blank = deck.slide_layouts[6]

    def slide(title: str, lines: list[str]) -> None:
        page = deck.slides.add_slide(blank)
        box = page.shapes.add_textbox(Inches(0.6), Inches(0.5), Inches(9), Inches(1))
        box.text_frame.text = title
        body = page.shapes.add_textbox(Inches(0.6), Inches(1.6), Inches(9), Inches(4))
        frame = body.text_frame
        for index, line in enumerate(lines):
            frame.paragraphs[0].text = line if index == 0 else frame.paragraphs[0].text
            if index:
                frame.add_paragraph().text = line

    slide("Dispatch review, Q1 2026", [
        "Callouts fell 12 percent against the same quarter last year.",
        "The dispatch board is rebuilt before the next season.",
        "Vendor threshold raised to 2,500 dollars without a second quote.",
        "Owner: R. Okafor. Next review in July.",
    ])
    slide("Notes", ["Figures are provisional and exclude the Reading contract."])
    path = OUT / "dispatch-review-q1.pptx"
    deck.save(path)
    return path


def build_eml() -> Path:
    """A message approving a rate, with the approval in the body and the signature in the headers.

    Email is the format where the metadata is content: who sent it, when, and who else saw it are facts a
    business relies on, and a parser that keeps only the body loses every one of them. The headers are written
    into the body of this fixture deliberately, because the pipeline reads a file and not a mailbox, so a
    message whose To and From matter has to carry them where a file reader will find them.
    """
    message = email.message.EmailMessage()
    message["From"] = "R. Okafor <r.okafor@meridian.example>"
    message["To"] = "Accounts <accounts@meridian.example>"
    message["Cc"] = "A. Stachowitz <anthony@meridian.example>"
    message["Subject"] = "Approved: emergency callout rate for 2026"
    message["Date"] = "Thu, 15 Jan 2026 09:12:00 -0500"
    message["Message-ID"] = "<20260115-callout-rate@meridian.example>"
    message.set_content(
        "Accounts,\n"
        "\n"
        "The emergency callout rate for 2026 is approved at 8,475.50 per visit, which is the figure in the\n"
        "rate card. This replaces the 2025 arrangement and applies from the first of February.\n"
        "\n"
        "Two conditions. It applies outside contracted hours only, and the dispatch board has to record the\n"
        "callout reason against every one, because we are reviewing them in July.\n"
        "\n"
        "R. Okafor\n"
        "Operations\n")
    path = OUT / "callout-rate-approval.eml"
    path.write_bytes(message.as_bytes())
    return path


def main() -> int:
    for builder in (build_xlsx, build_pptx, build_eml):
        path = builder()
        print(f"  {path.name:<32} {path.stat().st_size:>7} bytes")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
