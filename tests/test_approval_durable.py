"""The approval decision and its record, against the real durable path.

**Why this file needs Postgres.** The property is a transaction: the job state change, its event rows and the
chain row either all commit or none do. A memory store has no transaction, so a test against one would test the
double. The C5 panel found six defects that a green memory-store suite could not see, and the approval route was
one of them until it was run with a valid session.

    OI_TEST_DATABASE_URL=postgresql://user:pass@host:5432/db python3 -m pytest tests/test_approval_durable.py -q
"""
from __future__ import annotations

import os
import secrets
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
for extra in (ROOT / "services", ROOT / "services" / "api", ROOT / "services" / "gateway"):
    if str(extra) not in sys.path:
        sys.path.insert(0, str(extra))

import approvals  # noqa: E402
import assertion  # noqa: E402
import jobs  # noqa: E402
import workflow.chain as chain_module  # noqa: E402

TEST_URL = (os.environ.get("OI_TEST_DATABASE_URL") or "").strip()
SKIP_WHY = (
    "no test database. These tests need a real Postgres because the property they check is a property of a "
    "transaction. Start one and name it: "
    "docker run -d -p 55432:5432 -e POSTGRES_PASSWORD=wftest -e POSTGRES_USER=wftest -e POSTGRES_DB=wftest "
    "postgres:16-alpine   then   OI_TEST_DATABASE_URL=postgresql://wftest:wftest@127.0.0.1:55432/wftest "
    "python3 -m pytest tests/test_approval_durable.py -q")

pytestmark = pytest.mark.skipif(not TEST_URL, reason=SKIP_WHY)

DEPLOYMENT = "oi-approval-test"


class FakeSession:
    def __init__(self, subject="dana", role="staff"):
        self.subject, self.role = subject, role
        self.assurance = None


@pytest.fixture(scope="module")
def store():
    os.environ.setdefault("CONTENT_DEFAULT", "full")
    key_file = Path("/tmp/oi-approval-test.key")
    if not key_file.exists() or len(key_file.read_text().strip()) < 32:
        key_file.write_text(secrets.token_hex(32))
    original_url = os.environ.get("DATABASE_URL")
    os.environ["OI_ASSERTION_KEY"] = str(key_file)
    os.environ["DATABASE_URL"] = TEST_URL
    os.environ["OI_ASSERTION_NONCES"] = "/tmp/oi-approval-test-nonces.json"
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
    return assertion.load_key(os.environ["OI_ASSERTION_KEY"])


def mint(key):
    return assertion.mint_audit(key, "dana", "staff", deployment_id=DEPLOYMENT)


def at_gate(store):
    job = jobs.new_job(deployment_id=DEPLOYMENT, subject="dana", actor_role="staff", roots={"company"},
                       credential_sha256="a" * 64, task="test_task", input="send the email",
                       data_class="public")
    store.put(job)
    jobs.transition(job, jobs.RUNNING)
    store.put(job)
    jobs.request_approval(job, store, "send the email to ferguson@example.com")
    return job


def decide(store, key, job, decision, reason=""):
    return approvals.decide_and_record(
        store=store, job=job, decision=decision, action_digest_quoted=job.pending_action_digest,
        session=FakeSession(), reason=reason, note="", deployment_id=DEPLOYMENT,
        key_path=os.environ["OI_ASSERTION_KEY"],
        mint=lambda actor, role: assertion.mint_audit(
            key, actor, role, deployment_id=DEPLOYMENT),
    )


def approval_rows(store):
    with store.conn.cursor() as cur:
        cur.execute("""select id, payload, payload_sha256
                         from audit_events
                         where payload->>'stage' = 'approval'
                           and payload->'approval'->>'job_id' is not null
                         order by id""")
        return cur.fetchall()


def test_approve_commits_the_state_and_the_record(store, key):
    job = at_gate(store)
    returned = decide(store, key, job, "approve")
    assert returned.state == jobs.RUNNING
    rows = approval_rows(store)
    matching = [row for row in rows if row[1]["approval"]["job_id"] == job.job_id]
    assert len(matching) == 1, "the decision must have exactly one approval row"
    row = matching[0]
    assert row[1]["approval"]["decision"] == "approve"
    assert row[1]["approval"]["identity"]["assurance"] == "unverified"
    assert row[1]["approval"]["identity"]["basis"] == "session_token"


def test_deny_and_request_changes_keep_their_reason_and_distinct_state(store, key):
    denied = at_gate(store)
    changed = at_gate(store)
    assert decide(store, key, denied, "deny", reason="wrong vendor").outcome_reason == "denied_at_the_gate"
    assert decide(store, key, changed, "request_changes",
                  reason="the total is wrong").outcome_reason == "changes_requested_at_the_gate"
    decisions = {row[1]["approval"]["job_id"]: row[1]["approval"]["decision"] for row in approval_rows(store)}
    assert decisions[denied.job_id] == "deny"
    assert decisions[changed.job_id] == "request_changes"


def test_a_failed_chain_write_rolls_back_the_decision(store, key, monkeypatch):
    """The defect this section exists to close. The state change used to commit before the record."""
    job = at_gate(store)

    def refuse(*args, **kwargs):
        raise RuntimeError("the audit writer is down")

    monkeypatch.setattr(chain_module, "record", refuse)
    with pytest.raises(RuntimeError):
        decide(store, key, job, "approve")

    fresh = store.get(job.job_id)
    assert fresh is not None
    assert fresh.state == jobs.AWAITING_APPROVAL, "a failed record must not leave a decided job"
    assert not [row for row in approval_rows(store) if row[1]["approval"]["job_id"] == job.job_id]


def test_nothing_irreversible_runs_before_approval(store, key, tmp_path):
    """APR-IRREVERSIBLE-001 on the durable path: the far side sees zero before the decision, one after."""
    import workflow

    sink = tmp_path / "sent.txt"
    reads = []

    def read(run):
        reads.append(1)
        return workflow.StepResult(output={"read": True})

    def send(run):
        with sink.open("a") as handle:
            handle.write("sent\n")
        return workflow.StepResult(
            output={"sent": True},
            effects=[workflow.Effect(key="send", mode="external", state="applied",
                                     detail={"applied": True})],
        )

    plan = workflow.Plan(name="approval-effect", steps=(
        workflow.StepSpec(name="read", run=read),
        workflow.StepSpec(name="approval", gate=True,
                          approval_of=lambda run: workflow.Action(
                              kind="email", summary="Send the email to ferguson@example.com",
                              reason="the invoice was approved", target="ferguson@example.com",
                              subject="Invoice 2026-0417", body="Attached.",
                              consequence="an email leaves the deployment")),
        workflow.StepSpec(name="send", run=send),
        workflow.StepSpec(name="close", run=lambda run: workflow.StepResult(output={"closed": True})),
    ))
    job = jobs.new_job(deployment_id=DEPLOYMENT, subject="dana", actor_role="staff",
                       roots={"company"}, credential_sha256="a" * 64, task="approval-effect",
                       input="send the email", data_class="public")
    store.put(job)
    runner = workflow.runner_for(
        store, plan, mint=lambda actor, role: assertion.mint_audit(
            key, actor, role, deployment_id=DEPLOYMENT),
        deployment_id=DEPLOYMENT, key_path=os.environ["OI_ASSERTION_KEY"])
    job = runner.start(job)
    assert job.state == jobs.AWAITING_APPROVAL
    assert not sink.exists(), "the send must not happen before approval"

    decide(store, key, job, "approve")
    job = runner.resume(store.get(job.job_id))
    assert job.state == jobs.SUCCEEDED
    assert len(reads) == 1, "the step before the gate must not run again on resume"
    assert sink.read_text().count("sent") == 1, "the effect must be applied exactly once"
    with store.conn.cursor() as cur:
        cur.execute("select count(*) from workflow_effects where job_id = %s", (job.job_id,))
        assert cur.fetchone()[0] == 1


def test_a_denial_runs_nothing_after_the_gate(store, key):
    import workflow

    sent = []

    def send(run):
        sent.append(1)
        return workflow.StepResult(output={"sent": True})

    plan = workflow.Plan(name="denial-effect", steps=(
        workflow.StepSpec(name="read", run=lambda run: workflow.StepResult(output={"read": True})),
        workflow.StepSpec(name="approval", gate=True,
                          approval_of=lambda run: workflow.Action(
                              kind="email", summary="Send the email", reason="approve the invoice",
                              target="ferguson@example.com", subject="Invoice", body="Attached.",
                              consequence="an email leaves the deployment")),
        workflow.StepSpec(name="send", run=send),
    ))
    job = jobs.new_job(deployment_id=DEPLOYMENT, subject="dana", actor_role="staff",
                       roots={"company"}, credential_sha256="a" * 64, task="denial-effect",
                       input="send the email", data_class="public")
    store.put(job)
    runner = workflow.runner_for(
        store, plan, mint=lambda actor, role: assertion.mint_audit(
            key, actor, role, deployment_id=DEPLOYMENT),
        deployment_id=DEPLOYMENT, key_path=os.environ["OI_ASSERTION_KEY"])
    job = runner.start(job)
    assert job.state == jobs.AWAITING_APPROVAL
    decide(store, key, job, "deny", reason="the vendor is wrong")
    job = runner.resume(store.get(job.job_id))
    assert job.state == jobs.FAILED
    assert sent == [], "nothing after the gate may run after a denial"


def test_a_gate_does_not_expire_into_a_failure(store, key):
    import time

    job = at_gate(store)
    time.sleep(0.3)
    fresh = store.get(job.job_id)
    assert fresh.state == jobs.AWAITING_APPROVAL
    assert fresh.outcome_reason in ("", None)


def test_the_decision_record_is_in_the_chain_and_chains_to_the_previous_row(store, key):
    job = at_gate(store)
    decide(store, key, job, "approve")
    rows = approval_rows(store)
    matching = [row for row in rows if row[1]["approval"]["job_id"] == job.job_id]
    assert len(matching) == 1
    row_id = matching[0][0]
    with store.conn.cursor() as cur:
        cur.execute("select prev_hash, row_hash from audit_events where id = %s", (row_id,))
        prev_hash, row_hash = cur.fetchone()
        cur.execute("select row_hash from audit_events where id < %s order by id desc limit 1", (row_id,))
        prior = cur.fetchone()
    assert prior is not None
    assert prev_hash == prior[0], "the approval row must be linked into the same chain"
    assert row_hash and row_hash != prev_hash
