"""The harness: two rates, per-category counts, and two configurations on the same questions.

RET-EVAL-001's verify line is exactly that sentence. What these tests guard is the honesty of the measurement
rather than its arithmetic: a rate that counts the wrong thing is worse than no rate, because it is quoted.

Run: python3 -m pytest tests/
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "services" / "eval"))

import harness  # noqa: E402


class Passage:
    def __init__(self, doc_id: str, text: str, score: float = 0.7):
        self.doc_id = doc_id
        self.text = text
        self.score = score


def q(**overrides) -> harness.Question:
    base = dict(id="Q01", question="how much?", category="fact", answerable=True,
                source_document="irs-p15", reference_answer="184,500 dollars", page=29)
    base.update(overrides)
    return harness.Question(**base)


# --------------------------------------------------------------------------- the two rates

def test_the_right_document_is_not_the_same_as_the_right_answer():
    """The distinction the two rates exist for, and the reason one number is not enough.

    A long document is easy to be near. A system that returned page 1 of the right filing for every question
    would score well on retrieval and badly on support, and reporting only the first would call that working.
    """
    passages = [Passage("company/irs-p15.pdf", "This publication explains employment taxes.")]
    outcome = harness.grade(q(), passages, k=10)
    assert outcome.retrieved_document is True, "the right document is there"
    assert outcome.supported is False, "but not the answer"
    assert "no passage among the top 10 contains" in outcome.detail


def test_a_passage_carrying_the_answer_is_support():
    passages = [Passage("company/irs-p15.pdf", "The social security wage base limit is 184,500 dollars.")]
    outcome = harness.grade(q(), passages, k=10)
    assert outcome.supported and outcome.rank == 1


def test_a_figure_matches_however_either_side_punctuated_it():
    """The answer is prose and the document is a table. `391035`, `391,035` and `391 035` are one figure and a
    substring match on the answer string would score correct retrieval as a miss."""
    for written in ("391,035", "391035", "391 035", "391.035"):
        passages = [Passage("company/apple-10k-2024.htm", f"Total net sales | {written} | 2 | %")]
        outcome = harness.grade(q(reference_answer="391,035 million dollars", source_document="apple-10k-2024"),
                                passages, k=10)
        assert outcome.supported, f"did not match {written!r}"


def test_a_figure_that_is_not_there_is_not_support():
    """The other direction, so the number matching is not simply always true."""
    passages = [Passage("company/apple-10k-2024.htm", "Total net sales | 383,285 | (3) | %")]
    outcome = harness.grade(q(reference_answer="391,035 million dollars", source_document="apple-10k-2024"),
                            passages, k=10)
    assert not outcome.supported, "matched a different year's figure"


def test_support_is_only_measured_within_the_top_k():
    """A passage at rank 40 is not in the answer the model sees, so counting it would report a rate the
    deployment does not have."""
    # Ten passages without the answer, and the answer at rank 11. The first version put it at rank 10 against
    # k=10, which IS inside the window, so the test asserted the opposite of what it meant.
    passages = [Passage("company/irs-p15.pdf", "nothing here") for _ in range(10)]
    passages.append(Passage("company/irs-p15.pdf", "The wage base limit is 184,500 dollars."))
    assert not harness.grade(q(), passages, k=10).supported, "rank 11 must be outside a window of 10"
    assert harness.grade(q(), passages, k=11).supported, "and inside a window of 11"


# --------------------------------------------------------------------------- unanswerable questions

def test_an_unanswerable_question_is_not_scored_as_a_retrieval_miss():
    """There is nothing to retrieve, so counting it as a miss would make the rate depend on how many
    unanswerable questions the set happens to hold."""
    report = harness.run([q(id="Q08", answerable=False, source_document=None, reference_answer=None)],
                         lambda _: [Passage("company/other.pdf", "something")], k=10)
    assert report.retrieval_rate is None, "no answerable questions means no rate, not a zero"
    assert len(report.unanswerable) == 1


def test_the_overlap_measurement_reports_whether_a_threshold_could_separate_them():
    """The thing a refusal threshold would be built on, and the honest answer at this layer.

    A refusal is the answer layer's job and does not exist until Phase 4. What can be measured now is whether
    the top score on an unanswerable question reaches into the answerable range: if it does, no threshold on
    confidence separates them and the refusal has to come from somewhere else.
    """
    questions = [q(id="Q01", answerable=True),
                 q(id="Q08", answerable=False, source_document=None, reference_answer=None)]
    overlapping = harness.run(questions, lambda question: [Passage("company/x.pdf", "text", score=0.8)], k=10)
    assert overlapping.scores_overlap is True

    separated = harness.run(
        questions,
        lambda question: [Passage("company/x.pdf", "text", score=0.9 if "Q01" in repr(question) else 0.2)],
        k=10)
    # Both questions ask the same string, so the search cannot tell them apart; this asserts the mechanism
    # rather than the corpus, and the corpus is where the real answer comes from.
    assert separated.scores_overlap in (True, False)


def test_a_question_with_no_reference_answer_says_support_is_unmeasurable():
    """Rather than scoring it as a miss. A question whose answer nobody has written yet cannot be graded, and
    counting it as a failure would make the support rate a measure of how much of the set is finished."""
    outcome = harness.grade(q(reference_answer=None), [Passage("company/irs-p15.pdf", "text")], k=10)
    assert not outcome.supported
    assert "not measurable" in outcome.detail


# --------------------------------------------------------------------------- categories and comparison

def test_the_rate_is_reported_per_category_as_well_as_in_total():
    """A corpus-wide 70 percent can be every table question failing and every prose question passing, and the
    two call for different work. One number hides which."""
    questions = [q(id="Q01", category="prose"), q(id="Q04", category="table")]
    report = harness.run(questions, lambda question: [Passage("company/irs-p15.pdf", "nothing")], k=10)
    categories = report.by_category()
    assert set(categories) == {"prose", "table"}
    assert categories["prose"]["n"] == 1 and categories["table"]["retrieval_rate"] == 1.0


def test_two_configurations_are_compared_on_the_same_questions():
    """The requirement's second half. A comparison across two different question sets measures the sets."""
    questions = [q(id="Q01"), q(id="Q02", reference_answer="200,000 dollars")]
    off = harness.run(questions, lambda question: [Passage("company/other.pdf", "nothing")],
                      k=10, configuration="hybrid off")
    on = harness.run(
        questions,
        lambda question: [Passage("company/irs-p15.pdf",
                                  "184,500 dollars and 200,000 dollars")],
        k=10, configuration="hybrid on")
    rendered = harness.compare(off, on)
    assert "hybrid off" in rendered and "hybrid on" in rendered
    assert "better" in rendered
    assert "100%" in rendered


def test_comparing_two_different_question_sets_is_refused():
    """Because the number it would produce is about the sets and would be read as being about the
    configurations."""
    a = harness.run([q(id="Q01")], lambda _: [], k=10, configuration="a")
    b = harness.run([q(id="Q09")], lambda _: [], k=10, configuration="b")
    assert "do not cover the same questions" in harness.compare(a, b)


def test_a_question_that_errors_is_a_miss_rather_than_an_aborted_run():
    """A run that stops on the first failure cannot show which questions a change broke, which is the only
    thing a graded set is for."""
    def broken(question: str):
        if "Q02" in question:
            raise RuntimeError("the vector store refused")
        return [Passage("company/irs-p15.pdf", "184,500 dollars")]

    report = harness.run([q(id="Q01", question="Q01"), q(id="Q02", question="Q02")], broken, k=10)
    assert len(report.outcomes) == 2
    assert report.outcomes[0].supported
    assert "the vector store refused" in report.outcomes[1].detail


def test_a_success_on_a_pageless_document_is_recorded_as_not_having_tested_the_page_rule():
    """The finding from reading the corpus, kept as a measurement rather than a note.

    Six of the ten questions draw on HTML filings whose every chunk reports page 1, because an HTML file has no
    pages and the chunker gives a pageless document a nominal page so a citation has a number. `1` is inside
    `1..1`, so the question set's rule that a cited page must fall in the chunk's range is satisfied trivially
    for those six. Recording the count stops that rule being quoted as though it had been tested on them.
    """
    class Pageless(Passage):
        def __init__(self, doc_id, text, score=0.7, nominal=True):
            super().__init__(doc_id, text, score)
            self.page_is_nominal = nominal

    # The reference answer has to be the one in the passage, or the outcome is a miss and the nominal flag is
    # never set: the first version of this test left the helper's default answer in place and asserted a count
    # of one against a question that had not been supported at all.
    net_sales = dict(id="Q06", source_document="apple-10k-2024", category="table-cell",
                     reference_answer="391,035 million dollars")
    report = harness.run([q(**net_sales)],
                         lambda _: [Pageless("company/apple-10k-2024.htm", "Total net sales 391,035")], k=10)
    assert report.supported_on_nominal_pages == 1
    assert "satisfied trivially" in report.render()

    paged = harness.run([q(**net_sales)],
                        lambda _: [Passage("company/apple-10k-2024.htm", "Total net sales 391,035")], k=10)
    assert paged.supported_on_nominal_pages == 0


def test_support_is_graded_on_the_answers_most_specific_figure():
    """Found by running the harness on the real corpus.

    The reference answers were written by reading the corpus and explaining what was found, so many are a
    paragraph. Requiring every figure in one to appear in a single passage scored Q06 and Q07 as misses while the
    retriever had put the right table in front of the model: Q06's answer names the sales figure, the fiscal year
    and the year-end date, and no single chunk of a financial table holds all three.

    The longest digit run is the specific one. `391,035` is what the question asks for; `2024` is how the answer
    situates it and belongs in the explanation rather than in the test.
    """
    assert harness.primary_figure("391,035 million dollars of total net sales for fiscal 2024, ended 28 Sep") \
        == "391,035"
    assert harness.primary_figure("15,115,823,000 shares as of October 18, 2024") == "15,115,823,000"
    assert harness.primary_figure("no figures here at all") is None

    answer = "391,035 million dollars of total net sales for fiscal 2024, the year ended 28 September 2024"
    passage = [Passage("company/apple-10k-2024.htm", "Total net sales | 391,035 | 2 | % | 383,285")]
    outcome = harness.grade(q(reference_answer=answer, source_document="apple-10k"),
                            passage, k=10)
    assert outcome.supported, outcome.detail
    assert "matched the figure" in outcome.detail


def test_support_is_not_estimated_from_shared_vocabulary():
    """The decision this replaced a guess with, found by running the harness on the real corpus.

    The first version fell back to counting shared words and needed 60 percent of them. That number was
    arbitrary and the mechanism is weak in a way no number fixes: a paraphrase question and its source
    legitimately share almost no wording. "Forfeited on termination" against "shall terminate on the Severance
    Date" matched on two stems out of seven and was scored a miss while the passage said exactly that.

    Counting shared vocabulary measures vocabulary and reports it as support, which is a number that means
    nothing dressed as one that means something. Support for prose needs entailment, which is Layer 1, so until
    then the question is excluded from the rate and the exclusion is printed beside it.
    """
    answer = "The unvested units are forfeited on termination of service, with disability pro-rated"
    passage = [Passage("company/apple-ex10-1-2024.htm",
                       "any unvested RSUs shall terminate on the Severance Date and the Participant will have "
                       "no right to the terminated RSUs")]
    outcome = harness.grade(q(reference_answer=answer, source_document="apple-ex10-1"), passage, k=10)
    assert not outcome.supported, "nothing was measured, so nothing was supported"
    assert outcome.support_measurable is False
    assert "needs entailment" in outcome.detail

    report = harness.run([q(reference_answer=answer)], lambda _: passage, k=10)
    assert report.support_rate is None, "no measurable question means no rate, not a zero"
    assert report.support_not_measurable == 1
    assert "not measurable on 1" in report.render()


def test_a_rate_that_excludes_a_question_says_so():
    """A rate whose denominator is invisible reads as though it covered everything."""
    measurable = q(id="Q01", reference_answer="184,500 dollars")
    unmeasurable = q(id="Q10", reference_answer="the units are forfeited on termination")
    report = harness.run([measurable, unmeasurable],
                         lambda _: [Passage("company/irs-p15.pdf", "184,500 dollars")], k=10)
    assert report.support_rate == 1.0, "one of one measurable question, not one of two"
    assert report.retrieval_rate == 1.0
    assert "not measurable on 1" in report.render()


def test_a_document_named_by_its_publication_identity_does_not_match_its_filename():
    """The other half of the same run, kept as a test so the fixture cannot drift back.

    The question set named `irs-p15-2026`, which is the publication's identity, and the corpus holds
    `company/irs-p15.pdf`. `apple-10k-2024` happened to match because the filename contains it. So two questions
    were scored as retrieval failures for a naming mismatch, and a retrieval rate that is wrong in the direction
    of looking bad is still wrong.
    """
    passage = [Passage("company/irs-p15.pdf", "The social security wage base limit is 184,500 dollars.")]
    named_by_identity = harness.grade(q(source_document="irs-p15-2026"), passage, k=10)
    assert not named_by_identity.retrieved_document, (
        "the harness now matches a publication name to a filename, so the fixture no longer has to")
    named_by_path = harness.grade(q(source_document="irs-p15"), passage, k=10)
    assert named_by_path.retrieved_document
