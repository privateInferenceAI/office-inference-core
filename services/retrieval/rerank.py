"""Reranking the fused candidates through a cross-encoder, and measuring whether it earns its place.

RET-RERANK-001 asks two things of a reranker: that it stays inside the latency budget, and that it improves
retrieval and support on the golden set. **The second is why the harness exists**, and it is the reason this file
does not simply rank and return.

**Why a reranker at all.** The fusion in step 3.9 ranks passages by where two independent searches put them, and
neither search has read the passage against the question. A bi-encoder embeds the question and the passage
separately and compares the vectors, which is fast and loses the interaction between them. A cross-encoder reads
both together and scores the pair, which is slower and is the thing that knows whether the passage answers the
question. So it runs on the fused head rather than on the corpus: **40 to 50 candidates**, which is the depth the
measurement in step 3.3 settled.

**The budget is the constraint that shapes everything else.** On the reference instance a CPU reranker costs
about 2.5 to 3 ms per token of candidate text, flat in candidate count, so a five-second budget buys three or
four of our 500-token chunks. That is why the cloud build calls a hosted reranker instead, and why the depth is
capped rather than greedy.

**What this refuses to do.** It does not fall back silently. If the reranker is unreachable the caller is told,
because a deployment that quietly answers from the fused order while believing it reranked is a deployment whose
measurements are about a configuration it is not running. Step 3.9 already has a precedent for this: the lexical
arm reports its own absence in `arms`, so "found nothing" and "did not run" are distinguishable.
"""
from __future__ import annotations

import json
import os
import time
import urllib.request
from dataclasses import dataclass, field

# How many fused candidates to send. The step 3.3 measurement settled 40 to 50 for the cloud build, and the cost
# is per token of candidate text rather than per candidate, so the cap is about the budget rather than the count.
RERANK_DEPTH = int(os.environ.get("RERANK_DEPTH", "50"))

# The budget the requirement names. Measured rather than asserted: `RerankOutcome.elapsed_ms` is what the run
# took, and the harness reports it beside the rates so a quality gain that costs more than the budget is visible
# as the trade it is.
RERANK_BUDGET_MS = int(os.environ.get("RERANK_BUDGET_MS", "5000"))

MODEL_ID = os.environ.get("RERANK_MODEL", "cohere.rerank-v3-5:0")
REGION = os.environ.get("AWS_REGION", "us-east-1")


@dataclass
class RerankOutcome:
    """The order, and what producing it cost."""

    order: list[int] = field(default_factory=list)
    scores: list[float] = field(default_factory=list)
    elapsed_ms: float = 0.0
    ran: bool = False
    reason: str | None = None

    @property
    def within_budget(self) -> bool:
        return self.elapsed_ms <= RERANK_BUDGET_MS

    def as_record(self) -> dict:
        return {"reranked": self.ran, "rerank_depth": len(self.order), "rerank_ms": round(self.elapsed_ms, 1),
                "rerank_budget_ms": RERANK_BUDGET_MS, "rerank_within_budget": self.within_budget,
                "rerank_model": MODEL_ID if self.ran else None, "rerank_reason": self.reason}


def available() -> bool:
    """Whether a reranker is configured at all.

    Absent means the lane does not rerank, which is a configuration and not a fault: the office build decides its
    own reranking on hardware that does not exist yet, and the cloud build is the one this was measured for.
    """
    return (os.environ.get("RERANK_ENABLED", "true").strip().lower() != "false"
            and bool(MODEL_ID))


def _bedrock_rerank(query: str, documents: list[str], top_n: int) -> list[tuple[int, float]]:
    """The reranker's order, as (index into `documents`, score).

    Bedrock rather than a local cross-encoder because of the budget: a CPU reranker costs about 2.5 to 3 ms per
    token of candidate text, so a five-second budget buys three or four of our chunks. Recorded in step 3.3 and
    the reason the stage moved rather than shrank.

    The request is signed with SigV4 through `botocore` rather than hand-rolled, because a signature this
    repository computes itself is a signature that breaks on a clock skew or a header change and looks like a
    permissions problem when it does.
    """
    import botocore.session
    from botocore.auth import SigV4Auth
    from botocore.awsrequest import AWSRequest

    body = json.dumps({"api_version": 2, "query": query, "documents": documents, "top_n": top_n})
    url = f"https://bedrock-runtime.{REGION}.amazonaws.com/model/{MODEL_ID}/invoke"
    session = botocore.session.get_session()
    credentials = session.get_credentials()
    if credentials is None:
        raise RuntimeError("no AWS credentials available to the reranker")
    request = AWSRequest(method="POST", url=url, data=body,
                         headers={"Content-Type": "application/json", "Accept": "application/json"})
    SigV4Auth(credentials.get_frozen_credentials(), "bedrock", REGION).add_auth(request)

    http = urllib.request.Request(url, data=body.encode("utf-8"),
                                  headers=dict(request.headers), method="POST")
    with urllib.request.urlopen(http, timeout=RERANK_BUDGET_MS / 1000 + 5) as response:
        parsed = json.loads(response.read())
    return [(item["index"], float(item["relevance_score"])) for item in parsed.get("results", [])]


def rerank(query: str, passages: list, depth: int = RERANK_DEPTH) -> RerankOutcome:
    """Reorder the head of a fused list, or say why it did not.

    Returns the ORDER rather than a new list, because the caller holds the passages and their payloads and
    rebuilding them here would mean carrying the payload through a service that has no use for it. The documents
    sent to the reranker are the passage texts and nothing else: **no identifiers, no document names, no ACL**
    labels, because a reranker needs the words and a service that receives a client's filenames is a service that
    has them.
    """
    if not passages:
        return RerankOutcome(ran=False, reason="nothing to rerank")
    if not available():
        return RerankOutcome(ran=False, reason="no reranker configured on this deployment")

    head = passages[:depth]
    documents = [(getattr(p, "text", "") or "")[:8000] for p in head]
    started = time.monotonic()
    try:
        results = _bedrock_rerank(query, documents, top_n=len(documents))
    except Exception as exc:  # noqa: BLE001
        # Reported rather than swallowed. A deployment answering from the fused order while believing it
        # reranked is one whose measurements describe a configuration it is not running.
        return RerankOutcome(ran=False, elapsed_ms=(time.monotonic() - started) * 1000,
                             reason=f"{type(exc).__name__}: {exc}")
    elapsed = (time.monotonic() - started) * 1000

    order = [index for index, _score in results]
    # Anything the reranker did not return keeps its fused position behind what it did, so a passage cannot be
    # dropped by being absent from the response.
    seen = set(order)
    order.extend(index for index in range(len(head)) if index not in seen)
    # And the tail beyond the reranked depth keeps its order behind them.
    order.extend(range(len(head), len(passages)))
    return RerankOutcome(order=order, scores=[score for _index, score in results], elapsed_ms=elapsed, ran=True)


def apply_order(passages: list, outcome: RerankOutcome) -> list:
    if not outcome.ran or not outcome.order:
        return passages
    return [passages[index] for index in outcome.order if index < len(passages)]
