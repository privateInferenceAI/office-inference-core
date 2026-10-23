"""The agent definitions the pack rendered.

**What these tests guard.** A definition is the difference between a deployment that can run a task and one that
only says it can. The pack carries it, the renderer resolves it, and the API must list it, budget it and refuse a
deployment that has none. These tests check the data path up to the API's task list; the dispatcher test lands
with the worker.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for extra in (ROOT / "services", ROOT / "services" / "api"):
    if str(extra) not in sys.path:
        sys.path.insert(0, str(extra))

import definitions  # noqa: E402

BRIEF = {
    "id": "research_brief",
    "label": "Research and brief",
    "description": "Research and cite.",
    "mode": "task",
    "role": "analyst",
    "tools": ["search_documents", "read_passages"],
    "child_tools": ["search_documents"],
    "max_steps": 12,
    "max_seconds": 600,
    "needs_approval": True,
}


def test_a_missing_rendered_file_is_an_empty_list():
    """A core-only deployment has no agentic pack, and an empty list is the honest answer."""
    assert definitions.load("/does/not/exist/agent_definitions.json") == []


def test_load_reads_only_entries_with_an_id(tmp_path):
    path = tmp_path / "agent_definitions.json"
    path.write_text(json.dumps({"runtime_count": 1, "definitions": [BRIEF, {"label": "no id"}]}))
    loaded = definitions.load(path)
    assert [entry["id"] for entry in loaded] == ["research_brief"]


def test_a_definition_appears_as_a_task_with_its_role_and_tools():
    task = definitions.as_task(BRIEF)
    assert task == {
        "id": "research_brief",
        "label": "Research and brief",
        "description": "Research and cite.",
        "tier": "analyst",
        "touches": ["search_documents", "read_passages"],
        "needs_approval": True,
        "kind": "agent",
    }


def test_merge_keeps_manifest_tasks_and_lets_a_definition_win():
    declared = [
        {"id": "draft", "label": "Draft", "tier": "front_desk"},
        {"id": "research_brief", "label": "Stale manifest label", "tier": "reader"},
    ]
    merged = {entry["id"]: entry for entry in definitions.merge_tasks(declared, [BRIEF])}
    assert set(merged) == {"draft", "research_brief"}
    assert merged["research_brief"]["label"] == "Research and brief"
    assert merged["research_brief"]["kind"] == "agent"
    assert merged["draft"]["label"] == "Draft"


def test_a_definition_supplies_the_attempt_and_time_budget():
    budgets = definitions.budgets(BRIEF)
    assert budgets == {"attempt_budget": 12, "time_budget_s": 600}


def test_a_malformed_definition_falls_back_to_the_default_budget():
    budgets = definitions.budgets({"id": "x", "max_steps": "many", "max_seconds": None})
    assert budgets == {"attempt_budget": 12, "time_budget_s": 600}


def test_the_api_task_list_includes_a_pack_definition(api_app):
    """AG-DEFS-001's visible half: the Do screen is built from this list, and the definition must appear with
    the role and tools the pack declared."""
    import app as app_module  # noqa: PLC0415

    app_module.STATE["manifest"] = {"agentic": {"tasks": [{"id": "draft", "label": "Draft"}]}}
    app_module.STATE["agent_definitions"] = [BRIEF]
    tasks = {task["id"]: task for task in app_module._tasks_this_deployment_declares()}
    assert set(tasks) == {"draft", "research_brief"}
    assert tasks["research_brief"]["tier"] == "analyst"
    assert tasks["research_brief"]["touches"] == ["search_documents", "read_passages"]
