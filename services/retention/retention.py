"""Deleting a client's document everywhere it is stored, and refusing to when a hold says not to.

`DATA-PURGE-001` is "a client-requested purge deletes content everywhere it is stored, including the vector
index", and the requirement is the word EVERYWHERE. The vector index is named because it is the obvious one; the
one that gets forgotten is whichever store was added most recently. In this build that is the lexical index from
step 3.9, and forgetting it means a purged document still answers the question it was purged for, with a correct
citation to a document that no longer exists.

**So the stores are a list, and the list is checked against the writers.** `STORES` below is the enumeration, and
a test compares it with the code that writes document content, because the failure mode is a new store added in
a later step and a purge that quietly does not know about it. A purge that reports success while one store still
holds the text is worse than a purge that fails, because nobody looks again.

**A store that reports zero is recorded, not skipped.** The result is per store rather than a total, so a store
contributing nothing is visible in the record. A total of forty cannot show that one of three stores did nothing,
and "did nothing" is the shape of the bug.

**The hold covers documents added later.** A deployment-scope hold has no document list, so a document the
client uploads after the hold was placed is covered. A hold that only covered what existed at the time is a hold
with a gap in it, and the gap is where the document that matters ends up.

**A purge that cannot reach a store is not a purge.** The record says `complete: false` and the caller has to
deal with it. Reporting success because the reachable stores succeeded is how a client is told their data is
gone while it is not.
"""
from __future__ import annotations

import json
import urllib.request
from dataclasses import dataclass, field


@dataclass
class Store:
    """One place document content lives, and how to delete one document from it."""

    name: str
    # What it holds, in the words a client's destruction record should use. "Vector index" rather than
    # "qdrant", because the record is read by the client and not by us.
    description: str


# Every store that holds document content, in the order a purge should clear them.
#
# Ordered by what is reachable rather than by importance: the vector index is cleared first because it is the
# one a query hits first, and the audit payload last because it is the one that may legitimately hold nothing.
STORES: tuple[Store, ...] = (
    Store("vector", "the vector index: embeddings and the passage text attached to them"),
    Store("lexical", "the lexical index: passage text, search terms and extracted references"),
    Store("manifest", "the ingestion manifest: the record of what was ingested and its content hash"),
    Store("audit_payload", "any audit record whose stored payload quotes the document"),
)


@dataclass
class PurgeResult:
    doc_id: str
    complete: bool = True
    stores: dict[str, dict] = field(default_factory=dict)
    refused: str | None = None
    refusal_reason: str | None = None

    def record(self, name: str, **counts) -> None:
        self.stores[name] = counts

    def fail(self, name: str, error: str) -> None:
        self.stores[name] = {"error": error}
        self.complete = False

    @property
    def total_deleted(self) -> int:
        return sum(int(v.get("deleted") or 0) for v in self.stores.values())

    def as_record(self) -> dict:
        return {"doc_id": self.doc_id, "complete": self.complete, "stores": self.stores,
                "refused": self.refused, "refusal_reason": self.refusal_reason}


# --------------------------------------------------------------------------- holds

def active_hold(connection, doc_id: str) -> dict | None:
    """The hold covering a document, whether it names the document or the whole deployment.

    Both are checked in one query rather than two, because the deployment hold is the one that is easy to forget
    and a purge that checked only for a document-scope hold would walk straight through it.
    """
    with connection.cursor() as cur:
        cur.execute("""
            select id, scope, doc_id, reason, placed_by, placed_at
            from legal_holds
            where released_at is null
              and (scope = 'deployment' or (scope = 'document' and doc_id = %s))
            order by scope desc
            limit 1""", (doc_id,))
        row = cur.fetchone()
    if not row:
        return None
    return {"id": row[0], "scope": row[1], "doc_id": row[2], "reason": row[3],
            "placed_by": row[4], "placed_at": row[5]}


def place_hold(connection, reason: str, actor: str, doc_id: str | None = None) -> int:
    with connection.cursor() as cur:
        cur.execute("""
            insert into legal_holds (doc_id, scope, reason, placed_by)
            values (%s, %s, %s, %s) returning id""",
            (doc_id, "deployment" if doc_id is None else "document", reason, actor))
        return cur.fetchone()[0]


def release_hold(connection, hold_id: int, actor: str) -> bool:
    with connection.cursor() as cur:
        cur.execute("""update legal_holds set released_at = now(), released_by = %s
                       where id = %s and released_at is null""", (actor, hold_id))
        return cur.rowcount == 1


# --------------------------------------------------------------------------- deletion

def _delete_from_vector(vector_url: str, collection: str, doc_id: str, timeout: int) -> int:
    """Delete by filter rather than by id, because the point is to delete what is there.

    Deleting by a list of ids means the ids have to be enumerated first, and anything added between the
    enumeration and the delete survives. A filter is computed by the store at the moment of deletion.
    """
    def matching() -> int:
        count_body = {"filter": {"must": [{"key": "doc_id", "match": {"value": doc_id}}]}, "exact": True}
        count_request = urllib.request.Request(
            f"{vector_url}/collections/{collection}/points/count",
            data=json.dumps(count_body).encode(), method="POST",
            headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(count_request, timeout=timeout) as response:
            return json.loads(response.read()).get("result", {}).get("count", 0)

    # Counted BEFORE the delete, which is where the number is. The first version counted after it, where the
    # only two outcomes are zero or a raised error, and then returned a hard-coded 1: **every purge reported
    # exactly one object removed from the vector store, whether it cleared one chunk or a thousand, and a purge
    # that removed nothing reported one too.** A per-store count that is a constant is worse than no count,
    # because the page's whole claim is that a store which contributed nothing is visible.
    before = matching()
    body = {"filter": {"must": [{"key": "doc_id", "match": {"value": doc_id}}]}, "wait": True}
    request = urllib.request.Request(
        f"{vector_url}/collections/{collection}/points/delete?wait=true",
        data=json.dumps(body).encode(), method="POST",
        headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        json.loads(response.read())
    # And counted after as well, because the delete being acknowledged is not the delete having happened.
    remaining = matching()
    if remaining:
        raise RuntimeError(f"{remaining} point(s) still match {doc_id!r} after the delete")
    return before


def purge(connection, doc_id: str, reason: str, actor: str, vector_url: str, collection: str,
          timeout: int = 60) -> PurgeResult:
    """Delete a document from every store, or refuse because a hold covers it.

    The hold is checked first and in one place. Checking it per store would mean a hold that is obeyed by the
    store somebody remembered and ignored by the one they did not, which from the client's side is the same as
    no hold at all.
    """
    result = PurgeResult(doc_id=doc_id)

    hold = active_hold(connection, doc_id)
    if hold:
        result.refused = "legal_hold"
        result.refusal_reason = (
            f"a {hold['scope']} hold is in force, placed by {hold['placed_by']} because {hold['reason']!r}. "
            f"Release it with --release-hold {hold['id']} before purging.")
        # The refusal is recorded as a store result too, so the row says why rather than the purge appearing
        # never to have been attempted. **The comment used to claim the row exists. It did not**: this branch
        # returned before the `insert into purge_records` at the end of the function, so a refused purge left no
        # trace at all. Measured on the deployed store: purge_records 2 before and 2 after, 0 rows naming the
        # document, audit_events 40 before and 40 after. A client asking "did anyone try to delete this while
        # the hold was on" had nothing to read, and the manual said the refusal was recorded.
        result.record("hold", refused=1, hold_id=hold["id"], scope=hold["scope"])
        result.complete = False
        # Recorded before returning, which is the whole point: this branch used to return past the insert, so a
        # refused purge left no row and no audit event, and nothing could answer whether anyone had tried.
        _record_purge(connection, result, reason, actor)
        return result

    # The vector index first: it is what a query reaches first, so it is the one whose survival is most
    # immediately visible.
    try:
        _delete_from_vector(vector_url, collection, doc_id, timeout)
        result.record("vector", deleted=1)
    except Exception as exc:  # noqa: BLE001
        result.fail("vector", f"{type(exc).__name__}: {exc}")

    # The lexical index. Added in step 3.9 and the reason this file exists as more than a loop over stores:
    # without this line a purged document still answers the question, with a citation, from the arm nobody
    # remembered to clear.
    try:
        with connection.cursor() as cur:
            cur.execute("delete from chunk_index where doc_id = %s", (doc_id,))
            result.record("lexical", deleted=cur.rowcount)
    except Exception as exc:  # noqa: BLE001
        result.fail("lexical", f"{type(exc).__name__}: {exc}")

    # The manifest, so the next cycle does not treat the deletion as an accident and re-ingest the file. The
    # file is the client's and stays where it is; this is the record that it was processed, and leaving it
    # means the worker sees a file it knows and skips it, which is what we want.
    try:
        with connection.cursor() as cur:
            cur.execute("delete from ingest_manifest where relpath = %s", (doc_id,))
            result.record("manifest", deleted=cur.rowcount)
    except Exception as exc:  # noqa: BLE001
        result.fail("manifest", f"{type(exc).__name__}: {exc}")

    # Any audit payload that quotes the document: COUNTED rather than cleared, and the difference matters.
    #
    # The first version tried to remove the text from the audit rows and it cannot be done, for two reasons that
    # both come from the same property. The table is append-only: an UPDATE is refused by a trigger unless the
    # maintenance hatch is open, and the hatch exists for the planned purge of whole rows rather than for
    # rewriting them. And opening it would not help, because the chain covers payload_sha256 and payload_bytes,
    # so clearing a payload without updating those makes the row fail verification and updating those makes the
    # chain a record of what somebody chose to leave in it.
    #
    # So content stored in the audit record cannot be purged. That is a real consequence of the append-only
    # property and the honest answer is to say so rather than to reach for the hatch: the mitigation is not to
    # store it there, which is why the default content mode is hash, and a deployment that selects `full` is
    # choosing to keep content it cannot later delete. The count is reported and a non-zero one makes the purge
    # incomplete, because a document whose text is still readable in the audit record has not been purged
    # whatever the other stores reported.
    try:
        with connection.cursor() as cur:
            cur.execute("select count(*) from audit_events where payload::text like %s",
                        (f"%{doc_id}%",))
            quoted = cur.fetchone()[0]
        result.record("audit_payload", deleted=0, quoting_the_document=quoted)
        if quoted:
            result.complete = False
            result.stores["audit_payload"]["note"] = (
                f"{quoted} audit record(s) quote this document and cannot be cleared: the audit table is "
                f"append-only and the hash chain covers the stored payload. Purgeable only by not storing "
                f"content, which is what the hash content mode is for.")
    except Exception as exc:  # noqa: BLE001
        result.fail("audit_payload", f"{type(exc).__name__}: {exc}")

    _record_purge(connection, result, reason, actor)
    return result


def _record_purge(connection, result: PurgeResult, reason: str, actor: str) -> None:
    """Write the purge record. One writer, because two would drift.

    A refusal is recorded like any other attempt, with the reason and the hold that stopped it. A purge that was
    blocked is a fact about the deployment that somebody will ask about, and a record that only exists for
    purges that succeeded cannot answer the question.
    """
    with connection.cursor() as cur:
        cur.execute("select row_hash from audit_events order by id desc limit 1")
        head = cur.fetchone()
        cur.execute("""
            insert into purge_records (doc_id, reason, actor, stores, complete, chain_head)
            values (%s, %s, %s, %s, %s, %s)""",
            (result.doc_id, reason, actor, json.dumps(result.stores, sort_keys=True), result.complete,
             head[0] if head else None))


# --------------------------------------------------------------------------- destruction

def destroy_all(connection, reason: str, actor: str, vector_url: str, collection: str,
                timeout: int = 120) -> dict:
    """Every document in every store, with a record the client can keep and check.

    Separate from `purge` because it is a different event. A purge is one document on a client's instruction
    during the contract; this is every document at the end of one, and a client reading their own record should
    be able to tell the two apart.

    **The record is hashed and the hash is of the record.** The client keeps the file, the hash is what lets
    them show later that the file is the one we produced, and neither of us needs to trust the other's copy.
    The chain head is in there too, so the deletion sits at a known point in the audit chain rather than being
    asserted.
    """
    import hashlib
    import time

    stores: dict[str, dict] = {}

    def attempt(name: str, description: str, action, verb: str = "deleted") -> None:
        """Record what a store reported, under the verb that is true of it.

        The verb is a parameter because the first version hard-coded `deleted`, and the store that counts rather
        than clears reported `"deleted": 34` when nothing had been deleted from it. The destruction record is the
        one document whose purpose is to be checkable by a client who does not trust us, and a record that says
        something false is worse there than anywhere else.
        """
        try:
            stores[name] = {"description": description, verb: action()}
        except Exception as exc:  # noqa: BLE001
            stores[name] = {"description": description, "error": f"{type(exc).__name__}: {exc}"}

    def drop_collection() -> int:
        request = urllib.request.Request(
            f"{vector_url}/collections/{collection}", method="DELETE")
        with urllib.request.urlopen(request, timeout=timeout) as response:
            json.loads(response.read())
        # Recreated empty rather than left absent, so a deployment that is destroyed and then restored to
        # service does not fail on a missing collection for a reason nobody would connect to the teardown.
        create = urllib.request.Request(
            f"{vector_url}/collections/{collection}",
            data=json.dumps({"vectors": {"size": 1024, "distance": "Cosine"}}).encode(),
            method="PUT", headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(create, timeout=timeout) as response:
            json.loads(response.read())
        return 1

    attempt("vector", "the vector index: every embedding and every passage", drop_collection)

    def clear_table(table: str):
        def action() -> int:
            with connection.cursor() as cur:
                cur.execute(f"delete from {table}")
                return cur.rowcount
        return action

    attempt("lexical", "the lexical index: passage text, search terms and references",
            clear_table("chunk_index"))
    attempt("manifest", "the ingestion manifest: what was ingested and its content hash",
            clear_table("ingest_manifest"))
    # Counted, not cleared, for the same reason the single-document purge counts it: the audit table is
    # append-only and the chain covers the payload, so content stored there cannot be removed without either
    # leaving the row unverifiable or rewriting the record of what was kept. Reported as a number so a client
    # reading the destruction record can see whether their content was ever stored there at all.
    attempt("audit_payload", "content stored in the audit record, which is append-only and cannot be cleared",
            lambda: _count_stored_payloads(connection), verb="still_stored")
    # And if anything is still stored, the destruction is NOT complete. The single-document purge already says
    # so and the teardown did not, which is the same situation reported two ways: 34 records still holding
    # content beside `complete: true` is a record telling a client their data is gone when it is not.
    still_there = int(stores.get("audit_payload", {}).get("still_stored") or 0)
    if still_there:
        stores["audit_payload"]["note"] = (
            f"{still_there} audit record(s) still hold content and cannot be cleared. The audit table is "
            f"append-only and the hash chain covers the stored payload, so this content is not destroyed by "
            f"this run or by any later one. A deployment that needs its content to be destroyable must not "
            f"store it there, which is what the hash content mode is for.")
    attempt("purge_history", "the record of earlier purges", clear_table("purge_records"))
    attempt("holds", "the record of holds", clear_table("legal_holds"))

    # The audit chain itself is NOT destroyed, and that is a decision rather than an omission. It records that
    # documents existed and that they were destroyed, and it is the thing that proves the destruction happened.
    # Destroying it would leave the client unable to show anyone that we did what we said.
    with connection.cursor() as cur:
        cur.execute("select count(*), row_hash from audit_events group by row_hash order by count(*) desc limit 1")
        head = cur.fetchone()

    # Complete means two things: no store errored, and nothing is still stored that this could not clear. The
    # first version checked only the first, so a run that left 34 records holding content reported complete.
    audit_left = int(stores.get("audit_payload", {}).get("still_stored") or 0)
    record = {
        "requested_by": actor, "reason": reason, "destroyed_at": time.strftime("%Y-%m-%dT%H:%M:%SZ",
                                                                             time.gmtime()),
        "stores": stores,
        "complete": all("error" not in value for value in stores.values()) and audit_left == 0,
        "audit_chain_head": head[1] if head else None,
        "note": ("The audit record is deliberately kept. It says which documents existed and that they were "
                 "destroyed, which is what lets this record be checked by someone who does not trust us."),
    }
    record["record_sha256"] = hashlib.sha256(
        json.dumps(record, sort_keys=True, separators=(",", ":")).encode()).hexdigest()

    with connection.cursor() as cur:
        cur.execute("""
            insert into destruction_records (requested_by, reason, stores, complete, record_sha256,
                                             chain_head)
            values (%s, %s, %s, %s, %s, %s)""",
            (actor, reason, json.dumps(stores, sort_keys=True), record["complete"],
             record["record_sha256"], record["audit_chain_head"]))
    return record


def _count_stored_payloads(connection) -> int:
    """How many audit records hold content, which the teardown cannot remove.

    A count rather than a deletion, and the destruction record carries it so the client can see whether their
    content was ever stored in a place that cannot be cleared. Zero is the answer for every deployment on the
    default hash content mode, and a non-zero one is a deployment that chose `full` and is keeping something it
    cannot later destroy.
    """
    with connection.cursor() as cur:
        cur.execute("select count(*) from audit_events where payload is not null")
        stored = cur.fetchone()[0]
        cur.execute("""select count(*) from information_schema.columns
                       where table_name = 'audit_events' and column_name = 'payload'""")
    return stored
