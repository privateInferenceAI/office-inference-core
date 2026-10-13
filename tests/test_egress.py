"""Redaction before anything leaves, and the gate that decides whether it leaves at all.

SEC-REDACT-001: PII is redacted before any frontier-bound request leaves, and the redaction is recorded.

The tests are shaped around the ways this could succeed while doing nothing, because that is the failure this
codebase has hit repeatedly: a check that reports success and changes nothing is worse than one that fails,
because nobody looks at it again. The three that matter most are that redaction covers the retrieved passages
and not only the question, that the request returned is the one to send, and that a refusal sends nothing.

Run: python3 -m pytest tests/
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "services" / "egress"))

import egress  # noqa: E402
import lane  # noqa: E402
import redaction  # noqa: E402


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    """No permissions and redaction on, which is what an unconfigured deployment looks like."""
    monkeypatch.delenv("FRONTIER_CLASSES", raising=False)
    monkeypatch.delenv("EGRESS_REDACTION", raising=False)


def permitted(*classes):
    return set(classes)


# --------------------------------------------------------------------------- the gate

def test_an_unconfigured_deployment_sends_nothing():
    """The direction the whole gate depends on. An unset permission set must not become a permission, or a
    misspelled variable becomes a system that sends a client's documents to a provider."""
    decision = lane.check("internal")
    assert decision.allowed is False
    assert "unconfigured deployment" in decision.reason


def test_a_permitted_class_is_allowed_and_an_unpermitted_one_is_not():
    assert lane.check("public", permitted=permitted("public", "internal")).allowed is True
    refused = lane.check("confidential", permitted=permitted("public", "internal"))
    assert refused.allowed is False
    assert "confidential is not permitted" in refused.reason
    # The reason names what IS permitted, because a refusal a person cannot act on is one they route around.
    assert "internal" in refused.reason and "public" in refused.reason


def test_an_unknown_class_is_refused_rather_than_treated_as_its_own_thing():
    """A class the system has never heard of cannot be shown to be permitted, and a gate that allows unknown
    values is a gate that allows anything with a typo in it."""
    decision = lane.check("secret")
    assert decision.allowed is False
    assert "not one of the four data classes" in decision.reason


def test_a_request_with_no_class_is_refused():
    """An unclassified request is the shape a caller produces when it forgot to set the field, and allowing it
    would make the field decorative."""
    decision = lane.check("")
    assert decision.allowed is False
    assert "no data class" in decision.reason


def test_a_refused_request_returns_no_payload_at_all():
    """Not a payload with a flag beside it. A caller that has both will eventually send the wrong one, and the
    mistake is invisible because both are valid requests."""
    prepared = egress.prepare({"question": "anything"}, "regulated", permitted=permitted("public"))
    assert prepared.allowed is False
    assert prepared.payload is None
    assert prepared.prompt_sha256 is None, "nothing was sent, so there is nothing to hash"
    assert prepared.as_record()["outcome"] == "denied"


def test_the_gate_runs_before_the_redaction():
    """Redacting a regulated document and sending the remainder answers a different question than the one the
    client decided. The refusal has to be about the document, not about the parts a pattern happened to catch."""
    prepared = egress.prepare({"question": "SSN 123-45-6789"}, "regulated", permitted=permitted("public"))
    assert prepared.allowed is False
    assert prepared.redaction.total == 0, "nothing was redacted, because nothing was going to be sent"


# --------------------------------------------------------------------------- what gets redacted

def test_the_passages_are_redacted_and_not_only_the_question():
    """The mistake this is shaped to prevent, and the one a redactor is most likely to make.

    The question is typed by a person who knows what not to type. The passages are whatever the corpus
    contained, and they are the part most likely to carry a client's identifiers. A redactor that covers the
    question and skips the passages reports a successful redaction on every request and leaves the actual risk
    untouched.
    """
    prepared = egress.prepare({
        "question": "What does the file say?",
        "passages": [{"text": "Account holder SSN 123-45-6789, card 4111 1111 1111 1111."}],
    }, "internal", permitted=permitted("internal"))
    assert prepared.allowed
    assert "123-45-6789" not in prepared.payload["passages"][0]["text"]
    assert "4111" not in prepared.payload["passages"][0]["text"]
    assert prepared.redaction.counts == {"ssn": 1, "card": 1}


def test_a_field_added_later_is_covered_without_being_named():
    """The walk covers every string, so a field nobody thought of when this was written is redacted anyway. A
    redactor with a list of fields to check is one that silently stops covering the request the first time
    somebody adds a field."""
    prepared = egress.prepare({
        "question": "q",
        "some_field_added_in_2027": {"nested": ["call 860-555-1234"]},
    }, "internal", permitted=permitted("internal"))
    assert "860-555-1234" not in str(prepared.payload)


def test_a_key_name_is_not_redacted():
    """A key is not a value. Redacting one changes the shape of a request the provider has to parse, and the
    request would be rejected for a reason nobody would connect to redaction."""
    prepared = egress.prepare({"ssn_field": "none"}, "internal", permitted=permitted("internal"))
    assert "ssn_field" in prepared.payload


def test_one_pass_cannot_re_match_its_own_output():
    """The bug the loop version had. Redacting pattern by pattern re-scans the text it just wrote, so a value
    replaced with a label can be picked up by a later pattern and replaced again, producing nested labels and a
    count that overstates what was found."""
    text = "SSN 123-45-6789 and email a@b.co"
    cleaned, found = redaction.redact(text)
    assert cleaned == "SSN [REDACTED-SSN] and email [REDACTED-EMAIL]"
    assert found.total == 2
    # Redacting the already-redacted text changes nothing, which is the property the single pass gives.
    again, more = redaction.redact(cleaned)
    assert again == cleaned and more.total == 0


def test_an_opening_bracket_is_not_left_behind():
    """`(860) 555-1234` matched without its bracket, because a word boundary cannot exist before an opening
    parenthesis. The result was `([REDACTED-PHONE]` in text that is supposed to read cleanly."""
    cleaned, _ = redaction.redact("Call (860) 555-1234 now.")
    assert cleaned == "Call [REDACTED-PHONE] now."


def test_a_decimal_and_a_thousands_separator_are_not_identifiers():
    """A fee schedule is full of figures and redacting them would break the product. `145.00`, `1,500.00` and a
    six-digit amount are not identifiers, and a pattern that catches them makes every answer about money
    unanswerable."""
    for text in ["The rate is 145.00 per hour.", "The total is 1,500.00.", "Code 418750 applies.",
                 "0.67 per mile and 32.50 per unit."]:
        cleaned, found = redaction.redact(text)
        assert cleaned == text, f"redacted a figure: {cleaned!r}"
        assert found.total == 0


# --------------------------------------------------------------------------- the trade this makes

def test_a_card_is_redacted_and_a_card_shaped_number_is_redacted_differently():
    """The false-positive trade, made deliberately rather than hidden.

    A 16-digit invoice number is not a card and redacting it costs a legitimate question its reference. A card
    number reaching a frontier model is a payment incident. So shape decides whether to redact, and the check
    digit decides only what the reader is told, because a card whose digits OCR damaged fails the check and is
    still a card.
    """
    valid, found = redaction.redact("Card 4111 1111 1111 1111 was declined.")
    assert "[REDACTED-CARD]" in valid and found.unverified_cards == 0

    shaped, found = redaction.redact("Invoice 1234567890123456 is unpaid.")
    assert "[REDACTED-LONG_NUMBER]" in shaped, "still redacted, because shape decides"
    assert found.unverified_cards == 1, "and reported separately, because it may not be a card"
    assert shaped.count("1234567890123456") == 0


def test_a_credential_is_redacted_because_it_is_an_incident_rather_than_a_privacy_one():
    """A key that reaches a provider's logs is not recoverable by policy. Rotation is the only remedy, so this
    is the one class where a false positive is cheap at any price."""
    for text, label in [
        ("key AKIAIOSFODNN7EXAMPLE here", "AWS_KEY"),
        ("Authorization: Bearer abcdefghijklmnopqrstuvwxyz012345", "BEARER"),
        ("password: hunter2", "PASSWORD"),
        ("-----BEGIN RSA PRIVATE KEY-----", "PRIVATE_KEY"),
    ]:
        cleaned, found = redaction.redact(text)
        assert f"[REDACTED-{label}]" in cleaned, f"{label} not redacted from {text!r}"
        assert found.total >= 1


def test_a_name_is_not_redacted_and_the_bound_is_real():
    """The honest limit, as a test so it cannot be quietly assumed away. This is pattern matching: it catches
    identifiers that have a shape and it does not catch a person's name. Presenting it as a guarantee is how a
    client ends up sending something they would not have."""
    cleaned, found = redaction.redact("Jane Doe owes 145.00 and lives in Colebrook.")
    assert "Jane Doe" in cleaned
    assert found.total == 0


# --------------------------------------------------------------------------- the record

def test_the_hash_is_of_what_was_sent_and_not_of_what_was_typed():
    """A hash of the original is a second copy of the thing the redaction removed. It cannot be read, but it can
    be compared, which is enough to confirm a guess about a Social Security number. The audit column already
    says it holds the hash of the prompt as sent."""
    typed = {"question": "My SSN is 123-45-6789"}
    prepared = egress.prepare(typed, "internal", permitted=permitted("internal"))
    assert prepared.prompt_sha256 == redaction.sha256_of(
        '{"question":"My SSN is [REDACTED-SSN]"}')
    assert prepared.prompt_sha256 != redaction.sha256_of('{"question":"My SSN is 123-45-6789"}')


def test_the_record_reports_the_data_class_and_what_was_removed():
    """The requirement's second half: the audit shows the data class. It shows the kinds and counts and never
    the values, because an audit record that keeps what it redacted has redacted nothing."""
    prepared = egress.prepare({"question": "SSN 123-45-6789, email a@b.co"},
                              "confidential", permitted=permitted("confidential"))
    record = prepared.as_record()
    assert record["data_class"] == "confidential"
    assert record["lane"] == "frontier"
    assert record["egress_allowed"] is True
    assert record["redaction_count"] == 2
    assert record["redactions"] == {"email": 1, "ssn": 1}
    assert "123-45-6789" not in str(record), "the record must not keep what it removed"


def test_the_caller_does_not_have_to_serialise_the_payload_again():
    """The probe serialised the payload a second time with different separators and recorded a hash that did not
    match the one this module computed. The record looked correct and proved nothing about what was sent, which
    is worse than an error because nothing failed.

    So the string whose hash is recorded is handed out. A caller that passes it gets the matching hash by
    construction rather than by remembering which separators were used.
    """
    prepared = egress.prepare({"question": "q", "model": "m"}, "internal",
                              permitted=permitted("internal"))
    assert prepared.sent_text is not None
    assert redaction.sha256_of(prepared.sent_text) == prepared.prompt_sha256
    import json
    assert json.loads(prepared.sent_text) == prepared.payload


def test_the_same_request_hashes_the_same_way_twice():
    """Key order in a dict is insertion order, so a request assembled in a different order would hash
    differently and the record could not be used to confirm that two calls sent the same thing."""
    a = egress.prepare({"question": "q", "model": "m"}, "internal", permitted=permitted("internal"))
    b = egress.prepare({"model": "m", "question": "q"}, "internal", permitted=permitted("internal"))
    assert a.prompt_sha256 == b.prompt_sha256


def test_redaction_can_be_turned_off_and_is_on_by_default():
    """A client's decision to make, and one they should have to make explicitly. The default matters more than
    the setting, because forgetting to configure it must not become forgetting to redact."""
    assert redaction.redact  # the default path
    off = egress.prepare({"question": "SSN 123-45-6789"}, "internal",
                         permitted=permitted("internal"), redact_enabled=False)
    assert "123-45-6789" in off.payload["question"], "turned off means turned off"
    assert off.redaction.total == 0

    on = egress.prepare({"question": "SSN 123-45-6789"}, "internal", permitted=permitted("internal"))
    assert "123-45-6789" not in on.payload["question"], "and on is what happens without being asked"


# --------------------------------------------------------------------------- the record on the default mode

def test_the_redaction_summary_is_recorded_whatever_the_content_mode():
    """The gap the deployed probe found, as a test.

    The summary was being put in the audit payload, and the default content mode is `hash`, which stores a hash
    of the payload and not the payload. So on the arrangement the system ships with and recommends, the record
    proved that a call happened and proved nothing about the redaction: an operator asking "was anything removed
    from this request" got a null.

    A redaction summary is metadata about the request, not content. It says how many things of which kind were
    removed and never what they were, so storing it is not storing the client's data and it must not be governed
    by a setting about whether to store content.
    """
    import json as _json
    import subprocess

    service = ROOT / "services" / "audit" / "audit.py"
    source = service.read_text()
    # The summary reaches columns of its own rather than only the payload.
    for column in ("redaction_count", "redactions", "unverified_card_shapes", "egress_allowed"):
        assert column in source, f"{column} is not written by the audit service"
    assert "--redactions" in source, "the CLI does not accept a redaction summary"

    # And the columns exist independently of the payload, which is what makes them survive hash mode.
    migration = (ROOT / "db" / "migrations" / "012_redaction_record.sql").read_text()
    assert "add column if not exists redactions jsonb" in migration
    assert "not governed by a setting" in migration or "content mode" in migration

    prepared = egress.prepare({"question": "SSN 123-45-6789"}, "internal",
                              permitted=permitted("internal"))
    record = prepared.as_record()
    assert record["redactions"] == {"ssn": 1}
    assert record["redaction_count"] == 1
    assert record["egress_allowed"] is True
    # The summary never carries a value, so writing it to a column is not writing the client's data.
    assert "123-45-6789" not in _json.dumps(record)
