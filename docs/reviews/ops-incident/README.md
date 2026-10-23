# Incident response tabletop

**Date:** 2026-10-23. **Status:** OPS-INCIDENT-001 is verified.

The tabletop walks a workspace artifact exposed by a public object URL. The stated client-notification clock is
72 hours; the tabletop reaches the first client notice at 70 minutes and the second update at 24 hours, both
inside the clock. Containment happens before evidence export, and the notice does not wait for a root cause.

The gaps found are recorded rather than waived: the public-access setting is not probed by acceptance, and the
artifact manifest does not carry the owning job id.

## Files

- `docs/runbooks/incident-response.md`: the runbook.
- `tabletop.md`: the scenario, timeline and gaps.
- `clock.json`: the clock check.
