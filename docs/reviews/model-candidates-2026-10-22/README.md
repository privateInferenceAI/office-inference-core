# Model candidates on corpus 2, 2026-10-22

**Why this exists.** The preferred model will change as models advance, and the stack must be able to change with
it without editing the answer path. This records the measurements behind the current preference and the rule for
changing it. All runs use the same corpus-2 question set, the same accuracy evidence profile, the same answer path,
and the fallback guard that refuses a run containing fallback model calls.

| Candidate | Provider / path | Five runs (of 37) | Mean | Notes |
|---|---|---|---|---|
| Claude Sonnet 4.6 | Bedrock, client account | 37, 37, 37, 37, 37 | **37.0** | Current accuracy reference; clean run, 185 Sonnet calls, no fallback |
| DeepSeek V4.1 Flash | External API, test-only | 37, 37, 36, 36, 37 | 36.6 | Refactored answer path; zero fallback; data leaves AWS |
| DeepSeek V3.2 | Bedrock | 34, 34, 33, 37, 35 | 34.6 | Zero fallback; older version than the external test model |
| Claude Haiku 4.5 | Bedrock, accuracy evidence | 36, 37, 37, 30, 30 | 34.0 | Earlier evidence run, before the refactor; high variance |

The difference between DeepSeek V4.1 Flash (36.6) and DeepSeek V3.2 on Bedrock (34.6) is two points of accuracy
from a version difference alone. The brand is not the predictor; the exact model version is. The Sonnet result
remains the best measured configuration on this corpus.

Failure clusters in the DeepSeek V3.2 runs: PUB_P01 (3 of 5), PUB_L02 (3 of 5), SYN_N01 (3 of 5), PUB_X02 (2 of
5). Sonnet's clean run had no failures. That pattern is a reading and table-verification difference, not an
infrastructure difference.

## Decision rule for changing the preferred model

1. The model is a manifest value, never code. Answer profiles set evidence size and are independent of the model.
   A model change is a manifest edit plus a re-render, not a branch in the answer path.
2. A candidate is admitted only after a five-run corpus-2 measurement with the fixed question set, the same
   accuracy profile, and zero fallback calls. The record names the provider, exact model id and version, date and
   quota conditions.
3. The candidate must also pass a capability preflight: tool calling through the gateway, the context window the
   profile needs, streaming, and an available quota or provisioned capacity. A model that cannot call tools is not
   a candidate for the answer path, whatever its benchmark score.
4. The data path is part of the decision. Client-owned Bedrock is the path for confidential and regulated work.
   External APIs are test-only or for explicitly non-regulated lanes, with the owner's approval, and never carry
   client documents.
5. Preferred models are reviewed when a new model appears or at least quarterly. The lifecycle table still fails a
   deprecated model in a manifest, and the benchmark record makes a change reproducible.

## Current preference

- **Bedrock accuracy reference:** Claude Sonnet 4.6. Best measured result, fully inside the client account. Its
  constraint is quota, not quality.
- **Test-only or non-regulated lane:** DeepSeek V4.1 Flash through the external API. Comparable accuracy at 36.6,
  no daily cap, but data leaves AWS and the key must stay out of the repository.
- **Bedrock alternates:** DeepSeek V3.2 is a usable fallback and test model inside AWS, but at 34.6 it is not an
  accuracy replacement for Sonnet. Haiku 4.5 is cheaper and faster but variable.

The gateway already makes the swap a configuration change. The missing pieces for easy model churn are the
capability preflight and the benchmark record; this document is the record format for the second.
