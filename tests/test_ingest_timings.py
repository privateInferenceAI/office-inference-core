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


def test_a_run_against_a_subset_of_the_corpus_removes_nothing():
    """**The guard, and it exists because of a mistake rather than a theory.**

    Reconciliation removes every document the manifest knows about whose file is not under the root being
    walked. That is correct when the root is the deployment's whole documents folder, which is what it is in
    production. It is destructive when the root is a subset: a measurement run pointed at 300 sample
    documents reconciled away the other 9,700, emptying the lexical table and the manifest for a corpus that
    had taken three and a half hours to build. The vectors survived only because that run happened to use a
    separate collection.

    This reads the source rather than running a cycle, because the assertion is about which root the code
    treats as the deployment's own, and a cycle needs Postgres, Qdrant and a parser to find out.
    """
    source = (ROOT / "services" / "ingestion" / "ingest.py").read_text()
    assert "DEFAULT_DOCUMENTS_ROOT" in source, "there is no notion of the deployment's own root"
    # The comparison has to resolve both sides, or a trailing slash on the default defeats it.
    assert "documents_root.resolve() == DEFAULT_DOCUMENTS_ROOT.resolve()" in source, (
        "the guard does not compare resolved paths, so `/documents/` or a relative path would defeat it")
    # **The decision belongs at the caller, because the cycle cannot tell a subset from a deployment.** Both
    # are a path. The caller knows whether somebody named a root other than the deployment's own.
    assert "removing: bool = True" in source, "the cycle has no way to be told not to remove"
    assert "if removing else []" in source.replace("(sorted(set(known) - seen_relpaths) if removing else []):",
                                                   "if removing else []"), \
        "the removal loop runs regardless of the decision"
    assert "ingest_cycle(services, documents_root, manifest, dry_run=args.dry_run, removing=removing)" in source, (
        "the CLI computes the decision and does not pass it, so the guard is decorative")


def test_the_guard_says_what_it_is_doing():
    """A run that silently declined to remove anything would look like a run that found nothing to remove."""
    source = (ROOT / "services" / "ingestion" / "ingest.py").read_text()
    assert "not removing anything" in source
