# C5 deliverable review, round 2: fidelity checker

Verdict: **no**

The pages reviewed: `docs/runbook.md` 6.0 to 6.6, `docs/manual.md` 13j to 13m, `docs/operator-guide.md`
13m.0 to 13m.6 plus 13i.1's checkpoint and 13i.5's suite list. Method: the documented commands run on the
deployment (`ssh ubuntu@52.204.202.66`, `/opt/oi-core` at `a6b0643`, `/opt/oic-ui` at `bcffdae`, both clean),
the same commands run on the workstation checkout, the sources read, and one throwaway Postgres started for
the two database-backed pastes and one experiment. The executor was working on the same box throughout, so
chain counts moved; where a page names the count as growing, that is recorded as expected rather than as a
finding. Nothing outside `/opt/oi-core/docs/reviews/` was edited, no image was rebuilt, and no service was
restarted.

Two findings would stop a reader today: the app's own test suite is red at the commit runbook 6.6 pins, and
the boundary runner in guide 13i.5 exits 1 on this deployment.

## Claims the code contradicts

**1. The app's unit suite does not pass at `bcffdae`. (Wrong, and the paste below is the same defect.)**
Runbook 6.5, line 5887: *"Expected: the unit suite passes with the live-view tests named"*, and the pasted run
at lines 5901-5916 shows `Test Files 7 passed | 1 skipped (8)` and `Tests 160 passed | 12 skipped (172)` with
`✓ tests/contract.test.ts (28 tests)`. At `bcffdae`, from a clean checkout:

```
$ cd /opt/oic-ui/app && npm test -- --run
 ❯ tests/contract.test.ts (28 tests | 2 failed) 28ms
 Test Files  1 failed | 6 passed | 1 skipped (8)
      Tests  2 failed | 158 passed | 12 skipped (172)
```

The two failures are `the paths the app calls > has no path the app does not know about` (the contract now
serves 12 paths including `/approvals`; the test's literal list at `tests/contract.test.ts:57-60` still names
11) and `the fields the app reads > reads no field of a job that the contract does not declare, beyond the
named allowance` (the allowance at line 162 is still `["events_through", "pending_action",
"pending_action_digest"]`, and the contract now declares all three, so the observed list is empty). Every
other number and every test name in the 6.5 paste matched my run exactly. Settled by `npm test -- --run` in
`/opt/oic-ui/app`, and by the same command on the workstation (`/home/anthony/projects/oic-ui`, same commit,
same file hashes: `contract.test.ts` sha256 `8111d34a…`, `docs/openapi.json` sha256 `73466307…` on both
machines). **Limit:** I could not run this on the deployment itself because the box has no node or npm, which
is what the brief says; the test reads only files committed in the app repository, so the result does not
depend on the machine.

**2. `bcffdae` did not touch the allowance list it is credited with shrinking. (Wrong.)**
Runbook 6.6, lines 6083-6084: *"`oic-ui` regenerated that copy at `bcffdae` and shrank its
`tests/contract.test.ts` allowance list with it, and the run above is the one that says so."*
`git -C /home/anthony/projects/oic-ui show --stat bcffdae` shows one file changed, `docs/openapi.json`, 88
insertions; `app/tests/contract.test.ts` is untouched by that commit, still holds the 11-path list and the
three-name allowance, and that is why finding 1 is red. The commit message itself says only that the contract
copy was refreshed. The same page's line 6092 (*"`tests/contract.test.ts` compares the whole path set rather
than a subset, so a route added on the service side and not on the client side fails the app's own suite"*) is
accurate and is exactly what is happening now, which is why the paragraph above it reads as if the suite were
green.

**3. Guide 13i.5's counts do not describe what the two commands collect today. (Wrong.)**
`docs/operator-guide.md:2167` annotates `npx vitest run` with `# 57: components, and the contract`. The
command collects 172 tests (runbook 6.5's own paste says so: 160 passed + 12 skipped). `docs/operator-guide.md:2170`
annotates `npx playwright test` with `# 10: a real browser, real flows`. The command collects 23 tests:
13 passed and 10 skipped with no deployment, measured at `bcffdae` on the workstation. The 10 is
`e2e/app.spec.ts` alone (the C4 spec); the two C5 specs the rest of the page is about are
`e2e/live.spec.ts` (4) and `e2e/review.spec.ts` (9), which the runbook documents at 6.5 and 6.6. Settled by
`npx vitest run` and `npx playwright test` in `/home/anthony/projects/oic-ui/app`.

**4. `bash tests/fixtures/gate_check_run.sh` fails on this deployment, and 13i.5's checkpoint says every
command passes. (Blocking for the boundary proof.)**
`docs/operator-guide.md:2176-2177` lists `bash tests/fixtures/gate_check_run.sh` and
`bash tests/fixtures/gate_check_run.sh --jobs`; line 2180 says *"Checkpoint. Every one passes"*. Both forms
exit 1:

```
$ cd /opt/oi-core && bash tests/fixtures/gate_check_run.sh
forbidden copy written: data_class=regulated, not in ['public', 'internal']
FAILED: the second container never confirmed it enforces regulated
```

The temporary deployment the runner starts dies immediately. Reproduced by starting the same container by
hand and reading its log:

```
docker run -d --name fc-gatecheck-tmp --network ai-net --user 1000:1000 -v $PWD:/w -w /w \
  --env-file rendered/env -e DATABASE_URL=… -v $PWD/rendered:/app/rendered \
  -e OI_MANIFEST=/app/rendered/fc-gate-check-forbidden.json oi-core/api:local
docker logs fc-gatecheck-tmp
  PermissionError: [Errno 13] Permission denied: '/app/services/workflow/__init__.py'
```

`services/workflow/__init__.py`, `chain.py` and `plans.py` are mode `0600` in the box's working tree and in
`oi-core/api:local`; the runner's temporary container runs as `--user $(id -u):$(id -g)` (uid 1000), so the
import of the workflow package fails and the container exits 1. The deployed `api` service is unaffected
because compose runs it as root (`docker inspect api --format '{{.Config.User}}'` is empty), which is why
`POST /jobs` works. **Limit and the reason this is not simply a code defect:** the git index mode for these
files is `100644` (`git ls-files -s services/workflow/`), so a fresh clone would have them readable and the
runner would pass; this is a property of this deployment's working tree (mode-only difference, invisible to
`git status`) meeting the runner's `--user`. Either way the documented command does not pass here, and the
`BOUNDARY` right in `job_probe.py` is skipped for the same reason (`skip BOUNDARY no forbidden-class
deployment was started; run gate_check_run.sh --jobs`).

**5. Manual 13j's worked example matches three patterns, not four. (Wrong.)**
`docs/manual.md:4444`: *"A reader who sends `total 4210.00 due 2026-11-15 from Ferguson` matches all
four"*. That text carries no `[A-Z]{2,}-[0-9]{4}-[0-9]{3,}` token, so the reference pattern cannot match.
Running the real patterns on it (`services/workflow/plans.py`, `_AMOUNT`/`_VENDOR`/`_DUE`/`_REFERENCE`):

```
'total 4210.00 due 2026-11-15 from Ferguson'
   total: 4210.00 | vendor: Ferguson | due: 2026-11-15 | reference: None
   matched: 3 of 4
```

The sentence's second half is correct: a lowercase `inv-2026-0417` also matches three and leaves `reference`
empty. Operator guide 13m.0's version of this paragraph (`docs/operator-guide.md:2726-2729`) makes no such
claim and is accurate.

**6. Runbook 6.2 counts the comparison tests wrong. (Wrong, minor.)**
`docs/runbook.md:4931`: *"Four of those fourteen tests compare them, for all three decisions"*. The file
collects 14 tests (`pytest tests/test_workflow_chain.py --collect-only -q`), and exactly three of them
compare the view's sentence with `approvals.describe()`: `test_the_feed_and_the_api_say_the_same_sentence`,
parametrised over approve, deny and request_changes. No other test in the file calls `describe()`
(`grep -n 'describe(' tests/test_workflow_chain.py`). Runbook 6.0's version at line 4611 says "compares the
view's sentence against `describe()` for all three decisions" and is right; only 6.2's "four" is wrong.

**7. The view's fallback branch does not match `describe()` on an unrecognised multi-word decision. (Wrong,
narrow.)** `docs/runbook.md:4933-4934`: *"it is why the view's fallback branch is written to match
`describe()`'s rather than being independently reasonable"*. Migration 022 uses Postgres `initcap`, which
capitalises every word; `approvals.describe()` uses Python `.capitalize()`, which capitalises the first word
only. Written into a throwaway database and read back:

```
view      : Needs Review V2 at 2026-10-16T00:00:00+00:00 under the staff role. The person who decided …
describe(): Needs review v2 at 2026-10-16T00:00:00+00:00 under the staff role. The person who decided …
```

**Limit:** this is outside the three decisions the comparison test covers, and the route refuses any decision
outside those three, so it is reachable only through a hand-written chain row (which the tool permits and the
chain will verify). The three-decision claim holds; the wider sentence about the fallback branch does not.

**8. Operator guide 13m.1 describes the mutation's output incorrectly. (Wrong, minor.)**
`docs/operator-guide.md:2777`: *"`ATOMIC` reports the job `running` with a step row and no chain row"*. The
runbook's own mutation record (`docs/runbook.md:4777`) is
`MUTATED FAIL ATOMIC child exit=-9 (killed=True), job row='running', 0 step row(s), 0 chain row(s)`. The kill
sits between `store.put(job)` and the chain write, before any `job_steps` row is written
(`services/workflow/engine.py`, the unit around lines 98-122 and the step write at 517), so the observed
output is a moved job row and no chain row — which is the gap the guide is describing, without the step row.
This is evidenced by the runbook's pasted mutation line rather than re-run: re-running it means editing
`PostgresUnit.__enter__`, which I may not do.

**9. Two probe enumerations are one row short. (Nitpick.)**
`docs/runbook.md:5477-5480` says *"prints one row per probe"* and then names five: own mailbox, second
mailbox, directory, a different Microsoft service, and a foreign host. `services/intake/reach.py:75-86`
defines six probes; the missing one is `profile` ("the signed-in user's profile"). `docs/manual.md:4971-4972`
says "It makes six requests" and names five, omitting `vendor_host` (the host we control). The acceptance run
reports `6 probe(s)`, so the count is right in both places and only the enumeration is incomplete.

## Pasted output that no longer matches

**10. Runbook 6.5's `npm test -- --run` block. (Stale at the commit the page names.)**
Lines 5901-5916 paste `Test Files 7 passed | 1 skipped (8)`, `Tests 160 passed | 12 skipped (172)` and
`✓ tests/contract.test.ts (28 tests) 9ms`. Running the documented command in the app checkout at `bcffdae`
prints `Test Files 1 failed | 6 passed | 1 skipped (8)`, `Tests 2 failed | 158 passed | 12 skipped (172)` and
`❯ tests/contract.test.ts (28 tests | 2 failed)`. Every other line of the paste — all eight file names, the
per-file counts, the two named live-view tests — reproduced exactly. Command:
`cd /opt/oic-ui/app && npm test -- --run`. The same run's `typecheck` paste (lines 5894-5896) and the
`npx playwright test e2e/live.spec.ts` paste (lines 5922-5929: 4 tests, same four names, 50.5s against the
page's 53.0s) both reproduce.

**11. Runbook 6.4's acceptance block is missing a line the command prints. (Stale, minor.)**
Lines 5305-5327 paste the acceptance run from `C5-C intake acceptance` to `10 PASS, 0 FAIL`. Running
`~/venv/bin/python tests/fixtures/intake_acceptance.py` prints one further line, on stderr, every time (two
runs, same output; stdout and stderr captured separately):

```
mail failure: Authentication to ap@acmetool.example is working but the mailbox cannot be read. Check that
its licence is still assigned, because a mailbox without one returns exactly this.
```

It is the pulled-licence scenario of `CON-SILENT-001` reporting itself through `services/intake/intake.py:388`
and `graph.py:138`. The ten PASS lines, the header, the timestamp and `10 PASS, 0 FAIL` are otherwise
character-for-character what the command prints.

**Everything else pasted in 6.0 to 6.6 reproduced.** The 6.1 probe block (lines 4732-4747) matched line for
line, including `SHOWSACTION read from the job row`, `RECORDED 6 step row(s), 13 chain row(s) naming the job`,
`BOTHPRESENT control run: 4 step row(s), 8 chain row(s)` and `PASSED: 13 right(s) held`; `BOUNDED` read
`0.53s` against the page's `0.52s`, which is run-to-run timing and not a claim. `CHAIN` read `679 event(s)`
against the pasted `108` and the page says in the next paragraph that the count grows and that `chain intact`
is the number to read. The 6.2 feed paste (lines 4921-4924) reproduced exactly, in the same order, from the
test database the page says it came from, and so did `14 passed`. The 6.4.a `watch-folder` block reproduced
byte for byte, down to the `intake_id` `in-86fdb4a2dc4bdb9f2daf` and the sha256 `1ccf7447…`. The 6.4.b
`cord-check` block and the manual 13l.1 `check-config` block reproduced byte for byte. The 6.6 review block
(9 tests, same nine names) and the `check-contract.sh` block (12 paths, 25 models) reproduced; so did the
operator guide 13i.1 `--live` checkpoint.

## Numbers

**Correct, checked one at a time.** 884 passed / 23 skipped on the box (runbook 6.4), with all 23 skips named
rather than silent: 14 in `test_workflow_chain.py`, 5 in `test_store_parity.py`, 2 in
`test_audit_statement.py`, 1 in `test_format_coverage.py`, 1 in `test_manifest_schema.py` (`pytest -rs`
prints the reason and the command to start a database for every one). 896 passed / 22 skipped on the
workstation, which is the one-skip difference `jsonschema` explains. `23 passed in 0.65s` for
`tests/test_workflow_engine.py`, in both 6.1 and 6.3, to the hundredth of a second. `90 passed` for the
intake files. `14 passed` for the chain file. `12 path(s)` and `/jobs/{job_id}/approvals` in the contract
check, and `25 model(s)` in the app's contract check. `Thirty-four rows, phase: C5` in
`docs/requirements.yaml`. `thirteen rights held` from the workflow probe, and the job probe's
`7 right(s) held …, 1 skipped`. `thirty-one keys` from `build_record` (counted). `ten PASS lines` and the
ten named register rows, all present in the register. `6 probe(s), reached: own_mailbox` and
`granted scopes Mail.Read,offline_access`. `FIRST_LOOKBACK_DAYS = 14`, `STALE_POLL_MULTIPLIER = 3`.
`46%`/`65%`/`8.4%`/the Wroblewski and Nielsen quotes and the CHI `n=240` paragraph are all in
`docs/research/live-updates-transport-and-agentic-ux.md`; the `inotify(7)` quotes are verbatim in the man
page.

**Wrong or stale:**
- 13i.5's `# 57` for `npx vitest run` → 172 collected (finding 3).
- 13i.5's `# 10` for `npx playwright test` → 23 collected, 13 passed / 10 skipped (finding 3).
- 6.2's "Four of those fourteen tests compare them" → three (finding 6).
- 13j's "matches all four" → three of four patterns (finding 5).
- 13i.5's "Every one passes" → the two `gate_check_run.sh` commands exit 1 (finding 4).

**The eighteen fields, settled.** `audit_row_canonical` takes 18 arguments
(`select pronargs, pg_get_function_arguments(oid) from pg_proc where proname='audit_row_canonical'` → `18`,
ending `… p_egress_allowed boolean`), and its body contains no reference to `approval`
(`pg_get_functiondef(oid) like '%approval%'` → `f`). So runbook 6.0 line 4563 and operator guide 13j.5 line
2430 are right, and the register's row says the same number. The manual's glossary count of "seventeen of its
columns" (section 24, outside this review's scope) is a different and also-true count: 17 values appear in
the version-2 canonical string, and the 18th argument, `p_chain_version`, selects which string to build
rather than being hashed. There is no disagreement to report here; the two numbers count different things.

## Claims I could not check, and why

- **Anything needing a Microsoft tenant.** No tenant and no SMB or NFS share exist here, so `fetch`, `watch`,
  `enumerate-reach`, `verify-manifest`, the token rotation, the `AADSTS` sentences and the folder connector's
  settle check on a network filesystem were not exercised against a real service. The pages say this
  themselves (manual 13l.8, runbook 6.4's "the tenant is simulated"), and the acceptance run I reproduced is
  the same simulated tenant the pages flag. The `watch-folder` run I did reproduce is a local directory, which
  is what the page says it is.
- **A customer deployment.** This is the only one, so every claim about what happens on a client's box —
  cord-check against a client's own tenant, the client's secret store, a second client's corpus — is
  unchecked.
- **Anything requiring sign-in or a browser I can look at.** The Playwright rungs ran headless and passed or
  failed as the pages say, but the Review and live screens were not seen; the `409 approval_drift` path was
  not driven against a real draft rewritten mid-read (both pages flag this as their own limit too).
- **The mutation exercises.** `Ten of the eleven mutations were caught by the intended assertion` (runbook
  6.4) and the `ATOMIC` mutation (6.1) cannot be re-run without editing the code, which I may not do. The
  `ATOMIC` case is corroborated by the page's own before/after lines and by the code path; the eleven-mutation
  count is not corroborated by anything I can run.
- **"Five research reports are the input"** (runbook 6.0). `docs/research/` holds seven top-level reports
  plus one under `email-intake/`, and the page names no list, so the count cannot be checked as written.
- **"This project has now hit a documented command that cannot run five times"** (runbook 6.0). The four wrong
  file names it and 6.1 name (`jobs_probe.py`, `test_workflow_failure.py`, `test_workflow_gate.py`,
  `test_workflow_feed.py`) are all absent from the tree, and `tests/fixtures/job_probe.py` exists; the
  historical tally is not checkable.
- **The exact row values in 6.2's live run** (`chain: 14 row(s) naming the job, 7 distinct step(s)`,
  `176 event(s)`) are one run's numbers on a growing chain. The shapes reproduce from the probe and the route;
  those particular totals do not, and the page says the count moves.

## What I checked and found accurate

**6.0, the contract.** The route order is now correct in the source: `@app.post("/jobs/{job_id}/approvals")`
is declared at `services/api/app.py:878`, the wildcard `@app.post("/jobs/{job_id}/{action}")` at line 1100,
with the load-bearing comment above it, and the commit that moved it is `e982014`. On the deployment today a
valid session and an unknown decision on a real job get the approvals route's answer,
`{"error":"'bogus' is not a decision…","kind":"bad_decision"}`, and `audit_feed` holds approval rows written
through that route. The paragraph sits in the step whose subject is what a contract check cannot
prove, which is where a reader about to trust the green contract check needs it; what it does not say is that
the routes were reordered, and the reader only learns the route works 190 lines later in 6.2. That is a
clarity gap rather than a false claim. The rest of 6.0 holds: `awaiting_approval` is reachable from `running`
(`services/api/jobs.py:64`), the eighteen fields are eighteen and exclude `approval`, the identity and
Part 11 blocks match `identity_for` and `part11_manifestation` field for field, the four assurance values and
the single reachable one match `approvals.py`, `UNVERIFIED_REASON` is the sentence in the quote, the
`017_sessions.sql` argument about a permission read live is quoted correctly, and `python3 -m pytest` on the
box really does say `No module named pytest` while `~/venv/bin/python -m pytest` runs the suite.

**6.1 and 6.3, the engine.** `PostgresUnit.__enter__` opens `store.conn.transaction()` and `put`, `append`
and the chain write all run inside it; the chain row is written through `chain.record` on the store's cursor
into `insert_event`; the position is derived from succeeded `job_steps` rows; `workflow_effects` exists
(migration 023); the effect key is derived from job and step with a test that says so by name. The probe's
thirteen rights all held on my run and the two pasted lines for 6.3 match exactly. The honest limits the page
states — a killed runner is not a restarted service, a blocked thread is not freed, an external effect is
only deduplicable — are stated as limits and are accurate.

**6.2, the gate and the feed.** `request_approval` writes the digest before the transition; migration 021
adds the column and `PostgresStore` writes it in `on conflict do update set` as well as on insert; the
manifest declares `invoice_intake` with `needs_approval`; the `jobs` table has no `result` column, so the
disclosed gap about an empty result after a resume is real; the feed's SQL and `describe()` agree word for
word for all three decisions on the deployment (`Approved at 2026-10-16T23:47:17+00:00 under the viewer
role…`) and in the test database. The one sentence in all four places (manual 13j line 4379, runbook 6.0 line
4604, runbook 6.2 line 4983, operator guide 13j.6 line 2448) is the sentence the system prints. Manual 13k's
variant at line 4600 substitutes `2:14pm` for the ISO timestamp the code actually prints; 13j's version says
"verbatim apart from the timestamp and the role" and the substitution is visibly illustrative, so I record it
as a note rather than a finding. The page's own account of three wrong test-file names is corroborated: none
of the three exists, and `tests/test_workflow_chain.py` is where the tests are.

**The four patterns and their casing.** Manual 13j's table (lines 4436-4439) and operator guide 13m.0's
(lines 2721-2724) match `services/workflow/plans.py` character for character, including the non-capturing
group, the `[0-9,]` class and the trailing `\b`. The first three carry `re.IGNORECASE` and `_REFERENCE` does
not; `Total`, `FROM`, `DUE` match and `inv-2026-0417` does not. Runbook 6.0 and 6.2 do not print the
patterns; 6.2's description of them is accurate, including the detail that an ellipsis after "from" is
captured as the vendor (`[^\s,;]+` matches `…`).

**6.4, 6.4.a and 6.4.b, the connector.** Every one of the table's twelve rows exists and owns what the table
says; the CLI carries all ten verbs; `build_record` has exactly 31 keys; the deposit path is
`<documents_root>/company/intake/<date>/<sha8>/<filename>` with a `.part` write and an `os.replace`; a
duplicate writes a row pointing at the first and deposits nothing; `already` writes no row at all; `marked`
is a verdict count on the deposited rows; the first poll looks back 14 days; the monitor's window is three
poll intervals floored at five minutes; `check-config` and `cord-check` print exactly the pasted output,
including `credential` at the path the demo configured and `no cord`; the acceptance run prints ten PASS
lines and exits 0; the eleven-module claim about no database and no tenant is true of the run I did. The two
silent-failure and rotation proofs are cited to files that exist and to output I reproduced.

**6.5 and 6.6, the screens.** `STALE_AFTER_MS = 45_000` in `app/src/screens/JobView.tsx`; the API's
keep-alive is `ping=15` by default (`services/api/app.py:1283`); `EventSource` is constructed only in
`app/src/api.ts` and the session travels as a query parameter; `JobView.tsx`, `jobSentences.ts`,
`Review.tsx`, `approvalSentences.ts` and `queue.ts` all exist and do what the pages say; `loadQueue()` reads
the jobs this browser started, which is why the screen states its scope. `GET /approvals` is scoped by access
roots and sorted newest first. The Playwright pastes reproduce: live 4 passed with the same four names, review
9 passed with the same nine names, and the stall test really waits out the shipped window (47.4s).

**13i.1 and the cross-references.** `check-contract.sh --live http://api:8088` runs in the documented
container form, prints the twelve paths and 25 models, and exits 0; the sibling-path caveat and the explicit
core-copy form both behave as the pages say. The cross-references I followed resolve: 6.2 to 6.0, 6.5 to
13i.1, 13m.0 to 13j.2, runbook 6.4 to manual 13l.7, operator guide 13m.6 to runbook 6.2.
