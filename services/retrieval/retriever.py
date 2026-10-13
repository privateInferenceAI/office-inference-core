"""Retrieval with the access label applied inside the query.

The requirement this module exists for, from the register: "Role-based access filtering happens inside the
search, before any model sees a passage." Every word of that is load-bearing.

*Inside the search* is the part that is easy to get wrong and impossible to see in a passing test. A
retriever that fetches the top ten and then discards the three the role cannot see produces the right answer
and breaks the requirement, because the restricted text left the vector store, was counted, and sat one
logging statement or one exception trace away from a leak. So the label is a clause in the query, and the
filtering in this module is a second line rather than the first.

*Before any model sees a passage* is why the passage objects carry no more than a citation needs. Text that
is not returned cannot be put in a prompt by a caller that forgot to check.

The permission model is deliberately small, and its smallness is a decision rather than an omission. Two
roots, `company` and `executive`, matching how a client's folder tree is organised, with a role permitted
one or both. Per-user and per-group permissions are a different requirement, `SEC-ACL-002`, in a later
phase; a model with two roles is one that can be read in full and tested exhaustively, and getting the
inside-the-search part right matters more than having more roles.
"""
from __future__ import annotations

import json
import sys
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "ingestion"))

import ingest  # noqa: E402

# Which document roots a role may retrieve from. A role absent from this map is refused rather than
# defaulted, because the failure a default would cause is the worst kind: a typo or a missing session field
# silently granting the broader of two permissions.
ROLES: dict[str, frozenset[str]] = {
    "staff": frozenset({"company"}),
    "executive": frozenset({"company", "executive"}),
}

# How many candidates to ask the store for before access filtering. The store is asked for more than the
# caller wants because withheld passages consume part of the result set, and asking for exactly the limit
# would return fewer than the limit whenever anything is withheld.
OVERFETCH = 3


@dataclass
class Passage:
    """One retrievable passage, carrying what a citation needs and nothing else."""

    doc_id: str
    acl: str
    text: str
    page_start: int
    page_end: int
    kind: str
    chunk_id: str
    score: float = 0.0
    is_ocr: bool = False
    page_is_nominal: bool = False
    table_id: str | None = None
    row_index: int | None = None

    @classmethod
    def from_payload(cls, payload: dict, score: float) -> "Passage":
        return cls(
            doc_id=payload.get("doc_id") or "",
            acl=payload.get("acl") or "",
            text=payload.get("text") or "",
            page_start=int(payload.get("page_start") or 1),
            page_end=int(payload.get("page_end") or 1),
            kind=payload.get("kind") or "prose",
            chunk_id=payload.get("chunk_id") or "",
            score=score,
            is_ocr=bool(payload.get("is_ocr")),
            page_is_nominal=bool(payload.get("page_is_nominal")),
            table_id=payload.get("table_id"),
            row_index=payload.get("row_index"),
        )


@dataclass
class Result:
    """What a question returned, and whether anything was withheld.

    `denied` discloses that restricted content exists, which is a real leak and a deliberate one: the
    alternative is a user who cannot tell a wrong answer from an unavailable one. It never says what the
    content is, how much of it there is beyond a count, or which document holds it.
    """

    question: str
    role: str
    passages: list[Passage] = field(default_factory=list)
    withheld: int = 0
    denied: bool = False
    reason: str = ""


def embed_query(services: ingest.Services, texts: list[str]) -> list[list[float]]:
    """The query's vector. A seam of its own so a test can watch what is embedded."""
    return ingest.embed_texts(services, texts)


def _search(services: ingest.Services, body: dict) -> list[dict]:
    """One vector search. A seam of its own because the filter's LOCATION is the requirement."""
    request = urllib.request.Request(
        f"{services.vector_url}/collections/{services.collection}/points/search",
        data=json.dumps(body).encode(), method="POST",
        headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(request, timeout=services.timeout) as response:
        return json.loads(response.read()).get("result") or []


def permitted_roots(role: str) -> frozenset[str]:
    """The roots a role may see, or a refusal.

    An unknown role raises rather than defaulting, and a role with an empty permission set raises too. The
    second is the one worth stating: a role that may see nothing is a configuration error, and treating it
    as a legitimate role that happens to see nothing makes a mistake look like a user with no documents.
    """
    if role not in ROLES:
        raise KeyError(
            f"unknown role {role!r}. Known roles: {sorted(ROLES)}. A missing role is refused rather than "
            f"defaulted, because defaulting grants the broader of two permissions to a typo.")
    roots = ROLES[role]
    if not roots:
        raise ValueError(
            f"role {role!r} is permitted no document roots, which is a configuration error rather than a "
            f"user with no documents")
    return roots


def search(services: ingest.Services, question: str, role: str, limit: int = 10) -> Result:
    """Retrieve the passages a role may see, with the restriction applied by the store.

    The filter is a clause in the query, and the check afterwards is a second line. Both are present because
    they fail differently: the query clause is what the requirement asks for, and the check catches the case
    where the store returns something it should not, which a store is not the only possible source of.
    """
    roots = permitted_roots(role)
    result = Result(question=question, role=role)

    vectors = embed_query(services, [question])
    if not vectors:
        return result

    # `any` rather than a single value, so a role with more than one permitted root needs no second clause.
    # Sorted for determinism: the same question asked twice should produce the same query.
    body = {
        "vector": vectors[0],
        "limit": max(1, limit) * OVERFETCH,
        "with_payload": True,
        "filter": {"must": [{"key": "acl", "match": {"any": sorted(roots)}}]},
    }
    raw = _search(services, body)

    for item in raw:
        payload = item.get("payload") or {}
        acl = payload.get("acl") or ""
        if acl not in roots:
            # The store returned something the role may not see. It should not have, given the query, and
            # this is the line that keeps a store bug from becoming a disclosure.
            result.withheld += 1
            continue
        if len(result.passages) >= limit:
            continue
        result.passages.append(Passage.from_payload(payload, float(item.get("score") or 0.0)))

    if result.withheld:
        result.denied = True
        result.reason = (
            f"{result.withheld} passage(s) were restricted and not available to the {role!r} role. The "
            f"answer may be incomplete.")
    return result
