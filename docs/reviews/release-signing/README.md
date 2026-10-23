# Release signing and rollback drill

**Date:** 2026-10-23. **Status:** OPS-UPDATE-001 verified.

## Key and role

- Key: alias/oi-key, RSA_2048 SIGN_VERIFY, account 878451098165.
- Role: arn:aws:iam::878451098165:role/oi-release-signer.
- The role can Sign, Verify, GetPublicKey and DescribeKey on the key only.

## Test

1. Built release 2026-10-23-01 over the current compose hash and image ids; signature verified valid.
2. Installed the good release for the API service; health returned 200.
3. Modified release.json after signing; verify refused it.
4. Built and signed a bad release whose API image entrypoint is /bin/false. Install retagged it, the health check
   failed, and the installer restored the previous image. Health returned 200.
5. The rollback record is releases/2026-10-23-01/rollback.json on the box.

## Limits

The release is local-state based. A production path should push image digests to a registry and verify with an
offline public key. That is distribution hardening, not a missing signature or missing rollback.
