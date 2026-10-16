"""The workflow engine: the loop, the gate and the failures, with a memory store.

**What these tests can prove, and what they cannot.** They prove the loop: the position, the gate, the resume,
the cancel, the typed failures, the deadline and the idempotency key. They cannot prove WF-ENGINE-001, because
that is a property of a transaction and a memory store has none. That right is proved twice elsewhere: by
`tests/fixtures/workflow_probe.py`, which kills a process against the deployment's Postgres, and by
`tests/test_workflow_chain.py`, which runs the engine against a real database when one is available.

**Every test here is paired with the failure it rules out**, because a test that only walks the happy path
passes on an engine that runs one step and reports success.
"""
from __future__ import annotations

import copy
import json
import socket
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
for extra in (ROOT / "services", ROOT / "services" / "api"):
    if str(extra) not in sys.path:
        sys.path.insert(0, str(extra))

import jobs  # noqa: E402
import workflow  # noqa: E402


INVOICE = "INV-2026-0417 from accounts@ferguson.example total 4210.00 USD due 2026-11-15"


def make_job(**kw):
    base = dict(deployment_id="oi-test", subject="dana", actor_role="staff", roots={"company"},
                credential_sha256="a" * 64, task="invoice_intake", input=INVOICE,
                **workflow.budgets_for("invoice_intake"))
    base.update(kw)
    job = jobs.new_job(**base)
    store = jobs.MemoryStore()
    store.put(job)
    return job, store


def invoice(**kw):
    job, store = make_job(**kw)
    return job, store, workflow.runner_for(store, workflow.plan_for("invoice_intake"))


def steps_named(job, name):
    return [step for step in job.steps if step.name == name]


# --------------------------------------------------------------------------- the plan and the gate


def test_a_plan_runs_to_the_gate_and_records_every_step_before_it():
    job, store, runner = invoice()
    job = runner.start(job)
    assert job.state == jobs.AWAITING_APPROVAL, (
        "a plan whose fourth step is a gate must stop there rather than running to the end")
    assert [step.name for step in job.steps] == ["read_message", "extract_fields", "draft_action",
                                                 "approval"]
    assert all(step.state == jobs.SUCCEEDED for step in job.steps[:3])


def test_the_gate_holds_a_digest_and_the_words_behind_it():
    """**The screen has to show what is being decided, not only its hash.**"""
    job, store, runner = invoice()
    job = runner.start(job)
    assert job.pending_action_digest.startswith("sha256:")
    action = job.pending_action
    assert action["kind"] == "ledger.post" and "4,210.00" in action["summary"]
    assert action["consequence"], "an approval without a stated consequence is a click, not a decision"


def test_no_step_after_the_gate_has_a_row():
    """**The wrong path this rules out**: a gate that only changes a label and lets the work continue.

    A test that asserted the state alone would pass on an engine that had already posted the entry and then
    said it was waiting for approval.
    """
    job, store, runner = invoice()
    job = runner.start(job)
    assert not steps_named(job, "post_entry") and not steps_named(job, "close_out")
    assert "post_entry" not in [event["data"].get("step") for event in store.events_since(job.job_id, 0)]


def test_approving_resumes_and_each_later_step_has_exactly_one_row():
    job, store, runner = invoice()
    job = runner.start(job)
    jobs.decide(job, store, "approve", job.pending_action_digest)
    job = runner.resume(store.get(job.job_id))
    assert job.state == jobs.SUCCEEDED
    counts = {name: len(steps_named(job, name)) for name in ("post_entry", "close_out")}
    assert counts == {"post_entry": 1, "close_out": 1}, (
        "a resumed run must not repeat a step it already did, and each post-gate step is exactly one row")


def test_a_resumed_run_does_not_repeat_the_steps_before_the_gate():
    job, store, runner = invoice()
    job = runner.start(job)
    before = {step.name: step.step_id for step in job.steps if step.state == jobs.SUCCEEDED}
    jobs.decide(job, store, "approve", job.pending_action_digest)
    job = runner.resume(store.get(job.job_id))
    after = {step.name: step.step_id for step in job.steps if step.name in before}
    assert after == before, "the position comes from the succeeded rows, so earlier steps are not re-run"


def test_a_denial_stops_the_job_and_nothing_after_the_gate_runs():
    job, store, runner = invoice()
    job = runner.start(job)
    jobs.decide(job, store, "deny", job.pending_action_digest)
    job = runner.resume(store.get(job.job_id))
    assert job.state == jobs.FAILED and job.outcome_reason == "denied_at_the_gate"
    assert not steps_named(job, "post_entry"), "a denial that still posts the entry is the defect"
    assert steps_named(job, "approval")[-1].state == jobs.FAILED, (
        "a terminal job may not have a step that still says it is running")


def test_requesting_changes_also_stops_the_job():
    job, store, runner = invoice()
    job = runner.start(job)
    jobs.decide(job, store, "request_changes", job.pending_action_digest)
    job = runner.resume(store.get(job.job_id))
    assert job.state == jobs.FAILED and job.outcome_reason == "changes_requested_at_the_gate"
    assert not steps_named(job, "post_entry")


def test_a_gate_as_the_first_step_still_goes_through_running():
    """**The machine refuses `queued -> awaiting_approval` and it is right to.**

    A job that has never run cannot be waiting to approve something it has not produced. The engine must reach
    the gate from `running`, and a plan that opens with a gate is how that is checked rather than assumed.
    """
    store = jobs.MemoryStore()
    plan = workflow.Plan(name="gate-first", steps=(
        workflow.StepSpec(name="approval", gate=True,
                          approval_of=lambda run: workflow.Action(
                              kind="k", summary="s", reason="r", target="t", subject="s", body="b",
                              consequence="c")),
    ))
    job = jobs.new_job(deployment_id="oi-test", subject="dana", actor_role="staff", roots={"company"},
                       credential_sha256="a" * 64, task="gate-first", input="x",
                       attempt_budget=4, time_budget_s=60)
    store.put(job)
    job = workflow.runner_for(store, plan).start(job)
    assert job.state == jobs.AWAITING_APPROVAL
    assert job.started_at is not None, "the job went through running, so it has a start time"


# --------------------------------------------------------------------------- cancellation and pause


def test_a_cancel_during_a_step_stops_the_run_and_the_step_is_not_left_running():
    import threading

    store = jobs.MemoryStore()

    def slow(run):
        for _ in range(200):
            if run.stopped():
                return workflow.StepResult(ok=False, failure_kind="cancelled_mid_step", retryable=False,
                                           failure_detail="noticed")
            time.sleep(0.01)
        return workflow.StepResult(output={"never": True})

    plan = workflow.Plan(name="cancel-me", steps=(
        workflow.StepSpec(name="slow", run=slow, timeout_s=20.0),
        workflow.StepSpec(name="after", run=lambda run: workflow.StepResult(output={"ran": True})),
    ))
    job = jobs.new_job(deployment_id="oi-test", subject="dana", actor_role="staff", roots={"company"},
                       credential_sha256="a" * 64, task="cancel-me", input="x",
                       attempt_budget=4, time_budget_s=60)
    store.put(job)

    def canceller():
        time.sleep(0.15)
        live = store.get(job.job_id)
        if live is not None and live.state == jobs.RUNNING:
            jobs.cancel(live, store)

    thread = threading.Thread(target=canceller, daemon=True)
    thread.start()
    job = workflow.runner_for(store, plan).start(job)
    thread.join(timeout=5)

    assert job.state == jobs.CANCELLED
    assert not steps_named(job, "after"), "a cancelled job runs no further step"
    assert steps_named(job, "slow")[-1].state != jobs.RUNNING, (
        "a step abandoned by a cancel may still be inside a blocking call, and the row says it was stopped "
        "rather than claiming the work was undone")


def test_a_paused_job_is_left_where_it_is():
    job, store, runner = invoice()
    job = runner.start(job)
    jobs.decide(job, store, "approve", job.pending_action_digest)
    jobs.pause(store.get(job.job_id), store)
    paused = store.get(job.job_id)
    resumed = runner.resume(paused)
    assert resumed.state == jobs.PAUSED
    assert not steps_named(resumed, "post_entry"), "a paused job does no work until it is resumed"


# --------------------------------------------------------------------------- failures


def test_a_job_that_succeeded_carries_no_reason_for_having_ended():
    """**`outcome_reason` is "why it ended, in the case that it did not succeed".**

    `jobs.transition` only ever sets it, so a workflow paused and then resumed to completion kept
    `paused_by_operator` on a `succeeded` job. Found by reading a job row after the first live run on a
    deployment, where it is the field next to the state and the first thing a client renders.
    """
    job, store, runner = invoice()
    job = runner.start(job)
    jobs.decide(job, store, "approve", job.pending_action_digest)
    jobs.pause(store.get(job.job_id), store)
    assert store.get(job.job_id).outcome_reason == "paused_by_operator", (
        "the pause is what puts a reason on the job, and without it this test proves nothing")

    # The `resume` action does both halves: it moves the job out of `paused`, and only then can the engine
    # drive it. Calling the engine on a paused job is a no-op by design.
    jobs.resume(store.get(job.job_id), store)
    job = runner.resume(store.get(job.job_id))
    assert job.state == jobs.SUCCEEDED
    assert job.outcome_reason == "", (
        f"a succeeded job still says {job.outcome_reason!r}, which is a reason for an ending that did not "
        f"happen")


def test_a_failed_step_is_typed_and_no_later_step_has_a_row():
    """**A test that asserted only the failure passes on a workflow that ran to the end.**

    The assertion that matters is the absence of a row for the step after the failing one.
    """
    store = jobs.MemoryStore()

    def refuse(run):
        return workflow.StepResult(ok=False, failure_kind="unreadable_invoice", retryable=False,
                                   failure_detail="the total could not be read")

    plan = workflow.Plan(name="typed", steps=(
        workflow.StepSpec(name="first", run=lambda run: workflow.StepResult(output={"ok": True})),
        workflow.StepSpec(name="refuse", run=refuse),
        workflow.StepSpec(name="third", run=lambda run: workflow.StepResult(output={"ran": True})),
    ))
    job = jobs.new_job(deployment_id="oi-test", subject="dana", actor_role="staff", roots={"company"},
                       credential_sha256="a" * 64, task="typed", input="x",
                       attempt_budget=5, time_budget_s=60)
    store.put(job)
    job = workflow.runner_for(store, plan).start(job)
    assert job.state == jobs.FAILED and job.outcome_reason == "unreadable_invoice"
    assert steps_named(job, "refuse")[-1].failure_kind == "unreadable_invoice"
    assert not steps_named(job, "third")


def test_a_step_that_never_answers_is_stopped_by_the_engine_not_by_the_step():
    """A socket a step connects to and never hears from, with a step that ignores its own deadline."""
    store = jobs.MemoryStore()
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen(1)

    def never_answers(run):
        client = socket.socket()
        client.connect(listener.getsockname())
        client.recv(16)
        return workflow.StepResult(output={"impossible": True})

    plan = workflow.Plan(name="bounded", steps=(
        workflow.StepSpec(name="hang", run=never_answers, timeout_s=0.4),
        workflow.StepSpec(name="after", run=lambda run: workflow.StepResult(output={"ran": True})),
    ))
    job = jobs.new_job(deployment_id="oi-test", subject="dana", actor_role="staff", roots={"company"},
                       credential_sha256="a" * 64, task="bounded", input="x",
                       attempt_budget=4, time_budget_s=30)
    store.put(job)
    started = time.monotonic()
    job = workflow.runner_for(store, plan).start(job)
    elapsed = time.monotonic() - started
    listener.close()

    assert job.state == jobs.FAILED
    assert elapsed < 10, f"the job must reach a terminal state inside its bound, and took {elapsed:.1f}s"
    assert steps_named(job, "hang")[-1].failure_kind == workflow.FAIL_TIMEOUT
    assert not steps_named(job, "after")


def test_a_step_that_raises_is_a_typed_failure_rather_than_a_crash():
    store = jobs.MemoryStore()

    def explode(run):
        raise RuntimeError("the far side hung up")

    plan = workflow.Plan(name="boom", steps=(
        workflow.StepSpec(name="explode", run=explode, timeout_s=5.0),
    ))
    job = jobs.new_job(deployment_id="oi-test", subject="dana", actor_role="staff", roots={"company"},
                       credential_sha256="a" * 64, task="boom", input="x",
                       attempt_budget=3, time_budget_s=30)
    store.put(job)
    job = workflow.runner_for(store, plan).start(job)
    assert job.state == jobs.FAILED
    assert steps_named(job, "explode")[-1].failure_kind == workflow.FAIL_RAISED
    assert "RuntimeError" in steps_named(job, "explode")[-1].failure_detail


# --------------------------------------------------------------------------- idempotency


def test_the_effect_key_is_derived_from_the_job_and_the_step_and_never_from_the_attempt():
    """**The whole of the guarantee for an effect that leaves our transaction.**

    An engine that keyed the effect by the attempt would hand the far side a different key on every retry, the
    far side would see two different effects, and the duplicate would look exactly like working code.
    """
    first = workflow.effect_key("job-1", "post_entry")
    assert first == workflow.effect_key("job-1", "post_entry"), "the same step of the same job is one effect"
    assert first != workflow.effect_key("job-1", "close_out"), "two steps are two effects"
    assert first != workflow.effect_key("job-2", "post_entry"), "two jobs are two effects"
    assert "attempt" not in first and first.startswith("sha256:")


def test_a_step_that_already_had_its_effect_reports_it_rather_than_repeating_it():
    """The far side counts applications, not calls. It deduplicates on the key the engine supplies."""
    store = jobs.MemoryStore()
    applied: dict = {}

    def effect_once(run):
        if run.effect_key in applied:
            return workflow.StepResult(
                output={"applied": False},
                effects=[workflow.Effect(key="notify", mode="external", state="already_applied",
                                         detail={"idempotency_key": run.effect_key})])
        applied[run.effect_key] = 1
        # The effect happened, and now the step fails before the engine can record anything.
        return workflow.StepResult(ok=False, failure_kind="downstream_reset", retryable=True,
                                   failure_detail="the connection dropped after the effect")

    plan = workflow.Plan(name="idempotent", steps=(
        workflow.StepSpec(name="notify", run=effect_once, timeout_s=5.0, attempts=2),
    ))
    job = jobs.new_job(deployment_id="oi-test", subject="dana", actor_role="staff", roots={"company"},
                       credential_sha256="a" * 64, task="idempotent", input="x",
                       attempt_budget=6, time_budget_s=30)
    store.put(job)
    runner = workflow.runner_for(store, plan)
    job = runner.start(job)

    assert len(steps_named(job, "notify")) == 2, "the step was attempted twice"
    assert len(applied) == 1, "and the effect was applied once, counted at the far side"


def test_the_engine_does_not_rerun_a_step_whose_effect_is_recorded():
    """**A step whose effect is in the ledger has already happened, whatever the rows say.**

    The setup is the crash this guards: the effect was applied and recorded, and the step's own row did not
    survive. Re-running the step would be the duplicate. The assertion is on the run count, so an engine that
    re-ran the step and got the same answer anyway would fail this.
    """
    store = jobs.MemoryStore()
    runs = {"n": 0}

    def counted(run):
        runs["n"] += 1
        return workflow.StepResult(output={"n": runs["n"]},
                                   effects=[workflow.Effect(key="entry", mode="transactional")])

    def after(run):
        return workflow.StepResult(output={"after": True})

    plan = workflow.Plan(name="once", steps=(
        workflow.StepSpec(name="counted", run=counted, timeout_s=5.0),
        workflow.StepSpec(name="after", run=after, timeout_s=5.0),
    ))
    job = jobs.new_job(deployment_id="oi-test", subject="dana", actor_role="staff", roots={"company"},
                       credential_sha256="a" * 64, task="once", input="x",
                       attempt_budget=4, time_budget_s=30)
    store.put(job)
    runner = workflow.runner_for(store, plan)
    runner.start(job)
    assert runs["n"] == 1 and runner.units.effects, "the first run applied the effect and recorded it"

    # The ledger survived, the step row did not: exactly what a crash between them would leave if the effect
    # had been committed in an earlier unit. The engine must not run the step again.
    survivor = store.get(job.job_id)
    survivor.state = jobs.RUNNING
    survivor.steps = [step for step in survivor.steps if step.name != "counted"]
    survivor.finished_at = None
    store.put(survivor)

    runner.resume(store.get(job.job_id))
    assert runs["n"] == 1, (
        f"the step ran {runs['n']} times; the effect was already in the ledger, so the second run is a "
        f"duplicate")


# --------------------------------------------------------------------------- the plans and the budgets


def test_a_six_step_plan_needs_a_budget_of_at_least_six_and_the_default_is_five():
    """The engine enforces the budget it is given, so a plan's steps have to fit inside it.

    Without this the last step of the invoice plan fails with `attempts_exhausted` after all the work has been
    done, which is a correct refusal that a caller can avoid by asking the plan.
    """
    plan = workflow.plan_for("invoice_intake")
    budgets = workflow.budgets_for("invoice_intake")
    assert budgets["attempt_budget"] >= plan.step_count
    assert not workflow.budgets_for("no_such_task")

    job, store = make_job(attempt_budget=3)          # deliberately too small
    job = workflow.runner_for(store, plan).start(job)
    assert job.state == jobs.FAILED and job.outcome_reason == workflow.FAIL_ATTEMPTS


def test_a_task_with_no_plan_has_no_plan_rather_than_a_default_one():
    """The honest answer for work nobody described is to fall back to the single-step executor."""
    assert workflow.plan_for("summarise_across_documents") is None
    assert workflow.plan_names() == ["invoice_intake"]


def test_the_invoice_plan_refuses_a_message_it_cannot_read_rather_than_guessing():
    job, store, runner = invoice(input="an invoice with no total on it from a vendor")
    job = runner.start(job)
    assert job.state == jobs.FAILED
    assert job.outcome_reason == "unreadable_invoice"
    assert not steps_named(job, "post_entry"), "an invoice nobody could read is not an invoice for zero"


# --------------------------------------------------------------------------- the unit of work


def test_a_store_that_drops_the_step_row_fails_with_a_sentence_naming_the_store():
    """**The shape of the `4a972cc` defect, without a database.**

    `PostgresStore.get()` built its steps list from a cursor whose result set a later query had replaced, so
    every job read back from the durable store had no steps. The engine's position is derived from those rows,
    so the symptom was `the step row for read_message attempt 1 is missing` — a sentence about the engine, from
    a defect in the store, and it sent two people to look at the wrong file.

    This store accepts a step row and never returns it. The engine must say so in those words rather than
    reporting a missing row as its own problem.
    """
    class ForgetfulStore(jobs.MemoryStore):
        """Writes everything, returns a job with no steps — the durable defect, made portable."""

        def get(self, job_id):
            job = super().get(job_id)
            if job is None:
                return None
            thin = copy.deepcopy(job)
            thin.steps = []
            return thin

    store = ForgetfulStore()
    job = jobs.new_job(deployment_id="oi-test", subject="dana", actor_role="staff", roots={"company"},
                       credential_sha256="a" * 64, task="forgetful", input="x",
                       attempt_budget=4, time_budget_s=30)
    store.put(job)
    plan = workflow.Plan(name="forgetful", steps=(
        workflow.StepSpec(name="first", run=lambda run: workflow.StepResult(output={"ok": True})),
    ))
    with pytest.raises(workflow.WorkflowError) as raised:
        workflow.runner_for(store, plan).start(job)

    message = str(raised.value)
    assert "store" in message.lower(), f"a store fault must name the store. The engine said: {message}"
    assert "workflow" not in message.lower() or "store" in message.lower(), (
        "the message must read as a store fault rather than as the engine losing its own work")


def test_the_memory_unit_rolls_back_everything_it_wrote_when_the_body_raises():
    """The memory unit mirrors commit-or-roll-back, so the engine's unit semantics are testable without a
    database. It proves the shape; only Postgres can prove the atomicity."""
    store = jobs.MemoryStore()
    job, _ = make_job()
    store.put(job)
    units = workflow.MemoryUnits(store)

    with pytest.raises(RuntimeError):
        with units() as unit:
            fresh = unit.get(job.job_id)
            fresh.state = jobs.RUNNING
            unit.put(fresh)
            unit.append(job.job_id, "step", {"x": 1})
            unit.record(event_type="tool_call", outcome="ok", data_class="internal", payload={"a": 1})
            raise RuntimeError("the chain write failed")

    assert store.get(job.job_id).state == jobs.QUEUED
    assert store.events_since(job.job_id, 0) == []
    assert units.journal == []


def test_a_committed_unit_leaves_all_three_writes_in_place():
    store = jobs.MemoryStore()
    job, _ = make_job()
    store.put(job)
    units = workflow.MemoryUnits(store)

    with units() as unit:
        fresh = unit.get(job.job_id)
        fresh.state = jobs.RUNNING
        unit.put(fresh)
        unit.append(job.job_id, "step", {"x": 1})
        unit.record(event_type="tool_call", outcome="ok", data_class="internal", payload={"a": 1})

    assert store.get(job.job_id).state == jobs.RUNNING
    assert len(store.events_since(job.job_id, 0)) == 1
    assert len(units.journal) == 1
    assert units.journal[0]["chain"] == "memory-double", (
        "a memory chain row says it is a double, so no test can mistake it for the real chain")
