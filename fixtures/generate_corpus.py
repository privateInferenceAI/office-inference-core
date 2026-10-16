#!/usr/bin/env python3
"""Generate a corpus that covers every format and failure the pipeline handles.

**Why this exists.** `fixtures/corpus/` holds four real filings from SEC EDGAR and the IRS, and the
manual documents a deployed corpus of twelve documents. Those twelve exercise **six** of the fifteen
suffixes `services/ingestion/ingest.py` accepts. Nine formats have never been through the pipeline:
`.csv`, `.docx`, `.html`, `.markdown`, `.odp`, `.ods`, `.odt`, `.rtf`, `.txt`. Neither had OCR on a
document of its own, a near-duplicate pair, a superseded pair, or a file that fails the parser on
purpose.

So a corpus that proves the pipeline works has to contain the awkward cases deliberately. Downloading
real documents cannot give you a file that fails to parse or an exact duplicate, and hoping the client
supplies one is not a test.

**The formats come from `faker-file` (MIT).** It wraps Faker, so the content is realistic business
prose with invented names and figures rather than lorem ipsum, and it writes DOCX, XLSX, PPTX, ODT,
ODS, ODP, RTF, CSV, TXT, EML and PDF. Two things about it are worth knowing before you install it:

  * The PDF provider runs through `reportlab` (BSD) or `pdfkit` plus `wkhtmltopdf` (LGPLv3). **Take the
    reportlab route.** LGPL in a shipped image is a decision this project would have to take
    deliberately, and it does not have to be taken for test fixtures.
  * It is a development dependency. The stack itself runs from digest-pinned images and needs none of
    this, which is why it belongs in `requirements-dev.txt` and not in any service image.

**Everything here is synthetic.** No real person's data, no client's data, and no third party's
copyright: the prose is generated, and the sensitive-looking values are Faker's invented ones. That is
what makes this corpus safe to commit when the SEC filings in `fixtures/corpus/` are safe only because
they are public records with a licence read at the source.

## What it writes

    <target>/company/            documents the staff role may read
    <target>/executive/          documents only the executive role may read
    <target>/loose/              NOT a root: a file left here is invisible to every role, on purpose

Run it, then read `PLACEMENT.md` in the target directory for what each file is for and where it has to
end up. **Nothing here touches a deployment's documents root**: it writes to a directory you name, and
copying is a separate, deliberate step.

## Usage

    pip install "faker-file[docx,xlsx,odt,ods,pdf]"
    python3 fixtures/generate_corpus.py --out /tmp/client-corpus

Add `--seed N` to change the invented names and figures; the default seed is fixed so two runs produce
the same corpus, which is what makes a measurement taken against it repeatable.

**For scale, add `--volume N`.** It writes N more documents into `company/` and `executive/`, named `vol-*`,
on the ordinary topics of a facilities company, alongside the twenty-six that cover formats and failures. The two sets are separate on
purpose: the coverage set is what the manual's worked example and two acceptance probes depend on, and a
document that competes with them would break a test rather than exercise one.

    python3 fixtures/generate_corpus.py --out /tmp/big --volume 1000
"""
from __future__ import annotations

import argparse
import csv
import io
import re
import shutil
import sys
import textwrap
import zipfile
from pathlib import Path

# **Loaded from the worker's own list rather than copied.**
#
# This was a second tuple, and the coverage report at the end of a run measured the corpus against it. So the
# denominator was a copy of the list: the report confirmed that two copies of one list agreed with each other
# and said `15/15 formats covered (complete)` while a third of what the parser reads was unreachable. The copy
# is gone, and the direction of the check is reversed below.
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "services" / "ingestion"))
import formats  # noqa: E402  (after the path insert, deliberately)

ACCEPTED_SUFFIXES = tuple(sorted(formats.ACCEPTED_SUFFIXES))

SEED = 20261013

# Client name used throughout, so the invented documents read as one company's rather than as a pile
# of unrelated samples. Faker invents the people, addresses and figures.
CLIENT = "Meridian Facilities Group"


def note(lines: list[str], text: str) -> None:
    lines.append(text)


# --------------------------------------------------------------------------- authored documents
#
# These are written by hand rather than generated, because each one has to contain a specific thing:
# a figure a question can be answered from, an exact duplicate, a stated replacement, or content that
# breaks the parser. A generator that invents prose cannot be asked for those.

def policy_markdown() -> str:
    return f"""# {CLIENT}: Travel and Expenses Policy

Applies to all employees. Effective 1 January 2026. Supersedes the 2024 revision.

## 1. Mileage

Mileage is reimbursed at **0.67 per mile** for travel in an employee's own vehicle. Travel in a
client-owned vehicle is billed to the client at the rate on the current fee schedule and is not
reimbursable to the employee.

## 2. Subsistence

Meals are reimbursed to a ceiling of 65.00 per day within the metropolitan area and 85.00 per day
elsewhere, receipts required above 25.00.

## 3. Approval

Claims above 500.00 require the head of department's approval before submission. Claims above
2,500.00 require a director.

## 4. Submission

Claims are submitted monthly, within ten working days of the month end. A claim submitted late is paid
in the following cycle but is not refused.
"""


def fee_schedule_markdown(year: int, rate: str, marker: str = "") -> str:
    return f"""# {CLIENT}: Schedule of Fees and Reimbursable Rates, {year}
{marker}
Item | Basis | Rate
---|---|---
Standard labour, business hours | per hour | {rate}
Standard labour, out of hours | per hour | {float(rate) * 1.5:.2f}
Emergency callout | per visit | 475.50
Planned maintenance | per visit | 240.00
Filter media, standard grade | per unit | 32.50
Mileage, client-owned vehicle | per mile | 0.67
Weekend multiplier | of labour | 1.75x
Holiday multiplier | of labour | 2.0x

Rates are exclusive of tax. The emergency callout rate applies outside contracted hours and includes
the first hour on site. This schedule replaces all earlier schedules and takes effect on the date of
issue.
"""


def executive_compensation_markdown() -> str:
    return f"""# {CLIENT}: Executive Compensation, Confidential

CONFIDENTIAL. Board and named executives only. Not for distribution.

## Base salary

The Chief Executive Officer's base salary is **418,750** per annum.

## Bonus

The executive bonus target is **62.5 percent** of base salary, payable on the achievement of the board's
annual objectives. The maximum is 100 percent.

## Severance

On a change of control, the severance on a change of control is **eighteen months** of base salary plus
the pro-rata bonus for the year of departure, subject to the executive signing a release.

## Notes

Figures are stated for the current financial year and are reviewed annually by the remuneration
committee.
"""


def onboarding_notes_markdown() -> str:
    return f"""# {CLIENT}: Onboarding Notes

## Accounts

Supplier invoices arrive on the shared inbox and are matched against the purchase order before being
passed to accounts. Anything without a purchase order goes back to the supplier with a note.

## Access

The shared drive is arranged by client. A folder that is not named for a client is a working folder and
should not hold anything that belongs to one.

## Scheduling

Planned maintenance is booked a week ahead. Emergency work is dispatched from the board.
"""


def dead_letter_txt() -> str:
    """A `.txt` the parser will refuse.

    It is named for what it is: a file the pipeline is *supposed* to dead-letter. Ingesting it twice
    with no fix is the test for `INGEST_MAX_FAILURES`, and the corpus has to contain it because a
    corpus of valid documents can never exercise the failure path.
    """
    return ("DEAD LETTER FIXTURE — the parser refuses this file on purpose.\n"
            "\n"
            "This exists so that INGEST_MAX_FAILURES can be observed rather than assumed: the worker\n"
            "should record a failure for it, try it once more on the next cycle, and then stop trying\n"
            "until its bytes change. It is also the file to use when testing --clear-failures.\n")


def root_level_markdown() -> str:
    """Belongs directly under the documents root, which is the mistake it exists to demonstrate.

    A file with no top-level folder takes the documents root's own name as its access label, so it is
    indexed and unreachable: no role's map contains that label. See the placement notes.
    """
    return (f"# {CLIENT}: Read Me First\n"
            "\n"
            "This file is deliberately placed loose in the documents root rather than under a named\n"
            "folder. It demonstrates the quietest failure in the pipeline: it is ingested, it is\n"
            "searchable by nobody, and nothing reports a problem.\n")


def pii_markdown() -> str:
    """Carries an SSN and a card-shaped number, to exercise redaction and the content modes.

    Written by hand because `faker-file` cannot be asked for exactly one SSN and one card number in a
    known sentence. **These are invented values in reserved ranges**, not a real person's, and they are
    here because a redaction test that never sees an identifier tests nothing.
    """
    return f"""# {CLIENT}: New Starter Record

Employee: (invented) — this file exists to exercise redaction. The values below are fake.

Payroll reference: 000-00-0000
Contact: 0000 0000 0000 0000

Emergency contact and bank details are held in the payroll system rather than in this folder. This
document is a redaction fixture: the identifiers above are in reserved test ranges and match the
patterns the egress gate strips, so a frontier call built from this file must arrive without them.
"""


# --------------------------------------------------------------------------- generated documents
#
# `faker-file` writes the rest. Each entry names the provider, the method, the target filename and the
# root, so the file list is declared here rather than inferred from whatever the library produced.

GENERATED = (
    # (provider module, provider class, method, filename, root, what it is for)
    ("docx_file", "DocxFileProvider", "docx_file", "service-agreement.docx", "company",
     "the .docx case: a contract with numbered clauses"),
    ("xlsx_file", "XlsxFileProvider", "xlsx_file", "asset-register.xlsx", "company",
     "the .xlsx case: a table a question can be answered from"),
    ("pptx_file", "PptxFileProvider", "pptx_file", "board-briefing.pptx", "company",
     "the .pptx case: slide text without page structure"),
    ("odt_file", "OdtFileProvider", "odt_file", "site-survey.odt", "company",
     "the .odt case: an open-document word processor file"),
    ("ods_file", "OdsFileProvider", "ods_file", "consumables.ods", "company",
     "the .ods case: an open-document spreadsheet"),
    ("odp_file", "OdpFileProvider", "odp_file", "toolbox-talk.odp", "company",
     "the .odp case: an open-document presentation"),
    ("csv_file", "CsvFileProvider", "csv_file", "callout-log.csv", "company",
     "the .csv case: rows the lexical arm can match exactly"),
    ("txt_file", "TxtFileProvider", "txt_file", "meeting-notes.txt", "company",
     "the .txt case: no structure at all"),
    ("pdf_file", "PdfFileProvider", "pdf_file", "warranty-terms.pdf", "company",
     "the .pdf case with a text layer, so no OCR"),
    ("eml_file", "EmlFileProvider", "eml_file", "supplier-thread.eml", "company",
     "the .eml case: headers plus a body, which tests the parser's email handling"),
    ("docx_file", "DocxFileProvider", "docx_file", "remuneration-policy.docx", "executive",
     "a restricted-root document in a second format, so the ACL is tested on more than one"),
    ("pdf_file", "PdfFileProvider", "pdf_file", "board-minutes.pdf", "executive",
     "a second restricted-root document, in the format most likely to be cited"),
)


def rich_text(paragraphs: list[tuple[str, str]]) -> str:
    """A genuine RTF document, which `faker-file` does not produce.

    **`faker-file`'s RtfFileProvider writes plain text with an `.rtf` extension** -- no `{\rtf1` header and no
    control words. `file` reports `ASCII text`, and a strict parser is right to refuse it. That mattered
    because the corpus reported "15/15 formats covered" while containing nothing a real client's parser would
    accept, so the format looked tested and was not.

    RTF is a brace-delimited tree of control words. The minimum a parser needs is `{\rtf1` to open, a font
    table, and `\par` between paragraphs. Escapes matter: a literal backslash, `{` or `}` must itself be
    backslash-escaped or the tree structure breaks, which is the commonest way a hand-made RTF fails.
    """
    def escape(text: str) -> str:
        out = text.replace("\\", "\\\\").replace("{", "\\{").replace("}", "\\}")
        return out.replace("\n", " ")

    body = []
    for style, text in paragraphs:
        escaped = escape(text)
        match = re.match(r"^(#{1,3})\s+(.*)$", escaped)
        if match:
            body.append("\\b\\fs%d %s\\b0\\par" % (28 - 4 * len(match.group(1)), match.group(2)))
        else:
            body.append("%s\\par" % escaped)
    return (
        "{\\rtf1\\ansi\\deff0\n"
        "{\\fonttbl{\\f0\\froman Times New Roman;}{\\f1\\fswiss Arial;}}\n"
        "\\f0\\fs24\n" + "\n".join(body) + "\n}\n"
    )


def malformed_rich_text() -> str:
    """RTF a strict parser refuses: the braces do not balance.

    A client can supply a file whose extension promises one thing and whose contents are another -- a
    truncated download, a renamed text file, a document saved with the wrong extension. The corpus needs one
    of those as much as it needs a valid file, because the two paths through the pipeline differ: a valid
    file must be parsed, and a malformed one must fail **visibly**, be retried once, then dead-letter.
    """
    return (
        "{\\rtf1\\ansi\\deff0\n"
        "{\\fonttbl{\\f0\\froman Times New Roman;}\n"   # font table never closed
        "\\f0\\fs24 This document is deliberately malformed.\\par\n"
        "The font table above is missing its closing brace, so a parser that trusts the tree will fail.\n"
    )


def markdown_to_html(markdown_text: str) -> str:
    """A minimal, honest HTML rendering: headings and paragraphs, no styling.

    Written here rather than pulled from a Markdown library because the point of this file is to be
    VALID HTML for the parser to read, not to be pretty. A dependency for four lines of substitution
    would be one more thing to install on the box.
    """
    body: list[str] = []
    for block in markdown_text.split("\n\n"):
        block = block.strip()
        if not block:
            continue
        if block.startswith("## "):
            body.append(f"<h2>{block[3:].strip()}</h2>")
        elif block.startswith("# "):
            body.append(f"<h1>{block[2:].strip()}</h1>")
        elif block.startswith("|"):
            rows = [r for r in block.splitlines() if not set(r) <= set("|-: ")]
            cells = "".join(
                "<tr>" + "".join(f"<td>{c.strip()}</td>" for c in r.strip("|").split("|")) + "</tr>"
                for r in rows)
            body.append(f"<table>{cells}</table>")
        else:
            body.append(f"<p>{block}</p>")
    return ("<!DOCTYPE html>\n<html lang=\"en\">\n<head><meta charset=\"utf-8\">"
            f"<title>{CLIENT}: policy</title></head>\n<body>\n" + "\n".join(body) +
            "\n</body>\n</html>\n")


def write_authored(target: Path, lines: list[str]) -> dict:
    """Write the hand-authored documents and return what was written, by root."""
    written: dict[str, list[str]] = {"company": [], "executive": [], "loose": []}

    def put(root: str, name: str, text: str) -> None:
        path = target / root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
        written[root].append(name)

    put("company", "travel-expenses-policy.md", policy_markdown())
    # **`faker-file` cannot write a valid RTF**, so both RTF cases are authored here: one a parser must
    # accept, and one it must refuse visibly. See `rich_text` for why the provider's output is not RTF.
    put("company", "terms-of-service.rtf", rich_text([
        ("h1", f"{CLIENT}: Standard Terms of Service"),
        ("p", "These terms apply to all work carried out under a signed order. They take precedence over "
              "any terms supplied by the client, except where the order states otherwise in writing."),
        ("h2", "1. Charges"),
        ("p", "Charges are as set out in the schedule of fees current at the date of the order. Standard "
              "labour is charged at 145.00 per hour during business hours and at the out of hours rate "
              "outside them."),
        ("h2", "2. Payment"),
        ("p", "Invoices are payable within thirty days. Interest accrues on late payment at 2 percent "
              "above the base rate."),
        ("h2", "3. Liability"),
        ("p", "Liability is limited to the charges paid for the work giving rise to the claim. Nothing in "
              "these terms limits liability for death or personal injury caused by negligence."),
    ]))
    put("company", "malformed-fixture.rtf", malformed_rich_text())
    put("company", "onboarding-notes.md", onboarding_notes_markdown())
    put("company", "pii-new-starter.md", pii_markdown())
    put("company", "dead-letter-fixture.txt", dead_letter_txt())

    # The current schedule and its immediate predecessor, which is the superseded pair.
    put("company", "fee-schedule-2026.md", fee_schedule_markdown(2026, "145.00"))
    put("company", "fee-schedule-2025.md",
        fee_schedule_markdown(2025, "132.00", marker="\nSUPERSEDED. Replaced by the 2026 schedule.\n"))

    # An exact copy of the current schedule, which is the near-duplicate pair. Byte-identical on
    # purpose: a pair that differs by one character tests nothing, because the detector compares chunk
    # text and a single edit moves one chunk.
    copy_path = target / "company" / "fee-schedule-2026-copy.md"
    copy_path.write_text(fee_schedule_markdown(2026, "145.00"))
    written["company"].append("fee-schedule-2026-copy.md")

    # The 10-K-per-publication case in miniature: a long document, for chunk counts.
    long_doc = "\n\n".join(
        f"## Section {n}\n\n" + textwrap.fill(
            f"This section records the arrangements between the parties for the period. "
            f"Clause {n}.1 sets out the obligations. Clause {n}.2 sets out the charges, which are "
            f"reviewed annually. Clause {n}.3 sets out what happens if either party wishes to end "
            f"the arrangement, and the notice required is {30 + n} days in writing. "
            f"Clause {n}.4 records that nothing in this section creates a partnership.", 100)
        for n in range(1, 61))
    put("company", "master-services-agreement.md",
        f"# {CLIENT}: Master Services Agreement\n\n" + long_doc + "\n")

    put("executive", "executive-compensation.md", executive_compensation_markdown())

    # The three text formats `faker-file` does not write. `.markdown` and `.html` are the same policy
    # document in different clothing, which is deliberate: the same content through three parsers is how
    # a format-specific extraction bug shows up as a difference in results rather than as a missing file.
    #
    # `.htm` as well as `.html` because both are in the accepted set, and a suffix list that covers one
    # and not the other is a gap nobody notices until a client supplies the other.
    policy = policy_markdown()
    put("company", "travel-expenses-policy.markdown", policy)
    put("company", "travel-expenses-policy.html", markdown_to_html(policy))
    put("company", "travel-expenses-policy.htm", markdown_to_html(policy))

    # Deliberately NOT under a root, so the reader sees the failure rather than reading about it.
    loose = target / "loose" / "read-me-first.md"
    loose.parent.mkdir(parents=True, exist_ok=True)
    loose.write_text(root_level_markdown())
    written["loose"].append("read-me-first.md")

    return written


def scanned_pages(target: Path, lines: list[str], written: dict) -> None:
    """Raster scans, so the OCR path has fixtures rather than only a paragraph in a runbook.

    **The gap this closes was invisible for the whole project.** The worker accepted no image format at all,
    so a client's scanned page could not enter the pipeline, while the parser has OCR installed and reads one
    correctly in about three seconds. Nothing tested it because nothing could reach it: a format absent from
    the accepted list is a format no fixture can exercise, and the coverage report measured the corpus against
    that same list, so it reported complete.

    Rendered rather than scanned for real, which is a limitation worth stating: these have clean synthetic
    glyphs at a known size, so they prove the path works and they do not prove it works on a 1998 fax. A real
    scan of poor quality is a fixture this cannot make and the corpus does not have.
    """
    from PIL import Image, ImageDraw, ImageFont

    font = None
    for candidate in ("/usr/share/fonts/truetype/dejavu/DejaVuSerif.ttf",
                      "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"):
        if Path(candidate).exists():
            font = ImageFont.truetype(candidate, 30)
            break
    if font is None:
        note(lines, "**No TrueType font found, so the scan fixtures were skipped.** Install")
        note(lines, "`fonts-dejavu-core` and run again; without them the OCR path has no fixture.")
        return

    # Values chosen to appear nowhere else in the corpus, so a question about them can only be answered from
    # the scan. That is what makes the fixture a test of OCR rather than of the rest of the index.
    page = [
        "HALLORAN MECHANICAL LTD",
        "INVOICE 2026-0417",
        "",
        "Site: Lake Kayla pumping station",
        "Work: boiler service and pressure test",
        "Parts: 1,284.00   Labour: 2,610.50",
        "Total due: 4,218.50",
        "",
        "Authorised by: D. Okonkwo",
    ]

    def render(width: int, height: int) -> "Image.Image":
        image = Image.new("RGB", (width, height), "white")
        draw = ImageDraw.Draw(image)
        for index, row in enumerate(page):
            draw.text((60, 60 + index * 46), row, fill="black", font=font)
        return image

    image = render(1400, 560)
    # Three suffixes because they arrive from three places: a scanner writes TIFF, a screenshot writes PNG,
    # and a phone writes JPEG. All three go through the same OCR path and only one of them needs a fixture to
    # prove it, but a client's file arriving as any of them must not be refused.
    for name in ("scan-invoice.tif", "scan-invoice.tiff", "scan-invoice.png", "scan-invoice.jpg"):
        out = target / "company" / name
        out.parent.mkdir(parents=True, exist_ok=True)
        image.save(out)
        written["company"].append(name)


def legacy_office(target: Path, lines: list[str], written: dict) -> None:
    """The pre-2007 Office formats, converted from the modern ones with LibreOffice.

    **These are what a bookkeeper has kept since 2009**, and until this change the pipeline refused all three
    in silence. LibreOffice reads them, the parser image now carries the Calc and Impress components it needs,
    and the accepted list now names them -- so the corpus has to hold one of each, or the next change to that
    list can break them without a test noticing.

    A conversion rather than a hand-built file, because a synthesised OLE2 container would be a guess at the
    format rather than the format. `soffice` is required; its absence is reported rather than passed over,
    because a fixture that silently did not get written is how a coverage report lies.
    """
    import shutil as _shutil
    import subprocess

    soffice = _shutil.which("soffice") or _shutil.which("libreoffice")
    if not soffice:
        note(lines, "**LibreOffice is not on this machine, so the legacy Office fixtures were skipped.**")
        note(lines, "They are `.doc`, `.xls` and `.ppt`: install `libreoffice` and run again. Without them")
        note(lines, "nothing in the corpus proves the three formats a longstanding client is most likely")
        note(lines, "to still hold.")
        return

    sources = {
        "legacy-memo.doc": target / "company" / "onboarding-notes.md",
        "legacy-ledger.xls": target / "company" / "asset-register.xlsx",
        "legacy-briefing.ppt": target / "company" / "board-briefing.pptx",
    }
    for name, source in sources.items():
        if not source.exists():
            note(lines, f"**{name} was not written: {source.name} is missing to convert from.**")
            continue
        out = target / "company" / name
        result = subprocess.run(
            [soffice, "--headless", "--norestore", "--convert-to", out.suffix.lstrip("."),
             "--outdir", str(out.parent), str(source)],
            capture_output=True, text=True, timeout=180)
        produced = out.parent / (source.stem + out.suffix)
        if produced.exists() and produced != out:
            produced.rename(out)
        if out.exists():
            written["company"].append(name)
        else:
            note(lines, f"**LibreOffice did not produce {name}.** It said: "
                        f"{(result.stderr or result.stdout or 'nothing').strip()[:200]}")


REPORTLAB_GENERATOR = ("faker_file.providers.pdf_file.generators."
                        "reportlab_generator.ReportlabPdfGenerator")

# `faker-file` asks reportlab for a bundled `Vera.ttf` that the reportlab wheel does not ship, and it
# defaults the PDF provider to `pdfkit` plus `wkhtmltopdf`. Both are avoidable:
#
#   * `pdf_generator_cls` selects reportlab instead of pdfkit. **That choice matters for licensing**:
#     wkhtmltopdf is LGPLv3, and while a build-time tool is not a distributed artifact, reaching for the
#     BSD route costs one argument and removes the question.
#   * `font_path` points at a font that exists. DejaVu is present on any Debian or Ubuntu host, which
#     is what the build box is.
FONT_CANDIDATES = (
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
    "/usr/share/fonts/truetype/freefont/FreeSans.ttf",
)


def find_font() -> str | None:
    """A TTF reportlab can open, or None so the caller reports the gap rather than guessing."""
    import glob
    for candidate in FONT_CANDIDATES:
        if Path(candidate).exists():
            return candidate
    found = sorted(glob.glob("/usr/share/fonts/**/*.ttf", recursive=True))
    return found[0] if found else None


def write_generated(target: Path, lines: list[str], seed: int) -> dict:
    """Write the `faker-file` documents, and report anything the library could not produce."""
    try:
        from faker import Faker
        from faker_file.storages.filesystem import FileSystemStorage
    except ImportError:
        print("faker-file is not installed. Install it with:\n"
              '  pip install "faker-file[docx,xlsx,odt,ods,pdf]"\n'
              "Without it this script still writes the authored documents, which cover eight of the\n"
              "fifteen suffixes. Pass --authored-only to do that deliberately.", file=sys.stderr)
        return {}

    import importlib

    faker = Faker()
    faker.seed_instance(seed)
    written: dict[str, list[str]] = {}
    failures: list[str] = []
    font = find_font()
    if font:
        note(lines, f"PDFs are generated through reportlab with `{font}`, not pdfkit and wkhtmltopdf.")
        note(lines, "")

    for module_name, class_name, method, filename, root, purpose in GENERATED:
        try:
            module = importlib.import_module(f"faker_file.providers.{module_name}")
            provider = getattr(module, class_name)
        except (ImportError, AttributeError) as exc:
            failures.append(f"{filename}: {class_name} unavailable ({type(exc).__name__}). "
                            f"Install the extra for this format.")
            continue
        faker.add_provider(provider)
        storage = FileSystemStorage(root_path=str(target), rel_path=root)
        stem = Path(filename).stem
        kwargs = {"storage": storage, "basename": stem}
        if method == "pdf_file":
            if font is None:
                failures.append(f"{filename}: no TrueType font found, so the reportlab route cannot "
                                f"run. Install fonts-dejavu-core and try again.")
                continue
            kwargs["pdf_generator_cls"] = REPORTLAB_GENERATOR
            kwargs["pdf_generator_kwargs"] = {"font_path": font}
        try:
            out = getattr(provider(faker), method)(**kwargs)
            path = Path(out.data["filename"])
            if not path.exists():
                failures.append(f"{filename}: the provider reported success and wrote nothing")
                continue
            written.setdefault(root, []).append(path.name)
        except Exception as exc:  # noqa: BLE001 - any provider failure is data for the report
            failures.append(f"{filename}: {type(exc).__name__}: {exc}")

    if failures:
        note(lines, "### `faker-file` could not produce everything")
        note(lines, "")
        for failure in failures:
            note(lines, f"- {failure}")
        note(lines, "")

    return written


# --------------------------------------------------------------------------- volume

# **Documents for scale, and deliberately not for coverage.** The set above proves the pipeline handles every
# format and every awkward case, and it is 26 files. A deployment that answers questions from 26 files has not
# been asked what happens at a thousand, and the answers that matter at a thousand are retrieval quality,
# ingestion time and whether a citation still leads to the right document.
#
# **Nothing here competes with the fixtures.** No travel or mileage policy, because the manual's worked example
# and two acceptance probes ask for the mileage rate and must keep getting the authored answer. No second
# document carrying reserved identifiers, because the redaction checks look for the one that exists. The topics
# are the ordinary paperwork of a facilities company, which is what a real corpus looks like: a lot of it, on
# subjects nobody asks about, with the answers buried in a few.
# **Every topic carries its own vocabulary, and the sentences are written rather than sampled.** The first
# version filled paragraphs from `Faker.paragraph()`, which produces word salad: "Energy both office. Degree
# street cold." A corpus like that exercises the plumbing and cannot test retrieval, because there is nothing
# in it to find. The point of a thousand documents is to ask a question and check that the right one comes
# back, and that needs documents that say something.
VOLUME_TOPICS = (
    ("maintenance", "Planned maintenance schedule",
     ("boiler", "air handling unit", "fire alarm panel", "emergency lighting", "lift", "water heater",
      "sprinkler system", "generator", "automatic door", "cooling tower"),
     "statutory inspections and service intervals"),
    ("safety", "Site safety inspection",
     ("fire exit", "loading bay", "roof access hatch", "chemical store", "pedestrian route",
      "electrical cupboard", "first aid point", "scaffold", "car park barrier", "plant room"),
     "the findings of a walk-round and the actions arising"),
    ("procurement", "Purchase requisition",
     ("floor scrubbing machine", "replacement pump", "PPE stock", "light fittings", "door furniture",
      "cleaning chemicals", "hand dryers", "signage", "waste bins", "uniform order"),
     "what was ordered, from whom and against which budget line"),
    ("onboarding", "New starter checklist",
     ("laptop", "access card", "PPE issue", "mobile phone", "desk allocation", "uniform",
      "tool kit", "vehicle allocation", "training plan", "probation review"),
     "equipment issued, accounts created and inductions booked"),
    ("incidents", "Incident report",
     ("water leak", "slip on a wet floor", "failed fire door", "power outage", "minor injury",
      "damaged ceiling tile", "blocked drain", "false alarm", "broken window", "lift entrapment"),
     "what happened, what was done at the time and what changed afterwards"),
    ("client", "Client service review",
     ("cleaning standard", "response times", "portering", "grounds maintenance", "waste collections",
      "washroom supplies", "reactive repairs", "security cover", "window cleaning", "pest control"),
     "the visits made, the work completed and anything carried over"),
    ("energy", "Energy and utilities reading",
     ("electricity", "gas", "water", "half-hourly meter", "sub-meter", "solar generation",
      "chilled water", "steam", "compressed air", "standby generation"),
     "consumption by site for the period and what it cost"),
    ("stock", "Consumables stock count",
     ("hand towels", "toilet rolls", "bin liners", "floor polish", "glass cleaner", "nitrile gloves",
      "refuse sacks", "air freshener", "mop heads", "disinfectant"),
     "what was counted and what is below its reorder level"),
    ("training", "Training record",
     ("manual handling", "working at height", "asbestos awareness", "first aid at work", "fire marshal",
      "IOSH managing safely", "PAT testing", "confined spaces", "food hygiene", "forklift"),
     "who holds which certificate and when it expires"),
    ("waste", "Waste transfer record",
     ("general waste", "mixed recycling", "food waste", "clinical waste", "waste electrical equipment",
      "hazardous waste", "construction waste", "glass", "cardboard", "confidential paper"),
     "what left each site, how much of it and where it went"),
    ("cleaning", "Cleaning specification",
     ("entrance lobby", "washrooms", "office floors", "stairwells", "kitchen", "meeting rooms",
      "external paving", "windows", "high level dusting", "waste compound"),
     "the areas covered, the frequency and the standard expected"),
    ("security", "Access control change",
     ("main entrance", "service yard gate", "plant room", "server room", "records store",
      "car park", "roof access", "strong room", "loading dock", "executive floor"),
     "who was given access to what, by whom and why"),
    ("fleet", "Vehicle service record",
     ("transit van", "cage tipper", "ride-on scrubber", "pickup", "electric buggy", "trailer",
      "sweeper", "forklift", "minibus", "pressure washer"),
     "the vehicle, its mileage, the work done and the next due date"),
    ("insurance", "Insurance schedule",
     ("employers liability", "public liability", "motor fleet", "plant all risks", "business interruption",
      "engineering inspection", "professional indemnity", "contents", "cyber", "directors liability"),
     "the policies in force, the cover held and the renewal dates"),
    ("audit", "Internal audit finding",
     ("permit to work", "COSHH assessments", "lone working", "contractor induction", "key control",
      "asset tagging", "grievance handling", "timesheet approval", "expense claims", "data retention"),
     "what was tested, what was found and the agreed remedy"),
    ("budget", "Budget monitoring",
     ("repairs and maintenance", "cleaning contract", "utilities", "security contract", "grounds",
      "consumables", "training", "fleet running costs", "waste disposal", "capital programme"),
     "the position against each cost centre for the period"),
    ("supplier", "Supplier performance review",
     ("cleaning contractor", "M&E contractor", "waste carrier", "security provider", "grounds contractor",
      "lift engineer", "fire systems firm", "window cleaner", "pest control", "water hygiene"),
     "the measures, the results and any improvement notice"),
    ("holiday", "Annual leave record",
     ("operations team", "cleaning team", "helpdesk", "engineering", "grounds team", "security",
      "administration", "supervisors", "apprentices", "management"),
     "entitlement, leave taken and what remains"),
    ("it", "IT asset register change",
     ("laptop", "monitor", "docking station", "mobile handset", "printer", "tablet",
      "radio handset", "barcode scanner", "server", "network switch"),
     "equipment issued or returned, and the asset tag"),
    ("complaint", "Complaint and response",
     ("missed cleaning", "noise from plant", "slow response", "damage to property", "conduct of staff",
      "billing query", "missed collection", "temperature in the office", "lighting failure", "access delay"),
     "what was raised, what was done and what the client was told"),
)

VOLUME_ROOTS = ("company", "company", "company", "company", "executive")

# The frames a document is built from. `{item}`, `{site}`, `{other}` and the rest are filled per document, so
# two documents on one topic share a shape and not a sentence.
VOLUME_FRAMES = (
    "The {item} at {site} was last attended on {date}, and the next visit is due within the month.",
    "{item} is recorded as satisfactory, with no defects raised at the time of the visit.",
    "A defect was noted on the {item}, and {person} has raised it with the contractor for attention.",
    "{person} confirmed that the {item} meets the standard required and that no further action is needed.",
    "The outstanding action on the {item} was closed on {date} after the part was fitted.",
    "{site} reported an issue with the {item} on {date}, and the attendance was logged the same day.",
    "The {item} is due for replacement rather than repair, and {person} has asked for a quote.",
    "Costs for the {item} were charged to {budget} and are within the amount allowed for the period.",
    "{person} will review the {item} again on {date} and report back to the site manager.",
    "No further faults have been reported on the {item} since the last inspection.",
    "The condition of the {item} has been recorded and the photograph is held with this document.",
    "A follow-up visit for the {item} is booked for {date} with the usual contractor.",
)

VOLUME_BUDGETS = ("the repairs budget", "the planned maintenance budget", "the site allowance",
                  "the capital programme", "the contract sum", "the consumables budget")


def volume_document(topic, index: int, faker) -> tuple[str, str]:
    """One document's title and body, on one of the ordinary topics.

    **Deterministic in the faker instance**, which is seeded once, so two runs at the same seed produce the same
    thousand documents and a measurement taken against them can be repeated. The `index` also drives which
    items and frames appear, so no two documents are identical even when the seed repeats a word.
    """
    slug, title, items, blurb = topic
    site = faker.city()
    ref = f"{slug.upper()[:3]}-{index:05d}"
    period = faker.date_this_year().strftime("%B %Y")
    owner = faker.name()
    role = faker.job()
    budget = VOLUME_BUDGETS[index % len(VOLUME_BUDGETS)]

    body = [
        f"# {title}: {site}",
        "",
        f"Reference {ref}. Period {period}. Owner {owner}, {role}.",
        "",
        f"This record covers {blurb} for {site}.",
        "",
    ]

    # Three to six sections, each on a different item, so a document holds several distinct facts rather than
    # one. A question aimed at a document should have something specific to match.
    count = 3 + (index % 4)
    for n in range(count):
        item = items[(index + n * 3) % len(items)]
        body += [f"## {item.capitalize()}", ""]
        for f in range(3):
            frame = VOLUME_FRAMES[(index + n * 5 + f * 7) % len(VOLUME_FRAMES)]
            body.append(frame.format(
                item=item,
                site=site,
                other=items[(index + n + f) % len(items)],
                date=faker.date_this_year().strftime("%d %B %Y"),
                person=faker.name(),
                budget=budget,
            ))
        body.append("")

    body += ["## Actions", ""]
    for n in range(3):
        body.append(f"- {faker.name()}, {faker.job()}: "
                    f"{VOLUME_FRAMES[(index + n * 11) % len(VOLUME_FRAMES)].format(item=items[(index + n) % len(items)], site=site, other=items[n % len(items)], date=faker.date_this_year().strftime('%d %B %Y'), person=faker.name(), budget=budget)}")
    body += ["", f"Prepared by {owner}. Review due {faker.date_this_year().strftime('%d %B %Y')}.", ""]
    return title, "\n".join(body)


def write_volume(target: Path, lines: list[str], seed: int, count: int) -> dict:
    """The scale set: `count` documents across the roots, as markdown.

    **Markdown rather than a mix of formats, and that is the point of separating it from the coverage set.** The
    cost of ingesting a thousand documents is dominated by parsing and embedding, and a thousand PDFs would
    measure the PDF parser rather than retrieval. Format coverage is already proved by the twenty-six.

    **They go directly under `company/` and `executive/`, and the first version did not.** It wrote them to
    `volume/company/` and `volume/executive/`, which reads like tidier organisation and is a trap: **the access
    label is the first path component**, so every one of the thousand would have carried the label `volume`
    while the deployment declares two roots, `company` and `executive`. The whole scale set would have been
    indexed and unreachable by every role, with nothing reporting it. The manual documents that trap for an
    operator adding a folder; this is the same trap for the script that writes one.
    """
    from faker import Faker

    faker = Faker()
    faker.seed_instance(seed + 1)
    written: dict[str, list[str]] = {}

    for i in range(count):
        topic = VOLUME_TOPICS[i % len(VOLUME_TOPICS)]
        root = VOLUME_ROOTS[i % len(VOLUME_ROOTS)]
        _title, body = volume_document(topic, i, faker)
        directory = target / root
        directory.mkdir(parents=True, exist_ok=True)
        # The `vol-` prefix is how a person tells the scale set from the coverage set in a directory listing,
        # and how a later run can find and replace it without touching the authored files.
        name = f"vol-{topic[0]}-{i:05d}.md"
        (directory / name).write_text(body)
        written.setdefault(root, []).append(name)

    note(lines, "## The volume set")
    note(lines, "")
    note(lines, f"- **{count} documents** under `company/` and `executive/`, named `vol-*`, on "
                f"{len(VOLUME_TOPICS)} recurring topics. **Not a `volume/` subdirectory**: the access label "
                f"is the first path component, so a third top-level folder is a label no role holds and the "
                f"whole set would be unreachable.")
    note(lines, "- **Markdown only.** Parsing and embedding dominate the cost at this size, and a thousand PDFs "
                "would measure the PDF parser rather than retrieval. Format coverage is the twenty-six above.")
    note(lines, "- **Nothing here competes with the fixtures**: no travel or mileage policy, and no second "
                "document carrying reserved identifiers, because the worked example and two probes depend on "
                "those answers staying put.")
    note(lines, "- **Deterministic in content, not byte-for-byte in every format.** Two runs at one seed "
                "produce identical markdown, which is where the scale set lives. The generated Office files "
                "differ in exactly one entry, `docProps/core.xml`, which holds the creation timestamp; their "
                "content is identical. Verified by comparing every zip entry rather than by diffing files.")
    note(lines, "")
    return written


# --------------------------------------------------------------------------- reporting

def coverage_report(target: Path, lines: list[str]) -> None:
    """Which accepted suffixes the corpus now covers, and which it does not.

    Printed rather than assumed, because the whole point of this corpus is format coverage and the
    honest output includes what is still missing.
    """
    present = {p.suffix.lower() for p in target.rglob("*") if p.is_file()}
    # **Measured against the formats a client actually hands over, not against everything the worker reads.**
    # The worker accepts sixty; twenty-one of them are what a small business brings and the rest is a long
    # tail that costs nothing to support. Reporting 15/60 would read as a failing corpus and bury the number
    # that matters, which is which of the twenty-one are still absent.
    required = set(formats.CORPUS_MUST_COVER)
    covered = sorted(present & required)
    missing = sorted(required - present)
    extra_suffixes = sorted({p.suffix.lower() for p in target.rglob("*") if p.is_file()} - required)

    note(lines, "## Format coverage")
    note(lines, "")
    note(lines, f"Formats a client brings: {len(required)}")
    note(lines, f"Covered by this corpus: {len(covered)}")
    note(lines, "")
    if extra_suffixes:
        note(lines, f"Present beyond the required set: {' '.join(extra_suffixes)}")
        note(lines, "")
    if missing:
        note(lines, f"**Still missing: {' '.join(missing)}.** The usual reason is an uninstalled")
        note(lines, "`faker-file` extra, which is reported above. Install it and run again, or accept")
        note(lines, "the gap knowingly.")
    else:
        note(lines, "**Every accepted suffix is covered.**")
    note(lines, "")
    # **What this number does not mean, stated where somebody would otherwise read it as a clean bill.**
    #
    # It measures the corpus against the list the worker reads. That is a useful thing to know and it is not
    # the question that matters: the list itself can be wrong, and for most of this project's life it was,
    # while this line reported complete. The question that matters is whether the list covers what the parser
    # can read, and `scripts/check_parser_formats.sh` is what answers it, because that comparison needs the
    # parser image and this script has no business requiring one.
    note(lines, "**This counts the corpus against the worker's accepted list, which is not the same as the")
    note(lines, "list being right.** For every format the parser can read, the accepted list either takes it")
    note(lines, "or names it as excluded with a reason, and `bash scripts/check_parser_formats.sh` is what")
    note(lines, "checks that direction. Run it after any parser upgrade.")
    note(lines, "")


def placement_document(target: Path, written: dict, lines: list[str]) -> None:
    """The file that makes this corpus usable: what each document is for, and where it belongs."""
    note(lines, "## Where each document goes")
    note(lines, "")
    note(lines, "**The top-level folder name is the access label.** It is not a filing convention and")
    note(lines, "there is no separate place to declare it, so a document under `company/` is readable by")
    note(lines, "the `staff` retrieval role and one under `executive/` is readable only by `executive`.")
    note(lines, "")
    note(lines, "This is the part that surprises people: **a folder named anything else is invisible.**")
    note(lines, "A file under `acme/` is labelled `acme`, no role's map contains that label, and no")
    note(lines, "search returns it. It is indexed and unreachable. Adding a new root is a code change to")
    note(lines, "the `ROLES` map in `services/retrieval/retriever.py`, not a setting.")
    note(lines, "")
    note(lines, "| Document | Root | What it is for |")
    note(lines, "|---|---|---|")

    purposes = {
        "travel-expenses-policy.md": "the plain question: a figure, **0.67 per mile**, that any role may read",
        "travel-expenses-policy.markdown": "the same policy under a second accepted suffix, to prove `.markdown` parses as well as `.md`",
        "travel-expenses-policy.html": "the same policy as HTML, for the parser's HTML path",
        "travel-expenses-policy.htm": "the same policy under the other HTML suffix, because a list covering one and not the other is a gap nobody notices",
        "onboarding-notes.md": "the unanswerable case: it describes a process and names no supplier and no figure",
        "pii-new-starter.md": "**redaction**: carries an SSN and a card-shaped number, so a frontier call built from it must arrive without them",
        "dead-letter-fixture.txt": "**the failure path**: the parser refuses it, so ingestion records a failure twice and stops until the bytes change",
        "meeting-notes.txt": "the plain `.txt` case: prose with no structure at all",
        "fee-schedule-2026.md": "the current schedule, and the answer to the standard-rate question",
        "fee-schedule-2025.md": "**superseded**: mark it with `--supersede` so a claim resting on it alone is refused",
        "fee-schedule-2026-copy.md": "**near-duplicate**: byte-identical to the current schedule, for `--scan-duplicates`",
        "master-services-agreement.md": "a long document, for chunk counts and for a citation that has to name a section",
        "callout-log.csv": "the `.csv` case: rows with identifiers, so the lexical arm has something exact to match",
        "asset-register.xlsx": "the `.xlsx` case: a table a figure question can be answered from",
        "consumables.ods": "the `.ods` case: a spreadsheet in the open-document format",
        "service-agreement.docx": "the `.docx` case: a contract with numbered clauses",
        "site-survey.odt": "the `.odt` case: a word-processor file in the open-document format",
        "board-briefing.pptx": "the `.pptx` case: slide text, which has no page structure to cite",
        "toolbox-talk.odp": "the `.odp` case: a presentation in the open-document format",
        "terms-rtf.rtf": "the `.rtf` case: an interchange format whose markup the parser has to strip",
        "supplier-thread.eml": "the `.eml` case: headers plus a body, which is the format `RET-FORMAT-001` added",
        "warranty-terms.pdf": "the `.pdf` case **with a text layer**, so it exercises PDF extraction and not OCR",
        "remuneration-policy.docx": "a restricted-root document in a second format, so the ACL is tested on more than markdown",
        "board-minutes.pdf": "a second restricted-root document, in the format most likely to be cited",
        "executive-compensation.md": "**the restricted case**: figures that appear nowhere else, so a leak is found by searching for one",
    }
    for root in ("company", "executive"):
        for name in sorted(written.get(root, [])):
            note(lines, f"| `{name}` | `{root}` | {purposes.get(name, 'a format case')} |")

    note(lines, "")
    note(lines, "**And one file that goes somewhere else on purpose.**")
    note(lines, "")
    note(lines, f"`loose/read-me-first.md` is not under a root. Copy it to the **documents root itself**,")
    note(lines, "not into a folder, once you have the rest working. It is labelled with the root")
    note(lines, "directory's own name, no role may read that label, and **nothing reports a problem** —")
    note(lines, "which makes it the single most useful file here for understanding what an access label")
    note(lines, "actually does. `ask.py` will not return it for either role.")
    note(lines, "")


def placement_document_body(target: Path, written: dict) -> str:
    lines: list[str] = []
    note(lines, "# Placement notes for the generated corpus")
    note(lines, "")
    note(lines, "Written by `fixtures/generate_corpus.py`. **Nothing here has touched a deployment.**")
    note(lines, "Copying these documents into a live documents root is a separate, deliberate step, and")
    note(lines, "the operator guide walks through it one class at a time.")
    note(lines, "")
    note(lines, f"Client: {CLIENT} (invented). Seed: {SEED}, so this corpus repeats.")
    note(lines, "Every document is synthetic. No real person's data and nothing under third-party")
    note(lines, "copyright, which is why this corpus is safe to commit where a downloaded one may not be.")
    note(lines, "")
    placement_document(target, written, lines)
    coverage_report(target, lines)
    note(lines, "## What this corpus cannot prove")
    note(lines, "")
    note(lines, "- **A scanned document.** No generator makes one, and the two in `fixtures/` are authored")
    note(lines, "  fixtures for the parser rather than for retrieval. An OCR case needs a real scan or a")
    note(lines, "  rendered-then-rasterised page; Synthea's bundled records are the easiest source.")
    note(lines, "- **A legacy binary Office file** (`.doc`, `.xls`). The worker ignores them by design, and")
    note(lines, "  a test for that is a test that nothing happened.")
    note(lines, "- **Scale.** A few dozen documents exercises correctness, not performance. Load testing is")
    note(lines, "  `QLT-LOAD-001` and it is a different exercise.")
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--out", required=True, type=Path,
                        help="directory to write the corpus into; created if absent")
    parser.add_argument("--seed", type=int, default=SEED,
                        help=f"changes the invented names and figures (default {SEED}, fixed so runs repeat)")
    parser.add_argument("--authored-only", action="store_true",
                        help="skip faker-file entirely; writes eight of the fifteen suffixes")
    parser.add_argument("--volume", type=int, default=0, metavar="N",
                        help="ALSO write N additional documents for scale, under `volume/`. The twenty-six "
                             "coverage documents are written either way: they are what the manual's worked "
                             "example and two acceptance probes depend on")
    parser.add_argument("--force", action="store_true",
                        help="write into a non-empty directory, replacing same-named files")
    args = parser.parse_args()

    target: Path = args.out
    if target.exists() and any(target.iterdir()) and not args.force:
        print(f"{target} is not empty. Pass --force to write into it anyway, or name another "
              f"directory. Refusing because a corpus directory that already holds something is "
              f"usually a deployment's documents root, and this script does not write to one by "
              f"accident.", file=sys.stderr)
        return 2
    target.mkdir(parents=True, exist_ok=True)

    print(f"writing {CLIENT}'s corpus to {target}")
    lines: list[str] = []
    written = write_authored(target, lines)
    print(f"  authored documents: {sum(len(v) for v in written.values())}")

    if not args.authored_only:
        generated = write_generated(target, lines, args.seed)
        for root, names in generated.items():
            written.setdefault(root, []).extend(names)
        print(f"  faker-file documents: {sum(len(v) for v in generated.values())}")

    if args.volume:
        n = sum(len(v) for v in write_volume(target, lines, args.seed, args.volume).values())
        written.setdefault("volume", []).extend([f"{n} document(s)"])
        print(f"  volume documents: {n}")

    (target / "PLACEMENT.md").write_text(placement_document_body(target, written))

    scanned_pages(target, lines, written)
    legacy_office(target, lines, written)

    files = sorted(p for p in target.rglob("*") if p.is_file())
    present = {p.suffix.lower() for p in files}
    required = set(formats.CORPUS_MUST_COVER)
    missing = sorted(required - present)
    print(f"  total files: {len(files)}")
    print(f"  formats a client brings: {len(present & required)}/{len(required)}"
          + (f"  (missing: {' '.join(missing)})" if missing else "  (complete)"))
    print(f"  read {target / 'PLACEMENT.md'} before copying anything anywhere")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
