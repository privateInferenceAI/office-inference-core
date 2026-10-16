"""The tests for what intake records, and for the second copy that must not run twice.

`CON-INTAKE-001` and `CON-DUPE-001`, and both are about a row rather than about a behaviour.

**The hash equality is tested against the ingestion worker's own function**, not against a second
implementation written here. `ingest_manifest.content_sha256` is produced by `ingest.sha256_of`, so the test
imports that function and hashes the deposited file with it. A test that reimplemented the hash would prove
that two copies of one algorithm agree, which is the failure `services/ingestion/formats.py` was written to
stop repeating.

**The duplicate test is the requirement's own wording**: the same invoice twice, once as a re-send and once
as a forward. Two messages, two `Message-ID`s, byte-identical attachments.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tests" / "fixtures"))
sys.path.insert(0, str(ROOT / "services" / "intake"))

import intake_env  # noqa: E402

env = intake_env
intake = intake_env.load("intake")
provenance = intake_env.load("provenance")
net = intake_env.load("net")


def ingestion_hasher():
    """The ingestion worker's own hash function, for the equality that `CON-INTAKE-001` asks for.

    `ingest.py` imports its neighbours by module name and pulls psycopg2 lazily, so it loads on a machine with
    no database. If it ever stops loading, this skips rather than substituting a different hash: a
    substitute would make the test prove nothing while still passing.
    """
    sys.path.insert(0, str(ROOT / "services" / "ingestion"))
    try:
        import ingest  # noqa: PLC0415
        return ingest.sha256_of
    except ImportError as exc:
        pytest.skip(f"the ingestion worker does not import here ({type(exc).__name__}: {exc})")


@pytest.fixture
def tenant(tmp_path, monkeypatch):
    server = env.FakeGraph(messages=env.sample_messages(include_duplicate=True)).start()
    env.install(server)
    config_path = env.environment(tmp_path, server)
    env.write_credential(config_path)
    yield server, config_path
    server.stop()
    net.set_transport(None)


def run_fetch(config_path):
    config = intake_env.load("config").load(config_path)
    assert not config.problems, config.problems
    store = provenance.Store(config.state_dir)
    summary = intake.poll_once(config, store)
    return config, store, summary


# --------------------------------------------------------------------------- CON-INTAKE-001

def test_a_row_names_the_sender_the_message_the_time_and_the_hash(tenant):
    """The requirement, field for field: sender, message identifier, arrival time, sha256 of the content."""
    _, config_path = tenant
    config, store, _ = run_fetch(config_path)
    rows = store.entries()
    assert rows, "no record was written at all"

    row = next(r for r in rows if r["filename"] == "INV-2026-0041.pdf")
    assert row["sender_from"] == "billing@supplier.example"
    assert row["message_id"] == "<inv-41@supplier.example>"
    assert row["received_at"], "the arrival time is missing"
    assert len(row["content_sha256"]) == 64
    assert row["bytes"] > 0
    assert row["relpath"].startswith("company/intake/")

    # And the file the row names is really there, with really those bytes.
    landed = config.documents_root / row["relpath"]
    assert landed.exists()
    assert provenance.sha256_file(landed)[0] == row["content_sha256"]


def test_the_hash_is_the_one_the_ingestion_worker_will_record(tenant):
    """`CON-INTAKE-001`: "the same hash appears on the ingest manifest row".

    The manifest's hash is computed by `ingest.sha256_of`, so that is the function used here. There is no
    database in this test, so what is proved is the equality of the number rather than the presence of the
    row: the worker hashes the file at `relpath` with this function, and this is that number.
    """
    hasher = ingestion_hasher()
    _, config_path = tenant
    config, store, _ = run_fetch(config_path)

    checked = 0
    for row in store.entries():
        if row["status"] != "deposited":
            continue
        landed = config.documents_root / row["relpath"]
        digest, size = hasher(landed)
        assert digest == row["content_sha256"], f"{row['relpath']} hashes differently"
        assert size == row["bytes"]
        checked += 1
    assert checked >= 4, "the fixture should have deposited several documents"


def test_the_deposit_lands_under_the_workers_root_so_the_access_label_is_derived(tenant):
    """The first path component is the access label. A deposit without one is a document nobody can find."""
    _, config_path = tenant
    config, store, _ = run_fetch(config_path)
    for row in store.entries():
        if row["status"] != "deposited":
            continue
        assert row["relpath"].split("/")[0] == config.intake.get("root")


def test_no_partial_file_is_left_for_the_worker_to_read(tenant):
    """`.part` is in the worker's ignored suffixes, and the deposit uses it and renames."""
    _, config_path = tenant
    config, _, _ = run_fetch(config_path)
    leftovers = list(config.documents_root.rglob("*.part")) + list(config.documents_root.rglob(".*.part"))
    assert leftovers == [], f"a temporary file was left behind: {leftovers}"


# --------------------------------------------------------------------------- CON-DUPE-001

def test_the_same_invoice_twice_creates_one_deposit_and_one_duplicate(tenant):
    """A re-send and a forward of byte-identical content.

    Three assertions, and the requirement names all three: the second is recognised by content hash, no
    second job is created, and the duplicate is recorded rather than discarded.
    """
    _, config_path = tenant
    config, store, summary = run_fetch(config_path)

    same_content = [row for row in store.entries()
                    if row["content_sha256"] == provenance.sha256_bytes(env.invoice_pdf())]
    assert len(same_content) == 2, "the forward of the same invoice produced no row"

    first, second = sorted(same_content, key=lambda row: row["received_at"])
    assert first["status"] == "deposited"
    assert second["status"] == "duplicate"
    assert second["duplicate_of"] == first["intake_id"], "the duplicate does not point at the original"
    assert second["message_id"] != first["message_id"], "the two arrivals must be two different messages"

    # No second job: the seam is the outbox, and a duplicate writes no notice.
    notices = store.notices()
    assert len(notices) == len([r for r in store.entries() if r["status"] == "deposited"])
    assert not any(notice["intake_id"] == second["intake_id"] for notice in notices)

    # And no second copy of the bytes on disk.
    landed = list(config.documents_root.rglob("INV-2026-0041.pdf"))
    assert len(landed) == 1, f"the same invoice was deposited twice: {landed}"
    assert summary["duplicates"] == 1


def test_reading_the_same_message_again_is_idempotent(tenant):
    """A crash between the read and the record re-reads the message. It must not write a second row."""
    _, config_path = tenant
    config, store, _ = run_fetch(config_path)
    before = len(store.entries())
    _, store2, summary = run_fetch(config_path)
    assert len(store2.entries()) == before
    assert summary["deposited"] == 0 and summary["duplicates"] == 0
    assert summary["already"] >= 4


def test_a_second_identical_invoice_dated_differently_is_still_a_duplicate(tmp_path, monkeypatch):
    """The hash decides, not the message id and not the date. Same bytes, different everything else."""
    from datetime import datetime, timezone
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    first = env.Message(id="A", internet_message_id="<a@supplier.example>",
                        from_address="billing@supplier.example", subject="Invoice",
                        received=stamp, return_path="bounce@supplier.example",
                        attachments=[env.Attachment("one.pdf", env.invoice_pdf())])
    second = env.Message(id="B", internet_message_id="<b@mailing-house.example>",
                         from_address="billing@mailing-house.example", subject="Invoice (2)",
                         received=stamp, return_path="bounce@mailing-house.example",
                         attachments=[env.Attachment("two.pdf", env.invoice_pdf())])
    server = env.FakeGraph(messages=[first, second]).start()
    try:
        env.install(server)
        config_path = env.environment(tmp_path, server)
        env.write_credential(config_path)
        _, store, summary = run_fetch(config_path)
        rows = [row for row in store.entries()
                if row["content_sha256"] == provenance.sha256_bytes(env.invoice_pdf())]
        assert len(rows) == 2
        assert sorted(row["status"] for row in rows) == ["deposited", "duplicate"]
        assert summary["duplicates"] == 1
    finally:
        server.stop()
        net.set_transport(None)


def test_a_different_invoice_is_not_a_duplicate(tenant):
    """The negative control. If everything were a duplicate, dedupe would be a silent drop."""
    _, config_path = tenant
    _, store, _ = run_fetch(config_path)
    hashes = {row["content_sha256"] for row in store.entries() if row["status"] == "deposited"}
    assert len(hashes) >= 4, "distinct invoices must keep distinct hashes"
