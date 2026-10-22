# Bedrock capacity and throttling: what production teams do, 2026-10-22

**Why this exists.** The accuracy configuration is correct but cannot always be measured: Bedrock returns
"Too many tokens per day" after roughly 24 to 25 Sonnet calls in this account, the gateway falls back to Haiku or
Nova, and the score looks fine while measuring a different model. That is a capacity problem, not a model-quality
problem, and clients will hit a version of it. This records the researched facts, our account's observations, and
the product decisions that follow.

## Our account's observation

- Service Quotas in account 878451098165, us-east-1: `Cross-Model Max Tokens Per Day` = 150,000,000,
  non-adjustable; Sonnet 4.6 cross-region TPM = 6,000,000 and RPM = 50.
- Repeated measurement attempts reached about 24 to 25 Sonnet calls, then Converse returned HTTP 429
  `ThrottlingException: Too many tokens per day`. Prompts up to roughly 12,000 tokens continued to succeed
  while 15,000-token prompts failed, which is consistent with a nearly exhausted daily allowance rather than a
  per-minute limit.
- No AWS document states the reset clock. Small probes passed at 20:21 Eastern and larger ones failed minutes
  later; the effective refund time is unconfirmed for this account.
- CloudWatch over 24 hours: 13.4M Sonnet input tokens and 24.5M Haiku input tokens. The published cross-model
  default in AWS documentation is 700M per account per Region; our granted value is 150M, and the practical
  usable value appears lower still after repeated attempts.

## What AWS documents

- Quotas are per account, per model, per Region, and both RPM and TPM are enforced together. Published defaults
  are not granted quotas: AWS says defaults may vary with region, account age, usage history and payment history.
- Tokens burn faster than raw counts: output tokens carry a 5x burndown for these Claude models, and admission
  reserves input plus max_tokens before reconciling at completion.
- `Cross-Model Max Tokens Per Day` is account-and-Region scoped and spans models, so one workload can exhaust the
  day for another.
- Cross-region inference profiles spread load across Regions. A profile is not application failover; the SDK does
  not move an impaired source Region to an independent one.
- Higher limits come from three paths: Service Quotas increase requests on cross-region TPM and the cross-model
  daily quota (support decides; quotas marked non-adjustable are not self-service), Provisioned Throughput model
  units with hourly billing and 1- or 6-month commitments, or the reserved service tier through the account team.
- Provider-side tools that reduce on-demand burn: prompt caching (cache reads do not count toward TPM; cache writes
  do), flex and priority service tiers, batch inference, and intelligent prompt routing.

## What production teams do

- Treat quota as a design input. Capacity is assessed before launch, alarms are deployed, and no vendor promises
  an RPM or TPM number that AWS has not granted.
- Put a gateway in front: per-tenant RPM/TPM limits, token budgets, max_tokens caps, retries with exponential
  backoff, cooldowns, a fallback chain, and a documented degraded mode.
- Keep more than one capacity pool: account sharding (quotas are per account), a second Region with a cross-region
  profile, and a second model family. Account sharding is an AWS-documented pattern with a working sample.
- Reserve for the baseline and burst on demand: Provisioned Throughput or the reserved tier for sustained load,
  standard on-demand for bursts, priority for critical peaks, flex or batch for offline work.
- Reduce tokens rather than buying more: routing hard tasks to the strong model and easy tasks to a cheap model,
  semantic caching, summarisation, and prompt caching.
- Monitor the right signals: `InvocationThrottles` (warning and page thresholds after several minutes),
  `EstimatedTPMQuotaUsage` at 70 and 85 percent of the granted value (recomputed from Service Quotas, and not the
  only signal because throttling also accounts for reserved consumption), `InvocationClientErrors` because quota
  rejections land there, token volume, and p99 latency.

## What this means for Office Inference

The product cannot promise a full-time AI business on Bedrock on-demand alone. A client account is the right unit
because the quota, bill and CloudWatch metrics belong to the client; the deliverable is a capacity assessment, a
quota request runbook, deployed alarms, and a named owner, not a performance number in a contract.

Concrete decisions:

1. Installer records the granted quotas for each model and Region, compares them with the accuracy profile's
   measured token cost per answer, and fails loudly when the account is below the design minimum.
2. The answer path keeps two profiles. `accuracy` is the measured configuration; `economy` is the deployment
   shape for small quotas. The profile is chosen at install, not discovered after throttling.
3. The gateway keeps a fallback chain for availability, but a fallback is visible in the answer metadata and the
   audit row, and measurement runs refuse to score a run that used one.
4. Offer Provisioned Throughput or the reserved tier for clients who need sustained throughput, and say so in the
   capacity assessment rather than waiting for the first throttle.
5. Add prompt caching, token caps and model routing as cost controls after correctness is measured, not before.
6. Keep the customer-owned-account model. It is also the cleanest answer to quota: the client owns the limit and
   the escalation path.

## Sources

- Bedrock quotas and runtime rules: https://docs.aws.amazon.com/bedrock/latest/userguide/quotas.html,
  https://docs.aws.amazon.com/bedrock/latest/userguide/quotas-runtime.html
- Token burndown: https://docs.aws.amazon.com/bedrock/latest/userguide/quotas-token-burndown.html
- Global and cross-Region inference: https://docs.aws.amazon.com/bedrock/latest/userguide/global-cross-region-inference.html
- Provisioned Throughput: https://docs.aws.amazon.com/bedrock/latest/userguide/prov-throughput.html
- Service tiers: https://docs.aws.amazon.com/bedrock/latest/userguide/service-tiers-inference.html
- Capacity optimisation: https://docs.aws.amazon.com/bedrock/latest/userguide/capacity-limits-cost-optimization.html
- Monitoring: https://docs.aws.amazon.com/bedrock/latest/userguide/monitoring-runtime-metrics.html
- AWS account-sharding sample: https://github.com/aws-samples/sample-resilient-llm-inference
- LiteLLM routing, fallbacks, budgets: https://docs.litellm.ai/docs/routing
- Practitioner quota report: https://www.proactiveops.io/archive/amazon-bedrock-leaves-builders-stuck-in-1st-gear
- AWS Builder Center on throttling: https://builder.aws.com/content/3IXe7Wh7blqDDkaGwPhHaPDVyQP/amazon-bedrock-throttling-beat-429s-in-production
- Bedrock error codes: https://docs.aws.amazon.com/bedrock/latest/userguide/troubleshooting-api-error-codes.html

## Open questions

- The actual reset clock for the cross-model daily quota in this account.
- The account's granted per-model values versus the published defaults.
- Whether a Service Quotas increase for Sonnet 4.6 TPM and the cross-model daily quota would be granted to this
  account, and how long it would take.
- Provisioned Throughput availability and cost for the model and Region a client would use.


## Refinement from a third research pass, 2026-10-22

Three findings change the practical plan.

**Provisioned Throughput does not cover Claude 4.x.** The supported-model table lists only Claude 3 Haiku and
Claude 3 Sonnet, and only in ap-south-1. Provisioned Throughput cannot solve Sonnet 4.6 or Haiku 4.5 throttling.
The 2026 route to dedicated capacity for these models is the Reserved service tier: dedicated capacity separate
from the on-demand pool, minimum 100,000 input TPM and 10,000 output TPM, one- or three-month terms, account-team
only, with automatic overflow to Standard. Priority and Flex remain on the shared on-demand quota; only Reserved is
separate. Do not recommend Provisioned Throughput to a client for Claude 4.x.

**The quota stack is more precise than one daily number.** The cross-model daily pool is one aggregate per account
per Region. Cross-region TPM and RPM are adjustable in Service Quotas; the daily quota is not a console slider, but
AWS's documented path is to request an increase on the cross-region TPM quota for the model, and support bundles the
daily increase with it. Legacy per-model daily quotas still exist for older models. Requests can be denied, and
priority goes to accounts already consuming their allocation. A bare request without evidence is likely to be
denied; include request IDs, CloudWatch token counts, InvocationThrottles and spend evidence.

**Quota units and max_tokens matter more than they look.** Quota units per request are input plus five times
output. Admission reserves input plus max_tokens, so the 64K default reserves 64K and crushes concurrency; the
recommended cap is 4K for chat and 8K to 16K for code. Prompt-cache reads are excluded from the quota while cache
writes count. Batch inference runs at roughly half price with separate quotas and suits nightly jobs. Flex is
cheaper and slower but still draws on the same on-demand pool.

**Why our account ran out after about 24 calls.** At documented accounting, 25 calls could not exhaust a
150,000,000 daily pool unless each call is large. Two explanations fit: the applied quota is lower than the
displayed default, or a "call" in the observation is a user turn containing 20 to 30 agent requests. Our audit
counts model calls, so the first explanation is more likely for this account. The next step is to compare the
applied quota with the default, count API requests rather than prompts, and file the cross-region TPM increase
with the evidence.

**Adjusted recommendations.** Per-client AWS accounts remain right. Quote the Reserved tier or Priority for
service-level work; never promise on-demand throughput. Cap max_tokens per task, cache stable prompts, move
non-interactive work to batch, use a cross-region profile, and build the fallback chain with bounded retries and a
queue. File quota increases early and budget days. Do not start new work on Legacy models; Sonnet 4.5 is Legacy
and Haiku 4.5 is near its end-of-life window.


## Daily token counts, and the billing-table question (2026-10-22)

The billing export shows total Bedrock cost on 10/20 higher than 10/21, which looks like the daily allowance was
lowered. CloudWatch token counts do not support that reading. Buckets are UTC days, sums from AWS/Bedrock
InputTokenCount and OutputTokenCount:

| Model | 10/19 | 10/20 | 10/21 |
|---|---|---|---|
| Claude Haiku 4.5 input / output | 37.1M / 0.92M | 22.3M / 0.54M | 25.6M / 0.40M |
| Claude Sonnet 4.5 input / output | 3.48M / 0.08M | none | 9 / 4 tokens |
| Claude Sonnet 4.6 input / output | none | none | 14.2M / 0.15M |

Total recorded Bedrock tokens were about 41.5M on 10/19, 22.8M on 10/20 and 40.3M on 10/21. So 10/21 was not a
lighter day; it was comparable to 10/19 and almost twice 10/20. The cost table is lower because Claude Sonnet 4.6
has no column in that export and appears only partly under the generic Bedrock line, and because the Haiku mix was
different. The billing table is not evidence that AWS lowered the quota.

What did change on 10/21 is the model: Sonnet 4.5 has a published per-model daily allowance of 27M tokens, while
Sonnet 4.6 has no published per-model daily allowance and draws from the shared cross-model pool, which AWS
describes as estimated from on-demand pricing. A price-weighted estimate makes Sonnet tokens count more than
Haiku tokens. The applied quota for Sonnet 4.6 in this account, and the exact weighting, remain unconfirmed.

The owner decided not to open a support ticket: the measurement campaign is finite, DeepSeek is available through
Bedrock as a test model, and the three accounts can be sharded once the credits are shared in November.

## Test-only verification without the Sona quota

Amazon Bedrock in us-east-1 lists `deepseek.v3.2` (DeepSeek V3.2, ACTIVE) and a Converse call with that model id
answers in this account, with the existing instance-role policy already allowing it. That gives a test-only answer
model inside AWS and inside the same gateway, with no external API key and a separate capacity pool. It is a
verification and fallback-testing model, not a replacement for the production Claude configuration.
