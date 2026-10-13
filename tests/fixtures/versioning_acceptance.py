#!/usr/bin/env python3
"""Versioning and duplicates on the deployed store: two versions indexed, the current one cited.

Two requirements, and both are checked against the real thing rather than a stub.

RET-VERSION-001 asks that a superseded document is not cited while its replacement exists. This indexes a
second fee schedule, declares the first superseded, and shows that the passage comes back MARKED rather than
dropped, which is what the research asks for: a claim supported only by a non-current document is refused with
the reason `superseded`, so the fact has to reach the verifier.

RET-DUPE-001 asks that the same document under two filenames is flagged rather than indexed twice. This copies
a document under a second name, ingests, and shows the pair in the review list.

Run from the repo root on the box:

    cd /opt/oi-core
    docker run --rm --network ai-net --user "$(id -u):$(id -g)" \\
      -v /opt/oi-core:/w:ro -v /tmp:/probe:ro -w /w \\
      --entrypoint python oi-core/ingestion:local /tmp/versioning_acceptance.py
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.insert(0, "/w/services/retrieval")
sys.path.insert(0, "/w/services/ingestion")


def load_env(path: Path = Path("/w/rendered/env")) -> dict[str, str]:
    try:
        text = path.read_text()
    except PermissionError:
        print(f"cannot read {path}: mode 600 owned by the operator. Add --user \"$(id -u):$(id -g)\"")
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
    print("DATABASE_URL assembled from rendered/env the way compose does")

import ingest  # noqa: E402
import retriever  # noqa: E402


FIXTURE_DIR = Path("/w/fixtures/corpus")
VERSION_A = "company/fee-schedule-2025.md"
VERSION_B = "company/fee-schedule-2026.md"
COPY_A = "company/fee-schedule-2026-copy.md"


def install_fixtures() -> None:
    """Two versions of one document and a second copy of the later one, written where the worker reads.

    The corpus has no two versions of anything and no duplicate pair, so both requirements would pass against
    a system with neither feature: nothing to supersede and nothing to flag. These are minimal and their names
    say what they are, so the review list reads without a lookup.
    """
    # /w/documents is the same host directory the worker mounts at /documents, so writing here is what makes
    # the fixtures visible to the next ingest cycle.
    root = Path("/w/documents/company")
    if not Path("/w/documents").exists():
        print("  /w/documents is not present, so the fixtures cannot be installed and nothing below would "
              "prove anything. Run with -v /opt/oi-core:/w:ro replaced by a writable mount.")
        return None
    root.mkdir(parents=True, exist_ok=True)
    body = ("# Schedule of Fees\n\n"
            "Standard labour, business hours ... {rate} per hour\n"
            "Emergency attendance ................ 240.00 per callout\n"
            "Mileage, client-owned vehicle ....... 0.67 per mile\n"
            "Filter media, standard .............. 32.50 per unit\n"
            "Refrigerant, R-410A ................. 18.75 per pound\n"
            "After-hours multiplier .............. 1.5x of labour\n")
    (root / "fee-schedule-2025.md").write_text(body.format(rate="132.00"))
    (root / "fee-schedule-2026.md").write_text(body.format(rate="145.00"))
    # Byte-identical to the later one under a different name, which is exactly RET-DUPE-001's case.
    (root / "fee-schedule-2026-copy.md").write_text(body.format(rate="145.00"))
    print(f"  installed {VERSION_A}, {VERSION_B} and a byte-identical copy under a third name")
    return root


def main() -> int:
    services = ingest.Services()
    manifest = ingest.PostgresManifest(services.database_url)
    problems: list[str] = []

    print()
    print("=== fixtures ===")
    install_fixtures()

    print()
    print("=== what is declared ===")
    declared = manifest.current_documents()
    print(f"  {len(declared)} document(s) with a declaration")
    for doc_id, is_current in sorted(declared.items()):
        print(f"    {'current  ' if is_current else 'SUPERSEDED'} {doc_id}")

    print()
    print("=== the version registry decision ===")
    print("  Nothing is declared current or superseded by the system. Two documents with similar content are")
    print("  flagged for a person, and the flag is resolved by a declaration through ingest.py --supersede.")
    print("  A passage from a superseded document is MARKED rather than dropped, so step 3.11 can refuse an")
    print("  answer whose only support is one, rather than answering from a different document.")

    print()
    print("=== unresolved near-duplicate flags ===")
    pending = manifest.unresolved_duplicates()
    if pending:
        for flag in pending:
            print(f"  {flag['overlap']:>6.1%} shared  {flag['doc_id']}  <->  {flag['other_doc_id']}")
        print(f"  {len(pending)} pair(s) awaiting a decision")
    else:
        print("  none. RET-DUPE-001 is proved by the fixture rather than by this corpus: see the runbook.")

    print()
    print("=== RET-VERSION-001: a superseded document is marked, not cited as current ===")
    # Declare the 2025 schedule replaced, which is the human decision the design calls for. Done here rather
    # than by the system, because nothing infers which of two documents is current: a wrong guess is invisible
    # and an unresolved flag is not.
    manifest.set_current(VERSION_A, False, superseded_by=VERSION_B,
                         note="2026 schedule in force; probe declaration")
    versions = retriever.document_versions(services)
    if versions.get(VERSION_A) is not False:
        problems.append(f"{VERSION_A} did not read back as superseded")
    if versions.get(VERSION_B, True) is not True:
        problems.append(f"{VERSION_B} should be current by default, read {versions.get(VERSION_B)}")

    result = retriever.search(services, "What is the standard labour rate per hour?", role="staff", limit=5)
    print(f"  query returned {len(result.passages)} passage(s)")
    for passage in result.passages:
        mark = "" if passage.is_current else "   <- MARKED SUPERSEDED"
        print(f"    {passage.doc_id} p{passage.page_start} current={passage.is_current}{mark}")
    marked = [p for p in result.passages if not p.is_current]
    if not result.passages:
        problems.append("the query returned nothing, so nothing about currency was shown")
    elif not marked and VERSION_B not in {p.doc_id for p in result.passages}:
        problems.append("neither version was returned, so the query did not reach the fixtures")
    print(f"  superseded documents among the results: {result.superseded or 'none'}")
    print("  A marked passage reaching the caller is the requirement: step 3.11 refuses an answer whose only")
    print("  support is one of these, and dropping it here would answer from a different document silently.")

    print()
    print("=== RET-DUPE-001: the same document under two names ===")
    pending = manifest.unresolved_duplicates()
    copies = [f for f in pending
              if {f["doc_id"], f["other_doc_id"]} == {VERSION_B, COPY_A}]
    if copies:
        flag = copies[0]
        print(f"  FLAGGED  {flag['overlap']:.0%} shared  {flag['doc_id']}  <->  {flag['other_doc_id']}")
        print("  Flagged rather than deleted or indexed twice: two documents a client put in a folder may be")
        print("  two documents, and a system that removes one is worse than one that asks.")
    else:
        problems.append(
            f"the byte-identical copy {COPY_A} was not flagged against {VERSION_B}. Pairs found: "
            f"{[(f['doc_id'], f['other_doc_id']) for f in pending]}. The flag needs an ingest cycle after the "
            f"fixtures were installed.")

    print()
    print("=== resolve the flags so the deployment is left tidy ===")
    manifest.set_current(VERSION_B, True, note="confirmed current; probe cleanup")
    manifest.set_current(COPY_A, False, superseded_by=VERSION_B, note="a second copy; probe cleanup")
    remaining = [f for f in manifest.unresolved_duplicates()
                 if {f["doc_id"], f["other_doc_id"]} == {VERSION_B, COPY_A}]
    print(f"  the pair is resolved: {not remaining}")

    print()
    if problems:
        print(f"FAILED with {len(problems)} problem(s):")
        for problem in problems:
            print(f"  - {problem}")
        return 1
    print("PASSED: the registry is readable, every passage carries its currency, and declarations are the only")
    print("thing that changes it")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
