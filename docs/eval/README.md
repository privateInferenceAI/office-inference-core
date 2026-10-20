# The data question set

Forty questions the system must answer from the group-01/02/03 corpus, in `data-questions.yaml`. It exists so the
data-specific acceptance pass tests retrieval, citation and access against facts read from the documents rather
than against whatever the system happens to say.

## Why it is written the way it is

Every expected value was read from the ingested source text and the carrying passage is quoted in `source_quote`.
No expected answer was written by looking at an assistant answer. That is the difference between testing the system
and testing the test.

The corpus is mostly public-domain filings and numeric tables with numeric file names, so some questions name the
document. A scoped question is still a real question an operator asks ("what does this table say"), and it is the
only way to keep a four-table census spreadsheet unambiguous.

## Categories

| Prefix | Kind | Count | What it tests |
|---|---|---|---|
| N | exact number from a table | 8 | table chunking, header folding, numeric fidelity |
| P | prose fact | 8 | retrieval of narrative passages, cited page |
| U | unanswerable | 8 | honesty: say the corpus does not contain it, do not invent, do not cite an unrelated passage |
| X | cross-document | 8 | two documents in one answer, arithmetic over retrieved values |
| A | access boundary | 8 | six executive-only sources must be denied to staff and answered for executive; two company sources must answer for both |

Each unanswerable question carries an `absent_pattern` that was checked against the index and returns zero rows.
If the corpus later grows and a pattern starts matching, the question is no longer unanswerable and must be
replaced, not silently left to fail.

## The five probes

Four probes live in `tests/fixtures/data_suite_run.py`, separate from the forty because they measure a mechanism
whose expected result changes with the chunking or answer-path policy:

| Probe | Measures | Redesigned 2026-10-19 |
|---|---|---|
| PR1 `probe_row_normal` | a pinpoint row in an ordinary table, using a fact no question uses | yes: it had become a duplicate of N07's premise |
| PR2 `probe_row_large` | a pinpoint row in a 41,000-row table | no |
| PR3 `probe_dataset_only` | an honest refusal for a question only an unindexed data dump answers | no |
| PR4 `probe_pollution` | a clean prose count that numeric table dumps compete with | yes: it had become a duplicate of N06 |
| PR5 `probe_redaction` | the PII gate end to end: a correct answer is masked, a leak is not | new |

PR5 matters because the redaction control is only real if a question that asks for a masked value still answers
and comes back masked. Its document holds reserved test values, so a leak is unambiguous.

## How a runner scores it

- `answered`: the system produced an answer grounded in retrieved passages rather than a fallback.
- `docs`: the documents that carry the answer. At least one must appear among the retrieved passages: the
  census tables repeat across several CSV files, so a twin file with different numbers must not be treated as
  the answer, which is why every table question is anchored on a value that appears in exactly one document.
- `contains`: compared case-insensitively after stripping commas, dollar signs, percent signs and spaces, so
  `48,240`, `48240` and `$48,240` are the same answer. All strings in the list must appear.
- `denied` and `forbidden_docs`: no passage from a forbidden document may be retrieved, and the answer must say it
  cannot see that material rather than answer it.
- An answer that contains the right number but wrong arithmetic is a fail; the check is the expected value, not
  the presence of numbers.
- An unanswerable question that gets a confident invented answer fails, even if the sentence sounds plausible.

## Running it

Retrieval half, no model call, fastest and the right first pass:

```
docker compose -f deploy/office/compose/docker-compose.yml --env-file rendered/env \
  run -T --rm --no-deps --entrypoint python -v /opt/oi-core:/w:ro -w /w eval \
  /w/tests/fixtures/ask.py --question "<question>" --role staff --json
```

Answer half, the whole path through the assistant API, as the client would ask it. The panel uses the API so that
the answer, its citations and the audit row are all exercised together.

Two runs per question for category A, one as `staff` and one as `executive`. The other categories run as `staff`
by default and a sample as `executive` to prove the company corpus is readable by both.

### Measurement hygiene

**Never run two suites against one deployment.** That is the rule, and the runner enforces it. On 2026-10-20 the
same configuration scored 53 of 53 on five idle runs and 48 to 51 while a second suite shared the box, with N06 and
N07 failing in the lower runs. Both windows would have been quotable as this configuration's result.

`scripts/run_answer_suite.sh` takes an flock on `/tmp/oi-suite.lock` for its whole life and exits 3 if another
suite holds it. The runner also takes a Postgres advisory lock, so a direct `docker run` of
`tests/fixtures/data_suite_run.py` is refused too. Before the first run the script reads `/proc/loadavg` and
`MemAvailable` and refuses to start above `OI_SUITE_MAX_LOAD` (default 2.0) or below
`OI_SUITE_MIN_AVAILABLE_MB` (default 2048).

Every run records where it ran. The script writes `run-context.json` into the output directory before the first
run, with the commit, the `OI_*` flags the API runs with, the `api` and `eval` image identities, load,
available memory, hostname and instance id. Each run's JSON carries the same record under `context`.
`scripts/acceptance_panel.py` warns, and does not fail, when a context is missing or shows a load above the limit
it was admitted under. A run without that record is a smoke test. The rule is
[0005](../decisions/0005-measurement-hygiene.md).

## Adding to the set

A new question needs four things: the question in a user's words, the expected value or the verified absence, the
document and quoting passage that carries it, and the role behaviour. A question without a source quote does not
go in.
