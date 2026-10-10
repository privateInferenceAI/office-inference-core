#!/usr/bin/env python3
"""Write a small multi-page PDF so citations can show a real page number.

Text-only, one font, no compression. It exists to give the document pipeline something with pages
to cite, because a markdown file can only ever cite page 1.

Usage: python3 tests/fixtures/make_sample_pdf.py > documents/company/vendor-agreement.pdf
"""

from __future__ import annotations

import sys

FACTS = [
    ("Vendor Agreement - Page 1 of 3", [
        "1. Term",
        "",
        "This agreement begins on 1 March 2026 and renews automatically for",
        "twelve months unless either party gives 60 days written notice.",
        "",
        "2. Payment terms",
        "",
        "Invoices are payable net 30 from the date of receipt. Late payments",
        "accrue interest at 1.5 percent per month.",
    ]),
    ("Vendor Agreement - Page 2 of 3", [
        "3. Service levels",
        "",
        "The vendor guarantees 99.5 percent uptime measured monthly. Credits",
        "are 5 percent of the monthly fee per hour of downtime beyond that.",
        "",
        "4. Support",
        "",
        "Support is available 8am to 6pm Eastern. Severity one incidents get a",
        "response within 30 minutes, severity two within four business hours.",
    ]),
    ("Vendor Agreement - Page 3 of 3", [
        "5. Termination",
        "",
        "Either party may terminate for material breach with 30 days notice and",
        "an opportunity to cure. Insolvency permits immediate termination.",
        "",
        "6. Data",
        "",
        "The vendor may process data only to provide the service, must delete it",
        "within 90 days of termination, and must notify us of any breach within",
        "72 hours.",
    ]),
]


def escape(text: str) -> str:
    return text.replace("\\", r"\\").replace("(", r"\(").replace(")", r"\)")


def build() -> bytes:
    objects: list[bytes] = []

    def add(body: bytes) -> int:
        objects.append(body)
        return len(objects)

    # Reserve object 1 for the catalog and 2 for the page tree.
    objects.append(b"")  # placeholder catalog
    objects.append(b"")  # placeholder pages
    font = add(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>")

    page_ids = []
    for title, lines in FACTS:
        content_lines = ["BT", "/F1 12 Tf", "72 720 Td", "16 TL"]
        for line in [title, ""] + lines:
            content_lines.append(f"({escape(line)}) Tj T*")
        content_lines.append("ET")
        stream = "\n".join(content_lines).encode("latin-1")
        content = add(b"<< /Length " + str(len(stream)).encode() + b" >>\nstream\n" + stream + b"\nendstream")
        page = add(
            b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
            b"/Resources << /Font << /F1 " + str(font).encode() + b" 0 R >> >> "
            b"/Contents " + str(content).encode() + b" 0 R >>"
        )
        page_ids.append(page)

    kids = b" ".join(str(p).encode() + b" 0 R" for p in page_ids)
    objects[1] = b"<< /Type /Pages /Kids [" + kids + b"] /Count " + str(len(page_ids)).encode() + b" >>"
    objects[0] = b"<< /Type /Catalog /Pages 2 0 R >>"

    out = bytearray(b"%PDF-1.4\n")
    offsets = [0]
    for number, body in enumerate(objects, start=1):
        offsets.append(len(out))
        out += str(number).encode() + b" 0 obj\n" + body + b"\nendobj\n"

    xref = len(out)
    out += b"xref\n0 " + str(len(objects) + 1).encode() + b"\n"
    out += b"0000000000 65535 f \n"
    for offset in offsets[1:]:
        out += f"{offset:010d} 00000 n \n".encode()
    out += (
        b"trailer\n<< /Size " + str(len(objects) + 1).encode() + b" /Root 1 0 R >>\n"
        b"startxref\n" + str(xref).encode() + b"\n%%EOF\n"
    )
    return bytes(out)


if __name__ == "__main__":
    sys.stdout.buffer.write(build())
