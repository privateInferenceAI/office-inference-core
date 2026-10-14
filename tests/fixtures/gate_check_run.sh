#!/usr/bin/env bash
# Run the data-boundary probe against a deployment whose class may NOT leave.
#
# **The setup is the probe.** A boundary is only testable against a request that must be refused, and the deployed
# manifest permits the classes this box uses. So this starts a second API container on a copy of the manifest with
# access.data_class set to a class that is not in egress.frontier_classes, waits until that container confirms it
# loaded the copy, runs the probe against it, and removes it.
#
# **The confirmation step is not optional.** Without it an API that fell back to the real manifest refuses for a
# different reason and the probe passes without testing anything. That happened once while reproducing the
# streaming bypass by hand.
set -euo pipefail
cd "$(dirname "$0")/../.."

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
docker run -d --name "$NAME" --network ai-net \
  --user "$(id -u):$(id -g)" -v "$PWD:/w" -w /w \
  --env-file rendered/env -e "OI_MANIFEST=/w/$COPY" oi-core/api:local >/dev/null

for _ in $(seq 1 30); do
  sleep 2
  seen=$(docker exec "$NAME" python -c "import json,pathlib;print(json.loads(pathlib.Path('/w/$COPY').read_text())['access']['data_class'])" 2>/dev/null || true)
  [ "$seen" = "$CLASS" ] && { echo "second container loaded: data_class=$seen"; break; }
done
[ "${seen:-}" = "$CLASS" ] || { echo "FAILED: the second container never confirmed it loaded $CLASS"; exit 1; }

docker run --rm --network ai-net --user "$(id -u):$(id -g)" \
  -v "$PWD:/w" -w /w --env-file rendered/env \
  -e "GATE_CHECK_CLASS=$CLASS" --entrypoint python oi-core/session:local /w/tests/fixtures/gate_check.py
