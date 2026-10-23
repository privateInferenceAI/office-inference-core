"""The chain covers the redaction summary, and no existing row's hash was invalidated.

The gap: migration 012 added four columns and the canonical string was not changed to include them, so a row
whose `redactions` value was edited by hand still verified. In a table whose purpose is detecting that, a field
the chain does not cover is a field that can be rewritten while every check passes and reports the record
intact.

The reason this is a version rather than an edit: a row's stored hash was computed from a canonical string, and
verification recomputes that string. Change the formula and every row written under the old one fails, which is
indistinguishable from tampering. A verifier that reports untouched history as a break is one whose real
findings nobody reads.

Run: python3 -m pytest tests/
"""
from __future__ import annotations

import sys
import types
from pathlib import Path

# The service imports psycopg at module level, so the same stub the other audit tests use.
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

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "services" / "audit"))

import audit  # noqa: E402


def row(**overrides) -> dict:
    """A row as the export writes it, at version 2."""
    base = {
        "ts_iso": "2026-10-13T13:38:40.305481Z", "event_type": "model_call", "actor": "someone",
        "actor_role": "system", "deployment_id": "dev", "data_class": "internal", "outcome": "ok",
        "tool": None, "model": "anthropic.claude-sonnet-4-5", "request_id": "r-1",
        "payload_sha256": "a" * 64, "payload_bytes": 12, "prev_hash": "b" * 64,
        "chain_version": 2, "redaction_count": 3,
        "redactions": {"ssn": 2, "card": 1}, "unverified_card_shapes": 0, "egress_allowed": True,
    }
    base.update(overrides)
    return base


def test_version_one_still_verifies_with_the_original_formula():
    """The rows written before this change. If the formula had simply been extended, these would fail and the
    failure would look exactly like a tampered record."""
    original = row(chain_version=1)
    # The thirteen fields, in the original order, and nothing else.
    expected = "|".join([
        original["ts_iso"], "model_call", "someone", "system", "dev", "internal", "ok", "",
        "anthropic.claude-sonnet-4-5", "r-1", "a" * 64, "12", "b" * 64,
    ])
    assert audit.row_canonical(original) == expected


def test_a_row_with_no_version_is_read_as_version_one():
    """An export taken before this change has no chain_version field, and a verifier that treated the absence as
    the newest version would fail every row in every existing export."""
    without = row()
    del without["chain_version"]
    assert audit.row_canonical(without) == audit.row_canonical(row(chain_version=1))


def test_version_two_adds_the_redaction_summary_in_a_form_both_languages_produce():
    """Flattened to sorted `key=value` pairs rather than JSON.

    The writer is Postgres and the verifier is Python, and `jsonb::text` puts a space after every colon and
    comma. Matching that textually would mean reproducing one language's serialiser in the other, and the day
    either changed, every row would fail verification at once.
    """
    canonical = audit.row_canonical(row())
    assert canonical.endswith("|3|card=1,ssn=2|0|true"), canonical


def test_the_summary_order_does_not_depend_on_insertion_order():
    """A dict assembled in a different order must produce the same hash, or the same event written twice would
    hash differently and the record could not be used to compare two calls."""
    a = audit.row_canonical(row(redactions={"ssn": 2, "card": 1}))
    b = audit.row_canonical(row(redactions={"card": 1, "ssn": 2}))
    assert a == b


def test_editing_a_redaction_count_changes_the_hash():
    """The whole point. Before this, changing this field left the hash valid and the verifier reported the
    record intact."""
    original = row()
    edited = row(redaction_count=0, redactions={})
    assert audit.row_canonical(original) != audit.row_canonical(edited)
    assert audit.sha256_hex(audit.row_canonical(original)) != \
        audit.sha256_hex(audit.row_canonical(edited))


def test_editing_an_egress_decision_changes_the_hash():
    """A gate that refused a request is a different fact from a gate that allowed it, and it must not be
    editable while the record still verifies."""
    assert audit.row_canonical(row(egress_allowed=True)) != \
        audit.row_canonical(row(egress_allowed=False))
    # Null is its own state: no egress was attempted, which is not the same as refused.
    assert audit.row_canonical(row(egress_allowed=None)) != audit.row_canonical(row(egress_allowed=False))


def test_nulls_and_zeroes_are_distinguishable_in_the_canonical_string():
    """A field that is absent and a field that is zero are different facts, and a canonical form that wrote
    both as the empty string would let a count be cleared without the hash noticing."""
    assert audit.row_canonical(row(redaction_count=0)) != audit.row_canonical(row(redaction_count=1))
    assert audit.row_canonical(row(unverified_card_shapes=0)) != \
        audit.row_canonical(row(unverified_card_shapes=1))


def test_the_migration_carries_the_same_formula_as_the_verifier():
    """The database function and the Python function are two copies of one formula, so they are checked against
    each other on the things that are easy to get subtly different: the field order, the version branch, and the
    separator."""
    migration = (ROOT / "db" / "migrations" / "013_chain_covers_redaction.sql").read_text()
    assert "p_chain_version < 2" in migration, "the migration does not branch on the version"
    assert "string_agg(key || '=' || value, ',' order by key)" in migration, \
        "the migration does not flatten the summary the way the verifier does"
    assert "coalesce(p_egress_allowed::text, '')" in migration
    # The base fields, in the order the verifier writes them.
    for field in ("p_ts_iso", "p_event_type", "p_actor", "p_actor_role", "p_deployment_id",
                  "p_data_class", "p_outcome", "p_tool", "p_model", "p_request_id",
                  "p_payload_sha256", "p_payload_bytes", "p_prev_hash"):
        assert field in migration, f"{field} is missing from the database canonical form"


def test_a_row_from_a_newer_writer_is_reported_rather_than_miscounted():
    """A row claiming a version this verifier does not know cannot be checked. Reporting it as a mismatch would
    call it tampering; saying nothing would let it pass. It is reported as unchecked, which is what it is.

    The ceiling moved to 3 in migration 025, so the guard is read from there now. 013's copy of the message is
    history; the function that runs is the newest one, and that is the one this test has to read.
    """
    assert audit.KNOWN_CHAIN_VERSION == 3
    migration = (ROOT / "db" / "migrations" / "025_the_approval_is_in_the_chain.sql").read_text()
    assert "chain version % has no canonical form" in migration, \
        "the writer accepts versions the verifier cannot check"
    assert "new.chain_version > 3" in migration, "the guard's ceiling and the verifier's disagree"
