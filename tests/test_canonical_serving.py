"""The canonical serving profile: default-off, and exactly the pinned v2 behavior when on.

The product branch carries two answer paths. The product default must behave as it does today; the canonical
profile must reproduce the files pinned in `deploy/canonical/serving-canonical-v2/`, which produced the v2
evidence (36/38 on one section pass, then 37, 38, 38, 38, 37 on the 38-role suite). These tests are what stops
the two from drifting: they compare the profile against the pin directly where the pin is importable, and they
pin the default path's behavior so a future "improvement" has to be a flag.

The pin is a deployment artifact, not a package. It is loaded by path the same way an operator copies it onto a
box, which is also why the comparison is against the file rather than a copy of its logic in this file.
"""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "services" / "api"))
sys.path.insert(0, str(ROOT / "services" / "retrieval"))
sys.path.insert(0, str(ROOT / "services" / "ingestion"))

import answer_contract  # noqa: E402
import chunker  # noqa: E402
import retriever  # noqa: E402
import tool_loop  # noqa: E402

PIN_TOOL_LOOP = ROOT / "deploy" / "canonical" / "serving-canonical-v2" / "tool_loop.py"

# Every switch the canonical profile forces. Kept in one place so a new switch has one list to join.
EXPERIMENT_SWITCHES = (
    "OI_PRESEARCH_IDENTIFIERS",
    "OI_IDENTIFIER_SINGLE_SHOT",
    "OI_RETRY_REFUSALS",
    "OI_REFUSAL_FALLBACK",
    "OI_LIMIT_FALLBACK",
    "OI_ROW_PRESEED",
    "OI_EXACT_ROW_FALLBACK",
    "OI_DOCUMENT_COMPLETION",
    "OI_DEBUG_LOOP",
)


def load_pin():
    """Load the pinned tool loop from the deployment artifact, the way a deployment applies it."""
    spec = importlib.util.spec_from_file_location("pinned_tool_loop", PIN_TOOL_LOOP)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class FakePermit:
    roots = frozenset({"company"})


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


def make_toolbox():
    return tool_loop.ToolBox(services=object(), permit=FakePermit(), role="staff")


def answer_reply(text):
    return {"content": text, "tool_calls": [], "error": None}


@pytest.fixture()
def canonical(monkeypatch):
    """Activate the canonical profile and leave every switch at its measured value."""
    monkeypatch.setenv("OI_CANONICAL_SERVING", "1")
    for name in EXPERIMENT_SWITCHES:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.delenv("OI_PROMPT_VARIANT", raising=False)
    monkeypatch.delenv("OI_LEXICAL_ARM", raising=False)
    return monkeypatch


# --------------------------------------------------------------------------- the flag itself


def test_the_canonical_profile_is_off_by_default(monkeypatch):
    monkeypatch.delenv("OI_CANONICAL_SERVING", raising=False)
    assert tool_loop.canonical_serving_enabled() is False
    assert retriever.canonical_serving_enabled() is False


def test_the_flag_reads_the_same_truthy_values_everywhere(canonical):
    assert tool_loop.canonical_serving_enabled() is True
    assert retriever.canonical_serving_enabled() is True
    canonical.setenv("OI_CANONICAL_SERVING", "off")
    assert tool_loop.canonical_serving_enabled() is False


# --------------------------------------------------------------------------- the pinned parity


GOLDEN = json.loads((Path(__file__).resolve().parent / "fixtures" / "canonical-v2" / "golden.json").read_text())


def golden_passage(entry):
    return FakePassage(entry["chunk_id"], entry["text"], doc_id=entry["doc_id"], page=entry["page"],
                       kind=entry["kind"], table_id=entry["table_id"], row_index=entry["row_index"])


def test_the_canonical_prompt_is_the_golden_prompt(canonical):
    assert tool_loop.system_prompt() == GOLDEN["prompt"]


def test_the_canonical_renderer_prints_the_golden_evidence(canonical):
    """Prose, focused prose, a table blob and an expansion passage, against the pinned renders."""
    for case in GOLDEN["render_cases"]:
        toolbox = make_toolbox()
        passages = [golden_passage(entry) for entry in case["passages"]]
        assert toolbox.render(passages, focus=case["focus"]) == case["expected"], case["name"]
    expanded = next(case for case in GOLDEN["render_cases"] if case["name"] == "expanded_table")
    assert "(full table):" in expanded["expected"]
    assert "table truncated" not in expanded["expected"]


def test_the_canonical_caps_are_the_golden_caps(canonical):
    canonical.setenv("OI_TOOL_MAX_STEPS", "12")
    canonical.setenv("OI_TOOL_MAX_CALLS", "20")
    assert list(tool_loop.limits()) == GOLDEN["limits"] == [6, 10]


@pytest.mark.skipif(not PIN_TOOL_LOOP.exists(),
                    reason="the deployment pin is removed once the product branch reproduces it")
def test_the_pin_file_still_matches_the_golden_fixture():
    """Guards the fixture: the golden values must be the pin's values for as long as the pin exists."""
    pin = load_pin()
    assert pin.system_prompt() == GOLDEN["prompt"]
    assert list(pin.limits()) == GOLDEN["limits"]
    for case in GOLDEN["render_cases"]:
        pinned = pin.ToolBox(services=object(), permit=FakePermit(), role="staff")
        passages = [golden_passage(entry) for entry in case["passages"]]
        assert pinned.render(passages, focus=case["focus"]) == case["expected"], case["name"]
    pin_retriever = (PIN_TOOL_LOOP.parent / "retriever.py").read_text()
    assert "def lexical_arm_enabled" not in pin_retriever


# --------------------------------------------------------------------------- switches


def test_every_experimental_switch_is_forced_off_in_the_canonical_profile(canonical):
    for name in EXPERIMENT_SWITCHES:
        canonical.setenv(name, "1")
    assert tool_loop.presearch_identifiers_enabled() is False
    assert tool_loop.identifier_single_shot_enabled() is False
    assert tool_loop.refusal_retry_enabled() is False
    assert tool_loop.refusal_fallback_enabled() is False
    assert tool_loop.limit_fallback_enabled() is False
    assert tool_loop.row_preseed_enabled() is False
    assert tool_loop.exact_row_fallback_enabled() is False
    assert tool_loop._document_completion_enabled() is False
    assert tool_loop.debug_loop_enabled() is False
    # ... and the one that stays on, because the pinned candidate offers it.
    assert tool_loop.read_passages_enabled() is True


def test_the_experimental_switches_still_read_the_environment_by_default(monkeypatch):
    monkeypatch.delenv("OI_CANONICAL_SERVING", raising=False)
    monkeypatch.setenv("OI_PRESEARCH_IDENTIFIERS", "1")
    assert tool_loop.presearch_identifiers_enabled() is True
    monkeypatch.setenv("OI_PRESEARCH_IDENTIFIERS", "0")
    assert tool_loop.presearch_identifiers_enabled() is False
    monkeypatch.delenv("OI_TOOL_READ_PASSAGES", raising=False)
    assert tool_loop.read_passages_enabled() is True


def test_the_corruption_guard_is_off_by_default_and_off_in_the_canonical_profile(canonical):
    canonical.setenv("OI_CORRUPTION_GUARD", "1")
    assert tool_loop.corruption_guard_enabled() is False
    canonical.delenv("OI_CANONICAL_SERVING")
    assert tool_loop.corruption_guard_enabled() is True


def test_the_canonical_retriever_is_dense_only(canonical):
    canonical.setenv("OI_LEXICAL_ARM", "1")
    assert retriever.lexical_arm_enabled() is False
    canonical.delenv("OI_CANONICAL_SERVING")
    canonical.delenv("OI_LEXICAL_ARM")
    assert retriever.lexical_arm_enabled() is True
    # The pin removed the arm outright; a product deployment can also switch it off by environment.
    canonical.setenv("OI_LEXICAL_ARM", "0")
    assert retriever.lexical_arm_enabled() is False


# --------------------------------------------------------------------------- the loop paths


def test_the_product_default_seeds_the_exact_phrase_and_the_canonical_profile_does_not(monkeypatch):
    monkeypatch.delenv("OI_CANONICAL_SERVING", raising=False)
    toolbox = make_toolbox()
    toolbox.add(FakePassage("c1", "the standard mileage rate is 67 cents per mile"))
    calls = []
    toolbox.exact_phrase_for_question = lambda question: (calls.append(question), "")[1]
    replies = iter([answer_reply("The standard mileage rate is 67 cents per mile [1].")])
    tool_loop.run_loop(lambda messages, tools: next(replies), toolbox, "What is the mileage rate?")
    assert calls == ["What is the mileage rate?"]

    monkeypatch.setenv("OI_CANONICAL_SERVING", "1")
    toolbox = make_toolbox()
    toolbox.add(FakePassage("c1", "the standard mileage rate is 67 cents per mile"))
    calls = []
    toolbox.exact_phrase_for_question = lambda question: (calls.append(question), "")[1]
    replies = iter([answer_reply("The standard mileage rate is 67 cents per mile [1].")])
    tool_loop.run_loop(lambda messages, tools: next(replies), toolbox, "What is the mileage rate?")
    assert calls == []


def test_the_numeric_contract_runs_only_in_the_canonical_profile(monkeypatch):
    """The pinned candidate checks numeric grounding on the final answer; the product default does not."""
    toolbox = make_toolbox()
    toolbox.add(FakePassage("c1", "the standard mileage rate is 67 cents per mile"))
    replies = iter([answer_reply("The standard mileage rate is 67 cents per mile [1].")])

    calls = []
    monkeypatch.delenv("OI_CANONICAL_SERVING", raising=False)
    monkeypatch.setattr(answer_contract, "numeric_violation",
                        lambda *args: (calls.append(args), None)[1])
    result = tool_loop.run_loop(lambda messages, tools: next(replies), toolbox, "What is the mileage rate?")
    assert calls == [] and result["answer"] is not None

    calls = []
    outcomes = iter(["numeric_grounding", None])
    monkeypatch.setenv("OI_CANONICAL_SERVING", "1")
    monkeypatch.setattr(answer_contract, "numeric_violation",
                        lambda *args: (calls.append(args), next(outcomes))[1])
    replies = iter([answer_reply("The standard mileage rate is 67 cents per mile [1]."),
                    answer_reply("The standard mileage rate is 67 cents per mile [1].")])
    result = tool_loop.run_loop(lambda messages, tools: next(replies), toolbox, "What is the mileage rate?")
    assert len(calls) == 2
    assert result["answer"] is not None and result["corrections"] == 1


def test_the_canonical_profile_expands_a_multi_table_page_and_the_default_does_not(canonical, monkeypatch):
    """The pinned candidate replaced both table chunks with one whole-table passage per table."""
    result_passages = [
        FakePassage("company/tables.md", "table one row", kind="table", table_id="t1", page=1),
        FakePassage("company/tables.md", "table two row", kind="table", table_id="t2", page=1),
    ]

    class Result:
        passages = result_passages

    monkeypatch.setattr(retriever, "search", lambda services, query, **kwargs: Result())
    payloads = {
        "t1": [{"text": "Table one\nColumns: a | b\nrow 1 | 1", "doc_id": "company/tables.md",
                "acl": "company", "page_start": 1, "page_end": 1, "row_start": 1, "ordinal": 0}],
        "t2": [{"text": "Table two\nColumns: c | d\nrow 2 | 2", "doc_id": "company/tables.md",
                "acl": "company", "page_start": 1, "page_end": 1, "row_start": 1, "ordinal": 0}],
    }
    toolbox = make_toolbox()
    monkeypatch.setattr(toolbox, "_table_points", lambda table_id: payloads[table_id])
    rendered = toolbox.search_documents("tables")
    assert "(full table):" in rendered and "Table one" in rendered and "Table two" in rendered

    canonical.delenv("OI_CANONICAL_SERVING")
    toolbox = make_toolbox()
    monkeypatch.setattr(toolbox, "_table_points", lambda table_id: payloads[table_id])
    rendered = toolbox.search_documents("tables")
    assert "(full table):" not in rendered


# --------------------------------------------------------------------------- the live worker's chunker


def test_the_live_chunker_version_is_not_the_bundles(canonical):
    """The bundle names its own chunker in the read contract; renaming the live worker broke the main stack."""
    assert chunker.CHUNKER_VERSION == "chunker-v5"
