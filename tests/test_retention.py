"""Purging a document everywhere it is stored, holding one against a purge, and destroying everything.

DATA-PURGE-001 is "deletes content everywhere it is stored, including the vector index", and the requirement is
the word EVERYWHERE. The vector index is named because it is the obvious one; the one that gets forgotten is
whichever store was added most recently, and in this build that is the lexical index from step 3.9.

So there are two tests here that matter more than the rest. One asserts the store list covers every store the
code writes document content to, which is the structural guard against a later step adding a store and the purge
not knowing. The other asserts a purge reads back as having touched each store, because the failure mode is a
purge reporting success while one store still holds the text and nobody looking again.

Run: python3 -m pytest tests/
"""
from __future__ import annotations

import re
import sys
import types
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "services" / "retention"))

if "psycopg" not in sys.modules:
    # The CLI imports psycopg, and this test does not need a database. The same stub the audit tests use.
    psycopg = types.ModuleType("psycopg")
    psycopg.connect = lambda *a, **k: None
    sys.modules["psycopg"] = psycopg

import retention  # noqa: E402


# --------------------------------------------------------------------------- the store list

def test_the_store_list_covers_every_store_the_code_writes_document_content_to():
    """The structural guard, and the reason this file exists as more than a loop.

    A purge is only as complete as its list of stores, and the list is written once while the stores accumulate.
    Step 3.9 added the lexical index six steps after the vector one, and a purge written before it would clear
    the vector store, report success, and leave the passage findable by the arm nobody remembered.

    So this reads the code that writes and asserts a purge store exists for each. A new store added without a
    purge path fails here rather than in a client's question six months later.
    """
    ingester = (ROOT / "services" / "ingestion" / "ingest.py").read_text()
    names = {store.name for store in retention.STORES}

    # Every write path in the ingestion worker, and what it implies about where content lives.
    writers = {
        # The vector upsert writes embeddings and payloads, which is the vector store.
        "upsert_points": "vector",
        # The lexical arm's write path. Added in step 3.9, and the store a purge written earlier would miss.
        "index_for_lexical": "lexical",
    }
    for function, store in writers.items():
        assert f"def {function}" in ingester, f"{function} is gone, so this test is stale"
        assert store in names, (
            f"the worker writes through {function} and there is no {store!r} purge store. A purge that does "
            f"not clear it leaves the document findable.")

    # And the tables a purge has to clear are the tables the worker writes to.
    for table, store in (("chunk_index", "lexical"), ("ingest_manifest", "manifest")):
        assert f"delete from {table}" in _purge_source(), f"{table} is never cleared by the purge"
        assert store in names


def _purge_source() -> str:
    return (ROOT / "services" / "retention" / "retention.py").read_text()


def test_every_store_has_a_description_a_client_can_read():
    """The destruction record goes to the client, so it says "the vector index" rather than "qdrant". A record
    naming our components is a record the client has to ask us to interpret."""
    for store in retention.STORES:
        assert store.description and len(store.description) > 20, store
        assert store.name.islower() and " " not in store.name, store


def test_a_purge_records_a_result_for_every_store_even_when_it_deleted_nothing():
    """A total cannot show that one store contributed nothing, and a store contributing nothing is the shape of
    the bug this requirement is about. The result is per store so a store that was never wired in is visible
    instead of looking like a store that had nothing to do."""
    result = retention.PurgeResult(doc_id="company/x.pdf")
    result.record("vector", deleted=20)
    result.record("lexical", deleted=0)
    assert result.complete
    assert result.total_deleted == 20
    assert result.stores["lexical"] == {"deleted": 0}, "a zero is recorded, not omitted"
    assert set(result.stores) == {"vector", "lexical"}


def test_a_store_that_could_not_be_reached_makes_the_whole_purge_incomplete():
    """Reporting success because the reachable stores succeeded is how a client is told their data is gone while
    it is not. One store failing makes the purge incomplete and the caller has to deal with it."""
    result = retention.PurgeResult(doc_id="company/x.pdf")
    result.record("vector", deleted=20)
    result.fail("lexical", "OperationalError: connection refused")
    assert not result.complete
    assert "error" in result.stores["lexical"]


# --------------------------------------------------------------------------- holds

class FakeCursor:
    def __init__(self, hold=None, rowcount=0):
        self.hold = hold
        self.rowcount = rowcount
        self.executed = []

    def execute(self, query, params=None):
        self.executed.append((" ".join(query.split()), params))

    def fetchone(self):
        return self.hold

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class FakeConnection:
    def __init__(self, hold=None, rowcount=0):
        self.cursor_obj = FakeCursor(hold, rowcount)

    def cursor(self):
        return self.cursor_obj


def test_a_document_hold_refuses_the_purge_and_records_the_refusal():
    """DATA-HOLD-001's verify line: a purge on a held document is refused and the refusal is recorded."""
    hold = (7, "document", "company/tax.pdf", "litigation in progress", "counsel", "2026-10-13")
    connection = FakeConnection(hold=hold)
    result = retention.purge(connection, "company/tax.pdf", "client asked", "support",
                            "http://vector:6339", "documents")
    assert result.refused == "legal_hold"
    assert not result.complete
    assert "litigation in progress" in result.refusal_reason
    assert "--release-hold 7" in result.refusal_reason, "the refusal has to say how to proceed"
    # Recorded, so the row exists and the attempt is visible rather than the purge appearing never to have run.
    assert result.stores["hold"]["refused"] == 1
    assert result.stores["hold"]["scope"] == "document"


def test_a_deployment_hold_covers_a_document_it_never_named():
    """The gap a document-list hold would have. A hold placed before the client uploaded the document that
    matters has to cover it, or the hold is exactly as good as the list it was written with."""
    source = _purge_source()
    assert "scope = 'deployment' or (scope = 'document' and doc_id = %s)" in source, (
        "the hold query does not cover a deployment-scope hold, which is the one that has to catch documents "
        "added after it was placed")


def test_the_hold_is_checked_before_any_store_is_touched():
    """Checking it per store would mean a hold obeyed by the store somebody remembered and ignored by the one
    they did not, which from the client's side is the same as no hold at all."""
    source = _purge_source()
    purge_body = source[source.index("def purge("):]
    hold_at = purge_body.index("active_hold")
    vector_at = purge_body.index("_delete_from_vector")
    assert hold_at < vector_at, "a store is cleared before the hold is checked"


def test_the_hold_check_is_one_query_rather_than_one_per_scope():
    """Two queries means two chances to forget one of them, and the deployment scope is the easy one to
    forget."""
    source = _purge_source()
    hold_body = source[source.index("def active_hold"):]
    # From the newline after this function's own `def`, not from position zero, which is the `def` that
    # starts it.
    hold_body = hold_body[:hold_body.index("\ndef ", 1)]
    assert hold_body.count("cur.execute") == 1, (
        "the hold check runs more than one query, so the scopes can drift apart")


# --------------------------------------------------------------------------- destruction

def test_the_destruction_record_is_hashed_and_the_hash_is_of_the_record():
    """The client keeps the file. The hash is what lets them show later that the file is the one we produced,
    and neither side has to trust the other's copy."""
    source = _purge_source()
    assert 'record["record_sha256"] = hashlib.sha256(' in source
    assert 'json.dumps(record, sort_keys=True' in source, (
        "the hash is taken over an unordered serialisation, so the same record would hash two ways")


def test_the_audit_chain_survives_the_teardown_and_the_record_says_so():
    """A decision rather than an omission, and it is in the record so a client can see it was deliberate.

    The audit says which documents existed and that they were destroyed, which is what lets the destruction be
    checked by someone who does not trust us. Destroying it would leave the client unable to show anyone that we
    did what we said we did.
    """
    source = _purge_source()
    assert '"audit_chain_head"' in source
    assert "deliberately kept" in source
    # And it is genuinely not deleted: no clear of the events table anywhere.
    assert "delete from audit_events" not in source, "the teardown deletes the audit chain"
    # The stored payloads are counted rather than cleared, and that is a finding rather than a shortcut: the
    # table is append-only and the chain covers payload_sha256, so clearing a payload leaves the row
    # unverifiable and updating the hash makes the chain a record of what somebody chose to leave in it.
    assert "_count_stored_payloads" in source
    assert "update audit_events set payload" not in source, (
        "the teardown tries to rewrite the audit record, which the append-only guarantee refuses and the hash "
        "chain would break if it did not")


def test_every_store_the_purge_knows_is_destroyed_by_the_teardown():
    """Otherwise the end of a contract leaves a store behind, and the store it leaves is the one nobody listed.
    The purge and the teardown have to cover the same ground."""
    source = _purge_source()
    teardown = source[source.index("def destroy_all"):]
    for table in ("chunk_index", "ingest_manifest", "purge_records", "legal_holds"):
        assert f'clear_table("{table}")' in teardown, f"the teardown does not clear {table}"
    assert 'attempt("vector"' in teardown
    # Counted rather than cleared, for the reason the other test gives.
    assert "_count_stored_payloads" in teardown


def test_a_document_still_readable_in_the_audit_record_is_not_reported_as_purged():
    """The conflict between two requirements, and the resolution.

    DATA-PURGE-001 says delete the content everywhere it is stored. The audit record is append-only and its hash
    chain covers the stored payload, so content in it cannot be removed: an UPDATE is refused by the trigger,
    and clearing the payload without updating payload_sha256 leaves the row unverifiable while updating it makes
    the chain a record of what somebody chose to leave in it.

    The resolution is to count it and make the purge incomplete. A document whose text is still readable in the
    audit record has not been purged, whatever the other stores reported, and a purge that said otherwise would
    be the exact failure this requirement exists to prevent.
    """
    source = _purge_source()
    assert "quoting_the_document" in source
    assert "result.complete = False" in source
    assert "cannot be cleared" in source
    # And the mitigation is named, because a client hitting this needs to know what to do differently.
    assert "hash content mode is for" in source or "not storing" in source


def test_the_teardown_does_not_claim_to_have_cleared_what_it_cannot():
    """The destruction record goes to the client, and a record saying a store was cleared when it was counted is
    a false statement in the one document whose purpose is to be checkable."""
    source = _purge_source()
    teardown = source[source.index("def destroy_all"):]
    assert "append-only and cannot be cleared" in teardown


def test_a_store_that_counts_does_not_report_that_it_deleted():
    """The bug the deployed teardown found, as a test.

    The audit store is append-only, so the teardown counts what it holds rather than clearing it. The reporting
    helper hard-coded the key `deleted`, so the record said `"deleted": 34` for a store where nothing had been
    deleted. The destruction record is the one document whose purpose is to be checkable by a client who does
    not trust us, and a record that says something false is worse there than anywhere else.

    The verb is a parameter now, so a store reports under the word that is true of it.
    """
    source = _purge_source()
    assert "verb: str = \"deleted\"" in source, "the reporting helper cannot describe a counted store"
    assert 'verb="still_stored"' in source, "the audit store reports a count under the wrong word"
    # And nothing claims the audit store was cleared.
    assert '"audit_payload": {"deleted"' not in source
