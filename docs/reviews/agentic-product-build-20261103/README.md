# Agentic product deployment, 2026-11-03

**Status:** built and verified on new infrastructure from the release artifacts. This is the deployment record
for the C6 agent runtime and WS workspace layers, the first deployment where all four packs are switched on
together. The C9 retrieval demo deployment is separate and was not modified.

## Deployment

| | |
|---|---|
| Product box | `i-0a3fca3c1141cbcad`, m7i.4xlarge, 16 vCPU / 64 GB, Elastic IP 100.60.208.224, root `/opt/oi-product` |
| Workspace host | `i-0cb950741232003bb`, m7i.xlarge, Elastic IP 100.60.62.166, root `/opt/oi-workspace`, Docker socket here only |
| Deployment id | `oi-core-product-20261103` |
| Packs | `core, document-chat, agentic-desk, workspace` |
| Release artifacts | `office-inference-core-release.tar.gz` sha256 `3494edf3c0922b0ac3e75a5a213e6f6c872308207aa2930a699c97741e4a4b9c`, `oic-ui-main.tar.gz` `99feddb75d262b751e347647c8551a435b72ca5e630b1a8c9e6665fcaedea43b`, bundle `84bd2a649b635110129e5febcef8b2920c17e485d798a4f1d4058bde91b1e1c1`, documents `e6718189f9894b632cb9cd0fb9a1a5441ec3ac153161e02203152ebdd916fe50` |
| Serving files | release tree's own: `tool_loop.py` `ee35c13c0a19d8a36ffc24287ac7ff81805361782975b88253bb434ff896278c`, `retriever.py` `2f34eefefe7715afeef7b6870ece8b8a122e684088f65f60ca82c0d8c3e365cd` |
| Corpus | `bundle-table-units-v2-20261028`, 80 documents, 10,576 chunks, chunker `chunker-canonical-v1`, Qdrant collection `documents-mixed-v2-20261028` |
| Analyst | `bedrock/converse/us.moonshotai.kimi-k3`, no fallback chain, `OI_MAX_TOKENS=2000` |
| Artifact bucket | `oi-core-workspace-artifacts-878451098165-product-20261103`, versioning, SSE-S3, 365-day lifecycle |
| Connector | real SES identity `anthony@officeinference.com`, send through the instance role |
| Workspace host IAM | `oi-core-workspace-host`: artifact bucket plus SSM only, per decision 0010 |
| Security group | `sg-05485a648e8baa4b8`: SSH from the operator address, 8090 between the two boxes |

The dedicated workspace host design in `docs/decisions/0010-workspace-sandbox.md` was accepted by the owner on
2026-11-03 and is what was built. The API box never holds the Docker socket.

## What was built

- A product manifest, `manifests/product-cloud.yaml`: the C9 serving configuration plus the `agentic-desk` and
  `workspace` packs and the MCP tool table (`send_email`).
- A product compose overlay, `deploy/product/docker-compose.product.yml`: keeps the parser, disables the native
  ingestion worker, adds the one-shot bundle importer, gives LiteLLM its own database, and passes the serving,
  workspace, egress, MCP and session-secret settings the base compose does not forward.
- A loopback-only port override for the app.
- On the product box, two systemd units: the allowlisted egress proxy on 8091 and the SES connector on 8092.
- On the workspace host, the workspace service as a container with the host Docker socket, its own task image
  and service image, and the artifact-only IAM role.
- The deployment scripts and units are tracked in `oi-canonical-stack` under `deploy/agentic-product` and
  `scripts/agentic-*`, commit `ca05cc3` and the pin-skip follow-up.
- Runbooks: `docs/runbooks/product-deployment.md` and `docs/runbooks/agentic-demo.md`.

## Defects found during the build and fixed

1. **The C9 serving pin is incompatible with the release-branch agent runtime.** The pin's `tool_loop.py`
   predates the runtime's `strict_audit` argument; every agent job failed in 5 ms with
   `TypeError: ToolBox.__init__() got an unexpected keyword argument 'strict_audit'`. The product deployment
   now uses the release tree's serving files; `agentic-product-prepare.sh` only applies the pin with
   `APPLY_SERVING_PIN=1`. Follow-up: forward-port the pin before one deployment carries both.
2. **The MCP gateway had no session secret.** The API signs a short-lived assertion with
   `OI_MCP_SESSION_SECRET`; the gateway reads `MCP_SESSION_SECRET`. The base compose passed the first and not
   the second, so every approved send was refused with `401 invalid_session`. The product overlay now passes it.
3. **The workspace service image did not copy `artifact_store.py`**, so a rebuilt service would fail on import.
4. **The workspace service image did not install boto3**, so S3 artifact upload failed with
   `ArtifactStoreError: artifact upload failed: ModuleNotFoundError`. Both image defects are fixed in
   `services/workspace/service.Dockerfile`.
5. The workspace host initially inherited the broad dev role; replaced with the artifact-only profile.

## Acceptance re-run

All jobs below ran on the product deployment against the Kimi K3 analyst. The October evidence used Claude
Sonnet 4.6, so this is a model substitution and the behaviour is re-proven rather than compared line for line.

| Row area | Evidence | Observable |
|---|---|---|
| C6 approvals gate (deterministic) | `evidence/c6-invoice-intake-gate.json`, `c6-invoice-intake-final.json` | `read_message`, `extract_fields`, `draft_action` then `awaiting_approval` with the ledger posting and digest; approve; `post_entry`, `close_out`; state succeeded |
| C6 approvals gate (agent runtime) | `evidence/ws-send-job.json` | `approval-4` gate holds the email with digest; approve; `act-4` sends; `turn-5`, `finish`; state succeeded |
| C6 runtime definition | `evidence/c6-research-brief.json` | `research_brief` finished, 3 turns, citations verified by the runtime rule |
| C6 child helper | `evidence/child-rows.txt` | `model_call` and two `search_documents` rows with span `child-1`, role `child`, under the parent job; job succeeded |
| C6 checkpoint and restart | `evidence/resume-row.txt`, `restart-job.json`, `chain-restart-verify.txt` | API restarted at step 1; `resume` row 193 at `turn-2`; job resumed and finished, 7 steps, chain intact |
| WS sandbox and tools | `evidence/ws-send-job.json`, audit rows | `workspace_write`, `workspace_read`, `workspace_run`, `workspace_list`, `web_fetch`, `search_documents` all ok |
| WS artifacts | `evidence/ws-send-artifacts.json`, `ws-send-brief.md` | `brief.md` 577 bytes, `note.txt`, `title.txt` stored in S3 (`store: s3`) and downloaded by hash; bucket versioned and AES256 |
| WS connector send | `evidence/ws-send-job.json` + audit row 175 | `send_email` outcome ok, `sent: true`, recipient `anthony@officeinference.com`, `transport: ses` |
| WS credential separation | infrastructure | workspace host profile can only touch the artifact bucket and SSM; the API box holds no Docker socket |
| WS cleanup | workspace smoke test | no task container left, task directory empty after cleanup |
| WS profile | `ws-profile-negative.txt` (laptop archive) | validator refuses the workspace pack on the office profile: "the workspace pack is cloud and hosted only, not 'office'" |
| Approval record | audit rows 146 and 171 | `approval` event rows with the digest; `payload_sha256` inside the chain |
| Boundary | audit row 132 | `access` denied, reason `boundary` |
| Chain | `chain-final-verify.txt`, `chain-send-verify.txt`, `chain-restart-verify.txt` | `chain intact` at 151, 182 and 212 events; no external anchor, so tail completeness is not shown |
| Unit suite | laptop archive `pytest-agentic.log` | 99 passed, 18 skipped across agent runtime, worker, queue, definitions, workspace service, connector and approval chain |

Not re-run live, recorded as such: `WS-LIMITS` typed limit stops, `WS-ARTIFACT` retention and cancellation
clauses, and the `WS-UI` browser flow. The workspace pack's profile refusal was re-run.

## Limits and open items

- The model is Kimi K3 here; the October evidence was Claude Sonnet 4.6.
- C7 (client-tenant gateway, TLS ingress, Cognito, Terraform) and C8 alarms are separate deployments and are
  not part of this box.
- The renderer still does not emit the agentic integration variables; the prepare script appends them. That is
  tracked but belongs in the renderer.
- No external anchor job is configured on this box.
- The implementation of the product deployment is not yet a Terraform module; `deploy/cloud/main.tf` remains a
  skeleton.
- The two integration defects (serving pin, MCP session secret) say the deployable product should include at
  least one agent job and one approved send in its own acceptance path, not only the retrieval demo.
