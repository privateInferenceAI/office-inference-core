"""Find the exact references in a question, and in the text that might answer it.

Why this module exists rather than a full-text query. The measurement that opened this step asked seven
identifier questions against the deployed vector arm and against Postgres full-text, and both failed:

    question                          plainto_tsquery                 matched
    What does Pub. 515 cover?         'pub' & '515' & 'cover'         nothing
    When must Form W-3SS be filed?    'must'&'form'&'w-3ss'&...      nothing
    How long must I keep a Form W-4?  'long'&'must'&'keep'&...        nothing

Two separate faults. `plainto_tsquery` ANDs every term of a natural question, so a chunk saying "Pub. 515"
without the word "cover" cannot match. And Postgres splits an identifier unpredictably: `Form W-4` becomes
`'w' & '-4'`, `Form W-3SS` becomes `'w-3ss' & 'w' & '3ss'`. Stemming is right for prose and wrong for a
reference, and no configuration of a text-search parser makes `W-4` stable.

So identifiers are extracted and normalised here, by rules this module owns, and matched as exact tokens.
What that buys is determinism: `W-4` is `w4` on both sides of the search, whatever Postgres's parser thinks.

The alias table is the part that needed a decision. `Pub. 515` and `Publication 515` are different strings
and normalise differently, so a user typing the abbreviation found nothing while the document that answers
them says the long form. Expanding a small set of known prefixes is the cheapest fix, and it is the same
"corpus-mined glossary" the architecture already calls for on the query side.
"""
from __future__ import annotations

import re

# Identifiers are a capitalised or uppercase run, optionally with a full stop, then a space or hyphen, then
# digits, optionally followed by letters: Publication 515, Pub. 515, Form W-4, W-3SS, Section 3(a)(1).
#
# The leading capital is what keeps the junk out. A first version without it extracted `is67` from "the
# mileage rate is 67 cents per mile", because any lowercase word before a number looks like a reference.
PATTERN = re.compile(
    r"\b(?:[A-Z][A-Za-z]{2,}\.?\s?(?:No\.\s?)?\d{1,4}[A-Za-z]{0,4}"   # Publication 515, Form W-4
    r"|[A-Z]-?\d{1,3}[A-Za-z]{0,4}"                                    # W-3SS, W-2
    r"|\d+\s?U\.?S\.?C\.?\s?§?\s?\d+"                                  # 26 USC 3402
    r"|§\s?\d+[A-Za-z]?)"                                              # section symbol
)

# Prefixes expanded to what a document generally spells out. Small and explicit on purpose: an abbreviation
# guessed wrong is worse than one not expanded, because it silently matches something else.
ALIASES = {
    "pub": "publication",
    "public": "publication",
    "form": "form",
    "sec": "section",
    "sect": "section",
}

# Words that look like a prefix but are not part of a reference, so the token behind them is prose.
STOP_PREFIXES = frozenset({"see", "the", "and", "for", "with", "from", "this", "that"})


def normalize(token: str) -> str:
    """The form both sides of the search agree on: lowercase, letters and digits only.

    `Form W-4` and `form w4` and `W4` all become `w4`. Punctuation and case are the two things a person is
    inconsistent about when they type a reference, and this is the only place either is decided.
    """
    return re.sub(r"[^a-z0-9]", "", token.lower())


def canonical(token: str) -> str:
    """A reference reduced to the one form every spelling of it shares.

    `pub515`, `publication515` and a passage that writes either should compare equal, and comparing the
    extracted lists directly does not do that: extraction is deliberately generous, so `Pub. 515` yields three
    tokens and a passage saying `Publication 515` yields two, and the two sets overlap without being equal. A
    verifier asking "is this reference in that passage" needs one form per reference, not a set that is
    different depending on how the writer spelled it.

    The alias table is the same one extraction uses, so the two cannot drift apart.
    """
    token = normalize(token)
    match = re.match(r"^([a-z]+)(\d.*)$", token)
    if not match:
        return token
    prefix, rest = match.groups()
    return f"{ALIASES.get(prefix, prefix)}{rest}"


def canonical_set(text: str) -> set[str]:
    """Every reference in a piece of text, in canonical form."""
    return {canonical(token) for token in extract(text)}


def extract(text: str) -> list[str]:
    """Every reference in a piece of text, normalised and alias-expanded, deduplicated and ordered.

    Order is preserved so a caller can rank by which reference appeared first, which is a weak but real
    signal that the first one names what the text is about.
    """
    out: list[str] = []
    for match in PATTERN.finditer(text):
        raw = match.group(0).strip()
        # Split a leading word from a trailing reference so "Publication 515" becomes two normalised forms,
        # the whole thing and the number alone. A document that says "Publication 515" should be findable by
        # someone who types just "515", and one that says "515" findable by someone who types the long form.
        parts = [normalize(raw)]
        head = re.match(r"^([A-Za-z.]{2,})\s?(\d.*)$", raw)
        if head:
            prefix, rest = normalize(head.group(1)).rstrip("."), normalize(head.group(2))
            expanded = ALIASES.get(prefix, prefix)
            if prefix not in STOP_PREFIXES:
                parts.extend([f"{expanded}{rest}", rest])
        for part in parts:
            if part and part not in out and len(part) > 1:
                out.append(part)
    return out
