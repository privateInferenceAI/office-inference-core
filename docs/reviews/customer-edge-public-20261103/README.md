# Public customer path validation, 2026-11-03

The customer login and TLS path was validated on the public internet with a real certificate, before the
business hostname exists. This is the Phase D technical validation. The owner's browser walkthrough still needs
the Namecheap record for `app.officeinference.com`.

## How it was done

- Temporary hostname `100-60-208-224.sslip.io`, which resolves to the product box's Elastic IP without any DNS
  account access.
- Temporary security-group rules: 80 and 443 from the internet. Both were needed at issuance because Caddy
  chose the TLS-ALPN-01 challenge, not HTTP-01. The rules were revoked after the test.
- The edge ran with the public compose file and the final `deploy/product/edge/Caddyfile`.
- A temporary Cognito account `public-accept@officeinference.com` was created and added to the rendered
  registry with the exec group. It was deleted afterwards and removed from the registry.
- The app client allowed `https://100-60-208-224.sslip.io/oauth2/callback` for the test. The callback was
  removed afterwards.

## Certificate

Real Let's Encrypt certificate for `100-60-208-224.sslip.io`: issuer `C=US, O=Let's Encrypt, CN=YE1`,
valid 2026-11-03 to 2027-02-01. The host did not need the DNS record or the Namecheap account.

## Acceptance result

`scripts/verify/customer-path-acceptance.py` ran from the operator workstation over HTTPS with certificate
verification enabled. All seven checks passed:

| Check | Result |
|---|---|
| Public URL redirects to the login | 302 to `/oauth2/start` |
| Credentials accepted through the Cognito hosted login | 200, back to the app |
| oauth2-proxy accepts the session | 202 |
| App loads for the signed-in user | 200, shell present |
| Provider session exchange | 200, `source=provider`, expected subject |
| Cited answer through the customer path | 200, 12 citations |
| Sign-out ends the session | app redirects to the login again |

Raw evidence: `evidence.json`.

## Cleanup

- Security-group rules revoked; only 22 from the operator address and 8090 between the two boxes remain.
- Edge containers stopped.
- Temporary Cognito user deleted, callback removed, registry entry removed.
- Temporary environment and credential files removed from the box and the operator machine.
- Public port check afterwards: 22 open, 80, 443 and 8443 closed.

## What remains for the real public path

1. Namecheap `A` record for `app.officeinference.com` to `100.60.208.224`.
2. Permanent inbound 80 and 443 on `sg-05485a648e8baa4b8`.
3. Start the edge with `deploy/product/edge/docker-compose.edge.yml`; Caddy obtains the certificate for the
   real name. Keep 443 reachable during issuance for TLS-ALPN.
4. Sign in as the owner in a browser and run Phase D and Phase V through the customer URL. The owner's
   temporary password is in the operator's local scratch area.

## Finding

Caddy selected TLS-ALPN-01, so allowing only port 80 for the HTTP challenge is not enough. The security-group
prerequisite now says both 80 and 443 from the internet before the first certificate.
