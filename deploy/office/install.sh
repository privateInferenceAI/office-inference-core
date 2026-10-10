#!/usr/bin/env bash
# Office build installer.
#
# Usage:
#   ./install.sh --manifest manifests/client.yaml            # office build
#   ./install.sh --manifest manifests/client.yaml --cloud-leg # hybrid
#
# Design rules:
#   - Never hand-configure a box. Everything comes from the manifest.
#   - Every step is idempotent; running it twice is safe.
#   - Health checks gate every stage, and failures stop the install rather than half-finish it.
#   - Nothing publishes to the network except the ingress service.

set -euo pipefail

MANIFEST=""
CLOUD_LEG=0
while [[ $# -gt 0 ]]; do
  case "$1" in
    --manifest) MANIFEST="$2"; shift 2 ;;
    --cloud-leg) CLOUD_LEG=1; shift ;;
    -h|--help) sed -n '2,12p' "$0"; exit 0 ;;
    *) echo "unknown argument: $1" >&2; exit 2 ;;
  esac
done
[[ -n "$MANIFEST" ]] || { echo "--manifest is required" >&2; exit 2; }
[[ -f "$MANIFEST" ]] || { echo "manifest not found: $MANIFEST" >&2; exit 2; }

step() { printf '\n=== %s\n' "$1"; }

step "Preflight"
# TODO: check Ubuntu version, NVIDIA driver, GPU memory, free disk, outbound connectivity,
# and that no other service is bound to the ports we need.
echo "TODO: preflight checks"

step "Read manifest"
# TODO: validate against schemas/manifest.schema.json, then derive the service set from
# packs/catalog.yaml. Fail loudly on an unknown pack id.
echo "TODO: parse and validate $MANIFEST"

step "Install runtime"
# TODO: Docker, NVIDIA container toolkit, the ai-net bridge, and UFW rules that deny inbound
# except SSH, the ingress port, and the consented support tunnel.

step "Write configuration"
# TODO: render compose and service config from the manifest. Secrets are generated locally
# with openssl and stored mode 600, or sealed with SOPS.

step "Pull images"
# TODO: pull by digest, and record the digests into the deployment record.

step "Start services"
# TODO: compose up in dependency order, waiting on each healthcheck.

step "Configure operations"
# TODO: backup schedule and restore drill, signed update timer, drift reporter, remote support
# tunnel that requires consent each time.

if [[ "$CLOUD_LEG" == "1" ]]; then
  step "Cloud leg"
  # TODO: apply deploy/cloud-leg to the client's account, then flip the frontier lane on in the
  # local routing policy. Requires credentials the client grants and can revoke.
  echo "TODO: terraform apply deploy/cloud-leg"
fi

step "First-run probe"
# TODO: ingest a sample document, ask a question, confirm the citation, confirm the audit record,
# and confirm that a frontier-lane request is refused while the lane is off.
echo "TODO: end-to-end probe"
