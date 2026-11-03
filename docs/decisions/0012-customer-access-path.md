# 0012: Customer access path for the product deployment

Status: proposed. Needs owner confirmation on the hostname, the identity account, and whether the product box
is the business pilot. Written 2026-11-03.

## The question

The product deployment is ready enough for the operator walkthroughs, but every check so far used the admin
path: SSH to the box, a tunnel to the app, and a development session minted by the session CLI. A customer
cannot use that. Daily business use needs what a customer actually touches: a real URL over TLS, a login, an
identity the audit record can name, and no operator routes exposed.

This decision sets the shape. It does not create the resources; the created resources are listed at the end.

## What exists now

- The app and API listen on loopback. The app is reached with an SSH tunnel, and the API is only reachable from
  inside the box or its containers.
- Sessions are minted by `session mint --dev`, which is explicitly a development tool. There is no login page.
- `officeinference.com` uses Namecheap BasicDNS (`dns1.registrar-servers.com`), with the apex and `www` pointing
  at GitHub Pages. There is no Route 53 hosted zone in the deployment account; the account has no ACM
  certificates.
- The product box runs under the development instance role `oi-core-dev-instance`. That role is broader than a
  customer deployment should have, including the release signer and SES send.
- C7 was verified at the client-tenant level for the MCP gateway with Caddy and TLS. Nothing in the repository
  implements application login; the Terraform modules in `deploy/cloud` are comments describing an open
  question, not a built edge.

## Requirements

- HTTPS on a real hostname, certificate renewals automatic, plain HTTP redirected.
- A login a customer uses, with logout, and a recovery path for a forgotten password.
- Sessions that name the provider identity in the audit chain, with `source: provider` rather than
  `development`.
- Roles and document roots mapped from the identity provider groups to the rendered access registry.
- The operator paths (SSH, tunnel, minted sessions, database, n8n admin listener, secrets) not reachable by a
  customer.
- No new customer-visible behaviour that cannot be explained in the runbook.

## Options

| Option | Shape | Verdict |
|---|---|---|
| A. Caddy + OIDC proxy + Cognito on the product box | Caddy terminates TLS on the box, an OIDC proxy handles the browser login with a Cognito user pool, and the API exchanges a verified provider token for a session | **Recommended for the pilot.** Cheapest, one box, reuses the Caddy pattern already proven in the client tenant |
| B. ALB + ACM + Cognito | AWS-managed edge in front of the box, Cognito authentication at the listener; the app still needs the provider session exchange | Later, when there are several clients or a client-owned account wants an AWS-native edge. Adds ALB cost and Terraform work |
| C. Caddy with local passwords | No identity provider | Rejected. It is not a customer login, it has no recovery story, and it does not produce a provider identity in the record |

## Recommendation: option A, pilot shape

1. DNS: add `app.officeinference.com` A record to `100.60.208.224` at Namecheap. This is a manual owner step,
   because the domain is not in Route 53 and no DNS API credential exists on this machine.
2. Security group `sg-05485a648e8baa4b8`: add inbound 80 and 443 from the internet to the product box. 80 is
   needed for the Let's Encrypt HTTP challenge and for the redirect; 443 is the customer path.
3. Caddy container on the box: TLS with automatic certificates, reverse proxy to `app:8080`, and a login gate
   through the OIDC proxy. The app and API stay unpublished on the host.
4. Cognito in account `878451098165`: one user pool for Office Inference, one app client, hosted UI domain.
   Anthony is the first owner. Groups map to the rendered registry, for example `oi-owners` and `oi-staff`.
   MFA is available and recommended once the account is in real use.
5. API change: add a provider session exchange. The API verifies a Cognito ID token against the pool's JWKS,
   maps the subject and groups to the access registry, and mints a session with `source: provider`, a short TTL
   and a refresh path. The existing development mint stays for the operator only.
6. Operator paths stay as they are and stay labelled admin-only. The n8n UI stays on loopback; if it needs a
   customer-facing URL later, it gets its own hostname and its own gate.

## What gets created or changed

| Resource | Action | Cost |
|---|---|---|
| Namecheap A record `app.officeinference.com` | Owner adds it; no API credential on the machine | none |
| SG `sg-05485a648e8baa4b8` | Add inbound 80/443 from the internet | none |
| Cognito user pool, app client, hosted domain | Create in account `878451098165` | Cognito free tier covers this use |
| Caddy container and config on the box | Add to the deployment overlay and systemd or compose | existing box |
| API provider session exchange | Code and tests | existing box |
| Scoped product instance role replacing `oi-core-dev-instance` | Separate hardening item, recommended before the first client | none |

No ECS, ALB, Route 53 zone or certificate is created for the pilot. TLS certificates are obtained by Caddy on
the box.

## Work and acceptance

Roughly two to three days after the DNS record exists: provider token verification and session exchange with
tests, Caddy and OIDC configuration, Cognito setup, then Phase D of the product verification runbook.

Acceptance is Phase D, steps D2 to D8: fresh browser login, logout, wrong password and lower-role refusal,
denied executive content for a lower role, no operator routes reachable from the internet, audit rows naming
the provider subject with `source: provider`, and the whole Phase V walkthrough done without a tunnel.

## Open decisions

1. Hostname: `app.officeinference.com` suggested. Confirm or choose another subdomain.
2. Identity: Cognito user pool in `878451098165`, confirmed above by default. If Office Inference should use a
   different identity provider, say so before the API exchange is built.
3. Deployment: this product box is the business pilot, with a fresh company corpus beside the synthetic
   corpus. A dedicated client deployment comes at the first client.
4. The scoped instance role: after the customer path works, or before the first client? Recommended before the
   first client, not before the pilot login.

## Notes on the licence of the edge

Caddy is Apache-2.0 and stays in the client's deployment. Cognito is an AWS service in the account. Nothing in
this path changes the n8n licence conditions recorded in decision 0011: one isolated client instance, no
white-labelling, no resale, no payment-gated access.

## Implementation status, 2026-11-03

The pilot shape is built and has been validated in staging. The public path is waiting on the DNS record and the
security-group rules.

Created in account `878451098165`, region us-east-1:

| Resource | Value |
|---|---|
| Cognito user pool | `us-east-1_F21GZAkHU`, name `oi-office-inference` |
| App client | `oi-product-app`, generated secret, code flow, openid/email/profile, callback `https://app.officeinference.com/oauth2/callback` |
| Hosted domain | `oi-office-inference.auth.us-east-1.amazoncognito.com` |
| Owner account | `anthony@officeinference.com`, temporary password, first sign-in changes it |
| Edge keys | in `/opt/oi-product/rendered/env`, mode 600, written by the provisioning script |

Built and merged: API provider session exchange (PR #349), UI provider login (oic-ui PR #8), edge configuration
and runbook (PRs #350, #351, #353), Cognito provisioning script (oi-canonical-stack PRs #52, #53).

Staging validation on 2026-11-03 passed end to end against the real Cognito pool: login, oauth2-proxy auth 202,
app 200, provider session with `source=provider`, and a cited answer through the customer path. Evidence:
`docs/reviews/customer-edge-staging-20261103/README.md`. The staging account was deleted, the staging callback
removed, the owner temporary password rotated, and the staging edge containers stopped.

Remaining before the public path is live:

1. An `A` record for `app.officeinference.com` at Namecheap pointing to `100.60.208.224`.
2. Inbound 80 and 443 on `sg-05485a648e8baa4b8`.
3. Start `deploy/product/edge/docker-compose.edge.yml`; Caddy obtains the real certificate.
4. Sign in as the owner in a browser and walk Phase D and Phase V through the customer URL.
5. Confirm the app image carries the merged UI before step 3.

The staging validation also found and fixed three edge defects: the unauthenticated response is a redirect now,
header stripping is a separate directive, and the Caddyfile mount is absolute. The API container needed the edge
secret in its environment, which is now in the product overlay.
