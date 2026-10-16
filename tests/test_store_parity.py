"""The two stores, answering the same questions the same way.

**Why this file exists, and the shape it is written against.** `MemoryStore` and `PostgresStore` are two
implementations of one interface. Every test in this repository except the probes runs against the memory one,
so a durable path that disagrees with it is a path no test covers. That has now happened five times in one
phase, and each occurrence looked like a different bug:

| what broke | what a parity test would have said |
|---|---|
| `pending_action_digest` written on the dataclass and in neither the INSERT nor the SELECT | the job came back from one store with the digest and from the other without it |
| `Jsonb` used and not imported | one store raised `NameError` on a write the other accepted |
| `events_through` added to `get()` and clobbering the step rows | one store returned no steps, the other returned them |
| `pending_action` written and not read back | the action was on the job in memory and absent from the row |
| a stale response after a resume | the same state read from two stores and differing |

**The rule this file encodes: one job, written through both stores, read back, compared field for field.** It
is deliberately not a test of the engine or of the chain — it is a test of the seam that keeps breaking, and it
needs no plan, no gate and no model to run.

    OI_TEST_DATABASE_URL=postgresql://user:pass@host:5432/db python3 -m pytest tests/test_store_parity.py -q

Without that variable the tests **skip with the reason and the command to start a database**, which is a
skipped right and not a held one. It is `OI_TEST_DATABASE_URL` and not `DATABASE_URL` on purpose: these tests
write rows, and a deployment's own variable would put them into a client's record.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
for extra in (ROOT / "services", ROOT / "services" / "api"):
    if str(extra) not in sys.path:
        sys.path.insert(0, str(extra))

import jobs  # noqa: E402

TEST_URL = (os.environ.get("OI_TEST_DATABASE_URL") or "").strip()
SKIP_WHY = (
    "no test database. This file compares the durable store against the memory one, and there is nothing to "
    "compare without a database. Start one and name it: "
    "docker run -d -p 55432:5432 -e POSTGRES_PASSWORD=wftest -e POSTGRES_USER=wftest -e POSTGRES_DB=wftest "
    "postgres:16-alpine   then   OI_TEST_DATABASE_URL=postgresql://wftest:wftest@127.0.0.1:55432/wftest "
    "python3 -m pytest tests/test_store_parity.py -q")

pytestmark = pytest.mark.skipif(not TEST_URL, reason=SKIP_WHY)


@pytest.fixture(scope="module")
def durable():
    store = jobs.PostgresStore(TEST_URL)
    store.migrate(ROOT / "db" / "migrations")
    yield store
    store.close()


def write_one(store) -> str:
    """One job with everything a caller can read: two steps, a digest, an action, and three events.

    Written through the store's own interface rather than with SQL, because the point is to compare what the
    interface returns, not what the table holds. The table is checked separately below, since a store that
    writes correctly and reads back nothing is exactly the failure this file was written for.
    """
    job = jobs.new_job(deployment_id="store-parity", subject="dana", actor_role="staff", roots={"company"},
                       credential_sha256="a" * 64, task="invoice_intake", input="INV-2026-0417",
                       attempt_budget=7, time_budget_s=130)
    store.put(job)
    job.steps.append(jobs.Step(step_id=job.job_id + "-1", job_id=job.job_id, attempt=1,
                               name="read_message", state=jobs.SUCCEEDED, duration_ms=3))
    job.steps.append(jobs.Step(step_id=job.job_id + "-2", job_id=job.job_id, attempt=2,
                               name="extract_fields", state=jobs.FAILED,
                               failure_kind="unreadable_invoice", failure_detail="the total could not be read",
                               retryable=False))
    job.pending_action_digest = "sha256:deadbeef"
    job.pending_action = {"kind": "ledger.post", "summary": "Post USD 4,210.00"}
    job.state = jobs.AWAITING_APPROVAL
    store.put(job)
    for index, name in enumerate(("progress", "step", "step"), start=1):
        store.append(job.job_id, name, {"n": index})
    return job.job_id


def observable(job) -> dict:
    """Everything a caller branches on, in one dictionary, so a difference is one line of output."""
    return {
        "state": job.state,
        "task": job.task,
        "input": job.input,
        "attempts": job.attempts,
        "steps": [(step.name, step.attempt, step.state, step.failure_kind, step.retryable)
                  for step in job.steps],
        "pending_action_digest": job.pending_action_digest,
        "pending_action": job.pending_action,
        "terminal": job.terminal,
        "time_budget_s": job.time_budget_s,
        "attempt_budget": job.attempt_budget,
        "subject": job.subject,
        "actor_role": job.actor_role,
        "data_class": job.data_class,
    }


def test_the_two_stores_agree_on_everything_a_caller_reads(durable):
    """**The whole file in one assertion.** Same job, two stores, one comparison.

    This is the test that fails on `4a972cc`, where `PostgresStore.get()` built its steps list from a cursor
    whose result set a later `execute` had replaced: the memory store returned two steps, the durable one
    returned none, and every durable read of a job was therefore a job with no history.
    """
    memory = jobs.MemoryStore()
    from_memory = observable(memory.get(write_one(memory)))
    from_durable = observable(durable.get(write_one(durable)))

    differences = {key: (from_memory[key], from_durable[key])
                   for key in from_memory if from_memory[key] != from_durable[key]}
    assert not differences, (
        "the memory store and the durable store disagree about the job they were both given, so every test "
        f"in this repository is testing the wrong one. Different: {differences}")


def test_the_steps_that_were_written_are_the_steps_that_come_back(durable):
    """The same failure, stated so that its output names the table as well as the store.

    A store that writes a row and does not read it back looks like an engine that cannot find its own work —
    which is what the 500 on `POST /jobs` said, and it sent two people looking at the engine.
    """
    job_id = write_one(durable)
    with durable.conn.cursor() as cur:
        cur.execute("select name, attempt, state from job_steps where job_id = %s order by attempt", (job_id,))
        in_the_table = cur.fetchall()
    read_back = [(step.name, step.attempt, step.state) for step in durable.get(job_id).steps]

    assert in_the_table == [("read_message", 1, jobs.SUCCEEDED),
                            ("extract_fields", 2, jobs.FAILED)], "the rows were not written at all"
    assert read_back == in_the_table, (
        f"job_steps holds {in_the_table} and the store returned {read_back}. A store that writes a row it "
        f"cannot read back is a store whose readers see a job with no history.")


def test_a_read_after_another_query_on_the_same_cursor_still_returns_the_steps(durable):
    """**The shape, isolated.** Two `execute` calls on one cursor, the rows read after the second.

    `cursor.fetchall()` reads the result set of the most recent `execute`. `PostgresStore.get()` reads the step
    rows and then asks for the event cursor on the same cursor before building its list, so the list was built
    from the wrong query. Any future reader that interleaves queries this way fails here in three lines rather
    than in a traceback through the engine.
    """
    job_id = write_one(durable)
    with durable.conn.cursor() as cur:
        cur.execute("select name from job_steps where job_id = %s order by attempt", (job_id,))
        first = cur.fetchall()
        cur.execute("select coalesce(max(seq), 0) from job_events where job_id = %s", (job_id,))
        through = cur.fetchone()[0]
        second = cur.fetchall()

    assert through == 3, "the second query is what the event cursor is computed from"
    assert first == [("read_message",), ("extract_fields",)], "the step rows were read before the second query"
    assert second == [], (
        "reading after a second execute returns the second query's rows, which is the defect this test "
        "documents. A caller that wants both must fetch the first result set before running the second query.")


def test_events_through_never_claims_more_than_exists(durable):
    """**The rule `events_through` has to obey, and the one place the two stores legitimately differ.**

    The field is where a client resumes from. A value higher than the last event makes a client skip events,
    which loses progress silently; a value lower makes it replay, which is safe. `PostgresStore` returns the
    highest seq it holds. `MemoryStore` does not maintain the field and returns 0, which is the safe direction
    and is a divergence rather than a bug — recorded here so a reader finds it rather than discovering it, and
    so that a change to either store has to keep the rule.
    """
    durable_id = write_one(durable)
    durable_job = durable.get(durable_id)
    durable_seqs = [event["seq"] for event in durable.events_since(durable_id, 0)]
    assert durable_seqs, "the events were not written, so this test would prove nothing"
    assert durable_job.events_through == durable_seqs[-1], (
        "the durable store does not report the last event it holds, so a client resuming from it would miss "
        "everything after it")

    memory = jobs.MemoryStore()
    memory_id = write_one(memory)
    memory_job = memory.get(memory_id)
    memory_seqs = [event["seq"] for event in memory.events_since(memory_id, 0)]
    assert 0 <= memory_job.events_through <= memory_seqs[-1], (
        f"the memory store reports events_through={memory_job.events_through} with {memory_seqs[-1]} event(s) "
        f"written, so a client resuming from it would skip events")


def test_pending_approval_answers_the_same_question_in_both_stores(durable):
    """The queue the Review screen reads: the newest method, and the same shape as the oldest.

    **Scoped to the jobs this test made.** The first version asserted a total count, and on a database that the
    probes and the chain tests had already written to it reported 41 waiting jobs against the memory store's 2 —
    a failure about the test's assumption rather than about the store. A shared table means a test may only
    make claims about rows it created.
    """
    memory = jobs.MemoryStore()
    made: dict[str, tuple] = {}
    for name, store in (("memory", memory), ("durable", durable)):
        ids = {jobs.AWAITING_APPROVAL: [], jobs.RUNNING: [], jobs.SUCCEEDED: []}
        for state in (jobs.RUNNING, jobs.AWAITING_APPROVAL, jobs.AWAITING_APPROVAL, jobs.SUCCEEDED):
            job = jobs.new_job(deployment_id="store-parity", subject="dana", actor_role="staff",
                               roots={"company"}, credential_sha256="b" * 64, task="invoice_intake",
                               input="queue", attempt_budget=7, time_budget_s=130)
            job.state = state
            store.put(job)
            ids[state].append(job.job_id)
        made[name] = (store, ids)

    for name, (store, ids) in made.items():
        queue = {job.job_id for job in store.pending_approval()}
        assert set(ids[jobs.AWAITING_APPROVAL]) <= queue, (
            f"the {name} queue left out a job that is waiting at a gate")
        assert not (set(ids[jobs.RUNNING]) | set(ids[jobs.SUCCEEDED])) & queue, (
            f"the {name} queue handed back a job that is not waiting, which is a screen asking somebody to "
            f"decide something that is already decided")
        assert all(store.get(job_id).state == jobs.AWAITING_APPROVAL for job_id in queue), (
            f"the {name} queue returned a job whose state is not awaiting_approval")
