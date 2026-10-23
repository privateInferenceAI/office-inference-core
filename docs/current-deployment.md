# Current deployment state

**As of:** 2026-10-23. This page is the single current snapshot. The manual, runbook and operator guide
describe how to build and operate the system; where they disagree with this page, this page is newer.

## Phase state

| Phase | State |
|---|---|
| C1 to C5 | Built and verified |
| C6 | Built and verified. Agent runtime, full MCP gateway, client-tenant gateway |
| C7 | Built and verified. Authenticated sessions, least-privilege support role, secret rotation, TLS, Object Lock |
| C8 | Partly built. Audit hashes, image scanning, drift reporting and the incident tabletop are verified. Alarming, backup and restore, rollback, SSM-only support, cost reporting, golden questions, acceptance on a fresh deployment, load test and the business rows are open |
| C9 | Not started |

The full suite is **1355 passed, 1 skipped** as of the last run on 2026-10-23.

## Where the pieces run

| Piece | Account | Host | Exposure |
|---|---|---|---|
| API, Postgres, LiteLLM, ingestion, retrieval, audit tool | 878451098165, Office Inference | Build box `i-096bebd46308d1673` | No public port. The API is reached from inside the box or its containers |
| MCP gateway | 426063972668, badj.ai client tenant | `i-0b6aa00095ae40e54`, `3.85.130.96` | HTTPS only through Caddy at `https://3-85-130-96.sslip.io`. Gateway listens on loopback, 8080 |
| Email connector | 426063972668 | Same tenant, systemd `client-connector`, port 8094 | Loopback only |
| SMTP capture | 426063972668 | Same tenant, Mailpit port 1025 | Loopback only |
| Caddy | 426063972668 | Same tenant | 80 and 443, public |
| Audit bucket | 878451098165 | S3 `oi-core-audit-dev` | Object Lock, GOVERNANCE, 90 days |

The gateway and connector are in the client tenant. The API calls the gateway at
`OI_MCP_URL=https://3-85-130-96.sslip.io`. The tenant has no database route back to account 878: the API
mints a short-lived HMAC session assertion and the gateway verifies it. The gateway still refuses a
caller-supplied role.

The connector's SMTP target is **Mailpit**, which captures mail instead of delivering it. That is deliberate
for the ownership and gateway tests. A production connector must point at the client's SMTP server or a
verified SES identity.

## Network and TLS

- Security group ingress on the tenant: 22, 80, 443.
- Caddy obtains a Let's Encrypt certificate for `3-85-130-96.sslip.io`, which resolves to the tenant address.
- Plain HTTP returns 308 to HTTPS.
- Ports 3000, 4000, 5432, 8025, 8080, 8088, 8090 and 8094 are closed externally.

## Models

Production routing is still Bedrock in account 878: `front_desk` Nova Micro, `analyst` Nova Lite, `reader`
Nova Pro, with an analyst fallback. The renderer supports an OpenAI-compatible provider and it was tested live
against DeepSeek (`https://api.deepseek.com`, model `deepseek-flash`) with the same three policy questions
answered correctly on both providers. The DeepSeek key is in the box's mode-600 rendered env only; it is not in
the repository.

## Audit record

Chain version 4. The canonical hash covers the approval digest, the prompt digest and the completion digest.
Migration `027_the_prompt_and_completion_are_in_the_chain.sql` added version 4 without rewriting old rows.
Editing any of those three columns fails verification. The export now carries prompt and completion hashes.

## Operate it

```bash
# Tenant health, from anywhere that can reach the public name
curl -s https://3-85-130-96.sslip.io/health

# API health, from the build box
ssh -i ~/ai-stack/ai-stack-key.pem ubuntu@52.204.204.133 'docker exec api python -c "import urllib.request; print(urllib.request.urlopen(\"http://127.0.0.1:8088/health\").read().decode())"'

# Rotate the connector token from Secrets Manager; no redeploy
ssh ubuntu@3.85.130.96 'cd /home/ubuntu/client && sudo AWS_DEFAULT_REGION=us-east-1 python3 rotate_connector_secret.py \
  --secret-id oi-core/connector-token \
  --token-file /home/ubuntu/client/connector.token \
  --env-file /home/ubuntu/client/connector.env --service client-connector'

# Deployment state and drift, from the checkout on the box
python3 scripts/deployment_state.py --compose /opt/oi-core/deploy/office/compose/docker-compose.yml \
  --manifest /opt/oi-core/rendered/manifest.json --compare expected-state.json

# Weekly image scan, installed as oi-image-scan.timer
bash scripts/scan_images.sh /var/log/oi-image-scan
```

Job runs are unchanged: create a job with `POST /jobs`, approve at the gate with
`POST /jobs/<id>/approvals`, then read `GET /jobs/<id>`. The runtime sends through the MCP gateway, which is in
the client tenant.

## Caveats an operator must know

1. **Mail is captured, not delivered.** Mailpit on the tenant holds the messages. Nothing leaves for a real
   recipient.
2. **The tenant's public address is derived from its IP.** If the instance is stopped and gets a new address,
   the hostname and certificate change. Allocate an Elastic IP before calling it production.
3. **The DeepSeek key is in the rendered env** because the provider comparison needed it. Rotate it and remove
   it after the comparison is done.
4. **The image scan has current critical findings.** They are recorded in `docs/reviews/ops-cve/`. They are not
   waived.
5. **No alarm, backup, restore drill, rollback, SSM-only support, cost report, golden question set,
   fresh-deployment acceptance or load test exists yet.** Those are the open C8 rows.

## Open rows, with what blocks them

| Row | What it needs |
|---|---|
| OPS-ALARM-001 | A notification destination and a confirmed subscription |
| OPS-BACKUP-001, OPS-RTO-001 | A restore target, an acceptable downtime window, and a drill |
| OPS-UPDATE-001 | A release signing source and rollback target |
| OPS-SUPPORT-001 | Approval to remove SSH and cut the client box to SSM only |
| OPS-COST-001 | Cost Explorer / Marketplace report access and a decision on the report shape |
| QLT-GOLDEN-001 | A vertical and sixty client-approved golden questions |
| QLT-ACCEPT-001 | A fresh deployment to run acceptance against |
| QLT-LOAD-001 | A concurrency target and latency budget |
| BIZ-* | Counsel, insurance, contracts, on-call staff, subprocessors and pricing decisions |

## Evidence

The phase and row evidence lives under `docs/reviews/`. The C6 gate is in `docs/reviews/ws-gate/`; the MCP
gateway in `docs/reviews/mcp-gateway/`; the client tenant in `docs/reviews/mcp-owned/`; C7 rows in
`docs/reviews/sec-*`, `docs/reviews/aud-lock/`; C8 rows in `docs/reviews/aud-hash/`,
`docs/reviews/ops-cve/`, `docs/reviews/ops-drift/` and `docs/reviews/ops-incident/`.
