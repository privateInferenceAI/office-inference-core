"""The agent runtime: a model/tool loop as a dynamic plan on the C5 engine.

**One model turn is one engine step.** The engine already gives every step a transaction, a step row, a chain
row, a budget and a resume point. Decision 0009 chose that over a loop hidden inside one step, because a loop
inside a step cannot be inspected, stopped between turns, or resumed without repeating work.

This module is the loop's controller. It does not open the gateway, resolve a session or know which tools are
allowed: those are injected. The API supplies the real ones; the tests supply fakes with the same shape, which
is what lets the loop be tested without a deployment.

The current scope is deliberately the core. It runs a model turn, executes the tool calls the turn returned,
carries the messages forward through the step rows, and finishes with a verification step. The strict audit,
the approval gate and sub-agents are the next layers; the seams for them are named where they belong.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Callable, Optional

import jobs
import tool_loop
import workflow

DEFAULT_MAX_STEPS = 12
DEFAULT_MAX_MESSAGES = 24
SYSTEM_PROMPT = """You are a careful assistant working on a task. Use the tools when you need evidence.
When you have enough, answer in prose. Every claim taken from a tool result must carry the number the tool
printed in square brackets, for example [1] or [2]. A file name alone is not a citation. If you cannot answer,
say so plainly and say what is missing rather than guessing. Do not claim to have used a source you did not
read."""


@dataclass
class RuntimeContext:
    """What the loop needs, injected so the controller has no deployment knowledge."""

    definition: dict
    call_model: Callable[[list, list], dict]
    toolbox: object
    tools: list
    verify: Optional[Callable[[str, dict], Optional[str]]] = None
    on_model_call: Optional[Callable[[dict], None]] = None
    max_steps: int = DEFAULT_MAX_STEPS
    max_messages: int = DEFAULT_MAX_MESSAGES
    messages: list = field(default_factory=list)
    done: bool = False
    turns: int = 0


class AgentPlan:
    """The dynamic plan. Each model turn is a step; the finish step verifies and sets the result."""

    name = "agent"

    def __init__(self, context: RuntimeContext, store=None):
        self.context = context
        self.store = store

    @property
    def attempt_budget(self) -> int:
        # Every turn can fail once, plus the finish step. A loop that is merely long is bounded by max_steps,
        # not by this budget.
        return max(2, self.context.max_steps + 2)

    @property
    def time_budget_s(self) -> int:
        return int(self.context.definition.get("max_seconds") or 600)

    def next_spec(self, job):
        done = {step.name for step in job.steps if step.state == jobs.SUCCEEDED}
        if "finish" in done:
            return None
        turns = [name for name in done if name.startswith("turn-")]
        if not turns:
            return workflow.StepSpec(name="turn-1", run=self._turn, timeout_s=120)
        latest = max(int(name.split("-", 1)[1]) for name in turns)
        # **A resumed process learns the last turn's outcome from the record, not from memory.** The plan is
        # rebuilt after a restart, so `context.done` is false; without this read the run would ask the model
        # for another turn after one that already produced the final answer.
        if not self.context.done:
            self.context.done = self._recorded_done(job, latest)
        if self.context.done:
            return workflow.StepSpec(name="finish", run=self._finish, timeout_s=30)
        if latest >= self.context.max_steps:
            # **A loop that hits its cap still gets a verification step.** The step fails the job with a typed
            # cause instead of leaving the engine's attempt budget to fail it with a less specific one.
            return workflow.StepSpec(name="finish", run=self._finish, timeout_s=30)
        return workflow.StepSpec(name=f"turn-{latest + 1}", run=self._turn, timeout_s=120)

    def is_final(self, job, spec) -> bool:
        return spec.name == "finish"

    def _recorded_done(self, job, latest: int) -> bool:
        if self.store is None:
            return False
        for event in reversed(self.store.events_since(job.job_id, 0)):
            if event.get("event") != "step":
                continue
            data = event.get("data") or {}
            if data.get("step") == f"turn-{latest}":
                output = data.get("output") or {}
                return bool(output.get("done"))
        return False

    # ---- the steps

    def _turn(self, run):
        context = self.context
        context.turns += 1
        messages = self._messages(run.prior, run.input)
        reply = context.call_model(messages, context.tools)
        if reply.get("error"):
            return workflow.StepResult(
                ok=False, failure_kind=str(reply["error"]), failure_detail="the model call failed",
                retryable=True)
        if context.on_model_call is not None:
            try:
                context.on_model_call(reply)
            except Exception as exc:  # noqa: BLE001
                # **A model call with no row is the same gap as a tool call with no row.** The runtime does
                # not continue without its record; the answer path's tolerance is deliberately not inherited.
                return workflow.StepResult(ok=False, failure_kind="model_audit",
                                           failure_detail=f"the model audit failed: {type(exc).__name__}",
                                           retryable=False)
        content = (reply.get("content") or "").strip()
        calls = reply.get("tool_calls") or []
        delta = []
        if content or calls:
            delta.append({"role": "assistant", "content": content,
                          "tool_calls": [{"id": c.get("id") or "", "type": "function",
                                          "function": {"name": c.get("name") or "",
                                                       "arguments": c.get("arguments") or "{}"}}
                                         for c in calls]})
        tool_results = []
        for call in calls:
            name = call.get("name") or ""
            arguments = call.get("arguments") or "{}"
            try:
                result = context.toolbox.execute(name, arguments)
            except tool_loop.ToolAuditError as exc:
                # **A tool with no audit row is a gap in the record, not a tool failure to retry.** The
                # answer path tolerates a lost audit row; the runtime does not.
                return workflow.StepResult(ok=False, failure_kind="tool_audit",
                                           failure_detail=str(exc), retryable=False)
            except Exception as exc:  # noqa: BLE001 - a tool failure is returned to the model, not fatal
                result = json.dumps({"error": f"the tool failed: {type(exc).__name__}"})
            tool_results.append({"name": name, "arguments": arguments, "result": str(result)})
            delta.append({"role": "tool", "tool_call_id": call.get("id") or "", "content": str(result)})

        # **The stop flag is checked between turns.** A cancel or pause that landed while the model was
        # thinking returns a retryable step outcome; the engine sees the job state and stops. The model call
        # itself is not interrupted, which is why the engine's comment says so rather than implying a kill.
        if run.stopped():
            return workflow.StepResult(ok=False, failure_kind=workflow.FAIL_CANCELLED,
                                       failure_detail="the job was stopped between turns",
                                       retryable=True)

        delta = self._cap(delta)
        done = bool(content) and not calls
        context.done = done
        return workflow.StepResult(output={
            "delta": delta,
            "done": done,
            "content": content,
            "tool_results": tool_results,
            "turns": context.turns,
        })

    def _finish(self, run):
        context = self.context
        messages = self._messages(run.prior, run.input)
        assistant = next((message for message in reversed(messages)
                          if message.get("role") == "assistant" and message.get("content")), None)
        answer = (assistant or {}).get("content", "").strip()
        if not answer:
            return workflow.StepResult(
                ok=False, failure_kind="no_answer",
                failure_detail="the loop ended without an assistant answer", retryable=False)
        evidence = [message for message in messages if message.get("role") == "tool"]
        failure = context.verify(answer, {"messages": messages, "tool_results": len(evidence)})             if context.verify else None
        if failure:
            return workflow.StepResult(ok=False, failure_kind="verification", failure_detail=failure,
                                       retryable=False)
        return workflow.StepResult(output={"answer": answer, "turns": context.turns,
                                           "tool_results": len(evidence)})

    # ---- messages

    def _messages(self, prior: dict, task_input: str = "") -> list:
        messages = list(self.context.messages) or [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": (self.context.definition.get("description") or "")
             + (f"\n\nTask:\n{task_input}" if task_input else "")},
        ]
        for name, output in prior.items():
            if not name.startswith("turn-") or not isinstance(output, dict):
                continue
            for message in output.get("delta") or []:
                messages.append(message)
        return messages

    def _cap(self, delta: list) -> list:
        """Keep the new turn's messages under the context ceiling.

        **The cap is on the live prompt, not on the record.** Older tool results are the right thing to drop:
        they are reproducible from the evidence namespace, while the assistant's reasoning is not. The drop is
        named in the step output so a later version can write the chain row that says what left.
        """
        if len(delta) <= self.context.max_messages:
            return delta
        if self.context.max_messages <= 1:
            return [{"role": "tool", "tool_call_id": "", "content": "[older tool results dropped]"}]
        keep = delta[-(self.context.max_messages - 1):]
        return [{"role": "tool", "tool_call_id": "", "content": "[older tool results dropped]"}] + keep


@dataclass
class RuntimeResult:
    job: object
    plan: AgentPlan


def run(store, job, context: RuntimeContext, *, mint=None, deployment_id: str = "", key_path=None):
    """Drive one agent job to a terminal state or a gate, with the engine doing the record."""
    plan = AgentPlan(context, store=store)
    runner = workflow.runner_for(store, plan, mint=mint, deployment_id=deployment_id,
                                 key_path=key_path, actor=job.subject, actor_role=job.actor_role)
    finished = runner.start(job)
    return RuntimeResult(job=finished, plan=plan)
