"""The record, the deposit, and the dedupe. `CON-INTAKE-001` and `CON-DUPE-001` live here.

**The record is written after the bytes are on disk, and the hash is taken from the file that landed.** That
ordering is the whole reason the hash in this row and the hash in `ingest_manifest` can be compared. Hashing
the bytes in memory and then writing them would be equivalent until the day something rewrites or truncates
the file, and on that day the two numbers would differ with nothing to explain why.

**Dedupe is by content hash rather than by message id.** A re-send and a forward are two messages with two
`Message-ID`s and identical attachments, and the requirement is explicit that both are recognised. A message
id would see two arrivals, because they are two arrivals. The content is what is the same, so the content is
what is compared.

**A duplicate is recorded, not dropped.** The row exists with `status = duplicate` and `duplicate_of` naming
the first one. A connector that silently drops the second copy is a connector where "the invoice never
arrived" and "the invoice arrived twice" look the same in the record, and they are different facts.

**The deposit lands under the ingestion worker's own root.** `<documents_root>/<root>/intake/<date>/<id>/`
puts the access label where the worker derives it, from the first path component. The file is written under
a `.part` name and renamed: the worker walks the same tree, and `.part` is already in its list of suffixes it
will not read, so a half-written invoice never becomes a document.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import sys
import unicodedata
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

# The columns of `schema/001_intake_events.sql`, in one place, and the test asserts the two lists are equal.
# A record whose keys and whose table drift apart writes a row with a column missing and nobody notices until
# an auditor asks for a field that was never stored.
COLUMNS = (
    "intake_id", "deployment_id", "channel", "mailbox", "folder",
    "message_id", "provider_id", "received_at", "observed_at",
    "sender_from", "sender_envelope", "sender_reply_to",
    "auth_spf", "auth_dkim", "auth_dmarc",
    "verdict", "verdict_reasons", "allowlisted",
    "content_sha256", "bytes", "relpath", "filename", "media_type", "unreadable_by_ingest",
    "raw_message_sha256", "duplicate_of", "status", "content_flags", "requires_review",
    "credential_path", "credential_mode",
)

UNSAFE = re.compile(r"[^A-Za-z0-9._-]+")

# What the folder connector will not look at. The worker's own list is longer; this one is about files that
# are not documents at all and would otherwise be deposited by the hundred.
SKIP_NAMES = frozenset({".ds_store", "thumbs.db", "desktop.ini"})
SKIP_SUFFIXES = frozenset({".tmp", ".temp", ".swp", ".swo", ".bak", ".lock", ".crdownload", ".part",
                           ".partial", ".lnk", ".url"})


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> tuple[str, int]:
    """The file's bytes, hashed, and its size. The same computation the ingestion worker makes."""
    digest = hashlib.sha256()
    size = 0
    with Path(path).open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
            size += len(block)
    return digest.hexdigest(), size


def safe_name(name: str) -> str:
    """A filename that cannot escape the directory it is written into.

    An attachment name is attacker-controlled text. `../../etc/cron.d/x` and a name with a newline in it are
    both legal in a MIME part, and both are handled by reducing the name to a conservative character set and
    refusing the path components outright rather than by sanitising the separators and hoping.
    """
    normalised = unicodedata.normalize("NFKD", str(name or "attachment"))
    base = os.path.basename(normalised.replace("\\", "/")).strip()
    base = base.lstrip(".") or "attachment"
    cleaned = UNSAFE.sub("_", base).strip("_")
    if not cleaned:
        cleaned = "attachment"
    return cleaned[:120]


def accepted_suffixes() -> frozenset[str]:
    """The suffixes the ingestion worker will actually read, imported from the worker rather than copied.

    A copy agrees on the day it is written and drifts on the day somebody adds a format. This follows the
    pattern the audit tool uses for the statement verifier: import it where it lives, look one directory over
    for the checkout, and if neither is present say so instead of guessing. A wrong answer here is a document
    reported as ingested while the worker walks past it.
    """
    try:
        import formats  # type: ignore
        return frozenset(formats.ACCEPTED_SUFFIXES)
    except ImportError:
        pass
    sibling = Path(__file__).resolve().parent.parent / "ingestion" / "formats.py"
    if sibling.exists():
        sys.path.insert(0, str(sibling.parent))
        import formats  # type: ignore
        return frozenset(formats.ACCEPTED_SUFFIXES)
    # Absent means unknown, and unknown is reported as unknown rather than as "fine".
    return frozenset()


def would_ingest(filename: str, accepted: frozenset[str]) -> bool | None:
    """Whether the worker will read this file, or None when the worker's list is not available."""
    if not accepted:
        return None
    return Path(filename).suffix.lower() in accepted


@dataclass
class Deposit:
    relpath: str
    path: Path
    filename: str
    content_sha256: str
    bytes: int
    unreadable_by_ingest: bool | None


def intake_id_for(*, message_id: str, filename: str, content_sha256: str) -> str:
    """A stable id, so re-processing the same message is idempotent rather than a second row.

    The same message read twice after a crash produces the same id and is recognised. A *different* message
    carrying the same attachment produces a different id and is caught by the content hash, which is the
    distinction `CON-DUPE-001` draws between a re-send and a forward.
    """
    material = f"{message_id}\x00{filename}\x00{content_sha256}".encode()
    return "in-" + hashlib.sha256(material).hexdigest()[:20]


def deposit(*, documents_root: Path, root: str, received_at: str, message_id: str,
            filename: str, content: bytes, index: int = 0) -> Deposit:
    """Write the bytes under the documents root and hash the file that landed."""
    documents_root = Path(documents_root)
    day = (received_at or datetime.now(timezone.utc).isoformat())[:10]
    message_key = hashlib.sha256((message_id or "unknown").encode()).hexdigest()[:8]

    name = safe_name(filename)
    if index:
        stem, dot, suffix = name.rpartition(".")
        name = f"{stem or name}-{index + 1}{dot}{suffix}" if dot else f"{name}-{index + 1}"

    target_dir = documents_root / root / "intake" / day / message_key
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / name

    # `.part` is in the ingestion worker's ignored suffixes, so the tree it walks never contains a partial
    # file. `os.replace` is atomic within a filesystem, which is what makes the rename visible all at once.
    temporary = target.with_name("." + target.name + ".part")
    with temporary.open("wb") as handle:
        handle.write(content)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, target)

    digest, size = sha256_file(target)
    return Deposit(
        relpath=str(target.relative_to(documents_root)),
        path=target,
        filename=name,
        content_sha256=digest,
        bytes=size,
        unreadable_by_ingest=would_ingest(name, accepted_suffixes()),
    )


def is_candidate(path: Path) -> bool:
    """Whether the folder connector should look at this file at all."""
    name = path.name
    if name.startswith("."):
        return False
    if name.lower() in SKIP_NAMES or name.lower().endswith(".part"):
        return False
    return path.suffix.lower() not in SKIP_SUFFIXES


def build_record(*, deployment_id: str, mailbox: str | None, folder: str | None,
                 channel: str, message_id: str, provider_id: str | None,
                 received_at: str, observed_at: str, sender: dict, content_scan: dict,
                 raw_message_sha256: str, deposit_row: Deposit | None,
                 duplicate_of: str | None, credential: dict, filename: str,
                 media_type: str) -> dict:
    """One row, whether or not the content is new.

    A duplicate carries the hash and the sender of the copy that arrived, and points at the first. That is
    what makes "it arrived twice" provable rather than asserted.

    **The mailbox is a parameter rather than something read off the configuration**, and the folder connector
    is why. A file that appeared in a share did not arrive at a mailbox, and a record that named one because
    the configuration happened to have one would be a record that lies about where the document came from.
    """
    status = "duplicate" if duplicate_of else "deposited"
    record = {
        "intake_id": intake_id_for(message_id=message_id or "", filename=filename,
                                   content_sha256=(deposit_row.content_sha256 if deposit_row else "")),
        "deployment_id": deployment_id,
        "channel": channel,
        "mailbox": mailbox,
        "folder": folder,
        "message_id": message_id,
        "provider_id": provider_id,
        "received_at": received_at,
        "observed_at": observed_at,
        "sender_from": sender.get("header_from"),
        "sender_envelope": sender.get("envelope_from"),
        "sender_reply_to": sender.get("reply_to"),
        "auth_spf": sender.get("auth", {}).get("spf"),
        "auth_dkim": sender.get("auth", {}).get("dkim"),
        "auth_dmarc": sender.get("auth", {}).get("dmarc"),
        "verdict": sender.get("verdict"),
        "verdict_reasons": sender.get("reasons", []),
        "allowlisted": sender.get("allowlisted"),
        "content_sha256": deposit_row.content_sha256 if deposit_row else None,
        "bytes": deposit_row.bytes if deposit_row else None,
        "relpath": deposit_row.relpath if deposit_row else None,
        "filename": filename,
        "media_type": media_type,
        "unreadable_by_ingest": deposit_row.unreadable_by_ingest if deposit_row else None,
        "raw_message_sha256": raw_message_sha256,
        "duplicate_of": duplicate_of,
        "status": status,
        "content_flags": content_scan.get("signals", []),
        "requires_review": True,
        "credential_path": credential.get("path"),
        "credential_mode": credential.get("mode"),
    }
    return record


class Store:
    """The append-only journal, and the mirror into Postgres when a deployment has one.

    **The journal is the record of record here**, and that is a decision rather than a fallback. It lives on
    the client's box under their own state directory, it survives the database being restored from a backup,
    and reading it needs no driver. The Postgres mirror exists so the row can be joined to the rest of the
    system, and a deployment without a database still has provenance.
    """

    def __init__(self, state_dir: str | Path, dsn: str | None = None, mirror=None):
        self.state_dir = Path(state_dir)
        self.state_dir.mkdir(parents=True, exist_ok=True)
        self.journal = self.state_dir / "intake-events.jsonl"
        self.outbox = self.state_dir / "outbox"
        self.dsn = dsn
        self._mirror = mirror

    # -- reading ------------------------------------------------------------------------------------------

    def entries(self) -> list[dict]:
        if not self.journal.exists():
            return []
        rows = []
        for line in self.journal.read_text().splitlines():
            if line.strip():
                try:
                    rows.append(json.loads(line))
                except json.JSONDecodeError:
                    # A torn last line is a crash between write and flush. Skipped rather than fatal: the
                    # next poll re-reads the message and writes it again, because `intake_id` is stable.
                    continue
        return rows

    def by_id(self, intake_id: str) -> dict | None:
        for row in self.entries():
            if row.get("intake_id") == intake_id:
                return row
        return None

    def first_with_content(self, content_sha256: str) -> dict | None:
        """The earliest deposited row for this content, which is the one a duplicate points at."""
        for row in self.entries():
            if row.get("content_sha256") == content_sha256 and row.get("status") == "deposited":
                return row
        return None

    def arrivals_between(self, start: datetime, end: datetime) -> list[dict]:
        out = []
        for row in self.entries():
            if row.get("status") != "deposited":
                continue
            try:
                when = datetime.fromisoformat(row.get("received_at") or row.get("observed_at"))
            except ValueError:
                continue
            if when.tzinfo is None:
                when = when.replace(tzinfo=timezone.utc)
            if start <= when < end:
                out.append(row)
        return out

    def last_arrival(self) -> datetime | None:
        latest = None
        for row in self.entries():
            if row.get("status") != "deposited":
                continue
            try:
                when = datetime.fromisoformat(row.get("received_at") or row.get("observed_at"))
            except (ValueError, TypeError):
                continue
            if when.tzinfo is None:
                when = when.replace(tzinfo=timezone.utc)
            if latest is None or when > latest:
                latest = when
        return latest

    # -- writing ------------------------------------------------------------------------------------------

    def append(self, entry: dict) -> dict:
        """Append one row, once. Re-appending the same `intake_id` returns the row already there."""
        existing = self.by_id(entry["intake_id"])
        if existing:
            return existing
        missing = [column for column in COLUMNS if column not in entry]
        if missing:
            raise ValueError(f"the record is missing {', '.join(missing)}; the schema and the writer disagree")
        line = json.dumps({column: entry[column] for column in COLUMNS}, sort_keys=True, ensure_ascii=False)
        with self.journal.open("a") as handle:
            handle.write(line + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        self._mirror_row(entry)
        if entry.get("status") == "deposited":
            self.write_notice(entry)
        return entry

    def _mirror_row(self, entry: dict) -> None:
        """The Postgres row, when the deployment has a database. Absent is not an error."""
        if self._mirror is None and self.dsn:
            import psycopg  # noqa: PLC0415 - only a deployment with a database needs the driver
            from psycopg.types.json import Jsonb  # noqa: PLC0415
            self._mirror = psycopg.connect(self.dsn, autocommit=True)
            self._jsonb = Jsonb
        if self._mirror is None:
            return
        placeholders = ",".join(["%s"] * len(COLUMNS))
        values = []
        for column in COLUMNS:
            value = entry[column]
            if isinstance(value, (list, dict)):
                value = self._jsonb(value)
            values.append(value)
        with self._mirror.cursor() as cur:
            cur.execute(
                f"insert into intake_events ({','.join(COLUMNS)}) values ({placeholders}) "
                f"on conflict (intake_id) do nothing", values)

    def write_notice(self, entry: dict) -> Path:
        """The seam to the workflow: one file per deposited document, and none for a duplicate.

        This is the only thing intake publishes. A duplicate writes no notice, which is how "the workflow did
        not run twice" is a fact about the filesystem rather than a claim about the workflow.
        """
        self.outbox.mkdir(parents=True, exist_ok=True)
        notice = self.outbox / f"{entry['intake_id']}.json"
        payload = {
            "intake_id": entry["intake_id"],
            "relpath": entry["relpath"],
            "content_sha256": entry["content_sha256"],
            "bytes": entry["bytes"],
            "received_at": entry["received_at"],
            "sender_from": entry["sender_from"],
            "verdict": entry["verdict"],
            "requires_review": True,
            "content_flags": entry["content_flags"],
            # Named in the notice so the workflow cannot describe this as an action intake asked for.
            "requested_actions": [],
        }
        notice.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
        return notice

    def notices(self) -> list[dict]:
        if not self.outbox.exists():
            return []
        return [json.loads(path.read_text()) for path in sorted(self.outbox.glob("*.json"))]
