"""The tool loop: bounded, evidence-numbered, and falling back rather than failing.

The loop is the part of the answer path that can spend money in a circle, so the tests are mostly about bounds:
that it stops at the step cap, that it stops at the tool-call cap, that it stops at the deadline, and that it
gives the caller a reason instead of an exception. The evidence tests matter because the evidence numbering is the
citation namespace the verifier will check the final answer against.
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "services" / "api"))
sys.path.insert(0, str(ROOT / "services" / "retrieval"))
sys.path.insert(0, str(ROOT / "services" / "ingestion"))

import tool_loop  # noqa: E402


class FakePassage:
    def __init__(self, chunk_id, text, doc_id="company/policy.doc", page=1, kind="prose"):
        self.chunk_id = chunk_id
        self.text = text
        self.doc_id = doc_id
        self.page_start = page
        self.kind = kind


class FakePermit:
    roots = frozenset({"company"})


def make_toolbox():
    return tool_loop.ToolBox(services=object(), permit=FakePermit(), role="staff")


def tool_reply(name, arguments, call_id="call-1"):
    return {"content": "", "tool_calls": [{"id": call_id, "name": name, "arguments": arguments}],
            "error": None}


def answer_reply(text):
    return {"content": text, "tool_calls": [], "error": None}


def test_evidence_is_numbered_once_and_deduplicated():
    toolbox = make_toolbox()
    first = toolbox.add(FakePassage("c1", "the mileage rate is 67 cents"))
    again = toolbox.add(FakePassage("c1", "the mileage rate is 67 cents"))
    second = toolbox.add(FakePassage("c2", "another passage"))
    assert (first, again, second) == (1, 1, 2)
    rendered = toolbox.render([FakePassage("c1", "the mileage rate is 67 cents")])
    assert "[1]" in rendered and "company/policy.doc" in rendered


def test_evidence_has_a_ceiling():
    toolbox = tool_loop.ToolBox(services=object(), permit=FakePermit(), role="staff", max_evidence=2)
    assert toolbox.add(FakePassage("c1", "a")) == 1
    assert toolbox.add(FakePassage("c2", "b")) == 2
    assert toolbox.add(FakePassage("c3", "c")) == 0


def test_unknown_tool_is_returned_to_the_model_as_an_error():
    toolbox = make_toolbox()
    out = toolbox.execute("delete_everything", "{}")
    payload = json.loads(out)
    assert "unknown tool" in payload["error"]


def test_malformed_arguments_are_returned_as_an_error():
    toolbox = make_toolbox()
    payload = json.loads(toolbox.execute("search_documents", "not json"))
    assert "JSON object" in payload["error"]


def test_a_search_then_an_answer_is_two_steps_and_one_call():
    toolbox = make_toolbox()

    def fake_search(query):
        number = toolbox.add(FakePassage("c1", "the standard mileage rate is 67 cents per mile"))
        return f"[{number}] company/policy.doc: the standard mileage rate is 67 cents per mile"

    toolbox.search_documents = fake_search
    replies = iter([
        tool_reply("search_documents", json.dumps({"query": "mileage rate"})),
        answer_reply("The standard mileage rate is 67 cents per mile [1]."),
    ])
    result = tool_loop.run_loop(lambda messages, tools: next(replies), toolbox,
                                "What is the mileage rate?")
    assert result["answer"].startswith("The standard mileage rate")
    assert result["steps"] == 2 and result["tool_calls"] == 1
    assert len(result["evidence"]) == 1


def test_the_step_cap_stops_a_model_that_never_answers():
    toolbox = make_toolbox()
    toolbox.search_documents = lambda query: "no results"

    def always_search(messages, tools):
        return tool_reply("search_documents", json.dumps({"query": "again"}))

    result = tool_loop.run_loop(always_search, toolbox, "q", max_steps=3)
    assert result["answer"] is None
    assert result["reason"] == "tool_step_limit"
    assert result["steps"] == 3


def test_the_tool_call_cap_stops_a_wide_model():
    toolbox = make_toolbox()
    toolbox.search_documents = lambda query: "no results"

    def many_calls(messages, tools):
        return {"content": "", "error": None, "tool_calls": [
            {"id": f"c{i}", "name": "search_documents", "arguments": "{}"} for i in range(5)]}

    result = tool_loop.run_loop(many_calls, toolbox, "q", max_steps=3, max_tool_calls=2)
    assert result["answer"] is None
    assert result["reason"] == "tool_budget"
    assert result["tool_calls"] == 2


def test_the_deadline_stops_before_a_model_call():
    toolbox = make_toolbox()
    called = []

    def call_model(messages, tools):
        called.append(1)
        return answer_reply("never")

    result = tool_loop.run_loop(call_model, toolbox, "q", deadline=time.monotonic() - 1)
    assert result["answer"] is None and result["reason"] == "tool_deadline"
    assert called == []


def test_an_unsupported_figure_gets_one_corrective_turn():
    toolbox = make_toolbox()
    toolbox.add(FakePassage("c1", "the standard mileage rate is 67 cents per mile"))
    replies = iter([
        answer_reply("The standard mileage rate is 999 cents per mile."),
        answer_reply("The standard mileage rate is 67 cents per mile [1]."),
    ])
    result = tool_loop.run_loop(lambda messages, tools: next(replies), toolbox,
                                "What is the mileage rate?")
    assert result["corrections"] == 1
    assert "67 cents" in result["answer"]


def test_progress_events_are_reported_for_steps_and_tools():
    toolbox = make_toolbox()
    toolbox.search_documents = lambda query: "no results"
    events = []
    replies = iter([
        tool_reply("search_documents", json.dumps({"query": "mileage"})),
        answer_reply("I could not find that in the available documents."),
    ])
    tool_loop.run_loop(lambda messages, tools: next(replies), toolbox, "q",
                       on_event=events.append)
    assert [event["type"] for event in events] == ["step", "tool", "step"]
    assert events[1]["tool"] == "search_documents"


def test_a_refusal_on_quoted_figures_triggers_one_lookup():
    toolbox = make_toolbox()
    calls = []

    def fake_lookup(value, doc_id=""):
        calls.append(value)
        number = toolbox.add(FakePassage("c1", "228865.1 | 188387.5 | -6.1"))
        return f"[{number}] company/coords.txt: 228865.1 | 188387.5 | -6.1"

    toolbox.lookup_value = fake_lookup
    replies = iter([
        answer_reply("I could not find that in the available documents."),
        answer_reply("The third value is -6.1 [1]."),
    ])
    result = tool_loop.run_loop(lambda messages, tools: next(replies), toolbox,
                                "What is the third value for row 228865.1 and 188387.5?")
    assert calls == ["228865.1 and 188387.5"]
    assert result["auto_lookups"] == 1
    assert "-6.1" in result["answer"]


def test_a_provider_error_returns_a_reason_not_an_exception():
    toolbox = make_toolbox()
    result = tool_loop.run_loop(lambda messages, tools: {"error": "throttled"}, toolbox, "q")
    assert result["answer"] is None and result["reason"] == "throttled"


def test_lookup_refuses_to_anchor_on_a_figure_in_many_documents():
    toolbox = make_toolbox()
    matches = [(f"chunk{i}", f"company/doc{i}.csv") for i in range(5)]
    toolbox._lookup_matches = lambda normalized, doc_id="": matches
    payload = json.loads(toolbox.lookup_value("59,473"))
    assert "does not identify one row" in payload["note"]


def test_the_lookup_tool_can_be_turned_off_for_a_measurement(monkeypatch):
    monkeypatch.delenv("OI_TOOL_LOOKUP", raising=False)
    assert "lookup_value" in [schema["function"]["name"] for schema in tool_loop.active_tools()]
    monkeypatch.setenv("OI_TOOL_LOOKUP", "off")
    assert "lookup_value" not in [schema["function"]["name"] for schema in tool_loop.active_tools()]


def test_the_tool_schemas_match_the_executor():
    names = [schema["function"]["name"] for schema in tool_loop.TOOLS]
    assert names == ["search_documents", "read_passages", "lookup_value"]


def test_lookup_value_uses_each_figure_it_is_given():
    toolbox = make_toolbox()
    seen = []

    def fake_lookup(normalized, doc_id=""):
        seen.append(normalized)
        return [("c1", "company/coords.txt")] if normalized in ("228865.1", "188387.5") else []

    toolbox._lookup_matches = fake_lookup
    import retriever as real_retriever
    real_fetch = real_retriever._fetch_payloads
    real_retriever._fetch_payloads = lambda services, ids, roots: {
        "c1": {"chunk_id": "c1", "doc_id": "company/coords.txt", "acl": "company",
               "text": "228865.1 | 188387.5 | -6.1", "page_start": 1, "page_end": 1,
               "kind": "table_row"}}
    try:
        out = toolbox.lookup_value("228865.1 and 188387.5")
    finally:
        real_retriever._fetch_payloads = real_fetch
    assert seen == ["228865.1", "188387.5"], "each figure is a literal match"
    assert "188387.5" in out and "company/coords.txt" in out


def test_lookup_value_rejects_an_empty_figure():
    toolbox = make_toolbox()
    payload = json.loads(toolbox.lookup_value("not a number"))
    assert "value must contain a figure" in payload["error"]


def test_lookup_value_renders_the_matching_rows():
    toolbox = make_toolbox()
    toolbox._lookup_matches = lambda normalized, doc_id="": []
    payload = json.loads(toolbox.lookup_value("59,473"))
    assert "no passage states" in payload["error"]

    toolbox._lookup_matches = lambda normalized, doc_id="": [("c1", "company/census.csv")]
    import retriever as real_retriever
    imports = real_retriever  # only to satisfy the import inside the method
    toolbox.add(FakePassage("c1", "median household income 59,473 dollars"))
    real_fetch = imports._fetch_payloads
    imports._fetch_payloads = lambda services, ids, roots: {
        "c1": {"chunk_id": "c1", "doc_id": "company/census.csv", "acl": "company",
               "text": "median household income 59,473 dollars", "page_start": 1, "page_end": 1,
               "kind": "table_row"}}
    try:
        out = toolbox.lookup_value("59,473")
    finally:
        imports._fetch_payloads = real_fetch
    assert "59,473" in out and "company/census.csv" in out


def test_the_prompt_carries_the_honesty_rule_for_absent_events():
    # The 2026-10-18 tool runs on the weaker model answered two unanswerable questions from adjacent facts.
    # The rule that stops a related figure being relabelled as the asked-for one lives in the system prompt.
    prompt = tool_loop.SYSTEM_PROMPT
    assert "specific event, period, meeting or document" in prompt
    assert "Do not infer that a related figure" in prompt
    assert "lookup_value" in prompt


def test_the_flag_defaults_off(monkeypatch):
    monkeypatch.delenv("OI_TOOL_LOOP", raising=False)
    assert tool_loop.enabled() is False
    monkeypatch.setenv("OI_TOOL_LOOP", "on")
    assert tool_loop.enabled() is True
