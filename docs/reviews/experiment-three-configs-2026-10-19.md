# Five runs, three configurations: what the loop is actually worth

Run overnight on 2026-10-19 to settle one disagreement: the Phase A measurement looked better than the final Phase
B/C configuration, and three-run comparisons could not tell a real regression from noise. Five runs per
configuration, frozen corpus, one scorer.

## Method

| | |
|---|---|
| Corpus | 539 documents, 100,477 chunks plus the restored canary document, frozen for the experiment |
| Model | Claude Haiku 4.5 through Bedrock, the same inference profile in every configuration |
| Suite | The forty questions plus four probes, 52 runs per suite run |
| Scorer | One scorer, the one committed with the data suite |
| Runs | Five per configuration, fifteen in total, completed 2026-10-19T01:57Z |

Configurations, each built from a frozen commit or a frozen flag:

- **Phase A**: commit `b76349c`, tool loop on, two tools (search and read), its original prompt.
- **Main**: commit `bc806a8`, tool loop on, lookup off, honesty rule on, current prompt.
- **Single-shot**: same commit and model as Main, with `OI_TOOL_LOOP=off`. This is the control.

The gateway image was the current budget-fixed one in all three, so the run measures the answer path and not the
budget defect fixed earlier that day.

## Results

| Configuration | Totals | Mean | Range | Mean suite seconds |
|---|---|---|---|---|
| Phase A | 37, 43, 40, 44, 42 | **41.2** | 37 to 44 | 373 |
| Main (current) | 33, 45, 44, 36, 43 | **40.2** | 33 to 45 | 365 |
| Single-shot | 36, 33, 34, 35, 37 | **35.0** | 33 to 37 | 112 |

**The loop is worth five to six points.** 40.2 and 41.2 against 35.0, more than five runs of run-to-run spread
explains, and the single-shot range does not reach the loop means.

**Phase A and current main are indistinguishable.** One point apart with almost completely overlapping ranges,
and per question Phase A wins 11, main wins 7, and 34 are ties. The earlier claim that the final configuration "did
worse" was wrong: the Phase A triple of 44, 44, 42 was the top of its own distribution, whose five-run mean is
41.2. The two prompt changes I shipped in response to that apparent regression were noise chasing and should not
have been made on three-run evidence. They are harmless, but they are recorded here as a method failure, not a fix.

## Where the loop earns its points

| Category | Phase A | Main | Single-shot |
|---|---|---|---|
| exact number from a table | 6.0 | 5.4 | **0.8** |
| prose fact | 6.0 | 5.2 | 6.0 |
| unanswerable | 7.4 | 7.0 | **8.0** |
| cross-document | 5.0 | 5.8 | 4.2 |
| access boundary | 14.8 | 14.4 | **15.0** |
| probe: row in normal table | 1.0 | 1.0 | 0.0 |
| probe: row in large table | 0.0 | 0.0 | 0.0 |
| probe: data-set question | 1.0 | 1.0 | 1.0 |
| probe: pollution | 0.0 | 0.4 | 0.0 |

Two things stand out. Exact-number questions are ten times better with the loop: six of eight against under one of
eight. And the two categories where single-shot is nominally ahead, unanswerable and access, are the honesty
categories: the loop answers a little more, including a little more than it should. That is the trade to watch,
and it is why the honesty rule stays on.

## Caveats

- Five runs still produce a wide interval; the difference between the two loop configurations is not measurable.
- Two individual runs carried elevated degraded counts (15 and 10 of 52) from provider or budget blips. Their totals
  stayed inside the range, and the means are dominated by the twelve clean runs.
- The Phase A configuration differs from main in code and prompt together, so this experiment says the two
  arrangements are equivalent; it does not isolate which difference matters, and nothing here justifies isolating
  it further.
- The corpus, model and gateway were held constant, but the environment is a shared Bedrock account; a quiet hour
  and a noisy hour are not the same measurement.

## Decision

The live development deployment runs current main with the loop on: Claude Haiku, six steps, ten calls, lookup
off, honesty rule on. Phase A is equivalent, so there is no reason to pin an old commit. No more prompt changes
until the structured query tool exists, which is the next piece of work and the one that explains the remaining gap
between the stack and the way the questions were answered by hand.

## What this corrects

`docs/reviews/phase-b-c-2026-10-18.md` reported the final configuration against a Phase A peak of 43.3. The
defensible numbers are 41.2 for Phase A and 40.2 for current main, both against 35.0 single-shot. The earlier
report's "within variance" wording was right in direction and wrong in method: it should have run five times
before drawing any conclusion at all.
