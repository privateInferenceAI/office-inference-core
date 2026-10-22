"""The honesty gate: an unstated company attribute is not answerable from related material.

**Why this exists.** U08 asked for the company's parental leave policy. The corpus has FMLA law text and a
contractor clause, and the model presented the law as the policy. Two prompt iterations were measured: one did not
stop it and cost N05, the second stopped it and cost N07 and X07. This gate is the mechanical version, narrow to
questions phrased as an attribute of the company, and it is checked on every answer path.
"""
from __future__ import annotations

import pytest

COMPANY_ATTRIBUTE = "What is the company's parental leave policy?"
FMLA_ONLY = "The Family and Medical Leave Act requires eligible employers to provide leave. Model jury instructions."
STATED = "The parental leave policy is twelve weeks of unpaid leave for eligible employees."


@pytest.fixture(scope="module")
def honesty(api_app):
    api_app  # noqa: B018 - the fixture installs the service path and the sse stub
    import honesty as module
    return module


def test_the_subject_phrase_is_what_the_question_asks_the_company_to_state(honesty):
    assert honesty.subject_phrase(COMPANY_ATTRIBUTE) == "parental leave"
    assert honesty.subject_phrase("What is the company's dress code?") == "dress code"
    assert honesty.subject_phrase("What is the firm\u2019s remote work policy?") == "remote work"


def test_a_question_without_the_possessive_is_not_gated(honesty):
    assert honesty.unstated_company_attribute("What is the parental leave policy?", [FMLA_ONLY]) is False
    assert honesty.unstated_company_attribute("What is the CEO's salary?", [FMLA_ONLY]) is False


def test_related_material_does_not_state_the_attribute(honesty):
    """The failure: a law that mentions the topic is not the company's policy."""
    assert honesty.unstated_company_attribute(COMPANY_ATTRIBUTE, [FMLA_ONLY]) is True


def test_a_stated_attribute_passes_the_gate(honesty):
    assert honesty.unstated_company_attribute(COMPANY_ATTRIBUTE, [STATED]) is False


def test_the_comparison_normalises_case_and_hyphens(honesty):
    assert honesty.unstated_company_attribute(
        "What is the company's parental-leave policy?", ["Parental Leave Policy: twelve weeks"]) is False


def _fakes():
    class Session:
        subject = "probe-staff@officeinference.com"
        role = "staff"

    class Permit:
        roots = frozenset({"company"})

    class Passage:
        doc_id = "company/group-02/338123.doc"
        acl = "company"
        text = FMLA_ONLY
        page_start = 1
        page_end = 1

    class Result:
        passages = [Passage()]
        denied = 0
        disclosure = None

    class Decision:
        notice = ""
        lane = "frontier"
        escalated = False
        escalation_requested = False
        trigger = ""
        reason = "no trigger fired"

    return Session(), Permit(), Result(), Decision()


def test_finish_replaces_a_fabricated_policy_answer(honesty):
    import answer as answer_module

    rows = []

    def audit(event, actor, actor_role, outcome, payload, **extra):
        rows.append({"event": event, "payload": payload, "extra": extra})

    session, permit, result, decision = _fakes()
    decided = answer_module.finish(
        question=COMPANY_ATTRIBUTE,
        answer="The company's parental leave policy is twelve weeks under the FMLA [1].",
        session=session, permit=permit, model="analyst", result=result, decision=decision,
        data_class="internal", request_budget=object(), redaction_report=None, audit=audit,
        resolved_model=lambda role: "bedrock/us.anthropic.claude-haiku-4-5-20251001-v1:0",
        attributed={}, request_id="req-gate")

    assert decided.payload["answer"] == honesty.REFUSAL
    row = next(row for row in rows if row["payload"].get("stage") == "ask")
    assert row["payload"]["honesty_gate"] == "unstated_company_attribute"


def test_finish_keeps_an_answer_when_the_evidence_states_the_attribute(honesty):
    import answer as answer_module

    class StatedPassage:
        doc_id = "company/group-02/016886.xls"
        acl = "company"
        text = STATED
        page_start = 1
        page_end = 1

    session, permit, _result, decision = _fakes()
    result = type("Result", (), {"passages": [StatedPassage()], "denied": 0, "disclosure": None})()
    decided = answer_module.finish(
        question=COMPANY_ATTRIBUTE,
        answer="The company's parental leave policy is twelve weeks of unpaid leave [1].",
        session=session, permit=permit, model="analyst", result=result, decision=decision,
        data_class="internal", request_budget=object(), redaction_report=None,
        audit=lambda *args, **kwargs: None,
        resolved_model=lambda role: "bedrock/us.anthropic.claude-haiku-4-5-20251001-v1:0",
        attributed={}, request_id="req-gate-2")

    assert decided.payload["answer"].startswith("The company's parental leave policy")


def test_the_gate_is_part_of_the_answer_path(honesty):
    assert honesty.gate_enabled() is True
def test_the_our_possessive_is_read(honesty):
    """The boundary measured on 2026-10-19: 'our' answered from FMLA because the gate only read 'the company's'."""
    assert honesty.subject_phrase("What is our parental leave policy?") == "parental leave"
    assert honesty.subject_phrase("What is our dress code?") == "dress code"
    assert honesty.unstated_company_attribute("What is our parental leave policy?", [FMLA_ONLY]) is True


def test_the_policy_on_shape_is_read(honesty):
    assert honesty.subject_phrase("What is the policy on parental leave?") == "parental leave"
    assert honesty.subject_phrase("What is the policy regarding jury duty?") == "jury duty"
    assert honesty.unstated_company_attribute("What is the policy on parental leave?", [FMLA_ONLY]) is True


def test_meta_questions_about_the_documents_are_not_gated(honesty):
    """'What do our documents say' asks about the evidence, not about a company attribute."""
    assert honesty.subject_phrase("What do our documents say about parental leave?") == ""
    assert honesty.unstated_company_attribute(
        "What do our documents say about parental leave?", [FMLA_ONLY]) is False


def test_our_possessive_passes_when_the_evidence_states_it(honesty):
    assert honesty.unstated_company_attribute(
        "What is our parental leave policy?", [STATED]) is False
