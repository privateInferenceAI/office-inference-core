#!/usr/bin/env python3
"""The retention commands: purge a document, hold one, and destroy everything at the end.

Kept as a separate entry point from the audit service because it deletes from stores the audit service does not
know about, and a command that clears the vector index should not live inside the thing that records what
happened.
"""
from __future__ import annotations

import argparse
import json
import os
import sys

import retention


def connect():
    import psycopg
    return psycopg.connect(os.environ["DATABASE_URL"], autocommit=True)


def main() -> int:
    parser = argparse.ArgumentParser(description="Purge a client's document everywhere it is stored.")
    parser.add_argument("--purge", metavar="DOC_ID",
                        help="delete this document from every store. Refused if a hold covers it.")
    parser.add_argument("--reason", help="why, in the client's words. Recorded.")
    parser.add_argument("--actor", default="retention-job", help="who or what is asking")
    parser.add_argument("--hold", metavar="DOC_ID", nargs="?", const="",
                        help="place a hold. With no document, the hold covers the whole deployment and every "
                             "document added later.")
    parser.add_argument("--release-hold", metavar="HOLD_ID", type=int, help="release a hold by id")
    parser.add_argument("--holds", action="store_true", help="list the holds in force")
    parser.add_argument("--purges", action="store_true", help="list what has been purged")
    parser.add_argument("--destroy-all", action="store_true",
                        help="destroy every document in every store and write a destruction record")
    parser.add_argument("--set-retention", metavar="DATASET",
                        help="set the period for a dataset: documents or conversations")
    parser.add_argument("--days", type=int, help="how many days to keep, with --set-retention")
    parser.add_argument("--legal-basis", help="why that period, in the client's or their regulator's words")
    parser.add_argument("--enforce", metavar="DATASET",
                        help="destroy what the period has run out on. Refuses if no policy is set.")
    parser.add_argument("--documents-root", default=os.environ.get("DOCUMENTS_ROOT", "/documents"),
                        help="where the client's files are, because retention destroys those too")
    parser.add_argument("--retention-runs", action="store_true", help="what retention has done")
    # The flag the enforce path reads and the manual documents. The commit that added --enforce called
    # `args.dry_run` without ever defining it, so `--enforce documents --dry-run` was rejected as an unknown
    # argument and plain `--enforce documents` died with AttributeError before doing anything. **The acceptance
    # probe called policy.enforce(dry_run=True) directly and never went through this path**, which is how a
    # broken command passed a test that was named for it.
    parser.add_argument("--dry-run", action="store_true",
                        help="report what a run would destroy without destroying it")
    parser.add_argument("--vector-url", default=os.environ.get("VECTOR_URL", "http://vector:6333"))
    parser.add_argument("--collection", default=os.environ.get("COLLECTION", "documents"))
    args = parser.parse_args()

    if not os.environ.get("DATABASE_URL"):
        print("DATABASE_URL is not set, so there is nothing to connect to", file=sys.stderr)
        return 1

    if args.set_retention:
        if not args.days:
            print("--set-retention needs --days. A period is the whole point.", file=sys.stderr)
            return 1
        import policy as policy_module
        with connect() as connection:
            policy_module.set_policy(connection, args.set_retention, args.days, args.legal_basis)
        print(f"{args.set_retention} retained for {args.days} days"
              + (f": {args.legal_basis!r}" if args.legal_basis else ""))
        print("Nothing is destroyed on a schedule until a policy exists, and this is now one.")
        return 0

    if args.enforce:
        import policy as policy_module
        from pathlib import Path as _Path
        with connect() as connection:
            run = policy_module.enforce(connection, args.enforce, _Path(args.documents_root),
                                        retention.purge, actor=args.actor, dry_run=args.dry_run)
        print(json.dumps(run.as_record(), indent=2, sort_keys=True))
        if run.retain_days == 0:
            print()
            print(f"NO POLICY for {args.enforce}, so nothing was destroyed. Set one with --set-retention.")
            return 1
        if not run.complete:
            print()
            print(f"INCOMPLETE: {run.failed} document(s) failed. The run is recorded and re-running is safe.")
            return 1
        print()
        print(f"destroyed {run.destroyed}, held {run.held}, failed {run.failed}"
              + (" (dry run, nothing was changed)" if run.dry_run else ""))
        return 0

    if args.retention_runs:
        with connect() as connection, connection.cursor() as cur:
            cur.execute("""select dataset, started_at, retain_days, examined, destroyed, held, failed, dry_run
                           from retention_runs order by id desc limit 20""")
            rows = cur.fetchall()
        if not rows:
            print("retention has never run")
        for row in rows:
            mark = "dry run " if row[7] else "        "
            print(f"  {row[1]}  {row[0]:<14}{mark}period {row[2]}d  examined {row[3]}  destroyed {row[4]}  "
                  f"held {row[5]}  failed {row[6]}")
        return 0

    if args.holds:
        with connect() as connection, connection.cursor() as cur:
            cur.execute("""select id, scope, coalesce(doc_id, '(whole deployment)'), reason, placed_by,
                                  placed_at
                           from legal_holds where released_at is null order by id""")
            rows = cur.fetchall()
        if not rows:
            print("no holds in force")
        for row in rows:
            print(f"  {row[0]}  {row[1]:<11} {row[2]:<40} {row[3]!r} by {row[4]} at {row[5]}")
        return 0

    if args.purges:
        with connect() as connection, connection.cursor() as cur:
            cur.execute("""select doc_id, complete, purged_at, stores from purge_records
                           order by id desc limit 20""")
            rows = cur.fetchall()
        if not rows:
            print("nothing has been purged")
        for doc_id, complete, when, stores in rows:
            mark = "complete" if complete else "INCOMPLETE"
            print(f"  {when}  {mark:<11} {doc_id}")
            print(f"      {json.dumps(stores, sort_keys=True)}")
        return 0

    if args.hold is not None:
        if not args.reason:
            print("--hold needs --reason: a hold nobody can explain is one nobody can release", file=sys.stderr)
            return 1
        with connect() as connection:
            hold_id = retention.place_hold(connection, args.reason, args.actor,
                                           args.hold or None)
        where = args.hold or "the whole deployment, including documents added later"
        print(f"hold {hold_id} placed on {where}: {args.reason!r}")
        print("A purge of anything it covers is refused and the refusal is recorded.")
        return 0

    if args.release_hold:
        with connect() as connection:
            released = retention.release_hold(connection, args.release_hold, args.actor)
        if released:
            print(f"hold {args.release_hold} released by {args.actor}")
            return 0
        print(f"hold {args.release_hold} is not in force, so there was nothing to release", file=sys.stderr)
        return 1

    if args.destroy_all:
        if not args.reason:
            print("--destroy-all needs --reason", file=sys.stderr)
            return 1
        with connect() as connection:
            record = retention.destroy_all(connection, args.reason, args.actor,
                                           args.vector_url, args.collection)
        print(json.dumps(record, indent=2, sort_keys=True))
        print()
        print("Keep that file. Its hash is what lets you show later that it is the record we produced.")
        return 0 if record["complete"] else 1

    if args.purge:
        if not args.reason:
            print("--purge needs --reason: the record of a deletion without a reason is one nobody can review",
                  file=sys.stderr)
            return 1
        with connect() as connection:
            result = retention.purge(connection, args.purge, args.reason, args.actor,
                                     args.vector_url, args.collection,
                                     dry_run=args.dry_run, documents_root=args.documents_root)
        print(json.dumps(result.as_record(), indent=2, sort_keys=True))
        # The rehearsal exits 0 and says what it would do, after the refusal check, because a refusal is a
        # refusal whether or not the run was real and it is the outcome a careful operator is checking for.
        if result.refused:
            print()
            print(f"REFUSED: {result.refusal_reason}")
            return 1
        if result.dry_run:
            # Every value here is a per-store dictionary. It is summed defensively anyway, because the note
            # used to live inside this map as a bare string and this line died with `'str' object has no
            # attribute 'get'` on the one case it exists for: a document id that matches nothing.
            counts = [int(v.get("would_delete") or 0) for v in result.stores.values() if isinstance(v, dict)]
            total = sum(counts)
            print()
            print(f"DRY RUN, nothing was changed. A real purge would remove {total} object(s) across "
                  f"{len([c for c in counts if c])} store(s).")
            if result.note:
                print(result.note)
            if not result.complete:
                print("It would be INCOMPLETE for the reason recorded under audit_payload.")
            return 0
        if not result.complete:
            print()
            print("INCOMPLETE: at least one store did not confirm. The document is NOT purged, whatever the "
                  "other stores reported.")
            return 1
        print()
        print(f"purged from {len(result.stores)} store(s), {result.total_deleted} object(s) reported removed")
        return 0

    parser.print_help()
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
