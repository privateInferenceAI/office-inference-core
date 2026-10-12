"""Can OCR be confidently wrong? Damage one known digit and see what tesseract reads and how sure it is.

The blur probe showed confidence tracking blur and ignoring speckle, which is the shape of a mechanism
that reports how sure it is of its best guess rather than how good the scan is. That leaves the question
this script exists for: when a defect makes OCR read the wrong character, does the confidence drop, or
does it report the wrong answer with high confidence?

It takes the one number in the fixture whose correct value is known, 145.00, and for each digit makes a
controlled defect over part of that glyph: a thin horizontal bar, which is what toner loss, a fold or a
staple does to a digit. Then it reads that cell alone and reports the text and the confidence.

Three outcomes are possible and they mean different things:

  * the digit is read correctly at high confidence  - the defect was not enough to matter
  * the digit is read wrongly at LOW confidence     - the mechanism works, and review catches it
  * the digit is read wrongly at HIGH confidence    - the mechanism is blind to the case it exists for

The size of the third bucket is the number that decides whether a review queue can be built on OCR
confidence at all.

Run it in the parser image, which has both tesseract and the docling stack:

    docker run --rm -v /opt/oi-core/tests/fixtures:/f:ro \
      --entrypoint python ghcr.io/docling-project/docling-serve-cpu:v1.36.0 /f/substitution_probe.py
"""
from __future__ import annotations

import subprocess
import sys
import tempfile
from pathlib import Path

from PIL import Image, ImageDraw

try:
    import pypdfium2 as pdfium
except ImportError:
    pdfium = None

SOURCE = Path(sys.argv[1] if len(sys.argv) > 1 else "/f/fee-schedule-scanned.pdf")
DPI = int(sys.argv[2] if len(sys.argv) > 2 else 400)

# From the parse of this fixture: the "Standard labour, business hours" row of the left-hand table,
# whose Rate cell reads 145.00. PDF coordinates, BOTTOMLEFT origin, so converted below.
CELL = {"l": 237.0, "r": 297.0, "t": 668.0, "b": 652.0}
PAGE_HEIGHT = 792.0
TRUTH = "145.00"


def render_page() -> Image.Image:
    if pdfium is None:
        raise SystemExit("this needs pypdfium2, which the parser image carries")
    pdf = pdfium.PdfDocument(str(SOURCE))
    return pdf[0].render(scale=DPI / 72).to_pil().convert("L")


def crop_cell(page: Image.Image) -> Image.Image:
    """The cell in image coordinates, with a margin so tesseract has context."""
    scale = DPI / 72
    pad = 4 * scale
    left = CELL["l"] * scale - pad
    right = CELL["r"] * scale + pad
    top = (PAGE_HEIGHT - CELL["t"]) * scale - pad
    bottom = (PAGE_HEIGHT - CELL["b"]) * scale + pad
    return page.crop((int(left), int(top), int(right), int(bottom)))


def read_cell(image: Image.Image, tmp: Path) -> tuple[str, float | None]:
    """Tesseract on one cropped cell, single-line mode, returning its text and mean confidence."""
    path = tmp / "cell.png"
    image.save(path)
    result = subprocess.run(
        ["tesseract", str(path), "stdout", "--psm", "7", "tsv"],
        capture_output=True, text=True)
    words, confidences = [], []
    for row in result.stdout.splitlines()[1:]:
        parts = row.split("\t")
        if len(parts) < 12 or parts[11].strip() == "":
            continue
        try:
            confidence = float(parts[10])
        except ValueError:
            continue
        if confidence >= 0:
            words.append(parts[11])
            confidences.append(confidence)
    text = "".join(words)
    if not confidences:
        return text, None
    return text, sum(confidences) / len(confidences)


def glyph_boxes(cell: Image.Image) -> list[tuple[int, int, int, int]]:
    """Where tesseract sees each character in the clean cell, so a defect lands on a real glyph."""
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "cell.png"
        cell.save(path)
        result = subprocess.run(
            ["tesseract", str(path), "stdout", "--psm", "7", "tsv"],
            capture_output=True, text=True)
    boxes = []
    for row in result.stdout.splitlines()[1:]:
        parts = row.split("\t")
        if len(parts) < 12 or not parts[11].strip():
            continue
        try:
            if int(parts[0]) != 5:
                continue
        except ValueError:
            continue
        boxes.append((int(parts[6]), int(parts[7]), int(parts[8]), int(parts[9])))
    return boxes


def main() -> int:
    page = render_page()
    clean_cell = crop_cell(page)
    with tempfile.TemporaryDirectory() as tmp_name:
        tmp = Path(tmp_name)
        clean_text, clean_conf = read_cell(clean_cell, tmp)
    print(f"cell as read clean: {clean_text!r} at "
          f"{'n/a' if clean_conf is None else format(clean_conf, '.1f')} (truth {TRUTH!r})")
    boxes = glyph_boxes(clean_cell)
    print(f"glyph boxes found: {len(boxes)}")
    if len(boxes) < len(TRUTH):
        print("tesseract did not separate the digits, so this cannot be run per digit")
        return 1

    print()
    print(f"{'digit':>5} {'truth':>6} {'read':>8} {'conf':>6}  verdict")
    outcomes = {"correct": 0, "wrong-low": 0, "wrong-high": 0, "unreadable": 0}
    with tempfile.TemporaryDirectory() as tmp_name:
        tmp = Path(tmp_name)
        for index, box in enumerate(boxes[: len(TRUTH)]):
            x, y, width, height = box
            damaged = clean_cell.copy()
            draw = ImageDraw.Draw(damaged)
            # A horizontal bar across the middle third of the glyph, the shape toner loss leaves.
            bar_top = y + int(height * 0.45)
            draw.rectangle([x, bar_top, x + width, bar_top + max(2, height // 12)], fill=255)
            text, confidence = read_cell(damaged, tmp)
            truth = TRUTH[index]
            read = text[index] if index < len(text) else "?"
            shown = "n/a" if confidence is None else format(confidence, ".1f")
            if confidence is None:
                verdict = "unreadable"
            elif read == truth:
                verdict = "correct"
            elif confidence < 60:
                verdict = "wrong, low confidence"
            else:
                verdict = "WRONG, HIGH CONFIDENCE"
            outcomes[{"correct": "correct", "wrong, low confidence": "wrong-low",
                      "WRONG, HIGH CONFIDENCE": "wrong-high",
                      "unreadable": "unreadable"}[verdict]] += 1
            print(f"{index:>5} {truth:>6} {read:>8} {shown:>6}  {verdict}")

    print()
    print(f"read correctly: {outcomes['correct']}   "
          f"wrong and flagged: {outcomes['wrong-low']}   "
          f"WRONG AND NOT FLAGGED: {outcomes['wrong-high']}   "
          f"unreadable: {outcomes['unreadable']}")
    if outcomes["wrong-high"]:
        print()
        print("The mechanism is blind to at least some substitutions: a number was read wrongly and")
        print("reported with the confidence of a clean read. That is the failure the review design")
        print("exists to catch, and confidence alone will not catch it.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
