# Security upgrade window, 2026-10-23

**Approved by:** Anthony, 2026-10-23. This document is the backup and rollback plan for the critical image findings
in docs/reviews/ops-cve/. The criticals were patched on 2026-10-23; the high findings remain.

## Current state

- Scan date: 2026-10-23. Current findings: LiteLLM 1 critical and 4 high; parser 5 critical and 38 high;
  postgres 16-alpine 1 critical and 25 high.
- Live stack: build box i-02aba2feef08f8e7b at 52.204.204.133.
- Backup taken before the window: s3://oi-core-backups-878451098165/backups/20261023T122439Z/
  - postgres.dump sha256 056b832c85ac75125334d3c240a65659bc5bb1d1cafa3afe0c60007ba63e21fe
  - state.tar.gz sha256 81289b8ced73b5124f16e77a9761353f7f60124de7202e5039906da2d82acdd6
- Rollback image tags saved on the box:
  - oi-core/api:pre-cve-2026-10-23 cda4fcfaef06
  - oi-core/app:pre-cve-2026-10-23 377669dd8f8c
  - oi-core/litellm:pre-cve-2026-10-23 12c6facda2d2
  - oi-core/parser:pre-cve-2026-10-23 4815754ace12
  - oi-core/ingestion:pre-cve-2026-10-23 e0f063a1774c
  - oi-core/mcp-gateway:pre-cve-2026-10-23 50aef45146d6
  - postgres:pre-cve-2026-10-23 deb28ee47118
- The restore drill on the scratch box passed on 2026-10-23: the restored API answered a cited question and the
  964-event audit chain verified. State restore took 48 seconds; the full drill took 15 minutes 42 seconds. The
  first upgrade may start from this backup. The backup and the rollback images above are the rollback path.

## Preconditions

1. The backup above exists and its checksums match.
2. The restore drill passes: API health, audit export and verify, and a cited answer from the restored stack.
3. The box is not running a client job; ingestion can be paused for the window.
4. The current image tags listed above exist on the box.
5. The operator has the compose file and rendered/env backup path.

## Upgrade order

One service at a time. Do not combine.

### 1. Postgres minor patch

The lowest-risk change is a patched 16-alpine image. No schema migration is expected from a minor patch, but the
dump is the rollback path if the new image cannot start.

    docker pull postgres:<patched-16-alpine-digest>
    docker tag postgres:<tag> postgres:candidate-cve-2026-10-23
    # change the postgres image in deploy/office/compose/docker-compose.yml with a version comment
    docker compose -f deploy/office/compose/docker-compose.yml --env-file rendered/env up -d --no-deps postgres
    docker exec postgres pg_isready -U oicore -d oicore

Validate before continuing: API health, /ask, audit count and verify.

### 2. Parser image

Rebuild the parser from the current Dockerfile with the patched base digest. The parser is CPU and its image is
large; the risk is a Docling or system-library regression.

    docker compose -f deploy/office/compose/docker-compose.yml --env-file rendered/env build parser
    docker compose -f deploy/office/compose/docker-compose.yml --env-file rendered/env up -d --no-deps parser
    docker exec parser python -c "import docling; print(docling.__version__)"

Validate: the parser acceptance probe and one real PDF.

### 3. LiteLLM

LiteLLM carries the custom authentication hook, budgets and the model list. This is the highest-risk change.
The target version must be the next patched release after 1.104.2, pinned by digest and recorded in compose.

    # update the image digest and the version comment together
    docker compose -f deploy/office/compose/docker-compose.yml --env-file rendered/env build litellm
    docker compose -f deploy/office/compose/docker-compose.yml --env-file rendered/env up -d --no-deps litellm

Validate: tests/test_gateway_auth.py and tests/test_gateway_budget.py, one /ask, one fallback turn, one budget
refusal, and the custom-auth assertion path.

## Rollback

### Image rollback, one service

    # example: parser
    docker tag oi-core/parser:pre-cve-2026-10-23 oi-core/parser:local
    docker compose -f deploy/office/compose/docker-compose.yml --env-file rendered/env up -d --no-deps parser
    docker logs parser --tail 20

### Database rollback

Only if the upgraded Postgres or a migration corrupts the database:

    ssh -i ~/ai-stack/ai-stack-key.pem ubuntu@52.204.204.133
    cd /opt/oi-core
    docker exec -i postgres pg_restore -U oicore -d oicore --clean --if-exists --no-owner < /home/ubuntu/backup/postgres.dump
    docker compose -f deploy/office/compose/docker-compose.yml --env-file rendered/env up -d --force-recreate api litellm

The dump is at the S3 path above. Download it to the box first. Restore only after stopping ingestion and jobs.

### Full rollback

Tag every pre-cve image back to local, restore the dump, recreate the stack, and run the audit export/verify. If
the stack cannot start, fall back to the pre-window AMI or a fresh restore from the backup.

## Validation and release gate

After each service:

1. curl API health and tenant gateway health.
2. Ask one known question and check the citation.
3. audit.py export and verify the chain.
4. Full test suite from the checkout.
5. Re-run the Trivy scan and record the new report.

If any check fails, roll back that service before touching the next one. Do not proceed to LiteLLM if Postgres or
parser rollback is needed.

## Record

Add the exact image digests, checksums and test outputs under docs/reviews/ops-cve/upgrade-2026-10-23/. Update
the compose version comments and the service inventory. Do not edit a pin without a version comment.
