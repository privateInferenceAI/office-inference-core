#!/usr/bin/env bash
# Ship audit artifacts off this box, and prove they arrived.
#
# Usage: oi-core-ship.sh anchors|exports
#
# Runs on the host and uses two containers. The shared audit tool writes the artifact, because it
# owns the chain and knows how to export it. The ship image delivers it, because the audit tool is
# not allowed any cloud dependency: the office appliance runs the same tool with no bucket in sight.
#
# Nothing is installed on the host. A build whose manifest declares no offsite destination exits
# successfully without doing anything, which is a valid configuration rather than a failure.
set -euo pipefail

MODE="${1:-}"
case "$MODE" in
  anchors|exports) ;;
  *) echo "usage: $0 anchors|exports" >&2; exit 2 ;;
esac

ROOT="${OI_ROOT:-/opt/oi-core}"
COMPOSE="$ROOT/deploy/office/compose/docker-compose.yml"
ENV_FILE="$ROOT/rendered/env"
EXPORTS="$ROOT/exports"
SHIP_IMAGE="oi-core-ship:local"

cd "$ROOT"

if [ ! -f "$ENV_FILE" ]; then
  echo "no rendered env at $ENV_FILE: nothing is configured on this box yet" >&2
  exit 1
fi

set -a
# shellcheck disable=SC1090  # the path is decided above, not baked in
. "$ENV_FILE"
set +a

BUCKET="${AUDIT_SHIP_BUCKET:-}"
if [ -z "$BUCKET" ]; then
  echo "no offsite destination in the manifest: everything stays on this box"
  exit 0
fi

SHIP_LIST="${AUDIT_SHIP_ARTIFACTS:-}"
case ",$SHIP_LIST," in
  *",$MODE,"*) ;;
  *) echo "the manifest does not list '$MODE' under offsite.ship (it lists: ${SHIP_LIST:-nothing})"; exit 0 ;;
esac

REGION="${AUDIT_SHIP_REGION:-${AWS_REGION:-us-east-1}}"

# 1. Produce the artifact with the audit tool. It runs as the operator, so the files belong to the
#    person who has to hand them over.
pairs=()
case "$MODE" in
  anchors)
    docker compose -f "$COMPOSE" --env-file "$ENV_FILE" run --rm audit anchor --out /app/exports/anchors
    newest="$(find "$EXPORTS/anchors" -name 'anchor-*.json' -printf '%T@ %p\n' 2>/dev/null | sort -rn | head -1 | cut -d' ' -f2-)"
    if [ -z "$newest" ]; then
      echo "the anchor step produced no file under $EXPORTS/anchors" >&2
      exit 1
    fi
    pairs+=("$newest|anchors/$(basename "$newest")")
    ;;
  exports)
    day="$(date -u +%F)"
    # Pull the gateway's calls into the record first. The gateway writes a row as each call happens,
    # and the record only has them after a backfill, so exporting without one ships whatever the last
    # backfill happened to catch. That is how the first shipped export came to hold two rows of a
    # twelve-row day: nothing was wrong with the export, it exported a record nobody had refreshed.
    docker compose -f "$COMPOSE" --env-file "$ENV_FILE" run --rm audit backfill
    docker compose -f "$COMPOSE" --env-file "$ENV_FILE" run --rm audit export --date "$day"
    pairs+=("$EXPORTS/$day/audit-events.jsonl|exports/$day/audit-events.jsonl")
    pairs+=("$EXPORTS/$day/manifest.json|exports/$day/manifest.json")
    ;;
esac

# 2. Build the courier from its pinned Dockerfile and deliver each artifact. The upload and the
#    read-back both happen inside that container, so the comparison is between what left the
#    filesystem and what the bucket actually holds.
docker build -q -f deploy/cloud/ship/Dockerfile -t "$SHIP_IMAGE" . >/dev/null

for pair in "${pairs[@]}"; do
  local_path="${pair%%|*}"
  key="${pair##*|}"
  rel="${local_path#"$EXPORTS"/}"
  docker run --rm \
    -e AWS_REGION="$REGION" \
    -e AWS_DEFAULT_REGION="$REGION" \
    -v "$EXPORTS:/evidence:ro" \
    "$SHIP_IMAGE" "/evidence/$rel" "s3://$BUCKET/$key"
done

echo "$MODE shipped to s3://$BUCKET"
