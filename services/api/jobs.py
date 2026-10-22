"""A job: work with an identity, a state, and a history that outlives the connection that started it.

**Why a job is not a longer answer.** `POST /ask` is one round trip. It ends when the answer does, and
nothing about it can be watched, stopped, or found afterwards. The requirement behind this module is a
sentence with several parts: *work on this for thirty minutes, let it fail four times, and on the fifth
failure change the approach*. That needs a durable record, one row per attempt with its typed failure, a
rule about what happens when attempts run out, progress that can be watched while it runs, and somewhere
to stop it.

**Three decisions are built into this file, and each one is a thing that goes wrong if it is made the
other way.**

* **Progress is a log, not a socket.** Every event is a numbered row in a table, written in the same
  transaction that assigns its number. A client that closes the tab and comes back reads from `?since=`
  and catches up. A design where the only copy of "what happened" was pushed down a connection loses it
  the moment the connection does, which is the moment it matters.
* **The job carries a snapshot of the permission it ran under**, not a reference to the session. A
  permission read live at display time is a permission that has since moved, and the record would then
  describe a decision nobody made.
* **Every failure is typed, and the retry decision is recorded beside it.** "It failed" is not something
  an operator can count or a client can act on. `failure_kind` plus `retryable` is, and the second one
  has to be written at the moment the decision was made rather than reconstructed later.

**What this module does not do.** It does not decide what a step does, and it does not talk to a model.
The work of one attempt is a callable passed in, which is what lets the engine be tested without a
deployment and lets C6 replace a one-step executor with a real loop without touching the state machine.
"""
from __future__ import annotations

import json
import os
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional

# The decision vocabulary lives with the record rather than here, because the record is what has to be read
# back years later and the state machine is one of several things that will act on it. Importing rather than
# redeclaring keeps a decision name from meaning two things.
from approvals import DECISION_APPROVE, DECISION_DENY

# --------------------------------------------------------------------------- the machine
#
# Small on purpose. A client has to be able to draw every state and know what may follow, and a state
# nobody can reach is a state that will be drawn wrongly.
#
# `awaiting_approval` is defined here and never reached in C4. It is in the machine because the interface
# has to render it, and a state added later would change the shape of the screen. Defining it now is
# cheaper than discovering it in C5.
QUEUED = "queued"
RUNNING = "running"
PAUSED = "paused"
AWAITING_APPROVAL = "awaiting_approval"
SUCCEEDED = "succeeded"
FAILED = "failed"
CANCELLED = "cancelled"

# What may follow what. **The refusal of an unlisted transition is the point**: resume-after-cancel and
# succeed-after-fail both look harmless in a handler and both produce a record that describes a job which
# could not have happened.
ALLOWED: dict[str, frozenset[str]] = {
    QUEUED: frozenset({RUNNING, CANCELLED}),
    RUNNING: frozenset({PAUSED, AWAITING_APPROVAL, SUCCEEDED, FAILED, CANCELLED}),
    PAUSED: frozenset({RUNNING, CANCELLED}),
    AWAITING_APPROVAL: frozenset({RUNNING, CANCELLED, FAILED}),
    SUCCEEDED: frozenset(),
    FAILED: frozenset(),
    CANCELLED: frozenset(),
}

# The states a client should stop watching in. A screen that keeps a spinner on a finished job is lying
# about the one thing it exists to report.
TERMINAL = frozenset({SUCCEEDED, FAILED, CANCELLED})

# The frame names, matching the stream a client reads. Kept as constants because a typo in one of these
# is a frame a client silently never sees.
EV_PROGRESS = "progress"
EV_STEP = "step"
EV_NOTICE = "notice"
EV_DEGRADED = "degraded"
EV_DONE = "done"
EV_ERROR = "error"

DEFAULT_TIME_BUDGET_S = 1800
DEFAULT_ATTEMPT_BUDGET = 5

# **The ceiling on a job nobody is watching.** A durable job that runs for ever is a bill nobody agreed to,
# and a cancelled one that keeps working is worse. This is enforced by the executor, not by hope.
MAX_TIME_BUDGET_S = 24 * 3600


class JobError(Exception):
    """A job that cannot be created, or a transition that may not happen."""


class TransitionRefused(JobError):
    """A state change the machine does not allow, named so a caller can report which one."""

    def __init__(self, job_id: str, was: str, wanted: str):
        self.job_id, self.was, self.wanted = job_id, was, wanted
        super().__init__(f"job {job_id} is {was} and may not become {wanted}")


class ApprovalDrift(JobError):
    """An approval quoted a digest that is not what the job is waiting about.

    **Its own exception rather than a `TransitionRefused`, because the two are different faults with different
    fixes.** A refused transition means the caller asked at the wrong moment; drift means the thing they
    decided about is no longer the thing in front of them. The second is the one that would otherwise produce
    a record saying somebody approved something they never saw.
    """

    def __init__(self, job_id: str, waiting: str, quoted: str):
        self.job_id, self.waiting, self.quoted = job_id, waiting, quoted
        super().__init__(
            f"job {job_id} is waiting for a decision on {waiting or 'nothing'}, and this decision names "
            f"{quoted or 'nothing'}. The content changed while it was being read, so the decision has to be "
            f"made again against what is there now."
        )


# --------------------------------------------------------------------------- the records


@dataclass
class Step:
    """One attempt. `failure_kind` is typed rather than prose because it is counted, not read.

    **Mutable, unlike `Session` and for a reason worth stating.** A session is a fact that was established
    and is never edited. A step is a record that fills in as the attempt proceeds: it exists the moment
    work starts, so a client watching has something to render, and its outcome is written when the
    attempt ends. Freezing it meant the executor could not record what happened, which the first version
    of this file got wrong and the tests caught.
    """

    step_id: str
    job_id: str
    attempt: int
    name: str
    state: str = RUNNING
    failure_kind: str = ""
    failure_detail: str = ""
    retryable: Optional[bool] = None
    lane: str = ""
    model: str = ""
    duration_ms: Optional[int] = None

    def to_dict(self) -> dict:
        out = {"step_id": self.step_id, "attempt": self.attempt, "name": self.name, "state": self.state}
        if self.failure_kind:
            out["failure"] = {"kind": self.failure_kind, "detail": self.failure_detail,
                              "retryable": self.retryable}
        for key in ("lane", "model"):
            value = getattr(self, key)
            if value:
                out[key] = value
        if self.duration_ms is not None:
            out["duration_ms"] = self.duration_ms
        return out


@dataclass
class Job:
    """The work itself. Everything a client needs to draw the screen is here rather than in a join."""

    job_id: str
    deployment_id: str
    subject: str
    actor_role: str
    roots: frozenset[str]
    # **A hash, not a credential.** The job resolves the session by this when it runs. Holding the credential
    # here is what made every job row a working identity, which migration 020 records.
    credential_sha256: str
    task: str
    input: str
    data_class: str = "internal"
    state: str = QUEUED
    outcome_reason: str = ""
    # **What this job is waiting to have approved, as a hash rather than a pointer.** When a job stops at the
    # gate it has something specific in mind: an email it has drafted, an entry it wants to post. The digest
    # of that thing is held here and an approval has to quote it. Without it the record says somebody approved
    # something, and the link between the approval and the act can be re-pointed afterwards by anything that
    # can edit a job row.
    pending_action_digest: str = ""
    # **What a person reads, where the digest is what the route checks.** The digest is a hash and a screen
    # cannot render it; this is the drafted email, the entry to post, the reason it was drafted and what
    # approving it will do. Only the engine fills it, and only while the job is at the gate.
    pending_action: dict = field(default_factory=dict)
    # **How far the event log has got, so a client can attach the stream where it stopped.** Written by the
    # store when it reads a job, because the events are rows and the job row does not carry them. A screen
    # renders from `GET /jobs/{id}` and then asks for `?since=<events_through>`.
    events_through: int = 0
    time_budget_s: int = DEFAULT_TIME_BUDGET_S
    attempt_budget: int = DEFAULT_ATTEMPT_BUDGET
    created_at: float = field(default_factory=time.time)
    started_at: Optional[float] = None
    finished_at: Optional[float] = None
    steps: list[Step] = field(default_factory=list)
    # **The answer, when the last step produced one.** Held on the job so a client that comes back gets the
    # result rather than only the history of producing it.
    result: dict = field(default_factory=dict)

    @property
    def terminal(self) -> bool:
        return self.state in TERMINAL

    @property
    def attempts(self) -> int:
        return len(self.steps)

    def may(self, wanted: str) -> bool:
        return wanted in ALLOWED.get(self.state, frozenset())

    def to_dict(self, *, include_result: bool = True) -> dict:
        out = {
            "job_id": self.job_id,
            "state": self.state,
            "task": self.task,
            "input": self.input,
            "attempts": self.attempts,
            "steps": [s.to_dict() for s in self.steps],
            # Told to the client rather than left to be inferred, because a screen that guesses whether a
            # job is finished gets it wrong exactly once and then nobody trusts it.
            "terminal": self.terminal,
            "time_budget_s": self.time_budget_s,
            "attempt_budget": self.attempt_budget,
            "submitted_by": self.subject,
            "role": self.actor_role,
            "data_class": self.data_class,
        }
        if self.started_at:
            out["started_at"] = _iso(self.started_at)
        if self.finished_at:
            out["finished_at"] = _iso(self.finished_at)
            out["duration_ms"] = int((self.finished_at - self.started_at) * 1000) if self.started_at else None
        if self.outcome_reason:
            out["outcome_reason"] = self.outcome_reason
        # **Published only while the job is waiting.** A client deciding needs to know what it is deciding
        # about, and the Review screen quotes this back so the route can refuse a decision that has drifted
        # from the thing it was made about. Once the gate is passed the digest is history and lives in the
        # audit row instead.
        if self.state == AWAITING_APPROVAL and self.pending_action_digest:
            out["pending_action_digest"] = self.pending_action_digest
        if self.state == AWAITING_APPROVAL and self.pending_action:
            out["pending_action"] = self.pending_action
        if include_result and self.result:
            out["result"] = self.result
        # **The cursor, so a client never starts from zero.** A screen renders the job from this response and
        # then attaches the stream with `?since=<events_through>`. Without it the live view had to use
        # `?since=0` and rely on replay being idempotent, which works and makes the client re-read everything
        # it already has every time it connects.
        if self.events_through:
            out["events_through"] = self.events_through
        return out


def _iso(ts: float) -> str:
    from datetime import datetime, timezone
    return datetime.fromtimestamp(ts, timezone.utc).isoformat()


# --------------------------------------------------------------------------- the stores
#
# Two implementations, and the memory one is not a test double. A deployment with no DATABASE_URL gets it,
# and it says so out loud rather than pretending to be durable. `durable` is on the store so a caller can
# tell the difference instead of assuming.


class MemoryStore:
    """Jobs in this process. Not durable, and it says so."""

    durable = False

    def __init__(self):
        self.jobs: dict[str, Job] = {}
        self.events: dict[str, list[dict]] = {}

    def migrate(self, migrations_dir: str | Path) -> list[str]:
        return []

    def put(self, job: Job) -> None:
        self.jobs[job.job_id] = job

    def get(self, job_id: str) -> Optional[Job]:
        return self.jobs.get(job_id)

    def pending_approval(self) -> list[Job]:
        """Every job waiting at a gate, oldest first.

        **A method rather than a filter over a list-everything, and `list-everything` should not exist.** A
        deployment accumulates jobs forever, and a caller wanting the handful at a gate must not be handed
        the whole history to sift. Both implementations answer the same question, which is what stops the
        memory path and the durable path disagreeing about what is waiting — the shape that has now bitten
        four times in one phase.
        """
        return sorted((j for j in self.jobs.values() if j.state == AWAITING_APPROVAL),
                      key=lambda j: j.created_at)

    def claim_queued(self, limit: int, task_ids: set[str] | None = None) -> list[Job]:
        """Move up to `limit` queued jobs to running, oldest first, and return them.

        **The claim is the queue.** A worker that read a queued row and then wrote it could claim the same job
        twice; the durable store does this in one `for update skip locked` statement. The memory path does the
        same transition under the same state machine, so the dispatcher is testable without a database.
        """
        if limit < 1:
            return []
        wanted = sorted(
            (j for j in self.jobs.values()
             if j.state == QUEUED and (task_ids is None or j.task in task_ids)),
            key=lambda j: j.created_at,
        )[:limit]
        for job in wanted:
            transition(job, RUNNING)
        return wanted

    def queue_position(self, job_id: str) -> Optional[int]:
        """This queued job's place in line, 1-based, or None when it is not queued."""
        job = self.get(job_id)
        if job is None or job.state != QUEUED:
            return None
        ahead = sum(1 for other in self.jobs.values()
                    if other.state == QUEUED and other.created_at < job.created_at)
        return ahead + 1

    def append(self, job_id: str, event: str, data: dict) -> int:
        seq = len(self.events.get(job_id, [])) + 1
        self.events.setdefault(job_id, []).append(
            {"seq": seq, "event": event, "data": data, "created_at": _iso(time.time())})
        return seq

    def events_since(self, job_id: str, since: int = 0) -> list[dict]:
        return [e for e in self.events.get(job_id, []) if e["seq"] > since]

    def close(self) -> None:
        pass


class PostgresStore:
    """Jobs in the deployment's database, which is where a record belongs."""

    durable = True

    def __init__(self, database_url: str):
        import psycopg  # here so the tests need no driver to import this module

        self.conn = psycopg.connect(database_url)
        self.conn.autocommit = True
        # Held for the dispatcher's advisory lock. The store's own connection is shared with request paths,
        # and a session-level lock on it would be released by a reconnect for reasons unrelated to the worker.
        self.database_url = database_url

    def migrate(self, migrations_dir: str | Path) -> list[str]:
        """Apply migrations, reusing the same bookkeeping table as every other component."""
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
                        raise JobError(
                            f"{path.name} was applied but has changed since. Write a new migration "
                            "instead of editing an applied one.")
                    continue
                cur.execute(path.read_text())
                cur.execute("insert into schema_migrations (filename, checksum) values (%s, %s)",
                            (path.name, checksum))
                applied.append(path.name)
        return applied

    def put(self, job: Job) -> None:
        # **Imported here for the same reason `psycopg` is imported inside `__init__`**: this module must be
        # importable with no driver present, so the tests need no database. The first version of
        # `pending_action` used `Jsonb` without this line, which is a `NameError` on the one path no test in
        # this repository reaches, because every test uses `MemoryStore`.
        from psycopg.types.json import Jsonb

        with self.conn.cursor() as cur:
            cur.execute(
                """
                insert into jobs (job_id, deployment_id, subject, actor_role, access_roots,
                                  credential_sha256, task, input, state, outcome_reason, time_budget_s,
                                  attempt_budget, created_at, started_at, finished_at, data_class,
                                  pending_action_digest, pending_action)
                values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, to_timestamp(%s), %s, %s, %s,
                        coalesce(%s, ''), %s)
                on conflict (job_id) do update set
                  state = excluded.state,
                  outcome_reason = excluded.outcome_reason,
                  started_at = excluded.started_at,
                  finished_at = excluded.finished_at,
                  -- **In the update as well as the insert, and that half is load-bearing.** An approval
                  -- clears the digest, and a job that carried the old value forward would be one whose
                  -- next gate could be satisfied by a decision made about the previous one. A column
                  -- written only on insert is a column that never changes, which is the opposite of what
                  -- this one is for.
                  pending_action_digest = excluded.pending_action_digest,
                  pending_action = excluded.pending_action
                """,
                (job.job_id, job.deployment_id, job.subject, job.actor_role,
                 json.dumps(sorted(job.roots)), job.credential_sha256 or None, job.task, job.input, job.state,
                 job.outcome_reason or None, job.time_budget_s, job.attempt_budget, job.created_at,
                 _ts(job.started_at), _ts(job.finished_at), job.data_class,
                 job.pending_action_digest or None,
                 Jsonb(job.pending_action) if job.pending_action else None))
            for step in job.steps:
                cur.execute(
                    """
                    insert into job_steps (step_id, job_id, attempt, name, state, failure_kind,
                                           failure_detail, retryable, lane, model, duration_ms, finished_at)
                    values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, now())
                    on conflict (step_id) do update set
                      state = excluded.state, failure_kind = excluded.failure_kind,
                      failure_detail = excluded.failure_detail, retryable = excluded.retryable,
                      lane = excluded.lane, model = excluded.model, duration_ms = excluded.duration_ms,
                      finished_at = now()
                    """,
                    (step.step_id, job.job_id, step.attempt, step.name, step.state,
                     step.failure_kind or None, step.failure_detail or None, step.retryable,
                     step.lane or None, step.model or None, step.duration_ms))

    def get(self, job_id: str) -> Optional[Job]:
        with self.conn.cursor() as cur:
            cur.execute(
                """
                select job_id, deployment_id, subject, actor_role, access_roots, coalesce(credential_sha256, ''),
                       task, input, state, coalesce(outcome_reason, ''), time_budget_s, attempt_budget,
                       extract(epoch from created_at), extract(epoch from started_at),
                       extract(epoch from finished_at), data_class, coalesce(pending_action_digest, ''),
                       coalesce(pending_action, '{}'::jsonb)
                  from jobs where job_id = %s
                """, (job_id,))
            row = cur.fetchone()
            if row is None:
                return None
            cur.execute(
                """
                select step_id, attempt, name, state, coalesce(failure_kind, ''),
                       coalesce(failure_detail, ''), retryable, coalesce(lane, ''), coalesce(model, ''),
                       duration_ms
                  from job_steps where job_id = %s order by attempt
                """, (job_id,))
            # **The rows are read before the next execute, and that ordering is the whole fix.**
            # `cur.fetchall()` reads the result set of the MOST RECENT execute. Putting the `max(seq)` query
            # between the steps query and this list left it reading the cursor's one-row result set with that
            # row already taken by `fetchone()`, so `steps` was always empty on the durable path. A job read
            # back from the store had no steps, the engine could not find the row it had just written, and
            # `POST /jobs` answered 500 on the first workflow ever started over HTTP.
            #
            # The intent in the comment below was right — one round trip rather than two queries a caller has
            # to remember — and the second query is what ate the first query's rows.
            step_rows = cur.fetchall()
            cur.execute("select coalesce(max(seq), 0) from job_events where job_id = %s", (job_id,))
            through = int((cur.fetchone() or [0])[0])

            steps = [Step(step_id=r[0], job_id=job_id, attempt=r[1], name=r[2], state=r[3],
                          failure_kind=r[4], failure_detail=r[5], retryable=r[6], lane=r[7],
                          model=r[8], duration_ms=r[9]) for r in step_rows]
        return Job(job_id=row[0], deployment_id=row[1], subject=row[2], actor_role=row[3],
                   roots=frozenset(row[4] or []), credential_sha256=row[5], task=row[6], input=row[7],
                   state=row[8], outcome_reason=row[9], time_budget_s=row[10],
                   attempt_budget=row[11], created_at=float(row[12]),
                   started_at=float(row[13]) if row[13] is not None else None,
                   finished_at=float(row[14]) if row[14] is not None else None,
                   data_class=row[15], pending_action_digest=row[16] or "",
                   pending_action=dict(row[17] or {}), events_through=through, steps=steps)

    def pending_approval(self) -> list[Job]:
        """Every job waiting at a gate, oldest first.

        **A method rather than a filter over a list-everything, and `list-everything` should not exist.** A
        deployment accumulates jobs forever, and a caller wanting the handful at a gate must not be handed
        the whole history to sift. Both implementations answer the same question, which is what stops the
        memory path and the durable path disagreeing about what is waiting — the shape that has now bitten
        four times in one phase.
        """
        with self.conn.cursor() as cur:
            cur.execute(
                "select job_id from jobs where state = %s order by created_at", (AWAITING_APPROVAL,))
            ids = [r[0] for r in cur.fetchall()]
        return [j for j in (self.get(i) for i in ids) if j is not None]

    def claim_queued(self, limit: int, task_ids: set[str] | None = None) -> list[Job]:
        """Move up to `limit` queued jobs to running in one statement, oldest first.

        **`for update skip locked` is the whole concurrency story.** Two workers, or two processes, ask for
        work at the same time; the first locks the rows it takes, the second skips them and gets different
        rows. A read followed by an update would let both claim the same job, which is the duplicate this
        product's record exists to prevent. The claim is also the transition, so a claimed job cannot be
        claimed again.
        """
        if limit < 1:
            return []
        wanted = sorted(task_ids) if task_ids is not None else None
        with self.conn.cursor() as cur:
            cur.execute(
                """
                with picked as (
                    select job_id from jobs
                     where state = %s
                       and (%s::text[] is null or task = any(%s::text[]))
                     order by created_at
                     for update skip locked
                     limit %s
                )
                update jobs set state = %s, started_at = now()
                  from picked
                 where jobs.job_id = picked.job_id
                 returning jobs.job_id
                """, (QUEUED, wanted, wanted, limit, RUNNING))
            ids = [row[0] for row in cur.fetchall()]
        return [j for j in (self.get(i) for i in ids) if j is not None]

    def queue_position(self, job_id: str) -> Optional[int]:
        """This queued job's place in line, 1-based, or None when it is not queued."""
        with self.conn.cursor() as cur:
            cur.execute("select state, created_at from jobs where job_id = %s", (job_id,))
            row = cur.fetchone()
            if row is None or row[0] != QUEUED:
                return None
            cur.execute("select count(*) from jobs where state = %s and created_at < %s",
                        (QUEUED, row[1]))
            return int(cur.fetchone()[0]) + 1

    def append(self, job_id: str, event: str, data: dict) -> int:
        """Write one event and assign its number in the same statement that inserts it.

        **`max(seq) + 1` inside the insert, rather than a read followed by a write.** Two writers that read
        the same maximum both write the same number, the second insert violates the primary key, and the
        client sees a gap or a failure. Taking the number in the insert makes the database do the
        sequencing, and the primary key on `(job_id, seq)` is what turns a race into an error rather than
        into a duplicate.
        """
        with self.conn.cursor() as cur:
            cur.execute(
                """
                insert into job_events (job_id, seq, event, data)
                values (%s, (select coalesce(max(seq), 0) + 1 from job_events where job_id = %s), %s, %s)
                returning seq
                """, (job_id, job_id, event, json.dumps(data)))
            return int(cur.fetchone()[0])

    def events_since(self, job_id: str, since: int = 0) -> list[dict]:
        with self.conn.cursor() as cur:
            cur.execute(
                """
                select seq, event, data, created_at from job_events
                 where job_id = %s and seq > %s order by seq
                """, (job_id, since))
            return [{"seq": r[0], "event": r[1], "data": r[2], "created_at": str(r[3])}
                    for r in cur.fetchall()]

    def close(self) -> None:
        self.conn.close()


def _ts(value: Optional[float]):
    if value is None:
        return None
    from datetime import datetime, timezone
    return datetime.fromtimestamp(value, timezone.utc)


def database_url() -> str:
    """The connection string, from DATABASE_URL or assembled from the parts.

    The same fallback `session.py` makes, and for the same reason: a deployed service has the joined URL
    and a probe run by hand against the mounted checkout has only the parts. Without this the store
    silently becomes the memory one and every job vanishes with the process.
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


def open_store(url: str | None = None, migrations_dir: str | Path | None = None):
    """The store this deployment uses. A missing URL is a memory store, and `durable` says so."""
    resolved = url if url is not None else database_url()
    if not resolved:
        return MemoryStore()
    store = PostgresStore(resolved)
    if migrations_dir:
        store.migrate(migrations_dir)
    return store


# --------------------------------------------------------------------------- transitions


def new_job(*, deployment_id: str, subject: str, actor_role: str, roots, credential_sha256: str, task: str,
            input: str, data_class: str = "internal", time_budget_s: int = DEFAULT_TIME_BUDGET_S,
            attempt_budget: int = DEFAULT_ATTEMPT_BUDGET) -> Job:
    """A job in `queued`, with its permission snapshotted rather than referenced."""
    if time_budget_s < 1 or time_budget_s > MAX_TIME_BUDGET_S:
        raise JobError(f"a time budget must be between 1 and {MAX_TIME_BUDGET_S} seconds")
    if attempt_budget < 1 or attempt_budget > 50:
        raise JobError("an attempt budget must be between 1 and 50")
    return Job(
        job_id=uuid.uuid4().hex, deployment_id=deployment_id, subject=subject, actor_role=actor_role,
        roots=frozenset(roots), credential_sha256=credential_sha256, task=task, input=input,
        data_class=data_class,
        time_budget_s=time_budget_s, attempt_budget=attempt_budget)


def transition(job: Job, wanted: str, *, reason: str = "") -> Job:
    """Move a job, or refuse and say which two states were involved.

    **The refusal is the feature.** A handler that sets `job.state = 'running'` directly will happily
    resume a cancelled job, and the record then describes work that happened after somebody stopped it.
    """
    if not job.may(wanted):
        raise TransitionRefused(job.job_id, job.state, wanted)
    job.state = wanted
    if reason:
        job.outcome_reason = reason
    if wanted == RUNNING and job.started_at is None:
        job.started_at = time.time()
    if wanted in TERMINAL:
        job.finished_at = time.time()
    return job


# --------------------------------------------------------------------------- the executor
#
# **One attempt is a callable, and that is the seam.** C4 passes one that routes a question through the
# same retrieval, gate, model and redaction path `/ask` uses. C6 passes one that runs a tool loop. The
# state machine, the event log, the budgets and the audit rows are the same code either way, which is what
# makes the interface not need rewriting when the engine arrives.


@dataclass
class StepOutcome:
    """What one attempt produced. `ok=False` carries a typed failure and whether retrying could help."""

    ok: bool
    name: str = "answer"
    lane: str = ""
    model: str = ""
    result: dict = field(default_factory=dict)
    failure_kind: str = ""
    failure_detail: str = ""
    retryable: bool = False
    notice: str = ""
    degraded: dict = field(default_factory=dict)


def run(job: Job, store, attempt_once: Callable[[Job, int], StepOutcome], *,
        audit: Optional[Callable[..., None]] = None, max_attempts: int | None = None) -> Job:
    """Run a job to a terminal state, writing a row for every attempt and an event for every change.

    **The loop is here and not in the step**, because "let it fail four times" is a property of the job
    rather than of the work. A step says whether its failure is retryable; the job decides whether it has
    attempts left and whether there is time.

    **A cancelled job stops.** The check happens before each attempt rather than only at the end, because
    the alternative is a job that keeps working after somebody stopped it, which is the one thing a stop
    button must not do.
    """
    started = time.monotonic()
    limit = max_attempts or job.attempt_budget

    if job.state == QUEUED:
        transition(job, RUNNING)
        store.put(job)
        store.append(job.job_id, EV_PROGRESS, {"state": RUNNING, "attempt": 0,
                                               "of": limit, "note": "started"})
        if audit:
            audit("tool_call", job.subject, job.actor_role, "ok",
                  {"stage": "job", "job_id": job.job_id, "task": job.task, "state": RUNNING,
                   "job_id": job.job_id, "attempts": 0}, tool=job.task)

    while True:
        # Re-read before every attempt. A cancel arrives through a different connection, so the copy in
        # memory is not the authority on whether this job is still wanted.
        current = store.get(job.job_id)
        if current is not None and current.state in TERMINAL:
            job.state, job.outcome_reason = current.state, current.outcome_reason
            job.finished_at = current.finished_at
            return job

        if len(job.steps) >= limit:
            transition(job, FAILED, reason="attempts_exhausted")
            store.put(job)
            store.append(job.job_id, EV_DONE,
                         {"state": FAILED, "reason": "attempts_exhausted", "attempts": job.attempts})
            if audit:
                audit("tool_call", job.subject, job.actor_role, "error",
                      {"stage": "job", "job_id": job.job_id, "task": job.task, "state": FAILED,
                       "reason": "attempts_exhausted", "attempts": job.attempts,
                       "job_id": job.job_id}, tool=job.task)
            return job

        if (time.monotonic() - started) > job.time_budget_s:
            transition(job, FAILED, reason="time_budget_exhausted")
            store.put(job)
            store.append(job.job_id, EV_DONE,
                         {"state": FAILED, "reason": "time_budget_exhausted", "attempts": job.attempts})
            if audit:
                audit("tool_call", job.subject, job.actor_role, "error",
                      {"stage": "job", "job_id": job.job_id, "task": job.task, "state": FAILED,
                       "reason": "time_budget_exhausted", "attempts": job.attempts,
                       "job_id": job.job_id}, tool=job.task)
            return job

        attempt = len(job.steps) + 1
        step = Step(step_id=uuid.uuid4().hex, job_id=job.job_id, attempt=attempt,
                    name="working", state=RUNNING)
        job.steps.append(step)
        store.put(job)
        store.append(job.job_id, EV_STEP, {"attempt": attempt, "of": limit, "name": step.name,
                                           "state": RUNNING})

        at = time.monotonic()
        try:
            outcome = attempt_once(job, attempt)
        except Exception as exc:  # noqa: BLE001 - a step that raises is a failed attempt, not a crash
            # **An unhandled exception is typed rather than propagated.** A job that dies with a traceback
            # leaves a row saying nothing, and the client is left watching a spinner on a job that is not
            # running. The failure belongs in the record.
            outcome = StepOutcome(ok=False, failure_kind="unhandled", failure_detail=type(exc).__name__,
                                  retryable=False)

        step.duration_ms = int((time.monotonic() - at) * 1000)
        step.name = outcome.name or step.name
        step.lane, step.model = outcome.lane, outcome.model

        if outcome.ok:
            step.state = SUCCEEDED
            job.result = outcome.result
            transition(job, SUCCEEDED)
            store.put(job)
            store.append(job.job_id, EV_STEP, {"attempt": attempt, "name": step.name, "state": SUCCEEDED,
                                               "lane": step.lane, "model": step.model,
                                               "duration_ms": step.duration_ms})
            if outcome.notice:
                store.append(job.job_id, EV_NOTICE, {"attempt": attempt, "text": outcome.notice})
            store.append(job.job_id, EV_DONE,
                         {"state": SUCCEEDED, "attempts": job.attempts,
                          "duration_ms": int((time.monotonic() - started) * 1000)})
            if audit:
                # **The attempt's own row, written for a success as well as for a failure.**
                #
                # The first version wrote one row when the job started and one when it finished, and a row
                # per attempt only when an attempt FAILED. So a job that worked first time left two rows
                # that both said "job", and nothing in the chain said a step had run. The probe caught it:
                # "2 row(s) name the job, 0 name an attempt".
                #
                # This is the C3 lesson in its general form. The columns existed, the flags existed, and
                # nobody connected them, so the record could not answer the question it is kept for. A
                # successful attempt is a thing that happened and belongs in the chain beside the failures,
                # or the chain tells a story in which work only occurs when something goes wrong.
                audit("tool_call", job.subject, job.actor_role, "ok",
                      {"stage": "job", "job_id": job.job_id, "task": job.task, "attempt": attempt,
                       "state": SUCCEEDED, "attempts": job.attempts, "lane": step.lane,
                       "duration_ms": step.duration_ms, "job_id": job.job_id},
                      tool=job.task, model=step.model or None, data_class=job.data_class,
                      egress_allowed=(step.lane == "frontier"))
                audit("tool_call", job.subject, job.actor_role, "ok",
                      {"stage": "job", "job_id": job.job_id, "task": job.task, "state": SUCCEEDED,
                       "attempts": job.attempts, "lane": step.lane, "job_id": job.job_id},
                      tool=job.task, model=step.model or None,
                      data_class=job.data_class, egress_allowed=(step.lane == "frontier"))
            return job

        step.state = FAILED
        step.failure_kind = outcome.failure_kind or "unclassified"
        step.failure_detail = outcome.failure_detail
        step.retryable = bool(outcome.retryable)
        store.put(job)
        store.append(job.job_id, EV_STEP, {"attempt": attempt, "name": step.name, "state": FAILED,
                                           "failure": {"kind": step.failure_kind,
                                                       "detail": step.failure_detail,
                                                       "retryable": step.retryable},
                                           "duration_ms": step.duration_ms})
        if audit:
            audit("tool_call", job.subject, job.actor_role, "error",
                  {"stage": "job", "job_id": job.job_id, "task": job.task, "attempt": attempt,
                   "state": RUNNING, "failure_kind": step.failure_kind,
                   "retryable": step.retryable, "job_id": job.job_id},
                  tool=job.task, model=step.model or None, data_class=job.data_class)

        if not outcome.retryable:
            # **A terminal failure is not retried, and the record says why.** Retrying a request that
            # cannot succeed spends the budget the degraded answer needed, which is the same rule the
            # request path already follows.
            transition(job, FAILED, reason=step.failure_kind)
            store.put(job)
            store.append(job.job_id, EV_DONE,
                         {"state": FAILED, "reason": step.failure_kind, "attempts": job.attempts,
                          "retryable": False})
            if audit:
                audit("tool_call", job.subject, job.actor_role, "error",
                      {"stage": "job", "job_id": job.job_id, "task": job.task, "state": FAILED,
                       "reason": step.failure_kind, "attempts": job.attempts,
                       "job_id": job.job_id}, tool=job.task, data_class=job.data_class)
            return job

        store.append(job.job_id, EV_PROGRESS,
                     {"attempt": attempt, "of": limit, "state": RUNNING,
                      "retrying_because": step.failure_kind})


def pause(job: Job, store) -> Job:
    """Stop after the current attempt. Refused once the job is finished, by the machine."""
    transition(job, PAUSED, reason="paused_by_operator")
    store.put(job)
    store.append(job.job_id, EV_PROGRESS, {"state": PAUSED})
    return job


def resume(job: Job, store) -> Job:
    transition(job, RUNNING)
    store.put(job)
    store.append(job.job_id, EV_PROGRESS, {"state": RUNNING, "note": "resumed"})
    return job


def cancel(job: Job, store) -> Job:
    """Stop, and keep what happened. The rows are the point of a job, so cancelling deletes nothing."""
    transition(job, CANCELLED, reason="cancelled_by_operator")
    store.put(job)
    store.append(job.job_id, EV_DONE, {"state": CANCELLED, "attempts": job.attempts})
    return job


# --------------------------------------------------------------------------- the approval gate
#
# **The state existed since C4 and nothing reached it.** `awaiting_approval` has been in ALLOWED since the
# machine was written, and the C4 notes say why: the interface has to render it, and a state added later
# would change the shape of the screen. C5 is its first writer, which is what makes two audit defects still
# cheap to fix rather than archaeology.


def action_digest(action: str) -> str:
    """The hash an approval quotes, over the thing being approved.

    **Over the content rather than an identifier.** A digest of a row id would survive the row being edited,
    so an approval would still verify against a draft that had since changed. Hashing the content means the
    thing approved and the thing recorded are the same thing or the route refuses.
    """
    import hashlib
    return "sha256:" + hashlib.sha256((action or "").encode("utf-8")).hexdigest()


def request_approval(job: Job, store, action: str, *, note: str = "") -> Job:
    """Stop at the gate, holding what it wants approved.

    The digest is written before the transition, so a job can never be observed in `awaiting_approval`
    without the thing it is waiting about. A window where the state says waiting and the digest is empty is a
    window where an approval could be recorded against nothing.
    """
    job.pending_action_digest = action_digest(action)
    transition(job, AWAITING_APPROVAL)
    store.put(job)
    store.append(job.job_id, EV_STEP, {
        "state": AWAITING_APPROVAL,
        "action_digest": job.pending_action_digest,
        "note": note or "waiting for a decision",
    })
    return job


def decide(job: Job, store, decision: str, action_digest_quoted: str) -> Job:
    """Apply a decision, and refuse one that has drifted from the thing it was made about.

    **The refusal is the feature, and it is the same argument as the state machine's.** An approval route that
    accepted any decision on a waiting job would record "somebody approved this" where the honest record is
    "somebody approved something else". A job whose draft changed while a client was reading it is exactly the
    case this catches, and it is not hypothetical: a retry can rewrite the draft between the screen rendering
    and the decision arriving.

    A denial and a request for changes both stop the job. The difference is what the next run learns: a denial
    says the work should not happen, and a request for changes says it should happen differently. Both keep the
    job's rows, because a decision is a fact about the work rather than a reason to delete it.
    """
    if job.state != AWAITING_APPROVAL:
        raise TransitionRefused(job.job_id, job.state, "decided")
    if not job.pending_action_digest or job.pending_action_digest != action_digest_quoted:
        raise ApprovalDrift(job.job_id, job.pending_action_digest, action_digest_quoted)

    if decision == DECISION_APPROVE:
        job.pending_action_digest = ""
        transition(job, RUNNING)
        store.append(job.job_id, EV_STEP, {"state": RUNNING, "note": "approved"})
    elif decision == DECISION_DENY:
        transition(job, FAILED, reason="denied_at_the_gate")
        store.append(job.job_id, EV_DONE, {"state": FAILED, "note": "denied"})
    else:
        transition(job, FAILED, reason="changes_requested_at_the_gate")
        store.append(job.job_id, EV_DONE, {"state": FAILED, "note": "changes requested"})
    store.put(job)
    return job
