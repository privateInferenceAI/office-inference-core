# C5 deliverable review, round 2: executor

Verdict: **no**

Reviewed, and nothing else: `docs/runbook.md` 6.0 through 6.6, `docs/manual.md` 13j through 13m,
`docs/operator-guide.md` 13m.0 through 13m.6, and the 13i.1 contract checkpoint. Every command below was copied
out of the page, run on the deployment, and its output compared with the block beside it. Where a page puts a
command on the box and the box cannot run it, I say so and then run it where it does run.

**Environment, as I found it.** `ubuntu@52.204.202.66`, `/opt/oi-core` at `a6b0643`, clean, and zero behind
after `git fetch`. `/opt/oic-ui` at `bcffdae`, clean. `bash scripts/check_images_current.sh` exits 0 and every
running container is on its current image, so nothing below was run against a stale image. `/home/anthony/projects/oic-ui`
on this workstation is at the same `bcffdae` commit, and its `docs/openapi.json` (sha256 `73466307…`) and
`app/tests/contract.test.ts` (sha256 `8111d34a…`) are byte-identical to the box's, which is what makes the app
rung results below results about the deployment's commit rather than about my checkout.

**State I added, because the chain is append-only and somebody should be able to account for it.** Nineteen jobs
started over HTTP (`invoice_intake`), one approval, one denial, one change request, two refusals that wrote
nothing (a wrong digest, a denial with no reason). Two `workflow_probe.py` runs, one `job_probe.py`, one
`--kill-child`, and the engine suite against the deployment are the probe's writes, which the pages document.
`oi run --rm audit export --date 2026-10-16` wrote `exports/2026-10-16/` in the box's checkout, which the page
documents. A throwaway `postgres:16-alpine` on 55432 held the chain tests and has been removed. Both checkouts
are still clean. My scratch scripts under `/tmp` are removed; `/tmp/oi-demo` now holds a fresh run of 13l.7's
own setup commands, and the directory that was there before it is no longer present (I moved it aside to
reproduce 13l.7's first-run output and cannot find the copy I meant to keep).

## The one correction round one had no execution behind

**`docs/manual.md` 4462-4484, "Start a job, and watch it stop at the gate".** Run verbatim. Page's block:

```
state  awaiting_approval
job_id 178e4523057540b3bb87d569852c93c9
digest sha256:5b480b869b4bbb02f…
steps  read_message succeeded | extract_fields succeeded | draft_action succeeded | approval running
```

What the command printed:

```
state  awaiting_approval
job_id bd3ca11b500843669bd4f88489287dcd
digest sha256:5b480b869b4bbb02f12ed892bdef87f0e27d89bb1160285b36e26e2c182c6687
steps  read_message succeeded | extract_fields succeeded | draft_action succeeded | approval running
```

`state`, `steps` and `job_id`'s field label are identical character for character, including the double spaces.
The `job_id` value is new because a job id is per-run. **The digest shown is the same digest**, content-addressed
as the brief says: `sha256:5b480b869b4bbb02f…` is the page's prefix of the value the command printed, and the
full value is 64 hex characters where the page prints 17 and an ellipsis. A rerun of the identical input gives
the identical value. This item is cleared by execution, not by the route or the registry.

The command runs, so the rest of this report is about the pages around it.

## Commands that could not run as written

**1. The app rungs are written for a machine with Node on it, and the runbook puts them on the deployment.**

- `docs/runbook.md` 5872-5881 (6.5), 6030-6041 (6.6), `docs/manual.md` 4635-4640 (13k):
  `cd /opt/oic-ui/app`, then `npm run typecheck`, `npm test -- --run`, `npx playwright test e2e/…`.
- Runbook 5883-5885 says it plainly: "**The app is a separate repository and `/opt/oic-ui` is its checkout on
  the deployment** (guide 13i.1); it is not on the core's machine at any other path." Manual 4091 agrees: "A
  path with `<oic-ui>` in it is on the box, not on your workstation."
- I ran all three on the box, in that directory. Every one exits 127:

  ```
  --- npm run typecheck
  exit=127
  --- npm test -- --run
  exit=127
  --- npx playwright test e2e/live.spec.ts
  exit=127
  bash: line 1: npm: command not found
  bash: line 1: npm: command not found
  bash: line 1: npx: command not found
  ```

  There is no `node`, `npm` or `npx` on the box, no `/opt/oic-ui/app/node_modules`, and no
  `~/.cache/ms-playwright`.
- **The limit in the note.** I did not stop there: the same rungs run on a workstation checkout at the same
  commit, and I ran them there. `npm run typecheck` exits 0. The unit suite fails (finding 2). Both browser
  specs pass, `live.spec.ts` 4 passed in 50.6s and `review.spec.ts` 9 passed in 6.9s. So the rungs are
  executable somewhere; they are not executable where the page says they are, and a reader who follows the page
  literally gets `command not found` six times and no way to tell whether that is a broken box or a broken page.

## Pasted output that no longer matches

**2. `docs/runbook.md` 5901-5916 pastes a green unit suite. The suite is red at the commit the page names.**

- The page, under `$ npm test -- --run`:

  ```
   Test Files  7 passed | 1 skipped (8)
        Tests  160 passed | 12 skipped (172)
  ```

  and at 5887: "Expected: the unit suite passes with the live-view tests named."
- What I ran, at `/home/anthony/projects/oic-ui/app` on `bcffdae` (the commit 5885 tells the reader to check):

  ```
   Test Files  1 failed | 6 passed | 1 skipped (8)
        Tests  2 failed | 158 passed | 12 skipped (172)
  ```

  Both failures are in `tests/contract.test.ts`, and both are the app's contract copy having caught up with the
  service while the test's own expectations stayed behind:

  - line 57, "has no path the app does not know about": expected the 11 hard-coded paths, received 12, the
    extra one being `/approvals`.
  - line 167, "reads no field of a job that the contract does not declare": the assertion is
    `expect(undeclared).toEqual(["events_through","pending_action","pending_action_digest"])` and `undeclared`
    is now `[]`, because the copy declares all three.
- `bcffdae` is "The app's contract copy catches up with the service". The commit that fixed the copy is the
  commit that broke these two assertions, and 6.6's `check-contract.sh` rung passes on the same tree (finding
  in "What I ran and passed"). The page's own account at 4651-4657 says the app regenerated its copy at
  `bcffdae`; the unit-suite paste was not re-run after that.
- **The limit in the note.** I could not run this on the box at all (finding 1). The failure is a property of
  the commit and not of my checkout: the test file and the contract copy it reads are byte-identical on both
  machines.

**3. `docs/runbook.md` 4570-4583 quotes a record field the system does not write.**

- The page, as "the record carries what the system does not know":

  ```
  "part11_11_50_manifestation": {
    "printed_name": null,
    "name_absent_reason": "sign-in is not enabled on this deployment, so the system cannot verify who acted",
    "signed_at": "2026-10-16T16:20:00Z",
    "meaning": "approved the action described by action_digest"
  }
  ```
- What I ran: the approval in 6.2, then `jsonb_pretty(payload)` on the approval row. The record holds
  `"meaning": "approve the action described by action_digest"`. `services/api/approvals.py:113` builds it as
  `f"{decision} the action described by action_digest"`, so it is the decision word and not a past tense.
- The same block's `"claimed_role": "staff"` is a role this deployment cannot mint: `rendered/access.json`
  declares `"roles": {"viewer": []}`, and `oi run --rm session mint --dev --role staff` answers
  `error: unknown role 'staff'. Known roles: ['viewer']` and exits 1. The subject `"dana"` is plainly an
  example, but the role is a field value a reader may copy.

**4. Two pastes are abridged where the command prints more, and the reader is not told.**

- `docs/runbook.md` 5921-5930 and 6051-6064 show Playwright lines as `✓  1 › a refresh mid-run comes back to
  the same job with the recorded steps (983ms)`. The command prints
  `✓  1 [chromium] › e2e/live.spec.ts:83:3 › the live view › a refresh mid-run comes back to the same job with
  the recorded steps (848ms)`. The project, file, line and describe block are dropped, and the timings differ.
  The pass counts and the durations' order of magnitude agree.
- `docs/runbook.md` 4967 shows `awaiting_approval | digest=sha256:5b480b869b4bbb0`. The command is
  `psql -tAc`, which prints `awaiting_approval|sha256:5b480b869b4bbb02f12ed…` with no spaces and no `digest=`
  label. The block is a labelled transcript of several commands rather than one paste, so this is presentation
  rather than a stale value, and the digest is a correct prefix of the live one.
- Reported because the round's rule is that a paste a reader cannot tell from a current one is a finding. Both
  of these are cosmetic: neither hides a failure and neither misstates a value.

## Claims that did not survive running

**5. "An empty `reference`" is not what the record holds. Two pages use it to reassure the reader.**

- `docs/manual.md` 4444-4446: "A reader who sends `total 4210.00 due 2026-11-15 from Ferguson` matches all
  four; a reader who sends `inv-2026-0417` matches three and gets an empty `reference`, which is not an error
  because that field is not required."
- `docs/operator-guide.md` 2728-2729: "A reader who sends lowercase text and sees `reference` come back empty
  has matched the table and not found a bug."
- What I ran: `POST /jobs` with `inv-2026-0417 from accounts@ferguson.example total 4210.00 due 2026-11-15`,
  and then `POST /jobs` with `total 4210.00 due 2026-11-15 from Ferguson`. Both reach the gate, and both jobs
  hold a **non-empty** reference, a 16-hex value:

  ```
  inv-2026-0417 from …    -> "reference": "e1c0945d2db81da0"
  total 4210.00 due …     -> "reference": "bc90a0ae3569796e"
  ```

  The lowercase reference does not match the pattern, which is the claim that survives. The consequence does
  not: `extract_fields` leaves the field empty (`services/workflow/plans.py:173`) and `draft_action` then
  substitutes the message id (`plans.py:204`, `fields.get("reference") or …get("message_id")`). So the value is
  not empty, it is the message's own id, and it is stable: the same text gave `e1c0945d2db81da0` on two separate
  jobs, so a reader cannot tell a fallback from a reference by looking at it. The operator guide's sentence is
  the worse of the two, because it tells the reader that the empty value is the signal that nothing is wrong;
  the signal they will actually see is a hex string that looks like a real invoice number.
- The "matches all four" half is false on the same observation: nothing in `total 4210.00 due 2026-11-15 from
  Ferguson` matches the reference pattern, so the reference that comes back was supplied, not matched. The
  sentence's own next clause contradicts it.

**6. `docs/manual.md` 4469-4470: "The input is the message above, flattened onto one line." It is not, and the
digest says so.**

- The message above is the block at 4427-4432 (`From: accounts@ferguson.example` / `Subject: Invoice
  INV-2026-0417` / `Total 4210.00 due 2026-11-15 from Ferguson Mechanical Ltd.`). The command at 4471-4474
  sends `INV-2026-0417 from accounts@ferguson.example Total 4210.00 due 2026-11-15`, which is neither that
  message nor a flattening of it.
- What I ran: both forms, and then read the rows.

  ```
  INV-2026-0417 from accounts@ferguson.example Total 4210.00 due 2026-11-15
      -> vendor "accounts@ferguson.example"  digest sha256:5b480b869b4bbb02f…
  From: … Subject: … Total 4210.00 due 2026-11-15 from Ferguson Mechanical Ltd.
      -> vendor "Ferguson"                   digest sha256:1eb963c0a6afc493e…
  ```

  Flattened, with newlines, either way: the message's own name gives `Ferguson`, because the header's `From:`
  does not match `\bfrom\s+` (the colon stops it) and the body's `from Ferguson` does. The page's input gives
  the sender's address instead. So a reader who sends the message the page prints gets a different pending
  action and a different digest from the one the page prints beside the command, and the digest is the value
  the approval has to quote.
- **The limit in the note.** Both forms reach the gate and neither is refused, so this is not a broken example;
  it is two different examples presented as one.

**7. `docs/manual.md` 4599-4601 quotes the sentence a reader gets, in a form no renderer produces.**

- The page: "And the sentence a reader gets afterwards never names anybody while the basis is a session token:
  > Approved at 2:14pm under the executive role. The person who decided was not identified: sign-in is not
  > enabled on this deployment, so the system cannot verify who acted."
- What I ran: all three decisions against the deployment, then the feed. The system printed:

  ```
  Approved at 2026-10-16T23:47:17+00:00 under the viewer role. The person who decided was not identified: sign-in is not enabled on this deployment, so the system cannot verify who acted.
  Denied at 2026-10-16T23:48:19+00:00 under the viewer role. The person who decided was not identified: sign-in is not enabled on this deployment, so the system cannot verify who acted.
  Changes requested at 2026-10-16T23:48:19+00:00 under the viewer role. The person who decided was not identified: sign-in is not enabled on this deployment, so the system cannot verify who acted.
  ```
- The wording after the timestamp is exact in all four places it appears: `runbook.md` 4604-4605,
  `manual.md` 4379-4380, `operator-guide.md` 2448-2449, and the test-database paste at `runbook.md` 4922-4924,
  which I reproduced character for character. Round one's correction landed. The one exception is this
  example: `2:14pm` is not a string either renderer produces. The service writes `signed_at` verbatim into the
  sentence (`approvals.py:187`), and the value recorded is the ISO timestamp (my runs: `2026-10-16T23:47:17+00:00`,
  `2026-10-16T16:20:00+00:00` in the tests). The app renders the same field with `toLocaleString()`
  (`app/src/approvalSentences.ts`, `formatWhen`), which is a locale string and not a 12-hour clock. `executive`
  is also not a role this deployment declares — only `viewer` is, as above — so the
  example's role cannot appear on this box at all. Manual 4377 licenses a different timestamp and a different
  role in its copy of the sentence ("verbatim apart from the timestamp and the role"); it does not license a
  timestamp format the system cannot emit.

**8. `docs/runbook.md` 4916-4917: "the deployment has no approval row yet". It has eleven.**

- The sentence explains why the block at 4920-4928 comes from `OI_TEST_DATABASE_URL` rather than the
  deployment. I ran it both ways. Against a throwaway `postgres:16-alpine` the block matches character for
  character, including `Approved … by dana (staff)` and the 14 passed. Against the deployment:

  ```
  select count(*) from audit_events where event_type='approval'  ->  11
  ```
- Eleven is the count at that point in my run: round one's fixer had already written the first ones, and my
  three decisions added to them. The block is honestly labelled as test-database output, so a reader is not
  misled about where it came from. What is stale is the reason: the deployment has approval rows, and the
  documented command in 6.2 is what produces them.

**9. `docs/manual.md` 4615-4623: the queue's scope is stated as the access roots, and `every` is false.**

- The page: "A job appears if the roots it carries are a subset of the roots the session holds… **The route
  scopes by access roots, not by role and not by subject**", and at 4621-4623, "Every session holding the roots
  sees every gate in them, so the person who drafted a job may approve it."
- What I ran: a viewer session for `probe-staff@officeinference.com` (roots `["company"]`), then
  `GET /approvals?session=…`, then the same rows read out of `jobs`.

  ```
  rows in awaiting_approval              19   all with access_roots ["company"]
  returned to a session holding ["company"]   13
  ```
- The six the route does not return are exactly the rows the workflow probe wrote, and they carry
  `deployment_id = 'workflow-probe'` where every returned row carries `oi-core-dev-cloud`. The route is scoped
  to the deployment as well as to the roots, and the paragraph does not say so. `data_class` is not the reason:
  a job started by the same session with `"data_class": "public"` comes back. `session_id` is null on both
  sets, so it is not a session join either.
- **The limit in the note.** Subject (`probe`) and actor role (`staff`) differ on those same six rows, and I
  could not separate them from the deployment id, because the API refuses to start a job for an undeclared
  subject or role, which is the right refusal. So what is established is that the roots rule as written is not
  sufficient and that "every gate in them" is false on this box; which extra field does the excluding is not
  established.

**10. `docs/manual.md` 4576-4581: "Four rules follow, and each is a test." Three of the four are.**

- Rules 1, 3 and 4 have named tests: `tests/review.test.tsx` 132 and 152 (content before control), 235, 253
  and 265 (the bulk rule and its two absences), 218 (the drift refusal), and `e2e/review.spec.ts` 133, 156,
  172, 186.
- Rule 2, "**Approve is never the default.** No autofocus, no key binding, no Enter-to-confirm", has none. No
  test in `tests/` or `e2e/` mentions `activeElement`, `toBeFocused` or `hasFocus` on the Review screen, and no
  Review test presses a key. The rule is implemented, with the comment at `src/screens/Review.tsx:365`, and
  nothing fails if it stops being true.
- **The limit in the note.** This is a check of the suite's names and assertions, not a mutation: I did not
  add an autofocus to the screen to watch a test fail, because that is an edit to a repository I may not touch.

## What I ran and passed

Everything the pages document, in the order the pages document it, except what is listed in "What I could not
test". The pass list is long because the round's rule is to test from the manual, and most of the manual holds.

**Runbook 6.0, the contract step.** `bash scripts/probe.sh job_probe.py` held 7 rights and skipped `BOUNDARY`
with its reason, exit 0. The contract check on `ai-net` printed `OK /w/docs/openapi.json matches
http://api:8088 (12 path(s): … /jobs/{job_id}/approvals …)` and exited 0. `~/venv/bin/python -m pytest tests/ -q`
printed **884 passed, 23 skipped**. Both commands the page says were wrong when first written are right now,
and `~/venv/bin/python` is the form that works (the bare `python3` has no pytest on the box, as the page says).
The route-order paragraph at 4614-4619 is in the right step: its subject is what a green contract check cannot
see, it is the last prose before the Verify block, and it points at 6.2 for the request that tells the two
matches apart. 6.2 is where that request lives, and I ran it. The paragraph's historical claim is consistent
with the present: `/jobs/{job_id}/approvals` answered 200 for all three decisions, so the wildcard no longer
shadows it.

**The count lines and the skips.** The deployment's 23 skips are exactly as 5337-5343 describes them: 14 in
`tests/test_workflow_chain.py`, 5 in `tests/test_store_parity.py`, 2 in `tests/test_audit_statement.py`, 1 in
`tests/test_format_coverage.py`, 1 in `tests/test_manifest_schema.py`. Every one is named, and each carries a
reason string that includes the command that would make it run. The workstation/box difference reconciles
exactly: this workstation prints **896 passed, 22 skipped**, and the difference is entirely
`tests/test_manifest_schema.py`, whose 12 tests are collected here and collapsed into one module-level skip
there because the deployment's venv has no `jsonschema` (`ModuleNotFoundError` confirmed on the box). 884 + 12
= 896; 23 - 1 = 22. The page states the deployment run and does not claim the number is universal.

**Runbook 6.1 and 6.3, the engine.** `workflow_probe.py` printed all thirteen rights and
`PASSED: 13 right(s) held against the documented workflow engine`, exit 0. `ATOMIC` is `job row='queued', 0
step row(s), 0 chain row(s)` after `exit=-9`, which is the defect stated in one line.
`tests/test_workflow_engine.py -q` printed `23 passed in 0.65s`, the same count and the same duration as the
paste. `BOUNDED` printed `0.53s (deadline 0.5s)` against the page's `0.52s`, which is a wall-clock measurement
and not a stale value. `CHAIN` printed 484 events against the page's 108, and the page explains that number at
4760-4763 ("the number to read is `chain intact`, not the count"), so it is caveated where it appears.

**Runbook 6.2, the restart and the approval over HTTP.** The whole sequence, run as written:

```
POST /jobs      -> 200, awaiting_approval, 4 steps, digest sha256:5b480b869b4bbb02f12ed892bdef87f0e27d89bb1160285b36e26e2c182c6687
docker restart api
jobs row        -> awaiting_approval|sha256:5b480b869b4bbb02f12ed892bdef87f0e27d89bb1160285b36e26e2c182c6687   (digest intact)
POST /jobs/{id}/approvals -> HTTP 200, state=succeeded, attempts=6
```

and the record it left: the job row `succeeded | (none) | (cleared)`; six step rows, one per name, in attempt
order, all `succeeded`; **14 chain rows naming the job and 7 distinct steps**, exactly the page's numbers; the
approval row `event_type=approval | outcome=ok | decision=approve | assurance=unverified |
claimed_subject=probe-staff@officeinference.com`, which is the page's line read from
`payload->approval->identity`; and the feed sentence in the honest form. The chain tests against a throwaway
database printed `14 passed`, and the feed out of that database reproduced 4922-4924 character for character,
including the verified-branch line `Approved at 2026-10-16T16:20:00+00:00 by dana (staff)`. The route's
refusals also hold: a wrong digest answered `HTTP 409` with `kind=approval_drift` and both digests in the body
(`waiting_on` and `quoted`), the job stayed at the gate, and a denial with no reason answered `HTTP 400` with
`kind=bad_decision`. The page's record-shape note about `outcome` is right: the decision lives in
`payload -> approval -> decision`, and `outcome` is `ok`.
`oi run --rm audit export --date …` then `oi run --rm audit verify …` printed
`ok /app/exports/2026-10-16/audit-events.jsonl: 499 event(s), chain intact`, with the page's own caveat about
the count printed beside it. The 176 in 6.2's `verify` line is the same whole-chain count at an earlier moment.

**Runbook 6.4, intake.** `tests/fixtures/intake_acceptance.py` printed `10 PASS, 0 FAIL` and every row matches
the paste, including the sha256 in `CON-DUPE-001` and the enumeration in `SEC-INTAKE-002`. The
`mail failure: …` line the paste does not show is on stderr, so the stdout block is faithful. The intake suite
printed `90 passed`. `watch-folder --iterations 3 --poll-seconds 1` printed the page's three lines **exactly**,
same sha256, same deterministic `intake_id`, with the settle check visible as the page reads it.
`cord-check` printed the page's block exactly and `no cord`, exit 0. The alert-block and least-privilege lines
at 5524-5529 match the acceptance output. `check-config`, `status` and `monitor` behave as 13l.5 describes:
`no alert` on a healthy scratch deployment.

**Manual 13l.7's five commands against the documented scratch directory** all ran as written, on
`~/venv/bin/python`, against `/tmp/oi-demo`. Every one exited 0.

**Manual 13m and the operator guide 13m.x.** `13m.0`'s two commands are the ones cleared above. `13m.1` is the
probe, run twice. `13m.2`'s `--kill-child` on a job the probe left behind printed `exit code 137`, and the
database reads then returned `queued`, `0`, `0`, which is the page's expected line exactly. `13m.3`'s query on
a job at the gate returned `read_message/extract_fields/draft_action succeeded`, `approval running`, and no
rows for `post_entry` or `close_out`. `13m.4`'s route approves and the post-gate steps have one row each.
`13m.5`'s chain read shows every step's rows naming the job, and the audit export/verify pair works as the page
describes, including the warning that without an anchor completeness cannot be shown. `13m.6`'s limits are
still limits: `GET /jobs/{id}` for the resumed job answers with **no `result` key at all**, and the answer is in
the `done` event, which carries the full `result` and is readable — so the documented workaround is real.

**The 13i.1 checkpoint.** `docker run … oi-core/session:local /w/scripts/check-contract.sh --live
http://api:8088` printed `OK the app's contract matches http://api:8088` with the 12 paths and 25 models and
exited 0, against a read-only mount of the app's checkout.

**Runbook 6.6 and manual 13k's contract rung.**
`bash /opt/oic-ui/scripts/check-contract.sh /opt/oi-core/docs/openapi.json` printed the page's block exactly:
12 paths with `/approvals` among them, 25 models, exit 0. So the two repositories' contracts agree, and the app
suite's two failures (finding 2) are the app's own stale expectations rather than a contract mismatch.

**`GET /approvals` answers, and it is newest first.** Thirteen gates came back to the session when I measured
it (I added two more afterwards), ordered exactly `created_at desc` for the rows returned; the heading's claims
about the route existing and answering hold. The scope claim is finding 9.

**The app rungs, run where they run.** `npm run typecheck` exits 0. `npx playwright test e2e/live.spec.ts` is
4 passed and `e2e/review.spec.ts` is 9 passed, with the stall test taking 47.4s inside the shipped 45-second
window, which is the claim the page makes for it. The unit suite is finding 2.

## What I could not test

- **No Microsoft tenant and no SMB or NFS share, as the pages themselves say.** The intake pages' own
  acceptance run is against `tests/fixtures/intake_env.py`, and I ran it. `13l.2`'s install sequence into
  `/etc/oi-intake` I did not run, because it writes configuration and my brief does not allow that; the same
  applies to `migrate` and `verify-manifest`, which also need the client's database. `fetch`, `watch` and
  `enumerate-reach` need the client's mailbox and were not run. The five commands that need neither a tenant
  nor a database are the ones I ran above, and they all pass. Nothing here is evidence about a real tenant,
  and the pages do not claim otherwise.
- **Runbook 6.2 step 1, `cd /opt/oi-core && oi build api && oi up -d api`.** Not run: rebuilding images is
  outside my authority. The page's reason for it is sound and its consequence is real (the API mounts none of
  its own source, so a restart alone reloads the old image). I ran steps 2 through 5 against the image that is
  running, and `check_images_current.sh` exits 0 with every container on its current image, so the sequence
  I ran is the sequence the page describes, minus the rebuild.
- **Runbook 6.1's `AS BUILT` / `MUTATED` pair at 4775-4778.** Reproducing it needs a code mutation
  (`PostgresUnit.__enter__` skipping its transaction). Not run, by a reviewer that may not edit the
  repositories. The `AS BUILT` half is the run I did run.
- **The screens against a live deployment, and the box's browser rung.** No browser on the box, and no client
  deployment other than this one. The browser rungs were run on a workstation checkout of the same commit.
- **The verification-by-mutation claims generally** (6.4's "ten of the eleven mutations were caught"), for the
  same reason: they are statements about work somebody did, not commands a reviewer can follow.
