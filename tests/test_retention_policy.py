"""Time-based retention: a period runs out and the material is destroyed, including the client's copy.

DATA-RETAIN-001. The tests are shaped around the half a job built by reusing the purge would miss: the object
store. `DATA-PURGE-001` deliberately leaves the client's file, because a client asking us to stop holding a copy
still has their document. Retention is a period running out, and "keep invoices for seven years" means after
seven years there is no invoice.

Run: python3 -m pytest tests/
"""
from __future__ import annotations

import sys
import types
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "services" / "retention"))

import policy  # noqa: E402


NOW = datetime(2026, 10, 13, 12, 0, tzinfo=timezone.utc)


class FakeCursor:
    def __init__(self, connection):
        self.connection = connection
        self.rowcount = 0

    def execute(self, query, params=None):
        self.connection.executed.append((" ".join(query.split()), params))
        flat = " ".join(query.split()).lower()
        if "from retention_policies" in flat:
            self._result = self.connection.policy_row
        elif "from ingest_manifest" in flat:
            self._result = self.connection.documents
            self.rowcount = len(self.connection.documents)
        elif "from legal_holds" in flat:
            held = self.connection.held or []
            doc = (params or [None])[0]
            self._result = [(1,)] if (doc in held or "__deployment__" in held) else None
        elif "select count(*) from conversations" in flat:
            self._result = (self.connection.expired_conversations,)
        elif "delete from conversations" in flat:
            self.rowcount = self.connection.expired_conversations
            self._result = None
        else:
            self._result = None

    def fetchone(self):
        return self._result

    def fetchall(self):
        return self._result or []

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class FakeConnection:
    def __init__(self, policy_row=None, documents=None, held=None, expired_conversations=0):
        self.policy_row = policy_row
        self.documents = documents or []
        self.held = held or []
        self.expired_conversations = expired_conversations
        self.executed: list[tuple] = []

    def cursor(self):
        return FakeCursor(self)


def document(doc_id: str, ingested: datetime, chunks: int = 5) -> tuple:
    """A row as psycopg returns one: a tuple, because the code indexes it positionally.

    The first version returned a dict and the code raised KeyError: 0, which is what a fake that models the
    shape it wishes it had rather than the shape the caller sees produces.
    """
    return (doc_id, "company", ingested, chunks)


def rows_of(connection, fragment: str):
    return [params for query, params in connection.executed if fragment in query]


# --------------------------------------------------------------------------- the policy

def test_no_policy_destroys_nothing():
    """The one mistake in this file that cannot be undone. A default period invented here would delete a
    client's documents on a number they never agreed to, so an absent policy is a refusal and not a default."""
    connection = FakeConnection(policy_row=None, documents=[document("company/old.pdf", NOW - timedelta(days=9999))])
    run = policy.enforce(connection, "documents", None, purge=lambda *a, **k: None, now=NOW)
    assert run.retain_days == 0
    assert run.destroyed == 0
    assert run.expired == 0, "nothing is even examined without a policy"
    assert not rows_of(connection, "delete from")


def test_a_period_of_zero_days_is_refused():
    """A retention of zero destroys everything on the next run, and a negative one destroys things that have
    not happened yet. Both are almost certainly a configuration error rather than an intention."""
    connection = FakeConnection()
    with pytest.raises(ValueError, match="not a period"):
        policy.set_policy(connection, "documents", 0)
    with pytest.raises(ValueError, match="not a period"):
        policy.set_policy(connection, "documents", -30)


def test_an_unknown_dataset_is_refused():
    """The two datasets are the ones the requirement names, and a third would be one nothing enforces."""
    with pytest.raises(ValueError, match="unknown dataset"):
        policy.set_policy(FakeConnection(), "emails", 365)


# --------------------------------------------------------------------------- the cutoff

def test_age_is_measured_from_when_we_ingested_it_not_from_the_files_date():
    """The file's date is a fact about the client's filesystem. Copying a folder resets every mtime, and a
    document whose mtime was reset would be retained for another period without anybody deciding to.

    `ingested_at` is when the obligation to keep it started, which is the date a retention policy is about. The
    query is asserted rather than the arithmetic, because the arithmetic is a subtraction and the choice of
    column is the decision.
    """
    connection = FakeConnection(policy_row=("documents", 365, None))
    policy.enforce(connection, "documents", None, purge=lambda *a, **k: None, now=NOW)
    params = rows_of(connection, "from ingest_manifest")[0]
    assert params == (NOW - timedelta(days=365),), params
    source = (ROOT / "services" / "retention" / "policy.py").read_text()
    assert "where ingested_at is not null and ingested_at < %s" in source
    assert "updated_at <" not in source, "the cutoff drifted onto a column that changes when a file is touched"


def test_a_document_with_no_ingest_date_is_not_expired():
    """An unknown age is not the same as an old one, and the query says so rather than treating a null as
    ancient."""
    source = (ROOT / "services" / "retention" / "policy.py").read_text()
    assert "ingested_at is not null" in source


# --------------------------------------------------------------------------- the object store

def test_the_clients_file_is_destroyed_and_not_only_the_index(tmp_path):
    """The half a job built by reusing the purge would miss, and the requirement's verify line names the object
    store explicitly.

    A run that cleared the indexes and left the file would report success while the thing the client asked to be
    destroyed sat in their documents directory, and nothing would surface it because the report would say the
    run succeeded.
    """
    (tmp_path / "company").mkdir()
    old_file = tmp_path / "company" / "old.pdf"
    old_file.write_text("a document whose period has run out")

    purged: list[str] = []

    def fake_purge(connection, doc_id, reason, actor):
        purged.append(doc_id)
        outcome = types.SimpleNamespace(complete=True, refused=None, stores={"vector": {"deleted": 5}})
        return outcome

    connection = FakeConnection(policy_row=("documents", 365, "tax law"),
                                documents=[document("company/old.pdf", NOW - timedelta(days=400))])
    run = policy.enforce(connection, "documents", tmp_path, purge=fake_purge, now=NOW)

    assert purged == ["company/old.pdf"], "the stores were cleared"
    assert not old_file.exists(), "and the client's own copy is gone, which is what retention means"
    assert run.destroyed == 1


def test_the_stores_are_cleared_before_the_file_is_removed(tmp_path):
    """The order matters and only one direction is recoverable. A failure between the two leaves a file with no
    index, which re-ingesting fixes. The other order leaves an index pointing at a file that is gone: a passage
    the system will cite and nobody can open."""
    source = (ROOT / "services" / "retention" / "policy.py").read_text()
    body = source[source.index("        try:\n            outcome = purge("):]
    body = body[:body.index("run.destroyed += 1")]
    assert body.index("purge(") < body.index("path.unlink()"), (
        "the file is removed before the stores are cleared, so a failure leaves a citation to nothing")


def test_a_document_whose_stores_did_not_all_confirm_is_not_counted_as_destroyed(tmp_path):
    """A store that did not confirm means the document is not gone, so the file stays too. Leaving the file is
    recoverable; removing it while an index still holds the text is not."""
    (tmp_path / "company").mkdir()
    old_file = tmp_path / "company" / "old.pdf"
    old_file.write_text("still indexed somewhere")

    def partial(connection, doc_id, reason, actor):
        return types.SimpleNamespace(complete=False, refused=None, stores={"lexical": {"error": "refused"}})

    connection = FakeConnection(policy_row=("documents", 365, None),
                                documents=[document("company/old.pdf", NOW - timedelta(days=400))])
    run = policy.enforce(connection, "documents", tmp_path, purge=partial, now=NOW)

    assert run.destroyed == 0
    assert run.failed == 1
    assert old_file.exists(), "the file stays while a store still holds the text"
    assert "did not all confirm" in run.failures[0]["error"]


# --------------------------------------------------------------------------- holds

def test_a_hold_beats_a_period_and_is_counted_separately():
    """The period says the client agreed to lose the document by now. The hold says a court has told them not
    to. The hold wins, and it is counted apart from a destruction because a run that skipped forty documents
    under a hold and a run that destroyed forty are different outcomes."""
    called: list[str] = []
    connection = FakeConnection(policy_row=("documents", 365, None),
                                documents=[document("company/held.pdf", NOW - timedelta(days=400))],
                                held=["company/held.pdf"])
    run = policy.enforce(connection, "documents", None,
                         purge=lambda *a, **k: called.append("purged"), now=NOW)
    assert run.held == 1
    assert run.destroyed == 0
    assert called == [], "the purge was not even attempted"


def test_a_deployment_hold_covers_a_document_the_period_expired():
    """The same scope rule the purge uses. A hold that only covered what existed when it was placed is a hold
    with a gap in it."""
    connection = FakeConnection(policy_row=("documents", 365, None),
                                documents=[document("company/held.pdf", NOW - timedelta(days=400))],
                                held=["__deployment__"])
    run = policy.enforce(connection, "documents", None, purge=lambda *a, **k: None, now=NOW)
    assert run.held == 1 and run.destroyed == 0


def test_a_dry_run_still_checks_the_hold():
    """A rehearsal whose output does not match the real thing is a rehearsal of a different operation."""
    connection = FakeConnection(policy_row=("documents", 365, None),
                                documents=[document("company/held.pdf", NOW - timedelta(days=400))],
                                held=["company/held.pdf"])
    run = policy.enforce(connection, "documents", None, purge=lambda *a, **k: None, dry_run=True, now=NOW)
    assert run.held == 1, "the dry run reports the hold it would have respected"
    assert run.destroyed == 0


# --------------------------------------------------------------------------- conversations

def test_conversations_are_governed_by_the_same_mechanism():
    """The requirement says documents AND conversations, and a policy for a dataset with no table is a policy
    that has never been tested against anything."""
    connection = FakeConnection(policy_row=("conversations", 90, None), expired_conversations=7)
    run = policy.enforce(connection, "conversations", None, purge=lambda *a, **k: None, now=NOW)
    assert run.examined == 7
    assert run.expired == 7
    assert run.destroyed == 7
    params = rows_of(connection, "delete from conversations")[0]
    assert params == (NOW - timedelta(days=90),), params


def test_a_dry_run_on_conversations_deletes_nothing():
    connection = FakeConnection(policy_row=("conversations", 90, None), expired_conversations=7)
    run = policy.enforce(connection, "conversations", None, purge=lambda *a, **k: None,
                         dry_run=True, now=NOW)
    assert run.examined == 7
    assert run.destroyed == 0
    assert not rows_of(connection, "delete from conversations")


# --------------------------------------------------------------------------- the record

def test_a_run_is_recorded_whether_or_not_it_destroyed_anything():
    """The absence of a run and a run that found nothing are different facts, and only one of them means
    retention is working."""
    connection = FakeConnection(policy_row=("documents", 365, None), documents=[])
    run = policy.enforce(connection, "documents", None, purge=lambda *a, **k: None, now=NOW)
    assert run.examined == 0 and run.destroyed == 0
    assert rows_of(connection, "insert into retention_runs"), "a run that found nothing is still a run"


def test_the_period_in_force_is_stored_with_the_run_rather_than_joined():
    """The policy can change and the record of what happened has to say what the rule was at the time."""
    source = (ROOT / "services" / "retention" / "policy.py").read_text()
    assert "retain_days  integer not null" in (ROOT / "db" / "migrations" / "016_retention_policy.sql").read_text()
    assert "insert into retention_runs (dataset, finished_at, retain_days" in source
