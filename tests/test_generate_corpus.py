"""The corpus generator produces what it claims, and refuses to write anywhere dangerous.

`fixtures/generate_corpus.py` exists because nine of the fifteen file formats the ingestion worker
accepts had never been through the pipeline, and because a corpus of valid documents can never exercise
the failure paths: no amount of downloading gives you an exact duplicate, a stated replacement, or a file
the parser refuses.

Two things about it are worth a test rather than a hope:

  * **It writes every format it says it does.** The whole point is coverage, and a generator that
    silently drops the PDFs is a corpus with a hole nobody notices until a client supplies a PDF.
  * **It refuses to write into a populated directory without `--force`.** This script writes DOCX, XLSX
    and PDF files into a tree the caller names. Getting that wrong in one direction destroys a client's
    documents root. The refusal is the safeguard, so it is asserted.

The full run is slow — it generates twenty-seven files through several libraries — so the coverage test
is marked and skipped when the generator's dependencies are absent, which is the state of a clean
checkout that has only installed the service dependencies.
"""
from __future__ import annotations

import importlib
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
GENERATOR = ROOT / "fixtures" / "generate_corpus.py"

# Every suffix the ingestion worker accepts. Duplicated from `services/ingestion/ingest.py` on purpose:
# a test that imports the list it is checking cannot notice the list changing.
EXPECTED_SUFFIXES = {".pdf", ".docx", ".pptx", ".xlsx", ".html", ".htm", ".md", ".markdown",
                     ".txt", ".csv", ".rtf", ".odt", ".ods", ".odp", ".eml"}


def have_generator_deps() -> bool:
    try:
        importlib.import_module("faker_file")
        importlib.import_module("faker")
    except ImportError:
        return False
    return True


requires_deps = pytest.mark.skipif(
    not have_generator_deps(),
    reason='faker-file is not installed; pip install "faker-file[docx,xlsx,pptx,odt,ods,odp,pdf]"')


def run_generator(out: Path, *extra: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(GENERATOR), "--out", str(out), *extra],
        capture_output=True, text=True, timeout=600,
    )


def test_the_generator_refuses_a_populated_directory(tmp_path):
    """The safeguard, and it is the one that matters.

    This script writes into a tree the caller names. A run pointed at a live documents root by mistake
    would add twenty-seven files to a client's corpus, and the refusal is what stands between the two.
    """
    populated = tmp_path / "looks-like-a-deployment"
    populated.mkdir()
    (populated / "some-clients-document.md").write_text("do not touch")

    result = run_generator(populated)

    assert result.returncode == 2, result.stdout + result.stderr
    assert "--force" in result.stderr
    # And it really did not write: the existing file is untouched and nothing was added.
    assert [p.name for p in populated.iterdir()] == ["some-clients-document.md"]


def test_authored_only_covers_its_formats_without_any_generator_dependency(tmp_path):
    """The generator's own fallback has to work on a machine with nothing installed.

    `--authored-only` is what a reviewer with a bare checkout can run, and it must cover the formats the
    script writes by hand rather than reporting an empty corpus.
    """
    result = run_generator(tmp_path / "corpus", "--authored-only")
    assert result.returncode == 0, result.stdout + result.stderr

    present = {p.suffix.lower() for p in (tmp_path / "corpus").rglob("*") if p.is_file()}
    for suffix in (".md", ".markdown", ".html", ".htm", ".txt"):
        assert suffix in present, f"authored documents should cover {suffix}, saw {sorted(present)}"


def test_placement_notes_are_written_and_name_the_invisible_folder_rule(tmp_path):
    """The file that makes the corpus usable.

    The single most important thing a reader of this corpus has to know is that the top-level folder name
    IS the access label, and that a folder named anything else makes its contents unreachable. The notes
    have to say so, because the loose file exists precisely to demonstrate it.
    """
    result = run_generator(tmp_path / "corpus", "--authored-only")
    assert result.returncode == 0, result.stdout + result.stderr

    notes = (tmp_path / "corpus" / "PLACEMENT.md").read_text()
    assert "the top-level folder name is the access label" in notes.lower()
    assert "invisible" in notes.lower() or "unreachable" in notes.lower()
    assert "read-me-first.md" in notes


@requires_deps
def test_the_full_run_covers_every_accepted_suffix(tmp_path):
    """**The claim the corpus exists to make.**

    Fifteen suffixes are accepted by the worker. Before this generator, six were exercised by anything in
    the repository. If this test fails, the corpus has a format hole and the number to look at is in the
    assertion's message.
    """
    result = run_generator(tmp_path / "corpus")
    assert result.returncode == 0, result.stdout + result.stderr

    present = {p.suffix.lower() for p in (tmp_path / "corpus").rglob("*") if p.is_file()}
    missing = sorted(EXPECTED_SUFFIXES - present)
    assert not missing, (
        f"the corpus does not cover {missing}. The usual cause is a missing faker-file extra, which the "
        f"generator reports in PLACEMENT.md rather than failing on."
    )


@requires_deps
def test_the_full_run_writes_both_roots_and_the_loose_file(tmp_path):
    """The corpus has to be usable for an access-control test, not just a format test.

    `company/` and `executive/` exercise the two roles, and the loose file exercises the failure that has
    no error message. A corpus missing either is a corpus that cannot test the ACL.
    """
    result = run_generator(tmp_path / "corpus")
    assert result.returncode == 0, result.stdout + result.stderr

    corpus = tmp_path / "corpus"
    assert (corpus / "company").is_dir()
    assert (corpus / "executive").is_dir()
    assert (corpus / "loose" / "read-me-first.md").is_file()
    # The restricted root must be small and specific: it is the one a leak would show up in.
    assert len(list((corpus / "executive").iterdir())) >= 2


@requires_deps
def test_the_pdfs_have_a_text_layer(tmp_path):
    """A PDF with no text layer is an OCR test, and this corpus is not that.

    The generated PDFs are here to exercise the ordinary PDF path, so they must carry extractable text.
    If reportlab ever silently produced images, the corpus would start testing OCR by accident and the
    retrieval numbers would move for a reason nobody would guess.
    """
    result = run_generator(tmp_path / "corpus")
    assert result.returncode == 0, result.stdout + result.stderr

    pdfs = [p for p in (tmp_path / "corpus").rglob("*.pdf")]
    assert pdfs, "the corpus should contain PDFs"
    for pdf in pdfs:
        raw = pdf.read_bytes()
        # A cheap structural check rather than a parser dependency: a text-bearing PDF contains font
        # objects and a content stream, an image-only one does not.
        assert b"/Font" in raw, f"{pdf.name} has no font object, so it carries no text"
