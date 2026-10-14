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

