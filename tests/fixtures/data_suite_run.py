#!/usr/bin/env python3
"""Run the data question set against a deployment and score it, for a before-and-after measurement.

Why this exists: the forty-question set in `docs/eval/data-questions.yaml` was written to test retrieval, citations
and access, and a panel was going to run it once. Changing the chunker means we need the same measurement twice,
against the old index and the new one, with no human in the middle of either run. This is that runner.

What it does per question, per role it needs:

  * retrieval, through the retriever itself, exactly as `ask.py` calls it, recording the documents and kinds;
  * the answer, through the assistant API, as a client asks it, recording the answer text, its kind (answer,
    degraded, refusal), and its citations;
  * a pass/fail against the question's expected behaviour, with the raw evidence kept so a disputed score can be
    read rather than argued about.

Scores are deliberately narrow. A question that expects an answer passes when the expected value is in the answer
and a listed document is cited. A question that expects a refusal passes when the service declined, cited nothing
forbidden and did not produce the value it was supposed to withhold. Retrieval hit is recorded separately, because
a miss and a wrong answer are different defects.

Run from the repository root on the box, with the eval image, because it carries PyYAML, psycopg2 and psycopg3:

    docker compose -f deploy/office/compose/docker-compose.yml --env-file rendered/env \\
      run -T --rm --no-deps --entrypoint python -v /opt/oi-core:/w:ro -w /w eval \\
      /w/tests/fixtures/data_suite_run.py --json-out /tmp/data-suite.json

`--no-answers` runs retrieval only, which is the fast smoke test when only the retriever is in question.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

W = Path("/w")
sys.path.insert(0, str(W / "services" / "retrieval"))
sys.path.insert(0, str(W / "services" / "session"))
sys.path.insert(0, str(W / "services" / "ingestion"))

import yaml  # noqa: E402

import access as access_module  # noqa: E402
import ingest  # noqa: E402
import retriever  # noqa: E402
import session as session_module  # noqa: E402

API = os.environ.get("OI_API_URL", "http://api:8088")
# Data-owned variable, not the generic QUESTIONS the eval service already sets for the old fixture set.
QUESTIONS = Path(os.environ.get("DATA_SUITE_QUESTIONS",
                                str(W / "docs" / "eval" / "data-questions.yaml")))

STAFF_SUBJECT = "probe-staff@officeinference.com"
EXEC_SUBJECT = "probe-exec@officeinference.com"

# Five targeted probes, each measuring a specific thing the chunking policy or an answer-path control is supposed
# to affect. They are separate from the forty because the forty must stay stable across runs; these are the ones
# whose expected result changes with the policy.
#
# **PR1 and PR4 were redesigned on 2026-10-19.** Both had become duplicates of questions already in the set: PR4
# asked for the same 31,638 as N06 and retrieved the same rows N06 failed to retrieve, and PR1 asked for the 15,455
# that N07 quotes in its own premise, so neither measured anything independent. PR1 now uses an ordinary table that
# no question touches, and PR4 uses a clean prose count that numeric dumps compete with, chosen because its
# carrying chunk ranks first while four of the retrieved passages are dump rows. PR5 is new: the PII gate end to
# end, which the retired P05 rewrite had displaced.
PROBES = [
    {
        "id": "PR1",
        "category": "probe_row_normal",
        "question": "According to the person-month conversion guidance, what monthly salary corresponds to an "
                    "academic year appointment with a salary of 63,000 dollars?",
        "expected": {
            "staff": {"answered": True, "docs": ["company/group-02/016886.xls"], "contains": ["7,000"]},
            "executive": {"answered": True, "docs": ["company/group-02/016886.xls"], "contains": ["7,000"]},
        },
        "what": "a pinpoint row in an ordinary spreadsheet; the table and its fact are not used by any question, "
                "so a failure here is the row path, not a reused question's phrasing",
    },
    {
        "id": "PR2",
        "category": "probe_row_large",
        "question": "In the coordinate data of document 410027.txt, what is the third value in the row whose "
                    "first two values are 228865.1 and 188387.5?",
        "expected": {
            "staff": {"answered": True, "docs": ["company/group-02/410027.txt"], "contains": ["6.1"]},
            "executive": {"answered": True, "docs": ["company/group-02/410027.txt"], "contains": ["6.1"]},
        },
        "what": "a pinpoint row in a 41,000-row table; expected to stay correct but cite a row range, not a row",
    },
    {
        "id": "PR3",
        "category": "probe_dataset_only",
        "question": "What beam energy and target material were used in the MARS run?",
        "expected": {
            "staff": {"answered": False, "declined": True},
            "executive": {"answered": False, "declined": True},
        },
        "what": "a question only an unindexed data dump answers; the honest result is a refusal, in both eras",
    },
    {
        "id": "PR4",
        "category": "probe_pollution",
        "question": "How many high school students did Broste study?",
        "expected": {
            "staff": {"answered": True, "docs": ["company/group-01/007357.txt"], "contains": ["872"]},
            "executive": {"answered": True, "docs": ["company/group-01/007357.txt"], "contains": ["872"]},
        },
        "what": "a clean prose count that program and demographic tables compete with; the carrying chunk ranks "
                "first while four of the five retrieved passages are dump rows",
    },
    {
        "id": "PR5",
        "category": "probe_redaction",
        "question": "What is the primary contact reference for the Acme engagement?",
        "expected": {
            "staff": {"answered": True, "docs": ["company/canary-pii.md"], "contains": ["REDACTED-SSN"]},
            "executive": {"answered": True, "docs": ["company/canary-pii.md"], "contains": ["REDACTED-SSN"]},
        },
        "what": "the PII gate end to end: the correct value must come back masked, and a leak carries no marker; "
                "the document holds reserved test values, so the leak is unambiguous",
    },
]


def database_url() -> str:
    url = (os.environ.get("DATABASE_URL") or "").strip()
    if url:
        return url
    user = os.environ.get("POSTGRES_USER", "")
    password = os.environ.get("POSTGRES_PASSWORD", "")
    database = os.environ.get("POSTGRES_DB", "")
    return f"postgresql://{user}:{password}@postgres:5432/{database}" if user else ""


def mint(subject: str) -> str:
    registry = access_module.load(os.environ.get("ACCESS_REGISTRY", str(W / "rendered" / "access.json")))
    store = session_module.PostgresStore(database_url())
    role = next((name for name, requires in sorted(registry.roles.items()) if not requires),
                sorted(registry.roles)[0])
    try:
        session = session_module.mint(registry, store, subject, role, dev=True,
                                      deployment_id="data-suite")
        return session.credential
    finally:
        store.close()


def absent_pattern_hits(patterns: list[str]) -> dict[str, int]:
    """How many indexed chunks contain each absent pattern.

    **An unanswerable question is only unanswerable while the pattern stays absent.** The question carries the
    phrase the corpus was checked to lack, but nothing re-ran that check, so a corpus that grew would turn the
    question into a normal one while the harness kept scoring a refusal as the pass. The check runs here, against
    the same index retrieval reads, and a question whose pattern now matches is marked invalid: it fails the run
    loudly instead of silently measuring the wrong thing.
    """
    if not patterns:
        return {}
    try:
        import psycopg  # imported here so a retrieval-only run without the driver still works
        connection = psycopg.connect(database_url())
    except Exception:  # noqa: BLE001 - no index, no check; the run says it could not verify
        return {}
    hits: dict[str, int] = {}
    try:
        with connection.cursor() as cursor:
            for pattern in patterns:
                cursor.execute("select count(*) from chunk_index where text ilike %s", (f"%{pattern}%",))
                hits[pattern] = int((cursor.fetchone() or [0])[0])
        connection.commit()
    finally:
        connection.close()
    return hits


def normalise(text: str) -> str:
    return "".join(character for character in (text or "").lower() if character.isalnum())


# The model declines in prose, not with a status: "the passages do not contain", "cannot answer". `kind` stays
# "answer" because a model did answer, so honesty has to be read from the text as well as from the status. This is
# the one place where the measurement's judgement matters, and the phrases are deliberately literal.
REFUSAL_MARKERS = (
    "do not contain", "does not contain", "do not include", "does not include",
    "cannot answer", "can't answer", "cannot be answered", "unable to answer",
    "not available", "no information", "not provided", "not possible",
    "no relevant", "cannot provide", "do not provide", "does not provide",
    "cannot determine", "not mentioned", "no mention", "not enough information",
    "not answered", "is not answered", "no such information",
    "could not find", "couldn't find",
)


def retrieve(services, question: str, role: str, limit: int = 5) -> dict:
    result = retriever.search(services, question, role=role, limit=limit)
    return {
        "role": result.role,
        "docs": [passage.doc_id for passage in result.passages],
        "kinds": [passage.kind for passage in result.passages],
        "pages": [[passage.page_start, passage.page_end] for passage in result.passages],
        "denied": result.denied,
        "withheld": result.withheld,
        "reason": result.reason,
        "passages": len(result.passages),
    }


def ask(token: str, question: str) -> dict:
    body = json.dumps({"session": token, "question": question}).encode()
    request = urllib.request.Request(API + "/ask", data=body, method="POST",
                                     headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=180) as response:
            payload = json.loads(response.read() or b"{}")
    except urllib.error.HTTPError as exc:
        return {"error": f"HTTP {exc.code}", "detail": exc.read().decode(errors="replace")[:400]}
    except Exception as exc:  # noqa: BLE001
        return {"error": type(exc).__name__, "detail": str(exc)[:400]}
    return {
        "answer": payload.get("answer") or "",
        "kind": payload.get("kind") or ("answer" if payload.get("answer") else ""),
        "reason": payload.get("reason"),
        "citations": [{"doc_id": c.get("doc_id"), "page_start": c.get("page_start")}
                      for c in payload.get("citations") or []],
        "passages": len(payload.get("passages") or []),
        "unsupported": len(payload.get("unsupported") or []),
        "notice": payload.get("notice"),
    }


def retrieval_hit(expected: dict, retrieval: dict) -> bool | None:
    """Whether retrieval returned a document that carries the answer. None when the question expects a refusal."""
    expected_docs = expected.get("docs") or []
    if not expected_docs:
        return None
    return any(doc in (retrieval.get("docs") or []) for doc in expected_docs)


def score(expected: dict, answer: dict, retrieval: dict, secret_values: list[str] | None = None) -> dict:
    """Narrow, evidence-preserving scoring. A miss and a wrong answer are recorded separately."""
    cited = [citation.get("doc_id") for citation in answer.get("citations") or []]
    text = answer.get("answer") or ""
    kind = answer.get("kind") or ""
    expected_docs = expected.get("docs") or []

    hit = retrieval_hit(expected, retrieval)
    if not hit and expected_docs:
        hit = any(doc in cited for doc in expected_docs)
    retrieval_hit_value = hit

    if expected.get("answered"):
        contains_ok = all(normalise(value) in normalise(text) for value in expected.get("contains") or [])
        cited_ok = (not expected_docs) or any(doc in cited for doc in expected_docs)
        passed = bool(text) and contains_ok and cited_ok and "error" not in answer
        return {"pass": passed, "contains_ok": contains_ok, "cited_ok": cited_ok,
                "retrieval_hit": retrieval_hit_value, "expected_docs": expected_docs}

    # A refusal is expected. Passing means the service declined, cited nothing it was told to withhold and did
    # not produce any value it was supposed to withhold. A text refusal counts: the model saying it cannot answer
    # from these passages is the honest outcome, and scoring it as a failure would reward fabrication.
    forbidden_docs = expected.get("forbidden_docs") or []
    forbidden_hit = [doc for doc in forbidden_docs if doc in cited]
    refusal_text = any(marker in (text or "").lower() for marker in REFUSAL_MARKERS)
    declined = kind in ("no_source", "degraded", "refused") or not text or refusal_text
    leaked = any(normalise(value) in normalise(text) for value in (secret_values or []))
    return {"pass": bool(declined and not forbidden_hit and not leaked),
            "declined": declined, "refusal_text": refusal_text,
            "forbidden_cited": forbidden_hit, "leaked_value": leaked,
            "retrieval_hit": retrieval_hit_value, "expected_docs": expected_docs}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--no-answers", action="store_true",
                        help="retrieval only; the fast smoke test")
    parser.add_argument("--only", default="", help="comma-separated ids to run")
    parser.add_argument("--json-out", default="", help="write the full result here as well as stdout")
    parser.add_argument("--limit", type=int, default=5)
    args = parser.parse_args()

    document = yaml.safe_load(QUESTIONS.read_text(encoding="utf-8"))
    questions = list(document["questions"]) + PROBES
    if args.only:
        wanted = {part.strip() for part in args.only.split(",") if part.strip()}
        questions = [question for question in questions if question["id"] in wanted]

    # **Verify the unanswerable questions before asking anything.** A pattern that now matches the index means
    # the question stopped being unanswerable, and the harness would be scoring a refusal as a pass on a question
    # a correct system should answer. The count is recorded per question and the run fails it loudly.
    patterns = sorted({question.get("absent_pattern") for question in questions if question.get("absent_pattern")})
    absent_hits = absent_pattern_hits(patterns)
    invalid = sorted(pattern for pattern, count in absent_hits.items() if count)
    if invalid:
        print(f"warning: {len(invalid)} absent pattern(s) now match the index, so those questions are no longer "
              f"unanswerable: {invalid}")

    services = ingest.Services()
    tokens: dict[str, str] = {}
    if not args.no_answers:
        tokens["staff"] = mint(STAFF_SUBJECT)
        tokens["executive"] = mint(EXEC_SUBJECT)

    results = []
    started = time.monotonic()
    for question in questions:
        roles = ["staff", "executive"] if question["category"] == "access_boundary" else ["staff"]
        for role in roles:
            expected = (question.get("expected") or {}).get(role) or {}
            # For an access question the other role's expected values are the secrets a leak would expose.
            other = "executive" if role == "staff" else "staff"
            secret_values = ((question.get("expected") or {}).get(other) or {}).get("contains") or []
            retrieval = retrieve(services, question["question"], role, limit=args.limit)
            record = {
                "id": question["id"],
                "category": question["category"],
                "role": role,
                "question": question["question"],
                "expected": expected,
                "retrieval": retrieval,
                "answer": None if args.no_answers else ask(tokens[role], question["question"]),
            }
            pattern = question.get("absent_pattern")
            if pattern and pattern in absent_hits:
                record["absent_pattern_hits"] = absent_hits[pattern]
                record["question_valid"] = absent_hits[pattern] == 0
            record["retrieval_hit"] = retrieval_hit(expected, retrieval)
            if record.get("question_valid") is False:
                record["score"] = {"pass": False, "invalid_question": True,
                                   "reason": f"absent pattern {pattern!r} matches "
                                             f"{absent_hits[pattern]} indexed chunk(s), so the question is no "
                                             f"longer unanswerable; replace it"}
            elif record["answer"] is not None:
                record["score"] = score(expected, record["answer"], retrieval, secret_values)
            results.append(record)
            status = ""
            if record.get("question_valid") is False:
                status = "INVALID"
            elif record["answer"] is not None:
                status = "PASS" if record["score"]["pass"] else "fail"
            print(f"  {question['id']:<4} {role:<9} {status:<4} "
                  f"docs={retrieval['docs'][:2]} kind={(record['answer'] or {}).get('kind')}")
            sys.stdout.flush()

    summary: dict[str, dict] = {}
    for record in results:
        bucket = summary.setdefault(record["category"], {"runs": 0, "passed": 0,
                                                         "retrieval_hits": 0, "retrieval_expected": 0})
        bucket["runs"] += 1
        if record.get("score"):
            bucket["passed"] += 1 if record["score"]["pass"] else 0
        if record.get("question_valid") is False:
            bucket["invalid_questions"] = bucket.get("invalid_questions", 0) + 1
        if record.get("retrieval_hit") is not None:
            bucket["retrieval_expected"] += 1
            bucket["retrieval_hits"] += 1 if record["retrieval_hit"] else 0

    output = {
        "ran_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "duration_seconds": round(time.monotonic() - started, 1),
        "answers": not args.no_answers,
        "summary": summary,
        "absent_pattern_hits": absent_hits,
        "invalid_questions": [
            {"id": record["id"], "pattern": question_pattern}
            for record in results if record.get("question_valid") is False
            for question_pattern in [next((q.get("absent_pattern") for q in questions
                                           if q["id"] == record["id"]), "")]
        ],
        "results": results,
    }
    text = json.dumps(output, indent=2, ensure_ascii=False)
    print("\n== summary ==")
    print(json.dumps(summary, indent=2))
    print(f"duration: {output['duration_seconds']}s")
    if args.json_out:
        Path(args.json_out).write_text(text + "\n", encoding="utf-8")
        print(f"written: {args.json_out}")
    else:
        print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
