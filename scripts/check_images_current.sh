#!/usr/bin/env bash
# Is every deployed image built from the source it claims to be?
#
# **Why this exists.** On 2026-10-16 a teardown on the reference deployment cleared every index, reported
# its stores by name, and **left all 10,033 client documents on disk.** The code that removes them had been
# in `main` since 13 October. The image had been built before it and never rebuilt, and nothing anywhere
# noticed: `git status` was clean, `git rev-list HEAD..origin/main` said zero, and every command ran.
#
# The failure is silent in a way that matters more than the individual bug. A `git pull` without a rebuild
# leaves a deployment running code nobody can point at, and the gap only surfaces when the missing behaviour
# is one somebody is relying on -- here, that a client's data is destroyed when we say it is.
#
# **A heuristic, and it says so.** It compares each image's build time against the last commit that touched
# the service's source. That catches the real case and it can be wrong in both directions: a rebuild with no
# source change is harmless and not flagged, and an image built from a dirty working tree is flagged as
# current when it is not. It is a tripwire, not a proof.
#
#     bash scripts/check_images_current.sh
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
COMPOSE="deploy/office/compose/docker-compose.yml"
ENVF="rendered/env"
[ -f "$ENVF" ] || { echo "no $ENVF: this runs on a deployment, not in a checkout" >&2; exit 2; }

echo "== where the source stands =="
printf "  HEAD        %s  %s\n" "$(git rev-parse --short HEAD)" "$(git log --format=%s -1 | cut -c1-46)"

# Each buildable service and the directory its Dockerfile builds from.
mapfile -t SERVICES < <(python3 - "$COMPOSE" <<'PY'
import re, sys
text = open(sys.argv[1]).read()
for m in re.finditer(r"^  ([a-z0-9-]+):\s*$(.*?)(?=^  [a-z0-9-]+:\s*$|\Z)", text, re.M | re.S):
    name, body = m.group(1), m.group(2)
    ctx = re.search(r"context:\s*(\S+)", body)
    if ctx:
        print(f"{name}\t{ctx.group(1).replace('../../../', '')}")
PY
)

stale=0
echo
echo "== every built image against the last commit that touched its source =="
for entry in "${SERVICES[@]}"; do
  name="${entry%%$'\t'*}"; ctx="${entry##*$'\t'}"
  # **`--profile tools` is not optional here.** Without it the tools-profile services are absent from the
  # resolved configuration, so the loop skips them -- and the image that broke was `retention`, which is one
  # of them. The first version of this script checked five services, reported all current, and said nothing
  # about the one that mattered.
  image=$(docker compose -f "$COMPOSE" --env-file "$ENVF" --profile tools config --format json 2>/dev/null \
          | python3 -c "import json,sys; print(json.load(sys.stdin)['services'].get('$name',{}).get('image',''))" 2>/dev/null)
  [ -z "$image" ] && continue
  created=$(docker image inspect "$image" --format '{{.Created}}' 2>/dev/null || echo "")
  if [ -z "$created" ]; then
    printf "  %-14s %-26s NOT BUILT\n" "$name" "$image"; stale=$((stale+1)); continue
  fi
  # The last commit touching the service's own directory.
  last=$(git log -1 --format=%cI -- "services/$name" 2>/dev/null || echo "")
  [ -z "$last" ] && last=$(git log -1 --format=%cI -- "$ctx" 2>/dev/null || echo "")
  if [ -z "$last" ]; then
    printf "  %-14s %-26s no source commit found\n" "$name" "$image"; continue
  fi
  if [[ "$created" < "$last" ]]; then
    printf "  %-14s %-26s STALE  image %s  source %s\n" "$name" "$image" "${created:0:19}" "${last:0:19}"
    stale=$((stale+1))
  else
    printf "  %-14s %-26s current\n" "$name" "$image"
  fi
done

echo
if [ "$stale" -gt 0 ]; then
  echo "** $stale image(s) are older than the source they build from.**"
  echo "   Rebuild before trusting anything they do:"
  echo "     docker compose -f $COMPOSE --env-file $ENVF --profile tools build"
  echo "     docker compose -f $COMPOSE --env-file $ENVF --profile tools up -d"
  exit 1
fi
echo "All ${#SERVICES[@]} images are at least as new as their source."
