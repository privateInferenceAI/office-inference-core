"""Catching a confidently wrong read, or saying nobody checked it.

RET-PARSE-002. The failure: OCR misreads a character, the wrong number is faithful to the document as parsed, so
retrieval finds it, the model cites it, and the citation layer confirms the number is present in the cited
passage because OCR is what put it there. Every check passes and the client gets a confident wrong figure.

Step 3.4 measured forty trials carving defects into a cell whose truth was 145.00: fifteen wrong readings, and
confidence flagged ONE. Fourteen wrong numbers passed at confidence 61 to 93.

So the tests are mostly about the ceiling. A check described as catching OCR errors would be believed, and this
one catches the malformed quarter of them and nothing else.

Run: python3 -m pytest tests/
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "services" / "retrieval"))

import figures  # noqa: E402


class Passage:
    def __init__(self, text: str, is_ocr: bool = False):
        self.text = text
        self.is_ocr = is_ocr
        self.doc_id = "company/x.pdf"
        self.page_start = 1


# --------------------------------------------------------------------------- format, and its ceiling

def test_a_misread_that_produces_an_impossible_date_is_caught():
    assert figures.check_format("2026-13-45"), "month 13"
    assert figures.check_format("2026-11-45"), "day 45"
    assert figures.check_format("02/31/2026"), "31 February"


def test_a_misread_that_produces_a_different_plausible_number_is_not_caught():
    """THE CEILING, as a test so it cannot be quietly assumed away.

    145.00 read as 148.00 is a well-formed amount. Nothing in this file can tell it from a document that says
    148.00, and the fourteen wrong readings step 3.4 produced were all of this kind. A check described as
    catching OCR errors would be believed; this one catches the malformed quarter and nothing else.
    """
    for damaged in ("148.00", "1450.00", "14.500", "945.00"):
        assert figures.check_format(damaged) == [], (
            f"{damaged} is a plausible figure and this layer cannot know it is wrong, which is the point")


def test_a_figure_too_large_for_a_small_business_is_flagged_as_two_columns_joined():
    """The failure this does catch is mechanical rather than numerical: a column boundary lost in the read, so
    two cells arrive as one number."""
    findings = figures.check_format("12345678901234.00")
    assert findings and findings[0].code == "malformed.implausible_magnitude"


def test_a_percentage_outside_its_range_is_flagged_but_not_refused():
    """A growth rate legitimately exceeds 100 percent, so the finding says what it might be rather than calling
    it wrong. A check that refuses a real growth rate is one that gets turned off."""
    assert figures.check_format("1450%"), "past a thousand percent is a damaged read"
    # And 145 percent is NOT flagged, which is the harder half of this decision. It exceeds a hundred and it is a
    # growth rate: a real figure in a real document. Flagging it would be a check that fires on correct
    # documents, and that is the failure mode that gets a check turned off.
    assert not figures.check_format("145%")
    assert not figures.check_format("-12.5%")


def test_parentheses_are_negative_which_is_how_a_statement_writes_one():
    """And how a figure that lost its minus sign can still be read correctly."""
    assert figures.parse_number("(1,250.00)") == -1250.0
    assert figures.parse_number("1,250.00") == 1250.0
    assert figures.parse_number("12.5%") == 12.5


# --------------------------------------------------------------------------- arithmetic

def test_line_items_that_sum_to_their_total_confirm_it():
    assert figures.check_arithmetic([["100.00"], ["45.00"]], "145.00") is True
    assert figures.check_arithmetic([["100.00"], ["45.00"]], "155.00") is False


def test_a_document_with_no_relationship_to_check_returns_none_rather_than_failing():
    """Most documents have nothing to add up, and a paragraph containing a rate has nothing at all. Returning
    False would mark every figure in every prose document as unconfirmed for a reason that is not about the
    figure."""
    assert figures.check_arithmetic([["145.00"]], "145.00") is None, "one item is not a column"
    assert figures.check_arithmetic([["a"], ["b"]], "145.00") is None, "no numbers to add"
    assert figures.check_arithmetic([["100.00"], ["45.00"]], None) is None, "no stated total"


def test_rounding_to_whole_units_does_not_fail_the_check():
    """A table rounded to whole units will not sum exactly, and refusing every such table would be a check that
    fires on correct documents."""
    assert figures.check_arithmetic([["100"], ["45"]], "145") is True
    assert figures.check_arithmetic([["100.4"], ["44.6"]], "145.0") is True


# --------------------------------------------------------------------------- the second reading

def test_a_disagreeing_second_reading_is_not_a_vote():
    """The decision records it as an escalation gate and never a vote: two readings that disagree do not
    average, they go to a person. The verdict says they disagreed rather than deciding between them."""
    assert figures.second_reading("145.00", "145.00") is True
    assert figures.second_reading("145.00", "148.00") is False
    assert figures.second_reading("145.00", None) is None, "no second reading is not a disagreement"


def test_no_second_reading_and_a_disagreeing_one_are_different_facts():
    """The distinction the disclosure rests on. One means nothing else looked; the other means something looked
    and did not agree."""
    assert figures.second_reading("145.00", None) is None
    assert figures.second_reading("145.00", "148.00") is False
    # And the verdict treats them differently.
    unlooked = figures.assess("145.00")
    disagreed = figures.assess("145.00", other_reading="148.00")
    assert unlooked.second_reading_agrees is None
    assert disagreed.second_reading_agrees is False
    assert not disagreed.verified


# --------------------------------------------------------------------------- disclosure

def test_a_figure_from_a_scanned_page_with_nothing_confirming_it_is_disclosed():
    """The honest half. A figure that passed no arithmetic check and had no second source is cited as
    machine-read rather than implied correct."""
    passage = Passage("The standard labour rate is 145.00 per hour.", is_ocr=True)
    note = figures.disclosure_for(passage, figures.assess("145.00"))
    assert note is not None
    assert "not confirmed" in note
    assert "machine-read" in note


def test_a_figure_from_a_digital_page_is_not_disclosed():
    """Nothing recognised the text, so there is nothing to disclose. Telling a client that a figure from a text
    layer is machine-read would be a caveat on everything and therefore a caveat on nothing."""
    passage = Passage("The standard labour rate is 145.00 per hour.", is_ocr=False)
    assert figures.disclosure_for(passage, figures.assess("145.00")) is None


def test_a_figure_the_document_itself_confirms_is_not_disclosed():
    """A number that ties to its own column total has been checked by the document, and caveating it would be
    the disclosure crying wolf on the one case where something did verify it."""
    passage = Passage("Line items 100.00 and 45.00, total 145.00", is_ocr=True)
    confirmed = figures.assess("145.00", arithmetic_rows=[["100.00"], ["45.00"]], arithmetic_total="145.00")
    assert confirmed.verified
    assert figures.disclosure_for(passage, confirmed) is None


def test_saying_nothing_is_not_a_guarantee():
    """The property worth stating in a test, because it is the one a reader will assume away.

    A citation with no caveat means there was nothing to disclose, not that the figure was checked. Most figures
    are in that state: the layer catches the malformed quarter of misreads and confirms whatever the document's
    own arithmetic confirms, and everything else is a single machine read.
    """
    passage = Passage("Some prose with a rate of 145.00 in it.", is_ocr=False)
    assert figures.disclosure_for(passage, figures.assess("145.00")) is None
    verdict = figures.assess("145.00")
    assert verdict.verified is False, "nothing verified it, and the absence of a caveat does not say otherwise"


def test_the_citation_carries_the_caveat_rather_than_a_separate_field():
    """A caveat in a field beside the citation is one the rendering has to remember. In the citation itself it
    travels wherever the citation goes."""
    passage = Passage("The rate is 145.00 per hour.", is_ocr=True)
    rendered = figures.disclose_citation(passage, "The rate is 145.00 per hour. [1]", figures.assess("145.00"))
    assert rendered.endswith("]"), rendered
    assert "machine-read" in rendered
    assert "[1]" in rendered
