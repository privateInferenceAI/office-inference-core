"""The mechanical layer: seven checks, no model.

The scope is the research's: fabricated citations, page drift, missing quotes, numeric substitution, identifier
substitution, superseded sources and low-confidence scans. The value is stated in the same place, that the
literal identifier assertion is the cheapest item in the architecture and the highest value.

What these tests are really guarding is the false refusal. A check that refuses correct answers gets turned
off, and a check that is turned off catches nothing. So there are as many tests here for answers that should
PASS as for answers that should be refused, and each refusal case is paired with the near miss that must not
trigger it.

Run: python3 -m pytest tests/
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "services" / "retrieval"))
sys.path.insert(0, str(ROOT / "services" / "ingestion"))

import citations  # noqa: E402


class Passage:
    """A retrieved passage, with the payload fields the checks read."""

    def __init__(self, doc_id: str, text: str, page: int = 1, is_current: bool = True,
                 parse_score: float | None = 1.0, page_is_nominal: bool = False):
        self.doc_id = doc_id
        self.text = text
        self.page_start = page
        self.page_end = page
        self.is_current = is_current
        self.parse_score = parse_score
        self.page_is_nominal = page_is_nominal


SCHEDULE = ("| Standard labour, business hours | per hour | 145.00 |\n"
            "| Mileage, client-owned vehicle | per mile | 0.67 |\n"
            "| Emergency attendance | per callout | 240.00 |\n"
            "Rates are reviewed each January and take effect on the date of issue.")


def passages(**kwargs):
    return [Passage("company/fee-schedule-2026.md", SCHEDULE, **kwargs)]


# --------------------------------------------------------------------------- numbers

def test_a_number_that_is_in_the_passage_passes():
    """The common case, and the one a false refusal would break. The passage writes 145.00 and an answer
    writes 145, which is the same figure said two ways."""
    result = citations.verify("The standard labour rate is 145 per hour.[1]", passages())
    assert result.all_supported, result.refusals
    assert result.claims[0].citations == [1]


def test_a_substituted_digit_is_refused():
    """Numeric substitution: the worst failure in the product, because 145.00 and 154.00 are both real numbers
    and only one of them is in the document."""
    result = citations.verify("The standard labour rate is 154 per hour.[1]", passages())
    assert not result.all_supported
    assert result.claims[0].reason == "number_not_in_source"
    assert "154" in result.claims[0].detail


def test_a_decimal_below_one_is_not_mangled_by_normalisation():
    """`0.67` was turned into `.67` by stripping leading zeros, which then matched nothing and refused a
    correct answer. The mileage rate is the figure a small business asks about most, so this was the first
    false refusal the check would have produced."""
    assert citations.normalize_number("0.67") == "0.67"
    assert citations.normalize_number("0.5") == "0.5"
    result = citations.verify("Mileage is reimbursed at 0.67 per mile.[1]", passages())
    assert result.all_supported, result.claims[0].detail


def test_thousands_separators_do_not_change_a_number():
    """A passage writes 1,500 and an answer writes 1500. Same figure."""
    ps = [Passage("company/invoice.md", "The total is 1,500.00 for the quarter.")]
    assert citations.verify("The total is 1500.00 for the quarter.[1]", ps).all_supported
    assert citations.verify("The total is 1500 for the quarter.[1]", ps).all_supported


def test_a_number_from_a_different_passage_is_refused():
    """The claim cites [1] and quotes a figure that lives in [2]. The words are real and the citation is
    wrong, which is the case a reader cannot catch by skimming."""
    ps = passages() + [Passage("company/terms.md", "Payment is due within 30 days.")]
    result = citations.verify("Payment is due within 30 days of the standard labour rate.[1]", ps)
    assert result.claims[0].reason == "number_not_in_source"


# --------------------------------------------------------------------------- identifiers

def test_an_identifier_not_in_the_passage_is_refused():
    """Identifier substitution, and the reason step 3.9 built a shared extractor: Form W-4 and Form W-2 are one
    character apart and both exist in the corpus."""
    ps = [Passage("company/irs-p15.pdf", "File Form W-2 for each employee.", page=11)]
    result = citations.verify("You must file Form W-4 for each employee.[1]", ps)
    assert result.claims[0].reason == "identifier_not_in_source"


def test_an_identifier_written_differently_still_matches():
    """`Pub. 515` and `Publication 515` are the same reference, and an answer that expands the abbreviation is
    correct. Refusing it would teach the model to copy the source's spelling, which is not the point."""
    ps = [Passage("company/irs-p515.pdf", "Publication 515 covers withholding on nonresident aliens.")]
    assert citations.verify("Pub. 515 covers withholding on nonresident aliens.[1]", ps).all_supported


# --------------------------------------------------------------------------- quotes

def test_a_quote_that_is_not_in_the_source_is_refused():
    """The most checkable kind of wrong: an answer puts words in quotation marks and the passage does not
    contain them."""
    result = citations.verify('The schedule says "the standard rate is 145.00 per hour" for all work.[1]',
                              passages())
    assert result.claims[0].reason == "quote_not_in_source"


def test_a_real_quote_with_different_whitespace_passes():
    """The passage is a table, so its text carries newlines and pipe characters. A quote spanning a line break
    is the same words and must not be refused for the line break."""
    ps = [Passage("company/notes.md", "Rates are reviewed each January\nand take effect on the date of issue.")]
    result = citations.verify('The note says "reviewed each January and take effect".[1]', ps)
    assert result.all_supported, result.claims[0].detail


# --------------------------------------------------------------------------- fabricated citations

def test_an_invented_citation_index_is_rejected():
    """The requirement's own verify line. Three passages were retrieved and the answer cites a fourth, so the
    citation names something that was never in front of the model."""
    ps = passages() + [Passage("company/b.md", "B."), Passage("company/c.md", "C.")]
    result = citations.verify("The rate is 145 per hour.[4]", ps)
    assert result.claims[0].reason == "citation_not_found"
    assert "never in front of the model" in result.claims[0].detail


def test_a_citation_of_zero_is_rejected():
    """Citations are one-based. `[0]` is off by one in the model's head and must not silently resolve to the
    first passage."""
    result = citations.verify("The rate is 145 per hour.[0]", passages())
    assert result.claims[0].reason == "citation_not_found"


def test_a_sentence_with_no_citation_is_uncited_rather_than_refused():
    """Every refusal has to be a claim that something is wrong. A transition sentence has nothing wrong with
    it, and refusing it would refuse most of a normal reply, which is how a check gets turned off."""
    result = citations.verify("Here is what the schedule says. The rate is 145 per hour.[1]", passages())
    assert result.claims[0].verdict == "uncited"
    assert result.claims[0].reason == "no_citation"
    assert result.claims[1].ok
    # Uncited is counted separately from refused, so a caller can tell the two apart.
    assert result.refusals == {"no_citation": 1}
    assert len(result.refused) == 1


# --------------------------------------------------------------------------- superseded

def test_a_claim_supported_only_by_a_superseded_document_is_refused():
    """The reason step 3.10 built the registry for. The figure is real and it is out of date, which no literal
    check can see: every word of the claim is in the passage."""
    result = citations.verify("The standard labour rate is 145 per hour.[1]",
                              passages(is_current=False))
    assert result.claims[0].reason == "superseded"
    assert "out of date" in result.claims[0].detail


def test_a_claim_citing_the_replacement_alongside_the_replaced_is_not_refused():
    """The word ONLY is the whole design. Once the replacement is indexed, an answer cites both, and refusing
    it because one support is superseded would refuse a correct answer that names the current source."""
    ps = [Passage("company/fee-2025.md", "The rate is 132.00 per hour.", is_current=False),
          Passage("company/fee-2026.md", "The rate is 145.00 per hour.", is_current=True)]
    result = citations.verify("The rate is 132.00 per hour and rises to 145.00 per hour.[1][2]", ps)
    assert result.all_supported, result.claims[0].detail


# --------------------------------------------------------------------------- confidence

def test_a_figure_from_a_low_confidence_scan_is_refused():
    """A real number from a misread scan. It passes every literal check, because the text the parser produced
    does contain the number, and the parser is what was unsure."""
    result = citations.verify("The standard labour rate is 145 per hour.[1]",
                              passages(parse_score=0.42))
    assert result.claims[0].reason == "low_confidence_scan"
    assert "read by eye" in result.claims[0].detail


def test_the_confidence_gate_does_not_fire_on_prose():
    """A digit read imperfectly is a different number. A word read imperfectly is still the word, and refusing
    every sentence from a scanned document would refuse whole scanned filings that were read correctly."""
    result = citations.verify("Rates are reviewed each January and take effect on the date of issue.[1]",
                              passages(parse_score=0.42))
    assert result.all_supported, result.claims[0].detail


def test_a_passage_with_no_score_does_not_trip_the_gate():
    """A format the parser does not score returns null, and null is not low. Treating an absent score as zero
    would refuse every claim from every HTML and spreadsheet in the corpus."""
    result = citations.verify("The rate is 145 per hour.[1]", passages(parse_score=None))
    assert result.all_supported, result.claims[0].detail


# --------------------------------------------------------------------------- page drift

def test_a_page_number_the_document_does_not_have_is_drift():
    """A citation to a real passage with the wrong page attached. Every literal check passes, because the words
    are all there and only the place is wrong."""
    ps = passages()
    drift = citations.check_page("The rate is 145 per hour (page 12).[1]", ps[0], [1])
    assert drift and "page drift" in drift


def test_a_nominal_page_is_reported_as_nominal_rather_than_as_drift():
    """A pageless format gets page 1 because a citation needs a number. Presenting that as a page of an HTML
    contract is a different kind of wrong from citing the wrong page of a PDF."""
    ps = passages(page_is_nominal=True)
    note = citations.check_page("The rate is 145 per hour (page 12).[1]", ps[0], [1])
    assert note and "nominal" in note


def test_a_claim_with_no_page_number_is_not_checked_for_drift():
    """Nothing to check against, so nothing is reported. Returning a string here would refuse every claim that
    does not mention a page, which is most of them."""
    ps = passages()
    assert citations.check_page("The rate is 145 per hour.[1]", ps[0], [1]) is None


# --------------------------------------------------------------------------- the answer as a whole

def test_the_splitter_keeps_a_marker_with_the_sentence_it_supports():
    """The bug that would refuse correct answers: a marker read as support for the NEXT sentence runs the
    checks against the wrong passage. The second version of this swallowed the marker instead, so every claim
    came out uncited and every claim was reported as having no support."""
    claims = citations.split_claims(
        "The rate is 145.00 per hour.[1] It applies from January. [2] No citation here.")
    # Compared with whitespace collapsed, because whether a marker is written `.[1]` or `. [1]` is not what
    # this test is about. It is about which sentence the marker lands on.
    assert [" ".join(c.split()) for c in claims] == ["The rate is 145.00 per hour. [1]",
                                                     "It applies from January. [2]",
                                                     "No citation here."], claims


def test_an_unsupported_sentence_is_marked_and_the_supported_one_is_kept():
    """The requirement's second verify line. Marking rather than dropping, because a reply that reads cleanly
    and lost the part that was wrong is the failure this layer exists to prevent."""
    result = citations.verify(
        "The standard labour rate is 145 per hour.[1] The callout fee is 310.00 per visit.[1]", passages())
    assert len(result.claims) == 2
    assert result.claims[0].ok, "the correct sentence is kept"
    assert result.claims[1].reason == "number_not_in_source", "the wrong one is marked, not dropped"
    assert result.claims[1].text in [c.text for c in result.claims]
    assert [t.split() for t in result.supported_text] == [
        "The standard labour rate is 145 per hour. [1]".split()]


def test_refusals_are_counted_by_reason():
    """The number to watch. A rising number_not_in_source is a generation problem and a rising
    citation_not_found is a prompt problem, and one total for both tells nobody which."""
    ps = passages()
    result = citations.verify(
        "The rate is 154 per hour.[1] The fee is 999.00 per visit.[1] See also [7].", ps)
    assert result.refusals["number_not_in_source"] == 2
    assert result.refusals["citation_not_found"] == 1


# --------------------------------------------------------------------------- the splitter's own traps

def test_a_reference_is_not_split_at_its_own_full_stop():
    """`Pub. 515` is a period followed by a space and a capital, which is exactly a sentence boundary, and the
    first splitter cut the sentence into `Pub.` and the rest. The remainder carried the citation, so the
    fragment was reported uncited and a correct answer was marked as having an unsupported sentence.

    The same shape is everywhere in this corpus: `No. 4`, `Rev. Proc. 2024-1`, `U.S.C. 1`.
    """
    for text in ["Pub. 515 covers withholding on nonresident aliens.[1]",
                 "See No. 4 for the exception.[1]",
                 "Rev. Proc. 2024-1 sets the rate.[1]",
                 "Form W-4 is filed by the employee.[1]",
                 "The rate is 145.00 per hour.[1]",
                 "The total is 1,500.00 for the quarter.[1]"]:
        assert len(citations.split_claims(text)) == 1, f"split a reference or a decimal: {text!r}"


def test_a_marker_immediately_after_a_period_still_ends_the_sentence():
    """`...for each employee.[1] Form W-4 is different.[2]` is two sentences. Requiring whitespace directly
    after the period missed the boundary and merged them, which then checked one sentence's figures against
    the other's passage."""
    claims = citations.split_claims("File Form W-2 for each employee.[1] Form W-4 is different.[2]")
    assert len(claims) == 2, claims


def test_the_splitter_never_drops_a_marker():
    """The second version matched the marker as part of the separator and swallowed it, so every claim came out
    with no citation and every claim was reported as uncited. A splitter that loses support is worse than one
    that merges sentences, because the loss is invisible in the output."""
    text = "One.[1] Two.[2] Three. [3] Four.[4]"
    found = [m for claim in citations.split_claims(text) for m in citations.CITATION.findall(claim)]
    assert sorted(found) == ["1", "2", "3", "4"], found
