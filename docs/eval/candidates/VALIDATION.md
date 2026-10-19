# Candidate validation, 2026-10-19

Mechanical validation of the 26 candidates authored on `feat/regional-fallback` (commit
`da08258`) against the deployed `chunk_index` (539 documents, 100,478 chunks). The validator is
`scripts/validate_data_questions.py`; the accepted questions are in
`docs/eval/data-questions-wider.yaml`.

    docker compose -f deploy/office/compose/docker-compose.yml --env-file rendered/env \
      run -T --rm --no-deps --entrypoint python -v /tmp/wider2:/w2:ro -w /w2 \
      eval /w2/validate_data_questions.py /w2/data-questions-wider.yaml

The eval service supplies `DATABASE_URL` from `rendered/env`; `/tmp/wider2` held the script and
the wider YAML on the box. The files need mode 644 because the container does not run as the
login user.

There were two passes. The first admitted only questions whose expected value appears literally
in the source quote: it kept 13 and rejected 13. The second added an optional `derivation` block
to the validator and re-ran the rejected 13. All 13 now pass, so the wider set holds 26
candidates.

Per question the validator checks: the id is unique; every expected and forbidden document
exists in `chunk_index`; every `contains` value is in the named document or in a verified
source quote (alphanumeric, lowercased); `source_quote` is in the index, with `" ... "` read as
an omitted row or a join between passages and an arithmetic quote read as operands before `"="`
plus the stated result; a `derivation`, when present, is grounded and recomputes; and an
`absent_pattern` matches zero chunks.

## Derivation

A question whose answer is computed rather than printed carries:

    derivation:
      operands: ["31,151", "7,452", "444"]
      operation: sum
      result: "39,047"

`operands` are the exact number or date phrases as they appear in the named documents.
`operation` is one of `sum`, `difference`, `latest`, `days-between`, `years-between`.
`result` is the expected `contains` value.

The rules:

- A `contains` value that is not literally in `source_quote` is accepted only when a derivation
  covers it. A question without a derivation gets exactly the checks it got before.
- Every operand must occur in the normalised text of the named documents. The check is the same
  alphanumeric, lowercased comparison used everywhere else, so `533,020` matches `533020`.
- `sum` and `difference` need at least two operands. A difference subtracts all later operands
  from the first, which is how the table arithmetic is written (`533,020 - 95,948 - 71,128 -
  81,511 = 284,433`). The recomputed value must equal `result`.
- `latest` needs date operands and a result that is one of them. The result must be the latest
  of them, not just any of them.
- `days-between` and `years-between` need exactly two date operands. Days are the absolute
  distance between the dates. Years are completed whole years, and `result` must be that whole
  number.
- `result` must be one of the question's expected `contains` values.

Numbers may be written with commas or decimals, or spelled out (`eight years`). Dates may be
written `September 9, 2004`, `30 September 2004` or `8/23/24`; a two-digit year is read as
20xx. Units are not converted, so operands must already share the unit the result is stated in.

The tests in `tests/test_validate_data_questions.py` cover the shape rules, each operation, the
operand grounding, the arithmetic, and the two failure paths. They use the in-memory index: no
database and no network.

## Verdicts

| Candidate | Category | Source document | Verdict | Grounding |
|---|---|---|---|---|
| CAND_AGGREGATION_01 | aggregation | `company/group-02/770592.csv` | KEPT | derivation: 31,151 + 7,452 + 444 = 39,047 |
| CAND_AGGREGATION_02 | aggregation | `company/group-02/770592.csv` | KEPT | derivation: 6,259 - 2,438 = 3,821 |
| CAND_AGGREGATION_03 | aggregation | `company/group-02/770592.csv` | KEPT | derivation: 42,259 - 2,438 = 39,821 |
| CAND_AGGREGATION_04 | aggregation | `company/group-01/970969.csv` | KEPT | derivation: 83,047 + 65,212 + 73,849 = 222,108 |
| CAND_AGGREGATION_05 | aggregation | `company/group-01/970969.csv` | KEPT | derivation: 100,336 - 81,511 = 18,825 |
| CAND_AGGREGATION_06 | aggregation | `company/group-01/970969.csv` | KEPT | derivation: 533,020 - 95,948 - 71,128 - 81,511 = 284,433 |
| CAND_AGGREGATION_07 | aggregation | `company/group-01/468293.csv` | KEPT | derivation: 14,455 + 26,599 + 45,240 = 86,294 |
| CAND_AGGREGATION_08 | aggregation | `company/group-02/159927.xls` | KEPT | derivation: 26,649,830.44 - 17,573,042.44 = 9,076,788 |
| CAND_date_arithmetic_01 | date_arithmetic | `company/group-02/545688.doc` | KEPT | derivation: September 30, 2004 - September 9, 2004 = 21 days |
| CAND_date_arithmetic_02 | date_arithmetic | `company/group-01/012148.doc` | KEPT | literal: `January 7, 2002` in quote and document |
| CAND_date_arithmetic_03 | date_arithmetic | `company/apple-10k-2024.htm` | KEPT | derivation: 8/23/24 - August 20, 2024 = 3 days |
| CAND_date_arithmetic_04 | date_arithmetic | `company/irs-p15.pdf` | KEPT | derivation: February 10, 2026 - February 2, 2026 = 8 days |
| CAND_multi_hop_01 | multi_hop | `company/group-02/069856.doc`, `company/group-02/545688.doc` | KEPT | derivation: three years + eight years = 11 years |
| CAND_multi_hop_02 | multi_hop | `company/group-02/069856.doc`, `company/group-02/173609.doc` | KEPT | derivation: eight years - five years = 3 years |
| CAND_multi_hop_03 | multi_hop | `company/apple-ex10-1-2024.htm`, `company/apple-10k-2024.htm` | KEPT | literal: `four years` in quote and document |
| CAND_multi_hop_04 | multi_hop | `company/apple-10k-2024.htm`, `company/group-01/acurx-pharmaceuticals-inc__S-1__2026-09-08.htm` | KEPT | literal: `24` in quote and document |
| CAND_UNTOUCHED_01 | coverage | `company/group-02/592778.doc` | KEPT | literal: `6117.10.1010` in quote and document |
| CAND_UNTOUCHED_02 | coverage | `company/group-01/251719.doc` | KEPT | literal: `six commercial poultry farms` in quote and document |
| CAND_UNTOUCHED_03 | coverage | `company/group-02/003549.txt` | KEPT | literal: both values in quote and document |
| CAND_UNTOUCHED_04 | coverage | `company/group-02/228801.txt` | KEPT | literal: `OR-205` in quote and document |
| CAND_UNTOUCHED_05 | coverage | `company/group-02/059190.csv` | KEPT | literal: `552,109` in quote and document |
| CAND_UNTOUCHED_06 | coverage | `company/group-02/016886.xls` | KEPT | literal: `7,000` in quote and document |
| CAND_UNTOUCHED_07 | coverage | `company/apple-10k-2024.htm` | KEPT | literal: `164,000` in quote and document |
| CAND_UNTOUCHED_08 | coverage | `company/irs-p15.pdf` | KEPT | literal: `176,100` in quote and document |
| CAND_UNTOUCHED_09 | coverage | `company/canary-pii.md` | KEPT | literal: `vendor record` in quote and document |
| CAND_UNTOUCHED_10 | coverage | `company/group-01/380391.rtf` | KEPT | literal: the safety-hood phrase in quote and document |

## Totals

| Axis | Offered | Kept | Rejected |
|---|---:|---:|---:|
| aggregation | 8 | 8 | 0 |
| date_arithmetic | 4 | 4 | 0 |
| multi_hop | 4 | 4 | 0 |
| coverage | 10 | 10 | 0 |
| **Total** | **26** | **26** | **0** |

The first pass kept 13 and rejected 13. Every rejection was the same mechanical failure: the
expected value was an arithmetic result and was not stated in `source_quote`. Seven of the 13
were also absent from the named document; the other six appeared there only as unrelated
strings, which is why a document check alone would not have caught them. Candidate rule 2 in
the candidate README at `da08258` says the `contains` value must appear in the named document,
and the validator applied the same rule to the quote.

That was a rule limitation, not a finding that the arithmetic was wrong. Each of the 13 now
carries a derivation built from the operands and arithmetic already recorded in its evidence
file, and the validator recomputes the value from the indexed operands. No candidate remains
rejected. No candidate failed for an unindexed operand or an ambiguous operation.

## Validation run

The full wider file returns `66 questions: 65 passed, 1 failed`. Every derived candidate passes.

A control run of the 13 derived candidates with their `derivation` blocks removed returns
`13 questions: 0 passed, 13 failed`, with the first-pass failures reproduced. The derivation is
what admits them, and the plain-literal path is unchanged.

## Baseline issue found in the live forty

The forty live questions pass except U05. Its `absent_pattern` `"cafeteria"` matches nine chunks
in the current index: seven in IRS Publication 15's cafeteria-plan tax text and two in a
Minnesota school pesticide table's `Cafeteria and kitchen` cell. No menu is indexed, so the
question is substantively unanswerable, but the suite's own rule says an `absent_pattern` must
match zero chunks.

U05 is left unchanged in the wider file because the forty are the frozen instrument for this
change. The validator therefore exits 1 with `65 passed, 1 failed`. Replacing U05 is a separate
edit to `docs/eval/data-questions.yaml`, not something this branch should do quietly.

## Where the candidates came from

The candidate YAMLs, evidence files and README are copied from commit `da08258` on
`feat/regional-fallback` with `git checkout da08258 -- docs/eval/candidates/<path>`, so the
passages and SQL behind each verdict sit beside the verdict. The wider YAML is the scored file;
accepted candidates do not move into `docs/eval/data-questions.yaml` because the forty are
frozen. `pollution-probes.md` is probe material for a different change and was not considered
for the wider set.
