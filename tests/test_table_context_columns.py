"""The lexical columns the table context lives in, and the statements that create them.

**Why these are tests of text rather than tests against a database.** The suite has no Postgres: every
other ingestion test drives `MemoryManifest`, and the repository has no fake connection to borrow. What can
be proved without one is the part that goes wrong silently -- that the migration and the worker agree on the
column names, that every statement is idempotent, and that the insert's column list and the tuple it is given
have the same length. The last one is not hypothetical: `execute_values` reports a count mismatch without
naming the column, so a six-column addition made to one side only fails with a message that sends the reader
to the wrong file.

The one thing a text test cannot prove is that Postgres accepts the DDL. That is what `audit migrate` on the
box is for, and the PR body carries the exact command.
"""
from __future__ import annotations

import inspect
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "services" / "ingestion"))

import ingest  # noqa: E402  (the import needs the path above)

MIGRATIONS = ROOT / "db" / "migrations"
TABLE_CONTEXT_COLUMNS = ("table_id", "row_index", "row_start", "row_end", "table_rows", "headings")


def migration_sql() -> str:
    """Every migration's text, whitespace collapsed so a wrapped statement still matches."""
    return " ".join(" ".join(path.read_text().split())
                    for path in sorted(MIGRATIONS.glob("*.sql")))


def row_dict(**overrides) -> dict:
    """A row as `index_for_lexical` builds it, with the table fields absent unless asked for."""
    row = {"chunk_id": "chunk-1", "doc_id": "company/policy.pdf", "acl": "company",
           "text": "Total revenues | 4,934,098", "page_start": 3, "page_end": 3,
           "kind": "table_row", "chunker_version": "chunker-v5", "source_sha256": "a" * 64,
           "identifiers": []}
    row.update(overrides)
    return row


def test_every_startup_statement_is_in_a_migration():
    """The worker's statements and the migration have to say the same thing. The worker runs them so an
    operator who starts it before migrating is not punished; the migration is the path a fresh database
    takes. Two copies of one list is a drift waiting to happen, and this is the check that stops it."""
    sql = migration_sql()
    for statement in ingest.table_context_column_sql():
        assert " ".join(statement.split()) in sql, statement


def test_the_startup_statements_are_idempotent():
    """Every one of them is `add column if not exists`, which is what makes it safe to run at startup, safe
    to run after the migration, and safe to run twice."""
    statements = ingest.table_context_column_sql()
    assert statements, "there should be statements to run"
    for statement in statements:
        assert statement.startswith("alter table "), statement
        assert "add column if not exists" in statement, statement
        assert "drop " not in statement and "update " not in statement, statement


def test_the_columns_the_insert_writes_are_the_columns_the_migration_adds():
    added = " ".join(ingest.table_context_column_sql())
    for column in TABLE_CONTEXT_COLUMNS:
        assert f"chunk_index add column if not exists {column} " in added, column
        assert column in ingest.CHUNK_INDEX_COLUMNS, column


def test_the_insert_column_list_and_the_row_tuple_agree():
    """The count is the property that matters: `execute_values` raises on a mismatch and does not say
    which column is missing."""
    assert len(ingest.lexical_row(row_dict())) == len(ingest.CHUNK_INDEX_COLUMNS)


def test_a_row_carrying_the_table_fields_puts_them_in_the_right_columns():
    values = ingest.lexical_row(row_dict(
        table_id="company/policy.pdf:table:0", row_index=4, row_start=4, row_end=4,
        table_rows=231, headings=["Fees"]))
    row = dict(zip(ingest.CHUNK_INDEX_COLUMNS, values))
    assert row["table_id"] == "company/policy.pdf:table:0"
    assert (row["row_index"], row["row_start"], row["row_end"], row["table_rows"]) == (4, 4, 4, 231)
    # jsonb, so the list is sent as JSON text rather than as a Postgres array.
    assert row["headings"] == '["Fees"]'


def test_a_row_without_the_table_fields_still_makes_a_tuple():
    """Prose has no table, and a caller written before these columns (a probe, a test) must not have to
    know they exist. The columns are nullable and the value is None rather than an exception."""
    values = ingest.lexical_row(row_dict())
    assert len(values) == len(ingest.CHUNK_INDEX_COLUMNS)
    row = dict(zip(ingest.CHUNK_INDEX_COLUMNS, values))
    assert row["table_id"] is None and row["row_index"] is None
    assert row["headings"] is None


def test_the_upsert_writes_every_column_and_updates_every_column_but_the_key():
    sql = ingest.chunk_index_upsert_sql()
    assert "values %s" in sql, "execute_values needs the placeholder"
    for column in ingest.CHUNK_INDEX_COLUMNS:
        assert column in sql, column
        if column != "chunk_id":
            assert f"{column} = excluded.{column}" in sql, column
    assert "chunk_id = excluded.chunk_id" not in sql, "the key is not updated"


def test_the_manifest_records_and_reads_the_chunker_version():
    """The skip decision is on the file's bytes, so the version has to be stored beside them or a chunker
    change never reaches a document whose bytes did not change."""
    assert "ingest_manifest add column if not exists chunker_version text" in migration_sql()
    for method in (ingest.PostgresManifest.get, ingest.PostgresManifest.all,
                   ingest.PostgresManifest.put):
        assert "chunker_version" in inspect.getsource(method), method.__name__


def test_the_worker_re_derives_a_document_whose_chunker_version_is_stale():
    """Read the cycle's condition rather than trusting the comment above it."""
    source = inspect.getsource(ingest.ingest_cycle)
    assert "chunker_version" in source and "CHUNKER_VERSION" in source
    assert "chunker.CHUNKER_VERSION" in source, "the running version, not a copy of the string"
