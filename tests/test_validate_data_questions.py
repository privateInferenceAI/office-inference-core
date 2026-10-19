"""Tests for the data-question validator's pure logic.

The validator has two halves: parsing and checking, which must run on a machine with no database,
and the Postgres reader, which must not be exercised here. These tests pin the first half and use
an in-memory index for the second, so a passing run says the checks work without saying anything
about the box.

Run: python3 -m pytest tests/test_validate_data_questions.py
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import validate_data_questions as v  # noqa: E402


def question(**overrides):
    base = {
        "id": "T01",
        "category": "prose_fact",
        "question": "What is the answer?",
        "expected": {"staff": {"answered": True, "docs": ["company/doc.txt"],
                               "contains": ["42"]}},
        "source_quote": "The answer is 42",
    }
    base.update(overrides)
    return base


def parse(**overrides):
    return v.parse_document({"questions": [question(**overrides)]})


class FakeIndex:
    """The ChunkIndex seam, in memory. No network, no database, no psycopg."""

    def __init__(self, documents: dict[str, str] | None = None, absent_matches: int = 0):
        self.documents = documents or {}
        self.absent_matches = absent_matches

    def document_exists(self, doc_id: str) -> bool:
        return doc_id in self.documents

    def document_text(self, doc_id: str) -> str:
        return self.documents.get(doc_id, "")

    def count_matching(self, pattern: str) -> int:
        return self.absent_matches

    def quote_in_index(self, fragment: str) -> bool:
        needle = v.normalise(fragment)
        return any(needle in v.normalise(text) for text in self.documents.values())


def check(index, **overrides):
    questions, errors = parse(**overrides)
    assert not errors, errors
    return v.validate_questions(questions, index)[0]


# --------------------------------------------------------------------------- normalisation

def test_normalise_keeps_only_alphanumerics_and_lowercases():
    assert v.normalise("$48,240 and 2.6%") == "48240and26"
    assert v.normalise("Acurx S-1") == "acurxs1"
    assert v.normalise(None) == ""


def test_normalise_makes_the_suites_value_forms_equal():
    assert v.normalise("48,240") == v.normalise("48240") == v.normalise("$48,240")


def test_quote_fragments_splits_the_ellipsis_convention():
    assert v.quote_fragments("row one ... row two\t… row three") == ["row one", "row two", "row three"]
    assert v.quote_fragments("only one") == ["only one"]
    assert v.quote_fragments("") == []


# --------------------------------------------------------------------------- duplicate detection

def test_duplicate_ids_are_counted():
    questions, errors = v.parse_document({"questions": [question(), question()]})
    assert not errors, errors
    assert v.duplicate_ids(questions) == {"T01": 2}


def test_distinct_ids_are_not_duplicates():
    second = question(id="T02")
    questions, errors = v.parse_document({"questions": [question(), second]})
    assert not errors, errors
    assert v.duplicate_ids(questions) == {}


# --------------------------------------------------------------------------- schema shape

def test_a_well_formed_question_parses():
    questions, errors = parse()
    assert errors == []
    assert [q.id for q in questions] == ["T01"]
    assert questions[0].expected["staff"].contains == ("42",)


def test_root_must_be_a_mapping_with_questions():
    questions, errors = v.parse_document([])
    assert questions == []
    assert "root" in errors[0]


def test_missing_id_is_refused():
    _, errors = parse(id="")
    assert any(".id" in error for error in errors)


def test_answered_question_without_docs_is_refused():
    _, errors = parse(expected={"staff": {"answered": True, "contains": ["42"]}})
    assert any("docs" in error for error in errors)


def test_answered_question_without_contains_is_refused():
    _, errors = parse(expected={"staff": {"answered": True, "docs": ["company/doc.txt"]}})
    assert any("contains" in error for error in errors)


def test_answered_question_without_a_source_quote_is_refused():
    _, errors = parse(source_quote=None)
    assert any("source_quote" in error for error in errors)


def test_access_boundary_needs_both_roles():
    _, errors = parse(category="access_boundary")
    assert any("both staff and executive" in error for error in errors)


def test_unanswerable_question_needs_an_absent_pattern():
    _, errors = parse(
        expected={"staff": {"answered": False, "denied": False}},
        source_quote=None,
        absent_pattern=None,
    )
    assert any("absent_pattern" in error for error in errors)


def test_unknown_role_is_refused():
    _, errors = parse(expected={"visitor": {"answered": True, "docs": ["d"], "contains": ["42"]}})
    assert any("unknown role" in error for error in errors)


# --------------------------------------------------------------------------- checks through the seam

def test_a_literal_question_passes_against_an_in_memory_index():
    report = check(FakeIndex({"company/doc.txt": "The answer is 42."}))
    assert report.passed, report.failures


def test_a_missing_document_is_reported():
    report = check(FakeIndex({}))
    assert not report.passed
    assert any("does not exist" in failure for failure in report.failures)


def test_a_value_absent_from_the_document_and_quote_is_reported():
    report = check(
        FakeIndex({"company/doc.txt": "The answer is something else."}),
        expected={"staff": {"answered": True, "docs": ["company/doc.txt"], "contains": ["42"]}},
        source_quote="The answer is something else",
    )
    assert not report.passed
    assert any("not inside source_quote" in failure for failure in report.failures)
    assert any("not in the named documents" in failure for failure in report.failures)


def test_a_derived_value_is_reported_when_the_quote_does_not_state_it():
    report = check(
        FakeIndex({"company/doc.txt": "row one 10 | row two 32"}),
        expected={"staff": {"answered": True, "docs": ["company/doc.txt"], "contains": ["42"]}},
        source_quote="row one 10 ... row two 32",
    )
    assert not report.passed
    assert any("not inside source_quote" in failure for failure in report.failures)


def test_an_arithmetic_quote_verifies_operands_and_the_stated_result():
    report = check(
        FakeIndex({"company/doc.txt": "The table reports 59,473 and 48,240."}),
        expected={"staff": {"answered": True, "docs": ["company/doc.txt"], "contains": ["11,233"]}},
        source_quote="59,473 - 48,240 = 11,233",
    )
    assert report.passed, report.failures


def test_an_arithmetic_quote_with_an_operand_missing_is_reported():
    report = check(
        FakeIndex({"company/doc.txt": "The table reports 59,473."}),
        expected={"staff": {"answered": True, "docs": ["company/doc.txt"], "contains": ["11,233"]}},
        source_quote="59,473 - 48,240 = 11,233",
    )
    assert not report.passed
    assert any("operand '48,240'" in failure for failure in report.failures)


def test_a_quote_fragment_missing_from_the_index_is_reported():
    report = check(
        FakeIndex({"company/doc.txt": "The answer is 42."}),
        source_quote="The answer is 42 ... a passage that is not there",
    )
    assert not report.passed
    assert any("source_quote fragment" in failure for failure in report.failures)


def test_absent_pattern_must_match_zero_chunks():
    report = check(FakeIndex({"company/doc.txt": "The answer is 42."}, absent_matches=2), absent_pattern="cafeteria")
    assert not report.passed
    assert any("matches 2 chunk" in failure for failure in report.failures)
