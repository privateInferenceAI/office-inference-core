"""The loop, the transaction, and the gate.

**Why this file exists rather than a queue and a worker.** A workflow here is a job with several steps, and a
job already has a state machine, a numbered event log, typed failures, budgets and two stores in
`services/api/jobs.py`. What C4 does not have is a loop with more than one step, and C5 does not replace the
machine — it drives it.

**The property the whole piece is for.** Every state change and the chain row that describes it are written in
**one transaction**. The unit of work below opens a transaction on the store's own connection, calls the same
`store.put` and `store.append` the rest of the system calls, and writes the chain row through
`services/audit/audit.py`'s `insert_event` on that connection's cursor. A process killed between the state
write and the chain write leaves the connection for Postgres to reap, which rolls back both. There is no
window to close because there is no window, and no code has to remember to be careful.

**Where the position lives, and why nowhere new.** A step that ran has a `job_steps` row with its name and
`state = 'succeeded'`. The engine's position is therefore the first plan step without one, derived from the
same rows a client reads. A cursor column would be a second answer to "how far did this get", and the two
would disagree the first time a step was retried.

**Where the prior outputs live.** In the event log, which is where progress already lives. `job_steps` has no
output column and does not need one: a resumed run rebuilds what the steps before it produced from the
`step` events it wrote, so the log is the record rather than a copy of one.

**The budget bounds one run, not the job's life.** A job that has been waiting at a gate for three hours is
not consuming the time its work is allowed; the clock starts when the engine takes the job and stops when it
gives it back. The alternative is a job that fails because a person took lunch, which is the sort of rule that
gets removed the first time it bites.
"""
from __future__ import annotations

import copy
import hashlib
import sys
import threading
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional

# The machine, the records and the stores live in the API's module, which is where they already were. The
# engine imports them rather than restating them: a second copy of the transition table would be a second
# answer to what a job may do.
_API_DIR = Path(__file__).resolve().parent.parent / "api"
if str(_API_DIR) not in sys.path:
    sys.path.insert(0, str(_API_DIR))

import jobs  # noqa: E402  (the path above is what makes this importable outside the API image)

from . import chain  # noqa: E402
from .plans import Plan, StepResult, StepRun, StepSpec  # noqa: E402

# The frame the gate writes, matching the stream a client reads.
EV_STEP = jobs.EV_STEP

# The typed failures this engine produces itself. A step's own kinds are its business; these four are the
# engine's, and they are counted by the probe.
FAIL_TIMEOUT = "step_timeout"
FAIL_CANCELLED = "cancelled_mid_step"
FAIL_RAISED = "step_raised"
FAIL_NO_RESULT = "no_step_result"
FAIL_ATTEMPTS = "attempts_exhausted"
FAIL_TIME = "time_budget_exhausted"


class WorkflowError(Exception):
    """A job that cannot be driven. Always a refusal about the plan or the store, never about the work."""


def effect_key(job_id: str, step_name: str) -> str:
    """The key an effect is remembered by, **derived from the job and the step and never from the attempt**.

    This is the whole of the idempotency guarantee for an effect that leaves our transaction. A key that
    included the attempt number would be a different key on every retry, the far side would see two different
    effects, and the duplicate would look exactly like working code. It is a plain function so a test can
    assert the property directly rather than inferring it from a counter.
    """
    return "sha256:" + hashlib.sha256(f"{job_id}|{step_name}".encode("utf-8")).hexdigest()


# --------------------------------------------------------------------------- the units of work
#
# Two implementations, and they are not interchangeable. `PostgresUnit` is the one that carries the property
# the register asks for. `MemoryUnit` is what lets the engine's behaviour be tested without a database, and it
# says in every row it writes that its chain is a double — a memory test proves the loop, never the chain.


class PostgresUnit:
    """One transaction on the store's connection, and the chain row inside it."""

    def __init__(self, store, *, mint: Callable[[str, str], str], deployment_id: str, key_path=None):
        self.store = store
        self.mint = mint
        self.deployment_id = deployment_id
        self.key_path = key_path
        self._tx = None

    def __enter__(self):
        # **A real transaction even though the store is in autocommit mode.** psycopg3's `transaction()`
        # issues BEGIN and COMMIT itself, so the store's normal single-statement writes and this unit's
        # multi-statement writes share one definition of the connection without the store changing.
        self._tx = self.store.conn.transaction()
        self._tx.__enter__()
        return self

    def __exit__(self, exc_type, exc, tb):
        return self._tx.__exit__(exc_type, exc, tb)

    # ---- the store protocol, so `jobs.request_approval` and `jobs.decide` can be handed a unit directly
    def get(self, job_id: str):
        return self.store.get(job_id)

    def put(self, job) -> None:
        self.store.put(job)

    def append(self, job_id: str, event: str, data: dict) -> int:
        return self.store.append(job_id, event, data)

    # ---- engine-only
    def record(self, **row) -> dict:
        with self.store.conn.cursor() as cur:
            return chain.record(cur, key_path=self.key_path, **row)

    def effect_applied(self, job_id: str, key: str) -> bool:
        with self.store.conn.cursor() as cur:
            cur.execute("select 1 from workflow_effects where job_id = %s and effect_key = %s",
                        (job_id, key))
            return cur.fetchone() is not None

    def record_effect(self, job_id: str, key: str, step_name: str, mode: str, state: str,
                      detail: dict) -> bool:
        import json as _json

        with self.store.conn.cursor() as cur:
            cur.execute("""
                insert into workflow_effects (job_id, effect_key, step_name, mode, state, detail)
                values (%s, %s, %s, %s, %s, %s)
                on conflict (job_id, effect_key) do nothing
                returning effect_key""",
                (job_id, key, step_name, mode, state, _json.dumps(detail or {})))
            return cur.fetchone() is not None


class MemoryUnit:
    """A transaction over a memory store, with the same commit-or-roll-back shape.

    **The chain rows here are a double and say so.** `chain` is set to `memory-double` on every row, so a test
    that reads the journal cannot mistake it for evidence that the real chain was written. The tests that make
    claims about the chain run against Postgres, which is the only place the claim can be true.
    """

    def __init__(self, factory: "MemoryUnits"):
        self.factory = factory
        self.store = factory.store
        self._snapshot = None

    def __enter__(self):
        self._snapshot = (copy.deepcopy(self.store.jobs), copy.deepcopy(self.store.events),
                          len(self.factory.journal), dict(self.factory.effects))
        return self

    def __exit__(self, exc_type, exc, tb):
        if exc_type is not None:
            jobs_before, events_before, journal_len, effects_before = self._snapshot
            self.store.jobs = jobs_before
            self.store.events = events_before
            del self.factory.journal[journal_len:]
            self.factory.effects = effects_before
        return False

    def get(self, job_id: str):
        # A copy, like a database read. Without this the memory store would let a caller mutate a job and
        # never write it, which is the one mistake every test of this engine must not be able to make.
        found = self.store.jobs.get(job_id)
        return copy.deepcopy(found) if found is not None else None

    def put(self, job) -> None:
        self.store.jobs[job.job_id] = copy.deepcopy(job)

    def append(self, job_id: str, event: str, data: dict) -> int:
        return self.store.append(job_id, event, copy.deepcopy(data))

    def record(self, **row) -> dict:
        payload = row.get("payload")
        entry = {"chain": "memory-double", "event_type": row.get("event_type"),
                 "outcome": row.get("outcome"), "data_class": row.get("data_class"),
                 "tool": row.get("tool_name"), "payload": payload,
                 "legacy_tool_in_payload": True}
        self.factory.journal.append(entry)
        return entry

    def effect_applied(self, job_id: str, key: str) -> bool:
        return (job_id, key) in self.factory.effects

    def record_effect(self, job_id: str, key: str, step_name: str, mode: str, state: str,
                      detail: dict) -> bool:
        if (job_id, key) in self.factory.effects:
            return False
        self.factory.effects[(job_id, key)] = {"mode": mode, "state": state, "step": step_name,
                                               "detail": detail}
        return True


class MemoryUnits:
    """The factory, and the journal and effect ledger the units share."""

    def __init__(self, store, *, actor: str = "memory", actor_role: str = "tester"):
        self.store = store
        self.actor, self.actor_role = actor, actor_role
        self.journal: list = []
        self.effects: dict = {}

    def __call__(self) -> MemoryUnit:
        return MemoryUnit(self)

    def effect_recorded_for_step(self, job_id: str, step_name: str):
        return [key for (owner, key), row in self.effects.items()
                if owner == job_id and row.get("step") == step_name]


# --------------------------------------------------------------------------- the runner


@dataclass
class Runner:
    """Drives one plan against one store.

    Held as an object rather than a function because everything it needs is fixed for the run: the plan, the
    store, the unit factory and who the engine may record as the actor. A function taking six arguments would
    be the same thing with more places to get the order wrong.
    """

    store: object
    plan: Plan
    units: object
    deployment_id: str = ""
    key_path: Optional[str] = None
    poll_s: float = 0.05

    # ---- what the caller calls

    def start(self, job):
        """Run the plan from wherever the job is. A queued job is moved to running first."""
        return self._drive(job)

    def resume(self, job):
        """Continue a job that stopped, from the step after the last one that succeeded."""
        return self._drive(job)

    # ---- the loop

    def _drive(self, job):
        started = time.monotonic()
        while True:
            live = self.store.get(job.job_id)
            if live is None:
                raise WorkflowError(f"job {job.job_id} is not in the store, so it cannot be driven")
            if live.state in jobs.TERMINAL:
                self._close_open_steps(live)
                return self.store.get(job.job_id) or live
            if live.state in (jobs.PAUSED, jobs.AWAITING_APPROVAL):
                return self._with_pending_action(live)
            if live.state == jobs.QUEUED:
                self._begin(live)
                continue
            if live.state != jobs.RUNNING:
                raise WorkflowError(f"job {live.job_id} is {live.state}, which the engine does not drive")

            if time.monotonic() - started > live.time_budget_s:
                return self._fail_job(live, FAIL_TIME, "the run used its whole time budget")
            if len(live.steps) >= live.attempt_budget:
                return self._fail_job(live, FAIL_ATTEMPTS, "the job used every attempt it was given")

            # **The plan decides the next step.** A static plan returns the first entry with no succeeded row;
            # a dynamic plan builds the next iteration from the definition and the history. The engine no
            # longer indexes a tuple, so a loop does not need a new engine.
            spec = self.plan.next_spec(live)
            if spec is None:
                return self._finish(live)

            if spec.gate:
                waiting = self._gate(live, spec)
                if waiting is None:
                    return self._with_pending_action(self.store.get(live.job_id) or live)
                continue

            if self._effect_recorded(live.job_id, spec.name):
                # The effect is already in the ledger, so the work happened. Running the step again would be
                # the duplicate this row exists to prevent; the row is closed as done and the loop moves on.
                self._skip_step(live, spec)
                continue

            self._run_step(live, spec)

    # ---- position and prior outputs

    def _prior_outputs(self, job_id: str) -> dict:
        """What the steps before this one produced, rebuilt from the event log.

        The log is the record, so this reads it rather than keeping a second copy. An event whose step did not
        succeed contributes nothing: a half-finished attempt's output is not something a later step may build
        on, and treating it as one would let a failed extraction be posted.
        """
        prior: dict = {}
        for event in self.store.events_since(job_id, 0):
            data = event.get("data") or {}
            if event.get("event") == EV_STEP and data.get("step_state") == jobs.SUCCEEDED:
                if data.get("output") is not None:
                    prior[data.get("step")] = data["output"]
        return prior

    # ---- the units, one per state change

    def _with_pending_action(self, job):
        """The job a caller gets back carries what it is waiting to have approved, not only the hash of it.

        **The digest is what the route checks; this is what a person reads.** `pending_action` is a column on
        the job row so it survives a restart, and until it is read back from there this falls back to the gate
        event, which has carried it since the gate was written. A decision screen that can render only a hash
        is asking somebody to approve something they cannot see, so the engine attaches it on the way out
        rather than leaving it in the log for a client to find.
        """
        if job.state != jobs.AWAITING_APPROVAL or getattr(job, "pending_action", None):
            return job
        for event in reversed(self.store.events_since(job.job_id, 0)):
            data = event.get("data") or {}
            if data.get("action"):
                job.pending_action = data["action"]
                break
        return job

    def _mint(self, job) -> str:
        """A signed statement naming the job's actor, minted for an audit row.

        Minted per write rather than once per run, because a statement carries a single-use nonce and a short
        life. The actor the writer records is the one inside this statement, verified against the deployment's
        key; the job's snapshot is what it names, which is why a job run under last week's role records last
        week's role rather than today's.
        """
        mint = getattr(self.units, "mint", None)
        if mint is None:
            # The memory unit writes a double and does not verify a statement. It says so in every row.
            return ""
        return mint(job.subject, job.actor_role)

    def _write(self, job_id: str, body, *, events=(), event_type="tool_call", outcome="ok",
               tool_name="", request_id="", payload_extra=None, mutate=None):
        """One state change, its events, its effects and its chain row, in one transaction.

        `body(unit, fresh)` does the mutation and returns the chain payload. Everything it touches commits
        together or not at all, which is the property this module exists for.
        """
        with self.units() as unit:
            fresh = unit.get(job_id)
            if fresh is None:
                raise WorkflowError(f"job {job_id} vanished from the store mid-run")
            payload = body(unit, fresh)
            unit.put(fresh)
            for name, data in events:
                unit.append(job_id, name, data)
            unit.record(statement=self._mint(fresh), event_type=event_type, outcome=outcome,
                        data_class=fresh.data_class, deployment_id=self.deployment_id or fresh.deployment_id,
                        payload={**(payload or {}), **(payload_extra or {})}, tool_name=tool_name,
                        request_id=request_id)
        return self.store.get(job_id)

    # ---- the moves

    def _begin(self, job):
        def body(unit, fresh):
            jobs.transition(fresh, jobs.RUNNING)
            return {"stage": "workflow", "job_id": fresh.job_id, "task": fresh.task,
                    "plan": self.plan.name, "step_state": jobs.RUNNING, "step": ""}

        return self._write(job.job_id, body, events=[(jobs.EV_PROGRESS, {"state": jobs.RUNNING,
                                                                        "note": "started"})],
                           tool_name=job.task)

    def _finish(self, job):
        """Every step is done. Only reachable when a run was interrupted between the last step and the state."""
        def body(unit, fresh):
            fresh.outcome_reason = ""
            jobs.transition(fresh, jobs.SUCCEEDED)
            return {"stage": "workflow", "job_id": fresh.job_id, "task": fresh.task,
                    "plan": self.plan.name, "step_state": jobs.SUCCEEDED, "step": ""}

        return self._write(job.job_id, body,
                           events=[(jobs.EV_DONE, {"state": jobs.SUCCEEDED, "attempts": job.attempts})],
                           tool_name=job.task)

    def _fail_job(self, job, kind: str, detail: str):
        def body(unit, fresh):
            jobs.transition(fresh, jobs.FAILED, reason=kind)
            return {"stage": "workflow", "job_id": fresh.job_id, "task": fresh.task,
                    "plan": self.plan.name, "step_state": jobs.FAILED, "failure_kind": kind,
                    "failure_detail": detail, "step": ""}

        return self._write(job.job_id, body,
                           events=[(jobs.EV_DONE, {"state": jobs.FAILED, "reason": kind, "detail": detail})],
                           outcome="error", tool_name=job.task)

    def _run_step(self, job, spec: StepSpec):
        """One step, in as many transactions as it honestly needs.

        Two per attempt: one that writes the step as running, so a client has something to render while it
        works, and one that writes the outcome, the effects and the chain row together. A process killed
        during the step leaves a row saying it started, which is the truth rather than a gap.

        A retry is a new step row, and the effect key is **the same key on every attempt**. That is the whole
        of the guarantee for an effect that leaves our transaction: the far side can deduplicate it because the
        key does not move.
        """
        for attempt_run in range(1, max(1, spec.attempts) + 1):
            live = self.store.get(job.job_id)
            if live is None or live.state in jobs.TERMINAL:
                return live
            attempt = len(live.steps) + 1
            run = StepRun(job=live, input=live.input, prior=self._prior_outputs(live.job_id),
                          attempt=attempt, step_attempt=attempt_run,
                          deadline=time.monotonic() + spec.timeout_s,
                          effect_key=effect_key(live.job_id, spec.name),
                          _stopped=lambda: self._cancelled(job.job_id))

            retry_of = attempt - 1 if attempt_run > 1 else None
            started_step_id = uuid.uuid4().hex
            self._write(live.job_id, self._started_body(spec, attempt, retry_of, started_step_id),
                        events=[(EV_STEP, {"step": spec.name, "step_state": jobs.RUNNING,
                                           "attempt": attempt, "retry_of": retry_of})],
                        tool_name=spec.name)

            live = self.store.get(job.job_id)
            if live.state in jobs.TERMINAL:
                return live
            # **The row written one transaction ago has to be readable, or the store is the fault and not the
            # engine.** This engine reads a job back between every pair of writes and derives its position from
            # the step rows, so a store that writes a row and does not return it makes the engine look like it
            # cannot find its own work. That happened: `PostgresStore.get()` built its steps list from a cursor
            # whose result set a later query had replaced, and the symptom was `the step row for read_message
            # attempt 1 is missing` — a sentence about the engine, from a defect in the store. The check costs
            # one comparison and turns that class of fault into a sentence that names the right component.
            if not any(step.step_id == started_step_id for step in live.steps):
                raise WorkflowError(
                    f"the store returned no row for {spec.name} attempt {attempt}, one transaction after it "
                    f"was written. The engine derives a job's position from its step rows, so a store that does "
                    f"not return the rows it accepted cannot drive a workflow. This is a store fault: check "
                    f"that reading a job back returns the steps that were written to it.")

            result = self._invoke(spec, run)
            if result.ok or not result.retryable or attempt_run >= max(1, spec.attempts) \
                    or not self._steps_left(live):
                return self._settle(job.job_id, spec, run, result, attempt)
            # A retryable failure with attempts left. The next pass writes its own step row, which is what
            # makes "let it fail four times" visible rather than a claim, and it carries the same effect key.
        raise WorkflowError(f"the retry loop for {spec.name} ended without a result, which cannot happen")

    def _started_body(self, spec: StepSpec, attempt: int, retry_of, step_id: str):
        def body(unit, fresh):
            fresh.steps.append(jobs.Step(step_id=step_id, job_id=fresh.job_id, attempt=attempt,
                                        name=spec.name, state=jobs.RUNNING))
            return {"stage": "workflow", "job_id": fresh.job_id, "task": fresh.task, "step": spec.name,
                    "step_state": jobs.RUNNING, "attempt": attempt, "retry_of": retry_of,
                    "plan": self.plan.name}
        return body

    def _settle(self, job_id: str, spec: StepSpec, run: StepRun, result: StepResult, attempt: int):
        """Write what the attempt produced: the step row, the effects, the job state and the chain row."""
        final = self.plan.is_final(run.job, spec)

        def body(unit, fresh):
            step = self._step_row(fresh, spec.name, attempt)
            if result.ok:
                step.state = jobs.SUCCEEDED
                applied = []
                for effect in result.effects:
                    key = f"{run.effect_key}:{effect.key}"
                    fresh_row = unit.record_effect(fresh.job_id, key, spec.name, effect.mode,
                                                   effect.state, effect.detail)
                    applied.append({"key": key, "mode": effect.mode,
                                    "state": effect.state if fresh_row else "already_applied"})
                if final:
                    fresh.result = result.output
                    # **A job that succeeded has no reason for having ended, and a stale one is a lie a
                    # client reads.** `outcome_reason` is documented as "why it ended, in the case that it
                    # did not succeed", and `jobs.transition` only ever sets it. A workflow paused and then
                    # resumed to completion carried `paused_by_operator` on a `succeeded` job — found by
                    # reading a job row after the first live run, where it is the field beside the state.
                    fresh.outcome_reason = ""
                    jobs.transition(fresh, jobs.SUCCEEDED)
                return {"stage": "workflow", "job_id": fresh.job_id, "task": fresh.task,
                        "step": spec.name, "step_state": jobs.SUCCEEDED, "attempt": attempt,
                        "plan": self.plan.name, "effects": applied, "output": result.output}
            step.state = jobs.FAILED
            step.failure_kind = result.failure_kind or "unclassified"
            step.failure_detail = result.failure_detail
            step.retryable = bool(result.retryable)
            # **A job somebody already stopped stays stopped.** A cancel that landed while the step was running
            # arrives here as a failed step, and calling `transition` on a cancelled job would raise and lose
            # the record of what the step did. The step's own outcome is written either way; the job's state is
            # the caller's to set once it is terminal.
            if fresh.state not in jobs.TERMINAL:
                jobs.transition(fresh, jobs.FAILED, reason=step.failure_kind)
            return {"stage": "workflow", "job_id": fresh.job_id, "task": fresh.task,
                    "step": spec.name, "step_state": jobs.FAILED, "attempt": attempt,
                    "plan": self.plan.name, "failure_kind": step.failure_kind,
                    "retryable": bool(result.retryable), "job_state": fresh.state}

        events = [(EV_STEP, {"step": spec.name, "step_state": jobs.SUCCEEDED if result.ok else jobs.FAILED,
                             "attempt": attempt, "output": result.output if result.ok else None,
                             "failure": None if result.ok else {"kind": result.failure_kind,
                                                                "detail": result.failure_detail,
                                                                "retryable": bool(result.retryable)}})]
        if result.ok and final:
            # **The answer goes on the event as well as on the job.** `jobs.result` has no column yet, so a
            # caller that reloads the job after a resume would get the state and not the answer. The event log
            # is the record of what happened, and the answer is part of that.
            events.append((jobs.EV_DONE, {"state": jobs.SUCCEEDED, "attempts": attempt,
                                          "result": result.output}))
        elif not result.ok:
            events.append((jobs.EV_DONE, {"state": jobs.FAILED, "reason": result.failure_kind}))
        return self._write(job_id, body, events=events, outcome="ok" if result.ok else "error",
                           tool_name=spec.name)

    def _skip_step(self, job, spec: StepSpec):
        def body(unit, fresh):
            fresh.steps.append(jobs.Step(step_id=uuid.uuid4().hex, job_id=fresh.job_id,
                                        attempt=len(fresh.steps) + 1, name=spec.name,
                                        state=jobs.SUCCEEDED))
            return {"stage": "workflow", "job_id": fresh.job_id, "task": fresh.task, "step": spec.name,
                    "step_state": jobs.SUCCEEDED, "skipped": "effect_already_recorded",
                    "plan": self.plan.name}

        return self._write(job.job_id, body,
                           events=[(EV_STEP, {"step": spec.name, "step_state": jobs.SUCCEEDED,
                                              "skipped": "effect_already_recorded"})],
                           tool_name=spec.name)

    # ---- the gate

    def _gate(self, job, spec: StepSpec):
        """Stop at the gate, or pass it if it was already decided.

        Returns None when the job is now waiting. The caller re-reads; a gate that returned a job while the
        row said `awaiting_approval` would let the loop run the step after it, which is the one thing a gate
        must prevent.
        """
        rows = [step for step in job.steps if step.name == spec.name]
        if rows and rows[-1].state == jobs.SUCCEEDED:
            return job
        if rows:
            if job.pending_action_digest:
                return None                      # still waiting for a decision
            return self._pass_gate(job, spec, rows[-1])

        action = spec.approval_of(self._gate_run(job, spec)) if spec.approval_of else None
        if action is None:
            raise WorkflowError(f"the gate {spec.name} holds no action, so there is nothing to approve")

        def body(unit, fresh):
            fresh.steps.append(jobs.Step(step_id=uuid.uuid4().hex, job_id=fresh.job_id,
                                        attempt=len(fresh.steps) + 1, name=spec.name,
                                        state=jobs.RUNNING))
            # `request_approval` is handed the unit, and it calls `unit.put` and `unit.append`, so the digest,
            # the state and the event land in this transaction rather than in three.
            jobs.request_approval(fresh, unit, action.canonical(), note=action.summary)
            # What the screen renders. The digest is what the route checks; this is what a person reads, and a
            # decision screen showing only a hash asks somebody to approve something they cannot see.
            fresh.pending_action = action.to_dict()
            return {"stage": "workflow", "job_id": fresh.job_id, "task": fresh.task,
                    "step": spec.name, "step_state": jobs.AWAITING_APPROVAL, "plan": self.plan.name,
                    "action_digest": fresh.pending_action_digest, "action": action.to_dict()}

        return self._write(job.job_id, body,
                           events=[(EV_STEP, {"step": spec.name, "step_state": jobs.AWAITING_APPROVAL,
                                              "action_digest": jobs.action_digest(action.canonical()),
                                              "action": action.to_dict()})],
                           tool_name=spec.name)

    def _pass_gate(self, job, spec: StepSpec, row):
        """Mark a gate that has been approved, in one transaction with its chain row."""
        def body(unit, fresh):
            step = next((s for s in fresh.steps if s.step_id == row.step_id), None)
            if step is None:
                raise WorkflowError(f"the gate row for {spec.name} disappeared mid-run")
            step.state = jobs.SUCCEEDED
            if getattr(fresh, "pending_action", None):
                fresh.pending_action = {}
            return {"stage": "workflow", "job_id": fresh.job_id, "task": fresh.task, "step": spec.name,
                    "step_state": jobs.SUCCEEDED, "gate": "passed", "plan": self.plan.name}

        return self._write(job.job_id, body,
                           events=[(EV_STEP, {"step": spec.name, "step_state": jobs.SUCCEEDED,
                                              "gate": "passed"})],
                           tool_name=spec.name)

    def _gate_run(self, job, spec) -> StepRun:
        return StepRun(job=job, input=job.input, prior=self._prior_outputs(job.job_id),
                       attempt=len(job.steps) + 1, step_attempt=1,
                       deadline=time.monotonic() + spec.timeout_s,
                       effect_key=effect_key(job.job_id, spec.name),
                       _stopped=lambda: self._cancelled(job.job_id))

    # ---- reading the store during a run

    def _cancelled(self, job_id: str) -> bool:
        current = self.store.get(job_id)
        return current is not None and current.state == jobs.CANCELLED

    def _steps_left(self, job) -> bool:
        current = self.store.get(job.job_id)
        return current is not None and len(current.steps) < current.attempt_budget

    def _effect_recorded(self, job_id: str, step_name: str) -> bool:
        """Whether this step's effect is already in the ledger, whatever the ledger's key was named.

        Asked before the step runs rather than after. A step whose effect is recorded has already happened, and
        running it again is the duplicate this whole mechanism exists to prevent.
        """
        return bool(self.units.effect_recorded_for_step(job_id, step_name))

    @staticmethod
    def _step_row(job, name: str, attempt: int):
        row = next((s for s in job.steps if s.name == name and s.attempt == attempt), None)
        if row is None:
            raise WorkflowError(f"the step row for {name} attempt {attempt} is missing")
        return row

    def _close_open_steps(self, job):
        """A terminal job may not have a step that says it is still running.

        **This is the honest half of cancellation.** A cancelled Python thread cannot be killed, so a step
        abandoned at its deadline or at a cancel may still be inside a blocking call. The row says the step was
        stopped rather than that the work was undone, because one of those is true.
        """
        open_rows = [step for step in job.steps if step.state == jobs.RUNNING]
        if not open_rows:
            return job

        def body(unit, fresh):
            for step in fresh.steps:
                if step.state == jobs.RUNNING:
                    step.state = jobs.FAILED
                    step.failure_kind = FAIL_CANCELLED if fresh.state == jobs.CANCELLED else "stopped"
                    step.failure_detail = ("the job reached a terminal state while this step was running; a "
                                           "thread already inside a blocking call may still be running")
            return {"stage": "workflow", "job_id": fresh.job_id, "task": fresh.task,
                    "step_state": fresh.state, "plan": self.plan.name,
                    "closed_open_steps": [step.name for step in open_rows]}

        return self._write(job.job_id, body,
                           events=[(EV_STEP, {"step_state": job.state,
                                              "closed_open_steps": [s.name for s in open_rows]})],
                           outcome="error", tool_name=job.task)

    # ---- running the step callable, with a deadline it cannot ignore

    def _invoke(self, spec: StepSpec, run: StepRun) -> StepResult:
        """Run the step in a worker thread and stop waiting at the deadline.

        **The engine bounds the job whether or not the step cooperates.** A step that respects `run.remaining()`
        returns its own timeout; one that blocks on a socket that never answers does not, and this is what
        makes the job reach a terminal state anyway. What it does not do is free the blocked thread, and the
        record says so instead of implying the work was stopped.
        """
        box: dict = {}

        def worker():
            try:
                box["result"] = spec.run(run)
            except BaseException as exc:  # noqa: BLE001 - a step that raises is a failed attempt
                box["error"] = exc

        thread = threading.Thread(target=worker, name=f"workflow-{spec.name}", daemon=True)
        thread.start()
        while True:
            left = run.deadline - time.monotonic()
            if left <= 0:
                return StepResult(
                    ok=False, failure_kind=FAIL_TIMEOUT, retryable=False,
                    failure_detail=(f"step stopped at its {spec.timeout_s:g}s deadline without answering; a "
                                    f"thread already inside a blocking call may still be running"))
            if self._cancelled(run.job.job_id):
                return StepResult(
                    ok=False, failure_kind=FAIL_CANCELLED, retryable=False,
                    failure_detail=("the job was cancelled while this step was running; the step was stopped "
                                    "and a thread already inside a blocking call may still be running"))
            thread.join(min(self.poll_s, left))
            if not thread.is_alive():
                break

        if "error" in box:
            exc = box["error"]
            return StepResult(ok=False, failure_kind=FAIL_RAISED, retryable=False,
                              failure_detail=f"{type(exc).__name__}: {exc}")
        result = box.get("result")
        if not isinstance(result, StepResult):
            return StepResult(ok=False, failure_kind=FAIL_NO_RESULT, retryable=False,
                              failure_detail=f"{spec.name} returned {type(result).__name__}")
        return result


def runner_for(store, plan: Plan, *, mint=None, deployment_id: str = "", key_path=None,
               actor: str = "workflow", actor_role: str = "engine") -> Runner:
    """A runner wired to the store it was given, with the unit that store can actually support.

    A durable store gets the transactional unit, which is the one that carries WF-ENGINE-001. A memory store
    gets a unit that mirrors its commit semantics and marks every chain row as a double.
    """
    if isinstance(store, jobs.PostgresStore):
        units = PostgresUnitFactory(store, mint=mint, deployment_id=deployment_id, key_path=key_path)
    else:
        units = MemoryUnits(store, actor=actor, actor_role=actor_role)
    return Runner(store=store, plan=plan, units=units, deployment_id=deployment_id, key_path=key_path)


class PostgresUnitFactory:
    """Mints a statement per write, and hands out one unit at a time."""

    def __init__(self, store, *, mint, deployment_id: str, key_path=None):
        if mint is None:
            raise WorkflowError(
                "a durable workflow run needs a way to mint audit statements, because an actor in the chain is "
                "a claim the engine must be able to prove. Pass mint=<callable(actor, role) -> statement>.")
        self.store = store
        self.mint = mint
        self.deployment_id = deployment_id
        self.key_path = key_path

    def __call__(self) -> PostgresUnit:
        return PostgresUnit(self.store, mint=self.mint, deployment_id=self.deployment_id,
                            key_path=self.key_path)

    def effect_recorded_for_step(self, job_id: str, step_name: str):
        with self.store.conn.cursor() as cur:
            cur.execute("select effect_key from workflow_effects where job_id = %s and step_name = %s",
                        (job_id, step_name))
            return [row[0] for row in cur.fetchall()]
