"""The folder connector polls, and this file is written so that it could not pass otherwise.

`CON-FOLDER-001` is a requirement about a test: "a test that writes locally and watches a local path passes on
a connector that would miss every real arrival, because inotify's own man page says it does not catch remote
events on network filesystems."

So the test does three things a local write-and-watch test does not:

1. **It runs the connector as its own process**, through the command line a deployment would use.
2. **It writes the file from a second process**, with `shutil.copy2`, which preserves mtime the way `rsync`
   and a scanner do.
3. **It watches the connector's file descriptors while it runs** and asserts the process never holds an
   inotify descriptor. A connector that cannot hold one cannot be relying on events, whatever its comments
   say.

**What this still does not prove, said plainly.** The share here is a local directory, so the kernel under the
connector does see the write. Proving the behaviour over a real SMB or NFS mount needs the client's server,
and that is a deployment step. What is proved here is the mechanism: the connector takes a file it was never
notified about, its discovery does not depend on any event source, and it holds no watcher. The remaining
question is the network filesystem itself, not this code.
"""
from __future__ import annotations

import ast
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tests" / "fixtures"))
sys.path.insert(0, str(ROOT / "services" / "intake"))

import intake_env  # noqa: E402

env = intake_env
config_module = intake_env.load("config")
content = intake_env.load("content")
folder = intake_env.load("folder")
net = intake_env.load("net")
provenance = intake_env.load("provenance")
sender = intake_env.load("sender")
secretstore = intake_env.load("secretstore")

INTAKE_DIR = ROOT / "services" / "intake"

# The modules a connector would import if it were listening for filesystem events rather than looking.
WATCHER_MODULES = {"inotify", "inotify_simple", "pyinotify", "watchdog", "fsevents", "pyfanotify"}


@pytest.fixture(autouse=True)
def clean_transport():
    yield
    net.set_transport(None)


def build_connector(tmp_path, *, settle_scans: int = 2):
    """A connector over the test's own directory tree, with no server and no mailbox involved."""
    server = env.FakeGraph()          # not started: the folder channel reaches nothing
    config_path = env.environment(tmp_path, server)
    config = config_module.load(config_path)
    assert not config.problems, config.problems
    store = provenance.Store(config.state_dir)
    connector = folder.FolderConnector(
        share=config.intake.get("share"), store=store, documents_root=config.documents_root,
        root_label=config.intake.get("root", "company"), deployment_id=config.deployment_id,
        settle_scans=settle_scans)
    return connector, store, config


# --------------------------------------------------------------------------- the mechanism

def test_nothing_in_the_service_imports_a_filesystem_watcher():
    """Read from the imports rather than from the text, because the docstrings talk about inotify at length."""
    imported = {}
    for path in sorted(INTAKE_DIR.glob("*.py")):
        tree = ast.parse(path.read_text())
        names = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names.update(alias.name.split(".")[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                names.add(node.module.split(".")[0])
        found = names & WATCHER_MODULES
        if found:
            imported[path.name] = sorted(found)
    assert imported == {}, f"the connector listens for events instead of polling: {imported}"


def test_the_connector_is_polling_and_says_so_in_its_own_interface():
    """A poll interval is a parameter of the loop, not a constant hidden inside it."""
    import inspect
    signature = inspect.signature(folder.FolderConnector.watch)
    assert "poll_seconds" in signature.parameters
    source = (INTAKE_DIR / "folder.py").read_text()
    assert "inotify(7)" in source, "the reason for polling is recorded where the polling is"
    assert "does not catch remote events" in source


def inotify_descriptors(pid: int) -> int:
    """How many inotify descriptors this process holds, read from the kernel rather than from the code."""
    count = 0
    directory = Path(f"/proc/{pid}/fd")
    try:
        for entry in directory.iterdir():
            try:
                if "inotify" in os.readlink(entry):
                    count += 1
            except (OSError, FileNotFoundError):
                continue
    except (FileNotFoundError, PermissionError):
        return 0
    return count


def test_a_file_written_by_another_process_is_taken_without_a_restart(tmp_path):
    """The requirement's own test, done the way that could actually fail.

    The connector is a separate process. The file arrives from a second process, through a copy that preserves
    mtime, which is what a scanner or an `rsync` does. The connector's descriptors are sampled throughout.
    """
    server = env.FakeGraph()
    config_path = env.environment(tmp_path, server)
    config = config_module.load(config_path)
    share = Path(config.intake["share"])
    share.mkdir(parents=True, exist_ok=True)

    source = tmp_path / "scanner-output.pdf"
    source.write_bytes(env.invoice_pdf("2210.00", "SCAN-1"))

    process = subprocess.Popen(
        [sys.executable, str(INTAKE_DIR / "intake.py"), "watch-folder",
         "--config", str(config_path), "--iterations", "60", "--poll-seconds", "1"],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)

    watcher_hits = 0
    delivered = False
    try:
        # Let the connector take its first scan before anything arrives.
        time.sleep(1.2)

        # A second process puts the file in the share. `copy2` preserves the source mtime, which is the
        # rsync and scanner behaviour, and is why the connector cannot use mtime as a watermark.
        subprocess.run([sys.executable, "-c",
                        "import shutil,sys; shutil.copy2(sys.argv[1], sys.argv[2])",
                        str(source), str(share / source.name)], check=True)

        deadline = time.time() + 25
        while time.time() < deadline:
            watcher_hits += inotify_descriptors(process.pid)
            landed = list(Path(config.documents_root).rglob("*.pdf"))
            if landed:
                delivered = True
                break
            assert process.poll() is None, "the connector exited instead of picking the file up"
            time.sleep(0.2)

        assert delivered, (f"the file was never taken. stdout={process.stdout.read()[:800]!r} "
                           f"stderr={process.stderr.read()[:800]!r}")
        assert process.poll() is None, "the connector restarted rather than polling"
    finally:
        process.terminate()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()

    assert watcher_hits == 0, "the connector held an inotify descriptor while it ran"


def test_the_deposit_from_the_folder_channel_is_a_normal_document(tmp_path):
    """The second channel produces the same row as the first, minus a sender it does not have."""
    connector, store, config = build_connector(tmp_path)
    share = Path(config.intake["share"])
    (share / "scanned-invoice.pdf").write_bytes(env.invoice_pdf("3100.00", "SCAN-2"))

    connector.scan_once()
    summary = connector.scan_once()
    assert summary["taken"], summary

    row = store.entries()[0]
    assert row["channel"] == "folder"
    assert row["mailbox"] is None, "a file that appeared in a share did not arrive at a mailbox"
    assert row["folder"] == "scanned-invoice.pdf"
    assert row["verdict"] == "marked", "a folder arrival has no sender, so it cannot be trusted"
    assert row["requires_review"] is True
    assert (config.documents_root / row["relpath"]).exists()


# --------------------------------------------------------------------------- the settle check

def test_a_file_still_being_written_is_not_taken(tmp_path):
    """Half a scanned invoice must not become a document.

    The second write changes the file, which resets the observation, so the file is taken only after two
    consecutive scans agree. Without this the connector deposits whatever the scanner had written by the time
    it looked.
    """
    connector, store, config = build_connector(tmp_path)
    target = Path(config.intake["share"]) / "still-writing.pdf"

    target.write_bytes(b"%PDF-1.4\n1 0 obj\n")
    first = connector.scan_once()
    assert first["taken"] == [] and first["waiting"], "a file seen once must not be taken"

    target.write_bytes(b"%PDF-1.4\n1 0 obj\n2 0 obj\n3 0 obj\nmore content\n")
    second = connector.scan_once()
    assert second["taken"] == [], "a file that changed since the last scan is not settled"

    third = connector.scan_once()
    assert third["taken"], "the file settled and was still not taken"


def test_a_file_copied_in_with_an_old_timestamp_is_still_taken(tmp_path):
    """The defect a naive polling connector has, and the reason it is here.

    `rsync`, a scanner and a plain copy all preserve or reset mtime. A connector that keeps a watermark of
    "newer than the last scan" therefore misses every file whose timestamp is older than the last poll, which
    on a share is most of them. The watermark here is the content hash, and this is the test that says so.
    """
    connector, store, config = build_connector(tmp_path)
    target = Path(config.intake["share"]) / "from-last-year.pdf"
    target.write_bytes(env.invoice_pdf("440.00", "OLD-1"))

    long_ago = time.time() - 400 * 24 * 3600
    os.utime(target, (long_ago, long_ago))

    connector.scan_once()
    summary = connector.scan_once()
    assert summary["taken"], "a file with an old timestamp was skipped, so mtime is being used as a watermark"
    assert store.entries()[0]["content_sha256"] == provenance.sha256_bytes(target.read_bytes())


def test_the_same_file_twice_is_taken_once_and_left_where_it_is(tmp_path):
    """The connector does not delete from a client's share. Deleting somebody's file is not its business."""
    connector, store, config = build_connector(tmp_path)
    target = Path(config.intake["share"]) / "duplicate.pdf"
    target.write_bytes(env.invoice_pdf("500.00", "DUP-1"))

    connector.scan_once()
    assert connector.scan_once()["taken"]
    for _ in range(3):
        summary = connector.scan_once()
        assert summary["taken"] == []

    assert target.exists(), "the connector removed a file from the client's share"
    assert len(store.entries()) == 1
    assert len(store.notices()) == 1


def test_files_that_are_not_documents_are_ignored(tmp_path):
    """A share is full of debris. Depositing it would fill the corpus with things nobody can search."""
    connector, store, config = build_connector(tmp_path, settle_scans=1)
    share = Path(config.intake["share"])
    (share / "notes.tmp").write_bytes(b"x")
    (share / ".hidden.pdf").write_bytes(b"x")
    (share / "Thumbs.db").write_bytes(b"x")
    (share / "invoice.pdf").write_bytes(env.invoice_pdf())

    summary = connector.scan_once()
    assert len(summary["taken"]) == 1
    assert store.entries()[0]["filename"] == "invoice.pdf"


def test_a_settled_file_is_taken_when_the_connector_starts_after_it_arrived(tmp_path):
    """The overnight drop: the file is already there when the connector starts, so no event ever happened."""
    share = None
    connector, store, config = build_connector(tmp_path, settle_scans=1)
    share = Path(config.intake["share"])
    (share / "before-start.pdf").write_bytes(env.invoice_pdf("600.00", "NIGHT-1"))

    summary = connector.scan_once()
    assert summary["taken"], "a file that arrived before the connector started was never taken"


# --------------------------------------------------------------------------- the record

def test_a_folder_arrival_carries_its_own_provenance(tmp_path):
    """Path, our observation time and a content hash. That is what a file gives an auditor, and it is kept."""
    connector, store, config = build_connector(tmp_path, settle_scans=1)
    target = Path(config.intake["share"]) / "invoice-9.pdf"
    target.write_bytes(env.invoice_pdf("700.00", "F-9"))

    connector.scan_once()
    row = store.entries()[0]
    assert len(row["content_sha256"]) == 64
    assert row["observed_at"], "the observation time is the fact we control"
    assert row["received_at"], "and the file's own timestamp is recorded beside it, as a claim rather than a fact"
    assert row["raw_message_sha256"] == row["content_sha256"]


def test_the_notice_names_no_action_for_the_workflow_to_have_invented(tmp_path):
    connector, store, config = build_connector(tmp_path, settle_scans=1)
    (Path(config.intake["share"]) / "invoice-10.pdf").write_bytes(env.invoice_pdf("800.00", "F-10"))
    connector.scan_once()
    notice = store.notices()[0]
    assert notice["requested_actions"] == []
    assert notice["requires_review"] is True
