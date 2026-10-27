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

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "services" / "api"))
sys.path.insert(0, str(ROOT / "services" / "retrieval"))
sys.path.insert(0, str(ROOT / "services" / "ingestion"))

import tool_loop  # noqa: E402


class FakePassage:
    def __init__(self, chunk_id, text, doc_id="company/policy.doc", page=1, kind="prose",
                 table_id=None, row_index=None):
        self.chunk_id = chunk_id
        self.text = text
        self.doc_id = doc_id
        self.page_start = page
        self.kind = kind
        self.table_id = table_id
        self.row_index = row_index


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


def test_a_provider_error_returns_a_reason_not_an_exception():
    toolbox = make_toolbox()
    result = tool_loop.run_loop(lambda messages, tools: {"error": "throttled"}, toolbox, "q")
    assert result["answer"] is None and result["reason"] == "throttled"


def test_the_tool_schemas_match_the_executor():
    names = [schema["function"]["name"] for schema in tool_loop.TOOLS]
    assert names == ["search_documents", "read_passages", "query_rows"]


def test_the_prompt_carries_the_honesty_rule_for_absent_events():
    # The 2026-10-18 tool runs on the weaker model answered two unanswerable questions from adjacent facts.
    # The rule that stops a related figure being relabelled as the asked-for one lives in the system prompt.
    prompt = tool_loop.system_prompt()
    assert "specific event, period, meeting or document" in prompt
    assert "Do not infer that a related figure" in prompt


def test_the_thoroughness_rule_is_in_the_prompt():
    # Dropping this rule in the Phase B rewrite halved the loop's duration and cost the suite seven points.
    prompt = tool_loop.system_prompt()
    assert "table row is a valid answer" in prompt
    assert "Check every passage before concluding" in prompt


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


def test_the_row_query_is_always_offered():
    names = [schema["function"]["name"] for schema in tool_loop.active_tools()]
    assert "query_rows" in names


def test_the_prompt_always_names_the_tools_it_offers():
    prompt = tool_loop.system_prompt()
    for name in ("search_documents", "read_passages", "query_rows"):
        assert name in prompt
    assert "exact table rows" in prompt
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



def test_row_evidence_gets_an_allowance_past_the_search_ceiling(monkeypatch):
    """A search can fill the evidence ceiling before the question asks for a row.

    The first five-run treatment lost one question in all five runs to the old behaviour: the row matched, the
    namespace was full, the tool rendered nothing, and the model read that as absence. Rows now get a bounded
    allowance past the ceiling so they can still be numbered and cited.
    """
    toolbox = tool_loop.ToolBox(services=object(), permit=FakePermit(), role="staff", max_evidence=2)
    toolbox._candidate_chunks = lambda values, doc_id="": [("c1", "company/census.csv")]
    install_payloads(monkeypatch, {"c1": payload("value | 59,473")})
    toolbox.add(FakePassage("s1", "search one"))
    toolbox.add(FakePassage("s2", "search two"))
    out = toolbox.query_rows(["59,473"])
    assert "59,473" in out, "a row past the search ceiling is still returned and citable"
    assert len(toolbox.evidence) == 3


def test_a_full_evidence_list_answers_the_row_query_with_an_explanation(monkeypatch):
    """The allowance is bounded, and a matched row that cannot be numbered says why instead of rendering nothing.

    A header with no rows reads as "no such row"; the error says the evidence list is full, which is a different
    fact and one the model can act on.
    """
    payloads = {}
    candidates = []
    for index in range(8):
        key = f"c{index}"
        payloads[key] = payload(f"row {index} | 59,473", chunk_id=key, row_start=index)
        candidates.append((key, "company/census.csv"))
    payloads["c8"] = payload("row eleven | 77,777", chunk_id="c8", row_start=11)
    toolbox = tool_loop.ToolBox(services=object(), permit=FakePermit(), role="staff", max_evidence=1)
    toolbox._candidate_chunks = lambda values, doc_id="": list(candidates) if "59,473" in values \
        else [("c8", "company/census.csv")]
    install_payloads(monkeypatch, payloads)
    toolbox.add(FakePassage("s1", "one search result fills the plain ceiling of one"))
    first = toolbox.query_rows(["59,473"], limit=8)
    assert "row 7" in first, "eight rows fill the allowance past the ceiling"
    assert len(toolbox.evidence) == tool_loop.QUERY_EXTRA_EVIDENCE + 1
    out = tool_loop.json.loads(toolbox.query_rows(["77,777"]))
    assert "evidence list is full" in out["error"]
    assert toolbox._last_facts["rows_matched"] == 1



def test_the_loop_carries_the_gateways_attribution_into_the_answer():
    """A failover answers from another model group; the loop has to pass that fact to the caller or the
    answer credits the primary model."""
    toolbox = make_toolbox()
    toolbox.add(FakePassage("c1", "the standard mileage rate is 67 cents per mile"))
    replies = iter([
        {"content": "The standard mileage rate is 67 cents per mile [1].", "tool_calls": [], "error": None,
         "attribution": {"model_group": "analyst-fallback",
                         "model": "us.anthropic.claude-sonnet-4-5-20250929-v1:0",
                         "fallbacks_attempted": 1}},
    ])
    result = tool_loop.run_loop(lambda messages, tools: next(replies), toolbox, "What is the mileage rate?")
    assert result["answer"].startswith("The standard mileage rate")
    assert result["attribution"]["model_group"] == "analyst-fallback"
    assert result["attribution"]["fallbacks_attempted"] == 1



def test_the_exact_query_is_parameterised_and_acl_filtered():
    sql, params = tool_loop.exact_query("Current Termination Date", "", ["company"])
    assert "acl = any(%(roots)s)" in sql
    assert "position(%(needle)s" in sql
    assert "%" not in params["needle"]
    assert params["needle"] == "current termination date"
    sql_with_doc, params_with_doc = tool_loop.exact_query("visitor badge", "company/doc.md", ["company"])
    assert "doc_id = %(doc)s" in sql_with_doc
    assert params_with_doc["doc"] == "company/doc.md"


def test_the_exact_excerpt_is_centred_on_the_match():
    text = ("A" * 600) + " the Current Termination Date is August 27, 2029 " + ("B" * 600)
    excerpt = tool_loop.exact_excerpt(text, "Current Termination Date")
    assert "Current Termination Date is August 27, 2029" in excerpt
    assert excerpt.startswith("...") and excerpt.endswith("...")


def test_exact_search_rejects_a_short_phrase():
    toolbox = make_toolbox()
    payload = json.loads(toolbox.search_exact("x"))
    assert "at least two characters" in payload["error"]


def test_exact_search_renders_a_centred_excerpt_and_numbers_the_evidence():
    toolbox = make_toolbox()
    toolbox._exact_matches = lambda phrase, doc_id="", limit=6: ["chunk-1"]
    import retriever as real_retriever
    original = real_retriever._fetch_payloads
    real_retriever._fetch_payloads = lambda services, wanted, roots: {
        "chunk-1": {"chunk_id": "chunk-1", "doc_id": "company/doc.md", "acl": "company",
                    "text": ("filler " * 100) + "visitor badges are valid for 24 hours maximum " + ("tail " * 100),
                    "page_start": 3, "page_end": 3, "kind": "prose"}}
    try:
        out = toolbox.search_exact("24 hours maximum")
    finally:
        real_retriever._fetch_payloads = original
    assert "Passages containing" in out
    assert "24 hours maximum" in out
    assert "company/doc.md p3" in out
    assert len(toolbox.evidence) == 1


def test_exact_search_points_at_the_row_query_when_the_phrase_is_too_broad():
    toolbox = make_toolbox()
    toolbox._exact_matches = lambda phrase, doc_id="", limit=6: ["chunk-1"]
    toolbox._exact_count = lambda phrase, doc_id="": 500
    payload = json.loads(toolbox.search_exact("net sales"))
    assert "does not identify a heading" in payload["note"]
    assert "query_rows" in payload["note"]



def test_a_capped_loop_answers_from_the_evidence_it_gathered():
    toolbox = make_toolbox()

    def fake_search(query):
        number = toolbox.add(FakePassage("c1", "the answer row"))
        return "[" + str(number) + "] company/doc.md: the answer row"

    toolbox.search_documents = fake_search
    seen_tools = []

    def model(messages, tools):
        seen_tools.append(list(tools))
        if tools:
            return tool_reply("search_documents", json.dumps({"query": "again"}))
        return answer_reply("The answer is here [1].")

    result = tool_loop.run_loop(model, toolbox, "q", max_steps=2)
    assert result["answer"] is not None
    assert result["synthesis"] == 1
    assert seen_tools[-1] == [], "the synthesis turn must not offer tools"


def test_a_failed_synthesis_keeps_the_old_none_result():
    toolbox = make_toolbox()

    def fake_search(query):
        number = toolbox.add(FakePassage("c1", "the answer row"))
        return "[" + str(number) + "] company/doc.md: the answer row"

    toolbox.search_documents = fake_search

    def model(messages, tools):
        if tools:
            return tool_reply("search_documents", json.dumps({"query": "again"}))
        return answer_reply("The figure is 999,999 [1].")

    result = tool_loop.run_loop(model, toolbox, "q", max_steps=2)
    assert result["answer"] is None
    assert result["synthesis"] == 1
    assert result["reason"] in ("tool_step_limit", "tool_budget", "tool_error")


def test_a_synthesized_refusal_falls_back_to_the_old_result():
    toolbox = make_toolbox()

    def fake_search(query):
        number = toolbox.add(FakePassage("c1", "the answer row"))
        return "[" + str(number) + "] company/doc.md: the answer row"

    toolbox.search_documents = fake_search

    def model(messages, tools):
        if tools:
            return tool_reply("search_documents", json.dumps({"query": "again"}))
        return answer_reply("I could not find that in the available documents.")

    result = tool_loop.run_loop(model, toolbox, "q", max_steps=2)
    assert result["answer"] is None
    assert result["synthesis"] == 1


# --------------------------------------------------------------------------- centred evidence snippets
#
# The visitor-badge measurement: the manual states the badge limit in a table row whose figure sat past the
# seven-hundred-character slice, so the model answered from the weaker "end of visit" line. These tests hold
# the deterministic fix: the window follows the query's terms, and nothing else about the render changes.

BADGE_ROW = ("Visitor | White | Guests, interviewees, customer reps | "
             "Expires at the end of the visit day, maximum 24 hours | Escort required")
BADGE_FILLER = "The manual covers facilities and access procedures. "


def badge_text():
    return (BADGE_FILLER * 45) + BADGE_ROW


def snippet_body(snippet):
    body = snippet[3:] if snippet.startswith("...") else snippet
    return body[:-3] if body.endswith("...") else body


def test_a_figure_at_the_end_of_a_long_passage_is_visible_when_the_query_hits_it():
    snippet = tool_loop.centred_snippet(badge_text(), "How long is a visitor badge valid?",
                                        tool_loop.SNIPPET_CHARS)
    assert "maximum 24 hours" in snippet
    assert snippet.startswith("..."), "the window starts after the beginning of the passage"
    assert len(snippet_body(snippet)) == tool_loop.SNIPPET_CHARS


def test_a_multiword_focus_term_wins_over_an_earlier_single_word():
    text = "badge " + (BADGE_FILLER * 30) + "visitor badge is valid for 24 hours maximum"
    snippet = tool_loop.centred_snippet(text, ["visitor badge", "badge"], tool_loop.SNIPPET_CHARS)
    assert "24 hours maximum" in snippet


def test_without_a_focus_the_snippet_is_the_start_slice():
    text = "alpha " * 300
    start = text[:tool_loop.SNIPPET_CHARS]
    assert tool_loop.centred_snippet(text, "", tool_loop.SNIPPET_CHARS) == start
    assert tool_loop.centred_snippet(text, "visitor badge", tool_loop.SNIPPET_CHARS) == start
    assert not start.startswith("...")


def test_render_without_a_focus_keeps_the_exact_old_line():
    toolbox = make_toolbox()
    text = "alpha " * 300
    rendered = toolbox.render([FakePassage("c1", text)])
    assert rendered == ("[1] company/policy.doc p1 kind=prose: "
                        + " ".join(text.split())[:tool_loop.SNIPPET_CHARS])


def test_a_short_text_is_unchanged_even_with_a_focus():
    snippet = tool_loop.centred_snippet("Visitor   badges\nare valid for 24 hours maximum.", "visitor badge",
                                        tool_loop.SNIPPET_CHARS)
    assert snippet == "Visitor badges are valid for 24 hours maximum."
    assert "..." not in snippet


def test_the_snippet_normalises_whitespace_before_matching_and_cutting():
    text = ("filler\n\n\t" * 200) + "Visitor   badges\tare valid for\n24 hours maximum"
    snippet = tool_loop.centred_snippet(text, "24 hours", 160)
    assert "24 hours maximum" in snippet
    assert "\n" not in snippet and "\t" not in snippet and "  " not in snippet
    assert snippet.startswith("...") and not snippet.endswith("...")


def test_a_focus_does_not_change_evidence_numbering_or_line_format():
    toolbox = make_toolbox()
    long_text = (BADGE_FILLER * 45) + BADGE_ROW
    rendered = toolbox.render([FakePassage("c1", long_text), FakePassage("c2", "second passage")],
                              focus="visitor badge hours")
    assert rendered.startswith("[1] company/policy.doc p1 kind=prose: ")
    assert "[2] company/policy.doc p1 kind=prose: second passage" in rendered
    assert "maximum 24 hours" in rendered
    again = toolbox.render([FakePassage("c1", long_text)], focus="24 hours")
    assert again.startswith("[1] "), "a repeated passage keeps its number"
    assert len(toolbox.evidence) == 2


def test_search_documents_centres_the_snippet_on_its_query(monkeypatch):
    toolbox = make_toolbox()
    passage = FakePassage("c1", badge_text(), doc_id="company/facilities.md", page=7)
    seen = []

    class Result:
        passages = [passage]

    import retriever as real_retriever

    def fake_search(services, query, **kwargs):
        seen.append(query)
        return Result()

    monkeypatch.setattr(real_retriever, "search", fake_search)
    out = toolbox.search_documents("visitor badge hours")
    assert seen == ["visitor badge hours"]
    assert "maximum 24 hours" in out
    assert out.startswith('Search results for "visitor badge hours":')



def test_a_period_row_is_a_table_header_and_a_data_row_is_not():
    assert tool_loop._looks_like_table_header("Three Months Ended June 30, | 2026 | 2025")
    assert tool_loop._looks_like_table_header("2026 | 2025 | 2026 | 2025")
    assert not tool_loop._looks_like_table_header("Total revenues | $ | 4,934,098 | $ | 4,136,543")


def test_table_prefix_pairs_a_caption_with_its_columns():
    rows = [
        (10, "CONDENSED CONSOLIDATED STATEMENTS OF OPERATIONS"),
        (11, "Three Months Ended June 30, | Six Months Ended June 30,"),
        (12, "2026 | 2025 | 2026 | 2025"),
        (13, "Total revenues | 4,934,098 | 4,136,543 | 8,947,032 | 8,183,283"),
    ]
    prefix = tool_loop._table_prefix_from_rows(rows, 13)
    assert prefix.startswith("Table: CONDENSED CONSOLIDATED STATEMENTS OF OPERATIONS.")
    assert "Three Months Ended June 30" in prefix and "2026 | 2025" in prefix


def test_a_whole_table_blob_carries_its_columns_into_the_rendered_evidence():
    toolbox = make_toolbox()
    blob = ("CONDENSED CONSOLIDATED STATEMENTS OF OPERATIONS\n"
            "Three Months Ended June 30, | Six Months Ended June 30,\n"
            "2026 | 2025 | 2026 | 2025\n"
            "Total revenues | 4,934,098 | 4,136,543 | 8,947,032 | 8,183,283")
    passage = FakePassage("c1", blob, kind="table", table_id="t1")
    rendered = toolbox.render([passage])
    assert "Columns: Three Months Ended June 30" in rendered
    assert "4,934,098" in rendered
    assert "Table: CONDENSED CONSOLIDATED STATEMENTS OF OPERATIONS." in rendered


def test_a_table_row_uses_its_sibling_rows_for_the_column_header():
    toolbox = make_toolbox()
    toolbox._table_sibling_rows = lambda table_id, row_index: [
        (10, "Three Months Ended June 30, | Six Months Ended June 30,"),
        (11, "2026 | 2025 | 2026 | 2025"),
    ]
    passage = FakePassage("c1", "Total revenues | 4,934,098 | 4,136,543", kind="table_row",
                          table_id="t1", row_index=12)
    rendered = toolbox.render([passage])
    assert "Columns: Three Months Ended June 30" in rendered


def test_a_row_that_already_carries_its_context_is_not_prefixed_twice():
    """A row re-ingested under chunker-v5 begins with its own columns and caption. The renderer still
    supports the lookup because rows indexed before v5 have no prefix, and adding a second one to a
    prefixed row would read "Columns: Columns: ..." -- and the prefixed row can itself pass the header
    test, so the duplicate would be built rather than merely repeated."""
    toolbox = make_toolbox()
    toolbox._table_sibling_rows = lambda table_id, row_index: [
        (10, "Three Months Ended June 30, | Six Months Ended June 30,"),
        (11, "2026 | 2025 | 2026 | 2025"),
    ]
    text = ("Columns: Three Months Ended June 30, | Six Months Ended June 30, / "
            "2026 | 2025 | 2026 | 2025. Total revenues | 4,934,098 | 4,136,543")
    passage = FakePassage("c1", text, kind="table_row", table_id="t1", row_index=12)
    rendered = toolbox.render([passage])
    assert rendered.count("Columns:") == 1, rendered
    assert "4,934,098" in rendered


def test_a_whole_table_blob_that_already_carries_its_context_is_not_prefixed_twice():
    """Same guard on the blob path. The whole-table text is unchanged by v5, so a blob never gains the
    prefix in the index; the guard is here because the two paths share the method and a future change
    that put the caption into the blob would otherwise double it."""
    toolbox = make_toolbox()
    blob = ("Table: CONDENSED CONSOLIDATED STATEMENTS OF OPERATIONS. Columns: Three Months Ended June 30,"
            " | Six Months Ended June 30, / 2026 | 2025 | 2026 | 2025. Total revenues | 4,934,098")
    passage = FakePassage("c1", blob, kind="table", table_id="t1")
    rendered = toolbox.render([passage])
    assert rendered.count("Columns:") == 1, rendered


def test_the_answer_path_swallows_an_audit_failure_but_the_strict_toolbox_does_not():
    """Two callers, two contracts. The answer path must not lose an answer to a record failure; the runtime
    must not continue a tool call that left no row."""
    def broken_audit(*args, **kwargs):
        raise RuntimeError("the chain is full")

    forgiving = tool_loop.ToolBox(services=object(), permit=FakePermit(), role="staff",
                                  audit=broken_audit)
    assert "unknown tool" in forgiving.execute("not_a_tool", "{}")

    strict = tool_loop.ToolBox(services=object(), permit=FakePermit(), role="staff",
                               audit=broken_audit, strict_audit=True)
    with pytest.raises(tool_loop.ToolAuditError):
        strict.execute("not_a_tool", "{}")


def test_the_delegate_tool_is_offered_only_to_a_definition_with_child_tools():
    """The answer path must never see the helper; only the runtime, and only when a pack declares it."""
    answer_names = {tool["function"]["name"] for tool in tool_loop.active_tools()}
    assert "delegate_research" not in answer_names
    without_child = {tool["function"]["name"] for tool in tool_loop.agent_tools({})}
    assert "delegate_research" not in without_child
    with_child = {tool["function"]["name"]
                  for tool in tool_loop.agent_tools({"child_tools": ["search_documents"]})}
    assert "delegate_research" in with_child
    without_workspace = {tool["function"]["name"] for tool in tool_loop.agent_tools({})}
    assert "workspace_run" not in without_workspace
    with_workspace = {tool["function"]["name"]
                      for tool in tool_loop.agent_tools({"workspace": True})}
    assert {"workspace_write", "workspace_read", "workspace_list", "workspace_run"} <= with_workspace
    assert "web_fetch" not in with_workspace
    with_web = {tool["function"]["name"] for tool in tool_loop.agent_tools({"web": True})}
    assert "web_fetch" in with_web
    assert "send_email" not in with_web
    with_connector = {tool["function"]["name"] for tool in tool_loop.agent_tools({"connector": True})}
    assert "send_email" in with_connector


def test_read_passages_can_be_disabled_for_a_bundle_serving_deployment(monkeypatch):
    """The accepted lab configuration did not offer read_passages.

    The product branch added the tool for the agent runtime. A canonical deployment needs a way to serve the
    measured answer path without silently using a different tool list.
    """
    names = lambda: [(tool.get("function") or {}).get("name") for tool in tool_loop.active_tools()]
    monkeypatch.delenv("OI_TOOL_READ_PASSAGES", raising=False)
    assert "read_passages" in names()

    monkeypatch.setenv("OI_TOOL_READ_PASSAGES", "0")
    assert "read_passages" not in names()
    assert "query_rows" in names()
    assert tool_loop.read_passages_enabled() is False

    monkeypatch.setenv("OI_TOOL_READ_PASSAGES", "1")
    assert "read_passages" in names()


def test_quoted_identifiers_are_found_in_order():
    question = ("Invoice INV-2025-001 totals 13,512.00 USD and invoice INV-2025-002 totals 11,297.94 USD.")
    assert tool_loop.quoted_identifiers(question) == ["INV-2025-001", "INV-2025-002"]
    assert tool_loop.quoted_identifiers("no identifiers here") == []


def test_the_loop_limits_can_be_raised_by_a_deployment(monkeypatch):
    monkeypatch.delenv("OI_TOOL_MAX_STEPS", raising=False)
    monkeypatch.delenv("OI_TOOL_MAX_CALLS", raising=False)
    assert tool_loop.limits() == (6, 10)

    monkeypatch.setenv("OI_TOOL_MAX_STEPS", "8")
    monkeypatch.setenv("OI_TOOL_MAX_CALLS", "12")
    assert tool_loop.limits() == (8, 12)

    monkeypatch.setenv("OI_TOOL_MAX_STEPS", "not-a-number")
    assert tool_loop.limits()[0] == 6
