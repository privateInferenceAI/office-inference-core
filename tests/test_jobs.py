"""The job machine: states, attempts, the event log, and the two things it must not do.

**Why this file is mostly about refusals.** A job runner is easy to write in a way that passes every
happy-path test and is still wrong. It resumes work somebody cancelled. It retries a failure that cannot
succeed. It lets a client watch a spinner on a job that finished twenty minutes ago. Each of those is a
plausible line of code rather than an obvious bug, so each one is asserted here as a refusal rather than
left to review.

**What is not tested here.** The model call. A step is a callable in this design precisely so the machine
can be tested without a deployment, and the callable in every test below is a scripted list of outcomes
rather than anything that reaches a network.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "services" / "api"))

import jobs  # noqa: E402
import routing  # noqa: E402


# --------------------------------------------------------------------------- helpers


def make_job(**kw) -> jobs.Job:
    # **`credential_sha256`, not a session id.** A job holds a hash so it can resolve the session it runs under
    # without the row being a working identity. The first version stored the credential here, which made every
    # job row an impersonation waiting to be read.
    base = dict(deployment_id="oi-test", subject="probe@example.com", actor_role="viewer",
                roots={"company"}, credential_sha256="a" * 64, task="reconcile", input="reconcile these")
    base.update(kw)
    return jobs.new_job(**base)


def scripted(outcomes: list[jobs.StepOutcome]):
    """A step callable that returns the next scripted outcome, and records what it was asked."""
    seen: list[int] = []

    def attempt_once(job, attempt):
        seen.append(attempt)
        index = min(attempt, len(outcomes)) - 1
        return outcomes[index]

    attempt_once.seen = seen
    return attempt_once


def audit_log():
    rows: list[dict] = []

    def audit(event, actor, role, outcome, payload, **extra):
        rows.append({"event": event, "actor": actor, "outcome": outcome, "payload": payload, **extra})

    return audit, rows


def ok(**kw) -> jobs.StepOutcome:
    base = dict(ok=True, name="answer", lane="frontier", model="analyst", result={"answer": "yes"})
    base.update(kw)
    return jobs.StepOutcome(**base)


def bad(kind="throttled", retryable=True, **kw) -> jobs.StepOutcome:
    return jobs.StepOutcome(ok=False, failure_kind=kind, failure_detail="upstream said so",
                            retryable=retryable, **kw)


# --------------------------------------------------------------------------- the machine


def test_a_job_starts_queued_and_is_not_terminal():
    job = make_job()
    assert job.state == jobs.QUEUED
    assert job.terminal is False
    assert job.attempts == 0


@pytest.mark.parametrize("was,wanted", [
    (jobs.QUEUED, jobs.RUNNING),
    (jobs.QUEUED, jobs.CANCELLED),
    (jobs.RUNNING, jobs.PAUSED),
    (jobs.RUNNING, jobs.AWAITING_APPROVAL),
    (jobs.RUNNING, jobs.SUCCEEDED),
    (jobs.RUNNING, jobs.FAILED),
    (jobs.RUNNING, jobs.CANCELLED),
    (jobs.PAUSED, jobs.RUNNING),
    (jobs.PAUSED, jobs.CANCELLED),
    (jobs.AWAITING_APPROVAL, jobs.RUNNING),
])
def test_the_transitions_that_are_allowed(was, wanted):
    job = make_job()
    job.state = was
    assert job.may(wanted) is True
    jobs.transition(job, wanted)
    assert job.state == wanted


@pytest.mark.parametrize("was,wanted", [
    (jobs.SUCCEEDED, jobs.RUNNING),
    (jobs.FAILED, jobs.RUNNING),
    (jobs.CANCELLED, jobs.RUNNING),
    (jobs.CANCELLED, jobs.SUCCEEDED),
    (jobs.SUCCEEDED, jobs.FAILED),
    (jobs.QUEUED, jobs.SUCCEEDED),
    (jobs.QUEUED, jobs.PAUSED),
])
def test_the_transitions_that_are_refused(was, wanted):
    """**The refusals are the feature.** Resume-after-cancel produces a record of work that could not
    have happened, and it looks like a harmless assignment in a handler."""
    job = make_job()
    job.state = was
    with pytest.raises(jobs.TransitionRefused) as caught:
        jobs.transition(job, wanted)
    assert caught.value.was == was and caught.value.wanted == wanted
    assert job.state == was, "a refused transition must not move the job"


def test_a_terminal_job_is_reported_as_terminal():
    for state in (jobs.SUCCEEDED, jobs.FAILED, jobs.CANCELLED):
        job = make_job()
        job.state = state
        assert job.terminal is True
    assert jobs.TERMINAL == frozenset({jobs.SUCCEEDED, jobs.FAILED, jobs.CANCELLED})


def test_a_budget_outside_its_bounds_is_refused_at_creation():
    with pytest.raises(jobs.JobError):
        make_job(time_budget_s=0)
    with pytest.raises(jobs.JobError):
        make_job(time_budget_s=jobs.MAX_TIME_BUDGET_S + 1)
    with pytest.raises(jobs.JobError):
        make_job(attempt_budget=0)
    with pytest.raises(jobs.JobError):
        make_job(attempt_budget=51)


def test_the_permission_is_snapshotted_rather_than_referenced():
    """A job read next week has to describe the permission it ran under, not today's."""
    job = make_job(roots={"company", "executive"})
    assert job.roots == frozenset({"company", "executive"})
    assert isinstance(job.roots, frozenset)


# --------------------------------------------------------------------------- running


def test_one_step_that_succeeds_finishes_the_job_and_records_it():
    store = jobs.MemoryStore()
    audit, rows = audit_log()
    job = make_job()
    store.put(job)

    jobs.run(job, store, scripted([ok()]), audit=audit)

    assert job.state == jobs.SUCCEEDED
    assert job.attempts == 1
    assert job.steps[0].state == jobs.SUCCEEDED
    assert job.result == {"answer": "yes"}
    assert [r["payload"]["state"] for r in rows] == [jobs.RUNNING, jobs.SUCCEEDED, jobs.SUCCEEDED]
    # `egress_allowed` is a column on the audit row rather than a key inside the payload, which is the
    # shape the audit tool takes it in. The first version of this test looked inside the payload and
    # failed on correct code.
    assert rows[-1]["egress_allowed"] is True


def test_an_attempt_that_raises_becomes_a_typed_failure_rather_than_a_crash():
    """A job that dies with a traceback leaves a row saying nothing, and a client watching a spinner."""
    store = jobs.MemoryStore()
    job = make_job(attempt_budget=1)
    store.put(job)

    def explode(job, attempt):
        raise RuntimeError("the model client is not configured")

    jobs.run(job, store, explode)

    assert job.state == jobs.FAILED
    assert job.steps[0].failure_kind == "unhandled"
    assert job.steps[0].failure_detail == "RuntimeError"
    assert job.steps[0].retryable is False


def test_a_retryable_failure_is_retried_up_to_the_budget_and_then_stops():
    """The sentence this whole module exists for: let it fail four times, then give up on the fifth."""
    store = jobs.MemoryStore()
    audit, rows = audit_log()
    job = make_job(attempt_budget=4)
    store.put(job)
    stepper = scripted([bad("throttled", True)])

    jobs.run(job, store, stepper, audit=audit)

    assert job.attempts == 4, "every attempt within the budget should have run"
    assert stepper.seen == [1, 2, 3, 4]
    assert job.state == jobs.FAILED
    assert job.outcome_reason == "attempts_exhausted"
    assert all(s.failure_kind == "throttled" for s in job.steps)
    assert all(s.retryable is True for s in job.steps)
    rows = store.events_since(job.job_id)
    assert rows[-1]["event"] == jobs.EV_DONE
    assert rows[-1]["data"]["reason"] == "attempts_exhausted"


def test_a_failure_that_cannot_succeed_is_not_retried():
    """A retry spent on an unknown failure is spent on a request that probably cannot succeed."""
    store = jobs.MemoryStore()
    job = make_job(attempt_budget=5)
    store.put(job)
    stepper = scripted([bad("refused", False)])

    jobs.run(job, store, stepper)

    assert job.attempts == 1, "a terminal failure must stop the loop"
    assert stepper.seen == [1]
    assert job.state == jobs.FAILED
    assert job.outcome_reason == "refused"


def test_a_retry_that_succeeds_leaves_both_attempts_in_the_record():
    """The interesting case for a client: attempt one failed, attempt two worked, and the screen shows
    both. A record that kept only the success would hide the retry that the operator is paying for."""
    store = jobs.MemoryStore()
    job = make_job(attempt_budget=5)
    store.put(job)

    jobs.run(job, store, scripted([bad("throttled", True), ok()]))

    assert job.attempts == 2
    assert [s.state for s in job.steps] == [jobs.FAILED, jobs.SUCCEEDED]
    assert job.steps[0].failure_kind == "throttled"
    assert job.state == jobs.SUCCEEDED


def test_a_job_cancelled_from_another_connection_stops():
    """**The one thing a stop button must not do is keep working.** The cancel arrives through a different
    connection, so the loop re-reads the row rather than trusting its own copy.

    **The cancel is applied to a *different* object, and that matters.** `MemoryStore.get` returns the same
    instance the loop holds, so calling `jobs.cancel` on it would set `job.state` directly and this test
    would pass with the guard deleted. It cancels a loaded copy instead, which is what actually happens
    when a second request arrives: a row is read, changed, and written. Without the re-read in the loop,
    the runner would carry on and the test would see a second attempt.
    """
    store = jobs.MemoryStore()
    job = make_job(attempt_budget=5)
    store.put(job)

    def cancel_then_fail(job, attempt):
        elsewhere = jobs.MemoryStore()
        elsewhere.jobs = store.jobs
        elsewhere.events = store.events
        jobs.cancel(elsewhere.get(job.job_id), elsewhere)
        return bad("throttled", True)

    jobs.run(job, store, cancel_then_fail)

    assert job.state == jobs.CANCELLED
    assert job.attempts == 1, "the loop should not start a second attempt after the cancel"


def test_the_time_budget_stops_a_job_that_would_run_for_ever():
    store = jobs.MemoryStore()
    job = make_job(attempt_budget=50, time_budget_s=1)
    store.put(job)
    clock = {"t": 0.0}

    class FakeClock:
        pass

    original = jobs.time.monotonic
    jobs.time.monotonic = lambda: clock["t"]  # type: ignore[assignment]
    try:
        def slow(job, attempt):
            clock["t"] += 0.6
            return bad("timeout", True)

        jobs.run(job, store, slow)
    finally:
        jobs.time.monotonic = original  # type: ignore[assignment]

    assert job.state == jobs.FAILED
    assert job.outcome_reason == "time_budget_exhausted"
    assert job.attempts == 2, "it should stop as soon as the budget is gone, not run all fifty"


def test_the_attempt_budget_is_a_ceiling_the_loop_cannot_exceed():
    store = jobs.MemoryStore()
    job = make_job(attempt_budget=3)
    store.put(job)
    stepper = scripted([bad("throttled", True)])

    jobs.run(job, store, stepper, max_attempts=2)

    assert job.attempts == 2
    assert stepper.seen == [1, 2]


# --------------------------------------------------------------------------- the event log


def test_events_are_numbered_from_one_without_a_gap():
    store = jobs.MemoryStore()
    job = make_job()
    store.put(job)

    jobs.run(job, store, scripted([bad("throttled", True), ok()]))

    events = store.events_since(job.job_id)
    assert [e["seq"] for e in events] == list(range(1, len(events) + 1))


def test_a_client_can_catch_up_from_where_it_stopped():
    """**This is why progress is a table rather than a socket.** A client that closed the tab reads
    `?since=` and resumes, and there is no moment at which what happened exists only in a connection."""
    store = jobs.MemoryStore()
    job = make_job()
    store.put(job)

    jobs.run(job, store, scripted([bad("throttled", True), ok()]))

    everything = store.events_since(job.job_id)
    assert len(everything) >= 4
    tail = store.events_since(job.job_id, since=everything[1]["seq"])
    assert [e["seq"] for e in tail] == [e["seq"] for e in everything[2:]]
    assert store.events_since(job.job_id, since=everything[-1]["seq"]) == []


def test_every_event_carries_a_frame_name_a_client_can_act_on():
    store = jobs.MemoryStore()
    job = make_job()
    store.put(job)

    jobs.run(job, store, scripted([ok()]))

    known = {jobs.EV_PROGRESS, jobs.EV_STEP, jobs.EV_NOTICE, jobs.EV_DEGRADED, jobs.EV_DONE,
             jobs.EV_ERROR}
    assert {e["event"] for e in store.events_since(job.job_id)} <= known
    assert store.events_since(job.job_id)[-1]["event"] == jobs.EV_DONE


def test_a_step_event_carries_the_typed_failure_and_the_retry_decision():
    """What an operator counts, and what a client renders. 'It failed' is neither."""
    store = jobs.MemoryStore()
    job = make_job(attempt_budget=1)
    store.put(job)

    jobs.run(job, store, scripted([bad("throttled", True)]))

    failed = [e for e in store.events_since(job.job_id)
              if e["event"] == jobs.EV_STEP and e["data"]["state"] == jobs.FAILED]
    assert len(failed) == 1
    assert failed[0]["data"]["failure"] == {"kind": "throttled", "detail": "upstream said so",
                                            "retryable": True}


def test_a_notice_reaches_the_log_so_a_screen_can_show_it():
    store = jobs.MemoryStore()
    job = make_job()
    store.put(job)

    jobs.run(job, store, scripted([ok(notice="Draft needs the stronger model, so it answered.")]))

    notices = [e for e in store.events_since(job.job_id) if e["event"] == jobs.EV_NOTICE]
    assert len(notices) == 1
    assert "stronger model" in notices[0]["data"]["text"]


# --------------------------------------------------------------------------- the record
#
# C3's lesson, applied before the fact: a control with no evidence in the record is a control nobody can
# audit. Every state change and every attempt writes its own row, and these tests read the row back.


def test_a_job_that_succeeds_leaves_a_row_for_the_start_the_attempt_and_the_finish():
    """**Three rows, not two, and the middle one is the defect this test now exists for.**

    The first version of the runner wrote a row when the job started and a row when it finished, and a row
    per attempt only when an attempt failed. A job that worked first time therefore left two rows that both
    said "job", and nothing in the chain recorded that a step had run at all. The live probe found it: "2
    row(s) name the job, 0 name an attempt".

    It is the C3 lesson in its general form. The columns existed, the flags existed, nobody connected them,
    and the record could not answer the question it is kept for. **A successful attempt is a thing that
    happened**, and a chain that only mentions work when something goes wrong is a chain that tells the
    wrong story about a system that is working.
    """
    store = jobs.MemoryStore()
    audit, rows = audit_log()
    job = make_job()
    store.put(job)

    jobs.run(job, store, scripted([ok()]), audit=audit)

    stages = [r["payload"].get("stage") for r in rows]
    assert stages == ["job", "job", "job"], f"unexpected rows: {stages}"
    assert rows[0]["payload"]["state"] == jobs.RUNNING
    assert rows[1]["payload"]["attempt"] == 1, "the successful attempt has no row of its own"
    assert rows[1]["payload"]["state"] == jobs.SUCCEEDED
    assert rows[2]["payload"]["state"] == jobs.SUCCEEDED
    assert rows[2]["payload"]["attempts"] == 1
    assert rows[-1]["tool"] == "reconcile", "the row names what was run"
    assert rows[-1]["egress_allowed"] is True
    assert rows[-1]["data_class"] == "internal"
    assert all(r["payload"]["job_id"] == job.job_id for r in rows)


def test_every_attempt_that_ran_has_a_row_whether_it_worked_or_not():
    """The general property, so the case above cannot be fixed by special-casing one attempt."""
    store = jobs.MemoryStore()
    audit, rows = audit_log()
    job = make_job(attempt_budget=5)
    store.put(job)

    jobs.run(job, store, scripted([bad("throttled", True), bad("timeout", True), ok()]), audit=audit)

    attempts = [r["payload"]["attempt"] for r in rows if "attempt" in r["payload"]]
    assert attempts == [1, 2, 3], f"attempts recorded: {attempts}"
    assert len(attempts) == job.attempts


def test_every_failed_attempt_leaves_its_own_row():
    store = jobs.MemoryStore()
    audit, rows = audit_log()
    job = make_job(attempt_budget=3)
    store.put(job)

    jobs.run(job, store, scripted([bad("throttled", True)]), audit=audit)

    attempts = [r for r in rows if r["payload"].get("attempt")]
    assert [r["payload"]["attempt"] for r in attempts] == [1, 2, 3]
    assert all(r["outcome"] == "error" for r in attempts)
    assert all(r["payload"]["retryable"] is True for r in attempts)
    assert rows[-1]["payload"]["reason"] == "attempts_exhausted"


def test_the_row_never_carries_the_raw_question_or_a_provider_string():
    """A row is readable by anyone who can read the job, and a validation error can echo input."""
    store = jobs.MemoryStore()
    audit, rows = audit_log()
    job = make_job(input="the client's SSN is 219-09-9999")
    store.put(job)

    jobs.run(job, store, scripted([bad("throttled", False)]), audit=audit)

    blob = repr(rows)
    assert "219-09-9999" not in blob
    assert "upstream said so" not in blob, "the provider's own wording stays out of the row"


def test_a_lane_of_none_is_not_recorded_as_egress():
    store = jobs.MemoryStore()
    audit, rows = audit_log()
    job = make_job()
    store.put(job)

    jobs.run(job, store, scripted([ok(lane="local", model="local-model")]), audit=audit)

    assert rows[-1]["egress_allowed"] is False


# --------------------------------------------------------------------------- the store it is not


def test_a_store_with_no_database_url_is_the_memory_one_and_says_it_is_not_durable():
    store = jobs.open_store(url="")
    assert isinstance(store, jobs.MemoryStore)
    assert store.durable is False


def test_the_memory_store_round_trips_a_job():
    store = jobs.MemoryStore()
    job = make_job()
    store.put(job)
    assert store.get(job.job_id) is job
    assert store.get("nothing") is None


def test_cancelling_keeps_the_rows():
    """Cancelling deletes nothing. The rows are the reason a job exists rather than a request."""
    store = jobs.MemoryStore()
    job = make_job()
    store.put(job)
    jobs.transition(job, jobs.RUNNING)
    store.put(job)
    store.append(job.job_id, jobs.EV_STEP, {"attempt": 1, "state": jobs.RUNNING})

    jobs.cancel(job, store)

    assert job.state == jobs.CANCELLED
    assert len(store.events_since(job.job_id)) == 2
    assert store.events_since(job.job_id)[-1]["data"]["state"] == jobs.CANCELLED


# --------------------------------------------------------------------------- where the task list lives
#
# The list of tasks used to be a frozenset in the routing module and nothing else. A client app could not
# discover them, could not label them, and could not say what they touch, so the Do screen would have had to
# be written into the app. These tests hold the list to the manifest instead.


def test_an_absent_block_keeps_the_deployment_behaving_as_it_did():
    """A manifest written before this block existed must not lose its escalation."""
    assert routing.declared_tasks({}) == routing.AGENTIC_TASKS
    assert "reconcile" in routing.declared_tasks({"agentic": {}})


def test_an_empty_list_means_no_tasks_rather_than_the_default_set():
    """**The distinction that makes the empty list honest.**

    `tasks: []` is a deployment saying it has no tasks. Falling back to the built-in six would offer a
    client a menu of work this deployment cannot do, which is the failure the block exists to prevent.
    """
    assert routing.declared_tasks({"agentic": {"tasks": []}}) == frozenset()


def test_tasks_declared_as_objects_and_as_bare_ids_both_read():
    manifest = {"agentic": {"tasks": [
        {"id": "draft", "label": "Draft a reply", "tier": "front_desk"},
        "compare",
    ]}}
    assert routing.declared_tasks(manifest) == frozenset({"draft", "compare"})


def test_the_trigger_uses_the_manifest_and_not_the_constant():
    """A task the deployment does not declare must not escalate, however familiar its name is."""
    from routing import TRIGGER_AGENTIC, TRIGGER_USER

    manifest = {
        "agentic": {"tasks": [{"id": "draft"}]},
        "access": {"egress": {"frontier_classes": ["public", "internal"]}},
    }
    declared = routing.route(manifest, data_class="internal", task="draft", model_name="analyst")
    assert declared.trigger == TRIGGER_AGENTIC

    undeclared = routing.route(manifest, data_class="internal", task="multi_step", model_name="analyst")
    assert undeclared.trigger == "", "multi_step is in the built-in set and not in this manifest"
    assert undeclared.trigger != TRIGGER_USER


def test_the_dev_deployment_declares_tasks_that_are_honest_about_what_they_do():
    """The C4 decision, asserted rather than described.

    **The first version of this test asserted the opposite**, on the reasoning that a task which cannot do the
    work teaches a client the feature is broken, so the dev manifest declared none. That made the job path
    untestable on the only deployment that exists, and a path nothing exercises is a path nobody knows works.

    What replaced it is the rule that matters: every task this deployment offers names a tier that exists, and
    its label describes one retrieval-and-answer step rather than promising a loop that arrives in C6.
    """
    import yaml

    manifest = yaml.safe_load((ROOT / "manifests" / "dev-cloud.yaml").read_text())
    tasks = manifest["agentic"]["tasks"]
    assert tasks, "the dev deployment declares no tasks, so the job path is never exercised"

    roles = set(manifest["models"])
    for task in tasks:
        assert task["label"] != task["id"], f"{task['id']} has no label a person would read"
        assert task["tier"] in roles, (
            f"{task['id']} names tier {task['tier']!r}, which this manifest does not declare. A task "
            "pointing at a role that does not exist is a menu entry that cannot run")
        assert task["description"], f"{task['id']} does not say what it does"
        # Nothing in this deployment writes, so nothing needs an approval gate. When something does, its
        # task flips this flag and the interface renders it.
        assert task["needs_approval"] is False

    assert routing.declared_tasks(manifest) == frozenset(t["id"] for t in tasks)


def test_a_manifest_that_declares_tasks_offers_them_to_the_app():
    """The other half: when the pack is there, the menu comes from the manifest rather than from code."""
    import yaml

    manifest = yaml.safe_load((ROOT / "manifests" / "example-compliant-cloud.yaml").read_text())
    ids = routing.declared_tasks(manifest)
    assert "draft" in ids and "reconcile" in ids
    labels = {t["id"]: t["label"] for t in manifest["agentic"]["tasks"]}
    assert labels["draft"] == "Draft a reply", "a label a person reads, not the identifier"
    assert labels["draft"] != "draft"
