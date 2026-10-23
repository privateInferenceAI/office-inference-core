"""Agent definitions the pack rendered into a deployment.

**A definition is what a deployment can be asked to do, and it is data.** The pack catalog carries it, the
renderer resolves it to `rendered/agent_definitions.json`, and this module is the only place the API reads it.
A deployment with no agentic pack gets an empty list, so the task it would have run is refused by name rather
than defaulting to something the client did not buy.

This module does not run a job. It answers three questions the API and the worker both need: which definitions
exist, how a definition appears on the Do screen, and what budget a job started from it gets.
"""
from __future__ import annotations

import json
from pathlib import Path

DEFAULT_MAX_STEPS = 12
DEFAULT_MAX_SECONDS = 600


def load(path: str | Path) -> list[dict]:
    """Read the rendered definitions, or an empty list when the deployment has none.

    A missing file is not an error. A core-only deployment has no agentic pack and therefore no definitions;
    an empty list is the honest answer, and the API already refuses a task that is not declared.
    """
    rendered = Path(path)
    if not rendered.exists():
        return []
    try:
        data = json.loads(rendered.read_text())
    except (OSError, ValueError):
        return []
    entries = data.get("definitions") if isinstance(data, dict) else None
    return [entry for entry in (entries or []) if isinstance(entry, dict) and entry.get("id")]


def find(definitions: list[dict], task: str) -> dict | None:
    """The definition with this id, or None. Exact match, like the task declaration itself."""
    for definition in definitions or []:
        if definition.get("id") == task:
            return definition
    return None


def as_task(definition: dict) -> dict:
    """The shape the Do screen already renders, from a definition.

    The app was built from `agentic.tasks`; a definition has the same fields plus the runtime's budgets and
    tools. Mapping here keeps the interface unchanged and keeps the runtime's extra fields out of it.
    """
    identifier = str(definition.get("id"))
    return {
        "id": identifier,
        "label": definition.get("label") or identifier,
        "description": definition.get("description", ""),
        "tier": definition.get("role", ""),
        "touches": definition.get("tools") or [],
        "needs_approval": bool(definition.get("needs_approval")),
        "kind": "agent",
    }


def merge_tasks(declared: list[dict], definitions: list[dict]) -> list[dict]:
    """The manifest's tasks plus the pack's definitions, with one entry per id.

    A definition wins over a manifest task with the same id, because the definition is what the runtime can
    actually run. The alternative lets a hand-written task label promise work the deployment does not have.
    """
    by_id: dict[str, dict] = {}
    for entry in declared or []:
        if isinstance(entry, str):
            by_id[entry] = {"id": entry, "label": entry, "description": "", "tier": "",
                            "touches": [], "needs_approval": False}
        elif isinstance(entry, dict) and entry.get("id"):
            by_id[entry["id"]] = dict(entry)
    for definition in definitions or []:
        by_id[definition["id"]] = as_task(definition)
    return list(by_id.values())


def budgets(definition: dict) -> dict:
    """The job budgets a definition implies. The runtime enforces the narrower token and child limits later.

    Attempts here are steps, because every loop iteration is one engine step in decision 0009. The cap is the
    definition's `max_steps`; a definition that does not set one gets the C4 default rather than an unbounded
    loop.
    """
    steps = definition.get("max_steps", DEFAULT_MAX_STEPS)
    seconds = definition.get("max_seconds", DEFAULT_MAX_SECONDS)
    try:
        attempt_budget = max(1, int(steps))
    except (TypeError, ValueError):
        attempt_budget = DEFAULT_MAX_STEPS
    try:
        time_budget_s = max(1, int(seconds))
    except (TypeError, ValueError):
        time_budget_s = DEFAULT_MAX_SECONDS
    return {"attempt_budget": attempt_budget, "time_budget_s": time_budget_s}
