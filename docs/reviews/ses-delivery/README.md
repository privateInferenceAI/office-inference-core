# SES delivery from the client tenant

**Date:** 2026-10-23. **Status:** the tenant connector sends real mail through SES.

## Identity

- Account: badj.ai, 426063972668, us-east-1.
- Identity: awstest@officeinference.com, type EMAIL_ADDRESS.
- Verification: SUCCESS. SES reports VerifiedForSendingStatus true.
- Account mode: sandbox. ProductionAccessEnabled false. Only verified recipients receive mail until production
  access is granted.

The Office Inference account still has its own verified anthony@officeinference.com identity. That identity
belongs to account 878 and does not transfer to the tenant.

## Connector configuration

The tenant systemd unit client-connector now uses:

    SES_REGION=us-east-1
    SMTP_FROM=awstest@officeinference.com
    CONNECTOR_ALLOWED_RECIPIENTS=awstest@officeinference.com

The IAM role client-connector-ses allows ses:SendEmail and ses:SendRawEmail on the badj.ai and the new address
identities. The connector still binds to loopback 8094 and the gateway still calls it over loopback.

## Evidence

Direct connector send:

    {"message_id":"010001a168a4cce3-822909a4-90a3-44e9-b86b-b1da1ef223ab-000000",
     "sent":true,"subject":"SES delivery test","to":"awstest@officeinference.com","transport":"ses"}

Send through the public HTTPS gateway with a signed session assertion:

    {"ok":true,"result":{"message_id":"010001a168a4de5f-bde8d6d8-e56f-4073-bce5-cc6a541e2692-000000",
     "sent":true,"subject":"SES through gateway","to":"awstest@officeinference.com","transport":"ses"},
     "tool":"send_email"}

SES accepted both messages and returned message ids. The recipient inbox is the catchall at
awstest@officeinference.com.

An API job was attempted with the same recipient, but the model did not reach the act step and the job failed
verification. The direct gateway call is the transport proof; the job failure is the model verification issue
recorded elsewhere.

## Limits

- Sandbox: only verified recipients. For a real client, request SES production access in account 426.
- The sender is on officeinference.com but the SES identity lives in the badj tenant. That is fine for the
  development proof and wrong for the independence promise. A production client connector uses the client's own
  SMTP or SES identity.
- Mailpit remains on the tenant as a capture fallback. It is no longer the connector's SMTP target.
