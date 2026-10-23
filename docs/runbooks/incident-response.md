# Incident response

**Scope:** this deployment. A incident is any event that could expose client content, lose audit evidence, or
take the service down. The on-call person is the deployment owner until a client names someone else.

## Clocks

- Client notification of a confirmed or suspected data exposure: within **72 hours** of the team becoming
  aware. The deployment contract names this clock; this runbook uses the same number.
- Internal escalation from detection to the incident lead: within **30 minutes**.
- Evidence preservation before remediation: within **1 hour**.
- Regulatory assessment decision: within **72 hours**.

## Steps

1. **Declare and open a timeline.** Record the detection time in UTC. Open a single incident document with
   every action and timestamp. Do not edit the audit chain.
2. **Contain.** Revoke the exposed credential, disable the affected session, or close the network path. Prefer
   the narrowest action that stops the exposure.
3. **Preserve.** Export the audit chain with an anchor before changing anything. Copy the export and anchor to
   the Object Lock bucket. Take the database snapshot.
4. **Assess.** Identify what content, whose, how much, and for how long. Read the audit rows: actor, tool,
   data class, egress verdict, redaction counts.
5. **Notify.** Tell the client within 72 hours with what is known, what is not, and the next update time. Do not
   speculate about cause.
6. **Remediate and verify.** Close the path, confirm with a probe, and keep the probe result with the timeline.
7. **Review.** Within five business days, write what failed, what changed, and the test that would catch it.

## Tabletop

The tabletop is a walkthrough, not a drill on live client data. The scenario and timeline are in
`docs/reviews/ops-incident/tabletop.md`.
