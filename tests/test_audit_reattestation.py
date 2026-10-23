"""APR-UPGRADE-001: append a re-attestation, never rewrite the approval it points at.

The original approval's assurance cannot be raised by a later statement. What can be recorded is that a
particular account held the session, under a new event that links the original by id and row hash, states what
the original said, and states what the new statement does not establish. These tests run against Postgres because
the append-only trigger and the chain are database facts.

    OI_TEST_DATABASE_URL=postgresql://user:pass@host:5432/db python3 -m pytest tests/test_audit_reattestation.py -q
"""
from __future__ import annotations

import argparse
import inspect
import io
import json
import os
import re
import sys
import uuid
from contextlib import redirect_stdout
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

import pytest

ROOT = Path(__file__).resolve().parents[1]
for extra in (ROOT / "services" / "audit", ROOT / "services" / "api"):
    if str(extra) not in sys.path:
        sys.path.insert(0, str(extra))

import approvals  # noqa: E402
import audit  # noqa: E402

TEST_URL = (os.environ.get("OI_TEST_DATABASE_URL") or "").strip()
SKIP_WHY = (
    "no test database. A re-attestation is an append to the chain and the append-only trigger is a database "
    "fact. Start one and name it: "
    "docker run -d -p 55432:5432 -e POSTGRES_PASSWORD=wftest -e POSTGRES_USER=wftest -e POSTGRES_DB=wftest "
    "postgres:16-alpine   then   OI_TEST_DATABASE_URL=postgresql://wftest:wftest@127.0.0.1:55432/wftest "
    "python3 -m pytest tests/test_audit_reattestation.py -q")

needs_database = pytest.mark.skipif(not TEST_URL, reason=SKIP_WHY)
DEPLOYMENT = "audit-reattestation-test"


def with_database(url: str, name: str) -> str:
    """The same server and credentials, a different database."""
    parts = urlsplit(url)
    return urlunsplit(parts._replace(path="/" + name))


@pytest.fixture(scope="module", autouse=True)
def environment():
    """A scratch database for this module, migrated here, dropped afterwards.

    **Why its own database.** A re-attestation is appended to a chain, and a chain is cumulative. Another
    suite's rows sit inside the export this file verifies, and a second process writing to the named database
    while these tests run can break a link for a reason that has nothing to do with the code under test. A
    database that starts empty is the only way to assert that the chain verifies. If the server refuses
    `CREATE DATABASE`, the named database is used as it is and a warning says so.

    The guard on TEST_URL is not decoration: this fixture is autouse, and without it a run with no test database
    would still clear the deployment's DATABASE_URL for every later test in the session.

    **The previous value of DATABASE_URL is restored, not just overwritten.** That was the other half of the
    same fault: a full-suite run left the scratch database's URL in the environment after this module, and the
    retrieval tests that ran later tried to connect to a database that had already been dropped.
    """
    if not TEST_URL:
        yield
        return

    original = (audit.DSN, audit.MIGRATIONS_DIR)
    original_url = os.environ.get("DATABASE_URL")
    name = "oi_audit_reattest_" + uuid.uuid4().hex[:10]
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
        # **Put the environment back the way the rest of the suite expects it.** A fixture that changes a
        # process-wide URL and does not restore it makes a later test fail for a reason in another file.
        if original_url is None:
            os.environ.pop("DATABASE_URL", None)
        else:
            os.environ["DATABASE_URL"] = original_url


def seed_approval() -> tuple[int, str, dict]:
    approval = {
        "decision": "approve",
        "reason": "",
        "action_digest": "sha256:abc",
        "identity": {
            "assurance": "unverified",
            "basis": "session_token",
            "claimed_subject": "dana",
            "claimed_role": "staff",
        },
    }
    payload = {"stage": "approval", "approval": approval}
    with audit.connect() as conn, conn.cursor() as cur:
        written = audit.insert_event(
            cur, event_type="approval", actor="dana", actor_role="staff",
            deployment_id=DEPLOYMENT, data_class="internal", outcome="ok",
            stored_payload=payload,
            payload_sha256=audit.sha256_hex(audit.canonical_json(payload)),
            approval=approval,
        )
    return written["id"], written["row_hash"], payload


def args_for(target_id: int, target_hash: str, **overrides) -> argparse.Namespace:
    base = dict(
        supersedes_event_id=target_id,
        supersedes_row_hash=target_hash,
        original_assurance="unverified",
        new_assurance="provider_verified",
        basis="identity provider",
        resolved_subject="cognito|us-east-1|subject-123",
        attested_by=None,
        reason="C7 identity provider established",
        limits="establishes the account; does not establish that the person personally decided",
        assertion=None,
        assertion_key=None,
        dev=True,
        actor="dana",
        actor_role="staff",
        deployment_id=DEPLOYMENT,
        data_class="internal",
        outcome="ok",
    )
    base.update(overrides)
    return argparse.Namespace(**base)


def rows(event_type: str):
    with audit.connect() as conn, conn.cursor() as cur:
        cur.execute("""select id, event_type, payload, approval, approval_sha256, chain_version
                         from audit_events where event_type = %s order by id""", (event_type,))
        return cur.fetchall()


def test_the_assurance_vocabulary_matches_the_api_record():
    assert audit.ASSURANCE_VALUES == approvals.ASSURANCE_VALUES


@needs_database
def test_reattestation_appends_and_leaves_the_original_byte_identical(capsys, tmp_path):
    target_id, target_hash, original_payload = seed_approval()
    before = rows("approval")
    original_row = next(row for row in before if row[0] == target_id)
    assert original_payload["approval"]["identity"]["assurance"] == "unverified"

    assert audit.cmd_reattest(args_for(target_id, target_hash)) == 0
    printed = json.loads(capsys.readouterr().out)

    after = rows("approval")
    same = next(row for row in after if row[0] == target_id)
    assert same == original_row, "the re-attestation must not edit the original approval"
    # **Section 5.4, as an assertion.** The original record still says its identity was unverified, because a
    # later statement is a new claim about an old act and cannot upgrade what the old row says. The new row
    # carries the stronger statement beside it, and both are readable.
    assert same[2]["approval"]["identity"]["assurance"] == "unverified", (
        "a re-attestation must not raise the original record's assurance")

    new = [row for row in rows("reattestation") if row[0] == printed["id"]][0]
    payload = new[2]
    assert payload["kind"] == "reattestation"
    assert payload["supersedes_event_id"] == target_id
    assert payload["supersedes_row_hash"] == target_hash
    assert payload["original_assurance"] == "unverified"
    assert payload["new_assurance"] == "provider_verified"
    assert payload["limits"], "the limits field is required, and for a reason"
    assert payload["attested_by"] == "dana", "the signer is who the claim is worth"
    assert payload["original_assurance"] == "unverified"
    assert new[3] is None and new[4] is None, "a re-attestation is not an approval"
    assert new[5] == 3

    day = datetime.now(timezone.utc).date().isoformat()
    out = tmp_path / "export"
    audit.cmd_export(argparse.Namespace(date=day, out=str(out)))
    assert audit.cmd_verify(argparse.Namespace(file=str(out / "audit-events.jsonl"), anchor=None)) == 0


@needs_database
def test_a_wrong_row_hash_is_refused_and_appends_nothing():
    target_id, target_hash, _ = seed_approval()
    before = len(rows("reattestation"))
    with pytest.raises(SystemExit):
        audit.cmd_reattest(args_for(target_id, "0" * 64))
    assert len(rows("reattestation")) == before, "a refused re-attestation must not append a row"


@needs_database
def test_a_different_original_assurance_is_refused():
    target_id, target_hash, _ = seed_approval()
    before = len(rows("reattestation"))
    with pytest.raises(SystemExit):
        audit.cmd_reattest(args_for(target_id, target_hash, original_assurance="proofed"))
    assert len(rows("reattestation")) == before


# --------------------------------------------------------------------------- the command, no database
#
# **These run without Postgres.** A re-attestation's refusal rules and its payload are decided in Python before
# the writer is reached, so they can be checked against a cursor that answers the target lookup and records what
# would have been inserted. The database tests above are for the parts only Postgres can answer: the append-only
# trigger, the chain, and whether the original row really is untouched.


class FakeCursor:
    """Answers the target lookup, then the insert's RETURNING, and records every statement."""

    def __init__(self, target) -> None:
        self.statements: list = []
        self.answers = [target, (99, "2026-10-22T12:00:00.000000Z", "new-row-hash")]

    def execute(self, query, params=None) -> None:
        self.statements.append((query, params))

    def fetchone(self):
        return self.answers.pop(0)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class FakeConnection:
    def __init__(self, target) -> None:
        self.cursor_obj = FakeCursor(target)

    def cursor(self) -> FakeCursor:
        return self.cursor_obj

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


TARGET = ("approval", "c" * 64, "unverified")


def run(monkeypatch, **overrides):
    connection = FakeConnection(TARGET)
    monkeypatch.setattr(audit, "connect", lambda: connection)
    buffer = io.StringIO()
    with redirect_stdout(buffer):
        code = audit.cmd_reattest(args_for(7, "c" * 64, **overrides))
    return code, buffer.getvalue(), connection


def inserted(connection: FakeConnection) -> dict:
    """The insert's parameters by column name, read from the SQL the writer was given."""
    statement = next((query, params) for query, params in connection.cursor_obj.statements
                     if "insert into audit_events" in query)
    query, params = statement
    match = re.search(r"insert into audit_events\s*\(([^)]+)\)\s*values", query)
    columns = [column.strip() for column in match.group(1).replace("\n", " ").split(",") if column.strip()]
    assert len(columns) == len(params), (
        f"the insert takes {len(columns)} parameters and got {len(params)}")
    return dict(zip(columns, params))


def payload_of(parameters: dict) -> dict:
    value = parameters["payload"]
    return getattr(value, "obj", value)


def test_the_command_builds_the_documented_payload_and_prints_the_new_row(monkeypatch):
    code, output, connection = run(monkeypatch)
    assert code == 0
    printed = json.loads(output)
    assert printed["id"] == 99
    assert printed["row_hash"] == "new-row-hash"
    assert printed["supersedes_event_id"] == 7
    assert printed["supersedes_row_hash"] == "c" * 64

    parameters = inserted(connection)
    assert parameters["event_type"] == "reattestation"
    payload = payload_of(parameters)
    assert payload["kind"] == "reattestation"
    assert payload["supersedes_event_id"] == 7
    assert payload["supersedes_row_hash"] == "c" * 64
    assert payload["original_assurance"] == "unverified"
    assert payload["new_assurance"] == "provider_verified"
    assert payload["resolved_subject"].startswith("cognito|")
    assert payload["limits"], "the limits field is part of the record, not a courtesy"
    assert parameters["payload_sha256"] == audit.sha256_hex(audit.canonical_json(payload))
    # A re-attestation is not an approval and carries neither of the approval columns.
    assert parameters["approval"] is None
    assert parameters["approval_sha256"] is None


def test_a_wrong_row_hash_is_refused_before_anything_is_written(monkeypatch, capsys):
    connection = FakeConnection(TARGET)
    monkeypatch.setattr(audit, "connect", lambda: connection)
    with pytest.raises(SystemExit) as refusal:
        audit.cmd_reattest(args_for(7, "0" * 64))
    assert refusal.value.code != 0
    assert "hashes to" in capsys.readouterr().err
    assert not [query for query, _ in connection.cursor_obj.statements
                if "insert into audit_events" in query], "a refused link must not append a row"


def test_an_unknown_target_is_refused(monkeypatch, capsys):
    connection = FakeConnection(None)
    monkeypatch.setattr(audit, "connect", lambda: connection)
    with pytest.raises(SystemExit):
        audit.cmd_reattest(args_for(7, "c" * 64))
    assert "nothing to re-attest" in capsys.readouterr().err
    assert not [query for query, _ in connection.cursor_obj.statements
                if "insert into audit_events" in query]


def test_a_statement_that_misstates_the_original_assurance_is_refused(monkeypatch, capsys):
    connection = FakeConnection(TARGET)
    monkeypatch.setattr(audit, "connect", lambda: connection)
    with pytest.raises(SystemExit):
        audit.cmd_reattest(args_for(7, "c" * 64, original_assurance="proofed"))
    assert "what the earlier record said" in capsys.readouterr().err
    assert not [query for query, _ in connection.cursor_obj.statements
                if "insert into audit_events" in query]


def test_attesting_on_somebody_elses_behalf_is_refused(monkeypatch, capsys):
    connection = FakeConnection(TARGET)
    monkeypatch.setattr(audit, "connect", lambda: connection)
    with pytest.raises(SystemExit):
        audit.cmd_reattest(args_for(7, "c" * 64, attested_by="mallory"))
    assert "refuses to attribute the claim" in capsys.readouterr().err
    assert not [query for query, _ in connection.cursor_obj.statements
                if "insert into audit_events" in query]


def test_the_command_never_updates_or_deletes_the_original_row(monkeypatch):
    """APR-UPGRADE-001's first clause, asserted on what the command actually sends. The append-only trigger would
    refuse an update, and a command that relied on that refusal would be one edit away from trying."""
    _, _, connection = run(monkeypatch)
    statements = [query for query, _ in connection.cursor_obj.statements]
    assert not [query for query in statements if re.match(r"\s*(update|delete)\b", query, re.IGNORECASE)]
    source = inspect.getsource(audit.cmd_reattest)
    assert "update audit_events" not in source.lower()
    assert "delete from audit_events" not in source.lower()


def test_the_parser_knows_the_reattestation_flags():
    """The flags are the record's interface. Every field of the design's payload has a way in, `limits` cannot be
    omitted, and `record --type reattestation` is accepted for a caller that already has a statement."""
    parser = audit.build_parser()
    args = parser.parse_args([
        "reattest", "--supersedes-event-id", "7", "--supersedes-row-hash", "c" * 64,
        "--basis", "identity provider", "--resolved-subject", "cognito|us-east-1|subject-123",
        "--reason", "C7 cutover", "--limits", "does not establish the person",
        "--dev", "--actor", "dana"])
    assert args.func is audit.cmd_reattest
    assert args.new_assurance == audit.DEFAULT_REATTESTATION_ASSURANCE == "re_attested"

    with pytest.raises(SystemExit):
        parser.parse_args([
            "reattest", "--supersedes-event-id", "7", "--supersedes-row-hash", "c" * 64,
            "--basis", "b", "--resolved-subject", "s", "--reason", "r"])

    record = parser.parse_args(["record", "--type", "reattestation", "--dev", "--actor", "dana"])
    assert record.type == "reattestation"
