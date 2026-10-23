#!/usr/bin/env bash
# Scan the deployment's current images and keep one JSON report per image.
#
# Run from /opt/oi-core, or pass the output directory. The systemd timer runs this weekly.
set -euo pipefail

OUT="${1:-/var/log/oi-image-scan-$(date +%Y%m%d)}"
mkdir -p "$OUT"

images=$(docker images --format '{{.Repository}}:{{.Tag}}' | grep -E '^(oi-core/|postgres:|qdrant/|ghcr.io/huggingface/)' | sed 's/:<none>$//' | sort -u)
if [ -z "$images" ]; then
  echo "no deployment images found" >&2
  exit 1
fi

for image in $images; do
  name=$(printf '%s' "$image" | tr '/:' '__')
  echo "scanning $image"
  docker run --rm -v /var/run/docker.sock:/var/run/docker.sock -v "$OUT":/out \
    aquasec/trivy:latest image --quiet --format json --output "/out/$name.json" "$image"
done

python3 - "$OUT" <<'PY'
import json, pathlib, sys
out = pathlib.Path(sys.argv[1])
summary = {}
for path in sorted(out.glob("*.json")):
    data = json.loads(path.read_text())
    vulns = [v for result in data.get("Results", []) for v in (result.get("Vulnerabilities") or [])]
    summary[path.stem] = {"vulnerabilities": len(vulns),
                          "high": sum(1 for v in vulns if v.get("Severity") == "HIGH"),
                          "critical": sum(1 for v in vulns if v.get("Severity") == "CRITICAL")}
(out / "summary.json").write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n")
print(json.dumps(summary, indent=2, sort_keys=True))
PY
