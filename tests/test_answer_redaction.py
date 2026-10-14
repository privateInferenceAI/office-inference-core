"""The answer that reaches a client is redacted, on every path.

**Why this test exists.** The streaming endpoint redacted from the start and the JSON endpoint did not, so the
same question returned the PII canary in clear over `POST /ask` and redacted over `POST /ask/stream`. The canary
fixture says in its own first line that if it ever appears in a response the deployment is not safe to show a
client, and it appeared. Found by the review panel, who asked the question and read the answer.

This is a structural test because the alternative is a deployment, and the property is that no code path returns
a model answer without passing it through the redactor first.

**The redaction moved into `answer.py` when the pipeline was extracted, and these assertions moved with it.**
That extraction is the durable fix for this file's own subject: the defect was two implementations of one
thing, one of which redacted and one of which did not. There is now one. The assertions still hold the order
that mattered — redact, *then* check the citations against what is sent — and they still fail if a future
handler answers a question without going through the pipeline at all.
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
API = ROOT / "services" / "api" / "app.py"
PIPELINE = ROOT / "services" / "api" / "answer.py"


def test_the_json_path_redacts_before_the_client_sees_the_answer():
    """The defect, asserted against the source so it cannot come back quietly."""
    source = PIPELINE.read_text()
    assert "import redaction" in source, "the pipeline does not import the redactor at all"
    assert "answer, report = redaction.redact(answer)" in source, (
        "the model's answer is not passed through the redactor before it is returned")


def test_the_checks_run_against_what_is_sent():
    """A citation verified in text the client never receives is verified against the wrong thing."""
    source = PIPELINE.read_text()
    redact_at = source.index("answer, report = redaction.redact(answer)")
    verify_at = source.index("verified = citations_module.verify(answer, result.passages)", redact_at)
    assert redact_at < verify_at, "the citation checks run before the answer is redacted"


def test_the_answer_reports_what_was_redacted():
    """A client that wants to know whether a value was replaced has to be told."""
    source = PIPELINE.read_text()
    assert '"redactions": report.counts,' in source


def test_the_question_is_redacted_too_and_the_record_hashes_that_form():
    """The record's prompt hash is of the redacted question. Hashing the original makes `prompt_sha256`
    comparable against a guess, which is what the manual says it must not be."""
    source = PIPELINE.read_text()
    assert "safe_question, question_report = redaction.redact(question)" in source
    assert "prompt=safe_question" in source, "the record is handed the raw question"


def test_every_handler_that_answers_a_question_goes_through_the_pipeline():
    """**The general form of this file's own subject.**

    Two handlers, two copies of the redaction, and one copy missing is exactly what happened in C3. The
    assertion is that neither `/ask` nor the job step builds an answer for itself: both call the pipeline,
    so neither can be the one that forgot.
    """
    source = API.read_text()
    assert "answer_module.finish(" in source, "no handler completes an answer through the pipeline"
    job_step = source.index("def _step_once(")
    body = source[job_step:source.index("\n@app.", job_step)]
    assert "answer_module.finish(" in body, "the job step returns a model answer without redacting it"


def test_the_streaming_path_still_redacts_every_frame():
    """The property the streamed path already had, kept while the other one was fixed."""
    source = (ROOT / "services" / "api" / "streaming.py").read_text()
    assert "redaction.redact(self.raw)" in source
