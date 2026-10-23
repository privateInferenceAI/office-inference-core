"""The agent runtime core: one model turn per engine step, tools in the turn, a verification finish."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for extra in (ROOT / "services", ROOT / "services" / "api"):
    if str(extra) not in sys.path:
        sys.path.insert(0, str(extra))

import agent_runtime  # noqa: E402
import jobs  # noqa: E402


DEFINITION = {"id": "research_brief", "description": "Research the question and answer it with cites.",
              "role": "analyst", "max_steps": 12, "max_seconds": 600}


class FakeModel:
    def __init__(self, replies):
        self.replies = list(replies)
        self.calls = []

    def __call__(self, messages, tools):
        self.calls.append(list(messages))
        if not self.replies:
            return {"content": "done", "tool_calls": [], "error": None}
        return self.replies.pop(0)


class FakeToolbox:
    def __init__(self, result='{"rows": []}'):
        self.result = result
        self.calls = []

    def execute(self, name, arguments):
        self.calls.append((name, arguments))
        return self.result


def make_job(context, *, attempt_budget=None):
    job = jobs.new_job(deployment_id="oi-test", subject="dana", actor_role="staff",
                       roots={"company"}, credential_sha256="a" * 64,
                       task="research_brief", input="What is the answer?", data_class="internal",
                       attempt_budget=attempt_budget or agent_runtime.AgentPlan(context).attempt_budget,
                       time_budget_s=120)
    store = jobs.MemoryStore()
    store.put(job)
    return job, store


def context(model, toolbox, *, verify=None, max_steps=12, max_messages=24):
    return agent_runtime.RuntimeContext(
        definition=DEFINITION, call_model=model, toolbox=toolbox, tools=[],
        verify=verify, max_steps=max_steps, max_messages=max_messages)


def test_one_model_turn_is_one_engine_step_and_the_tool_runs_in_that_turn():
    model = FakeModel([
        {"content": "", "tool_calls": [{"id": "call-1", "name": "search_documents",
                                         "arguments": '{"query": "answer"}'}], "error": None},
        {"content": "The answer is 42 [1].", "tool_calls": [], "error": None},
    ])
    toolbox = FakeToolbox()
    ctx = context(model, toolbox, verify=lambda answer, evidence: None if "42" in answer else "no cite")
    job, store = make_job(ctx)

    result = agent_runtime.run(store, job, ctx)

    assert result.job.state == jobs.SUCCEEDED
    assert [step.name for step in result.job.steps] == ["turn-1", "turn-2", "finish"]
    assert result.job.result["answer"] == "The answer is 42 [1]."
    assert toolbox.calls == [("search_documents", '{"query": "answer"}')]
    assert len(model.calls) == 2
    # The second turn sees the first turn's assistant/tool messages, rebuilt from the step rows.
    second = model.calls[1]
    assert any(message.get("role") == "tool" and "rows" in message.get("content", "")
               for message in second)


def test_a_turn_that_raises_a_model_error_fails_the_job_with_a_typed_cause():
    model = FakeModel([{"content": "", "tool_calls": [], "error": "gateway_unavailable"}])
    ctx = context(model, FakeToolbox())
    job, store = make_job(ctx)
    result = agent_runtime.run(store, job, ctx)
    assert result.job.state == jobs.FAILED
    assert result.job.outcome_reason == "gateway_unavailable"


def test_verification_failure_fails_the_job_and_records_the_finish_step():
    model = FakeModel([{"content": "I think it is around 40.", "tool_calls": [], "error": None}])
    ctx = context(model, FakeToolbox(),
                  verify=lambda answer, evidence: "the answer does not cite a source")
    job, store = make_job(ctx)
    result = agent_runtime.run(store, job, ctx)
    assert result.job.state == jobs.FAILED
    assert result.job.outcome_reason == "verification"
    assert [step.name for step in result.job.steps] == ["turn-1", "finish"]
    assert result.job.steps[-1].state == jobs.FAILED


def test_the_loop_stops_at_its_step_cap_and_fails_with_no_answer():
    model = FakeModel([
        {"content": "", "tool_calls": [{"id": "c", "name": "search_documents", "arguments": "{}"}],
         "error": None},
    ])
    ctx = context(model, FakeToolbox(), max_steps=1)
    job, store = make_job(ctx)
    result = agent_runtime.run(store, job, ctx)
    assert result.job.state == jobs.FAILED
    assert result.job.outcome_reason == "no_answer"
    assert [step.name for step in result.job.steps] == ["turn-1", "finish"]


def test_a_resumed_plan_reads_the_last_turn_outcome_from_the_record():
    """A new process has no `context.done`; the recorded turn output must still send it to finish."""
    ctx = context(FakeModel([]), FakeToolbox())
    job, store = make_job(ctx)
    job.steps.append(jobs.Step(step_id="turn-1-id", job_id=job.job_id, attempt=1,
                               name="turn-1", state=jobs.SUCCEEDED))
    store.put(job)
    store.append(job.job_id, "step", {"step": "turn-1", "step_state": jobs.SUCCEEDED,
                                      "output": {"done": True, "content": "final"}})
    plan = agent_runtime.AgentPlan(ctx, store=store)
    spec = plan.next_spec(job)
    assert spec.name == "finish"


def test_the_api_verification_rule_requires_a_citation(api_app):
    import app as app_module  # noqa: PLC0415

    verify = app_module._agent_verify({"verify": "citations_present"})
    assert verify("The answer is 42.", {}) == "the answer cites no source"
    assert verify("The answer is 42 [1].", {}) is None
    assert app_module._agent_verify({}) is None


def test_a_long_turn_is_capped_in_the_live_prompt_not_in_the_record():
    ctx = context(FakeModel([]), FakeToolbox(), max_messages=2)
    plan = agent_runtime.AgentPlan(ctx)
    delta = [
        {"role": "assistant", "content": "thinking"},
        {"role": "tool", "tool_call_id": "a", "content": "old-1"},
        {"role": "tool", "tool_call_id": "b", "content": "old-2"},
        {"role": "tool", "tool_call_id": "c", "content": "new"},
    ]
    capped = plan._cap(delta)
    assert len(capped) <= 2
    assert capped[-1]["content"] == "new"
