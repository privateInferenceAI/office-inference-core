#!/usr/bin/env bash
# Run a data-boundary probe against a deployment whose class may NOT leave.
#
# **The setup is the probe.** A boundary is only testable against a request that must be refused, and the deployed
# manifest permits the classes this box uses. So this starts a second API container on a copy of the manifest with
# access.data_class set to a class that is not in egress.frontier_classes, waits until that container confirms it
# loaded the copy, runs the probe against it, and removes it.
#
# **The confirmation step is not optional.** Without it an API that fell back to the real manifest refuses for a
# different reason and the probe passes without testing anything. That happened once while reproducing the
# streaming bypass by hand.
#
# Usage:
#     bash tests/fixtures/gate_check_run.sh           the request path: /ask and /ask/stream
#     bash tests/fixtures/gate_check_run.sh --jobs     the job path as well, against the same second deployment
#
# **One second deployment serves both probes**, because they are testing the same boundary and starting a
# third container for the second one would only add something else to go wrong. They run in sequence rather
# than together: the earlier version of this file started containers that outlived the run.
set -euo pipefail
cd "$(dirname "$0")/../.."

# **The file name is checked before the container starts.** The first version of this mapping said
# `jobs_probe` where the file is `job_probe`, and the run failed inside the container with "can't open
# file" — while the pipeline's last command exited 0, so the runner reported success. A probe that cannot
# be found is not a probe, and a runner that calls that a pass is worse than one that crashes.
PROBE=gate_check
[ "${1:-}" = "--jobs" ] && PROBE=job
PROBE_FILE="tests/fixtures/${PROBE}_probe.py"
[ -f "$PROBE_FILE" ] || { echo "FAILED: $PROBE_FILE does not exist, so nothing would be tested"; exit 1; }

CLASS="${GATE_CHECK_CLASS:-regulated}"
NAME=gate-check-tmp-api
COPY=rendered/gate-check-forbidden.json

python3 - "$CLASS" "$COPY" <<'PY'
import json, sys
cls, out = sys.argv[1], sys.argv[2]
m = json.load(open('rendered/manifest.json'))
permitted = (m.get('access') or {}).get('egress', {}).get('frontier_classes') or []
if cls in permitted:
    sys.exit('%s is permitted on this deployment, so it cannot test the boundary' % cls)
m['access']['data_class'] = cls
open(out, 'w').write(json.dumps(m))
print('forbidden copy written: data_class=%s, not in %s' % (cls, permitted))
PY

cleanup() { docker rm -f "$NAME" >/dev/null 2>&1 || true; rm -f "$COPY"; }
trap cleanup EXIT

docker rm -f "$NAME" >/dev/null 2>&1 || true

# **The database URL comes from the compose service, not from rendered/env.** The API resolves a session token
# against Postgres, so a container started without this answers `401 no_session` to every request — which looks
# like a broken session rather than a missing environment variable. Learned the hard way.
DB_URL=$(docker inspect api --format '{{range .Config.Env}}{{println .}}{{end}}' | sed -n 's/^DATABASE_URL=//p' | head -1)
[ -n "$DB_URL" ] || { echo "FAILED: could not read DATABASE_URL from the running api container"; exit 1; }

docker run -d --name "$NAME" --network ai-net \
  --user "$(id -u):$(id -g)" -v "$PWD:/w" -w /w \
  --env-file rendered/env -e "DATABASE_URL=$DB_URL" -v "$PWD/rendered:/app/rendered" \
  -e "OI_MANIFEST=/app/rendered/$(basename "$COPY")" oi-core/api:local >/dev/null

for _ in $(seq 1 30); do
  sleep 2
  seen=$(docker exec "$NAME" python -c "import json,pathlib;print(json.loads(pathlib.Path('/app/rendered/$(basename "$COPY")').read_text())['access']['data_class'])" 2>/dev/null || true)
  [ "$seen" = "$CLASS" ] && { echo "second container loaded: data_class=$seen"; break; }
done
[ "${seen:-}" = "$CLASS" ] || { echo "FAILED: the second container never confirmed it loaded $CLASS"; exit 1; }

# The probe's own exit code is the runner's. **Without capturing it, a probe that failed inside the
# container still left this script exiting 0**, because the last command in a pipeline decides the status
# and the probe was not the last command.
set +e
docker run --rm --network ai-net --user "$(id -u):$(id -g)" \
  -v "$PWD:/w" -w /w --env-file rendered/env \
  -e "GATE_CHECK_CLASS=$CLASS" -e "JOB_PROBE_CLASS=$CLASS" \
  --entrypoint python oi-core/session:local "/w/$PROBE_FILE"
status=$?
set -e
echo "$(basename "$PROBE_FILE"): exit $status"
exit "$status"
