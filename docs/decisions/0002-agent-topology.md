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
