#!/usr/bin/env python3
"""The teardown at the end of a contract, run for real.

DATA-EXIT-001's verify line: a teardown run produces a signed destruction record naming every store and its
state.

**This test destroys the corpus.** That is not a side effect to be worked around; running the teardown is what
the requirement asks for, and the most dangerous operation in the system is the one that must not go unexecuted.
The recovery path is deliberate and it is on disk rather than in a backup: the teardown clears the indexes and
the records and does NOT delete the client's files, so one ingest cycle rebuilds everything. A teardown that
deleted the documents would be unrecoverable by design, which is a different product.

The record is checked by recomputing its hash from its own contents, which is what a client would do with the
file we hand them and is the only check that does not require trusting us.

Run from the repo root on the box:

    cd /opt/oi-core
    docker run --rm --network ai-net --user "$(id -u):$(id -g)" \\
      -v /opt/oi-core:/w -v /tmp:/probe:ro -w /w \\
      --entrypoint python oi-core/ingestion:local /tmp/destruction_acceptance.py
"""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, "/w/services/retrieval")
sys.path.insert(0, "/w/services/ingestion")
sys.path.insert(0, "/w/services/retention")


def load_env(path: Path = Path("/w/rendered/env")) -> dict[str, str]:
    try:
        text = path.read_text()
    except PermissionError:
        print(f"cannot read {path}: mode 600 owned by the operator. Add --user \"$(id -u):$(id -g)\"")
        return {}
    out: dict[str, str] = {}
    for line in text.splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, _, value = line.partition("=")
            out[key.strip()] = value.strip().strip('"').strip("'")
    return out


env = load_env()
if env and not os.environ.get("DATABASE_URL"):
    from urllib.parse import quote
    os.environ["DATABASE_URL"] = (
        f"postgresql://{quote(env['POSTGRES_USER'], safe='')}:"
        f"{quote(env['POSTGRES_PASSWORD'], safe='')}@postgres:5432/{env['POSTGRES_DB']}")
    os.environ.setdefault("VECTOR_URL", "http://vector:6333")

import psycopg2  # noqa: E402
import retention  # noqa: E402

COLLECTION = os.environ.get("COLLECTION", "documents")
URL = os.environ.get("VECTOR_URL", "http://vector:6333")


def stores() -> dict[str, int]:
    import urllib.request
    counts = {}
    with psycopg2.connect(os.environ["DATABASE_URL"]) as connection:
        with connection.cursor() as cur:
            cur.execute("select count(*) from chunk_index")
            counts["lexical"] = cur.fetchone()[0]
            cur.execute("select count(*) from ingest_manifest")
            counts["manifest"] = cur.fetchone()[0]
            cur.execute("select count(*) from purge_records")
            counts["purge_history"] = cur.fetchone()[0]
            cur.execute("select count(*) from legal_holds")
            counts["holds"] = cur.fetchone()[0]
    with urllib.request.urlopen(f"{URL}/collections/{COLLECTION}", timeout=30) as response:
        counts["vector"] = json.loads(response.read())["result"]["points_count"]
    return counts


def main() -> int:
    problems: list[str] = []

    before = stores()
    print()
    print("=== what is about to be destroyed ===")
    for store, count in sorted(before.items()):
        print(f"  {store:<14} {count}")
    if not any(before.values()):
        print("  everything is already empty, so a teardown would prove nothing")
        return 1

    with psycopg2.connect(os.environ["DATABASE_URL"]) as connection:
        connection.autocommit = True
        record = retention.destroy_all(connection, "probe: contract ended, DATA-EXIT-001", "probe",
                                       URL, COLLECTION)

    print()
    print("=== the destruction record ===")
    print(json.dumps(record, indent=2, sort_keys=True)[:1600])

    print()
    print("=== every store is named, with its state ===")
    for name, value in sorted(record["stores"].items()):
        state = value.get("error") or f"deleted {value.get('deleted')}"
        print(f"  {name:<14} {state}")
        if not value.get("description"):
            problems.append(f"{name} has no description, so a client reading this cannot tell what it was")
    # The stores the purge knows about, plus the ones only the teardown touches. A teardown naming fewer stores
    # than a purge does is a teardown that leaves one behind.
    purge_stores = {store.name for store in retention.STORES}
    named = set(record["stores"])
    missing = purge_stores - named
    if missing:
        problems.append(f"the teardown does not name {sorted(missing)}, which a single-document purge does")

    print()
    print("=== the record is signed, and checked by recomputing it ===")
    claimed = record.get("record_sha256")
    body = {key: value for key, value in record.items() if key != "record_sha256"}
    recomputed = hashlib.sha256(
        json.dumps(body, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    print(f"  claimed    {claimed}")
    print(f"  recomputed {recomputed}")
    if claimed != recomputed:
        problems.append("the record's own hash does not match its contents, so the file cannot be checked")
    else:
        print("  ok  the hash matches, so a client holding the file can show it is the one we produced")

    print()
    print("=== the stores are empty afterwards ===")
    after = stores()
    for store, count in sorted(after.items()):
        print(f"  {store:<14} {count}")
        if count:
            problems.append(f"{store} still holds {count} object(s) after the teardown")

    print()
    print("=== the one store the teardown cannot clear, and why ===")
    stored = record["stores"].get("audit_payload", {})
    print(f"  audit_payload reports: {stored}")
    print("  Content in the audit record cannot be removed: the table is append-only and the chain covers the")
    print("  stored payload, so clearing it leaves the row unverifiable and updating the hash makes the chain a")
    print("  record of what somebody chose to leave in it. The count is reported instead, and a non-zero one")
    print("  makes the purge incomplete. On the default hash content mode the count is zero.")
    # The verb matters. The first teardown reported `"deleted": 34` for a store it had only counted, and this
    # check used to look for that key, so it was reading a count as a deletion and reporting a failure for the
    # right reason and the wrong one at once.
    if "deleted" in stored:
        problems.append(f"the teardown reports the audit store under the word deleted: {stored}")
    else:
        print(f"  ok  reported as {sorted(k for k in stored if k != 'description')}, which is what happened")

    print()
    print("=== complete means nothing survived, not only that nothing errored ===")
    stored_count = int(stored.get("still_stored") or 0)
    print(f"  record says complete: {record['complete']}, audit records still holding content: {stored_count}")
    if stored_count and record["complete"]:
        problems.append("the record reports complete while the audit store says content survives")
    elif not stored_count and not record["complete"]:
        problems.append("the record reports incomplete and nothing survived, so the flag is wrong the other way")
    else:
        print(f"  ok  complete={record['complete']} is consistent with {stored_count} record(s) surviving")
    if stored.get("note"):
        print(f"  and it says why: {stored['note'][:130]}")

    print()
    print("=== and the record survives the thing it records ===")
    with psycopg2.connect(os.environ["DATABASE_URL"]) as connection:
        with connection.cursor() as cur:
            # Counted, not asserted to be one. A second teardown legitimately adds a second record: these are
            # append-only evidence, and a system that overwrote its own destruction record would be a system
            # whose record of a destruction can be replaced by a later one. The first version asserted exactly
            # one and failed on a re-run, which read as the record being destroyed rather than as the assertion
            # being wrong.
            cur.execute("""select count(*), (select record_sha256 from destruction_records
                                             order by id desc limit 1)
                           from destruction_records""")
            count, stored_hash = cur.fetchone()
            cur.execute("select count(*) from audit_events")
            events = cur.fetchone()[0]
    print(f"  destruction_records: {count} (at least one, and purge_records is deliberately cleared)")
    print(f"  audit_events: {events} kept, deliberately")
    if count < 1:
        problems.append("the destruction record was itself destroyed")
    elif stored_hash != claimed:
        problems.append("the newest stored record is not the one this run produced")
    if stored_hash != claimed:
        problems.append("the stored hash is not the one in the record returned")
    if events == 0:
        problems.append("the audit chain was destroyed, and it is what lets the destruction be checked")

    print()
    print("=== the client's files are still on disk, which is the recovery path ===")
    documents = Path("/w/documents")
    files = [p for p in documents.rglob("*") if p.is_file()] if documents.exists() else []
    print(f"  {len(files)} file(s) under {documents}")
    if not files:
        problems.append("the teardown removed the client's files, and the recovery path is that it does not")

    print()
    if problems:
        print(f"FAILED with {len(problems)} problem(s):")
        for problem in problems:
            print(f"  - {problem}")
        return 1
    print("PASSED: the teardown emptied every store, named each one with its state, and produced a record whose")
    print("hash checks against its own contents. The corpus is rebuildable from the files, which are untouched.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
