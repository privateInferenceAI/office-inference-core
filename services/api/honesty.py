"""The honesty gate: a question about the company's own attribute needs the attribute stated in the evidence.

**Why this is mechanical rather than another prompt rule.** U08 asked for the company's parental leave policy.
The corpus contains FMLA law text and a contractor clause but no company leave policy. Two prompt iterations were
measured: the first named policies and did not stop the answer, and cost N05 four runs of five; the second did
stop it and cost N07 and X07 five runs of five each, because the refusal wording changed how the model searched
numeric tables. A prompt rule that trades one failure for two is not a fix.

This gate is the narrow deterministic version. It applies only to questions phrased as an attribute of the
company (`the company's <subject>`). If no retrieved passage states that subject as a phrase, the honest answer
is the refusal, because the corpus is a collection of filings and tables rather than the company's own handbook.
The check runs before the answer is accepted: the JSON path replaces the model's answer, the tool stream replaces
it before emitting, and the single-shot stream never calls the model at all.

**What it deliberately does not do.** It does not guess synonyms, and it does not fire on questions about the
documents in general. A deployment whose client asks policy questions in different words is not covered, and that
is the intended boundary: a rule that cannot be checked is what this replaced.
"""
from __future__ import annotations

import os
import re

REFUSAL = "I could not find that in the available documents."

# Possessives that mean "the client's own organisation", not a document or a person.
_POSSESSIVE = re.compile(
    r"\b(?:the\s+)?(?:company|firm|organisation|organization)'s\s+([^?.!,;:]{3,90})", re.IGNORECASE)

# Words that carry no distinguishing meaning in the subject; stripped from the ends of the phrase.
_LEADING = ("the ", "its ", "own ")
_TRAILING = (" policy", " procedure", " plan", " handbook")

_STOPWORDS = frozenset({
    "the", "a", "an", "of", "for", "on", "in", "to", "and", "or", "is", "are", "was", "were",
    "does", "do", "did", "what", "which", "who", "how", "say", "says", "stated", "state",
    "according", "as", "by", "with", "its", "their", "this", "that", "it", "be", "been",
})


def _words(text: str) -> list[str]:
    return re.findall(r"[a-z0-9]+", (text or "").replace("\u2019", "'").lower())


def gate_enabled() -> bool:
    """The gate rides the prompt-honesty flag, so turning that off is the rollback for both."""
    return os.environ.get("OI_TOOL_HONESTY", "on").strip().lower() not in ("off", "0", "false", "no")


def subject_phrase(question: str) -> str:
    """The phrase the question asks the company to state, normalised to lowercase words."""
    match = _POSSESSIVE.search((question or "").replace("\u2019", "'"))
    if not match:
        return ""
    phrase = " ".join(_words(match.group(1)))
    for prefix in _LEADING:
        if phrase.startswith(prefix.strip() + " "):
            phrase = phrase[len(prefix):]
    # Remove a trailing generic noun only when something distinguishing remains before it.
    for suffix in _TRAILING:
        stripped = phrase[: -len(suffix)]
        if phrase.endswith(suffix) and " ".join(_words(stripped)).strip():
            phrase = " ".join(_words(stripped))
            break
    return phrase.strip()


def unstated_company_attribute(question: str, passage_texts) -> bool:
    """True when the question asks for a company attribute and the evidence never states it.

    The comparison is a contiguous phrase match over normalised words, so "parental leave" in the evidence matches
    "parental leave" in the question and "parental-leave" too, while a passage that only mentions the topic (a
    law, a form, a court instruction) does not match. A question with no distinctive subject is not gated: an
    over-broad gate would refuse questions this deployment can answer.
    """
    phrase = subject_phrase(question)
    if not phrase or all(word in _STOPWORDS for word in _words(phrase)):
        return False
    evidence = " " + " ".join(_words(" ".join(passage_texts or []))) + " "
    return (" " + phrase + " ") not in evidence
