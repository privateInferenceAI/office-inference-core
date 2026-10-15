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

# **The worker is stopped for the measurement, and that is not tidiness.** It runs a cycle every fifteen
# minutes, so leaving it up means two runs against one manifest: the timings interleave, and whichever
# finishes second decides what the index holds. The one-off below uses the same image, the same environment
# and the same mounts, so it measures the real thing with nothing else touching it.
docker compose -f "$COMPOSE" --env-file "$ENVF" stop ingestion 2>&1 | tail -1

echo "  instrumented code present:"
docker compose -f "$COMPOSE" --env-file "$ENVF" run --rm --no-deps --entrypoint grep ingestion \
  -c PhaseTimings /app/ingest.py

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
# **Spread across topics rather than taken from one**, because a sample of three hundred maintenance
# records is a sample of one document shape. `md5(relpath)` rather than `random()` so the same three hundred
# come back on a second run, which is what makes a measurement repeatable.
N="${1:-300}"
$DB -A -t -c "
  delete from ingest_manifest
   where relpath in (
     select relpath from ingest_manifest
      where relpath like 'company/vol-%'
      order by md5(relpath) limit $N)" | sed 's/^/  rows removed: /'

echo "  topics in the sample:"
$DB -A -t -c "
  select split_part(split_part(relpath, '/', 2), '-', 2) as topic, count(*)
    from (select relpath from ingest_manifest
           where relpath like 'company/vol-%'
           order by md5(relpath) limit $N) t
   group by 1 order by 2 desc limit 6" | sed 's/^/    /'

docker compose -f "$COMPOSE" --env-file "$ENVF" run --rm --no-deps --entrypoint python ingestion \
  /app/ingest.py --documents /documents 2>&1 | tee /tmp/decomp.log \
  | grep -E "^(seen |chunks written|where the time went|  per document|  failed)" || true

echo
echo "  (the other documents were skipped, which is what makes this a sample rather than a rebuild)"

echo
echo "=== 4. the worker back up ==="
docker compose -f "$COMPOSE" --env-file "$ENVF" up -d ingestion 2>&1 | tail -1
sleep 8
docker inspect ingestion --format '  ingestion: {{.State.Status}}, restarts={{.RestartCount}}'
