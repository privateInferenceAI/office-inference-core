# Customer edge: apply the login and TLS path

This is the operator procedure for Phase D of `docs/runbooks/product-verification.md`. It puts the customer
path in front of the product deployment: a hostname over TLS, a Cognito login, a provider session, and the app
behind it. The shape is decision 0012.

**Until this is applied the deployment has no customer login.** Everything in Phase V and Phase B runs on the
operator path (SSH tunnel and a minted development session), and the runbook labels it that way.

## What must exist before applying

1. **DNS record.** An `A` record for `app.officeinference.com` pointing to `100.60.208.224`, added at
   Namecheap (the domain uses Namecheap BasicDNS; there is no Route 53 hosted zone in the deployment account).
   This is the owner's step; it cannot be automated from the box.
2. **Security group ingress.** On `sg-05485a648e8baa4b8`, allow inbound 80 and 443 from the internet to the
   product box. Port 80 is used by the Let's Encrypt HTTP challenge and then redirects to 443. The SSH rule
   stays as it is.
3. **Cognito resources.** Run the provisioning script on the product box:

   ```bash
   python3 /opt/oi-product/scripts/cognito-provision.py            # prints the plan, changes nothing
   python3 /opt/oi-product/scripts/cognito-provision.py --apply    # creates the pool, client, domain, owner
   ```

   It creates: user pool `oi-office-inference`, app client `oi-product-app` with a generated secret, hosted
   domain `oi-office-inference`, and the owner user `anthony@officeinference.com`. It writes the OIDC and edge
   keys to `rendered/env` (mode 600) and prints the owner's temporary password once. Cost is Cognito's free
   tier for this use.

4. **The app image with the provider login.** The UI change is merged in `oic-ui` (PR #8). Rebuild the app image
   from the updated source and recreate the app container before applying the edge:

   ```bash
   cd /opt/oi-product
   docker compose -p oi-product \
     -f deploy/office/compose/docker-compose.yml \
     -f deploy/product/docker-compose.product.yml \
     -f deploy/product/edge/docker-compose.edge.yml \
     --env-file rendered/env build app
   ```

   The API image with the provider session exchange is already merged (office-inference-core PR #349); rebuild
   the API image the same way if the running image predates it.

## Apply

```bash
cd /opt/oi-product
docker compose -p oi-product \
  -f deploy/office/compose/docker-compose.yml \
  -f deploy/product/docker-compose.product.yml \
  -f deploy/product/edge/docker-compose.edge.yml \
  --env-file rendered/env up -d api app caddy oauth2-proxy
```

The edge file is `deploy/product/edge/docker-compose.edge.yml`; the Caddy configuration is
`deploy/product/edge/Caddyfile`. Caddy obtains the certificate on first request. Watch it:

```bash
docker logs -f caddy
curl -sS -o /dev/null -w '%{http_code}\n' https://app.officeinference.com/
```

## Staging validation before DNS

The chain was validated on 2026-11-03 without the public DNS record. The record is
`docs/reviews/customer-edge-staging-20261103/README.md`. In short: Caddy internal TLS on 8443, the real Cognito
login, the provider session exchange through the edge headers, and a cited answer through the customer path.

The staging files are `deploy/product/edge/Caddyfile.staging` and
`deploy/product/edge/docker-compose.edge-staging.yml`. They are test artifacts, not the public configuration.
The edge containers are stopped after a staging run; the test account is deleted and the staging callback is
removed from the app client.

## How the trust boundary works

- Caddy terminates TLS and runs the OIDC login through oauth2-proxy.
- Only after a successful login does Caddy set `X-OI-Provider-Email`, `X-OI-Provider-Subject` and the shared
  `X-OI-Edge-Secret` on the request to the app. It strips any client-supplied copies first; the secret is not
  known to the browser.
- The app's proxy passes the headers to the API. `POST /sessions/provider` refuses a missing or wrong secret,
  matches the email against the rendered users, resolves role and roots from the registry, and returns a
  session with `source=provider`.
- The browser never sees the API directly; the API is not published.

A direct connection to the app on loopback (the operator tunnel) has no edge headers, so it falls back to the
token gate on the UI and the minted development session for the API. That is the admin-only path and stays
labelled.

## Verify

Run Phase D of the product verification runbook, then Phase V end to end through the customer URL:

1. Fresh browser, `https://app.officeinference.com/`, redirect to the Cognito login, sign in with the owner.
2. The app opens without the token gate; the identity shown is the provider subject.
3. Sign out, sign in again.
4. Wrong password refused; a lower-role account cannot see executive material.
5. From outside the box, ports other than 80 and 443 are closed.
6. Activity shows the provider subject and the approval record carries `provider_verified` assurance and
   `identity_provider` basis.
7. Run the Phase V walkthrough using only the browser.

## Rollback

Stop and remove the edge containers:

```bash
docker compose -p oi-product \
  -f deploy/office/compose/docker-compose.yml \
  -f deploy/product/docker-compose.product.yml \
  -f deploy/product/edge/docker-compose.edge.yml \
  --env-file rendered/env stop caddy oauth2-proxy
```

Remove the 80 and 443 ingress rules if the box should not be reachable, and remove the DNS record if the
hostname should stop resolving. The API provider route can stay; it only answers with the edge secret, and the
secret stays in `rendered/env`. The Cognito pool is left in place because removing it deletes the accounts;
delete it only on purpose.

## Known gaps at apply time

- The product box still uses the development instance role (`oi-core-dev-instance`). Replacing it with a
  scoped role is recommended before the first client, tracked in decision 0012.
- The session cookie and the edge secret rotate by re-running provisioning and recreating the edge containers.
  There is no automatic rotation yet.
