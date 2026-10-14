"""Routing: the gate first, the three triggers inside it, and the user always told."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "services" / "api"))

import routing  # noqa: E402


class P:
    def __init__(self, doc_id, score):
        self.doc_id = doc_id
        self.score = score
        self.acl = 'company'
        self.page_start = 1
        self.page_end = 1
        self.text = 'text'


PERMISSIVE = {'access': {'egress': {'frontier_classes': ['public', 'internal', 'confidential', 'regulated']}}}
FORBIDDING = {'access': {'egress': {'frontier_classes': ['public']}}}


def test_a_good_question_does_not_escalate_by_itself():
    """**The requirement in its own words.** A question the cheap tier answers poorly must not escalate on its
    own judgement, and there is nothing here that could form one."""
    decision = routing.route(PERMISSIVE, data_class='internal', passages=[P('a.md', 0.9)])
    assert decision.escalated is False
    assert decision.trigger == ''
    assert decision.notice == "", 'nothing happened that the user needs to know about'


def test_an_agentic_task_escalates_naming_its_trigger():
    decision = routing.route(PERMISSIVE, data_class='internal', task='reconcile',
                             passages=[P('a.md', 0.9)])
    assert decision.escalated is True
    assert decision.trigger == routing.TRIGGER_AGENTIC
    assert decision.notice, 'the user is told'


def test_a_user_request_escalates():
    decision = routing.route(PERMISSIVE, data_class='internal', user_wants_stronger=True,
                             passages=[P('a.md', 0.9)])
    assert decision.trigger == routing.TRIGGER_USER
    assert decision.escalation_requested is True
    assert decision.notice


def test_a_data_class_that_forbids_the_lane_stops_every_trigger():
    """**The one-way boundary.** No trigger may move content toward a more exposed lane."""
    cases = ({'task': 'reconcile', 'passages': [P('a.md', 0.9)]},
             {'user_wants_stronger': True, 'passages': [P('a.md', 0.9)]},
             {'task': 'compare', 'passages': [P('a.md', 0.9), P('b.md', 0.9)]})
    for kwargs in cases:
        decision = routing.route(FORBIDDING, data_class='regulated', **kwargs)
        assert decision.lane == 'local', kwargs
        assert decision.escalated is False, kwargs
        assert decision.trigger == '', kwargs


def test_a_refused_escalation_is_announced():
    """The decision of 2026-10-14: the user is never left guessing."""
    decision = routing.route(FORBIDDING, data_class='regulated', user_wants_stronger=True,
                             passages=[P('a.md', 0.9)])
    assert decision.escalation_requested is True
    assert 'may not be sent' in decision.notice
    assert decision.reason, 'and the record says why'


def test_the_gate_refuses_rather_than_defaulting_when_nothing_is_declared():
    """An empty egress declaration means nothing leaves, which is the safe direction to fail in."""
    permitted, why = routing.gate('analyst', 'internal', [], remote=True)
    assert permitted is False
    assert 'nothing' in why


def test_a_local_model_is_not_an_egress_question():
    permitted, why = routing.gate('analyst', 'regulated', [], remote=False)
    assert permitted is True


def test_the_audit_facts_carry_both_facts_separately():
    """What was asked for, and what ran. One field would conflate them."""
    decision = routing.route(FORBIDDING, data_class='regulated', user_wants_stronger=True,
                             passages=[P('a.md', 0.9)])
    facts = routing.audit_facts(decision, 'regulated')
    assert facts['escalation_requested'] is True
    assert facts['lane'] == 'local'
    assert facts['trigger'] == ''
    assert facts['routing_version'] == 1


def test_every_decision_names_a_lane_and_a_reason():
    """A table of cases, because this table is the compliance artifact."""
    cases = [
        (PERMISSIVE, dict(data_class='internal', passages=[P('a.md', 0.9)])),
        (PERMISSIVE, dict(data_class='internal', task='compare', passages=[P('a.md', 0.9)])),
        (PERMISSIVE, dict(data_class='internal', passages=[P('a.md', 0.9), P('b.md', 0.9), P('c.md', 0.9)])),
        (PERMISSIVE, dict(data_class='internal', user_wants_stronger=True, passages=[P('a.md', 0.9)])),
        (FORBIDDING, dict(data_class='regulated', passages=[P('a.md', 0.9)])),
        (FORBIDDING, dict(data_class='regulated', user_wants_stronger=True, passages=[P('a.md', 0.9)])),
    ]
    for manifest, kwargs in cases:
        decision = routing.route(manifest, **kwargs)
        assert decision.lane in ('local', 'frontier'), kwargs
        assert decision.reason, kwargs
        assert decision.steps, 'every decision leaves a trail'


def test_many_documents_alone_does_not_escalate():
    """**The third trigger was removed, and this is the assertion that keeps it removed.**

    Escalating on a count of distinct documents was measured against real retrieval and failed: a plain
    single-fact question returned six documents while a genuine multi-document question returned four, and no
    combination of count and score floor separated them. An unusable predicate that fires on everything is
    worse than no trigger, because it escalates cost silently and makes the audit row meaningless.
    """
    decision = routing.route(PERMISSIVE, data_class='internal',
                             passages=[P('a.md', 0.9), P('b.md', 0.9), P('c.md', 0.9), P('d.md', 0.9)])
    assert decision.escalated is False
    assert decision.trigger == ''
    assert decision.notice == ''
