#!/usr/bin/env bash
# Run an acceptance probe with the mounts and variables it needs, without asking anyone to remember them.
#
# **Why this exists.** The probes are documented as a `docker run` line with an entrypoint override, and that
# line is wrong in a way that only shows up in one right: the session image has no audit tool at
# `/app/audit.py`, because the compose service mounts it — so every probe ran, and `access_probe`'s REFUSED
# right reported "the refusal could not be recorded, so it is not a control". It was reading the deployment's
# own refusal path correctly and the runner was at fault.
#
# The C4 panel's executor was the third reviewer to hit a documented command that could not work as printed. A
# probe runner that takes the probe's name is shorter than the line it replaces, which is the only kind of fix
# that survives.
set -euo pipefail

PROBE="${1:?usage: scripts/probe.sh <probe.py> [args...]}"
shift
COMPOSE="deploy/office/compose/docker-compose.yml"
ENV_FILE="rendered/env"
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

if [ ! -f "$ROOT/$ENV_FILE" ]; then
  echo "no $ENV_FILE: run this from the deployment root, the way every other command in the runbook does" >&2
  exit 2
fi

case "$PROBE" in
  /*) PROBE_PATH="$PROBE" ;;
  *)  PROBE_PATH="$ROOT/tests/fixtures/$PROBE" ;;
esac

if [ ! -f "$PROBE_PATH" ]; then
  echo "no such probe: $PROBE_PATH" >&2
  exit 2
fi

# The image the probe needs. `access_probe` reads the registry and the chain, so it needs both drivers and the
# audit tool; the rest need neither.
IMAGE="${OI_PROBE_IMAGE:-oi-core/session:local}"

cd "$ROOT"

# **The probe itself has to be inside the container, and the first version was not.** It named the host path
# `/opt/oi-core/tests/fixtures/access_probe.py`, which the container cannot see: the service mounts `rendered`,
# `db/migrations` and the audit tool, and nothing else.
#
# **The whole checkout goes in at `/w`, read-only, because that is the path the probes already look for.** They
# insert `/w/services/retrieval` and `/w/services/ingestion` into `sys.path` before importing, so a runner that
# mounted only the fixtures answered `ModuleNotFoundError: No module named 'access'`. Mounting the repository the
# probes were written against costs one line and means the fixtures do not have to be changed to suit the
# runner, which is the wrong direction for a change to travel.
RELATIVE="${PROBE_PATH#"$ROOT"/}"

exec docker compose -f "$COMPOSE" --env-file "$ENV_FILE" --profile tools run --rm \
  --no-deps --entrypoint python \
  -v "$ROOT:/w:ro" \
  session "/w/$RELATIVE" "$@"
