#!/usr/bin/env bash
# Run after the repair ingest finishes. Three things, in order.
#
# **Why this is a script rather than a list of commands.** The sequence has one non-obvious step: the
# decomposition cannot come from a normal run, because once the repair is complete every document is in the
# manifest and a re-run skips all of them. So the sample is forced by *removing manifest rows for documents
# whose files are still present*, which makes the worker treat them as new while reconciliation sees nothing
# missing. The alternative — pointing the worker at a subset directory — is what emptied the index once
# already, and the guard now refuses it, but it is also simply the wrong tool.
set -euo pipefail
cd /opt/oi-core
COMPOSE="deploy/office/compose/docker-compose.yml"
ENVF="rendered/env"
DB="docker exec postgres psql -U oicore -d oicore"

echo "=== 1. the instrumented image, actually running ==="
# **The build in the earlier script created the image and left the container alone**, which is why the repair
# run produced no timings. A container has to be recreated to pick up a new image, and that kills whatever it
# is doing — so it happens here, after the ingest is finished, and never while one is running.
if docker exec ingestion cat /tmp/ingest4.finished >/dev/null 2>&1; then
  echo "  the repair finished at $(docker exec ingestion cat /tmp/ingest4.finished)"
else
  echo "  the repair is STILL RUNNING. Recreating the container now would kill it. Stopping."
  exit 1
fi
docker compose -f "$COMPOSE" --env-file "$ENVF" build ingestion 2>&1 | tail -1
docker compose -f "$COMPOSE" --env-file "$ENVF" up -d ingestion 2>&1 | tail -1
sleep 8
docker exec ingestion grep -c PhaseTimings /app/ingest.py | sed 's/^/  PhaseTimings present: /'

echo
echo "=== 2. what the repair left ==="
$DB -A -t -c "select count(*) from chunk_index" | sed 's/^/  lexical chunks: /'
$DB -A -t -c "select count(distinct doc_id) from chunk_index" | sed 's/^/  documents:      /'
$DB -A -t -c "select count(*) from ingest_manifest" | sed 's/^/  manifest rows:  /'
docker run --rm --network ai-net curlimages/curl -sS http://vector:6333/collections/documents 2>/dev/null \
  | python3 -c "import json,sys; print('  vectors:       ', json.load(sys.stdin)['result']['points_count'])"

echo
echo "=== 3. the decomposition, from 300 documents forced back through the worker ==="
# **The manifest is the control, not the documents root.** Deleting the rows for documents whose files are
# still present makes the worker treat them as new. Reconciliation looks for manifest rows whose *file* has
# gone, and none have, so it removes nothing. This is the safe way to sample a pipeline whose sample mode is
# otherwise in the hands of a deletion pass.
$DB -A -t -c "
  delete from ingest_manifest
   where relpath in (
     select relpath from ingest_manifest
      where relpath like 'company/vol-maintenance-%'
      order by relpath limit 300)" | sed 's/^/  rows removed: /'

N="${1:-300}"
docker exec ingestion sh -c "python3 /app/ingest.py > /tmp/decomp.log 2>&1" || true
docker exec ingestion grep -E "^(seen|chunks written|where the time went|  per document)" /tmp/decomp.log \
  || docker exec ingestion tail -20 /tmp/decomp.log

echo
echo "  (the remaining documents were skipped, which is what makes this a sample rather than a rebuild)"
