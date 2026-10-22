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
