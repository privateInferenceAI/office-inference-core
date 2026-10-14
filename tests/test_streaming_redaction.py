"""The stream redactor: the guarantee, and the boundary case that makes it necessary."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "services" / "api"))
sys.path.insert(0, str(ROOT / "services" / "egress"))

import redaction  # noqa: E402
import streaming  # noqa: E402

# A real SSN shape, and the two other patterns the corpus sentence uses.
SSN = "219-09-9999"
CARD = "4111 1111 1111 1111"


def run(chunks):
    """Feed chunks through the redactor the way a stream would, and return everything emitted."""
    r = streaming.StreamRedactor()
    out = ""
    for chunk in chunks:
        out += r.feed(chunk)
    out += r.finish()
    return out, r


def test_a_pattern_inside_one_chunk_is_redacted():
    out, r = run(["The number is ", SSN, " and that is all."])
    assert SSN not in out
    assert "[REDACTED-SSN]" in out
    assert r.counts.get("ssn", 0) >= 1


def test_a_pattern_split_across_two_chunks_is_redacted():
    """**The case the whole design exists for.** Per-delta redaction leaks this; a holdback does not."""
    out, _ = run(["The number is 219-09-", "9999 and that is all."])
    assert SSN not in out, "a pattern split across a chunk boundary reached the stream"
    assert "[REDACTED-SSN]" in out


def test_a_pattern_split_into_single_characters_is_redacted():
    """The worst case: every character in its own frame, which is what a slow model really produces."""
    text = "Reference " + SSN + " on file."
    out, _ = run(list(text))
    assert SSN not in out
    assert "[REDACTED-SSN]" in out


def test_a_card_split_across_chunks_is_redacted():
    out, _ = run(["Card ", "4111 1111", " 1111 1111", " on file."])
    assert CARD not in out
    assert "[REDACTED-CARD]" in out


def test_a_marker_is_not_rescanned_into_a_leak():
    """The invariant: the raw buffer is redacted, never text that already carries a marker.

    A marker breaks the character class of the pattern that produced it, so re-scanning emitted text is how a
    boundary-split secret's continuation stops matching.
    """
    out, _ = run(["Key ", "sk-abcdefghijklmnop", "qrstuvwx and then text."])
    assert "sk-abcdefghijklmnopqrstuvwx" not in out


def test_nothing_is_emitted_before_the_holdback_fills():
    """The cost of the guarantee, stated as a test so it cannot surprise anybody later."""
    r = streaming.StreamRedactor()
    assert r.feed("short") == ""
    assert r.feed("er still") == ""


def test_the_tail_is_flushed_redacted_at_the_end():
    """A short answer never fills the holdback, so finish() is what emits it, and it must still be redacted."""
    out, _ = run(["The number is " + SSN])
    assert SSN not in out
    assert "[REDACTED-SSN]" in out


def test_a_multibyte_character_is_never_split():
    """Emitting half a codepoint would be mojibake, which is a different bug in the same clothes."""
    r = streaming.StreamRedactor()
    out = r.feed("café " * 40)
    out += r.finish()
    assert "\ufffd" not in out
    assert out.encode("utf-8").decode("utf-8") == out


def test_the_holdback_covers_every_pattern():
    """**The test that makes the guarantee a property rather than a claim.**

    A pattern longer than the holdback is a pattern that can straddle it, and the failure would be silent. This
    does not prove the longest MATCH is under the holdback, because a regular expression's longest match is not
    its source length; it pins the source lengths, so a longer pattern is a deliberate change that fails here.
    """
    assert streaming.longest_pattern() <= streaming.HOLDBACK


def test_the_report_counts_without_carrying_values():
    """The audit row gets counts. A report carrying the value would be a second copy of the PII."""
    _, r = run(["The number is " + SSN + " and the card " + CARD])
    report = r.report()
    assert report["redactions"].get("ssn", 0) >= 1
    assert report["redactions"].get("card", 0) >= 1
    assert SSN not in str(report)
    assert CARD not in str(report)
    assert report["holdback"] == streaming.HOLDBACK


def test_a_clean_stream_passes_through_unchanged():
    """The complementary case. A redactor that mangled ordinary text would be its own outage."""
    text = "The mileage rate is 0.67 per mile for travel in an employee's own vehicle. " * 4
    out, r = run([text[i:i + 20] for i in range(0, len(text), 20)])
    assert out == text
    assert r.counts == {}
