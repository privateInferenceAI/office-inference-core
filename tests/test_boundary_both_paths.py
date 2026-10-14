"""Both endpoints guard the data boundary, and the audit record tells the truth.

**Why this test exists.** `/ask` had a `lane == "none"` guard and `/ask/stream` did not, so a class the
manifest forbade was refused with a 403 on one endpoint and answered by the remote model on the other — with an
audit row that said `egress_allowed: false` while the content left the building.

Found by the C3 panel, who ran a second API container against a manifest copy with a forbidden class and read
both answers. **The same shape as the redaction defect found the same way: a control implemented on the path
somebody was looking at.** So this asserts the guard on BOTH paths rather than the one it was written for.
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
API = ROOT / "services" / "api" / "app.py"


def test_the_json_handler_refuses_a_lane_that_may_not_answer():
    source = API.read_text()
    assert 'if decision.lane == "none" or (' in source, "the JSON handler has no lane guard"


def test_the_streaming_handler_refuses_one_too():
    """The defect. Without this, one endpoint is a boundary and the other is a hole."""
    source = API.read_text()
    assert 'if stream_decision.lane == "none" or (' in source, (
        "the streaming handler computes a routing decision and never checks it")


def test_the_stream_refusal_happens_before_the_response_starts():
    """A stream that has begun cannot say no: the status code is spent with the first frame."""
    source = API.read_text()
    guard = source.index('if stream_decision.lane == "none" or (')
    event_source = source.index("return EventSourceResponse(", guard)
    assert guard < event_source, "the guard runs after the stream has been opened"
    between = source[guard:event_source]
    assert "status_code=403" in between, "the refusal is not a status code"


def test_every_routing_decision_is_read_at_least_once():
    """The general shape of the defect: a decision computed and then dropped."""
    source = API.read_text()
    for name in ("decision", "stream_decision"):
        assert source.count(name) > 1, f"{name} is assigned and never used again"


def test_the_stream_refusal_reaches_the_record():
    """A refusal nobody can find is not a control."""
    source = API.read_text()
    guard = source.index('if stream_decision.lane == "none" or (')
    block = source[guard:guard + 1400]
    assert 'audit("access"' in block, "the stream refusal is not recorded"
    assert 'egress_allowed=False' in block, "and the record does not say egress was refused"

