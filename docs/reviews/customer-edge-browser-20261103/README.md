# Customer UI browser validation, 2026-11-03

The real app, in a real browser, signing in through Cognito over public HTTPS with a real certificate. This
closes the gap left by the API-level acceptance: it exercises the UI's provider login path, not only the
endpoint behind it.

## Setup

- Temporary hostname `100-60-208-224.sslip.io` with a real Let's Encrypt certificate, the same temporary
  security-group rules as the earlier public validation.
- Playwright Chromium, headless, from the operator machine.
- Temporary Cognito account `browser-test@officeinference.com` in the exec group. Deleted afterwards.

## Result

| Check | Result |
|---|---|
| The public URL reached the Cognito hosted login | pass |
| Credentials accepted and returned to the app | pass |
| App shell loaded for the signed-in user | pass, deployment id and nav visible |
| UI gained the provider session automatically | pass, session token in `sessionStorage`, provider flag set |
| Operator token gate hidden | pass, zero occurrences of the paste-a-token prompt |
| Question asked through the UI | pass, mileage answer with 0.67 visible |
| Console errors | zero |

The UI's auto-login behaved exactly as the API validation predicted: with no stored token it exchanged the
edge login for a provider session, stored it, and rendered the shell. A customer never sees the operator token
form.

Screenshots: `after-login.png` and `answer.png`. Raw result: `browser-result.json`.

## Cleanup

The test account was deleted, its registry entry removed, the sslip callback removed from the app client, the
edge stopped, and the temporary 80/443 rules revoked. Port check afterwards: 22 open, 80, 443 and 8443 closed.

## What remains for the real path

The Namecheap `A` record for `app.officeinference.com`, the permanent 80/443 rules, and the owner's own browser
walkthrough on the real hostname. After that, Phase E company onboarding.
