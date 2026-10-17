# C5 deliverable review, round 3: fidelity checker

Verdict: no

Three claims are wrong in substance — a line pointer and a command's path in the runbook, and a
retention figure in the manual — and two are stale or loose: a test count in the guide and one sentence
describing `watch-folder`'s output. None is a behaviour defect. Every pasted block reproduced, every
command ran once its path was the reader's own checkout, and every count I could check exactly is right
except the one below.

Round three was run from the manual: `main` at `0d0dfa2` locally, the deployment at `0d0dfa2` with
`/opt/oic-ui` at `6d28c3c`, all commands as printed. Nothing was rebuilt and no configuration was
changed. Test rows in the chain are approved; the live HTTP sequence in 6.2 was run and did write rows.

**The tree was clean when this round started and is not now.** Another reviewer made working-tree edits
to `docs/runbook.md` (and `services/api/answer.py`) while I was working: the block at old line 5902 now
reads `cd <oic-ui>/app` instead of `cd /opt/oic-ui/app`, and a new paragraph was added in 6.6 about the
12-hour clock. Line numbers and claims below are the committed text at `0d0dfa2`, which is what I read
and ran; the two edits are noted where they touch a finding.

## Claims the code contradicts

**1. `docs/runbook.md` 6.4.b, line 5541 — the wrong lines are named.**

Claim as written: *"**The fourth and fifth lines of the check section are the ones that matter.** The
host allowlist is asserted rather than described, and then exercised."*

What the command actually prints: `cord-check` prints a header, then **five** component rows from
`config_module.ownership(config)` (intake address, mailbox, app registration, consent grant,
credential), and only then the three network checks (host allowlist, the refusal of a host we control,
the licence/update/telemetry vocabulary check), then `no cord`. The host allowlist is the **7th line**
of the block and the refusal is the **8th**; of the marked `ok` lines they are the 6th and 7th. The 4th
and 5th lines are `app registration` and `consent grant` (or, not counting the header, `consent grant`
and `credential`). The page's own paste at lines 5528–5539 shows this order, so the sentence contradicts
the block directly above it.

Settled by: `~/venv/bin/python services/intake/intake.py cord-check --config /tmp/oi-demo/intake.yaml |
cat -n` on the deployment, using the scratch configuration from guide 13l.7. Output line 7 is
`ok   host allowlist      login.microsoftonline.com, graph.microsoft.com` and line 8 is
`ok   a host we control is refused before a socket is opened`. The code that fixes the order is
`services/intake/intake.py`, `cmd_cord_check` (header, `for row in config_module.ownership(config)`,
then the allowlist lines). `check-config` and `cord-check` both exited 0.

**2. `docs/runbook.md` 6.4.a, line 5419 — minor: `watch-folder` does not print one line per file taken.**

Claim as written: *"`watch-folder` prints one line per file it takes, and prints nothing at all for a
file that is still being written."*

What it does: it prints **one JSON object per scan**, whether or not the scan takes anything. Run with
`--iterations 3 --poll-seconds 1` against a pre-existing file it printed three lines: the first with
`"taken": []` (the file had not settled), the second with the file in `taken`, the third with the file
in `skipped` as `already taken`. Two of the three lines describe no take. The second half of the claim
is right: the unsettled file is named nowhere in the first line. The page's own paste at lines
5446–5449 shows exactly these per-scan lines, so only the summary sentence is loose.

Settled by: the same scratch run, and `services/intake/folder.py` (`watch_folder` prints one summary
per scan). This is a wording defect, not a behaviour one.

**3. `docs/runbook.md` 6.5, line 5902 — the printed path is not the path the page tells you to run
from. (Environment, not code.)**

Claim as written: the verify block opens `cd /opt/oic-ui/app`, under the sentence *"Run them in a clone
of the app on a workstation, with `npm ci` once first"*. Manual 13k's equivalent block opens
`cd <oic-ui>/app`, which marks the substitution; the runbook's does not. On a workstation clone the
deployment path does not exist: on this host the checkout is `/home/anthony/projects/oic-ui`, and
`/opt/oic-ui` exists only on the deployment box, where all three commands answer `command not found`. A
reader who follows the runbook literally from a workstation gets `cd: no such file or directory` before
`npm ci`; a reader on the box gets past `cd` and then hits the missing toolchain the same page describes.

Settled by: running the block from the workstation clone (`/home/anthony/projects/oic-ui/app`), against
`ls -d /opt/oic-ui` on the box and `command -v node npm npx` there (nothing), and runbook 6.5's own
sentence at line 5897 that the deployment does not carry Node. The concurrent reviewer's uncommitted
edit changes the runbook's line to `cd <oic-ui>/app`, which closes it.

## Pasted output that does not match

None. Every pasted block I could reproduce reproduced, including the blocks where a wrong line would
have been invisible:

- Runbook 6.1 probe block: all thirteen `ok` lines as pasted, `SHOWSACTION` reading from the job row,
  `ATOMIC child exit=-9 (killed=True), job row='queued', 0 step row(s), 0 chain row(s)`,
  `BOTHPRESENT control run: 4 step row(s), 8 chain row(s)`, `PASSED: 13 right(s) held`. The `CHAIN`
  line differs in its event count (71 today against 108 in the paste); that is the number the page
  says grows with every run, and it says the number to read is `chain intact`, which it was.
- Runbook 6.1 and 6.3: `23 passed` for `tests/test_workflow_engine.py` and `14 passed` for
  `tests/test_workflow_chain.py` against a scratch Postgres started with the command the skip message
  prints.
- Runbook 6.2 feed block: the three SQL sentences match character for character, including
  `Approved at 2026-10-16T16:20:00+00:00 by dana (staff)` for the verified branch.
- Runbook 6.2 record block: job row `succeeded | reason=(none) | digest=(cleared)`, six step rows, one
  per step, `event_type=approval | outcome=ok | decision=approve | assurance=unverified |
  actor=probe-staff@officeinference.com`, and the live feed rendering the honest sentence.
- Runbook 6.2 five-command sequence: session mint, `POST /jobs` (state `awaiting_approval`, digest
  `sha256:5b480b869b4bbb02f…`, four steps, `attempts 4`), `docker restart api`, digest intact after the
  restart, `POST /jobs/{id}/approvals` returning `succeeded` with six steps each one row. The
  `oi build api` half of step 1 was **not** run — rebuilding images is out of bounds for this round —
  and the running image already carried the route.
- Runbook 6.4 acceptance block: banner text, all ten `PASS` lines, `10 PASS, 0 FAIL`, exit 0, and the
  stderr sentence quoted at line 5318 (`mail failure: Authentication to ap@acmetool.example is working
  but the mailbox cannot be read. Check that its licence is still assigned, because a mailbox without
  one returns exactly this.`) appears verbatim on stderr.
- Runbook 6.4.a folder block: the same three lines, the same `1ccf744751ce302df4a1219520d9546f14cd45f1b32bf1dea4f2b4be0491be54`
  hash and the same 84 bytes (the hash of the documented PDF bytes, computed independently).
- Runbook 6.4.b `cord-check` block: identical to the live output, as noted above in finding 1.
- Runbook 6.5 app suite: `npm run typecheck` exit 0, then `Test Files 7 passed | 1 skipped (8)` and
  `Tests 161 passed | 12 skipped (173)`, with the per-file counts 28/37/28/33/7/12 skipped/16/12 as
  pasted. Playwright `e2e/live.spec.ts`: four tests, same names, 4 passed.
- Runbook 6.6 `e2e/review.spec.ts`: nine tests, same names, 9 passed. `check-contract.sh`: `OK the
  app's contract matches … 12 path(s): … 25 model(s)`.
- Guide 13i.1 checkpoint and runbook 6.0 contract check: both ran in a container on `ai-net`, printed
  the twelve paths and exited 0; the host form fails with `Temporary failure in name resolution`, as
  the runbook says.
- Guide 13i.5: `npx playwright test` collects 23 (13 passed, 10 skipped); `23 collected: 13 pass,
  10 skip` holds.

One caveat that is not a page claim: the first full `npx playwright test` run failed six of the review
tests, and the immediate re-run passed 13 with 10 skipped. The six failures were not reproducible on a
second run, and both specs pass when run as the pages print them (`e2e/review.spec.ts`,
`e2e/live.spec.ts`). I am recording the flake rather than a finding.

## Numbers

**4. `docs/operator-guide.md` 13i.5, line 2167 — the collected count is one behind the suite.**

Claim as written: `npx vitest run                                  # 172 collected: components, and the
contract`.

What the command collects today: `Tests 161 passed | 12 skipped (173)`, `Test Files 7 passed | 1
skipped (8)`. 172 is the total **before** round two's added test — 160 passed plus 12 skipped. Runbook
6.5 records that move (160 to 161) in the same round; the guide's comment was not updated with it.

Settled by: `cd /home/anthony/projects/oic-ui/app && npm test -- --run` on the workstation checkout at
`6d28c3c`.

**5. `docs/manual.md` 13l, line 4758 — the wrong Microsoft store and the wrong number.**

Claim as written: *"Entra keeps audit records for 180 days on a Business Standard licence. If somebody
asks in eight months whether a supplier's vendor ever had access, the tenant no longer knows."*

What Microsoft documents: Microsoft Entra ID retains **audit logs 30 days** on Entra ID P1/P2 and
**7 days** on Entra ID Free ([Microsoft Entra data
retention](https://learn.microsoft.com/en-us/entra/identity/monitoring-health/reference-reports-data-retention),
updated 2026-03-25). The same page states that Entra audit and sign-in logs are **separate** from the
Microsoft 365 Unified Audit Log, whose retention is managed through Microsoft Purview — that is where a
180-day default belongs. The page's conclusion (the tenant will not have the record in eight months) is
true, and more so than written; the sentence as it stands would have an operator tell a client they
have 180 days of Entra history when the Entra number is 30 days on P1/P2 and 7 days on Free, whichever
tier the licence carries.

Settled by: the Microsoft Learn page above, fetched 2026-10-16.

**Numbers I checked and found accurate.** The contract is 12 paths and 25 schemas, and `Job` declares
`pending_action`, `pending_action_digest` and `events_through` (`docs/openapi.json`; the same twelve
printed by `scripts/fetch_openapi.py --check` against the running service). `audit_row_canonical` takes
eighteen parameters — `p_chain_version` plus the thirteen-field base plus the four redaction fields
(`db/migrations/013_chain_covers_redaction.sql`). The nine `fetch` keys, thirteen rights, six step rows,
thirteen chain rows for the recorded job, four/eight control rows, and seven distinct steps all
reproduce. `tests/test_workflow_chain.py` is fourteen collected tests, three of them one parametrised
equality test over the three decisions. Intake: thirty-one keys in `build_record` and thirty-one in the
schema; ten CLI commands in the table and ten `cmd_*` functions; ten register rows and ten `PASS` lines;
ninety intake tests; 884 passed and 23 skipped on the box, with the skip split exactly as the runbook
says (14 chain, 5 store parity, 2 audit statement, 1 format coverage, 1 manifest schema); six reach
probes with one reached; FIRST_LOOKBACK_DAYS 14; STALE_POLL_MULTIPLIER 3; six AADSTS conditions over
seven codes, with the 65001 sentence quoted verbatim in the manual; `VENDOR_TENANT_IDS` empty. The
guide's twenty-four redactor unit tests resolve as `test_answer_redaction.py` (6) +
`test_redactor_failure.py` (4) + `test_streaming_redaction.py` (14). The manual's 46%/65%/8.4% and the
CHI n=240 figures are exactly as the cited research document holds them
(`docs/research/live-updates-transport-and-agentic-ux.md` §4.1, §5.1). The Part 11 claim is right: the
FDA's 2003 guidance lists §§ 11.50, 11.70, 11.100, 11.200 and 11.300 under "we intend to enforce all
other provisions", while the discretion covers validation, audit trails, retention and copying
([FDA, Part 11 Scope and Application](https://www.fda.gov/regulatory-information/search-fda-guidance-documents/part-11-electronic-records-electronic-signatures-scope-and-application)).

## Claims I could not check, and why

- **Everything that needs a Microsoft tenant.** `fetch`, `watch`, `enumerate-reach` against a real
  mailbox, the first interactive sign-in, consent, Conditional Access, licence removal and the
  customer secret store. The acceptance run proves these against `tests/fixtures/intake_env.py`, which
  is not the service. `check-config` and `cord-check` were run against the example configuration, not a
  client's.
- **`migrate` and `verify-manifest` against the deployment database.** I verified the half that the
  command depends on — `oi run … eval -c` has `DATABASE_URL` set and the image imports `yaml` and
  `psycopg` — but I did not apply intake's table to the shared deployment database. The page's claim
  that the host shell has no `DATABASE_URL` is confirmed (`[<empty>]`), and the page's `pending` case
  for `verify-manifest` is untested.
- **A rebuild of the api image.** `oi build api` in 6.2 is a rebuild, which this round forbids. The
  deployed image already serves the route, so the sequence after that step was run in full.
- **The mutation record in 6.1 and the mutation programme in 6.4.** Reverting a deliberate source
  mutation is an edit; `ATOMIC` itself was verified green, and its failure mode is a claim about an
  edited tree I did not build.
- **The C4 panel's twelve-runs-ten-fired history** in guide 13i.5. No C4 panel report is in
  `docs/reviews/`; the only record in the repositories is the app's own comment in
  `app/tests/integration.test.ts`, which repeats the figure and does implement the four attempts.
- **The dated measurement in manual 13k** — "of nineteen gates carrying `company` a `company` session
  was returned thirteen". The data has moved under another reviewer. The mechanism is exact and visible
  now: 26 gates carry `company`, 11 are written under the `workflow-probe` deployment and excluded, 15
  belong to `oi-core-dev-cloud`, and a `company` session was returned 14 (one further gate fails the
  root-subset test). The 19/13 shape is the same shape; I cannot re-run that day.
- **Anything requiring a browser I can look at, a real SMB/NFS share, or a client deployment.** The
  screens were checked through their tests, which is what the pages claim as their acceptance.

## What I checked and found accurate

Runbook 6.0: the eighteen-field argument, the four assurance values with only `unverified` reachable,
the `payload -> 'approval' -> 'identity'` path against the design document's wrong
`payload -> 'identity'` (line 329 of `docs/design/c5-approval-record-identity.md`), the route-order
defect described in the past tense with the wildcard now declared last (`services/api/app.py` 1090 and
1100), the twelve-path contract check, the `job_probe.py` filename, and the bare `python3` failure
(`No module named pytest`). Runbook 6.1: `insert_event(cur, ...)` called by `cmd_record`; the four-line
transaction; position from succeeded `job_steps` rows; `psycopg3` transaction comment in the engine;
the three files added. Runbook 6.2: `request_approval` writing the digest before the transition;
migration 021; migration 022's fallback and its SQL/Python pairs; the honest sentence in the live
feed; `jobs.result` genuinely absent from the table; the route's refusal to record a decision on a
running job; `409 approval_drift` carrying both digests; the four-defect narrative matching the code
comments. Runbook 6.3: the worker thread and deadline, `workflow_effects` in the unit,
`effect_key(job_id, step_name)` never the attempt, and the named test. Runbook 6.4: the module table
against the files, `.part` in both the deposit writer and the ingestion worker's skip list, the deposit
path shape, `requires_review` constant, the ten commands, the fetch keys including `last_error`, the
credential reader's three required and two optional keys with 0600 enforcement, the two-host allowlist
and GET-only Graph, the settlement and content-hash rules, the heartbeat multiplier and the three
alert classes. Runbook 6.5: `EventSource` constructed only in `api.ts`, the `?session=` reason, the
`EV_` constants' lowercase wire values, the 15-second ping, the 45-second window (`STALE_AFTER_MS =
45_000`), the record-first rendering and `seq` keying in `JobView.tsx`, and the box having no `node`,
`npm` or `npx` while `/opt/oic-ui/app` exists. Runbook 6.6: the empty allowance list in
`tests/contract.test.ts`, the bulk rule grouping by digest, the banner string, the app not calling
`GET /approvals` (`loadQueue()` reads known jobs), and the queue being scoped by deployment and access
roots. Manual 13j: the four patterns character for character, IGNORECASE on the first three, the
sixteen-hex-character reference fallback (verified by running the plan: `469c8ac29ae5d592`), total and
vendor required, and the `approvals.describe()` sentence. Manual 13k: the two screens' claims and the
live view's 45-second stall. Manual 13l: the installed-state comments, the credential refusal of a
certificate, the journal on the client's disk, the folder arrival's `marked` verdict with
`no_sender_on_a_folder_arrival` named, and the six AADSTS sentences. Manual 13m and guide 13m.0–13m.6:
the six-step plan, the step signature, a step with no database access, the gate reachable only from
`running`, resume from the step rows, the typed refusal, the bounded step, the stable effect key, the
empty `result` after a resume, the queue's lack of separation of duties, and the migration, probe and
audit commands.

Two editorial notes, neither a claim failure. Runbook 6.5 repeats the sentence about checking
`/opt/oic-ui`'s commit at lines 5914 and 5916. The migration 022 comment still names
`tests/test_workflow_feed.py`, a file the runbook correctly says was never written; migration 022 is
outside the pages under review, so I record it here rather than as a finding.
