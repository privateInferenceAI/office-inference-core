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
if "psycopg" not in sys.modules:
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
              "prompt": None, "completion": None}
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


def stored_payload(insert_params):
    return insert_params[-3]


def prompt_hash(insert_params):
    return insert_params[-2]


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
