# C9 clean run, 2026-10-30 build, walks on 2026-11-03

**Status:** the clean C9 deployment was built from the release artifacts on a new machine and its gates
passed. The presenter's untimed walkthrough ran clean on 2026-11-03. The timed narration pass and the local
backup/restore rehearsal were not run before this record was written; both are listed as open below.

## Target

| | |
|---|---|
| Account, Region | 878451098165, us-east-1b |
| Machine | `i-071698b48469162a4`, m7i.4xlarge, 200 GB gp3, Elastic IP 18.208.119.4 |
| Image | Ubuntu 24.04, `ami-0d6dd9e8265d48906` |
| Deployment root | `/opt/oi-core-clean` |
| Compose project | `oi-clean` |
| Deployment id | `oi-core-c9-demo-20261030` |
| Product code | `office-inference-core` release `release/canonical-v2-20261028` (`16ed3d3` at build time) |
| Serving pin | `deploy/canonical/serving-canonical-v2/`: tool_loop `fd2a0afe6c3acd149509e4e4d0e3b58157e8ceca314150ef0948891bdd9971b2`, retriever `34ac0c33ca97b8b7c67918dcb9e5ece8e6b942f7f00d926d78e1c09c3d81f4e2` |
| UI | `oic-ui` main `b04ca0a`, C9 v2 one-Ask UI |
| Corpus | `bundle-table-units-v2-20261028`, 80 documents, 10,576 chunks, `chunker-canonical-v1` |
| Collection | `documents-mixed-v2-20261028` |
| Analyst | `bedrock/converse/us.moonshotai.kimi-k3`, no fallback chain, empty temperature, 2,000 max tokens |
| Services | eight: api, app, embeddings, litellm, mcp-gateway, parser, postgres, vector; app on 127.0.0.1:8080 |

The machine was built from the four release tarballs at
`/home/anthony/oi-corpus-data/c9-clean-release-20261028/` (SHA256SUMS verified on the machine):
core `3494edf3...`, UI `99feddb7...`, bundle `84bd2a64...`, documents `e6718189...`. The serving pin was
applied over `services/api/tool_loop.py` and `services/retrieval/retriever.py`; the product tree's own
files are not what the deployment served.

## Acceptance before the walkthrough

| Check | Result |
|---|---|
| Bundle verification at import | 80 accepted, 0 rejected, 10,576 chunks |
| Qdrant points | 10,576, status green |
| Postgres `chunk_index` | 80 documents, 10,576 chunks |
| Collection contract | `documents-mixed-v2-20261028`, generation 1, `chunker-canonical-v1` |
| API `/health` | `{"status":"ok","deployment_id":"oi-core-c9-demo-20261030",...}` |
| Parser | healthy |
| MCP gateway | running, restart count 0 |
| App | root and `/api/health` 200 on loopback |
| Gateway teams | provisioned before the first answer |

## Search measurements, fresh import

Section suite: **38/38**. s1 10/10, s2 7/7, s3 7/7, s4 8/8, s5 6/6.

38-role five-run, fresh import: **38, 38, 37, 38, 37 = 188/190**. Failures: `PUB_X02` (run 3) and
`SYN_N01` (run 5). This matches the reference band measured on restored reference state (188/190); the same
machine's first five-run before restoring the reference database was 183/190.

Five known answers: **5/5**. One-Ask streamed path: `0.67 USD per mile`, resolved
`bedrock/converse/us.moonshotai.kimi-k3`, `fallback_used false`. Boundary both ways: staff refused with the
plain disclosure, executive answered 385,000.

## The eight scenes

The presenter's untimed error-finding pass ran clean on 2026-11-03. No scene failed and no defect was found in
that pass. Scene 7 was corrected during the review, see the finding below.

| # | Scene | Timed operation | Result |
|---|---|---|---|
| 1 | The deployment | not timed | clean |
| 2 | A policy answer | not timed | clean |
| 3 | The cited page | not timed | clean |
| 4 | Across the boundary | not timed | clean |
| 5 | The denial in the record | not timed | clean |
| 6 | The same question, permitted | not timed | clean |
| 7 | Export and hand over | not timed | clean after the backfill correction |
| 8 | Tamper and fail | not timed | clean |

The timed human-narration pass has not been run. It stays open.

## Findings

1. **`audit backfill` does not work on this deployment.** It reads `LiteLLM_SpendLogs` over the `oicore`
   connection, but this deployment keeps the gateway in its own `litellm` database, so the command exits with
   `UndefinedTable: relation "LiteLLM_SpendLogs" does not exist`. The export and verify proof is unaffected,
   and every model call on this box is written by the API. Scene 7 was corrected to drop `backfill`; the
   runbook and the limit note carry the reason. The proper fix is a separate spend-log connection for the
   audit tool, or a plain statement that `backfill` requires a shared database.
2. **A successful streamed ask records no question hash.** The JSON `/ask` path passes the prompt to the audit
   tool; the streamed path used by the C9 UI does not. Proposed together with the next item.
3. **A denial records the boundary, not the question.** The `access` row carries actor, role, roots, reason,
   `restricted_matched`, score, disclosure, endpoint and request id, but no question and no hash. Proposal:
   hash every question, and record the denial question under the content-mode policy behind a manifest
   switch; privacy notice and retention need deciding with it.
4. **`scripts/check_model_lifecycle.py` fails on this release tree.** The Kimi K3 entry was added to `main`
   after the release branch was cut. The renderer does not depend on it. Recorded rather than hidden.
5. **Serving pin and agent runtime are incompatible.** Found while building the product deployment: the pin's
   `tool_loop.py` predates the runtime's `strict_audit` argument. The C9 deployment does not run the agent
   runtime, so it is unaffected; the product deployment uses the release tree's serving files. Recorded in
   `docs/reviews/agentic-product-build-20261103/`.

## Evidence

- The run evidence was copied off the box to the workstation at
  `/home/anthony/projects/ai-stack/.scratch/c9-clean-20261030/evidence-offbox/` (five-run and section JSON,
  measurement summary, verify outputs, gates log, redacted env, runbook).
- The five-run summary is `eval-out/five-run/measurement-summary.json`; the section totals are in
  `logs/gates-*.log`.
- The audit export from the walkthrough day was not archived before this record; the chain verified at
  acceptance with the no-anchor note.

## Open items

1. Timed walkthrough pass with the narration timer, recorded per scene.
2. Local backup and restore rehearsal on this machine, with timings. Scripts are staged at
   `/home/ubuntu/staging/scripts/backup.sh` and `restore-drill.sh`; one backup exists from 2026-10-30.
3. Decide the audit backfill fix and the denial-question recording proposal.
4. Archive the walkthrough-day audit export.

## Limits

- No external anchor on this box; the verifier says tail completeness cannot be shown.
- The product test suite is not run with the pin. The pin predates the current tests.
- The export is one UTC day and includes acceptance rows when acceptance ran the same day.
- The demo box is the C0 to C5 cloud demo; the agentic product deployment is a separate record.
