# Cost report from Cost Explorer

**Date:** 2026-10-23. **Status:** OPS-COST-001 verified.

## What it reads

scripts/cost_report.py calls Cost Explorer GetCostAndUsage, grouped by SERVICE, for the previous month and the
current month to date. It prints and saves the total, every service line, and the provider lines separately.

The provider lines are separate Cost Explorer services, not part of the generic Amazon Bedrock line:

- Claude Haiku 4.5 (Amazon Bedrock Edition): 194.77 USD
- Claude Sonnet 4.6 (Amazon Bedrock Edition): 91.54 USD
- Cohere Rerank v3.5 (Amazon Bedrock Edition): 85.92 USD
- Claude Sonnet 4.5 (Amazon Bedrock Edition): 13.85 USD
- Amazon Bedrock: 7.70 USD
- Claude Opus 4.6 and 4.5: 0.0005 USD combined

## Current report

Account 878451098165, 2026-10-01 to 2026-10-24:

- Total: 637.52 USD
- EC2 compute: 227.50 USD
- Claude Haiku 4.5: 194.77 USD
- Claude Sonnet 4.6: 91.54 USD
- Cohere Rerank: 85.92 USD
- Tax: 3.86 USD
- S3: 2.44 USD
- VPC: 1.67 USD

The previous month is zero because this account began spending in October.

## Verification

The requirement is that a deployment using Claude shows a non-zero cost in the report. The report shows Claude
Haiku and Sonnet as non-zero provider lines, not only the generic Amazon Bedrock service. The JSON is
report-2026-10-23.json.

## Run it

    cd /opt/oi-core
    python3 scripts/cost_report.py --out /var/backups/oi-core/cost-report.json

The build-box instance role has ce:GetCostAndUsage, ce:GetDimensionValues and ce:GetCostForecast.

## Limits

This is an account-level report, not per client or per deployment. A tagged multi-tenant deployment should group
by its deployment tag once resources carry one.
