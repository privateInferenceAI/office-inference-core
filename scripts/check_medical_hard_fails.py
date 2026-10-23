#!/usr/bin/env python3
"""Release-blocking checks for the medical golden set.

The suite is mechanical: every question has one hard-fail category. A candidate answer is checked against that
category, not only against whether it looks plausible. The gate is deliberately small enough to run before a
release:

* fabricated_number: a numeric token in the answer must appear in the source quote or the expected answer;
* wrong_document: the cited documents must include the expected document;
* cross_role_leak: an executive document must not appear in a company-scoped answer;
* unanswerable: a refusal-expected question must be refused.

The self-test proves each category actually fails a seeded bad candidate. A gate that cannot fail is not a gate.

Usage:
    python3 scripts/check_medical_hard_fails.py --suite docs/eval/medical-data-questions.yaml --self-test
    python3 scripts/check_medical_hard_fails.py --suite ... --answers answers.jsonl
"""
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

import yaml

NUMBER = re.compile(r"\d[\d,]*(?:\.\d+)?")


def normal(value: str) -> str:
    return re.sub(r"[^a-z0-9]", "", str(value).lower())


def numbers(value: str) -> set[str]:
    return {match.replace(",", "") for match in NUMBER.findall(str(value))}


def expected_for(question: dict) -> dict:
    return question["expected"]["staff"]


def violations(question: dict, answer: str, docs: list[str], refused: bool = False) -> list[str]:
    expected = expected_for(question)
    category = question.get("hard_fail", "")
    problems = []
    if category == "fabricated_number":
        allowed = numbers(question.get("source_quote", "")) | numbers(" ".join(expected.get("contains") or []))
        for value in numbers(answer):
            if value not in allowed:
                problems.append(f"fabricated number {value}")
    elif category == "wrong_document":
        wanted = expected.get("docs") or []
        if not all(doc in docs for doc in wanted):
            problems.append("expected document missing from citations")
    elif category == "cross_role_leak":
        if any(str(doc).startswith("executive/") for doc in docs):
            problems.append("executive document leaked into a company answer")
    elif category == "unanswerable":
        if not refused:
            problems.append("an unanswerable question was answered")
    else:
        problems.append(f"unknown hard-fail category {category!r}")
    return problems


def clean_answers(suite: dict) -> list[dict]:
    answers = []
    for question in suite["questions"]:
        expected = expected_for(question)
        answers.append({"id": question["id"],
                        "answer": (expected.get("contains") or [""])[0],
                        "docs": list(expected.get("docs") or []),
                        "refused": False})
    return answers


def run_answers(suite: dict, answers: list[dict]) -> list[dict]:
    by_id = {question["id"]: question for question in suite["questions"]}
    findings = []
    for answer in answers:
        question = by_id.get(answer.get("id"))
        if question is None:
            findings.append({"id": answer.get("id"), "problem": "unknown question id"})
            continue
        problems = violations(question, answer.get("answer", ""), list(answer.get("docs") or []),
                              bool(answer.get("refused")))
        if problems:
            findings.append({"id": question["id"], "category": question.get("hard_fail"),
                             "problems": problems})
    return findings


def self_test(suite: dict) -> int:
    questions = suite["questions"]
    clean = clean_answers(suite)
    baseline = run_answers(suite, clean)
    if baseline:
        print(f"FAIL clean baseline: {baseline[:3]}")
        return 1
    print(f"PASS clean baseline: {len(clean)} answers, no findings")
    # One clean answer per category is mutated in the way that category exists to catch.
    targets = {
        "fabricated_number": {"answer": "The mileage rate is 99.99 USD per mile.", "docs": clean[0]["docs"]},
        "wrong_document": {"answer": "The answer is in the source.", "docs": ["company/unrelated.md"]},
        "cross_role_leak": {"answer": "The answer is in the source.", "docs": clean[0]["docs"] + ["executive/comp.md"]},
        "unanswerable": {"answer": "The answer is 42.", "docs": clean[0]["docs"], "refused": False},
    }
    for category, mutation in targets.items():
        target = next((question for question in questions if question.get("hard_fail") == category), None)
        if target is None and category == "unanswerable":
            # The sixty answerable medical questions do not contain a fabricated unanswerable case. The gate
            # still has to prove it blocks one, so the self-test constructs the case directly.
            synthetic = {"id": "SELF-U", "hard_fail": "unanswerable",
                         "expected": {"staff": {"answered": False, "docs": [], "contains": []}},
                         "source_quote": ""}
            problems = violations(synthetic, "The answer is 42.", [], refused=False)
            if not problems:
                print("FAIL seeded unanswerable was not caught")
                return 1
            print(f"PASS seeded unanswerable was caught: {problems[0]}")
            continue
        if target is None:
            print(f"FAIL no question with hard-fail category {category}")
            return 1
        candidate = next(item for item in clean if item["id"] == target["id"])
        candidate = {**candidate, **mutation}
        findings = run_answers(suite, [candidate])
        if not findings:
            print(f"FAIL seeded {category} was not caught")
            return 1
        print(f"PASS seeded {category} was caught: {findings[0]['problems'][0]}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="medical golden hard-fail gate")
    parser.add_argument("--suite", type=Path, default=Path("docs/eval/medical-data-questions.yaml"))
    parser.add_argument("--answers", type=Path)
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args(argv)
    suite = yaml.safe_load(args.suite.read_text())
    if args.self_test:
        return self_test(suite)
    if args.answers:
        answers = [json.loads(line) for line in args.answers.read_text().splitlines() if line.strip()]
        findings = run_answers(suite, answers)
    else:
        findings = run_answers(suite, clean_answers(suite))
    if findings:
        print(json.dumps(findings, indent=2))
        print(f"FAIL: {len(findings)} finding(s)")
        return 1
    print(f"PASS: {len(suite['questions'])} questions, no hard-fail finding")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
