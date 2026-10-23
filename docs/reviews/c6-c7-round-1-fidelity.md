# C6–C8 docs panel, round 1: fidelity checker

**Date:** 2026-10-23. **Reviewer:** fidelity checker. **Round:** 1, C6 to C8 docs update.

Scope: manual sections 3a and 26 (sections 3 and 25 as context), runbook Phase C6.1 to C8.5, operator
guide current-state banner and Stage 17, docs/current-deployment.md. Method: docs/reviews/acceptance-plan-post-c5.md.
Every claim below was checked against the code and the live deployment, read-only.

**Live targets.** Build box reached at 52.204.204.133; the instance that answers there is
**i-02aba2feef08f8e7b** (m7i.4xlarge, account 878451098165). Client tenant is **i-0b6aa00095ae40e54** at
3.85.130.96, t3a.xlarge, account 426063972668. No destructive command was run; no file outside this
findings file was changed.

**Verdict: the C6/C7 claims are mostly supported by live evidence, but the docs do not yet describe the
deployment accurately.** Three blocking findings are commands that cannot succeed as written; six more
findings are wrong identifiers, wrong exposure descriptions, or evidence that overstates a failed job.
Fix the blocking three before round 2.

## Blocking

### F1. The documented drift command cannot run: `expected-state.json` does not exist in the checkout

- **Pages:** docs/current-deployment.md "Operate it" (drift command); docs/manual.md §26.4; docs/runbook.md C8.3;
  docs/operator-guide.md Stage 17d.
- **Claim:** `python3 scripts/deployment_state.py --compose /opt/oi-core/deploy/office/compose/docker-compose.yml
  --manifest /opt/oi-core/rendered/manifest.json --compare expected-state.json` reports "no drift" or exits 1 on
  drift. Runbook C8.3: "Keep the expected state with the release and compare."
- **Evidence (run on the build box, 2026-10-23):**
  ```
  $ cd /opt/oi-core && python3 scripts/deployment_state.py --compose /opt/oi-core/deploy/office/compose/docker-compose.yml \
      --manifest /opt/oi-core/rendered/manifest.json --compare expected-state.json
  FileNotFoundError: [Errno 2] No such file or directory: 'expected-state.json'
  EXIT=1
  ```
  `git ls-files | grep expected-state` is empty: the file is not in the repository either. The only copy on the
  box is `/home/ubuntu/expected-state.json`; run from there with that copy the same script prints `no drift`
  and exits 0, so the script and the state are fine. The documented path/CWD is what fails. Stage 17d is
  explicit: `cd /opt/oi-core` first, so there is no documented working directory where this command succeeds.
- **Severity: blocking.** A documented operator cannot run the drift check as written. Fix either by adding the
  expected-state file to the release and saying where it lives, or by documenting the absolute path.
- **Supported:** the comparison itself works ("no drift", exit 0) when pointed at the copy under
  `/home/ubuntu`. The drift-detection claim in C8.3 is otherwise consistent with docs/reviews/ops-drift/.

### F2. The §26.4 endpoint ownership check is not in the deployment's checkout

- **Page:** docs/manual.md §26.4, last command: `python3 scripts/check_client_endpoint.py https://3-85-130-96.sslip.io`.
- **Claim:** the ownership check runs from the checkout.
- **Evidence (build box):** `python3 scripts/check_client_endpoint.py ...` → `python3: can't open file
  '/opt/oi-core/scripts/check_client_endpoint.py': [Errno 2] No such file or directory`, exit 2. The script is
  committed in the repository and works from a full checkout: on the laptop clone it prints
  `client-owned: https://3-85-130-96.sslip.io` and exits 0, so the check's logic is supported. The deployed
  `/opt/oi-core` checkout is behind HEAD: it also lacks `docs/current-deployment.md` and
  `db/migrations/027_…` (see F7).
- **Severity: blocking** for an operator working on the box, which is how the manual's §26.4 block is written
  (tenant health, rotation, drift, scan are all box or tenant commands). Fix: sync the deployment checkout to
  the release, or run this check from a release checkout and say so.

### F3. The image-scan command cannot finish as the operator user

- **Pages:** docs/current-deployment.md; docs/manual.md §26.4; docs/runbook.md C8.2; docs/operator-guide.md Stage 17d.
- **Claim:** `bash scripts/scan_images.sh /var/log/oi-image-scan` runs the scan and writes the reports.
- **Evidence (build box):** the output directory is `drwxr-xr-x root root` and the ubuntu user cannot write it:
  `test -w /var/log/oi-image-scan` → NOT_WRITABLE; writing `summary.json` raises
  `PermissionError: [Errno 13] Permission denied`. The script creates the directory if missing but also runs
  its own summary python as the invoking user (`scripts/scan_images.sh`, last heredoc), so an unprivileged run
  scans every image and then fails on the summary. The systemd timer runs it as root, which is why the reports
  in `/var/log/oi-image-scan` exist (they were written 2026-10-23 10:38). No page says `sudo`.
- **Severity: blocking** as written (the command exits non-zero after a long scan). Fix: add `sudo`, or point
  the command at a writable directory, or document that this one is the timer's command.

## Misleading

### F4. The build box instance id is stale, and the page contradicts its own SSH command

- **Pages:** docs/current-deployment.md "Where the pieces run"; docs/manual.md §3 table row "The machine".
- **Claim:** the build box is `i-096bebd46308d1673` (m7i.4xlarge, account 878).
- **Evidence:** the live API/Postgres/LiteLLM box is `i-02aba2feef08f8e7b` (m7i.4xlarge, account 878451098165,
  running, public 52.204.204.133; IMDS `instance-id` read on the box). `i-096bebd46308d1673` is **stopped**
  with public IP 52.204.202.66 (EC2 DescribeInstances, account 878). current-deployment's own API health command
  SSHes to 52.204.204.133, which is the new instance, so the page is internally inconsistent. The fresh-install
  record already knows this instance as `i-02aba2feef08f8e7b` (docs/reviews/fresh-install-2026-10-22).
- **Severity: misleading** (an operator checking or stopping "the build box" by id acts on the wrong instance).

### F5. "Loopback only" is wrong for the connector and Mailpit

- **Pages:** docs/current-deployment.md "Where the pieces run" (connector 8094, Mailpit 1025); docs/manual.md §3a
  table (same two rows); docs/manual.md §26.1 ("Caddy is the only public listener … the gateway is on loopback
  8080, the connector on 8094 and Mailpit on 1025").
- **Claim:** the connector and the SMTP capture listen on loopback only.
- **Evidence (tenant, `ss -ltnp`):** gateway **is** loopback (`127.0.0.1:8080`, host-network container);
  but the connector listens on `0.0.0.0:8094` (`python3 email_connector.py`) and Mailpit listens on
  `*:1025` and `*:8025`. The external exposure claim elsewhere ("ports … closed externally") is true only
  because security-group ingress is just 22/80/443 (verified by scan: 3000, 4000, 5432, 8025, 8080, 8088, 8090,
  8094 closed; 22/80/443 open). Anyone reading the doc would believe the bindings themselves are restricted.
- **Severity: misleading** (wrong security posture; the external closure is an SG property, not a binding one).

### F6. The C7 TLS evidence job did not succeed, and neither did the mcp-owned follow-up

- **Pages:** docs/runbook.md C7.4 ("A job sent through the HTTPS gateway; audit row 1242"); the same job is the
  TLS evidence in docs/reviews/sec-tls/README.md. Docs/current-deployment cites mcp-owned for MCP-OWNED-001.
- **Claim:** a job went through the HTTPS gateway; audit row 1242 records `sent: true`.
- **Evidence:** row 1242 does exist and says `sent: true` (verified in Postgres). But job
  `57309daac87244d9acea35f4f65dbffb` is `state = failed`, `outcome_reason = verification` in the live
  `jobs` table. The mcp-owned follow-up job `43dfdbbd5d5b465489b165013a442d0a` (row 1204) is also
  `failed / verification`. Both sends happened; the overall jobs did not complete.
- **Severity: misleading** (the send/TLS claim is supported by the row, but the docs present these as working
  job runs; the failed final state is not recorded anywhere in the C7 text). Say "the act step sent; the job
  later failed verification" or cite a succeeded job (`aa1b739255744e6f98c1cb1a38bed610`, row 1190, succeeded).

### F7. Migration 027 is neither in the deployment's migrations directory nor in its ledger

- **Pages:** docs/current-deployment.md "Audit record"; docs/manual.md §26.3; docs/runbook.md C8.1.
- **Claim:** `Migration 027_the_prompt_and_completion_are_in_the_chain.sql added it` (chain version 4) without
  rewriting old rows.
- **Evidence:** on the build box `/opt/oi-core/db/migrations` ends at
  `026_context_and_resume_events.sql`; the live `schema_migrations` ledger's newest row is 026 applied
  2026-10-23 00:51. The v4 trigger and canonical function **are** live (`audit_events_chain` sets
  `chain_version := 4`; `audit_row_canonical` has the v4 branch), and v4 rows run from id 1250. So the
  system does not support the sentence as written: the named migration is not present in the deployed
  checkout and was not applied through the migration runner it documents. A fresh `audit.py migrate` against
  this checkout would not apply it.
- **Severity: misleading.** The outcome (chain version 4, old rows untouched) is supported; the account of how
  it was added is not. Either sync the migration file to the box and apply it through the runner, or say the
  functions were applied directly.

### F8. The build box runs more than "where the pieces run" says, including its own gateway and connector

- **Pages:** docs/current-deployment.md "Where the pieces run" (build-box row lists only API, Postgres, LiteLLM,
  ingestion, retrieval, audit tool); docs/manual.md §3a (same) and §3 ("No container publishes a port … The
  machine itself listens on one port, 22").
- **Evidence (build box):** `docker ps` shows ten containers, including `mcp-gateway` (a compose service with
  no profile, so it starts by default) and `oi-mailpit` publishing `127.0.0.1:8025` and `127.0.0.1:1025`;
  `ss -ltn` shows four host services on `0.0.0.0:8090–8093` (workspace, egress proxy, email connector, MCP
  gateway). The tenant-side gateway is the one the API uses (`OI_MCP_URL=https://3-85-130-96.sslip.io` in the
  api container), so these are not the production path — but the pages say the gateway, connector and Mailpit
  are tenant pieces and that no container publishes a port, which the live box contradicts.
- **Severity: misleading.** Externally the box is closed (SG allows 22 only from one address), so this is a
  description error, not an exposure. The topology tables should name the build-box duplicates or the extra
  services should be stopped before the docs call the box "no public port".

## Cosmetic

### F9. The session secret has one name in the manual and another on the tenant

- **Page:** docs/manual.md §26.1: "The signing secret is `OI_MCP_SESSION_SECRET` on both sides."
- **Evidence:** the api container has `OI_MCP_SESSION_SECRET`. The tenant gateway container runs with
  `MCP_SESSION_SECRET` (read in `gateway.py` as `os.environ.get("MCP_SESSION_SECRET")`). The secret works
  (assertions pass), so this is naming only; but §C6.4 says "rotate it … update both sides", and a rotation
  reader will not find the documented name on the tenant.
- **Severity: cosmetic.**

### F10. The operator guide points at Stage 17 for a model list that is not there

- **Page:** docs/operator-guide.md "What you need": "Production uses Nova Micro, Nova Lite and Nova Pro … The
  manual's section 3 and Stage 17 list them."
- **Evidence:** Stage 17 (17a–17f) never names a model. Manual §3 does ("four models, Nova and DeepSeek" —
  matches the live manifest: front_desk Nova Micro, analyst Nova Lite + Nova Pro fallback, reader Nova Pro,
  coder `bedrock/deepseek.v3.2`).
- **Severity: cosmetic.**

## Claims checked and supported

These were checked against the live deployment or the code and hold:

- **Tenant address and account.** https://3-85-130-96.sslip.io resolves to 3.85.130.96; instance
  `i-0b6aa00095ae40e54` (t3a.xlarge) is in account 426063972668, as the pages say. `curl …/health` → 200
  `{"status":"ok"}`; plain HTTP → **308** to the HTTPS URL; Let's Encrypt certificate, SAN
  `DNS:3-85-130-96.sslip.io`, valid 2026-10-23 to 2027-01-21.
- **Tenant exposure.** Security-group ingress is exactly 22, 80, 443. External scan: 22/80/443 open; 3000,
  4000, 5432, 8025, 8080, 8088, 8090, 8094 (and 8091–8093) closed. Caddyfile is
  `https://3-85-130-96.sslip.io { reverse_proxy 127.0.0.1:8080 }`. Gateway is loopback 8080 (host-networked
  container); connector systemd unit `client-connector` is active; Caddy, gateway and Mailpit containers are up.
- **Mailpit captures, does not deliver.** Connector env: `SMTP_HOST=127.0.0.1`, `SMTP_PORT=1025`,
  `SMTP_FROM=agent@badj.ai`; `curl 127.0.0.1:8025/api/v1/messages` returned 10 captured messages.
- **Credential and rotation.** `/home/ubuntu/client/connector.token` and `connector.env` are mode 600;
  `rotate_connector_secret.py` exists and is executable; gateway reads the file per call (code and
  docs/reviews/sec-secret evidence).
- **API calls the tenant gateway.** `OI_MCP_URL=https://3-85-130-96.sslip.io` in the api container; the API has
  `OI_MCP_SESSION_SECRET`; no VPC peering exists from the tenant account (DescribeVpcPeeringConnections: 0).
- **Audit chain version 4 works.** Live trigger writes version 4; rows 1250–1288 are v4, older rows keep
  versions 2 and 3. Row 1250 carries approval, prompt and completion hashes. `docker exec api python
  /app/audit.py export-verify` → `ok … chain intact`; the tamper failure mode is recorded in
  docs/reviews/aud-hash/tamper-results.json for all three columns.
- **Cited audit rows exist and say what the docs say.** 1070 no session, 1071 invalid session, 1072 authority
  claim, 1073 undeclared tool `wire_money`, 1074 data class, 1083 sent true, 1090 role policy, 1091 expired
  session (detail "the session has expired"), 1148 lane-on sent true, 1190/1204 sent true, 1218/1219/1220,
  1228 rotation, 1242 TLS. Jobs 12479c11…, b36f84f1…, aa1b7392…, 4795a993… exist and succeeded.
- **Object Lock.** `oi-core-audit-dev` in account 878451098165: enabled, GOVERNANCE, 90 days, matching
  docs/contracts/audit-evidence-retention.md.
- **Image scan plumbing.** `oi-image-scan.timer` installed, next run Mon 2026-10-26 00:00 UTC; reports under
  `/var/log/oi-image-scan`; docs/reviews/ops-cve/ current critical findings match what the pages say.
- **LiteLLM pin and version.** Compose records "LiteLLM v1.104.2, pinned 2026-10-10"; running container reports
  1.104.2.
- **Suite count.** `python3 -m pytest tests/ -q --collect-only` at HEAD collects **1,356 tests**, consistent
  with 1,355 passed + 1 skipped. I did not have time in this round to run the full suite; the count is
  supported, the pass result is not re-verified here.
- **DeepSeek caveat.** `DEEPSEEK_API_KEY` is present in the mode-600 `/opt/oi-core/rendered/env` and not in the
  repository; production model routing in `rendered/litellm.yaml` is all Bedrock (Nova Micro/Lite/Pro, plus
  bedrock/deepseek.v3.2 for the coder role).

## Could not verify in this round (not findings, just open checks)

- Full suite run on the box: the box has no `~/venv` (manual §22 documents `~/venv/bin/python -m pytest`), so
  the suite could not be run there in the timebox; the count check above was done from the laptop checkout.
- Live connector 401/200 rotation replay and the EIP inventory in account 426 (no AWS CLI on the tenant);
  both are consistent with the recorded evidence in docs/reviews/sec-secret and the bucket check.

## What has to change before round 2

1. Fix F1–F3 so the three documented commands run as written (expected-state file, synced checkout, writable
   scan output or sudo).
2. Correct the instance id (F4) and the loopback claims (F5); record the failed job state honestly (F6).
3. Reconcile the migration record (F7) and decide whether the build-box duplicates belong in the topology
   tables (F8).
