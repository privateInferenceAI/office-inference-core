# Five alarms and paging

**Date:** 2026-10-23. **Status:** built; the email subscription is awaiting confirmation.

## Destination

- SNS topic: arn:aws:sns:us-east-1:878451098165:oi-core-alarms
- Subscription: email, anthony@officeinference.com
- AWS sent the confirmation email. Until it is clicked, SNS records the publish but does not deliver to the inbox.

## The five alarms

| Alarm | Metric | Rule |
|---|---|---|
| oi-core-CoreHealthy | OI/Health CoreHealthy | API, Postgres or LiteLLM unhealthy for 10 minutes |
| oi-core-IngestionHealthy | OI/Health IngestionHealthy | ingestion healthcheck failing for 10 minutes |
| oi-core-BackupAge | OI/Health BackupAgeHours | newest backup older than 26 hours |
| oi-core-AuditChain | OI/Health AuditChainValid | audit export or verify failing for 10 minutes |
| oi-core-Disk | OI/Health RootDiskUsedPercent | root disk above 80 percent for 10 minutes |

All five treat missing data as breaching. That is deliberate: a metrics publisher that dies is a failure to
notice, not a reason to stay silent.

## What publishes the metrics

- scripts/publish_health_metrics.py runs through oi-health-metrics.timer every five minutes.
- scripts/check_audit_chain.py runs through oi-audit-check.timer daily at 03:10 UTC and writes
  /var/lib/oi-health/audit-chain.json.
- The publisher reads the audit marker, the newest local backup, Docker healthchecks and the root filesystem.

## Self-test

Each alarm was deliberately set to ALARM and then back to OK. CloudWatch history shows the transition for all
five:

- CoreHealthy: OK to ALARM to OK
- IngestionHealthy: OK to ALARM to OK
- BackupAgeHours: ALARM (stale marker from before the first backup) to OK, then ALARM to OK
- AuditChainValid: OK to ALARM to OK
- RootDiskUsedPercent: OK to ALARM to OK

The SNS publish is recorded on each transition. The email confirmation is the only remaining step before the
notification reaches the inbox.

## Remaining

After the subscription is confirmed, trigger one alarm and confirm the email arrives. Then OPS-ALARM-001 can be
marked verified. The alarm set does not include cost or certificate expiry; those are separate rows.
