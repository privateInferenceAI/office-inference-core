# Pilot user rehearsal, 2026-11-03

A rehearsal of the account path in `docs/runbooks/pilot-users.md`, run against the live deployment with a
temporary staff account. The account was deleted afterwards.

## What was done

1. Created a Cognito account with a temporary password.
2. Added the person to the rendered registry with the `staff` group and restarted the API.
3. Signed in through `https://app.officeinference.com` in a headless browser.

## Result

| Step | Result |
|---|---|
| First sign-in | Forced to the Cognito change-password page, as intended |
| Password change | Accepted; returned to the app |
| App shell | Loaded, provider session present, operator token gate absent |
| Executive question as `staff` | Denied with disclosure: documents that answer it more directly exist and are not available to the viewer role |
| Console errors | Zero |

Screenshots: `change-password.png`, `after-first-login.png`, `staff-denial.png`. Raw result: `result.json`.

The disclosure names the viewer role rather than the staff group. That is the role the provider session maps to;
the person sees no executive content, which is the requirement.

## Cleanup

The Cognito account was deleted and the registry entry removed. The API was restarted and health checked. The
registry is back to the owner plus the demo and probe identities that onboarding removes.

## Note for onboarding

The first sign-in really does force a password change. The operator gives the person a temporary password, the
person chooses their own on first use, and the operator never handles the permanent password. That matches the
runbook.
