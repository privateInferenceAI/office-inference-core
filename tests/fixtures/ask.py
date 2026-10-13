#!/usr/bin/env python3
"""Ask one question against the deployed corpus, as a retrieval role.

This is the smallest complete path a question takes, and it exists because the manual documented
everything about reading documents except how to read one. Every other probe here answers a specific
question about a specific control; this one answers the operator's question, which is "what does the
system return for this?"

It runs the real retriever against the real store: both arms, the access filter, the denial comparison and
the reranker, exactly as a caller would. What it does NOT do is call a model. Retrieval and generation are
separate steps, and this probe stops after retrieval so the passages can be read without a provider in the
way. Handing these passages to a model is C3's assistant API.

Run from the repo root on the box:

    docker run --rm --network ai-net -v /opt/oi-core:/w:ro -w /w \\
      --env-file rendered/env --entrypoint python oi-core/eval:local \\
      /w/tests/fixtures/ask.py --question "What is the mileage rate?" --role staff

The `eval` image rather than the ingestion one because this imports the retriever, which imports
botocore through the reranker. `--env-file rendered/env` supplies DATABASE_URL for the lexical arm and
the rerank settings; without it the lexical arm is skipped and the output says so.

Exit codes: 0 when passages came back, 1 when nothing did, and 2 when the role is not one the retriever
knows. A denial is deliberately NOT an error exit: a denied search still returns the passages the role may
see, so it exits 0 with `denied: True` in the output. A script that wants to act on a denial reads that
field rather than the exit code, because treating the control working as a failure would page someone
every time it worked.
"""
from __future__ import annotations

import argparse
import json
import os
import sys

sys.path.insert(0, "/w/services/retrieval")
sys.path.insert(0, "/w/services/ingestion")

import citations  # noqa: E402
import ingest  # noqa: E402
import retriever  # noqa: E402


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--question", "-q", required=True, help="the question, in the client's words")
    parser.add_argument("--role", default="staff",
                        help="the retrieval role: staff or executive. Decides which folders may be read")
    parser.add_argument("--limit", type=int, default=5, help="how many passages to return")
    parser.add_argument("--denial", choices=[m.value for m in retriever.DenialMode], default=None,
                        help="override the configured denial mode for this call")
    parser.add_argument("--no-rerank", action="store_true",
                        help="skip reranking, to see the fused order instead")
    parser.add_argument("--verify", metavar="ANSWER",
                        help="check a candidate answer's citations against what was retrieved")
    parser.add_argument("--json", action="store_true", help="machine-readable output")
    return parser


def main() -> int:
    args = build_parser().parse_args()

    try:
        roots = retriever.permitted_roots(args.role)
    except (KeyError, ValueError) as exc:
        # Two refusals, both exit 2. KeyError is an unknown role. ValueError is a role whose permission set is
        # empty, which `permitted_roots` raises rather than treating as "sees nothing": a role that may see
        # nothing is a configuration error, and calling it a legitimate role makes a mistake look like a user
        # with no documents. Only the first was caught until a review noticed the second escaped as a
        # traceback and exit 1.
        print(f"error: {exc}", file=sys.stderr)
        return 2

    services = ingest.Services()
    result = retriever.search(
        services, args.question, role=args.role, limit=args.limit,
        denial=args.denial, rerank_passages=not args.no_rerank,
    )

    if args.json:
        print(json.dumps({
            "question": result.question,
            "role": result.role,
            "roots": sorted(roots),
            "withheld": result.withheld,
            "denied": result.denied,
            "reason": result.reason,
            "disclosure": None if result.disclosure is None else {
                "mode": result.disclosure.mode,
                "message": result.disclosure.message,
                "count": result.disclosure.count,
            },
            "restricted_matched": result.restricted_matched,
            "restricted_best_score": result.restricted_best_score,
            "arms": result.arms,
            "rerank": result.rerank,
            "passages": [
                {
                    "n": i,
                    "doc_id": p.doc_id,
                    "acl": p.acl,
                    "pages": [p.page_start, p.page_end],
                    "kind": p.kind,
                    "chunk_id": p.chunk_id,
                    "score": p.score,
                    "is_current": p.is_current,
                    "superseded_by": p.superseded_by,
                    "text": p.text,
                }
                for i, p in enumerate(result.passages, 1)
            ],
        }, indent=2))
        return 0 if result.passages else 1

    print(f"question: {result.question}")
    print(f"role:     {result.role}   roots: {sorted(roots)}")
    print(f"arms:     {result.arms}")
    if result.rerank:
        print(f"rerank:   {result.rerank}")
    print(f"withheld: {result.withheld}   denied: {result.denied}"
          f"   restricted matched: {result.restricted_matched}")
    if result.disclosure is not None:
        count = "" if result.disclosure.count is None else f" ({result.disclosure.count} passages)"
        print(f"told the user [{result.disclosure.mode}]: {result.disclosure.message}{count}")

    if not result.passages:
        print("\nnothing returned.")
        return 1

    print()
    for i, p in enumerate(result.passages, 1):
        score = "n/a" if p.score is None else f"{p.score:.4f}"
        pages = f"{p.page_start}-{p.page_end}"
        current = "" if p.is_current else f"  ** NOT CURRENT, superseded by {p.superseded_by}"
        print(f"[{i}] {p.doc_id}  pages {pages}  acl={p.acl}  score={score}{current}")
        for line in (p.text or "").strip().splitlines()[:6]:
            print(f"    {line}")
        print()

    if args.verify:
        verified = citations.verify(args.verify, result.passages)
        print()
        for claim in verified.claims:
            mark = "supported" if claim.ok else f"REFUSED ({claim.reason})"
            print(f"  [{mark}] {claim.text[:100]}")
            if claim.detail:
                print(f"      {claim.detail}")
        if verified.refusals:
            print(f"\nrefusals by reason: {verified.refusals}")
        print(f"all supported: {verified.all_supported}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
