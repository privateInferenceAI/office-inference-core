# C6 Section 2 runtime evidence

**Date:** 2026-10-23. **Box:** `i-02aba2feef08f8e7b`, m7i.4xlarge, us-east-1b, credits account
`878451098165`, public IP `52.204.204.133`. **Deployment:** `oi-core-dev-cloud-workspace`, cloud profile.

This is the first live run of the agent runtime on a real deployment. It is not the section gate: the approval
gate, sub-agents, context summaries and the kill/restart test are still open. It is enough to show that a claimed
definition job runs through the real gateway, the real tools, the engine step rows and the v3 audit chain.

## What was deployed

The box was a core-only cloud deployment. It was rendered from `manifests/dev-cloud-workspace.yaml` into
`/opt/oi-core/rendered`, which added:

- packs `core, document-chat, agentic-desk`
- one definition, `research_brief`, role `analyst`, tools `search_documents`, `read_passages`, `query_rows`
- `agentic.runtime_count: 1`
- `rendered/agent_definitions.json`

The API image was built from the integration branch at commit `934bf4a` (runtime commit `7333a8e` plus the
Section 1 merge). The box database was already on chain version 3 from the Section 1 deployment.

## The live runs

The first two runs failed for real product reasons, and both were fixed.

| Job | State | What happened |
|---|---|---|
| `50b5ab951d1b42aab3e3c4f0d4b126b1` | failed, `verification` | The runtime omitted the job input from the model prompt. The model answered the definition description as if it had been greeted. Fixed by passing `run.input`. |
| `af2c72a083e047c1b32bb71cdbed8d4d` | failed, `verification` | The model used tools correctly and wrote a good brief, but cited files by name instead of the tool's numbered evidence. Fixed by making the citation format explicit. |
| `69ad59730f0345b997cb32ef92821f0f` | succeeded | First successful run: 4 steps, 12.3 seconds, cited answer. The chain check then found no model-call rows and no job or step on the tool rows. |
| `2426a6306cd3408995a809f73cd0735f` | succeeded | After the audit fix: 4 steps, 12.3 seconds, 3 `model_call` rows, 3 real `tool_call` rows and 8 engine step rows. |
| `e7d41c0e3a0847de9533ff4019dd44fd` | succeeded | After the Section 1 merge: 3 steps, 6.0 seconds, cited answer. This is the run that proves the v3 chain and the runtime coexist. |

The successful answer for job `2426a6306cd3408995a809f73cd0735f` is a cited expense-policy brief. It names the
Travel and Expense Policy and the Employee Handbook and carries `[1]`, `[8]`, `[5]` and `[24]` citations.

## Audit chain

The chain export holds 49 events. It verifies intact with the v3 verifier. No external anchor was supplied, so
this proves the links are intact, not that no row was deleted from the end.

For job `2426a6306cd3408995a809f73cd0735f` the chain holds:

- 3 `model_call` rows, one per model completion, each with `job_id`, `task` and `step`.
- 3 `tool_call` rows for `search_documents` and the two `read_passages` calls, each with the job and step.
- 8 engine rows, one started and one succeeded row for each of the four steps.

Rows 361 to 374 are the job's rows. The model rows name `bedrock/us.anthropic.claude-sonnet-4-6` in the
attribution payload.

## Gate live run

Job `af8ab77097f945c196a59eb22c5e602e` stopped at the gate before its first tool call.

- At the gate: state `awaiting_approval`, steps `turn-1` and `approval-1`, pending target
  `search_documents`, digest `sha256:f7198afb...`.
- The approval route accepted the digest, resumed the runtime, and the job finished with six steps:
  `turn-1`, `approval-1`, `act-1`, `turn-2`, `turn-3`, `finish` in 27.7 seconds.
- The chain row for `search_documents` is row 391, after the approval row 388 and the act row 390. No
  `search_documents` row exists before the approval.
- The chain export holds 68 events and verifies intact.

**The test used `search_documents` as the gated tool.** The `research_brief` definition normally lists
`send_email` as irreversible, but there is no email connector yet, so the live test temporarily marked a
read-only tool as irreversible to exercise the gate. The original definition was restored and the API
restarted after the run. This proves the gate mechanics and the resume path, not the email connector.

## Sub-agent live run

Job `46d4cf57ac0e45a897b454c373821705` used the helper. The parent task asked it to use the research helper,
and the model called `delegate_research`.

- The job succeeded in three steps, `turn-1`, `turn-2` and `finish`, in 12.4 seconds.
- The audit rows show two child model calls at 405 and 407 and one child tool call at 406, all with span
  `child-1` and role `child`, under parent task and step `turn-1`.
- The parent's own model rows are 404 and 410. The child's search result came back as a tool message, and the
  parent answered in `turn-2`.
- The chain export holds 79 events and verifies intact.

The child used the same session permit and a subset of the parent's declared tools. It had no delegate tool, so
it could not spawn another child.

## Kill and restart live run

Job `88b19c45060f4e319665611d98e301bf` was killed while `turn-2` was running.

- At the kill: state `running`, attempts 2, steps `turn-1` succeeded and `turn-2` running.
- The API container was killed and restarted. Its log says `agent dispatcher: recovering 1 running job(s)`.
- After recovery: the interrupted `turn-2` row is failed with kind `stopped`, a new `turn-2` row succeeded,
  then `turn-3` and `finish` succeeded. The job finished in 21.3 seconds.
- `turn-1` did not repeat. The recovered job has one succeeded row for it.
- The audit rows show the interrupted turn at 419 and 420 and the recovered turn at 421 onward. The chain
  export holds 97 events and verifies intact.

## What this proves

- A definition job is claimed by the dispatcher and runs without an inline request.
- One model turn is one engine step, and each step is a row a client can read.
- The real gateway, the real session, the real toolbox and the signing key are wired correctly.
- A tool failure in the strict audit path fails the step instead of losing the row.
- The runtime writes the model row and the tool row the record needs, addressed by job and step.
- The chain stays intact with v3 rows and the runtime's rows in it.

## What this does not prove

- A real irreversible connector. The gate mechanics are live-tested; the email connector is not.
- Token budgets for the child. The child has a step cap and a wall-clock cap; the token ceiling is not enforced
  yet.
- A live context trim. The trim path has unit tests; no live run has forced a trim yet.
- The workspace sandbox, web access, artifacts and workspace UI. That is the WS phase.

## Reproduce

On the box, from `/opt/oi-core`:

    python3 scripts/render_manifest.py --manifest manifests/dev-cloud-workspace.yaml --root /opt/oi-core
    docker compose -f deploy/office/compose/docker-compose.yml --env-file rendered/env build api
    docker compose -f deploy/office/compose/docker-compose.yml --env-file rendered/env up -d --no-deps api

Mint a development session and submit a job:

    docker exec api python /app/services/session/cli.py mint \
      --subject probe-staff@officeinference.com --role viewer --ttl 2 --dev --json

Then POST to `http://127.0.0.1:8088/jobs` from inside the API container, with the session token in the
`session` field of the JSON body. Poll `GET /jobs/{id}` with `X-OI-Session`.

Export and verify the chain:

    docker exec api python /app/audit.py export --out /tmp/chain.json
    docker exec api python /app/audit.py verify /tmp/chain.json/audit-events.jsonl

## Files in raw/

- `audit-events.jsonl`: the 49-event chain export.
- `verify.txt`: the verifier's result.
- `export.txt`: the export summary and last row hash.
- `jobs.json`: the two post-fix successful jobs as returned by the API.
- `api.log`: the API startup log from the final deployment.
- `SHA256SUMS`: hashes for the files above.

## Files in recovery/

- `job-after.json`: the recovered job.
- `audit-rows.txt`: the audit rows for the killed and recovered turns.
- `recovery.log`: the API startup line that names the recovered job.
- `audit-events.jsonl`: the 97-event chain export.
- `verify.txt`: the verifier's result.
- `export.txt`: the export summary.
- `SHA256SUMS`: hashes for the files above.

## Files in subagent/

- `job-after.json`: the successful parent job.
- `audit-rows.txt`: the parent and child rows, with the `child-1` span and role.
- `audit-events.jsonl`: the 79-event chain export.
- `verify.txt`: the verifier's result.
- `export.txt`: the export summary.
- `SHA256SUMS`: hashes for the files above.

## Files in gate/

- `job-after.json`: the job after approval and success.
- `job-events.txt`: the step events, including the gate and the act.
- `audit-rows.txt`: the chain rows for the job, showing the approval before the act and the act before the
  tool call.
- `audit-events.jsonl`: the 68-event chain export.
- `verify.txt`: the verifier's result.
- `export.txt`: the export summary.
- `SHA256SUMS`: hashes for the files above.
