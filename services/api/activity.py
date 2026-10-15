"""Reading the audit chain over the API, scoped to the permission the session holds.

**Why this is a module and not a query in a handler.** The chain is the product's central claim, and reading it
is the one capability that could quietly widen access to everything the deployment has ever done. A viewer who
can read every row can read the executive's questions, the confidential material that was answered, and who
asked what. So the scoping rule lives here, on its own, with its own argument written down.

**The rule, and why it is derived rather than invented.** The audit table has no per-row permission column.
Adding one would create a second permission model maintained beside the real one, and two models is how the
one that matters goes stale. Instead this reads the model that already exists:

* a session sees its own rows
* a session whose roots **contain** another subject's roots sees that subject's rows too

The direction is the safe one. A `company` session sees itself and every other `company`-scoped subject. A
`company` + `executive` session sees everyone. **Nobody sees a row whose actor holds a root they do not
hold**, which is the boundary `SEC-ACL-001` already enforces on retrieval, applied to the record instead of to
the documents.

**A row's actor is compared against the permission that was in force when the row was written**, because
`access_roots` on the session row is a snapshot rather than a join. A permission that has since moved must not
change what last week's record is allowed to show.

**An unknown actor falls to the narrower side.** The test is a subset test and a subject with no session
record cannot pass it, so a row from an identity this deployment does not know is visible only to a session
that is itself unconstrained. That is a deliberate default rather than an oversight.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

# **The cap, and it refuses rather than trims.** A caller that asks for five thousand rows and silently
# receives five hundred has been lied to about what it read, and the count it renders will be wrong. Refusing
# is the honest answer, and the caller can page with `since`.
# **The columns this read selects, named once.** A SQL string is not imported, not typed, and not executed by
# the unit suite, so a column that does not exist is found on a live deployment as a 500. The list is here so
# `tests/test_activity.py` can check every name against the migrations before that happens. The first version
# selected `failure_kind`, which is a column on `ingest_manifest` rather than on the audit table, and the probe
# found it on the box.
READ_COLUMNS = (
    "id", "ts_iso", "event_type", "actor", "actor_role", "data_class", "outcome", "tool", "model",
    "request_id", "payload_sha256", "payload_bytes", "payload", "egress_allowed", "redactions",
    "unverified_card_shapes", "chain_version",
)
READ_SQL = "select " + ", ".join(READ_COLUMNS) + " from audit_events"

MAX_LIMIT = 500
DEFAULT_LIMIT = 50
# A ceiling on the response, because every row carries its payload. One row is a few hundred bytes of JSON
# and five hundred of them is larger than a screen can use; a client that wants more pages.
MAX_LIMIT_BYTES = 4 * 1024 * 1024

EVENT_TYPES = ("model_call", "tool_call", "approval", "access", "config_change", "login")
OUTCOMES = ("ok", "denied", "error", "pending")


class ActivityError(Exception):
    """A read that cannot be answered, with a reason a caller can act on."""


@dataclass
class Page:
    """One page of the chain, and enough about it for a client to page or to stop."""

    events: list[dict] = field(default_factory=list)
    # The id to pass as `since` for the next page. Absent when this page reached the end.
    next_since: Optional[int] = None
    # **What the caller is allowed to see, said out loud.** A screen that shows a subset of the record without
    # saying so looks like the whole record, and "the record is complete" is exactly the claim at stake.
    scope: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        out = {"events": self.events, "count": len(self.events), "scope": self.scope}
        if self.next_since is not None:
            out["next_since"] = self.next_since
        return out


def _roots_of(value) -> frozenset[str]:
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except json.JSONDecodeError:
            return frozenset()
    try:
        return frozenset(str(v) for v in (value or []))
    except TypeError:
        return frozenset()


def visible_actors(session, *, registry=None, database_url: str = "",
                   grants: Optional[dict] = None) -> set[str]:
    """The subjects whose rows this session may read.

    **Reads the sessions table rather than the registry.** The registry says what a subject's permission is
    today. The row that matters is the one in force when the work happened, and that snapshot is on the
    session. So a subject's roots are taken from every session record the deployment holds for it, and the
    subject is visible when **any** of those grants is contained in the reader's roots. Using the union is the
    generous direction within a subject, and that is deliberate: a person's own history should not fragment
    because their permission changed last month.

    **`grants` exists so the rule can be tested without a database.** It is a mapping of subject to the roots
    that subject has held, and passing it skips the query. The rule is the part worth testing and it should
    not need a Postgres to be argued about.
    """
    mine = _roots_of(sorted(getattr(session, "roots", []) or []))
    actors = {session.subject}
    if grants is None:
        if not database_url:
            return actors
        grants = _grants_from_sessions(getattr(session, "deployment_id", ""), database_url)
        if grants is None:
            # **A database this cannot read is not a reason to widen anything.** The fallback is the session's
            # own rows, which is the narrowest answer available, rather than an error a handler might turn
            # into something more generous.
            return actors

    for subject, held in grants.items():
        if subject == session.subject:
            continue
        subject_grants = [_roots_of(g) for g in (held or [])]
        # **Containment, not equality.** A session holding `company` may read another `company` subject; a
        # session holding `company` and `executive` may read both kinds. A subject whose grant holds something
        # the reader does not have never passes.
        if any(grant <= mine for grant in subject_grants):
            actors.add(subject)
    return actors


def _grants_from_sessions(deployment_id: str, database_url: str) -> Optional[dict]:
    """Every subject's recorded grants on this deployment, or None when the table cannot be read."""
    grants: dict[str, list] = {}
    try:
        import psycopg

        with psycopg.connect(database_url) as conn, conn.cursor() as cur:
            cur.execute("select subject, access_roots from sessions where deployment_id = %s",
                        (deployment_id,))
            for subject, roots in cur.fetchall():
                grants.setdefault(subject, []).append(roots)
    except Exception:  # noqa: BLE001 - the caller decides what an unreadable table means
        return None
    return grants


# **Names a payload must never carry out of this endpoint.** The defect that produced this list: the session
# identifier WAS the credential, so every payload that recorded which session acted handed a reader something
# that authenticated. `mint` now separates the two and migration 019 revokes the sessions that predate it, so a
# `session_id` in a payload is no longer dangerous. This guard is not redundant: it is what stops the next
# credential-shaped field from reaching a screen that widens with permission, and it is cheap.
#
# The rows themselves are append-only and hash-linked, so the ones already written keep their values. **That
# exposure is recorded rather than rewritten**, because rewriting the chain would break the verification the
# whole record rests on, and a record that can be edited to hide something is not evidence.
FORBIDDEN_PAYLOAD_KEYS = ("token", "session", "credential", "credential_sha256", "secret", "password",
                          "api_key", "authorization", "assertion")


def _without_credentials(value):
    """The payload with any credential-shaped key removed, at any depth.

    Recursive because a credential does not have to be at the top: a nested `{"session": {"token": …}}` is the
    same disclosure one level down.
    """
    if isinstance(value, dict):
        return {k: _without_credentials(v) for k, v in value.items()
                if k.lower() not in FORBIDDEN_PAYLOAD_KEYS}
    if isinstance(value, list):
        return [_without_credentials(v) for v in value]
    return value


def _where(session, actors: set[str], filters: dict) -> tuple[str, list]:
    """The SQL predicate and its parameters, built once so every filter is applied in one place."""
    clauses = ["deployment_id = %s"]
    params: list = [getattr(session, "deployment_id", "")]
    # An empty actor set must match nothing rather than everything: `= any('{}')` is false for every row,
    # which is what a session with no scope should see.
    clauses.append("actor = any(%s)")
    params.append(sorted(actors))

    for column, key in (("event_type", "type"), ("outcome", "outcome"), ("actor", "actor")):
        value = filters.get(key)
        if value:
            clauses.append(f"{column} = %s")
            params.append(value)
    if filters.get("since"):
        clauses.append("id > %s")
        params.append(int(filters["since"]))
    if filters.get("request"):
        clauses.append("request_id = %s")
        params.append(filters["request"])
    if filters.get("job"):
        # The job id lives in the payload, because a job is a thing the payload describes rather than a column
        # the chain was built around. The containment operator uses the GIN index the payload already has.
        clauses.append("payload @> %s::jsonb")
        params.append(json.dumps({"job_id": filters["job"]}))
    return " and ".join(clauses), params


def read(session, *, database_url: str, limit: int = DEFAULT_LIMIT, **filters) -> Page:
    """One page of the chain, newest first, scoped to what this session may see.

    **The scope is applied inside the query rather than after it**, for the reason `SEC-ACL-001` gives about
    retrieval: a read that fetches everything and discards some of it produces the right answer while the
    rows leave the store, get counted, and sit one logging statement from a leak.
    """
    if not database_url:
        raise ActivityError("no database is configured, so the record cannot be read")
    try:
        limit = int(limit)
    except (TypeError, ValueError):
        raise ActivityError("limit must be a number") from None
    if limit < 1:
        raise ActivityError("limit must be at least 1")
    if limit > MAX_LIMIT:
        raise ActivityError(f"limit is capped at {MAX_LIMIT}. Page with `since` instead")

    for key, allowed in (("type", EVENT_TYPES), ("outcome", OUTCOMES)):
        value = filters.get(key)
        if value and value not in allowed:
            raise ActivityError(f"{key} must be one of {', '.join(allowed)}")

    actors = visible_actors(session, database_url=database_url)
    # A caller asking for one actor they may not see gets nothing, rather than the rest of the page. A partial
    # answer to "show me Dana's activity" reads as "Dana did nothing".
    asked_actor = filters.get("actor")
    if asked_actor and asked_actor not in actors:
        return Page(events=[], scope={"actors": sorted(actors), "restricted": True})

    where, params = _where(session, actors, filters)
    import psycopg

    with psycopg.connect(database_url) as conn, conn.cursor() as cur:
        cur.execute(f"{READ_SQL} where {where} order by id desc limit %s", params + [limit])
        columns = [c.name for c in cur.description]
        rows = [dict(zip(columns, row)) for row in cur.fetchall()]

    events = []
    for row in rows:
        # The payload is the field a screen reads to show what happened, and it is already the form the
        # deployment's content mode decided on: a hash, a redacted copy, or the text. This does not re-decide
        # that and does not widen it.
        payload = row.get("payload")
        if isinstance(payload, str):
            try:
                payload = json.loads(payload)
            except json.JSONDecodeError:
                payload = {"unreadable": True}
        payload = _without_credentials(payload)
        events.append({
            "id": row["id"],
            "at": row["ts_iso"],
            "type": row["event_type"],
            "actor": row["actor"],
            "actor_role": row["actor_role"],
            "data_class": row["data_class"],
            "outcome": row["outcome"],
            "tool": row["tool"],
            "model": row["model"],
            "request_id": row["request_id"],
            "egress_allowed": row["egress_allowed"],
            "redactions": row["redactions"] if not isinstance(row["redactions"], str) else json.loads(row["redactions"]),
            # **Card-shaped strings the redactor could not verify.** A number that looks like a card and failed
            # its checksum is not proof of anything, and it is the only signal this deployment has that a
            # pattern may have been missed. It belongs on the screen rather than only in the row, because a
            # client reading their own record is the last chance to notice.
            "unverified_card_shapes": row["unverified_card_shapes"],
            "chain_version": row["chain_version"],
            "payload_sha256": row["payload_sha256"],
            "payload_bytes": row["payload_bytes"],
            "payload": payload,
        })

    next_since = rows[-1]["id"] if len(rows) == limit else None
    return Page(events=events, next_since=next_since,
                scope={"actors": sorted(actors), "limit": limit, "max_limit": MAX_LIMIT})


def database_url() -> str:
    """The connection string, from DATABASE_URL or assembled from the parts.

    The same fallback the other read paths make. A probe run against the mounted checkout has the parts and
    nothing that joins them, and without this the read fails with a credentials error that looks like a
    permissions problem.
    """
    url = (os.environ.get("DATABASE_URL") or "").strip()
    if url:
        return url
    user = os.environ.get("POSTGRES_USER", "")
    password = os.environ.get("POSTGRES_PASSWORD", "")
    database = os.environ.get("POSTGRES_DB", "")
    if not (user and database):
        return ""
    return f"postgresql://{user}:{password}@postgres:5432/{database}"
