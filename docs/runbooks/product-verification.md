# Product verification runbook

This is how the product deployment is checked. It is a process a person walks through, step by step, recording
what they saw. There is deliberately no single command that prints "ready". The probes in
`oi-canonical-stack/scripts/verify/` are aids to individual steps. They do not replace the steps, and a green
probe does not finish the walkthrough.

Use it in four situations:

| Situation | What to run |
|---|---|
| Quick check after a restart or config change | Phase V, steps V1 to V3, then record |
| Regression check after code changes | Whole Phase V, then Phase B once |
| Before an upgrade or a client demo | Phase V, then Phase B twice, then Phase C |
| Before daily business use | Everything, including Phase D and Phase E |

## Mode labels

Every step carries one label. Read it before you start.

- **CUSTOMER PATH** means the step uses what a customer uses: a browser login with a real identity, the app
  pages, or a normal email. No box access.
- **ADMIN ONLY** means box access, a minted development session, a database query, or a secret file. These
  are operator tools. They are never presented to a customer and are replaced by the customer path where one
  exists.
- **MIXED** means the customer action is checked with an operator query underneath.

Today the product box has no customer ingress. Until C7 is deployed on it, sign-in is an admin-only tunnel and
a minted development session. That is a temporary operator path, labelled as such in every step, and Phase D
is the work that replaces it.

## Before a run

1. Record the run at the top of this file: date and time, deployment id, git commit of the product tree,
   rendered image pins, who ran it, and why.
2. Create an evidence directory on the box, for example `/opt/oi-product/eval-out/verification-<timestamp>/`.
   Copy it off the box at the end of the run. A run with no evidence is not a run.
3. Confirm the C9 demo box is not part of this deployment and is not touched.
4. Read the last run's record first. A failure that repeats is a different problem from a failure that appeared.

## Phase V: verification walkthrough

Work down the list. Run the probe for the step only where one is named. Write PASS or FAIL and one sentence of
what you actually saw. If a step fails, stop; fix it or record it as an open finding before continuing.

| Step | Mode | Do this | Expect | Evidence |
|---|---|---|---|---|
| V0 | ADMIN ONLY | Record the run header and confirm the deployment id from `/health` | deployment id matches the one in the deployment runbook | run header |
| V1 | ADMIN ONLY | Run `scripts/verify/step-01-services.sh` | all containers running; healthchecks healthy; API, app, n8n answer | probe output |
| V2 | ADMIN ONLY today, CUSTOMER PATH after C7 | Today: `ssh -L 8080:127.0.0.1:8080` from the operator machine and open `http://127.0.0.1:8080`. After C7: open the customer URL and sign in with a real account | the app loads; after C7 the browser shows a login and a signed-in identity | screenshot, probe log |
| V3 | ADMIN ONLY today, CUSTOMER PATH after C7 | Run `scripts/verify/step-02-answer.sh`. After C7, ask the same question in the app | answer states the mileage rate as 0.67 USD per mile and shows at least one citation | probe output, screenshot |
| V4 | CUSTOMER PATH | Open Do and start an agent task: a short research brief that asks for the mileage rule with citations | the plan appears, steps run, the result keeps its citations | job id and screenshot |
| V5 | CUSTOMER PATH | In Review, open the pending item from V4, read the action and its digest, approve it | the item leaves Review, the job finishes, the activity row records the approval | job id, screenshot |
| V6 | MIXED | In Activity, find the V3 to V5 events for the user. Then run `scripts/verify/step-03-audit.sh` | the customer sees their own actions; the chain exports and verifies, count above zero | Activity screenshot, probe output |
| V7 | MIXED | Ask for a task that writes a file in the workspace, then download the artifact from the job. Run `scripts/verify/step-05-workspace.sh` | the artifact downloads and opens; the workspace health check answers 200 | artifact and probe output |
| V8 | MIXED | Run `scripts/verify/step-04-n8n.sh`. In the n8n UI (admin tunnel) confirm both templates are active | invoice webhook starts a governed job at the gate; scheduled report is active | probe output, job id |
| V9 | CUSTOMER PATH | Approve an email send to the allowed recipient and check delivery | the mail arrives; the activity row records the send; no other recipient is possible | sent mail, job id |

After V9, write the run result: PASS, PASS with findings, or FAIL. Findings go into the review record, not into
this runbook.

## Phase B: full acceptance, run twice

Phase V proves the deployment works. Phase B is the longer regression pass used before an upgrade or a demo.
Run Phase V first, then this list, twice, and compare. The second run is the baseline. Do not change code
between the two runs unless the first run found a failure.

| Step | Mode | Do this | Expect |
|---|---|---|---|
| B1 | CUSTOMER PATH after C7, ADMIN ONLY today | Run `scripts/verify/step-b1-questions.sh` | five answers, citations on each, the cross-document question cites both documents |
| B2 | CUSTOMER PATH after C7, ADMIN ONLY today | Run `scripts/verify/step-b2-invoice.sh` | job stops at the gate; the drafted posting matches the invoice |
| B3 | CUSTOMER PATH after C7, ADMIN ONLY today | Run `product-acceptance.py` on the box and read its research brief section | job succeeds after approval; email arrives |
| B4 | CUSTOMER PATH after C7, ADMIN ONLY today | Same script, workspace section | artifacts exist and download; steps are visible |
| B5 | ADMIN ONLY | Run `scripts/verify/step-03-audit.sh` | chain intact; count is higher than the previous run |
| B6 | ADMIN ONLY | Run `scripts/verify/step-04-n8n.sh` | both templates active; webhook job at the gate; a person can open that gate |
| B7 | ADMIN ONLY | The staff boundary denial inside `product-acceptance.py` | denied with a disclosure, not a silent answer |
| B8 | ADMIN ONLY | Save the evidence directory and the run header off the box | evidence archived before the box is stopped or upgraded |

The first baseline is `docs/reviews/product-verification-20261103/README.md`: runs 3 and 4 passed every step,
run 5 confirmed after the second fix, and the two product defects it found are fixed with tests. Compare later
runs against that record, not against a memory of the last run.

Record both runs side by side with timings. A slower answer or a missing citation between run one and run two is
a finding even when every step says PASS.

## Phase C: restore drill

This proves the backup is real. It is not a smoke test and it is not run on the live deployment in place.

The whole phase is one script, `scripts/restore-drill.sh` in `oi-canonical-stack`. It runs the steps below in
order, records each timing, and deletes every scratch copy at the end. Read the output step by step rather than
trusting the final line.

| Step | Mode | Do this | Expect |
|---|---|---|---|
| C1 | ADMIN ONLY | Fresh backup and checksum verification | dumps for `oicore`, `litellm`, `n8n`; `rendered.tar.gz`; `documents.tar.gz`; `data/n8n`; checksums pass |
| C2 | ADMIN ONLY | Restore the three dumps into scratch databases | restore finishes without errors |
| C3 | ADMIN ONLY | Compare row counts in eleven tables between live and restored | all counts equal |
| C4 | ADMIN ONLY | Start a scratch API against the restored database, mint a session, ask the known question | cited answer, same facts as live |
| C5 | ADMIN ONLY | Export and verify the audit chain from the restored database | chain intact, event count matches |
| C6 | ADMIN ONLY | Restore `data/n8n` and `documents` to scratch paths; run the n8n CLI against the restored database; compare the documents file count and content hash | both templates present; config mode 600; documents identical |
| C7 | ADMIN ONLY | Record the measured restore duration | numbers recorded in the review record |
| C8 | ADMIN ONLY | Cleanup | no scratch databases or containers; production healthy |

The 2026-11-03 drill passed all twelve checks in 27 seconds. Record:
`docs/reviews/restore-drill-20261103/README.md`. A second run restored from the copy in S3, not the local
directory: eleven checks in 21 seconds, counts compared against the backup manifest. Qdrant is still rebuilt
from documents rather than restored, and the rebuild time is not measured.

Backups run daily at 03:00 Eastern through `oi-product-backup.timer` and are copied to
`s3://oi-core-backups-878451098165/backups/product-oi-core-product-20261103/`. Local retention is seven days,
remote retention is thirty. The manifest carries the row counts so an off-box restore can be checked. See
`docs/reviews/product-backup-20261103/README.md`.

## Phase D: customer access path

Phase D is the C7 work on this product deployment. A deployment is not ready for daily customer use until
these steps pass, because a tunnel and a minted session are operator tools.

| Step | Mode | Do this | Expect |
|---|---|---|---|
| D1 | ADMIN ONLY | Deploy the C7 ingress: TLS certificate, reverse proxy, and the identity provider for the customer domain | public HTTPS endpoint resolves; HTTP redirects to HTTPS |
| D2 | CUSTOMER PATH | Open the customer URL in a fresh browser and sign in | login page, successful sign-in, visible identity |
| D3 | CUSTOMER PATH | Sign out, then sign in again | session ends cleanly; no stale access |
| D4 | CUSTOMER PATH | Try a wrong password and then a second account with a lower role | both refused correctly; no account enumeration in the response |
| D5 | CUSTOMER PATH | With the lower-role account, ask for executive content | denied with the disclosure; the customer sees the refusal |
| D6 | ADMIN ONLY | Confirm the customer cannot reach operator routes, the database, or the tunnel | ports closed from the internet; only 443 open |
| D7 | ADMIN ONLY | Confirm the activity record shows the provider identity, not a minted session | audit rows name the real subject; source is not `development` |
| D8 | MIXED | Run Phase V again end to end using only the customer path | every customer step passes without a tunnel |

After D8, the admin-only shortcuts in this runbook stay for support and diagnostics. They are not the product.

Phase D was accepted on 2026-11-03 on `https://app.officeinference.com`: DNS, real TLS, Cognito login, provider session, browser walkthrough, wrong-password refusal, staff boundary and closed operator ports. Record:
`docs/reviews/customer-edge-live-20261103/README.md`. The operator path in the steps above stays admin-only.

## Phase E: company onboarding, after Phase D

| Step | Mode | Do this | Expect |
|---|---|---|---|
| E1 | CUSTOMER PATH | Create the real accounts and roles for the company | each person can sign in; roles match the access matrix |
| E2 | ADMIN ONLY | Ingest the company's documents into the deployment's corpus | document and chunk counts match the input set |
| E3 | CUSTOMER PATH | Ask three questions whose answers are in the company documents | correct answers with citations to the right documents |
| E4 | CUSTOMER PATH | Set the email sending identity and the allowed recipients | a test send arrives; other recipients are refused |
| E5 | ADMIN ONLY | Schedule the backup and confirm it runs | backup appears at the expected time; checksums verify |
| E6 | ADMIN ONLY | Record the support path: who is called, what is logged, where evidence goes | the incident runbook is filled in for this deployment |
| E7 | MIXED | Run Phase V again on the real deployment with the real accounts | PASS with the company's own data |

After E7 the deployment can be used daily. Keep the cadence: Phase V after restarts, Phase B weekly, Phase C
after changes to storage or the database.

## Run record

Copy this table into the run record for every walkthrough. One row per step. Evidence file next to it.

| Step | Mode | Result | What was seen | Evidence |
|---|---|---|---|---|
| | | | | |
