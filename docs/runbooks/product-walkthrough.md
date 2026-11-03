# First use walkthrough

This is the guide for the first hour with the product deployment. It is written for the person who will use the
system, not for the operator. Operator checks live in `docs/runbooks/product-verification.md`.

**Today the deployment has no customer login.** Until C7 is deployed on it, the only way in is the operator
tunnel: `ssh -L 8080:127.0.0.1:8080 ...` then open `http://127.0.0.1:8080`. That is an admin-only shortcut, not
the product. A customer signs in at a real URL with a real account after Phase D of the verification runbook.
When something in this guide says "customer path" and the deployment has no login yet, you are using the
operator path on the customer's behalf, and that should be written down as a temporary gap.

## What you are looking at

The application has four working areas:

- **Ask** is a single question with citations from the company documents.
- **Do** is an agent task that plans, uses tools, and can stop for approval.
- **Review** is where a person approves or denies an action before it happens.
- **Activity** is the record of what happened: who asked, what ran, what was approved, what was sent.

The n8n automation layer runs workflows that start governed jobs. It has its own login and is reached through
the tunnel until C7 puts it behind the customer edge.

## 1. Get in

Customer path after C7: open the deployment URL, sign in with your account.

Today, admin only: open the tunnel and the app at `http://127.0.0.1:8080`. Sign-in is a minted session. Note
the time and what you did, because this step is a known gap.

What to check: the page loads, your identity is visible, and you can reach Ask, Do, Review and Activity.

## 2. Ask a question and read the citation

Ask: "What is the mileage reimbursement rate in the travel and expense policy?"

What to check: the answer states 0.67 USD per mile and shows a citation. Click the citation and confirm it
opens the policy document. If the answer is right but the citation is missing, that is a finding, not a pass.

Then ask a question that the documents do not answer. What to check: a refusal or an honest "not in the
documents", not an invented answer.

## 3. Start an agent task

Open Do and start a short research brief: "Write a short brief on the travel and expense policy, with
citations, and send it to me by email after I approve it."

What to check: the plan appears, steps run in order, and the task stops at Review instead of sending on its
own.

## 4. Approve the action

Open Review, read the pending action, check the recipient and the digest, then approve.

What to check: the approval is recorded, the job finishes, and the email arrives at the allowed recipient.
Then look at Activity: the ask, the task, the approval and the send should all be there, in order, with your
identity.

If the recipient looks wrong, deny it. A denial is a successful test of the gate.

## 5. Use the workspace

Start a task that writes a file, does a calculation and saves the result as an artifact, for example:
"Calculate the total of 250 units at 17.00 each, write it to total.txt, and keep it as an artifact."

What to check: the job lists its steps, the artifact appears under the job, and the downloaded file opens with
the expected number.

## 6. Watch the automation layer

The invoice workflow starts when an invoice arrives on its webhook and stops at Review with a drafted posting.
The scheduled report runs every five minutes and writes a cited summary.

What to check: after the scheduled run, the job appears in Activity with its citations. If the invoice webhook
is exercised, the job sits in Review and does nothing until a person approves it.

## 7. Check the record yourself

Open Activity. Pick the last hour and read the rows.

What to check: every action you took is there, the refused questions are there, the approvals name you, and
nothing is missing. This record is the reason the system is usable in a business: if it is wrong, tell the
operator before doing more work in the system.

## 8. When something is wrong

Record four things: the time, the page or job, what you expected, and what happened. Do not retry an approval
or a send blindly, because the record is the audit trail. For a send that looks wrong, deny first and report
after. The incident path for this deployment is in `docs/runbooks/incident-response.md`.

## What a customer never needs

A customer never needs SSH, a session minted by the session CLI, the database, the rendered secrets, or the
n8n tunnel. If a task requires one of those, it is an operator task and it should be marked as such in the
record. The operator verification runbook keeps those steps for diagnostics and support.
