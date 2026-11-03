# Product verification baseline, 2026-11-03

Phase B of `docs/runbooks/product-verification.md`, run on the live product deployment. The purpose was to
walk the whole acceptance list twice, fix what it found, and leave a baseline that later runs can be compared
against.

## Environment

| Item | Value |
|---|---|
| Deployment | `oi-core-product-20261103` on `i-0a3fca3c1141cbcad`, EIP 100.60.208.224 |
| Release branch head | `e7397ae`, after PR #342 and PR #343 |
| Corpus | canonical v2 synthetic, 80 documents, 10,576 chunks |
| Analyst model | `bedrock/converse/us.moonshotai.kimi-k3` |
| Operator path | SSH to the box, minted development sessions. This is admin-only and is replaced by Phase D |

## Procedure

Phase B was run twice with the same steps, then a third time after the second product fix. Each step used the
probe or script named in the runbook; there was no single command that declared readiness.

| Step | What ran |
|---|---|
| B1 | `scripts/verify/step-b1-questions.sh`, five questions including one cross-document question |
| B2 | `scripts/verify/step-b2-invoice.sh`, synthetic invoice stops at the gate |
| B3, B4, B7 | `product-acceptance.py` on the box: research brief with helper and approval, workspace brief with files, code, fetch, artifact and approval, and the staff boundary denial |
| B5 | `scripts/verify/step-03-audit.sh`, export and verify the audit chain |
| B6 | `scripts/verify/step-04-n8n.sh`, both templates active, invoice webhook starts a governed job at the gate, and a person can open that gate |
| B8 | Evidence copied off the box before any further work |

## Results

| Run | When | B1 | B2 | B3/B4/B7 | B5 | B6 | Total | Result |
|---|---|---|---|---|---|---|---|---|
| 1 | 2026-11-03 22:10Z | pass after a probe fix | pass | pass (51s) | pass | pass | not timed | pass, but gate cleanup exposed the review defect |
| 2 | 2026-11-03 22:10Z | pass | pass | pass (53s) | pass | pass | 71s | pass, before fixes |
| 3 | 2026-11-03 22:18Z | pass (10s) | pass | pass (51s) | pass | pass (4s) | 65s | pass, after the review fix |
| 4 | 2026-11-03 22:19Z | pass (12s) | pass (1s) | pass (51s) | pass (1s) | pass (4s) | 69s | pass, baseline pair with run 3 |
| 5 | 2026-11-03 22:21Z | pass (14s) | pass | pass (57s) | pass | pass (4s) | 75s | pass, after the approval-actor fix |

Runs 3 and 4 are the baseline pair. Run 5 confirms the same steps after the second fix. The timings are not a
load measurement, they are a record that the walkthrough is short enough to do often.

What the checks actually saw:

- B1 answered all five questions with citations. Mileage 0.67 USD per mile, receipt threshold 25 USD, accounts
  payable invoices retained 7 years, vendor insurance 1,000,000 per occurrence and 2,000,000 aggregate, and a
  cross-document answer citing both the travel policy and the retention schedule.
- B2 produced job `ed828c92` at the gate with the pending summary "Post USD 4,250.00 to accounts payable for
  Acme" and a digest.
- B3/B4/B7 produced a research brief with citations, a workspace job with `brief.md`, `note.txt` and
  `example_title.txt` artifacts, an approved email send to the allowed recipient, and a staff boundary denial
  with a disclosure.
- B5 verified the chain intact; the B6 run in run 5 verified against 500+ events and no broken links.
- B6 started a governed job from the invoice webhook and confirmed a person could open the resulting gate.

## Re-runs after the customer path changes, 2026-11-03

The API provider session exchange, the UI provider login and the edge environment changes were deployed to the
product box while the public edge stayed closed. Two more Phase B runs checked that the operator path was
unaffected. Both passed every step: run 6 in 74 seconds, run 7 in 72 seconds. The invoice gates left by the
runs were denied with the reason "phase B re-run cleanup", leaving zero pending. The evidence files are
`evidence/run6-summary.txt` and `evidence/run7-summary.txt`.

The five smoke probes also passed after the change. No public port is open: from the operator machine, only 22
answers; 80, 443, 8443, 8080, 8088 and 5678 are closed. The staging edge containers are stopped.

## Findings and fixes

| Finding | Fix | Verification |
|---|---|---|
| F1: a job started by an automation principal appeared in the Review queue and answered 404 when a person opened or decided it. No person could approve an n8n-created gate. | PR #342: `_find_job_for_review` uses the queue's roots rule for gates; history stays subject-scoped. | Three HTTP tests; live reviewer GET 200 and deny 200 on an automation gate. |
| F2: when a person decided an automation-created gate, the chain row named the automation principal, while the payload named the person. Activity reads the row actor. | PR #343: the approval row is minted for the deciding session. | Durable test added; live decision row actor is `probe-staff@officeinference.com` for a job whose subject is `automation+invoice_trigger@...`. |
| F3: the B1 probe treated two alternatives as two required groups, so a correct answer failed the check. | Probe fixed; the check now requires either alternative. | Run 3, 4 and 5 B1 pass. |
| F4: inactive duplicate workflows from the n8n build remain in the instance. | Not fixed; they are inactive and do not affect the checks. Tidy-up recorded. | The B6 probe reports the active templates and ignores the inactive ones. |

Two smaller items: the Phase B runs left invoice gates in Review by design, and they were denied after each run
with the reason "phase B baseline cleanup", leaving zero pending. The acceptance runs sent synthetic email to
the verified recipient on the deployment; the sends are in the chain with the `send_email` tool.

## What this baseline does not cover

- The customer browser path. Phase D puts a real login and ingress on the deployment; until then every check
  above uses the operator path and is labelled admin-only.
- A restore drill. That is Phase C and it comes next.
- A fresh deployment from the scripts. The scripts exist and the probes run, but the prepare/start/provision
  sequence has not been replayed from scratch.
- Visual or accessibility checks in the app. B3 and B4 drive the API, not the browser.

## Evidence

`evidence/` holds the raw outputs: run summaries, B1 questions and answers, B2 invoice, the acceptance log,
the chain verification, the n8n reviewer check, the two fix verifications and the gate cleanup. Secrets are
not present in these files.
