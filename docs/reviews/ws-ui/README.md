# Workspace UI, artifact panel

**Date:** 2026-10-23. **Status:** WS-UI-001 is verified.

## What changed

The client app is `privateInferenceAI/oic-ui`. Its job view already showed the run, the steps, the approval and
the record. It did not show the files a workspace task produced.

PR `privateInferenceAI/oic-ui#1` adds:

- `api.artifacts(token, jobId)`, reading `GET /jobs/{id}/artifacts`.
- `api.artifactBlob(token, jobId, sha256)`, fetching the hash route with the session header.
- An Artifacts section on the job view that lists each path and size and downloads the blob as a save.
- A component test for the list, the size, the authenticated request and the download click.
- Styles for the artifact rows.

The section is quiet when the deployment has no artifact route, so an older service does not make the job view
look broken.

## Evidence

```
$ npm --prefix app test
Test Files  8 passed | 1 skipped (9)
Tests       162 passed | 12 skipped (174)

$ npm --prefix app run typecheck
(no output, exit 0)
```

The existing job view tests cover the state sentence, the step list, the approval gate, the event record and the
stall line. The new test covers the artifact panel. The browser suite in `app/e2e` remains the deployment-level
check when `OI_TOKEN` names a live session.

## What is not here

The browser suite was not run against the box in this slice. The live API routes it exercises are covered by the
workspace artifact evidence in `docs/reviews/ws-artifacts`.
