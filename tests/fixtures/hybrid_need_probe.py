#!/usr/bin/env python3
"""Does the vector arm actually miss exact identifiers? Measure before building the lexical one.

The architecture already decides hybrid search, and the reason given is that a vector arm matches meaning
while a lexical arm matches the literal token. That is true in general and unmeasured on our corpus. The
lexical arm is the largest piece of this step — a second store, a second write path in ingestion, a second
place the access filter has to be applied — so it is worth one command to find out whether it fixes something
that is actually broken here.

Two arms are compared on the same questions:

  vector   the deployed retrieval, top 10, for a question that contains an exact identifier
  lexical  a full scan for the literal token, which is an upper bound on what Postgres full-text would
           return and needs no schema to compute

A question where vector misses and lexical finds is a question the lexical arm earns its place on. A question
where both find it, or neither does, is not evidence for it.

Run from the repo root on the box, without changing the working tree:

    cd /opt/oi-core
    git fetch origin measure/hybrid-need
    git show origin/measure/hybrid-need:tests/fixtures/hybrid_need_probe.py > /tmp/hybrid_need_probe.py
    docker run --rm --network ai-net -v /opt/oi-core:/w:ro -v /tmp:/probe:ro -w /w \\
      --entrypoint python ghcr.io/docling-project/docling-serve-cpu:v1.36.0 /probe/hybrid_need_probe.py
"""
from __future__ import annotations

import json
import re
import sys
import urllib.request

sys.path.insert(0, "/w/services/retrieval")
sys.path.insert(0, "/w/services/ingestion")

import ingest  # noqa: E402
import retriever  # noqa: E402

# Identifiers from the corpus, ordered from rarest to commonest. A question a person would actually type,
# with the token they would type it with.
QUESTIONS = [
    ("Form W-3SS", "What is Form W-3SS used for?"),
    ("Pub. 519", "What does Pub. 519 cover?"),
    ("Pub. 515", "What is in Pub. 515?"),
    ("Form W-3", "When must Form W-3 be filed?"),
    ("Pub. 15", "What does Pub. 15 say about withholding?"),
    ("Form W-2", "What are the copy requirements for Form W-2?"),
    ("Form W-4", "How long must I keep a Form W-4?"),
]

TOP = 10


def all_chunks(services: ingest.Services) -> list[dict]:
    """Every point's payload, for the lexical side. A full scan is the upper bound on what the lexical arm
    could return, and it needs no new table to measure."""
    out: list[dict] = []
    offset = None
    while True:
        body = {"limit": 512, "with_payload": True, "with_vector": False}
        if offset is not None:
            body["offset"] = offset
        request = urllib.request.Request(
            f"{services.vector_url}/collections/{services.collection}/points/scroll",
            data=json.dumps(body).encode(), method="POST",
            headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(request, timeout=120) as response:
            result = json.loads(response.read())["result"]
        out.extend(result.get("points") or [])
        offset = result.get("next_page_offset")
        if not offset:
            return out


def main() -> int:
    services = ingest.Services()
    corpus = all_chunks(services)
    print(f"indexed points: {len(corpus)}")
    print(f"question set: {len(QUESTIONS)} identifiers, rarest first")
    print()
    print(f"{'identifier':<14}{'in corpus':>10}{'vector top1':>13}{'vector in top10':>17}"
          f"{'lexical hits':>14}  verdict")
    earns: list[str] = []
    for token, question in QUESTIONS:
        # The lexical side: every chunk whose text contains the literal token. This is an upper bound, not
        # what Postgres would return, because a tsquery for "W-3SS" would also stem and rank.
        lexical = [p for p in corpus if token.lower() in (p["payload"].get("text") or "").lower()]

        result = retriever.search(services, question, role="staff", limit=TOP)
        texts = [p.text for p in result.passages]
        in_top1 = token.lower() in texts[0].lower() if texts else False
        in_top10 = any(token.lower() in t.lower() for t in texts)

        if lexical and not in_top10:
            verdict = "VECTOR MISSES IT"
            earns.append(token)
        elif lexical and not in_top1:
            verdict = "vector finds it, but not first"
        elif lexical:
            verdict = "both find it"
        else:
            verdict = "not in corpus"
        print(f"{token:<14}{len(lexical):>10}{str(in_top1):>13}{str(in_top10):>17}"
              f"{len(lexical):>14}  {verdict}")

    print()
    if earns:
        print(f"the vector arm misses {len(earns)} of {len(QUESTIONS)}: {earns}")
        print("Those are the questions the lexical arm earns its place on, and they are the ones to build")
        print("the acceptance test around.")
    else:
        print("the vector arm found every identifier in its top 10, so this question set does not show a")
        print("gap. A lexical arm may still help on identifiers this corpus does not contain, but the")
        print("acceptance test needs a question where the difference is visible.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
