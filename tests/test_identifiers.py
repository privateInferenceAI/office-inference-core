"""The reference extractor, tested against the strings the measurement produced.

These exist because the first version of the extraction was wrong in two ways that only showed in the numbers.
An early pattern without a leading capital pulled `is67` out of "the mileage rate is 67 cents per mile", and
normalisation alone could not match `Pub. 515` to a document that says `Publication 515`.

Run: python3 -m pytest tests/
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "services" / "ingestion"))
sys.path.insert(0, str(ROOT / "services" / "retrieval"))

import identifiers  # noqa: E402


def test_the_two_sides_agree_on_a_reference_however_it_is_typed():
    """The property the whole arm rests on: the same reference normalises the same way in a question and in
    the document that answers it."""
    pairs = [
        ("When must Form W-3SS be filed?", "File Form W-3SS with Copy A of Form W-2 and Form W-3."),
        ("How long must I keep a Form W-4?", "You must keep a copy of Form W-4 for four years."),
        ("What does Pub. 515 cover?", "Publication 515, Withholding of Tax on Nonresident Aliens."),
        ("What is in Publication 15?", "Pub. 15 explains withholding."),
    ]
    for question, document in pairs:
        q, d = set(identifiers.extract(question)), set(identifiers.extract(document))
        assert q & d, f"the two sides disagree: question gave {q}, document gave {d}"


def test_the_abbreviation_gap_is_closed_in_both_directions():
    """`Pub. 515` and `Publication 515` are different strings and the corpus uses both, so the alias table
    expands the short form rather than hoping a document spells it the way the user typed it."""
    assert "publication515" in identifiers.extract("What does Pub. 515 cover?")
    assert "publication515" in identifiers.extract("What does Publication 515 cover?")
    # And the number alone, so a document saying only "515" is reachable by either form.
    assert "515" in identifiers.extract("What does Pub. 515 cover?")
    assert "515" in identifiers.extract("Publication 515, Withholding of Tax")


def test_prose_before_a_number_is_not_mistaken_for_a_reference():
    """The junk the first pattern produced, which would have made the arm match on ordinary sentences.

    "the mileage rate is 67 cents per mile" yielded `is67`, so every question mentioning a number would have
    matched passages that share nothing but a numeral.
    """
    assert identifiers.extract("What is the mileage rate?") == []
    assert identifiers.extract("The mileage rate is 67 cents per mile for business travel.") == []
    assert identifiers.extract("") == []
    assert identifiers.extract("There are four years to keep it.") == []


def test_normalisation_strips_only_case_and_punctuation():
    """The two things a person is inconsistent about when typing a reference, and the only two this decides."""
    assert identifiers.normalize("Form W-4") == "formw4"
    assert identifiers.normalize("W4") == "w4"
    assert identifiers.normalize("w-4") == "w4"
    assert identifiers.normalize("Pub. 515") == "pub515"
    assert identifiers.normalize("(515)") == "515"


def test_extraction_is_deduplicated_and_ordered():
    """A chunk mentioning Form W-4 six times gets one token, and the order is the order they appear, which a
    caller can use as a weak signal about what the text is about."""
    out = identifiers.extract("Form W-4 ... Form W-4 ... Form W-2")
    assert out.count("w4") == 1, out
    assert out.index("w4") < out.index("w2"), out


def test_a_single_character_token_is_dropped():
    """A bare `1` matches too much to be a reference, and `a1` is not a reference either."""
    assert "1" not in identifiers.extract("See 1 above")
    for token in identifiers.extract("Form W-4 and Pub. 515"):
        assert len(token) > 1
