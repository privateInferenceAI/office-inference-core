#!/usr/bin/env bash
# Does the pipeline still account for every format the parser reads?
#
# **Why this is a script rather than a test.** The comparison spans two places: the parser image holds
# docling, and the repository holds the decided list. A pytest run sees one or the other and never both, so a
# test would skip in every environment -- and a test that cannot run is not a test that passed.
#
# This asks the deployed parser what it reads, then asks whether every one of those formats has been decided
# in services/ingestion/formats.json. **It exits non-zero on drift or on an undecided format**, so it belongs
# wherever the deployment is verified: after a parser upgrade, and in the acceptance ladder.
#
#     bash scripts/check_parser_formats.sh
#
# After an intended upgrade, regenerate the committed list and review the diff:
#
#     bash scripts/check_parser_formats.sh --write
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

LIVE="$(mktemp)"
trap 'rm -f "$LIVE"' EXIT

echo "== what the deployed parser reads =="
docker cp scripts/dump_parser_formats.py parser:/tmp/ >/dev/null
docker exec parser python3 /tmp/dump_parser_formats.py > "$LIVE"
python3 - "$LIVE" <<'PY'
import json, sys
live = json.load(open(sys.argv[1]))
print(f"  docling {live['docling_version']}: {len(live['formats'])} formats, "
      f"{sum(len(v) for v in live['formats'].values())} extensions")
PY

if [ "${1:-}" = "--write" ]; then
  cp "$LIVE" "$ROOT/docs/parser-formats.json"
  echo "  wrote docs/parser-formats.json; review the diff and decide any new format in formats.json"
  exit 0
fi

echo
echo "== does docs/parser-formats.json match the deployed parser =="
python3 - "$LIVE" "$ROOT/docs/parser-formats.json" <<'PY'
import json, sys
live = json.load(open(sys.argv[1]))["formats"]
committed = json.load(open(sys.argv[2]))["formats"]
live_n = {k: sorted(v) for k, v in live.items()}
comm = {k: sorted(v) for k, v in committed.items()}
added = {k: v for k, v in live_n.items() if k not in comm}
removed = {k: v for k, v in comm.items() if k not in live_n}
changed = {k: (comm[k], live_n[k]) for k in live_n if k in comm and comm[k] != live_n[k]}
if added or removed or changed:
    print("  DRIFT -- the parser has moved and the committed list has not:")
    for k, v in sorted(added.items()):
        print(f"    the parser gained {k}: {v}")
    for k, v in sorted(removed.items()):
        print(f"    the parser lost {k}: {v}")
    for k, (c, l) in sorted(changed.items()):
        print(f"    {k} changed: committed {c}, live {l}")
    print()
    print("  Regenerate with --write, then decide each new format in services/ingestion/formats.json.")
    sys.exit(1)
print(f"  matches: {len(comm)} formats, {sum(len(v) for v in comm.values())} extensions")
PY

echo
echo "== is every format the parser reads decided in formats.json =="
python3 - "$LIVE" <<'PY'
import json, sys
from pathlib import Path
sys.path.insert(0, "services/ingestion")
import formats

live = json.load(open(sys.argv[1]))["formats"]
undecided = set()
for name, extensions in live.items():
    for extension in extensions:
        # The worker matches the last suffix component, so this has to as well: `.tar.gz` is seen as `.gz`.
        suffix = Path("x." + extension.lower().lstrip(".")).suffix.lower()
        if suffix not in formats.ACCEPTED_SUFFIXES and suffix not in formats.EXCLUDED_SUFFIXES:
            undecided.add(f"{suffix}  (parser calls it {name})")
if undecided:
    print("  UNDECIDED -- the parser reads these and nobody has said whether the pipeline should:")
    for item in sorted(undecided):
        print(f"    {item}")
    print()
    print("  Decide each one in services/ingestion/formats.json: accept it, or exclude it with a reason.")
    print("  An absence here is how a client's document type goes missing without anyone noticing.")
    sys.exit(1)
print(f"  all decided: {len(formats.ACCEPTED_SUFFIXES)} accepted, "
      f"{len(formats.EXCLUDED_SUFFIXES)} excluded with reasons")
PY
