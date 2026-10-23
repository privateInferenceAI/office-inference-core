# The eight-scene demo

**Status:** the C9 runbook, written 2026-10-23. Rehearsal evidence lands in `docs/reviews/c9/`.
The build plan calls C9 "demo rehearsal, twelve minutes, eight scenes"; this is the script and the
deployment that runs it.

**What it is.** A twelve-minute demonstration on a deployment built for it, not on the development
box. A cited answer, the source page, a permission boundary, the denial in the record, the same
question with permission, the export handed over, and a tampered copy that fails verification. The
record is the thing the audience takes away, so every scene that touches it runs late enough to
land.

## The deployment

| | |
|---|---|
| Account | 878451098165, us-east-1b |
| Instance | On-Demand `m7i.4xlarge`, 200 GB gp3, no Elastic IP |
| Deployment root | `/opt/oi-core` |
| Deployment id | `oi-core-demo-2026-10-23` (change it in the manifest for a rebuild) |
| Manifest | [`manifests/demo-cloud.yaml`](../manifests/demo-cloud.yaml) |
| Corpus | `second-corpus-2026-10-22`, 80 documents, 24,296 chunks |
| App | `oic-ui` main after the boundary-disclosure merge |
| Answer profile | `OI_ANSWER_PROFILE=accuracy` |
| Analyst | `bedrock/us.anthropic.claude-sonnet-4-6`, fallback Haiku 4.5 then Nova Pro |

The install is [`fresh-install-agent.md`](fresh-install-agent.md) with four deltas: the tracked
demo manifest instead of a copy, the app built from a current `oic-ui` archive, the accuracy answer
profile, and the acceptance below before any rehearsal. The startup order and the four known
defects are in that runbook; do not re-derive them.

## Build it

1. Archive the demo commit and the current `oic-ui` main, upload both to
   `officeinference-corpus-878451098165`, and record the sha256 of each.
2. Launch the box per the fresh-install runbook. Record the instance id, public IP and launch time.
3. Extract the code and the UI archive. The code archive contains the tracked demo manifest.
4. Render it, set the profile, validate:

       cd /opt/oi-core
       python3 scripts/render_manifest.py --manifest manifests/demo-cloud.yaml --root /opt/oi-core
       grep -q '^OI_ANSWER_PROFILE=' rendered/env \
         && sed -i 's/^OI_ANSWER_PROFILE=.*/OI_ANSWER_PROFILE=accuracy/' rendered/env \
         || echo 'OI_ANSWER_PROFILE=accuracy' >> rendered/env
       python3 scripts/validate_manifests.py manifests/demo-cloud.yaml
       python3 scripts/check_model_lifecycle.py manifests/demo-cloud.yaml

   Expected: `declared: 2 group(s), 4 user(s)`, `1/1 manifests valid`, `1/1 manifests clean`.

5. Create the network and build the API image first. The MCP gateway's Dockerfile is
   `FROM oi-core/api:local`, and a single parallel build can try to pull that image from a registry
   before it exists locally, which fails with `pull access denied`. Build the API, then everything:

       docker network create ai-net
       docker compose -f deploy/office/compose/docker-compose.yml --env-file rendered/env build api
       docker compose -f deploy/office/compose/docker-compose.yml --env-file rendered/env \
         --profile tools build

   Start the gateway before the API on a blank database. Then start the eight services this
   deployment uses, named explicitly. The MCP gateway is left out: the scenes do not touch the agent
   runtime, and without a connector token it exits with `MCP_GATEWAY_TOKEN is required` and
   restart-loops.

       docker compose -f ... up -d postgres litellm
       # wait for /health/liveliness, then
       docker compose -f ... up -d postgres litellm embeddings parser vector api app ingestion

6. Provision teams, then ingest the corpus and wait for the counts:

       docker compose -f ... --profile tools run -T --rm keys --provision-teams
       docker exec postgres psql -U oicore -d oicore -tAc "select count(distinct doc_id), count(*) from chunk_index;"

   Expected: `80|24296`. If the counts differ, stop and investigate; the baseline is not adjusted.

7. Build the app and put it behind a loopback relay, because neither the API nor the app publishes
   a port:

       export OI_UI_ROOT=/opt/oic-ui
       docker compose -f ... build app && docker compose -f ... up -d app
       docker run -d --name app-relay --network ai-net -p 127.0.0.1:8090:8090 alpine/socat \
         -d -d TCP-LISTEN:8090,fork,reuseaddr TCP:app:8080
       docker run --rm --network ai-net curlimages/curl -sS -o /dev/null -w '%{http_code}\n' http://app:8080/
       docker run --rm --network ai-net curlimages/curl -sS http://app:8080/api/health

   Expected: `200` and a health document. Then tunnel from the workstation:

       ssh -i ~/ai-stack/ai-stack-key.pem -N -L 18080:127.0.0.1:8090 ubuntu@<public-ip>

   The browser address is `http://127.0.0.1:18080`. The relay is review scaffolding; remove it
   with `docker rm -f app-relay` afterwards.

## Acceptance before the first rehearsal

Run all of this on the demo deployment and save the transcripts.

1. **Four probes**, exactly as `fresh-install-agent.md` section 9. Stop LiteLLM for one question
   first so `interface_probe`'s DEGRADED right has a row to read, then start it again. Expect four
   `PASSED: N right(s) held` lines.
2. **Five known answers** through `data_suite_run.py`: `PUB_N01`, `PUB_P01`, `PUB_L01`,
   `SYN_A01` for staff and executive. Expect 5 of 5. `SYN_A01` is the boundary question: staff
   refused, executive 385,000.
3. **One smoke answer** through `ask_client.py`, then read the audit row: `resolved_model` is
   `bedrock/us.anthropic.claude-sonnet-4-6` and `fallback_used` is false.
4. **The app**: `200` from `/`, a health document from `/api/health`, and
   `bash scripts/check-contract.sh --live http://app:8080/api` reports the contract matches.
5. **The boundary, end to end.** Mint a session for `zoe.reyes@northwind.example`, ask the CEO salary
   question through `POST /ask`, and check three things: the answer does not contain 385,000, the
   response has `denied: true` and a disclosure, and exactly one `access`/`denied` row was written
   with `reason=boundary` and a `restricted_matched` count. Then ask as `adam.hale@northwind.example`
   and expect 385,000 with the executive compensation document cited.
6. **Export, verify, tamper**: export today, verify it clean, tamper a copy, and confirm the verifier
   fails with both the row hash and the file hash. Do this before the rehearsal so a failure is a
   fix rather than a surprise on stage.

## The eight scenes

Target 10:30, hard stop 12:00. Two browser tabs are open before the clock starts, one with the
staff token and one with the executive token. Tokens are minted the same morning with a 12-hour TTL.

| # | Scene | At | Action | Expected |
|---|---|---|---|---|
| 1 | The deployment | 0:00 | `docker compose ps --status running`; API health | Eight running services, Postgres and parser healthy, health names the deployment and the analyst |
| 2 | A policy answer | 0:45 | Staff asks "What is the mileage rate for reimbursement?" | 0.67 per mile, cited to the handbook and the travel policy, receipt with document count and seconds |
| 3 | The cited page | 1:45 | Staff asks "What limitation does GAO identify in the primary federal procurement database's information about the origin of defense goods?" then opens the p.2 source | "Limited information about the countries of origin"; the GAO report opens with the Highlights passage on page 2 marked as cited |
| 4 | Across the boundary | 3:15 | Staff asks "What is Northwind's Chief Executive Officer's base salary?" | No 385,000; a disclosure under the answer that documents exist the role cannot read |
| 5 | The denial in the record | 4:30 | Staff Activity, outcome denied, open the row; then `audit list --type access` | One access row, outcome denied, reason boundary, roots company, restricted count, disclosure, request id; the CLI shows the same row |
| 6 | The same question, permitted | 5:45 | Executive tab asks the same question with **Ask without streaming**, then Activity as executive | 385,000 with the executive compensation citation in about 10 seconds; the scope line names the people the page covers; the staff denial and the executive answer both appear |
| 7 | Export and hand over | 7:00 | `audit backfill`, `audit export`, `audit verify`, `sha256sum`, copy the directory to the laptop | Exported N events, verification ok with the no-anchor completeness note, the file and its manifest on the laptop |
| 8 | Tamper and fail | 8:45 | Copy the export to `/tmp/tamper`, change one actor with `sed`, verify the copy | FAIL with two independent reasons: the row hash and the file hash against the manifest. Close on what the exported file proves and what it does not |

**Scene 5, the commands.** The row is the point of the demo, so it is read twice, once as a person
and once as an operator:

    docker compose -f ... --profile tools run -T --rm audit list --type access --limit 5

The Activity screen shows the same row first; the CLI is the proof that the screen is not the
record.

**Scene 7, the commands.**

    cd /opt/oi-core
    alias oi='docker compose -f deploy/office/compose/docker-compose.yml --env-file rendered/env'
    oi --profile tools run -T --rm audit backfill
    oi --profile tools run -T --rm audit export
    DAY=$(date -u +%F)
    oi --profile tools run -T --rm audit verify /app/exports/$DAY/audit-events.jsonl
    sha256sum exports/$DAY/audit-events.jsonl

Copy `exports/$DAY` to the laptop and show the file and its manifest. The verifier prints the
honest limit: with no anchor it can show the chain is intact and cannot show that rows were not cut
from the end. Say that sentence out loud in the close.

**Scene 8, the commands.** Edit one row, not every row. A global `sed` on the actor changes every
row that actor appears in, which reports five failures where the demo is about one edit.

    cp -r exports/$DAY /tmp/tamper
    python3 - <<'PY'
    import json
    path = '/tmp/tamper/audit-events.jsonl'
    rows = [json.loads(line) for line in open(path) if line.strip()]
    rows[0]['actor'] = 'someone.else@northwind.example'
    with open(path, 'w') as f:
        for r in rows:
            f.write(json.dumps(r, sort_keys=True) + '\n')
    print('changed row', rows[0].get('id'))
    PY
    oi --profile tools run -T --rm -v /tmp/tamper:/app/exports \
      audit verify /app/exports/audit-events.jsonl

Expected: `FAIL`, one `row_hash does not match the row contents` line for the edited row, and `the
file hash does not match the manifest`. The clean copy on the laptop is untouched; its hash still
matches the manifest. If the narration is about the denial row, edit that row instead by finding the
one whose payload carries `"reason": "boundary"`.

**Cut order if it runs long.** Scene 6 first, then scene 2. Never cut 3, 4, 5, 7 or 8. If a scene
overruns, cut rather than skip the audit proof.

## Before the demo

Run this thirty minutes before the clock starts. Save the output beside the evidence.

| Check | Command | Expected |
|---|---|---|
| Health | `docker compose ps --status running` | Eight running services; Postgres and parser healthy. The MCP gateway is stopped and is not part of this deployment |
| API | `docker exec api python -c "import urllib.request; print(urllib.request.urlopen('http://127.0.0.1:8088/health').read().decode())"` | The deployment id and `analyst` |
| App | `200` from `http://app:8080/` and a health document from `/api/health` | Both answer |
| Chain | export and verify today | `chain intact` |
| Model | last `model_call` row in `audit_events` | `bedrock/us.anthropic.claude-sonnet-4-6`, `fallback_used false` |
| Corpus | `select count(distinct doc_id), count(*) from chunk_index;` | `80|24296`, dead-letter count 0 |
| Tokens | mint staff and executive, 12-hour TTL | Both print a token and an expiry |
| Tunnel | load the app, paste the staff token | The Ask screen answers |
| Alarms | the five `oi-core-*` alarms | OK. The demo box does not publish `OI/Health` metrics and is not covered by them |
| Tenant | `i-0b6aa00095ae40e54` | Stopped. Do not thaw it for a demo |
| Email | none | SES is in the sandbox; the demo does not use it |

The demo box has no backup timer, no alarm publisher and no SSO. Say so if asked rather than
implying coverage that is not there. The full product shape is the client deployment, not this box.

## Rehearse

Run the eight scenes twice, start to finish, with a clock. Record the start and end of each scene
in a small table; a scene that runs long is cut, not narrated faster. If the first run exceeds
twelve minutes, cut something before the second run. The second run is the one whose timings and
artifacts are preserved.

**Fallbacks.** If the tunnel drops, reload the page; the token is in sessionStorage and survives.
If the model falls back to Haiku, the answer may still be right but say so rather than claim the
primary. If an answer degrades, stop the run and reschedule; a degraded answer is not the demo. If
the export is from the same UTC day as acceptance, the file contains the acceptance rows; the note
says so, and the artifact is still this deployment's record.

## Evidence

Keep under `docs/reviews/c9/`:

- the target, instance id, commit hashes, archive sha256, corpus counts and probe transcripts;
- the timings table and the total;
- the clean export and its manifest with the export's sha256;
- the tampered copy and the verifier's failure output;
- the scene notes: exact answers, the page the citation opened, the counts and the seconds;
- the limits, including the checks this deployment does not run.

## Limits

- The demo deployment is the cloud build. Model calls go to Bedrock in account 878. The in-office
  build with local models is a different deployment and is not this demo.
- No alarm publisher, backup timer, SES or SSO on the box. The pre-demo table says what is checked
  where.
- No MCP gateway and no agent runtime. The compose file declares the gateway and this deployment
  stops it on purpose, because the scenes do not use tools. Started without a connector token it
  crash-loops with `MCP_GATEWAY_TOKEN is required`.
- **The Ask screen's streamed path is single-shot; the JSON button runs the tool-assisted path.** On this
deployment the two paths can answer differently: the CEO salary question missed the row on the stream and
found it through the JSON button in about ten seconds. The accuracy profile's measurements are on the
tool-assisted path, so the executive scene uses **Ask without streaming**. Aligning the stream with it is
a product gap for after C9, recorded in the rehearsal evidence.
- The four acceptance probes do not touch the app. The UI checks above are the only acceptance the
  screen gets, so run them.
- The export is one UTC day. Without an anchor the verifier cannot prove the tail is complete; the
  hourly anchor and the Object Lock bucket are the production shape, not the demo box.
- A model fallback names a different model in the record. The record is right; do not paper over it.
