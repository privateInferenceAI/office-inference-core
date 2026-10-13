"""Reranking: the order it produces, the budget it costs, and reporting when it did not run.

RET-RERANK-001 asks that reranking the fused candidate list stays inside the agreed latency budget AND improves
retrieval and support on the golden set. The second half is the harness's job and cannot be a unit test, so these
tests are about the first half and about the property that makes the second half measurable: a reranker that did
not run has to say so, or the measurement describes a configuration the deployment is not running.

Run: python3 -m pytest tests/
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "services" / "retrieval"))

import rerank  # noqa: E402


class Passage:
    def __init__(self, text: str, score: float = 0.5):
        self.text = text
        self.score = score
        self.doc_id = "company/x.md"
        self.page_start = 1


def passagess(*texts) -> list:
    return [Passage(text) for text in texts]


def test_the_order_reorders_the_head_and_leaves_the_tail_alone(monkeypatch):
    """A reranker that saw the top of a list cannot reorder the rest of it.

    The depth is 40 to 50 because the cost is per token of candidate text, so the tail beyond it was never read
    by the model and keeping its fused order is the honest thing rather than an oversight.
    """
    passages = passagess(*[f"passage {i}" for i in range(6)])
    monkeypatch.setattr(rerank, "available", lambda: True)
    monkeypatch.setattr(rerank, "_bedrock_rerank", lambda q, d, top_n: [(2, 0.9), (0, 0.5), (1, 0.1)])

    outcome = rerank.rerank("q", passages, depth=3)
    assert outcome.ran
    assert outcome.order[:3] == [2, 0, 1], "the head is the reranker's order"
    assert outcome.order[3:] == [3, 4, 5], "the tail keeps its fused order"

    ordered = rerank.apply_order(passages, outcome)
    assert [p.text for p in ordered] == ["passage 2", "passage 0", "passage 1",
                                         "passage 3", "passage 4", "passage 5"]


def test_a_passage_the_reranker_omits_is_kept_rather_than_dropped(monkeypatch):
    """A response that names fewer documents than were sent must not silently delete the rest. A reranker is
    allowed to be unsure; it is not allowed to lose a passage the role was permitted to see."""
    passages = passagess("a", "b", "c")
    monkeypatch.setattr(rerank, "available", lambda: True)
    monkeypatch.setattr(rerank, "_bedrock_rerank", lambda q, d, top_n: [(2, 0.9)])

    outcome = rerank.rerank("q", passages, depth=3)
    assert sorted(outcome.order) == [0, 1, 2], outcome.order
    assert outcome.order[0] == 2
    assert [p.text for p in rerank.apply_order(passages, outcome)] == ["c", "a", "b"]


def test_an_unreachable_reranker_reports_rather_than_falling_back_silently(monkeypatch):
    """The property that makes the quality measurement meaningful.

    A deployment answering from the fused order while believing it reranked is one whose measurements describe a
    configuration it is not running, and the numbers would be quoted as though the reranker had run. Step 3.9 has
    the precedent: the lexical arm reports its own absence so that 'found nothing' and 'did not run' are
    distinguishable.
    """
    monkeypatch.setattr(rerank, "available", lambda: True)

    def refused(query, documents, top_n):
        raise RuntimeError("AccessDeniedException: not authorized to invoke")
    monkeypatch.setattr(rerank, "_bedrock_rerank", refused)

    outcome = rerank.rerank("q", passagess("a", "b"), depth=2)
    assert outcome.ran is False
    assert "AccessDeniedException" in outcome.reason
    record = outcome.as_record()
    assert record["reranked"] is False
    assert record["rerank_model"] is None, "a model is named only when it ran"
    # And the caller keeps its original order, unchanged.
    assert rerank.apply_order(passagess("a", "b"), outcome)


def test_a_deployment_with_no_reranker_says_so_rather_than_failing(monkeypatch):
    """The office build decides its own reranking on hardware that does not exist yet, so absent is a
    configuration rather than a fault."""
    monkeypatch.setenv("RERANK_ENABLED", "false")
    assert rerank.available() is False
    outcome = rerank.rerank("q", passagess("a"), depth=1)
    assert outcome.ran is False
    assert "no reranker configured" in outcome.reason


def test_the_budget_is_reported_beside_what_the_run_cost():
    """The requirement names a budget, so the outcome carries the elapsed time and whether it fit. A quality gain
    that costs more than the budget is a trade, and a trade nobody can see is not one anybody agreed to."""
    outcome = rerank.RerankOutcome(order=[0], elapsed_ms=1234.5, ran=True)
    assert outcome.within_budget is True
    record = outcome.as_record()
    assert record["rerank_ms"] == 1234.5
    assert record["rerank_within_budget"] is True
    assert record["rerank_budget_ms"] == rerank.RERANK_BUDGET_MS

    over = rerank.RerankOutcome(order=[0], elapsed_ms=rerank.RERANK_BUDGET_MS + 1, ran=True)
    assert over.within_budget is False


def test_only_the_passage_text_is_sent(monkeypatch):
    """A reranker needs the words. A service that receives a client's filenames has them, and the payload carries
    document ids and access labels that the model has no use for."""
    sent: list[list[str]] = []

    def capture(query, documents, top_n):
        sent.append(documents)
        return [(0, 0.9)]
    monkeypatch.setattr(rerank, "available", lambda: True)
    monkeypatch.setattr(rerank, "_bedrock_rerank", capture)

    rerank.rerank("q", passagess("the rate is 145.00 per hour"), depth=1)
    assert sent == [["the rate is 145.00 per hour"]]
    assert "company/" not in sent[0][0], "the document id was not sent"


def test_the_search_reports_what_reranking_did(monkeypatch):
    """Read rather than imported: the retriever pulls in the ingestion service and a database driver, and what
    is asserted is that the result carries the record rather than that a particular number came back."""
    source = (ROOT / "services" / "retrieval" / "retriever.py").read_text()
    assert "rerank: dict = field(default_factory=dict)" in source
    assert "result.rerank = outcome.as_record()" in source
    # And it runs on the fused head before the limit, because the point is to change which passages are in the
    # head rather than to reorder one the fusion already chose.
    search = source[source.index("def search("):]
    assert search.index("fused = reciprocal_rank_fusion") < search.index("rerank_module.rerank")


def test_reranking_can_be_turned_off_for_a_comparison():
    """The harness compares two configurations on the same questions, and one of them has to be able to be the
    fused order without a reranker in it."""
    source = (ROOT / "services" / "retrieval" / "retriever.py").read_text()
    assert "rerank_passages: bool = True" in source


def test_the_tool_image_carries_the_sdk_the_reranker_signs_with():
    """Found by running the reranker acceptance on the box: `No module named 'botocore'`.

    The retriever imports the reranker, so any image that runs a retrieval needs the AWS SDK in it, and the tool
    image did not have it. The unit tests passed because they run in the source tree where every module resolves,
    which is the same gap as the worker's Dockerfile missing `contract.py`: **an image is a set of files and
    nothing was checking that the set was complete.**
    """
    dockerfile = (ROOT / "services" / "eval" / "Dockerfile").read_text()
    assert "botocore" in dockerfile, (
        "the tool image runs the retriever, which imports the reranker, which signs through botocore")
    # And the module that needs it says so rather than assuming the environment has it.
    source = (ROOT / "services" / "retrieval" / "rerank.py").read_text()
    assert "import botocore.session" in source
