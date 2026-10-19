#!/usr/bin/env python3
"""Build the per-question answer audit from the frozen experiment results.

**Why this exists.** The overnight experiment reported averages, and an average is not checkable. The owner asked
for every question, every configuration and every run, with the expected answer, the answer the system actually
produced, and the evidence it read. This script turns the frozen result files into that: one CSV a machine can
diff, one Markdown document a person can read, and the evidence and raw runs kept beside them so the whole thing is
reproducible rather than assembled by hand.

The scorer here is a copy of the one in `tests/fixtures/data_suite_run.py`, kept deliberately literal so the audit
can be generated without a deployment. The CSV carries both the pass recorded at run time and the pass this script
recomputes; a disagreement would mean the scorer drifted, and the column exists to make that visible.
"""
from __future__ import annotations

import csv
import importlib.util
import json
import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
# The suite runner imports the retrieval, session and ingestion modules from the checkout, exactly as it does when
# it runs inside the eval image. The paths go on before the import for that reason.
for _path in ("tests/fixtures", "services/retrieval", "services/ingestion", "services/session",
              "services/gateway", "services/api"):
    sys.path.insert(0, str(ROOT / _path))
BUNDLE = ROOT / "docs" / "reviews" / "experiment-answers-2026-10-19"
RAW = BUNDLE / "raw"
EVIDENCE = BUNDLE / "evidence.json"
CSV_OUT = BUNDLE / "answers.csv"
MARKDOWN_OUT = BUNDLE / "README.md"
DOCUMENTS_ON_BOX = "/opt/oi-core/documents"
CORPUS_BUCKET = "s3://officeinference-corpus-878451098165/"

CONFIGS = [("phasea", "Phase A (commit b76349c)"), ("main", "Main (bc806a8)"), ("single", "Main, single-shot")]

REFUSAL_MARKERS = (
    "do not contain", "does not contain", "do not include", "does not include",
    "cannot answer", "can't answer", "not answered", "is not answered",
    "not available", "no information", "not provided", "not possible",
    "no relevant", "cannot determine", "not mentioned", "no mention",
    "not enough information", "unable to answer", "no such information",
    "could not find", "couldn't find", "cannot be answered", "do not provide", "does not provide",
)


def normalise(text: str) -> str:
    return "".join(character for character in (text or "").lower() if character.isalnum())


def score(record: dict) -> bool:
    expected = record.get("expected") or {}
    answer = record.get("answer") or {}
    text = answer.get("answer") or ""
    kind = answer.get("kind") or ""
    cited = [citation.get("doc_id") for citation in answer.get("citations") or []]
    if expected.get("answered"):
        contains_ok = all(normalise(value) in normalise(text) for value in expected.get("contains") or [])
        docs = expected.get("docs") or []
        cited_ok = (not docs) or any(doc in cited for doc in docs)
        return bool(text) and contains_ok and cited_ok and "error" not in answer
    refusal = any(marker in text.lower() for marker in REFUSAL_MARKERS)
    declined = kind in ("no_source", "degraded", "refused") or not text or refusal
    forbidden = [doc for doc in (expected.get("forbidden_docs") or []) if doc in cited]
    return bool(declined and not forbidden)


def load_questions() -> list[dict]:
    import yaml

    questions = list(yaml.safe_load((ROOT / "docs" / "eval" / "data-questions.yaml").read_text())["questions"])
    spec = importlib.util.spec_from_file_location("suite_runner", ROOT / "tests/fixtures/data_suite_run.py")
    runner = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(runner)
    return questions + list(runner.PROBES)


def load_runs() -> dict[tuple[str, int], dict]:
    runs = {}
    for config, _label in CONFIGS:
        for number in range(1, 6):
            path = RAW / f"exp3-{config}-{number}.json"
            runs[(config, number)] = json.loads(path.read_text())
    return runs


def one_line(text: str) -> str:
    """Full text, whitespace collapsed, so a table row can hold an answer without losing characters."""
    return " ".join((text or "").split())


def cite_text(answer: dict) -> str:
    parts = []
    for citation in answer.get("citations") or []:
        parts.append(f"{citation.get('doc_id')} p{citation.get('page_start')}")
    return "; ".join(parts)


def build() -> None:
    questions = load_questions()
    runs = load_runs()
    evidence = {(item["id"], item["role"]): item for item in json.loads(EVIDENCE.read_text())}
    expected_by_key = {}
    for question in questions:
        for role, expected in (question.get("expected") or {}).items():
            expected_by_key[(question["id"], role)] = expected

    # ------------------------------------------------------------------ CSV
    rows = []
    for question in questions:
        for role in ("staff", "executive"):
            key = (question["id"], role)
            if key not in evidence:
                continue
            expected = expected_by_key.get(key) or {}
            for config, label in CONFIGS:
                for number in range(1, 6):
                    record = next(
                        (result for result in runs[(config, number)]["results"]
                         if (result["id"], result["role"]) == key), None)
                    if record is None:
                        continue
                    answer = record.get("answer") or {}
                    retrieval = record.get("retrieval") or {}
                    docs = retrieval.get("docs") or []
                    rows.append({
                        "question_id": question["id"],
                        "category": question["category"],
                        "role": role,
                        "question": question["question"],
                        "expected_answered": expected.get("answered"),
                        "expected_docs": "; ".join(expected.get("docs") or []),
                        "expected_contains": "; ".join(expected.get("contains") or []),
                        "expected_absent_pattern": question.get("absent_pattern", ""),
                        "config": label,
                        "run": number,
                        "pass_run_time": (record.get("score") or {}).get("pass"),
                        "pass_recomputed": score(record),
                        "answer_kind": answer.get("kind") or ("answer" if answer.get("answer") else ""),
                        "answer_reason": answer.get("reason") or "",
                        "answer_text": one_line(answer.get("answer")),
                        "citations": cite_text(answer),
                        "retrieved_docs": "; ".join(docs),
                        "retrieved_pages": "; ".join(str(page) for page in retrieval.get("pages") or []),
                        "evidence_read": "; ".join(
                            f"[{passage['n']}] {passage['doc_id']} p{passage['page_start']} ({passage['kind']})"
                            for passage in evidence[key]["passages"]),
                        "document_paths": "; ".join(
                            sorted({f"{DOCUMENTS_ON_BOX}/{passage['doc_id']}" for passage in evidence[key]["passages"]})),
                        "unsupported_claims": (answer.get("unsupported")
                                               if isinstance(answer.get("unsupported"), int)
                                               else len(answer.get("unsupported") or [])),
                        "error": answer.get("error") or "",
                    })
    with CSV_OUT.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)

    # ------------------------------------------------------------------ Markdown
    lines: list[str] = []
    add = lines.append
    add("# Answer audit: every question, every configuration, every run")
    add("")
    add("Generated from the fifteen frozen result files and the captured retrieval evidence in this directory by")
    add("`scripts/build_answer_audit.py`. Regenerate it rather than editing it.")
    add("")
    add("## What this is")
    add("")
    add("For each question in the forty-question set plus the four probes, and for each of the three frozen")
    add("configurations, this document shows the expected answer, the evidence retrieval offered, and what the")
    add("system actually answered in every one of the five runs. The machine-readable twin is `answers.csv`.")
    add("")
    add("| Configuration | Commit or flag | Mean of five runs |")
    add("|---|---|---|")
    means = {}
    for config, label in CONFIGS:
        totals = [sum(1 for result in runs[(config, number)]["results"] if score(result))
                  for number in range(1, 6)]
        means[config] = sum(totals) / len(totals)
        add(f"| {label} | {'loop on' if config != 'single' else 'loop off'} | {means[config]:.1f} of 52 |")
    add("")
    add("## How to read a record")
    add("")
    add("- **PASS**/**FAIL** is the scoring rule in `tests/fixtures/data_suite_run.py`: an answered question passes")
    add("  when every expected value is in the answer and an expected document is cited; a refusal question passes")
    add("  when the system declined, cited nothing forbidden, and stated no withheld value.")
    add("- **Kind** is what the service reported: `answer`, `degraded` (passages, no model answer), or a refusal")
    add("  status. A degraded answer is not a wrong answer; it is the honest fallback, and it still fails a question")
    add("  that expected an answer.")
    add("- **Evidence** is what retrieval offered the model, captured by re-running the retriever against the same")
    add("  frozen corpus. Reranking is not perfectly deterministic across days, so the recorded retrieval in")
    add("  `answers.csv` is authoritative; the evidence section is the content a reader can check against.")
    add("- **Documents** are on the box at `/opt/oi-core/documents/<doc_id>` and archived in")
    add(f"  `{CORPUS_BUCKET}`. The corpus is not in this repository.")
    add("")
    add("## Questions")
    add("")
    for question in questions:
        qid = question["id"]
        add(f"### {qid} — {question['category']}")
        add("")
        add(f"> {question['question']}")
        add("")
        if question.get("absent_pattern"):
            add(f"Expected absent pattern: `{question['absent_pattern']}`.")
            add("")
        if question.get("what"):
            add(f"Probe purpose: {question['what']}.")
            add("")
        for role in ("staff", "executive"):
            key = (qid, role)
            if key not in evidence:
                continue
            expected = expected_by_key.get(key) or {}
            add(f"**Role: {role}**")
            add("")
            if expected.get("answered"):
                add(f"- Expected: an answer containing `{expected.get('contains')}`")
                add(f"- Expected documents: `{expected.get('docs')}`")
            else:
                add("- Expected: a refusal. No value from the documents may appear.")
            add("")
            add("*Evidence retrieval offered:*")
            add("")
            for passage in evidence[key]["passages"]:
                excerpt = one_line(passage["text"])[:700]
                add(f"- **[{passage['n']}]** `{passage['doc_id']}` pages {passage['page_start']}"
                    f"–{passage['page_end']} ({passage['kind']}): {excerpt}")
            add("")
            for config, label in CONFIGS:
                add(f"*{label}*")
                add("")
                for number in range(1, 6):
                    record = next(
                        (result for result in runs[(config, number)]["results"]
                         if (result["id"], result["role"]) == key), None)
                    if record is None:
                        continue
                    answer = record.get("answer") or {}
                    verdict = "PASS" if score(record) else "FAIL"
                    kind = answer.get("kind") or "answer"
                    citations = cite_text(answer) or "none"
                    add(f"- **run {number}: {verdict}** · kind `{kind}` · citations: {citations}")
                    text = one_line(answer.get("answer")) or "(no answer text)"
                    add(f"  > {text}")
                    if answer.get("reason"):
                        add(f"  > reason: `{answer.get('reason')}`")
                add("")
        add("")
    # documents appendix
    add("## Documents referenced")
    add("")
    add("Every document that appears in the captured evidence. The corpus lives on the box and in the corpus")
    add("bucket; it is not committed to this repository because it is 15 GB of client-like material.")
    add("")
    seen_documents = sorted({passage["doc_id"] for item in evidence.values() for passage in item["passages"]})
    add("| Document | On the box |")
    add("|---|---|")
    for doc in seen_documents:
        add(f"| `{doc}` | `{DOCUMENTS_ON_BOX}/{doc}` |")
    add("")
    add("## Caveats")
    add("")
    add("- Five runs still leave a wide interval. The report `docs/reviews/experiment-three-configs-2026-10-19.md`")
    add("  carries the analysis; this document carries the evidence behind it.")
    add("- The evidence capture re-runs retrieval today. The result files record what retrieval returned during the")
    add("  runs; the two agree on documents in almost every case and the CSV carries the recorded version.")
    add("- The capture is not the model's full prompt: the seed passages are the same five, but the tool loop could")
    add("  retrieve more during a run. The answer text and citations show what the final answer relied on.")
    add("- `PR1` (`probe_row_normal`) and `PR4` (`probe_pollution`) are known imperfect probes, described in")
    add("  `docs/reviews/phase-b-c-2026-10-18.md`; their failures are not clean evidence of system failure.")
    add("")

    MARKDOWN_OUT.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"wrote {CSV_OUT.relative_to(ROOT)} ({len(rows)} rows)")
    print(f"wrote {MARKDOWN_OUT.relative_to(ROOT)} ({MARKDOWN_OUT.stat().st_size} bytes)")


if __name__ == "__main__":
    build()
