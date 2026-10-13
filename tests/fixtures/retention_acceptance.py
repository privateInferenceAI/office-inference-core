#!/usr/bin/env python3
"""Purging a document everywhere it is stored, on the deployed store.

DATA-PURGE-001's verify line is exact and it is the whole test: after a purge, the same question returns no
passage from the deleted document.

That is a stronger check than it looks. Clearing the vector index is the obvious half and the one a purge written
first would do; the lexical index from step 3.9 is a second copy of every document, and a purge that misses it
returns the passage with a correct citation to a document that no longer exists. So the question is asked
BEFORE the purge to show it was answerable, and again AFTER to show the answer is gone. A test that only asked
afterwards could pass on a corpus that never held the document.

DATA-HOLD-001 is checked in the same run, because a hold that does not block the purge is not a hold.

Run from the repo root on the box:

    cd /opt/oi-core
    docker run --rm --network ai-net --user "$(id -u):$(id -g)" \\
      -v /opt/oi-core:/w -v /tmp:/probe:ro -w /w \\
      --entrypoint python oi-core/ingestion:local /tmp/retention_acceptance.py

The image is the INGESTION one and not the retention one, which is not obvious. This probe imports the
retriever, and the retriever's lexical arm reaches Postgres through `psycopg2`; the retention image carries
`psycopg` version 3 for its own CLI and those are different packages. The retention module imports neither, so
this is the same file reached through the same path, in the image that has the driver the other half needs.
"""
from __future__ import annotations

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

import ingest  # noqa: E402
import retention  # noqa: E402
import retriever  # noqa: E402

DOC = Path("/w/documents/company/retention-fixture.md")
DOC_ID = "company/retention-fixture.md"
# A reference that exists nowhere else, so a passage mentioning it can only have come from this document.
MARKER = "Meridian Retention Fixture 8842"
QUESTION = f"What does {MARKER} say about the disposal schedule?"

BODY = f"""# {MARKER}: Records Disposal Schedule

This schedule sets out how long each class of record is kept and how it is disposed of.

## Retention periods

Invoices and receipts are kept for seven years from the end of the financial year in which they were raised.
Contracts are kept for ten years after they expire, or six years after the last amendment, whichever is later.
Payroll records are kept for seven years, and pension records for the life of the scheme plus six years.

## Disposal

At the end of the retention period, paper records are shredded to DIN 66399 level P-4 and the shredding is
recorded in the disposal log with the date, the class of record and the name of the person who authorised it.
Electronic records are deleted from the live system and from the backup within thirty days.

## Authorisation

Disposal is authorised by the office manager. Records subject to a legal hold are removed from this schedule
until the hold is released, and the hold register is checked before every disposal run.
"""


def db():
    # psycopg2 rather than psycopg 3, because this runs in the ingestion image which has the driver the
    # retriever's lexical arm needs. `retention.py` imports neither, so the module under test is the same file.
    import psycopg2
    connection = psycopg2.connect(os.environ["DATABASE_URL"])
    connection.autocommit = True
    return connection


def present() -> dict:
    """What each store holds for this document, asked of the stores directly.

    Asked of the stores rather than of the purge's own report, because a purge that reports success while a
    store still holds the text is the failure this test exists for.
    """
    import psycopg2
    counts = {}
    with psycopg2.connect(os.environ["DATABASE_URL"]) as connection:
        with connection.cursor() as cur:
            cur.execute("select count(*) from chunk_index where doc_id = %s", (DOC_ID,))
            counts["lexical"] = cur.fetchone()[0]
            cur.execute("select count(*) from ingest_manifest where relpath = %s", (DOC_ID,))
            counts["manifest"] = cur.fetchone()[0]
    counts["vector"] = vector_count()
    return counts


def vector_count() -> int:
    import urllib.request
    url = os.environ.get("VECTOR_URL", "http://vector:6333")
    collection = os.environ.get("COLLECTION", "documents")
    body = {"filter": {"must": [{"key": "doc_id", "match": {"value": DOC_ID}}]}, "exact": True}
    request = urllib.request.Request(f"{url}/collections/{collection}/points/count",
                                     data=json.dumps(body).encode(), method="POST",
                                     headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.loads(response.read()).get("result", {}).get("count", 0)


def ask() -> list[str]:
    """The passages a question returns, by document. The requirement's own check."""
    services = ingest.Services()
    result = retriever.search(services, QUESTION, role="staff", limit=10)
    return [passage.doc_id for passage in result.passages]


def main() -> int:
    problems: list[str] = []

    print()
    print("=== the fixture ===")
    DOC.write_text(BODY)
    print(f"  wrote {DOC_ID}, {len(BODY)} characters, carrying the marker {MARKER!r}")
    print(f"  the marker appears in no other document, so a passage mentioning it is this one")

    if present()["vector"] == 0:
        # Ingested through the worker's own cycle, exactly as a real document would be. Skipped when the worker
        # has already picked it up, because running a second cycle beside the worker's is how the payload
        # version bump put this box at load 38.
        # The environment is passed explicitly. Without DATABASE_URL the worker falls back to an in-memory
        # manifest, treats every document as new and re-embeds the whole corpus: a run of this probe that
        # forgot it re-embedded 764 chunks beside the worker's own cycle and the embedding service answered 429.
        cycle = subprocess.run([sys.executable, "/app/ingest.py", "--documents", "/w/documents"],
                               capture_output=True, text=True, env=dict(os.environ))
        print(f"  ingest exited {cycle.returncode}")
        for line in (cycle.stdout or "").strip().splitlines()[-4:]:
            print(f"    {line}")
        if "keeps its manifest in memory" in (cycle.stdout or ""):
            problems.append("the ingest ran without DATABASE_URL, so it used a memory manifest and re-embedded "
                            "the whole corpus instead of adding one document")

    before = present()
    print(f"  indexed: {before}")
    if not all(before.values()):
        problems.append(f"the fixture is not in every store before the purge, so the purge cannot be shown to "
                        f"remove it: {before}")

    print()
    print("=== the question is answerable BEFORE the purge ===")
    asked_before = ask()
    print(f"  {QUESTION!r}")
    print(f"  returned: {asked_before}")
    if DOC_ID in asked_before:
        print(f"  ok  the deleted document is among them, so the purge has something to remove")
    else:
        problems.append("the fixture is not retrievable before the purge, so a purge that removes it would "
                        "not be shown to have changed anything")

    print()
    print("=== DATA-HOLD-001: a hold refuses the purge and records the refusal ===")
    with db() as connection:
        hold_id = retention.place_hold(connection, "probe: testing that a hold blocks a purge", "probe",
                                       DOC_ID)
        print(f"  placed hold {hold_id} on {DOC_ID}")
        refused = retention.purge(connection, DOC_ID, "probe purge under hold", "probe",
                                  os.environ.get("VECTOR_URL", "http://vector:6333"),
                                  os.environ.get("COLLECTION", "documents"))
        print(f"  purge refused: {refused.refused}")
        print(f"  reason: {refused.refusal_reason}")
        if refused.refused != "legal_hold":
            problems.append("a purge ran while a hold was in force")
        if "probe: testing that a hold blocks a purge" not in (refused.refusal_reason or ""):
            problems.append("the refusal does not say why the hold was placed")

    after_refusal = present()
    print(f"  stores after the refused purge: {after_refusal}")
    if after_refusal != before:
        problems.append(f"the refused purge changed something: {before} became {after_refusal}")

    print()
    print("=== release the hold, then purge ===")
    with db() as connection:
        released = retention.release_hold(connection, hold_id, "probe")
        print(f"  hold released: {released}")
        result = retention.purge(connection, DOC_ID, "probe: verifying DATA-PURGE-001", "probe",
                                 os.environ.get("VECTOR_URL", "http://vector:6333"),
                                 os.environ.get("COLLECTION", "documents"))
    print(f"  complete: {result.complete}")
    for store, counts in sorted(result.stores.items()):
        print(f"    {store:<14} {counts}")
    if not result.complete:
        problems.append(f"the purge reported incomplete: {result.stores}")

    print()
    print("=== DATA-PURGE-001: the same question returns no passage from the deleted document ===")
    after = present()
    print(f"  stores after the purge: {after}")
    for store, count in sorted(after.items()):
        if count:
            problems.append(f"{store} still holds {count} object(s) for the purged document")
        else:
            print(f"  ok  {store}: empty")

    asked_after = ask()
    print(f"  asking the same question again returned: {asked_after}")
    if DOC_ID in asked_after:
        problems.append(f"the purged document still answers the question, from the arm nobody cleared. It "
                        f"appeared in {asked_after}")
    else:
        print(f"  ok  the purged document is absent from the answer")

    print()
    print("=== and the purge is recorded ===")
    with db() as connection, connection.cursor() as cur:
        cur.execute("""select doc_id, complete, reason, stores from purge_records
                       where doc_id = %s order by id desc limit 1""", (DOC_ID,))
        row = cur.fetchone()
    if row:
        print(f"  recorded: {row[0]} complete={row[1]} reason={row[2]!r}")
        print(f"  per store: {json.dumps(row[3], sort_keys=True)}")
        if not row[1]:
            problems.append("the purge record says incomplete")
        if "lexical" not in (row[3] or {}):
            problems.append("the purge record does not name the lexical store, so a store reporting nothing "
                            "would be invisible")
    else:
        problems.append("no purge record was written")

    print()
    print("=== leaving the deployment as it was found ===")
    if DOC.exists():
        DOC.unlink()
        print(f"  removed {DOC_ID} from the documents directory, so the next worker cycle does not re-ingest it")

    print()
    if problems:
        print(f"FAILED with {len(problems)} problem(s):")
        for problem in problems:
            print(f"  - {problem}")
        return 1
    print("PASSED: a hold refused the purge and changed nothing, and after the purge the same question returns")
    print("no passage from the document, from either arm, with the deletion recorded per store")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
