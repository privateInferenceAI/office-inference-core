"""Routing: the gate first, the triggers inside it, and the user always told.

**Two triggers, not three.** A third escalated on a count of distinct documents, on the theory that several
documents means the answer needs reconciling. Measurement killed it: a plain single-fact question returned six
documents while a genuine multi-document question returned four, and no combination of count and score floor
separated them. `tests/fixtures/trigger_rules.py` prints the whole table. An unusable predicate that fires on
everything is worse than no trigger, because it escalates cost silently and makes the audit row meaningless.
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "services" / "api"))

import routing  # noqa: E402

PERMISSIVE = {'access': {'egress': {'frontier_classes': ['public', 'internal', 'confidential', 'regulated']}}}
FORBIDDING = {'access': {'egress': {'frontier_classes': ['public']}}}


def test_a_good_question_does_not_escalate_by_itself():
    """**The requirement in its own words.** A question the cheap tier answers poorly must not escalate on its
    own judgement, and there is nothing here that could form one."""
    decision = routing.route(PERMISSIVE, data_class='internal')
    assert decision.escalated is False
    assert decision.trigger == ''
    assert decision.notice == ''


def test_an_unescalated_request_still_reaches_the_configured_model():
    """**A defect this test exists for.** Not escalating does not mean answering locally: escalation selects a
    stronger model, and no escalation selects the ordinary one, which on a cloud deployment is still remote.
    Conflating the two made every unescalated request fail on a deployment with no local model."""
    decision = routing.route(PERMISSIVE, data_class='internal', model_name='analyst')
    assert decision.lane == 'frontier'
    assert decision.model == 'analyst', 'and it names the model that will answer'


def test_an_agentic_task_escalates_naming_its_trigger():
    decision = routing.route(PERMISSIVE, data_class='internal', task='reconcile')
    assert decision.escalated is True
    assert decision.trigger == routing.TRIGGER_AGENTIC
    assert decision.notice


def test_an_unknown_task_does_not_escalate():
    """The list is a lookup, not a judgement, so a task outside it is not escalated on a hunch."""
    decision = routing.route(PERMISSIVE, data_class='internal', task='something_new')
    assert decision.escalated is False


def test_a_user_request_escalates():
    decision = routing.route(PERMISSIVE, data_class='internal', user_wants_stronger=True)
    assert decision.trigger == routing.TRIGGER_USER
    assert decision.escalation_requested is True
    assert decision.notice


def test_many_documents_alone_does_not_escalate():
    """The removed trigger, kept removed. See the module docstring for the measurement."""
    decision = routing.route(PERMISSIVE, data_class='internal')
    assert decision.escalated is False


def test_a_data_class_that_forbids_the_lane_stops_every_trigger():
    """**The one-way boundary.** No trigger may move content toward a more exposed lane."""
    for kwargs in ({'task': 'reconcile'}, {'user_wants_stronger': True}):
        decision = routing.route(FORBIDDING, data_class='regulated', default_lane='local', **kwargs)
        assert decision.lane == 'local', kwargs
        assert decision.escalated is False, kwargs
        assert decision.trigger == '', kwargs


def test_a_refused_escalation_with_no_local_model_names_no_lane():
    """Where there is nothing to answer with, saying so beats announcing an answer that cannot exist."""
    decision = routing.route(FORBIDDING, data_class='regulated', user_wants_stronger=True)
    assert decision.lane == 'none'
    assert 'no local model' in decision.notice


def test_a_refused_escalation_is_announced_when_a_local_model_exists():
    """Decided 2026-10-14: the user is never left guessing."""
    decision = routing.route(FORBIDDING, data_class='regulated', default_lane='local',
                             user_wants_stronger=True)
    assert decision.escalation_requested is True
    assert 'may not be sent' in decision.notice
    assert decision.reason


def test_the_gate_refuses_rather_than_defaulting_when_nothing_is_declared():
    """An empty egress declaration means nothing leaves, which is the safe direction to fail in."""
    permitted, why = routing.gate('internal', [])
    assert permitted is False
    assert 'nothing' in why


def test_a_permitted_class_is_permitted():
    permitted, _ = routing.gate('regulated', ['regulated'])
    assert permitted is True


def test_the_audit_facts_carry_both_facts_separately():
    """What was asked for, and what ran. One field would conflate them."""
    decision = routing.route(FORBIDDING, data_class='regulated', default_lane='local',
                             user_wants_stronger=True)
    facts = routing.audit_facts(decision, 'regulated')
    assert facts['escalation_requested'] is True
    assert facts['lane'] == 'local'
    assert facts['trigger'] == ''
    assert facts['routing_version'] == 2


def test_every_decision_names_a_lane_and_a_reason():
    """A table of cases, because this table is the compliance artifact."""
    cases = [
        (PERMISSIVE, dict(data_class='internal')),
        (PERMISSIVE, dict(data_class='internal', task='compare')),
        (PERMISSIVE, dict(data_class='internal', user_wants_stronger=True)),
        (FORBIDDING, dict(data_class='regulated', default_lane='local')),
        (FORBIDDING, dict(data_class='regulated', user_wants_stronger=True)),
    ]
    for manifest, kwargs in cases:
        decision = routing.route(manifest, **kwargs)
        assert decision.lane in ('local', 'frontier', 'none'), kwargs
        assert decision.reason, kwargs
        assert decision.steps, 'every decision leaves a trail'

