#!/usr/bin/env bash
# Run the forty-question answer suite N times against the running deployment.
#
# **Why a script rather than a command in a document.** The evening of 2026-10-18 produced a measurement that could
# not be reproduced from the repository alone, because the runner was staged in /tmp. A measurement you cannot
# regenerate is a claim, not evidence. This runs the committed runner (tests/fixtures/data_suite_run.py) and leaves
# one JSON per run in the output directory.
#
# Usage, from the deployment root on the box:
#   scripts/run_answer_suite.sh [runs] [output-directory]
#
# Defaults: five runs (the minimum for a configuration claim), output under /tmp.
set -euo pipefail

ROOT="${OI_ROOT:-/opt/oi-core}"
RUNS="${1:-5}"
OUT="${2:-/tmp/oic-suite-$(date -u +%Y%m%dT%H%M%SZ)}"
cd "$ROOT"

if [ ! -f rendered/env ]; then
  echo "no rendered environment at $ROOT/rendered/env; render the deployment first" >&2
  exit 2
fi
mkdir -p "$OUT"
echo "running $RUNS suite run(s) against $(git -C "$ROOT" log --oneline -1 2>/dev/null || echo 'unknown commit')"
echo "output: $OUT"

# **The output directory has to be writable by the container's user, and the image's user is not root.**
# The eval image runs as uid 10003; a directory this script creates belongs to the operator, so the runner's
# final write failed with PermissionError after a full suite had already run, and the run was lost. Running the
# container as the invoking user is the same choice the probes in the runbook make, and it keeps the output the
# operator's own file. Found 2026-10-19 while re-running the five-run comparison from a fresh directory.
for n in $(seq 1 "$RUNS"); do
  docker compose -f deploy/office/compose/docker-compose.yml --env-file rendered/env run -T --rm --no-deps \
    --user "$(id -u):$(id -g)" \
    --entrypoint python \
    -v "$ROOT":/w:ro -v "$OUT":/out \
    -e OI_API_URL=http://api:8088 -e ACCESS_REGISTRY=/w/rendered/access.json \
    eval /w/tests/fixtures/data_suite_run.py --json-out "/out/run-$n.json" < /dev/null >/dev/null
  echo "run $n complete: $OUT/run-$n.json"
done
echo "five runs are the minimum before a configuration claim; read the range, not one number."
