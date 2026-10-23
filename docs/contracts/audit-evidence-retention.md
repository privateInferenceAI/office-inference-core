# Audit evidence retention

This is the deployment contract's retention clause for audit evidence. It must name the same mode and period
as the bucket configuration in the client's account.

The deployment stores audit evidence in the bucket named in its manifest. For the development deployment that
bucket is `oi-core-audit-dev`.

- Object Lock: enabled.
- Default retention mode: GOVERNANCE.
- Default retention period: 90 days.

An object written to the bucket inherits the 90-day GOVERNANCE retention. Deleting or overwriting it before
the period ends requires a principal with `s3:BypassGovernanceRetention`. That permission is not granted to the
API, the gateway, the connector or the sandbox. Only the account administrator holds it.

Changing the mode or the period is a contract change and a bucket configuration change together. A deployment
whose bucket names a different mode or period from this clause is not installable under this contract.
