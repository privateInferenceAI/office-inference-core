"""The chain row, the transaction, and the two writers that must produce one record.

**Why these tests need a database and say so.** The property WF-ENGINE-001 asks for is a property of a
transaction. A memory store has none, so a test of it against a memory store would be a test of the test
double. These run against a real Postgres when one is named, and **skip with the variable to set when it is
not**, rather than reporting a right green that nothing checked.

    OI_TEST_DATABASE_URL=postgresql://user:pass@host:5432/db python3 -m pytest tests/test_workflow_chain.py -q

**Why it is `OI_TEST_DATABASE_URL` and not `DATABASE_URL`.** These tests write rows into an append-only chain.
Pointing them at a deployment would put probe rows into a client's record, which is the one table in the system
that cannot be tidied afterwards. Naming the variable separately means a deployment's own `DATABASE_URL` is
never picked up by accident.

The process-kill version of the same property is `tests/fixtures/workflow_probe.py`, which is the evidence the
runbook pastes. This file is the version that runs in a suite, and it tests the rollback with an exception
rather than a signal. Both are the same claim; a signal is the harsher test.
"""
from __future__ import annotations

import argparse
import io
import json
import os
import sys
from contextlib import redirect_stdout
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
for extra in (ROOT / "services", ROOT / "services" / "api", ROOT / "services" / "gateway"):
    if str(extra) not in sys.path:
        sys.path.insert(0, str(extra))

import approvals  # noqa: E402
import assertion  # noqa: E402
import jobs  # noqa: E402
import workflow  # noqa: E402
import workflow.chain as chain_module  # noqa: E402
from workflow import plans  # noqa: E402

TEST_URL = (os.environ.get("OI_TEST_DATABASE_URL") or "").strip()
SKIP_WHY = (
    "no test database. These tests need a real Postgres because the property they check is a property of a "
    "transaction, and a memory store has none. Start one and name it: "
    "docker run -d -p 55432:5432 -e POSTGRES_PASSWORD=wftest -e POSTGRES_USER=wftest -e POSTGRES_DB=wftest "
    "postgres:16-alpine   then   OI_TEST_DATABASE_URL=postgresql://wftest:wftest@127.0.0.1:55432/wftest "
    "python3 -m pytest tests/test_workflow_chain.py -q")

pytestmark = pytest.mark.skipif(not TEST_URL, reason=SKIP_WHY)

INVOICE = "INV-2026-0417 from accounts@ferguson.example total 4210.00 USD due 2026-11-15"


@pytest.fixture(scope="module")
def store():
    """A migrated store, and the environments the chain writer reads.

    Migrations run here rather than being assumed, so this file is the thing that would catch migration 021 or
    023 being wrong. `CONTENT_DEFAULT=full` keeps the payload, which the feed assertions read; a deployment's
    own mode comes from its rendered environment and this fixture does not touch it.
    """
    os.environ.setdefault("CONTENT_DEFAULT", "full")
    key_file = Path(os.environ.get("OI_TEST_ASSERTION_KEY", "/tmp/oi-workflow-test.key"))
    if not key_file.exists() or len(key_file.read_text().strip()) < 32:
        import secrets

        key_file.write_text(secrets.token_hex(32))
    original_url = os.environ.get("DATABASE_URL")
    os.environ["OI_ASSERTION_KEY"] = str(key_file)
    # **Forced, not defaulted.** `cmd_record` reads `DATABASE_URL` and opens its own connection, and the
    # equivalence test below writes one row through it. Leaving whatever the environment had would let this
    # suite put a row into a deployment's chain, which is the one table that cannot be tidied afterwards.
    os.environ["DATABASE_URL"] = TEST_URL
    os.environ["OI_ASSERTION_NONCES"] = "/tmp/oi-workflow-test-nonces.json"
    store = jobs.PostgresStore(TEST_URL)
    store.migrate(ROOT / "db" / "migrations")
    yield store
    store.close()
    if original_url is None:
        os.environ.pop("DATABASE_URL", None)
    else:
        os.environ["DATABASE_URL"] = original_url


@pytest.fixture()
def key(store):
    """The signing key, **after** the store fixture has put it in the environment.

    The dependency is not decoration: `load_key` reads `OI_ASSERTION_KEY`, and a fixture that ran first would
    look at the deployment's default path and refuse, which reads as a missing key rather than as an ordering
    mistake.
    """
    return assertion.load_key(os.environ["OI_ASSERTION_KEY"])


def mint(key, actor="dana", role="staff"):
    return assertion.mint_audit(key, actor, role, deployment_id="oi-workflow-test")


def make_job(store, **kw):
    base = dict(deployment_id="oi-workflow-test", subject="dana", actor_role="staff", roots={"company"},
                credential_sha256="a" * 64, task="invoice_intake", input=INVOICE, data_class="public",
                **plans.budgets_for("invoice_intake"))
    base.update(kw)
    job = jobs.new_job(**base)
    store.put(job)
    return job


def runner_for(store, plan, key):
    return workflow.runner_for(store, plan, mint=lambda a, r: mint(key, a, r),
                               deployment_id="oi-workflow-test",
                               key_path=os.environ["OI_ASSERTION_KEY"])


def rows(store, sql, params=()):
    with store.conn.cursor() as cur:
        cur.execute(sql, params)
        return cur.fetchall()


# --------------------------------------------------------------------------- the same-transaction property


def test_a_chain_write_that_fails_rolls_back_the_state_change_with_it(store, key, monkeypatch):
    """**WF-ENGINE-001, as a test that fails if the two writes are ever separated.**

    The chain write is made to fail with an exception instead of a signal. If the state change and the chain
    row are in one transaction, the rollback takes the state change with it and the job is still `queued` with
    no step rows. If somebody moves the state write outside the unit, the job is left `running` with a step row
    and no chain row, which is the gap this piece exists to close.
    """
    job = make_job(store)
    job_id = job.job_id

    def refuse(cur, **row):
        raise RuntimeError("the chain row could not be written")

    monkeypatch.setattr(chain_module, "record", refuse)
    with pytest.raises(RuntimeError):
        runner_for(store, plans.INVOICE_INTAKE, key).start(job)
    monkeypatch.undo()

    state = rows(store, "select state from jobs where job_id = %s", (job_id,))
    steps = rows(store, "select name from job_steps where job_id = %s", (job_id,))
    recorded = rows(store, "select id from audit_events where payload @> %s::jsonb",
                    (json.dumps({"job_id": job_id}),))
    assert state and state[0][0] == jobs.QUEUED, (
        "the state change outlived the chain write, so they are not in one transaction")
    assert not steps and not recorded, "neither may be present when one of them failed"


def test_the_same_run_without_the_failure_leaves_both(store, key):
    """The control, so the test above cannot pass on an engine that simply never writes anything."""
    job = make_job(store)
    job = runner_for(store, plans.INVOICE_INTAKE, key).start(job)
    steps = rows(store, "select name from job_steps where job_id = %s", (job.job_id,))
    recorded = rows(store, "select payload->>'step' from audit_events where payload @> %s::jsonb",
                    (json.dumps({"job_id": job.job_id}),))
    assert job.state == jobs.AWAITING_APPROVAL
    assert len(steps) == 4, "the gate is reached, and its own row is there"
    assert len(recorded) >= 4, "every step that ran has a chain row naming the job"


def test_every_step_row_has_a_chain_row_and_every_chain_row_names_the_job(store, key):
    """AUD-WF-001: a step that ran with no chain row fails, and so does a row that cannot be traced to a job."""
    job = make_job(store)
    job = runner_for(store, plans.INVOICE_INTAKE, key).start(job)
    jobs.decide(job, store, "approve", job.pending_action_digest)
    job = runner_for(store, plans.INVOICE_INTAKE, key).resume(store.get(job.job_id))

    step_names = [row[0] for row in rows(store, "select name from job_steps where job_id = %s", (job.job_id,))]
    chain_steps = {row[0] for row in rows(
        store, "select payload->>'step' from audit_events where payload @> %s::jsonb",
        (json.dumps({"job_id": job.job_id}),))}
    assert job.state == jobs.SUCCEEDED
    assert set(step_names) <= chain_steps, "a step ran with no chain row"
    assert chain_steps >= {"read_message", "extract_fields", "draft_action", "approval", "post_entry",
                           "close_out"}


def test_a_resumed_run_does_not_repeat_a_step_the_chain_already_records(store, key):
    """WF-RESUME-001's second half, which the digest alone does not cover."""
    job = make_job(store)
    job = runner_for(store, plans.INVOICE_INTAKE, key).start(job)
    jobs.decide(job, store, "approve", job.pending_action_digest)
    job = runner_for(store, plans.INVOICE_INTAKE, key).resume(store.get(job.job_id))

    counts = dict(rows(store, """select name, count(*) from job_steps where job_id = %s
                                  group by name""", (job.job_id,)))
    assert counts["post_entry"] == 1 and counts["close_out"] == 1, (
        f"each step after the gate has exactly one row, and this job has {counts}")
    attempts = rows(store, "select count(*) from job_steps where job_id = %s", (job.job_id,))
    assert attempts[0][0] == 6, "six steps, six rows"


# --------------------------------------------------------------------------- one writer


def test_the_engine_and_the_cli_write_the_same_record(store, key):
    """**One INSERT, two callers, one digest.**

    `cmd_record` and the engine's writer both go through `insert_event`, so the record they produce for the
    same event has to be identical. This asserts it rather than trusting it: a second INSERT statement with a
    different column list is exactly the divergence the audit tool's own comment forbids.
    """
    shared = {"event_type": "tool_call", "outcome": "ok", "data_class": "public",
              "payload": {"stage": "workflow", "check": "equivalence", "job_id": "compare-1"},
              "tool": "compare"}

    # Through the CLI path.
    args = argparse.Namespace(
        type=shared["event_type"], actor=None, actor_role="staff", deployment_id="oi-workflow-test",
        assertion=mint(key), assertion_key=os.environ["OI_ASSERTION_KEY"], dev=False,
        payload=json.dumps(shared["payload"], sort_keys=True), store_payload=False,
        data_class=shared["data_class"], outcome=shared["outcome"], prompt=None, completion=None,
        redactions=None, egress_allowed=None, approval=None, unverified_card_shapes=0, request_id=None,
        tool=shared["tool"], model=None)
    tool = chain_module.audit_tool()
    buffer = io.StringIO()
    with redirect_stdout(buffer):
        assert tool.cmd_record(args) == 0
    from_cli = json.loads(buffer.getvalue())

    # Through the engine's writer, on a cursor of our own.
    with store.conn.cursor() as cur:
        from_engine = chain_module.record(
            cur, statement=mint(key), event_type=shared["event_type"], outcome=shared["outcome"],
            data_class=shared["data_class"], deployment_id="oi-workflow-test",
            payload=shared["payload"], tool_name=shared["tool"],
            key_path=os.environ["OI_ASSERTION_KEY"])

    assert from_engine["payload_sha256"] == from_cli["payload_sha256"], (
        "the two writers disagree about what the payload hashes to, which means one of them applies the "
        "content mode differently")
    assert from_engine["row_hash"] and from_cli["row_hash"]
    assert from_engine["row_hash"] != from_cli["row_hash"], (
        "each row's hash covers the previous row, so two rows must not share one")


def test_a_forged_statement_is_refused_and_nothing_is_written(store):
    """The actor is a claim the engine proves. A caller that can type one can name somebody else."""
    before = rows(store, "select count(*) from audit_events")[0][0]
    with store.conn.cursor() as cur:
        with pytest.raises(chain_module.ChainRefused):
            chain_module.record(cur, statement="not.a.statement", event_type="tool_call", outcome="ok",
                                data_class="public", deployment_id="oi-workflow-test",
                                payload={"job_id": "forged"}, tool_name="forged",
                                key_path=os.environ["OI_ASSERTION_KEY"])
    after = rows(store, "select count(*) from audit_events")[0][0]
    assert after == before, "a refused statement must not leave a half-written row behind"


# --------------------------------------------------------------------------- the sentence


class _Session:
    """A session with the two fields the record reads, and no more."""

    def __init__(self, subject="dana", role="staff", assurance=None):
        self.subject, self.role = subject, role
        if assurance is not None:
            self.assurance = assurance


def _approval_row(store, key, decision, stored_assurance=None):
    """One approval record, written the way the route writes it.

    `stored_assurance` is what lands **in the record**, which is not always what the session claimed: that
    difference is the case the downward-resolution rule exists for. The payload is patched before the write so
    the stored row is what the feed actually reads.
    """
    job = make_job(store)
    job.state = jobs.RUNNING
    jobs.request_approval(job, store, "post the entry")

    payload = approvals.build_payload(_Session(), job, decision, "because", "",
                                      job.pending_action_digest,
                                      signed_at="2026-10-16T16:20:00+00:00")
    if stored_assurance is not None:
        payload["approval"]["identity"]["assurance"] = stored_assurance
    with store.conn.cursor() as cur:
        # `outcome` is the outcome of the recording, not the decision. The constraint on the column allows
        # only ok/denied/error/pending, and the decision belongs in the payload, where it already is.
        written = chain_module.record(cur, statement=mint(key), event_type="approval", outcome="ok",
                                      data_class="public", deployment_id="oi-workflow-test",
                                      payload=payload, tool_name=job.task,
                                      key_path=os.environ["OI_ASSERTION_KEY"])
    return written["id"], payload


@pytest.mark.parametrize("decision", ["approve", "deny", "request_changes"])
def test_the_feed_and_the_api_say_the_same_sentence(store, key, decision):
    """**UI-FEED-001, and the shape defined twice.**

    The view assembles the sentence in SQL and `approvals.describe()` assembles it in Python. Two
    implementations of one sentence drift, so this compares them for each decision. The record is unverified,
    which is the only assurance this build can reach.
    """
    row_id, payload = _approval_row(store, key, decision)
    summary = rows(store, "select summary from audit_feed where id = %s", (row_id,))[0][0]
    assert summary == approvals.describe(payload), (
        "the feed and the API disagree about the same record, so one of them is telling a client something "
        "the other is not")


@pytest.mark.parametrize("decision", ["approve", "deny", "request_changes"])
def test_the_feed_never_claims_an_identified_person_while_the_basis_is_a_session(store, key, decision):
    row_id, _ = _approval_row(store, key, decision)
    summary = rows(store, "select summary from audit_feed where id = %s", (row_id,))[0][0]
    assert "dana" not in summary.lower(), (
        "an unverified claim must not sit where a verified name would sit")
    assert "not identified" in summary, "the sentence must say what is missing, not only omit it"


def test_the_feed_names_a_person_only_when_the_record_says_it_verified_one(store, key):
    """The other direction, so the rule above is not simply 'never name anybody'."""
    row_id, _ = _approval_row(store, key, "approve", stored_assurance="proofed")
    summary = rows(store, "select summary from audit_feed where id = %s", (row_id,))[0][0]
    assert "dana" in summary.lower()


def test_a_value_the_reader_does_not_recognise_lands_in_the_honest_branch(store, key):
    """Resolving upward would let a typo, an old schema or a hand-edited row claim a verification.

    **And it is the case the design document's suggested expression gets wrong invisibly.** That expression
    reads `payload -> 'identity'`; the record nests identity under `approval`, so the path is SQL null, every
    row takes this branch, and a verified approval renders as unverified. Fail-safe, and wrong. The test below
    in `test_the_feed_names_a_person_only_when_the_record_says_it_verified_one` is what makes the difference
    visible: the two cannot both pass on a path that never resolves.
    """
    row_id, _ = _approval_row(store, key, "approve", stored_assurance="PROOFED")
    summary = rows(store, "select summary from audit_feed where id = %s", (row_id,))[0][0]
    assert "not identified" in summary, (
        "an assurance value this build does not recognise must resolve downward, never upward")
    assert "dana" not in summary.lower()
