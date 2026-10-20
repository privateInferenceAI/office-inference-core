# 0006. A capped tool loop answers from the evidence it gathered

**Date:** 2026-10-20. **Status:** implemented, measurement pending. **Owner context:** reliability work continues.

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

## Consequences

- The rescue affects every answer path, so it ships only after a clean five-run comparison on both corpora.
- Once measured, the same evidence should show whether the rescue subsumes part of the exact-phrase fallback's
  purpose; the exact flag remains default off until its own gate is certified.
