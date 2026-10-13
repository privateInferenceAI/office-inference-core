"""Time-based retention: a period runs out and the material is destroyed, including the client's own copy.

`DATA-RETAIN-001` asks for retention defined for documents and conversations rather than only the audit log, and
its verify line names three places a document has to be gone from: **the vector index, the object store, and the
answer.**

**The object store is the half that a job built by reusing the purge would miss.** `DATA-PURGE-001` is a client
asking us to stop holding a copy and it deliberately leaves the file: the client still has their document.
Retention is a period running out, and "keep invoices for seven years" means that after seven years there is no
invoice. A retention job that cleared the indexes and left the file would report success while the thing the
client asked to be destroyed sat in their documents directory, and nothing would ever surface it because the
report would say the run succeeded.

**No policy means nothing is destroyed.** A default period invented here would delete a client's documents on a
number they never agreed to, which is the one mistake in this file that cannot be undone.

**A hold beats a period.** The period says the client agreed to lose the document by now; the hold says a court
has told them not to. The hold wins, it is counted separately from a destruction, and the run says so.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path

# The datasets the requirement names. Kept beside the check constraint in the migration so the two cannot drift
# without one of them refusing.
DATASETS = ("documents", "conversations")


@dataclass
class Policy:
    dataset: str
    retain_days: int
    legal_basis: str | None = None


@dataclass
class Run:
    """What a retention run did, and what it deliberately did not."""

    dataset: str
    retain_days: int
    cutoff: datetime
    examined: int = 0
    expired: int = 0
    destroyed: int = 0
    held: int = 0
    failed: int = 0
    dry_run: bool = False
    failures: list[dict] = field(default_factory=list)

    @property
    def complete(self) -> bool:
        return self.failed == 0

    def as_record(self) -> dict:
        return {"dataset": self.dataset, "retain_days": self.retain_days,
                "cutoff": self.cutoff.isoformat(), "examined": self.examined,
                "expired": self.expired, "destroyed": self.destroyed, "held": self.held,
                "failed": self.failed, "dry_run": self.dry_run, "failures": self.failures}


def read_policy(connection, dataset: str) -> Policy | None:
    with connection.cursor() as cur:
        cur.execute("select dataset, retain_days, legal_basis from retention_policies where dataset = %s",
                    (dataset,))
        row = cur.fetchone()
    return Policy(row[0], row[1], row[2]) if row else None


def set_policy(connection, dataset: str, retain_days: int, legal_basis: str | None = None) -> None:
    if dataset not in DATASETS:
        raise ValueError(f"unknown dataset {dataset!r}. One of {list(DATASETS)}.")
    if retain_days <= 0:
        raise ValueError(f"a retention of {retain_days} days destroys everything on the next run or nothing "
                         f"ever. A period that is not a period is not a policy.")
    with connection.cursor() as cur:
        cur.execute("""
            insert into retention_policies (dataset, retain_days, legal_basis, updated_at)
            values (%s, %s, %s, now())
            on conflict (dataset) do update set
              retain_days = excluded.retain_days,
              legal_basis = excluded.legal_basis,
              updated_at = now()""", (dataset, retain_days, legal_basis))


def expired_documents(connection, cutoff: datetime) -> list[dict]:
    """Documents ingested before the cutoff, with where their file is.

    Age is measured from `ingested_at`, which is when this system first held the document, and not from the
    file's modification time. The file's date is a fact about the client's filesystem: copying a folder resets
    every mtime, and a document whose mtime was reset would be retained for another period without anybody
    deciding to. `ingested_at` is when the obligation to keep it started, which is the date a retention policy
    is about.

    A document with no `ingested_at` is NOT expired, which is the safe direction: an unknown age is not the same
    as an old one.
    """
    with connection.cursor() as cur:
        cur.execute("""
            select relpath, root, ingested_at, chunk_count
            from ingest_manifest
            where ingested_at is not null and ingested_at < %s
            order by ingested_at""", (cutoff,))
        return [{"doc_id": row[0], "root": row[1], "ingested_at": row[2], "chunks": row[3]}
                for row in cur.fetchall()]


def expired_conversations(connection, cutoff: datetime) -> int:
    with connection.cursor() as cur:
        cur.execute("select count(*) from conversations where asked_at < %s", (cutoff,))
        return cur.fetchone()[0]


def enforce(connection, dataset: str, documents_root: Path | None, purge, actor: str = "retention-job",
            dry_run: bool = False, now: datetime | None = None) -> Run:
    """Destroy what a period has run out on. `purge` is `retention.purge`, injected so this is testable.

    The document case clears the stores AND unlinks the file, in that order. The stores first because a failure
    between the two leaves a file with no index, which is recoverable by re-ingesting; the other order would
    leave an index pointing at a file that is gone, which is a passage the system will cite and nobody can open.
    """
    policy = read_policy(connection, dataset)
    if policy is None:
        # No policy is not an error and not a default. It is the state a deployment is in until somebody
        # decides, and destroying on an invented number is the one mistake here that cannot be undone.
        now = now or datetime.now(timezone.utc)
        return Run(dataset=dataset, retain_days=0, cutoff=now, dry_run=dry_run)

    now = now or datetime.now(timezone.utc)
    cutoff = now - timedelta(days=policy.retain_days)
    run = Run(dataset=dataset, retain_days=policy.retain_days, cutoff=cutoff, dry_run=dry_run)

    if dataset == "conversations":
        run.examined = expired_conversations(connection, cutoff)
        run.expired = run.examined
        if not dry_run and run.expired:
            with connection.cursor() as cur:
                cur.execute("delete from conversations where asked_at < %s", (cutoff,))
                run.destroyed = cur.rowcount
        else:
            run.destroyed = 0 if dry_run else run.destroyed
        _record(connection, run, actor)
        return run

    documents = expired_documents(connection, cutoff)
    run.examined = len(documents)
    run.expired = len(documents)
    for document in documents:
        doc_id = document["doc_id"]
        if dry_run:
            # The hold is still checked on a dry run, because a dry run whose output does not match the real one
            # is a rehearsal of a different operation.
            if _held(connection, doc_id):
                run.held += 1
            continue
        if _held(connection, doc_id):
            run.held += 1
            continue
        try:
            outcome = purge(connection, doc_id, f"retention: {policy.retain_days} days elapsed", actor)
            if outcome.refused:
                run.held += 1
                continue
            if not outcome.complete:
                raise RuntimeError(f"the stores did not all confirm: {outcome.stores}")
            # The client's own copy, which is the half a job built by reusing the purge would miss.
            if documents_root is not None:
                path = Path(documents_root) / doc_id
                if path.exists():
                    path.unlink()
            run.destroyed += 1
        except Exception as exc:  # noqa: BLE001
            run.failed += 1
            run.failures.append({"doc_id": doc_id, "error": f"{type(exc).__name__}: {exc}"})

    _record(connection, run, actor)
    return run


def _held(connection, doc_id: str) -> bool:
    with connection.cursor() as cur:
        cur.execute("""select 1 from legal_holds
                       where released_at is null
                         and (scope = 'deployment' or (scope = 'document' and doc_id = %s))
                       limit 1""", (doc_id,))
        return cur.fetchone() is not None


def _record(connection, run: Run, actor: str) -> None:
    with connection.cursor() as cur:
        cur.execute("""
            insert into retention_runs (dataset, finished_at, retain_days, cutoff, examined, expired,
                                        destroyed, held, failed, dry_run, actor, failures)
            values (%s, now(), %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
            (run.dataset, run.retain_days, run.cutoff, run.examined, run.expired, run.destroyed,
             run.held, run.failed, run.dry_run, actor,
             json.dumps(run.failures, sort_keys=True) if run.failures else None))
