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
"""
from __future__ import annotations

import argparse
import csv
import io
import shutil
import sys
import textwrap
import zipfile
from pathlib import Path

# The suffix set the worker accepts, copied here rather than imported: this script must be runnable
# without the service code on the path, and a drift between the two is caught by the coverage check at
# the end of a run rather than by an import error here.
ACCEPTED_SUFFIXES = (".pdf", ".docx", ".pptx", ".xlsx", ".html", ".htm", ".md", ".markdown",
                     ".txt", ".csv", ".rtf", ".odt", ".ods", ".odp", ".eml")

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
    ("rtf_file", "RtfFileProvider", "rtf_file", "terms-rtf.rtf", "company",
     "the .rtf case: older interchange format, plain markup"),
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


# --------------------------------------------------------------------------- reporting

def coverage_report(target: Path, lines: list[str]) -> None:
    """Which accepted suffixes the corpus now covers, and which it does not.

    Printed rather than assumed, because the whole point of this corpus is format coverage and the
    honest output includes what is still missing.
    """
    present = {p.suffix.lower() for p in target.rglob("*") if p.is_file()}
    covered = sorted(present & set(ACCEPTED_SUFFIXES))
    missing = sorted(set(ACCEPTED_SUFFIXES) - present)

    note(lines, "## Format coverage")
    note(lines, "")
    note(lines, f"Accepted by the worker: {len(ACCEPTED_SUFFIXES)}")
    note(lines, f"Covered by this corpus: {len(covered)}")
    note(lines, "")
    if missing:
        note(lines, f"**Still missing: {' '.join(missing)}.** The usual reason is an uninstalled")
        note(lines, "`faker-file` extra, which is reported above. Install it and run again, or accept")
        note(lines, "the gap knowingly.")
    else:
        note(lines, "**Every accepted suffix is covered.**")
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
        "onboarding-notes.md": "the unanswerable case: it describes a process and names no supplier or figure",
        "pii-new-starter.md": "**redaction**: carries an SSN and a card-shaped number, so a frontier call built from it must arrive without them",
        "dead-letter-fixture.txt": "**the failure path**: the parser refuses it, so ingestion records a failure twice and stops",
        "fee-schedule-2026.md": "the current schedule, and the answer to the standard-rate question",
        "fee-schedule-2025.md": "**superseded**: mark it with `--supersede` so a claim resting on it alone is refused",
        "fee-schedule-2026-copy.md": "**near-duplicate**: byte-identical to the current schedule, for `--scan-duplicates`",
        "master-services-agreement.md": "a long document, for chunk counts and for a citation that has to name a section",
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

    (target / "PLACEMENT.md").write_text(placement_document_body(target, written))

    files = sorted(p for p in target.rglob("*") if p.is_file())
    present = {p.suffix.lower() for p in files}
    missing = sorted(set(ACCEPTED_SUFFIXES) - present)
    print(f"  total files: {len(files)}")
    print(f"  suffixes covered: {len(present & set(ACCEPTED_SUFFIXES))}/{len(ACCEPTED_SUFFIXES)}"
          + (f"  (missing: {' '.join(missing)})" if missing else "  (complete)"))
    print(f"  read {target / 'PLACEMENT.md'} before copying anything anywhere")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
