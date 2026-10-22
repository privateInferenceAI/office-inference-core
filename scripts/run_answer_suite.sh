#!/usr/bin/env bash
# Run the forty-question answer suite N times against the running deployment.
#
# **Why a script rather than a command in a document.** The evening of 2026-10-18 produced a measurement that could
# not be reproduced from the repository alone, because the runner was staged in /tmp. A measurement you cannot
# regenerate is a claim, not evidence. This runs the committed runner (tests/fixtures/data_suite_run.py) and leaves
# one JSON per run in the output directory.
#
# **One suite at a time per box.** On 2026-10-20 the same configuration scored 53 of 53 on five runs taken while the
# box was otherwise idle, then 48 to 51 of 53 in a window when a second suite was running against the same
# deployment, with N06 and N07 failing in the lower runs. Nothing about the configuration changed. This script
# therefore holds a host lock for its whole life and refuses to start while another suite holds it; the runner
# takes a Postgres advisory lock as well, so a direct docker run cannot slip past the host lock. It also refuses to
# start when the one-minute load or the available memory says the box is busy. The rule and its evidence are in
# docs/decisions/0005-measurement-hygiene.md.
#
# Usage, from the deployment root on the box:
#   scripts/run_answer_suite.sh [runs] [output-directory]
#
# Defaults: five runs (the minimum for a configuration claim), output under /tmp. Environment: OI_ROOT,
# OI_SUITE_LOCK, OI_SUITE_MAX_LOAD, OI_SUITE_MIN_AVAILABLE_MB, OI_SUITE_DAILY_TOKEN_LIMIT,
# OI_SUITE_TOKEN_RESERVE and OI_SUITE_TOKEN_GUARD (see scripts/suite_hygiene.py).
set -euo pipefail

ROOT="${OI_ROOT:-/opt/oi-core}"
RUNS="${1:-5}"
OUT="${2:-/tmp/oic-suite-$(date -u +%Y%m%dT%H%M%SZ)}"
LOCK="${OI_SUITE_LOCK:-/tmp/oi-suite.lock}"

# **The host lock is the first thing this script does.** Each invocation has to take it before it reads the
# deployment or starts a container, or two invocations can both get as far as running and only discover each other
# in the results. Descriptor 9 is held for the life of the script and flock releases it on any exit, including a
# Ctrl-C, so there is no cleanup path to forget. The file is opened for append rather than truncated: truncating it
# here would erase the holder's pid before the check below could report it.
exec 9>>"$LOCK"
if ! flock -n 9; then
  holder="$(tail -n 1 "$LOCK" 2>/dev/null || true)"
  echo "another answer suite holds $LOCK${holder:+ (pid $holder)}; refusing to start" >&2
  echo "two suites against one deployment make both measurements unusable; wait for it to finish" >&2
  exit 3
fi
printf '%s\n' "$$" >&9

cd "$ROOT"

if [ ! -f rendered/env ]; then
  echo "no rendered environment at $ROOT/rendered/env; render the deployment first" >&2
  exit 2
fi
mkdir -p "$OUT"

# **The gate and the context record run before the first container.** A busy box is refused in milliseconds rather
# than after a suite has contended for half an hour, and the record is written only for a run that was admitted.
# The checks live in scripts/suite_hygiene.py because they are tested without a database and shared with the
# runner; this script would otherwise carry a second copy of the thresholds.
python3 "$ROOT/scripts/suite_hygiene.py" preflight \
  --root "$ROOT" \
  --out "$OUT/run-context.json" \
  --compose-file deploy/office/compose/docker-compose.yml \
  --env-file rendered/env

# **The day's provider tokens are an admission condition too.** Bedrock's 150M-token daily quota is account-wide
# and not adjustable, so a phase that starts near it cannot finish a series. This check reads today's total from
# LiteLLM_SpendLogs through the deployment's own postgres container, and stops the phase with exit 5 when the next
# run is projected to cross the budget. The start and every run are written to token-budget.json beside the runs,
# so the panel can read what the phase cost. The projection and the refusal live in scripts/suite_hygiene.py,
# where the tests exercise them without a database.
BUDGET="$OUT/token-budget.json"
token_budget() {
  python3 "$ROOT/scripts/suite_hygiene.py" budget \
    --out "$BUDGET" \
    --compose-file deploy/office/compose/docker-compose.yml \
    --env-file rendered/env "$@"
}
token_budget --stage start

echo "running $RUNS suite run(s) against $(git -C "$ROOT" log --oneline -1 2>/dev/null || echo 'unknown commit')"
echo "output: $OUT"
echo "context: $OUT/run-context.json"
echo "token budget: $BUDGET"

# **The output directory has to be writable by the container's user, and the image's user is not root.**
# The eval image runs as uid 10003; a directory this script creates belongs to the operator, so the runner's
# final write failed with PermissionError after a full suite had already run, and the run was lost. Running the
# container as the invoking user is the same choice the probes in the runbook make, and it keeps the output the
# operator's own file. Found 2026-10-19 while re-running the five-run comparison from a fresh directory.
#
# OI_RUN_CONTEXT points the runner at the record written above, so the run's JSON carries the same commit, flags,
# images, load and memory rather than guessing at them from inside the container.
for n in $(seq 1 "$RUNS"); do
  # **Checked again before every run.** The average of the runs already recorded here is the projection, so a
  # series stops before the run that would cross the budget instead of after it.
  token_budget --stage before --run "$n"
  # The fallback check is per run, not per phase, because one fallback call makes that run a measurement of a
  # different model even when the score looks right. The runner returns before this line, so the run is already
  # written; a failure marks the output directory rather than deleting evidence.
  RUN_START="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
  docker compose -f deploy/office/compose/docker-compose.yml --env-file rendered/env run -T --rm --no-deps \
    --user "$(id -u):$(id -g)" \
    --entrypoint python \
    -v "$ROOT":/w:ro -v "$OUT":/out \
    -e OI_API_URL=http://api:8088 -e ACCESS_REGISTRY=/w/rendered/access.json \
    -e OI_RUN_CONTEXT=/out/run-context.json \
    eval /w/tests/fixtures/data_suite_run.py --json-out "/out/run-$n.json" < /dev/null >/dev/null
  echo "run $n complete: $OUT/run-$n.json"
  if ! python3 "$ROOT/scripts/suite_hygiene.py" fallback --since "$RUN_START" \
      --compose-file deploy/office/compose/docker-compose.yml --env-file rendered/env; then
    printf '%s\n' "run $n CONTAMINATED: fallback model calls present; not a valid measurement" \
      | tee "$OUT/CONTAMINATED"
    exit 6
  fi
  # The run has finished, so this records its cost and never refuses: a refusal here would discard a run that
  # already happened. The next loop's check is what stops the phase.
  token_budget --stage after --run "$n"
done
echo "token usage for this phase: $BUDGET"
echo "five runs are the minimum before a configuration claim; read the range, not one number."
