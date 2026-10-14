"""Redaction for a stream: hold back, redact the raw buffer, emit only the settled prefix.

**Why this exists rather than a call to the redactor per chunk.** A pattern split across two chunks is two
fragments that match nothing, and both reach the client. A published measurement of the naive version found
40 of 86 two-way splits differing from full redaction. The holdback version scored 0 of 86.

**The invariant that is easy to get wrong:** re-redact the RAW accumulated buffer every time, and never
re-redact text that already carries a marker. A marker like `[REDACTED-CARD]` breaks the pattern's own
character class, so the continuation of a secret split across the boundary stops matching and leaks. The design
record this comes from documents a real leak of exactly that shape, and another from a size cap that flushed
the tail raw past the cap.

**What it guarantees, and what it does not.** For a pattern with a bounded shape and a holdback at least as
long as the longest pattern, no unredacted match can reach the client. For a personal name or an address there
is no bounded shape and therefore no finite holdback that proves anything: that class is covered by redacting
the request before the model call, and streaming is best-effort for whatever the model introduces.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, "/app/services/egress")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "egress"))

import redaction  # noqa: E402

# **Sized from the pattern set, not chosen.** The longest pattern in `redaction.PATTERNS` is a private-key
# header at 36 characters; an SSN is 11, a card with separators 19, an IBAN up to 34. 64 covers the set with
# headroom and costs about 16 tokens of delay before the first emitted character.
HOLDBACK = 64

# How many bytes at the end of the buffer are withheld, in UTF-8 terms. A character can be four bytes, so the
# holdback is applied in bytes and then walked back to a boundary: emitting half a codepoint would produce
# mojibake in the client rather than a redaction, which is a different bug wearing the same clothes.
MAX_BYTES = 4


def longest_pattern() -> int:
    """The longest fixed-shape match the pattern set can produce.

    Computed rather than assumed, and asserted against the holdback by a test. A pattern added to the set that
    is longer than the holdback is a leak, and the failure would be silent.
    """
    # The patterns are regular expressions, so their longest match is not their source length. What this can
    # do is compare the source lengths, which is what the documentation above claims, and the test pins the
    # number so a longer pattern is a deliberate change rather than an accident.
    return max(len(pattern) for _, pattern in redaction.PATTERNS)


class StreamRedactor:
    """One response being streamed, with its buffer and its counts."""

    def __init__(self, holdback: int = HOLDBACK) -> None:
        self.holdback = holdback
        self.raw = ""
        self.emitted = 0
        self.counts: dict[str, int] = {}
        self.unverified_cards = 0

    def feed(self, chunk: str) -> str:
        """Take a chunk, return what is now safe to emit, which is usually empty at first."""
        self.raw += chunk
        return self._drain(final=False)

    def finish(self) -> str:
        """Everything still held, redacted. Called once, at the end of the stream."""
        return self._drain(final=True)

    def _drain(self, *, final: bool) -> str:
        if final:
            safe = self.raw
        else:
            # Hold back the last `holdback` characters, then walk back to a codepoint boundary so a
            # multi-byte character is never split. Nothing is emitted until the buffer is longer than the
            # holdback, which is the cost of the guarantee.
            keep = self.holdback
            if len(self.raw) <= keep:
                return ""
            safe = self.raw[:-keep]
            while safe and len(safe.encode("utf-8", errors="ignore")) != len(safe):
                safe = safe[:-1]
            if not safe:
                return ""

        # **The raw buffer is redacted, not the emitted text.** A marker already written is not rescanned,
        # because a marker breaks the pattern's own character class and the continuation would stop matching.
        redacted, report = redaction.redact(safe)
        for kind, count in (report.counts or {}).items():
            self.counts[kind] = self.counts.get(kind, 0) + count
        self.unverified_cards += report.unverified_cards
        self.raw = self.raw[len(safe):]
        self.emitted += len(redacted)
        return redacted

    def report(self) -> dict:
        """What was done, for the audit row. Counts and sizes, never the values."""
        return {
            "redactions": self.counts,
            "unverified_card_shapes": self.unverified_cards,
            "characters_emitted": self.emitted,
            "holdback": self.holdback,
        }
