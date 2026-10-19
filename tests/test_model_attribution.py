"""What answered, not what was configured: failover attribution.

**Why this exists.** Once a gateway fallback exists, the manifest model is no longer the model that ran. The
answer and the audit row both used to name the primary unconditionally, so a failover was invisible in the
record and INF-ROLE-001 was false exactly when it mattered. The gateway puts the routing decision on the
response headers, and these tests hold that contract: the header parser, the normalisation of the reported
model back to the manifest spelling, and the two facts `finish` writes into the payload and the row.

The app module is imported through the `api_app` fixture rather than at module level, because that fixture is
where conftest installs the sse_starlette stub and the service paths; a bare import in a test module would fail
on a machine without the real transport.
"""
from __future__ import annotations

import pytest

HAIKU = "bedrock/us.anthropic.claude-haiku-4-5-20251001-v1:0"
SONNET = "bedrock/us.anthropic.claude-sonnet-4-5-20250929-v1:0"


@pytest.fixture(scope="module")
def modules(api_app):
    """The app and the shared answer module, with the imports conftest prepares."""
    api_app  # noqa: B018 - the fixture exists for its side effects on sys.path and sys.modules
    import app as app_module
    import answer as answer_module
    return app_module, answer_module


class Headers:
    def __init__(self, values: dict):
        self.values = values

    def items(self):
        return self.values.items()


def test_attribution_reads_the_gateway_routing_headers(modules):
    app_module, _answer = modules
    attribution = app_module.attribution_from(Headers({
        "x-litellm-model-group": "analyst-fallback",
        "x-litellm-model-name": "us.anthropic.claude-sonnet-4-5-20250929-v1:0",
        "x-litellm-attempted-fallbacks": "1",
    }))
    assert attribution == {
        "model_group": "analyst-fallback",
        "model": "us.anthropic.claude-sonnet-4-5-20250929-v1:0",
        "fallbacks_attempted": 1,
    }


def test_attribution_is_empty_when_there_are_no_headers(modules):
    app_module, _answer = modules
    assert app_module.attribution_from(None) == {}
    assert app_module.attribution_from(Headers({})) == {}


def test_attribution_survives_a_malformed_fallback_count(modules):
    app_module, _answer = modules
    attribution = app_module.attribution_from(Headers({
        "x-litellm-model-group": "analyst",
        "x-litellm-model-name": "us.anthropic.claude-haiku-4-5-20251001-v1:0",
        "x-litellm-attempted-fallbacks": "not-a-number",
    }))
    assert attribution["fallbacks_attempted"] == 0
    assert attribution["model_group"] == "analyst"


def test_model_facts_report_the_fallback_and_the_configured_model(modules):
    _app, answer_module = modules
    facts = answer_module.model_facts("analyst", HAIKU, {
        "model_group": "analyst-fallback",
        "model": "us.anthropic.claude-sonnet-4-5-20250929-v1:0",
        "fallbacks_attempted": 1,
    })
    assert facts["resolved_model"] == SONNET
    assert facts["configured_model"] == HAIKU
    assert facts["model_group"] == "analyst-fallback"
    assert facts["fallback_used"] is True
    assert facts["fallbacks_attempted"] == 1


def test_model_facts_on_a_healthy_call_report_the_primary_once(modules):
    _app, answer_module = modules
    facts = answer_module.model_facts("analyst", HAIKU, {
        "model_group": "analyst",
        "model": "us.anthropic.claude-haiku-4-5-20251001-v1:0",
        "fallbacks_attempted": 0,
    })
    assert facts["resolved_model"] == HAIKU
    assert facts["configured_model"] == HAIKU
    assert facts["fallback_used"] is False


def test_finish_credits_the_answering_model_in_the_payload_and_the_row(modules):
    _app, answer_module = modules

    class Session:
        subject = "probe-staff@officeinference.com"
        role = "staff"

    class Permit:
        roots = frozenset({"company"})

    class Passage:
        doc_id = "company/group-02/471787.csv"
        acl = "company"
        text = "DP03 | 64.0 | Mean household income (dollars) | 77,258"
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

    rows = []

    def audit(event, actor, actor_role, outcome, payload, **extra):
        rows.append({"event": event, "outcome": outcome, "payload": payload, "extra": extra})

    decided = answer_module.finish(
        question="What is the mean household income?",
        answer="The mean household income is 77,258 dollars [1].",
        session=Session(), permit=Permit(), model="analyst", result=Result(), decision=Decision(),
        data_class="internal", request_budget=object(), redaction_report=None, audit=audit,
        resolved_model=lambda role: HAIKU, attributed={
            "model_group": "analyst-fallback",
            "model": "us.anthropic.claude-sonnet-4-5-20250929-v1:0",
            "fallbacks_attempted": 1,
        }, request_id="req-1")

    assert decided.payload["resolved_model"] == SONNET
    assert decided.payload["configured_model"] == HAIKU
    assert decided.payload["model"] == "analyst"
    assert decided.payload["fallback_used"] is True
    assert decided.payload["model_group"] == "analyst-fallback"
    model_call = next(row for row in rows if row["payload"].get("stage") == "ask")
    assert model_call["extra"]["model"] == SONNET
    assert model_call["payload"]["resolved_model"] == SONNET
    assert model_call["payload"]["model_role"] == "analyst"
    assert model_call["payload"]["fallback_used"] is True
