# Candidate validation, 2026-10-19

Mechanical validation of the 26 candidates authored on `feat/regional-fallback` (commit
`da08258`) against the deployed `chunk_index` (539 documents, 100,478 chunks). The validator is `scripts/validate_data_questions.py`; the
accepted questions are in `docs/eval/data-questions-wider.yaml`.

    docker compose -f deploy/office/compose/docker-compose.yml --env-file rendered/env \
      run -T --rm --no-deps --entrypoint python -v /tmp/wider:/w2:ro -w /w2 \
      eval /w2/validate_data_questions.py /w2/data-questions-wider.yaml

The eval service supplies `DATABASE_URL` from `rendered/env`; `/tmp/wider` held the script and
the wider YAML on the box. The same validator run against a candidates-only file returned 13 passed
and 13 failed, which is the split below.

Per question the validator checks: the id is unique; every expected and forbidden document exists in
`chunk_index`; every `contains` value is in the named document or in a verified source quote
(alphanumeric, lowercased); `source_quote` is in the index, with `" ... "` read as an omitted row or
a join between passages and an arithmetic quote read as operands before `"="` plus the stated result;
and an `absent_pattern` matches zero chunks.

## Verdicts

| Candidate | Category | Source document | Verdict | Reason |
|---|---|---|---|---|
| CAND_AGGREGATION_01 | aggregation | `company/group-02/770592.csv` | REJECT | `39,047` is not inside `source_quote` and not in the document; the quote carries the three operands, not the sum. |
| CAND_AGGREGATION_02 | aggregation | `company/group-02/770592.csv` | REJECT | `3,821` is not inside `source_quote`; the document contains the string, but the quote states the two operands, not the difference. |
| CAND_AGGREGATION_03 | aggregation | `company/group-02/770592.csv` | REJECT | `39,821` is not inside `source_quote` and not in the document. |
| CAND_AGGREGATION_04 | aggregation | `company/group-01/970969.csv` | REJECT | `222,108` is not inside `source_quote` and not in the document. |
| CAND_AGGREGATION_05 | aggregation | `company/group-01/970969.csv` | REJECT | `18,825` is not inside `source_quote` and not in the document. |
| CAND_AGGREGATION_06 | aggregation | `company/group-01/970969.csv` | REJECT | `284,433` is not inside `source_quote` and not in the document. |
| CAND_AGGREGATION_07 | aggregation | `company/group-01/468293.csv` | REJECT | `86,294` is not inside `source_quote`; the document contains the string, but the quote states the three age rows, not the sum. |
| CAND_AGGREGATION_08 | aggregation | `company/group-02/159927.xls` | REJECT | `9,076,788` is not inside `source_quote` and not in the document. |
| CAND_date_arithmetic_01 | date_arithmetic | `company/group-02/545688.doc` | REJECT | `21` is not inside `source_quote`; the quote states the two dates, not the interval. |
| CAND_date_arithmetic_02 | date_arithmetic | `company/group-01/012148.doc` | PASS | `January 7, 2002` is in the quote and the document, and all four dates are quoted. |
| CAND_date_arithmetic_03 | date_arithmetic | `company/apple-10k-2024.htm` | REJECT | `3 days` is not inside `source_quote` and not in the document; the quote states the effective and filing dates, not the interval. |
| CAND_date_arithmetic_04 | date_arithmetic | `company/irs-p15.pdf` | REJECT | `8` is not inside `source_quote`; the document contains the digit, but the quote states the two deadlines, not the interval. |
| CAND_multi_hop_01 | multi_hop | `company/group-02/069856.doc`, `company/group-02/545688.doc` | REJECT | `11` is not inside `source_quote`; the quote states three years and eight years, not the sum. |
| CAND_multi_hop_02 | multi_hop | `company/group-02/069856.doc`, `company/group-02/173609.doc` | REJECT | `3` is not inside `source_quote`; the quote states eight years and five years, not the difference. |
| CAND_multi_hop_03 | multi_hop | `company/apple-ex10-1-2024.htm`, `company/apple-10k-2024.htm` | PASS | `four years` is in the quote and in the Apple 10-K, and both named documents exist. |
| CAND_multi_hop_04 | multi_hop | `company/apple-10k-2024.htm`, `company/group-01/acurx-pharmaceuticals-inc__S-1__2026-09-08.htm` | PASS | `24` is in the quote (`24 months`) and both named documents exist. |
| CAND_UNTOUCHED_01 | coverage | `company/group-02/592778.doc` | PASS | Quote and `6117.10.1010` found in the named document. |
| CAND_UNTOUCHED_02 | coverage | `company/group-01/251719.doc` | PASS | Quote and `six commercial poultry farms` found in the named document. |
| CAND_UNTOUCHED_03 | coverage | `company/group-02/003549.txt` | PASS | Quote and both values found in the named document. |
| CAND_UNTOUCHED_04 | coverage | `company/group-02/228801.txt` | PASS | Quote and `OR-205` found in the named document. |
| CAND_UNTOUCHED_05 | coverage | `company/group-02/059190.csv` | PASS | Quote and `552,109` found in the named document. |
| CAND_UNTOUCHED_06 | coverage | `company/group-02/016886.xls` | PASS | Quote and `7,000` found in the named document. |
| CAND_UNTOUCHED_07 | coverage | `company/apple-10k-2024.htm` | PASS | Quote and `164,000` found in the named document. |
| CAND_UNTOUCHED_08 | coverage | `company/irs-p15.pdf` | PASS | Quote and `176,100` found in the named document. |
| CAND_UNTOUCHED_09 | coverage | `company/canary-pii.md` | PASS | Quote and `vendor record` found; the question asks for the storage location, not the redacted number. |
| CAND_UNTOUCHED_10 | coverage | `company/group-01/380391.rtf` | PASS | Quote and the safety-hood phrase found in the named document. |

## Totals

| Axis | Offered | Kept | Rejected |
|---|---:|---:|---:|
| aggregation | 8 | 0 | 8 |
| date_arithmetic | 4 | 1 | 3 |
| multi_hop | 4 | 2 | 2 |
| coverage | 10 | 10 | 0 |
| **Total** | **26** | **13** | **13** |

Thirteen questions were added to the wider set. Their ids did not collide with the live ids or the
four runner probes, so no id was rewritten, and their `source_quote` and `evidence` fields are
retained.

## Why the thirteen were rejected

Every rejection is the same mechanical failure: the expected value is an arithmetic result and is not
stated in `source_quote`. Seven of the thirteen are also absent from the named document; the other
six appear there only as unrelated strings, which is why the document check alone would not have
caught them. Candidate rule 2 in the candidate README at `da08258` says the `contains` value must
appear in the named document, and the validator applies the same rule to the quote. A computed answer does not
satisfy either test even when the arithmetic in the evidence file is sound.

That is a rule limitation, not a finding that the arithmetic is wrong. The eight aggregation
candidates, three date-arithmetic candidates and two multi-hop candidates could be admitted by a
derivation-aware check that verifies an equation and its operands, or by rewriting each expected
value as a literal passage. Neither is part of this change.

## Baseline issue found in the live forty

The forty live questions pass except U05. Its `absent_pattern` `"cafeteria"` matches nine chunks in
the current index: seven in IRS Publication 15's cafeteria-plan tax text and two in a Minnesota
school pesticide table's `Cafeteria and kitchen` cell. No menu is indexed, so the question is
substantively unanswerable, but the suite's own rule says an `absent_pattern` must match zero chunks.

U05 is left unchanged in the wider file because the forty are the frozen instrument for this change.
The validator therefore exits 1 with `52 passed, 1 failed`. Replacing U05 is a separate edit to
`docs/eval/data-questions.yaml`, not something this branch should do quietly.

## Where the candidates came from

`docs/eval/candidates/` is not on `main`; the candidate YAMLs and evidence were read from commit
`da08258` on `feat/regional-fallback`. This branch records the verdicts and the accepted questions,
and does not copy the candidate sources. `pollution-probes.md` is probe material for a different
change and was not considered for the wider set.