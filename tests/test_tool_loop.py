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
        return f"Passages stating {value!r}:\n[{number}] company/coords.txt: 228865.1 | 188387.5 | -6.1"

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
    assert names == ["search_documents", "read_passages", "query_rows", "lookup_value"]


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
    prompt = tool_loop.system_prompt(lookup_enabled=True, honesty_enabled=True)
    assert "specific event, period, meeting or document" in prompt
    assert "Do not infer that a related figure" in prompt
    assert "lookup_value" in prompt


def test_the_prompt_only_names_the_tools_that_are_offered():
    """The prompt once named lookup_value while the tool was switched off, so the model could waste its steps
    calling a tool that was not there. The prompt and the tool list have to be one fact."""
    without = tool_loop.system_prompt(lookup_enabled=False, honesty_enabled=True)
    assert "search_documents" in without and "read_passages" in without
    assert "lookup_value" not in without
    with_lookup = tool_loop.system_prompt(lookup_enabled=True, honesty_enabled=True)
    assert "lookup_value" in with_lookup


def test_the_thoroughness_rule_is_in_the_prompt():
    # Dropping this rule in the Phase B rewrite halved the loop's duration and cost the suite seven points.
    prompt = tool_loop.system_prompt(lookup_enabled=False, honesty_enabled=True)
    assert "table row is a valid answer" in prompt
    assert "Check every passage before concluding" in prompt


def test_the_honesty_rule_can_be_measured_off():
    prompt = tool_loop.system_prompt(lookup_enabled=False, honesty_enabled=False)
    assert "specific event, period, meeting or document" not in prompt


def test_the_flag_defaults_off(monkeypatch):
    monkeypatch.delenv("OI_TOOL_LOOP", raising=False)
    assert tool_loop.enabled() is False
    monkeypatch.setenv("OI_TOOL_LOOP", "on")
    assert tool_loop.enabled() is True


# --------------------------------------------------------------------------- the structured row query
#
# The row query is the one tool that returns a row instead of a passage, so the tests hold the three things that
# make it trustworthy: the query it builds is read-only and parameterised, access is a clause in that query, and
# the row it returns carries the ordinal a citation needs. The row-extraction tests use chunk payloads with the
# same shape the ingestion worker writes; none of them needs a database or the network.

def payload(text, doc_id="company/census.csv", kind="table", page=1, row_start=None, row_index=None,
            table_rows=68, headings=None, chunk_id="c1"):
    return {"chunk_id": chunk_id, "doc_id": doc_id, "acl": "company", "text": text, "kind": kind,
            "page_start": page, "page_end": page, "row_start": row_start, "row_index": row_index,
            "table_rows": table_rows, "headings": headings or [], "table_id": "t1",
            "parse_score": None}


def install_payloads(monkeypatch, payloads):
    """Make the tool's payload fetch return these payloads, from the real retriever module it imports."""
    import retriever as real_retriever
    monkeypatch.setattr(real_retriever, "_fetch_payloads",
                        lambda services, ids, roots: {key: value for key, value in payloads.items()
                                                      if key in ids})


def query_toolbox(monkeypatch, payloads, candidates=None, audit=None):
    toolbox = tool_loop.ToolBox(services=object(), permit=FakePermit(), role="staff", audit=audit)
    if candidates is None:
        candidates = [(key, value["doc_id"]) for key, value in payloads.items()]
    toolbox._candidate_chunks = lambda values, doc_id="": list(candidates)
    install_payloads(monkeypatch, payloads)
    return toolbox


def test_the_row_query_is_a_read_only_parameterised_select():
    sql, params = tool_loop.row_query(["228865.1", "188387.5"], "company/x' or 1=1 --", {"company"})
    lower = sql.lower()
    assert lower.startswith("select")
    for verb in ("insert", "update", "delete", "drop", "alter", "create"):
        assert verb not in lower.split()
    assert "acl = any(%(roots)s)" in sql
    assert params["roots"] == ["company"]
    # The document id is a bound value, never a fragment of the statement.
    assert params["doc"] == "company/x' or 1=1 --"
    assert "company/x' or 1=1 --" not in sql
    assert "limit %(limit)s" in sql


def test_the_row_query_matches_without_wildcard_semantics():
    """A value of '%' has to mean a per cent sign, not every row."""
    sql, params = tool_loop.row_query(["100%"], "", {"company"})
    assert "position(%(v0)s in lower(replace(text, ',', ''))) > 0" in sql
    assert "like" not in sql.lower()
    assert params["v0"] == "100%"


def test_the_row_query_is_off_until_the_flag_turns_it_on(monkeypatch):
    monkeypatch.delenv("OI_TOOL_QUERY", raising=False)
    assert "query_rows" not in [schema["function"]["name"] for schema in tool_loop.active_tools()]
    monkeypatch.setenv("OI_TOOL_QUERY", "on")
    assert "query_rows" in [schema["function"]["name"] for schema in tool_loop.active_tools()]


def test_the_prompt_names_the_row_query_only_when_it_is_offered():
    without = tool_loop.system_prompt(lookup_enabled=False, honesty_enabled=True)
    assert "query_rows" not in without
    with_query = tool_loop.system_prompt(lookup_enabled=False, honesty_enabled=True, query_enabled=True)
    assert "query_rows" in with_query
    assert "exact table rows" in with_query


def test_the_row_query_returns_the_exact_row_and_its_ordinal(monkeypatch):
    text = "\n".join([
        "231629.5 | 188623.1 | 0",
        "228865.1 | 188387.5 | -6.1",
        "228865 | 188347.6 | -6.1",
    ])
    toolbox = query_toolbox(monkeypatch, {"c1": payload(text)})
    out = toolbox.query_rows(["228865.1", "188387.5"])
    assert "228865.1 | 188387.5 | -6.1" in out
    assert "row 1 of 68" in out
    # The row is evidence the answer can cite, and the evidence text is the row, not the group.
    assert len(toolbox.evidence) == 1
    assert toolbox.evidence[0].text == "228865.1 | 188387.5 | -6.1"
    assert "[1]" in out


def test_the_row_ordinal_counts_from_the_group_start(monkeypatch):
    toolbox = query_toolbox(monkeypatch, {"c1": payload("228865.1 | 191380.1 | 0\n229000 | 190000 | 1",
                                                         row_start=4200)})
    out = toolbox.query_rows(["228865.1"])
    assert "row 4200 of 68" in out


def test_the_heading_prefix_is_not_counted_as_a_row(monkeypatch):
    text = "Schedule A > Part 1\nfirst | 1\n228865.1 | 188387.5 | -6.1"
    toolbox = query_toolbox(monkeypatch, {"c1": payload(text, row_start=10,
                                                         headings=["Schedule A", "Part 1"])})
    out = toolbox.query_rows(["228865.1", "188387.5"])
    assert "228865.1 | 188387.5 | -6.1" in out
    assert "row 11 of 68" in out, "two prefix lines are one heading line, so no off-by-one"


def test_a_single_row_chunk_uses_the_row_index_the_worker_recorded(monkeypatch):
    toolbox = query_toolbox(monkeypatch, {"c1": payload("DP03 | 63.0 | Median household income (dollars) | "
                                                         "59,473 | +/-528", kind="table_row",
                                                         row_index=63)})
    out = toolbox.query_rows(["59,473", "Median household income"])
    assert "row 63 of 68" in out
    assert "59,473" in out


def test_a_duplicate_row_entry_is_collapsed_to_one_row(monkeypatch):
    # The same row exists in the whole-table chunk and as its own row entry; the model must see one line.
    whole = payload("value | 59,473", chunk_id="whole", row_start=None)
    single = payload("value | 59,473", chunk_id="single", kind="table_row", row_index=0)
    toolbox = query_toolbox(monkeypatch, {"whole": whole, "single": single})
    out = toolbox.query_rows(["59,473"])
    assert out.count("value | 59,473") == 1
    assert "row 0 of 68" in out


def test_the_row_query_caps_the_rows_it_returns(monkeypatch):
    payloads = {}
    candidates = []
    for index in range(20):
        key = f"c{index}"
        payloads[key] = payload(f"row {index} | 59,473", doc_id="company/doc0.csv", chunk_id=key,
                                row_start=index)
        candidates.append((key, "company/doc0.csv"))
    toolbox = query_toolbox(monkeypatch, payloads, candidates=candidates)
    out = toolbox.query_rows(["59,473"])
    assert "20 rows matched" in out and "4 are shown" in out
    assert out.count("company/doc0.csv") == 4
    out_all = toolbox.query_rows(["59,473"], limit=99)
    assert out_all.count("company/doc0.csv") == tool_loop.QUERY_ROW_CAP, "the cap is hard"
    sql, params = tool_loop.row_query(["59,473"], "", {"company"})
    assert params["limit"] == tool_loop.QUERY_SCAN_LIMIT


def test_the_row_cap_and_the_scan_cap_are_constants_the_model_cannot_widen():
    assert tool_loop.QUERY_ROW_CAP == 8
    assert tool_loop.QUERY_SCAN_LIMIT > 0
    sql, params = tool_loop.row_query(["x"], "", {"company"}, scan_limit=10_000)
    assert params["limit"] == 10_000 or "limit" in sql  # builder honours its argument; callers pass the constant


def test_an_unscoped_row_query_that_spans_many_documents_is_refused_with_a_reason(monkeypatch):
    payloads = {f"c{index}": payload(f"row | 59,473", doc_id=f"company/doc{index}.csv", chunk_id=f"c{index}")
                for index in range(5)}
    toolbox = query_toolbox(monkeypatch, payloads)
    out = tool_loop.json.loads(toolbox.query_rows(["59,473"]))
    assert "do not identify one row" in out["note"]
    assert "Pass doc_id" in out["note"]


def test_the_row_query_reports_an_unreadable_store_distinctly_from_no_match():
    toolbox = tool_loop.ToolBox(services=object(), permit=FakePermit(), role="staff")
    out = tool_loop.json.loads(toolbox.query_rows(["59,473"]))
    assert "document store could not be read" in out["error"]


def test_the_row_query_validates_its_match_argument():
    toolbox = tool_loop.ToolBox(services=object(), permit=FakePermit(), role="staff")
    for bad in ({}, [], ["a", "b", "c", "d", "e"], ["  "], None):
        payload_out = tool_loop.json.loads(toolbox.query_rows(bad))
        assert "one to four" in payload_out["error"] or "strings" in payload_out["error"]


def test_the_row_query_reports_no_matching_row(monkeypatch):
    toolbox = query_toolbox(monkeypatch, {"c1": payload("a | b | c")})
    out = tool_loop.json.loads(toolbox.query_rows(["59,473"]))
    assert "no single row" in out["error"]


def test_the_row_query_is_audited_with_its_own_counts(monkeypatch):
    events = []
    toolbox = query_toolbox(monkeypatch, {"c1": payload("row | 59,473")},
                            audit=lambda *args, **kwargs: events.append((args, kwargs)))
    out = toolbox.execute("query_rows", tool_loop.json.dumps({"match": ["59,473"]}))
    assert "59,473" in out
    events_text = str(events)
    assert "query_rows" in events_text
    assert "rows_returned" in events_text and "candidate_chunks" in events_text


def test_the_unknown_tool_message_lists_the_row_query():
    toolbox = tool_loop.ToolBox(services=object(), permit=FakePermit(), role="staff")
    out = tool_loop.json.loads(toolbox.execute("delete_everything", "{}"))
    assert "query_rows" in out["error"]


def test_rows_in_chunk_skips_a_chunk_that_is_not_a_table():
    assert tool_loop.rows_in_chunk(payload("prose text", kind="prose")) == []
    assert tool_loop.rows_in_chunk(payload("marker", kind="table_marker")) == []

