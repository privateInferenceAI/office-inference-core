"""How confidence responds to scan quality, per line, on the fixture and on degraded copies of it.

The measurement behind RET-PARSE-002 and behind the review design's first question: how much of a real
archive would come out needing a person. Two things make that answerable before we have a client's
scans.

First, degradation is controlled. The same page is blurred, dimmed and softened by known amounts, so
the confidence numbers can be read against a known amount of damage rather than against an impression
of one.

Second, the output is a rate, not a verdict. Several thresholds are reported for each variant, because
the threshold is the thing this measurement is supposed to inform. A threshold picked in advance would
only confirm itself.

Tesseract is already in the parser image, so this runs where the work would run:

    docker run --rm -v /opt/oi-core/tests/fixtures:/f:ro \
      --entrypoint python ghcr.io/docling-project/docling-serve-cpu:v1.36.0 /f/confidence_probe.py

Bounds on the answer, worth stating before the number arrives: this is one page of synthetic text at
one font size on one paper-like background. It says how the mechanism behaves, not what fraction of a
given client's archive will need review. That needs real scans, which is a C2 fixture-corpus question.
"""
from __future__ import annotations

import glob
import shutil
import statistics
import subprocess
import sys
import tempfile
from pathlib import Path

from PIL import Image, ImageEnhance, ImageFilter

try:
    import pypdfium2 as pdfium
except ImportError:  # the fallback is the poppler CLI, if the image happens to have it
    pdfium = None

SOURCE = Path(sys.argv[1] if len(sys.argv) > 1 else "/f/fee-schedule-scanned.pdf")
DPI = int(sys.argv[2] if len(sys.argv) > 2 else 300)
PSM = sys.argv[3] if len(sys.argv) > 3 else "4"   # 4: single column of variable-size text
THRESHOLDS = (60, 70, 80, 90)

# The variants, in the order a real archive degrades: a good scan, then progressively worse copies of
# it. Each is described by what it simulates so the number has a meaning attached.
VARIANTS = (
    ("clean", {}),
    ("light blur, 1px", {"blur": 1.0}),
    ("medium blur, 2px", {"blur": 2.0}),
    ("heavy blur, 3px", {"blur": 3.0}),
    ("faded, 50% contrast", {"contrast": 0.5}),
    ("faded, 30% contrast", {"contrast": 0.3}),
    ("light blur + faded", {"blur": 1.0, "contrast": 0.6}),
    ("skewed 1 degree", {"rotate": 1.0}),
    ("speckled, 0.2% of pixels", {"speckle": 0.002}),
)


def first_page(out_dir: Path) -> Image.Image:
    """The page as a grayscale image.

    pypdfium2 first, because it is what Docling itself renders with and therefore what the parser
    image already carries. The first attempt at this used pdftoppm, which is not in that image: it
    installed tesseract and GL libraries and no poppler CLI, so the probe died on its first variant
    after printing a header.
    """
    if pdfium is not None:
        pdf = pdfium.PdfDocument(str(SOURCE))
        return pdf[0].render(scale=DPI / 72).to_pil().convert("L")
    subprocess.run(["pdftoppm", "-r", str(DPI), "-gray", "-png", str(SOURCE),
                    str(out_dir / "page")], check=True)
    png = sorted(glob.glob(str(out_dir / "page*.png")))[0]
    return Image.open(png).convert("L")


def render(variant: dict, out_dir: Path) -> Path:
    """Rasterise the page, apply the variant's damage, and return the PNG."""
    image = first_page(out_dir)
    if "blur" in variant:
        image = image.filter(ImageFilter.GaussianBlur(variant["blur"]))
    if "contrast" in variant:
        image = ImageEnhance.Contrast(image).enhance(variant["contrast"])
    if "rotate" in variant:
        image = image.rotate(variant["rotate"], resample=Image.BICUBIC, fillcolor=255,
                             expand=False)
    if "speckle" in variant:
        pixels = image.load()
        width, height = image.size
        step = max(1, int((width * height * variant["speckle"]) ** 0.5))
        for y in range(0, height, step):
            for x in range(0, width, step):
                pixels[x, y] = 0
    damaged = out_dir / "damaged.png"
    image.save(damaged)
    return damaged


def ocr_lines(png: Path) -> list[tuple[float, str]]:
    """Per-line mean confidence and the text, read from tesseract's TSV word rows."""
    result = subprocess.run(["tesseract", str(png), "stdout", "--psm", PSM, "tsv"],
                            capture_output=True, text=True)
    grouped: dict[tuple, list[tuple[float, str]]] = {}
    for row in result.stdout.splitlines()[1:]:
        parts = row.split("\t")
        if len(parts) < 12 or parts[11].strip() == "":
            continue
        try:
            level = int(parts[0])
            conf = float(parts[10])
        except ValueError:
            continue
        if level != 5 or conf < 0:
            continue
        key = (parts[1], parts[2], parts[3], parts[4])
        grouped.setdefault(key, []).append((conf, parts[11]))
    lines = []
    for key in sorted(grouped):
        words = grouped[key]
        confidences = [c for c, _ in words]
        lines.append((sum(confidences) / len(confidences), " ".join(t for _, t in words)))
    return lines


def main() -> int:
    if not SOURCE.exists():
        print(f"missing {SOURCE}")
        return 1
    renderer = "pypdfium2" if pdfium is not None else "pdftoppm"
    print(f"{SOURCE.name} at {DPI} dpi via {renderer}, tesseract psm {PSM}")
    if pdfium is None and not shutil.which("pdftoppm"):
        print("neither pypdfium2 nor pdftoppm is available, so no page can be rendered")
        return 1
    print(f"{'variant':<24} {'lines':>5} {'min':>6} {'p05':>6} {'med':>6} "
          + " ".join(f"{'<'+str(t):>7}" for t in THRESHOLDS))
    for label, variant in VARIANTS:
        with tempfile.TemporaryDirectory() as tmp:
            png = render(variant, Path(tmp))
            lines = ocr_lines(png)
        if not lines:
            print(f"{label:<24} {'0':>5}  no lines recovered")
            continue
        confidences = sorted(c for c, _ in lines)
        p05 = confidences[max(0, len(confidences) // 20)]
        median = statistics.median(confidences)
        rates = []
        for threshold in THRESHOLDS:
            flagged = sum(1 for c in confidences if c < threshold)
            rates.append(f"{100 * flagged / len(confidences):6.1f}%")
        print(f"{label:<24} {len(lines):>5} {confidences[0]:>6.1f} {p05:>6.1f} {median:>6.1f} "
              + " ".join(f"{r:>7}" for r in rates))
    print()
    print("The <60 column is the share of lines a reviewer would be asked about at that threshold.")
    print("Read the clean row first: if it is not near zero, the threshold is wrong for good scans,")
    print("and every point of it is a page of a client's archive queued for no reason.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
