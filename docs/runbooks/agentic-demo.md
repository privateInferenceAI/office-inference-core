# The agentic demo: product deployment, 2026-11-03

**Status:** draft runbook for the product deployment. It covers Ask, Do, Review and Activity on one
deployment where the C6 agent runtime and the WS workspace layer are both switched on. The C9 eight-scene
retrieval demo stays its own script; this one is the product half.

## The deployment

| | |
|---|---|
| Deployment id | `oi-core-product-20261103` |
| Product box | `i-0a3fca3c1141cbcad`, m7i.4xlarge, Elastic IP 100.60.208.224, root `/opt/oi-product` |
| Workspace host | `i-0cb950741232003bb`, m7i.xlarge, Elastic IP 100.60.62.166, root `/opt/oi-workspace` |
| Packs | `core, document-chat, agentic-desk, workspace` |
| Definitions | `research_brief` (document search, read-only child, approval before irreversible send), `workspace_brief` (sandbox files and code, allowlisted web, artifacts, approval before send) |
| Corpus | `bundle-table-units-v2-20261028`, 80 documents, 10,576 chunks |
| Models | analyst Kimi K3 on Bedrock, no fallback chain |
| Host services | egress proxy 8091, SES connector 8092 (systemd); workspace service 8090 on the workspace host |
| Analyst | `bedrock/converse/us.moonshotai.kimi-k3` |

The workspace host is a separate instance with its own IAM role (`oi-core-workspace-host`) that can read and
write only the artifact bucket. The API box never holds the Docker socket. That is decision 0010 as built.

## Command prefix

```
cd /opt/oi-product
alias op='docker compose -p oi-product \
  -f deploy/office/compose/docker-compose.yml \
  -f deploy/product/docker-compose.product.yml \
  -f app-ports-override.yml \
  --env-file rendered/env'
```

## Setup before the demo

1. Tunnel: `ssh -i ~/ai-stack/ai-stack-key.pem -N -L 18080:127.0.0.1:8080 ubuntu@100.60.208.224`, then
   `http://127.0.0.1:18080`.
2. Two sessions, staff and executive:
   ```
   op --profile tools run --rm session mint --dev --subject zoe.reyes@northwind.example --role viewer --ttl 12
   op --profile tools run --rm session mint --dev --subject adam.hale@northwind.example --role viewer --ttl 12
   ```
3. Health and layer checks:
   ```
   docker exec api python -c "import urllib.request; print(urllib.request.urlopen('http://127.0.0.1:8088/health').read().decode())"
   python3 -c "import json;d=json.load(open('rendered/agent_definitions.json'));print(d['runtime_count'],[x['id'] for x in d['definitions']])"
   curl -s http://127.0.0.1:8091/health; curl -s http://127.0.0.1:8092/health
   curl -s -H "X-Workspace-Token: $(grep '^OI_WORKSPACE_TOKEN=' rendered/env | cut -d= -f2-)" http://172.31.93.141:8090/health
   ```
4. One cheap Ask to confirm retrieval and the model route.

## The scenes

### Scene A. The layer is real. 1 min.

Show the two definitions, the MCP tool table, and the workspace host answering. Then `op ps --status running`
and the audit record's last model call, which should be Kimi with `fallback_used false`.

### Scene B. Ask, the familiar ground. 2 min.

Staff asks the mileage question. Expect 0.67 USD per mile with citations. This is the C9 behaviour, kept as the
anchor before the agentic part.

### Scene C. Research and brief, a real agent job. 4 to 6 min.

Staff opens Do and starts `Research and brief`: "What does the travel and expense policy say about mileage and
receipts? Write a brief with citations and send it to the verified recipient."

What to narrate while it runs:
- the step list advances (search, read, child helper, draft);
- the child helper is a separate read-only tool span (`child-1`) with its own budget;
- when the job reaches the irreversible send, it stops at the approval gate and shows the pending action, the
  digest and what happens if approved.

Then Review, approve it. The job finishes the send and closes out. Activity shows the tool rows, the approval
row and the completion.

### Scene D. Workspace, where files and code happen. 5 to 7 min.

Staff starts `Workspace brief`: "Write note.txt with the mileage rate, run a Python calculation of 19*23, fetch https://example.com and
save its title to title.txt, write the result to brief.md, then send exactly one email to
anthony@officeinference.com and finish."

Narrate: the job gets a task-scoped sandbox with no network, writes and reads files, runs code, fetches the
approved URL through the egress proxy, stops at the gate before the send, and after approval the artifact is
stored in S3 and appears in the job's artifact panel.

Open the artifact, download it, show the hash.

### Scene E. The record. 2 min.

```
op --profile tools run -T --rm audit export
DAY=$(date -u +%F)
op --profile tools run -T --rm audit verify /app/exports/$DAY/audit-events.jsonl
docker exec postgres psql -U oicore -d oicore -c "select id, event_type, outcome, actor, tool, payload->>'job_id' as job, payload->>'stage' as stage from audit_events order by id desc limit 25;"
```

Expected rows: `tool_call` for `search_documents`, `read_passages`, `delegate_research`, `workspace_write`,
`workspace_read`, `workspace_run`, `web_fetch`, `send_email`; an `approval` row; `model_call` rows with the
model and fallback flag; the chain intact with the no-anchor note.

### Scene F. What is not in this deployment. 1 min.

C7 (client-tenant gateway, TLS ingress, Terraform into the client account) and C8 alarms are separate
operational deployments. Say so rather than implying the demo box is the whole product.

## Measured on the deployment, 2026-11-03

- `research_brief`: 3 turns, child helper span `child-1` with two `search_documents` calls, finished in
  under a minute on Kimi, state succeeded.
- `workspace_brief`: 4 to 8 turns; workspace write, read, run, list, web fetch and document search; one
  approval gate holding the email; after approval `act-4` sends through the SES connector and the job closes.
  About 40 to 90 seconds to the gate.
- `invoice_intake`: read, extract, draft, gate, approve, post entry, close out; the deterministic C5 path.
- The gateway needs `MCP_SESSION_SECRET` from the API's `OI_MCP_SESSION_SECRET`; without it every approved send
  is refused with `401 invalid_session`. The product overlay now passes it.
- The analyst invents a recipient if the input does not name one, and the connector refuses it. Name
  `anthony@officeinference.com` explicitly in the demo input.

## If something misbehaves

- A job that ends `failed` at `verification` usually means the model produced a brief without numbered
  citations. Retry once; if it repeats, show the job JSON and the model name.
- A job stuck at `running`: check `docker logs api` for the runtime, and the workspace service logs on the
  workspace host.
- The connector accepts only the verified recipient; anything else is refused and recorded.
- No model fallback is configured. A throttle produces a degraded answer, not another model's answer.

## Limits

- The evidence model was Claude Sonnet 4.6; this deployment runs Kimi K3, so behaviour is re-proven on a
  different model and the record says so.
- One API box and one workspace host. No TLS ingress, no Cognito, no tenant split in this deployment.
- The artifact retention and cancellation clauses were not exercised in the earlier WS evidence; this build
  should record whether they were.
