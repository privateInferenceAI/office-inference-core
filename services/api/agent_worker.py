"""The in-process agent dispatcher.

**One runtime, and it is this process.** The API container runs the dispatcher in a background thread; there is
no second service, no queue broker and no second state store. A queued job is claimed with the same store that
the API already uses, and the claim is the state transition, so two workers cannot run the same job.

**The singleton is a lock, not a convention.** A Postgres advisory lock on a dedicated connection means a second
API container, or a second process, starts without the lock and refuses to be a worker instead of silently
doubling the claim rate. The memory store gets the same guarantee for tests and for a deployment with no
database.

The dispatcher does not know what an agent is. It calls the runner it is given; the runtime supplies that. That
is what lets the queue, the lock, the concurrency and the failure path be tested before the loop exists.
"""
from __future__ import annotations

import threading
from typing import Callable

import jobs

WORKER_LOCK_KEY = 72_001_001
_MEMORY_LOCK = threading.Lock()
_MEMORY_HELD: set[int] = set()


class _MemoryLease:
    def __init__(self, key: int):
        self.key = key

    def release(self) -> None:
        with _MEMORY_LOCK:
            _MEMORY_HELD.discard(self.key)


def _try_lease(store, key: int):
    """A lease, or None when another worker holds it.

    The durable path uses a Postgres session advisory lock on a connection of its own. It must be its own
    connection: a lock on the store's shared connection would be released by any reconnect that has nothing to
    do with the worker, and the second worker would start without anyone noticing.
    """
    if getattr(store, "durable", False):
        import psycopg  # imported here so a test with no driver still imports this module

        try:
            conn = psycopg.connect(store.database_url)
            conn.autocommit = True
        except Exception:  # noqa: BLE001 - the dispatcher must not take the API down if the lock cannot open
            return None
        with conn.cursor() as cur:
            cur.execute("select pg_try_advisory_lock(%s)", (key,))
            got = bool(cur.fetchone()[0])
        if not got:
            conn.close()
            return None
        return conn
    with _MEMORY_LOCK:
        if key in _MEMORY_HELD:
            return None
        _MEMORY_HELD.add(key)
    return _MemoryLease(key)


def _release_lease(lease, key: int) -> None:
    if lease is None:
        return
    if isinstance(lease, _MemoryLease):
        lease.release()
        return
    try:
        with lease.cursor() as cur:
            cur.execute("select pg_advisory_unlock(%s)", (key,))
    except Exception:  # noqa: BLE001 - closing the connection releases a session lock anyway
        pass
    finally:
        lease.close()


class AgentWorker:
    """Claim queued agent jobs and run them, up to a concurrency limit, under a singleton lock."""

    def __init__(self, store, run_job: Callable, task_ids: set[str], *, concurrency: int = 1,
                 poll_s: float = 0.05, lock_key: int = WORKER_LOCK_KEY):
        self.store = store
        self.run_job = run_job
        self.task_ids = set(task_ids)
        self.concurrency = max(1, int(concurrency))
        self.poll_s = poll_s
        self.lock_key = lock_key
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._lease = None
        self._slots = threading.Lock()
        self._active: set[threading.Thread] = set()

    # ---- lifecycle

    def start(self) -> bool:
        """Start the loop, or return False when another worker holds the lease."""
        if self._thread is not None and self._thread.is_alive():
            return True
        lease = _try_lease(self.store, self.lock_key)
        if lease is None:
            return False
        self._lease = lease
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, name="agent-dispatcher", daemon=True)
        self._thread.start()
        return True

    def stop(self, timeout: float = 5.0) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout)
            self._thread = None
        with self._slots:
            running = list(self._active)
        for thread in running:
            thread.join(timeout)
        _release_lease(self._lease, self.lock_key)
        self._lease = None

    @property
    def active(self) -> int:
        with self._slots:
            return len(self._active)

    # ---- the loop

    def _loop(self) -> None:
        while not self._stop.is_set():
            free = self.concurrency - self.active
            if free > 0 and self.task_ids:
                for job in self.store.claim_queued(free, task_ids=self.task_ids):
                    thread = threading.Thread(target=self._run, args=(job,),
                                              name=f"agent-job-{job.job_id}", daemon=True)
                    with self._slots:
                        self._active.add(thread)
                    thread.start()
            self._stop.wait(self.poll_s)

    def _run(self, job) -> None:
        try:
            self.run_job(job)
        except Exception as exc:  # noqa: BLE001 - one job's failure must not kill the dispatcher
            self._fail(job, "runtime_error", f"the runtime raised {type(exc).__name__}: {exc}")
        finally:
            with self._slots:
                self._active.discard(threading.current_thread())
        # **A runner that returns without moving the job is a bug, and the dispatcher says so.** Leaving it
        # running would make a stuck job look busy forever; succeeding it would be a lie. The typed failure is
        # the only honest outcome.
        fresh = self.store.get(job.job_id)
        if fresh is not None and fresh.state == jobs.RUNNING:
            self._fail(job, "runtime_returned_running",
                       "the agent runtime returned without a terminal state or a gate")

    def _fail(self, job, reason: str, detail: str) -> None:
        fresh = self.store.get(job.job_id)
        if fresh is None or fresh.state in jobs.TERMINAL:
            return
        try:
            jobs.transition(fresh, jobs.FAILED, reason=reason)
            fresh.result = dict(fresh.result or {}, error=detail)
            self.store.put(fresh)
            self.store.append(job.job_id, "error", {"reason": reason, "detail": detail})
        except Exception:  # noqa: BLE001 - the dispatcher has done all it can; the API must stay up
            pass
