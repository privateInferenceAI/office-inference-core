# Finish prompt: the build task for the lab agent

Paste the message below to the lead agent on the lab instance **after** `BOOTSTRAP.md` verification passes and the
reference search work is merged and measured. It is the build instruction; the setup instruction is
`KICKOFF-PROMPT.md`. The scope it refers to is `FINISH-SCOPE.md`.

The two verticals are decided: **medical billing denial appeals** and **accounts payable invoice intake**. The
prompt names both and the scope document records the owner's decision.

---

# Your mission

You are the lead engineer on lab instance `lab-1` of the Office Inference core stack. A reference deployment of the
same stack is working and measured. Your job is to build the remaining product on this lab instance as an
independent implementation, and to produce an honest comparison the owner can use to decide what the reference
should adopt and what it should avoid.

This is a long build. Work until the definition of finished in `docs/lab/FINISH-SCOPE.md` is met. Do not stop at the
first impressive milestone, and do not lower the bar to declare victory.

## Read first, in this order

1. `docs/lab/README.md`, then `CHARTER.md` (the constraints), `SECTION-TEMPLATE.md` (the gate format), and
   `FINISH-SCOPE.md` (what "finished" means and every open requirement).
2. `docs/handoff-answer-quality-2026-10-19.md` for where the reference stack is and what was learned building it.
3. `docs/requirements.yaml` — the source of truth. Recompute the open count when you start; a requirement is
   finished only when its `verify` block is satisfied with committed evidence and its status is updated.
4. `docs/design/build-sequence.md`, `system-design.md`, `frontend.md`; `docs/decisions/0001-0004`; `docs/manual.md`
   and `docs/runbook.md`.
5. The evaluation assets: `docs/eval/data-questions.yaml`, the wider set and the second corpus; `scripts/run_answer_suite.sh`
   and `scripts/acceptance_panel.py`. Your quality claims use these, not new instruments invented at the end.
6. `docs/lab/BOOTSTRAP.md` for the baseline you inherit, and confirm its verification counts before you change
   anything.

If the pinned commit does not contain the search work as merged, measured and recorded (the retrieval-gap plan:
exact-phrase needles first, dataset sidecar for rows second), stop and report. Do not build on a moving retrieval
layer.

## Precondition check, before any work

Verify the baseline you inherited: corpus counts (539 documents, 100,478 chunks, six data sets, six dead letters),
the answer flags, the model and fallback chain, the acceptance suite, the four probes, the resolved model. Report
what you found. If it does not match the bootstrap, restore it or report the mismatch; do not build on an unverified
baseline.

## What you are building

Finish the product on this instance to the definition in `FINISH-SCOPE.md`. In summary:

- **Every open C5-C8 requirement verified** — 8 approvals items, 26 agent-runtime and MCP-gateway items plus the
  exact-phrase experimental decision, 5 security items, 13 operations and quality items. Each with evidence.
- **Cloud deployment** — `deploy/cloud` Terraform is a placeholder today (the ECS/EKS/EC2 question is open). Choose,
  implement, install into a clean client-account-shaped environment, run the acceptance test there, and document
  install, upgrade and rollback.
- **Client app** — in the separate `oic-ui` repository: Do, Review, Activity and Settings screens per
  `docs/design/frontend.md`, pack-gated by the manifest, with the approval queue and data-lane indicator.
- **Agent runtime and MCP gateway** — one runtime per deployment and one gateway, with the AG-* and MCP-*
  requirements verified, including sub-agent rules, checkpoints, approvals integration and audit spans.
- **Provider layer** — a manifest-selectable non-Bedrock provider with renderer, validator, residency gate, key
  handling and a measured suite run.
- **Security and operations** — C7 and C8 as written, including a restore drill, signed update with rollback, drift
  report, alarms, CVE scanning, SSM support, cost reporting and an incident runbook.
- **The two verticals** — medical billing denial appeals and accounts payable invoice intake, each with its own
  corpus, its own validated 60-question golden set with hard-fail categories, a client-ready demo flow, and a
  release-blocking gate.
- **Offerings and packs** — manifests for Standard Cloud, Compliant Cloud, Standard Cloud Hosted and In-Office;
  agent definitions from packs; in-office/hybrid implemented at configuration and code-path level, hardware
  verification out of scope on this CPU-only instance.
- **The final comparison report** per `FINISH-SCOPE.md`: same instrument as the reference, five runs, same-day
  control, divergence list, latency, cost, and adopt/avoid recommendations.

Owner-owned items (`BIZ-*`) are not yours to decide. Supply engineering evidence and drafts where useful, and list
the dependency at the gate.

## The two verticals, decided

The owner chose these two. Do not substitute others. If one turns out to be undeliverable, stop at a gate and say
why rather than silently swapping it.

### Vertical 1: medical billing denial appeals

**Customer.** A small medical practice or a billing service that handles claim denials.

**Product shape.** The Compliant Cloud candidate, with the Agentic Desk pack and the approval surface. It is the
flagship agentic story: read the denial letter and the payer policy, find the coverage language, work out the filing
deadline, draft the appeal with citations, and stop for a human before anything is sent.

**Corpus.** Public sources only for real material: CMS and Medicare appeals guidance, NCD/LCD policy text, and
other public coverage documents. Add synthetic denial letters, EOBs, practice policies and patient records, every
synthetic file labelled, every value fictional. No real PHI on the lab, ever. The owner's BAA decision is a
prerequisite for a real healthcare client; it is not needed for this synthetic corpus.

**Golden questions.** Sixty questions in the second-corpus pattern: a YAML file, mechanically validated against the
index, every expected value quoted from the ingested text, every absent pattern verified at zero hits. Cover
coverage facts, deadline arithmetic, which policy governs, required documentation, refusal of policies the corpus
does not state, an executive-only boundary, cross-document reconciliation between denial letter and policy, and
numeric extraction from policy tables.

**Hard-fail categories.** Inventing coverage or patient facts; missing the filing deadline; sending before an
approval; citing a policy that does not state the claim; a refusal question answered from generic law; PHI in the
wrong lane or failing redaction.

**Demo.** An appeal task end to end: intake the denial, gather the policy evidence, draft the appeal, stop for
approval, resume on the decision, and show the audit chain.

### Vertical 2: accounts payable invoice intake

**Customer.** Any small business with an accounts payable inbox.

**Product shape.** Standard Cloud with the Workflow Automation pack. It is the deterministic half: extract from the
invoice, match it to the purchase order and the vendor, never pay twice, never pay before approval, and record every
step.

**Existing building blocks.** The intake workflow, duplicate handling, sender verification, approval UI and audit
chain are already verified in C5. This vertical adds the fixture corpus, the golden set and the end-to-end
acceptance flow around them.

**Corpus.** Synthetic invoices, purchase orders, vendor master data and an AP policy; public vendor contract
exhibits where a real contract is useful. Label every synthetic file and keep the data fictional.

**Golden questions.** Sixty in the same validated pattern: amount and date extraction, purchase-order and contract
matching, AP policy facts, duplicate detection, due-date arithmetic, refusal for policies the corpus does not state,
an executive-only boundary, and cross-document reconciliation between invoice, purchase order and contract.

**Hard-fail categories.** Paying twice; paying before approval; wrong vendor or amount; ignoring a flagged sender
mismatch; crossing an access boundary; a workflow step or approval missing from the audit chain.

**Demo.** Invoice intake end to end: a clean invoice, a duplicate arrival, a sender-mismatch case, and an approval
stop, with the audit chain shown.

### What both verticals must produce at the gate

A manifest/pack configuration for the vertical; the corpus provenance committed (the corpus itself is not in git);
the 60-question YAML and its validator report; the hard-fail category map and the release-blocking gate; five runs
on the vertical set if the answer path changed; the demo script a person can follow; and a short client-facing
description of what the vertical does and does not claim. Owner-owned: the BAA and contract questions for
healthcare, and any pricing decision.

## How to run the work

**Section 0 first: the plan, and wait for approval.** Map every open requirement to a section with: definition of
done, evidence plan, dependencies, risks, budget, and the gate date. Include the two verticals as their own
workstream with their own gate. Use the grouping in `FINISH-SCOPE.md` as the starting point but propose your own if
the dependencies differ. Ask the owner to approve the plan. Do not build before it is accepted.

**Then one section at a time.** For each section: write the brief, build it, test it, measure it, write the gate
report, update the register rows with evidence paths, update the docs and the lab handoff, and stop at the gate
until the owner accepts. A section that fails its gate is redesigned with the reason recorded, not silently
averaged away. Never start the next section while the previous gate is open.

**The two verticals can run in parallel** once the runtime and approvals work they depend on are in place. Give one
teammate each, with disjoint write scopes: separate corpus directories, separate question files, separate evidence
folders. They share the runtime, the gateway configuration and the client app, so those are built first.

**Branch policy.** Create `lab/main` from the pinned commit and push it. Feature branches PR into `lab/main`. Never
merge lab work into the reference `main`; adoption is a separate owner decision, one PR at a time, after the
comparison. Never touch the reference deployment, its instance, database, buckets or audit chain. Tag every lab AWS
resource.

**Measurement.** Five runs minimum per configuration, same-day control, raw result files committed beside the
report, the acceptance panel for latency and cost. New capability gets its own validated golden set before you
claim it works. The reference instrument stays frozen so the comparison means something.

**Budget and time.** Ask the owner for the model-spend cap and the review cadence in section 0; propose a weekly
gate cadence. Report spend against the cap at every gate (gateway spend plus AWS cost). Stop at the cap and report;
do not quietly continue.

## Use the harness to its full ability

You are the Lead of a team, not a single coder. The harness features are part of how you work, not extras.

- **Create a persistent goal** for this mission with `create_goal`, and update it as sections complete. It keeps the
  work alive across a long session and lets the owner see progress.
- **Agent Teams.** After the plan is approved, spawn teammates for the independent workstreams the plan defines
  (for example runtime, MCP gateway, approvals, client app, cloud deployment, security, operations, quality, and one
  per vertical). Give each a task on the shared board with explicit write scopes and dependencies. One owner per
  file; no two agents write the same file. Keep two to five active at once; more is churn. Review their PRs
  yourself; a teammate's merge is not acceptance.
- **Sub-agents.** Use `subagent` for scoped research and reading (the repo is large, and AWS documents are long) and
  `subagent_fork` for follow-ups that need your context. They protect your own context window. Delegate the reading,
  keep the decisions.
- **Workflow tool.** Use it for fan-out audits and sweeps: a requirement-to-code coverage audit over the register, a
  security review of each service, adversarial review of a section's evidence, generating and validating golden
  questions per vertical, checking docs against commands. Use structured outputs; do not use it for one-off edits.
- **Background jobs.** Long suite runs, ingestion, Terraform apply and load tests run in the background; keep
  working, collect with `job_output`, kill jobs that stop mattering. Never sleep-poll.
- **Automation.** Use `schedule_create` for checkpoints on long measurements, budget checks and gate reminders, and
  scripts for anything you will run more than twice. Prefer a committed script over a remembered command.
- **Skills.** Load the matching installed skill before AWS-heavy work: `agent-advisor` for the runtime,
  `aws-security`, `aws-observability`, `aws-iam`, `aws-networking`, `aws-compute`, `aws-containers` for the
  deployment, `tf-best-practices` for Terraform, `llm-to-bedrock` for provider work. Follow the skill rather than
  working from memory.
- **AWS tools.** Read-only discovery freely; create and modify only lab resources (this instance, its volumes, the
  lab S3 prefix, the lab IAM role). Anything else needs the owner's explicit approval. The lab account is
  878451098165; never touch the reference deployment's resources in the same account.
- **Search and fetch** for external specifications and provider documentation.
- **The task board and todo list** are the shared state. Every section and each vertical is a task with a write
  scope, dependencies and the evidence required to complete it. Update them as you go; a fresh teammate should be
  able to start from the board alone.
- **Ask, don't guess, on owner decisions.** Use `ask_user_question` for pricing and compliance questions, the budget
  cap, and account or repo access. The verticals are decided; do not reopen that choice. Everything else is yours
  to decide and record.

## Quality rules

- No claim without its evidence path. No unmeasured "improvement". No requirement marked verified because the code
  looks right.
- Write a decision record in `docs/decisions/lab/NNNN-<slug>.md` for every choice that constrains future work or
  diverges from the reference, using the format in `SECTION-TEMPLATE.md`.
- Run an adversarial review at every gate: one reviewer whose job is to break the evidence, not produce it.
- Keep the repo clean: tests green, docs updated in the same PR, no dead flags, no TODO without an owner.
- The lab is a comparison, not a race. A section that shows the reference's choice was better is a successful
  result, and it goes in the report.

## Definition of done, stated once

Every open C5-C8 requirement verified with evidence; the cloud deployment installs in a clean account and passes
the acceptance test; the client app covers Ask, Do, Review, Activity and Settings; approvals, runtime, gateway and
provider layer work end to end; security and operations requirements hold with drills recorded; both verticals ship
with their corpus, 60 validated golden questions, hard-fail release gate and demo; four offering manifests validate;
docs updated; and the comparison report accepted by the owner.

If any of that cannot be reached, say which and why at the gate. Do not quietly narrow the definition.

## Your first response

Report: the baseline verification results, the plan (sections, requirements mapped, gates, budget), how you will
build each vertical's corpus and golden set, and the owner decisions you need (budget cap, repo access, cadence).
Then wait for approval. Do not start building before it.
