#!/usr/bin/env python3
"""Write a PDF with no text layer, to stand in for a scan or a fax.

Every page is one image. A text extractor returns nothing, which is exactly what happens when a
client hands over a scanned contract. This fixture exists so that gap is a tested behaviour rather
than a surprise discovered at a client site.

Usage: python3 tests/fixtures/make_scanned_pdf.py > documents/company/scanned-contract.pdf
"""

from __future__ import annotations

import sys
import zlib

WIDTH, HEIGHT = 850, 1100          # roughly letter at 100 dpi


def page_bitmap(seed: int) -> bytes:
    """A grayscale page of 'text lines': dark bars on a light background."""
    rows = []
    lines = [120 + (i * 34) for i in range(0, 20) if (i + seed) % 7 != 0]   # gaps look like paragraphs
    for y in range(HEIGHT):
        row = bytearray([245]) * WIDTH
        for line_top in lines:
            if line_top <= y < line_top + 9:
                # variable line lengths so it reads as text, not as a barcode
                length = 420 + ((line_top + seed) * 37) % 260
                for x in range(90, min(90 + length, WIDTH - 90)):
                    row[x] = 40
        rows.append(bytes(row))
    return b"".join(rows)


def build() -> bytes:
    objects: list[bytes] = []
    objects.append(b"")     # 1 catalog
    objects.append(b"")     # 2 page tree
    image_ids, content_ids, page_ids = [], [], []

    for page in range(2):
        raw = zlib.compress(page_bitmap(page))
        objects.append(
            b"<< /Type /XObject /Subtype /Image /Width " + str(WIDTH).encode() +
            b" /Height " + str(HEIGHT).encode() +
            b" /ColorSpace /DeviceGray /BitsPerComponent 8 /Filter /FlateDecode /Length " +
            str(len(raw)).encode() + b" >>\nstream\n" + raw + b"\nendstream"
        )
        image_ids.append(len(objects))

        content = f"q {WIDTH} 0 0 {HEIGHT} 0 0 cm /Im0 Do Q".encode()
        objects.append(b"<< /Length " + str(len(content)).encode() + b" >>\nstream\n" + content +
                       b"\nendstream")
        content_ids.append(len(objects))

    for page in range(2):
        objects.append(
            b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 " + str(WIDTH).encode() + b" " +
            str(HEIGHT).encode() + b"] /Resources << /XObject << /Im0 " +
            str(image_ids[page]).encode() + b" 0 R >> >> /Contents " +
            str(content_ids[page]).encode() + b" 0 R >>"
        )
        page_ids.append(len(objects))

    kids = b" ".join(str(p).encode() + b" 0 R" for p in page_ids)
    objects[1] = b"<< /Type /Pages /Kids [" + kids + b"] /Count " + str(len(page_ids)).encode() + b" >>"
    objects[0] = b"<< /Type /Catalog /Pages 2 0 R >>"

    out = bytearray(b"%PDF-1.4\n")
    offsets = [0]
    for number, body in enumerate(objects, start=1):
        offsets.append(len(out))
        out += str(number).encode() + b" 0 obj\n" + body + b"\nendobj\n"

    xref = len(out)
    out += b"xref\n0 " + str(len(objects) + 1).encode() + b"\n0000000000 65535 f \n"
    for offset in offsets[1:]:
        out += f"{offset:010d} 00000 n \n".encode()
    out += (b"trailer\n<< /Size " + str(len(objects) + 1).encode() + b" /Root 1 0 R >>\n"
            b"startxref\n" + str(xref).encode() + b"\n%%EOF\n")
    return bytes(out)


if __name__ == "__main__":
    sys.stdout.buffer.write(build())
