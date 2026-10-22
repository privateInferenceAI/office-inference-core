# 0009. The agent runtime is a dynamic plan on the C5 engine

- Status: the Cloud Workspace target is accepted by the owner, 2026-10-22. The runtime shape is proposed.
- Section: C6 Section 2 / WS
- Date: 2026-10-22
- Supersedes: the `agent-runtime` row of `0001-service-implementations.md`, and only that row

## Context

Section 2 builds the 14 open `AG-*` rows: a loop, checkpoints, two modes, one runtime, a queue, context
control, verification, four sub-agent rules, the approval gate, the C5 seam and definitions from packs.

**The product target is the Cloud Workspace.** The cloud build is a workspace agent: it researches, writes files,
runs code, uses approved web access and client systems, produces artifacts and stops for approval before anything
irreversible. The design is `docs/design/cloud-workspace.md`; the phase is `WS`. The office build is a separate
product with its own boundary, and the office rule that nothing leaves the building is not a rule for this build.
The runtime described here is the execution core of the workspace, not the whole product. Its tool interface must
be able to host the sandbox, web and connector tools; the workspace phase supplies them.

C5 built the durable thing. A job has a store, a state machine, an event log, step rows, an approval gate, an
effects ledger and a hash-chained audit row written in the transaction that changes the state. The engine is
ours for that reason: an adopted engine reports over a webhook from outside the transaction, and the gap it
leaves is unclosable from where it sits.

Three facts pull the design in different directions:

1. The C6 handover says "the loop is a step whose function calls a model several times". Read literally, the
   loop is one engine step and checkpoints are phase boundaries.
2. `AG-LOOP-001` says "a step row per step, and a chain row naming the task id for every step and every tool
   call", and `AG-CHECKPOINT-001` says a run resumes from its last recorded step and no step that already ran
   runs again. `0002-agent-topology.md` gives "a failure at step 30" as the reason checkpoints exist.
3. `docs/decisions/0001-service-implementations.md` names the OpenAI Agents SDK for the runtime. That record
   predates the C5 engine and its audit transaction, and it carries named open risks.

The constraint that decides between them is the audit row. Every model call, tool call, child span and
checkpoint has to be in the same chain as the workflow rows, addressable by the task and the step that caused
it. A runtime that owns its own sessions and checkpoints would have to be mirrored into our chain, and the
mirror is the second record the requirements forbid.

## Options considered

### 1. Fixed-phase plan with the loop inside a step

The definition becomes a small static plan: plan, gather, draft, gate, act, verify, close. The gather and draft
steps call the model several times and run tools. Checkpoints are the phase rows.

- Satisfies `AG-SEAM-001`, `AG-GATE-001` and `AG-VERIFY-001` directly.
- Needs extra machinery for `AG-LOOP-001` and `AG-CHECKPOINT-001`: the model and tool calls happen inside
  one step, so the loop iterations are not step rows, and a crash mid-gather repeats every tool call made in
  that phase. A 30-step run has six checkpoints, not thirty.
- Engine change: none. That is its attraction and its limit.

### 2. Dynamic plan on the engine, one step per loop iteration

The definition is a dynamic plan. The engine asks the plan for the next `StepSpec` after reading the job's
succeeded rows. Each iteration is an engine step, so the step row, its event and its chain row are written in
one transaction. A model call, a tool call, a context summary, a child spawn, a checkpoint, a gate and an act
are all plan steps.

- Satisfies `AG-LOOP-001` and `AG-CHECKPOINT-001` literally: every step has a row, and a kill between
  iterations resumes from the first step without a succeeded row.
- The engine change is two hooks, not a new engine: a way for a plan to return the next spec from the job's
  history, and a way to say whether a spec is the final one. The static plan path stays exactly as it is.
- Cost: a dynamic plan is more code than a fixed phase plan. The controller has to decide the next step from
  the definition, the prior outputs and the pending action.
- The audit rows for tool and model calls are written by the step's runtime context through the same chain
  writer the engine uses. They are not a second writer; they are the same `insert_event` inside the same
  `audit_events` table.

### 3. OpenAI Agents SDK inside a step

The SDK runs the loop inside one engine step. It owns sessions, checkpoints, tracing, handoffs and MCP
transports.

- It solves the loop and the sub-agent vocabulary quickly.
- It brings a second state and a second trace. To make the record true we would either let the SDK be the
  runtime, which means the job store, event log, approval route and chain are no longer the authority, or
  mirror its events into our chain, which is a second way to record a model or tool call.
- Its MCP and handoff features are not the Section 2 problem. MCP is Section 3's gateway; swarms are
  `NEVER-SWARM-001`.
- It adds a dependency and an execution model to learn, against the owner's stated preference for fewer moving
  parts.

## Decision

**Option 2. The runtime is a dynamic plan on the C5 engine.** The runtime is one worker inside the API service
for this build, not a second container. The API already holds the session store, the assertion key, the job
store, the policy registry and the tool keeper; a second process would duplicate them or need a new seam for
no requirement that one process cannot meet. One deployment, one API process, one runtime worker. The worker
takes a Postgres advisory lock at startup so a second instance cannot run, and it resumes orphaned running
jobs on restart.

Supporting decisions:

- **The engine gains two hooks, not a second engine.** `Runner._drive` asks a plan for the next step when the
  plan implements `next_spec(job)`; `_settle` asks `is_final(job, spec)` when the plan implements it.
  The existing `Plan` and every C5 plan keep the static path. The change is roughly 50 to 80 lines
  including the budget and deterministic step-naming path, and the existing workflow probe is its regression
  check. An independent research pass put the same estimate at 50-80 lines in `engine.py` and `plans.py`,
  250-350 in a new `services/workflow/agent_plan.py`, and 80-120 in the API routing.
- **The loop is the plan, not one step.** Each iteration is a step row. The handover's sentence is read as the
  step function calling the model once per step, with the loop in the plan. The requirement evidence about
  step rows and a resume at step 30 outweighs the shorter reading. The divergence is recorded here.
- **The runtime uses the existing gateway and toolbox.** Model calls go through LiteLLM with a minted
  assertion, as `/ask` does. Tool calls go through `tool_loop.ToolBox` with the session's permit and a
  strict audit callback: in the runtime a tool call whose chain row cannot be written fails the step, because
  `AUD-TOOL-001` is a claim about the record rather than about the log. Section 3 replaces the tool resolver
  with the MCP gateway behind one interface; the runtime does not know the difference.
- **Two modes are the existing two surfaces.** `/ask` stays the conversational mode, with its deadline and no
  approval gate. Agent jobs are task mode, with their own job budgets and a gate. A request cannot turn a
  conversation into a task: `/ask` never creates a job. A task never inherits the conversational deadline:
  the job carries its own budget. This satisfies `AG-MODES-001` without duplicating the answer path.
- **Definitions come from packs.** The pack catalog gains an `agentic-desk` definition set (id, label, role,
  mode, tools, budgets, verify, irreversible tools). The manifest selects packs; the renderer writes
  `rendered/agent_definitions.json`; the API and worker read it. A deployment with no agentic pack gets zero
  definitions and `POST /jobs` refuses the task by name. A manifest that declares a runtime count other than
  one is refused by the validator.
- **Queue and concurrency.** The worker claims queued agent jobs with a transactional
  `select ... for update skip locked` update, runs up to `OI_AGENT_CONCURRENCY` jobs, and publishes a
  queued job's position. `pause`, `resume` and `cancel` stay the C5 routes; the worker respects the state
  before each step. An approval changes the state to running and the worker resumes; the approval route does
  not drive an agent plan in the API process.
- **Audit rows.** Model calls and tool calls use their existing event types. Child spans and checkpoints get
  explicit event types in one migration, and the feed and the coverage test learn them. The rows carry the
  task id, the step, the role, the model or tool, the span and the attribution. The answer path's per-answer
  `model_call` row is not redefined; the runtime adds per-completion rows beside it, which is the gap the
  baseline recorded.
- **Context control.** The runtime keeps a message history under a configured ceiling. When it drops or
  summarises older tool results it writes a chain row with what left, how much and when. The prompt size is
  checked before the call; a silent truncation is a failed step.
- **Sub-agents.** A child is an in-process span under the parent step. It gets a narrower read-only tool set, a
  structured brief, token and wall-clock budgets, depth one, and a structured return of facts with sources.
  Its tool calls and model calls are chain rows under the parent task id and the child span. It cannot act;
  the parent acts after the return and names the return it used.
- **The gate.** The dynamic plan inserts a gate step when the next action is irreversible. The gate's action
  digest covers the tool name and its canonical arguments. The C5 route decides; the act step applies the tool
  with the engine's stable effect key, so it is applied once. A changed argument fails as
  `approval_drift`.
- **Verification.** The last plan step checks the run's structured outcome, not the model's prose. A failed
  check ends the job failed with a typed cause, and no step after the check has a row.

## Consequences

What becomes easier: one store, one event log, one gate, one chain, and the existing pause/resume/cancel
routes. A killed run resumes from the step rows the engine already reads. A client sees agent work in the same
Do and Review screens as workflow work.

What becomes harder: the dynamic plan controller is the new complex piece; the engine hooks are small but they
touch the file every workflow test uses, so the workflow probe and the engine tests are part of every change.

What is now harder to change later: the runtime's step names are part of the record, so renaming a plan step
after a deployment has runs is a migration of meaning. The event-type additions are also chain schema, so they
land before the first runtime row.

## Evidence

- `services/workflow/engine.py:277` reads `self.plan.steps[index]`; `:302` computes position from
  `self.plan.steps`; `:477` checks `spec is self.plan.steps[-1]`. These are the three static assumptions a
  dynamic plan has to replace.
- `services/workflow/plans.py:113-147` is the static `Plan` and its budgets; `tests/fixtures/workflow_probe.py`
  holds 13 rights over the engine, including the kill test.
- `services/api/jobs.py:605-700` is the attempt loop and the budgets; `:313-500` is the durable store.
- `services/api/tool_loop.py:506-1016` is the toolbox, its permit filter and its audit callback.
  `tool_loop.py:1003-1015` swallows an audit failure inside the toolbox; the runtime cannot inherit that,
  because `AUD-TOOL-001` is a claim about the record rather than about the log.
- `docs/decisions/0002-agent-topology.md:27-52` is the one-runtime rule and the 0-to-6 definition count.
- `docs/handover-c6.md` section 6 is the loop-is-a-step sentence this record qualifies.

### Independent review pass

A read-only research pass compared the three options against the register and the code. It reached the same
recommendation and added the specific failures:

- **A fails as written on `AG-LOOP-001`, `AG-GATE-001`, `AG-CONTEXT-001` and `AG-CHILD-*`.** The step's own
  state change and chain row are atomic (`engine.py:363-375`), but the N model calls and M tool calls inside
  the step are not. Stopping at one tool call would need a suspend result `StepResult` does not have
  (`plans.py:74-84`) and writes `_settle` cannot make (`engine.py:475-531`). It also breaks the stated rule
  in `plans.py:8-12` that a step cannot touch the database.
- **B's step source is the existing transaction unit.** Each iteration gets its own unit; effects keep a
  stable key because the step name carries the iteration index (`engine.py:70-78`, `:485`), so a replay
  dedupes. The cost is row volume, which is the record the rows ask for.
- **C fails `AG-LOOP-001`, `AG-CHECKPOINT-001`, `AG-GATE-001`, `AG-SEAM-001`, `AG-RUNTIME-001` and
  `AG-DEFS-001`.** Its tracing, sessions and guardrails are a second sink, store and policy
  (`0002-agent-topology.md:31-37`). `0001-service-implementations.md:24` chose it on 2026-10-09, before the
  C5 engine existed; `:42` allows revisiting the choice through a seam. This record is that seam.
- **The dispatcher is genuinely absent today.** `services/api/app.py:854` runs a job inline and the API image
  has one worker (`services/api/Dockerfile:38`). The step-source PR cannot smuggle a worker in with it.
- **The inherited tests already encode the property:** `tests/test_workflow_chain.py:162-176` (every step row
  has a chain row) and `tests/test_workflow_engine.py:101-108` (a resume does not repeat a step).

## Build order

Each is one logical change and one PR.

1. **The dynamic step source.** The engine hooks, the `AgentPlan` protocol, deterministic step naming and a
   resume-at-iteration-n test. The workflow probe and the existing engine tests stay green.
2. **The dispatcher and the definitions.** The queue, the concurrency setting, the singleton lock, the
   pack/manifest definition render, and the zero-definition refusal.
3. **The runtime step.** Model call, strict tool audit, context control, verification and the C5
   pause/resume/cancel save points.
4. **Sub-agents and the gate.** The four child rules, child budgets and spans, the exact-call gate and the
   act-once effect.
5. **The live acceptance.** The research-brief task with a child, a checkpoint, a kill and restart, a gate on
   an irreversible test send, and `verify_chain`.

The cloud workspace phase `WS` follows this core. It adds the sandbox, the approved egress proxy, the artifact
store, the first real connector and the workspace UI, and it uses the tool interface this record defines. The
office build does not carry it.

## Open decisions for the owner

1. **In-process worker or a separate `agent` container?** The record recommends in-process for Section 2: one
   process, one policy, one key, no new image. A separate container is the architecture diagram's shape and can
   be added later without changing the plan, the gate or the record.
2. **Which definition is the acceptance task?** The record proposes one read-only `research_brief` definition
   in the agentic pack: gather with tools, one child, one checkpoint, a draft, a verify step, a gate on a test
   send, and an act. The denial-appeal and invoice-reconciliation definitions belong to the vertical sections.

## Divergence from the reference stack

The reference stack names the OpenAI Agents SDK for the runtime. This decision keeps the C5 engine and writes
the loop as a plan. The divergence is deliberate: the SDK's state and trace sit outside the transaction the C5
record exists for, and the runtime's audit rows have to be the same rows as the workflow's. The SDK row in
`0001-service-implementations.md` is superseded for the runtime only; the other service choices in that record
stand. Its MCP support belongs to Section 3's gateway if it is used there, which is a separate decision.
