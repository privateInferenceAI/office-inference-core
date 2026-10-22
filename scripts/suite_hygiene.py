#!/usr/bin/env python3
"""The checks that keep a suite run from being taken under contention, and the record of where it was taken.

**Why this is a module rather than two lines in each caller.** A suite run starts in one of three ways: the host
script (`scripts/run_answer_suite.sh`), the runner it launches (`tests/fixtures/data_suite_run.py`), or a bare
`docker run` of that runner from a shell. The first two have to agree on one lock name, one pair of thresholds and
one definition of what a run context contains, or the rule that admitted a run and the record that describes it
can disagree. The logic lives here, in the standard library only, so the host script and the container runner can
share it and the tests can exercise every refusal without a database.

**What is enforced where.** The host script holds an flock for its whole life and refuses to start when the
one-minute load average or `MemAvailable` says the box is busy. The runner holds a Postgres advisory lock so a
direct `docker run` is caught too. Neither check is advisory: a run that fails one does not start, because a
measurement taken under load is not repaired by a caveat.

Usage on the box, from the deployment root:

    python3 scripts/suite_hygiene.py gate
    python3 scripts/suite_hygiene.py preflight --root /opt/oi-core --out /tmp/suite/run-context.json \
        --compose-file deploy/office/compose/docker-compose.yml --env-file rendered/env
    python3 scripts/suite_hygiene.py budget --out /tmp/suite/token-budget.json --stage start \
        --compose-file deploy/office/compose/docker-compose.yml --env-file rendered/env

The runner calls the functions directly; the subcommands exist so the shell script and an operator use the same
implementation as the tests.
"""
from __future__ import annotations

import argparse
import contextlib
import json
import math
import os
import re
import shlex
import socket
import subprocess
import sys
from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

PROC_LOADAVG = Path("/proc/loadavg")
PROC_MEMINFO = Path("/proc/meminfo")
CLOUD_INSTANCE_ID = Path("/var/lib/cloud/data/instance-id")

# **The defaults are the values the 2026-10-20 decision records.** The reference box is an m7i.4xlarge with 16
# vCPUs, so a one-minute load above 2.0 means something other than the suite is working. Raising either limit is
# a decision to measure under contention, not a tuning knob, and belongs in the decision record.
MAX_LOAD_VARIABLE = "OI_SUITE_MAX_LOAD"
MIN_AVAILABLE_MB_VARIABLE = "OI_SUITE_MIN_AVAILABLE_MB"
DEFAULT_MAX_LOAD = 2.0
DEFAULT_MIN_AVAILABLE_MB = 2048

# **One key for every deployment that shares a Postgres.** The value is the date the rule was adopted, not a hash,
# so a person reading `pg_locks` can recognize it. It has to stay stable: changing it silently stops excluding the
# older runners, which is the failure this lock exists to prevent.
ADVISORY_LOCK_KEY = 20261020
LOCK_EXIT_CODE = 4
TOKEN_EXIT_CODE = 5

# Names that describe the runner or the host rather than the answer path, and names that hold credentials. Neither
# belongs in a context record that can end up committed with a measurement.
NON_ANSWER_VARIABLES = (
    "OI_ROOT", "OI_UI_ROOT", "OI_API_URL", "OI_RUN_CONTEXT", "OI_INSTANCE_ID",
    "OI_SUITE_", "OI_TEST_", "OI_ASSERTION_",
)
SECRET_MARKERS = ("SECRET", "TOKEN", "PASSWORD", "PASSWD", "PRIVATE_KEY", "CREDENTIAL")

REQUIRED_CONTEXT_KEYS = (
    "timestamp", "git_commit", "oi_env", "images",
    "load_average", "mem_available_mb", "hostname", "instance_id",
)


class GateRefused(RuntimeError):
    """The box is not quiet enough to start a run, or the signal to tell is missing."""


class SuiteRefused(RuntimeError):
    """The run cannot take its serial lock, or cannot reach the database to try."""


class SuiteLockBusy(SuiteRefused):
    """Another suite run holds the database lock."""


@dataclass(frozen=True)
class GateSettings:
    max_load: float = DEFAULT_MAX_LOAD
    min_available_mb: int = DEFAULT_MIN_AVAILABLE_MB


def _number(environ: Mapping[str, str], variable: str, cast: Callable[[str], Any], default: Any) -> Any:
    raw = (environ.get(variable) or "").strip()
    if not raw:
        return default
    try:
        value = cast(raw)
    except ValueError:
        raise ValueError(f"{variable} must be a number, got {raw!r}") from None
    if isinstance(value, float) and not math.isfinite(value):
        raise ValueError(f"{variable} must be a finite number, got {raw!r}")
    if value <= 0:
        raise ValueError(f"{variable} must be greater than zero, got {raw!r}")
    return value


def settings_from_env(environ: Mapping[str, str] | None = None) -> GateSettings:
    """The limits the run is admitted under, read from the environment the script runs in.

    A mistyped limit is an error rather than a fallback to the default. Falling back would quietly widen the gate
    that the operator believed they had set, which is the same class of mistake as a check that passes while
    testing nothing.
    """
    environ = os.environ if environ is None else environ
    return GateSettings(
        max_load=_number(environ, MAX_LOAD_VARIABLE, float, DEFAULT_MAX_LOAD),
        min_available_mb=_number(environ, MIN_AVAILABLE_MB_VARIABLE, int, DEFAULT_MIN_AVAILABLE_MB),
    )


def read_load_average(path: Path | str = PROC_LOADAVG) -> float | None:
    """The one-minute load average, or None when it cannot be read."""
    try:
        return float(Path(path).read_text(encoding="utf-8").split()[0])
    except (OSError, IndexError, ValueError):
        return None


def read_mem_available_mb(path: Path | str = PROC_MEMINFO) -> int | None:
    """MemAvailable in megabytes, or None when it cannot be read. `/proc/meminfo` reports kilobytes."""
    try:
        for line in Path(path).read_text(encoding="utf-8").splitlines():
            if line.startswith("MemAvailable:"):
                return int(line.split()[1]) // 1024
    except (OSError, IndexError, ValueError):
        return None
    return None


def check_gate(load_average: float | None, mem_available_mb: int | None,
               settings: GateSettings | None = None) -> dict:
    """Refuse when the box is busy, naming what was observed. Returns the observation on success.

    A missing signal is a refusal, not a pass. `/proc` being unreadable on the box means the check cannot tell
    whether the measurement is serial, and a run that cannot prove it is serial is the case the rule is for.
    """
    settings = settings or GateSettings()
    problems = []
    if load_average is None:
        problems.append(f"could not read a load average from {PROC_LOADAVG}")
    elif load_average > settings.max_load:
        problems.append(f"load average {load_average:.2f} is above the {settings.max_load:.2f} limit for a run")
    if mem_available_mb is None:
        problems.append(f"could not read MemAvailable from {PROC_MEMINFO}")
    elif mem_available_mb < settings.min_available_mb:
        problems.append(f"MemAvailable {mem_available_mb} MB is below the {settings.min_available_mb} MB floor")
    if problems:
        raise GateRefused(
            "; ".join(problems)
            + f". The limits are {MAX_LOAD_VARIABLE} and {MIN_AVAILABLE_MB_VARIABLE}; raising either is a decision "
              "to measure under contention, not a fix. Wait for the box to go idle and start again."
        )
    return {"load_average": load_average, "mem_available_mb": mem_available_mb,
            "gates": {"max_load": settings.max_load, "min_available_mb": settings.min_available_mb}}


# --------------------------------------------------------------------------- the daily provider-token budget

# **The provider quota is account-wide and is not adjustable.** Bedrock enforces a Cross-Model Max Tokens Per Day
# of 150,000,000 on the account, there is no premium support plan to raise it, and both deployments here draw on
# the same number. The suite's own budget is a fifth of it: large enough for a long measurement day, small enough
# that other gateway traffic and the other box cannot turn the last run of a series into a degraded answer. The
# figure is a working budget under a quota that cannot move today. It is not a tuning knob.
DEFAULT_DAILY_TOKEN_LIMIT = 30_000_000
DEFAULT_TOKEN_RESERVE = 0

# **The projection when this output directory has no measured run.** 2,000,000 tokens is the documented
# conservative default. It is deliberately above the cost of a normal run, because stopping one run early costs a
# run while crossing the quota mid-series costs the series. From the second run onwards, the average of the runs
# recorded in the same output directory replaces it.
DEFAULT_RUN_TOKENS = 2_000_000

DAILY_TOKEN_LIMIT_VARIABLE = "OI_SUITE_DAILY_TOKEN_LIMIT"
TOKEN_RESERVE_VARIABLE = "OI_SUITE_TOKEN_RESERVE"
TOKEN_GUARD_VARIABLE = "OI_SUITE_TOKEN_GUARD"
TOKEN_GUARD_REFUSE = "refuse"
TOKEN_GUARD_WARN = "warn"
TOKEN_BUDGET_FILE = "token-budget.json"

UTC_DAY = re.compile(r"\d{4}-\d{2}-\d{2}")


class TokenBudgetRefused(RuntimeError):
    """The day's provider-token budget would be crossed, or the usage cannot be read to prove otherwise."""


@dataclass(frozen=True)
class TokenBudgetSettings:
    daily_limit: int = DEFAULT_DAILY_TOKEN_LIMIT
    reserve: int = DEFAULT_TOKEN_RESERVE
    guard: str = TOKEN_GUARD_REFUSE


def _whole_number(environ: Mapping[str, str], variable: str, default: int, minimum: int) -> int:
    raw = (environ.get(variable) or "").strip()
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError:
        raise ValueError(f"{variable} must be a whole number of tokens, got {raw!r}") from None
    if value < minimum:
        raise ValueError(f"{variable} must be at least {minimum}, got {raw!r}")
    return value


def token_settings_from_env(environ: Mapping[str, str] | None = None) -> TokenBudgetSettings:
    """The budget the phase is admitted under, read from the environment.

    A mistyped limit is an error, not a fallback to the default, for the same reason the load gate treats one that
    way: falling back would quietly widen the budget the operator believed they had set. The provider quota above
    the budget is not adjustable today, so only the working budget moves.
    """
    environ = os.environ if environ is None else environ
    daily_limit = _whole_number(environ, DAILY_TOKEN_LIMIT_VARIABLE, DEFAULT_DAILY_TOKEN_LIMIT, 1)
    reserve = _whole_number(environ, TOKEN_RESERVE_VARIABLE, DEFAULT_TOKEN_RESERVE, 0)
    if reserve >= daily_limit:
        raise ValueError(
            f"{TOKEN_RESERVE_VARIABLE} ({reserve}) must be below {DAILY_TOKEN_LIMIT_VARIABLE} ({daily_limit})")
    guard = (environ.get(TOKEN_GUARD_VARIABLE) or TOKEN_GUARD_REFUSE).strip().lower()
    if guard not in (TOKEN_GUARD_REFUSE, TOKEN_GUARD_WARN):
        raise ValueError(
            f"{TOKEN_GUARD_VARIABLE} must be {TOKEN_GUARD_REFUSE!r} or {TOKEN_GUARD_WARN!r}, got {guard!r}")
    return TokenBudgetSettings(daily_limit=daily_limit, reserve=reserve, guard=guard)


def utc_day(now: datetime | None = None) -> str:
    """The UTC day a usage figure belongs to. The spend table records UTC, so the day boundary is UTC too."""
    moment = now or datetime.now(timezone.utc)
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(timezone.utc).strftime("%Y-%m-%d")


def utc_stamp(now: datetime | None = None) -> str:
    moment = now or datetime.now(timezone.utc)
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def token_usage_query(day: str) -> str:
    """The SQL that totals today's provider tokens from the gateway's spend table.

    startTime is the UTC timestamp LiteLLM writes per call and total_tokens is the prompt plus completion count
    it recorded. The literal is a plain timestamp and the session runs with PGTZ=UTC, so the comparison means the
    same thing whether the column is timestamp or timestamptz. Every status is counted: a refused call spends
    what the provider says it spent, usually nothing, and filtering would make the total a guess.
    """
    if not UTC_DAY.fullmatch(day or ""):
        raise ValueError(f"a UTC day must look like 2026-10-21, got {day!r}")
    return ('select coalesce(sum(total_tokens), 0) from "LiteLLM_SpendLogs" '
            f"where \"startTime\" >= timestamp '{day} 00:00:00'")


# A fallback call is a model call the gateway made against the recovery model after the primary failed. A run
# containing one is not a measurement of the primary model, however good the score looks. The check is a separate
# admission condition from the token budget because the failure is silent: the answer comes back, and only the
# audit row says it came from a different model.
UTC_SECOND = re.compile(r"^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z$")


def fallback_usage_query(since: str) -> str:
    """The SQL that counts fallback model calls at or after a UTC timestamp.

    The timestamp is validated against a fixed shape and then embedded as a plain timestamp with the session in
    UTC, so a caller cannot smuggle SQL through it and the comparison means one thing everywhere.
    """
    if not UTC_SECOND.fullmatch(since or ""):
        raise ValueError(f"a UTC timestamp must look like 2026-10-21T20:00:00Z, got {since!r}")
    return ("select count(*) from audit_events where event_type = 'model_call' "
            "and payload->>'fallback_used' = 'true' "
            f"and occurred_at >= timestamp '{since}'")


def compose_postgres_fallbacks(compose_args: list[str] | tuple[str, ...], since: str,
                               run: Callable[..., str | None] | None = None) -> int | None:
    """Fallback calls at or after a timestamp, read through the deployment's own postgres container.

    None means the count could not be read, which the caller must treat as a refusal: a run whose model mix is
    unknown is not a valid measurement. A malformed answer raises ValueError.
    """
    sql = fallback_usage_query(since)
    command = ["docker", "compose", *compose_args, "exec", "-T", "postgres", "sh", "-c",
               'PGTZ=UTC psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -A -t -c ' + shlex.quote(sql)]
    output = (run or run_command)(command, 30.0)
    if output is None:
        return None
    return parse_token_count(output)


def parse_token_count(output: str) -> int:
    """The single integer psql prints for an aggregate in its -A -t mode, or an error naming what came back.

    psql prints nothing at all when it fails before the query, and prints the error sentence on stderr, which
    run_command does not return. An empty answer is therefore a refusal rather than a zero: a phase that cannot
    read its usage must not conclude that it has spent nothing.
    """
    lines = [line.strip() for line in (output or "").splitlines() if line.strip()]
    if not lines:
        raise ValueError("the spend query returned no rows, so today's token usage is unknown")
    value = lines[-1]
    try:
        count = int(value)
    except ValueError:
        raise ValueError(f"the spend query returned {value!r}, not a token count") from None
    if count < 0:
        raise ValueError(f"the spend query returned a negative token count: {value!r}")
    return count


def compose_postgres_tokens(compose_args: list[str] | tuple[str, ...], day: str,
                            run: Callable[..., str | None] | None = None) -> int | None:
    """Today's token total, read through the deployment's own postgres container.

    This is the approach the other preflight checks already use: docker compose exec against a service the
    deployment runs, with the compose file and env file the script was started with. The container's own
    POSTGRES_USER and POSTGRES_DB name the database, so the host needs no copy of them and no database driver.
    None means the total could not be read; a malformed answer raises ValueError.
    """
    sql = token_usage_query(day)
    command = ["docker", "compose", *compose_args, "exec", "-T", "postgres", "sh", "-c",
               'PGTZ=UTC psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -A -t -c ' + shlex.quote(sql)]
    output = (run or run_command)(command, 30.0)
    if output is None:
        return None
    return parse_token_count(output)


def run_cost_projection(runs: list[Mapping[str, Any]]) -> tuple[int, str]:
    """What the next run is expected to cost, and the sentence that says where the number came from.

    The average is taken over the completed runs in this output directory, not over the day, because a different
    phase's corpus or flag set is not this phase's cost. A run whose spend did not move is left out rather than
    averaged in as zero: the gateway can lag the table it writes, and a zero would understate the next run.
    """
    measured = [int(run["tokens_consumed"]) for run in runs
                if isinstance(run.get("tokens_consumed"), int) and run["tokens_consumed"] > 0]
    if not measured:
        return DEFAULT_RUN_TOKENS, ("the conservative default, because no completed run in this output "
                                    "directory has a measurable token count")
    return max(1, round(sum(measured) / len(measured))), (
        f"the average of {len(measured)} completed run(s) in this output directory")


def format_tokens(value: int | None) -> str:
    return "unknown" if value is None else f"{value:,}"


def evaluate_token_budget(observed: int | None, settings: TokenBudgetSettings | None = None, *,
                          projected_run: int = DEFAULT_RUN_TOKENS,
                          projection_source: str = "the conservative default",
                          completed_runs: int = 0, unavailable_reason: str = "") -> dict:
    """Whether the next run fits under the day's budget, as a decision rather than an exception.

    The refusal is returned here so the sidecar can record it and the caller can decide how to stop. A missing
    reading is a refusal unless the operator set OI_SUITE_TOKEN_GUARD=warn, which is an explicit decision to
    measure without a budget proof and is recorded as a warning in the sidecar.
    """
    settings = settings or TokenBudgetSettings()
    decision: dict[str, Any] = {
        "allowed": False,
        "observed_tokens": observed,
        "day_limit": settings.daily_limit,
        "reserve": settings.reserve,
        "projected_run_tokens": projected_run,
        "projection_source": projection_source,
        "completed_runs": completed_runs,
        "available_tokens": None,
        "headroom_after_run": None,
        "warnings": [],
        "reason": "",
    }

    if observed is None:
        problem = unavailable_reason or (
            "could not read today's token usage from the deployment's Postgres (\"LiteLLM_SpendLogs\")")
        message = (f"{problem}, so the phase cannot prove it is inside the {settings.daily_limit:,}-token daily "
                   "limit")
        if settings.guard == TOKEN_GUARD_WARN:
            decision["allowed"] = True
            decision["warnings"].append(
                f"{message}. {TOKEN_GUARD_VARIABLE}=warn accepts that; the phase has no budget proof")
            return decision
        decision["reason"] = (
            f"refusing to start the next run: {message}. Start the deployment's postgres container, or set "
            f"{TOKEN_GUARD_VARIABLE}={TOKEN_GUARD_WARN} to measure without that proof.")
        return decision

    available = settings.daily_limit - settings.reserve - observed
    decision["available_tokens"] = available
    decision["headroom_after_run"] = available - projected_run
    if available < 0:
        position = (f"{observed:,} tokens are recorded today against the {settings.daily_limit:,}-token daily "
                    f"limit, which the day has already passed by {-available:,}")
    elif settings.reserve:
        position = (f"{observed:,} tokens are recorded today against the {settings.daily_limit:,}-token daily "
                    f"limit, leaving {available:,} after a {settings.reserve:,}-token reserve")
    else:
        position = (f"{observed:,} tokens are recorded today against the {settings.daily_limit:,}-token daily "
                    f"limit, leaving {available:,}")
    crossing = (f"The next run is projected at {projected_run:,} tokens ({projection_source}), which would take "
                f"the day to {observed + projected_run:,}")
    if projected_run > available:
        decision["reason"] = (
            f"refusing to start the next run: {position}. {crossing} and cross the budget. The phase stops here "
            f"rather than mid-series. {DAILY_TOKEN_LIMIT_VARIABLE} sets the working budget; the account-wide "
            "provider quota behind it is not adjustable today.")
        return decision
    decision["allowed"] = True
    return decision


def check_token_budget(observed: int | None, settings: TokenBudgetSettings | None = None, *,
                       projected_run: int = DEFAULT_RUN_TOKENS,
                       projection_source: str = "the conservative default",
                       completed_runs: int = 0, unavailable_reason: str = "") -> dict:
    """evaluate_token_budget, raising the refusal so a caller cannot ignore a false answer."""
    decision = evaluate_token_budget(
        observed, settings, projected_run=projected_run, projection_source=projection_source,
        completed_runs=completed_runs, unavailable_reason=unavailable_reason)
    if not decision["allowed"]:
        raise TokenBudgetRefused(decision["reason"])
    return decision


def load_token_budget(path: Path | str) -> dict:
    """The sidecar beside the runs, or an empty record when it is absent or unreadable."""
    try:
        document = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return document if isinstance(document, dict) else {}


def write_token_budget(path: Path | str, document: Mapping[str, Any]) -> None:
    Path(path).write_text(json.dumps(dict(document), indent=2) + "\n", encoding="utf-8")


def _add_warning(warnings: list[str], message: str) -> None:
    if message not in warnings:
        warnings.append(message)


def token_budget_step(path: Path | str, *, stage: str, run_number: int = 0,
                      compose_args: list[str] | tuple[str, ...] = (),
                      settings: TokenBudgetSettings | None = None,
                      now: datetime | None = None,
                      query: Callable[..., int | None] | None = None) -> dict:
    """One checkpoint of the budget, written to the sidecar beside the runs.

    The start stage checks before the phase. The before stage checks before a run and records the tokens it
    starts from. The after stage records what the run cost and never refuses, because the run it describes has
    already finished. A refusal raises TokenBudgetRefused after the sidecar is written, so the record says why
    the phase stopped and the panel can read the observation that stopped it.
    """
    if stage not in ("start", "before", "after"):
        raise ValueError(f"unknown token-budget stage {stage!r}")
    settings = settings or token_settings_from_env(os.environ)
    moment = now or datetime.now(timezone.utc)
    day = utc_day(moment)
    query = query or compose_postgres_tokens

    document = load_token_budget(path)
    if document.get("day") != day:
        # A phase that crosses UTC midnight starts a new day's budget. The finished day stays in the file, and the
        # measured run costs carry over because they describe the phase, not the date. The day boundary itself is
        # the quota's: the provider resets per UTC day, so the guard does too.
        previous = [record for record in document.get("previous_days") or [] if isinstance(record, dict)]
        if document:
            previous.append({key: document.get(key) for key in
                             ("day", "start_tokens", "observed_tokens", "available_tokens", "limit", "stopped")})
        document = {"previous_days": previous, "runs": list(document.get("runs") or [])}
    runs = [dict(record) for record in document.get("runs") or [] if isinstance(record, dict)]
    checkpoints = [dict(record) for record in document.get("checkpoints") or [] if isinstance(record, dict)]
    warnings = [str(warning) for warning in document.get("warnings") or []]

    observed: int | None = None
    problem = ""
    try:
        observed = query(list(compose_args), day)
    except ValueError as exc:
        problem = str(exc)
    if observed is None and not problem:
        problem = "could not read today's token usage from the deployment's Postgres (\"LiteLLM_SpendLogs\")"

    document.update({
        "schema": 1,
        "day": day,
        "started_at": document.get("started_at") or utc_stamp(moment),
        "updated_at": utc_stamp(moment),
        "limit": settings.daily_limit,
        "reserve": settings.reserve,
        "guard": settings.guard,
        "default_run_tokens": DEFAULT_RUN_TOKENS,
    })

    if stage == "after":
        entry = next((record for record in runs if record.get("run") == run_number), None)
        if entry is None:
            entry = {"run": run_number}
            runs.append(entry)
        if observed is None:
            _add_warning(warnings, f"run {run_number}: {problem}")
        else:
            entry["tokens_after"] = observed
            entry["recorded_at"] = utc_stamp(moment)
            document["observed_tokens"] = observed
            before = entry.get("tokens_before")
            if isinstance(before, int):
                entry["tokens_consumed"] = observed - before
                if entry["tokens_consumed"] <= 0:
                    _add_warning(warnings, f"run {run_number}: the spend total did not grow during the run, "
                                           "so its cost cannot be measured and the default is used for the next "
                                           "projection")
                    entry["tokens_consumed"] = None
            if observed > settings.daily_limit - settings.reserve:
                _add_warning(warnings, f"the day is at {observed:,} tokens, at or above the "
                                       f"{settings.daily_limit:,}-token budget; no further run will start")
    else:
        if stage == "start" and observed is not None and "start_tokens" not in document:
            document["start_tokens"] = observed
        projection, source = run_cost_projection(runs)
        completed = sum(1 for record in runs if isinstance(record.get("tokens_consumed"), int))
        decision = evaluate_token_budget(
            observed, settings, projected_run=projection, projection_source=source,
            completed_runs=completed, unavailable_reason=problem)
        checkpoints.append({
            "at": utc_stamp(moment),
            "stage": stage,
            "run": run_number or None,
            "observed_tokens": observed,
            "allowed": decision["allowed"],
            "projected_run_tokens": projection,
            "projection_source": source,
        })
        document["checkpoints"] = checkpoints
        document["runs"] = runs
        document["warnings"] = warnings
        if observed is not None:
            document["observed_tokens"] = observed
        document["available_tokens"] = decision["available_tokens"]
        document["projected_run_tokens"] = projection
        document["projection_source"] = source
        document["completed_runs"] = completed
        if stage == "before" and observed is not None:
            entry = next((record for record in runs if record.get("run") == run_number), None)
            if entry is None:
                entry = {"run": run_number, "tokens_before": observed, "started_at": utc_stamp(moment)}
                runs.append(entry)
            else:
                entry["tokens_before"] = observed
                entry.setdefault("started_at", utc_stamp(moment))
        for warning in decision["warnings"]:
            _add_warning(warnings, warning)
        if not decision["allowed"]:
            document["stopped"] = {"at": utc_stamp(moment), "stage": stage, "run": run_number or None,
                                   "reason": decision["reason"]}
            document["runs"] = runs
            document["warnings"] = warnings
            write_token_budget(path, document)
            raise TokenBudgetRefused(decision["reason"])

    document["runs"] = runs
    document["warnings"] = warnings
    write_token_budget(path, document)
    return document


def token_budget_summary(document: Mapping[str, Any], *, stage: str, run_number: int = 0) -> str:
    """The one line the operator sees at each checkpoint."""
    observed = document.get("observed_tokens")
    limit = document.get("limit") or DEFAULT_DAILY_TOKEN_LIMIT
    if stage == "after":
        entry = next((record for record in document.get("runs") or [] if record.get("run") == run_number), {})
        consumed = entry.get("tokens_consumed")
        spent = f"{consumed:,} tokens" if isinstance(consumed, int) else "a cost that cannot be measured"
        return (f"run {run_number}: {spent}; {format_tokens(observed)} of {limit:,} tokens recorded today")
    left = document.get("available_tokens")
    left_text = f"{left:,} left" if isinstance(left, int) else "headroom unknown"
    projected = document.get("projected_run_tokens") or DEFAULT_RUN_TOKENS
    return (f"token budget: {format_tokens(observed)} of {limit:,} tokens recorded today; run "
            f"{run_number or 1} projected at {int(projected):,} "
            f"({document.get('projection_source')}); {left_text}")


def oi_environment(environ: Mapping[str, str]) -> dict[str, str]:
    """The `OI_*` variables that describe the answer path, without the runner's own plumbing or credentials."""
    selected = {}
    for key, value in sorted(environ.items()):
        if not key.startswith("OI_"):
            continue
        if key.startswith(NON_ANSWER_VARIABLES):
            continue
        if any(marker in key.upper() for marker in SECRET_MARKERS):
            continue
        selected[key] = value
    return selected


def git_commit(root: Path | str) -> str | None:
    """The commit the checkout is on, or None. `safe.directory` is set because the eval image's uid is not the
    checkout's owner, and a dubious-ownership error would otherwise turn a recorded commit into a blank."""
    command = ["git", "-c", "safe.directory=*", "-C", str(root), "rev-parse", "HEAD"]
    try:
        completed = subprocess.run(command, capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.SubprocessError):
        return None
    if completed.returncode != 0:
        return None
    return completed.stdout.strip() or None


def host_name() -> str:
    try:
        return socket.gethostname()
    except OSError:
        return "unknown"


def instance_id(environ: Mapping[str, str] | None = None,
                paths: tuple[Path | str, ...] | None = None) -> str | None:
    """The box's instance id when one is readable. `OI_INSTANCE_ID` wins, so a deployment can name it without
    cloud-init, then the file cloud-init writes on EC2. No metadata call: this must not wait on a network."""
    environ = os.environ if environ is None else environ
    named = (environ.get("OI_INSTANCE_ID") or "").strip()
    if named:
        return named
    for path in (paths if paths is not None else (CLOUD_INSTANCE_ID,)):
        try:
            value = Path(path).read_text(encoding="utf-8").strip()
        except OSError:
            continue
        if value:
            return value
    return None


def build_context(*, timestamp: str | None = None, git_commit: str | None = None,
                  oi_env: Mapping[str, str] | None = None,
                  images: Mapping[str, Any] | None = None,
                  load_average: float | None = None, mem_available_mb: int | None = None,
                  hostname: str | None = None, instance_id: str | None = None,
                  gates: GateSettings | None = None, oi_env_source: str | None = None) -> dict:
    """The run context. Every required key is present even when its value is unknown, so a missing fact reads as
    null rather than as a field the reader has to notice is absent."""
    return {
        "timestamp": timestamp or datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "git_commit": git_commit,
        "oi_env": dict(sorted((oi_env or {}).items())),
        "oi_env_source": oi_env_source,
        "images": dict(images or {}),
        "load_average": load_average,
        "mem_available_mb": mem_available_mb,
        "hostname": hostname if hostname is not None else host_name(),
        "instance_id": instance_id,
        "gates": ({"max_load": gates.max_load, "min_available_mb": gates.min_available_mb}
                  if gates is not None else None),
    }


class SuiteLock:
    """A held advisory lock. Releasing twice is harmless; the connection close would release it anyway."""

    def __init__(self, connection: Any, key: int = ADVISORY_LOCK_KEY):
        self._connection = connection
        self._key = key
        self._released = False

    def release(self) -> None:
        if self._released:
            return
        self._released = True
        try:
            with self._connection.cursor() as cursor:
                cursor.execute("select pg_advisory_unlock(%s)", (self._key,))
            self._connection.commit()
        except Exception:  # noqa: BLE001 - closing the session is the backstop that actually releases it
            pass
        finally:
            self._connection.close()


@contextlib.contextmanager
def hold_advisory_lock(connect: Callable[[], Any],
                       key: int = ADVISORY_LOCK_KEY) -> Iterator[SuiteLock]:
    """Take the database lock for the duration of the block, or refuse with a sentence a person can act on.

    `pg_try_advisory_lock` is session-scoped, so the connection stays open for the whole run and the lock goes
    with it however the run ends. That property is why this is a context manager: a caller cannot forget to
    release it on the exception path.
    """
    try:
        connection = connect()
    except Exception as exc:  # noqa: BLE001 - a driver error is a refusal, not a traceback for the operator
        detail = " ".join(str(exc).split()) or type(exc).__name__
        raise SuiteRefused(
            f"could not reach Postgres to take the suite lock (key {key}): {detail}") from None
    try:
        with connection.cursor() as cursor:
            cursor.execute("select pg_try_advisory_lock(%s)", (key,))
            row = cursor.fetchone()
        connection.commit()
    except Exception as exc:  # noqa: BLE001
        connection.close()
        detail = " ".join(str(exc).split()) or type(exc).__name__
        raise SuiteRefused(f"could not take the suite lock (key {key}): {detail}") from None
    if not (row and row[0]):
        connection.close()
        raise SuiteLockBusy(
            f"another suite run holds the Postgres advisory lock (key {key}). Two runs against one deployment "
            "make both measurements unusable; wait for the other run to finish, then start again."
        )
    lock = SuiteLock(connection, key)
    try:
        yield lock
    finally:
        lock.release()


def run_command(command: list[str], timeout: float = 20.0) -> str | None:
    """Best-effort command output. Absence is recorded as absent, never raised: the context must not stop a run
    because Docker was slow to answer a question about itself."""
    try:
        completed = subprocess.run(command, capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.SubprocessError):
        return None
    return completed.stdout if completed.returncode == 0 else None


def compose_service_images(config_json: str) -> dict[str, str]:
    """The `api` and `eval` image references from `docker compose config --format json`."""
    try:
        document = json.loads(config_json)
    except ValueError:
        return {}
    services = document.get("services") or {}
    resolved = {}
    for name in ("api", "eval"):
        image = (services.get(name) or {}).get("image")
        if image:
            resolved[name] = image
    return resolved


def image_record(reference: str | None) -> dict:
    """An image's name, content id and repo digests, as far as Docker will say."""
    record: dict[str, Any] = {"name": reference, "id": None, "digests": []}
    if not reference:
        return record
    identity = run_command(["docker", "image", "inspect", reference, "--format",
                            '{{.Id}}|{{join .RepoDigests ","}}'])
    if identity is None:
        return record
    image_id, _, digest_text = identity.strip().partition("|")
    if image_id:
        record["id"] = image_id
    if digest_text:
        record["digests"] = [digest for digest in digest_text.split(",") if digest]
    return record


def compose_oi_environment(compose_args: list[str]) -> tuple[dict[str, str], str]:
    """The `OI_*` variables the running API was started with. The API container is where the answer path's flags
    actually live; the host shell usually has none of them. Falling back to the process environment is recorded
    as such, so a context is never silently thinner than it looks."""
    output = run_command(["docker", "compose", *compose_args, "exec", "-T", "api", "env"])
    if output is None:
        return oi_environment(os.environ), "process environment"
    values = {}
    for line in output.splitlines():
        key, separator, value = line.partition("=")
        if separator and key.startswith("OI_"):
            values[key] = value
    return oi_environment(values), "api container"


def context_for_runner(root: Path | str = Path("/w"), environ: Mapping[str, str] | None = None) -> dict:
    """The context a run records. When the host script wrote one, that record is reused verbatim, so the run's
    JSON and `run-context.json` cannot describe different boxes. A direct `docker run` builds what it can see."""
    environ = dict(os.environ if environ is None else environ)
    reference = (environ.get("OI_RUN_CONTEXT") or "").strip()
    if reference:
        try:
            return json.loads(Path(reference).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            pass  # a bad reference must not lose the run; the local facts below are the fallback
    return build_context(
        git_commit=git_commit(root),
        oi_env=oi_environment(environ),
        oi_env_source="runner process environment",
        images={},
        load_average=read_load_average(),
        mem_available_mb=read_mem_available_mb(),
        hostname=host_name(),
        instance_id=instance_id(environ),
    )


def preflight(root: Path | str, out: Path | str, compose_file: str = "", env_file: str = "",
              loadavg_path: Path | str = PROC_LOADAVG, meminfo_path: Path | str = PROC_MEMINFO,
              instance_id_path: Path | str | None = None) -> dict:
    """Gate the box, then write `run-context.json` into the output directory.

    The gate runs before the Docker queries so a busy box is refused in milliseconds, and the context is written
    only for a run that was admitted. A context that cannot be written is a refusal: the rule is that a run
    without a context is a smoke test, and a smoke test is not what this script exists to produce.
    """
    settings = settings_from_env(os.environ)
    observed = check_gate(read_load_average(loadavg_path), read_mem_available_mb(meminfo_path), settings)

    compose_args: list[str] = []
    if compose_file:
        compose_args += ["-f", compose_file]
    if env_file:
        compose_args += ["--env-file", env_file]

    images: dict[str, Any] = {}
    if compose_args:
        config_text = run_command(["docker", "compose", *compose_args, "config", "--format", "json"])
        if config_text:
            images = {name: image_record(reference)
                      for name, reference in compose_service_images(config_text).items()}

    if compose_args:
        oi_env, source = compose_oi_environment(compose_args)
    else:
        oi_env, source = oi_environment(os.environ), "process environment"

    context = build_context(
        git_commit=git_commit(root),
        oi_env=oi_env,
        oi_env_source=source,
        images=images,
        load_average=observed["load_average"],
        mem_available_mb=observed["mem_available_mb"],
        hostname=host_name(),
        instance_id=instance_id(paths=((instance_id_path,) if instance_id_path else None)),
        gates=settings,
    )
    Path(out).write_text(json.dumps(context, indent=2) + "\n", encoding="utf-8")
    return context


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    commands = parser.add_subparsers(dest="command", required=True)

    gate = commands.add_parser("gate", help="refuse when the box is too busy for a serial run")
    gate.add_argument("--loadavg", default=str(PROC_LOADAVG))
    gate.add_argument("--meminfo", default=str(PROC_MEMINFO))

    preflight_parser = commands.add_parser(
        "preflight", help="gate the box and write the run context the suite records")
    preflight_parser.add_argument("--root", default=".")
    preflight_parser.add_argument("--out", required=True)
    preflight_parser.add_argument("--compose-file", default="")
    preflight_parser.add_argument("--env-file", default="")
    preflight_parser.add_argument("--loadavg", default=str(PROC_LOADAVG))
    preflight_parser.add_argument("--meminfo", default=str(PROC_MEMINFO))
    preflight_parser.add_argument("--instance-id-file", default="")

    fallback_parser = commands.add_parser(
        "fallback", help="refuse a run whose model calls fell back to the recovery model")
    fallback_parser.add_argument("--since", required=True, help="UTC run start, 2026-10-21T20:00:00Z")
    fallback_parser.add_argument("--compose-file", default="")
    fallback_parser.add_argument("--env-file", default="")

    budget_parser = commands.add_parser(
        "budget", help="check and record the day's provider-token budget")
    budget_parser.add_argument("--out", required=True, help="token-budget.json beside the runs")
    budget_parser.add_argument("--compose-file", default="")
    budget_parser.add_argument("--env-file", default="")
    budget_parser.add_argument("--stage", required=True, choices=("start", "before", "after"))
    budget_parser.add_argument("--run", type=int, default=0, dest="run_number")

    args = parser.parse_args(argv)
    try:
        if args.command == "gate":
            settings = settings_from_env(os.environ)
            observed = check_gate(read_load_average(args.loadavg),
                                  read_mem_available_mb(args.meminfo), settings)
            print(json.dumps(observed, indent=2))
            return 0
        if args.command == "fallback":
            compose_args = []
            if args.compose_file:
                compose_args += ["-f", args.compose_file]
            if args.env_file:
                compose_args += ["--env-file", args.env_file]
            count = compose_postgres_fallbacks(compose_args, args.since)
            if count is None:
                print("refusing to accept the run: could not read fallback calls from the deployment's Postgres",
                      file=sys.stderr)
                return 2
            if count:
                print(f"refusing to accept the run: {count} model call(s) fell back to another model since "
                      f"{args.since}; this measures the recovery model, not the configured one", file=sys.stderr)
                return 6
            print(f"no fallback model calls since {args.since}")
            return 0
        if args.command == "budget":
            compose_args: list[str] = []
            if args.compose_file:
                compose_args += ["-f", args.compose_file]
            if args.env_file:
                compose_args += ["--env-file", args.env_file]
            document = token_budget_step(args.out, stage=args.stage, run_number=args.run_number,
                                         compose_args=compose_args)
            print(token_budget_summary(document, stage=args.stage, run_number=args.run_number))
            return 0
        context = preflight(
            root=args.root,
            out=args.out,
            compose_file=args.compose_file,
            env_file=args.env_file,
            loadavg_path=args.loadavg,
            meminfo_path=args.meminfo,
            instance_id_path=args.instance_id_file or None,
        )
        print(f"run context: {args.out}")
        print(json.dumps({"load_average": context["load_average"],
                          "mem_available_mb": context["mem_available_mb"],
                          "git_commit": context["git_commit"],
                          "images": sorted(context["images"])}, indent=2))
        return 0
    except GateRefused as exc:
        print(f"refusing to start: {exc}", file=sys.stderr)
        return LOCK_EXIT_CODE
    except TokenBudgetRefused as exc:
        print(str(exc), file=sys.stderr)
        return TOKEN_EXIT_CODE
    except ValueError as exc:
        print(f"refusing to start: {exc}", file=sys.stderr)
        return 2
    except OSError as exc:
        print(f"refusing to start: could not write the run context or the token budget: {exc}",
              file=sys.stderr)
        return LOCK_EXIT_CODE


if __name__ == "__main__":
    raise SystemExit(main())
