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
    assert "restricted" in result.reason.lower() or "not available" in result.reason.lower()


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
    assert "restricted" in result.reason.lower(), "the internal reason stays accurate"


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
