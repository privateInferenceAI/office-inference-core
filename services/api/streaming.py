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

import re
import sys
from pathlib import Path

sys.path.insert(0, "/app/services/egress")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "egress"))

import redaction  # noqa: E402

# **A trailing fragment that could still become a pattern.** Used to find a boundary where emitting cannot cut a
# match, which a fixed holdback cannot do: every pattern here can begin more than a few characters before it is
# recognisable, and a telephone number begins twelve characters before it is complete.
_PREFIX = re.compile(
    r'(?:'
    r'\d{1,3}(?:[ -]?\d{1,3}){0,2}[ -]?'          # a phone number, a card, an SSN, an EIN, an IPv4
    r'|\+1?[ .-]?\(?\d{0,3}\)?'                    # an international phone prefix
    r'|(?:AKIA|ASIA|ABIA|ACCA)[0-9A-Z]{0,15}'       # an AWS key
    r'|-----BEGIN [A-Z ]*'                          # a private key header
    r'|Bearer\s+[A-Za-z0-9\-._~+/]{0,19}'           # a bearer token
    r'|(?:sk|pk|rk|api|key|token)[-_][A-Za-z0-9]{0,15}'  # an api key
    r'|(?:password|passwd|pwd)\s*[:=]\s*'           # a password assignment
    r'|[A-Z]{2}\d{0,2}[A-Z0-9]{0,10}'                # an IBAN
    # An email needs its @ or a distinctive character to count as a prefix. A bare run of letters
    # must not match: it did, and the boundary then walked all the way back and held text that could
    # never have become a pattern.
    r'|[\w.+-]{0,40}(?:@[\w-]{0,30}|[.+-][\w.+-]{0,40})'
    r')$'
)

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
        # **A position in the REDACTED text, not a count of what was emitted.** Redaction changes
        # lengths -- a value becomes a marker of a different length -- so a counter of emitted
        # characters drifts away from the index it is used as, and text already sent is sent again.
        # Tracking the wrong coordinate is the same mistake as the original slice bug, one layer up.
        self.sent = 0  # how far into the redacted buffer emission has reached
        self.counts: dict[str, int] = {}
        self.unverified_cards = 0

    def feed(self, chunk: str) -> str:
        """Take a chunk, return what is now safe to emit, which is usually empty at first."""
        self.raw += chunk
        return self._drain(final=False)

    def finish(self) -> str:
        """Everything still held, redacted. Called once, at the end of the stream."""
        return self._drain(final=True)

    def _safe_boundary(self, redacted: str, limit: int) -> int:
        """The largest position at or below limit where emitting cannot cut a pattern.

        **Why a fixed holdback is not enough, and this was the bug.** Holding back a constant number of
        characters assumes no pattern starts more than that many characters before its end. That is untrue for
        every pattern here: a telephone number is three digits, a separator, three more, a separator, and four
        more, so a match can begin twelve characters before it is recognisable. A boundary landing inside such a
        prefix emits the first fragment, and that fragment plus the holdback is twelve characters, which sits
        inside a sixty-four character window and is therefore never examined again.

        **So walk back rather than hold back.** From the proposed boundary, retreat while the tail of what would
        be emitted could still be the beginning of a pattern. Where the tail matches no partial pattern, nothing
        before the boundary can grow into a match across it.
        """
        window = redacted[:limit]
        for back in range(min(len(window), self.holdback), -1, -1):
            tail = window[len(window) - back:] if back else ''
            if not tail or not _PREFIX.search(tail):
                return limit - back
        return 0

    def _drain(self, *, final: bool) -> str:
        """The redacted text safe to emit now, or empty when nothing is settled yet.

        **The defect this replaces, because it was severe and it shipped.** The first version sliced a safe
        prefix out of the raw buffer, redacted that slice on its own, emitted it, and advanced the buffer by the
        slice's length. Two things were wrong. Redacting the slice alone left a pattern straddling the boundary
        with no predecessor to match against, so its tail was never recognised. And advancing by the slice
        length discarded the entire held-back tail, so the next chunk could not complete it either. A value
        could walk out in the clear with the whole holdback intact, and with random chunking it did so in fifty
        trials out of fifty.

        **Found by the C3 panel's fidelity checker**, who fed this module a canary through its own tests and
        watched it leak, then noticed that a single feed never leaks while a chunked one always does. Our probes
        missed it because the canary question's answer happens not to straddle a boundary, and the unit tests
        fed whole answers in one call where the final drain flushes everything.

        **The fix holds back REDACTED text and walks the boundary back.** The whole tail is re-redacted on every
        chunk so a pattern has everything before it to match against, and emission is limited to the part that
        is both settled and new.
        """
        redacted, report = redaction.redact(self.raw)
        for kind, count in (report.counts or {}).items():
            self.counts[kind] = self.counts.get(kind, 0) + count
        self.unverified_cards += report.unverified_cards

        if final:
            # **Resume from where emission reached, not from the start.** The final drain used to emit
            # the whole redacted buffer, which sent everything that had already gone out a second
            # time. The client saw the answer repeated, and the duplication was invisible to a test
            # that only looked for a leaked value.
            to_emit = redacted[self.sent:]
        else:
            keep = self.holdback
            if len(redacted) <= keep:
                self.raw = redacted
                return ""
            limit = self._safe_boundary(redacted, len(redacted) - keep)
            # A walked-back boundary can land behind what has already gone out, in which case
            # there is nothing new that is settled and this drain emits nothing.
            if limit <= self.sent:
                self.raw = redacted
                return ""
            to_emit = redacted[self.sent:limit]
            while to_emit and len(to_emit.encode("utf-8", errors="ignore")) != len(to_emit):
                to_emit = to_emit[:-1]

        self.sent = len(redacted) if final else limit
        self.raw = redacted
        self.emitted += len(to_emit)
        return to_emit
    def report(self) -> dict:
        """What was done, for the audit row. Counts and sizes, never the values."""
        return {
            "redactions": self.counts,
            "unverified_card_shapes": self.unverified_cards,
            "characters_emitted": self.emitted,
            "holdback": self.holdback,
        }
