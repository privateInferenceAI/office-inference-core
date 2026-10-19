# The fallback chain, and why the deployment's model is now tracked

**Date:** 2026-10-19. **Deployment:** `oi-core-dev-cloud`. **Register:** `AGENT-FALLBACK-001`,
`INF-ROLE-001` (attribution, separate PR).

## What changed

The manifest's `fallback` field takes a list tried in order, and an entry may carry `@<region>`. The
development deployment's model choice moves from an untracked `rendered/` override into
`manifests/dev-cloud-claude.yaml`:

    models:
      analyst:
        frontier: bedrock/us.anthropic.claude-haiku-4-5-20251001-v1:0
        fallback:
          - bedrock/us.anthropic.claude-haiku-4-5-20251001-v1:0@us-east-2   # same model, other Region
          - bedrock/amazon.nova-pro-v1:0                                    # different model

Rendered gateway configuration on the box after `render_manifest.py`:

~~~
analyst            {'model': 'bedrock/us.anthropic.claude-haiku-4-5-20251001-v1:0'}
analyst-fallback   {'model': 'bedrock/us.anthropic.claude-haiku-4-5-20251001-v1:0', 'aws_region_name': 'us-east-2'}
analyst-fallback-2 {'model': 'bedrock/amazon.nova-pro-v1:0'}
fallbacks: [{'analyst': ['analyst-fallback', 'analyst-fallback-2']}]
~~~

The validator bounds the chain at three entries, validates the Region form, refuses a duplicate or a repeat of
the primary in the same Region, keeps the chain cloud-only, and holds every entry to the residency and lifecycle
rules. `scripts/model_admin.py` is the client's procedure: list, check, set, with the same two gates CI runs.

## The IAM change, and the client installation step

The credits account's `oi-core-dev-bedrock-invoke` policy moved from v5 to v6, adding exactly two resources:

~~~
arn:aws:bedrock:us-east-2:878451098165:inference-profile/us.anthropic.claude-haiku-4-5-20251001-v1:0
arn:aws:bedrock:us-east-2:878451098165:inference-profile/us.anthropic.claude-sonnet-4-5-20250929-v1:0
~~~

Nothing else changed; v5 is still available to roll back. **Why it is needed:** a geographic inference profile
is resolved from the Region the call is made in, so the second entry needs the profile ARN in us-east-2. Direct
in-region foundation-model invocation was tried first and Bedrock rejects it for this model ("on-demand
throughput isn't supported"), which is why the regional profile is the mechanism. After the change, the
instance role invoked the Haiku profile from us-east-2 and got a completion.

**A client installation must do the same in its own account.** The runbook's installation section now states it,
and the shape to copy is the policy above with the client's account id.

## Measurement

All runs use the redesigned five-probe instrument (PR #192), tool loop on, query tool on.

All runs use the redesigned five-probe instrument (PR #192), tool loop on, query tool on. The healthy
configuration has the chain configured and inert; the fault configuration points the primary at the fault stub.

| Configuration | Runs | Mean of 53 | Failures | Degraded | Fallback used | p50 | p95 | Cost per answer |
|---|---|---|---|---|---|---|---|---|
| pre-chain baseline (instrument PR) | 52, 52, 52, 51, 52 | 51.8 | U08 x5, N04 x1 | 0 | n/a | 3,436 ms | 12,297 ms | 0.01343 USD |
| chain, healthy | 52, 52, 52, 52, 52 | 52.0 | U08 x5 | 0 | 0 of 492 calls | 3,198 ms | 12,744 ms | 0.01339 USD |
| chain, primary failed | 52, 52, 52, 52, 52 | 52.0 | U08 x5 | 0 | 265 of 265 answer rows via analyst-fallback (us-east-2 Haiku) | 3,428 ms | 11,819 ms | 0.01345 USD |
| primary and first fallback failed (4-question subset) | 2 of 4 | n/a | N04, N06 | 0 | 4 of 4 via analyst-fallback-2 (Nova Pro) | n/a | n/a | n/a |

**What the numbers say.** A primary outage costs nothing in answer quality while the same model still answers from
the other Region: score, latency and cost match the healthy baseline. When the same model is unavailable in both
Regions, the third entry answers, and the subset shows the expected quality drop (two of four exact-number
questions wrong) rather than a degraded answer. The third leg is a last resort, not an equivalent.

**The client's wait and spend** are on the acceptance panel in
docs/reviews/acceptance-panel-2026-10-19.md: about 0.0134 USD per answer at about three provider calls per answer,
p95 between 11.8 and 12.7 seconds across the three configurations.
