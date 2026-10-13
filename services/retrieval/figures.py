"""Catching a confidently wrong read, or saying that nobody checked it.

`RET-PARSE-002`, and the failure it answers is the worst this product has. OCR misreads a character; the wrong
number is faithful to the document **as parsed**, so retrieval finds it, the model cites it, and the citation
layer confirms the number is present in the cited passage **because OCR is what put it there**. Every check
passes and the client receives a confident wrong figure with a real citation.

Measured in step 3.4: forty trials carving defects into a cell whose truth was 145.00 produced fifteen wrong
readings, and the parser's own confidence flagged **one** of them. Fourteen wrong numbers passed at confidence 61
to 93. So confidence is not the instrument, and the decision recorded in the architecture says what is:

**Format and arithmetic checks, whose honest ceiling is the malformed quarter of misreads and nothing else.** A
misread that produces something a number cannot be is caught. A misread that produces a different plausible
number is not, and no amount of care in this file changes that. Saying so is the point: a check described as
catching OCR errors would be believed, and this one catches the quarter of them that are malformed.

**And disclosure, for everything the checks cannot reach.** A figure that passed no arithmetic check and has no
second source is cited **as machine-read** rather than implied correct. That is the honest half: the client is
told which numbers nobody verified, instead of being told all of them were.

**What this deliberately is not.** It is not a refusal. A refusal fires on correct answers too, and the decision
records that as the reason it was rejected. It is not a review of every document: every page is read without a
person and most documents get no review at all.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

# A figure as a person writes one in a business document: a currency amount, a percentage, a count, a date.
AMOUNT = re.compile(r"^\(?-?[$€£]?\s?\d{1,3}(?:,\d{3})*(?:\.\d{1,2})?\)?$")
PLAIN = re.compile(r"^\(?-?\d+(?:\.\d{1,4})?\)?$")
# Four digits before the point, not three, so that an out-of-range percentage is caught by the RANGE check and
# gets a message about what it might be. Capping the shape at three made 1450 percent fall through to "not a
# figure", which is true and useless: the digits are fine and the value is what is wrong.
PERCENT = re.compile(r"^-?\d{1,4}(?:\.\d{1,2})?\s?%$")
DATE_ISO = re.compile(r"^(\d{4})-(\d{2})-(\d{2})$")
DATE_SLASH = re.compile(r"^(\d{1,2})/(\d{1,2})/(\d{4})$")


@dataclass
class Finding:
    """Something wrong with a figure, or something that could not be checked."""

    code: str
    detail: str


@dataclass
class Verdict:
    value: str
    findings: list[Finding] = field(default_factory=list)
    # Whether an arithmetic relationship in the document confirmed it. None means the document had no
    # relationship to check against, which is most documents and is not a failure.
    confirmed_by_arithmetic: bool | None = None
    # A second independent reading, when one exists. None means there was none, which is different from one that
    # disagreed.
    second_reading_agrees: bool | None = None

    @property
    def malformed(self) -> bool:
        return any(finding.code.startswith("malformed") for finding in self.findings)

    @property
    def verified(self) -> bool:
        """Whether anything other than the one reading confirmed this figure.

        The property the disclosure turns on. A figure confirmed by arithmetic or by a second reading that agrees
        is as verified as this system can make it. Everything else is a single machine read of a number nobody
        checked, and it is disclosed as such.
        """
        if self.malformed or (self.second_reading_agrees is False):
            return False
        return bool(self.confirmed_by_arithmetic) or self.second_reading_agrees is True

    def as_record(self) -> dict:
        return {"figure": self.value, "malformed": self.malformed, "verified": self.verified,
                "confirmed_by_arithmetic": self.confirmed_by_arithmetic,
                "second_reading_agrees": self.second_reading_agrees,
                "findings": [{"code": f.code, "detail": f.detail} for f in self.findings]}


def parse_number(token: str) -> float | None:
    """The number a token denotes, or None when it denotes nothing.

    Parentheses are negative, which is how a financial statement writes one and how a figure that lost its minus
    sign in OCR can still be read correctly. A trailing percent is a hundredth of the number, so a range check on
    a percentage is a range check on the number.
    """
    token = token.strip()
    negative = token.startswith("(") and token.endswith(")")
    # The parentheses are removed as well as the currency and separators, because they are the sign rather than
    # decoration. Leaving them made `(1,250.00)` fail to parse at all, so a statement's negative figure read as
    # not a number.
    cleaned = re.sub(r"[$€£,%\s()]", "", token)
    if not cleaned or not re.match(r"^-?\d*\.?\d+$", cleaned):
        return None
    try:
        value = float(cleaned)
    except ValueError:
        return None
    return -value if negative else value


def check_format(token: str) -> list[Finding]:
    """Whether a token could be the kind of number it is shaped like.

    The honest ceiling, stated where it is implemented: a misread that produces a malformed figure is caught
    here, and a misread that produces a different well-formed figure is not caught by anything in this file.
    """
    token = token.strip()
    if not token:
        return [Finding("malformed.empty", "an empty figure is not a figure")]
    findings: list[Finding] = []

    iso = DATE_ISO.match(token)
    slashed = DATE_SLASH.match(token)
    if iso or slashed:
        if iso:
            year, month, day = (int(part) for part in iso.groups())
        else:
            month, day, year = (int(part) for part in slashed.groups())
        # A real calendar rather than a range on each part, because 31 February passes `1 <= day <= 31` and is
        # exactly the kind of damaged read this is for. The first version accepted it.
        import datetime
        try:
            datetime.date(year, month, day)
        except ValueError as exc:
            findings.append(Finding("malformed.date", f"{token} is not a date: {exc}"))
        return findings

    if PERCENT.match(token):
        value = parse_number(token)
        # The line is where a percentage stops being plausible as ANY percentage rather than where it stops being
        # plausible as a rate. **145 percent is a growth rate and a real figure**, so flagging it would be a check
        # that fires on correct documents, which is the failure mode that gets a check turned off. A thousand
        # percent is a damaged read with near certainty: nothing in a small business's documents grows by an
        # order of magnitude and writes it as a percentage.
        #
        # This is narrower than "a percentage outside its range" and it is narrower on purpose. The decision
        # records that this control's honest ceiling is the malformed quarter of misreads, and a plausible
        # out-of-range percentage is not malformed.
        if value is not None and abs(value) >= 1000:
            findings.append(Finding("malformed.percent_range",
                                    f"{token} is past a thousand percent, which is what a damaged read looks "
                                    f"like rather than what a rate looks like"))
        return findings

    if AMOUNT.match(token) or PLAIN.match(token):
        value = parse_number(token)
        if value is None:
            findings.append(Finding("malformed.not_a_number", f"{token!r} does not parse as a number"))
        elif abs(value) >= 1e13:
            # Past this a figure is more likely two cells run together than a real amount. Not a hard limit:
            # the finding says which it looks like.
            findings.append(Finding("malformed.implausible_magnitude",
                                    f"{token} is larger than any figure a small business document carries, "
                                    f"which is what two columns joined together looks like"))
        return findings

    return [Finding("malformed.not_a_figure", f"{token!r} is not shaped like any figure this reads")]


def check_arithmetic(rows: list[list[str]], total_token: str | None = None) -> bool | None:
    """Whether a column of line items sums to its stated total.

    `None` when the document has no such relationship, which is most documents and is not a failure: a paragraph
    containing a rate has nothing to add up. This is the second control the decision names and it needs a
    document that already states the relationship, which financial tables do and prose does not.

    Tolerance is a cent, because a table rounded to whole units will not sum exactly and refusing every such
    table would be a check that fires on correct documents.
    """
    if total_token is None or len(rows) < 2:
        return None
    total = parse_number(total_token)
    if total is None:
        return None
    items = [parse_number(cell) for row in rows for cell in row]
    items = [value for value in items if value is not None]
    if not items:
        return None
    return abs(sum(items) - total) < 0.005


def second_reading(token: str, other: str | None) -> bool | None:
    """Whether an independent reading of the same figure agrees.

    `None` when there was no second reading, which is different from one that disagreed and is the distinction the
    disclosure rests on. **An escalation gate and never a vote**, as the decision records: two readings that
    disagree do not average, they go to a person.
    """
    if other is None:
        return None
    first, second = parse_number(token), parse_number(other)
    if first is None or second is None:
        return False
    return abs(first - second) < 0.005


def assess(token: str, arithmetic_total: str | None = None, arithmetic_rows: list[list[str]] | None = None,
           other_reading: str | None = None) -> Verdict:
    """Everything checkable about one figure, and whether anything confirmed it."""
    verdict = Verdict(value=token, findings=check_format(token))
    if arithmetic_rows is not None:
        verdict.confirmed_by_arithmetic = check_arithmetic(arithmetic_rows, arithmetic_total)
    verdict.second_reading_agrees = second_reading(token, other_reading)
    return verdict


# --------------------------------------------------------------------------- disclosure

MACHINE_READ = ("machine-read from a scanned page and not confirmed by any other check in the document")


def disclosure_for(passage, verdict: Verdict | None = None) -> str | None:
    """What a citation has to say about a figure nobody verified, or None when it has nothing to add.

    Two conditions, and both are needed. The passage must have been read off an image, because a figure from a
    digital text layer was not recognised by anything. And nothing else may have confirmed the figure, because a
    number that ties to its own column total has been checked by the document.

    Returning None is the common case and is not a claim that the figure is right: it is the absence of something
    to disclose. **A citation that says nothing is not a guarantee, and the difference is why this returns text
    rather than a boolean**: the client is told which numbers nobody verified rather than being reassured about
    all of them.
    """
    if verdict is not None and verdict.verified:
        return None
    if not getattr(passage, "is_ocr", False):
        return None
    return MACHINE_READ


def disclose_citation(passage, text: str, verdict: Verdict | None = None) -> str:
    """A citation with its caveat attached, if it has one."""
    note = disclosure_for(passage, verdict)
    if note is None:
        return text
    return f"{text} [{note}]"
