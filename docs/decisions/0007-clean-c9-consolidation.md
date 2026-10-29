# 0007: Clean C9 consolidation and the canonical serving pin

**Date:** 2026-10-28. **Status:** release candidate.

## What was wrong

The C9 demo box `i-0a1a390f790324337` held the full product code tree and the C9 version 2 UI, but its
working tree was a hybrid:

- `services/api/answer_contract.py` came from the canonical serving port.
- `services/api/tool_loop.py` was the canonical version plus uncommitted experimental patches for
  document completion, comparison retrieval, raised loop limits and instrumentation.
- `services/ingestion/bundle_import.py` and `chunker.py` came from the canonical branch.
- The index was the 80-document mixed bundle with 10,607 chunks, not the C9 rehearsal corpus.
- Only the eight-service demo profile was running. C7 and C8 runtime pieces were not running on that box.
- The tree was not a git checkout, so the experimental modifications had no provenance.

The experimental `tool_loop.py` patches are rejected. They are not part of this release.

## What is authoritative

- Full product code: `office-inference-core` main and this release branch.
- C9 version 2 UI: `oic-ui` main `b04ca0a`. The one-Ask change is `feat/one-ask-ui`; the streamed proxy
  fix is `fix/proxy-streams-sse`.
- Exact canonical v2 serving behavior: `deploy/canonical/serving-canonical-v2/`. These files are a
  deployment pin, not the product-branch default.
- Pre-ingestion, chunking and bundle import: the canonical release pipeline and the v2 bundle.
- Canonical v2 bundle: 80 documents, 10,576 chunks, `chunker-canonical-v1`.

## C9 version 1 and version 2

Version 1 of the C9 demo ran on the old second corpus and exposed the streamed Ask path's limitation.
Version 2 is the one-Ask UI with the streaming proxy and the current scene behavior. The version 1 audit
record and exports are preserved outside the rebuilt demo box; they explain why version 2 exists.

The preserved database dump and evidence are under
`/home/anthony/oi-corpus-data/c9-demo-evidence-20261028/` on the build laptop. A copy of the evidence
must accompany any release record; do not rely on the old demo instance as the only copy.

## Known test separation

The product test suite runs against the product code. The canonical deployment overlays the serving pin
after the product image is built. The pin is older than the current tests, so running the product test
suite with the pin overlaid produces failures that the product branch does not have. The deployment must
record which files were replaced and must not claim the product test suite passed with the pin applied.

## Rebuild rule

1. Extract the full product tree from this release.
2. Apply the serving pin only for the canonical search profile.
3. Keep the C9 UI from `oic-ui` main.
4. Import the v2 bundle into a fresh collection.
5. Prove API health, collection counts and the section/five-run measurements.
6. Keep C7/C8 runtime components in their correct deployments; do not collapse the client tenant into
   the demo box.

## Open work

- Reconcile the serving pin with the product branch and replace it with a tested feature flag.
- Make the per-client Postgres isolation change before onboarding more clients.
- Rerun the full C9 scenes on the clean deployment.
- Update the C9 evidence with the clean deployment result.
