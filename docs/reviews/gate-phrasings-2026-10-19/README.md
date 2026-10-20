# The honesty gate's boundary, measured and widened

**Date:** 2026-10-19 night. **Deployment:** `oi-core-dev-cloud`. **Register:** `AGENT-REFUSAL-001`.
**Supersedes the boundary paragraph in** `docs/reviews/honesty-gate-2026-10-19.md`.

## What was assumed, and what was measured

The gate shipped earlier the same day reads questions phrased as **the company's** attribute. Its stated boundary
was that "a question worded differently is not covered". That was an assumption. Six probes were written to
measure it, each with an absent pattern checked at zero hits on the index, and run as a subset against the
deployed gate before any change:

| Probe | Question | Gate as shipped |
|---|---|---|
| P1 | What is our parental leave policy? | **answered from FMLA, failed** |
| P2 | What is the policy on parental leave? | **answered from FMLA, failed** |
| P3 | What is the policy regarding jury duty? | refused (model declined) |
| P4 | What does the company's employee handbook say about parental leave? | refused (gate fired) |
| P5 | What is our dress code? | **answered from a contractor clause, failed** |
| P6 | What is the company's remote work policy? | refused (gate fired) |

Three of six failed: the two "our" shapes and the "policy on" shape. P3 passed by the model's own judgement rather
than by the gate, which is not a control.

## The fix

The gate now reads three measured shapes, in this order:

1. `the company's` / `the firm's` / `the organisation's` possessive (as before)
2. `the policy on|about|regarding|for <subject>`
3. `our <subject>`

Meta-questions are excluded: `what do our documents say about X` is about the evidence, not the company, and is
not refused. Four tests were added: the "our" shape, the "policy on" shape, the meta exclusion, and the "our" shape
passing when the evidence states the attribute.

## After

All six probes refuse with the exact refusal text:

~~~
P1 refused, P2 refused, P3 refused, P4 refused, P5 refused, P6 refused
~~~

## Regression measurement

Five runs of the scored suite (53 role-runs, the same instrument as the honesty-gate measurement): **(pending,
filled before merge)**.

## The wider set now keeps this measured

The six probes are appended to `docs/eval/data-questions-wider.yaml` as `W_P1`..`W_P6` (72 questions in total),
and U05's stale-pattern note in that file was corrected to match the live forty. The validator passes **72 of 72**
against the production index. If a future change narrows the gate, these questions fail in the wider measurement
and say so, which is the point of putting them there rather than in a side file.

## Risk

- **"Our" widens the false-refusal surface.** "What is our revenue" is gated if the word "revenue" appears in no
  passage, which is right for this corpus, but a client corpus that states a fact in different words from the
  question could be refused. The meta-question exclusion covers the obvious case; anything beyond it is a measured
  change, not a guess.
- The gate still does not guess synonyms. A question that uses a synonym for the attribute is not covered.
- The verifier is unchanged: a fabricated sentence whose digits appear somewhere in the evidence can still pass.
