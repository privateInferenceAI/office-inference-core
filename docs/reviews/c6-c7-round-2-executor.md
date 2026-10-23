# C6, C7 and C8 executor pass, round 2: verification

**Date:** 2026-10-23 (06:5x ET / 10:5x UTC). **Reviewer:** executor, round 2.
**Scope:** verify each round-one executor finding against the current working tree (branch docs/panel-round-1, HEAD b627ac2) and the live deployment. Build box i-02aba2feef08f8e7b (52.204.204.133, /opt/oi-core), client tenant i-0b6aa00095ae40e54 (3.85.130.96).
**Method:** re-ran the commands that failed or were changed. No code, manifest, rendered file or doc was changed; only this file was written. A fresh job was not needed, and the image scan was not re-run (its root-run output from round one is on the box); both choices are noted under limitations.

Round-one reports are docs/reviews/c6-c7-round-1-*.md. Each finding below says **fixed** or **still open**, with the evidence taken this round.

## Round-one findings, re-checked

### 1. C6.2 gateway tests with bare python3: documentation fixed, box-side execution still not possible

- **Round one:** python3 -m pytest tests/test_mcp_gateway.py -q failed (No module named pytest), and the deployed checkout had no tests/test_mcp_gateway.py.
- **Now:** runbook C6.2 line 5668 reads ~/venv/bin/python -m pytest tests/test_mcp_gateway.py -q. From the repository checkout at HEAD with a throwaway venv: **16 passed in 0.06s**, exit 0.
- **Still open, on the box only:** /home/ubuntu/venv does not exist (so the documented line on the box fails with No such file or directory), and /opt/oi-core/tests/test_mcp_gateway.py is still absent. The same is true of scripts/rotate_connector_secret.py (that script is used on the tenant, where it is present). The docs say the suite runs from a checkout; the deployment copy at /opt/oi-core is not a full checkout.
- **Status:** documentation **fixed**; the deployed-checkout gap is **still open** (residual, misleading).

### 2. Manual 26.4 endpoint check from the deployed checkout: fixed

- **Now:** /opt/oi-core/scripts/check_client_endpoint.py exists (synced 10:44 UTC). Running cd /opt/oi-core && python3 scripts/check_client_endpoint.py https://3-85-130-96.sslip.io prints client-owned: https://3-85-130-96.sslip.io, exit 0.
- **Status:** **fixed**.

### 3. C8.3 drift comparison with expected-state.json: fixed

- **Now:** the expected state is tracked at deploy/office/expected-state.json and is present on the box at /opt/oi-core/deploy/office/expected-state.json (997 bytes, manifest_sha256 20ce7cd221cf...). Both documented forms were run: python3 scripts/deployment_state.py --compose deploy/office/compose/docker-compose.yml --manifest rendered/manifest.json --compare deploy/office/expected-state.json prints no drift, exit 0; and the manual/current-deployment absolute-path form prints no drift, exit 0.
- **Status:** **fixed**.

### 4. C8.2 image scan permission: fixed

- **Now:** all four documents (runbook C8.2, manual 26.4, operator guide 17d, current-deployment Operate it) invoke it as sudo bash scripts/scan_images.sh /var/log/oi-image-scan. sudo -n works for the ubuntu user. /var/log/oi-image-scan is root-owned and holds 13 per-image JSON reports plus summary.json from the round-one root run; the numbers match ops-cve/summary.json exactly (litellm 1 critical, parser 5, postgres 1, other images 0).
- **Status:** **fixed**. (The scan itself was not re-run this round; see limitations.)

### 5. Tenant gateway registry contradicting the manifest: fixed

- **Now:** /home/ubuntu/client/mcp_tools.json, its copy inside client-mcp-gateway at /app/mcp_tools.json, and rendered/mcp_tools.json all agree: allowed_roles [viewer, analyst, admin], data_classes [public, internal, confidential, regulated], egress true, credential_file /app/rendered/connector.token.
- **Live probes** through https://3-85-130-96.sslip.io/call with freshly minted signed assertions (no caller fields):
  - analyst, data_class internal -> **200**, {ok: true, result: {sent: true, ... transport: smtp}}.
  - viewer, data_class confidential, egress_approved true -> **200**, sent true.
  - viewer, data_class confidential, no egress_approved -> **403** egress_gate, the frontier lane has not approved this egress.
  - Mailpit then held the two probe messages (round2 policy probe), so the connector really received them.
- **Status:** **fixed**.

### 6. C8.4 tabletop margin: fixed

- **Now:** docs/reviews/ops-incident/tabletop.md line 22 reads: The 72-hour clock is met with 70 hours and 50 minutes to spare. That is the correct margin for the first notice at T+70 minutes.
- **Status:** **fixed**.

### 7. Undocumented step0 indexer on the client tenant: fixed

- **Now:** current-deployment Where the pieces run has a row: Unrelated workload | 426063972668 | Same tenant, step0-hello-indexer (Midnight indexer) on port 8088 | Not part of this deployment; left running. Manual 26.5 also says it runs on the tenant on port 8088. The container is still running on the tenant (step0-hello-indexer, healthy), which is now documented.
- **Status:** **fixed** (documented as an unrelated workload, not part of the deployment).

### 8. Build-box container inventory: fixed

- **Now:** the stale services are gone. docker ps on the build box lists exactly eight containers: api app embeddings ingestion litellm parser postgres vector. That matches the operator guide (Eight containers run all the time on the build box) and current-deployment's build-box list (API, Postgres, LiteLLM, ingestion, retrieval, audit tool). The running mcp-gateway and oi-mailpit containers from round one are no longer present.
- **Status:** **fixed**.

## Additional checks made while verifying (not round-one findings)

- **Build-box instance id.** current-deployment and manual section 3 now name i-02aba2feef08f8e7b; a DescribeInstances filter on ip-address 52.204.204.133 in account 878451098165 returns i-02aba2feef08f8e7b (m7i.4xlarge, running). The round-one docs id was the older box. **Fixed.**
- **Migration 027 and its ledger row.** db/migrations/027_the_prompt_and_completion_are_in_the_chain.sql exists and is byte-identical to the copy in docs/reviews/aud-hash/ (sha256 5f85a82bae9ee3d13e7ab8bfe2fc520ce0ecf3f3c04d7dcf85e94584ce914afd). The Postgres ledger schema_migrations holds row 027 with that same sha256, applied 2026-10-23 10:44:31 UTC; rows 024 to 026 sit beneath it. **Verified** (this was the migration 027 in the ledger fix in the panel commit).

## Limitations of this round

- **No fresh job.** The C6 job path and C6.5 lane-on evidence were exercised in round one; the round-two question was the registry, which the direct /call probes answered without a job. The two probe sends did travel to the connector and were captured by Mailpit.
- **Image scan not re-run.** It takes about twelve minutes; the docs fix, the retained root-run output and the sudo check cover the finding. The round-one scan output is still at /var/log/oi-image-scan.
- **Destructive retention, deletion, purge, restore and teardown not run.**
- The working tree had one pre-existing uncommitted modification (docs/operator-guide.md) that this review did not touch.

## Result

All eight round-one executor findings are **fixed** in documentation and on the live deployment, with one residual: the deployment copy at /opt/oi-core still lacks tests/test_mcp_gateway.py and the box has no ~/venv, so the gateway test command cannot be run *on the box* even though it now runs correctly from a checkout (16 passed). Everything re-run this round produced the documented result.
