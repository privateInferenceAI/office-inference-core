"""The auth hook passes what the team row declares.

**Why this test exists.** `tpm_limit` and `rpm_limit` sat in the team table unread: the hook returned a
`UserAPIKeyAuth` without them, so a rate limit declared on a team was silently unenforced. 591 tests passed while
that was true, because nothing exercised the hook with a limit set. Found by trying to provoke a real throttle
and watching the limit do nothing, which is the second time this phase has found a declaration with no enforcer.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
AUTH = ROOT / "services" / "gateway" / "custom_auth.py"


def test_the_hook_reads_the_rate_limits_it_passes():
    """Structural, because the alternative needs a database and a gateway."""
    source = AUTH.read_text()
    assert "tpm_limit" in source, "the hook does not read the tokens-per-minute column"
    assert "rpm_limit" in source, "the hook does not read the requests-per-minute column"
    assert "_team_rate_limits" in source, "and there is no reader for them"


def test_the_hook_returns_the_limits_on_the_identity():
    """A limit that is not passed to the component that enforces it is a comment."""
    source = AUTH.read_text()
    assert "tpm_limit=limits[0]" in source, "the tokens-per-minute limit is read but not returned"
    assert "rpm_limit=limits[1]" in source, "the requests-per-minute limit is read but not returned"


def test_an_absent_limit_stays_absent():
    """A deployment that declares no rate limit must not get one of zero, which would refuse everything."""
    source = AUTH.read_text()
    marker = "return (int(row[0]) if row[0] else None, int(row[1]) if row[1] else None)"
    assert marker in source, "an absent limit is being turned into a number"


def test_the_hook_still_refuses_when_the_ceiling_cannot_be_read():
    """The property the budget reader already had, kept while adding the limits beside it."""
    source = AUTH.read_text()
    assert "no DATABASE_URL, so no ceiling could be read and no call may be allowed" in source



def test_the_database_url_is_stripped_of_prismas_pool_parameters():
    """The hook connects with psycopg, and LiteLLM's process rewrites DATABASE_URL with Prisma's parameters.

    **Why this is a test and not a comment.** On 2026-10-16 every answer through the API came back
    `degraded` with `reason: unreachable` while the master key worked and retrieval returned passages. The
    gateway was logging `401 ... invalid URI query parameter: "connection_limit"` for every virtual key, so
    the failure looked like a bad key and was a malformed DSN: LiteLLM appends Prisma's pool parameters to
    `DATABASE_URL` in-process, psycopg rejects any query parameter it does not know, and the two reads in this
    hook handed it the rewritten string unchanged. A new virtual key could never have fixed it.
    """
    source = AUTH.read_text()
    assert "_connection_url" in source, "there is no reader that strips Prisma's pool parameters"
    for name in ("connection_limit", "pool_timeout", "socket_timeout",
                 "max_idle_connection_lifetime", "pgbouncer"):
        assert name in source, f"{name} is not named among the parameters psycopg rejects"
    assert source.count('os.environ.get("DATABASE_URL")') == 1, (
        "the raw environment value should be read in exactly one place, the stripper")
    assert "psycopg.connect(_connection_url())" in source, (
        "the budget read does not connect with the stripped URL")
    assert "_connection_url(required=False)" in source, (
        "the rate-limit read does not connect with the stripped URL")


def test_every_role_gets_its_own_identity_name():
    """One identity name for the deployment meant four roles shared one spend counter, and the gateway compared
    the sum against a per-role budget. Measured on 2026-10-18: the fake budget refused at half its value."""
    source = AUTH.read_text()
    assert 'api_key=f"oi-assertion-{role}"' in source, "roles still share one identity name"


def test_the_identity_is_given_the_ceiling_not_the_remainder():
    """The gateway subtracts identity spend itself; passing a remainder that already had spend subtracted made
    the budget fire at half its nominal value."""
    source = AUTH.read_text()
    assert "max_budget=budget" in source
    assert "_team_ceiling" in source, "the hook no longer reads the ceiling"
    assert "remaining = float(ceiling) - float(spend" not in source, "the remainder is still being passed"
