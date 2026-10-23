# Five alarms and paging

**Date:** 2026-10-23. **Status:** OPS-ALARM-001 verified.

## Destination

- SNS topic: arn:aws:sns:us-east-1:878451098165:oi-core-alarms
- Subscription: email, anthony@officeinference.com, confirmed
- Subscription ARN: arn:aws:sns:us-east-1:878451098165:oi-core-alarms:29aba45f-6a9f-4a83-a30d-7b0977f3a475

## The five alarms

| Alarm | Metric | Rule |
|---|---|---|
| oi-core-CoreHealthy | OI/Health CoreHealthy | API, Postgres or LiteLLM unhealthy for 10 minutes |
| oi-core-IngestionHealthy | OI/Health IngestionHealthy | ingestion healthcheck failing for 10 minutes |
| oi-core-BackupAge | OI/Health BackupAgeHours | newest backup older than 26 hours |
| oi-core-AuditChain | OI/Health AuditChainValid | audit export or verify failing for 10 minutes |
| oi-core-Disk | OI/Health RootDiskUsedPercent | root disk above 80 percent for 10 minutes |

All five treat missing data as breaching.

## Publishing

- scripts/publish_health_metrics.py runs through oi-health-metrics.timer every five minutes.
- scripts/check_audit_chain.py runs through oi-audit-check.timer daily at 03:10 UTC and writes
  /var/lib/oi-health/audit-chain.json.
- The publisher reads the audit marker, the newest local backup, Docker healthchecks and the root filesystem.

## Test

Each alarm was deliberately set to ALARM and back to OK. CloudWatch history shows every transition. After the
email subscription was confirmed, the five alarms were triggered again; SNS accepted each transition with the
confirmed subscription ARN. The inbox receipt is the remaining human observation.

If an email did not arrive, check the SNS subscription and the spam folder before changing the alarms.
