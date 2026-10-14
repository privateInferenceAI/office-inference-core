"""What happens when the redactor itself breaks.

**This exists because of a specific reported failure.** LiteLLM issue 35257 records a guardrail that did not
scan a stream and logged `guardrail_status: "success"` anyway, so the audit trail claimed the output was
checked when it was not. Research on 2026-10-14 also found fail-open branches in three streaming paths, where an
exception emitted the buffer unmasked.

Our property is the opposite: a redactor that cannot run must emit nothing raw, tell the client, and leave a
failure in the record rather than a success. It held before this test existed, by accident, because the exception
propagated out of the generator. It is asserted now because an accident is not a control.
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "services" / "api"))

import streaming  # noqa: E402


def test_the_api_treats_a_redactor_failure_as_a_stop_not_a_pass():
    """The call that produces client text is wrapped, and the wrapped failure emits no text.

    The assertion is structural because the alternative is a deployment. What it pins is the shape: no delta is
    yielded on the way out, an error event is, and the record says the stage was redaction.
    """
    source = (ROOT / "services" / "api" / "app.py").read_text()
    marker = 'except Exception as exc:  # noqa: BLE001 - a redactor that cannot run must not pass text'
    assert marker in source, 'the redactor call is not wrapped'
    block = source[source.index(marker):]
    block = block[:block.index('                return')]
    assert 'yield frame("delta"' not in block, 'a failure must not emit text on its way out'
    assert 'yield frame("error"' in block, 'it must emit an error event instead'
    assert 'redaction_failed' in block, 'and the client must be able to tell what happened'
    assert '"stage": "redaction"' in block, 'and the record must say which stage failed'


def test_held_text_does_not_escape_before_it_is_settled():
    """The property the holdback exists for, stated directly."""
    redactor = streaming.StreamRedactor()
    cut = 'the reference is 219-09'
    first = redactor.feed(cut)
    assert '219' not in first, 'a prefix of a held pattern reached the client'
    assert len(first) <= len(cut) - 1, 'only settled text is emitted'


def test_the_office_build_gets_the_same_protection():
    """**The control has to live where both legs pass through.**

    The office build runs a local model with no gateway guardrail, so anything implemented at the gateway is
    absent there. The holdback sits in the API, which both builds use, and this asserts it stays there rather
    than migrating into a gateway callback in a later refactor.
    """
    source = (ROOT / "services" / "api" / "app.py").read_text()
    assert 'streaming.StreamRedactor()' in source, 'the redaction belongs to the API, not the gateway'


def test_a_marker_is_never_rescanned_as_input():
    """A marker that matched its own pattern would shift the text and leak the tail."""
    redactor = streaming.StreamRedactor()
    text = '219-09-9999 and later 078-05-1120 both appear here, well apart from each other. '
    out = redactor.feed(text) + redactor.finish()
    assert '219-09-9999' not in out
    assert '078-05-1120' not in out
    assert '[REDACTED' in out

