# Tabletop: a workspace artifact exposed by a public object URL

Scenario: a report is found at a public S3 URL for three hours before anyone notices. The object is a workspace
artifact with internal content. No credential was posted.

Stated clock: client notification within 72 hours of awareness.

| Time (UTC) | Actor | Action |
|---|---|---|
| T+0 | Analyst | Finds the public URL in a browser history and opens the incident. |
| T+8 min | Incident lead | Declares a data-exposure incident. Opens the timeline document. |
| T+14 min | Platform | Applies a bucket policy denying public reads. Confirms the URL returns 403. |
| T+27 min | Platform | Exports the audit chain and anchors it to the Object Lock bucket. |
| T+46 min | Analyst | Identifies the object, the owning job, the data class and the 3-hour window. |
| T+70 min | Incident lead | Sends the client the first notice: what is known, what is not, next update. |
| T+4 h | Platform | Finds the cause: a deployment change removed the public-access block on one prefix. |
| T+6 h | Platform | Restores the block and adds a regression test that fails if the bucket policy allows public reads. |
| T+24 h | Incident lead | Sends the second update with the cause and the fix. |
| T+48 h | Incident lead | Confirms no other object was public. Closes the containment. |
| T+5 business days | Team | Writes the review and the test that would catch the next one. |

The 72-hour clock is met with 2 hours and 50 minutes to spare. The tabletop also shows the two steps the
scenario depends on: containment before preservation is not allowed, and the client notice does not wait for a
root cause.

## Gaps found

- The bucket policy was not tested by the deployment's acceptance checks. A probe that reads the public-access
  configuration after deploy is needed.
- The first notice took 70 minutes because the analyst had to find the job id. The job id should be on the
  artifact manifest.
