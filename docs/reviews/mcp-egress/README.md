# MCP egress gate

**Date:** 2026-10-23. **Status:** MCP-EGRESS-001 is verified.

## What is wired

`send_email` consults the manifest's `access.egress.frontier_classes` before the MCP gateway. The same
manifest the job boundary reads is the one the tool gate reads, so the two cannot disagree.

- Denied class: `egress_not_permitted`, no gateway call, no connector request.
- Allowed class: the body is redacted when `access.egress.redaction` is on, the gateway is called with
  `egress_approved`, and the audit row carries the data class, the egress answer and the redaction counts.
- The gateway registry lists all four data classes; the API's policy gate is the authority.

## Lane off

A job created with `data_class: regulated` against `frontier_classes: [public, internal]` was refused before
the job existed:

```json
{"error":"Permission denied on this resource (or it might not exist). This request needs a lane this deployment does not permit and has no local model to answer with.","kind":"no_permitted_lane","reason":"the deployment permits ['public', 'internal'] on a remote lane, and this request is regulated"}
```

The connector saw no request because there was no gateway call.

## Lane on

With `regulated` added to the manifest's frontier classes, job `b36f84f12f7d428fa205c844afe319e0` ran
`turn-1`, `approval-1`, `act-1`, `turn-2` and `finish` successfully. Audit row 1148:

```json
{"to":"anthony@officeinference.com","sent":true,"step":"act-1","task":"workspace_brief","tool":"send_email",
 "job_id":"b36f84f12f7d428fa205c844afe319e0","data_class":"regulated","redactions":{},
 "egress_reason":"the deployment permits regulated on a remote lane","egress_allowed":true,
 "redaction_count":0,"unverified_card_shapes":0}
```

The send reached SES. The live deployment was then restored to `frontier_classes: [public, internal]` and
the API recreated.

## The bug the test found

The first lane-on job succeeded but audit row 1133 showed `sent: false` and `egress_allowed: false` while
the manifest permitted regulated. The tool gate read the `FRONTIER_CLASSES` environment variable rather
than the manifest the job boundary used, so the boundary admitted the job and the tool refused the send.
The gate now reads the manifest first. That is why the lane-on evidence exists at all.

## Files

- `lane-off.json`: the refusal.
- `lane-on.json`: the successful job and audit row.
- `lane-on-bug.json`: the first run that exposed the env/manifest disagreement.
