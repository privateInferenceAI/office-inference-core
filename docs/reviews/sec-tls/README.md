# TLS at the edge of the client tenant

**Date:** 2026-10-23. **Status:** SEC-TLS-001 is verified.

## Shape

Caddy runs on the client tenant instance in front of the gateway. The gateway listens on 127.0.0.1:8080
only. Caddy listens on 80 and 443 and obtains a Let's Encrypt certificate for the IP-derived name
`3-85-130-96.sslip.io`, which resolves to the tenant's public address without a DNS change.

Security group ingress after the change: 22, 80 and 443. Port 8080 was removed.

## Evidence

- `https://3-85-130-96.sslip.io/health` returns `{"status": "ok"}` with a valid certificate.
- `http://3-85-130-96.sslip.io/health` returns 308 to the HTTPS URL.
- External scan of the tenant address: 22, 80 and 443 open; 3000, 4000, 5432, 8025, 8080, 8088, 8090 and 8094 closed.
- Job `57309daac87244d9acea35f4f65dbffb` went through the HTTPS gateway; its final state was failed verification, but audit row 1242 records `sent: true`.

## Files

- `Caddyfile`: the proxy configuration.
- `https.json`: health, redirect and certificate result.
- `port-scan.json`.
- `audit-row.json`: row 1242.
