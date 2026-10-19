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

**(pending - filled before merge)**
