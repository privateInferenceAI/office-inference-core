# Customer edge staging validation, 2026-11-03

The full customer login chain was validated on the product box before the public DNS record exists. This is
staging evidence, not the public acceptance. The public path still needs the Namecheap A record, the 80/443
security-group rules, and a real certificate.

## What was created

Cognito in account `878451098165`, region us-east-1:

| Resource | Value |
|---|---|
| User pool | `us-east-1_F21GZAkHU`, name `oi-office-inference` |
| App client | `oi-product-app`, generated secret, code flow, scopes openid email profile |
| Hosted domain | `oi-office-inference.auth.us-east-1.amazoncognito.com` |
| Owner | `anthony@officeinference.com`, temporary password, first sign-in changes it |

A separate `staging-test@officeinference.com` account was created for the test, then deleted. The owner's
temporary password was rotated after the test and is not in this repository.

## How it was staged

- The edge ran on port 8443 with Caddy's internal certificate, using
  `deploy/product/edge/docker-compose.edge-staging.yml` and `Caddyfile.staging`.
- `app.officeinference.com` was resolved to `127.0.0.1` only inside the test process, so no DNS record or
  public security-group change was needed.
- oauth2-proxy used the real Cognito issuer and app client. The redirect URL was
  `https://app.officeinference.com:8443/oauth2/callback`, which the app client allowed for the test and which
  was removed afterwards.

## Results

| Step | Result |
|---|---|
| Open the edge URL | 302 to `/oauth2/start`, then 302 to the Cognito hosted login |
| Sign in with the staging account | login accepted, redirect back to the app |
| `GET /oauth2/auth` | 202 from oauth2-proxy |
| Load the app | 200, app shell present |
| `POST /api/sessions/provider` through the edge | 200, `source: provider`, subject `staging-test@officeinference.com`, role viewer, roots company and executive |
| `POST /api/ask` with the provider token | 200, denial false, mileage answer at 0.67 USD per mile, 12 citations |

The provider exchange returned 401 until the Caddy header handling was corrected, then 400 until the identity
header mapping was corrected. Both were edge configuration defects, fixed in the files this record ships with.
The final chain passed end to end. Evidence, with transient login URLs and cookies removed, is in
`evidence.json`.

## Cleanup after the test

- `staging-test@officeinference.com` deleted.
- The staging callback `https://app.officeinference.com:8443/oauth2/callback` removed from the app client.
- The owner's temporary password rotated. It is stored only in the operator's local scratch area, not here.
- The staging edge containers were stopped.

## What is still needed for the public path

1. A record for `app.officeinference.com` at Namecheap pointing to `100.60.208.224`.
2. Inbound 80 and 443 on `sg-05485a648e8baa4b8`.
3. Start the edge with `deploy/product/edge/docker-compose.edge.yml` and Caddy obtains the real certificate.
4. Run Phase D of the product verification runbook in a browser and sign in as the owner.
5. Rebuild the app image with the merged UI before step 3; the box was rebuilt during this validation, so this
   may already be current.

## Findings carried into the repo

- Caddy's `forward_auth` returns 401 by default. The edge now redirects an unauthenticated browser to
  `/oauth2/start` instead of showing plain text.
- `header_up` removal and addition in one reverse_proxy block did not strip the headers reliably. Stripping is
  now a separate `request_header -...` directive, and the proxy block only sets values.
- Volume paths in overlay compose files resolve against the first compose file's directory. The Caddyfile mount
  is absolute now; a relative path pointed at the wrong directory and Caddy failed to start.
- The API container needs `OI_EDGE_SECRET` in its environment, not only in `rendered/env`. Added to the product
  overlay.
