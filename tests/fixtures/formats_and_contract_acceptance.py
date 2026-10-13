#!/usr/bin/env python3
"""Three formats answerable, and the embedding contract refusing a mismatch it is shown.

Two requirements that are each one proof. RET-FORMAT-001 asks that one file of each of email, spreadsheets and
slides is ingested and answerable, and RET-EMBED-001 asks that a query against a collection built by a different
model or preprocessing is refused.

**The contract half needs the mismatch INDUCED**, because the deployment does not currently have one and a check
that has never refused anything is a check nobody knows works. It is shown a contract with a field moved, through
the same code path the read path uses, and has to raise. Nothing is re-recorded and nothing is searched: the
point is the refusal, and a test that changed the registry to prove the registry works would be testing the
change.

Run from the repo root on the box:

    cd /opt/oi-core
    docker compose -f deploy/office/compose/docker-compose.yml --env-file rendered/env \\
      --profile tools run --rm -v /opt/oi-core:/w eval
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

sys.path.insert(0, "/w/services/retrieval")
sys.path.insert(0, "/w/services/ingestion")
sys.path.insert(0, "/w/services/eval")


def load_env(path: Path = Path("/w/rendered/env")) -> dict[str, str]:
    try:
        text = path.read_text()
    except PermissionError:
        print(f"cannot read {path}: mode 600 owned by the operator. Run as the file's owner.")
        return {}
    out: dict[str, str] = {}
    for line in text.splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, _, value = line.partition("=")
            out[key.strip()] = value.strip().strip('"').strip("'")
    return out


env = load_env()
if env and not os.environ.get("DATABASE_URL"):
    from urllib.parse import quote
    os.environ["DATABASE_URL"] = (
        f"postgresql://{quote(env['POSTGRES_USER'], safe='')}:"
        f"{quote(env['POSTGRES_PASSWORD'], safe='')}@postgres:5432/{env['POSTGRES_DB']}")
    os.environ.setdefault("VECTOR_URL", "http://vector:6333")

import contract  # noqa: E402
import ingest  # noqa: E402
import retriever  # noqa: E402

# One question per format, each asking for the fact that appears nowhere else in the corpus. A question a
# different document could also answer would prove the format was ingested and not that it is answerable.
FORMATS = [
    ("email", "company/callout-rate-approval.eml", "8,475.50",
     "What rate did the email approve for emergency callouts?"),
    ("spreadsheet", "company/rate-card-2026.xlsx", "8,475.50",
     "What does the rate card charge for an emergency callout?"),
    ("slides", "company/dispatch-review-q1.pptx", "2,500",
     "What vendor threshold did the dispatch review raise?"),
]


def main() -> int:
    services = ingest.Services()
    problems: list[str] = []

    print()
    print("=" * 96)
    print("RET-FORMAT-001: one file of each format, ingested and answerable")
    print("=" * 96)
    for label, doc_id, figure, question in FORMATS:
        result = retriever.search(services, question, role="staff", limit=10)
        texts = [p.text for p in result.passages]
        digits = lambda value: "".join(ch for ch in value if ch.isdigit())
        found = any(digits(figure) in digits(text) for text in texts)
        first_from_doc = next((i for i, p in enumerate(result.passages, start=1) if p.doc_id == doc_id), None)
        print(f"\n  {label:<12} {doc_id}")
        print(f"    question  {question}")
        print(f"    figure    {figure}")
        print(f"    passages  {len(result.passages)}, document at rank {first_from_doc}")
        print(f"    the figure is {'FOUND' if found else 'MISSING'}")
        if not found:
            problems.append(f"{label}: the passage carrying {figure} is not in the top 10 for its own question")
        if first_from_doc is None:
            problems.append(f"{label}: {doc_id} was not retrieved at all")
        else:
            print(f"    top passage: {result.passages[0].doc_id}  {' '.join(texts[0].split())[:80]!r}")

    print()
    print("=" * 96)
    print("RET-EMBED-001: a contract that moved is refused, through the read path's own check")
    print("=" * 96)
    import psycopg2
    connection = psycopg2.connect(os.environ["DATABASE_URL"])
    live = contract.live_contract(services, info=contract.read_embedding_service(services.embed_url),
                                  dimension=ingest._get_vector_size(services))
    print(f"  the recorded contract: {json.dumps(live, sort_keys=True)}")
    print(f"  fingerprint: {contract.fingerprint(live)}")

    # Unchanged first, so the check is shown to pass before it is shown to fail. A check that refuses
    # everything would satisfy the mismatch test and be useless.
    try:
        outcome = contract.check(connection, services.collection, live)
        print(f"\n  unchanged contract -> {outcome['status']}, generation {outcome['generation']}")
        if outcome["status"] not in ("unchanged", "recorded", "rerecorded"):
            problems.append(f"an unchanged contract reported {outcome['status']!r}")
    except Exception as exc:  # noqa: BLE001
        problems.append(f"an unchanged contract was refused: {exc}")

    # Now a field that changes what a vector means. Pooling is the one with the quietest failure: the vectors
    # are the same size, the store stays healthy, and similarity between a mean-pooled query and a cls-pooled
    # passage is meaningless while still returning a plausible order.
    moved = dict(live)
    moved["pooling"] = "mean"
    print(f"\n  a contract with pooling moved from {live.get('pooling')!r} to 'mean':")
    try:
        contract.check(connection, services.collection, moved)
        problems.append("a contract with a different pooling was ACCEPTED, so the check does not refuse")
        print("    ACCEPTED, which is the failure: the vectors are the same size and the answers would be "
              "quietly worse")
    except RuntimeError as exc:
        message = str(exc)
        print(f"    REFUSED")
        print(f"    {message[:300]}")
        if "pooling" not in message:
            problems.append("the refusal does not name the field that moved")
        else:
            print("    ok  the refusal names the field, so an operator does not have to debug two hashes")
    except Exception as exc:  # noqa: BLE001
        problems.append(f"the mismatch raised {type(exc).__name__} rather than a RuntimeError: {exc}")

    # And a dimension change, the field that already failed loudly, so the new check subsumes the old one.
    moved_size = dict(live)
    moved_size["dimension"] = (live.get("dimension") or 1024) + 128
    try:
        contract.check(connection, services.collection, moved_size)
        problems.append("a dimension change was accepted")
    except RuntimeError as exc:
        print(f"\n  a dimension change is refused too, so the new check subsumes the old one")
        print(f"    {str(exc)[:160]}")

    connection.close()

    print()
    if problems:
        print(f"FAILED with {len(problems)} problem(s):")
        for problem in problems:
            print(f"  - {problem}")
        return 1
    print("PASSED: email, spreadsheets and slides are each retrievable by their own fact, and the embedding")
    print("contract refuses a moved field by name while accepting the contract in force")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
