# C6 Section 2 gate report

**Status:** gate passed on the live box, awaiting owner acceptance. All 19 Section 2 rows are verified.

The live evidence is in this directory. The box was `i-02aba2feef08f8e7b` at `52.204.204.133`, credits account
`878451098165`, deployment `oi-core-dev-cloud-workspace`.

## What was built

- A dynamic plan on the C5 engine: one model turn is one engine step.
- A durable queue, a singleton dispatcher, a concurrency setting and a readable queue position.
- Definitions loaded from the Agentic Desk pack.
- A model call and tool call path with strict audit, addressed by job, task and step.
- A verification step over the answer.
- A read-only child helper, depth one, with a subset of the parent's tools and its own span.
- An approval gate for declared irreversible tool calls, resumed through the existing approval route.
- A context ceiling that drops whole message groups and records what left.
- Startup recovery for a job a dead process left running, with a resume row.
- Pause, resume and cancel through the existing C5 routes.

## Verified by live run

| Row | Evidence |
|---|---|
| AG-LOOP-001 | Job `2426a630` ran four steps, one per model turn, with a row for each. |
| AG-CHECKPOINT-001 | Job `88b19c45` was killed during `turn-2` and resumed on restart. `turn-1` did not repeat. |
| AG-RUNTIME-001 | One API process ran the dispatcher and the runtime; a second worker is refused by the advisory lease. |
| AG-CONTEXT-001 | Job `095074f7` wrote `context_trim` rows at turns 2 through 7 and finished. |
| AG-VERIFY-001 | An answer with no numbered citation failed `finish`; a cited answer passed. |
| AG-GATE-001 | Job `af8ab770` stopped at `approval-1`; the approval route resumed it; `act-1` ran the exact call. |
| AG-SEAM-001 | Job `b366c97b` paused, resumed and finished; job `06cd13cb` cancelled. A gate decision used the same approval route. |
| AG-DEFS-001 | The rendered pack supplied `research_brief`; a deployment without the pack has no definition. |
| AG-CHILD-READS-001 | The child used only search and read tools. |
| AG-CHILD-RETURN-001 | The child returned a JSON brief with sources, and the parent continued from it. |
| AG-CHILD-DEPTH-001 | The child had no delegate tool, so it could not spawn another child. |
| AUD-TOOL-001 | The live jobs have a tool row for each search and read, addressed by job and step. |
| AUD-SPAN-001 | The child rows carry span `child-1` and role `child` under the parent task. |
| AUD-MODEL-001 | The live jobs have a `model_call` row per completion, addressed by job and step. |
| AG-MODES-001 | `/ask` answered and created no job (38 to 38); `/jobs` created one queued job (39) with a 600 second budget. |
| AG-QUEUE-001 | Two definitions, concurrency one: the second job waited `queued` with position 2, then both succeeded. |
| AG-CHILD-BUDGET-001 | With `child_max_tokens` at 1, the child returned `child_token_budget` spent 994 budget 1; the parent continued. The child row and the parent rows carry 994 tokens. |
| SEC-AGENT-001 | A child scope naming `send_email` outside the parent made the job fail `definition_child_scope`, and audit row 685 names the tool. |
| AUD-CHECKPOINT-001 | The kill run wrote resume row 705 naming step `turn-2`, and the 381-event chain verifies intact. |

## Open rows

None. The five rows that were open at the first report are verified by the `close/` evidence.

## Tests

The full suite on `main` with a test database: **1292 passed, 1 skipped**.

The live runs found and fixed six defects that the unit tests did not:

1. The runtime omitted the job input from the prompt.
2. The runtime wrote no model rows and did not address tool rows by job and step.
3. The audit tool and the table did not accept `context_trim`.
4. The trim kept a tool result without its assistant call.
5. The first pause/resume started a second runner and repeated steps.
6. The approval and control routes did not resume an agent definition.

## Next

Section 3 (MCP gateway and connectors) and the `WS` workspace phase.
