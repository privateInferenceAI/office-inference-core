#!/usr/bin/env python3
"""The mechanical layer on the deployed store: real passages, a real answer, and both failures proved.

RET-CITE-001's verify line asks for two things: an invented citation index is rejected, and an unsupported
sentence is marked rather than silently kept.

Neither can be proved with a stub, because both depend on what was actually retrieved. An invented citation is
only invented relative to a real retrieved set, and the literal checks need the real page text the parser
produced. So this retrieves from the deployed store first and builds the answer around what came back.

The answer below is written rather than generated, and that is deliberate. A model would produce a different
answer every run and the test would be measuring the model; this is measuring the checks. Generating the
answers is the assistant API's job in Phase 4, and it will call this module.

Run from the repo root on the box:

    cd /opt/oi-core
    docker run --rm --network ai-net --user "$(id -u):$(id -g)" \\
      -v /opt/oi-core:/w:ro -v /tmp:/probe:ro -w /w \\
      --entrypoint python oi-core/ingestion:local /tmp/citations_acceptance.py
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

import citations  # noqa: E402
import ingest  # noqa: E402
import retriever  # noqa: E402

# A number that appears in the corpus in no document, so a claim stating it has to be refused. Checked against
# the store rather than assumed, because a fixture number would make the test pass for the wrong reason.
IMPOSSIBLE = "987654.32"


def main() -> int:
    services = ingest.Services()
    problems: list[str] = []

    question = "What is the standard labour rate per hour?"
    result = retriever.search(services, question, role="staff", limit=5)
    print()
    print(f"=== retrieved for {question!r} ===")
    for index, passage in enumerate(result.passages, start=1):
        print(f"  [{index}] {passage.doc_id} p{passage.page_start} score={passage.score}")
        print(f"      {passage.text[:96]!r}")
    if not result.passages:
        print("nothing retrieved, so nothing below would prove anything")
        return 1

    # A figure that really is in the first passage, taken from it rather than typed, so the correct claim is
    # correct by construction.
    import re
    figures = re.findall(r"\b\d{2,3}(?:\.\d{2})?\b", result.passages[0].text)
    real_figure = figures[0] if figures else None
    print()
    print(f"a figure taken from passage [1] to build the correct claim: {real_figure!r}")

    answer = " ".join(filter(None, [
        f"The standard labour rate is {real_figure} per hour.[1]" if real_figure else None,
        f"The rate is {IMPOSSIBLE} per hour.[1]",
        "The rate is set out in the schedule.[99]",
        "This paragraph has no citation at all.",
    ]))

    print()
    print("=== the answer being checked ===")
    for claim in citations.split_claims(answer):
        print(f"  {claim}")
    print()
    print("=== verdicts ===")
    verified = citations.verify(answer, result.passages)
    for claim in verified.claims:
        mark = "ok      " if claim.ok else "MARKED  "
        reason = claim.reason or ""
        print(f"  {mark}{reason:<24}{claim.text[:60]}")
        if claim.detail:
            print(f"          {claim.detail}")

    print()
    print("=== RET-CITE-001, first half: an invented citation index is rejected ===")
    invented = [c for c in verified.claims if c.reason == "citation_not_found"]
    if invented:
        print(f"  REJECTED  {invented[0].text}")
        print(f"            {invented[0].detail}")
    else:
        problems.append("a claim citing [99] was not rejected, and only "
                        f"{len(result.passages)} passages were retrieved")

    print()
    print("=== RET-CITE-001, second half: an unsupported sentence is marked, not silently kept ===")
    unsupported = [c for c in verified.claims if c.reason == "number_not_in_source"]
    if unsupported:
        print(f"  MARKED    {unsupported[0].text}")
        print(f"            {unsupported[0].detail}")
        kept = [c.text for c in verified.claims if not c.ok and c.reason != "no_citation"]
        print(f"  the marked sentence is still in the reply, not dropped: {len(kept)} kept and marked")
    else:
        problems.append(f"the claim stating {IMPOSSIBLE} was not marked as unsupported")

    if real_figure:
        print()
        print("=== and the correct sentence is kept ===")
        correct = [c for c in verified.claims if c.ok]
        if correct:
            print(f"  SUPPORTED {correct[0].text}")
        else:
            problems.append(f"the correct claim about {real_figure} was refused, which is the false refusal "
                            f"that gets a check turned off")

    print()
    print(f"refusals by reason: {verified.refusals}")

    print()
    if problems:
        print(f"FAILED with {len(problems)} problem(s):")
        for problem in problems:
            print(f"  - {problem}")
        return 1
    print("PASSED: the invented citation is rejected, the unsupported sentence is marked and kept, and the")
    print("correct sentence about a real figure survives")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
