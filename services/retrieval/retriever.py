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
import os
import sys
import urllib.request
from dataclasses import dataclass, field
from enum import Enum
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


class DenialMode(str, Enum):
    """What a denied user is told, which is a client's policy rather than ours.

    The three modes exist because the trade between them is a business decision and not a technical one, and
    a client is the only party who can make it.

    SILENT   Nothing is said. The answer is built from what the role can see, and the user has no way to
             know that anything was withheld. Nothing is disclosed, and the cost is that an incomplete
             answer is indistinguishable from a complete one.
    DENIED   The user is told that documents exist which may answer the question and that they do not have
             access to them. That discloses the EXISTENCE of restricted content, and it is what lets
             someone stop trusting an answer they should not trust.
    COUNTED  The same, plus how many passages were withheld. A count is more information than a denial and
             still says nothing about the content, though a count on a question narrow enough to have one
             answer does imply how many documents hold it.
    """

    SILENT = "silent"
    DENIED = "denied"
    COUNTED = "counted"


# What a denied user is told, read from the environment because it is a client's declared policy. The
# renderer writes it from the manifest, the same way the content modes reach the audit tool.
#
# The default is DENIED rather than SILENT, and the reasoning is the product's own: a silent system returns
# an answer built from part of the corpus with no way for a user to tell it apart from a complete one, which
# is the confidently-wrong failure this whole design exists to fight. A client who weighs the leak of
# EXISTENCE more heavily sets this to silent and gets no disclosure at all. Both are defensible, so the
# choice is declared rather than assumed.
DEFAULT_DENIAL_MODE = os.environ.get("ACCESS_DENIAL_MODE", DenialMode.DENIED.value)

@dataclass
class Disclosure:
    """What the caller may show the user, and None means nothing may be shown.

    Separate from the facts in `Result` on purpose. A system that suppresses the SIGNAL is not a system that
    does not know: `withheld` and `denied` stay accurate in every mode so the record and the audit trail can
    report what happened, and only this object is governed by policy. Collapsing the two would make
    `silent` mean "we did not withhold anything", which is a lie the record would then carry.
    """

    mode: str
    message: str
    count: int | None = None


@dataclass
class Result:
    """What a question returned, what was withheld, and what may be said about it."""

    question: str
    role: str
    passages: list[Passage] = field(default_factory=list)
    withheld: int = 0
    denied: bool = False
    reason: str = ""
    disclosure: Disclosure | None = None
    # The best score found among passages this role may not see, kept for the record rather than shown to a
    # user. It is what makes "the answer may be incomplete" a measured statement rather than a guess.
    restricted_best_score: float | None = None


def embed_query(services: ingest.Services, texts: list[str]) -> list[list[float]]:
    """The query's vector. A seam of its own so a test can watch what is embedded."""
    return ingest.embed_texts(services, texts)


def _restricted_best_score(services: ingest.Services, vector: list[float],
                           roots: frozenset[str]) -> float | None:
    """The best score among passages this role may NOT see, or None if there are none.

    This exists because the obvious design does not work. The first version inferred a denial from the
    passages the store returned that the role could not see, which means it only ever fired when the store
    misbehaved: the filter is in the query and the store honours it, so nothing restricted comes back, so the
    count stayed zero and the denial never fired. The client's whole policy on what a denied user is told was
    dead code in normal operation, and the probe run that found it is the one that reported `withheld 0` on
    every question.

    The requirement is that a staff query for executive content returns a denial, which needs knowing whether
    restricted content WOULD have answered. So this runs a second search against the complement of the role's
    roots and takes its best score.

    Two things keep it from being a leak. `with_payload` is false, so no restricted text leaves the store:
    only a number does. And only the SCORE is compared, never the content, so what comes back is "something
    you may not see matched better than anything you can", which is exactly what a denial has to say and no
    more. The point id Qdrant returns anyway is a uuid5 over the document id, so it does not name a file.
    """
    forbidden = {"must_not": [{"key": "acl", "match": {"any": sorted(roots)}}]}
    hits = _search(services, {"vector": vector, "limit": 1, "with_payload": False,
                             "filter": forbidden})
    if not hits:
        return None
    try:
        return float(hits[0].get("score") or 0.0)
    except (TypeError, ValueError):
        return None


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


def denial_mode(value: str | None = None) -> DenialMode:
    """The configured mode, refused if it is not one of the three.

    An unrecognised value raises rather than falling back, for the same reason an unknown role does: a typo
    in a client's manifest must not silently select the more disclosing of two behaviours.
    """
    raw = (value if value is not None else DEFAULT_DENIAL_MODE) or DenialMode.DENIED.value
    try:
        return DenialMode(str(raw).strip().lower())
    except ValueError:
        raise ValueError(
            f"unknown denial mode {raw!r}. One of {[m.value for m in DenialMode]}. A typo must not "
            f"silently choose the more disclosing behaviour.") from None


def _disclose(mode: DenialMode, role: str, withheld: int) -> Disclosure | None:
    """What may be said, given the mode. None means nothing may be.

    The SILENT case returns None rather than an empty Disclosure, so a caller that forgets to check the mode
    has nothing to render by accident.
    """
    if mode is DenialMode.SILENT:
        return None
    if mode is DenialMode.COUNTED:
        return Disclosure(
            mode=mode.value,
            message=(f"Documents that answer this more directly exist and are not available to the {role!r} "
                     f"role. The answer below is drawn from what you can see and may be incomplete."),
            count=withheld or None)
    return Disclosure(
        mode=mode.value,
        message=(f"Documents that answer this more directly exist and are not available to the {role!r} "
                 f"role. The answer below is drawn from what you can see and may be incomplete."),
        count=None)


def search(services: ingest.Services, question: str, role: str, limit: int = 10,
           denial: str | None = None) -> Result:
    """Retrieve the passages a role may see, with the restriction applied by the store.

    The filter is a clause in the query, and the check afterwards is a second line. Both are present because
    they fail differently: the query clause is what the requirement asks for, and the check catches the case
    where the store returns something it should not, which a store is not the only possible source of.

    `denial` overrides the configured mode for one call. It exists so a test can exercise all three without
    touching the environment, and so a caller with a reason of its own can be explicit rather than relying on
    a global.
    """
    mode = denial_mode(denial)
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

    # Whether content the role may not see would have answered BETTER than anything it can see. This runs in
    # every mode, including silent, because the record has to be able to report that a partial answer was
    # returned — that is precisely what an auditor asks about, and a silent organisation is the one most
    # likely to be asked.
    best_permitted = max((p.score for p in result.passages), default=None)
    restricted_best = _restricted_best_score(services, vectors[0], roots)

    result.restricted_best_score = restricted_best
    if restricted_best is not None:
        # Only a BETTER match counts. A weak match exists for almost any question, and denying on one would
        # train users to ignore a warning that fires constantly, which costs the disclosure its only job.
        if best_permitted is None or restricted_best > best_permitted:
            result.denied = True
            # The facts, which are accurate in every mode and belong to the record.
            if result.withheld:
                result.reason = (
                    f"{result.withheld} passage(s) were returned that the {role!r} role may not see, which "
                    f"means the store did not apply the filter.")
            else:
                result.reason = (
                    f"a passage the {role!r} role may not see matched this question better than any passage "
                    f"it may see, so the answer drawn from what is available is incomplete by construction.")
            # The disclosure, which is governed by the client's policy and is the only part a user sees.
            result.disclosure = _disclose(mode, role, result.withheld)
    return result
