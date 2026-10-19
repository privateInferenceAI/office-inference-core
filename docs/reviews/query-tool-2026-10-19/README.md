# The row query: five runs with a structured table tool

Written 2026-10-19. This is the measurement for the `query_rows` tool added by PR #183. The ten result files are
committed beside this document in `raw/`, and `compare.py` recomputes every number below from them.

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
rather than anchored on the first, and rows are allowed eight evidence slots past the two-dozen search ceiling so
a full namespace cannot silently swallow the one row the answer needs. Every call is a `tool_call` audit row
with its candidate, matched and returned counts.

The prompt gained one sentence naming the tool, generated from the offered list like the other tools. No rule,
step or call cap was changed. The tool is off by default behind `OI_TOOL_QUERY`; the flag is the opt-in and the
rollback. Two defects found while making the comparison runnable are fixed in the same pull request: the suite
script could not write its output directory as the eval image's non-root user, and the API never passed
`--tool` to the audit tool, so every tool call in the chain had an empty tool column.

## Method

| | |
|---|---|
| Corpus | 539 documents, 100,478 chunks, six data-set cards, six dead letters, frozen since 2026-10-18 |
| Model | Claude Haiku 4.5 through Bedrock, `us.anthropic.claude-haiku-4-5-20251001-v1:0` |
| Suite | the forty questions plus four probes, 52 role-runs per suite run |
| Scorer | the committed `tests/fixtures/data_suite_run.py`, one scorer for both configurations |
| Runs | five per configuration, sequential, same corpus and model |
| Control | `OI_TOOL_QUERY` unset: the tool is not offered and the answer path is the measured configuration |
| Treatment | `OI_TOOL_QUERY=on`: the tool is offered; nothing else changes |
| Flags in both | loop on, six steps, ten calls, lookup off, honesty rule on |
| Build | control `59c17dc`, treatment `4805ca2`, both on `feat/structured-query-tool`; the only code difference between them is the row evidence allowance, which is unreachable while `OI_TOOL_QUERY` is off |
| Window | control 2026-10-19 14:20Z to 14:56Z, treatment 15:40Z to 16:16Z |

**The suite script had to be fixed to run this.** The eval image runs as uid 10003 and the output directory
belongs to the operator, so the runner's final write failed with `PermissionError` after a full suite had
already run, and the run was lost. `scripts/run_answer_suite.sh` now runs the container as the invoking user.

**One environment note.** At 14:05Z a live question returned `degraded / provider_unavailable`. Bedrock's
model-access API reported Haiku available and authorized in us-east-1, a direct invoke from the box failed with
`ServiceUnavailableException`, and Sonnet in the same region plus Haiku in us-east-2 and us-west-2 all answered.
The condition cleared by 14:12Z, before the first control run. Bedrock served a handful of short
`provider_unavailable` blips during the treatment runs, counted in the table below; none of them is a response
of this configuration.

## Results

| Configuration | Totals | Mean | Range | Degraded answers | Degraded reason |
|---|---|---|---|---|---|
| Query off (control) | 43, 44, 43, 43, 42 | **43.0** | 42 to 44 | 7 | 7 unsupported number |
| Query on | 47, 45, 47, 47, 47 | **46.6** | 45 to 47 | 11 | 11 provider unavailable |

**The tool is worth 3.6 points, and the ranges do not overlap**: the control's best run is 44 and the
treatment's worst is 45. The two kinds of degraded answer are worth separating. The control's seven are
`unsupported_number`: an answer stated a figure the citation verifier could not find in the evidence, so it was
refused and the passages were returned instead. The treatment has none of those, and its eleven are provider
blips. Measured against the control, the tool both raises the totals and removes the failure class it was built
for.

By category (mean passes per run):

| Category | Query off | Query on | Change |
|---|---|---|---|
| exact number from a table (8) | 4.8 | 7.4 | +2.6 |
| prose fact (8) | 6.0 | 6.0 | 0 |
| unanswerable (8) | 6.8 | 7.2 | +0.4 |
| cross-document (8) | 7.6 | 8.0 | +0.4 |
| access boundary (16) | 15.0 | 14.6 | -0.4 |
| probe: row in normal table (1) | 1.0 | 1.0 | 0 |
| probe: row in large table (1) | 0.0 | 0.8 | +0.8 |
| probe: data-set question (1) | 1.0 | 1.0 | 0 |
| probe: pollution (1) | 0.8 | 0.6 | -0.2 |

The exact-number category is the point of the change: 4.8 to 7.4 of 8. The large-table probe passes four runs of
five; its one failure is a provider blip, not a wrong row. Per question, the material moves are N01 (3 to 5),
N02 (1 to 4), N05 (0 to 5), N07 (0 to 5), X02 (3 to 5), and PR2 (0 to 4). The small offsetting moves are N03 and
N04 (5 to 4, both provider blips), A07 (5 to 4, a provider blip) and PR4 (4 to 3, a known-imperfect probe). N06
passes in all five runs after the evidence-allowance fix described below.

Across the five treatment runs the audit chain records 53 `query_rows` calls: 34 returned rows, 13 found no
candidate chunk (the model guessing a value or a document), and the rest are cases where the strings matched a
chunk but no single row. Twenty-seven calls were document-scoped and eight were not. The comparison with
`search_documents` (377 calls) and `read_passages` (98 calls) is a count of use, not of value; the scores are
the value.

## The one question the first treatment lost, and the fix

A first five-run treatment was taken on the build before the evidence-allowance fix: 47, 46, 46, 47, 46, a mean of
46.4, with N06 failing in all five runs. The cause was in the tool, not the model. N06 asked for an overall median
earnings figure in a table the model had already read. The evidence namespace was full at twenty-four entries, so
the rows the model queried matched and were **not rendered**; the tool answered with a header and no lines, and
the model read that as absence. It then exhausted its steps and fell back to single-shot, whose answer the
citation verifier refused. Rows now get eight evidence slots past the ceiling, and a matched row that still
cannot be numbered returns an explicit error instead of a silent empty result. N06 passes in all five runs of the
re-measurement. The first treatment's result files are in `raw/rowquery-before-cap-fix/` and are not the
measurement of record; the re-measurement above is, on the fixed build.

## Caveats

- Five runs per configuration, one corpus, one account. The control's range is 42 to 44 and the treatment's 45 to
  47; the conclusion rests on those not overlapping, not on the size of the mean alone.
- **The control mean is 43.0, and the frozen five-run mean for the same configuration was 40.2.** Same flags,
  same model, one day apart, so the difference is the environment: a quieter account or a provider serving better.
  This is why the comparison is against a same-day control and not against the frozen number.
- The treatment window carries eleven `provider_unavailable` degradations against the control's zero of that
  kind. They understate the treatment, not overstate it, and they are the reason three per-question moves are
  negative by one run.
- `PR1` and `PR4` are known-imperfect probes; the hand-off says their results should not decide anything alone.
  They did not.
- The audit call counts come from the tool's own records, and one question can call the tool more than once.
- The tool remains off by default in code. The development deployment has it on; a different corpus measures
  differently, and the opt-in is deliberate.

## Acceptance probes on the live build

Captured against the deployed code after the measurement: interface 8 rights, activity 8, access 4, stream 4, all
passing. Outputs are in `probes/`.

## Evidence

- `raw/control/run-1.json` to `run-5.json`: the five control suite runs.
- `raw/rowquery/run-1.json` to `run-5.json`: the five treatment suite runs of record.
- `raw/rowquery-before-cap-fix/`: the first treatment, kept because the section above refers to it.
- `compare.py`: recomputes the totals, ranges, degraded counts, category table and per-question counts from
  `raw/control` and `raw/rowquery`. `python3 docs/reviews/query-tool-2026-10-19/compare.py`.

## Decision

The tool stays on for the development deployment, because the comparison is a 3.6-point gain with non-overlapping
ranges and it removes the unsupported-number failures. It stays off by default in code: a deployment opts in with
`OI_TOOL_QUERY=on` after measuring its own corpus, and the flag is the rollback. The known limits are the tool's
error rate on guessed values (13 of 53 calls found no candidate) and the honesty trade the loop already had,
which this change does not worsen (unanswerable 6.8 to 7.2) and does not fix.
