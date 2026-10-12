"""The disagreement probe's summary arithmetic, pinned against a recorded run.

This exists because that arithmetic was wrong twice, in two different ways, and the second way is the
reason a test is warranted rather than a careful reading.

The first version divided by the wrong denominator and printed "750 percent of them". That is absurd on
its face, so it was caught immediately by looking at it. The second version had the two per-engine
totals swapped, and it read perfectly coherently: tesseract wrong on 4, rapidocr wrong on 15, every
number self-consistent, every conclusion plausible. It would have been read straight through by anyone
who did not happen to recheck the input data, and it said the opposite of the truth about which engine
this pipeline should trust.

So the numbers below are one real run, recorded from step 3.4 of the runbook: forty trials of damage
carved into the cell whose truth is 145.00, read by Tesseract and by the parser's own RapidOCR.

Run: python3 -m pytest tests/
"""
from __future__ import annotations

# One recorded run. Each row is (tesseract, rapidocr), values as digits only.
RECORDED = [
    ("14500", "14500"), ("14500", "14300"), ("1450", "14500"), ("14560", "14500"),
    ("14500", "14500"), ("94500", "14500"), ("14500", "14500"), ("14500", "14500"),
    ("44500", "14500"), ("14500", "14500"), ("14500", "14500"), ("14500", "14500"),
    ("14500", "14500"), ("14500", "14500"), ("14500", "14500"), ("14600", "14600"),
    ("4500", "14500"), ("14500", "14500"), ("145100", "14500"), ("14500", "14500"),
    ("14500", "14500"), ("1450", "1450"), ("14500", "14500"), ("1450", "14500"),
    ("14500", "14500"), ("14500", "11500"), ("500", "14500"), ("14500", "14500"),
    ("14508", "14500"), ("14500", "14500"), ("14500", "14500"), ("145800", "14500"),
    ("1450", "14500"), ("14500", "14500"), ("14500", "14500"), ("145800", "14500"),
    ("14500", "14500"), ("14500", "14500"), ("14508", "14500"), ("14500", "14500"),
]
TRUTH = "14500"


def tally() -> dict[str, int]:
    stats = {"both_right": 0, "disagree": 0, "both_wrong_agree": 0,
             "t_right_r_wrong": 0, "r_right_t_wrong": 0}
    for t_value, r_value in RECORDED:
        t_ok, r_ok = t_value == TRUTH, r_value == TRUTH
        if t_ok and r_ok:
            stats["both_right"] += 1
        elif not t_ok and not r_ok:
            if t_value == r_value:
                stats["both_wrong_agree"] += 1
            else:
                stats["disagree"] += 1
        else:
            stats["disagree"] += 1
            stats["t_right_r_wrong" if t_ok else "r_right_t_wrong"] += 1
    return stats


def totals(stats: dict[str, int], total: int) -> tuple[int, int, int]:
    """The three derived numbers the summary prints, in the order it prints them."""
    t_wrong = total - stats["both_right"] - stats["t_right_r_wrong"]
    r_wrong = total - stats["both_right"] - stats["r_right_t_wrong"]
    caught = stats["t_right_r_wrong"]
    return t_wrong, r_wrong, caught


def test_the_recorded_run_counts_as_it_did_on_the_box():
    stats = tally()
    assert stats["both_right"] == 23
    assert stats["disagree"] == 15
    assert stats["both_wrong_agree"] == 2
    assert stats["t_right_r_wrong"] == 2
    assert stats["r_right_t_wrong"] == 13


def test_rapidocr_is_the_stronger_engine_on_this_run():
    """The direction that matters. RapidOCR is what the pipeline already uses, and it failed four
    times where Tesseract failed fifteen. The swapped version of this summary asserted the inverse,
    which would have pointed the design at replacing the wrong component."""
    t_wrong, r_wrong, _ = totals(tally(), len(RECORDED))
    assert r_wrong < t_wrong, "rapidocr should have fewer failures than tesseract on this run"
    assert (t_wrong, r_wrong) == (15, 4)


def test_the_second_reader_catches_half_of_what_the_first_misses():
    stats = tally()
    _, r_wrong, caught = totals(stats, len(RECORDED))
    assert r_wrong == 4
    assert caught == 2, "tesseract caught two of rapidocr's four failures"
    assert r_wrong - caught == 2, "two wrong figures would still go unflagged"


def test_both_engines_failing_together_is_rare_but_real():
    """The number no second reader can reduce, and the one worth watching as the sample grows."""
    stats = tally()
    assert stats["both_wrong_agree"] == 2
    assert stats["both_wrong_agree"] / len(RECORDED) == 0.05
