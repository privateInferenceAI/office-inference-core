# Customer path live, 2026-11-03

The customer-grade access path is live on the real hostname: `https://app.officeinference.com`.

This closes Phase D of `docs/runbooks/product-verification.md`. The operator shortcuts (SSH, minted development
sessions) remain for support and are labelled admin-only; a customer uses the browser login.

## What is live

| Item | Value |
|---|---|
| URL | `https://app.officeinference.com` |
| Certificate | Let's Encrypt, issuer `C=US, O=Let's Encrypt, CN=YE1`, valid 2026-11-03 to 2027-02-01 |
| DNS | Namecheap `A` record to `100.60.208.224` |
| Edge | Caddy and oauth2-proxy on the product box, digest-pinned images |
| Identity | Cognito pool `us-east-1_F21GZAkHU`, app client `oi-product-app`, hosted domain `oi-office-inference` |
| Owner | `anthony@officeinference.com`, permanent password set for the walkthrough, stored only in the operator's local scratch area |
| Ports | 80 and 443 from the internet; 22 from the operator address; 8443, 8080, 8088, 5678, 3000 and 4000 closed |

HTTP answers 308 to HTTPS. HTTPS answers 302 to `/oauth2/start` until the visitor is signed in.

## Acceptance, real hostname

`scripts/verify/customer-path-acceptance.py` ran against the real hostname as the owner. Seven of seven checks
passed: login redirect, Cognito credentials accepted, oauth2-proxy auth 202, app loaded, provider session 200
with `source=provider` and subject `anthony@officeinference.com`, cited answer with 12 citations, and sign-out
ending the session. Evidence: `acceptance.json`.

## Browser walkthrough, real hostname

Playwright Chromium, headless, signed in as the owner through the real Cognito login:

- App shell loaded with the deployment id and Ask/Activity navigation.
- The session token was in `sessionStorage`, the provider flag was set, and the operator token gate was absent.
- A question asked through the UI returned the cited 0.67 USD per mile answer.
- Zero console errors.

Screenshots: `owner-after-login.png` and `owner-answer.png`. Evidence: `browser-result.json`.

## Authentication and boundary checks

- A wrong password stays on the Cognito login and does not open the app.
- A staff-group account received a provider session with company roots only and was denied the executive salary
  question with a disclosure. Evidence: `authz.json`.
- The audit chain records the provider session with the person as the actor and `source=provider`.
- Operator routes, the database, the API and n8n are not reachable from the internet.

## Cleanup and state

- Test accounts (`browser-test@`, `staff-test@`) were deleted and removed from the rendered registry.
- No staging callback remains on the app client.
- The owner password is permanent now, stored in `.scratch/product-readiness/cognito-owner-password.txt` on the
  operator machine, mode 600. Change it before daily use if that file is not where you want the only copy.
- The edge, API and app are running. The backup timer is active. The C9 demo box was not touched and no AWS
  instance was deleted or changed.

## Post-launch fixes, 2026-11-03

Two defects were found while closing the Phase E email checks and fixed on the live deployment.

- **The gateway did not record connector refusals.** A non-allowlisted recipient was refused by the connector,
  but the gateway returned an error without an audit row, so the chain could not show a send blocked at the last
  boundary. Fixed in product PR #367; the live gateway image was rebuilt. A refusal now records
  `denied`/`refusal=connector` with the refused recipient. Verified: audit event 1321.
- **The oauth2-proxy healthcheck could never run.** The pinned image is distroless, so the `CMD-SHELL` healthcheck
  with wget reported unhealthy forever while the login worked. Replaced with `healthcheck: disable: true` and the
  reason in the compose file; edge health is checked from outside. Fixed in PR #369.

After both changes the owner acceptance was re-run against the real hostname: seven of seven checks passed.
Five smoke probes pass, and no gates remain open. Record for the email path:
`docs/reviews/email-path-verification-20261103/`. Record for the pilot account path:
`docs/reviews/pilot-user-rehearsal-20261103/`.

## Local resolver note

Public DNS resolves the hostname, but the operator workstation's local resolver still answered NXDOMAIN during
the validation; the acceptance script and browser were given a host-to-IP mapping for the test. If a browser on
that workstation cannot resolve the name, flush its DNS or test from another network or device. This is a
workstation resolver issue, not the deployment.

## What remains

Phase E: the company's accounts, its documents as a separate corpus, the email identity and recipient policy,
and the support path. The checklists and the corpus runbook are written; the deployment is ready for them.
