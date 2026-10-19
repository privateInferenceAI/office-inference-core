# The row query: five runs with a structured table tool

Written 2026-10-19. This is the measurement for the `query_rows` tool added by PR #183. The per-run result
files are committed beside this document in `raw/`, and `compare.py` recomputes every number below from them.

## What was asked

The hand-off named the biggest remaining gap: the stack cannot answer structured-data questions well because the
model sees at most a few passages, cannot query rows, cannot verify a figure with a second query, and has no way
to write a filter. The proposed next milestone was a read-only table query, ACL-filtered, bounded by rows and
bytes, fully audited, measured over five runs against the current configuration, with the prompt left alone. The
expected effect was exact-number rows from about 5 or 6 of 8 to 7 or 8, and the large-table probe starting to
pass.

## What was built

`query_rows` takes one to four literal strings and returns the table rows that contain all of them, each with
its document, page and row ordinal, numbered into the evidence namespace so the citation verifier checks the
answer against the row. The query is one parameterised SELECT over `chunk_index` with the session's access roots
in its where clause. Constants bound it: 400 chunks scanned, 8 rows returned (4 by default), 600 characters a
row, 6,000 characters a response. An unscoped match spanning more than three documents is refused with examples
rather than anchored on the first. Every call is a `tool_call` audit row with its candidate, matched and
returned counts.

The prompt gained one sentence naming the tool, generated from the offered list like the other tools. No rule,
step or call cap was changed. The tool is off by default behind `OI_TOOL_QUERY`; the flag is the opt-in and the
rollback.

## Method

| | |
|---|---|
| Corpus | 539 documents, 100,478 chunks, six data-set cards, six dead letters, frozen since 2026-10-18 |
| Model | Claude Haiku 4.5 through Bedrock, `us.anthropic.claude-haiku-4-5-20251001-v1:0` |
| Suite | the forty questions plus four probes, 52 role-runs per suite run |
| Scorer | the committed `tests/fixtures/data_suite_run.py`, one scorer for both configurations |
| Runs | five per configuration, sequential, same build, same corpus, same model |
| Control | `OI_TOOL_QUERY` unset: the tool is not offered and the answer path is the measured configuration |
| Treatment | `OI_TOOL_QUERY=on`: the tool is offered; nothing else changes |
| Flags in both | loop on, six steps, ten calls, lookup off, honesty rule on |
| Build | `59c17dc` on `feat/structured-query-tool` for both |
| Window | 2026-10-19 14:20Z to TREATMENT_ENDZ |

**The suite script had to be fixed to run this.** The eval image runs as uid 10003 and the output directory
belongs to the operator, so the runner's final write failed with `PermissionError` after a full suite had
already run, and the run was lost. `scripts/run_answer_suite.sh` now runs the container as the invoking user.
That fix is in the same pull request, and the first control attempt was lost to it before the numbers below.

**One environment note.** At 14:05Z a live question returned `degraded / provider_unavailable`. Bedrock's
model-access API reported Haiku available and authorized in us-east-1, a direct invoke from the box failed with
`ServiceUnavailableException`, and Sonnet in the same region plus Haiku in us-east-2 and us-west-2 all answered.
The condition cleared by 14:12Z, before the first control run, and the run below records its own degraded counts.
That is an incident of the provider account, not of this configuration.

## Results

| Configuration | Totals | Mean | Range | Degraded answers |
|---|---|---|---|---|
| Query off (control) | 43, 44, 43, 43, 42 | **43.0** | 42 to 44 | 1, 2, 2, 1, 1 |
| Query on | TREATMENT_TOTALS | **TREATMENT_MEAN** | TREATMENT_RANGE | TREATMENT_DEGRADED |

TREATMENT_DELTA

By category (mean passes per run):

| Category | Query off | Query on |
|---|---|---|
| exact number from a table (8) | 4.8 | TREATMENT_EXACT |
| prose fact (8) | 6.0 | TREATMENT_PROSE |
| unanswerable (8) | 6.8 | TREATMENT_UNANSWERABLE |
| cross-document (8) | 7.6 | TREATMENT_CROSS |
| access boundary (16) | 15.0 | TREATMENT_ACCESS |
| probe: row in normal table (1) | 1.0 | TREATMENT_PR1 |
| probe: row in large table (1) | 0.0 | TREATMENT_PR2 |
| probe: data-set question (1) | 1.0 | TREATMENT_PR3 |
| probe: pollution (1) | 0.8 | TREATMENT_PR4 |

TREATMENT_ANALYSIS

## Caveats

- Five runs per configuration, on one corpus, in one account. The control's own range is 42 to 44 and the
  treatment's is TREATMENT_RANGE; TREATMENT_CAVEAT
- **The control mean is 43.0, and the frozen five-run mean for the same configuration was 40.2.** Same build
  behaviour, same flags, same model, one day apart, so the difference is the environment: a quieter account or a
  provider serving better. This is why the comparison below is to a same-day control and not to the frozen
  number. Any claim that the tool adds points is a claim about the difference between two configurations measured
  in the same window.
- `PR1` and `PR4` are known-imperfect probes; the hand-off says their failures are not clean evidence. They are
  reported as measured and should not decide anything on their own.
- The audit-call counts are the tool's own record, not an inference about which questions used it: one question
  can call it more than once, and a run can answer without it.
- The tool is off by default in code. The development deployment has it on. A different corpus measures
  differently, and the opt-in is deliberate.

## Evidence

- `raw/control/run-1.json` to `run-5.json`: the five control suite runs.
- `raw/rowquery/run-1.json` to `run-5.json`: the five treatment suite runs.
- `compare.py`: recomputes the totals, ranges, degraded counts, category table and per-question counts from the
  ten files. `python3 docs/reviews/query-tool-2026-10-19/compare.py`.

## Decision

TREATMENT_DECISION
