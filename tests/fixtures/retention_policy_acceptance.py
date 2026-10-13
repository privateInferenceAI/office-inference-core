#!/usr/bin/env python3
"""A period running out, on the deployed store, with the client's own file destroyed.

DATA-RETAIN-001's verify line names three places: the vector index, the object store, and the answer. The object
store is the half a job built by reusing the purge would miss, and it is checked here by looking for the file.

**The fixture is aged rather than waited for.** Its `ingested_at` is moved back past the cutoff, because the
alternative is a retention period of minutes on a real deployment, which is a policy nobody would ever set and
would prove the arithmetic rather than the mechanism.

Run from the repo root on the box:

    cd /opt/oi-core
    docker compose -f deploy/office/compose/docker-compose.yml --env-file rendered/env \\
      --profile tools run --rm retention --enforce documents --dry-run
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

DOC_ID = "company/retention-policy-fixture.md"
MARKER = "Meridian Retention Policy Fixture 9917"

BODY = f"""# {MARKER}: Records Disposal Schedule

This schedule sets out how long each class of record is kept. It exists so a retention run has a document whose
own text states the periods, which is a small joke and also makes the passage findable by a question about
retention.

Invoices and receipts are kept for seven years. Contracts are kept for ten years after they expire. Payroll
records are kept for seven years and pension records for the life of the scheme plus six years. Disposal is
authorised by the office manager and recorded in the disposal log with the date, the class and the name of the
person who authorised it.
"""


def main() -> int:
    problems: list[str] = []
    import policy

    import psycopg2
    connection = psycopg2.connect(os.environ["DATABASE_URL"])
    connection.autocommit = True

    documents_root = Path(os.environ.get("DOCUMENTS_ROOT", "/documents"))
    path = documents_root / DOC_ID

    print()
    print("=" * 96)
    print("SETUP: a document whose period has run out")
    print("=" * 96)
    if not documents_root.exists():
        print(f"  {documents_root} does not exist, so the file half of this test cannot run")
        return 1
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(BODY)
    print(f"  wrote {DOC_ID}, {len(BODY)} characters")

    # Ingested if it is not already, through the worker's own cycle so it arrives the way a real one would.
    with connection.cursor() as cur:
        cur.execute("select count(*) from chunk_index where doc_id = %s", (DOC_ID,))
        indexed = cur.fetchone()[0]
    if not indexed:
        cycle = subprocess.run([sys.executable, "/app/ingest.py", "--documents", str(documents_root)],
                               capture_output=True, text=True, env=dict(os.environ))
        print(f"  ingest exited {cycle.returncode}")
        for line in (cycle.stdout or "").strip().splitlines()[-3:]:
            print(f"    {line}")
        with connection.cursor() as cur:
            cur.execute("select count(*) from chunk_index where doc_id = %s", (DOC_ID,))
            indexed = cur.fetchone()[0]
    print(f"  chunks in the lexical index: {indexed}")
    if not indexed:
        problems.append("the fixture was not indexed, so retention has nothing to destroy")

    # AGE IT. The alternative is a retention period of minutes, which is a policy nobody would set and would
    # prove the arithmetic rather than the mechanism.
    with connection.cursor() as cur:
        cur.execute("update ingest_manifest set ingested_at = now() - interval '800 days' where relpath = %s",
                    (DOC_ID,))
        print(f"  aged its ingested_at by 800 days")

    print()
    print("=" * 96)
    print("NO POLICY MEANS NOTHING IS DESTROYED")
    print("=" * 96)
    with connection.cursor() as cur:
        cur.execute("delete from retention_policies where dataset = 'documents'")
    run = policy.enforce(connection, "documents", documents_root, _purge(connection), dry_run=True)
    print(f"  without a policy: retain_days={run.retain_days} examined={run.examined} destroyed={run.destroyed}")
    if run.retain_days != 0 or run.destroyed != 0:
        problems.append("a run with no policy examined or destroyed something")
    if not path.exists():
        problems.append("the file was removed by a run with no policy")

    print()
    print("=" * 96)
    print("A HOLD BEATS A PERIOD")
    print("=" * 96)
    policy.set_policy(connection, "documents", 365, "fixture: the period is short so the run has work to do")
    with connection.cursor() as cur:
        cur.execute("""insert into legal_holds (doc_id, scope, reason, placed_by)
                       values (%s, 'document', 'probe: a hold must beat a period', 'probe') returning id""",
                    (DOC_ID,))
        hold_id = cur.fetchone()[0]
    run = policy.enforce(connection, "documents", documents_root, _purge(connection))
    print(f"  with a hold: examined={run.examined} destroyed={run.destroyed} held={run.held}")
    if run.held != 1 or run.destroyed != 0:
        problems.append(f"a hold did not stop the destruction: {run.as_record()}")
    if not path.exists():
        problems.append("the file was destroyed while a hold was in force")

    print()
    print("=" * 96)
    print("A DRY RUN CHANGES NOTHING AND TELLS THE TRUTH")
    print("=" * 96)
    with connection.cursor() as cur:
        cur.execute("update legal_holds set released_at = now() where id = %s", (hold_id,))
    run = policy.enforce(connection, "documents", documents_root, _purge(connection), dry_run=True)
    print(f"  dry run: examined={run.examined} expired={run.expired} destroyed={run.destroyed} held={run.held}")
    if run.expired < 1:
        problems.append("the dry run did not find the expired document")
    if run.destroyed != 0:
        problems.append("a dry run destroyed something")
    if not path.exists():
        problems.append("a dry run removed the file")

    print()
    print("=" * 96)
    print("THE RUN, for real")
    print("=" * 96)
    run = policy.enforce(connection, "documents", documents_root, _purge(connection))
    print(f"  {json.dumps(run.as_record(), indent=2, sort_keys=True)}")
    if run.destroyed < 1:
        problems.append(f"nothing was destroyed: {run.as_record()}")

    print()
    print("=" * 96)
    print("DATA-RETAIN-001: gone from the vector index, the object store and the answer")
    print("=" * 96)
    with connection.cursor() as cur:
        cur.execute("select count(*) from chunk_index where doc_id = %s", (DOC_ID,))
        lexical = cur.fetchone()[0]
        cur.execute("select count(*) from ingest_manifest where relpath = %s", (DOC_ID,))
        manifest = cur.fetchone()[0]
    vector = _vector_count()
    print(f"  vector index:  {vector}")
    print(f"  lexical index: {lexical}")
    print(f"  manifest:      {manifest}")
    print(f"  object store:  {'present' if path.exists() else 'GONE'}")
    for name, count in (("vector index", vector), ("lexical index", lexical), ("manifest", manifest)):
        if count:
            problems.append(f"the {name} still holds {count} object(s) for the destroyed document")
    if path.exists():
        problems.append("the client's own file is still there, so the document was not destroyed, only hidden")

    import retriever
    import ingest
    services = ingest.Services()
    result = retriever.search(services, f"What does {MARKER} say about disposal?", role="staff", limit=10)
    documents = [passage.doc_id for passage in result.passages]
    print(f"  asking about it returned: {sorted(set(documents))[:4]}")
    if DOC_ID in documents:
        problems.append("the destroyed document still answers a question about it")

    print()
    if problems:
        print(f"FAILED with {len(problems)} problem(s):")
        for problem in problems:
            print(f"  - {problem}")
        return 1
    print("PASSED: a period with no policy destroyed nothing, a hold beat the period, a dry run changed nothing,")
    print("and the real run left the document gone from the vector index, the lexical index, the manifest and the")
    print("client's own files, with a question about it returning no passage from it")
    return 0


def _purge(connection):
    import retention
    return lambda c, doc_id, reason, actor: retention.purge(
        c, doc_id, reason, actor, os.environ.get("VECTOR_URL", "http://vector:6333"),
        os.environ.get("COLLECTION", "documents"))


def _vector_count() -> int:
    import urllib.request
    url = os.environ.get("VECTOR_URL", "http://vector:6333")
    body = {"filter": {"must": [{"key": "doc_id", "match": {"value": DOC_ID}}]}, "exact": True}
    request = urllib.request.Request(f"{url}/collections/{os.environ.get('COLLECTION', 'documents')}/points/count",
                                     data=json.dumps(body).encode(), method="POST",
                                     headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.loads(response.read()).get("result", {}).get("count", 0)


if __name__ == "__main__":
    raise SystemExit(main())
