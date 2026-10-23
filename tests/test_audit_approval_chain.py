"""AUD-APPROVAL-001: the approval column is inside the chain, and the verifier notices an edit.

The chain has to cover a field a reader would treat as a fact, and `audit_events.approval` did not until chain
version 3. These tests run against a real Postgres because the trigger, the canonical function and the version
guard are database functions; a Python-only test would test a copy of the formula rather than the one that writes
the hash.

**Why a digest instead of the JSON itself.** The writer is a Postgres trigger and the verifier is Python.
Postgres renders jsonb with a space after every colon and comma and does not keep the key order a caller wrote;
`canonical_json` sorts keys and separates without spaces. Reproducing either in the other language is a bug
waiting for a dependency upgrade, so the chain covers `sha256(canonical_json(approval))`: a form both can
produce exactly. The writer computes it, the trigger refuses a row that arrived without one, and verification
compares the column to the stored JSON the same way it compares `payload` to `payload_sha256`.

**The file has two layers.** The unit tests below the helpers need no database and run anywhere. The database
tests skip with the command to start one. The last test enumerates the table's columns from
`information_schema`, so a column added later fails a test instead of becoming the next instance of this defect.

    OI_TEST_DATABASE_URL=postgresql://user:pass@host:5432/db python3 -m pytest tests/test_audit_approval_chain.py -q
"""
from __future__ import annotations

import contextlib
import io
import json
import os
import re
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "services" / "audit"))

import audit  # noqa: E402

TEST_URL = (os.environ.get("OI_TEST_DATABASE_URL") or "").strip()
SKIP_WHY = (
    "no test database. The approval digest is covered by a database trigger and a database canonical function, "
    "so this file needs a real Postgres. Start one and name it: "
    "docker run -d -p 55432:5432 -e POSTGRES_PASSWORD=wftest -e POSTGRES_USER=wftest -e POSTGRES_DB=wftest "
    "postgres:16-alpine   then   OI_TEST_DATABASE_URL=postgresql://wftest:wftest@127.0.0.1:55432/wftest "
    "python3 -m pytest tests/test_audit_approval_chain.py -q")

needs_database = pytest.mark.skipif(not TEST_URL, reason=SKIP_WHY)
DEPLOYMENT = "audit-approval-chain-test"


def with_database(url: str, name: str) -> str:
    """The same server and credentials, a different database."""
    parts = urlsplit(url)
    return urlunsplit(parts._replace(path="/" + name))


@pytest.fixture(scope="module", autouse=True)
def environment():
    """A scratch database for this module, migrated here, dropped afterwards.

    **Why its own database.** A chain is cumulative and shared. Another suite's rows sit inside every export
    this file verifies, and while this file is being written a second process was inserting into the named
    database between the head read and the row insert, which breaks a link for a reason that has nothing to do
    with the code under test. A database that starts empty is the only way to assert that the chain verifies.

    If the server refuses `CREATE DATABASE`, the named database is used as it is and a warning says so.

    **Guarded on TEST_URL because this fixture is autouse.** Without the guard it would clear the deployment's
    `DATABASE_URL` in the environment of every later test in the session, a side effect no test asked for that
    would only surface somewhere else.
    """
    if not TEST_URL:
        yield
        return

    original = (audit.DSN, audit.MIGRATIONS_DIR)
    original_url = os.environ.get("DATABASE_URL")
    name = "oi_audit_chain_" + uuid.uuid4().hex[:10]
    audit.DSN = TEST_URL
    created = False
    with audit.connect() as admin, admin.cursor() as cur:
        try:
            cur.execute(f'create database "{name}"')
            created = True
        except Exception as exc:  # noqa: BLE001 - a refusal is a fallback, not a failure
            print(f"warning: could not create a scratch database ({exc}); using the named database as it is",
                  file=sys.stderr)

    audit.DSN = with_database(TEST_URL, name) if created else TEST_URL
    audit.MIGRATIONS_DIR = ROOT / "db" / "migrations"
    try:
        assert audit.cmd_migrate(None) == 0
        os.environ["DATABASE_URL"] = audit.DSN
        os.environ.setdefault("CONTENT_DEFAULT", "full")
        os.environ.setdefault("CONTENT_MODE_INTERNAL", "full")
        yield
    finally:
        audit.DSN, audit.MIGRATIONS_DIR = original
        if created:
            audit.DSN = TEST_URL
            try:
                with audit.connect() as admin, admin.cursor() as cur:
                    cur.execute(f'drop database if exists "{name}"')
            except Exception as exc:  # noqa: BLE001 - report the leftover, do not fail the suite for it
                print(f"warning: the scratch database {name} could not be dropped: {exc}", file=sys.stderr)
        if original_url is None:
            os.environ.pop("DATABASE_URL", None)
        else:
            os.environ["DATABASE_URL"] = original_url


# --------------------------------------------------------------------------- the canonical form, no database


def entry(**overrides) -> dict:
    """A row as an export writes it, at version 3, carrying an approval and its digest."""
    value = {
        "id": 1,
        "ts_iso": "2026-10-22T12:00:00.000000Z", "event_type": "approval", "actor": "dana",
        "actor_role": "staff", "deployment_id": "test", "data_class": "internal", "outcome": "ok",
        "tool": None, "model": None, "request_id": None,
        "payload_sha256": "a" * 64, "payload_bytes": 12, "prev_hash": "b" * 64,
        "chain_version": 3, "redaction_count": 0, "redactions": None,
        "unverified_card_shapes": 0, "egress_allowed": None,
        "approval": {"decision": "approve"}, "approval_sha256": None,
    }
    value["approval_sha256"] = audit.approval_digest(value["approval"])
    value.update(overrides)
    return value


def sealed(value: dict) -> dict:
    value["row_hash"] = audit.sha256_hex(audit.row_canonical(value))
    return value


def test_version_three_appends_the_approval_digest():
    value = entry()
    canonical = audit.row_canonical(value)
    assert canonical.endswith("|" + value["approval_sha256"]), canonical
    # Sixty-four hex characters and nothing else. The JSON in this position would be unverifiable across two
    # serialisers, which is the reason the digest is here instead.
    assert re.fullmatch(r"[0-9a-f]{64}", canonical.rsplit("|", 1)[1])


@pytest.mark.parametrize("version", [1, 2])
def test_versions_one_and_two_ignore_the_digest_entirely(version):
    """**The migration's first promise.** If the new field were appended unconditionally, every row written
    before 025 would stop verifying, and a chain full of false breaks is a chain nobody reads."""
    without = entry(chain_version=version, approval=None, approval_sha256=None)
    with_digest = entry(chain_version=version)
    assert audit.row_canonical(without) == audit.row_canonical(with_digest)


def test_a_version_one_row_is_still_the_thirteen_original_fields():
    value = entry(chain_version=1)
    expected = "|".join([
        value["ts_iso"], "approval", "dana", "staff", "test", "internal", "ok", "", "", "",
        "a" * 64, "12", "b" * 64,
    ])
    assert audit.row_canonical(value) == expected


def test_the_digest_is_over_the_canonical_form_not_the_json_text():
    """Postgres writes `{"a": 1, "b": 2}`; this writes `{"a":1,"b":2}`. The digest has to be over the form
    both can produce, or the two sides disagree the first time a space or a key order changes."""
    assert audit.approval_digest({"b": 2, "a": 1}) == audit.sha256_hex('{"a":1,"b":2}')
    assert audit.approval_digest({"a": 1, "b": 2}) == audit.approval_digest({"b": 2, "a": 1})
    assert audit.approval_digest(None) is None


def test_editing_the_digest_changes_the_canonical_string():
    assert audit.row_canonical(entry()) != audit.row_canonical(entry(approval_sha256="0" * 64))


class Recorder:
    """A cursor that remembers what it was asked to write."""

    def __init__(self) -> None:
        self.statements: list = []

    def execute(self, query, params=None) -> None:
        self.statements.append((query, params))

    def fetchone(self):
        return (7, "2026-10-22T12:00:00.000000Z", "row-hash")


def insert_parameters(cursor: Recorder) -> dict:
    """The insert's parameters by column name.

    Read from the SQL rather than written out, so a column added to the writer and not to this test shows up as
    a length mismatch instead of shifting every value one place to the left.
    """
    query, params = cursor.statements[0]
    match = re.search(r"insert into audit_events\s*\(([^)]+)\)\s*values", query)
    assert match, "could not find the audit insert statement"
    columns = [column.strip() for column in match.group(1).replace("\n", " ").split(",") if column.strip()]
    assert len(columns) == len(params), (
        f"the insert takes {len(columns)} parameters and got {len(params)}")
    return dict(zip(columns, params))


def write(approval=None) -> dict:
    cursor = Recorder()
    written = audit.insert_event(
        cursor, event_type="approval", actor="dana", actor_role="staff", deployment_id=DEPLOYMENT,
        data_class="internal", outcome="ok", stored_payload={"stage": "approval"},
        payload_sha256="a" * 64, approval=approval)
    return {"written": written, "parameters": insert_parameters(cursor)}


def test_the_writer_computes_the_digest_so_a_caller_never_has_to():
    """`cmd_record` hands over the parsed JSON and nothing else. If the caller had to compute the digest, the
    first caller that forgot would write a row the trigger refuses, or worse, one it accepted with a digest
    that does not match the JSON."""
    approval = {"decision": "approve", "reviewer_note": "checked the fee schedule"}
    result = write(approval)
    # psycopg wraps a jsonb parameter and the stub used on a machine without the driver is the identity
    # function, so read the value either way. The same unwrap the other audit tests use.
    assert getattr(result["parameters"]["approval"], "obj", result["parameters"]["approval"]) == approval
    assert result["parameters"]["approval_sha256"] == audit.sha256_hex(
        audit.canonical_json(approval))
    assert result["written"]["approval_sha256"] == result["parameters"]["approval_sha256"]


def test_the_writer_writes_no_digest_for_an_event_with_no_approval():
    result = write(None)
    assert result["parameters"]["approval"] is None
    assert result["parameters"]["approval_sha256"] is None


# --------------------------------------------------------------------------- verification of an export


def write_export_file(directory: Path, rows: list[dict]) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    data = directory / "audit-events.jsonl"
    data.write_text("".join(audit.canonical_json(value) + "\n" for value in rows))
    manifest = {
        "date": "2026-10-22",
        "event_count": len(rows),
        "first_id": rows[0].get("id") if rows else None,
        "first_prev_hash": rows[0].get("prev_hash") if rows else None,
        "last_id": rows[-1].get("id") if rows else None,
        "last_row_hash": rows[-1].get("row_hash") if rows else None,
        "file": data.name,
        "file_sha256": audit.sha256_hex(data.read_text()),
    }
    (directory / "manifest.json").write_text(json.dumps(manifest))
    return data


def verify_export(path: Path) -> tuple[int, str]:
    buffer = io.StringIO()
    with contextlib.redirect_stdout(buffer):
        code = audit.cmd_verify(type("Args", (), {"file": str(path), "anchor": None})())
    return code, buffer.getvalue()


def test_a_clean_export_with_an_approval_verifies(tmp_path):
    code, output = verify_export(write_export_file(tmp_path, [sealed(entry())]))
    assert code == 0, output


def test_an_edited_approval_fails_verification_without_touching_the_row_hash(tmp_path):
    """**AUD-APPROVAL-001, as the failing case.** The row hash still matches, because the canonical string
    covers the digest and not the JSON. The digest is what catches this, and before version 3 nothing did."""
    tampered = dict(sealed(entry()))
    tampered["approval"] = {"decision": "deny"}
    assert audit.sha256_hex(audit.row_canonical(tampered)) == tampered["row_hash"]
    code, output = verify_export(write_export_file(tmp_path, [tampered]))
    assert code == 1, "an edited approval must not verify"
    assert "the stored approval does not match approval_sha256" in output


def test_an_edited_digest_breaks_the_row_hash(tmp_path):
    tampered = dict(sealed(entry()))
    tampered["approval_sha256"] = "0" * 64
    code, output = verify_export(write_export_file(tmp_path, [tampered]))
    assert code == 1
    assert "row_hash does not match the row contents" in output


def test_an_approval_with_no_digest_is_reported_rather_than_passed(tmp_path):
    """A row that carries an approval and no digest was never covered. None can be computed without trusting
    the very column that was editable, so it is reported as uncovered, which is what it is."""
    value = entry(approval_sha256=None)
    value["row_hash"] = audit.sha256_hex(audit.row_canonical(value))
    code, output = verify_export(write_export_file(tmp_path, [value]))
    assert code == 1
    assert "never covered by the chain" in output


def test_the_migration_defines_the_formula_the_verifier_uses():
    """The database function and the Python function are two copies of one formula. These are the parts that
    are easy to get subtly different: the version branch, the null handling, and the field order."""
    migration = (ROOT / "db" / "migrations" / "025_the_approval_is_in_the_chain.sql").read_text()
    assert "p_chain_version < 3" in migration, "the migration has no version 3 branch"
    assert "version_two || '|' || coalesce(p_approval_sha256, '')" in migration, \
        "the migration does not append the digest, or appends a bare null that concat_ws would drop"
    assert "new.chain_version := 3" in migration
    assert "new.approval is not null and new.approval_sha256 is null" in migration, \
        "the trigger would accept an approval with no digest, which is the defect this closes"
    for field in ("p_ts_iso", "p_event_type", "p_actor", "p_actor_role", "p_deployment_id",
                  "p_data_class", "p_outcome", "p_tool", "p_model", "p_request_id",
                  "p_payload_sha256", "p_payload_bytes", "p_prev_hash", "p_redaction_count",
                  "p_redactions", "p_unverified_card_shapes", "p_egress_allowed",
                  "p_approval_sha256"):
        assert field in migration, f"{field} is missing from the database canonical form"
    assert "'reattestation'" in migration, "025 also adds the event type a re-attestation uses"


# --------------------------------------------------------------------------- against a real database


def insert_approval(approval: dict) -> dict:
    payload = {"stage": "approval", "approval": approval}
    with audit.connect() as conn, conn.cursor() as cur:
        written = audit.insert_event(
            cur, event_type="approval", actor="dana", actor_role="staff",
            deployment_id=DEPLOYMENT, data_class="internal", outcome="ok",
            stored_payload=payload,
            payload_sha256=audit.sha256_hex(audit.canonical_json(payload)),
            approval=approval,
        )
    return written


def row(row_id: int):
    with audit.connect() as conn, conn.cursor() as cur:
        cur.execute("""select event_type, chain_version, approval, approval_sha256, row_hash, prev_hash
                         from audit_events where id = %s""", (row_id,))
        return cur.fetchone()


def export_report(tmp_path: Path) -> tuple[int, str]:
    day = datetime.now(timezone.utc).date().isoformat()
    buffer = io.StringIO()
    with contextlib.redirect_stdout(buffer):
        audit.cmd_export(type("Args", (), {"date": day, "out": str(tmp_path / "export")})())
        code = audit.cmd_verify(type("Args", (), {
            "file": str(tmp_path / "export" / "audit-events.jsonl"), "anchor": None})())
    return code, buffer.getvalue()


def export_and_verify(tmp_path: Path) -> int:
    return export_report(tmp_path)[0]


def legacy_row(version: int, event_type: str, actor: str, ts_iso: str, payload: dict) -> int:
    """A row whose stored hash is the one its chain version's formula produces.

    **The trigger is disabled for this insert, deliberately.** After 025 it always writes version 3, and what
    this test needs is a row carrying the hash a version 1 or 2 writer produced, which is what every row
    written before 025 carries. The hash comes from the DATABASE function's version branch, so the comparison
    is against the database's own old formula rather than a string this file made up.
    """
    text = audit.canonical_json(payload)
    with audit.connect() as conn, conn.cursor() as cur:
        cur.execute("select row_hash from audit_events order by id desc limit 1")
        head = cur.fetchone()
        previous = head[0] if head else None
        cur.execute("""
            select audit_row_canonical(%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                                       0, '{}'::jsonb, 0, null::boolean, null::text)""",
                    (version, ts_iso, event_type, actor, "system", DEPLOYMENT, "internal", "ok",
                     "", "", "", audit.sha256_hex(text), len(text), previous))
        row_hash = audit.sha256_hex(cur.fetchone()[0])
        cur.execute("alter table audit_events disable trigger audit_events_chain")
        try:
            cur.execute("""
                insert into audit_events
                  (ts_iso, event_type, actor, actor_role, deployment_id, data_class, outcome,
                   payload_sha256, payload_bytes, payload, prev_hash, chain_version, row_hash)
                values (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb,%s,%s,%s)
                returning id""",
                        (ts_iso, event_type, actor, "system", DEPLOYMENT, "internal", "ok",
                         audit.sha256_hex(text), len(text), text, previous, version, row_hash))
            return cur.fetchone()[0]
        finally:
            cur.execute("alter table audit_events enable trigger audit_events_chain")


@needs_database
def test_an_approval_written_through_the_writer_carries_a_digest_and_chain_version_3():
    written = insert_approval({"decision": "approve", "reason": "", "action_digest": "sha256:abc"})
    event_type, version, approval, digest, row_hash, _ = row(written["id"])
    assert event_type == "approval"
    assert version == 3
    assert approval is not None
    assert digest == audit.sha256_hex(audit.canonical_json(approval))
    assert digest == written["approval_sha256"]
    assert row_hash == written["row_hash"]


@needs_database
def test_the_trigger_refuses_an_approval_with_no_digest():
    with audit.connect() as conn, conn.cursor() as cur:
        with pytest.raises(Exception) as raised:
            cur.execute("""
                insert into audit_events
                  (event_type, actor, actor_role, deployment_id, data_class, outcome,
                   approval, payload_sha256, approval_sha256)
                values ('approval', 'dana', 'staff', %s, 'internal', 'ok',
                        %s, %s, null)""",
                        (DEPLOYMENT, json.dumps({"decision": "approve"}),
                         audit.sha256_hex(audit.canonical_json({}))))
    assert "approval_sha256" in str(raised.value)


@needs_database
def test_editing_the_approval_fails_verification(tmp_path):
    written = insert_approval({"decision": "approve", "reason": "", "action_digest": "sha256:abc"})
    original = row(written["id"])[2]
    try:
        with audit.connect() as conn, conn.cursor() as cur:
            cur.execute("set oi.allow_audit_maintenance = on")
            cur.execute("update audit_events set approval = %s where id = %s",
                        (json.dumps({"decision": "deny", "reason": "replaced"}), written["id"]))
            cur.execute("set oi.allow_audit_maintenance = off")
            cur.execute("select row_hash from audit_events where id = %s", (written["id"],))
            assert cur.fetchone()[0] == written["row_hash"], "the row hash covers only the digest"
        code, output = export_report(tmp_path)
        assert code != 0, "an edited approval verified"
        assert "the stored approval does not match approval_sha256" in output
    finally:
        with audit.connect() as conn, conn.cursor() as cur:
            cur.execute("set oi.allow_audit_maintenance = on")
            cur.execute("update audit_events set approval = %s where id = %s",
                        (json.dumps(original), written["id"]))
            cur.execute("set oi.allow_audit_maintenance = off")


@needs_database
def test_editing_the_digest_breaks_the_chain(tmp_path):
    """The other direction. The digest is inside the canonical string, so the row hash stops matching, and the
    next row's prev_hash stops matching with it. Restoring the value restores the record, which proves the edit
    was the only difference."""
    written = insert_approval({"decision": "approve", "action_digest": "sha256:def"})
    original = written["approval_sha256"]
    try:
        with audit.connect() as conn, conn.cursor() as cur:
            cur.execute("set oi.allow_audit_maintenance = on")
            cur.execute("update audit_events set approval_sha256 = %s where id = %s",
                        ("0" * 64, written["id"]))
            cur.execute("set oi.allow_audit_maintenance = off")
        code, output = export_report(tmp_path)
        assert code != 0, "an edited digest verified"
        assert "row_hash does not match the row contents" in output
    finally:
        with audit.connect() as conn, conn.cursor() as cur:
            cur.execute("set oi.allow_audit_maintenance = on")
            cur.execute("update audit_events set approval_sha256 = %s where id = %s",
                        (original, written["id"]))
            cur.execute("set oi.allow_audit_maintenance = off")
    assert export_and_verify(tmp_path) == 0


@needs_database
def test_rows_written_under_versions_one_and_two_still_verify(tmp_path):
    """**The reason the formula is versioned.** A row's stored hash was computed from a canonical string, and
    verification recomputes it. Changing the formula and not the version would report untouched history as
    tampering, and the first real break would be lost in the noise."""
    first = legacy_row(1, "access", "legacy-one", "2026-10-01T00:00:01.000000Z", {"legacy": 1})
    second = legacy_row(2, "access", "legacy-two", "2026-10-01T00:00:02.000000Z", {"legacy": 2})

    code, output = export_report(tmp_path)
    assert code == 0, output
    exported = {value["id"]: value for value in (
        json.loads(line) for line in
        (tmp_path / "export" / "audit-events.jsonl").read_text().splitlines() if line.strip())}
    assert exported[first]["chain_version"] == 1
    assert exported[second]["chain_version"] == 2


@needs_database
def test_the_database_and_the_verifier_agree_on_every_version(tmp_path):
    """The two copies of the formula, compared field for field on a real row of each version.

    A drift between them reports every untouched row as tampering, which is why section 5.5 of the design says
    they must change in the same commit.
    """
    identifiers = {
        1: legacy_row(1, "access", "agree-one", "2026-10-01T00:00:03.000000Z", {"agrees": 1}),
        2: legacy_row(2, "access", "agree-two", "2026-10-01T00:00:04.000000Z", {"agrees": 2}),
        3: insert_approval({"decision": "approve", "action_digest": "sha256:ghi"})["id"],
    }
    assert export_and_verify(tmp_path) == 0
    exported = {value["id"]: value for value in (
        json.loads(line) for line in
        (tmp_path / "export" / "audit-events.jsonl").read_text().splitlines() if line.strip())}

    for version, identifier in identifiers.items():
        value = exported[identifier]
        assert value["chain_version"] == version
        with audit.connect() as conn, conn.cursor() as cur:
            cur.execute("""
                select audit_row_canonical(%s::smallint, %s::text, %s::text, %s::text, %s::text,
                                           %s::text, %s::text, %s::text, %s::text, %s::text,
                                           %s::text, %s::text, %s::integer, %s::text,
                                           %s::integer, %s::jsonb, %s::integer, %s::boolean,
                                           %s::text)""",
                        (value["chain_version"], value["ts_iso"], value["event_type"], value["actor"],
                         value["actor_role"], value["deployment_id"], value["data_class"],
                         value["outcome"], value["tool"], value["model"], value["request_id"],
                         value["payload_sha256"], value["payload_bytes"], value["prev_hash"],
                         value["redaction_count"],
                         audit.canonical_json(value["redactions"])
                         if value["redactions"] is not None else None,
                         value["unverified_card_shapes"], value["egress_allowed"],
                         value["approval_sha256"]))
            database = cur.fetchone()[0]
        assert database == audit.row_canonical(value), (
            f"the database and the verifier disagree about a version {version} row")


# --------------------------------------------------------------------------- the schema, enumerated
#
# **Every column is classified, and the classification is checked against the table.** A test that lists the
# columns it knows about passes forever while a new one sits outside the chain, which is how the approval column
# stayed out for two phases. The database test below asserts that this list and the table are the same set.

CANONICAL_V3 = {
    "ts_iso", "event_type", "actor", "actor_role", "deployment_id", "data_class", "outcome",
    "tool", "model", "request_id", "payload_sha256", "payload_bytes", "prev_hash",
    "redaction_count", "redactions", "unverified_card_shapes", "egress_allowed",
    "approval_sha256",
}

#: Content columns, and the digest covering each one. Content is hashed rather than cast into the canonical
#: string for the serialiser reason this module opens with.
COVERED_BY = {
    "payload": "payload_sha256",
    "approval": "approval_sha256",
}

#: Digests named by AUD-APPROVAL-001 as coverage. The stored prompt and completion live inside the payload,
#: which payload_sha256 covers; these are the narrower hashes recorded beside it.
CONTENT_DIGESTS = {"prompt_sha256", "completion_sha256"}

#: Columns a reader does not treat as a fact about the event, each with its reason. An entry without a reason is
#: the next AUD-APPROVAL-001, so the reason is part of the entry.
BOOKKEEPING = {
    "id": "the chain's own sequence. Order is enforced by prev_hash, not by the id.",
    "row_hash": "the hash itself, the output of the formula rather than an input to it.",
    "chain_version": "which formula produced row_hash; a row claiming a version it was not written with is "
                     "refused by audit_events_version_guard.",
    "occurred_at": "the same instant as ts_iso, which is in the canonical string. Known residual: editing "
                   "occurred_at alone is not detected, because nothing compares the two. Written down rather "
                   "than assumed away, and outside AUD-APPROVAL-001.",
}


def test_the_approval_is_covered_by_a_digest_the_canonical_string_carries():
    assert COVERED_BY["approval"] == "approval_sha256"
    assert "approval_sha256" in CANONICAL_V3
    for column, cover in COVERED_BY.items():
        assert cover in CANONICAL_V3 or cover in CONTENT_DIGESTS, (
            f"{column} is said to be covered by {cover}, which is not covered by anything")


@needs_database
def test_a_column_that_carries_meaning_is_covered_by_the_chain_or_a_content_hash():
    """The guard against the next AUD-APPROVAL-001: a new meaningful column outside the chain fails here.

    Enumerated from `information_schema`, not from this file, and the canonical function's own argument list is
    compared with the verifier's field list in the same test, so a drift between the two copies of the formula
    is caught by the thing that already exists rather than by a future incident.
    """
    with audit.connect() as conn, conn.cursor() as cur:
        cur.execute("""select column_name from information_schema.columns
                         where table_name = 'audit_events' order by ordinal_position""")
        columns = [name for (name,) in cur.fetchall()]
        cur.execute("""
            select count(*), min(pg_get_function_arguments(p.oid))
            from pg_proc p join pg_namespace n on n.oid = p.pronamespace
            where p.proname = 'audit_row_canonical' and n.nspname = current_schema()""")
        function_count, arguments = cur.fetchone()

    classified = CANONICAL_V3 | set(COVERED_BY) | CONTENT_DIGESTS | set(BOOKKEEPING)

    unclassified = set(columns) - classified
    assert not unclassified, (
        "audit_events has a column the coverage list does not name. Decide whether it carries meaning, and if "
        "it does, put it inside the chain or hash it into a covered digest before adding it to this set. A "
        f"column the chain does not cover can be edited while every check passes: {sorted(unclassified)}")
    stale = classified - set(columns)
    assert not stale, (
        f"the coverage list names column(s) the table does not have: {sorted(stale)}")

    assert function_count == 1, (
        "audit_row_canonical exists more than once, which means an older argument list was left behind by "
        "create or replace and a caller could silently get the formula that cannot see an approval")
    argument_names = set()
    for part in arguments.split(","):
        name = part.split()[0]
        if name.startswith("p_") and name != "p_chain_version":
            argument_names.add(name[2:])
    assert argument_names == CANONICAL_V3, (
        "the database canonical function's arguments and the verifier's field list have drifted apart: "
        f"{sorted(argument_names ^ CANONICAL_V3)}")
