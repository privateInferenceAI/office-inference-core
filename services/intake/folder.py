"""The folder connector, which polls, because a network filesystem does not raise a local event.

`CON-FOLDER-001` is really a requirement about a test rather than about a feature: "a test that writes locally
and watches a local path passes on a connector that would miss every real arrival". The connector that would
miss them is an inotify one, and the man page is the authority:

    "Inotify reports only events that a user-space program triggers through the filesystem API. As a result,
     it does not catch remote events that occur on network filesystems. (Applications must fall back to
     polling the filesystem to catch such events.)"  -- inotify(7)

A scanner writing into a share, or a person dragging a file onto a mapped drive, is a remote writer. The
event happens on the file server and the kernel under this process never hears about it. So there is no watch
here at all, and the test that proves it does not watch a local path either: it starts this connector as a
process, writes from another process, and asserts the connector holds no inotify descriptor while it runs.

**Two more things a polling connector gets wrong, and both are handled here.**

* **A file that is still being written.** Size and mtime are compared across consecutive scans, and a file
  that has not settled is left alone. Without this, half a scanned invoice becomes a document.
* **A file whose mtime is old.** `rsync`, a scanner and a copy all preserve or reset mtime, so mtime is not a
  fact about arrival. The only watermark kept is the content hash: a file is taken because its bytes have not
  been seen, never because its timestamp is newer than the last scan. A file dropped in with a timestamp from
  last year is taken on this scan.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import content
import provenance
import sender


@dataclass
class Observation:
    size: int
    mtime_ns: int
    scans: int


class FolderConnector:
    """One directory tree, polled, with a settle check and a content-hash watermark."""

    def __init__(self, *, share: str | Path, store: provenance.Store, documents_root: str | Path,
                 root_label: str, deployment_id: str = "unknown", settle_scans: int = 2):
        self.share = Path(share)
        self.store = store
        self.documents_root = Path(documents_root)
        self.root_label = root_label
        self.deployment_id = deployment_id
        self.settle_scans = max(1, int(settle_scans))
        self._observations: dict[str, Observation] = {}

    # -- one scan -----------------------------------------------------------------------------------------

    def scan_once(self, *, now: datetime | None = None) -> dict:
        now = now or datetime.now(timezone.utc)
        result = {"scanned": 0, "waiting": [], "taken": [], "duplicates": [], "skipped": [], "unreadable": 0}

        if not self.share.exists():
            result["missing"] = str(self.share)
            return result

        for path in sorted(self.share.rglob("*")):
            if not path.is_file() or not provenance.is_candidate(path):
                continue
            result["scanned"] += 1
            key = str(path)

            try:
                info = path.stat()
            except FileNotFoundError:
                # The file was renamed or removed between the walk and the stat, which is the race inotify
                # has too. Not an error: the next scan sees whatever is there now.
                continue

            observation = self._observations.get(key)
            if observation and (observation.size, observation.mtime_ns) == (info.st_size, info.st_mtime_ns):
                observation.scans += 1
            else:
                observation = Observation(info.st_size, info.st_mtime_ns, 1)
                self._observations[key] = observation

            if observation.scans < self.settle_scans:
                result["waiting"].append(key)
                continue

            self._take(path, info, result, now)

        return result

    def _take(self, path: Path, before: os.stat_result, result: dict, now: datetime) -> None:
        try:
            raw = path.read_bytes()
        except (OSError, PermissionError) as exc:
            result["unreadable"] += 1
            result["skipped"].append({"path": str(path), "reason": f"{type(exc).__name__}: {exc}"})
            return

        # Re-stat after the read. A file that changed while it was being read is not settled, whatever the
        # previous scan said, and taking it would deposit a torn document.
        try:
            after = path.stat()
        except FileNotFoundError:
            return
        if (after.st_size, after.st_mtime_ns) != (before.st_size, before.st_mtime_ns):
            result["waiting"].append(str(path))
            self._observations.pop(str(path), None)
            return

        digest = provenance.sha256_bytes(raw)
        if self.store.first_with_content(digest) or any(
                row.get("content_sha256") == digest for row in self.store.entries()):
            # Already known by content. No new row, and the file is left where it is: deleting something
            # from a client's share is not this connector's business.
            result["skipped"].append({"path": str(path), "reason": "already taken", "content_sha256": digest})
            return

        relpath = str(path.relative_to(self.share))
        received_at = datetime.fromtimestamp(before.st_mtime, timezone.utc).isoformat()
        message_id = f"folder:{relpath}"

        row = provenance.deposit(
            documents_root=self.documents_root, root=self.root_label, received_at=received_at,
            message_id=message_id, filename=path.name, content=raw)

        # A folder arrival has no sender, no envelope and no authentication results. It is marked rather
        # than trusted, and the reason names the absence instead of leaving it to be inferred from a null.
        sender_verdict = sender.verify(
            headers={}, header_from="", reply_to=None, allowlist=[])
        sender_verdict["verdict"] = "marked"
        sender_verdict["reasons"] = ["no_sender_on_a_folder_arrival"]

        scan = content.scan(subject=path.name)
        record = provenance.build_record(
            deployment_id=self.deployment_id, mailbox=None, folder=relpath,
            channel="folder", message_id=message_id, provider_id=None,
            received_at=received_at, observed_at=now.isoformat(), sender=sender_verdict,
            content_scan=scan, raw_message_sha256=digest, deposit_row=row, duplicate_of=None,
            credential={}, filename=row.filename, media_type="application/octet-stream")

        self.store.append(record)
        result["taken"].append({"relpath": relpath, "intake_id": record["intake_id"],
                                "content_sha256": digest, "bytes": len(raw)})

    # -- the loop -----------------------------------------------------------------------------------------

    def watch(self, *, poll_seconds: int = 60, iterations: int | None = None,
              heartbeat=None, sleep=None) -> list[dict]:
        """Poll until stopped. `iterations` and `sleep` exist for the test, which runs a real process."""
        import time as _time
        sleep = sleep or _time.sleep
        runs = []
        count = 0
        while iterations is None or count < iterations:
            summary = self.scan_once()
            runs.append(summary)
            if heartbeat is not None:
                heartbeat(summary)
            count += 1
            if iterations is not None and count >= iterations:
                break
            sleep(poll_seconds)
        return runs


def heartbeat_payload(summary: dict, channel: str = "folder") -> dict:
    return {
        "at": datetime.now(timezone.utc).isoformat(),
        "channel": channel,
        "messages_seen": summary.get("scanned", 0),
        "deposited": len(summary.get("taken", [])),
        "duplicates": len(summary.get("duplicates", [])),
        "last_error": None,
    }
