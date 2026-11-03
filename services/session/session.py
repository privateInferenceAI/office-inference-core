"""The session: who is asking, what they may read, and the refusal when a request claims its own authority.

**One thing is trusted, and it is a row.** The client presents a token that carries a session id and nothing
else. There is no role in it, no subject, no expiry and no signature, so there is nothing in the token for a
caller to edit into a privilege. It resolves server-side to a record, and the record is the authority.

**A request that tries to set or override its own role is refused and recorded.** That sentence is the
phase's acceptance criterion, and it is here rather than in the API because it is a property of the session
boundary: the API is one caller, and a rule enforced by one caller is a convention rather than a control.

**The refusal is written before the client is told no.** Three properties, each of which is a real failure
somewhere:

* **The guard runs before anything touches the database.** A refusal that half-wrote a session or a row
  would be worse than the request it refused. The same order as the audit tool's hand-typed-actor guard,
  which refuses before it opens a connection.
* **The record is committed before the error is raised.** If the response went out first and the write
  second, a crash between them would leave an access decision with no trace.
* **The claimed role is recorded as a role, not as text in a message.** The audit row's `actor_role` holds
  what the request *claimed*, because that is the value the row has to be findable by. The role the session
  actually held goes in the payload beside the field it was supplied in.

**The development mint follows the pattern already in the tree.** `services/audit/audit.py` refuses a
hand-typed actor unless `--dev` is passed and says why. This does the same, and its refusal names what will
replace it, because the person reading that message is the next agent.

**What C7 changes, named here so it is inherited rather than rediscovered.** C7 swaps the verifier, deletes
the mint command, and changes the manifest field naming the verifier. It keeps the record and its schema, the
role and roots resolution, the retriever call, and this refusal path. Production auth does not remove the
refusal path: an identity provider that rejects a bad token never sees the body, and the API is what sees a
valid session asking for something it does not hold.
"""
from __future__ import annotations

import json
import os
import secrets
import subprocess
import sys
import uuid
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta, timezone
from pathlib import Path

# The registry lives in the retrieval service because the retriever and this module have to agree about what
# a root is, and one definition is the only way that stays true. This module owns the session; the access
# model owns the permission.
_HERE = Path(__file__).resolve().parent
if not (_HERE / "access.py").exists():
    sys.path.insert(0, str(_HERE.parent / "retrieval"))

import access  # noqa: E402

# Where the audit tool is, in the image and in the checkout. **Read at call time rather than at import time**, 
# which is a bug this had: the value was captured when the module loaded, so a caller that set the variable 
# afterwards, which is exactly what a probe or a test does, silently recorded through a path that did not 
# exist. It failed loudly in the end, but the loud failure was one layer away from the cause.
DEFAULT_AUDIT_TOOL = "/app/audit.py"


def database_url() -> str:
    """The connection string, from DATABASE_URL or assembled from the parts.

    **A deployed service has DATABASE_URL and a bare container often does not.** Compose builds it for the
    session service from the POSTGRES_* variables; a probe run by hand against the mounted checkout gets the
    parts and nothing that joins them. Without this the audit tool refuses to run and the refusal is never
    written, which is a control silently not working rather than an error anybody sees.
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

def audit_tool() -> str:
    """The audit tool for this call, from the environment if it is set."""
    return os.environ.get("AUDIT_TOOL", DEFAULT_AUDIT_TOOL)

# The longest a session may live, whatever is asked for. A development session that outlives the work it was
# minted for is a credential nobody is watching, and this is the only place that can bound it.
MAX_TTL_HOURS = 24


class SessionError(Exception):
    """A session that cannot be established, or a claim that cannot be accepted.

    Raised rather than returned so a caller cannot continue with a falsy value and treat it as "no
    permission" rather than "refused". The distinction matters: one is a user who sees nothing, the other is
    a request that tried to give itself authority.
    """


@dataclass(frozen=True)
class Session:
    """One established session, as the record holds it."""

    session_id: str
    subject: str
    role: str
    roots: frozenset[str]
    groups: tuple[str, ...]
    deployment_id: str
    expires_at: datetime
    actor_name: str = ""
    source: str = "development"
    # **The SHA-256 of the credential, never the credential.** The caller holds the value; this record holds
    # something that can check it and cannot be used as it. The session id is not a secret and is recorded and
    # printed freely, which is only safe because these are two different things.
    credential_sha256: str = ""
    # **The plain credential, on the object that minted it, and nowhere else.** `compare=False` and
    # `repr=False` are both load-bearing: it must not make two sessions unequal, and it must not appear when
    # something prints a session for a log. A field that shows up in a repr is a field that reaches a log, and
    # reaching a log is exactly how this defect was found.
    credential: str = field(default="", compare=False, repr=False)

    def expired(self, now: datetime | None = None) -> bool:
        return (now or datetime.now(timezone.utc)) >= self.expires_at

    def permit(self) -> access.Permit:
        """The access decision this session holds, for the retriever."""
        return access.Permit(subject=self.subject, role=self.role, roots=self.roots,
                             groups=self.groups, session_id=self.session_id)


# --------------------------------------------------------------------------- stores


class MemoryStore:
    """Sessions in a dictionary, for tests and for a diagnostic run with no database.

    Not a fallback: a deployment whose database is unreachable must refuse rather than fall back to an
    in-memory store, because a session that survives no restart is a session nobody can audit.
    """

    def __init__(self):
        self.rows: dict[str, Session] = {}

    def put(self, session: Session) -> None:
        # Keyed by the session id, because a memory store has no reason to hide anything and a test that wants
        # to look a session up by id should be able to. The credential is not in the key and cannot be.
        self.rows[session.session_id] = session

    def get(self, credential_sha256: str) -> Session | None:
        """The session a credential belongs to, by the hash of the credential.

        **The same contract as the Postgres store, and it has to be.** Two stores that resolve sessions
        differently is how a test passes against one and a deployment fails on the other.
        """
        if not credential_sha256:
            return None
        for session in self.rows.values():
            if session.credential_sha256 == credential_sha256 and not session.expired():
                return session
        return None

    def revoke(self, session_id: str) -> bool:
        return self.rows.pop(session_id, None) is not None

    def close(self) -> None:
        return None


class PostgresStore:
    """Sessions in the deployment's database, which is where a record belongs."""

    def __init__(self, database_url: str):
        import psycopg  # imported here so the tests need no driver to import this module

        self.conn = psycopg.connect(database_url)
        self.conn.autocommit = True

    def migrate(self, migrations_dir: str | Path) -> list[str]:
        """Apply the session migration, reusing the audit tool's bookkeeping.

        The same `schema_migrations` table as every other migration in this repository, so a deployment has
        one record of what was applied rather than one per component.
        """
        import hashlib

        applied: list[str] = []
        with self.conn.cursor() as cur:
            cur.execute("""
                create table if not exists schema_migrations (
                  filename   text primary key,
                  checksum   text not null,
                  applied_at timestamptz not null default now()
                )""")
            cur.execute("select filename, checksum from schema_migrations")
            seen = dict(cur.fetchall())
            for path in sorted(Path(migrations_dir).glob("*.sql")):
                checksum = hashlib.sha256(path.read_text().encode()).hexdigest()
                if path.name in seen:
                    if seen[path.name] != checksum:
                        raise SessionError(
                            f"{path.name} was applied but has changed since. Write a new migration instead "
                            f"of editing an applied one.")
                    continue
                cur.execute(path.read_text())
                cur.execute("insert into schema_migrations (filename, checksum) values (%s, %s)",
                            (path.name, checksum))
                applied.append(path.name)
        return applied

    def put(self, session: Session) -> None:
        with self.conn.cursor() as cur:
            cur.execute(
                """
                insert into sessions (session_id, subject, actor_name, role, access_roots, groups,
                                      deployment_id, expires_at, source, credential_sha256)
                values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (session.session_id, session.subject, session.actor_name or None, session.role,
                 json.dumps(sorted(session.roots)), json.dumps(list(session.groups)),
                 session.deployment_id, session.expires_at, session.source,
                 # **The hash, and there is no parameter here that could hold the value.** A caller cannot
                 # persist a credential through this function even by mistake.
                 session.credential_sha256 or None))

    def get(self, credential_sha256: str) -> Session | None:
        """The session a credential belongs to, by the hash of the credential.

        **The parameter is a hash and the query says so.** There is no lookup by identifier here on purpose: an
        identifier that resolves a session is a credential, and that equivalence is the defect this replaced.
        """
        if not credential_sha256:
            return None
        with self.conn.cursor() as cur:
            cur.execute(
                """
                select session_id, subject, coalesce(actor_name, ''), role, access_roots, groups,
                       deployment_id, expires_at, source, coalesce(credential_sha256, '')
                  from sessions
                 where credential_sha256 = %s and revoked_at is null and expires_at > now()
                """, (credential_sha256,))
            row = cur.fetchone()
        if row is None:
            return None
        return Session(
            session_id=row[0], subject=row[1], actor_name=row[2], role=row[3],
            roots=frozenset(row[4] or []), groups=tuple(row[5] or []),
            deployment_id=row[6], expires_at=row[7], source=row[8], credential_sha256=row[9])

    def revoke(self, session_id: str) -> bool:
        with self.conn.cursor() as cur:
            cur.execute("update sessions set revoked_at = now() where session_id = %s and revoked_at is null",
                        (session_id,))
            return cur.rowcount > 0

    def refusals_for(self, session_id: str, limit: int = 5) -> list[dict]:
        """The refusals recorded against one session, read back out of the chain.

        ``RECORDED`` is the right that says a refusal must be findable afterwards, and it cannot be asserted
        from the refusal's own return value: a message saying "the refusal has been recorded" is exactly what a
        broken writer would print. This reads the audit table, so the assertion is about the record rather
        than about the code that claims to have written it.
        """
        with self.conn.cursor() as cur:
            cur.execute(
                """
                select occurred_at, event_type, actor, actor_role, outcome, payload
                  from audit_events
                 where payload @> %s::jsonb
                 order by occurred_at desc
                 limit %s
                """, (json.dumps({"session_id": session_id}), limit))
            rows = cur.fetchall()
        return [{"occurred_at": str(r[0]), "event_type": r[1], "actor": r[2], "actor_role": r[3],
                 "outcome": r[4], "payload": r[5]} for r in rows]

    def close(self) -> None:
        self.conn.close()


def open_store(database_url: str | None = None) -> MemoryStore | PostgresStore:
    """The store this deployment uses. A missing DATABASE_URL is a memory store, and it says so."""
    url = database_url or os.environ.get("DATABASE_URL", "")
    if not url:
        return MemoryStore()
    return PostgresStore(url)


# --------------------------------------------------------------------------- establishment


def credential_hash(credential: str) -> str:
    """What the record keeps of a credential: a hash, and nothing that can be presented.

    **A plain SHA-256, and its weakness is stated rather than hidden.** A session credential is 256 bits of
    randomness from `secrets.token_urlsafe(32)`, so there is no dictionary to attack and no need for a slow
    password hash: the thing being defended against is a database read, not a guessing attack. If a credential
    ever becomes something a person chooses, this has to become Argon2 or scrypt in the same commit.
    """
    import hashlib

    return hashlib.sha256(credential.encode()).hexdigest()


def mint(registry: access.Registry, store, subject: str, role: str, *, ttl_hours: int = 8,
         deployment_id: str = "", dev: bool = False, actor_name: str = "") -> Session:
    """Establish a session, and refuse one this deployment did not grant.

    **The guard comes first, before the registry is read and before the store is touched.** A refusal must not
    leave a half-written row behind, and the order is the cheap way to be sure of it rather than an intention
    to be careful.

    **The credential is generated here and never stored.** `mint` used to generate one random value and use it
    as both the identifier and the credential, so every place that recorded which session did something
    recorded something that authenticated. The caller reads the plain value from `session.credential` exactly
    once, and the row keeps only its hash.
    """
    if not dev:
        raise SessionError(
            "mint refuses to establish a session outside development mode. An identity comes from an "
            "authenticated session established by the identity provider, which is the assistant API's job at "
            "C7; this command exists so the refusal path can be tested before then. Pass --dev when you are "
            "the developer and the box holds no client data.")

    if ttl_hours < 1:
        raise SessionError(f"a session must live at least an hour, got {ttl_hours}")
    if ttl_hours > MAX_TTL_HOURS:
        raise SessionError(
            f"a development session may live at most {MAX_TTL_HOURS} hours, got {ttl_hours}. A session that "
            f"outlives the work it was minted for is a credential nobody is watching.")

    # The registry refuses an unknown subject once the deployment declares users, and an unknown role always.
    # Both happen here rather than at the first request, because a session that cannot resolve anything is
    # not a session.
    permit = access.resolve(registry, subject, role)
    credential = secrets.token_urlsafe(32)
    session = Session(
        # The identifier, which is not a secret: it is recorded and shown on the Activity screen on purpose.
        session_id=secrets.token_urlsafe(32),
        subject=subject,
        actor_name=actor_name or (registry.users[subject].display() if subject in registry.users else ""),
        role=role,
        roots=permit.roots,
        groups=permit.groups,
        deployment_id=deployment_id or "unknown",
        expires_at=datetime.now(timezone.utc) + timedelta(hours=ttl_hours),
        source="development",
        credential_sha256=credential_hash(credential),
    )
    store.put(session)
    # A fresh object with the credential on it, because the dataclass is frozen: a session that is established
    # is a fact, and mutating one is how a record stops describing what happened.
    return replace(session, credential=credential)


def service_session(*, subject: str, role: str, roots, session_id: str, deployment_id: str = "",
                    actor_name: str = "", source: str = "service") -> Session:
    """A session for work whose identity is the job row, not a stored credential.

    **Why this exists.** A client-owned n8n workflow runs on a schedule, when nobody is logged in, so there
    is no user credential to resolve and a 24-hour development session would break the second day. The job
    carries the permission snapshot it was created with, and this builds the session object the answer
    pipeline needs from that snapshot. It is never stored and never presented; `expires_at` exists because
    the type requires it, and nothing consults it for a service session.
    """
    if not subject or not role:
        raise SessionError("a service session needs a subject and a role")
    if not roots:
        raise SessionError("a service session needs at least one document root")
    return Session(
        session_id=session_id,
        subject=subject,
        actor_name=actor_name or subject,
        role=role,
        roots=frozenset(roots),
        groups=(),
        deployment_id=deployment_id or "unknown",
        expires_at=datetime.now(timezone.utc) + timedelta(days=1),
        source=source,
    )


def establish_provider(registry: access.Registry, store, subject: str, role: str, *,
                       ttl_hours: int = 8, deployment_id: str = "", actor_name: str = "") -> Session:
    """Establish a session for an identity the deployment's edge already authenticated.

    **The seam between the proxy and the application.** The edge (Caddy and the OIDC proxy) verifies the
    provider login and passes an authenticated subject to the API over an internal connection. This function
    turns that subject into a session with `source="provider"`, so every row written under it says how the
    identity was established rather than pretending a development token was involved.

    The permission is resolved from the rendered access registry, not from anything the edge sent. The edge
    proves who the person is; the registry decides what they may read. A subject the registry does not declare
    is refused here, before a row is written, exactly as the development path refuses.
    """
    if ttl_hours < 1:
        raise SessionError(f"a session must live at least an hour, got {ttl_hours}")
    if ttl_hours > MAX_TTL_HOURS:
        raise SessionError(f"a session may live at most {MAX_TTL_HOURS} hours, got {ttl_hours}")
    permit = access.resolve(registry, subject, role)
    credential = secrets.token_urlsafe(32)
    session = Session(
        session_id=secrets.token_urlsafe(32),
        subject=subject,
        actor_name=actor_name or (registry.users[subject].display() if subject in registry.users else ""),
        role=role,
        roots=permit.roots,
        groups=permit.groups,
        deployment_id=deployment_id or "unknown",
        expires_at=datetime.now(timezone.utc) + timedelta(hours=ttl_hours),
        source="provider",
        credential_sha256=credential_hash(credential),
    )
    store.put(session)
    return replace(session, credential=credential)


def resolve_credential_hash(store, credential_sha256: str) -> Session:
    """The session a stored hash belongs to, or a refusal.

    **For a job, which holds a hash rather than a credential.** A job has to run after the request that created
    it has ended, and the first version solved that by keeping the credential in the row. This resolves the same
    way `resolve_token` does, from a value that cannot be presented anywhere, and a job whose session has
    expired or been revoked is refused with the one answer the refusal path already gives.
    """
    if not credential_sha256:
        raise SessionError(
            "this job does not name a session it may run under. It was created before the credential stopped "
            "being stored, so there is nothing to resolve.")
    session = store.get(credential_sha256)
    if session is None:
        raise SessionError(
            "the session this job was created under is not live. It expired, or it was revoked, or it was "
            "minted before the credential stopped being stored; the three are one answer on purpose.")
    return session


def resolve_token(store, token: str) -> Session:
    """A token becomes a session, or a refusal that says which of the reasons it was."""
    if not token or not token.strip():
        raise SessionError("no session token was presented, so there is no identity to act as")
    # **The presented value is hashed, and the hash is what is looked up.** The alternative — treating the
    # identifier as the credential — is what put a live session token into every audit row that named its
    # session, which the C4 panel's cold reader found by opening one. This line is the whole fix on this path,
    # and the first attempt to make it did not match the text it was replacing and said nothing.
    session = store.get(credential_hash(token.strip()))
    if session is None:
        raise SessionError(
            "the session token resolves to no live session. It was never issued, it expired, or it was "
            "revoked; the three are one answer on purpose, because a refusal that says which is a refusal "
            "that confirms a token once existed.")
    return session


# --------------------------------------------------------------------------- the refusal


def check_role_claim(session: Session, supplied_field: str | None, claimed_role: str | None, *,
                     record=None, dev: bool = False) -> None:
    """Refuse a request that supplies its own authority, and record the refusal before raising.

    **Every supplied field is refused, including one that agrees with the session.** A request that names a
    role is a request that believes authority is a parameter, and accepting the ones that happen to match
    teaches a caller that the field is honoured. The refusal is about the attempt.

    `record` is the audit writer, injected so a test can watch the order of operations and so the API can
    pass its own. It defaults to the audit tool's CLI, which is what makes this the same writer as every other
    event rather than a second one.
    """
    if not supplied_field:
        return

    # Nothing above this line touches a store or a registry. A refusal cannot corrupt state it never read.
    payload = {
        "supplied_field": supplied_field,
        "attempted_role": claimed_role or "",
        "session_role": session.role,
        "session_subject": session.subject,
        "session_id": session.session_id,
        "roots_held": sorted(session.roots),
        "refusal": "a request may not supply or override its own role",
    }
    writer = record or record_refusal
    writer(session, payload, dev=dev)
    raise SessionError(
        f"refused: the request supplied {supplied_field!r}. A role comes from the session and never from a "
        f"request field. The session holds {session.role!r} and the refusal has been recorded.")


def record_refusal(session: Session, payload: dict, *, dev: bool = False) -> None:
    """Write one refused authority claim to the audit chain, and fail loudly if it cannot.

    **The same writer as every other event.** The audit tool owns the chain and the canonical form, so this
    shells out to it rather than inserting a row: a second writer is a second definition of the hash, and the
    chain is the one thing in the system that has to have exactly one.
    """
    command = [
        sys.executable, audit_tool(), "record",
        "--type", "access",
        "--actor", session.subject,
        # The role the request CLAIMED, because that is what a reviewer searches by. The role the session
        # actually held is in the payload below.
        "--actor-role", payload.get("attempted_role") or session.role,
        "--outcome", "denied",
        "--data-class", "internal",
        "--request-id", f"refusal-{uuid.uuid4()}",
        "--payload", json.dumps(payload, sort_keys=True),
    ]
    if dev:
        command.append("--dev")
    # Assigned into a copy rather than defaulted, and passed explicitly. The audit tool reads DATABASE_URL from
    # its environment; inheriting this process's environment gave it a variable that was absent, so it exited
    # non-zero and the refusal went unrecorded while everything else looked healthy.
    env = dict(os.environ)
    env["DATABASE_URL"] = database_url()
    result = subprocess.run(command, capture_output=True, text=True, env=env)
    if result.returncode != 0:
        raise SessionError(
            f"the refusal could not be recorded, so it is not a control: "
            f"{result.stderr.strip() or result.stdout.strip() or 'the audit tool exited non-zero'}. The "
            f"request was refused and the deployment should be treated as having no working record.")
