# Simplification: one answer path, one profile, 2026-10-21

**Goal.** Reduce maintenance surface without giving up the measured answer quality. This is a code and
configuration reduction pass, not a token-cost pass.

## What changed

The answer path had ten environment switches: seed passages, snippet characters, evidence slots, search limit,
and switches for the loop, the row query, the exact-phrase search, the honesty gate, the step cap and the call cap.
Each was a deployment fact to set, document and test in combination, and a wrong combination was a silent quality
regression.

There is now one setting, `OI_ANSWER_PROFILE`, with two measured values:

| Profile | Seed passages | Snippet characters | Evidence slots | Search results |
|---|---|---|---|---|
| accuracy | 12 | 1,600 | 32 | 12 |
| economy | 6 | 700 | 24 | 8 |

The tool loop, the three tools, the exact-phrase seed, the honesty gate and the step and call caps are constants,
because the product path always uses them. `OI_ANSWER_PROFILE=accuracy` is the configuration behind the
37-of-37 corpus-2 benchmark.

Dead code an independent audit found is gone too: a duplicated `read_passages` method (the first copy was
shadowed, 43 lines), an unreachable branch in the table-prefix code, an unused import and constant, and five
orphaned compose comments. An earlier change in the same pass deleted the two exact-retry branches that the
seed-time exact search had made unreachable (PR #226). Net for this change: 215 lines removed, 60 added, plus
tests for the profile.

## Verification

**Structural equivalence.** The old and new system prompts and tool lists were compared by executing both
versions and diffing the strings: byte-for-byte identical. The suite passes: 1173 passed, 22 skipped.

**Provider verification, DeepSeek V4.1 Flash, test-only.** Five runs on the refactored path, with a guard that
aborts if any model call falls back: **37, 37, 36, 36, 37 (mean 36.6 of 37), zero fallback calls**. That matches
the provider's earlier pre-refactor result (36.8), so the answer path did not regress. The DeepSeek key was held
only in the box's rendered environment and removed afterwards.

**Bedrock verification is blocked by quota, not by code.** Three attempts each produced about 24 to 25 Sonnet
calls and then 429 "Too many tokens per day" from Bedrock; the gateway silently fell back to Haiku and Nova. The
fallback guard marked each run contaminated instead of scoring it. A run produces 37 or more model calls, so the
daily budget currently available cannot complete one clean run. The last clean Bedrock benchmark remains
**37, 37, 37, 37, 37** with its audit trail of 185 Sonnet calls and zero fallbacks.

**Consequence for the product.** The accuracy profile may exhaust a small per-account daily token budget in one
or two suite runs. The `economy` profile exists for that deployment shape, and the daily budget should be part
of the installation checklist.

## Deliberately not changed

- The retrieval loop and the three tools: measured use in the benchmark window was 345 document searches, 143
  row queries and 65 passage reads across 185 answers.
- The exact-phrase lane: without it PUB_L01 fails every run; raising the seed to 20 passages did not recover it
  and lowered the score.
- The index-time table metadata: the answer path reads `table_id`, `row_index` and `headings` when rendering
  table context.
- Chunker payload fields with no readers (`bbox`, neighbour chunk ids, `token_estimate`, the un-serialised
  `table_context`) are recorded as a separate cleanup with its own tests, because they touch ingestion and
  deserve their own measurement.

Raw verification runs: `/home/anthony/oi-eval-runs/deepseek-refactor-runs.tgz`.
