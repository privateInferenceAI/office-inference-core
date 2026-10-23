# MCP egress gate (work in progress)

**Status:** not verified. MCP-EGRESS-001 stays open.

## What is wired

`send_email` now asks the existing `routing.gate` before calling the MCP gateway:

- `FRONTIER_CLASSES` from the manifest's `access.egress.frontier_classes`;
- a denied class returns `egress_not_permitted` without a gateway call;
- an allowed class redacts the body when `EGRESS_REDACTION=on` and records
  `data_class`, `egress_allowed`, `egress_reason` and the redaction counts on the audit row.

The gateway's tool registry now lists all four data classes; the API gate is the policy point.

## Evidence

Lane off, job created with `data_class: regulated`:

```json
{"error":"Permission denied on this resource (or it might not exist). This request needs a lane this deployment does not permit and has no local model to answer with.","kind":"no_permitted_lane","reason":"the deployment permits ['public', 'internal'] on a remote lane, and this request is regulated"}
```

The request was refused before a job existed, so the connector saw no request.

## What failed

With the manifest's frontier classes extended to include `regulated`, the regulated
`workspace_brief` job failed at `turn-1` with `outcome_reason: verification` and never reached
the `act` step. The egress gate was therefore not exercised end to end. The three lane-on
attempts are recorded in `lane-on-failure.json`.

## Still to do

- Find why the regulated job fails verification before the act step.
- Run the same send job with the lane on and read the audit row's egress fields.
- Record the connector request count to prove the lane-off refusal never reached it.

## Deployment note

The live deployment was restored to `frontier_classes: [public, internal]` and the API restarted
after the test. This branch is not merged.
