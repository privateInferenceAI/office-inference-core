# 0006. A capped tool loop answers from the evidence it gathered

**Date:** 2026-10-20, accepted 2026-10-21. **Status:** accepted and enabled by default (no flag).

## Why

The tool loop caps steps and tool calls. When it reaches a cap without an answer, the old path returned no
answer and the API fell back to the single-shot request built from the six passages retrieval had already
chosen, discarding every search, read and row-query result the loop had gathered. That is the measured N06/N07
flakiness: the loop found the row and the caller threw it away, and the single-shot answer refused or read the
wrong table. The same shape appears in the committed raw runs of the 2026-10-19 deterministic-failure review.

## Decision

When the loop is about to exit because of the step cap or the tool-call cap, and it holds at least one piece of
evidence, it makes one final model call with no tools offered. The prompt asks for an answer from the evidence
already gathered, with the same citation rule, and the same answer contract verifies it. If the content is empty
or violates the contract, the loop returns exactly what it returned before. The deadline exit is unchanged
because there is no time left for another call.

The call is recorded in the loop's stats as `synthesis: 1` and does not count as a step or a tool call. The
synthesis turn is invisible to the measured system prompt: it is a user message added at the cap.

## Evidence

- Root cause found 2026-10-20 by tracing N06 against the API audit and the committed raw runs: the successful
  runs answer from evidence [25], the failing run is a degraded single-shot answer with six passages.
- Unit tests cover success, no-tools, and failed-synthesis paths.
- Five-run acceptance is pending. The new measurement-hygiene rules (0005) require a serial run; the exact-lane
  comparison was re-run under contention and could not be certified, which is exactly why the rescue exists.

## Acceptance, 2026-10-21

Five serial runs per phase under the measurement rules in 0005, with the run context recorded by the suite script.

| Comparison | Control (main) | Rescue |
|---|---|---|
| Corpus-1 live suite (53) | 53, 53, 53, 53, 53 | 53, 53, 53, 53, 52 |
| Corpus-2 live set (37) | 29, 30, 29, 29, 27 (mean 29.4) | 29, 30, 28, 31, 28 (mean 29.2) |

The corpus-1 difference is one verification of A04 executive (the CEO long-term incentive question). A04 is
demonstrably unstable in isolation on both builds: five standalone runs of it failed 5 of 5 on main as well as on
the rescue build, because the model sometimes refuses to read the incentive figure out of the compensation table.
The rescue's first shape also regressed A04 by accepting a synthesized refusal as an answer; the shipped shape
rejects a synthesized refusal and falls through to the old single-shot path, which restored 4 of the 5 runs. No
other corpus-1 question changed, and corpus-2 is flat within the five-run spread.

The first acceptance attempt was invalidated by provider quota: running a suite on each deployment at once
exhausted the shared Bedrock daily token quota, and the 429s degraded the later runs. That produced rule 8 in
decision 0005 (one suite at a time across every deployment on one provider account). The accepted runs above were
taken serially afterwards.

## Consequences

- The rescue affects every answer path, so it ships only after a clean five-run comparison on both corpora.
- Once measured, the same evidence should show whether the rescue subsumes part of the exact-phrase fallback's
  purpose; the exact flag remains default off until its own gate is certified.
