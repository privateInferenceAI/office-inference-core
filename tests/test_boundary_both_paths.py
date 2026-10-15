"""Both endpoints guard the data boundary, and the audit record tells the truth.

**Why this test exists.** `/ask` had a `lane == "none"` guard and `/ask/stream` did not, so a class the
manifest forbade was refused with a 403 on one endpoint and answered by the remote model on the other — with an
audit row that said `egress_allowed: false` while the content left the building.

Found by the C3 panel, who ran a second API container against a manifest copy with a forbidden class and read
both answers. **The same shape as the redaction defect found the same way: a control implemented on the path
somebody was looking at.** So this asserts the guard on BOTH paths rather than the one it was written for.

**One assertion moved to `answer.py`, and the move is the fix rather than a concession.** The guard existed
twice, in two handlers, and one copy was missing. The pipeline now lives in one module that every path calls,
so the guard exists once and cannot be present on one endpoint and absent on another. The assertions below
check that the extraction actually happened, because a shared pipeline that one handler does not use is a
shared pipeline that has not fixed anything.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
API = ROOT / "services" / "api" / "app.py"
PIPELINE = ROOT / "services" / "api" / "answer.py"


def test_the_shared_pipeline_refuses_a_lane_that_may_not_answer():
    """The JSON path's guard, after the extraction. One implementation, reached by every path."""
    source = PIPELINE.read_text()
    assert 'if decision.lane == "none" or (decision.lane == "local"' in source, (
        "the shared pipeline has no lane guard, so every path that answers a question lost it at once")


def test_the_streaming_handler_refuses_one_too():
    """The defect. Without this, one endpoint is a boundary and the other is a hole."""
    source = API.read_text()
    assert 'if stream_decision.lane == "none" or (' in source, (
        "the streaming handler computes a routing decision and never checks it")


def test_the_json_handler_goes_through_the_shared_pipeline():
    """**The structural fix for the class of defect this file was written for.**

    Two handlers with two copies of a guard is how the guard goes missing from one of them. `/ask` calls the
    same pipeline a job does, so there is one implementation and no second copy to keep in step.
    """
    source = API.read_text()
    assert "answer_module.prepare(" in source, (
        "the JSON handler does not go through the shared pipeline, so the guards are duplicated again")


def test_the_job_path_goes_through_it_as_well():
    """A job is another way to ask a model a question, so it carries the same guards or it is the hole."""
    source = API.read_text()
    start = source.index("def _step_once(")
    body = source[start:source.index("\n@app.", start)]
    assert "answer_module.prepare(" in body, (
        "the job step builds its own retrieval and routing path, which is where the next boundary defect "
        "would live")


def test_the_stream_refusal_happens_before_the_response_starts():
    """A stream that has begun cannot say no: the status code is spent with the first frame."""
    source = API.read_text()
    guard = source.index('if stream_decision.lane == "none" or (')
    event_source = source.index("return EventSourceResponse(", guard)
    assert guard < event_source, "the guard runs after the stream has been opened"
    between = source[guard:event_source]
    assert "status_code=403" in between, "the refusal is not a status code"


def test_every_routing_decision_is_read_at_least_once():
    """The general shape of the defect: a decision computed and then dropped.

    **Two files, because there are two places a decision is made now.** The streaming path still computes
    its own, because a stream has to decide before it opens; everything else goes through the shared
    pipeline. A count of one in either file means a decision was taken and nothing acted on it.
    """
    assert API.read_text().count("stream_decision") > 1, "stream_decision is assigned and never used again"
    assert PIPELINE.read_text().count("decision") > 1, "the pipeline's decision is assigned and never read"


def test_the_stream_refusal_reaches_the_record():
    """A refusal nobody can find is not a control."""
    source = API.read_text()
    guard = source.index('if stream_decision.lane == "none" or (')
    block = source[guard:guard + 1400]
    assert 'audit("access"' in block, "the stream refusal is not recorded"
    assert 'egress_allowed=False' in block, "and the record does not say egress was refused"


def test_the_shared_pipeline_records_its_refusal_too():
    """The same right, on the path every other endpoint takes. Asserted separately, because the streaming
    assertion above would pass while this one was broken."""
    source = PIPELINE.read_text()
    guard = source.index('if decision.lane == "none" or (')
    block = source[guard:guard + 900]
    assert 'audit("access"' in block, "the shared refusal is not recorded"
    assert "egress_allowed=False" in block, "and the record does not say egress was refused"



def test_a_job_is_refused_before_it_is_created():
    """**The third path, and the one the boundary probe found open.**

    A job whose first attempt discovered the boundary answered HTTP 200 with a failed job row: the refusal
    was inside `result`, the status code said fine, and a row existed for work no lane was permitted to do.
    `/ask` refused the same request with a 403.

    So the boundary is consulted before the job exists. This asserts the ordering in the source, because the
    behaviour needs a deployment whose class may not leave and that is what `gate_check_run.sh --jobs` is for.
    """
    source = API.read_text()
    create = source.index("async def create_job(")
    check = source.index("answer_module.check_boundary(", create)
    store_put = source.index("store.put(job)", create)
    assert check < store_put, (
        "the job is stored before the boundary is consulted, so work no lane may do gets a row and a 200")


def test_the_boundary_check_is_not_duplicated():
    """One implementation. The defect this whole file is about was two copies of a control."""
    pipeline = PIPELINE.read_text()
    assert pipeline.count("def check_boundary(") == 1
    # `prepare` delegates rather than repeating the refusal, so the two paths cannot drift.
    assert "check_boundary(manifest=" in pipeline
    assert "def check_boundary(" in pipeline
    assert API.read_text().count("no_permitted_lane") + pipeline.count("no_permitted_lane") <= 3, (
        "the refusal wording appears in more than one place per path, which is how the copies drift")


def test_the_boundary_refusal_runs_rather_than_being_present():
    """**The check that would have caught a NameError on the refusal path.**

    `check_boundary` was given a `request_id` parameter and its body used the name, and the test written for it
    asserted the string appeared in the source. The string did appear — in `prepare`, which passed it to nothing
    — and the function itself raised `NameError` the first time a forbidden class reached it. The probe found
    that on a deployment, which is an expensive place to find a missing parameter.

    So this calls the function. It goes down the refusal path, which is the path that only runs when a class may
    not leave, and that is why nothing on the ordinary path noticed.
    """
    # **The service paths, the way the rest of the suite sets them up.** `answer.py` imports its neighbours by
    # module name because the image is what normally arranges that, so a test has to reproduce the layout.
    import sys

    for name in ("api", "retrieval", "ingestion", "session", "gateway", "egress"):
        path = str(ROOT / "services" / name)
        if path not in sys.path:
            sys.path.insert(0, path)
    import answer as answer_module  # noqa: PLC0415

    class Session:
        subject = "probe@example.com"
        role = "viewer"
        session_id = "an-identifier"

    rows = []

    def audit(*args, **kwargs):
        rows.append((args, kwargs))

    manifest = {"access": {"egress": {"frontier_classes": ["public", "internal"]}},
                "models": {"analyst": {"local": "", "frontier": "bedrock/x"}},
                "lanes": {"frontier": {"enabled": True, "provider": "bedrock"}}}

    with pytest.raises(answer_module.Refused) as refused:
        answer_module.check_boundary(
            manifest=manifest, data_class="regulated", task="", model_name="analyst",
            default_lane="frontier", frontier_model="bedrock/x", session=Session(), audit=audit,
            endpoint="/ask", request_id="the-id")
    assert refused.value.status == 403
    assert refused.value.kind == "no_permitted_lane"
    # And the row it wrote names the same id the refusal carries, which is what ties the two together.
    assert refused.value.payload.get("request_id") == "the-id"
    assert rows and rows[0][1].get("request_id") == "the-id", "the refusal's row does not carry the id"
