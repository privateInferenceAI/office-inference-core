"""The workflow engine, asserted against the deployment's own database.

**Why this is a probe and not more unit tests.** `tests/test_workflow_*.py` prove the loop with a scripted plan
and a memory store. They cannot prove the property the piece exists for, because that property is a property
of a *transaction*, and a memory store has none. **WF-ENGINE-001 is tested here or it is not tested**: this
file kills a process between the state write and the chain write and then reads Postgres.

    right        what the runbook promises                                    the wrong path it rules out
    -----------  ----------------------------------------------------------   --------------------------------
    RUNS         a six-step plan reaches the gate with real step rows          a loop that only runs one step
    GATED        the job stops at awaiting_approval holding a digest,          a gate that lets the work past
                 and no step after it has a row
    RESUMED      an approval finishes the job, and each post-gate step         a resume that repeats work
                 has exactly one row
    DENIED       a denial fails the job and nothing after the gate ran         a denial that only changes a label
    CANCELLED    a cancel stops the run and no later step has a row            a stop that leaves work running
    RECORDED     every step has a chain row naming the job id                  a step with no record
    ATOMIC       **killing the process between the state write and the         a webhook that reports
                 chain write leaves neither**                                  afterwards
    TYPED        a failed step carries a typed cause and no later step         a job that failed at the end
                 has a row                                                     after running everything
    BOUNDED      a step on a socket that never answers is stopped and          a job that hangs for ever
                 the job is terminal within its bound
    IDEMPOTENT   an effect performed before a crash is applied once            a retry that duplicates it
                 across the retry, counted at the far side
    CHAIN        the chain verifies after all of it                            rows written outside the chain

**Every right is paired with the wrong path it rules out**, because this project has twice shipped a check that
reported success while testing nothing.

Run from the deployment root on the box:

    cd /opt/oi-core
    bash scripts/probe.sh workflow_probe.py

Exit codes: 0 when every right held, 1 when one did not, 2 when nothing could be tested.
"""
from __future__ import annotations

import json
import os
import signal
import socket
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

W = Path("/w")
ROOT = W if W.is_dir() else Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "services"))
sys.path.insert(0, str(ROOT / "services" / "api"))
sys.path.insert(0, str(ROOT / "services" / "gateway"))

# The tool that owns the chain, from wherever this deployment keeps it. `/w` on the box, the checkout locally.
AUDIT_TOOL = os.environ.get("AUDIT_TOOL") or str(ROOT / "services" / "audit" / "audit.py")
os.environ.setdefault("AUDIT_TOOL", AUDIT_TOOL)

DEPLOYMENT = "workflow-probe"


def database_url() -> str:
    url = (os.environ.get("DATABASE_URL") or "").strip()
    if url:
        return url
    user = os.environ.get("POSTGRES_USER", "")
    password = os.environ.get("POSTGRES_PASSWORD", "")
    database = os.environ.get("POSTGRES_DB", "")
    if not (user and database):
        return ""
    return f"postgresql://{user}:{password}@postgres:5432/{database}"


def sql(statement: str, params: tuple = ()):
    import psycopg

    conn = psycopg.connect(database_url())
    try:
        with conn.cursor() as cur:
            cur.execute(statement, params)
            rows = cur.fetchall() if cur.description else []
        conn.commit()
        return rows
    finally:
        conn.close()


def key_path() -> str:
    explicit = os.environ.get("OI_ASSERTION_KEY")
    for candidate in (explicit, str(W / "rendered" / "assertion.key"), "/app/rendered/assertion.key"):
        if candidate and Path(candidate).is_file():
            return candidate
    return explicit or str(W / "rendered" / "assertion.key")


# --------------------------------------------------------------------------- shared plumbing

import assertion  # noqa: E402
import jobs  # noqa: E402
import workflow  # noqa: E402

KEY = assertion.load_key(key_path())


def mint(actor: str, role: str) -> str:
    return assertion.mint_audit(KEY, actor, role, deployment_id=DEPLOYMENT)


def connection():
    return jobs.PostgresStore(database_url())


def make_job(store, *, task: str, text: str, attempt_budget: int = 8, time_budget_s: int = 60):
    job = jobs.new_job(deployment_id=DEPLOYMENT, subject="probe", actor_role="staff", roots={"company"},
                       credential_sha256="0" * 64, task=task, input=text, data_class="public",
                       attempt_budget=attempt_budget, time_budget_s=time_budget_s)
    store.put(job)
    return job


def runner_for(store, plan):
    return workflow.runner_for(store, plan, mint=mint, deployment_id=DEPLOYMENT, key_path=key_path())


INVOICE = ("INV-2026-0417 from accounts@ferguson.example total 4210.00 USD due 2026-11-15")


class Ledger:
    def __init__(self) -> None:
        self.rows = []
        self.skipped = []

    def record(self, right: str, held: bool, observed: str, wrong_path: str) -> None:
        self.rows.append((right, held))
        print(("ok   " if held else "FAIL ") + right.ljust(11) + " " + observed, flush=True)
        if not held:
            print(" " * 12 + "what the runbook promised: " + wrong_path, flush=True)

    def skip(self, right: str, why: str) -> None:
        self.skipped.append(right)
        print("skip " + right.ljust(11) + " " + why, flush=True)

    def failures(self) -> int:
        return sum(1 for _, held in self.rows if not held)


def chain_rows(job_id: str) -> list:
    """Every chain row that names this job, by step, in order."""
    return sql("""select coalesce(payload->>'step', ''), coalesce(payload->>'step_state', '')
                    from audit_events where payload @> %s::jsonb order by id""",
               (json.dumps({"job_id": job_id}),))


def step_rows(job_id: str) -> list:
    return sql("""select name, state, coalesce(failure_kind, '') from job_steps
                   where job_id = %s order by attempt""", (job_id,))


# --------------------------------------------------------------------------- the rights


def right_runs_and_gate(ledger: Ledger) -> str:
    """RUNS and GATED: a six-step plan reaches the gate, and nothing after it has run."""
    store = connection()
    job = make_job(store, task="invoice_intake", text=INVOICE)
    runner = runner_for(store, workflow.plan_for("invoice_intake"))
    job = runner.start(job)

    rows = step_rows(job.job_id)
    before_gate = [row for row in rows if row[0] != "approval"]
    ledger.record("RUNS", len(rows) >= 4 and all(row[1] == "succeeded" for row in before_gate),
                  f"state={job.state!r}, {len(rows)} step row(s), "
                  f"{len(before_gate)} succeeded before the gate",
                  "a multi-step plan runs its steps and records each one")

    after = [row for row in rows if row[0] in ("post_entry", "close_out")]
    held = (job.state == jobs.AWAITING_APPROVAL and bool(job.pending_action_digest) and not after)
    ledger.record("GATED", held,
                  f"state={job.state!r}, digest={(job.pending_action_digest or '')[:18]}…, "
                  f"{len(after)} row(s) after the gate",
                  "the job stops at the gate holding what it wants approved, and no later step has a row")
    store.close()
    return job.job_id


def right_resumed(ledger: Ledger, job_id: str) -> None:
    """RESUMED: an approval finishes the job, and each post-gate step has exactly one row."""
    store = connection()
    job = store.get(job_id)
    shown = getattr(job, "pending_action", None)
    listed = sql("""select data->'action'->>'summary' from job_events
                     where job_id = %s and data ? 'action' order by seq desc limit 1""", (job_id,))
    held = bool(shown) or bool(listed and listed[0][0])
    where = "the job row" if shown else ("the gate event" if held else "nowhere")
    ledger.record("SHOWSACTION", held,
                  f"read from {where}: "
                  f"{(listed[0][0] if listed and listed[0][0] else '')[:70]!r}",
                  "the screen asking for a decision can show what is being decided, not only its hash")

    jobs.decide(job, store, "approve", job.pending_action_digest)
    runner = runner_for(store, workflow.plan_for("invoice_intake"))
    job = runner.resume(store.get(job_id))

    rows = step_rows(job_id)
    after = [row for row in rows if row[0] in ("post_entry", "close_out")]
    counted = {name: sum(1 for row in rows if row[0] == name) for name in ("post_entry", "close_out")}
    held = job.state == jobs.SUCCEEDED and len(after) == 2 and set(counted.values()) == {1}
    ledger.record("RESUMED", held,
                  f"state={job.state!r}, post-gate rows={counted}",
                  "an approval finishes the job and each step after the gate has exactly one row")
    store.close()


def right_denied(ledger: Ledger) -> None:
    """DENIED: a denial stops the job, and the steps after the gate never run."""
    store = connection()
    job = make_job(store, task="invoice_intake", text=INVOICE)
    runner = runner_for(store, workflow.plan_for("invoice_intake"))
    job = runner.start(job)
    jobs.decide(job, store, "deny", job.pending_action_digest)
    job = runner.resume(store.get(job.job_id))

    rows = step_rows(job.job_id)
    after = [row for row in rows if row[0] in ("post_entry", "close_out")]
    ledger.record("DENIED", job.state == jobs.FAILED and not after
                  and job.outcome_reason == "denied_at_the_gate",
                  f"state={job.state!r}, reason={job.outcome_reason!r}, {len(after)} row(s) after the gate",
                  "a denial ends the job with a typed reason and nothing after the gate runs")
    store.close()


def right_cancelled(ledger: Ledger) -> None:
    """CANCELLED: a cancel during a run stops it, and no later step has a row."""
    store = connection()

    def slow(run):
        for _ in range(100):
            if run.stopped():
                return workflow.StepResult(ok=False, failure_kind="cancelled_mid_step", retryable=False,
                                           failure_detail="the step noticed the cancel")
            time.sleep(0.05)
        return workflow.StepResult(output={"never": True})

    plan = workflow.Plan(name="cancel-me", steps=(
        workflow.StepSpec(name="slow", run=slow, timeout_s=20.0),
        workflow.StepSpec(name="after", run=lambda run: workflow.StepResult(output={"ran": True})),
    ))
    job = make_job(store, task="cancel-me", text="x", attempt_budget=4, time_budget_s=60)
    runner = runner_for(store, plan)

    def canceller():
        time.sleep(0.4)
        live = store.get(job.job_id)
        if live is not None and live.state == jobs.RUNNING:
            jobs.cancel(live, store)

    thread = threading.Thread(target=canceller, daemon=True)
    thread.start()
    job = runner.start(job)
    thread.join(timeout=5)

    rows = step_rows(job.job_id)
    after = [row for row in rows if row[0] == "after"]
    slow_row = [row for row in rows if row[0] == "slow"]
    held = (job.state == jobs.CANCELLED and not after and slow_row
            and slow_row[-1][1] != "running")
    ledger.record("CANCELLED", held,
                  f"state={job.state!r}, slow row={slow_row[-1] if slow_row else None}, "
                  f"{len(after)} row(s) after the stop",
                  "a cancelled job runs no further step, and the in-flight step is not left saying running")
    store.close()


def right_recorded(ledger: Ledger, job_id: str) -> None:
    """RECORDED: every step row has a chain row naming the job."""
    rows = step_rows(job_id)
    named = chain_rows(job_id)
    steps_in_chain = {row[0] for row in named if row[0]}
    held = len(named) >= len(rows) and len(steps_in_chain) >= 6
    ledger.record("RECORDED", held,
                  f"{len(rows)} step row(s), {len(named)} chain row(s) naming the job, "
                  f"{len(steps_in_chain)} distinct step(s)",
                  "every step the engine took left a row in the chain, addressable by job id")


def right_atomic(ledger: Ledger) -> None:
    """ATOMIC: killing the process between the state write and the chain write leaves neither.

    **The right the whole piece exists for.** A webhook implementation passes every other right in this file.
    This one starts a job in a child process, kills that process with SIGKILL at the moment the chain row is
    about to be written, and then reads Postgres. The state change was already sent on the same connection, so
    if the two are in one transaction the rollback takes it too.
    """
    store = connection()
    job = make_job(store, task="invoice_intake", text=INVOICE)
    job_id = job.job_id
    store.close()

    child = subprocess.run([sys.executable, str(Path(__file__).resolve()), "--kill-child", job_id],
                           capture_output=True, text=True, env=dict(os.environ))
    killed = child.returncode in (-signal.SIGKILL, 128 + signal.SIGKILL)

    rows = sql("select state from jobs where job_id = %s", (job_id,))
    state_after = rows[0][0] if rows else None
    steps = step_rows(job_id)
    recorded = chain_rows(job_id)

    # Neither present: the job is still queued, no step row survived, and no chain row names the job.
    held = killed and state_after == jobs.QUEUED and not steps and not recorded
    ledger.record("ATOMIC", held,
                  f"child exit={child.returncode} (killed={killed}), job row={state_after!r}, "
                  f"{len(steps)} step row(s), {len(recorded)} chain row(s)",
                  "killing the process between the state write and the chain write leaves neither behind")
    if not held:
        print(" " * 12 + "child stderr: " + (child.stderr or "")[-300:], flush=True)

    # The control: the same job, without the kill, leaves both.
    store = connection()
    job = make_job(store, task="invoice_intake", text=INVOICE)
    runner = runner_for(store, workflow.plan_for("invoice_intake"))
    runner.start(job)
    control_steps = step_rows(job.job_id)
    control_chain = chain_rows(job.job_id)
    ledger.record("BOTHPRESENT", bool(control_steps) and bool(control_chain),
                  f"control run: {len(control_steps)} step row(s), {len(control_chain)} chain row(s)",
                  "the same run without the kill leaves both, so ATOMIC is not passing on a broken probe")
    store.close()


def kill_child(job_id: str) -> int:
    """Run a job with the chain write replaced by SIGKILL. Exits by being killed, never by returning."""
    import workflow.chain as chain_module

    def die(cur, **row):
        os.kill(os.getpid(), signal.SIGKILL)
        raise SystemExit(137)

    chain_module.record = die
    store = connection()
    job = store.get(job_id)
    if job is None:
        print(f"child: no job {job_id}", file=sys.stderr)
        return 2
    runner = runner_for(store, workflow.plan_for("invoice_intake"))
    runner.start(job)
    print("child: finished without being killed, so the kill point was never reached", file=sys.stderr)
    return 3


def right_typed(ledger: Ledger) -> None:
    """TYPED: a failed step carries a cause, and no later step has a row."""
    store = connection()

    def refuse(run):
        return workflow.StepResult(ok=False, failure_kind="unreadable_invoice", retryable=False,
                                   failure_detail="the total could not be read")

    plan = workflow.Plan(name="typed", steps=(
        workflow.StepSpec(name="first", run=lambda run: workflow.StepResult(output={"ok": True})),
        workflow.StepSpec(name="refuse", run=refuse),
        workflow.StepSpec(name="third", run=lambda run: workflow.StepResult(output={"ran": True})),
    ))
    job = make_job(store, task="typed", text="x", attempt_budget=5)
    job = runner_for(store, plan).start(job)
    rows = step_rows(job.job_id)
    third = [row for row in rows if row[0] == "third"]
    refused = [row for row in rows if row[0] == "refuse"]
    held = (job.state == jobs.FAILED and not third and refused
            and refused[-1][2] == "unreadable_invoice")
    ledger.record("TYPED", held,
                  f"state={job.state!r}, refusing step={refused[-1] if refused else None}, "
                  f"{len(third)} row(s) after it",
                  "a failed step is typed and the loop does not continue past it")
    store.close()


def right_bounded(ledger: Ledger) -> None:
    """BOUNDED: a step on a socket that never answers is stopped, within the bound."""
    store = connection()
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen(1)

    def never_answers(run):
        client = socket.socket()
        client.connect(listener.getsockname())
        client.recv(16)          # nothing is ever sent, and the step ignores its own deadline
        return workflow.StepResult(output={"impossible": True})

    plan = workflow.Plan(name="bounded", steps=(
        workflow.StepSpec(name="hang", run=never_answers, timeout_s=0.5),
        workflow.StepSpec(name="after", run=lambda run: workflow.StepResult(output={"ran": True})),
    ))
    job = make_job(store, task="bounded", text="x", attempt_budget=4, time_budget_s=30)
    started = time.monotonic()
    job = runner_for(store, plan).start(job)
    elapsed = time.monotonic() - started
    listener.close()

    rows = step_rows(job.job_id)
    hang = [row for row in rows if row[0] == "hang"]
    after = [row for row in rows if row[0] == "after"]
    held = (job.state == jobs.FAILED and elapsed < 10 and not after and hang
            and hang[-1][2] == workflow.FAIL_TIMEOUT)
    ledger.record("BOUNDED", held,
                  f"state={job.state!r} after {elapsed:.2f}s (deadline 0.5s), "
                  f"kind={hang[-1][2] if hang else None!r}, {len(after)} row(s) after it",
                  "a step that never answers is stopped and the job reaches a terminal state inside its bound")
    store.close()


def right_idempotent(ledger: Ledger) -> None:
    """IDEMPOTENT: an effect performed before a failure is applied once across the retry.

    **The far side counts applications, not calls.** The step checks a file keyed by the effect key the engine
    gave it: the first attempt writes the key and then fails, the retry finds the key and reports the effect
    already applied. If the engine derived the key from the attempt number the far side would see two keys and
    apply the effect twice, which is the defect this right exists for.
    """
    store = connection()
    far_side = Path(tempfile.gettempdir()) / "workflow-probe-far-side.json"
    if far_side.exists():
        far_side.unlink()

    calls = {"n": 0}

    def external_effect(run):
        calls["n"] += 1
        seen = json.loads(far_side.read_text()) if far_side.exists() else {}
        if run.effect_key in seen:
            return workflow.StepResult(
                output={"applied": False},
                effects=[workflow.Effect(key="notify", mode="external", state="already_applied",
                                         detail={"idempotency_key": run.effect_key,
                                                 "applied": seen[run.effect_key], "calls": calls["n"]})])
        seen[run.effect_key] = calls["n"]
        far_side.write_text(json.dumps(seen))
        # The effect has happened, and now the step fails before the engine can record anything.
        return workflow.StepResult(ok=False, failure_kind="downstream_reset", retryable=True,
                                   failure_detail="the connection dropped after the effect was applied")

    plan = workflow.Plan(name="idempotent", steps=(
        workflow.StepSpec(name="notify", run=external_effect, timeout_s=5.0, attempts=2),
        workflow.StepSpec(name="after", run=lambda run: workflow.StepResult(output={"ran": True})),
    ))
    job = make_job(store, task="idempotent", text="x", attempt_budget=6, time_budget_s=30)
    job = runner_for(store, plan).start(job)

    seen = json.loads(far_side.read_text()) if far_side.exists() else {}
    applications = len(seen)
    ledger_rows = sql("select effect_key, mode, state from workflow_effects where job_id = %s", (job.job_id,))
    rows = step_rows(job.job_id)
    notify_rows = [row for row in rows if row[0] == "notify"]
    key_stable = len(seen) == 1 and len(notify_rows) >= 2
    held = job.state == jobs.SUCCEEDED and applications == 1 and key_stable and len(ledger_rows) == 1
    ledger.record("IDEMPOTENT", held,
                  f"state={job.state!r}, far side applied {applications} time(s) over {calls['n']} call(s), "
                  f"{len(notify_rows)} attempt row(s), {len(ledger_rows)} ledger row(s)",
                  "a retried step does not repeat an effect it already had, because the key does not move")
    store.close()


def right_chain(ledger: Ledger) -> None:
    """CHAIN: the export verifies after everything above wrote to it."""
    out = Path(tempfile.mkdtemp(prefix="workflow-probe-export-"))
    tool = AUDIT_TOOL
    exported = subprocess.run([sys.executable, tool, "export", "--out", str(out)],
                              capture_output=True, text=True, env=dict(os.environ))
    jsonl = out / "audit-events.jsonl"
    if exported.returncode != 0 or not jsonl.exists():
        ledger.record("CHAIN", False, f"export failed: {(exported.stderr or '')[-200:]}",
                      "the chain exports and verifies after a workflow run")
        return
    verified = subprocess.run([sys.executable, tool, "verify", str(jsonl)],
                              capture_output=True, text=True, env=dict(os.environ))
    first = (verified.stdout or "").strip().splitlines()
    ledger.record("CHAIN", verified.returncode == 0,
                  (first[0] if first else (verified.stderr or "")[-120:]),
                  "the chain verifies after a full run, a resumed run and a denied one")


# --------------------------------------------------------------------------- main


def main() -> int:
    if len(sys.argv) > 2 and sys.argv[1] == "--kill-child":
        return kill_child(sys.argv[2])

    ledger = Ledger()
    if not database_url():
        print("FAILED with 1 problem: no database credentials", file=sys.stderr)
        return 2
    try:
        store = connection()
        store.migrate(str(ROOT / "db" / "migrations"))
        store.close()
    except Exception as exc:  # noqa: BLE001 - the message is what a person needs
        print(f"FAILED: the database could not be prepared: {exc}", file=sys.stderr)
        return 2

    job_id = right_runs_and_gate(ledger)
    right_resumed(ledger, job_id)
    right_denied(ledger)
    right_cancelled(ledger)
    right_recorded(ledger, job_id)
    right_typed(ledger)
    right_bounded(ledger)
    right_idempotent(ledger)
    right_atomic(ledger)
    right_chain(ledger)

    held = len(ledger.rows) - ledger.failures()
    if ledger.failures():
        print(f"\nFAILED with {ledger.failures()} problem(s): the runbook and the engine disagree")
    else:
        print(f"\nPASSED: {held} right(s) held against the documented workflow engine"
              + (f", {len(ledger.skipped)} skipped" if ledger.skipped else ""))
    return 1 if ledger.failures() else 0


if __name__ == "__main__":
    raise SystemExit(main())
