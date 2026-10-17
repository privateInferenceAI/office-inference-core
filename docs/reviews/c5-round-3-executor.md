# C5 deliverable review, round 3: executor

Verdict: yes

Round three tested the C5 pages from the pages. Every command in runbook 6.0 through 6.6 and manual 13j
through 13m was run as written on the deployment at `0d0dfa2` (box `/opt/oi-core`), plus the app rungs on a
workstation clone at `6d28c3c`. The corrections hold: the probes report the rights the pages paste, the
approval survives a service restart and finishes over HTTP, the feed renders the honest sentence, the
extraction contract is what 13j's table says, and the app's counts are the counts the pages print. Two page
problems were found, one of which can stop a reader on the first command of a section. Neither is in the
engine, the gate, the record, the connector or the screens.

## Commands that could not run as written

**1. `docs/operator-guide.md` 13i.5 (line 2176), `bash tests/fixtures/gate_check_run.sh` — exits 1 on this
deployment, and the reason is not one the page names for it.**

The page's suite list says every one passes. What it prints here:

```
$ cd /opt/oi-core && bash tests/fixtures/gate_check_run.sh
forbidden copy written: data_class=regulated, not in ['public', 'internal']
FAILED: open: http://gate-check-tmp-api:8088/ask answered HTTP 200 for class 'regulated'. The service is not
enforcing the class it was given, so the probe that follows would be testing nothing.
Body: {"answer":"No document you may read answers this question.", ..., "kind":"no_source", ...}
EXIT=1
```

`--jobs` fails the same way. This is the known box state (zero points in `documents`, empty
`/opt/oi-core/documents/`): the request never reaches a model, so the boundary runner cannot observe an
enforcement. The page is right that a `no_source` body produces this sentence, and I am reporting it as
blocked by box state rather than as a page defect. It is listed here so the reader knows the run was made
and what it produced.

**2. `docs/operator-guide.md` 13i.4 (line 2118), the request-id check returns zero rows — and so does the
same right in `activity_probe.py`, which the same page lists.**

The page's checkpoint is that a question leaves a row findable by the request id the answer carried. On this
deployment it does not:

```
$ docker run --rm --network ai-net curlimages/curl -sS -X POST http://api:8088/ask \
    -H 'Content-Type: application/json' \
    -d '{"session":"<viewer token>","question":"What is the mileage rate for reimbursement?"}'
{"answer":"No document you may read answers this question.","citations":[],"kind":"no_source",
 "role":"viewer","roots":["company"],"request_id":"3b970465ef434e48befdcb555016df07"}

$ curl -sS "http://api:8088/activity?request=3b970465ef434e48befdcb555016df07" -H "X-OI-Session: <token>"
0 row(s) for that request
```

The answer carries a request id and no chain row carries it. The cause is in the answer path, not in the
corpus: `services/api/answer.py` line 203 audits the empty-retrieval case as `audit("access", subject, role,
"denied", {"stage": "retrieval", "reason": ...})` with no `request_id`, while every other audited path on
that route passes one (`app.py` lines 378 and 404). So a `no_source` answer is the one answer whose id
cannot find its own row. The consequence for the pages:

```
$ cd /opt/oi-core && bash scripts/probe.sh activity_probe.py
ok   READ       10 row(s), ids [13753]..[13744], newest first=True, every row carries [...]
ok   SCOPED     a ['company'] session asking for probe-exec@officeinference.com -> 200, 0 row(s)
ok   ACTOR      actor=nobody@officeinference.com -> 200, 0 row(s)
ok   FILTERED   type=model_call -> 0 row(s), all model_calls=True; type=not_a_type -> 400 bad_request
FAIL BYREQUEST  request_id e43947b1… -> 0 row(s)
           what the manual promised: a row is findable by the request id its answer carried
ok   BYJOB      job 584d060e… -> 3 row(s) naming it
ok   CAPPED     limit=500 -> 200, limit=5000 -> 400 limit is capped at 500. Page with `since` instead
ok   RECORDED    7 activity read(s) recorded in the chain

FAILED with 1 problem(s): the manual and the service disagree
EXIT=1
```

A reader who checks the collection's point count and its directory (both empty, as the page instructs for the
*other* runner) still cannot explain this one, because the page connects an empty collection only to
`gate_check_run.sh`. The honest fix is one sentence, not a code change: with an empty corpus, `/ask` answers
`no_source`, that answer carries a request id, and nothing writes a row with it, so `BYREQUEST` is
unreachable here. On a populated corpus the right is testable — I did not have one, and I am not claiming the
right holds.

**3. `docs/runbook.md` 6.5 (lines 5902-5909), the command block names the deployment's path for commands
that cannot run on the deployment.**

The block is `cd /opt/oic-ui/app; npm run typecheck; npm test -- --run; npx playwright test e2e/live.spec.ts`,
and the prose above it says the box has no Node and to run them "in a clone of the app on a workstation". A
reader who pastes the block onto the box gets `cd: /opt/oic-ui/app: No such file or directory` or, on the
deployment where the checkout exists, `npm: command not found`. `manual.md` 13k's Run it block (line 4653)
does this correctly with `cd <oic-ui>/app`, so the two pages disagree with each other. The page was not made
worse this round and the behaviour is correct on both sides; the path is the problem.

## Pasted output that does not match

**4. `docs/runbook.md` 6.6 (lines 6046-6048) says the app prints the ISO sentence. It prints a locale one.**

The page's text: "The sentence a reader gets afterwards is the one `approvals.describe()` prints, in the
app's own words in `approvalSentences.ts`: *'Approved at 2026-10-16T18:18:25+00:00 under the viewer
role. …'*"

The app's sentence does not carry that timestamp. I rendered the module in the app's own checkout with the
record the page quotes:

```
$ cd /home/anthony/projects/oic-ui/app && npx vite-node /tmp/checksentence.mjs
"Approved at 10/16/2026, 2:18:25 PM under the viewer role. The person who decided was not identified:
 sign-in is not enabled on this deployment, so the system cannot verify who acted."
```

`approvalSentences.ts` line 114 builds it, and `formatWhen()` (line 96) renders `signed_at` with
`toLocaleString()`. `screens/Activity.tsx` line 210 is where it reaches a reader. The service's half is
exactly as pasted — I read it out of the view on the deployment and out of the test database, and both
matched to the character.

`manual.md` 13k gets this right (line 4612: "The service writes `signed_at` into the sentence verbatim, and
the app renders the same field with `toLocaleString()`"), so the runbook's version is the one left behind:
round three's edit fixed the manual sentence and not this one. Ranked above the path problem because it is a
claim about a page's own product that a reader only has to run the app once to disbelieve.

## Claims that did not survive running

None. Every executable claim on the pages under review held, and the two that did not survive were about box
state and app wording, not behaviour. For the record, the claims I specifically tried to break and could not
are the load-bearing ones:

- **"Kill the process between the two writes and neither is present."** Run as 13m.2 documents it, on a job
  the probe had left behind: `exit code 137`, state `queued`, `0` step rows, `0` chain rows. The full probe
  reports `ATOMIC child exit=-9 (killed=True), job row='queued', 0 step row(s), 0 chain row(s)` with
  `BOTHPRESENT` as its control.
- **"A job survives `docker restart api` with its digest intact, and the approval route finishes it."** Done
  end to end: minted a session with the documented command, `POST /jobs` -> `awaiting_approval` with four
  steps and digest `sha256:5b480b869b4bbb02f12ed892bdef87f0e27d89bb1160285b36e26e2c182c6687`,
  `docker restart api`, read the row back `awaiting_approval|sha256:5b480b869b4bbb02f…`,
  `POST /jobs/{id}/approvals` -> `{"attempts": 6, "state": "succeeded", "terminal": true}` with
  `post_entry` and `close_out` each exactly one row and the job row `succeeded|(none)|` with the digest
  cleared.
- **"UI-FEED-001 is fixed and rendering live."** The deployment's own feed returned
  `Approved at 2026-10-16T23:47:17+00:00 under the viewer role. The person who decided was not identified:
  sign-in is not enabled on this deployment, so the system cannot verify who acted.` for the job I approved.
  The test database's three-decision block reproduces the runbook's paste exactly, including the verified
  branch.
- **"`RATIO`-style counts and the app's suite."** `npm test -- --run` printed `161 passed | 12 skipped (173)`
  and `npm run typecheck` exited 0, which is what 6.5 pastes; `npx playwright test e2e/live.spec.ts` printed
  the four test names with the 47.4s stall line; `e2e/review.spec.ts` printed nine; the full run collected 23
  with 13 passed and 10 skipped, which is guide 13i.5's list.
- **"The contract check passes at twelve paths and twenty-five schemas."** `fetch_openapi.py --check` on
  `ai-net` printed `OK … (12 path(s): … /jobs/{job_id}/approvals …)` and exited 0; the workstation check
  printed the same twelve plus `25 model(s)`; guide 13i.1's `--live` form passed against `http://api:8088`.

## What I ran and passed

| Command, as the page writes it | Where | Result |
|---|---|---|
| `bash scripts/probe.sh job_probe.py` (6.0, manual 13j) | box | `PASSED: 7 right(s) held …, 1 skipped` |
| `docker run … fetch_openapi.py --check` (6.0, manual 13j) | box | `OK … 12 path(s)`, exit 0 |
| `~/venv/bin/python -m pytest tests/ -q` (6.0, 6.4) | box | `884 passed, 23 skipped in 33.54s` |
| `bash scripts/probe.sh workflow_probe.py` (6.1, 6.2, 6.3, guide 13m.1) | box | `PASSED: 13 right(s) held`, incl. `ATOMIC`, `TYPED`, `BOUNDED`, `IDEMPOTENT` |
| `~/venv/bin/python -m pytest tests/test_workflow_engine.py -q` (6.1, 6.3) | box | `23 passed in 0.65s` |
| `OI_TEST_DATABASE_URL=… pytest tests/test_workflow_chain.py -q` (6.2) | box | `14 passed in 0.67s` (throwaway `postgres:16-alpine` on `ai-net`, as the test's own skip message documents; removed after) |
| the 6.2 five-command restart-and-approve sequence | box | HTTP 200, `state=succeeded`, six steps one row each |
| `select summary from audit_feed … limit 3` (6.2, guide 13j.6) | box + test db | honest sentence for all three decisions |
| `docker exec postgres psql … job_steps` (guide 13m.3) | box | 3 succeeded, `approval running`, no `post_entry`/`close_out` rows |
| `workflow_probe.py --kill-child <job_id>` then the three reads (guide 13m.2) | box | exit 137, `queued`, `0`, `0` |
| `oi run --rm audit export` + `verify` (guide 13m.5) | box | `ok …: 280 event(s), chain intact` |
| `~/venv/bin/python tests/fixtures/intake_acceptance.py` (6.4, manual 13l.7) | box | `10 PASS, 0 FAIL`, exit 0, with the documented stderr line about the pulled licence |
| `~/venv/bin/python -m pytest tests/test_intake_*.py -q` (6.4) | box | `90 passed in 19.85s` |
| manual 13l.7's five commands against `/tmp/oi-demo` | workstation | `check-config` and `cord-check` print the pasted blocks; `watch-folder` prints the three-line settle check with the same hash and `in-86fdb4a2dc4bdb9f2daf`; `status` counts 1 record; `monitor` prints `no alert` |
| `npm run typecheck`, `npm test -- --run` (6.5, 6.6, manual 13k) | workstation | exit 0; `161 passed | 12 skipped (173)`, the same eight files and counts the page pastes |
| `npx playwright test e2e/live.spec.ts` (6.5) | workstation | `4 passed`, stall line at 47.4s |
| `npx playwright test e2e/review.spec.ts` (6.6) | workstation | `9 passed`, names as pasted |
| `npx playwright test` (guide 13i.5) | workstation | `13 passed`, `10 skipped`, 23 collected |
| `bash /opt/oic-ui/scripts/check-contract.sh /opt/oi-core/docs/openapi.json` (6.6, manual 13k) | core checkout | `OK … 12 path(s) … 25 model(s)`, exit 0 |
| `docker run … check-contract.sh --live http://api:8088` (guide 13i.1) | box | `OK … 12 path(s) … 25 model(s)`, exit 0 |
| guide 13i.2's `docker inspect app … \| grep -vE '^(PATH\|…\|OI_API_URL)='` | box | one empty line, which is the "expect no output" the page asks for |

I also ran `npx vitest run tests/integration.test.ts` with `OI_API_URL=http://127.0.0.1:18080` through the
guide's own SSM port-forward (guide 13i.3's relay, `app-relay` still up on the box) and a freshly minted
`viewer` token. Eight of the twelve pass. The four that fail are all the answer path
(`answers a question with the shape the app renders`, `never lets a reserved identifier through`,
`streams an answer, with the citations arriving before the words`, `finds the row for a request id the answer
carried`), and all four fail for the same reason as finding 1 and finding 2: no corpus, so `/ask` answers
`no_source` and there is nothing for those assertions to read. The other eight — deployment identity,
self-supplied authority, invalid session, task list, undeclared task, refusal on the streamed path, chain
scoping, and the read cap — pass live. This is a state gap in guide 13i.5's list, not a claim the page makes.

## What I could not test

- **No Microsoft tenant and no SMB or NFS share.** So `check-config`, `cord-check`, `migrate`, `fetch`,
  `watch`, `enumerate-reach`, `verify-manifest` and the `/etc/oi-intake` install path were not run as a
  client would run them; `/etc/oi-intake/` does not exist on this box. The ten intake register rows are
  proved against the simulated tenant the pages themselves name, and I ran that. `enumerate-reach`'s
  six-probe table is from the acceptance run, not from a mailbox. The pages say all of this, and finding 3
  of round two's disposition already records it.
- **No browser and no sign-in on the box, and no client deployment.** The two screens were exercised through
  the app's own browser rung against a stubbed deployment, exactly as 6.5 and 6.6 say they are. I did not
  see a live gate in a real browser, and I did not see the `409 approval_drift` path driven by a real draft
  rewritten mid-read.
- **The four integration assertions that need a model** and `activity_probe.py`'s `BYREQUEST`, for the reason
  in findings 1 and 2. I am reporting them as blocked, not as held.
- **`gate_check_run.sh`'s boundary** (`--jobs` included), for the same reason.
- **Anything about the intake connector's real credential lifetime:** token rotation past ninety days,
  Conditional Access, and the app registration's removal are scenarios the acceptance probe simulates.
