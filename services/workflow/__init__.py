"""The workflow engine: a multi-step job, its gate, and the transaction its record shares.

**What this package is for.** `services/api/jobs.py` has the state machine, the event log, the budgets and the
stores. It runs one attempt of one step. This package adds the loop, the approval gate and the effects, and it
does the one thing an adopted engine cannot: it writes the state change and the chain row in the same
transaction.

**How to drive it.** Build a job with `jobs.new_job`, pick the plan with `plan_for(task)`, and hand both to a
runner:

    from workflow import plan_for, runner_for
    plan = plan_for(job.task)
    runner = runner_for(store, plan, mint=mint_statement, deployment_id=DEPLOYMENT_ID)
    runner.start(job)          # runs to a terminal state or stops at the gate
    runner.resume(job)         # after an approval, continues from the step after the gate

`mint(actor, role) -> statement` is required for a durable store and is how the engine proves who it is
recording. In the API that is `assertion.mint_audit(STATE["key"], actor, role, deployment_id=DEPLOYMENT_ID)`.
"""
from .engine import (  # noqa: F401
    FAIL_ATTEMPTS,
    FAIL_CANCELLED,
    FAIL_NO_RESULT,
    FAIL_RAISED,
    FAIL_TIME,
    FAIL_TIMEOUT,
    MemoryUnits,
    PostgresUnitFactory,
    Runner,
    WorkflowError,
    effect_key,
    runner_for,
)
from .plans import (  # noqa: F401
    INVOICE_INTAKE,
    PLANS,
    Action,
    Effect,
    Plan,
    StepResult,
    StepRun,
    StepSpec,
    budgets_for,
    plan_for,
    plan_names,
)

__all__ = [
    "Action", "Effect", "Plan", "StepResult", "StepRun", "StepSpec",
    "Runner", "runner_for", "effect_key", "WorkflowError", "plan_for", "plan_names", "PLANS",
    "budgets_for", "INVOICE_INTAKE", "MemoryUnits", "PostgresUnitFactory",
    "FAIL_TIMEOUT", "FAIL_CANCELLED", "FAIL_RAISED", "FAIL_NO_RESULT", "FAIL_ATTEMPTS", "FAIL_TIME",
]
