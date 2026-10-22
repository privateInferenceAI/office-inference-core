"""The in-process agent dispatcher.

These tests run the real worker against the memory store. They prove the claim, the singleton lease, the
concurrency cap, the queue position and the two failure paths. The Postgres claim is exercised by the same
store methods the engine tests use when a database is configured; the dispatcher does not reach around them.
"""
from __future__ import annotations

import itertools
import os
import sys
import threading
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
for extra in (ROOT / "services", ROOT / "services" / "api"):
    if str(extra) not in sys.path:
        sys.path.insert(0, str(extra))

import agent_worker  # noqa: E402
import jobs  # noqa: E402

_LOCK_KEYS = itertools.count(810_000)


def make_store() -> jobs.MemoryStore:
    return jobs.MemoryStore()


def make_job(store, task: str = "research_brief", *, created_at: float | None = None):
    job = jobs.new_job(deployment_id="oi-test", subject="dana", actor_role="staff",
                       roots={"company"}, credential_sha256="a" * 64, task=task, input="x",
                       attempt_budget=20, time_budget_s=600)
    if created_at is not None:
        job.created_at = created_at
    store.put(job)
    return job


def wait_for(predicate, timeout: float = 3.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.01)
    return False


def succeed(store):
    def run(job):
        fresh = store.get(job.job_id)
        jobs.transition(fresh, jobs.SUCCEEDED)
        fresh.result = {"answer": "done"}
        store.put(fresh)
    return run


def test_claim_is_oldest_first_and_bounded_and_queue_position_moves():
    store = make_store()
    first = make_job(store, created_at=1.0)
    second = make_job(store, created_at=2.0)
    third = make_job(store, created_at=3.0)
    assert [store.queue_position(j.job_id) for j in (first, second, third)] == [1, 2, 3]

    claimed = store.claim_queued(2, task_ids={"research_brief"})
    assert [job.job_id for job in claimed] == [first.job_id, second.job_id]
    assert first.state == jobs.RUNNING and second.state == jobs.RUNNING
    assert store.queue_position(third.job_id) == 1
    assert store.queue_position(first.job_id) is None


def test_the_claim_only_takes_the_definition_tasks():
    store = make_store()
    wanted = make_job(store, "research_brief")
    other = make_job(store, "draft")
    claimed = store.claim_queued(5, task_ids={"research_brief"})
    assert [job.job_id for job in claimed] == [wanted.job_id]
    assert store.get(other.job_id).state == jobs.QUEUED


def test_one_worker_starts_and_the_second_refuses_the_lease():
    store = make_store()
    key = next(_LOCK_KEYS)
    first = agent_worker.AgentWorker(store, succeed(store), {"research_brief"}, lock_key=key)
    second = agent_worker.AgentWorker(store, succeed(store), {"research_brief"}, lock_key=key)
    try:
        assert first.start() is True
        assert second.start() is False
        first.stop()
        assert second.start() is True
    finally:
        first.stop()
        second.stop()


def test_the_worker_claims_a_queued_definition_and_succeeds_it():
    store = make_store()
    job = make_job(store)
    worker = agent_worker.AgentWorker(store, succeed(store), {"research_brief"},
                                      lock_key=next(_LOCK_KEYS), poll_s=0.01)
    try:
        assert worker.start() is True
        assert wait_for(lambda: store.get(job.job_id).state == jobs.SUCCEEDED)
        assert store.get(job.job_id).result == {"answer": "done"}
    finally:
        worker.stop()


def test_the_concurrency_cap_is_not_exceeded():
    store = make_store()
    lock = threading.Lock()
    active = 0
    peak = 0
    finished = []

    def run(job):
        nonlocal active, peak
        with lock:
            active += 1
            peak = max(peak, active)
        time.sleep(0.05)
        fresh = store.get(job.job_id)
        jobs.transition(fresh, jobs.SUCCEEDED)
        store.put(fresh)
        with lock:
            active -= 1
            finished.append(job.job_id)

    created = [make_job(store, created_at=float(i)) for i in range(3)]
    worker = agent_worker.AgentWorker(store, run, {"research_brief"}, concurrency=2,
                                      lock_key=next(_LOCK_KEYS), poll_s=0.01)
    try:
        worker.start()
        assert wait_for(lambda: len(finished) == 3)
        assert peak == 2
        assert all(store.get(job.job_id).state == jobs.SUCCEEDED for job in created)
    finally:
        worker.stop()


def test_a_runtime_that_returns_with_the_job_running_fails_it_typed():
    store = make_store()
    job = make_job(store)
    worker = agent_worker.AgentWorker(store, lambda claimed: None, {"research_brief"},
                                      lock_key=next(_LOCK_KEYS), poll_s=0.01)
    try:
        worker.start()
        assert wait_for(lambda: store.get(job.job_id).state == jobs.FAILED)
        assert store.get(job.job_id).outcome_reason == "runtime_returned_running"
        assert "without a terminal state" in store.get(job.job_id).result["error"]
    finally:
        worker.stop()


@pytest.mark.skipif(not os.environ.get("OI_TEST_DATABASE_URL"),
                    reason="needs OI_TEST_DATABASE_URL for the durable claim")
def test_the_postgres_claim_takes_each_job_once():
    """The durable path is the one a deployment uses; `for update skip locked` is exercised for real."""
    url = os.environ["OI_TEST_DATABASE_URL"]
    store = jobs.open_store(url=url, migrations_dir=ROOT / "db" / "migrations")
    task = "research_brief_pg_" + next(_LOCK_KEYS).__str__()
    try:
        created = []
        for index in range(2):
            job = jobs.new_job(deployment_id="oi-test", subject="dana", actor_role="staff",
                               roots={"company"}, credential_sha256="a" * 64, task=task,
                               input=str(index), attempt_budget=5, time_budget_s=60)
            store.put(job)
            created.append(job)
        first = store.claim_queued(1, task_ids={task})
        second = store.claim_queued(1, task_ids={task})
        third = store.claim_queued(1, task_ids={task})
        assert len(first) == 1 and len(second) == 1 and third == []
        assert {job.job_id for job in first + second} == {job.job_id for job in created}
        assert all(store.get(job.job_id).state == jobs.RUNNING for job in created)
        assert store.queue_position(created[0].job_id) is None
    finally:
        store.close()


def test_a_runtime_exception_fails_the_job_and_keeps_the_worker_alive():
    store = make_store()
    first = make_job(store, created_at=1.0)
    second = make_job(store, created_at=2.0)

    def run(job):
        if job.job_id == first.job_id:
            raise RuntimeError("the model gateway is down")
        succeed(store)(job)

    worker = agent_worker.AgentWorker(store, run, {"research_brief"},
                                      lock_key=next(_LOCK_KEYS), poll_s=0.01)
    try:
        worker.start()
        assert wait_for(lambda: store.get(first.job_id).state == jobs.FAILED)
        assert store.get(first.job_id).outcome_reason == "runtime_error"
        assert wait_for(lambda: store.get(second.job_id).state == jobs.SUCCEEDED)
    finally:
        worker.stop()
