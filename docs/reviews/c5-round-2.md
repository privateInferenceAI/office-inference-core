# C5 deliverable review, round 2

Three agents that did not write the work, run against `main` at `a6b0643` in this repository and `bcffdae` in
`oic-ui`, on the live deployment at `/opt/oi-core`. Scope: `docs/runbook.md` 6.0 to 6.6, `docs/manual.md` 13j to
13m, `docs/operator-guide.md` stage 13m.0 to 13m.6, plus guide 13i.1's contract checkpoint and 13i.5's suite
list. Everything else in those three documents is round three's business or another phase's.

The rules, unchanged from round one and from `docs/handover-c5.md` section 7:

1. **Test from the manual, not from the diff.** Run the documented thing and see what happens now.
2. **State a verdict: yes or no.**
3. **A clean round is a real result.** Do not manufacture a finding to seem useful.

Each reviewer was told plainly what it could not have — no Microsoft tenant, no SMB share, no client
deployment, no browser — so a gap is reported rather than implied.

| Reviewer | Verdict | Report |
|---|---|---|
| executor | **no** | [c5-round-2-executor.md](c5-round-2-executor.md) |
| cold reader | **yes** | [c5-round-2-cold-reader.md](c5-round-2-cold-reader.md) |
| fidelity checker | **no** | [c5-round-2-fidelity.md](c5-round-2-fidelity.md) |

**The round is a no.** Two reviewers said no and the third said yes with a blocking finding, and they converged
rather than scattering: the behaviour C5 describes works, and the pages' instructions for checking it did not.

## The correction round one could not execute, executed

Round one left one correction with no execution behind it, deliberately, because running it writes to the
deployment's chain: the "start a job" command in manual 13j and guide 13m.0. The executor ran it. Its output
matches the page line for line — `state`, `steps`, `job_id`'s label, and the digest `sha256:5b480b869b4bbb02f…`,
which is content-addressed, so the same input gives the same value on every rerun. The command is now cleared by
execution rather than by the route table, the registry and the contract.

## Findings, and what happened to each

Twenty-five findings across the three reports, after merging three duplicates. "Fixed" means the change is in
this round's commits; the roles' own reports hold the evidence in their words.

| # | Finding | Found by | Disposition |
|---|---|---|---|
| 1 | The app rungs are run with `npm` at `/opt/oic-ui/app` on a box with no Node: all three exit 127 | executor, cold reader | Fixed: four pages now say the rungs run on a workstation, and 6.5/6.6 say the box's checkout is there for the build |
| 2 | The app unit suite is red at `bcffdae` (2 failed, 158 passed) against the pasted 160 passed | executor, fidelity | Fixed in `oic-ui`: the path list gained `/approvals`, the allowance list was deleted, and the suite is 161 passed |
| 3 | Runbook 6.6 credited `bcffdae` with shrinking the allowance list; that commit changed one file | fidelity | Fixed: the sentence was mine, from the pre-round commit, and it is corrected |
| 4 | `gate_check_run.sh` exits 1 | fidelity | Partly fixed: the checkout's modes were normalized and the api image now normalizes what it copies. It still fails, for a second and unrelated reason — see 24 |
| 5 | Manual 13j says the example matches all four patterns; it matches three | cold reader, executor, fidelity | Fixed, with the sharper half the executor found: an unmatched reference is not empty in the entry |
| 6 | Manual 13j calls the command's input "the message above, flattened"; it is neither | executor | Fixed: the page now says what the command sends and that the two forms give different digests |
| 7 | Manual 13k quotes the approval sentence with a `2:14pm` timestamp no renderer produces | executor | Fixed: the example is the recorded ISO value, and the app's locale rendering is stated |
| 8 | Manual 13k says the queue is scoped by access roots and "every session holding the roots sees every gate" | executor | Fixed: the deployment condition is now stated, with the measurement that exposed it |
| 9 | Manual 13k says all four Review rules are tests; the second had none | executor | Fixed in `oic-ui`: a test now asserts no control takes focus and Enter approves nothing, proved by mutation |
| 10 | Runbook 6.0's Part 11 block says `approved` where the record says `approve`, and `staff` where the role is `viewer` | executor | Fixed: both values, and a sentence saying which is which |
| 11 | Runbook 6.2 says four of fourteen tests compare the two sentences; three do | fidelity | Fixed |
| 12 | Runbook 6.2 says the view's fallback matches `describe()`; `initcap` and `capitalize` differ on multi-word input | fidelity | Fixed: the limit is written down, and the route's three decisions are named as the boundary |
| 13 | Runbook 6.2 says the deployment has no approval row yet; it has eleven | executor | Fixed |
| 14 | Runbook 6.4's acceptance paste omits the `mail failure:` line the command prints on stderr | cold reader, fidelity | Fixed: runbook 6.4 and manual 13l.7 both show it and say it is `CON-SILENT-001` working |
| 15 | Two pages name five of the six `enumerate-reach` probes | fidelity | Fixed in both places |
| 16 | Runbook 6.5's diagnostic names `EV_STEP` and never the wire name `step` | cold reader | Fixed: the mapping is in the bullet |
| 17 | Two Playwright pastes are abridged without saying so | executor | Fixed: both are labelled as trimmed, and the label says what was dropped |
| 18 | Guide 13i.5 annotates `npx vitest run` as 57 and `npx playwright test` as 10 | fidelity | Fixed: 172 collected and 23 collected, one of them split into passed and skipped |
| 19 | Guide 13i.5's checkpoint says every command passes | fidelity | Fixed as a page: two ways the boundary runner fails without the gate being broken are now written down |
| 20 | Guide 13m.0 says an unmatched reference comes back empty | executor | Fixed, with the substitution named |
| 21 | Guide 13m.1 says the ATOMIC mutation reports a step row; the mutation record shows none | fidelity | Fixed |
| 22 | Manual 13l.7's demo does not reproduce against a state directory left from an earlier run | cold reader | Fixed: the scratch directory is removed first, and the reason is stated |
| 23 | A denied job leaves its gate step row `running` while the job is terminal | this round's Lead pass | Documented in guide 13m.3 and runbook 6.2, with the difference from cancellation named. The code change is not made: see below |
| 24 | The deployment's `documents` collection holds zero points and `documents/` is empty, so retrieval answers `no_source` for every question | this round's Lead pass | Open. It is box state rather than a page defect, and it is what the boundary runner now trips over |
| 25 | `docs/review-log.md` had no C5 entries at all | this round's Lead pass | Fixed: round one's entry is reconstructed from the handover with its provenance stated, and round two's follows |

Two findings are deliberately not fixed as code in this round.

**23, the gate row left `running` on a denial.** The job reaches `failed` with `denied_at_the_gate` and no
post-gate step has a row, which is what `APR-RESUME-001` requires. The gate's own row keeps the state it had,
because it is the step that was interrupted rather than one that failed, and a cancellation marks its in-flight
step `failed` instead. A reader seeing a `running` row on a finished job will go looking for work that is not
happening, so the behaviour is written down in both pages. Closing the row is a change to `jobs.decide()` and the
engine, it needs its own test, and it belongs beside C6's second caller of the gate rather than in a review round.

**24, the empty corpus.** The 10,033 documents this deployment held are no longer on the box, and the vector
collection exists with zero points, so every question answers `no_source`. That makes the retrieval checkpoints
unverifiable here and it is what `gate_check_run.sh` now fails on: its first request never reaches a model, so the
boundary has nothing to refuse. The corpus is archived in `officeinference-corpus-878451098165` and
`disk-ingest-documents-878451098165`, and restoring it is a deployment decision rather than a page fix.

## What the reviewers could not test, so nothing here implies it

- **No Microsoft tenant and no SMB or NFS share.** `fetch`, `watch`, `enumerate-reach`, the credential rotation
  and `verify-manifest` were not exercised against the real service. The intake acceptance run is the simulated
  tenant the pages themselves flag, and the pages' register notes already say so.
- **No client deployment.** Every claim about a client's own box is unverified on this one.
- **No browser and no sign-in.** The Playwright rungs ran headless on a workstation; the 409 drift path against a
  genuinely rewritten draft is unexercised, as both pages say of themselves.
- **The mutation exercises.** `ATOMIC`'s mutation and 6.4's eleven mutations need code edits a reviewer may not
  make. `ATOMIC`'s is corroborated by the page's own before-and-after lines and by the code path.

## What the round cost, in state

The chain is append-only, so the reviewers' writes are part of the record: nineteen jobs started over HTTP, one
approval, one denial, one change request, three probe runs, a `--kill-child`, one audit export, and two throwaway
Postgres instances that were removed. The Lead's pass added four more jobs, one approval, two denials and one
drift refusal. Both checkouts were left clean, and every scratch file the reviewers wrote was removed.
