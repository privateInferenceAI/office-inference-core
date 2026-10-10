#!/usr/bin/env python3
"""Ingest documents into the vector store.

Run it as often as you like. It compares each file's hash against the ingest manifest and only
processes what changed, deletes the points of documents that have gone away, and records what it did
in the audit sink. That combination is what makes it safe to put on a timer: a no-op run costs
nothing, and a document that gets deleted stops being retrievable instead of lingering forever.

Usage:
    python ingest.py                 # ingest changes under DOCS_DIR
    python ingest.py --force         # re-ingest everything
    python ingest.py --dry-run       # say what would happen
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import psycopg

from common import (DOCS_DIR, acl_for, chunk_pages, delete_document, doc_id_for, embed,
                    ensure_collection, parse, qdrant, record_event, sha256_file, upsert_chunks,
                    DATABASE_URL, COLLECTION)


def known_documents() -> dict[str, str]:
    if not DATABASE_URL:
        return {}
    with psycopg.connect(DATABASE_URL) as conn, conn.cursor() as cur:
        cur.execute("select doc_id, sha256 from ingest_manifest")
        return dict(cur.fetchall())


def remember(doc_id: str, digest: str, chunks: int, pages: int, acl: str) -> None:
    if not DATABASE_URL:
        return
    with psycopg.connect(DATABASE_URL, autocommit=True) as conn, conn.cursor() as cur:
        cur.execute(
            """insert into ingest_manifest (doc_id, sha256, chunks, pages, acl, ingested_at)
               values (%s,%s,%s,%s,%s, now())
               on conflict (doc_id) do update
                 set sha256 = excluded.sha256, chunks = excluded.chunks, pages = excluded.pages,
                     acl = excluded.acl, ingested_at = now()""",
            (doc_id, digest, chunks, pages, acl),
        )


def forget(doc_id: str) -> None:
    if not DATABASE_URL:
        return
    with psycopg.connect(DATABASE_URL, autocommit=True) as conn, conn.cursor() as cur:
        cur.execute("delete from ingest_manifest where doc_id = %s", (doc_id,))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--force", action="store_true", help="re-ingest unchanged documents")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--docs-dir", default=str(DOCS_DIR))
    args = parser.parse_args()

    root = Path(args.docs_dir)
    if not root.exists():
        print(f"no documents directory at {root}", file=sys.stderr)
        return 2

    client = qdrant()
    ensure_collection(client)

    previous = known_documents()
    files = sorted(p for p in root.rglob("*") if p.is_file() and p.suffix.lower() in
                   {".pdf", ".docx", ".html", ".htm", ".md", ".markdown", ".txt", ".csv"})
    seen: set[str] = set()
    added = changed = skipped = failed = removed = 0

    for path in files:
        doc_id = doc_id_for(path)
        seen.add(doc_id)
        digest = sha256_file(path)
        acl = acl_for(doc_id)

        if previous.get(doc_id) == digest and not args.force:
            skipped += 1
            continue

        action = "update" if doc_id in previous else "add"
        try:
            pages = parse(path)
        except Exception as exc:                                    # a broken file must not stop the run
            print(f"  FAIL {doc_id}: {exc}")
            failed += 1
            continue

        # A scanned document extracts as nothing. That is a gap in capability, not an empty file, and
        # it must be loud: a document the system quietly ignores looks exactly like a document it has
        # never been told about, which is how somebody loses trust in an answer.
        empty_pages = [number for number, page in enumerate(pages, start=1) if not page.strip()]
        chunks = chunk_pages(pages)
        if not chunks:
            reason = ("no extractable text; likely a scan or an image-only PDF, which needs OCR "
                      "(not enabled yet)")
            print(f"  FAIL {doc_id}: {reason}")
            if not args.dry_run:
                record_event("config_change", actor="ingestion", actor_role="system",
                             outcome="error", tool="ingest",
                             payload={"doc_id": doc_id, "reason": reason, "pages": len(pages),
                                      "acl": acl, "sha256": digest})
            failed += 1
            continue
        if empty_pages:
            print(f"  NOTE {doc_id}: pages {empty_pages} of {len(pages)} have no extractable text")

        if args.dry_run:
            print(f"  would {action} {doc_id}: {len(pages)} page(s), {len(chunks)} chunk(s), acl={acl}")
            continue

        vectors = embed([chunk.text for chunk in chunks])
        delete_document(client, doc_id)                              # replace, never accumulate
        upsert_chunks(client, doc_id, acl, doc_id, chunks, vectors, digest)
        remember(doc_id, digest, len(chunks), len(pages), acl)
        record_event("config_change", actor="ingestion", actor_role="system", tool="ingest",
                     data_class="internal" if acl == "company" else "confidential",
                     payload={"doc_id": doc_id, "action": action, "pages": len(pages),
                              "chunks": len(chunks), "acl": acl, "sha256": digest,
                              "pages_without_text": empty_pages})
        print(f"  {action:>6} {doc_id}: {len(pages)} page(s), {len(chunks)} chunk(s), acl={acl}")
        if action == "add":
            added += 1
        else:
            changed += 1

    for doc_id in sorted(set(previous) - seen):
        if args.dry_run:
            print(f"  would remove {doc_id}")
            continue
        delete_document(client, doc_id)
        forget(doc_id)
        record_event("config_change", actor="ingestion", actor_role="system", tool="ingest",
                     payload={"doc_id": doc_id, "action": "remove"})
        print(f"  remove {doc_id}")
        removed += 1

    try:
        total = client.count(COLLECTION).count
    except Exception as exc:                                        # reporting must not break a run
        total = f"unavailable ({exc})"
    print(f"\nadded {added}, changed {changed}, removed {removed}, skipped {skipped}, failed {failed}")
    print(f"points in {COLLECTION}: {total}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
