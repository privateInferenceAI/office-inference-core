# SES production access

The pilot SES account is in the sandbox: one verified identity, 200 messages per 24 hours, one per second, and
only verified recipient addresses. That is enough for owner-only testing and not enough for staff email.

Decide one of two paths before onboarding staff:

1. **Stay in the sandbox.** Verify each recipient address that will ever receive mail from the deployment. Fewer
   than five recipients and low volume makes this the honest choice for a small pilot.
2. **Request production access.** Removes the verified-recipient limit and raises the quota. AWS reviews the
   request. This is an owner action in the AWS console; the agent does not submit support requests.

## Request text

Use this as the request body, adjusted to the business:

> We run a private document assistant for a small business. Email is one tool the assistant may use after a person
> approves the draft. Messages go only to addresses on a connector allowlist controlled by the business owner.
> Expected volume is under 50 messages a day, mostly replies to the owner and staff. Recipients are
> opted in because they are the business's own people and its existing counterparties. Bounces and complaints
> are reviewed by the owner. We will keep the allowlist and review it when staff change. The sending identity is
> `anthony@officeinference.com`.
>
> Requested: move the account out of the SES sandbox in `us-east-1` so the allowlisted recipients do not need
> individual verification.

## After either path

- [ ] Update the connector allowlist in `rendered/host-services.env` and restart `oi-product-connector`.
- [ ] Send a governed test message and confirm it arrives.
- [ ] Attempt a message to an address outside the allowlist and confirm the refusal is in the audit chain
      (the gateway fix on 2026-11-03 records it).
- [ ] Record who may approve an email send in the onboarding record.

## Current state, 2026-11-03

SES sending enabled, identity `anthony@officeinference.com` verified, sandbox limits in place, nine messages in
the prior 24 hours from acceptance runs. Evidence: `docs/reviews/phase-e-readiness-20261103/`.
