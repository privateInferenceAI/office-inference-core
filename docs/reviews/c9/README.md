# C9 rehearsal, 2026-10-23

**Status:** the eight scenes were run end to end on a clean demo deployment. Every scene produced its
expected output. Two limits are recorded rather than smoothed over: the run used the fallback model
because the account's daily Sonnet allotment was exhausted, and the timing is operation time inside
the script's boxes, not a human narration run.

## Target

| | |
|---|---|
| Account | 878451098165, us-east-1b |
| Instance | `i-0a1a390f790324337`, On-Demand `m7i.4xlarge`, 200 GB gp3, no Elastic IP |
| Public IP | 34.230.74.252 at rehearsal time (ephemeral; a restart changes it) |
| Deployment root | `/opt/oi-core` |
| Deployment id | `oi-core-demo-2026-10-23` |
| Manifest | `manifests/demo-cloud.yaml` |
| Answer profile | `OI_ANSWER_PROFILE=accuracy` |
| Corpus | second corpus, 80 documents, 24,296 chunks |
| Code | office-inference-core commit `115f2ad`; later merges are docs and the contract file |
| App | oic-ui commit `f74adf9` (contract catch-up, panel scroll, disclosure) |

### Archives, sha256

| Archive | sha256 |
|---|---|
| Code `oi-core-demo-115f2ad.tar.gz` | `c901c00b807ae7f7d845a2d25240be0cf3ebd120c3d08187a365b946a63c458c` |
| UI `oic-ui-b3486a8.tar.gz` (installed first) | `47b8afe66326c4d5be1260376e8dc2d8612e14a9de00ed2c1988a5db4fc8202d` |
| UI `oic-ui-f74adf9.tar.gz` (final, app rebuilt) | `9225cabbcb2255ae5cb19721bae12c22da7b13f8880016c4e8da6508499d53ed` |
| Corpus `second-corpus-2026-10-22.tar.gz` | `0b154bcb452af0789cdc34a6453b3806924fb22cdbf15a217eb73175072a88a1` |

## Acceptance, before the rehearsal

- The four probes: interface `8`, activity `8`, access `4` (FORGED skipped with its reason, as on
  the reference install), stream `4`. Transcripts in `acceptance/probe-*.txt`.
- The five-question corpus set: 5 of 5, all categories passed. `acceptance/corpus2-check.txt`.
- One forced degradation: `kind degraded`, reason `unclassified`, 12 passages.
  `acceptance/degraded-answer.json`.
- One smoke answer: `resolved_model` Sonnet 4.6, `fallback_used` false. This is the last call the
  primary answered that day.
- Counts: `80|24296`, zero files at the dead-letter threshold. `acceptance/counts.txt`.
- The app: root `200`, `/api/health` ok, and the contract check against the live API passed with 14
  paths and 25 models. `acceptance/ui-*.txt`.
- The boundary, end to end: the staff question returned `denied: true` with a disclosure and wrote an
  `access`/`denied` row with `reason=boundary`, `restricted_matched=50` and the request id; the
  executive answer contained 385,000. `acceptance/boundary-*.json`, `acceptance/audit-boundary.txt`.

## The model state, which is a limit of this run

At 19:40 UTC the primary model refused every call with:

    ThrottlingException ... Too many tokens per day, please wait before trying again

The manifest's fallbacks did their job and the answers came from Haiku 4.5. The exact response is in
`acceptance/model-state.txt`, and the audit rows carry `fallback_used: true` for every
model-dependent check after that point. The acceptance is therefore partial on the model that runs:
the mechanics and the answers were verified on the fallback, and the primary was confirmed working
earlier the same day. **A timed run on the primary is still owed before a client demo**, and the
pre-demo check in the runbook exists for exactly that.

## The eight scenes

Operation time is what the interface or the terminal took on this run. The script's box for each scene
is the narration budget around it. The two are not the same measurement, and the table says which is
which.

| # | Scene | Script box | Operation time | Result |
|---|---|---|---|---|
| 1 | The deployment | 0:45 | 0.2 s | Eight running services, Postgres and parser healthy; the MCP gateway stopped on purpose |
| 2 | A policy answer | 1:00 | 2.4 s | "The mileage rate for reimbursement is 0.67 USD per mile." cited to the employee handbook, 1.9 s, 1 of 12 documents |
| 3 | The cited page | 1:30 | 3.6 s | GAO answer; the source panel opened the report and scrolled the cited page into view (panel scroll 14,817 px, cited block at 129 px from the top) |
| 4 | Across the boundary | 1:15 | 2.4 s | No 385,000; the disclosure was shown under the answer |
| 5 | The denial in the record | 1:15 | 2.0 s | The Activity row opened: denied, reason boundary, roots company, `restricted_matched` 50, request id; the audit CLI showed the same row |
| 6 | The same question, permitted | 1:15 | 11.4 s | 385,000 with the executive compensation citation, through **Ask without streaming** |
| 7 | Export and hand over | 1:45 | 4.1 s | 306 events exported for the day, chain intact, sha256 in `rehearsal/scene7-sha256.txt` |
| 8 | Tamper and fail | 1:45 | 1.3 s | One row edited; the verifier failed on that row's hash and on the file hash |

All eight fit their boxes with room to spare. The rehearsal total was 27.4 seconds of operation time;
the remaining 10 minutes of a delivery is narration, transitions and waiting on the model, and that
part has not been rehearsed by a human. Say that plainly if the evidence is asked about.

The two browser runs were driven by Playwright against the deployment through the tunnel. Screenshots
of the answer, the source panel, the denial, the record and the executive answer are in
`screenshots/`. `rehearsal/scene-results.json` holds the machine-readable answers and timings.

### The streaming gap that changed scene 6

The Ask screen's streamed path is single-shot. The JSON path (`POST /ask`) runs the tool-assisted
retrieval, which searches rows and exact phrases and can return up to 32 evidence passages. On the
stream the "what is the CEO's base salary" question failed to find the row in three of three trials;
through the JSON button it found it in about ten seconds, twice in two tries and then in the
rehearsal. Scene 6 therefore uses **Ask without streaming**. This is a product gap, not a demo
preference: the accuracy profile's measurements are on the tool-assisted path and the streamed client
does not get it by default. Recorded in the runbook's limits.

## Findings that became changes

1. **A boundary denial is now in the record and on screen.** office-inference-core #322 and oic-ui #2.
   Before that, the refusal lived only in the HTTP response.
2. **A citation marker is no longer read as a figure.** #323. Found on the live box when an answer
   citing `[14]` was refused as stating 14.
3. **The contract copies caught up with the service.** The two artifact paths were missing from both
   repositories; the demo's contract check caught it. Core #326, oic-ui #3 and #4.
4. **The source panel opens at the cited part.** oic-ui #5. Without it a citation on a long document
   opened at page one and the marked passage was screens down.
5. **Two install findings.** Build the api image before the full build (the MCP gateway builds
   `FROM` it), and the MCP gateway is a ninth service that needs a connector token. Core #325.

## Limits

- **The run used the fallback model.** Every model-dependent result here is Haiku 4.5, not the
  manifest's Sonnet 4.6. The primary was throttled for the day.
- **This is operation timing, not a human delivery.** The 27.4 seconds is the interface and the
  terminal. The 10:30 script is narration plus these operations; a person has not run it against a
  clock.
- **The export is one UTC day and includes acceptance and rehearsal rows.** 306 events, because the
  deployment was built, accepted and rehearsed the same day. It is the deployment's own record and
  the note says so. A demo day's export is cleaner if acceptance runs the business day before.
- **No anchor was used.** The verifier says plainly that completeness of the tail cannot be shown
  without one; the hourly anchor and the Object Lock bucket are the production shape, not this box.
- **The demo box is not the product shape.** No alarm publisher, backup timer, SES or SSO, and no MCP
  gateway or agent runtime. The pre-demo table says what is checked where.
- **The four probes do not touch the app.** The UI checks and these screenshots are the app's
  acceptance on this deployment.
- The temporary app relay on `127.0.0.1:8090` is review scaffolding and is removed when the box is
  cleaned up.

## What this proves, and what it does not

It proves a clean deployment can be built from a commit, accepted, and driven through the eight
scenes with every expected output, with the artifacts preserved. It does not prove the primary model
answers as expected on demo day, or that a person can deliver it in twelve minutes. Both are checks
for the next run.

## Changes after this rehearsal, 2026-10-24

This rehearsal is the historical record. A human walkthrough the next day produced a defect list, and three
of the fixes were deployed to the same box. The eight-scene script changed with them, so the next timed run
reads `docs/runbooks/demo.md`, not the table above.

- **The answer profile chooses the retrieval path.** Before, `/ask` always ran the tool-assisted loop and
  `/ask/stream` ran it only when the caller asked, so one deployment answered the same question two ways. Both
  endpoints read `OI_ANSWER_PROFILE` now. Evidence: office-inference-core#329.
- **The Ask screen has one action and shows the loop's progress.** The second button is gone; progress is one
  sentence per step or named tool. Evidence: oic-ui#6.
- **The app's proxy was buffering the whole event stream before forwarding it.** The screen had never actually
  streamed a frame, which is why the progress work needed a proxy fix as well. Event streams are forwarded as
  they arrive now. Evidence: oic-ui#7.
- **The streamed denial writes its own boundary row.** Verified on the deployed build: row 402, 2026-10-24
  14:11 ET, endpoint `/ask/stream`, reason boundary, `restricted_matched` 50.
- **Scene 3 changed to the inspection schedule backflow question.** The tool-assisted path produced a garbled
  answer on the GAO question, losing a phrase mid-sentence. Recorded as a bug.

Deployed builds: code `oi-core-ask-7122c65.tar.gz` (sha256 `cadcc56a...`), UI `oic-ui-ask-f4a9d56.tar.gz`
(`0a8db9e9...`) then `oic-ui-proxy-b04ca0a.tar.gz` (`8cbad082...`). A functional rerun of scenes 2 through 6
passed on these builds: 2.4s, 2.8s, 10.0s and 8.4s for scenes 2, 3, 4 and 6, with row 402 and 385,000 as
expected. The timed human narration is still owed.
