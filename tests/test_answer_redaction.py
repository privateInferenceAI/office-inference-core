"""The answer that reaches a client is redacted, on every path.

**Why this test exists.** The streaming endpoint redacted from the start and the JSON endpoint did not, so the
same question returned the PII canary in clear over `POST /ask` and redacted over `POST /ask/stream`. The canary
fixture says in its own first line that if it ever appears in a response the deployment is not safe to show a
client, and it appeared. Found by the review panel, who asked the question and read the answer.

This is a structural test because the alternative is a deployment, and the property is that no code path returns
a model answer without passing it through the redactor first.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
API = ROOT / "services" / "api" / "app.py"


def test_the_json_path_redacts_before_the_client_sees_the_answer():
    """The defect, asserted against the source so it cannot come back quietly."""
    source = API.read_text()
    assert "import redaction" in source, "the API does not import the redactor at all"
    assert "answer, redaction_report = redaction.redact(answer)" in source, (
        "the model's answer is not passed through the redactor before it is returned")


def test_the_checks_run_against_what_is_sent():
    """A citation verified in text the client never receives is verified against the wrong thing."""
    source = API.read_text()
    redact_at = source.index("answer, redaction_report = redaction.redact(answer)")
    verify_at = source.index("verified = citations.verify(answer, result.passages)", redact_at)
    assert redact_at < verify_at, "the citation checks run before the answer is redacted"


def test_the_answer_reports_what_was_redacted():
    """A client that wants to know whether a value was replaced has to be told."""
    source = API.read_text()
    assert '"redactions": redaction_report.counts,' in source


def test_the_streaming_path_still_redacts_every_frame():
    """The property the streamed path already had, kept while the other one was fixed."""
    source = (ROOT / "services" / "api" / "streaming.py").read_text()
    assert "redaction.redact(self.raw)" in source

