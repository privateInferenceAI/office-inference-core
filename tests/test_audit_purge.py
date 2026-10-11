"""Tests for the retention purge and its tombstone.

Two things make the tombstone work, and neither is visible from the outside: it has to be written
*before* the rows are deleted, so its prev_hash is the head from before the purge, and the whole thing
has to run with the maintenance setting on or the append-only trigger refuses it. A scripted cursor
lets both be asserted without a database.
"""
from __future__ import annotations

import sys
import types
from pathlib import Path

import pytest

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

HEAD_BEFORE = "a" * 64
HEAD_AFTER = "b" * 64


class ScriptedCursor:
    """Answers the queries cmd_purge makes and records every statement it was given."""

    def __init__(self, rows_to_purge: int, first_id: int | None, last_id: int | None) -> None:
        self.rows_to_purge = rows_to_purge
        self.first_id = first_id
        self.last_id = last_id
        self.executed: list[tuple[str, object]] = []
        self.rowcount = 0
        self._next = None

    def execute(self, query, params=None) -> None:
        flat = " ".join(query.split())
        self.executed.append((flat, params))
        lowered = flat.lower()
        if lowered.startswith("select count"):
            self._next = (self.rows_to_purge, self.first_id, self.last_id)
        elif lowered.startswith("select id, row_hash"):
            self._next = (10, HEAD_BEFORE)
        elif lowered.startswith("insert into audit_events"):
            self._next = (11, "2026-10-11T03:00:00.000000Z", HEAD_BEFORE, HEAD_AFTER)
        elif lowered.startswith("delete from audit_events"):
            self.rowcount = self.rows_to_purge

    def fetchone(self):
        return self._next

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class ScriptedConnection:
    def __init__(self, cursor: ScriptedCursor) -> None:
        self.cursor_obj = cursor

    def cursor(self) -> ScriptedCursor:
        return self.cursor_obj

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def purge_args(**overrides):
    values = {"before": "2026-01-01", "reason": "retention: 90 days", "actor": "retention-job",
              "dry_run": False}
    values.update(overrides)
    return type("Args", (), values)()


def statements(cursor: ScriptedCursor) -> list[str]:
    return [q for q, _ in cursor.executed if not q.lower().startswith("select count")
            and not q.lower().startswith("select id, row_hash")]


@pytest.fixture
def connection(monkeypatch):
    def build(rows_to_purge=3, first_id=1, last_id=3):
        cursor = ScriptedCursor(rows_to_purge, first_id, last_id)
        monkeypatch.setattr(audit, "connect", lambda: ScriptedConnection(cursor))
        return cursor
    return build


def test_the_tombstone_is_written_before_the_rows_are_deleted(connection):
    """The order is what makes prev_hash the head from before the purge. Reverse it and the tombstone
    chains from the surviving rows instead, and the hash before the gap is lost with the rows."""
    cursor = connection()
    assert audit.cmd_purge(purge_args()) == 0

    written = statements(cursor)
    insert_at = next(i for i, q in enumerate(written) if q.lower().startswith("insert"))
    delete_at = next(i for i, q in enumerate(written) if q.lower().startswith("delete"))
    assert insert_at < delete_at, written


def test_the_maintenance_setting_is_switched_on_before_anything_is_written(connection):
    """Without it the append-only trigger refuses both the insert and the delete, which is the whole
    point of the trigger."""
    cursor = connection()
    assert audit.cmd_purge(purge_args()) == 0

    written = statements(cursor)
    assert written[0] == "set local oi.allow_audit_maintenance = 'on'", written
    assert sum(1 for q in written if "allow_audit_maintenance" in q) == 1


def test_the_tombstone_names_what_was_removed(connection):
    cursor = connection()
    assert audit.cmd_purge(purge_args(reason="court order")) == 0
    insert_params = next(p for q, p in cursor.executed if q.lower().startswith("insert"))
    payload = insert_params[-1]
    assert payload["deleted_count"] == 3
    assert payload["first_id"] == 1 and payload["last_id"] == 3
    assert payload["reason"] == "court order"
    assert payload["before"] == "2026-01-01"


def test_a_dry_run_changes_nothing(connection):
    cursor = connection()
    assert audit.cmd_purge(purge_args(dry_run=True)) == 0
    written = statements(cursor)
    assert not any(q.lower().startswith("insert") for q in written), written
    assert not any(q.lower().startswith("delete") for q in written), written


def test_an_empty_window_writes_no_tombstone(connection):
    """A tombstone with nothing to explain would be a false entry in the record."""
    cursor = connection(rows_to_purge=0, first_id=None, last_id=None)
    assert audit.cmd_purge(purge_args()) == 0
    written = statements(cursor)
    assert not any(q.lower().startswith("insert") for q in written), written


def test_a_malformed_date_is_refused_before_the_database_is_opened(monkeypatch):
    def explode():
        raise AssertionError("the date should be checked before connecting")
    monkeypatch.setattr(audit, "connect", explode)
    with pytest.raises(SystemExit):
        audit.cmd_purge(purge_args(before="last Tuesday"))


# --- how a call is classified in the record ----------------------------------------------------

def test_a_successful_call_is_recorded_as_ok():
    assert audit.call_outcome("success", None, None) == "ok"


def test_a_budget_refusal_is_denied_not_ok():
    """The defect this replaced: the gateway writes a refused call to the spend log with
    status='failure' and zero tokens, and an earlier version of the backfill recorded it as a
    success, which put 'a model answered' in the record for a call that never happened."""
    assert audit.call_outcome("failure", "422", "429_BUDGET_EXCEEDED") == "denied"


def test_a_model_access_refusal_is_denied():
    assert audit.call_outcome("failure", "403", "403_MODEL_ACCESS_DENIED") == "denied"


def test_a_provider_failure_is_an_error_rather_than_a_denial():
    """A decision and a failure should not look alike to whoever reads the record later."""
    assert audit.call_outcome("failure", "500", None) == "error"
    assert audit.call_outcome("failure", None, None) == "error"


def test_the_role_comes_out_of_the_key_alias():
    assert audit.role_from_alias("oi-core-dev-cloud:analyst") == "analyst"


def test_an_event_without_an_alias_falls_back_to_the_gateway():
    assert audit.role_from_alias(None) == "gateway"
    assert audit.role_from_alias("no-colon-here") == "gateway"
