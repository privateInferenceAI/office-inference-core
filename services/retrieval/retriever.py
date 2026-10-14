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

# The reranker, imported defensively because a deployment without one is a configuration rather than a fault:
# the office build decides its own reranking on hardware that does not exist yet.
try:
    import rerank as rerank_module  # noqa: E402
except Exception:  # noqa: BLE001
    rerank_module = None

# The normalisation shared with the ingestion worker, imported from where the worker keeps it. It lives beside
# `ingest.py` rather than here because the ingestion image copies a fixed file list, and a module both images
# need has to sit inside one of their build contexts. The dependency already ran this way: this module imports
# `ingest` for the Services record, so the two are one unit whichever directory they are filed under.
import identifiers  # noqa: E402

# Reciprocal rank fusion's constant. The architecture fixes it at 60, and the value is conventional rather
# than tuned here: it damps the difference between the top few ranks so one arm's confident first place does
# not simply win, which is the behaviour that makes a second arm worth having.
RRF_K = 60

# Candidates taken from each arm before fusing. Equal weights, as the architecture specifies, so the number is
# the same for both arms and the fusion is the only thing that decides an order.
ARM_DEPTH = 500

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
    # The vector arm's cosine similarity, or None for a chunk only the lexical arm returned. This is the
    # number the denial comparison uses, because it is the only one comparable across arms: an RRF score is a
    # rank-derived quantity on a different scale, and comparing the two is what made a permitted passage look
    # weaker than a restricted one and denied an executive their own document.
    score: float | None = None
    # The fused score, kept for ordering and for a caller that wants to see why something ranked where it did.
    fused_score: float = 0.0
    is_ocr: bool = False
    # The parser's own confidence in the page this chunk came from. It is stored in the payload by the worker and
    # was NOT copied here, which meant `citations.verify_claim` read `getattr(passage, "parse_score", None)`,
    # always got None, and skipped every passage. **The confidence gate never refused anything on the path a real
    # question takes**, and only its unit tests supplied the attribute. A check that succeeds while doing nothing
    # is worse than no check, because it is reported as one.
    parse_score: float | None = None
    page_is_nominal: bool = False
    table_id: str | None = None
    row_index: int | None = None
    # Whether an answer may rely on this passage's document. True unless a human has declared otherwise, so a
    # corpus with no declarations behaves exactly as it did before the registry existed. This is a MARK rather
    # than a filter: the research is explicit that a claim supported only by a non-current document is refused
    # with the reason `superseded`, which means the passage has to reach the verifier carrying the fact.
    # Dropping it here would turn a refusal into a wrong answer from a different document.
    is_current: bool = True
    superseded_by: str | None = None

    @classmethod
    def from_payload(cls, payload: dict, score: float | None,
                     fused_score: float = 0.0) -> "Passage":
        return cls(
            doc_id=payload.get("doc_id") or "",
            acl=payload.get("acl") or "",
            text=payload.get("text") or "",
            page_start=int(payload.get("page_start") or 1),
            page_end=int(payload.get("page_end") or 1),
            kind=payload.get("kind") or "prose",
            chunk_id=payload.get("chunk_id") or "",
            score=score,
            fused_score=fused_score,
            is_ocr=bool(payload.get("is_ocr")),
            parse_score=(float(payload["parse_score"])
                         if payload.get("parse_score") is not None else None),
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
    # How many passages outside the role's roots matched the question. This is the number a `counted` denial
    # reports, because the number it used to report was `withheld`, which is zero on every question by
    # construction: the filter works, so nothing restricted is ever returned to be counted.
    restricted_matched: int = 0
    # What reranking did, or why it did not. Reported rather than assumed for the same reason the arms are:
    # a deployment answering from the fused order while believing it reranked is one whose measurements
    # describe a configuration it is not running.
    rerank: dict = field(default_factory=dict)
    # Which arms returned anything. A caller reading an empty result needs to know whether the lexical arm
    # ran and found nothing or did not run at all, because those are different faults.
    arms: dict[str, int] = field(default_factory=dict)
    # Documents a returned passage came from that a human has declared replaced. The caller decides what to
    # do about it: step 3.11 refuses an answer whose only support is one of these, and until then the fact is
    # carried so it can be reported rather than silently dropped.
    superseded: list[str] = field(default_factory=list)


def embed_query(services: ingest.Services, texts: list[str]) -> list[list[float]]:
    """The query's vector. A seam of its own so a test can watch what is embedded."""
    return ingest.embed_texts(services, texts)


# Whether this process has already checked the embedding contract for a collection. Checking on every search
# would add a database round trip and a call to the embedding service to every question, and the answer cannot
# change while the process runs: a deployment that swaps its embedding model restarts its readers.
_CONTRACT_CHECKED: set[str] = set()


def verify_contract(services: ingest.Services, connection=None) -> None:
    """Refuse to search a collection whose vectors came from a different embedding contract.

    RET-EMBED-001 asks for this on the READ path as well as the write one, and the reason is that a change only
    the writer notices is a reader answering from vectors it should not trust. The writer refusing to add to a
    mismatched collection does nothing about the queries already going to it.

    Cached per process, because the answer cannot change while the process runs and a question should not pay a
    database round trip and an HTTP call for a fact that was true at startup.

    Raising rather than warning. **A retrieval system that answers from meaningless vectors looks exactly like
    one answering a hard question**, so a warning is a line in a log nobody reads beside answers that look
    normal. The deployment stops answering until somebody decides, which is the direction that gets it fixed.
    """
    if services.collection in _CONTRACT_CHECKED:
        return
    if not services.database_url:
        return

    import contract as contract_module
    owned = connection is None
    if owned:
        import psycopg2
        connection = psycopg2.connect(services.database_url)
    try:
        # Measured here rather than read off the Services object. A reader that has not embedded anything has
        # never measured the size, and a contract carrying None for a field the writer filled in refuses every
        # search: that is what happened on the deployed run, and the eval harness reported all ten questions as
        # misses before anybody read the reason.
        live = contract_module.live_contract(
            services, info=contract_module.read_embedding_service(services.embed_url),
            dimension=ingest._get_vector_size(services))
        contract_module.check(connection, services.collection, live)
    finally:
        if owned:
            connection.close()
    _CONTRACT_CHECKED.add(services.collection)


def document_versions(services: ingest.Services) -> dict[str, bool]:
    """Which documents may be relied on, from the registry.

    Absent for a document with no declaration, which reads as current. A corpus that has never used the
    registry therefore behaves as though every document is current, which is what it did before this existed.

    `doc_id -> is_current`. **`superseded_by` is deliberately not merged in here** even though the same row
    carries it: this function's contract is a boolean per document, tests stub it with plain booleans, and
    widening the value type to satisfy one caller would make every caller check which shape it got. The
    replacement name is fetched separately by `superseded_replacement`, which costs nothing on the path
    that needs it and leaves this one alone.
    """
    if not services.database_url:
        return {}
    try:
        import psycopg2
        with psycopg2.connect(services.database_url) as connection:
            with connection.cursor() as cur:
                cur.execute("select doc_id, is_current from document_versions")
                return {row[0]: row[1] for row in cur.fetchall()}
    except Exception as exc:  # noqa: BLE001
        # A registry that cannot be read must not stop retrieval. Every document reads as current, which is
        # the permissive direction, so this says so rather than failing quietly: the alternative is refusing
        # answers because a table was unreachable.
        print(f"could not read the version registry, so every document is treated as current: "
              f"{type(exc).__name__}: {exc}")
        return {}


def superseded_replacement(services: ingest.Services, doc_id: str) -> str | None:
    """What replaced this document, when a person recorded one.

    Separate from `document_versions`, and separate on purpose. That function answers "may I rely on this"
    for every document a single query needs, and its value type is a boolean that callers and tests rely on.
    This answers "what took its place" for the one passage being marked, which is a different question asked
    once per superseded passage rather than once per search.

    The marker used to read `superseded by None` because `search` set `is_current` from the registry and never
    read the replacement name, so the fact that a document had been replaced reached the reader and the name of
    its replacement did not. The name is the useful half: it tells a person which document to look at instead.
    """
    if not services.database_url:
        return None
    try:
        import psycopg2
        with psycopg2.connect(services.database_url) as connection:
            with connection.cursor() as cur:
                cur.execute("select superseded_by from document_versions where doc_id = %s", (doc_id,))
                row = cur.fetchone()
        return (row[0] or None) if row else None
    except Exception as exc:  # noqa: BLE001
        # Same permissive direction as `document_versions`: a registry that cannot be read does not stop
        # retrieval, and an unnamed replacement is a worse marker than a named one but not a wrong one.
        print(f"could not read the replacement for {doc_id!r}: {type(exc).__name__}: {exc}")
        return None


def lexical_search(services: ingest.Services, question: str, roots: frozenset[str],
                   limit: int = ARM_DEPTH) -> list[str]:
    """Chunk ids from the lexical arm, best first, restricted to the roots the role may see.

    Two match strategies in one query, because they answer different questions and the measurement showed
    neither alone is enough:

      identifiers  exact references, matched with `&&` against the normalised tokens. This is what finds
                   `Pub. 515` and `Form W-3SS`, and it is the reason the arm exists at all.
      tsv          stemmed words, which is what full-text is good at and what catches a phrasing the vector
                   arm reads past.

    An identifier match outranks a word match, because a question that contains a reference is usually asking
    about that reference. Within each group the order is the database's rank with the chunk id as a final
    tie-break, so two identical queries cannot return different orders.

    The access label is a clause in this query, exactly as it is in the vector one. A lexical arm without it
    would be a second path to the same content with no filter on it, which is the leak step 3.8 closed
    arriving by a new road.
    """
    dsn = services.database_url
    if not dsn:
        return []
    tokens = identifiers.extract(question)

    import psycopg2
    sql = """
        with permitted as (
          select chunk_id, tsv, identifiers
          from chunk_index
          where acl = any(%(roots)s)
        ),
        by_identifier as (
          select chunk_id, 0 as tie, 0 as arm_rank
          from permitted
          where identifiers && %(tokens)s
          order by chunk_id
          limit %(limit)s
        ),
        by_word as (
          select chunk_id, 1 as tie,
                 row_number() over (
                   order by ts_rank(tsv, websearch_to_tsquery('english', %(question)s)) desc, chunk_id
                 ) as arm_rank
          from permitted
          where tsv @@ websearch_to_tsquery('english', %(question)s)
          order by ts_rank(tsv, websearch_to_tsquery('english', %(question)s)) desc, chunk_id
          limit %(limit)s
        )
        select chunk_id from (
          select chunk_id, tie, arm_rank from by_identifier
          union all
          select chunk_id, tie, arm_rank from by_word
        ) as merged
        order by tie, arm_rank, chunk_id
        limit %(limit)s
    """
    try:
        with psycopg2.connect(dsn) as connection:
            with connection.cursor() as cur:
                cur.execute(sql, {"roots": sorted(roots), "tokens": tokens or [""],
                                  "question": question, "limit": limit})
                return [row[0] for row in cur.fetchall()]
    except Exception as exc:  # noqa: BLE001
        # A lexical arm that is down must not take retrieval with it. The dense arm still answers, and the
        # result records that the lexical arm contributed nothing so a caller can see the difference between
        # "no lexical match" and "no lexical arm".
        print(f"lexical arm unavailable: {type(exc).__name__}: {exc}")
        return []


def reciprocal_rank_fusion(arms: list[list[str]], k: int = RRF_K) -> list[tuple[str, float]]:
    """Fuse ranked id lists into one ranking, by reciprocal rank.

    The reason the architecture keeps this in our own code rather than using a store's: determinism. A citation
    that moves between two identical runs is a citation nobody can check, and a fusion implemented inside a
    database is one whose tie-breaking we do not control.

    Score is the sum over arms of 1/(k + rank). Ties break on chunk id, which is derived from the document, the
    content hash and the chunk's own text, so the order is stable across processes and runs.
    """
    scores: dict[str, float] = {}
    for arm in arms:
        for rank, chunk_id in enumerate(arm):
            scores[chunk_id] = scores.get(chunk_id, 0.0) + 1.0 / (k + rank + 1)
    return sorted(scores.items(), key=lambda item: (-item[1], item[0]))


def _fetch_payloads(services: ingest.Services, chunk_ids: list[str],
                    roots: frozenset[str]) -> dict[str, dict]:
    """Payloads for chunk ids the vector arm did not return, restricted to the permitted roots.

    The filter is applied here as well as in the lexical query, because this call reaches the vector store
    directly by id: it is a second path to content, and a second path is where a missing filter hides.
    """
    if not chunk_ids:
        return {}
    body = {"ids": chunk_ids, "with_payload": True,
            "filter": {"must": [{"key": "acl", "match": {"any": sorted(roots)}}]}}
    request = urllib.request.Request(
        f"{services.vector_url}/collections/{services.collection}/points",
        data=json.dumps(body).encode(), method="POST",
        headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=services.timeout) as response:
            points = json.loads(response.read()).get("result") or []
    except Exception as exc:  # noqa: BLE001
        print(f"could not fetch payloads for the lexical arm: {type(exc).__name__}: {exc}")
        return {}
    out: dict[str, dict] = {}
    for point in points:
        payload = point.get("payload") or {}
        if (payload.get("acl") or "") in roots:
            out[payload.get("chunk_id") or point.get("id") or ""] = payload
    return out


def _restricted_reach(services: ingest.Services, vector: list[float],
                      roots: frozenset[str], limit: int = 50) -> tuple[float | None, int]:
    """The best score among passages this role may NOT see, and how many there are.

    The count is here because the requirement's `counted` mode says a denial carries a number, and the number
    it carried was zero. The reason is the design that made the denial work at all: the access filter is in the
    query and the store honours it, so nothing restricted is ever RETURNED and `withheld` is honestly zero on
    every question. **A mode whose purpose is to say how many passages were withheld was therefore always
    silent about the number**, which is the disclosure doing nothing in the case it was written for.

    What it counts instead is how many restricted passages matched, which is the fact the user is being told
    about: not what was filtered out of this answer, but how much exists that would have answered. The count is
    over the same limit as the search, so it reads "at least this many" rather than an exact total, and the
    message says so.
    """
    forbidden = {"must_not": [{"key": "acl", "match": {"any": sorted(roots)}}]}
    hits = _search(services, {"vector": vector, "limit": limit, "with_payload": False,
                              "filter": forbidden})
    if not hits:
        return None, 0
    best = max(float(hit.get("score") or 0.0) for hit in hits)
    return best, len(hits)


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


def permitted_roots(role: str, permit=None) -> frozenset[str]:
    """The roots a role may see, or a refusal.

    An unknown role raises rather than defaulting, and a role with an empty permission set raises too. The
    second is the one worth stating: a role that may see nothing is a configuration error, and treating it
    as a legitimate role that happens to see nothing makes a mistake look like a user with no documents.

    **`permit` is where `SEC-ACL-002` enters, and it replaces the role rather than adding to it.** When a
    session resolved the access, those roots are the permission: the role is not consulted at all, so there
    is no precedence between two sources and nothing for a caller to widen by passing both. The object is
    duck-typed rather than imported, because this module is shared with the ingestion image and the
    permission registry is not — and because a retriever that can name the registry is one that could
    consult it as a fallback, which is the failure this signature exists to prevent.
    """
    if permit is not None:
        roots = getattr(permit, "roots", None)
        if not isinstance(roots, (frozenset, set, tuple, list)) or not roots:
            raise ValueError(
                "the permit resolved no document roots, which is a configuration error rather than a user "
                "with no documents")
        return frozenset(roots)
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
        # The count is how many restricted passages matched, not how many were withheld, and the wording says
        # which. `withheld` is zero by construction on a working filter, so a disclosure built on it would
        # always report a number it does not have.
        if not withheld:
            return Disclosure(
                mode=mode.value,
                message=(f"Documents that answer this more directly exist and are not available to the {role!r} "
                         f"role. The answer below is drawn from what you can see and may be incomplete."),
                count=None)
        return Disclosure(
            mode=mode.value,
            message=(f"At least {withheld} passage(s) that answer this more directly exist and are not "
                     f"available to the {role!r} role. The answer below is drawn from what you can see and may "
                     f"be incomplete."),
            count=withheld)
    return Disclosure(
        mode=mode.value,
        message=(f"Documents that answer this more directly exist and are not available to the {role!r} "
                 f"role. The answer below is drawn from what you can see and may be incomplete."),
        count=None)


def search(services: ingest.Services, question: str, role: str, limit: int = 10,
           denial: str | None = None, rerank_passages: bool = True, permit=None) -> Result:
    """Retrieve the passages a role may see, with the restriction applied by the store.

    The filter is a clause in the query, and the check afterwards is a second line. Both are present because
    they fail differently: the query clause is what the requirement asks for, and the check catches the case
    where the store returns something it should not, which a store is not the only possible source of.

    `denial` overrides the configured mode for one call. It exists so a test can exercise all three without
    touching the environment, and so a caller with a reason of its own can be explicit rather than relying on
    a global.

    **`permit` is the session's access decision and it supersedes `role`.** A caller that holds a session
    passes the permit and the role beside it for the record; the roots come from the permit alone. A caller
    with no session — the probes, the tests, a diagnostic — passes the role and gets the deployment's
    default, which is why every existing caller keeps working unchanged.
    """
    mode = denial_mode(denial)
    roots = permitted_roots(role, permit)
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
    # Before the search, so a reader cannot get an answer built on vectors from another contract. Cheap after
    # the first call in a process, which is what makes it affordable on the path a question takes.
    verify_contract(services)

    raw = _search(services, body)

    dense: list[str] = []
    by_id: dict[str, dict] = {}
    similarities: dict[str, float] = {}
    for item in raw:
        payload = item.get("payload") or {}
        acl = payload.get("acl") or ""
        if acl not in roots:
            # The store returned something the role may not see. It should not have, given the query, and this
            # is the line that keeps a store bug from becoming a disclosure.
            result.withheld += 1
            continue
        chunk_id = payload.get("chunk_id") or item.get("id") or ""
        if chunk_id:
            dense.append(chunk_id)
            by_id[chunk_id] = payload
            similarities[chunk_id] = float(item.get("score") or 0.0)
    result.arms["dense"] = len(dense)

    # The lexical arm, filtered by the same roots inside its own query.
    lexical = lexical_search(services, question, roots)
    result.arms["lexical"] = len(lexical)

    # Fusion decides the order; the payloads come from whichever arm has them. A chunk only the lexical arm
    # returned needs its payload fetched, because a citation needs the text and the page.
    fused = reciprocal_rank_fusion([dense, lexical])

    # The reranker reads the question against the candidate texts, so it needs the texts. They are fetched here
    # rather than after a limit is applied, because the passages it must see are the ones the fusion put in its
    # head and not the ones a limit happened to keep.
    if rerank_passages and fused and rerank_module is not None:
        head_ids = [chunk_id for chunk_id, _score in fused[:rerank_module.RERANK_DEPTH]]
        need = [chunk_id for chunk_id in head_ids if chunk_id not in by_id]
        if need:
            by_id.update(_fetch_payloads(services, need, roots))
        head_passages = [Passage.from_payload(by_id[chunk_id], similarities.get(chunk_id), 0.0)
                         for chunk_id in head_ids if chunk_id in by_id]
        outcome = rerank_module.rerank(question, head_passages)
        result.rerank = outcome.as_record()
        if outcome.ran and outcome.order:
            reordered = rerank_module.apply_order(head_passages, outcome)
            # The order is over positions in the head, so the head ids are reordered in place and the fusion's
            # tail keeps its order behind them. A reranker that saw the top of a list cannot reorder the rest.
            position = {id(passage): index for index, passage in enumerate(head_passages)}
            new_head = [head_ids[position[id(passage)]] for passage in reordered if id(passage) in position]
            new_head.extend(head_ids[len(new_head):])
            fused = [(chunk_id, 0.0) for chunk_id in new_head] + fused[len(head_ids):]
    missing = [chunk_id for chunk_id, _ in fused[:limit] if chunk_id not in by_id]
    if missing:
        by_id.update(_fetch_payloads(services, missing, roots))

    versions = document_versions(services)
    for chunk_id, _score in fused:
        if len(result.passages) >= limit:
            break
        payload = by_id.get(chunk_id)
        if payload is None:
            # The lexical arm named a chunk the vector store does not hold, which means the two stores have
            # drifted. Counted as withheld rather than skipped silently, because a chunk that is findable in
            # one place and not the other is a reconciliation fault worth seeing.
            result.withheld += 1
            continue
        passage = Passage.from_payload(payload, similarities.get(chunk_id), _score)
        # Default true, so only a declaration can mark a passage. A document nobody has ruled on is usable.
        passage.is_current = versions.get(passage.doc_id, True)
        if not passage.is_current:
            # Named, not just flagged: the marker read `superseded by None` until this line, because the
            # registry's boolean was read and its replacement column was not.
            passage.superseded_by = superseded_replacement(services, passage.doc_id)
            result.superseded.append(passage.doc_id)
        result.passages.append(passage)

    # Whether content the role may not see would have answered BETTER than anything it can see. This runs in
    # every mode, including silent, because the record has to be able to report that a partial answer was
    # returned — that is precisely what an auditor asks about, and a silent organisation is the one most
    # likely to be asked.
    # Vector similarity only, because that is the one scale both sides of this comparison share. A passage the
    # lexical arm alone returned has no similarity and cannot be compared, so it is skipped rather than
    # treated as zero, which would deny every question the lexical arm answered.
    permitted_scores = [p.score for p in result.passages if p.score is not None]
    best_permitted = max(permitted_scores, default=None)
    restricted_best, restricted_count = _restricted_reach(services, vectors[0], roots)

    result.restricted_best_score = restricted_best
    if restricted_best is not None:
        # Only a BETTER match counts. A weak match exists for almost any question, and denying on one would
        # train users to ignore a warning that fires constantly, which costs the disclosure its only job.
        if best_permitted is None:
            # The permitted side returned nothing with a similarity, so there is no scale to compare on. A
            # restricted match is treated as decisive, because the alternative is silence while content that
            # answers the question exists.
            result.denied = True
            result.reason = (
                f"nothing available to the {role!r} role matched this question, and a passage it may not see "
                f"did.")
            result.restricted_matched = restricted_count
            result.disclosure = _disclose(mode, role, restricted_count)
            return result
        if restricted_best > best_permitted:
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
            result.restricted_matched = restricted_count
            result.disclosure = _disclose(mode, role, restricted_count)
    return result
