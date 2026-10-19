#!/usr/bin/env python3
"""Validate a data-question YAML against the deployed chunk_index.

The suite in docs/eval/data-questions.yaml is only as good as its ground truth. This script is the
mechanical gate between a candidate and the scored set: it reads a questions file, checks each
question's shape, and asks the Postgres index whether the documents, the quoted passage and the
expected values are actually there.

What is checked, per question:

  * ids are unique across the file;
  * every document in `expected.<role>.docs` and `forbidden_docs` exists in chunk_index;
  * every `contains` value appears in the named documents, or in a source quote that has itself
    been verified against the index. The comparison is alnum-only and lowercased, the same
    comparison the runner uses, so "48,240" and "48240" are the same value;
  * `source_quote`, when present, is in the index (also alnum-only). A quote may use " ... " to
    omit rows or join two passages, as the live suite does; each surviving fragment is checked.
    A quote may also state an arithmetic result, as the eight cross-document live questions do
    ("59,473 - 48,240 = 11,233"): the operands before the "=" must be in the index and the expected
    values must be inside the quote. The arithmetic itself is reviewed in the evidence file, not
    recomputed here;
  * an unanswerable question's `absent_pattern` matches zero chunks;
  * a question whose expected value is computed rather than printed may carry a `derivation`
    block. Every operand must occur in the named documents, the operation is recomputed, and the
    result must be the expected value. The literal path above is unchanged; a derivation is only
    needed when the source quote does not state the answer.

Usage:

    DATABASE_URL=postgresql://user:pass@host:5432/db
    python3 scripts/validate_data_questions.py docs/eval/data-questions-wider.yaml

Exit status is 0 when every question passes, 1 when any check fails, and 2 when the file or the
database cannot be read. The report is per question, so a failure names the question and the check.

The database access lives behind `PostgresIndex`; `parse_document` and `validate_questions`
take plain objects and are what the tests exercise. There is no psycopg import at module scope, so
the pure checks run on a machine without the driver.
"""
from __future__ import annotations

import argparse
import os
import re
import sys
from collections import Counter
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Any, Iterable, Mapping, Protocol, Sequence

import yaml

ROLES = ("staff", "executive")
# " ... " is the suite's convention for an omitted row or a join between two passages.
ELLIPSIS = re.compile(r"\s*(?:\.\.\.|…)\s*")
# The operators an arithmetic source quote may use before its "=". Deliberately excludes "x" so a
# prose quote containing the letter is not split into operands.
ARITHMETIC_OPERATORS = re.compile(r"\s*[+\-*/×]\s*")

# Derivation support. A derivation states the operands as they appear in the named documents, the
# operation, and the value the question expects. It exists for answers computed from indexed rows
# (a sum over three counties, a span between two dates), which cannot appear literally in the text.
DERIVATION_OPERATIONS = ("sum", "difference", "latest", "days-between", "years-between")
BETWEEN_OPERATIONS = ("days-between", "years-between")

_NUMBER = re.compile(r"[-+]?\d[\d,]*(?:\.\d+)?")
_NUMBER_WORDS = {
    "zero": 0, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6,
    "seven": 7, "eight": 8, "nine": 9, "ten": 10, "eleven": 11, "twelve": 12,
    "thirteen": 13, "fourteen": 14, "fifteen": 15, "sixteen": 16, "seventeen": 17,
    "eighteen": 18, "nineteen": 19, "twenty": 20, "thirty": 30, "forty": 40,
    "fifty": 50, "sixty": 60, "seventy": 70, "eighty": 80, "ninety": 90,
}
_MONTHS = {
    "january": 1, "february": 2, "march": 3, "april": 4, "may": 5, "june": 6, "july": 7,
    "august": 8, "september": 9, "october": 10, "november": 11, "december": 12,
}
_MONTH_FIRST = re.compile(r"([A-Za-z]+)\s+(\d{1,2})(?:st|nd|rd|th)?\s*,?\s+(\d{4})")
_DAY_FIRST = re.compile(r"(\d{1,2})\s+([A-Za-z]+)\s+(\d{4})")
_SLASH_DATE = re.compile(r"(?<!\d)(\d{1,2})/(\d{1,2})/(\d{2}|\d{4})(?!\d)")


def operand_number(value: Any) -> Decimal | None:
    """The number an operand or result states, as an exact Decimal.

    A digit run wins ("26,649,830.44", "5-year terms", "3 days"). Without one, a spelled-out
    number is read ("eight years"), because the corpus states some terms in prose. Units are not
    converted: a derivation's operands must already share the unit its result is stated in.
    """
    text = str(value or "").strip()
    match = _NUMBER.search(text)
    if match:
        try:
            return Decimal(match.group(0).replace(",", ""))
        except InvalidOperation:
            return None
    return _spelled_number(text)


def _spelled_number(text: str) -> Decimal | None:
    total = 0
    current = 0
    found = False
    for token in re.findall(r"[a-z]+(?:-[a-z]+)?", text.lower()):
        for word in token.split("-"):
            if word in _NUMBER_WORDS:
                current += _NUMBER_WORDS[word]
                found = True
            elif word == "hundred":
                current = (current or 1) * 100
                found = True
            elif word == "thousand":
                total += (current or 1) * 1000
                current = 0
                found = True
    return Decimal(total + current) if found else None


def parse_date(value: Any) -> date | None:
    """A date an operand or result states, or None.

    Handles the forms in the corpus: "September 9, 2004" (month first), "30 September 2004" (day
    first) and the exhibit index's "8/23/24". A two-digit year is read as 20xx, which is safe for
    this corpus and is pinned by the tests.
    """
    text = " ".join(str(value or "").replace("\u00a0", " ").split())
    match = _MONTH_FIRST.search(text)
    if match and match.group(1).lower() in _MONTHS:
        return _safe_date(int(match.group(3)), _MONTHS[match.group(1).lower()], int(match.group(2)))
    match = _DAY_FIRST.search(text)
    if match and match.group(2).lower() in _MONTHS:
        return _safe_date(int(match.group(3)), _MONTHS[match.group(2).lower()], int(match.group(1)))
    match = _SLASH_DATE.search(text)
    if match:
        year = int(match.group(3))
        year = 2000 + year if year < 100 else year
        return _safe_date(year, int(match.group(1)), int(match.group(2)))
    return None


def _safe_date(year: int, month: int, day: int) -> date | None:
    try:
        return date(year, month, day)
    except ValueError:
        return None


def completed_years(first: date, second: date) -> int:
    """Whole years elapsed between two dates, ignoring the order they are given in."""
    start, end = sorted((first, second))
    years = end.year - start.year
    if (end.month, end.day) < (start.month, start.day):
        years -= 1
    return years


def normalise(text: Any) -> str:
    """The runner's comparison: lowercase, keep alphanumerics only.

    Both sides of every textual check go through this function. It is intentionally the same
    function as tests/fixtures/data_suite_run.py uses, because a validator that normalised
    differently from the scorer would pass questions the scorer then fails.
    """
    return "".join(character for character in str(text or "").lower() if character.isalnum())


def quote_fragments(quote: str) -> list[str]:
    """The non-empty fragments of a source quote, split on the suite's ellipsis convention."""
    return [fragment.strip() for fragment in ELLIPSIS.split(quote or "") if fragment.strip()]


def duplicate_ids(questions: Sequence["Question"]) -> dict[str, int]:
    """Ids that occur more than once, with the number of occurrences."""
    counts = Counter(question.id for question in questions)
    return {question_id: count for question_id, count in counts.items() if count > 1}


def _shorten(text: str, limit: int = 90) -> str:
    text = " ".join(str(text).split())
    return text if len(text) <= limit else text[: limit - 3] + "..."


@dataclass(frozen=True)
class Expectation:
    role: str
    answered: bool
    docs: tuple[str, ...] = ()
    contains: tuple[str, ...] = ()
    denied: bool = False
    forbidden_docs: tuple[str, ...] = ()


@dataclass(frozen=True)
class Derivation:
    """How a computed expected value follows from indexed operands."""

    operands: tuple[str, ...]
    operation: str
    result: str


@dataclass(frozen=True)
class Question:
    id: str
    category: str
    question: str
    expected: Mapping[str, Expectation]
    source_quote: str | None = None
    absent_pattern: str | None = None
    evidence: str | None = None
    derivation: Derivation | None = None


@dataclass
class QuestionReport:
    question_id: str
    failures: list[str] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return not self.failures


def _string_list(value: Any, where: str, errors: list[str], *, required: bool = True) -> tuple[str, ...]:
    if value is None and not required:
        return ()
    if not isinstance(value, list) or not value:
        errors.append(f"{where} must be a non-empty list of strings")
        return ()
    strings: list[str] = []
    for item in value:
        if not isinstance(item, str) or not item.strip():
            errors.append(f"{where} must contain only non-empty strings")
            return ()
        strings.append(item)
    return tuple(strings)


def parse_derivation(raw: Any, where: str, errors: list[str]) -> Derivation | None:
    """Parse a derivation block, appending shape errors. None when it cannot be used."""
    if not isinstance(raw, Mapping):
        errors.append(f"{where}.derivation must be a mapping")
        return None
    operands = _string_list(raw.get("operands"), f"{where}.derivation.operands", errors)
    operation_raw = raw.get("operation")
    operation = operation_raw.strip() if isinstance(operation_raw, str) else ""
    if operation not in DERIVATION_OPERATIONS:
        errors.append(
            f"{where}.derivation.operation must be one of {', '.join(DERIVATION_OPERATIONS)}; "
            f"got {operation_raw!r}"
        )
    result_raw = raw.get("result")
    result = result_raw.strip() if isinstance(result_raw, str) else ""
    if not result:
        errors.append(f"{where}.derivation.result must be a non-empty string")

    if operands and operation in ("sum", "difference") and len(operands) < 2:
        errors.append(f"{where}.derivation.operation {operation!r} needs at least two operands")
    if operands and operation in BETWEEN_OPERATIONS and len(operands) != 2:
        errors.append(f"{where}.derivation.operation {operation!r} needs exactly two operands")
    if operands and operation == "latest" and len(operands) < 2:
        errors.append(f"{where}.derivation.operation 'latest' needs at least two dates")

    if not operands or operation not in DERIVATION_OPERATIONS or not result:
        return None
    return Derivation(operands=operands, operation=operation, result=result)


def parse_document(document: Any) -> tuple[list[Question], list[str]]:
    """Parse a questions YAML document into questions and shape errors.

    Shape errors are returned rather than raised so one bad question does not hide the rest of the
    file. A question with a shape error is not returned, so it cannot be silently checked against
    defaults it never declared.
    """
    errors: list[str] = []
    if not isinstance(document, Mapping):
        return [], ["the YAML root must be a mapping with a 'questions' list"]
    raw_questions = document.get("questions")
    if not isinstance(raw_questions, list) or not raw_questions:
        return [], ["'questions' must be a non-empty list"]

    questions: list[Question] = []
    for index, raw in enumerate(raw_questions, start=1):
        where = f"questions[{index}]"
        if not isinstance(raw, Mapping):
            errors.append(f"{where} must be a mapping")
            continue

        question_id = raw.get("id")
        if not isinstance(question_id, str) or not question_id.strip():
            errors.append(f"{where}.id must be a non-empty string")
            question_id = f"<question {index}>"
            where = str(question_id)
        else:
            question_id = question_id.strip()
            where = question_id

        category = raw.get("category")
        if not isinstance(category, str) or not category.strip():
            errors.append(f"{where}.category must be a non-empty string")
        text = raw.get("question")
        if not isinstance(text, str) or not text.strip():
            errors.append(f"{where}.question must be a non-empty string")

        source_quote = raw.get("source_quote")
        if source_quote is not None and (not isinstance(source_quote, str) or not source_quote.strip()):
            errors.append(f"{where}.source_quote must be a non-empty string when present")
            source_quote = None
        absent_pattern = raw.get("absent_pattern")
        if absent_pattern is not None and (not isinstance(absent_pattern, str) or not absent_pattern.strip()):
            errors.append(f"{where}.absent_pattern must be a non-empty string when present")
            absent_pattern = None
        evidence = raw.get("evidence")
        if evidence is not None and not isinstance(evidence, str):
            errors.append(f"{where}.evidence must be a string when present")
        derivation_raw = raw.get("derivation")
        derivation = (
            parse_derivation(derivation_raw, where, errors)
            if derivation_raw is not None
            else None
        )

        expected_raw = raw.get("expected")
        if not isinstance(expected_raw, Mapping) or not expected_raw:
            errors.append(f"{where}.expected must be a non-empty mapping of roles")
            continue

        expectations: dict[str, Expectation] = {}
        for role, expectation_raw in expected_raw.items():
            if role not in ROLES:
                errors.append(f"{where}.expected has unknown role {role!r}; expected roles are {', '.join(ROLES)}")
                continue
            if not isinstance(expectation_raw, Mapping):
                errors.append(f"{where}.expected.{role} must be a mapping")
                continue
            answered = expectation_raw.get("answered")
            if not isinstance(answered, bool):
                errors.append(f"{where}.expected.{role}.answered must be true or false")
                continue

            docs = _string_list(expectation_raw.get("docs"), f"{where}.expected.{role}.docs",
                                errors, required=answered)
            contains = _string_list(expectation_raw.get("contains"),
                                    f"{where}.expected.{role}.contains", errors, required=answered)
            denied = expectation_raw.get("denied", False)
            if not isinstance(denied, bool):
                errors.append(f"{where}.expected.{role}.denied must be true or false when present")
                denied = False
            forbidden_docs = _string_list(expectation_raw.get("forbidden_docs"),
                                          f"{where}.expected.{role}.forbidden_docs",
                                          errors, required=denied)

            if answered and not docs:
                errors.append(f"{where}.expected.{role} is answered but names no docs")
            if answered and not contains:
                errors.append(f"{where}.expected.{role} is answered but has no contains values")
            if not answered and denied and not forbidden_docs:
                errors.append(f"{where}.expected.{role} is denied but names no forbidden_docs")

            expectations[role] = Expectation(
                role=role, answered=answered, docs=docs, contains=contains,
                denied=denied, forbidden_docs=forbidden_docs,
            )

        if not expectations:
            continue
        if category == "access_boundary" and set(expectations) != set(ROLES):
            errors.append(f"{where} is access_boundary and must declare both staff and executive expectations")
        unanswered = [expectation for expectation in expectations.values() if not expectation.answered]
        if unanswered and not any(expectation.denied for expectation in unanswered) and not absent_pattern:
            errors.append(f"{where} expects a refusal but has neither denied nor an absent_pattern")
        if any(expectation.answered for expectation in expectations.values()) and not source_quote:
            errors.append(f"{where} has an answered expectation but no source_quote")

        questions.append(Question(
            id=question_id,
            category=str(category or ""),
            question=str(text or ""),
            expected=expectations,
            source_quote=source_quote.strip() if isinstance(source_quote, str) else None,
            absent_pattern=absent_pattern.strip() if isinstance(absent_pattern, str) else None,
            evidence=evidence,
            derivation=derivation,
        ))

    return questions, errors


class ChunkIndex(Protocol):
    """The three reads validation needs. Tests provide a fake with the same methods."""

    def document_exists(self, doc_id: str) -> bool: ...

    def document_text(self, doc_id: str) -> str: ...

    def count_matching(self, pattern: str) -> int: ...

    def quote_in_index(self, fragment: str) -> bool: ...


def _quote_failures(question: Question, documents: Sequence[str], index: ChunkIndex,
                    doc_texts: Mapping[str, str]) -> tuple[list[str], list[str]]:
    """Check the source quote, returning (failures, normalised document texts used).

    A literal fragment must be in the named documents. An arithmetic quote is split at its last
    "="; the operands before it must be in the documents, and the expected values are checked
    against the whole quote by the caller. That is what makes the live cross-document quotes,
    which state a subtraction rather than a sentence, checkable without pretending the result is
    printed in a table.
    """
    failures: list[str] = []
    quote = question.source_quote or ""
    named = [normalise(doc_texts[doc]) for doc in documents if doc in doc_texts]

    for fragment in quote_fragments(quote):
        if "=" in fragment:
            operands = fragment.rsplit("=", 1)[0]
            parts = [part.strip() for part in ARITHMETIC_OPERATORS.split(operands) if part.strip()]
            if not parts:
                failures.append(f"source_quote arithmetic has no operands: {_shorten(fragment)!r}")
            for part in parts:
                needle = normalise(part)
                if not needle:
                    continue
                found = any(needle in text for text in named) if named else index.quote_in_index(part)
                if not found:
                    failures.append(f"source_quote operand {_shorten(part)!r} not found in the index")
            continue

        needle = normalise(fragment)
        if not needle:
            continue
        found = any(needle in text for text in named) if named else index.quote_in_index(fragment)
        if not found:
            failures.append(f"source_quote fragment {_shorten(fragment)!r} not found in the index")
    return failures, named


def _derivation_failures(derivation: Derivation, expected_values: Sequence[str],
                         documents: Sequence[str],
                         doc_texts: Mapping[str, str]) -> list[str]:
    """Check a derivation against the named documents and its own arithmetic.

    An empty list means the operands are indexed, the operation recomputes and the result is one of
    the expected values. That is what lets a computed answer stand in for a literal one.
    """
    failures: list[str] = []
    named = [normalise(doc_texts[doc]) for doc in documents if doc in doc_texts]
    for operand in derivation.operands:
        needle = normalise(operand)
        if not needle:
            failures.append(f"derivation operand {operand!r} has no alphanumerics to look up")
        elif named and not any(needle in text for text in named):
            failures.append(f"derivation operand {operand!r} is not in the named documents")

    expected_normalised = {normalise(value) for value in expected_values}
    if normalise(derivation.result) not in expected_normalised:
        failures.append(
            f"derivation.result {derivation.result!r} is not one of the expected contains values"
        )

    operation = derivation.operation
    numbers = [operand_number(operand) for operand in derivation.operands]
    if operation in ("sum", "difference"):
        if any(number is None for number in numbers):
            bad = [operand for operand, number in zip(derivation.operands, numbers)
                   if number is None]
            failures.append(f"derivation operands {bad!r} are not numeric")
        else:
            computed = sum(numbers) if operation == "sum" else numbers[0] - sum(numbers[1:])
            stated = operand_number(derivation.result)
            if stated is None:
                failures.append(f"derivation.result {derivation.result!r} is not numeric")
            elif computed != stated:
                failures.append(
                    f"derivation {operation} recomputes to {computed} but the result states {stated}"
                )
    elif operation == "latest":
        dates = [parse_date(operand) for operand in derivation.operands]
        if any(parsed is None for parsed in dates):
            bad = [operand for operand, parsed in zip(derivation.operands, dates) if parsed is None]
            failures.append(f"derivation operands {bad!r} are not dates")
        else:
            result_date = parse_date(derivation.result)
            if result_date is None:
                failures.append(f"derivation.result {derivation.result!r} is not a date")
            elif result_date not in dates:
                failures.append(
                    f"derivation.result {derivation.result!r} is not one of the operands"
                )
            elif result_date != max(dates):
                failures.append(
                    f"derivation.result {derivation.result!r} is not the latest operand date"
                )
    else:  # days-between or years-between; the parser guarantees exactly two operands
        dates = [parse_date(operand) for operand in derivation.operands]
        if any(parsed is None for parsed in dates):
            bad = [operand for operand, parsed in zip(derivation.operands, dates) if parsed is None]
            failures.append(f"derivation operands {bad!r} are not dates")
        else:
            stated = operand_number(derivation.result)
            if stated is None:
                failures.append(f"derivation.result {derivation.result!r} is not numeric")
            elif operation == "days-between":
                computed = abs((dates[0] - dates[1]).days)
                if stated != computed:
                    failures.append(
                        f"derivation days-between recomputes to {computed} days "
                        f"but the result states {stated}"
                    )
            else:
                computed = completed_years(dates[0], dates[1])
                if stated != stated.to_integral_value() or stated != computed:
                    failures.append(
                        f"derivation years-between recomputes to {computed} completed years "
                        f"but the result states {stated}"
                    )
    return failures


def validate_questions(questions: Sequence[Question], index: ChunkIndex,
                       duplicates: Iterable[str] = ()) -> list[QuestionReport]:
    """Run every check against an index, returning one report per question.

    This function knows nothing about psycopg. The index argument is the seam: the CLI passes
    PostgresIndex and the tests pass an in-memory fake.
    """
    duplicate_set = set(duplicates)
    reports: list[QuestionReport] = []

    for question in questions:
        report = QuestionReport(question_id=question.id)
        if question.id in duplicate_set:
            report.failures.append("duplicate id")

        documents: list[str] = []
        for expectation in question.expected.values():
            for doc_id in (*expectation.docs, *expectation.forbidden_docs):
                if doc_id not in documents:
                    documents.append(doc_id)

        missing: set[str] = set()
        for doc_id in documents:
            if not index.document_exists(doc_id):
                missing.add(doc_id)
                report.failures.append(f"document {doc_id!r} does not exist in chunk_index")

        doc_texts = {
            doc_id: index.document_text(doc_id)
            for doc_id in documents
            if doc_id not in missing
        }

        quote_norm = normalise(question.source_quote or "")
        expected_values = [
            value
            for expectation in question.expected.values()
            for value in expectation.contains
        ]

        # A valid derivation lets a computed value stand where the quote states only operands.
        # The literal path is untouched: with no derivation, derived_value stays empty.
        derivation_ok = False
        derived_value = ""
        if question.derivation is not None:
            derivation_problems = _derivation_failures(
                question.derivation, expected_values, documents, doc_texts
            )
            report.failures.extend(derivation_problems)
            derivation_ok = not derivation_problems
            if derivation_ok:
                derived_value = normalise(question.derivation.result)

        if question.source_quote:
            quote_problems, _ = _quote_failures(question, documents, index, doc_texts)
            report.failures.extend(quote_problems)
            for value in expected_values:
                if normalise(value) not in quote_norm:
                    if derivation_ok and normalise(value) == derived_value:
                        continue
                    report.failures.append(f"contains {value!r} is not inside source_quote")

        for role, expectation in question.expected.items():
            role_text = normalise(" ".join(doc_texts[doc_id] for doc_id in expectation.docs
                                           if doc_id in doc_texts))
            for value in expectation.contains:
                needle = normalise(value)
                # A value in a verified quote is grounded even when the document states the
                # operands and the answer is derived (the live cross-document questions). A
                # verified derivation grounds it too.
                if needle not in role_text and needle not in quote_norm:
                    if derivation_ok and needle == derived_value:
                        continue
                    report.failures.append(
                        f"expected.{role}.contains {value!r} is not in the named documents "
                        "or in source_quote"
                    )

        if question.absent_pattern:
            matches = index.count_matching(question.absent_pattern)
            if matches:
                report.failures.append(
                    f"absent_pattern {question.absent_pattern!r} matches {matches} chunk(s), "
                    "so the question is no longer unanswerable"
                )

        reports.append(report)

    return reports


class PostgresIndex:
    """The chunk_index reads, through psycopg 3.

    The connection is opened once; document text is cached because a question set names the same
    documents repeatedly and fetching a 10-K once is cheaper than once per question.
    """

    def __init__(self, dsn: str):
        import psycopg  # imported here so the pure checks run without the driver

        self._connection = psycopg.connect(dsn)
        self._texts: dict[str, str] = {}
        self._exists: dict[str, bool] = {}

    def close(self) -> None:
        self._connection.close()

    def document_exists(self, doc_id: str) -> bool:
        if doc_id not in self._exists:
            with self._connection.cursor() as cursor:
                cursor.execute("select exists(select 1 from chunk_index where doc_id = %s)", (doc_id,))
                row = cursor.fetchone()
            self._exists[doc_id] = bool(row and row[0])
        return self._exists[doc_id]

    def document_text(self, doc_id: str) -> str:
        if doc_id not in self._texts:
            with self._connection.cursor() as cursor:
                cursor.execute(
                    "select text from chunk_index where doc_id = %s "
                    "order by page_start nulls first, chunk_id",
                    (doc_id,),
                )
                self._texts[doc_id] = "\n".join(row[0] or "" for row in cursor.fetchall())
        return self._texts[doc_id]

    def count_matching(self, pattern: str) -> int:
        with self._connection.cursor() as cursor:
            cursor.execute(
                "select count(*) from chunk_index where strpos(lower(text), lower(%s)) > 0",
                (pattern,),
            )
            row = cursor.fetchone()
        return int(row[0]) if row else 0

    def quote_in_index(self, fragment: str) -> bool:
        with self._connection.cursor() as cursor:
            cursor.execute(
                "select exists(select 1 from chunk_index "
                "where strpos(regexp_replace(lower(text), '[^a-z0-9]', '', 'g'), %s) > 0)",
                (normalise(fragment),),
            )
            row = cursor.fetchone()
        return bool(row and row[0])


def load_document(path: str) -> Any:
    with open(path, encoding="utf-8") as handle:
        return yaml.safe_load(handle)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("questions", help="questions YAML to validate")
    parser.add_argument("--database-url", default=os.environ.get("DATABASE_URL", ""),
                        help="Postgres DSN; defaults to DATABASE_URL")
    args = parser.parse_args(argv)

    try:
        document = load_document(args.questions)
    except (OSError, yaml.YAMLError) as exc:
        print(f"cannot read {args.questions}: {exc}", file=sys.stderr)
        return 2

    questions, shape_errors = parse_document(document)
    for error in shape_errors:
        print(f"FAIL  {error}")
    if not questions:
        print("no valid questions to check", file=sys.stderr)
        return 1

    duplicates = duplicate_ids(questions)
    if not args.database_url:
        print("DATABASE_URL is not set and --database-url was not given", file=sys.stderr)
        return 2
    try:
        index = PostgresIndex(args.database_url)
    except Exception as exc:  # noqa: BLE001 - the driver's exception types vary with version
        print(f"cannot connect to the database: {exc}", file=sys.stderr)
        return 2

    try:
        reports = validate_questions(questions, index, duplicates=duplicates)
    finally:
        index.close()

    for report in reports:
        print(f"{'PASS' if report.passed else 'FAIL'}  {report.question_id}")
        for failure in report.failures:
            print(f"      - {failure}")

    failed = sum(1 for report in reports if not report.passed)
    passed = len(reports) - failed
    print()
    print(f"== {len(reports)} questions: {passed} passed, {failed} failed ==")
    if shape_errors:
        print(f"== {len(shape_errors)} schema error(s) ==")
    return 0 if failed == 0 and not shape_errors else 1


if __name__ == "__main__":
    raise SystemExit(main())