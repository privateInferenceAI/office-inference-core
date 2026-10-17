# C5 deliverable review, round 3: cold reader

Verdict: yes

A competent stranger can follow these pages and end up with a working C5. I followed them on
`ssh -i … ubuntu@52.204.202.66`, repo at `/opt/oi-core`, and every end-to-end sequence ran and
matched its recorded output, including the numbers: 6.0's probe and contract check, the whole
thirteen-right workflow probe, 6.2's five-command gate-and-restart run, 13m.0 to 13m.5, and the
intake acceptance. Nothing blocked me. The ten findings below are real but none of them stops the
work; the worst leaves rights unheld without saying so.

**Revision reviewed.** Citations are against `runbook.md` md5 `0951d2224d8f6270098d6827b13ea8f8`,
`manual.md` md5 `f5c6b554a34705ac74a8505eca87681d`, `operator-guide.md` md5
`446de6cf55e205d88d97d15a5b6ffdd6`, read 2026-10-16 20:16-20:26 EDT. The runbook's mtime is
20:25:46, nine minutes after this session opened: 6.5's verify block read `cd /opt/oic-ui/app` when I
first opened it and reads `cd <oic-ui>/app` now. I re-checked every sentence I quote against the
current text; finding 3 below is the one that is still there.

## Where I could not proceed

**1. The first command of 6.4 names a file nothing on that page creates — runbook.md:5241.**
*"`~/venv/bin/python services/intake/intake.py check-config --config /etc/oi-intake/intake.yaml`"*,
with 6.4:5284 promising *"`check-config` prints `ok` and the five components with their owner."*
A newcomer pastes it and gets `FAIL … is not a configuration this service will run / - no-config: no
configuration at /etc/oi-intake/intake.yaml`, exit 1. `/etc/oi-intake` does not exist on the box, and
6.4 never says to create it: the origin is manual.md:4762-4792, and the no-client substitute is
manual.md:5022-5047, which 6.4 names only in the "As it ran" paragraph at 5325-5326 — four hundred
lines below the commands, and that paragraph itself says *"There is no client tenant and no database
on this machine."* What goes wrong is a stop plus a guess: the reader cannot tell whether the product
refused their deployment or their page order is wrong. The failure message is clean, so recovery is
one search away. Ranked first among the non-blocking items because it is the first thing the step
asks you to run.

**2. A job id with no source — operator-guide.md:2839, with 2812-2815.**
*"`JOB=<the job_id 13m.0 printed, or one the probe reports>`"* and, in 13m.2, *"on a job id the probe
left behind:"* followed by `--kill-child <job_id>`. Neither source exists for a reader who ran 13m.1
first: `bash scripts/probe.sh workflow_probe.py` prints 17 lines and **zero** 32-hex job ids
(measured), while leaving 29 jobs in `awaiting_approval` in Postgres. 13m.0 does print one, but the
page offers the probe as an alternative and the probe is silent. I guessed
`select job_id from jobs where state='awaiting_approval' order by created_at desc limit 1`, which no
page gives. If the reader instead reuses the gated job from 13m.0, `--kill-child` exits 3 with
`child: finished without being killed, so the kill point was never reached` — safe, but it reads as
an engine failure rather than a wrong job id.

**3. The commit check cannot be answered, and the sentence is printed twice — runbook.md:5914-5916.**
*"Check it is at the commit these screens were built from before running the commands:
`git -C /opt/oic-ui log --oneline -1`."* No page in range names that commit; `bcffdae` is named at 6127-6134 for
the app's copy of the contract, not for the screens, so I assumed they are close and cannot confirm
it. The instruction then repeats verbatim at 5915-5916 around *"That check is on the deployment and
the three commands above are not"*, which reads as a leftover edit.

**4. `AUD-APPROVAL-001` is named and never explained — runbook.md:4531.**
*"why two defects are still cheap to fix (`AUD-APPROVAL-001`, `UI-FEED-001`)."* `UI-FEED-001` is
explained at 4848-4856. `AUD-APPROVAL-001` appears exactly once in the three pages I read; a newcomer
who wants to know what was left open has to leave the pages for `docs/requirements.yaml`, which
4509-4510 does name as the register.

## Where two pages disagree

**5. `oi` is defined twice, and the two definitions disagree about scripts — runbook.md:181-210
against manual.md:420-434 and operator-guide.md:126-129.**
The runbook: *"install `oi`, which every command after this one assumes. The wrapper is in the
repository at `deploy/office/oi`"* → `sudo ln -sf /opt/oi-core/deploy/office/oi /usr/local/bin/oi`,
with the history that the documentation once referred to `oi` more than a hundred times when *"the
command did not exist: not on the box, not in the repository, not as an alias"*, and that this is
*"how a deployment came to be running an image from before a fix"* — 10,033 documents left on disk.
The manual §10 and guide stage 0 say the opposite: *"Set an alias once per shell"* and *"**The alias
exists only in the shell where you typed it.** In `ssh host 'command'`, in a script, or in a cron
entry, write the compose command in full … you will get `oi: command not found`."* Both are in force,
and the difference decides how a reader writes every command: I ran the pages' commands through
`ssh host 'bash -s'` — precisely the form the manual says cannot work — and they worked, because
`/usr/local/bin/oi` is a symlink to the repo wrapper. A newcomer following §10 rewrites commands by
hand or avoids one-liners; one who installs only the alias on a fresh box meets the incident the
runbook records.

**6. The identity example carries the two values the runbook says were wrong — manual.md:4355-4362
against runbook.md:4571-4576 and 4585-4589.**
The manual's record sample: `claimed_subject: "dana"`, `claimed_role: "staff"`. The runbook: the role
is a session role, *"`viewer` is the one this deployment declares, while `staff` is a group in the
access registry. A copy that said `approved` and `staff` was wrong twice in one block, and both were
found by reading a real row."* I confirmed the deployment: `rendered/access.json` holds
`"roles": {"viewer": []}` and `"groups": {"staff": […]}`, the job row carries `role: viewer`, and the
feed sentence says *"under the viewer role"*. A newcomer writing a parser or a test from 13j would
expect `staff` in `claimed_role` and never see it.

**7. The pulled licence is described as silent on one page and classified on another — runbook.md:5379
and 5385-5387 against manual.md:4961-4968 and runbook.md:5348.**
6.4.a: *"the two ways intake stops that produce no error at all"*, then *"A revoked scope, a pulled
licence and a mail rule diverting invoices all leave a credential that authenticates and a queue that
is empty … Error logging reports nothing in all three cases."* The manual: *"**Three of the four
produce no error at all**"*, with the licence row reading *"A 403 with a healthy token | The
classification, and the floor as well"* — and 6.4's own paste at 5348 says
`pulled licence: exit 2, kind mailbox_not_readable, alerts mailbox_not_readable,volume_floor`. A
newcomer building the monitoring from 6.4.a expects a licence pull to be invisible to the classifier
and would read the classifier firing as a defect; "two ways" also cannot be reconciled with the three
scenarios named in the same paragraph.

**8. The two `fetch` key lists differ — runbook.md:5285-5287 against manual.md:4883-4894.**
The runbook lists `channel`, `messages_seen`, `no_attachment`, `attachments`, `deposited`,
`duplicates`, `already`, `marked`. The manual's table lists the same attachment counters plus
`last_error` and omits `channel`. `fetch` needs a client mailbox, so I could not run it and cannot say
which list is complete; a newcomer writing a parser from one page meets an unexpected key.

## Where I would have acted wrongly

**9. The page promises a skip reason and the command it gives prints none — runbook.md:4915-4919 with
the command at 4865.**
*"**The last command needs its own database, and it says so rather than skipping quietly.** …
Without the variable the tests **skip with the reason and the command to start one**, which is a
skipped right and not a held one."* Run as written, `~/venv/bin/python -m pytest
tests/test_workflow_chain.py -q` prints `ssssssssssssss [100%]` and `14 skipped in 0.03s` — nothing
else. The reason and the `docker run -d -p 55432:5432 …` command appear only under `-rs`. So I would
have ticked off fourteen rights as explained-and-skipped and never started the test database,
including the three tests 6.2:4938 says hold the feed sentence to `describe()` for all three
decisions. The tests do say it; the documented command hides it. This is the only finding that would
have changed the state of the deliverable, and it is the reason this one is ranked worst.

**10. "round two" is not a thing a stranger has — runbook.md:5898, 5947, 6134.**
*"which round two's executor confirmed by running each of them there"*, *"The counts moved by one in
round two"*, *"round two's fidelity checker is what found that."* A reader holding only the pages
cannot know what round two is, and the sentences are the pages' evidence for why the numbers are what
they are. I read them as an earlier review round of this document set and moved on.

## What I had to guess

- The psql query for a job id at the gate (13m.2, 13m.3); the pages give none.
- That `/etc/oi-intake/intake.yaml` must be created from manual 13l's install block before 6.4's
  commands can print `ok`.
- That `--kill-child` needs a job the probe left *queued*, and that a wrong choice exits 3 rather
  than doing damage.
- That the union of the two `fetch` key lists is the real object.
- What `AUD-APPROVAL-001` is.
- What "round two" refers to.
- Which app commit "the commit these screens were built from" is; I assumed `bcffdae`.

## What worked

Everything I could run, ran, and matched the pages rather than approximately:

- **6.0 verbatim.** `job_probe.py` → `PASSED: 7 right(s) held … 1 skipped`. The network-contained
  contract check → `OK … (12 path(s): … /jobs/{job_id}/approvals …)`, exit 0; the host form fails
  with `[Errno -3] Temporary failure in name resolution` exactly as 6.0 says. `pytest tests/ -q` →
  `884 passed, 23 skipped in 33.25s` against the page's 884/23 in 32.23s.
- **6.1-6.3 verbatim.** All thirteen probe rights, line for line as pasted, including
  `ATOMIC child exit=-9 … job row='queued', 0 step row(s), 0 chain row(s)` and
  `IDEMPOTENT … applied 1 time(s) over 2 call(s)`; `test_workflow_engine.py` → 23 passed (page: 23).
- **6.2's five-command sequence, end to end.** Rebuild, mint, `POST /jobs` → `awaiting_approval` with
  four rows, digest `sha256:5b480b869b4bbb02f12ed892bdef87f0e27d89bb1160285b36e26e2c182c6687` — the
  page's own value — `docker restart api` with the digest intact, approval → HTTP 200, six steps each
  exactly one row, digest cleared, and the feed rendering the honest sentence verbatim.
  `oi run --rm session mint` works without `--profile tools`, though earlier runbook sections use the
  profile.
- **The refusals.** Deny with no reason → HTTP 400 `bad_decision`, job left `awaiting_approval`;
  deny with a reason → `failed/denied_at_the_gate`; request_changes → `failed/changes_requested_at_the_gate`;
  a wrong digest → HTTP 409 `approval_drift` carrying `waiting_on` and `quoted`, which is 13j's "both
  hashes".
- **The extractor claims are exact.** The one-liner gives vendor `accounts@ferguson.example` and
  digest `5b480b86…`; the three-line message gives vendor `Ferguson` and digest `1eb963c0…` —
  "different entries with different digests", proved.
- **13m.0 through 13m.5 verbatim**, including the four-line expected output, the gate rows
  (three succeeded, `approval running`, no `post_entry`/`close_out`), one row per post-gate step, the
  chain query listing every step, and `audit export`/`verify` → `ok … 272 event(s), chain intact`
  with its honest completeness note. 13m.2's kill child → exit 137 and `queued/0/0`.
- **6.4's acceptance and the no-client path.** `10 PASS, 0 FAIL`; the skip census `-rs` confirms 14
  workflow_chain + 5 store_parity + 2 audit_statement + 1 format_coverage + 1 manifest_schema, exactly
  as 6.4:5363-5368 says. Manual 13l.7's five commands, verbatim, reproduce the page's three
  watch-folder lines and the same content hash `1ccf744751ce…`; `cord-check` → `no cord`, exit 0;
  `monitor` → `no alert`. `check-config` reports two deliberate faults in one pass, as 13l.1 claims,
  and the documented container forms of `migrate` and `verify-manifest` run (`$DATABASE_URL` is set
  in the `eval` service).
- **6.6's contract check** → `OK … 12 path(s) … 25 model(s)`.
- **Every path the pages name exists**, in the core and in `/opt/oic-ui`, including all six app
  sources, both spec files and `check-contract.sh`. The recorded "no Node" warning is accurate:
  `node`, `npm` and `npx` are all absent.

## What I could not test

- **No Microsoft tenant.** `fetch`, `watch` and `enumerate-reach` cannot get past the credential —
  they exit with `CredentialRefused: no credential at …`. So 13l's Entra steps 2-9, the app
  registration and consent, the six `AADSTS` mappings, rotation, Conditional Access and the licence
  scenarios are untested by me, as 13l.8 and 6.4's simulated-tenant note already say.
- **No SMB or NFS share.** The polling and settle behaviour is proved against a local directory only;
  a network share is the client's, as 13l.8 says.
- **No client deployment.** The real `/etc/oi-intake` configuration, the timer, the alert transport
  (outbox or SMTP) and `verify-manifest` against a live ingestion cycle. One loose end I did not
  chase: on the demo file it printed `pending` plus *"demo-invoice.pdf is a format the ingestion
  worker does not read"* — 13l.3 says `pending` is normal until the next cycle, and I could not
  resolve the note without leaving the pages.
- **No Node or browser.** 6.5's and 6.6's three commands, the 161/12-skipped unit counts and both
  Playwright runs are the page's evidence, not mine, and the pages warn correctly about the box.
- **App-internal claims** I may not read source for: `api.ts` as the only `EventSource`,
  `loadQueue()` reading this browser's jobs, the coverage of `approvalSentences.ts`, and the screen's
  409 branch.
- **The register.** `docs/requirements.yaml` is outside the pages I was allowed to read, so
  "Thirty-four rows, `phase: C5`" (4509-4510) is unverified by me, as is the requirement text behind
  each ID.
- **The runbook moved during the review** (mtime 20:25:46), so line numbers are only as good as the
  hash at the top of this report.
