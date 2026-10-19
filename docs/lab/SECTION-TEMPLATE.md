# Section template: definition of done, evidence, decision record

Use this for every section in the lab. A section is not finished when the code runs; it is finished when the gate
below is satisfied and the owner accepts it.

## 1. Before the section: the section brief

Write and commit a short brief before building. It answers:

- **What capability does this section add?** One paragraph in the language a client would use, not a technology
  list.
- **Definition of done.** The observable behaviour that proves it works. Include a demo a person can run.
- **Out of scope.** What this section deliberately does not do, so the boundary is a decision rather than an
  omission.
- **Risks.** The two or three ways this could be wrong, and how you will notice.
- **Budget.** The maximum model spend and wall-clock time for this section.
- **Measurement plan.** What the acceptance instrument will show, and which additional checks are needed.

## 2. Definition of done

A section is done only when all of these are true.

- [ ] The capability works end to end through the real service, not through a unit test or a stub.
- [ ] The demo in the brief runs as written, from a clean checkout, on the lab instance.
- [ ] New behaviour has tests. Existing tests pass. The suite is green.
- [ ] Access control applies to the new surface: every read of client documents is scoped by the session permit.
- [ ] Every new model call, tool call and refusal is recorded in the audit chain.
- [ ] The baseline 52-run suite has not regressed beyond run-to-run variance. If the section touches the answer
      path, it carries a five-run measurement with a same-day control, raw files committed.
- [ ] New configuration is in the manifest or environment with a documented default and a rollback.
- [ ] A person who has not seen this code can run it from the documentation alone.
- [ ] No stub, no dead flag, no "temporary" path that shipped.

## 3. Evidence to commit beside the section

Under `docs/reviews/lab-<section>/`:

- `README.md`: what was built, why, how it was verified, what it cost, what surprised you.
- `raw/`: the raw result files from any measurement (suite runs, probes, benchmark output), unedited.
- The section brief and the gate report.
- Decision records (below), one file per decision.
- Repro commands: the exact commands a reader runs to reproduce the evidence.

A measurement without its raw files is not evidence.

## 4. Decision record format

One file per decision that matters, `docs/decisions/lab/NNNN-<slug>.md`:

```
# NNNN: <the decision in one line>

- Status: proposed | accepted | superseded by NNNN
- Section: <section number>
- Date: <YYYY-MM-DD>

## Context
What forced the decision. The constraint, the measured problem, the two options that were live.

## Options considered
1. <option> — cost, benefit, risk
2. <option> — cost, benefit, risk

## Decision
What was chosen, in one paragraph.

## Consequences
What becomes easier. What becomes harder. What is now harder to change later.

## Evidence
The measurement, test or experiment that supports it. Raw files if any.

## Divergence from the reference stack
If this differs from the reference implementation, say so and say why. If it does not, say "none".
```

Decisions to record include: choosing a framework, choosing a data model, changing a contract, adding a
dependency that constrains future work, replacing a reference component, and accepting a known limitation.

## 5. Gate report format

At the end of the section, stop and report:

```
# Gate report: section N — <name>

## What a person can now do
## Evidence
  - suite: <five-run totals and mean, control>, raw files <path>
  - probes: <which passed>
  - new tests: <count, coverage of the new surface>
  - demo: <command that proves it>
## Decisions
  - <record ids>
## Cost
  - model spend: <from the gateway record>
  - wall clock: <hours>
## Risks and known limitations
## What is next
## What the owner needs to decide or unblock
```

The owner replies accept or send back. A send-back says which item failed; the section stays open until the gate
is accepted.

## 6. Stop rules

- On budget or timebox, stop and write the gate report as "blocked on budget", with everything that works.
- After three failed gates on the same section, stop and ask the owner whether to cut the scope or abandon it.
- If the measurement apparatus itself is broken, fix it in its own small section before measuring anything else;
  a measurement of a broken instrument is worse than no measurement.
