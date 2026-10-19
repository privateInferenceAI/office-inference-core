# Provider fallback: a second model behind the gateway

Written 2026-10-19. This is the measurement for the fallback added by PR #189. The raw runs, the exact rendered
gateway configuration for each measurement, and the no-fallback control are committed beside this document.

## What was built

A role may declare a second remote model in the manifest:

    models:
      analyst:
        frontier: bedrock/amazon.nova-lite-v1:0
        fallback: bedrock/amazon.nova-pro-v1:0

The renderer emits two things, and both are required: a second gateway model group `<role>-fallback` and a
`litellm_settings.fallbacks` mapping from the role to it. The gateway therefore does the failover once, and the
question path, the stream and every tool-loop call get it without a second implementation. The retry count stays
at zero: one attempt at the primary, then one at the fallback, so a provider that is already struggling is not
asked three times.

It is cloud-only and the validator enforces it. On an office or hybrid build the primary can be the local lane,
and failing over to a remote model would send a client's content off the box on an undeclared egress path. The
model-lifecycle check reads the fallback too, because a deprecated recovery path stops working exactly when it is
needed. The tracked manifests declare a default pair (Nova Lite to Nova Pro); the development deployment overrides
the pair to Claude Haiku and Sonnet, applied in `rendered/litellm.yaml` and recorded in `rendered/manifest.json`,
with the commands in runbook 6.7.

The repository's fault stub now answers on `/converse`, the path the deployed gateway's Bedrock provider
actually calls, and takes a default scenario from `OI_STUB_FAULT` because a request that arrives through the
gateway carries no per-request header. Both were found by running the first failover attempt: it produced a 404
from an unrouted path rather than the 503 the scenario describes.

## Why

Five of the 27 remaining failing runs in the row-query treatment were `provider_unavailable`, and that
treatment window carried eleven degradations. Earlier on 2026-10-19 a live question returned
`degraded / provider_unavailable`: Bedrock reported Haiku available and authorized in us-east-1, the same
invoke failed with `ServiceUnavailableException`, and Sonnet in the same region answered. A second model in the
same account is the cheapest recovery for that class of failure.

## Method

| Configuration | Primary | Fallback | Runs |
|---|---|---|---|
| Healthy, with fallback | real Bedrock Haiku | Sonnet 4.5, real Bedrock | five |
| **Fault-injected, with fallback** | fault stub, 503 on every request | Sonnet 4.5, real Bedrock | five |
| Fault-injected, no fallback (control) | fault stub, 503 | none | four-question subset |
| Healthy baseline (previous milestone) | real Bedrock Haiku | none | five |

The stub is reached by adding `api_base: http://oi-fault-stub:8099` to the analyst primary only, so the primary
fails and the fallback resolves normally. `OI_STUB_FAULT=unavailable` makes every request a 503 with the AWS
error shape. Corpus, model roles, flags and the evaluation instrument were otherwise unchanged; the instrument is
the corrected one from `docs/reviews/deterministic-failures-2026-10-19.md`.

## Results

**Fault-injected, with the fallback: 49, 49, 49, 48, 49, a mean of 48.8, zero degraded answers.** The stub's
access log records 847 failed primary attempts across the five runs, and every one was answered through the
fallback. The suite took about 550 to 580 seconds per run against about 370 to 390 for a healthy run, because
each model call pays a failed round trip and Sonnet is the larger model.

**The control: with the same broken primary and no fallback, every answer degraded.** N01 and P05 degraded and
failed, A04 staff degraded and passed only because a degraded answer counts as a decline for a question that
expects one, and A04 executive degraded and failed. That is the failure the fallback removes.

**Healthy, with the fallback configured: 51, 51, 51, 51, 49, a mean of 50.6, range 49 to 51, one degraded
answer (PR4, a timeout).** The fallback is inert while the primary works: no failover is exercised and the score
is the same 50.6 the corrected instrument measured before the fallback existed (52, 50, 51, 51, 49). The change
does not move healthy traffic.

## What this shows, and what it does not

- The failover works end to end for the question path, the tool loop and the single-shot fallback, under a total
  primary outage, with no lost answers and no answer-shaped degradations.
- The fault-injected score is about two points below the healthy baseline. That is Sonnet answering (and taking
  longer, which tightens the loop's clock) rather than answers being lost; the comparison that matters is the
  control, which loses every answer.
- **The answer and its audit row still name the configured primary model.** The gateway's response carries the
  model that actually answered (a direct call under fault returned `us.anthropic.claude-sonnet-4-5-...`), but the
  API reports the manifest model, so a failover is not visible in the record the client sees. That is a real gap
  against `INF-ROLE-001` and is recorded as follow-up work, not as done.
- One fault shape was measured: a 503 on the primary. The gateway fails over on any exception from the primary,
  but throttles, timeouts and connection errors were not separately measured.

## Caveats

- The failure is injected by a test double. The real `ServiceUnavailableException` was seen on 2026-10-19 but
  not under a measured run; the fault stub reproduces the AWS error shape.
- Five runs per configuration, one corpus, one day. The healthy baseline was measured minutes apart from the
  fault runs on the same deployment.
- Fallback calls cost money. Sonnet 4.5 is several times Haiku's price, and the analyst team spend moved from
  about 34 USD to about 55 USD across the two five-run measurements (the fault set is the expensive one, at 847
  fallback calls). A production deployment should budget the fallback deliberately rather than treat it as free.
- Cloud-only by design. An office build cannot fall back to a remote model, and the validator refuses it.

## Decision

The fallback stays configured on the development deployment: Claude Haiku primary, Claude Sonnet 4.5 fallback,
same account and region. The renderer and validator support stays for the shipped manifests, with Nova Lite to
Nova Pro as the tracked default pair. The answering-model gap above is the first follow-up.

## Evidence

- `raw/fault-injected/run-1.json` to `run-5.json`: five runs with the primary failing on every request.
- `raw/healthy/run-1.json` to `run-5.json`: five runs with the healthy primary and the fallback configured.
- `raw/control/no-fallback-subset.json`: the four-question control with the primary broken and no fallback.
- `configs/litellm-fault-with-fallback.yaml` and `configs/litellm-healthy-with-fallback.yaml`: the exact
  gateway configuration used in each measurement, including the `api_base` override.
- The fault stub is `tests/faultinject/fault_stub.py`; the run commands are in runbook 6.7.
