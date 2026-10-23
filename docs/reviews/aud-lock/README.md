# Object Lock matches the contract

**Date:** 2026-10-23. **Status:** AUD-LOCK-001 is verified.

## Bucket configuration

`oi-core-audit-dev` in account 878451098165:

```json
{"ObjectLockEnabled":"Enabled","Rule":{"DefaultRetention":{"Mode":"GOVERNANCE","Days":90}}}
```

## Contract clause

`docs/contracts/audit-evidence-retention.md` names the same bucket configuration:

- Object Lock enabled.
- Default retention mode: GOVERNANCE.
- Default retention period: 90 days.

It also records who may bypass retention: only the account administrator, not the API, gateway, connector or
sandbox.

## What this checks

The row is a consistency check between the contract and the bucket. Both name GOVERNANCE and 90 days. If either
changes alone, the deployment is not installable under this contract. The clause is a deployment contract
draft; counsel review is tracked separately by BIZ-LEGAL-001 and BIZ-CONTRACT-001.

## Files

- `bucket-config.json`: the live Object Lock configuration.
- `contract.md`: a copy of the clause in `docs/contracts/audit-evidence-retention.md`.
