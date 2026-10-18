# The day the answers started working: contract, models, and tools

One day, four measurements, on the same forty questions and four probes (52 runs each). The point was to find out
why the assistant was right about 57 percent of the time when retrieval was already finding the right document 89
percent of the time. The answer was three layers deep: the model was too weak for the contract we gave it, the
contract itself forbade the thing cross-document questions need, and the model had no way to look at anything
beyond the five passages it was handed.

## Progression

| Configuration | Passes (3 runs) | Average | Suite seconds |
|---|---|---|---|
| Nova Lite, single shot, original prompt | 32, 25, 31, 29 | 29.2 | ~75 |
| Nova Lite, single shot, answer contract (temperature 0) | 31, 29, 27 | 29.0 | ~75 |
| Nova Lite, single shot, contract + multi-passage rule | 29, 28, 29 | 28.7 | ~75 |
| Claude Haiku 4.5, single shot, contract + multi-passage rule | 36, 36, 34 | 35.3 | ~112 |
| Nova Lite, tool loop | 42, 36, 35 | 37.7 | ~187 |
| **Claude Haiku 4.5, tool loop** | **44, 44, 42** | **43.3** | **~339** |

**83 percent, against 55 percent at the start of the day**, measured three times on the same set.

The order matters. The answer contract on its own changed nothing: temperature zero and a stricter prompt moved
the total by zero and a bit, because the model still could not see more than it was given. The frontier comparison
found two things at once. Claude Haiku was better on prose facts, honest refusals and access completeness, and both
Claude models refused every cross-document question, 0 of 8, because the contract said "using only the numbered
passages" and a capable model read that as one passage. Nova's tendency to ignore that instruction had been hiding
the wording defect. Fixing the wording recovered cross-document for Haiku and moved the single-shot total to 35.3.

The tool loop is the layer that made the difference. `search_documents` and `read_passages` let the model look for
the row it needed and read around a hit. Exact-number questions went from 1 or 2 of 8 to 5 to 7 of 8. Cross-document
went from 3 or 4 of 8 to 7 or 8. The normal-table row probe now passes in every run.

## Per-category, all four configurations

| Category | Nova single | Haiku single | Nova tools | Haiku tools |
|---|---|---|---|---|
| exact number from a table | 3, 0, 1 | 3, 2, 0 | 5, 3, 2 | 7, 6, 5 |
| prose fact | 4, 5, 4 | 6, 6, 6 | 6, 6, 6 | 6, 6, 6 |
| unanswerable | 6, 7, 6 | 8, 8, 8 | 6, 5, 6 | 7, 7, 7 |
| cross-document | 2, 2, 4 | 3, 4, 4 | 8, 6, 6 | 7, 8, 7 |
| access boundary | 13, 13, 13 | 15, 15, 15 | 14, 14, 13 | 15, 15, 15 |
| probe: row in normal table | 0, 0, 0 | 0, 0, 0 | 1, 1, 0 | 1, 1, 1 |
| probe: row in large table | 0, 0, 0 | 0, 0, 0 | 0, 0, 0 | 0, 0, 0 |
| probe: data-set question | 1, 1, 1 | 1, 1, 1 | 1, 1, 1 | 1, 1, 1 |
| probe: pollution | 0, 0, 0 | 0, 0, 0 | 1, 0, 1 | 0, 0, 0 |

## What is still wrong, stated plainly

- **A pinpoint row in the 41,000-row table still fails.** The row cap means those rows are not individually
  indexed, and the model reading a bounded group still picks the wrong line. This is the accepted tradeoff, and
  Phase B's `lookup_value` tool is the answer: exact numeric lookup rather than reading groups.
- **Tool use on the weaker model made honesty slightly worse.** Nova with tools answered two unanswerable
  questions from adjacent facts (a Q4 dividend and a board decision that the corpus does not state). Haiku held
  7 of 7. The fix is a stricter honesty rule in the loop prompt, not a smaller tool set.
- **The answer often opens with a sentence about the evidence** ("Based on the evidence already retrieved..."),
  which the contract forbids and the prompt does not yet stop reliably.
- **Latency and cost rose.** The same suite takes 339 seconds against 112, so roughly three times the model calls
  per answer. That is the price of a loop, and it is bounded by the step and call caps. Per-answer latency
  percentiles are not measured yet; that belongs with Phase C.
- **The two remaining bad probes** (PR1 is fine now; PR4 was always a bad probe) should be replaced before the
  next comparison.

## Live configuration and rollback

The development deployment now runs Claude Haiku 4.5 with `OI_TOOL_LOOP=on`. Rollback is one of:

- `OI_TOOL_LOOP=off` in `rendered/env`, recreate the API: back to the single-shot path with the same model.
- revert `manifests/dev-cloud.yaml` analyst to `bedrock/amazon.nova-lite-v1:0`, re-render, recreate: back to the
  cheaper model.

The Anthropic access was enabled in the credits account today: use-case form submitted, and `oi-core-dev-bedrock-invoke`
policy versions v4 and v5 add the Claude Haiku and Sonnet inference profiles and the foundation-model ARNs in the
three US regions the profile routes to. Version v5 is the default; rolling back is setting v3 as default.

## Next

Phase B: `lookup_value` as a third tool, table-row reading, and the honesty tightening. Phase C: streaming progress
frames, interface work, and the acceptance panel on the finished corpus. The forty-question suite remains the
measurement, three runs per configuration, same scorer.
