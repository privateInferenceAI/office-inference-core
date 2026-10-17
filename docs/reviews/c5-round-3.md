# C5 deliverable review, round 3

The last round under the three-round cap in `AGENTS.md`. Three agents that did not write the work, run against
`main` at `0d0dfa2` (this repository) and `6d28c3c` (`oic-ui`), on the live deployment. Same scope and same three
rules as rounds one and two: **test from the manual and not the diff; state a verdict; a clean round is a real
result.** The reviewers were told the box's corpus is empty and that the api image is not theirs to rebuild, so
neither is reported as a discovery.

| Reviewer | Verdict | Report |
|---|---|---|
| executor | **yes** | [c5-round-3-executor.md](c5-round-3-executor.md) |
| cold reader | **yes**, 0 blocking, 10 non-blocking | [c5-round-3-cold-reader.md](c5-round-3-cold-reader.md) |
| fidelity checker | **no**, 5 page-side findings | [c5-round-3-fidelity.md](c5-round-3-fidelity.md) |

**Round two's corrections held.** The executor re-ran every command in scope from the pages: the "start a job"
command still prints the digest the page prints, the restart-and-approve sequence finishes over HTTP with six
step rows one each, the feed renders the honest sentence in both databases, the probes report their rights, the
intake acceptance is `10 PASS, 0 FAIL` with the documented stderr line, and the app's counts are the counts the
pages now print (161 passed, 12 skipped; live 4; review 9; 23 collected). The cold reader could follow every
end-to-end sequence and found nothing blocking.

## What round three found, and what happened to it

Nothing in the engine, the gate, the approval record, the connector or the screens. One code defect, and the rest
page-side.

**The code defect, and it was worth the round.** `activity_probe.py`'s `BYREQUEST` right failed and the guide's
13i.4 checkpoint returned zero rows, because the `no_source` branch in `services/api/answer.py` audited without a
`request_id` while every other audited path on that route passes one. So an answer that said "no document you may
read answers this question" carried an id in its body that could not find its own row. Fixed, and verified on the
deployment: `activity_probe.py` now holds all eight rights, `BYREQUEST` among them. The retrieval-error branch
had the same gap and is fixed with it.

| # | Finding | Found by | Disposition |
|---|---|---|---|
| 1 | The `no_source` and retrieval-error rows carry no `request_id`, so the answer's id finds nothing | executor | Fixed in code and verified on the box |
| 2 | Runbook 6.6 quotes the app's approval sentence with the feed's ISO timestamp; the app renders it through `toLocaleString()` | executor | Fixed: both renderers named |
| 3 | Runbook 6.4.b says "the fourth and fifth lines" of `cord-check` are the ones that matter; they are the seventh and eighth | fidelity | Fixed |
| 4 | Runbook 6.4.a says `watch-folder` prints a line per file taken; it prints one JSON object per scan | fidelity | Fixed |
| 5 | Guide 13i.5 annotates `npx vitest run` as 172 collected; it is 173 after round two's added test | fidelity | Fixed |
| 6 | Manual 13l says Entra keeps audit records for 180 days on Business Standard | fidelity | Fixed: Entra is 30 days on P1/P2 and 7 on Free, and 180 is the Microsoft 365 Unified Audit Log in Purview |
| 7 | Runbook 6.4's first command needs `/etc/oi-intake/intake.yaml`, which nothing on that page creates | cold reader | Fixed: the install page is named, and the tenant-free alternative |
| 8 | Guide 13m.3's `JOB=<… or one the probe reports>` has no source: the probe prints no job ids | cold reader | Fixed: the query, and the note that `--kill-child` needs a queued job |
| 9 | Runbook 6.2's chain-test command hides the skip reason without `-rs` | cold reader | Fixed: `-rs` and the reason it is not decoration |
| 10 | `oi` is defined twice and the definitions disagree about scripts and non-interactive shells | cold reader | Fixed in the manual and the guide: the deployment installs the symlink, the alias is for a box without it |
| 11 | Manual 13j's identity example carries `claimed_role: "staff"`, which is a group and not a role | cold reader | Fixed, with the distinction stated |
| 12 | Runbook 6.4.a calls the pulled licence one of the silent failures; the classifier names it and the floor catches it | cold reader | Fixed: two are silent, the licence is the half-way case |
| 13 | Runbook 6.5's commit check is printed twice and names no commit | cold reader | Fixed |
| 14 | `AUD-APPROVAL-001` is named once in the runbook and never explained | cold reader | Fixed: it is a register row and 6.2 explains it |
| 15 | The two `fetch` key lists differ | cold reader | Fixed: both carry `channel` and `last_error` |
| 16 | "round two" is not a thing a reader holding only the pages has | cold reader | Fixed: the second review round, named with its filed report |
| 17 | The app rung was introduced to a reader as `cd /opt/oic-ui/app` under a sentence sending them to a workstation | executor, fidelity | Fixed: `cd <oic-ui>/app` |

## What the reviewers could not test

No Microsoft tenant and no SMB or NFS share, so `fetch`, `watch`, `enumerate-reach`, the consent and licence
paths and the client's secret store are unproven against the real service; the acceptance run is the simulated
tenant the pages flag. No client deployment. No sign-in, and no browser the reviewers could look at. The source
mutations in 6.1 and 6.4 cannot be re-run by a reviewer that may not edit code. And the corpus is empty, which is
why `gate_check_run.sh` still exits 1 and why four of the twelve live integration tests fail: the request never
reaches a model, so there is no enforcement for the boundary runner to observe.

## State at the close of round three

    core                 main at the round-three fix commit, register well formed, 896 passed / 22 skipped
    oic-ui               main, 161 passed / 12 skipped
    the deployment       core and app current, ten images current, api rebuilt, BYREQUEST holding
    the register         145 items: 77 verified, 62 open, 6 waived; C5 34 rows, C6 25 rows
    open from the rounds the gate step row a denial leaves `running`, and the empty corpus

**The cap is reached, so this is where the panel stops.** Rounds one, two and three each found real defects and
each round's findings were fixed and re-tested; the residual items are the two above, both of which need a
decision rather than a page edit. A fourth round would be measuring the fixes to a round that already passed,
which is what the cap exists to prevent.
