#!/usr/bin/env python3
"""Check the corpus manifest and the question set against each other, and against the lockfile.

Two YAML files that reference each other drift silently: a question naming a document that was renamed
still parses, still looks right, and fails at retrieval time for a reason that reads like a retrieval
bug. This is the cheap check that stops that, in the spirit of the repository's other validators.

What it refuses:
  * a question drawing on a document id that is not in the manifest;
  * a question marked unanswerable that names a source document, or answerable that names none;
  * an answerable question whose reference answer is missing while the question set claims to be
    complete, because a question set that grades nothing is a smoke test wearing a grader's clothes;
  * a document in the lockfile whose recorded hash is absent, or a lockfile entry for a document that
    has left the manifest.

Run: python3 fixtures/corpus/validate_corpus.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import yaml

HERE = Path(__file__).resolve().parent


def main() -> int:
    problems: list[str] = []
    manifest = yaml.safe_load((HERE / "manifest.yaml").read_text())
    document_ids = {d["id"] for d in manifest["documents"]}

    lock = json.loads((HERE / "corpus.lock.json").read_text()) if (HERE / "corpus.lock.json").exists() else None
    if lock is None:
        problems.append("no corpus.lock.json: run fetch_corpus.py so a run can be reproduced")
    else:
        locked = set((lock.get("documents") or {}).keys())
        missing = document_ids - locked
        extra = locked - document_ids
        if missing:
            problems.append(f"in the manifest but never fetched: {sorted(missing)}")
        if extra:
            problems.append(f"in the lockfile but no longer in the manifest: {sorted(extra)}")
        if lock.get("corpus_version") != manifest.get("corpus_version"):
            problems.append(
                f"corpus_version differs: manifest {manifest.get('corpus_version')!r}, "
                f"lockfile {lock.get('corpus_version')!r}")
        for doc_id, entry in (lock.get("documents") or {}).items():
            if not entry.get("sha256"):
                problems.append(f"{doc_id}: locked without a sha256, so a changed source cannot be detected")

    questions_path = HERE / "questions.yaml"
    if not questions_path.exists():
        problems.append("no questions.yaml: the corpus has nothing to be measured against")
        questions = None
    else:
        questions = yaml.safe_load(questions_path.read_text())

    if questions:
        if questions.get("corpus_version") != manifest.get("corpus_version"):
            problems.append(
                f"the question set names corpus {questions.get('corpus_version')!r} while the manifest "
                f"says {manifest.get('corpus_version')!r}")
        seen: set[str] = set()
        for question in questions["questions"]:
            qid = question["id"]
            if qid in seen:
                problems.append(f"{qid}: duplicate id")
            seen.add(qid)

            for doc_id in question.get("draws_on") or []:
                if doc_id not in document_ids:
                    problems.append(f"{qid}: draws on {doc_id!r}, which is not in the manifest")

            expected = question["expected"]
            answerable = expected["answerable"]
            source = expected.get("source_document")
            if answerable and not source:
                problems.append(f"{qid}: marked answerable but names no source document")
            if not answerable and source:
                problems.append(f"{qid}: marked unanswerable but names {source!r} as its source")
            if source not in (None, "both") and source not in document_ids:
                problems.append(f"{qid}: source {source!r} is not in the manifest")

        # A question set is allowed to be a draft with answers pending, but not to claim otherwise.
        status = str(questions.get("status") or "")
        pending = "pending" in status.lower()
        unanswered = [q["id"] for q in questions["questions"]
                      if q["expected"]["answerable"]
                      and not (q["expected"]["references"] or {}).get("reference_answer")]
        if unanswered and not pending:
            problems.append(
                f"{len(unanswered)} answerable questions have no reference answer while the set claims "
                f"to be complete: {unanswered}")

    total = len(questions["questions"]) if questions else 0
    answerable = sum(1 for q in (questions or {}).get("questions", []) if q["expected"]["answerable"])
    print(f"corpus: {len(document_ids)} documents, corpus_version {manifest.get('corpus_version')!r}")
    if questions:
        print(f"questions: {total}, {answerable} answerable and {total - answerable} that must be refused")
        print(f"status: {questions.get('status')}")

    if problems:
        print()
        print(f"{len(problems)} problems:")
        for problem in problems:
            print(f"  - {problem}")
        return 1
    print()
    print("both files agree with each other and with the lockfile")
    return 0


if __name__ == "__main__":
    sys.exit(main())
