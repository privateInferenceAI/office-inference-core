# Release signing

**Date:** 2026-10-23. **Status:** key and role created; sign and verify tested.

## Answer to "which IAM user needs access to the key"

None, if the key is an AWS KMS asymmetric signing key. No IAM user holds the private key. The private key never
leaves KMS. Two principals interact with it:

- A release-signing role, oi-release-signer, allowed kms:Sign, kms:Verify and kms:GetPublicKey on the key. The build box or
  CI assumes this role only during a release.
- The account administrator, who creates the key, manages its policy and can disable it. The administrator
  does not need kms:Sign for daily operations.

If you choose an offline Ed25519 key instead, the private key stays on your workstation or hardware token; the
box gets only the public key. Then no IAM principal has signing access at all.

## KMS design

- Key: alias/oi-key, asymmetric, SIGN_VERIFY, RSA_2048. Actual ARN:
  arn:aws:kms:us-east-1:878451098165:key/5fee7576-291d-485d-b672-b59374e40daf.
- Key policy: account root as key administrator; oi-release-signer gets kms:Sign, kms:Verify and kms:GetPublicKey with
  encryption context release.
- Role trust: the build-box instance role and DSHAgentRole may assume oi-release-signer.
- Release artifact: a JSON manifest with the git commit, image digests, migration list, SHA-256 of each artifact,
  and the KMS signature over the canonical JSON.
- Box verification: download the public key once, verify the release signature before installing, keep the
  previous release directory for rollback.
- Rotation: create a new key version or key, re-sign the current release, retire the old public key after one
  release cycle.

## Why not an IAM access key

A long-lived access key in an environment variable is exactly the secret that ends up in a container, a log or a
backup. KMS removes the private key from the box and makes every signature auditable in CloudTrail.

## Created

The key and role exist, and one sign and verify returned True. See docs/reviews/security-signing/README.md.
The remaining work is the release artifact, signing script and box-side verification.
