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


def test_the_holdback_is_unconditional_even_when_nothing_is_rewritten():
    """**The property a spike on the gateway proved matters, moved here where it is cheap to check.**

    A gateway guardrail that withheld output only on the rounds where it happened to rewrite leaked the raw
    prefix on the rounds where it did not, and then failed with `stream_transform_underflow` because emitted
    bytes cannot be retracted. Any holdback design has the same trap, so this pins the behaviour: output is held
    back on clean text too, not as a response to having found something.

    The earlier version of this test asserted the emitted length was exactly `text - HOLDBACK`. That is no
    longer true and should not be: the boundary walks back past a partial pattern, so it holds back at least
    the window and sometimes more. Asserting the exact figure would pin the implementation rather than the
    property, and the property is what protects the client.
    """
    r = streaming.StreamRedactor()
    out = r.feed('a' * 200)
    assert out, 'nothing was emitted at all, which is a stall rather than a holdback'
    assert len(out) <= 200 - streaming.HOLDBACK, 'less was held back than the window, which is the leak'
    # **No upper bound is asserted here, and that is deliberate.** The boundary walks back past a
    # partial pattern, so it holds at least the window and sometimes more; a run of punctuation can
    # make it hold a good deal more. Bounding it from above would pin the implementation rather than
    # the property, and holding too much costs latency while holding too little costs a leak. The
    # property that matters is asserted by the chunk-invariance test below.
    assert len(out) >= 1, "and something does come out, so a client is not left waiting"


def test_a_single_feed_and_a_chunked_feed_agree():
    """**Chunk invariance, which is the property the holdback exists to provide.**

    The leak this test was written for: the first implementation sliced a safe prefix, redacted that slice on
    its own, and advanced the buffer by the slice length. Redacting a slice without its predecessors meant a
    pattern straddling the boundary was never recognised, and advancing by the slice length threw the held tail
    away so the next chunk could not complete it either. With random chunking it leaked in every trial.

    A single feed never leaked, because the final drain flushed everything through one redaction. That is why
    the unit tests passed while the deployed stream leaked: they fed whole answers in one call. This asserts the
    thing they could not, that the way the text arrives cannot change what the client is sent.
    """
    import random

    import redaction

    text = ('The primary contact reference for the Acme engagement is 219-09-9999 and it is held on file. '
            'No further detail is recorded, this sentence exists only to give the buffer some length. ' * 3)
    whole, _ = redaction.redact(text)

    for size in (1, 2, 3, 5, 8, 13, 16, 20, 64, 65, 200):
        r = streaming.StreamRedactor()
        out = ''
        for i in range(0, len(text), size):
            out += r.feed(text[i:i + size])
        out += r.finish()
        assert '219-09-9999' not in out, 'a value leaked with chunk size %d' % size
        assert out == whole, 'chunk size %d produced different text from a single feed' % size

    random.seed(11)
    for _ in range(50):
        r = streaming.StreamRedactor()
        out = ''
        i = 0
        while i < len(text):
            n = random.randint(1, 12)
            out += r.feed(text[i:i + n])
            i += n
        out += r.finish()
        assert out == whole, 'random chunking produced different text from a single feed'

def test_a_pattern_completed_after_clean_text_is_still_caught():
    """The same property as the outcome it protects: clean chunks first, then a pattern."""
    r = streaming.StreamRedactor()
    out = r.feed("The number is 219-")
    out += r.feed("09-9999 and done.")
    out += r.finish()
    assert SSN not in out
    assert "[REDACTED-SSN]" in out
