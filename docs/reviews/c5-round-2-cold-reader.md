# C5 deliverable review, round 2: cold reader

Verdict: yes

I read only the pages the brief allows: runbook 6.0 through 6.6, manual 13j through 13m, guide 13m.0 through
13m.6, plus the cross-references those pages make (manual 22, 23 and 13l's install section, guide 13i.1 to
13i.5, runbook 0.8 for `oi`). I read no source, no git history and no other project document. I ran the pages'
own commands on the deployment at 52.204.202.66 and got the C5 core working: a job reached
`awaiting_approval`, survived `docker restart api`, and finished after an approval over HTTP with one step row
each. On that evidence a stranger does end up with a working C5.

One step stops a stranger on the machine the pages name: the two screens are verified with `npm` and the
deployment box has no Node. That is the only blocking finding. Four smaller ones follow it.

## Where I could not proceed

**1. Runbook 6.5 lines 5870-5885 and 6.6 lines 6028-6041, manual 13k lines 4631-4640: the app rung is run with
`npm` on a box that has no Node. Blocking.**

The block a newcomer is handed (6.5, lines 5872-5881):

```
cd /opt/oic-ui/app
npm run typecheck
npm test -- --run

# The browser rung...
npx playwright test e2e/live.spec.ts
```

Eleven lines below it, 6.5 says where that directory is (lines 5883-5885): "**The app is a separate repository
and `/opt/oic-ui` is its checkout on the deployment** (guide 13i.1); it is not on the core's machine at any
other path." Manual 13k does the same thing at line 4636 (`cd <oic-ui>/app`), and guide 13i.5, which is what
6.5's reference leads to, does it again at line 2165 (`cd /opt/oic-ui/app`, then `npm ci`).

On the deployment: `command -v node`, `command -v npm` and `command -v npx` all return nothing. So the reader
is told the checkout lives here and that it lives nowhere else, and then told to run a toolchain that is not
here. There is no install step for Node anywhere in the pages, and no sentence saying these commands belong on
a workstation. A newcomer who tries believes the deployment is broken. A newcomer who gives up skips the rung
that 6.5 itself calls "the rung that catches a screen which renders but does nothing".

What would fix it: one sentence at the head of 6.5's Verify saying these run from a workstation with Node and a
clone of the app, and naming `npm ci` as the prerequisite. The core side of 6.6 is fine: its
`check-contract.sh` line (6040) is plain bash and ran from the box, printing `OK ... 12 path(s) ... 25 model(s)`
exactly as pasted at 6072-6078.

## Where two pages disagree

**2. Manual 13j lines 4444-4446 disagree with the pattern table on lines 4436-4443.**

The sentence:

> A reader who sends `total 4210.00 due 2026-11-15 from Ferguson` matches all four; a reader who sends
> `inv-2026-0417` matches three and gets an empty `reference`, which is not an error because that field is not
> required.

The table three lines above gives `reference` as `\b([A-Z]{2,}-[0-9]{4}-[0-9]{3,})\b` and line 4443 says the
fourth pattern has no `re.IGNORECASE`. The first example text contains no uppercase reference at all, so it
matches three fields, not four. The second example, `inv-2026-0417` on its own, contains no total, no `from`
and no `due`, so it matches none of the four, not three.

What a newcomer does: sends the first example, checks the four fields, sees `reference` empty, and concludes
the extractor is broken. Section 13j exists to prevent exactly that trip, and 13m.0 (line 2728) states the rule
correctly. Non-blocking, but the wrong half is on the page whose stated purpose is "before you blame the
extractor". (Verified live: the extractor returns `reference: "INV-2026-0417"` when the uppercase reference is
present, and the draft action carries it.)

## Where I would have acted wrongly

**3. Runbook 6.4 lines 5299-5327: the pasted acceptance transcript is missing a line the command really
prints, and the missing line reads like a failure. Non-blocking.**

The paste starts at line 5306 with `C5-C intake acceptance`. My run printed this first, on stderr:

```
mail failure: Authentication to ap@acmetool.example is working but the mailbox cannot be read. Check that its
licence is still assigned, because a mailbox without one returns exactly this.
```

It is the pulled-licence scenario of `CON-SILENT-001` doing what manual 13l.5 line 4950 says it does, and the
run ends `10 PASS, 0 FAIL`, exit 0. But a newcomer sees an authentication failure above the header before
reaching the ten PASS lines, and the page they are following does not contain that line. What goes wrong:
they stop and debug a credential they do not have, or they report the acceptance run as failed. What would fix
it: `2>&1` in the paste, or one sentence saying the acceptance probe prints one alert on purpose. Manual 13l.7
(the command at line 4994) has the same gap.

**4. Runbook 6.5 lines 5953-5955: four event names are used with no definition, and the diagnostic cannot be
carried out as written. Non-blocking.**

> **An event arrives and the screen does not change**: check the event name against what the route sends.
> `EV_STEP`, `EV_PROGRESS`, `EV_NOTICE` and `EV_DONE` are four different things and only some of them move the
> step list.

Those four identifiers appear nowhere else in the three pages except a code sketch at 6.1 line 4704. The page
never says what the route actually sends, so "check the event name against what the route sends" cannot be
done from the page. I did it by curling the documented endpoint,
`GET /jobs/{job_id}/events?since=0&session=...`, and the wire names are lowercase: `event: progress` and
`event: step` (with `done` on the terminal frame). A newcomer debugging a silent screen has to discover that,
and 6.0's own goal for this phase is that a client can be written against the contract without reading source.
One line mapping the four constants to the wire strings closes it.

## What I had to guess

- **How to read `rendered/access.json`.** 6.2 line 4869 and manual 13j line 4465 both say the subject "has to
  be one this deployment declares. `rendered/access.json` lists them", with no command. I guessed
  `cat /opt/oi-core/rendered/access.json`, and it works: the file declares `probe-staff@officeinference.com`
  and the role `viewer`, so the pages' two values are valid. Had it not been readable to the login user I would
  have been stuck, because the pages give no other way to list the subjects. (`oi run --rm session show` prints
  the same list, which I found in manual 22, not on these pages.)
- **How to pass a session to `GET /approvals`.** 6.6 lines 6011-6013 and 13k line 4611 describe the route and
  no page gives a call for it. 6.5 line 5867 says the event stream carries the session as `?session=`, so I
  guessed the same here. It answered, with a pending job. Had the guess been wrong I would have had to read the
  contract file or the app, both outside what I was allowed to read, so the finding would have been "no
  documented way to call the route the screen is built for".
- **`OI_TEST_DATABASE_URL=<a database of your own>`** (6.2 line 4859). The page gives no command to start one,
  and the deployment's Postgres publishes no host port (`docker port postgres` is empty), so I guessed the
  test's own skip message would name one. It does: the skip text gives a throwaway Postgres on 55432 plus the
  full URL. Following it gave `14 passed`, and the three feed sentences match the paste at 4920-4928 word for
  word, including the verified case that reads `Approved at ... by dana (staff)`. Had the skip been silent I
  would have reported 6.2 as blocked.
- **Runbook 6.4's first command needs `/etc/oi-intake/intake.yaml`, and 6.4 never creates it.** The pointer is
  one paragraph later ("following manual section 13l.7") and the manual's Install section builds it. I guessed
  the manual was the setup path and ran the tenant-free demo instead. A reader who reads only the runbook
  reaches a missing file, which reads as a bad install.
- **The `note` field in 6.0's request body** (line 4547) is never explained anywhere I was allowed to read. I
  assumed free text the route stores. I did not send one.

## What worked

Everything the phase's core claims, on the deployment, as written.

- **6.0's two commands** (lines 4623-4634). `job_probe.py`: `PASSED: 7 right(s) held ... 1 skipped`, and the
  skip is named (`BOUNDARY`). The contract check printed `OK ... 12 path(s)` with `/jobs/{job_id}/approvals`
  among them.
- **6.1 and 6.3's probe.** `workflow_probe.py`: `PASSED: 13 right(s) held`. Every line matched the paste,
  including `ATOMIC child exit=-9 ... job row='queued', 0 step row(s), 0 chain row(s)`, `BOTHPRESENT`,
  `IDEMPOTENT far side applied 1 time(s) over 2 call(s)` and `BOUNDED ... 0.52s (deadline 0.5s)`. `CHAIN` read
  414 events against the paste's 108, and 6.1 lines 4760-4763 explain that the count grows and that
  `chain intact` is the number to read. `~/venv/bin/python -m pytest tests/test_workflow_engine.py -q` gave
  `23 passed`, as pasted.
- **6.2's five-command sequence** (lines 4865-4895). The mint accepted the declared subject and the `viewer`
  role. `POST /jobs` returned `awaiting_approval` with four step rows and the same digest
  `sha256:5b480b869b4bbb02f...` the page prints. `docker restart api` left the row `awaiting_approval` with
  the digest intact. The approval returned `succeeded` with six steps, and the step rows are one each:
  `read_message`, `extract_fields`, `draft_action`, `approval`, `post_entry`, `close_out`. The job row reads
  `succeeded | (none) | (cleared)`, and the feed renders the documented sentence verbatim, role and all.
- **6.2's chain tests.** With the skip message's throwaway database: `14 passed`. The three sentences match
  lines 4922-4924 exactly.
- **6.4's intake.** `tests/fixtures/intake_acceptance.py`: `10 PASS, 0 FAIL`, line for line as pasted. The five
  tenant-free commands of manual 13l.7 all work against a fresh scratch directory: `check-config` prints the
  five components with their owner, `cord-check` prints `no cord` and exercises the allowlist refusal,
  `watch-folder` shows the two-scan settle check and then `already taken` by content hash, `status` counts the
  record, `monitor` prints `no alert`.
- **`~/venv/bin/python -m pytest tests/ -q`: `884 passed, 23 skipped in 32.88s`**, the same numbers 6.4 pastes
  at line 5334. The bare `python3 -m pytest` really does fail with `No module named pytest`, as 6.0 line 4640
  warns.
- **`oi run --rm session ...` works without `--profile tools`** even though manual 22 prints the long form. The
  wrapper adds the profile itself, so the short form on these pages is correct and not a contradiction.

## What I could not test

- **The npm rungs.** No node, npm or npx on the box, and no other client deployment. This covers 6.5's
  `typecheck`, unit suite and `live.spec.ts`, 6.6's unit suite and `review.spec.ts`, and manual 13k's Run it.
  I could not see a single one of the pasted app outputs (the 37/28/12 test counts, the four live-view
  playwright tests, the nine review tests). The contract check in 6.6 did run, from the box, and passed.
- **Anything needing the client's tenant or a share.** No Microsoft tenant, no SMB or NFS share. `fetch`,
  `watch`, `enumerate-reach`, `migrate` and `verify-manifest` were not run. The pages say this themselves
  (13l.8, and 6.4's "the tenant is simulated"), so nothing here is being implied as covered.
- **The 409 `approval_drift` path against a live job.** It needs a job parked at the gate and a wrong digest,
  which spends more rows in an append-only chain. The pages prove it in the chain tests and in guide 13j.4,
  which is outside my allowed range.
- **The screens against a live gate.** 6.5 lines 5936-5942 and 6.6 lines 6096-6103 state this limit
  themselves.
- **The app checkout's commit.** 6.5 line 5885 says to check it with `git log`; git history is off-limits to
  me, so I did not, and I did not run `oi build app`.
- **Box state I changed.** I left one job and its chain rows in the deployment record (job
  `6142a243d4ae42cd80d18ca40bb8a495`, approved, six steps). I removed my scratch files from `/tmp`. I also
  removed `/tmp/oi-demo`, which the demo itself creates, so the next reader starts from the clean state the
  page assumes. One note on that: running manual 13l.7 again on this box without removing
  `/tmp/oi-demo/state` first does not reproduce the page. The fixed demo bytes are already in the journal, so
  every scan prints `"taken": []` and the middle line the page highlights never appears. I hit that, guessed a
  stale journal, removed the directory, and then got the page's three lines exactly, down to
  `intake_id in-86fdb4a2dc4bdb9f2daf`. A newcomer would read it as a connector that missed the file. It is the
  lowest-ranked finding here because a fresh box has no stale state.
