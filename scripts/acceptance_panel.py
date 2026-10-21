#!/usr/bin/env python3
"""Turn a set of suite runs into the acceptance panel's numbers.

**The panel is what a client's decision rests on: answers, wait and cost, measured together.** The suite
answers measure correctness; this script reads their records for the accuracy figures, pools the per-answer
latency the runner captured, and joins the same window against the gateway's spend records for the cost of the
answers the client actually asked for. It also reports the phase's token-budget sidecar when the suite script
left one beside the runs, so the provider tokens the phase spent and any budget stop are read with the scores.
One run is not a panel: point it at the five runs of a configuration.

Usage, on the box, from the deployment root, inside a Python that can reach Postgres (the eval image does):

    python3 scripts/acceptance_panel.py /tmp/oic-attribution-2/run-1.json ... --out /tmp/panel.json

DATABASE_URL is read from the environment, or assembled from POSTGRES_* like the suite runner does. The spend
window is taken from the runs themselves: the first run's start minus its duration to the last run's end, so the
join cannot silently include a different afternoon.

The cost figure is **cost per answered question, tool calls included**, because a tool loop makes several
provider calls for one question and a per-call number would flatter the deployment. It is not a per-token
price; it is what this configuration spent to answer, which is the number a client can compare against an
alternative.

**The contexts are checked, and the panel warns instead of refusing.** A run file from before 2026-10-20 has no
context, and a run taken under contention records a load average above the limit it was admitted under. Both are
printed as warnings and kept in the panel under `warnings`. The numbers are still computed, because the older
files are the evidence behind the reviews; the warning says how much of it was proven serial.
"""
from __future__ import annotations

import argparse
import json
import os
import statistics
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

# The load limit is the one the run was admitted under, so it comes from the same module the script and the runner
# use. Resolving it from this file's directory keeps `python3 scripts/acceptance_panel.py` and a test import on one
# implementation.
sys.path.insert(0, str(Path(__file__).resolve().parent))
import suite_hygiene  # noqa: E402


def database_url() -> str:
    url = (os.environ.get("DATABASE_URL") or "").strip()
    if url:
        return url
    user = os.environ.get("POSTGRES_USER", "")
    password = os.environ.get("POSTGRES_PASSWORD", "")
    database = os.environ.get("POSTGRES_DB", "")
    return f"postgresql://{user}:{password}@postgres:5432/{database}" if user else ""


def load_runs(paths: list[Path]) -> list[dict]:
    return [json.loads(path.read_text(encoding="utf-8")) for path in paths]


def context_warnings(runs: list[dict], names: list[str] | None = None,
                     settings: suite_hygiene.GateSettings | None = None) -> list[str]:
    """What the runs' contexts say about how much their numbers can be trusted.

    **A warning, never a failure.** Run files written before 2026-10-20 have no `context` key. They are still the
    evidence behind every number in the reviews, and refusing them would throw away the record instead of
    annotating it. The warning is the point: a run without a context is a smoke test, and a run that started above
    the load limit was measured under contention, so whoever quotes the number knows which one they have.

    The limit is the one recorded in the run's context when it is there, because that is what the run was admitted
    under, and OI_SUITE_MAX_LOAD otherwise.
    """
    settings = settings or suite_hygiene.settings_from_env(os.environ)
    warnings = []
    for index, run in enumerate(runs):
        name = names[index] if names and index < len(names) else f"run {index + 1}"
        context = run.get("context")
        if not context:
            warnings.append(f"{name}: no run context (an older run file); serial measurement cannot be verified")
            continue
        gates = context.get("gates") or {}
        max_load = gates.get("max_load") or settings.max_load
        load = context.get("load_average")
        if load is None:
            warnings.append(f"{name}: the run context has no load average; contention cannot be checked")
        elif isinstance(load, (int, float)) and load > max_load:
            warnings.append(f"{name}: started at load average {load:.2f}, above the {max_load:.2f} limit; "
                            "the run may have contended with other work")
    return warnings


def score_totals(runs: list[dict]) -> dict:
    """Accuracy, about the answers the runs actually scored."""
    scored = passed = degraded = invalid = 0
    per_run = []
    for run in runs:
        run_scored = run_passed = 0
        for record in run.get("results") or []:
            score = record.get("score")
            if not score:
                continue
            run_scored += 1
            if record.get("question_valid") is False:
                invalid += 1
            if score.get("pass"):
                run_passed += 1
            answer = record.get("answer") or {}
            if answer.get("kind") == "degraded":
                degraded += 1
        scored += run_scored
        passed += run_passed
        per_run.append(run_passed)
    return {"runs": len(runs), "scored": scored, "passed": passed,
            "mean": round(statistics.mean(per_run), 2) if per_run else None,
            "low": min(per_run) if per_run else None, "high": max(per_run) if per_run else None,
            "per_run": per_run, "degraded_answers": degraded, "invalid_questions": invalid}


def latency_totals(runs: list[dict]) -> dict:
    values = sorted(record["answer"]["elapsed_ms"]
                    for run in runs for record in run.get("results") or []
                    if (record.get("answer") or {}).get("elapsed_ms") is not None)

    def percentile(fraction: float) -> int | None:
        if not values:
            return None
        index = min(len(values) - 1, max(0, int(round(fraction * (len(values) - 1)))))
        return values[index]

    return {"answers": len(values), "p50_ms": percentile(0.50), "p95_ms": percentile(0.95),
            "max_ms": values[-1] if values else None}


def spend_window(runs: list[dict]) -> tuple[str, str]:
    """The wall-clock window the runs occupied, from their own records."""
    starts, ends = [], []
    for run in runs:
        ran_at = datetime.strptime(run["ran_at"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
        starts.append(ran_at - timedelta(seconds=float(run.get("duration_seconds") or 0)))
        ends.append(ran_at)
    return min(starts).isoformat(), max(ends).isoformat()


def spend_totals(start: str, end: str) -> dict:
    """What the gateway recorded spending while the runs were in progress."""
    try:
        import psycopg
    except Exception:  # noqa: BLE001 - no driver, no cost figure; say so rather than guess
        return {"available": False, "reason": "psycopg is not installed in this interpreter"}
    url = database_url()
    if not url:
        return {"available": False, "reason": "no DATABASE_URL and no POSTGRES_* variables"}
    try:
        connection = psycopg.connect(url, connect_timeout=10)
    except Exception as exc:  # noqa: BLE001
        return {"available": False, "reason": f"could not reach the gateway's database: {exc}"}
    try:
        with connection.cursor() as cursor:
            cursor.execute(
                'select coalesce(sum(spend), 0), count(*), count(*) filter (where status != %s) '
                'from "LiteLLM_SpendLogs" where "startTime" >= %s and "startTime" <= %s',
                ("success", start, end))
            total, calls, failed = cursor.fetchone()
            cursor.execute(
                'select model, coalesce(sum(spend), 0), count(*) from "LiteLLM_SpendLogs" '
                'where "startTime" >= %s and "startTime" <= %s group by model order by 2 desc',
                (start, end))
            by_model = [{"model": row[0], "spend": float(row[1]), "calls": int(row[2])}
                        for row in cursor.fetchall()]
    finally:
        connection.close()
    return {"available": True, "window": [start, end], "spend": float(total), "calls": int(calls),
            "failed_calls": int(failed), "by_model": by_model}


def token_budget_record(runs: list[Path]) -> dict:
    """The phase's token-budget sidecar, when the script wrote one beside the runs.

    The suite writes token-budget.json into the output directory before the first run and updates it after every
    run. The panel reports it as it stands, including a stop, so the accuracy numbers and the token cost of the
    phase are read from the same directory. Older run directories have no sidecar and the panel says so.
    """
    for run in runs:
        candidate = Path(run).parent / suite_hygiene.TOKEN_BUDGET_FILE
        if candidate.is_file():
            document = suite_hygiene.load_token_budget(candidate)
            if document:
                return {"available": True, "path": str(candidate), **document}
    return {"available": False, "reason": f"no {suite_hygiene.TOKEN_BUDGET_FILE} beside the runs"}


def build(runs: list[dict], names: list[str] | None = None, token_budget: dict | None = None) -> dict:
    accuracy = score_totals(runs)
    latency = latency_totals(runs)
    start, end = spend_window(runs)
    spend = spend_totals(start, end)
    panel = {"accuracy": accuracy, "latency": latency, "spend": spend,
             "token_budget": token_budget or {"available": False,
                                              "reason": f"no {suite_hygiene.TOKEN_BUDGET_FILE} beside the runs"},
             "warnings": context_warnings(runs, names)}
    if spend.get("available") and accuracy["scored"]:
        panel["cost_per_answer"] = round(spend["spend"] / accuracy["scored"], 5)
        panel["cost_per_pass"] = round(spend["spend"] / accuracy["passed"], 5) if accuracy["passed"] else None
        panel["calls_per_answer"] = round(spend["calls"] / accuracy["scored"], 2)
    return panel


def main() -> int:
    parser = argparse.ArgumentParser(description="Acceptance-panel numbers from suite runs.")
    parser.add_argument("runs", nargs="+", type=Path)
    parser.add_argument("--out", default="")
    args = parser.parse_args()
    panel = build(load_runs(args.runs), names=[path.name for path in args.runs],
                  token_budget=token_budget_record(args.runs))
    for warning in panel["warnings"]:
        print(f"WARNING: {warning}", file=sys.stderr)
    if panel["warnings"]:
        print("WARNING: not every run was proven serial; the panel's numbers carry that caveat.",
              file=sys.stderr)
    text = json.dumps(panel, indent=2)
    print(text)
    if args.out:
        Path(args.out).write_text(text + "\n", encoding="utf-8")
        print(f"written: {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
