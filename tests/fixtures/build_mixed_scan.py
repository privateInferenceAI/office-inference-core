#!/usr/bin/env python3
"""Author a small fixture that mixes scanned and digital pages, so per-page OCR can be tested in CI.

Why a new fixture. The existing scanned fixture is one page, and a one-page scan cannot test the thing that
matters: whether the pipeline can tell WHICH pages were read off an image. A document that is wholly scanned
and a document that is wholly digital are both labelled correctly by a document-level answer, and the
failure only appears in a mixture.

How it is built, and why this way. PIL renders text to an image and pypdfium2 puts that image in a PDF with
no text layer, which is what a real scan is. Rendering the text through an image is what makes the page
genuinely textless: writing text with a PDF library would embed a font and the parser would read it as
digital. Each page prints its own page number in large type, so a chunk's citation can be checked against
the page it came from by eye as well as by assertion.

The fixture is deliberately small. The real document this was proved on is 59 pages and takes nine minutes
to convert, which is too slow to run on every change.

Run: python3 tests/fixtures/build_mixed_scan.py
Output: tests/fixtures/mixed-scan.pdf, four pages, pages 2 and 4 scanned.
"""
from __future__ import annotations

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
TARGET = HERE / "mixed-scan.pdf"

# Which pages are scans. Page 1 digital, page 2 scan, page 3 digital, page 4 scan: an interleaving rather
# than a block, so a fixture that mistakenly marks a range fails.
SCANNED_PAGES = (2, 4)
TOTAL_PAGES = 4

BODY = (
    "MERIDIAN FACILITIES SERVICES\n"
    "Schedule of Fees and Reimbursable Rates\n\n"
    "Standard labour, business hours .......... 145.00 per hour\n"
    "After-hours multiplier ................... 1.5x of labour\n"
    "Emergency attendance ..................... 240.00 per callout\n"
    "Mileage, client-owned vehicle ............ 0.67 per mile\n"
    "Filter media, standard ................... 32.50 per unit\n"
    "Refrigerant, R-410A ...................... 18.75 per pound\n\n"
    "Rates are stated in United States dollars and exclude applicable taxes.\n"
)


def extract_text_per_page() -> list[str]:
    """The text layer of each page, read back from the file just written."""
    from pypdf import PdfReader

    reader = PdfReader(str(TARGET))
    return [(page.extract_text() or "") for page in reader.pages]


def render_page(page_number: int, scanned: bool) -> bytes:
    """One page as PNG bytes. The scanned variant is degraded the way a fax degrades."""
    from PIL import Image, ImageDraw, ImageFilter, ImageFont

    width, height = 1275, 1650            # 150 dpi at US Letter, which is a plausible scan resolution
    image = Image.new("RGB", (width, height), "white")
    draw = ImageDraw.Draw(image)
    try:
        body_font = ImageFont.truetype("DejaVuSansMono.ttf", 26)
        head_font = ImageFont.truetype("DejaVuSansMono-Bold.ttf", 34)
    except OSError:
        body_font = ImageFont.load_default()
        head_font = body_font

    draw.text((90, 120), f"PAGE {page_number} OF {TOTAL_PAGES}", fill="black", font=head_font)
    y = 200
    for line in BODY.splitlines():
        draw.text((90, y), line, fill="black", font=body_font)
        y += 44
    # A figure that appears only on this page, so a citation can be traced to it.
    draw.text((90, y + 40), f"Page {page_number} reference: RATE-{page_number}00", fill="black",
              font=body_font)

    if scanned:
        # What a scan does to a page: a small rotation, softening, and speckle. Without this the page is a
        # clean render that OCR reads perfectly and the confidence signal does not separate from digital.
        image = image.rotate(0.6, expand=False, fillcolor="white")
        image = image.filter(ImageFilter.GaussianBlur(0.8))
        pixels = image.load()
        for x in range(0, width, 7):
            for y in range(0, height, 11):
                if (x * y) % 13 == 0:
                    pixels[x, y] = (120, 120, 120)

    import io
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


def main() -> int:
    """Four pages, two of them scans, written with reportlab.

    The scanned pages are an image and nothing else, which is what a scan is to a parser: no font, no text
    layer, nothing to extract. The digital pages are drawn as text with a Courier base font, so the parser
    finds a text layer and does not run OCR on them. Getting that difference right is the whole point of the
    fixture, so it is asserted below rather than assumed.
    """
    import io

    from PIL import Image
    from reportlab.lib.pagesizes import letter
    from reportlab.pdfgen import canvas

    page_width, page_height = letter
    pdf = canvas.Canvas(str(TARGET), pagesize=letter)
    for page_number in range(1, TOTAL_PAGES + 1):
        if page_number in SCANNED_PAGES:
            image = Image.open(io.BytesIO(render_page(page_number, scanned=True)))
            pdf.drawInlineImage(image, 0, 0, width=page_width, height=page_height)
        else:
            pdf.setFont("Courier-Bold", 14)
            pdf.drawString(60, page_height - 70, f"PAGE {page_number} OF {TOTAL_PAGES}")
            pdf.setFont("Courier", 10)
            y = page_height - 110
            for line in BODY.splitlines():
                pdf.drawString(60, y, line)
                y -= 16
            pdf.drawString(60, y - 24, f"Page {page_number} reference: RATE-{page_number}00")
        pdf.showPage()
    pdf.save()

    # The fixture is only useful if the scanned pages really have no text and the digital ones do. Both
    # halves are checked here, because a fixture that silently became all-digital would make the per-page
    # test pass while testing nothing.
    text_per_page = extract_text_per_page()
    problems = []
    for page_number in range(1, TOTAL_PAGES + 1):
        present = bool(text_per_page[page_number - 1].strip())
        should = page_number not in SCANNED_PAGES
        if present != should:
            problems.append(
                f"page {page_number}: text layer is {'present' if present else 'absent'} but should be "
                f"{'present' if should else 'absent'}")

    print(f"wrote {TARGET.name}: {TOTAL_PAGES} pages, {TARGET.stat().st_size} bytes")
    print(f"  digital pages: {[p for p in range(1, TOTAL_PAGES + 1) if p not in SCANNED_PAGES]}")
    print(f"  scanned pages: {list(SCANNED_PAGES)}")
    if problems:
        print()
        print("the fixture does not have the shape it claims:")
        for problem in problems:
            print(f"  - {problem}")
        return 1
    print()
    print("text layer checked: present on the digital pages, absent on the scanned ones")
    print("The parser should report an ocr_score for the scanned pages and null for the others.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
