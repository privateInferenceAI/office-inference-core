"""When a defect changes the value, does confidence notice? Damage the cell at random and find out.

Why this shape rather than damaging one glyph at a time. The blur probe showed confidence tracking blur
and ignoring speckle, which is what you would expect of a mechanism that reports certainty about its
best guess rather than quality of the scan. What that leaves unresolved is the case that matters: a
defect that changes the reading. Does the confidence fall, or does OCR report the wrong value with the
confidence of a clean read?

The first two attempts at this damaged individual glyphs and both were measuring the wrong thing. The
first cropped the wrong region because the box was transcribed by hand. The second derived the box
properly but indexed glyphs from tesseract's word boxes, and with a single-line page segmentation
tesseract reports the whole number as one word, so there was nothing to index.

The question does not need per-glyph bookkeeping. Damage the cell at random, read it back, and sort each
trial by whether the reading changed and whether the confidence fell. Four buckets, and the one that
matters is the fourth.

    read same, confident          the defect was cosmetic, which is the common case
    read same, low confidence     a false alarm: the threshold would queue a page that was fine
    read changed, low confidence  the mechanism works, and review catches the bad number
    read changed, still confident a wrong number goes out unflagged, which confidence cannot fix

The trial count is small, so the result is a direction rather than a rate. Its job is to answer whether
the fourth bucket is empty, because an empty fourth bucket would mean confidence is sufficient and a
non-empty one means the review design needs a second signal.

Run it in the parser image, on ai-net so it can ask the parser where the cell is:

    docker run --rm --network ai-net -v /opt/oi-core/tests/fixtures:/f:ro \\
      --entrypoint python ghcr.io/docling-project/docling-serve-cpu:v1.36.0 /f/substitution_probe.py
"""
from __future__ import annotations

import json
import random
import subprocess
import sys
import tempfile
import urllib.request
from pathlib import Path

from PIL import Image, ImageDraw

try:
    import pypdfium2 as pdfium
except ImportError:
    pdfium = None

SOURCE = Path(sys.argv[1] if len(sys.argv) > 1 else "/f/fee-schedule-scanned.pdf")
DPI = int(sys.argv[2] if len(sys.argv) > 2 else 400)
TRIALS = int(sys.argv[3] if len(sys.argv) > 3 else 40)
SEED = 20261012
TRUTH = "145.00"
PARSER = "http://parser:5001"
LOW_CONFIDENCE = 60.0

# What "the reading changed" means for a rate cell. Punctuation and a gridline caught in the margin are
# not changes; the digits are.
def value_of(text: str) -> str:
    return "".join(character for character in text if character.isdigit())


def parse_document() -> dict:
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
        return (json.loads(response.read()).get("document") or {}).get("json_content") or {}


def find_cell(document: dict) -> dict:
    """The box of the cell whose text is TRUTH, straight from the parser rather than from a human."""
    for table in document.get("tables") or []:
        for cell in (table.get("data") or {}).get("table_cells") or []:
            if (cell.get("text") or "").strip() == TRUTH and cell.get("bbox"):
                return cell["bbox"]
    raise SystemExit(f"no cell in the parsed document reads {TRUTH!r}")


def render_page() -> Image.Image:
    if pdfium is None:
        raise SystemExit("this needs pypdfium2, which the parser image carries")
    pdf = pdfium.PdfDocument(str(SOURCE))
    return pdf[0].render(scale=DPI / 72).to_pil().convert("L")


def crop_cell(page: Image.Image, box: dict, inset_points: float = 1.5) -> tuple[Image.Image, tuple[int, int]]:
    """The cell as an image, and where it sits on the page.

    The vertical extent is the smaller and larger of `t` and `b` rather than trusting an orientation:
    two earlier versions got that wrong in opposite directions and both were silent, because a wrong
    crop is still a valid image. The cell is inset slightly so the table's gridlines stay out, which
    they otherwise do not: the clean read came back as '|145.00'.
    """
    scale = DPI / 72
    pad = inset_points * scale
    left = box["l"] * scale + pad
    right = box["r"] * scale - pad
    top = min(box["t"], box["b"]) * scale + pad
    bottom = max(box["t"], box["b"]) * scale - pad
    box_px = (int(left), int(top), int(right), int(bottom))
    return page.crop(box_px), (box_px[0], box_px[1])


def read_cell(image: Image.Image, tmp: Path) -> tuple[str, float | None]:
    path = tmp / "cell.png"
    image.save(path)
    result = subprocess.run(["tesseract", str(path), "stdout", "--psm", "7", "tsv"],
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
    text = " ".join(words)
    if not confidences:
        return text, None
    return text, sum(confidences) / len(confidences)


def damage(cell: Image.Image, rng: random.Random) -> str:
    """Erase or ink a small rectangle somewhere over the number, and name what was done.

    Two shapes, because they fail differently. A white bar erases part of a glyph, which is toner loss,
    a fold, or a staple. A black bar adds ink, which is a speck, a pen mark, or show-through from the
    other side of the sheet.
    """
    width, height = cell.size
    bar_width = max(2, int(width * rng.uniform(0.04, 0.12)))
    bar_height = max(2, int(height * rng.uniform(0.25, 0.6)))
    x = rng.randint(0, max(0, width - bar_width))
    y = rng.randint(max(0, int(height * 0.15)), max(1, int(height * 0.85) - bar_height))
    colour = 255 if rng.random() < 0.6 else 0
    ImageDraw.Draw(cell).rectangle([x, y, x + bar_width, y + bar_height], fill=colour)
    return f"{'white' if colour else 'black'} bar {bar_width}x{bar_height} at {x},{y}"


def main() -> int:
    document = parse_document()
    box = find_cell(document)
    print(f"cell {TRUTH!r} at l={box['l']:.1f} r={box['r']:.1f} t={box['t']:.1f} b={box['b']:.1f}")
    page = render_page()
    clean_cell, _ = crop_cell(page, box)

    with tempfile.TemporaryDirectory() as tmp_name:
        tmp = Path(tmp_name)
        clean_text, clean_confidence = read_cell(clean_cell, tmp)
    clean_value = value_of(clean_text)
    shown = "n/a" if clean_confidence is None else format(clean_confidence, ".1f")
    print(f"clean read: {clean_text!r} -> value {clean_value!r} at {shown} (truth {TRUTH!r})")
    if clean_value != value_of(TRUTH):
        print()
        print("The clean read is not the right value, so an experiment on damaged copies of it would")
        print("be measuring the crop rather than the damage. Fix the cell before reading any verdict.")
        return 1

    rng = random.Random(SEED)
    buckets = {"same-confident": 0, "same-low": 0, "changed-low": 0, "changed-confident": 0,
               "unreadable": 0}
    print()
    print(f"{TRIALS} trials, low-confidence line at {LOW_CONFIDENCE:.0f}")
    print(f"{'#':>3}  {'read':>10} {'value':>7} {'conf':>6}  what changed")
    with tempfile.TemporaryDirectory() as tmp_name:
        tmp = Path(tmp_name)
        for trial in range(1, TRIALS + 1):
            damaged = clean_cell.copy()
            what = damage(damaged, rng)
            text, confidence = read_cell(damaged, tmp)
            value = value_of(text)
            if confidence is None:
                bucket = "unreadable"
            elif value == clean_value:
                bucket = "same-confident" if confidence >= LOW_CONFIDENCE else "same-low"
            else:
                bucket = "changed-low" if confidence < LOW_CONFIDENCE else "changed-confident"
            buckets[bucket] += 1
            shown = "n/a" if confidence is None else format(confidence, ".0f")
            marker = ""
            if bucket == "changed-confident":
                marker = "  <-- WRONG, NOT FLAGGED"
            elif bucket == "changed-low":
                marker = "  <-- wrong, flagged"
            print(f"{trial:>3}  {text:>10} {value:>7} {shown:>6}  {what}{marker}")

    print()
    print(f"read unchanged, confident:      {buckets['same-confident']:>3}")
    print(f"read unchanged, low confidence: {buckets['same-low']:>3}   (false alarms)")
    print(f"read CHANGED, low confidence:   {buckets['changed-low']:>3}   (caught)")
    print(f"read CHANGED, still confident:  {buckets['changed-confident']:>3}   (missed)")
    print(f"unreadable:                     {buckets['unreadable']:>3}")
    print()
    if buckets["changed-confident"]:
        print("Confidence is not sufficient. A defect changed the value and OCR reported the change with")
        print("the confidence of a clean read, so a threshold would pass a wrong number through. The")
        print("review design needs a second signal for substitution, not only for blur.")
    else:
        print("Every changed reading came with a confidence drop in this sample, so a threshold would")
        print("have caught them. Small sample, so treat it as a direction rather than a rate.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
