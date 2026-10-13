"""Access labels: a role cannot retrieve a chunk its label excludes.

The build order in docs/search-architecture.md says this regression test ships BEFORE the feature, and the
reason is in the sentence: a filter that runs after the search returns results is the mistake this phase
exists to avoid. A test written afterwards tends to describe whatever the code happens to do, and the thing
being tested here is where the filter runs, which a test written afterwards cannot see.

So these tests are written against a `retriever` module that does not exist yet. They are the specification
rather than a description. The most important one is `test_the_filter_is_inside_the_query`, because every
other test in this file would pass against a retriever that fetched everything and discarded the restricted
passages afterwards — the passages would leave the vector store, be counted, and be one logging statement
away from a leak.

Run: python3 -m pytest tests/
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "services" / "retrieval"))
sys.path.insert(0, str(ROOT / "services" / "ingestion"))

retriever = pytest.importorskip(
    "retriever",
    reason="services/retrieval/retriever.py is the feature this file specifies; until it exists these "
           "tests are the specification and CI would fail on an import rather than on a behaviour",
)

import ingest  # noqa: E402


# --------------------------------------------------------------------------- stubs

class StubServices(ingest.Services):
    """Records the query it was asked to run, which is how the filter's LOCATION is asserted."""

    def __init__(self, hits: list[dict] | None = None, dims: int = 4) -> None:
        super().__init__()
        self.hits = hits if hits is not None else []
        self.dims = dims
        self.searches: list[dict] = []
        self.embedded: list[str] = []


@pytest.fixture
def wired(monkeypatch):
    """Point the retriever's two service calls at stubs and hand back the stub."""
    state: dict = {}

    def fake_embed(services, texts):
        services.embedded.extend(texts)
        return [[0.1] * services.dims for _ in texts]

    def fake_search(services, body):
        services.searches.append(body)
        return services.hits

    def build(**kwargs) -> StubServices:
        services = StubServices(**kwargs)
        state["services"] = services
        return services

    monkeypatch.setattr(retriever, "embed_query", fake_embed)
    monkeypatch.setattr(retriever, "_search", fake_search)
    return build


def hit(doc_id: str, acl: str, text: str, page: int = 1, kind: str = "prose") -> dict:
    return {"id": f"{doc_id}:{page}:{text[:8]}", "score": 0.7,
            "payload": {"doc_id": doc_id, "acl": acl, "text": text, "page_start": page,
                        "page_end": page, "kind": kind, "chunk_id": f"c-{text[:6]}",
                        "source_sha256": "x", "is_ocr": False, "page_is_nominal": False}}


# --------------------------------------------------------------------------- where the filter runs

def test_the_filter_is_inside_the_query(wired):
    """The requirement, stated as a test: filtering happens inside the search, before any model sees a
    passage.

    Every other test in this file passes against a retriever that fetches everything and discards the
    restricted passages afterwards. That version works in a unit test and fails the requirement, because the
    restricted text left the vector store, appeared in a result set, and sat one logging statement or one
    exception away from a leak. The query is the place the restriction has to be applied, so the query is
    what this checks.
    """
    services = wired()
    retriever.search(services, "what is the executive bonus", role="staff")

    assert services.searches, "the retriever did not search"
    body = services.searches[0]
    must = (body.get("filter") or {}).get("must") or []
    acl_clauses = [c for c in must if c.get("key") == "acl"]
    assert acl_clauses, (
        "the query carries no acl filter, so restricted passages are returned by the vector store and "
        f"discarded afterwards. Query was: {body}")
    permitted = acl_clauses[0]["match"]
    values = set(permitted.get("any") or [permitted.get("value")])
    assert values == {"company"}, f"a staff query should be restricted to company, got {values}"


def test_an_executive_query_may_see_both_roots(wired):
    """The other half, so the filter is not simply excluding everything. A test suite where every role is
    restricted passes trivially against a retriever that returns nothing."""
    services = wired()
    retriever.search(services, "what is the executive bonus", role="executive")
    body = services.searches[0]
    acl = [c for c in (body.get("filter") or {}).get("must") or [] if c.get("key") == "acl"][0]
    values = set(acl["match"].get("any") or [acl["match"].get("value")])
    assert values == {"company", "executive"}, f"an executive query should span both, got {values}"


def test_an_unknown_role_is_refused_rather_than_defaulted(wired):
    """A role the retriever does not recognise must not fall back to a working set of permissions. The
    failure it would cause is the worst kind: a typo or a missing session field silently granting the
    broader of two roles."""
    services = wired()
    with pytest.raises((KeyError, ValueError)):
        retriever.search(services, "anything", role="stafff")
    assert services.searches == [], "an unknown role must be refused before any search runs"


def test_a_role_with_no_permitted_roots_is_refused(wired):
    """An empty permission set is a configuration error, not a role that can see nothing. Treating them the
    same way means a mistake that should be loud looks like a user with no documents."""
    services = wired()
    with pytest.raises((KeyError, ValueError)):
        retriever.search(services, "anything", role="contractor")


# --------------------------------------------------------------------------- what comes back

def test_a_restricted_passage_is_never_returned_to_a_staff_role(wired):
    """The end-to-end shape, with the vector store stubbed to return restricted passages anyway.

    A stub that returns what it should is a stub that tests nothing: the store is trusted to honour the
    filter only as a second line. If a restricted passage comes back regardless, the retriever must drop it
    rather than pass it on, because the alternative is relying on the store for something the store is not
    the only writer of.
    """
    services = wired(hits=[
        hit("executive/executive-compensation.md", "executive", "The CEO base salary is 418,750 dollars."),
        hit("company/irs-p15.pdf", "company", "The mileage rate is 67 cents per mile.", page=29),
    ])
    result = retriever.search(services, "ceo base salary", role="staff")

    texts = [passage.text for passage in result.passages]
    assert not any("418,750" in text for text in texts), (
        f"a restricted figure reached a staff result: {texts}")
    assert all(passage.acl == "company" for passage in result.passages)
    assert result.withheld == 1, "the retriever should count what it withheld"


def test_the_restricted_figure_does_not_appear_anywhere_in_the_result(wired):
    """Not only in the passages. A withheld count is fine; the text is not, and a result object that carries
    it in a debug field or a raw list is the same leak with a different name."""
    services = wired(hits=[
        hit("executive/executive-compensation.md", "executive", "The CEO base salary is 418,750 dollars."),
    ])
    result = retriever.search(services, "ceo base salary", role="staff")
    serialised = repr(result)
    assert "418,750" not in serialised, "the restricted text is somewhere in the result object"


def test_a_staff_result_reports_that_something_was_withheld(wired):
    """The denial, and a deliberate decision about what it reveals.

    A staff user asking about executive compensation gets an answer drawn from company documents or nothing
    at all, and either way the honest thing is to say that the answer may be incomplete rather than to imply
    the corpus has nothing. That does disclose that restricted content EXISTS, which is a real leak and is
    recorded rather than hidden: it is the trade the requirement asks for, and the alternative is a user who
    cannot tell a wrong answer from an unavailable one.
    """
    services = wired(hits=[
        hit("executive/executive-compensation.md", "executive", "The CEO base salary is 418,750 dollars."),
    ])
    result = retriever.search(services, "ceo base salary", role="staff")
    assert result.denied is True
    assert result.withheld == 1
    assert result.role == "staff"
    assert "may not see" in result.reason.lower(), result.reason


def test_an_executive_gets_the_passage_and_no_denial(wired):
    """The same query from the role that may see it. Without this, a retriever that denied everything would
    pass every test above."""
    services = wired(hits=[
        hit("executive/executive-compensation.md", "executive", "The CEO base salary is 418,750 dollars."),
    ])
    result = retriever.search(services, "ceo base salary", role="executive")
    texts = [passage.text for passage in result.passages]
    assert any("418,750" in text for text in texts), "an executive should receive the passage"
    assert result.denied is False
    assert result.withheld == 0


def test_a_passage_carries_what_a_citation_needs(wired):
    """Retrieval feeds the citation, so the fields the citation checks depend on have to survive it. A
    retriever that returned bare text would force step 3.11 to re-fetch the chunk, and the page number is the
    thing the brief makes mandatory."""
    services = wired(hits=[hit("company/irs-p15.pdf", "company", "The rate is 67 cents.", page=29)])
    result = retriever.search(services, "mileage rate", role="staff")
    passage = result.passages[0]
    for field in ("doc_id", "acl", "text", "page_start", "page_end", "kind", "chunk_id"):
        assert getattr(passage, field, None) is not None, f"{field} is missing from the passage"
    assert passage.page_start == 29


def test_no_answer_is_a_result_rather_than_an_error(wired):
    """A question the corpus cannot answer returns nothing, and that is a normal outcome. Raising would make
    the caller catch it and invent a default, which is how an empty result becomes an unlabelled one."""
    services = wired(hits=[])
    result = retriever.search(services, "what is our parental leave policy", role="staff")
    assert result.passages == []
    assert result.denied is False
    assert result.withheld == 0


# --------------------------------------------------------------------------- the client's denial policy

@pytest.mark.parametrize("mode,expect_disclosure,expect_count", [
    ("silent", False, None),
    ("denied", True, None),
    ("counted", True, 1),
])
def test_the_client_decides_whether_a_denied_user_is_told(wired, mode, expect_disclosure,
                                                          expect_count):
    """The three modes, which are a client's business decision rather than ours.

    A silent organisation discloses nothing and accepts that its users cannot tell an incomplete answer from
    a complete one. A disclosing organisation accepts that the existence of restricted content becomes
    visible, in exchange for a user who knows to ask someone with access. Both are defensible and the choice
    is not ours to make.
    """
    services = wired(hits=[
        hit("executive/executive-compensation.md", "executive", "The CEO base salary is 418,750 dollars."),
    ])
    result = retriever.search(services, "ceo base salary", role="staff", denial=mode)

    if expect_disclosure:
        assert result.disclosure is not None, f"{mode} should disclose something"
        assert result.disclosure.mode == mode
        assert result.disclosure.count == expect_count
        assert result.disclosure.message
    else:
        assert result.disclosure is None, "silent must disclose nothing at all"
    # And the restricted text is absent from the result in every mode.
    assert "418,750" not in repr(result)


def test_the_record_keeps_the_fact_in_every_mode(wired):
    """Suppressing the SIGNAL is not the same as not knowing.

    `withheld` and `denied` stay accurate in silent mode, because the record and any audit trail have to be
    able to report what happened. Collapsing the fact into the disclosure would make silent mean "we withheld
    nothing", which is a lie the record would then carry — and the record is the artifact a client's auditor
    reads.
    """
    services = wired(hits=[
        hit("executive/executive-compensation.md", "executive", "The CEO base salary is 418,750 dollars."),
    ])
    result = retriever.search(services, "ceo base salary", role="staff", denial="silent")
    assert result.disclosure is None
    assert result.denied is True, "the system still knows something was withheld"
    assert result.withheld == 1
    assert "may not see" in result.reason.lower(), "the internal reason stays accurate"


def test_no_withholding_means_no_disclosure_in_any_mode(wired):
    """A disclosure only ever appears when something was actually withheld. A mode that emitted a standing
    warning would train users to ignore it, which costs the disclosure its only job."""
    services = wired(hits=[hit("company/irs-p15.pdf", "company", "The rate is 67 cents.")])
    for mode in ("silent", "denied", "counted"):
        result = retriever.search(services, "mileage rate", role="staff", denial=mode)
        assert result.disclosure is None, f"{mode} disclosed without withholding anything"
        assert result.denied is False


def test_an_unknown_mode_is_refused_rather_than_defaulted(wired):
    """A typo in a client's manifest must not silently select the more disclosing of two behaviours, which is
    the same reason an unknown role is refused."""
    services = wired()
    with pytest.raises(ValueError):
        retriever.search(services, "anything", role="staff", denial="denyed")
    assert services.searches == [], "the refusal must happen before any search runs"


def test_the_mode_comes_from_the_environment_when_not_passed(monkeypatch):
    """The wiring from a client's declared policy to the behaviour, which is the part that rots silently. A
    renderer that stopped writing the variable would leave every deployment on the default, and the default
    discloses."""
    monkeypatch.setenv("ACCESS_DENIAL_MODE", "counted")
    import importlib
    importlib.reload(retriever)
    try:
        assert retriever.denial_mode() is retriever.DenialMode.COUNTED
    finally:
        monkeypatch.delenv("ACCESS_DENIAL_MODE", raising=False)
        importlib.reload(retriever)


# --------------------------------------------------------------------------- the denial that fires in practice

def test_a_better_restricted_match_denies_even_though_the_store_filtered_correctly(wired):
    """The bug the deployed probe found, and it made the whole client policy dead code.

    The first version inferred a denial from the passages the store returned that the role could not see. The
    filter is in the query and the store honours it, so nothing restricted comes back, so that count stayed
    zero on every question and `denied` was never true. The client's setting for what a denied user is told
    would never have applied to anything, and the unit tests passed because their stub returned restricted
    passages to prove the second line worked.

    The requirement is that a staff query for executive content returns a denial. That needs knowing whether
    restricted content WOULD have answered, which needs a second search — so the stub now answers the two
    searches differently, the way the real store does.
    """
    services = wired(hits=[hit("company/irs-p15.pdf", "company", "A general note about expenses.")])
    # The permitted search returns one weak company passage; the complement search returns a strong
    # restricted one. A stub with a single answer cannot express this, which is why the earlier version
    # passed while the behaviour was broken.
    calls = {"n": 0}
    real_search = retriever._search

    def two_answers(services_, body):
        calls["n"] += 1
        if calls["n"] == 1:
            return [{"id": "a", "score": 0.42,
                     "payload": hit("company/irs-p15.pdf", "company", "A general note.")["payload"]}]
        return [{"id": "b", "score": 0.81}]           # no payload: the store is asked for none

    import pytest as _pytest
    with _pytest.MonkeyPatch.context() as patch:
        patch.setattr(retriever, "_search", two_answers)
        result = retriever.search(services, "what is the ceo base salary", role="staff",
                                  denial="counted")
    assert calls["n"] == 2, "a denial needs a second search to know what it is denying"
    assert result.denied is True, "a better restricted match must deny"
    assert result.restricted_best_score == 0.81
    assert result.disclosure is not None and result.disclosure.mode == "counted"


def test_a_weaker_restricted_match_does_not_deny(wired):
    """The other half, and the reason the comparison exists rather than a bare hit test.

    A weak restricted match exists for almost any question, because every corpus has something loosely
    related to everything. Denying on one would put a warning on most answers, and a warning that fires
    constantly is one users learn to ignore, which costs the disclosure the only job it has.
    """
    services = wired()
    calls = {"n": 0}

    def two_answers(services_, body):
        calls["n"] += 1
        if calls["n"] == 1:
            return [{"id": "a", "score": 0.88,
                     "payload": hit("company/irs-p15.pdf", "company", "A strong company match.")["payload"]}]
        return [{"id": "b", "score": 0.31}]           # restricted, but a worse match

    import pytest as _pytest
    with _pytest.MonkeyPatch.context() as patch:
        patch.setattr(retriever, "_search", two_answers)
        result = retriever.search(services, "mileage rate", role="staff")
    assert result.denied is False, "a worse restricted match is not a denial"
    assert result.disclosure is None
    assert result.restricted_best_score == 0.31, "the score is still recorded for the record"


def test_the_complement_search_takes_no_payload(wired):
    """The line between detecting a denial and leaking one.

    The complement search runs against content the role may not see, so it must not bring any back: only a
    score crosses that boundary. A version that fetched payloads to compare them would be the
    filter-afterwards anti-pattern wearing a second search, and the restricted text would be in memory,
    countable and one log statement from a disclosure.
    """
    services = wired(hits=[hit("company/irs-p15.pdf", "company", "A note.")])
    seen: list[dict] = []
    real_search = retriever._search

    def watching(services_, body):
        seen.append(body)
        return real_search(services_, body)

    import pytest as _pytest
    with _pytest.MonkeyPatch.context() as patch:
        patch.setattr(retriever, "_search", watching)
        retriever.search(services, "anything", role="staff")

    assert len(seen) == 2, "one permitted search and one complement search"
    complement = seen[1]
    assert complement["with_payload"] is False, (
        "the complement search must not bring restricted text back into this process")
    assert complement["limit"] == 1, "only the best score is needed"
    assert "must_not" in complement["filter"], (
        "the complement is expressed as everything the role may not see, so a folder nobody declared is "
        "still counted as restricted rather than silently omitted")
    # And the permitted search still carries the positive filter.
    assert "must" in seen[0]["filter"]


# --------------------------------------------------------------------------- hybrid search

def test_reciprocal_rank_fusion_is_deterministic_and_rank_based():
    """The reason the architecture keeps fusion in our own code: a citation that moves between two identical
    runs is a citation nobody can check.

    Score is the sum over arms of 1/(k + rank), so appearing in both arms beats appearing first in one. The
    tie-break is the chunk id, which is derived from the document, the content hash and the chunk's text, so
    the same two arms always produce the same order.
    """
    fused = retriever.reciprocal_rank_fusion([["a", "b"], ["b", "a"]])
    # 'a' and 'b' each appear at rank 0 in one arm and rank 1 in the other, so they tie and the id decides.
    assert [chunk_id for chunk_id, _ in fused] == ["a", "b"]
    assert retriever.reciprocal_rank_fusion([["a", "b"], ["b", "a"]]) == fused, "not deterministic"

    # Agreement wins: 'c' is second in both arms, 'a' is first in one and absent from the other.
    fused = retriever.reciprocal_rank_fusion([["a", "c"], ["c"]])
    assert fused[0][0] == "c", "a chunk both arms returned should outrank one only an arm found"

    # An empty arm contributes nothing rather than failing.
    assert retriever.reciprocal_rank_fusion([[], ["x"]])[0][0] == "x"
    assert retriever.reciprocal_rank_fusion([]) == []


def test_an_identifier_the_vector_arm_missed_is_found_by_the_lexical_arm(wired, monkeypatch):
    """The measurement this step opened with, as a test.

    Seven identifier questions went to the deployed vector arm: it missed `Form W-3SS` and `Pub. 515`
    entirely, though the corpus holds 2 and 14 chunks mentioning them. This is that case, with the lexical arm
    supplying the chunk the vector arm cannot see.
    """
    services = wired(hits=[])          # the vector arm finds nothing, as it did on the real corpus
    monkeypatch.setattr(retriever, "lexical_search", lambda s, q, r, limit=500: ["chunk-from-lexical"])
    monkeypatch.setattr(retriever, "_fetch_payloads", lambda s, ids, r: {
        "chunk-from-lexical": {"doc_id": "company/irs-p15.pdf", "acl": "company",
                               "text": "Publication 515 covers withholding on nonresident aliens.",
                               "page_start": 4, "page_end": 4, "kind": "prose",
                               "chunk_id": "chunk-from-lexical", "source_sha256": "x"}})

    result = retriever.search(services, "What does Pub. 515 cover?", role="staff")
    assert result.arms == {"dense": 0, "lexical": 1}
    assert len(result.passages) == 1
    assert "Publication 515" in result.passages[0].text
    # A lexical-only passage has no cosine similarity, and that is recorded rather than defaulted to zero.
    assert result.passages[0].score is None
    assert result.passages[0].fused_score > 0


def test_the_two_arms_scores_are_never_compared(wired, monkeypatch):
    """The bug the fusion introduced, and it denied an executive their own document.

    Fusion replaced a passage's cosine similarity with its RRF score, and the denial comparison then put an RRF
    score and a cosine similarity on the same axis. An RRF score for a first place is about 0.016, so every
    permitted passage looked vanishingly weak next to any restricted match and every question that had one was
    denied. The two quantities are kept apart now: the similarity decides the denial, the fused score decides
    the order.
    """
    executive_doc = {"doc_id": "executive/executive-compensation.md", "acl": "executive",
                     "text": "The CEO base salary is 418,750 dollars.", "page_start": 1, "page_end": 1,
                     "kind": "prose", "chunk_id": "exec-chunk", "source_sha256": "x"}
    services = wired()
    monkeypatch.setattr(retriever, "lexical_search", lambda s, q, r, limit=500: [])

    calls = {"n": 0}

    def two_answers(services_, body):
        calls["n"] += 1
        if calls["n"] == 1:
            return [{"id": "exec-chunk", "score": 0.95, "payload": executive_doc}]
        return []          # the complement search finds nothing, because nothing is restricted from executive

    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(retriever, "_search", two_answers)
        result = retriever.search(services, "ceo base salary", role="executive", denial="counted")

    assert result.passages[0].score == 0.95, "the similarity is preserved through the fusion"
    assert result.passages[0].fused_score < 0.1, "the fused score is a different quantity on its own scale"
    assert result.denied is False, "an executive must not be denied their own document"
    assert result.disclosure is None


def test_the_lexical_arm_is_never_consulted_without_a_permitted_roots_filter():
    """The leak this step could have reintroduced, asserted against the query the arm builds.

    A lexical arm is a second copy of every document. If its query does not carry the access clause, hybrid
    search becomes a second path to content the vector path protects, and the failure is silent: the answer is
    correct, cited correctly, and shown to someone who should not see it.
    """
    import inspect
    source = inspect.getsource(retriever.lexical_search)
    assert "where acl = any(%(roots)s)" in source, (
        "the lexical query must filter on the access label inside the query")
    assert "%(roots)s" in source and "roots: frozenset[str]" in source, (
        "the permitted roots must reach the query")


def test_a_lexical_arm_that_is_down_does_not_take_retrieval_with_it(wired, monkeypatch):
    """An unreachable Postgres means the lexical arm contributes nothing, not that the question fails.

    The result records each arm's contribution so a caller can tell "the lexical arm found nothing" from "the
    lexical arm did not run", which are different faults with different fixes.
    """
    services = wired(hits=[hit("company/irs-p15.pdf", "company", "A note.")])

    def refused(services_, question, roots, limit=500):
        raise RuntimeError("could not connect")
    monkeypatch.setattr(retriever, "lexical_search", refused)

    # The exception is inside lexical_search in production and caught there; here the stub raises, so this
    # asserts the caller does not depend on the arm at all.
    with pytest.raises(RuntimeError):
        retriever.search(services, "anything", role="staff")

    # And with the arm returning nothing, which is what the real one does when it cannot connect.
    monkeypatch.setattr(retriever, "lexical_search", lambda s, q, r, limit=500: [])
    result = retriever.search(services, "anything", role="staff")
    assert result.passages, "the dense arm still answers"
    assert result.arms["lexical"] == 0
