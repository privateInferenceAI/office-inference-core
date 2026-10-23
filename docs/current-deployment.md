# Current deployment state

**As of:** 2026-10-23. This page is the single current snapshot. The manual, runbook and operator guide
describe how to build and operate the system; where they disagree with this page, this page is newer.

## Phase state

| Phase | State |
|---|---|
| C1 to C5 | Built and verified |
| C6 | Built and verified. Agent runtime, full MCP gateway, client-tenant gateway |
| C7 | Built and verified. Authenticated sessions, least-privilege support role, secret rotation, TLS, Object Lock |
| C8 | Partly built. Audit hashes, image scanning, drift reporting, the incident tabletop, backup and the restore/RTO drill, the 20-concurrent load test, and the signed release with a tested rollback are verified. Five alarms are built and awaiting the email subscription confirmation. SSM-only support, cost reporting, golden questions, acceptance on a fresh deployment and the business rows are open |
| C9 | Not started |

The full suite is **1355 passed, 1 skipped** as of the last run on 2026-10-23.

## Where the pieces run

| Piece | Account | Host | Exposure |
|---|---|---|---|
| API, Postgres, LiteLLM, ingestion, retrieval, audit tool | 878451098165, Office Inference | Build box `i-02aba2feef08f8e7b`, 52.204.204.133 | No public port. The API is reached from inside the box or its containers |
| MCP gateway | 426063972668, badj.ai client tenant | `i-0b6aa00095ae40e54`, `34.225.62.245` | HTTPS only through Caddy at `https://mcp.badj.ai`. Gateway listens on loopback, 8080 |
| Tenant state | 426063972668, badj.ai client tenant | Instance stopped 2026-10-23; Elastic IP and DNS retained | Test fixture frozen to stop compute billing; thaw with docs/reviews/client-tenant-freeze/ |
| Email connector | 426063972668 | Same tenant, systemd `client-connector`, port 8094 | Loopback only |
| Email delivery | 426063972668 | Same tenant, SES identity awstest@officeinference.com | Sends through SES; the account is still in the sandbox |
| Mailpit fallback | 426063972668 | Same tenant, port 1025 | Capture only; retained as a fallback |
| Unrelated workload | 426063972668 | Same tenant, `step0-hello-indexer` (Midnight indexer) on port 8088 | Not part of this deployment; left running |
| Caddy | 426063972668 | Same tenant | 80 and 443, public |
| Audit bucket | 878451098165 | S3 `oi-core-audit-dev` | Object Lock, GOVERNANCE, 90 days |

The gateway and connector are in the client tenant. The API calls the gateway at
`OI_MCP_URL=https://mcp.badj.ai`. The tenant has no database route back to account 878: the API
mints a short-lived HMAC session assertion and the gateway verifies it. The gateway still refuses a
caller-supplied role.

The connector now sends through SES from awstest@officeinference.com to the same verified address. The badj
SES account is still in the sandbox, so arbitrary recipients need production access. Mailpit is retained as a
capture fallback, not the delivery path.

The tenant gateway has no `AUDIT_TOOL` and no route to the Office audit database. Its refusals are returned to
the API but are not written to the Office chain. That is a known gap in the client-tenant deployment, not a
claim the docs should make. Refusals that run on the build box are still recorded.

## Network and TLS

- Security group ingress on the tenant: 22, 80, 443.
- Caddy obtains a Let's Encrypt certificate for `mcp.badj.ai`, which resolves to the tenant address.
- Plain HTTP returns 308 to HTTPS.
- Ports 3000, 4000, 5432, 8025, 8080, 8088, 8090 and 8094 are closed externally.

## Models

Production routing is still Bedrock in account 878: `front_desk` Nova Micro, `analyst` Nova Lite, `reader`
Nova Pro, with an analyst fallback. The renderer supports an OpenAI-compatible provider and it was tested live
against DeepSeek (`https://api.deepseek.com`, model `deepseek-flash`) with the same three policy questions
answered correctly on both providers. The DeepSeek key lives in AWS Secrets Manager as deepseek-oi-key; the box
materializes it into the mode-600 rendered env. It is not in the repository.

## Audit record

Chain version 4. The canonical hash covers the approval digest, the prompt digest and the completion digest.
Migration `027_the_prompt_and_completion_are_in_the_chain.sql` added version 4 without rewriting old rows.
Editing any of those three columns fails verification. The export now carries prompt and completion hashes.

## Operate it

```bash
# Tenant health, from anywhere that can reach the public name
curl -s https://mcp.badj.ai/health

# API health, from the build box
ssh -i ~/ai-stack/ai-stack-key.pem ubuntu@52.204.204.133 'docker exec api python -c "import urllib.request; print(urllib.request.urlopen(\"http://127.0.0.1:8088/health\").read().decode())"'

# Rotate the connector token from Secrets Manager; no redeploy
ssh -i ~/ai-stack/ai-stack-key.pem ubuntu@34.225.62.245 'cd /home/ubuntu/client && sudo AWS_DEFAULT_REGION=us-east-1 python3 rotate_connector_secret.py \
  --secret-id oi-core/connector-token \
  --token-file /home/ubuntu/client/connector.token \
  --env-file /home/ubuntu/client/connector.env --service client-connector'

# Deployment state and drift, from the checkout on the box
python3 scripts/deployment_state.py --compose /opt/oi-core/deploy/office/compose/docker-compose.yml \
  --manifest /opt/oi-core/rendered/manifest.json --compare deploy/office/expected-state.json

# Weekly image scan, installed as oi-image-scan.timer
sudo bash scripts/scan_images.sh /var/log/oi-image-scan
```

Job runs are unchanged: create a job with `POST /jobs`, approve at the gate with
`POST /jobs/<id>/approvals`, then read `GET /jobs/<id>`. The runtime sends through the MCP gateway, which is in
the client tenant.

## Caveats an operator must know

1. **Mail is delivered through SES only to the verified address.** awstest@officeinference.com is verified in
   the badj account; the account is in the SES sandbox. Arbitrary client recipients need SES production access.
   Mailpit is a fallback capture service.
2. **The Elastic IP 34.225.62.245 is associated.** The public API name is mcp.badj.ai, an A record at Porkbun.
   Caddy has a certificate for it and scripts/check_client_endpoint.py reports it as client-owned. The earlier
   mcp.officeinference.com name was deleted from DNS and removed from Caddy; it is not an alias any more.
3. **The DeepSeek key comes from Secrets Manager.** The secret is deepseek-oi-key in account 878. The old key was
   replaced, the LiteLLM environment restarted, and the owner revoked the old key on 2026-10-23.
4. **The three critical image findings are patched.** Postgres gosu, LiteLLM certifi and parser certifi/Pillow
   were upgraded on 2026-10-23 and the three patched images scan with zero criticals. High findings remain and
   are not waived. Evidence: docs/reviews/ops-cve/upgrade-2026-10-23/.
5. **Backup and the restore/RTO drill exist.** The bucket is in account 878, daily at 02:00 UTC. The measured
   state restore was 48 seconds and the full drill 15 minutes 42 seconds against a 4-hour RTO. The backup target
   is not client-owned yet. No alarm, rollback, SSM-only support, cost report, golden question set or
   fresh-deployment acceptance exists yet.
6. **The 20-concurrent load test now passes.** The fix moves the blocking retrieval, model loop and audit write
   into the thread pool. Five repeated runs improved from p95 80.8 seconds to a worst p95 of 7.8 seconds, with
   zero failures and a 9.8-second maximum. The before and after are in docs/reviews/ops-load/.
7. **Tenant refusals are now recorded by the API.** The tenant gateway still has no database route, but when it
   returns a 403 the API writes a denied tool_call row with the refusal reason. The live tenant path has not been
   re-exercised since the tenant was stopped; the code and chain behavior are on the dev box.
8. **An unrelated Midnight indexer, step0-hello-indexer, runs on the tenant on 8088.** It is not part of this
   deployment.

## Open rows, with what blocks them

| Row | What it needs |
|---|---|
| OPS-ALARM-001 | Five alarms and the SNS topic are built; the anthony@officeinference.com subscription is awaiting confirmation |
| OPS-SUPPORT-001 | Approval to remove SSH and cut the client box to SSM only |
| OPS-COST-001 | Cost Explorer / Marketplace report access and a decision on the report shape |
| QLT-GOLDEN-001 | A drafted 60-question medical set exists at docs/eval/medical-golden-questions.draft.yaml. Clinical review, loading and a hard-fail run are still required |
| QLT-ACCEPT-001 | A fresh deployment to run acceptance against |
| BIZ-* | Counsel, insurance, contracts, on-call staff, subprocessors and pricing decisions |

## Evidence

The phase and row evidence lives under `docs/reviews/`. The C6 gate is in `docs/reviews/ws-gate/`; the MCP
gateway in `docs/reviews/mcp-gateway/`; the client tenant in `docs/reviews/mcp-owned/`; C7 rows in
`docs/reviews/sec-*`, `docs/reviews/aud-lock/`; C8 rows in `docs/reviews/aud-hash/`,
`docs/reviews/ops-cve/`, `docs/reviews/ops-drift/` and `docs/reviews/ops-incident/`.
