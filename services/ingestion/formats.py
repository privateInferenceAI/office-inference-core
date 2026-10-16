"""What the pipeline will read, loaded from one file rather than restated in two.

**Why this module exists.** The accepted-suffix set lived in `ingest.py`, and `fixtures/generate_corpus.py`
carried a second copy. The corpus's format-coverage report compared the corpus against that second copy, so
its denominator was the list itself: it could confirm that two copies of one list agreed, and it reported
`15/15 formats covered (complete)` while a third of the formats the parser reads were unreachable from the
documents folder. Nothing in the pipeline was wrong about any individual format; the measurement could not
see the gap.

So the list is data, both readers load it, and the test that guards it compares it against **the parser's own
advertised formats** rather than against itself. That direction is the whole point: an allowlist checked
against an allowlist is a mirror, not a test.
"""
from __future__ import annotations

import json
from pathlib import Path

FORMATS_PATH = Path(__file__).with_name("formats.json")

# Loading is strict on purpose. A worker that cannot find its own format list has no business deciding which
# of a client's documents to read, and falling back to a hardcoded set would put the list back in two places
# with a different failure mode.
with FORMATS_PATH.open() as handle:
    _FORMATS = json.load(handle)

ACCEPTED_SUFFIXES = frozenset(_FORMATS["accepted"])

#: Formats the parser reads that the pipeline deliberately will not, each with its reason. Kept so the gap
#: between what the parser can do and what the product does is a decision on the record rather than an
#: absence nobody can explain.
EXCLUDED_SUFFIXES: dict[str, str] = dict(_FORMATS["excluded"])

#: The formats the coverage corpus has to demonstrate, which is a much shorter list than the one the worker
#: reads. It is the set a small business actually hands over: word processor files, spreadsheets, slides,
#: scans, saved email and saved web pages. Measuring a fixture corpus against all sixty accepted formats
#: would report a failure that means nothing and hide the failure that means something.
CORPUS_MUST_COVER = frozenset(_FORMATS["corpus_must_cover"])


def is_accepted(path) -> bool:
    """Whether the worker will pick this file up."""
    return Path(path).suffix.lower() in ACCEPTED_SUFFIXES


def is_excluded(path) -> bool:
    """Whether this is a format we chose not to read, as opposed to one nobody considered."""
    return Path(path).suffix.lower() in EXCLUDED_SUFFIXES
