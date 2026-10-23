# Release signing key and role

**Date:** 2026-10-23. **Status:** key and role created; sign and verify tested.

## Key

- Alias: alias/oi-key
- ARN: arn:aws:kms:us-east-1:878451098165:key/5fee7576-291d-485d-b672-b59374e40daf
- Type: asymmetric, RSA_2048
- Usage: SIGN_VERIFY
- State: Enabled
- Account: 878451098165, us-east-1

The key is managed in KMS. No private key is exported and no IAM user holds one.

## Role

- Role: arn:aws:iam::878451098165:role/oi-release-signer
- Trust: the build-box role oi-core-dev-instance and DSHAgentRole may assume it.
- Permissions: kms:Sign, kms:Verify, kms:GetPublicKey, kms:DescribeKey on the key only.
- Key policy: DSHAgentRole is the key administrator; oi-release-signer is the only signing principal; the account
  root remains the administrator of last resort.

## Test

A message was signed and verified while assuming oi-release-signer:

    aws kms sign --key-id alias/oi-key --message fileb://message --message-type RAW --signing-algorithm RSASSA_PKCS1_V1_5_SHA_256
    aws kms verify --key-id alias/oi-key --message fileb://message --message-type RAW --signature fileb://signature --signing-algorithm RSASSA_PKCS1_V1_5_SHA_256

The verify returned True. The first attempt returned AccessDenied for kms:Verify because the role had only Sign;
the role and key policy were corrected and the test repeated.

## What this does not do yet

The release artifact format, the signing script and the box-side verification are still to be built. The key and
role are the foundation; OPS-UPDATE-001 stays open until a release is signed, installed, failed and rolled back.

## Which IAM user needs the key

None. A release process assumes oi-release-signer. The account administrator manages the key policy but does not
need the private key. If a person needs to sign manually, they assume the role; they do not receive an access key.

## Files

- docs/runbooks/release-signing.md
- This record.
