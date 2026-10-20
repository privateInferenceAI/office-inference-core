# Finish prompt: the build task for the lab agent

Paste the message below to the lead agent on the lab instance **after** `BOOTSTRAP.md` verification passes and the
reference search work is merged and measured. It is the build instruction; the setup instruction is
`KICKOFF-PROMPT.md`. The scope it refers to is `FINISH-SCOPE.md`.

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
- **Quality** — 60 golden questions per implemented vertical with hard-fail categories blocking release, an
  acceptance test on every new deployment, and a load test with ingestion running.
- **Offerings and packs** — manifests for Standard Cloud, Compliant Cloud, Standard Cloud Hosted and In-Office;
  agent definitions from packs; in-office/hybrid implemented at configuration and code-path level, hardware
  verification out of scope on this CPU-only instance.
- **The final comparison report** per `FINISH-SCOPE.md`: same instrument as the reference, five runs, same-day
  control, divergence list, latency, cost, and adopt/avoid recommendations.

Owner-owned items (`BIZ-*`) are not yours to decide. Supply engineering evidence and drafts where useful, and list
the dependency at the gate.

## How to run the work

**Section 0 first: the plan, and wait for approval.** Map every open requirement to a section with: definition of
done, evidence plan, dependencies, risks, budget, and the gate date. Use the grouping in `FINISH-SCOPE.md` as the
starting point but propose your own if the dependencies differ. Ask the owner to approve the plan. Do not build
before it is accepted.

**Then one section at a time.** For each section: write the brief, build it, test it, measure it, write the gate
report, update the register rows with evidence paths, update the docs and the lab handoff, and stop at the gate
until the owner accepts. A section that fails its gate is redesigned with the reason recorded, not silently
averaged away. Never start the next section while the previous gate is open.

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
  (for example runtime, MCP gateway, approvals, client app, cloud deployment, security, operations, quality). Give
  each a task on the shared board with explicit write scopes and dependencies. One owner per file; no two agents
  write the same file. Keep two to five active at once; more is churn. Review their PRs yourself; a teammate's merge
  is not acceptance.
- **Sub-agents.** Use `subagent` for scoped research and reading (the repo is large, and AWS documents are long) and
  `subagent_fork` for follow-ups that need your context. They protect your own context window. Delegate the reading,
  keep the decisions.
- **Workflow tool.** Use it for fan-out audits and sweeps: a requirement-to-code coverage audit over the register, a
  security review of each service, adversarial review of a section's evidence, generating and validating golden
  questions, checking docs against commands. Use structured outputs; do not use it for one-off edits.
- **Background jobs.** Long suite runs, ingestion, Terraform apply and load tests run in the background; keep
  working, collect with `job_output`, kill jobs that stop mattering. Never sleep-poll.
- **Automation.** Use `schedule_create` for checkpoints on long measurements, budget checks and gate reminders, and
  scripts for anything you will run more than twice. Prefer a committed script over a remembered command.
- **Skills.** Load the matching installed skill before AWS-heavy work: `agent-advisor` for the runtime, `aws-security`,
  `aws-observability`, `aws-iam`, `aws-networking`, `aws-compute`, `aws-containers` for the deployment,
  `tf-best-practices` for Terraform, `llm-to-bedrock` for provider work. Follow the skill rather than working from
  memory.
- **AWS tools.** Read-only discovery freely; create and modify only lab resources (this instance, its volumes, the
  lab S3 prefix, the lab IAM role). Anything else needs the owner's explicit approval. The lab account is
  `878451098165`; never touch the reference deployment's resources in the same account.
- **Search and fetch** for external specifications and provider documentation.
- **The task board and todo list** are the shared state. Every section is a task with a write scope, dependencies and
  the evidence required to complete it. Update them as you go; a fresh teammate should be able to start from the
  board alone.
- **Ask, don't guess, on owner decisions.** Use `ask_user_question` for vertical choice, pricing and compliance
  questions, the budget cap, and account or repo access. Everything else is yours to decide and record.

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
provider layer work end to end; security and operations requirements hold with drills recorded; 60 golden questions
per implemented vertical with hard-fail gates; four offering manifests validate; docs updated; and the comparison
report accepted by the owner.

If any of that cannot be reached, say which and why at the gate. Do not quietly narrow the definition.

## Your first response

Report: the baseline verification results, the plan (sections, requirements mapped, gates, budget), the verticals
you propose for the golden questions, and the owner decisions you need. Then wait for approval. Do not start
building before it.
