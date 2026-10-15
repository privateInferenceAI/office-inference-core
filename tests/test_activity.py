"""Reading the record without reading more of it than you may.

**Why this file is mostly about the scope.** The audit chain is the product's central claim, and reading it is
the one capability that could quietly widen access to everything the deployment has ever done. A viewer who
can read every row can read the executive's questions, what was answered, and who asked. So the containment
rule is tested from both directions rather than only for the case that works.

**What is not tested here.** The SQL. It needs a deployment, and the probe in `tests/fixtures/activity_probe.py`
is what runs against one. What is tested here is the decision: given a set of grants, which actors may this
session see.
"""
from __future__ import annotations

import sys
import types
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "services" / "api"))

import activity  # noqa: E402


class FakeSession:
    """A session with just enough on it for the scope rule, which is all the rule reads."""

    def __init__(self, subject="staff@example.com", roots=("company",), deployment_id="oi-test"):
        self.subject = subject
        self.roots = frozenset(roots)
        self.deployment_id = deployment_id


# --------------------------------------------------------------------------- the rule


def test_a_session_always_sees_its_own_rows():
    seen = activity.visible_actors(FakeSession(), grants={})
    assert seen == {"staff@example.com"}


def test_a_narrow_session_sees_another_equally_narrow_subject():
    """Two people with the same grant can see each other's activity. That is the working case."""
    seen = activity.visible_actors(
        FakeSession(subject="a@example.com", roots=("company",)),
        grants={"a@example.com": [["company"]], "b@example.com": [["company"]]})
    assert seen == {"a@example.com", "b@example.com"}


def test_a_narrow_session_does_not_see_a_wider_subject():
    """**The direction that matters.** A `company` session must not see an `executive` subject's rows."""
    seen = activity.visible_actors(
        FakeSession(subject="staff@example.com", roots=("company",)),
        grants={"staff@example.com": [["company"]],
                "exec@example.com": [["company", "executive"]]})
    assert seen == {"staff@example.com"}, "a company-scoped session could read an executive subject"


def test_a_wider_session_sees_both_kinds():
    seen = activity.visible_actors(
        FakeSession(subject="exec@example.com", roots=("company", "executive")),
        grants={"staff@example.com": [["company"]],
                "exec@example.com": [["company", "executive"]]})
    assert seen == {"staff@example.com", "exec@example.com"}


def test_an_unknown_subject_is_visible_to_nobody_narrow():
    """A row whose actor holds a root the reader does not have stays invisible, even if the actor is unknown.

    The test is containment, not recognition, so an identity with no session record cannot pass it. That is
    the safe default: a row this deployment cannot account for is not shown to a restricted reader.
    """
    seen = activity.visible_actors(
        FakeSession(subject="staff@example.com", roots=("company",)),
        grants={"ghost@example.com": [["company", "executive"]]})
    assert "ghost@example.com" not in seen


def test_a_subject_whose_permission_shrank_is_still_visible_at_the_old_grant():
    """A person's own history should not fragment because their permission changed last month.

    The union of a subject's recorded grants is used, so a row they wrote while they held more is still
    visible to a reader who holds that much.
    """
    seen = activity.visible_actors(
        FakeSession(subject="exec@example.com", roots=("company", "executive")),
        grants={"staff@example.com": [["company"], ["company", "executive"]]})
    assert "staff@example.com" in seen


def test_a_session_with_no_roots_sees_only_itself():
    seen = activity.visible_actors(
        FakeSession(subject="none@example.com", roots=()),
        grants={"staff@example.com": [["company"]]})
    assert seen == {"none@example.com"}


def test_an_unreadable_session_table_does_not_widen_the_scope():
    """The narrowest answer rather than the most generous, because the failure is a read, not a write."""
    seen = activity.visible_actors(FakeSession(subject="staff@example.com", roots=("company",)),
                                   database_url="")
    assert seen == {"staff@example.com"}


def test_malformed_roots_do_not_crash_the_rule():
    """A jsonb column read as a string, or a null, must not take the endpoint down."""
    seen = activity.visible_actors(
        FakeSession(subject="a@example.com", roots=("company",)),
        grants={"a@example.com": ['["company"]'], "b@example.com": [None],
                "c@example.com": [["company"]]})
    assert "a@example.com" in seen and "c@example.com" in seen


# --------------------------------------------------------------------------- the limits


def read_with_fake_db(limit=50, **filters):
    """`read` with psycopg replaced, so the argument checks can be exercised without a database."""
    captured = {}

    class Cursor:
        description = []

        def execute(self, statement, params):
            captured["statement"] = statement
            captured["params"] = params

        def fetchall(self):
            return []

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    class Conn:
        def cursor(self):
            return Cursor()

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    fake = types.ModuleType("psycopg")
    fake.connect = lambda *a, **k: Conn()
    sys.modules["psycopg"] = fake
    try:
        page = activity.read(FakeSession(), database_url="postgresql://x", limit=limit, **filters)
    finally:
        sys.modules.pop("psycopg", None)
    return page, captured


def test_a_limit_above_the_cap_is_refused_rather_than_trimmed():
    """A caller that asked for five thousand rows and silently received five hundred has been lied to about
    what it read, and the count it renders will be wrong."""
    with pytest.raises(activity.ActivityError) as caught:
        activity.read(FakeSession(), database_url="postgresql://x", limit=activity.MAX_LIMIT + 1)
    assert str(activity.MAX_LIMIT) in str(caught.value)


@pytest.mark.parametrize("bad", ["0", "-3", "many"])
def test_a_limit_that_is_not_a_usable_number_is_refused(bad):
    with pytest.raises(activity.ActivityError):
        activity.read(FakeSession(), database_url="postgresql://x", limit=bad)


def test_an_unknown_filter_value_is_refused_by_name():
    with pytest.raises(activity.ActivityError) as caught:
        activity.read(FakeSession(), database_url="postgresql://x", type="not_a_type")
    assert "not_a_type" not in str(caught.value) or "must be one of" in str(caught.value)


def test_reading_without_a_database_is_an_error_rather_than_an_empty_page():
    """An empty list would read as "nothing happened", which is a different and false statement."""
    with pytest.raises(activity.ActivityError):
        activity.read(FakeSession(), database_url="")


def test_the_scope_is_applied_inside_the_query():
    """**The property SEC-ACL-001 states about retrieval, applied to the record.**

    A read that fetches everything and discards some of it produces the right answer while the rows leave the
    store, get counted, and sit one logging statement from a leak. So the assertion is that the actor filter
    is in the statement rather than applied to the rows afterwards.
    """
    _, captured = read_with_fake_db(limit=10)
    statement = captured["statement"]
    assert "actor = any(" in statement, "the scope is not in the query"
    assert "deployment_id = %s" in statement, "rows from another deployment could be returned"
    assert "order by id desc" in statement, "the chain is only readable newest first"
    assert captured["params"][1] == ["staff@example.com"]


def test_asking_for_an_actor_you_may_not_see_returns_nothing_rather_than_the_rest():
    """A partial answer to "show me Dana's activity" reads as "Dana did nothing"."""
    page = activity.read(FakeSession(subject="staff@example.com", roots=("company",)),
                         database_url="postgresql://x", actor="exec@example.com")
    assert page.events == []
    assert page.scope.get("restricted") is True


def test_the_filters_that_reach_the_query():
    _, captured = read_with_fake_db(limit=5, type="model_call", outcome="denied", job="abc123",
                                    request="deadbeef", since=42)
    statement = captured["statement"]
    for fragment in ("event_type = %s", "outcome = %s", "payload @> %s::jsonb", "request_id = %s",
                     "id > %s"):
        assert fragment in statement, f"{fragment} is missing from the query"
    assert '{"job_id": "abc123"}' in captured["params"]


def test_paging_is_offered_only_when_the_page_was_full():
    """`next_since` on a short page would invite a caller to fetch nothing for ever."""
    page, _ = read_with_fake_db(limit=50)
    assert page.next_since is None, "an empty page should not offer a next one"
    assert page.to_dict()["count"] == 0
