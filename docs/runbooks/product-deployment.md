# Product deployment runbook: agentic product, 2026-11-03

This is the operator runbook for the full product deployment: the composed stack on the product box plus a
dedicated workspace host. It is not the C9 retrieval demo; that has its own runbook.

## Shape

| | |
|---|---|
| Product box | `i-0a3fca3c1141cbcad`, m7i.4xlarge, 16 vCPU, 64 GB, Elastic IP 100.60.208.224 |
| Workspace host | `i-0cb950741232003bb`, m7i.xlarge, Elastic IP 100.60.62.166 |
| Deployment id | `oi-core-product-20261103` |
| Packs | `core, document-chat, agentic-desk, workspace` |
| Analyst model | `bedrock/converse/us.moonshotai.kimi-k3`, no fallback |
| Corpus | canonical v2 bundle, 80 documents, 10,576 chunks, `chunker-canonical-v1` |
| Artifact bucket | `oi-core-workspace-artifacts-878451098165-product-20261103` |
| SES identity | `anthony@officeinference.com`, verified, send through the instance role |
| Workspace host IAM | `oi-core-workspace-host`: artifact bucket plus SSM only |

The two boxes share one security group (`sg-05485a648e8baa4b8`): SSH from the operator address and port 8090
between the boxes. The workspace host runs the workspace service container with the host Docker socket; the
product box never receives that socket. The API reaches the workspace service over the VPC at
`OI_WORKSPACE_URL`; it reaches the egress proxy and the SES connector through the Docker bridge gateway on
8091 and 8092.

## Build, in order

Stage the release tarballs at `/home/ubuntu/release` and the deployment inputs at
`/home/ubuntu/product-config` (from `office-inference-core` branch `feat/agentic-product-deployment` and the
scripts in `oi-canonical-stack/deploy/agentic-product`).

1. Product box: `bash product-prepare.sh` with `WS_PRIVATE_IP` set to the workspace host private address.
   It verifies the tarballs, extracts the product tree and UI, applies the serving pin, renders the product
   manifest, appends the integration env and generates the tokens, builds every image including the workspace
   images, and creates the runtime directories.
2. Product box: `bash product-start.sh`. Postgres, the LiteLLM database, gateway, vector, embeddings, API,
   teams, sessions, parser, MCP gateway and app come up in order.
3. Product box: `bash product-import.sh`. Imports the v2 bundle with CPU embeddings, restarts the API and checks
   the stores. About an hour.
4. Product box: `bash product-host-services.sh`. Installs and starts the egress proxy and the SES connector
   units, and verifies both listen on 8091 and 8092.
5. Workspace host: copy `/opt/oi-product/rendered/workspace-host.env` to
   `/opt/oi-workspace/rendered/workspace-host.env`, then `bash workspace-host-prepare.sh`. It builds the task
   image and the workspace service image and starts the service container on 8090.
6. Product box: `bash run-acceptance.sh`. Waits for the import, runs the C6 and WS acceptance jobs, then
   exports and verifies the chain into `eval-out/acceptance-<timestamp>/`.

## Operate it

```
cd /opt/oi-product
alias op='docker compose -p oi-product \
  -f deploy/office/compose/docker-compose.yml \
  -f deploy/product/docker-compose.product.yml \
  -f app-ports-override.yml \
  --env-file rendered/env'
op ps --status running
docker exec api python -c "import urllib.request; print(urllib.request.urlopen('http://127.0.0.1:8088/health').read().decode())"
systemctl status oi-product-egress oi-product-connector --no-pager
```

Workspace host:

```
docker ps --filter name=workspace-service
docker logs workspace-service --tail 50
```

## Known gaps

- C7 (client-tenant gateway, TLS ingress, Cognito, Terraform into the client account) and C8 alarms are
  separate deployments and are not part of this box.
- The renderer does not emit the C6/WS integration variables; the prepare script appends them. That is
  tracked configuration, but it belongs in the renderer eventually.
- The workspace service image needed `artifact_store.py` added to its Dockerfile; fixed on the product branch.
- The acceptance evidence from October used Claude Sonnet 4.6; this deployment measures Kimi K3.
- The product deployment runs the release tree's serving files, not the C9 serving pin. The pin's
  `tool_loop.py` predates the agent runtime and rejects the runtime's `strict_audit` argument; applying it
  breaks every agent job. `agentic-product-prepare.sh` now skips the pin unless `APPLY_SERVING_PIN=1`.
- The MCP gateway needs `MCP_SESSION_SECRET` (the API's `OI_MCP_SESSION_SECRET`); the product overlay passes it.
- The workspace service image needs boto3 for the S3 artifact store; added to `service.Dockerfile`.
