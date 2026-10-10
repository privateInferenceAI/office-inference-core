# 0002. Agent topology: how many agents, and of what

Date: 2026-10-09
Status: accepted

## The confusion to kill first

"Agent" means four different things, and only one of them is a thing we deploy more than once.

| Word | What it actually is | How many |
|---|---|---|
| **Model role** | Which model answers: analyst, front desk, reader, coder | 3 to 4 per deployment, chosen in the manifest |
| **Agent runtime** | The service that runs loops, calls tools, checkpoints, and pauses | **Exactly one per deployment** |
| **Agent definition** | A named job: research, reconciliation, intake, denial appeal | 0 to 6, depending on the packs |
| **Swarm** | Several agents cooperating on one task | **Zero.** Not used |

So a hybrid deployment does not have two agents. It has one runtime, the same definitions, and a
router that can send some steps to a frontier model.

## Model roles are not agents

The friend's build locked "three LLM roles, all Qwen3": an analyst, a front desk, and a document
reader. Those are **models**, not agents. One of them will sit behind a voice conversation and one
behind document reading, and neither is a separate agent. When the answer to a question needs a
bigger brain, the role changes, and the agent stays where it is.

## The runtime count

One runtime for everything, in both builds.

- It holds sessions, so a task can be resumed.
- It holds the tool policy, so permissions live in one place.
- It writes to one audit sink, so "what did the system do" has one answer.
- It can run several definitions at once, so concurrency is a queue setting, not another service.

A second runtime would mean a second audit path, a second policy, and two places for a bug to hide.
There is no case for it at this scale.

## The definitions, by pack

| Pack | Agent definitions | Notes |
|---|---|---|
| Document Chat | **0** | Retrieval plus a model call. No loop needed. If a question needs several hops, add a "search" definition later |
| Workflow Automation | **0** | n8n is deterministic. That is the point of it |
| Agentic Desk | **4 to 6** | Research and brief, reconciliation, intake, follow-up, plus one vertical (denial appeal, or quote for a shop) |
| Frontier Lane | **0** | A lane, not an agent. It changes which model a step uses |
| Voice | **1** | The conversational definition, on the front desk role |
| Coding | **1** | Writes small scripts. The clearest case for the frontier lane |

An office build with Document Chat and Workflows has **one runtime and no agent definitions**. That
surprises people, and it is correct: most of the value is retrieval and deterministic automation.
A build with Agentic Desk has one runtime and four to six definitions.

## Two modes, not two agents

A conversation and a long task have opposite requirements. A person talking wants an answer in a
second. An agent doing month-end close runs for twenty minutes and stops for approval.

So there are two **modes**, with different prompts, timeouts, and model roles:

- **Conversational mode**: short turns, small fast model, no long tool chains.
- **Task mode**: long runs, checkpoints, approvals, possibly a frontier model.

Both are definitions inside the same runtime. The client app shows them as Ask and Do, which is
also why the interface has two verbs.

## Voice is a pipeline, not an agent

Voice is speech to text, then the model, then text to speech. It adds two services (ASR and TTS)
and a VRAM budget, not an agent. In the friend's build, better voice quality means a bigger ASR and
TTS model, which competes with the analyst model for GPU memory. That is the real cost of quality
voice, and it is a hardware decision, not an agent-count decision.

## Do agentic jobs need swarms

No. What a long job actually needs:

1. A loop with tools.
2. Checkpoints so a failure at step 30 does not restart the job.
3. Context management, so 200 tool results do not fill the window.
4. Verification steps, so it knows whether it worked.
5. An approval gate for anything irreversible.

**Delegation is not a swarm.** When the denial appeal agent needs the payer's policy for a code, it
spawns a sub-task, gets a summary back, and keeps going. One agent with helpers. That is the pattern
worth copying from the harnesses that do it well, and it captures most of what people imagine swarms
buy them.

Swarms become worth considering when a job genuinely splits into independent workstreams that need
different expertise and can run in parallel, such as a large code migration or a multi-day research
project, or when adversarial review is worth the cost. None of that is small business work. The
coordination overhead and the new failure modes would be ours to support, and the client would never
see the difference.

**Decision: no swarms. Revisit if a customer's job cannot be expressed as one agent with sub-tasks.**

### Sub-agents are agents. The difference is ownership.

A fair objection: if sub-agents exist, then a run really does involve more than one agent, so what is
the rule? The rule is not about counting. It is about **who owns the task**.

- **Sub-agent, hierarchical.** One parent owns the goal, the task state, the budget, and the final
  answer. A child gets a narrow brief, works in its own context window, and returns a summary. The
  child has no goal of its own, no memory of the wider task, and no authority to spawn further work.
  Control never leaves the parent.
- **Swarm, peer.** Several agents negotiate, hand off control, or run against a shared state with no
  single owner. Control moves between agents, and task state becomes everyone's and no one's.

Both involve multiple agent instances. Only the second makes the task's state and its failure modes
ambiguous. That is why "no swarms" and "yes, sub-agents" are consistent statements.

Industry naming is inconsistent, which is where the confusion comes from: sub-agents, child agents,
workers, handoffs, crews. They describe the same fan-out idea and are marketed as different things.

### Rules for sub-agents in this system

1. **Children read, parents act.** Sub-agents gather, extract, and analyze. Any action with a side
   effect belongs to the parent, so approvals and irreversibility have exactly one owner.
2. **A narrower tool scope than the parent.** Never wider. A child that can do more than its parent
   is a privilege escalation.
3. **A structured return.** Facts with citations, not prose. The parent should not have to re-read a
   transcript.
4. **A budget per child**, in tokens and in wall-clock time. A runaway child is a runaway bill.
5. **Depth limit of one** by default. A child that spawns children is where understanding a run
   becomes hard.
6. **One audit trail.** Every child span is recorded under the parent task id, with its brief, its
   tools, and its result. That is what answers "what did the system do" without a swarm-style fog.

### The three counts, stated once

| Count | What it is | Typical number |
|---|---|---|
| Deployed definitions | Persistent, configured jobs that appear in the interface | 0 to 6 |
| Concurrent runs | Instances of a definition executing at once | 1 to 5 |
| Sub-agents | Ephemeral children spawned inside one run, then gone | 0 to 8 per run |

Sub-agents do not change the deployment. They are a property of how a single run manages its
context.

## What this means for sizing

The number of definitions is not what sizes a box. These are:

| Unit | Why it matters |
|---|---|
| Models resident at once | VRAM. The conversational model and the analyst both want to be loaded |
| Concurrent task runs | CPU, RAM, and queue depth |
| Tool latency | How long a run takes, which decides whether a person waits or walks away |
| ASR and TTS models | VRAM, if voice is on |

A single box can host one runtime, six definitions, and five concurrent runs without difficulty.
The GPUs are for the models, not for the agents.
