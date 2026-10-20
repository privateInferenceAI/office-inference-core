# The lab: a second implementation, built in the open

This directory is the setup for a parallel implementation of the stack: a second instance, the same starting line,
and an agent free to build the remaining product its own way. It exists so the working deployment can learn from a
different set of decisions, including the ones that turn out badly.

It is not a second product deployment. It has no clients, no traffic, no backup and no promises.

## The documents

| File | What it is | Who owns it |
|---|---|---|
| `CHARTER.md` | What is fixed (data boundary, acceptance instrument, budget, security) and what the lab agent decides; the plan-then-sections structure; the rubric; the stop rules | Owner writes, agent obeys |
| `SECTION-TEMPLATE.md` | Definition of done, the evidence required, the decision-record format, the gate report, the stop rules | Agent uses per section |
| `BOOTSTRAP.md` | Getting a fresh instance to the reference starting line, including the corpus package and the exact verification counts | Agent executes once |
| `KICKOFF-PROMPT.md` | The paste-ready prompt that starts the bootstrap agent | Owner pastes to the agent |
| `FINISH-SCOPE.md` | What "finished" means: the remaining requirements, the suggested sections, the definition of done, and what is owner-owned or out of scope | Owner and agent share |
| `FINISH-PROMPT.md` | The paste-ready build prompt: mission, scope, method, and how to use the harness's teams, sub-agents, workflows, background jobs, schedules, skills and AWS tools | Owner pastes when the search work is done |

Supporting references: `docs/handoff-answer-quality-2026-10-19.md` (state of the reference stack),
`docs/runbook.md` sections 6.7 and 19 (operator procedure), and `docs/reviews/` (every measurement the reference
made, so the lab can check its own claims against a known instrument).

## The starting line

- Repository pinned at the commit in the kick-off prompt, at least `a886050`.
- Corpus package: `s3://officeinference-corpus-878451098165/lab-baseline/lab-corpus-2026-10-19.tar.gz`
  (50,652,986 bytes, sha256 `cc36924a730eb566bf5cd006d480965fc07af413d9710d37a7a4f03cbcb0c90d`), the same 545
  files the reference deployment has, including the redaction canary.
- After ingestion: 539 indexed documents, 100,478 chunks, 6 data-set cards, 6 dead letters.
- Answer path: Claude Haiku 4.5 through Bedrock (`us.` inference profile), tool loop on, row query on, lookup
  off, honesty rule on, six steps, ten calls. Same as the reference's measured configuration.
- Baseline instance: one `m7i.4xlarge` in account `878451098165`, Ubuntu 24.04, 200 GB gp3.

## How the work is judged

The same 52-run answer suite, the same four acceptance probes, five runs per configuration with a same-day
control, plus a widened evaluation set the lab adds. The rubric is in `CHARTER.md`. The comparison is reported as
numbers and prose, and a lab that scores slightly lower but shows better-reasoned architecture is a useful result.

## Housekeeping

- Separate deployment id, database, vector store and audit chain. Nothing shared with the working deployment.
- Tag the instance `purpose=lab`, `owner=anthony`.
- No real client data, no S3 evidence shipping, no alerts, no traffic.
- Stop the instance when nobody is working. Take the baseline snapshots before any teardown.
