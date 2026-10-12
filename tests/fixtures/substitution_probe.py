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

    docker run --rm --network ai-net -v /opt/oi-core/tests/fixtures:/f:ro \
      --entrypoint python ghcr.io/docling-project/docling-serve-cpu:v1.36.0 /f/substitution_probe.py

The network flag is required: the cell's bounding box is read from the running parser rather than
transcribed by hand, which is the mistake that made the first version read the wrong region.
"""
from __future__ import annotations

import json
import subprocess
import urllib.request
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

# The value whose cell this script damages. It is found in the parsed output rather than transcribed,
# because transcribing it by eye from a probe log is exactly how the first version of this script
# cropped the wrong region and read "Onbeeen" where the truth was 145.00.
TRUTH = "145.00"
PARSER = "http://parser:5001"


def render_page() -> Image.Image:
    if pdfium is None:
        raise SystemExit("this needs pypdfium2, which the parser image carries")
    pdf = pdfium.PdfDocument(str(SOURCE))
    return pdf[0].render(scale=DPI / 72).to_pil().convert("L")


def parse_document() -> dict:
    """The parser's structured output, so the cell's box comes from the parser rather than from me."""
    boundary = "----subprobe"
    body = bytearray()

    def part(headers: list[str], content: bytes) -> None:
        body.extend(f"--{boundary}\r\n".encode())
        for header in headers:
            body.extend(f"{header}\r\n".encode())
        body.extend(b"\r\n")
        body.extend(content)
        body.extend(b"\r\n")

    part(['Content-Disposition: form-data; name="files"; filename="f.pdf"',
          "Content-Type: application/pdf"], SOURCE.read_bytes())
    part(['Content-Disposition: form-data; name="to_formats"'], b"json")
    body.extend(f"--{boundary}--\r\n".encode())
    request = urllib.request.Request(
        PARSER + "/v1/convert/file", data=bytes(body),
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}"})
    with urllib.request.urlopen(request, timeout=900) as response:
        payload = json.loads(response.read())
    return (payload.get("document") or {}).get("json_content") or {}


def find_cell(document: dict) -> tuple[dict, float]:
    """The box of the cell whose text is TRUTH, in PDF coordinates, plus the page height."""
    pages = document.get("pages") or {}
    heights = [page.get("size", {}).get("height") for page in pages.values()]
    page_height = heights[0] if heights and heights[0] else 792.0
    for table in document.get("tables") or []:
        for cell in (table.get("data") or {}).get("table_cells") or []:
            if (cell.get("text") or "").strip() == TRUTH:
                box = cell.get("bbox") or {}
                if box:
                    return box, float(page_height)
    raise SystemExit(f"no cell in the parsed document reads {TRUTH!r}, so there is nothing to damage")


def crop_box(page: Image.Image, box: dict, pad_points: float = 6.0) -> Image.Image:
    """A box from the parser as an image crop.

    The vertical extent is taken as the smaller and larger of `t` and `b` rather than trusted to mean
    a particular orientation. Two attempts got this wrong in opposite directions. The first hardcoded
    a flipped box and cropped a paragraph. The second flipped the parser's numbers on the theory that
    `coord_origin: BOTTOMLEFT` meant they counted up from the page bottom, and inverted a range that
    was already correct: the failing crop reported t=696.4 b=709.1, and 792 minus those is 95.6 to
    82.9 from the top, which is where the rate cell actually sits.

    Both mistakes are silent, because a wrong crop is still a valid image. Taking the extremes cannot
    be wrong in that way, and it removes the need for the page height, which the first version took as
    an argument and used to do the flipping that caused the second mistake.
    """
    scale = DPI / 72
    pad = pad_points * scale
    left = box["l"] * scale - pad
    right = box["r"] * scale + pad
    top = min(box["t"], box["b"]) * scale - pad
    bottom = max(box["t"], box["b"]) * scale + pad
    width, height = page.size
    return page.crop((int(max(0, left)), int(max(0, top)),
                      int(min(width, right)), int(min(height, bottom))))


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
    document = parse_document()
    box, page_height = find_cell(document)
    print(f"cell {TRUTH!r} found at l={box['l']:.1f} r={box['r']:.1f} "
          f"t={box['t']:.1f} b={box['b']:.1f}, page height {page_height:.0f}")
    page = render_page()
    clean_cell = crop_box(page, box)
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
