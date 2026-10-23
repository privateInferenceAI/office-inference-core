# C6, C7 and C8 executor pass, round 1

**Date:** 2026-10-23 (run 06:31 to 06:50 ET / 10:31 to 10:50 UTC). **Reviewer:** executor.
**Deployment:** build box `i-096bebd46308d1673` (account 878451098165, /opt/oi-core) and client tenant
`i-0b6aa00095ae40e54` (account 426063972668, 3.85.130.96).
**Scope:** manual sections 3 and 3a (context), 25 and 26; runbook Phase C6, C7 and C8; operator guide
current-state banner and Stage 17. Every documented command was run against the live deployment in the
order the documents give. No code, manifest, rendered file or other doc was changed. The only file written
is this one. Destructive retention, deletion, purge, restore and teardown procedures were not run, and the
connector token was not rotated (dry observation only, recorded below).

Findings are numbered. Each carries the section, the command, what the document says should happen, what
happened, and a severity from the acceptance plan: blocking, misleading, cosmetic.

## Findings

### 1. C6.2 gateway tests cannot run with the documented interpreter, and the deployed checkout has no test file

- **Section:** runbook C6.2 ("the gateway tests"); operator-guide convention in section 16 (tests run with
  `~/venv/bin/python` because the box has no pytest).
- **Command:** `cd /opt/oi-core && python3 -m pytest tests/test_mcp_gateway.py -q`
- **Expected:** the gateway tests run and pass (mcp-gateway README says 12 tests).
- **Actual:** `/usr/bin/python3: No module named pytest`, exit 1. Separately, `/opt/oi-core/tests/` contains
  no `test_mcp_gateway.py` (the deployed tests directory stops at `test_manifest_schema.py`; the file is in
  the repository HEAD). Run with a throwaway venv against the review checkout at HEAD:
  `16 passed in 0.06s`. The README's "12 tests" also no longer matches the file (16 test functions).
- **Severity:** blocking.

### 2. Manual 26.4 endpoint check cannot run from the deployed checkout

- **Section:** manual 26.4 ("endpoint ownership check"); mcp-owned README.
- **Command:** `cd /opt/oi-core && python3 scripts/check_client_endpoint.py https://3-85-130-96.sslip.io`
- **Expected:** `client-owned: https://3-85-130-96.sslip.io`, exit 0 (mcp-owned/endpoint-check.json).
- **Actual:** `python3: can't open file '/opt/oi-core/scripts/check_client_endpoint.py': [Errno 2] No such
  file or directory`, exit 2. The script exists in the repository HEAD and running it from the review
  checkout gives the documented output and exit 0. The negative control `https://mcp.officeinference.com`
  is refused, exit 1, as the evidence says.
- **Severity:** blocking.

### 3. C8.3 drift comparison cannot run: expected-state.json is not in the checkout

- **Section:** runbook C8.3; manual 26.4; operator guide 17d; current-deployment "Operate it".
- **Command:** `cd /opt/oi-core && python3 scripts/deployment_state.py --compose
  deploy/office/compose/docker-compose.yml --manifest rendered/manifest.json --compare expected-state.json`
  (also tried with the absolute paths from current-deployment/manual 26.4).
- **Expected:** `no drift`, exit 0 (ops-drift/drift.json `compare_clean`).
- **Actual:** `FileNotFoundError: [Errno 2] No such file or directory: 'expected-state.json'`, exit 1. There
  is no expected-state.json in the repository and none in /opt/oi-core. One exists on the box at
  `/home/ubuntu/expected-state.json`; comparing against that path explicitly prints `no drift` and exits 0,
  so the live state matches the expected state and only the documented path is wrong.
- **Severity:** blocking.

### 4. C8.2 image scan cannot write its documented output directory as the operator user

- **Section:** runbook C8.2; manual 26.4; operator guide 17d; current-deployment "Operate it".
- **Command:** `cd /opt/oi-core && bash scripts/scan_images.sh /var/log/oi-image-scan`
- **Expected:** one JSON report per image plus a summary (ops-cve README: 13 deployment images).
- **Actual:** `mkdir: cannot create directory '/var/log/oi-image-scan': Permission denied`; the script exits
  before scanning anything. The systemd unit runs it as root, but the documented operator command has no
  sudo. Re-run as root (the equivalent of what the timer does), the scan completes with exit 0 and writes
  13 per-image JSON reports plus summary.json; the summary matches ops-cve/summary.json exactly (litellm 1
  critical, parser 5, postgres 1; the same high and total counts on every image). Before the run,
  /var/log/oi-image-scan did not exist and the timer had never fired (LAST "-"; installed 05:38 UTC, next
  Mon 2026-10-26).
- **Severity:** blocking.

### 5. The production tenant gateway runs a hand-maintained registry that contradicts the manifest and the evidence

- **Section:** runbook C6.2 ("the registry is rendered from the manifest's mcp.tools block"); manual 26.2
  ("the gateway reads the tool registry rendered from the manifest's mcp.tools block"); mcp-egress README
  ("The gateway registry lists all four data classes"). Also C6.4/C6.5.
- **Command:** read `/home/ubuntu/client/mcp_tools.json` on the tenant and `docker exec client-mcp-gateway cat
  /app/mcp_tools.json`; compare with `/opt/oi-core/rendered/mcp_tools.json`; then POST to
  `https://3-85-130-96.sslip.io/call` with a correctly signed session assertion (minted inside the api
  container from `OI_MCP_SESSION_SECRET`).
- **Expected:** the tenant registry equals `rendered/mcp_tools.json`: `allowed_roles` [viewer, analyst,
  admin], `data_classes` [public, internal, confidential, regulated], `credential_file`
  /app/rendered/connector.token. An analyst-role send and a confidential-class send are both within policy.
- **Actual:** the tenant registry is `allowed_roles` ["viewer"], `data_classes` ["internal"],
  `credential_file` /app/connector.token (different content, different mount path). Live probes:
  analyst-role assertion with `send_email` → 403 `role_policy` "role 'analyst' does not hold 'send_email'";
  viewer assertion with `data_class: confidential` → 403 `data_class` "the tool does not accept
  'confidential' data". The manifest permits both. The signed assertion itself verified correctly, and
  `no session` → 401 and a caller-supplied `role` → 403 `refused_authority_claim` all matched the code and
  the evidence. The divergence is only in the registry.
- **Severity:** blocking (a security-relevant policy the documents say comes from the manifest is coming
  from a stale copy on the tenant).

### 6. C8.4 tabletop states the wrong notification margin

- **Section:** runbook C8.4; docs/reviews/ops-incident/tabletop.md, clock.json.
- **Command:** read the artifact and the clock.
- **Expected:** the runbook says the first client notice lands at 70 minutes against a 72-hour clock; the
  remaining margin is 70 hours 50 minutes.
- **Actual:** tabletop.md says "The 72-hour clock is met with 2 hours and 50 minutes to spare", which is
  68 hours short of the real margin. clock.json records `stated_hours: 72`, `first_client_notice_minutes:
  70`, `within_clock: true`; the conclusion (inside the clock) is right, the stated margin is not.
- **Severity:** misleading.

### 7. An unrelated container is running on the client tenant, undocumented

- **Section:** manual 3a and 26.1, current-deployment "Where the pieces run", operator guide 17a.
- **Command:** `ssh ubuntu@3.85.130.96 'systemctl is-active client-connector; docker ps --format "{{.Names}}
  {{.Status}}"'\)
- **Expected:** client-mcp-gateway, mailpit, caddy, and the connector unit (manual 3a, current-deployment).
- **Actual:** those four are present and healthy, plus `step0-hello-indexer`
  (`midnightntwrk/indexer-standalone:4.3.3`, compose project `step0-hello-devnet`, config
  /home/ubuntu/midnight/step0-hello/docker-compose.yml), restarting, up about a minute at the check, logs
  showing a DNS failure reaching its node. It listens on 8088 inside the tenant (not externally reachable;
  the external scan of 8088 was closed). An unrelated workload running on, or beside, a client tenant is
  exactly the sort of state the operator inventory should show.
- **Severity:** misleading.

### 8. The build-box inventory in the current docs is missing two running containers

- **Section:** current-deployment "Where the pieces run"; manual 3/3a; operator guide "The shape of the
  thing" ("Eight containers run all the time on the build box").
- **Command:** `docker ps` on `i-096bebd46308d1673`; `docker inspect mcp-gateway oi-mailpit`.
- **Expected:** the documented build-box pieces: API, Postgres, LiteLLM, ingestion, retrieval, audit tool
  (current-deployment), eight containers (operator guide).
- **Actual:** ten containers are running. In addition to the documented ones, `mcp-gateway`
  (`oi-core/mcp-gateway:local`, restart unless-stopped, part of
  /opt/oi-core/deploy/office/compose/docker-compose.yml, no published port) and `oi-mailpit` (bound
  127.0.0.1:1025 and 127.0.0.1:8025) are running. The API sets `OI_MCP_URL=https://3-85-130-96.sslip.io`,
  so the in-box gateway is not the one in the running path, but it is running and not in the inventory.
- **Severity:** misleading.

## Documented procedures that produced the documented result

| Section | Command (abbreviated) | Actual | Evidence reference |
|---|---|---|---|
| C6.3, C7.4, 26.4, 17a | `curl -sS https://3-85-130-96.sslip.io/health` | 200, `{"status": "ok"}` | sec-tls/https.json |
| C7.4, 17a | `curl -sS -I http://3-85-130-96.sslip.io/health` | 308, `Location: https://3-85-130-96.sslip.io/health` | sec-tls/https.json |
| Manual 3a, 26.1 | TLS certificate | Let's Encrypt, CN 3-85-130-96.sslip.io, valid 2026-10-23 to 2027-01-21 | sec-tls |
| C7.4, 3a | external port scan of 3.85.130.96 | 22, 80, 443 open; 3000, 4000, 5432, 8025, 8080, 8088, 8090, 8094 closed | sec-tls/port-scan.json |
| C6.3, 17a | `systemctl is-active client-connector` and `docker ps` on the tenant | connector `active` (main PID 7814, Restart=always); gateway/mailpit/caddy up, gateway restart unless-stopped | mcp-owned/tenant.json |
| C6.3 | gateway health on tenant loopback | 200, `{"status": "ok"}` | - |
| 17a | `curl -s http://127.0.0.1:8025/api/v1/messages` | 9 messages before the job, 10 after; newest "C6 review fresh job", agent@badj.ai to anthony@badj.ai | operator guide 17a |
| C6 (job path) | `POST /jobs` with session/task/input, approve at `POST /jobs/<id>/approvals`, read `GET /jobs/<id>` | job `1c90798f75bb44cf89b13e61c3a53baa`: queued, awaiting_approval with digest `sha256:92d847e3...`, approved, succeeded in 27.7 s with turn-1, approval-1, act-1, turn-2..turn-7, finish | mcp-gateway/job.json |
| C6.4 | secret parity and assertion path | `OI_MCP_SESSION_SECRET` sha256 on the API equals `MCP_SESSION_SECRET` sha256 in the tenant gateway (`7df72419...`); minted assertions verified; caller role refused 403 `refused_authority_claim`; no session 401 `no_session` | mcp-gateway/probe.json |
| C6.5 lane-off | `POST /jobs` with `data_class: regulated` | 403 `no_permitted_lane`, reason "the deployment permits ['public', 'internal'] on a remote lane, and this request is regulated" | mcp-egress/lane-off.json |
| C6.5 lane-on | fresh job's send | audit row 1258: `send_email`, sent true, data_class internal, egress_allowed true, redaction_count 0; Mailpit received the mail | mcp-egress/lane-on.json |
| C7.1 | `POST /ask` no token; valid session plus `role: executive` | 401 `no_session`; 403 `refused_authority_claim`; new audit rows 1285, 1286, 1287 record the attempt, the session role viewer and the attempted role executive | sec-auth |
| C7.2 | GetRole office-inference-support in account 426; AssumeRole from 878 | `NoSuchEntity`; `AccessDenied` (caller account 878451098165) | sec-least |
| C7.5 | GetObjectLockConfiguration oi-core-audit-dev | Object Lock Enabled, GOVERNANCE, 90 days; public access block all on; versioning Enabled | aud-lock/bucket-config.json |
| C8.1 | `docker exec api python /app/audit.py export --out /tmp/chain` and `verify` | exported 948 events; `ok ... 948 event(s), chain intact` plus the two documented notes; 913 rows chain version 3 and 35 rows version 4 | aud-hash |
| C8.1 | edit approval_sha256, prompt_sha256, completion_sha256 in copies | each exits 1 with `row_hash does not match the row contents` (+ "the file hash does not match the manifest") | aud-hash/tamper-results.json |
| C8.2 | `systemctl list-timers oi-image-scan.timer --no-pager` | NEXT Mon 2026-10-26 00:00:00 UTC, LEFT 2 days, weekly, enabled | ops-cve/timer.txt |
| C8.2 | LiteLLM pin and running version | compose comment "LiteLLM v1.104.2, pinned 2026-10-10"; container reports 1.104.2 | ops-cve/litellm-version.txt |
| C8.2 | `sudo bash scripts/scan_images.sh /var/log/oi-image-scan` (the documented command without sudo fails, finding 4) | exit 0, 13 image reports plus summary.json; numbers match ops-cve/summary.json exactly (litellm 1 critical, parser 5, postgres 1) | ops-cve/summary.json |
| C8.3 | drift compare with the expected file at its real path | `no drift`, exit 0 | ops-drift/drift.json |
| C8.4 | runbook and tabletop exist; the two recorded gaps are the ones in the runbook | present and consistent; see finding 6 for the clock arithmetic | ops-incident |
| 26.5 | caveats 1 to 3 | Mailpit captures and does not deliver; tenant has no Elastic IP (only EIP 52.71.58.92, attached to i-01fd7e6c62bb0c36e); `DEEPSEEK_API_KEY` present in mode-600 /opt/oi-core/rendered/env | current-deployment |
| 26.6, 17f, C8.5 | open C8 rows and BIZ rows | the list matches the current-deployment table (alarms, backup/restore, RTO, update/rollback, SSM-only, cost, golden questions, acceptance, load, BIZ) | current-deployment |
| current-state banner | C6/C7 built, C8 partly built, Stage 17 exists | matches the live deployment and docs/current-deployment.md | - |

## Procedures not executed, and why

- **C7.3 and operator guide 17b, connector token rotation.** Not run: rotating the live secret is outside a
  read-only review. Dry observation only: the secret `oi-core/connector-token` exists in account
  `426063972668` (ARN ends `-GdnTVu`, LastChanged 2026-10-23T05:24:01Z, automatic rotation not enabled);
  `rotate_connector_secret.py` on the tenant is byte-identical to the repository HEAD (sha256
  `e598fffa...`), as are `email_connector.py` and `gateway.py`; token and env files are mode 600; the token
  file is mounted read-only into the gateway; the gateway reads it per call; boto3 1.34.46 is installed;
  an invalid token to the connector returns 401 `{"error":"unauthorised"}`. The documented old-401 /
  new-200 sequence was not re-run.
- **C8.1 anchor check.** The documented export/verify used no anchor; the verifier printed the documented
  note that completeness cannot be shown without one. That is expected, not a failure.
- **Destructive retention, deletion, purge, restore, teardown.** Not run, per the plan.

## Environment notes

- The deployed checkout at /opt/oi-core is not a git repository and is missing files that are in the
  repository HEAD (`scripts/check_client_endpoint.py`, `scripts/rotate_connector_secret.py`,
  `tests/test_mcp_gateway.py`, and everything in the tests directory after `test_manifest_schema.py`). It
  also has no expected-state.json. Findings 1, 2, 3 and 4 all trace to that gap between the document's
  "checkout on the box" and what is actually deployed there.
- The tenant gateway health response carries `Server: BaseHTTP/0.6 Python/3.12.15` through Caddy; the build
  box API health returns `{"status":"ok","deployment_id":"oi-core-dev-cloud-workspace","registry_declared":
  true,"default_model_role":"analyst"}`.
- Review artifacts on the boxes were left undisturbed. Temporary exports are under /tmp in the api
  container (/tmp/chain, /tmp/chain2, /tmp/chain/tampered-*.jsonl); the review venv is /tmp/oi-review-venv
  on the laptop. No repository file other than this one was written.
