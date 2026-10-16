"""Does the pipeline read what the parser can read, and does anyone know when it stops?

**These are the tests that were missing, and their absence is why a client's scanned TIFF was invisible.**

The old coverage check compared the corpus against the worker's own accepted-suffix list -- and that list was
also the denominator, so it could only confirm that the corpus held every format the list named. It printed
`15/15 formats covered (complete)` while a third of the formats the parser reads were unreachable from the
documents folder. **An allowlist measured against itself is a mirror, not a test.**

So these measure in the other direction. `docs/parser-formats.json` is what the parser advertises, dumped from
the parser image by `scripts/dump_parser_formats.py`. Every extension in it must be either accepted or
excluded with a reason, and an exclusion whose reason does not say how it was decided is a failure. A parser
upgrade that adds a format therefore fails here until somebody makes a decision, which is the control that was
absent.

`test_the_dumped_format_list_matches_the_running_parser` closes the last gap: without it the committed file
could drift from the deployed parser and these tests would pass against a stale copy of the truth.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "services" / "ingestion"))

import formats  # noqa: E402

PARSER_FORMATS = json.loads((ROOT / "docs" / "parser-formats.json").read_text())
ADVERTISED: dict[str, list[str]] = PARSER_FORMATS["formats"]


def advertised_extensions() -> dict[str, list[str]]:
    """Every extension the parser claims, mapped to the formats that claim it."""
    out: dict[str, list[str]] = {}
    for name, extensions in ADVERTISED.items():
        for extension in extensions:
            out.setdefault(f".{extension.lower().lstrip('.')}", []).append(name)
    return out


def effective_suffix(extension: str) -> str:
    """The suffix the worker would actually see.

    `dclg.xml` and `tar.gz` are compound, and `Path('x.dclg.xml').suffix` is `.xml`: the match the worker
    makes is on the last component, so a coverage test has to make the same one or it reports a gap that
    cannot exist.
    """
    return Path("x" + extension).suffix.lower()


def test_every_format_the_parser_advertises_is_accepted_or_excluded():
    """**The control.** No format may be absent from both lists without anybody saying why.

    This is the assertion that would have failed the day a client's `.tiff` was dropped in silence, and it is
    the one nobody had written.
    """
    advertised = advertised_extensions()
    unaccounted = sorted(
        extension for extension in advertised
        if effective_suffix(extension) not in formats.ACCEPTED_SUFFIXES
        and effective_suffix(extension) not in formats.EXCLUDED_SUFFIXES
    )
    assert not unaccounted, (
        "the parser reads these and nobody has decided whether the pipeline should:\n  "
        + "\n  ".join(f"{e}  (from {', '.join(advertised[e])})" for e in unaccounted)
        + "\n\nDecide in services/ingestion/formats.json: accept it, or exclude it with a reason. "
          "An absence here is how a client's document type goes missing without anyone noticing."
    )


def test_no_exclusion_is_undocumented():
    """An exclusion without a reason is the defect this file exists to catch, wearing a different hat."""
    for extension, reason in formats.EXCLUDED_SUFFIXES.items():
        assert isinstance(reason, str) and len(reason) > 40, (
            f"{extension} is excluded and the reason does not explain it: {reason!r}")
        assert any(word in reason.upper() for word in ("TESTED", "UNVERIFIED", "REFUSED", "BY DESIGN")), (
            f"{extension}'s reason does not say how it was decided. Label it TESTED, UNVERIFIED, "
            f"REFUSED PENDING A DECISION, or BY DESIGN, because those are four different answers.")


def test_nothing_is_both_accepted_and_excluded():
    overlap = formats.ACCEPTED_SUFFIXES & set(formats.EXCLUDED_SUFFIXES)
    assert not overlap, f"these are accepted and excluded at once: {sorted(overlap)}"


def test_the_formats_a_client_keeps_records_in_are_accepted():
    """The regression guard, stated as the business case rather than as a list of extensions.

    Every one of these was refused in silence before this change, and the parser could read all of them: OCR
    is installed, and LibreOffice reads the legacy Office formats once its Calc and Impress components are
    present. The allowlist was the only thing in the way.
    """
    for extension in (".tif", ".tiff", ".jpg", ".jpeg", ".png",   # a scanner or a phone camera
                      ".doc", ".xls", ".ppt",                      # what a bookkeeper kept since 2009
                      ".docm", ".xlsm", ".pptm",                   # macro-enabled, routine in an office
                      ".dotx", ".xltx", ".potx",                   # templates
                      ".mht", ".mhtml", ".xhtml",                  # saved web pages
                      ".odt", ".ods", ".odp", ".ott", ".ots", ".otp",
                      ".epub", ".tex", ".vtt"):                    # verified working 2026-10-16
        assert extension in formats.ACCEPTED_SUFFIXES, f"{extension} should be read and is not"


def test_operating_system_debris_is_not_treated_as_an_unreadable_document():
    """A count that includes `.DS_Store` is a count an operator learns to ignore, and the count is the point."""
    import ingest
    for junk in (".DS_Store", "Thumbs.db", "notes.tmp", "draft.bak", "x.lock", "y.crdownload", "z.part"):
        assert not formats.is_accepted(junk)
        assert Path(junk).suffix.lower() in ingest.IGNORED_SUFFIXES or Path(junk).name.startswith("."), (
            f"{junk} would be reported to an operator as an unreadable document")


def test_the_dumped_format_list_matches_the_running_parser():
    """**Without this the other tests pass against a stale copy of the truth.**

    `docs/parser-formats.json` is a photograph of the parser at one moment. A parser upgrade that adds or
    removes a format changes the parser and not the photograph, so every assertion above would keep passing
    while the deployed parser moved away from them.

    The comparison runs wherever docling is importable, which is the parser image. Everywhere else it skips
    with the instruction rather than passing quietly, because a test that cannot run is not a test that
    passed.

        docker cp scripts/dump_parser_formats.py parser:/tmp/
        docker exec parser python3 /tmp/dump_parser_formats.py --out /tmp/parser-formats.json
        docker cp parser:/tmp/parser-formats.json docs/parser-formats.json

    Regenerating is step 3.6 of the runbook, and the diff is the point: a parser upgrade that adds a format
    shows up as a change to a committed file instead of as an absence nobody can see.
    """
    try:
        from docling.datamodel.base_models import FormatToExtensions
    except ImportError:
        pytest.skip("docling is not importable here. Run this inside the parser image, or regenerate "
                    "docs/parser-formats.json with scripts/dump_parser_formats.py and diff it.")

    live: dict[str, list[str]] = {}
    for fmt, extensions in FormatToExtensions.items():
        live[str(fmt).rsplit(".", 1)[-1]] = sorted({e.lower().lstrip(".") for e in extensions})

    committed = {name: sorted(e.lower().lstrip(".") for e in exts) for name, exts in ADVERTISED.items()}
    added = {f: e for f, e in live.items() if f not in committed}
    removed = {f: e for f, e in committed.items() if f not in live}
    changed = {f: (committed[f], live[f]) for f in live if f in committed and committed[f] != live[f]}

    assert not (added or removed or changed), (
        "the parser's format list has moved and docs/parser-formats.json has not.\n"
        f"  added:   {added}\n  removed: {removed}\n  changed: {changed}\n"
        "Regenerate the file and decide each new format in services/ingestion/formats.json."
    )
