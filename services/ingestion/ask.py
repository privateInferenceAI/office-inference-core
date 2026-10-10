#!/usr/bin/env python3
"""Ask a question across the documents, with citations.

The retrieval path in one place: embed the question, search the vector store restricted to the
role's access labels, rerank, then ask the model to answer only from those passages and cite them.

Two behaviours worth knowing:

- **A document outside your role is never retrieved.** The filter is applied in the search, so a
  restricted passage never reaches the model and cannot be summarised by accident. Asking for one
  explicitly is refused and recorded as a denial.
- **Everything is recorded.** Each retrieval writes an access event, and the answer writes a model
  call event carrying the gateway's request id, so an answer can be traced to the passages behind it.

Usage:
    python ask.py --role staff --question "what is the mileage rate?"
    python ask.py --role staff --question "..." --show-context
    python ask.py --role staff --question "..." --doc executive/comp-memo.md     # refused, by design
"""

from __future__ import annotations

import argparse
import json
import sys

import httpx
from qdrant_client.http import models as qm

from common import (COLLECTION, GATEWAY_KEY, GATEWAY_URL, ROLE_ACLS, acl_for, embed, qdrant,
                    record_event, rerank)

CANDIDATES = 20


def search(question: str, role: str, doc: str | None, use_rerank: bool, top_k: int):
    allowed = sorted(ROLE_ACLS.get(role, set()))
    if not allowed:
        raise SystemExit(f"unknown role {role!r}; known roles: {', '.join(sorted(ROLE_ACLS))}")

    must = [qm.FieldCondition(key="acl", match=qm.MatchAny(any=allowed))]
    if doc:
        must.append(qm.FieldCondition(key="doc_id", match=qm.MatchValue(value=doc)))

    vector = embed([question])[0]
    hits = qdrant().query_points(
        collection_name=COLLECTION, query=vector, limit=CANDIDATES,
        query_filter=qm.Filter(must=must), with_payload=True,
    ).points

    if use_rerank and len(hits) > 1:
        # rerank() returns (index into the candidate list, score). Sort by score and carry the
        # original hit alongside it, rather than looking the score up again after reordering.
        scored = rerank(question, [hit.payload["text"] for hit in hits])
        ranked = sorted(scored, key=lambda pair: pair[1], reverse=True)[:top_k]
        return [(hits[index], score) for index, score in ranked]
    return [(hit, hit.score) for hit in hits[:top_k]]


def build_prompt(question: str, passages):
    blocks = []
    for number, (hit, _score) in enumerate(passages, start=1):
        payload = hit.payload
        blocks.append(f"[{number}] {payload['doc_id']} (page {payload['page']})\n{payload['text']}")
    context = "\n\n".join(blocks)
    system = (
        "You answer strictly from the numbered passages below. Cite every claim with its passage "
        "number in square brackets, like [2]. If the passages do not contain the answer, say so "
        "plainly and do not guess. Two or three sentences unless asked for more."
    )
    return system, f"Passages:\n\n{context}\n\nQuestion: {question}"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--role", default="staff")
    parser.add_argument("--question", required=True)
    parser.add_argument("--doc", help="restrict to one document (used to demonstrate a denial)")
    parser.add_argument("--top-k", type=int, default=4)
    parser.add_argument("--no-rerank", action="store_true")
    parser.add_argument("--retrieve-only", action="store_true",
                        help="show the passages and stop, without calling a model")
    parser.add_argument("--show-context", action="store_true")
    parser.add_argument("--max-tokens", type=int, default=400)
    args = parser.parse_args()

    if args.doc and acl_for(args.doc) not in ROLE_ACLS.get(args.role, set()):
        record_event("access", actor=args.role, actor_role=args.role, outcome="denied", tool="retrieve",
                     data_class="confidential",
                     payload={"doc_id": args.doc, "reason": "acl", "question": args.question})
        print(f"denied: {args.doc} is not readable by role {args.role!r}. "
              f"That refusal is recorded in the audit log.")
        return 3

    passages = search(args.question, args.role, args.doc, not args.no_rerank, args.top_k)
    if not passages:
        print("No passages matched. Nothing was sent to a model.")
        return 4

    for hit, score in passages:
        payload = hit.payload
        record_event("access", actor=args.role, actor_role=args.role, tool="retrieve",
                     data_class="internal" if payload["acl"] == "company" else "confidential",
                     payload={"doc_id": payload["doc_id"], "page": payload["page"],
                              "score": round(float(score), 4), "question": args.question})

    if args.retrieve_only:
        print("Passages retrieved (nothing was sent to a model):")
        for number, (hit, score) in enumerate(passages, start=1):
            payload = hit.payload
            preview = payload["text"][:160].replace("\n", " ")
            print(f"  [{number}] {payload['doc_id']} page {payload['page']} "
                  f"(score {float(score):.3f}) {preview}...")
        return 0

    system, user = build_prompt(args.question, passages)
    if args.show_context:
        print("--- context sent to the model ---")
        print(user)
        print("--- end context ---\n")

    headers = {"Content-Type": "application/json"}
    if GATEWAY_KEY:
        headers["Authorization"] = f"Bearer {GATEWAY_KEY}"
    response = httpx.post(
        f"{GATEWAY_URL}/v1/chat/completions", headers=headers, timeout=300,
        json={"model": "analyst", "max_tokens": args.max_tokens,
              "messages": [{"role": "system", "content": system},
                           {"role": "user", "content": user}]},
    )
    response.raise_for_status()
    body = response.json()
    message = body["choices"][0]["message"]
    answer = (message.get("content") or "").strip()
    if not answer:
        # Reasoning models can spend the whole budget thinking. Say so rather than printing nothing.
        answer = "(the model produced no answer within the token budget; raise --max-tokens)"

    record_event("model_call", actor=args.role, actor_role=args.role, model=body.get("model"),
                 request_id=body.get("id"), tool="ask",
                 payload={"question": args.question, "passages": len(passages),
                          "total_tokens": body.get("usage", {}).get("total_tokens")})

    print(answer)
    print("\nSources:")
    for number, (hit, score) in enumerate(passages, start=1):
        payload = hit.payload
        print(f"  [{number}] {payload['doc_id']} page {payload['page']} (score {float(score):.3f})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
