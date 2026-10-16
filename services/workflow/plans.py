"""What a workflow is made of, and the one this deployment ships.

**A plan is data, not code that runs.** It is an ordered list of steps, each a name plus a callable. The engine
owns the loop, the transaction, the gate and the budgets; a plan owns only what the work is. That split is what
lets C6 put a tool loop behind the same engine without the engine changing, and it is why a plan can be tested
without a deployment.

**A step returns data and cannot touch the database.** The callable is handed a `StepRun` carrying the input,
the outputs of the steps before it, a deadline and a stable effect key. It returns a `StepResult`. Effects come
back as values and the engine writes them, which is the only arrangement in which an effect and the state
change that records it can be one transaction. A step that reached for a connection would reopen the gap this
whole piece exists to close.

**The gate is a step with no callable.** It has a name, so it has a row, so a resumed run can tell that it was
reached and passed. It holds an `Action`: the words behind the digest, so the screen asking for a decision can
show what is being decided.
"""
from __future__ import annotations

import hashlib
import json
import re
import time
from dataclasses import asdict, dataclass, field
from typing import Callable, Optional

# --------------------------------------------------------------------------- the pieces


@dataclass(frozen=True)
class Action:
    """What a gate is holding, and what an approval is about.

    `canonical()` is what the digest is computed over. It is a canonical serialisation of the content rather
    than an identifier, because a digest of an identifier survives the content being edited and the approval
    would then verify against a draft nobody saw.

    The display fields are separate from the digest on purpose: the digest covers the content, and the screen
    renders the content. Neither is derived from the other, and a change to one without the other is a bug the
    Review screen would show as a decision about nothing.
    """

    kind: str
    summary: str
    reason: str
    target: str
    subject: str
    body: str
    consequence: str

    def canonical(self) -> str:
        return json.dumps(asdict(self), sort_keys=True, separators=(",", ":"), ensure_ascii=False)

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass(frozen=True)
class Effect:
    """Something a step did that must not happen twice.

    `mode` says where it happened, and the two answers are not interchangeable. `transactional` means the
    engine writes it inside the step's transaction, so a crash cannot separate it from the state change.
    `external` means it happened outside, and the engine can only hand the far side a key that is stable across
    attempts and record what the far side reported.
    """

    key: str = "default"
    mode: str = "transactional"
    state: str = "applied"
    detail: dict = field(default_factory=dict)


@dataclass
class StepResult:
    """What one attempt produced. A failure carries a typed kind, never prose in the kind's place."""

    ok: bool = True
    output: dict = field(default_factory=dict)
    effects: list = field(default_factory=list)
    failure_kind: str = ""
    failure_detail: str = ""
    retryable: bool = False
    notice: str = ""


@dataclass
class StepRun:
    """Everything a step may know, and the two things it must respect: the deadline and the stop flag."""

    job: object
    input: str
    prior: dict
    attempt: int
    step_attempt: int
    deadline: float
    effect_key: str
    _stopped: Optional[Callable[[], bool]] = None

    def remaining(self) -> float:
        return max(0.0, self.deadline - time.monotonic())

    def stopped(self) -> bool:
        """Whether the job has been cancelled or paused since this step started.

        A cooperative step checks this and returns. The engine also stops waiting at the deadline, because a
        step blocked on a socket will not check anything — but a step that ignores it keeps running, and the
        engine's comment says so rather than implying it was killed.
        """
        return bool(self._stopped and self._stopped())


@dataclass(frozen=True)
class StepSpec:
    """One entry in a plan. `run` is absent exactly when `gate` is true."""

    name: str
    run: Optional[Callable[[StepRun], StepResult]] = None
    gate: bool = False
    approval_of: Optional[Callable[[StepRun], Action]] = None
    timeout_s: float = 30.0
    attempts: int = 1


@dataclass(frozen=True)
class Plan:
    """An ordered list of steps, and the budgets a job running it needs."""

    name: str
    steps: tuple

    def names(self) -> list:
        return [step.name for step in self.steps]

    @property
    def step_count(self) -> int:
        return len(self.steps)

    @property
    def attempt_budget(self) -> int:
        """Enough attempts for every step to fail once and be retried, which is what the demo needs."""
        return sum(max(1, step.attempts) for step in self.steps)

    @property
    def time_budget_s(self) -> int:
        return max(60, int(sum(step.timeout_s * max(1, step.attempts) for step in self.steps) * 2))


# --------------------------------------------------------------------------- the invoice plan
#
# The plan the C5 pieces are built around: an invoice arrives, gets read, gets turned into an entry somebody
# has to approve, and only then gets posted. It is deterministic and offline on purpose. A plan that needed a
# model to run could not be used to test the engine's transaction, and the thing being tested here is the
# transaction rather than the extraction.

_AMOUNT = re.compile(r"\btotal\s+([0-9][0-9,]*(?:\.[0-9]{1,2})?)", re.IGNORECASE)
_VENDOR = re.compile(r"\bfrom\s+([^\s,;]+)", re.IGNORECASE)
_DUE = re.compile(r"\bdue\s+([0-9]{4}-[0-9]{2}-[0-9]{2})", re.IGNORECASE)
_REFERENCE = re.compile(r"\b([A-Z]{2,}-[0-9]{4}-[0-9]{3,})\b")


def _read_message(run: StepRun) -> StepResult:
    """Read the message, and record the hash of what was read rather than the message itself."""
    raw = (run.input or "").strip()
    if not raw:
        return StepResult(ok=False, failure_kind="empty_input", retryable=False,
                          failure_detail="the job carries no input to read")
    digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()
    reference = _REFERENCE.search(raw)
    return StepResult(output={
        "message_sha256": digest,
        "message_id": digest[:16],
        "reference": reference.group(1) if reference else "",
        "length": len(raw),
    })


def _extract_fields(run: StepRun) -> StepResult:
    """Pull the four fields an entry needs, and refuse rather than guess when one is missing."""
    raw = run.input or ""
    amount = _AMOUNT.search(raw)
    vendor = _VENDOR.search(raw)
    if not amount or not vendor:
        # **A typed refusal rather than a default.** An invoice whose total could not be read is not an invoice
        # for zero, and continuing with a zero would post an entry nobody approved the shape of.
        missing = "total" if not amount else "vendor"
        return StepResult(ok=False, failure_kind="unreadable_invoice", retryable=False,
                          failure_detail=f"the {missing} could not be read from the message")
    due = _DUE.search(raw)
    cleaned = amount.group(1).replace(",", "")
    return StepResult(output={
        "vendor": vendor.group(1),
        "amount": cleaned,
        "amount_minor": int(round(float(cleaned) * 100)),
        "currency": "USD",
        "due": due.group(1) if due else "",
        "reference": run.prior.get("read_message", {}).get("reference", ""),
    })


def _draft_action(run: StepRun) -> StepResult:
    """Compose the entry that will be posted, which is the thing the gate holds."""
    fields = run.prior.get("extract_fields") or {}
    reference = fields.get("reference") or run.prior.get("read_message", {}).get("message_id", "")
    entry = {
        "reference": reference,
        "vendor": fields.get("vendor", ""),
        "amount_minor": fields.get("amount_minor", 0),
        "currency": fields.get("currency", "USD"),
        "due": fields.get("due", ""),
        "account": "accounts_payable",
    }
    return StepResult(output={"entry": entry, "body": json.dumps(entry, sort_keys=True)})


def _approval_of(run: StepRun) -> Action:
    """The action the gate holds, built from the draft the step before it produced."""
    draft = run.prior.get("draft_action") or {}
    entry = draft.get("entry") or {}
    amount = entry.get("amount_minor", 0) / 100
    return Action(
        kind="ledger.post",
        summary=f"Post {entry.get('currency', 'USD')} {amount:,.2f} to accounts payable for "
                f"{entry.get('vendor', 'an unnamed vendor')}",
        reason="an entry that moves money is posted by a person, not by the thing that read the invoice",
        target=entry.get("vendor", ""),
        subject=f"invoice {entry.get('reference') or '(no reference)'}",
        body=draft.get("body", ""),
        consequence="the entry is written to the ledger and the vendor is notified on the next run",
    )


def _post_entry(run: StepRun) -> StepResult:
    """Post the approved entry, and report both effects.

    The ledger write is `transactional`: the engine writes it in the same transaction as this step's state
    change, so a crash between them cannot happen. The vendor notification is `external`: it leaves the
    process, so all the engine can do is hand it a key that does not change between attempts.
    """
    draft = run.prior.get("draft_action") or {}
    entry = dict(draft.get("entry") or {})
    entry["posted"] = True
    if run.stopped():
        return StepResult(ok=False, failure_kind="cancelled_mid_step", retryable=False,
                          failure_detail="the job was cancelled while this step was running")
    return StepResult(
        output={"entry": entry},
        effects=[
            Effect(key="ledger", mode="transactional", state="applied",
                   detail={"reference": entry.get("reference", ""), "amount_minor": entry.get("amount_minor", 0)}),
            Effect(key="notify", mode="external", state="applied",
                   detail={"idempotency_key": run.effect_key, "to": entry.get("vendor", "")}),
        ],
    )


def _close_out(run: StepRun) -> StepResult:
    """The answer the job keeps, assembled from what the steps before it produced."""
    posted = (run.prior.get("post_entry") or {}).get("entry") or {}
    fields = run.prior.get("extract_fields") or {}
    return StepResult(output={
        "summary": f"posted {posted.get('reference') or fields.get('reference') or 'the entry'}",
        "posted": posted,
        "vendor": fields.get("vendor", ""),
        "amount_minor": fields.get("amount_minor", 0),
        "steps": [step for step in run.prior],
    })


INVOICE_INTAKE = Plan(name="invoice_intake", steps=(
    StepSpec(name="read_message", run=_read_message, timeout_s=10.0),
    StepSpec(name="extract_fields", run=_extract_fields, timeout_s=10.0),
    StepSpec(name="draft_action", run=_draft_action, timeout_s=10.0),
    StepSpec(name="approval", gate=True, approval_of=_approval_of, timeout_s=5.0),
    StepSpec(name="post_entry", run=_post_entry, timeout_s=10.0, attempts=2),
    StepSpec(name="close_out", run=_close_out, timeout_s=10.0),
))

PLANS = {INVOICE_INTAKE.name: INVOICE_INTAKE}


def plan_for(task: str) -> Optional[Plan]:
    """The plan this deployment would run for a task, or None.

    None is the honest answer for a task with no plan: the caller falls back to the single-step executor rather
    than inventing a multi-step shape for work nobody described.
    """
    return PLANS.get((task or "").strip())


def budgets_for(task: str) -> dict:
    """The budgets a job running this task has to be created with, or `{}` when there is no plan.

    **A plan with six steps needs a budget of at least six, and the default is five.** That is not a detail:
    the engine enforces the budget it is given, so a six-step plan run under the default would fail at the
    last step with `attempts_exhausted`, having done all the work and recorded none of the outcome. The
    failure is correct and it is the caller's to prevent, so the number a job must be created with is a
    function of the plan rather than a constant somebody remembers.
    """
    plan = plan_for(task)
    if plan is None:
        return {}
    return {"attempt_budget": plan.attempt_budget, "time_budget_s": plan.time_budget_s}


def plan_names() -> list:
    return sorted(PLANS)
