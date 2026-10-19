# Lab charter: a second implementation, built by agents

Purpose: run a second, completely separate deployment of the stack and let an agent build the remaining product
on it, from the same baseline, with its own architectural choices. The result is not a second production system.
It is a comparison: what another implementation looks like, where it is better, and what it does that we should
not do. The working deployment remains the reference.

This charter is the constraint set. Read it before touching the lab instance. Read
`docs/handoff-answer-quality-2026-10-19.md` for the state of the reference stack, and `BOOTSTRAP.md` for getting
the lab to the baseline.

## What is fixed and what is free

**Fixed. The lab agent may not change these.**

- **The data boundary.** No real client data on the lab instance. The corpus is the same synthetic corpus the
  reference deployment uses, restored from the same package. Where the reference deployment refuses a data class
  or a lane, the lab must refuse the same thing. No new external egress without the owner's approval.
- **The acceptance instrument.** The 52-run answer suite (`tests/fixtures/data_suite_run.py`, run with
  `scripts/run_answer_suite.sh`) and the four acceptance probes. They must keep working. A change that improves
  the product but breaks the instrument is not a result.
- **The baseline floor.** The lab starts from the current commit with the current measured configuration. It may
  replace components, but the baseline suite must not regress beyond run-to-run variance, and every replacement
  is a recorded decision with evidence, not a refactor by preference.
- **Security and audit.** Sessions, access roots, the audit chain and the gateway budget must remain real
  mechanisms, not stubs. Tool calls, model calls and refusals stay recorded.
- **Isolation.** A separate EC2 instance, a separate deployment id, separate volumes, no traffic from the working
  deployment, no shared database. The working box and its resources are not touched.
- **Budget and time.** The cap is stated at the start of each section and reported against weekly. The lab stops
  when the cap is reached, not when the work feels finished.
- **Repository discipline.** Feature branches, pull requests, tests, evidence committed beside the measurement.
  No direct commits to `main`.

**Free. The lab agent decides these.**

- The architecture of everything that does not exist yet: agent runtime and tool orchestration beyond the current
  loop, MCP gateway, provider abstraction, data path for data sets, workflow and connector surface, interface,
  deployment and operations tooling, evaluation tooling.
- Whether to reuse, extend or replace a reference component, as long as the fixed constraints hold.
- The order of the work, proposed in the plan and agreed at the first gate.
- The internal design of each section, and its implementation choices.

If the agent believes a fixed constraint should change, it writes the argument and asks. It does not change it.

## Structure: plan first, then sections

**Section 0: the plan.** Before building, the agent reads the repository and the reference documents and writes a
plan: the sections it proposes, their order, the scope of each, the definition of done for each, the risks, the
budget per section, and what it expects to learn or improve. The owner accepts or adjusts the plan. No section
starts before the plan is accepted.

**Sections 1 to N.** One section at a time. A section is a coherent capability, not a folder of code: for example
"a workflow engine with one connector", "a provider layer with one non-Bedrock provider", "a data path for data
sets". Each section has a definition of done from `SECTION-TEMPLATE.md`, and ends with a gate report.

**The gate.** At the end of a section the agent stops and reports:

- what was built and what a person can now do that they could not before;
- the evidence: tests, the five-run measurement if the answer path changed, probe results, and the raw files;
- the decisions taken and the alternatives rejected, in decision records;
- what it would do next and what it needs;
- the budget spent so far.

The owner accepts the gate or sends it back. The next section does not start until the gate is accepted. A
section that is running out of budget mid-way is reported as blocked rather than silently continued.

## The rubric

At each gate, and for the final comparison, the lab is judged on:

| Dimension | How it is judged |
|---|---|
| Quality | The 52-run suite mean over five runs, plus a widened evaluation set |
| Reliability | Completed answers against degraded answers; provider failure handling |
| Speed | p95 latency per answer, and time to first token on the stream |
| Cost | Model spend per answer and per suite run, from the gateway record |
| Honesty | Refusal correctness on questions the corpus cannot answer; no fabricated figures |
| Engineering | Tests, no dead code, no stubs behind flags, documentation that lets another person run it |
| Evidence | Every claim measured on the same instrument, with raw files committed |
| Spending | Budget against result, reported per section |
| Independence | Decisions that differ from the reference, with the argument for each |

The comparison is reported as both numbers and prose. A lab that scores lower but produces a better-reasoned
architecture is a useful result; a lab that scores higher because it changed the instrument is not.

## What "stop" means

The lab stops, and reports, when any of these happens:

- the timebox or the budget cap is reached;
- three consecutive sections fail their gate;
- a fixed constraint cannot be met without the owner changing it;
- the owner says stop.

On stop, the agent writes a final report: the complete section list and state, the rubric table, the decision
record index, the divergences from the reference and what it recommends the reference stack adopt or avoid, and
anything that was left incomplete. The instance stays up only until the owner says to stop it.

## Practical rules for the lab

- Deployment id: `lab-1` (or a suffix the owner chooses), used in the manifest, the audit rows and the key
  aliases. It must not match the reference deployment.
- Tag every AWS resource `purpose=lab`, `owner=anthony`, so cost is visible and cleanup is unambiguous.
- No alerts, no traffic, no client onboarding. The lab is not a product deployment.
- If the lab's agent changes a reference component's contract, the change goes in the lab's own branch; it does
  not flow back to the reference until the owner adopts it through the normal repository process.
