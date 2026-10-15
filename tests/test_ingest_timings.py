"""The phase timings, and the line that answers why an ingest is slow.

**Why these tests exist at all.** Two attempts to say what a slow ingest waits on were wrong, and both
were wrong in the same way: the measurement was taken while the ingest was running, so it measured a queue
rather than a cost. The worker recorded nothing per phase, so no real run could answer the question. These
hold the shape of the answer, because the arithmetic below is easy to get subtly wrong and a wrong split is
worse than no split: it sends somebody to buy a GPU for a problem that is parsing.
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "services" / "ingestion"))

import ingest  # noqa: E402


def test_the_phases_sum_to_the_total():
    t = ingest.PhaseTimings(parse_ms=10, chunk_ms=5, embed_ms=80, write_ms=5)
    assert t.total_ms == 100


def test_shares_are_fractions_of_the_whole():
    t = ingest.PhaseTimings(parse_ms=25, chunk_ms=25, embed_ms=25, write_ms=25)
    assert t.shares() == {"parse_ms": 0.25, "chunk_ms": 0.25, "embed_ms": 0.25, "write_ms": 0.25}


def test_shares_of_nothing_is_empty_rather_than_all_zeroes():
    # **The distinction matters in the summary line.** A row of 0% reads like a fast run; no line at all
    # reads like a run that did not ingest anything, which is what happened.
    assert ingest.PhaseTimings().shares() == {}


def test_add_accumulates_every_phase():
    total = ingest.PhaseTimings()
    total.add(ingest.PhaseTimings(parse_ms=1, chunk_ms=2, embed_ms=3, write_ms=4))
    total.add(ingest.PhaseTimings(parse_ms=10, chunk_ms=20, embed_ms=30, write_ms=40))
    assert (total.parse_ms, total.chunk_ms, total.embed_ms, total.write_ms) == (11, 22, 33, 44)


def test_the_phase_list_covers_every_field_the_type_has():
    # **A phase added to the type and missed in the list would be silently absent from the summary**, which
    # is the failure mode of every hand-maintained pairing.
    declared = {f for f in ingest.PhaseTimings.__dataclass_fields__}
    listed = {field for field, _label in ingest.PHASES}
    assert declared == listed, f"the type and the summary disagree: {declared ^ listed}"


def test_the_line_names_every_phase_and_the_total():
    line = ingest.timings_line("timing", ingest.PhaseTimings(parse_ms=1, chunk_ms=2, embed_ms=3, write_ms=4), 7)
    for word in ("parse", "chunk", "embed", "write", "total", "7 chunk"):
        assert word in line, f"{word!r} is missing from the summary line"
