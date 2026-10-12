#!/usr/bin/env python3
"""Do two independent OCR engines disagree usefully, or do they agree on the wrong answer?

The decision this serves is recorded in `docs/search-architecture.md` under "An uncertain read". That
design puts a second independent reading of any cited figure in the controls, as an escalation gate
rather than a vote. The evidence for it is thin in one specific place: the only measurement found of
two-engine disagreement came from a thesis on synthetic Telugu characters, where 26.8 percent of
disagreements were both-wrong and the ensemble recovered under 57 percent of them. The equivalent number
for damaged printed English digits does not exist, and this script is how this system finds its own.

Three questions, and the third decides the design.

  1. On a CLEAN cell, how often do the two engines disagree? This is the false-alarm rate. If two
     engines disagree on a clean scan often, then disagreement is noise and cannot gate anything.
  2. On a DAMAGED cell, how often do they disagree? This is the recall of the signal, at least in
     principle: a disagreement is what would send a figure to a person.
  3. On a DAMAGED cell, how often do they AGREE AND BOTH BE WRONG? This is the failure that matters. If
     it is high, a second reading buys nothing, because the two readers fail together.

The engines are genuinely different implementations, which is the point: Tesseract's LSTM and
PaddleOCR's CTC recogniser, different training, different preprocessing. What they share is the crop
this script hands them, and that sharing is a known limit rather than a hidden one: a defect that
destroys the pixels destroys them for both. Varying the staging as well as the engine is the mitigation
the research recommends, and the honest way to test it is to run this twice, once with both engines on
the same crop and once with each engine given its own preprocessing.

Tesseract runs in this container, which carries it. The second engine is the parser service's own
RapidOCR, reached over HTTP by uploading the crop as an image, so nothing new is installed and the
second reader is the one the production pipeline actually uses.

Run it from the parser image on ai-net:

    docker run --rm --network ai-net -v /opt/oi-core/tests/fixtures:/f:ro \\
      --entrypoint python ghcr.io/docling-project/docling-serve-cpu:v1.36.0 /f/disagreement_probe.py
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


def value_of(text: str) -> str:
    """The digits of a reading. Punctuation and a gridline in the margin are not the value."""
    return "".join(character for character in text if character.isdigit())


def parse_document() -> dict:
    boundary = "----disagree"
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


def crop_cell(page: Image.Image, box: dict, inset_points: float = 1.5) -> Image.Image:
    """The cell as an image. The vertical extent takes the smaller and larger of t and b, because two
    earlier versions guessed an orientation and both were wrong in opposite directions."""
    scale = DPI / 72
    pad = inset_points * scale
    left = box["l"] * scale + pad
    right = box["r"] * scale - pad
    top = min(box["t"], box["b"]) * scale + pad
    bottom = max(box["t"], box["b"]) * scale - pad
    return page.crop((int(left), int(top), int(right), int(bottom)))


def read_with_tesseract(cell: Image.Image, tmp: Path) -> str:
    path = tmp / "t.png"
    cell.save(path)
    result = subprocess.run(["tesseract", str(path), "stdout", "--psm", "7"],
                            capture_output=True, text=True)
    return result.stdout.strip()


def read_with_rapidocr(cell: Image.Image, tmp: Path) -> str:
    """The parser's own OCR, reached by uploading the crop as an image.

    `force_ocr` because the crop is an image and there is no text layer to prefer, and the markdown
    output because the reading is all this needs.
    """
    path = tmp / "r.png"
    cell.save(path)
    boundary = "----cell"
    body = bytearray()

    def part(headers: list[str], content: bytes) -> None:
        body.extend(f"--{boundary}\r\n".encode())
        for header in headers:
            body.extend(f"{header}\r\n".encode())
        body.extend(b"\r\n")
        body.extend(content)
        body.extend(b"\r\n")

    part(['Content-Disposition: form-data; name="files"; filename="cell.png"',
          "Content-Type: image/png"], path.read_bytes())
    for key, value in (("to_formats", "md"), ("force_ocr", "true"), ("do_ocr", "true"),
                       ("image_export_mode", "placeholder")):
        part([f'Content-Disposition: form-data; name="{key}"'], value.encode())
    body.extend(f"--{boundary}--\r\n".encode())
    request = urllib.request.Request(
        PARSER + "/v1/convert/file", data=bytes(body),
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}"})
    with urllib.request.urlopen(request, timeout=900) as response:
        payload = json.loads(response.read())
    return ((payload.get("document") or {}).get("md_content") or "").strip()


def damage(cell: Image.Image, rng: random.Random) -> str:
    """Erase or ink a small rectangle over the number. Two thirds erasure, one third added ink,
    because toner loss and a pen mark fail differently."""
    width, height = cell.size
    bar_width = max(2, int(width * rng.uniform(0.04, 0.12)))
    bar_height = max(2, int(height * rng.uniform(0.25, 0.6)))
    x = rng.randint(0, max(0, width - bar_width))
    y = rng.randint(max(0, int(height * 0.15)), max(1, int(height * 0.85) - bar_height))
    colour = 255 if rng.random() < 0.6 else 0
    ImageDraw.Draw(cell).rectangle([x, y, x + bar_width, y + bar_height], fill=colour)
    return f"{'white' if colour else 'black'} bar at {x},{y}"


def main() -> int:
    document = parse_document()
    box = find_cell(document)
    page = render_page()
    clean_cell = crop_cell(page, box)
    truth = value_of(TRUTH)

    print(f"cell {TRUTH!r} at l={box['l']:.1f} r={box['r']:.1f} t={box['t']:.1f} b={box['b']:.1f}")
    with tempfile.TemporaryDirectory() as tmp_name:
        tmp = Path(tmp_name)
        t_clean = value_of(read_with_tesseract(clean_cell, tmp))
        r_clean = value_of(read_with_rapidocr(clean_cell, tmp))
    print(f"clean cell: tesseract {t_clean!r}, rapidocr {r_clean!r}, truth {truth!r}")
    if t_clean != truth or r_clean != truth:
        print()
        print("At least one engine does not read the clean cell correctly, so an experiment on damaged")
        print("copies would be measuring the crop rather than the damage. Fix the cell first.")
        return 1

    rng = random.Random(SEED)
    stats = {
        "both_right": 0, "disagree": 0, "both_wrong_agree": 0,
        "t_right_r_wrong": 0, "r_right_t_wrong": 0,
    }

    # The control, and it has to vary something to measure anything. Reading one clean cell twice
    # through the same path is deterministic and would only ever report zero. What varies in the
    # design is the STAGE, which is the research's own warning: two engines over one identically
    # prepared crop share a failure mode. So the control stages each engine differently and asks
    # whether they still agree on an undamaged cell.
    #
    # Two stagings, both defensible on their own: as rendered, and at double resolution, which is what
    # a pipeline does when a figure looks small. If the engines disagree on a clean cell under a
    # staging change, then staging noise is part of every disagreement this run reports and the gate
    # is weaker than the damaged-cell numbers alone suggest.
    stagings = {"as-rendered": 1.0, "upscaled x2": 2.0}
    print("clean-cell control, engines staged differently:")
    staging_disagreements = 0
    with tempfile.TemporaryDirectory() as tmp_name:
        tmp = Path(tmp_name)
        for label, factor in stagings.items():
            w, h = clean_cell.size
            scaled = clean_cell.resize((int(w * factor), int(h * factor)), Image.LANCZOS)
            t_value = value_of(read_with_tesseract(scaled, tmp))
            r_value = value_of(read_with_rapidocr(scaled, tmp))
            agree = t_value == r_value == truth
            staging_disagreements += not agree
            print(f"  {label:<14} tesseract {t_value!r} rapidocr {r_value!r} "
                  f"{'agree' if agree else 'DISAGREE'}")
    if staging_disagreements:
        print("  a staging change alone moves a reading, so part of every disagreement below is the")
        print("  staging rather than the document, and the gate needs that noise floor subtracted")
    else:
        print("  both stagings read the clean cell correctly on both engines, so the noise floor of the")
        print("  signal is low and the damaged-cell disagreements below carry information")
    print()
    print()
    print(f"{TRIALS} trials on damaged copies of the clean cell")
    print(f"{'#':>3}  {'tesseract':>10} {'rapidocr':>10}  {'verdict':<26} what changed")
    with tempfile.TemporaryDirectory() as tmp_name:
        tmp = Path(tmp_name)
        for trial in range(1, TRIALS + 1):
            damaged = clean_cell.copy()
            what = damage(damaged, rng)
            t_value = value_of(read_with_tesseract(damaged, tmp))
            r_value = value_of(read_with_rapidocr(damaged, tmp))
            t_ok = t_value == truth
            r_ok = r_value == truth
            if t_ok and r_ok:
                verdict = "both right"
                stats["both_right"] += 1
            elif not t_ok and not r_ok:
                if t_value == r_value:
                    verdict = "BOTH WRONG, AGREED"
                    stats["both_wrong_agree"] += 1
                else:
                    verdict = "both wrong, disagreed"
                    stats["disagree"] += 1
            else:
                verdict = "disagreed"
                stats["disagree"] += 1
                stats["t_right_r_wrong" if t_ok else "r_right_t_wrong"] += 1
            print(f"{trial:>3}  {t_value:>10} {r_value:>10}  {verdict:<26} {what}")

    total = TRIALS
    print()
    print(f"both engines right:              {stats['both_right']:>3} of {total}")
    print(f"engines DISAGREED:               {stats['disagree']:>3} of {total}"
          f"   ({100 * stats['disagree'] / total:.0f} percent)")
    print(f"  of those, tesseract was right: {stats['t_right_r_wrong']:>3}")
    print(f"  of those, rapidocr was right:  {stats['r_right_t_wrong']:>3}")
    print(f"BOTH WRONG AND AGREEING:         {stats['both_wrong_agree']:>3} of {total}"
          f"   ({100 * stats['both_wrong_agree'] / total:.0f} percent)")
    print()
    both_wrong = stats["both_wrong_agree"] + (stats["disagree"] - stats["t_right_r_wrong"]
                                              - stats["r_right_t_wrong"])
    # The counter that matters is per engine, because the design names one engine as primary and the
    # other as the checker. An earlier version of this summary compared counts that did not belong
    # together and printed "750 percent of them", which is how the error was caught.
    t_wrong = total - stats["both_right"] - stats["r_right_t_wrong"]
    r_wrong = total - stats["both_right"] - stats["t_right_r_wrong"]
    caught = stats["t_right_r_wrong"]
    both_wrong = t_wrong + r_wrong - (stats["t_right_r_wrong"] + stats["r_right_t_wrong"]) \
        + stats["both_wrong_agree"]
    print(f"tesseract wrong on:  {t_wrong:>3} of {total}")
    print(f"rapidocr  wrong on:  {r_wrong:>3} of {total}")
    print()
    print("A second reading with rapidocr primary and tesseract as the checker:")
    print(f"  rapidocr alone leaves {r_wrong} wrong figures unflagged.")
    print(f"  tesseract catches {caught} of those {r_wrong}, so "
          f"{r_wrong - caught} would still go unflagged.")
    print(f"  cost: {caught} correct readings escalated to a reviewer, on damaged cells only.")
    print()
    print(f"Both engines wrong AND agreeing: {stats['both_wrong_agree']} of {total}. These are the")
    print("failures no second reader can see, and the number to watch as this measurement grows.")
    print()
    print("Read the clean line above before trusting any of this: if the engines disagree on a clean")
    print("scan, every disagreement in the archive is a coin toss and the gate is noise.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
