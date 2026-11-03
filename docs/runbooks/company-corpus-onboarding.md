# Company corpus onboarding

How the company's own documents become the corpus the deployment serves. This is the document part of Phase E
in `docs/runbooks/product-verification.md`. The rest of onboarding is in `docs/runbooks/company-onboarding.md`.

## Before any document is copied

- [ ] Phase D is accepted on the real hostname and the owner has signed in through the browser.
- [ ] The backup timer has run, the latest backup is in S3, and an off-box restore has passed.
- [ ] The pilot decision is made: the company corpus replaces the synthetic collection on this deployment, or
      the business gets its own deployment. Do not mix a client's documents into the demo collection.
- [ ] The owner's account is in the rendered access registry with the right group.

## Two ingestion paths

### A. Canonical bundle, recommended for serving

`oi-corpus-builder` builds a validated bundle: canonical documents, deterministic chunks under the measured
token ceiling, originals, fidelity records and checksums. The deployment's `importer` verifies the bundle
against the documents root and writes it to Qdrant and Postgres. This is the same path the canonical v2 corpus
used and the one the serving contract expects.

At a high level:

1. Put the company documents in one folder, one subfolder per access group if they differ.
2. On the builder machine, run the builder's extract, canonicalise, validate, chunk and bundle steps for that
   folder and a new corpus id.
3. Copy the bundle to `/opt/oi-product/bundles/<company-bundle>` and the source documents to
   `/opt/oi-product/company-documents`.
4. Import with the importer container, a new collection and the company documents root:

   ```bash
   docker compose -p oi-product -f deploy/office/compose/docker-compose.yml \
     -f deploy/product/docker-compose.product.yml -f app-ports-override.yml \
     --env-file rendered/env run --rm --no-deps importer \
     --bundle /bundles/<company-bundle> --documents /company-documents \
     --collection company-<slug>-v1
   ```

   The importer needs a mount for the company documents root; add it to the product overlay or run the image
   directly with the same environment and an extra volume.
5. Verify counts and one known question before switching anything.

The builder step is not automated on the deployment box and is an operator build task. Record its version and
the bundle checksums.

### B. Raw ingestion worker, rehearsed

The deployment can also ingest a folder with the raw worker. This path parses, chunks and embeds directly, and
was rehearsed on 2026-11-03: three documents in, two ingested and one refused for a chunk past the embedding
limit. It is useful as a reachability and failure check, and acceptable for small, well-behaved document sets.
It is not the recommended serving path because it does not enforce the canonical chunk contract before writing.

Rehearsal command, against a scratch database and collection:

```bash
docker run --rm --network ai-net \
  -v /path/to/company-documents:/company-documents:ro \
  -e DATABASE_URL="postgresql://oicore:<password>@postgres:5432/<scratch-db>" \
  -e PARSER_URL=http://parser:5001 -e EMBED_URL=http://embeddings:80 \
  -e VECTOR_URL=http://vector:6333 -e COLLECTION=company-rehearsal \
  -e DOCUMENTS_ROOT=/company-documents \
  oi-core/ingestion:local --documents /company-documents --json
```

A failure list is a gate: do not switch the served collection while any document is in it. Fix the document or
use the canonical bundle path. The rehearsal record is
`docs/reviews/company-ingestion-rehearsal-20261103/README.md`.

## Safe configuration changes

- **Re-rendering the manifest preserves the existing secrets.** Verified on a copy of the live rendered env:
  all 65 keys survived a re-render, including the edge, n8n and workspace secrets. Still copy
  `/opt/oi-product/rendered` to a timestamped backup before rendering, and diff the env keys after.
- The served collection is set by `COLLECTION` in `rendered/env`, not by the manifest renderer. Change it
  explicitly when switching.
- Update the access users through the manifest and re-render in place on the box, or edit
  `rendered/access.json` deliberately and restart the API. The manifest is the source of truth for the next
  render.
- Restart the API after changing `COLLECTION` or the access registry.

## Switch and verify

1. Take a rendered-directory snapshot and a fresh backup.
2. Import the company corpus.
3. Verify: zero ingestion failures, document and chunk counts as expected, one known answer with a citation to
   a company document, one boundary question denied for a lower group.
4. Change `COLLECTION` to the company collection, restart the API, and repeat the checks.
5. Record the before and after counts and the questions used.

## Rollback

Set `COLLECTION` back to `documents-mixed-v2-20261028`, restore the previous `rendered/env` snapshot if needed,
and restart the API. The synthetic collection is not deleted by onboarding; keep it until the company corpus
has passed its own acceptance and a restore drill.
