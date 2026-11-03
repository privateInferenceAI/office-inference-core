# Decision: the canonical document pipeline

**Status:** accepted 2026-10-26, after measurement.

## Decision

The document reading, ingestion and search path is:

1. Canonical pre-formatting: every source document is rendered once to an `oi-doc-v0` canonical copy, with the
   LLM copy where fidelity checks pass and the deterministic control copy as the fallback.
2. `chunker-canonical-v1` chunking into an `oi-bundle-v0` bundle with measured token counts.
3. Import through the canonical bundle importer, which verifies checksums, document identity, chunk ids, originals
   and cross-store counts before writing.
4. Serving: whole-table expansion for multi-table pages, the numeric answer contract, no automatic exact-phrase
   seed, renderer and reranker kept.
5. Answers: Kimi K3 through Bedrock.

## Evidence

38-role lab suite, complete canonical mixed bundle, Kimi K3, five passes: 38, 37, 37, 35, 34 of 38. The 38/38
is reproduced. The all-control bundle on the same suite scored 35, 34, 33, 36, 36, so the LLM-formatted copies
are worth about 1.4 questions per pass and the deterministic-only path is the fallback, not the default.
Product stack (demo) 42-role harness, same canonical index and Kimi: 37, 37, 37, 37, 38 of 42, with the four
corpus-specific probes the only consistent failures.

Raw runs: `/tmp/lab-suite-full-20261026T181513Z`, `/tmp/lab-suite-control-20261026T202859Z`,
`/tmp/demo-suite-42f-20261026T185902Z` on the lab and demo boxes at the time of the measurement. The joint
findings file in `oi-core-lab` carries the same numbers, commit `dd46296`.

## Consequences

- The product carries the canonical bundle importer and the canonical chunker version in `services/ingestion`.
- The eval runner's retrieval contract check must accept the canonical chunker version when the collection is a
  bundle import; the product chunker was set to `chunker-canonical-v1` for this.
- The deterministic copy path stays maintained as the fallback and the comparison arm.
- The four main-harness probes remain an open gap for the corpus-specific probe coverage.
