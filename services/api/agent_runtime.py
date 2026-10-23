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
    # What left the live prompt and why, for the chain row. The record has to be able to answer what the
    # model did not see, not only what it did.
    on_context_trim: Optional[Callable[[dict], None]] = None
    # A child brief goes in, a structured JSON result comes out. The controller does not know how the child
    # runs; the deployment supplies a read-only child loop with its own budget.
    run_child: Optional[Callable[[str], str]] = None
    # A workspace tool name and its JSON arguments go in, a result string comes out. The deployment supplies the
    # client to the workspace service; the controller only routes the call and keeps the step.
    workspace_tool: Optional[Callable[[str, str], str]] = None
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
        # A turn can fail once, and every irreversible action adds a gate step and an act step. A loop that is
        # merely long is still bounded by max_steps, not by this budget.
        return max(4, self.context.max_steps * 2 + 4)

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
        last_output = self._recorded_output(job, f"turn-{latest}")
        pending = last_output.get("pending_tool") if isinstance(last_output, dict) else None
        if pending:
            # **A tool that must not happen without a decision is materialised as a gate step, not run inside
            # the turn.** The C5 route approves or refuses it. The gate is named after the turn so a second
            # irreversible call later in the run gets its own gate.
            gate_name = f"approval-{latest}"
            if gate_name not in done:
                return workflow.StepSpec(
                    name=gate_name, gate=True,
                    approval_of=lambda run: self._approval_action(pending), timeout_s=30)
            act_name = f"act-{latest}"
            if act_name not in done:
                return workflow.StepSpec(name=act_name, run=self._act, timeout_s=120)
        # **A resumed process learns the last turn's outcome from the record, not from memory.** The plan is
        # rebuilt after a restart, so `context.done` is false; without this read the run would ask the model
        # for another turn after one that already produced the final answer.
        if not self.context.done:
            self.context.done = bool(last_output.get("done")) if isinstance(last_output, dict) else False
        if self.context.done:
            return workflow.StepSpec(name="finish", run=self._finish, timeout_s=30)
        if latest >= self.context.max_steps:
            # **A loop that hits its cap still gets a verification step.** The step fails the job with a typed
            # cause instead of leaving the engine's attempt budget to fail it with a less specific one.
            return workflow.StepSpec(name="finish", run=self._finish, timeout_s=30)
        return workflow.StepSpec(name=f"turn-{latest + 1}", run=self._turn, timeout_s=120)

    def is_final(self, job, spec) -> bool:
        return spec.name == "finish"

    def _recorded_output(self, job, step_name: str) -> dict:
        """The output the record holds for a succeeded step, or an empty dict.

        Read from the event log because step outputs are not on the job row. The plan is rebuilt after a
        restart, so the gate, the act and the resume all have to be derivable from the record alone.
        """
        if self.store is None:
            return {}
        for event in reversed(self.store.events_since(job.job_id, 0)):
            if event.get("event") != "step":
                continue
            data = event.get("data") or {}
            if data.get("step") == step_name and data.get("step_state") == jobs.SUCCEEDED:
                return dict(data.get("output") or {})
        return {}

    def _approval_action(self, pending: dict):
        """What the person is approving, and the content the digest covers.

        The action carries the exact tool name and its arguments. The C5 route compares the decision to this
        digest, so a call whose arguments changed between the screen and the decision fails as drift.
        """
        name = str(pending.get("name") or "")
        arguments = str(pending.get("arguments") or "{}")
        return workflow.Action(
            kind="tool.call",
            summary=f"Run the irreversible tool {name}",
            reason="the agent requested a tool that cannot be undone",
            target=name,
            subject=arguments,
            body=arguments,
            consequence=("The tool runs once after approval. If its arguments change before the decision, the "
                         "approval is refused as drifted."),
        )

    def _act(self, run):
        """Run the approved tool exactly as it was recorded when the gate was raised."""
        context = self.context
        turns = [name for name in run.prior if name.startswith("turn-")]
        if not turns:
            return workflow.StepResult(ok=False, failure_kind="no_pending_tool",
                                       failure_detail="the gate was reached with no turn to act on",
                                       retryable=False)
        latest = max(int(name.split("-", 1)[1]) for name in turns)
        pending = (run.prior.get(f"turn-{latest}") or {}).get("pending_tool") or {}
        name = str(pending.get("name") or "")
        arguments = str(pending.get("arguments") or "{}")
        if not name:
            return workflow.StepResult(ok=False, failure_kind="no_pending_tool",
                                       failure_detail="the approved turn holds no tool call", retryable=False)
        try:
            result = context.toolbox.execute(name, arguments)
        except tool_loop.ToolAuditError as exc:
            return workflow.StepResult(ok=False, failure_kind="tool_audit",
                                       failure_detail=str(exc), retryable=False)
        except Exception as exc:  # noqa: BLE001
            return workflow.StepResult(ok=False, failure_kind="tool_error",
                                       failure_detail=f"the approved tool failed: {type(exc).__name__}",
                                       retryable=False)
        return workflow.StepResult(
            output={"delta": [{"role": "tool", "tool_call_id": pending.get("id") or "",
                               "content": str(result)}],
                    "pending_tool": None, "act": name},
            # The engine records the effect with the act step and skips a replayed act. A real connector must
            # use the same key on its side; the runtime passes no credential to the tool itself.
            effects=[workflow.Effect(key="irreversible", mode="external", state="applied",
                                     detail={"tool": name})],
        )

    # ---- the steps

    def _turn(self, run):
        context = self.context
        context.turns += 1
        messages = self._messages(run.prior, run.input)
        messages, trimmed = self._trim(messages)
        if trimmed:
            if context.on_context_trim is None:
                return workflow.StepResult(
                    ok=False, failure_kind="context_audit",
                    failure_detail="older context was dropped and there is no trim callback", retryable=False)
            try:
                context.on_context_trim(trimmed)
            except Exception as exc:  # noqa: BLE001
                return workflow.StepResult(
                    ok=False, failure_kind="context_audit",
                    failure_detail=f"the context trim audit failed: {type(exc).__name__}", retryable=False)
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
        irreversible = {str(name) for name in (context.definition.get("irreversible_tools") or [])}
        pending_call = next((call for call in calls if str(call.get("name") or "") in irreversible), None)
        if pending_call is not None:
            # **Nothing in this turn runs before the decision.** The call that needs approval is held as a gate
            # step; the model's other calls, if any, wait for the next turn so no side effect can happen while
            # the person is deciding.
            delta = self._cap(delta)
            context.done = False
            return workflow.StepResult(output={
                "delta": delta,
                "done": False,
                "content": content,
                "tool_results": [],
                "pending_tool": {"id": pending_call.get("id") or "",
                                 "name": str(pending_call.get("name") or ""),
                                 "arguments": str(pending_call.get("arguments") or "{}")},
                "turns": context.turns,
            })
        tool_results = []
        for call in calls:
            name = call.get("name") or ""
            arguments = call.get("arguments") or "{}"
            try:
                if name.startswith("workspace_") and context.workspace_tool is not None:
                    result = context.workspace_tool(name, arguments)
                elif name == "delegate_research":
                    if context.run_child is None:
                        result = json.dumps({"error": "this definition has no child runner"})
                    else:
                        brief = ""
                        try:
                            parsed = json.loads(arguments or "{}")
                            brief = str(parsed.get("brief") or "")
                        except (ValueError, TypeError):
                            brief = ""
                        result = context.run_child(brief)
                else:
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
            # **The act step's tool result belongs in the conversation.** Without it the model would see its
            # own tool call, the approval gate, and then no result, and would ask again.
            if not (name.startswith("turn-") or name.startswith("act-")) or not isinstance(output, dict):
                continue
            for message in output.get("delta") or []:
                messages.append(message)
        return messages

    def _trim(self, messages: list) -> tuple[list, dict]:
        """Keep the live prompt under the ceiling and say what left.

        **The record is the reason this is not silent.** A model that answered without a document because the
        document had been dropped looks identical to one that never found it. The trim report names the count,
        the ceiling and the step, and the deployment writes it to the chain.
        """
        if not messages:
            return messages, {}
        limit = max(2, int(self.context.max_messages))
        if len(messages) <= limit:
            return messages, {}
        head = []
        if messages[0].get("role") == "system":
            head.append(messages[0])
        for message in messages[len(head):]:
            if message.get("role") == "user":
                head.append(message)
                break
        # **Drop whole message groups, never half of one.** A tool result without the assistant call that
        # requested it is a malformed prompt: the provider rejects it, and the model that caused the trim then
        # loses the run. A group starts at an assistant message and carries the tool results that follow it.
        body = messages[len(head):]
        groups: list[list[dict]] = []
        current: list[dict] = []
        for message in body:
            if message.get("role") == "assistant" and current:
                groups.append(current)
                current = []
            current.append(message)
        if current:
            groups.append(current)
        kept: list[list[dict]] = []
        total = len(head)
        for group in reversed(groups):
            if total + len(group) > limit and kept:
                break
            kept.insert(0, group)
            total += len(group)
        kept_count = sum(len(group) for group in kept)
        drop_count = len(body) - kept_count
        dropped = body[:drop_count]
        tail = [message for group in kept for message in group]
        return head + tail, {
            "reason": "message_cap",
            "dropped": len(dropped),
            "kept": len(head) + len(tail),
            "max_messages": limit,
            "dropped_roles": sorted({str(message.get("role")) for message in dropped}),
        }

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
