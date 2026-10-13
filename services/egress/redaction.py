"""What may leave: the data-class gate and the redaction that runs before anything goes to a frontier model.

The audit module has said since Phase 0 that its four patterns are "not a compliance guarantee" and that this
requirement "owns the real engine, which has to run before anything leaves for a frontier model rather than
only on the way into the record". This is that engine.

Two jobs, in this order, because they answer different questions.

**The gate asks whether the request may leave at all.** A data class the client has not permitted on a frontier
lane is refused before redaction is attempted, and the refusal is recorded. Redacting a regulated document and
sending the remainder would be worse than refusing it: the client's decision was about the document, not about
the parts of it a pattern happened to catch.

**The redaction asks what must not leave inside a request that may.** It replaces the identifiers a business
document carries, and it counts what it replaced so the record can say what was removed without keeping it.

Three properties this is built around, each from a way the check could succeed while doing nothing:

* **It runs on the bytes that are sent.** A redactor that runs on a copy, or after serialisation, or on the
  prompt but not the retrieved passages, reports success and changes nothing. So the entry point takes the
  assembled request and returns what to send.
* **One pass, ordered alternatives.** Redacting in a loop over patterns re-scans the text it just wrote, so a
  card number replaced with a label can be re-matched by a later pattern, and an email replaced with
  `[REDACTED-EMAIL]` can be picked up as something else. A single combined pattern cannot re-match its own
  output.
* **The record proves what was sent, not what was typed.** The hash is of the redacted text, because a hash of
  the original is a second copy of the thing being removed, and the audit's own column says it holds "SHA-256 of
  the prompt as sent".

**The honest bound.** This is pattern matching. It catches the identifiers that have a shape, and it does not
catch a name, an address, or an account number written in words. It is a reduction in what leaves, not a
guarantee about what cannot, and calling it a guarantee is how a client ends up sending something they would
not have. The count it returns is the number to watch: a request that suddenly redacts forty things is a request
worth looking at.
"""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field

# The data classes the system uses, weakest first. The order is the escalation order and the gate depends on it.
DATA_CLASSES = ("public", "internal", "confidential", "regulated")


def _luhn(digits: str) -> bool:
    """Whether a digit string satisfies the check digit cards carry.

    Used as evidence rather than as a filter. A 16-digit number that passes Luhn is very likely a card, and one
    that fails may still be a card read imperfectly by OCR. So this decides nothing on its own; it labels the
    match, and the count of unverified long numbers is reported separately so a reader can tell the two apart.
    """
    total, parity = 0, len(digits) % 2
    for index, char in enumerate(digits):
        value = int(char)
        if index % 2 == parity:
            value *= 2
            if value > 9:
                value -= 9
        total += value
    return total % 10 == 0


# Ordered alternatives in one pattern, most specific first, so nothing re-matches what a previous alternative
# replaced. Each label becomes part of the replacement a reader sees.
PATTERNS: tuple[tuple[str, str], ...] = (
    # Credentials first. A key that reaches a frontier model is not a privacy incident, it is an incident: the
    # model provider's logs hold it and rotation is the only remedy. These have unambiguous shapes.
    ("aws_key", r"\b(?:AKIA|ASIA|ABIA|ACCA)[0-9A-Z]{16}\b"),
    ("private_key", r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
    ("bearer", r"\bBearer\s+[A-Za-z0-9\-._~+/]{20,}=*"),
    ("api_key", r"\b(?:sk|pk|rk|api|key|token)[-_][A-Za-z0-9]{16,}\b"),
    # A scoped flag rather than a bare `(?i)`. An inline flag in the middle of a combined expression is a
    # DeprecationWarning on 3.10 and an ERROR on 3.11 and later, and CI runs both, so the bare form would have
    # failed on the newer interpreter with a message about the pattern rather than about this line.
    ("password", r"(?i:\b(?:password|passwd|pwd)\s*[:=]\s*\S+)"),
    # Identity and payment.
    ("ssn", r"\b\d{3}-\d{2}-\d{4}\b"),
    ("ein", r"\b\d{2}-\d{7}\b"),
    ("iban", r"\b[A-Z]{2}\d{2}[A-Z0-9]{11,30}\b"),
    ("card", r"\b(?:\d[ -]?){12,18}\d\b"),
    ("email", r"\b[\w.+-]+@[\w-]+\.[\w.]{2,}\b"),
    # No leading `\b`: a boundary cannot exist before an opening parenthesis, because `(` and the space
    # before it are both non-word characters. The first version therefore matched `860) 555-1234` and left the
    # bracket behind, producing `([REDACTED-PHONE]` in text that is supposed to read cleanly.
    ("phone", r"(?<![\w(])(?:\+1[ .-]?)?\(?\d{3}\)?[ .-]?\d{3}[ .-]?\d{4}\b"),
    ("ipv4", r"\b(?:\d{1,3}\.){3}\d{1,3}\b"),
)

_ALTERNATIVES = "|".join(f"(?P<{label}>{pattern})" for label, pattern in PATTERNS)
# One pass. `finditer` never re-examines text it has already consumed, and a single `sub` cannot see its own
# replacement, which is the property the loop version did not have.
COMBINED = re.compile(_ALTERNATIVES)


@dataclass
class Redaction:
    """What was removed, by kind and count, and never the values."""

    counts: dict[str, int] = field(default_factory=dict)
    # Long numbers that failed the card check digit. Reported separately, because "we redacted a card" and "we
    # redacted something shaped like a card" are different claims and only one of them is certain.
    unverified_cards: int = 0

    @property
    def total(self) -> int:
        return sum(self.counts.values())

    def as_record(self) -> dict:
        return {"redactions": dict(sorted(self.counts.items())),
                "redaction_count": self.total,
                "unverified_card_shapes": self.unverified_cards}


def redact(text: str) -> tuple[str, Redaction]:
    """Replace every pattern match, and report what was replaced without keeping it.

    The replacement carries the label so a reader of the redacted text can see what was there, and so a person
    debugging a refusal can tell a redacted email from a redacted card. It does not carry the length or any part
    of the value, because either would narrow a guess.
    """
    found = Redaction()

    def replace(match: re.Match) -> str:
        label = match.lastgroup or "unknown"
        value = match.group(0)
        if label == "card":
            digits = re.sub(r"\D", "", value)
            if not _luhn(digits):
                # Redacted either way, and labelled differently. This is the false-positive trade made
                # deliberately rather than hidden: a 16-digit invoice number is not a card and redacting it
                # costs a legitimate question its reference, while a card number reaching a frontier model is a
                # payment incident. So shape decides whether to redact and the check digit decides only what the
                # reader is told, because a card whose digits OCR damaged fails the check and is still a card.
                found.unverified_cards += 1
                found.counts[label] = found.counts.get(label, 0) + 1
                return "[REDACTED-LONG_NUMBER]"
        found.counts[label] = found.counts.get(label, 0) + 1
        return f"[REDACTED-{label.upper()}]"

    return COMBINED.sub(replace, text), found


def redact_request(request: dict) -> tuple[dict, Redaction]:
    """Redact every string a request will carry, and return what to send.

    Every string, not the ones somebody remembered. The first version of a function like this typically
    redacts the user's question and leaves the retrieved passages, which are the part most likely to hold a
    client's identifiers: the question is typed by a person who knows what not to type, and the passages are
    whatever the corpus contained. Walking the whole structure means a field added later is covered without
    anyone remembering to cover it.

    Keys are left alone. A key name is not a value, and redacting one would change the shape of a request the
    provider has to parse.
    """
    total = Redaction()

    def walk(value):
        if isinstance(value, str):
            cleaned, found = redact(value)
            for label, count in found.counts.items():
                total.counts[label] = total.counts.get(label, 0) + count
            total.unverified_cards += found.unverified_cards
            return cleaned
        if isinstance(value, dict):
            return {key: walk(item) for key, item in value.items()}
        if isinstance(value, (list, tuple)):
            return [walk(item) for item in value]
        return value

    return walk(request), total


def sha256_of(text: str) -> str:
    """The hash the record keeps.

    Of the redacted text, deliberately. The audit column says it holds the SHA-256 of the prompt as sent, and a
    hash of the original would be a second copy of the thing the redaction just removed: it cannot be read, but
    it can be compared, which is enough to confirm a guess about a Social Security number.
    """
    return hashlib.sha256(text.encode("utf-8")).hexdigest()
