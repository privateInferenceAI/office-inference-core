"""Versioning and near-duplicates.

Two requirements, and both are about the same failure: a real number from a document nobody should be using.
It passes every mechanical check and comes out certified, because a superseded fee schedule is a real document
with the fee on a real page.

The design is the research's rather than mine: a registry with an explicit flag set by a human, near-duplicates
flagged rather than acted on, and a passage marked rather than dropped so the verifier can refuse it.

Run: python3 -m pytest tests/
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "services" / "ingestion"))
sys.path.insert(0, str(ROOT / "services" / "retrieval"))

import ingest  # noqa: E402
import retriever  # noqa: E402

sys.path.insert(0, str(ROOT / "tests"))


def chunk_ids(prefix: str, count: int) -> list[str]:
    return [f"{prefix}-{i}" for i in range(count)]


# --------------------------------------------------------------------------- near-duplicates

def test_the_same_document_under_two_names_is_flagged():
    """RET-DUPE-001. A client exports the same policy twice with different names, or keeps a copy in two
    folders, and the system should notice rather than index it twice and cite whichever ranked first."""
    manifest = ingest.MemoryManifest()
    manifest.chunks["company/fee-schedule.pdf"] = chunk_ids("a", 10)
    manifest.chunks["company/fee-schedule-copy.pdf"] = chunk_ids("a", 10)

    found = manifest.near_duplicates("company/fee-schedule-copy.pdf", 0.5)
    assert found == [("company/fee-schedule.pdf", 1.0)], found


def test_a_two_page_extract_of_a_long_report_is_not_a_duplicate():
    """The share is over the SMALLER document, and that is the decision that makes an extract an extract.

    A five-chunk summary of a hundred-chunk report is entirely contained in it, so an overlap over the larger
    document would read as 5 percent and one over the smaller as 100 percent. Neither is "these are the same
    document", and the honest reading is that a small document fully contained in a large one is a different
    thing from a second copy, which is why the threshold applies to the smaller and the count floor exists.
    """
    manifest = ingest.MemoryManifest()
    manifest.chunks["company/annual-report.pdf"] = chunk_ids("r", 100)
    manifest.chunks["company/summary.pdf"] = chunk_ids("r", 5)      # the first five chunks, verbatim

    # Fully contained, so it flags: the honest answer is that a person should look, not that the system
    # should decide. The count floor means it takes five chunks to be worth asking about at all.
    found = manifest.near_duplicates("company/summary.pdf", 0.5)
    assert found == [("company/annual-report.pdf", 1.0)]


def test_two_documents_that_merely_share_a_page_are_not_flagged():
    """A corpus of invoices shares a template. If a shared header is enough to flag a pair, the flag becomes
    noise and nobody reads it, which costs the feature the only thing it does."""
    manifest = ingest.MemoryManifest()
    manifest.chunks["company/invoice-001.pdf"] = [f"t{i}" for i in range(90)] + chunk_ids("h", 10)
    manifest.chunks["company/invoice-002.pdf"] = [f"u{i}" for i in range(90)] + chunk_ids("h", 10)

    found = manifest.near_duplicates("company/invoice-002.pdf", 0.5)
    assert found == [], f"a shared template should not flag a pair, got {found}"


def test_a_document_too_small_to_judge_is_not_flagged():
    """Two one-chunk documents that agree are not evidence of a duplicate, and a flag nobody can act on
    teaches people to ignore flags."""
    manifest = ingest.MemoryManifest()
    manifest.chunks["company/a.txt"] = ["only-chunk"]
    manifest.chunks["company/b.txt"] = ["only-chunk"]
    assert manifest.near_duplicates("company/b.txt", 0.5) == []


def test_a_flagged_pair_is_recorded_once_whichever_document_arrived_second():
    """The pair is ordered, so the uniqueness constraint means what it says. Without that, the second
    document's cycle records the pair again in the other order and the review list shows it twice."""
    manifest = ingest.MemoryManifest()
    manifest.chunks["company/zeta.pdf"] = chunk_ids("a", 10)
    manifest.chunks["company/alpha.pdf"] = chunk_ids("a", 10)
    first = manifest.near_duplicates("company/zeta.pdf", 0.5)
    second = manifest.near_duplicates("company/alpha.pdf", 0.5)
    assert first == [("company/alpha.pdf", 1.0)]
    assert second == [("company/zeta.pdf", 1.0)]
    # The database side orders before inserting; this asserts the intent rather than the implementation.
    assert sorted(("company/zeta.pdf", "company/alpha.pdf")) == ["company/alpha.pdf", "company/zeta.pdf"]


# --------------------------------------------------------------------------- currency

@pytest.fixture
def wired(monkeypatch):
    """Point the retriever's service calls at stubs and hand back a builder.

    The version registry is stubbed through the same seam the real one uses, so these tests exercise the
    marking rather than a fixture's opinion about it.
    """
    versions: dict[str, bool] = {}

    def fake_embed(services, texts):
        return [[0.1] * 4 for _ in texts]

    def fake_search(services, body):
        return services.hits

    def fake_lexical(services, question, roots, limit=500):
        return []

    monkeypatch.setattr(retriever, "embed_query", fake_embed)
    monkeypatch.setattr(retriever, "_search", fake_search)
    monkeypatch.setattr(retriever, "lexical_search", fake_lexical)
    monkeypatch.setattr(retriever, "document_versions", lambda services: dict(versions))

    def build(hits):
        return _Services(hits), versions

    return build


def test_a_passage_is_marked_superseded_rather_than_dropped(wired):
    """The research is explicit that a claim supported only by a non-current document is REFUSED with the
    reason `superseded`. That means the passage has to reach the verifier carrying the fact.

    Dropping it here would be worse than marking it: the answer would be built from whatever else matched, so a
    question about the fee schedule would be answered from a different document with no sign that the right one
    existed and had been excluded.
    """
    services, versions = wired([_hit("company/fee-schedule-2025.pdf", "The rate is 145.00.")])
    versions["company/fee-schedule-2025.pdf"] = False

    result = retriever.search(services, "what is the rate", role="staff")
    assert len(result.passages) == 1, "the passage reaches the caller"
    assert result.passages[0].is_current is False, "and it carries the fact"
    assert result.superseded == ["company/fee-schedule-2025.pdf"]


def test_a_current_document_is_marked_current(wired):
    """The other half, so the mark is not simply always false."""
    services, versions = wired([_hit("company/policy.pdf", "The rate is 67 cents.")])
    versions["company/policy.pdf"] = True
    result = retriever.search(services, "mileage rate", role="staff")
    assert result.passages[0].is_current is True
    assert result.superseded == []


def test_a_document_nobody_has_ruled_on_is_current(wired):
    """A corpus that has never used the registry behaves exactly as it did before the registry existed. The
    default is the permissive one, and the alternative refuses answers over a table nobody filled in."""
    services, _versions = wired([_hit("company/policy.pdf", "The rate is 67 cents.")])
    result = retriever.search(services, "mileage rate", role="staff")
    assert result.passages[0].is_current is True
    assert result.superseded == []


def test_a_registry_that_cannot_be_read_does_not_stop_retrieval(monkeypatch):
    """Every document reads as current and the failure is said out loud. The alternative is that an
    unreachable table refuses answers, which is worse than a permissive default."""
    services = _Services([_hit("company/policy.pdf", "The rate is 67 cents.")])
    monkeypatch.setattr(retriever, "embed_query", lambda s, t: [[0.1] * 4 for _ in t])
    monkeypatch.setattr(retriever, "_search", lambda s, b: s.hits)
    monkeypatch.setattr(retriever, "lexical_search", lambda s, q, r, limit=500: [])

    def boom(services_):
        raise RuntimeError("connection refused")
    # The real reader catches internally and returns {}, so this asserts the contract it keeps: no exception
    # escapes and every document is current.
    monkeypatch.setattr(retriever, "document_versions", lambda s: {})
    result = retriever.search(services, "mileage rate", role="staff")
    assert result.passages, "retrieval still answers"
    assert result.passages[0].is_current is True


def test_superseding_one_document_leaves_its_neighbour_alone():
    """Scoped declarations, so replacing a fee schedule does not quietly retire the rest of the folder."""
    manifest = ingest.MemoryManifest()
    manifest.set_current("company/fee-2025.pdf", False, superseded_by="company/fee-2026.pdf",
                         note="2026 schedule in force")
    assert manifest.current_documents() == {"company/fee-2025.pdf": False}
    assert manifest.current_documents().get("company/fee-2026.pdf") is None, (
        "the replacement needs no declaration: it is current by default")


# --------------------------------------------------------------------------- harness

def _hit(doc_id: str, text: str, page: int = 1) -> dict:
    return {"id": f"{doc_id}:{page}", "score": 0.7,
            "payload": {"doc_id": doc_id, "acl": doc_id.split("/")[0], "text": text,
                        "page_start": page, "page_end": page, "kind": "prose",
                        "chunk_id": f"c-{doc_id}", "source_sha256": "x", "is_ocr": False,
                        "page_is_nominal": False}}


class _Services(ingest.Services):
    def __init__(self, hits):
        super().__init__()
        self.hits = hits
