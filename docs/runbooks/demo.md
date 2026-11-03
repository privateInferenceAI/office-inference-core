# The eight-scene demo: clean canonical v2 build, 2026-10-30

**Status:** C9 runbook for the clean demo deployment on the new machine. It replaces the 2026-10-23
script, which used the old 24,296-chunk corpus and the fallback model. Evidence lands in
`docs/reviews/c9/`.

**What it is.** Twelve minutes on a deployment built for it. A cited answer, the source page, a permission
boundary, the denial in the record, the same question with permission, the export handed over, and a tampered
copy that fails verification. The record is the thing the audience takes away, so every scene that touches
it runs late enough to land.

## The deployment

| | |
|---|---|
| Deployment id | `oi-core-c9-demo-20261030` |
| Machine | `i-071698b48469162a4`, m7i.4xlarge, us-east-1b, Elastic IP 18.208.119.4 |
| Deployment root | `/opt/oi-core-clean` |
| Compose project | `oi-clean` |
| Manifest | `manifests/c9-demo-clean-20261030.yaml` under the root |
| Base compose | `deploy/office/compose/docker-compose.yml` |
| Overlay | `deploy/docker-compose.canonical.yml` |
| Port override | `app-ports-override.yml` (publishes the app on 127.0.0.1:8080 only) |
| Corpus | `bundle-table-units-v2-20261028`, 80 documents, 10,576 chunks, `chunker-canonical-v1` |
| Collection | `documents-mixed-v2-20261028` |
| Serving code | product tree from release `16ed3d3` with the pin applied |
| Analyst | `bedrock/converse/us.moonshotai.kimi-k3`, no fallback chain |
| Answer profile | `OI_ANSWER_PROFILE=accuracy` |
| App | `oic-ui` main `b04ca0a`, the C9 v2 one-Ask UI |
| Running services | eight: `api`, `app`, `embeddings`, `litellm`, `mcp-gateway`, `parser`, `postgres`, `vector` |

The corpus arrives by import, not by a worker. The one-shot `importer` service reads
`bundles/bundle-table-units-v2-20261028` and writes Postgres and Qdrant, then exits. The native ingestion
worker is profiled off and must not run against this database; it can reconcile an empty manifest and
delete canonical chunks.

**The analyst has no fallback.** `litellm_settings.fallbacks` is empty, matching the pinned v2 measurement.
A Bedrock throttle produces a degraded answer with passages, not a quieter answer from another model. If
the model stops answering, stop and reschedule.

### Command prefix

```
cd /opt/oi-core-clean
alias oi='docker compose -p oi-clean \
  -f deploy/office/compose/docker-compose.yml \
  -f deploy/docker-compose.canonical.yml \
  -f app-ports-override.yml \
  --env-file rendered/env'
```

Every `oi ...` below means that alias. Omitting the overlay or the port override is a different deployment.

## How it was built

The deployment scripts are tracked in `oi-canonical-stack/scripts/`, run in order. Each writes a
timestamped log under `/opt/oi-core-clean/logs/`.

| Script | What it does |
|---|---|
| `c9-clean-prepare.sh` | Verifies the four release tarballs and the bundle checksums, extracts the product tree and UI, applies the pin (`tool_loop.py`, `retriever.py`) after diffing it, renders the Kimi manifest with fresh secrets, creates the data directories, builds every image |
| `c9-clean-start.sh` | Starts Postgres and creates the gateway database, starts LiteLLM, vector, embeddings and the API, provisions the gateway teams before the first answer, starts the parser and the MCP gateway, starts the app on 127.0.0.1:8080 |
| `c9-clean-import.sh` | Imports the v2 bundle with CPU embeddings, restarts the API, verifies the stores |
| `c9-clean-tests.sh` | Runs the section suite and the 38-role five-run |
| `c9-clean-verify.sh` | Stores, health, the one-Ask path, the model row, the boundary, demo session tokens |

The pin's two file hashes are the tested ones:

```
fd2a0afe6c3acd149509e4e4d0e3b58157e8ceca314150ef0948891bdd9971b2  tool_loop.py
34ac0c33ca97b8b7c67918dcb9e5ece8e6b942f7f00d926d78e1c09c3d81f4e2  retriever.py
```

The product test suite is not run with the pin applied. The pin is older than the current tests; failures
there belong to the pin, not to the product branch.

## Pre-demo setup, thirty minutes before the clock

| Check | Command | Expected |
|---|---|---|
| Services | `oi ps --status running` | **Eight**, all up: api, app, embeddings, litellm, mcp-gateway, parser, postgres, vector |
| API health | `docker exec api python -c "import urllib.request; print(urllib.request.urlopen('http://127.0.0.1:8088/health').read().decode())"` | `{"status":"ok","deployment_id":"oi-core-c9-demo-20261030",...}` |
| Parser | `docker inspect -f '{{.State.Health.Status}}' parser` | `healthy` |
| MCP gateway | `docker inspect -f '{{.State.Running}}' mcp-gateway` | `true` |
| App | `curl -s -o /dev/null -w '%{http_code}\n' http://127.0.0.1:8080/` | `200` |
| App contract | `curl -s http://127.0.0.1:8080/api/health` | a health document naming the deployment |
| Corpus | `docker exec postgres psql -U oicore -d oicore -tAc "select count(distinct doc_id), count(*) from chunk_index;"` | `80|10576` |
| Vector store | `docker exec api python -c "import json,urllib.request;print(json.load(urllib.request.urlopen('http://vector:6333/collections/documents-mixed-v2-20261028'))['result']['points_count'])"` | `10576` |
| Model | last `model_call` row in `audit_events` | `bedrock/converse/us.moonshotai.kimi-k3`, `fallback_used false` |
| Chain | `oi --profile tools run -T --rm audit export` then `audit verify` | `chain intact` plus the no-anchor note |
| Tokens | mint the two demo sessions, 12-hour TTL | both print a token and an expiry |
| Tunnel | load the app, paste the staff token | the Ask screen answers |

The two sessions:

```
oi --profile tools run --rm session mint --dev \
  --subject zoe.reyes@northwind.example --role viewer --ttl 12
oi --profile tools run --rm session mint --dev \
  --subject adam.hale@northwind.example --role viewer --ttl 12
```

Two browser windows, not two tabs of one profile. `sessionStorage` is copied by duplication, so a
duplicated tab carries the staff token into the executive window.

Tunnel from the workstation:

```
ssh -i ~/ai-stack/ai-stack-key.pem -N -L 18080:127.0.0.1:8080 ubuntu@18.208.119.4
```

Browser address `http://127.0.0.1:18080`. The app is published only on the box's loopback.

## The eight scenes

Target 10:30, hard stop 12:00. Boxes are the narration budget from the 2026-10-23 script. Kimi timings in
brackets are measured on the v2 bundle.

### Scene 1, the deployment. Box 0:45. [0.2 s]

Terminal:

```
oi ps --status running
docker exec api python -c "import urllib.request; print(urllib.request.urlopen('http://127.0.0.1:8088/health').read().decode())"
```

Expected: eight running services, Postgres and parser healthy, the health document names
`oi-core-c9-demo-20261030` and `analyst`.

### Scene 2, a policy answer. Box 1:00. [3 to 9 s on Kimi]

Staff asks: "What is the mileage rate for reimbursement?"

Expected: **0.67 USD per mile**, cited to the travel and expense policy and the employee handbook. A
receipt line under the answer naming the document count and the seconds.

### Scene 3, the cited page. Box 1:30. [2 to 6 s]

Staff asks: "According to the 2025 inspection schedule, by what date must the annual backflow preventer test
be completed?"

Expected: "before 30 June". The inspection schedule PDF is cited. On the v2 bundle its chips appear at
positions 3, 6 and 9, not first; open one of those and the panel should scroll the cited page into view.

### Scene 4, across the boundary. Box 1:15. [6.5 to 18.8 s]

Staff asks: "What is Northwind's Chief Executive Officer's base salary?"

Expected: no 385,000. The answer is a refusal, and the disclosure under it says documents that answer this
more directly exist and are not available to the viewer role. With the deployment's `denied` mode the
sentence does not include a count; the count is in the record for scene 5.

### Scene 5, the denial in the record. Box 1:15. [2 s]

Staff Activity screen: open the row, outcome denied. Then the terminal, which is the proof the screen is not
the record:

```
oi --profile tools run -T --rm audit list --type access --limit 5
docker exec postgres psql -U oicore -d oicore -c "select id, event_type, outcome, actor, payload->>'reason' as reason, payload->>'restricted_matched' as restricted_matched, payload->>'endpoint' as endpoint from audit_events where event_type='access' order by id desc limit 3;"
```

Expected: one access row, outcome denied, reason `boundary`, endpoint `/ask/stream`, the restricted count
that was matched, and the same row id in the CLI and the screen.

### Scene 6, the same question, permitted. Box 1:15. [8 to 13 s]

Executive window asks the same question.

Expected: **385,000** with the executive compensation citation, in about eight to thirteen seconds. The
staff denial and the executive answer both appear in the executive Activity screen.

### Scene 7, export and hand over. Box 1:45. [4 s]

```
oi --profile tools run -T --rm audit export
DAY=$(date -u +%F)
oi --profile tools run -T --rm audit verify /app/exports/$DAY/audit-events.jsonl
sha256sum exports/$DAY/audit-events.jsonl
```

Expected: the exported count, `chain intact`, the no-anchor completeness note, and the file hash in the
manifest. Copy `exports/$DAY` to the workstation and show it.

`audit backfill` is deliberately not in this scene. It copies the gateway's spend-log rows into the chain to
recover calls that bypassed the API, and it reads them over the `oicore` connection. This deployment keeps the
gateway in its own `litellm` database, so the command exits with `UndefinedTable: relation
"LiteLLM_SpendLogs" does not exist`. Every model call on this box goes through the API and is already in the
chain, so nothing is missing; export and verify are the proof. The failure is recorded in the run record and
the tool needs a separate spend-log connection before the command is usable here.

### Scene 8, tamper and fail. Box 1:45. [1.3 s]

Edit one row, not every row:

```
cp -r exports/$DAY /tmp/tamper
python3 - <<'PY'
import json
path = '/tmp/tamper/audit-events.jsonl'
rows = [json.loads(line) for line in open(path) if line.strip()]
rows[0]['actor'] = 'someone.else@northwind.example'
with open(path, 'w') as f:
    for row in rows:
        f.write(json.dumps(row, sort_keys=True) + '\n')
print('edited row', rows[0].get('id'))
PY
oi --profile tools run -T --rm -v /tmp/tamper:/app/exports \
  audit verify /app/exports/audit-events.jsonl
```

Expected: `FAIL`, one row-hash failure for the edited row, and the file hash does not match the manifest.

**Cut order if it runs long.** Scene 6 first, then scene 2. Never cut 3, 4, 5, 7 or 8.

## If the stream is slow

Kimi is slower than the version 1 models. Measured on the v2 bundle: backflow 1.6 to 2.1 s, executive
salary 8.1 to 13.2 s, staff salary 6.5 to 18.8 s, section 4 p50 9447 ms and p95 23290 ms.

The env allows `OI_FIRST_TOKEN_SECONDS=60`, `OI_STALL_SECONDS=60`, `OI_TOTAL_SECONDS=180`, so the screen can
sit on progress lines for a long time before it calls anything stalled. That is the configuration working.

- Keep talking through the progress lines. They advance per step or named tool.
- Do not press Ask again. Each press is another model call and another audit row.
- After about 45 seconds with no new progress line, reload the page. The token survives in `sessionStorage`.
- If the answer arrives as `kind: degraded`, stop the run and reschedule.

## Two passes

**Pass 1, error-finding.** Not timed. Walk all eight scenes, note every defect, wrong string, slow step or
layout problem. Nothing is claimed from this pass.

**Pass 2, timed.** Clock from scene 1 to the end of scene 8, with narration. Record per scene:

| Scene | Start | End | Operation time | Notes |
|---|---|---|---|---|

A scene that overruns is cut, not narrated faster. If the first timed run exceeds twelve minutes, cut
something before the second.

## Evidence

Keep under `docs/reviews/c9/` and on the laptop:

- the target table, image list, commit and pin hashes, bundle checksums;
- the eight-scene timings table and the total;
- the scene notes: exact answers, the page the citation opened, the counts, the seconds;
- the clean export and its manifest with the sha256, and the tampered copy with the verifier's failure;
- the section suite and five-run results with their measurement summaries;
- the backup and restore rehearsal timings;
- the limits below.

## Limits

- The single EC2 is the C9 demo deployment. The C7 client-tenant TLS edge, identity, connectors, alarms and
  backups are separate operational deployments. Say so if asked.
- No model fallback is configured. A throttle produces a degraded answer, not another model's answer.
- The product test suite is not run with the pin; the pin predates the current tests.
- The export is one UTC day and, on a build day, includes acceptance rows.
- No anchor is configured on this box. The verifier says plainly that completeness of the tail cannot be
  shown without one.
- `audit backfill` does not run on this deployment. The gateway keeps its spend logs in a separate `litellm`
  database and the tool reads them over the `oicore` connection, so it fails with `UndefinedTable`. Its
  recovery function is not needed here because the API writes every model call into the chain; export and
  verify are unaffected.
- The demo corpus is the 80-document v2 bundle. The old 24,296-chunk and 10,607-chunk collections are not
  part of this deployment.
