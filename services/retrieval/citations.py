"""The mechanical layer: seven checks over an answer's citations, no model involved.

The research states the scope and the value in the same sentence. The mechanical layer "covers fabricated
citations, page drift, missing quotes, numeric substitution, identifier substitution, superseded sources and
low-confidence scans", and the literal identifier assertion within it is "the cheapest item in the entire
architecture and the highest value".

Why it matters is one level up from retrieval. Every layer below this one checks whether the answer matches the
source, and none of them checks whether the source is the right source or was read correctly. Those are the two
most likely wrong answers in a small business, a real number from a superseded document and a real number from
a misread scan, and both pass the mechanical layer, both pass entailment, and both come out certified.

This module is Layer 0: deterministic, no model, no new dependency. Entailment is Layer 1 and is not here,
because it needs a model and the reply path does not have one yet. What this returns is a verdict per claim and
a reason a person can read.

**The honest bound.** The confidence gate is document-level rather than page-level, because docling-serve does
not populate the per-page confidence map this release exposes. So a page whose digits were misread cannot be
told from a page read cleanly, and the gate refuses on a low-scoring document rather than on a low-scoring
page. That is coarser than the architecture asks for and it is recorded rather than papered over.
"""
from __future__ import annotations

import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "ingestion"))
import identifiers as identifier_extractor  # noqa: E402

# Below this, a number or an identifier in an answer is not trusted to have been read correctly. The research
# asks for a per-page threshold and the score available is per document, so this is the document's own score.
# Chosen rather than tuned: the corpus's digitally-native documents score 1.0 and its scanned fixture scores
# null, so the threshold only has to separate "the parser was confident" from "the parser was not".
MIN_PARSE_SCORE = 0.80

# A citation marker: [1], [12]. Deliberately the only supported form, because a verifier that guesses at
# citation syntax is a verifier that can be made to see a citation where there is none.
CITATION = re.compile(r"\[(\d{1,3})\]")

# A quoted span, either kind of quote. Two characters minimum, so an apostrophe in "client's" is not a quote.
QUOTED = re.compile(r"[\"“]([^\"”]{2,})[\"”]")

# A number as a person writes one: 145.00, 1,500, 0.67, 32. Two digits minimum unless it has a decimal point,
# because a bare "1" is a list marker or a page number far more often than it is a figure.
NUMBER = re.compile(r"\b\d{1,3}(?:,\d{3})+(?:\.\d+)?\b|\b\d+\.\d+\b|\b\d{2,}\b")


def normalize_number(token: str) -> str:
    """A figure reduced to what two spellings of it have in common.

    `145.00`, `145.0` and `145` are one number, and so are `1,500` and `1500`. Trailing zeros after a decimal
    point are dropped because a fee schedule writes `145.00` and an answer writes `145`, and refusing that
    answer would be a false refusal on a correct claim, which is the failure mode that gets a check turned off.
    """
    token = token.replace(",", "").strip()
    if "." in token:
        # Trailing zeros only. `rstrip("0")` on `0.67` would give `.67`, which is why the integer part is not
        # touched here: the first version stripped the leading zero off every decimal below one and turned
        # `0.67`, the mileage rate, into `.67`, which then matched nothing.
        token = token.rstrip("0").rstrip(".")
        whole, _, fraction = token.partition(".")
        whole = whole.lstrip("0") or "0"
        return f"{whole}.{fraction}" if fraction else whole
    return token.lstrip("0") or "0"


def numbers_in(text: str) -> set[str]:
    return {normalize_number(match.group(0)) for match in NUMBER.finditer(text)}


@dataclass
class Claim:
    """One sentence of an answer, the passages it cites, and what the checks made of it."""

    text: str
    citations: list[int] = field(default_factory=list)
    # supported, or refused with a reason. `supported` means every mechanical check passed, which is not the
    # same as true: entailment is Layer 1 and is not built here.
    verdict: str = "supported"
    reason: str | None = None
    # What failed, in words a person can act on. A verdict without a detail is a refusal nobody can check.
    detail: str | None = None
    # The passages that supported it, as the citation indices they were cited by.
    supporting: list[int] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.verdict == "supported"


@dataclass
class Verified:
    claims: list[Claim] = field(default_factory=list)
    # Claims refused and why, counted by reason, which is the number to watch: a rising `number_not_in_source`
    # is a generation problem and a rising `citation_not_found` is a prompt problem.
    refusals: dict[str, int] = field(default_factory=dict)

    @property
    def all_supported(self) -> bool:
        return all(claim.ok for claim in self.claims)

    @property
    def supported_text(self) -> list[str]:
        return [claim.text for claim in self.claims if claim.ok]

    @property
    def refused(self) -> list[Claim]:
        return [claim for claim in self.claims if not claim.ok]


# Words whose full stop is part of the word. A reference like `Pub. 515` is a period followed by a space and
# an uppercase letter, which is exactly what a sentence boundary looks like, and the first splitter cut
# "Pub. 515 covers withholding" into "Pub." and the rest. The remainder then carried the citation and the
# fragment was reported uncited, so a correct answer was marked as having an unsupported sentence.
ABBREVIATIONS = frozenset({
    "pub", "no", "nos", "rev", "proc", "sec", "sect", "art", "para", "ch", "vol", "ed", "fig", "eq",
    "inc", "ltd", "co", "corp", "dept", "div", "est", "approx", "vs", "cf", "eg", "ie", "al", "repr",
    "jan", "feb", "mar", "apr", "jun", "jul", "aug", "sep", "sept", "oct", "nov", "dec", "mr", "mrs",
    "ms", "dr", "st", "ave", "rd", "u.s", "u.s.c", "i.r.c",
})

# A citation index, kept for the fold-back below.
MARKER_ONLY = re.compile(r"^\[\d{1,3}\]$")


def _splits_here(text: str, dot: int) -> bool:
    """Whether the period at `dot` ends a sentence.

    Deliberately conservative, because the two errors are not symmetric. Splitting wrongly takes a sentence's
    words away from its citation, so the fragment is checked against nothing and reported uncited: a false
    refusal on a correct answer. Not splitting wrongly merges two sentences into one claim, which is checked as
    a whole and stays correct as long as both are supported. So anything ambiguous is not a boundary.
    """
    before = text[:dot].rstrip()
    if not before:
        return False
    word = re.split(r"[\s(\[]", before)[-1].rstrip(".").lower()
    if word in ABBREVIATIONS:
        return False
    # A single initial, as in "R. Smith".
    if len(word) == 1:
        return False
    # Markers may sit between the period and the space: `...for each employee.[1] Form W-4...` is two
    # sentences, and requiring whitespace immediately after the period missed the boundary entirely and merged
    # them into one claim. Skipping the markers first is what finds it.
    after = text[dot + 1:]
    while True:
        marker = re.match(r"\s*\[\d{1,3}\]", after)
        if not marker:
            break
        after = after[marker.end():]
    if not after:
        return True          # the period ends the text, markers and all
    if not after[0].isspace():
        return False
    rest = after.lstrip()
    # A sentence starts with a capital, a digit, or nothing. `145.00 per hour` has no space after the point so
    # it never reaches here, and `0.67 per mile` likewise.
    return not rest or rest[0].isupper() or rest[0].isdigit() or rest[0] in "[("


def split_claims(answer: str) -> list[str]:
    """Sentences, each keeping the citation markers that follow it.

    The markers trail the sentence they support, which is the prompt's convention. A claim's support is only
    meaningful if the marker stays with the words it belongs to: `January. [2]` means [2] supports the January
    sentence, and reading it as support for whatever comes next would check the wrong passage and refuse a
    correct answer.
    """
    text = answer.strip()
    boundaries = [0]
    for index, char in enumerate(text):
        if char in "!?":
            boundaries.append(index + 1)
        elif char == "." and _splits_here(text, index):
            boundaries.append(index + 1)
    boundaries.append(len(text))

    claims: list[str] = []
    for start, end in zip(boundaries, boundaries[1:]):
        part = text[start:end].strip()
        if not part:
            continue
        # Leading markers belong to the sentence before this one, because a marker trails what it supports and
        # the boundary was found at the sentence it follows. `January. [2] Done here.` is two claims and [2]
        # supports the first, so leaving it on the second would check the wrong passage.
        leading = re.match(r"^(?:\[\d{1,3}\]\s*)+", part)
        if leading and claims:
            moved = leading.group(0).strip()
            claims[-1] = f"{claims[-1]} {moved}"
            part = part[leading.end():].strip()
            if not part:
                continue
        if claims and MARKER_ONLY.fullmatch(part):
            claims[-1] = f"{claims[-1]} {part}"
        else:
            claims.append(part)
    return claims


def verify_claim(text: str, passages: list, min_score: float = MIN_PARSE_SCORE) -> Claim:
    """Run the checks for one claim against the passages it cites.

    The order is deliberate. A citation that does not resolve is checked first, because every later check reads
    the passage it names and a fabricated index would make them all meaningless. Supersession is second,
    because a number from a replaced document is a correct quote of the wrong source and no amount of literal
    matching catches it. The literal checks come last, since they are the ones that can be false-refused.
    """
    claim = Claim(text=text)
    cited = sorted({int(match.group(1)) for match in CITATION.finditer(text)})

    if not cited:
        # Not a refusal. A sentence with no citation may be a transition or a direct answer to a question about
        # the conversation, and refusing those would refuse most of a normal reply. The caller decides what to
        # do with a claim that has no support; this reports it rather than inventing a verdict.
        claim.verdict = "uncited"
        claim.reason = "no_citation"
        claim.detail = "the sentence carries no citation marker, so none of the checks could be run on it"
        return claim

    claim.citations = cited
    supporting = []
    for index in cited:
        # A citation index is a position in the retrieved set, so an index outside it is a citation to
        # something that was never retrieved. This is the fabricated-citation check and it is exact.
        if index < 1 or index > len(passages):
            claim.verdict = "refused"
            claim.reason = "citation_not_found"
            claim.detail = (f"cites [{index}] and {len(passages)} passage(s) were retrieved, so the citation "
                            f"names something that was never in front of the model")
            return claim
        supporting.append(passages[index - 1])
    claim.supporting = cited

    # Supersession, and the word ONLY is the whole design. A claim citing two passages is supported if either
    # is current, because the current one is the source the answer should rest on. Refusing on any superseded
    # citation would refuse an answer that correctly cites the replacement alongside the thing it replaced.
    if supporting and all(not getattr(passage, "is_current", True) for passage in supporting):
        replaced = sorted({passage.doc_id for passage in supporting})
        claim.verdict = "refused"
        claim.reason = "superseded"
        claim.detail = (f"every passage it cites is from a document a person has declared replaced "
                        f"({', '.join(replaced)}), so the figure may be real and out of date")
        return claim

    # The confidence gate. It applies to numbers and identifiers only, because prose read imperfectly is still
    # readable prose and a digit read imperfectly is a different number. This is the document-level version of
    # a check the architecture specifies per page.
    # **A citation marker is a pointer, not a figure.** The supported form is `[n]`, and the number
    # extractor read the `12` in `[12]` as a number the cited passage had to contain. Correct sentences were
    # therefore refused as soon as an answer cited ten or more passages. Strip the marker before every literal
    # check; the claim's text keeps its markers, because that is what the reader is shown.
    literal = CITATION.sub(" ", text)
    figures = numbers_in(literal) | set(identifier_extractor.extract(literal))
    if figures:
        for passage in supporting:
            score = getattr(passage, "parse_score", None)
            if score is None:
                continue
            if score < min_score:
                claim.verdict = "refused"
                claim.reason = "low_confidence_scan"
                claim.detail = (
                    f"states a number or reference and rests on {passage.doc_id}, which the parser scored "
                    f"{score:.2f} against a floor of {min_score:.2f}. The page image should be shown beside "
                    f"the figure so it can be read by eye.")
                return claim

    # The literal checks. Everything the claim states as a fact has to appear in the text it cites.
    haystack = "\n".join(getattr(passage, "text", "") or "" for passage in supporting)
    haystack_numbers = numbers_in(haystack)
    haystack_normal = _normal(haystack)

    # Written out rather than relying on `&` and `-` binding the way it looks like they do, which they do not.
    # The first version read as though it intersected with the claim's figures and happened to be right because
    # the missing set is a subset of them.
    claimed_numbers = numbers_in(literal)
    missing_numbers = sorted(claimed_numbers - haystack_numbers)
    if missing_numbers:
        claim.verdict = "refused"
        claim.reason = "number_not_in_source"
        claim.detail = (f"states {', '.join(missing_numbers)} and the cited passage does not contain it. "
                        f"This is the check that catches a substituted digit.")
        return claim

    # Canonical on both sides. Extraction is deliberately generous, so `Pub. 515` yields three tokens and a
    # passage saying `Publication 515` yields two: the sets overlap without being equal, and comparing them
    # directly reported `pub515` as missing from a passage that says exactly that in its long form. Comparing
    # canonical forms is the same equality the retrieval arm uses, so the two cannot disagree about what
    # counts as the same reference.
    claimed_identifiers = identifier_extractor.canonical_set(literal)
    present_identifiers = identifier_extractor.canonical_set(haystack)
    missing_identifiers = sorted(
        token for token in claimed_identifiers
        if token not in present_identifiers and token not in haystack_normal)
    if missing_identifiers:
        claim.verdict = "refused"
        claim.reason = "identifier_not_in_source"
        claim.detail = (f"names {', '.join(missing_identifiers)} and the cited passage does not mention it. "
                        f"This is the check that catches a citation to the right document and the wrong "
                        f"form.")
        return claim

    for quoted in QUOTED.findall(literal):
        if _normal(quoted) not in haystack_normal:
            claim.verdict = "refused"
            claim.reason = "quote_not_in_source"
            claim.detail = (f'quotes "{quoted[:60]}" and the cited passage does not contain those words. A '
                            f'quote that is not in the source is the most checkable kind of wrong.')
            return claim

    return claim


def _normal(text: str) -> str:
    """Whitespace and case reduced, so a quote broken across a line still matches."""
    return re.sub(r"\s+", " ", text).strip().lower()


def verify(answer: str, passages: list, min_score: float = MIN_PARSE_SCORE) -> Verified:
    """Every claim in an answer, checked.

    A claim is marked rather than dropped. The research's requirement is that an unsupported sentence is marked,
    not silently kept, and marking is also what lets a person see which sentence failed and why. Dropping it
    here would leave a reply that reads cleanly and lost the part that was wrong, which is the failure this
    whole layer exists to prevent.
    """
    result = Verified()
    for text in split_claims(answer):
        claim = verify_claim(text, passages, min_score)
        result.claims.append(claim)
        if not claim.ok:
            result.refusals[claim.reason or "unknown"] = result.refusals.get(claim.reason or "unknown", 0) + 1
    return result


# --------------------------------------------------------------------------- page drift

def check_page(text: str, passage, pages: list) -> str | None:
    """Whether a claim's stated page matches the passage it cites.

    Page drift is a citation to a real passage with the wrong page number attached, which survives every literal
    check because the words are all there. It is separated from `verify_claim` because it needs the passage list
    rather than one passage, and it is a function rather than a flag so a caller that has no page claims pays
    nothing for it.
    """
    claimed = re.search(r"\bpage\s+(\d{1,4})\b", text, re.IGNORECASE)
    if not claimed:
        return None
    page = int(claimed.group(1))
    if page in pages:
        return None
    if getattr(passage, "page_is_nominal", False):
        return (f"the claim says page {page} and this document has no pages: it is a format the parser reads "
                f"without page numbers, so the page is nominal and should not be shown as a citation")
    return (f"the claim says page {page} and the cited passage is page {passage.page_start}, which is page "
            f"drift: the words are right and the place is wrong")
