#!/usr/bin/env python3
"""Author restricted content, because a permission test needs something to protect.

The corpus is four documents, all under `company/`. The requirement for this step is that a staff-role
query cannot retrieve executive content, and with no executive content that test passes for the wrong
reason: it would pass against a retriever with no filter at all, because there is nothing restricted to
leak. That is the failure mode a permissions test is most prone to, so the fixture comes first.

The content is synthetic and obviously so. It carries two figures that appear nowhere else in the corpus,
so a leak is detectable by searching for the figure rather than only by comparing counts, and it is a
Markdown file because the parser reads one without OCR and the whole fixture ingests in seconds.

Run: python3 tests/fixtures/build_executive_fixture.py
Output: fixtures/corpus/executive-compensation.md
"""
from __future__ import annotations

from pathlib import Path

TARGET = Path(__file__).resolve().parents[2] / "fixtures" / "corpus" / "executive-compensation.md"

# Two figures that exist nowhere else in the corpus. Chosen so they cannot be confused with a figure from
# the 10-K or the IRS publication, which makes a leak greppable.
SALARY = "418,750"
BONUS_TARGET = "62.5"

CONTENT = f"""# Executive Compensation Memorandum

**CONFIDENTIAL. Board and named executives only.**

This memorandum records the compensation decisions for the current fiscal year. It is not distributed
beyond the board and the executives named in it.

## Base compensation

The Chief Executive Officer's base salary is {SALARY} dollars per year, effective from the first day of
the fiscal year. This figure replaces the prior-year base of 398,000 dollars.

The Chief Financial Officer's base salary is 315,400 dollars per year. The Chief Operating Officer's base
salary is 297,800 dollars per year.

## Annual incentive

The annual bonus target for the Chief Executive Officer is {BONUS_TARGET} percent of base salary, payable
on achievement of the board's stated objectives. The target for other named executives is 45 percent of
base salary.

## Equity

Long-term incentive awards are granted annually in restricted stock units. The Chief Executive Officer's
target award for the current year is 1,250,000 dollars in grant-date value. Vesting is over four years,
with one quarter vesting on each anniversary of the grant date.

## Severance

Severance for the Chief Executive Officer is payable on a change of control under the terms of the
executive severance plan, at two times base salary plus target bonus. Severance arrangements for other
named executives are set out in their individual agreements.

## Distribution

This memorandum is retained in the executive folder only. Questions about it are answered by the board
chair, not by the management team.
"""


def main() -> int:
    TARGET.parent.mkdir(parents=True, exist_ok=True)
    TARGET.write_text(CONTENT)
    print(f"wrote {TARGET.name}: {len(CONTENT)} characters")
    print(f"  restricted figures: {SALARY}, {BONUS_TARGET} percent")
    print(f"  both appear nowhere else in the corpus, so a leak is greppable")
    print()
    print("Install it by copying into the executive documents root:")
    print(f"  mkdir -p documents/executive && cp {TARGET.relative_to(TARGET.parents[2])} "
          f"documents/executive/")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
