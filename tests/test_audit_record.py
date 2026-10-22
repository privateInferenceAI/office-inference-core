"""Tests for recording an event, and for the refusal of a hand-typed actor.

An actor is an identity, not a word someone types into a shell. Until the assistant API passes an
authenticated one, the CLI is a development tool and has to say so, so the guard is tested here
without a database: it runs before anything connects.
"""
from __future__ import annotations

import sys
import types
from pathlib import Path

import pytest

# The service imports psycopg at module level, the same stub the verifier tests use.
try:
    import psycopg  # noqa: F401 - the real driver when it is installed
except ImportError:
    psycopg = types.ModuleType("psycopg")
    psycopg.connect = lambda *a, **k: None
    psycopg.types = types.ModuleType("psycopg.types")
    psycopg.types.json = types.ModuleType("psycopg.types.json")
    psycopg.types.json.Jsonb = lambda value: value
    sys.modules["psycopg"] = psycopg
    sys.modules["psycopg.types"] = psycopg.types
    sys.modules["psycopg.types.json"] = psycopg.types.json

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "services" / "audit"))
import audit  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]


class FakeCursor:
    def __init__(self) -> None:
        self.executed: list = []

    def execute(self, query, params=None) -> None:
        self.executed.append((query, params))

    def fetchone(self):
        return (7, "2026-10-11T03:00:00.000000Z", "row-hash")

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class FakeConnection:
    def __init__(self) -> None:
        self.cursor_obj = FakeCursor()

    def cursor(self) -> FakeCursor:
        return self.cursor_obj

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def record_args(**overrides):
    values = {"payload": None, "actor": "someone", "actor_role": None,
              "deployment_id": "test-deploy", "data_class": "internal", "outcome": "ok",
              "tool": None, "model": None, "request_id": None, "approval": None,
              "type": "access", "store_payload": False, "dev": False,
              "prompt": None, "completion": None,
              # The redaction summary. Defaults here rather than getattr in the writer, so a field added to the
              # CLI without being added here fails a test instead of being silently absent at run time.
              "redactions": None, "unverified_card_shapes": 0, "egress_allowed": None}
    values.update(overrides)
    return type("Args", (), values)()


def test_a_hand_typed_actor_is_refused_without_the_dev_flag(capsys):
    """The requirement, in one assertion: record --actor someone, no dev flag, non-zero exit."""
    with pytest.raises(SystemExit) as exit_info:
        audit.cmd_record(record_args())
    assert exit_info.value.code != 0
    assert "hand-typed actor" in capsys.readouterr().err


def test_the_refusal_happens_before_the_database_is_touched(monkeypatch):
    """A refusal that half-writes an event would be worse than no refusal."""
    def explode():
        raise AssertionError("the guard must run before anything connects")

    monkeypatch.setattr(audit, "connect", explode)
    with pytest.raises(SystemExit):
        audit.cmd_record(record_args())


def test_dev_mode_records_and_says_what_it_is_doing(monkeypatch, capsys):
    connection = FakeConnection()
    monkeypatch.setattr(audit, "connect", lambda: connection)

    assert audit.cmd_record(record_args(dev=True)) == 0

    printed = capsys.readouterr()
    assert "warning" in printed.err.lower()
    assert "authenticated identity" in printed.err

    inserts = [q for q, _ in connection.cursor_obj.executed if "insert into audit_events" in q]
    assert len(inserts) == 1, "the event should be inserted once"


# --- content modes -----------------------------------------------------------------------------
#
# What the record keeps is a client decision per data class. These hold the two cases the
# requirement names: full mode stores the text and its hash matches, hash mode stores no text.

def record_with_content(monkeypatch, **overrides):
    connection = FakeConnection()
    monkeypatch.setattr(audit, "connect", lambda: connection)
    args = record_args(dev=True, **overrides)
    assert audit.cmd_record(args) == 0
    insert = next(p for q, p in connection.cursor_obj.executed if "insert into audit_events" in q)
    return insert


def insert_columns() -> list[str]:
    """The column list the writer inserts into, read from its own SQL.

    Read rather than written out, because the failure this guards against is the two drifting apart: a helper
    that counts from the end of the parameter list reads whatever happens to be there after somebody adds a
    column, and it reads it as a hash without complaining.
    """
    import re
    source = (ROOT / "services" / "audit" / "audit.py").read_text()
    match = re.search(r"insert into audit_events\s*\(([^)]+)\)\s*values", source)
    assert match, "could not find the audit insert statement"
    return [column.strip() for column in match.group(1).replace("\n", " ").split(",") if column.strip()]


def field(insert_params, name: str):
    """One parameter by column name, so adding a column cannot silently shift what a test reads."""
    columns = insert_columns()
    index = columns.index(name)
    assert len(insert_params) == len(columns), (
        f"the insert takes {len(columns)} parameters and got {len(insert_params)}")
    return insert_params[index]


def stored_payload(insert_params):
    value = field(insert_params, "payload")
    # psycopg's Jsonb wrapper is what the writer passes the driver; the stub used on a machine without the
    # driver is the identity function. Unwrap it so the test reads the value either way.
    return getattr(value, "obj", value)


def prompt_hash(insert_params):
    """The prompt hash, by column name.

    It used to be `[-2]`, the second-from-last parameter, until the redaction summary added four more after it
    and it silently became the unverified-card count: a test comparing a hash to a small integer, failing for a
    reason that had nothing to do with what it was testing. Position from the end is as fragile as position from
    the start, so both directions are gone.
    """
    return field(insert_params, "prompt_sha256")


def test_hash_mode_stores_no_content(monkeypatch):
    monkeypatch.setenv("CONTENT_MODE_INTERNAL", "hash")
    monkeypatch.setenv("CONTENT_DEFAULT", "hash")
    insert = record_with_content(monkeypatch, payload='{"document":"expense-policy.pdf"}',
                                 prompt="what is the mileage rate?")
    assert stored_payload(insert) is None, "hash mode must not keep the text"
    assert prompt_hash(insert) == audit.sha256_hex("what is the mileage rate?"), \
        "the hash is recorded even when the text is not"


def test_full_mode_stores_content_and_the_hash_matches(monkeypatch):
    monkeypatch.setenv("CONTENT_MODE_INTERNAL", "full")
    prompt = "summarise the attached fee schedule"
    insert = record_with_content(monkeypatch, payload='{"document":"fee-schedule.pdf"}',
                                 prompt=prompt)
    payload = stored_payload(insert)
    assert payload["prompt"] == prompt
    assert prompt_hash(insert) == audit.sha256_hex(payload["prompt"]), \
        "the stored prompt must hash to the recorded prompt_sha256"


def test_redacted_mode_keeps_the_text_without_the_identifiers(monkeypatch):
    monkeypatch.setenv("CONTENT_MODE_INTERNAL", "redacted")
    insert = record_with_content(monkeypatch, prompt="call the client on 555-123-4567")
    stored = stored_payload(insert)["prompt"]
    assert "[REDACTED-PHONE]" in stored
    assert "555-123-4567" not in stored


def test_the_mode_comes_from_the_data_class(monkeypatch):
    monkeypatch.setenv("CONTENT_MODE_REGULATED", "hash")
    monkeypatch.setenv("CONTENT_MODE_PUBLIC", "full")
    inserted = record_with_content(monkeypatch, data_class="regulated", payload='{"a":1}')
    assert stored_payload(inserted) is None
    inserted = record_with_content(monkeypatch, data_class="public", payload='{"a":1}')
    assert stored_payload(inserted) == {"a": 1}


def test_an_unset_environment_falls_back_to_hashing(monkeypatch):
    """Storing content nobody asked to store is the failure that matters, so the fallback is the
    conservative one."""
    monkeypatch.delenv("CONTENT_MODE_INTERNAL", raising=False)
    monkeypatch.delenv("CONTENT_DEFAULT", raising=False)
    assert audit.content_mode("internal") == "hash"
