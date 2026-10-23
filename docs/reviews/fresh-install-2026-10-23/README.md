# Fresh deployment acceptance, 2026-10-23

**Status:** accepted on a blank Spot instance, with a partial corpus. All four probes passed, 24 of 24 rights.

## Target

- Account: 878451098165, us-east-1b
- Instance: i-0adf4bc69011742e1, m7i.4xlarge Spot
- Public IP: 18.208.170.165 (no Elastic IP)
- Deployment root: /opt/oi-core
- Code archive: git commit ed1f540, sha256 2c0b3f639915e7fa7951f80b8d0c3807f75a2eec25d22fb3cc0f5e13423d1c79
- Corpus archive: second-corpus-2026-10-22, sha256 0b154bcb452af0789cdc34a6453b3806924fb22cdbf15a217eb73175072a88a1
- Deployment id: oi-core-fresh-2026-10-23

## Install

1. Launched a blank Ubuntu 24.04 instance with the oi-core-dev-instance profile and a 200 GB gp3 root.
2. Installed Docker 29.9.2 and Compose v5.6.1.
3. Extracted the current code archive and the 80-document corpus archive.
4. Created the fresh manifest from dev-cloud-accuracy.yaml and set client.deployment_id and client.name.
5. Rendered, set OI_ANSWER_PROFILE=accuracy, validated the manifest and lifecycle.
6. Started the stack without the client app, which needs a separate /opt/oic-ui archive.
7. Provisioned teams and ingested a 40-document subset: 35 synthetic company documents and 5 executive documents.

## Findings that changed the install path

- The deployment id is under client.deployment_id. A top-level deployment_id change did not reach rendered/env and the API reported the old id.
- Ingestion must wait for Postgres, parser and embeddings health. The compose file now has health conditions.
- LiteLLM must start before the API on a blank database. Starting the API first applies migrations that make Prisma refuse to baseline; the fix is to stop the API, reset the database, start LiteLLM alone, then start the API.
- The client app requires /opt/oic-ui. The four probes do not need it, so the acceptance ran core-only.
- The full 80-document ingestion did not finish on this small Spot instance within the acceptance window. The accepted probes ran over a 40-document subset. The baseline counts 80 documents and 24,296 chunks were not reproduced on this box.

## Probe results

| Probe | Result |
|---|---|
| interface_probe.py | PASSED: 8 rights |
| activity_probe.py | PASSED: 8 rights |
| access_probe.py | PASSED: 4 rights |
| stream_probe.py | PASSED: 4 rights |

All four exited 0. Transcripts are beside this file.

## Instance state

The Spot instance was stopped after the evidence was copied. No Elastic IP was created.

## What this does and does not prove

It proves a blank host can install this code, start the stack, ingest a subset and pass the four acceptance probes. It does not reproduce the full 80-document corpus baseline, and it does not install the client UI. The restored-scratch acceptance in docs/reviews/acceptance-2026-10-23/ remains the full-corpus probe evidence.
