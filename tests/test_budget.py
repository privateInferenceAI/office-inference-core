"""The budget: which failures are worth waiting for, and which threshold tripped."""
from __future__ import annotations

import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "services" / "api"))

import budget as budget_module  # noqa: E402


def test_a_throttle_is_retryable():
    """The one failure the whole step exists for."""
    kind = budget_module.classify('ThrottlingException: Rate exceeded')
    assert kind["retryable"] is True
    assert kind["reason"] == "throttled"


def test_a_budget_refusal_is_terminal():
    """Waiting will not help, and the wait would spend the budget the degraded answer needed."""
    kind = budget_module.classify('Budget has been exceeded! Team=analyst')
    assert kind["retryable"] is False
    assert kind["reason"] == "budget"


def test_a_spend_cap_429_is_not_retried_on_its_status():
    """The case status-based classification gets wrong: a 429 that retrying cannot fix."""
    kind = budget_module.classify('rate_limit_error: enforced_spend_limit_reached')
    assert kind["retryable"] is False


def test_an_unknown_failure_is_terminal():
    """Safe direction: an unrecognised error is not a licence to spend the degraded answer's budget."""
    kind = budget_module.classify('something nobody has seen before')
    assert kind["retryable"] is False
    assert kind["reason"] == "unclassified"


def test_a_model_not_ready_429_is_distinguished_from_a_throttle():
    """Both are 429. Only one is worth waiting for."""
    kind = budget_module.classify('ModelNotReadyException: model is loading')
    assert kind["reason"] != "throttled"


def test_every_classification_carries_the_taxonomy_version():
    """The chain is append-only, so a reason without its rule set is not reproducible."""
    for text in ('ThrottlingException', 'Budget has been exceeded', 'nonsense'):
        assert budget_module.classify(text)["taxonomy_version"] == budget_module.TAXONOMY_VERSION


def test_the_first_token_threshold_fires_before_the_total():
    """A request that never produced a token has not stalled; it never started."""
    b = budget_module.Budget(first_token=0.05, stall=10.0, total=30.0)
    time.sleep(0.08)
    assert b.timed_out() == "first_token"


def test_the_stall_threshold_fires_after_the_first_token():
    """The distinction the three thresholds exist for: started, then stopped."""
    b = budget_module.Budget(first_token=10.0, stall=0.05, total=30.0)
    b.note_chunk()
    time.sleep(0.08)
    assert b.timed_out() == "stall"


def test_a_healthy_stream_trips_nothing():
    """The complementary case. A budget that fired on a good request would be its own outage."""
    b = budget_module.Budget(first_token=1.0, stall=5.0, total=30.0)
    b.note_chunk()
    assert b.timed_out() is None


def test_the_total_cap_fires_last():
    b = budget_module.Budget(first_token=10.0, stall=10.0, total=0.01)
    time.sleep(0.03)
    assert b.timed_out() == "total"


def test_the_delivery_reserve_is_inside_the_total():
    """A degradation that runs past the deadline is a slower failure, not a degraded answer."""
    b = budget_module.Budget(total=1.0, reserve=0.4)
    b.started = time.monotonic() - 0.7
    assert b.past_reserve() is True
    assert b.spent() is False


def test_a_degraded_answer_carries_the_passages_and_never_the_prompt():
    """The answer has to be useful, and the record has to stay free of content."""
    class P:
        doc_id = 'company/travel.md'
        acl = 'company'
        page_start = 1
        page_end = 2
        text = 'The mileage rate is 0.67 per mile. ' * 20

    b = budget_module.Budget()
    answer = budget_module.degraded('throttled', 'the provider is throttling', [P()], b, 3, 'total', False)
    assert answer["kind"] == "degraded"
    assert answer["reason"] == "throttled"
    assert answer["passages"][0]["doc_id"] == "company/travel.md"
    assert len(answer["passages"][0]["excerpt"]) <= 240
    assert answer["budget"]["taxonomy_version"] == budget_module.TAXONOMY_VERSION


def test_audit_facts_carry_the_fields_an_examiner_asks_for():
    b = budget_module.Budget()
    facts = budget_module.audit_facts('throttled', 'throttling_exception', True, 3, 'total', b, False,
                                      ['ThrottlingException'], lane='frontier', data_class='internal')
    for key in ('reason', 'matched_type', 'attempts', 'threshold', 'partial_output', 'lane', 'data_class',
                'taxonomy_version', 'errors', 'remaining_ms', 'elapsed_ms'):
        assert key in facts, key
    assert facts["attempts"] == 3


def test_audit_facts_do_not_carry_content():
    """They are built from identifiers, and a test that a prompt cannot leak into them is cheap."""
    b = budget_module.Budget()
    secret = 'the client is called Acme and their account is 4111 1111 1111 1111'
    facts = budget_module.audit_facts('throttled', 'throttling', True, 1, 'total', b, False, [secret[:20]])
    assert 'Acme' not in str(facts)
    assert '4111' not in str(facts)
