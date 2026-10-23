"""The agent runtime core: one model turn per engine step, tools in the turn, a verification finish."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for extra in (ROOT / "services", ROOT / "services" / "api"):
    if str(extra) not in sys.path:
        sys.path.insert(0, str(extra))

import agent_runtime  # noqa: E402
import approvals  # noqa: E402
import jobs  # noqa: E402


DEFINITION = {"id": "research_brief", "description": "Research the question and answer it with cites.",
              "role": "analyst", "max_steps": 12, "max_seconds": 600,
              "irreversible_tools": ["send_email"]}


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
    # The first call has to carry the job's input, not only the definition description. The live run found
    # this missing: the model answered the description as if it were a greeting.
    assert "What is the answer?" in model.calls[0][1]["content"]
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


def test_the_parent_can_ask_one_read_only_child_for_a_brief():
    model = FakeModel([
        {"content": "", "tool_calls": [{"id": "child-call", "name": "delegate_research",
                                         "arguments": '{"brief": "Find the travel threshold"}'}],
         "error": None},
        {"content": "The helper found 500 USD [1].", "tool_calls": [], "error": None},
    ])
    toolbox = FakeToolbox()
    ctx = context(model, toolbox, verify=lambda answer, evidence: None)
    briefs = []
    ctx.run_child = lambda brief: (briefs.append(brief) or
                                   '{"brief": "The threshold is 500 USD [1].", "sources": []}')
    job, store = make_job(ctx)

    result = agent_runtime.run(store, job, ctx)

    assert result.job.state == jobs.SUCCEEDED
    assert briefs == ["Find the travel threshold"]
    assert toolbox.calls == [], "the child must use its own tool box, not the parent's"
    assert [step.name for step in result.job.steps] == ["turn-1", "turn-2", "finish"]
    assert result.job.result["answer"] == "The helper found 500 USD [1]."


def test_an_irreversible_tool_call_stops_at_the_gate_and_runs_only_after_approval():
    """AG-GATE-001 and AG-IRREVERSIBLE-001: the call is held, the digest covers it, and the act is once."""
    model = FakeModel([
        {"content": "", "tool_calls": [{"id": "call-send", "name": "send_email",
                                         "arguments": '{"to": "dana@example.com", "body": "hello"}'}],
         "error": None},
        {"content": "The message was sent [1].", "tool_calls": [], "error": None},
    ])
    toolbox = FakeToolbox(result='{"sent": true}')
    ctx = context(model, toolbox, verify=lambda answer, evidence: None)
    job, store = make_job(ctx)

    first = agent_runtime.run(store, job, ctx)
    assert first.job.state == jobs.AWAITING_APPROVAL
    assert [step.name for step in first.job.steps] == ["turn-1", "approval-1"]
    assert first.job.pending_action["target"] == "send_email"
    assert "dana@example.com" in first.job.pending_action["body"]
    assert toolbox.calls == [], "the tool must not run before the decision"

    approved = jobs.decide(first.job, store, approvals.DECISION_APPROVE,
                           first.job.pending_action_digest)
    second = agent_runtime.run(store, approved, ctx)

    assert second.job.state == jobs.SUCCEEDED
    assert toolbox.calls == [("send_email", '{"to": "dana@example.com", "body": "hello"}')]
    assert "act-1" in [step.name for step in second.job.steps]
    assert "turn-2" in [step.name for step in second.job.steps]
    assert second.job.result["answer"] == "The message was sent [1]."


def test_a_denied_irreversible_call_never_runs_and_the_job_fails():
    model = FakeModel([
        {"content": "", "tool_calls": [{"id": "call-send", "name": "send_email",
                                         "arguments": '{"to": "dana@example.com"}'}], "error": None},
    ])
    toolbox = FakeToolbox()
    ctx = context(model, toolbox)
    job, store = make_job(ctx)
    first = agent_runtime.run(store, job, ctx)
    denied = jobs.decide(first.job, store, approvals.DECISION_DENY, first.job.pending_action_digest)
    assert denied.state == jobs.FAILED
    assert toolbox.calls == []


def test_each_model_call_is_reported_to_the_audit_hook():
    model = FakeModel([
        {"content": "The answer is 42 [1].", "tool_calls": [], "error": None},
    ])
    ctx = context(model, FakeToolbox())
    reported = []
    ctx.on_model_call = reported.append
    job, store = make_job(ctx)
    result = agent_runtime.run(store, job, ctx)
    assert result.job.state == jobs.SUCCEEDED
    assert len(reported) == 1 and reported[0]["content"].startswith("The answer")


def test_a_model_audit_failure_fails_the_step_instead_of_losing_the_row():
    model = FakeModel([{"content": "The answer is 42 [1].", "tool_calls": [], "error": None}])
    ctx = context(model, FakeToolbox())
    def broken(reply):
        raise RuntimeError("the chain is full")
    ctx.on_model_call = broken
    job, store = make_job(ctx)
    result = agent_runtime.run(store, job, ctx)
    assert result.job.state == jobs.FAILED
    assert result.job.outcome_reason == "model_audit"


def test_the_api_verification_rule_requires_a_citation(api_app):
    import app as app_module  # noqa: PLC0415

    verify = app_module._agent_verify({"verify": "citations_present"})
    assert verify("The answer is 42.", {}) == "the answer cites no source"
    assert verify("The answer is 42 [1].", {}) is None
    assert app_module._agent_verify({}) is None


def test_dropping_older_context_is_reported_before_the_model_call():
    """AG-CONTEXT-001: a model that answered without a dropped document must not look like one that never found it."""
    ctx = context(FakeModel([{"content": "The answer is 42 [1].", "tool_calls": [], "error": None}]),
                  FakeToolbox(), max_messages=4)
    ctx.messages = [{"role": "system", "content": "system"},
                    {"role": "user", "content": "task"}]
    for index in range(6):
        ctx.messages.append({"role": "assistant", "content": f"old {index}"})
    reported = []
    ctx.on_context_trim = reported.append
    job, store = make_job(ctx)

    result = agent_runtime.run(store, job, ctx)

    assert result.job.state == jobs.SUCCEEDED
    assert len(reported) == 1
    assert reported[0]["dropped"] == 4
    assert reported[0]["max_messages"] == 4
    assert len(result.job.result["answer"]) > 0


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
