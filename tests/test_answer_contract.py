"""The answer contract: what an answer must contain, and the check that can refuse a fabricated figure.

These tests are the measured failures as assertions. The value is in the second half: an answer whose numbers
appear nowhere in the passages must not reach a client, while an arithmetic answer whose operands are present
must, because refusing a correct subtraction would be worse than the thing being prevented.
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "services" / "api"))

import answer_contract  # noqa: E402

PASSAGES = [
    "DP03 | 63.0 | Median household income (dollars) | 59,473 | +/-250",
    "DP03 | 64.0 | Mean household income (dollars) | 77,258 | +/-646",
]


def test_the_prompt_asks_for_the_value_first_and_a_refusal_only_when_absent():
    prompt = answer_contract.build_prompt("[1] somewhere\n[2] else", "What is the income?")
    assert "first sentence" in prompt
    assert "I could not find that in the available documents" in prompt
    assert "Do not reply with only a citation" in prompt
    assert "Passages:" in prompt and "Question: What is the income?" in prompt


def test_the_prompt_allows_combining_two_passages():
    # Measured on 2026-10-18: both Claude models refused cross-document questions 8 times in 8 because the
    # contract said "using only the passages" and they read it as "one passage". The frontier comparison is what
    # found the wording defect; Nova's tendency to ignore the rule had been hiding it.
    prompt = answer_contract.build_prompt("[1] a\n[2] b", "What is the difference?")
    assert "two passages combined" in prompt
    assert "cite both" in prompt


def test_a_value_copied_from_the_passages_is_accepted():
    assert answer_contract.violation("The median household income is 59,473 dollars [1].", PASSAGES) is None


def test_a_figure_no_passage_contains_is_a_violation():
    # The solar-farm fabrication and the invented revenue are this shape: a confident number with no evidence.
    assert answer_contract.violation("The revenue last year was 283,963,765 dollars [4].", PASSAGES) == \
        "unsupported_number"


def test_an_arithmetic_answer_beside_its_operands_is_accepted():
    # 77,258 minus 59,473 is 17,785, and the result is in no passage on purpose: both operands are.
    answer = "The difference is 17,785 dollars, from 77,258 [2] and 59,473 [1]."
    assert answer_contract.violation(answer, PASSAGES) is None


def test_a_citation_without_an_answer_is_a_violation():
    assert answer_contract.violation("[3]", PASSAGES) == "citation_only"
    assert answer_contract.violation("  [1] [2]  ", PASSAGES) == "citation_only"


def test_an_empty_answer_is_a_violation():
    assert answer_contract.violation("", PASSAGES) == "empty_answer"


def test_the_corrective_message_names_the_defect():
    assert "figure that does not appear" in answer_contract.corrective_message("unsupported_number")
    assert "only a citation" in answer_contract.corrective_message("citation_only")
