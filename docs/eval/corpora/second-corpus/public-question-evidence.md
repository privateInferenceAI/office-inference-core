# Public corpus questions: evidence

Twelve questions for the public half of the second evaluation corpus. Every expected value
was read from extracted document text, and every quote below was checked with an exact-string
search. No model was run, no network was used, and nothing outside `/tmp/second-corpus` was
modified.

- Questions: `/tmp/second-corpus/questions/public.yaml`
- Corpus: `/tmp/second-corpus/public` (40 files, 30 company + 10 government)
- Extracted text: `/tmp/second-corpus/questions/text/` (one `.txt` per document)
- Validator: `python3 validate.py` (12 questions, ids unique, docs resolve, quotes match)

## Document ids

The ids in `public.yaml` are relative to the documents root and use the requested scheme:

```
company/second-public/company/<issuer>/<filename>
company/second-public/government/<agency>/<filename>
```

The source file on disk is the same path with `company/second-public/` removed, under
`/tmp/second-corpus/public`.

## Extraction and verification tooling

| Tool | Version | Use |
|---|---|---|
| Python | 3.10.12 | extraction and verification |
| `html.parser` (stdlib) | — | HTML text; table cells joined with a space-pipe-space fence |
| `pdftotext` (poppler) | 22.02.0 | PDF text; default reading order, no `-layout` |
| `pypdf` | 6.19.0 | available as a fallback; not needed |
| PyYAML | 6.0.3 | parsing `public.yaml` in the validator |

**HTML.** `extract.py` walks the document with stdlib `html.parser`, skips `script` and
`style`, starts a new line at block elements and at `tr`, and puts `" | "` around `td` and
`th`. It then collapses whitespace and drops empty lines. That keeps a table row readable and
makes an exact quote check meaningful.

**PDF.** `pdftotext -enc UTF-8` without `-layout`. The GAO report is two-column; `-layout`
interleaves the left and right columns, which produced text like "The January 2024 National
Defense The Department of Defense..." on one line. The default reading order keeps each column
together. PDF line breaks remain, so `verify.py` falls back to a whitespace-normalized search
and reports the original line span.

**Verification.** `verify.py` first looks for the literal string on one extracted-text line.
If that fails, it collapses whitespace in the whole document and searches again, reporting the
line span. `verify.py --lines START-END` prints a contiguous block for table rows that the
HTML extractor split across lines.

## Summary

| Id | Category | Document(s) | Expected |
|---|---|---|---|
| N01 | exact_number | Target 2025 10-K | Net sales 104,780 (USD millions) |
| N02 | exact_number | Caterpillar 2025 10-K | Construction Industries profit 4,675 (USD millions) |
| N03 | exact_number | DaVita Q2 2026 10-Q | Net income attributable 462,927 (USD thousands) |
| N04 | exact_number | C.H. Robinson Q2 2026 10-Q | Total revenues 4,934,098 (USD thousands) |
| P01 | contract_clause | Intuit January 2026 credit agreement | Maximum Leverage Ratio 4.00 to 1.00 |
| P02 | prose_fact | HCA 2025 10-K | Hurricanes Helene and Milton |
| P03 | government_finding | GAO-25-107283 | Little visibility into origin of defense goods |
| X01 | cross_document | Caterpillar 10-K and Union Pacific 10-K | 43,079 (USD millions) |
| X02 | cross_document | C.H. Robinson 10-Q and DaVita 10-Q | 1,977,400 (USD thousands) |
| D01 | date | Xcel deferred compensation plan | August 1, 2026 |
| L01 | long_document | Caterpillar credit agreement | August 27, 2029 |
| L02 | long_document | Duke Energy 2025 10-K | 600 (USD millions) |

Coverage: four table numbers, three prose/contract/government passages, two cross-document
arithmetic questions, one date, two long-document needles.

---

## N01 — Target fiscal 2025 net sales

- **Doc id:** `company/second-public/company/target-corp/10-K_2026-03-11_annual-report.htm`
- **Source file:** `/tmp/second-corpus/public/company/target-corp/10-K_2026-03-11_annual-report.htm`
- **Extracted text:** `/tmp/second-corpus/questions/text/company__target-corp__10-K_2026-03-11_annual-report.htm.txt`
- **Extraction:** `html.parser`; `extract.py`
- **Locator:** Consolidated Statements of Operations, fiscal 2025 column, line 1122
  (repeated in the MD&A summary at line 1340)

Exact quote:

> Net sales | $ | 104,780 | $ | 106,566 | $ | 107,412

The preceding line is `(millions, except per share data) | 2025 | 2024 | 2023`, so the
first value is fiscal 2025 net sales in USD millions.

Verify:

```
$ cd /tmp/second-corpus/questions
$ python3 verify.py company/target-corp/10-K_2026-03-11_annual-report.htm \
    'Net sales | $ | 104,780 | $ | 106,566 | $ | 107,412'
OK company/target-corp/10-K_2026-03-11_annual-report.htm:1122: Net sales | $ | 104,780 | $ | 106,566 | $ | 107,412
OK company/target-corp/10-K_2026-03-11_annual-report.htm:1340: Net sales | $ | 104,780 | $ | 106,566 | $ | 107,412
```

## N02 — Caterpillar Construction Industries segment profit

- **Doc id:** `company/second-public/company/caterpillar-inc/10-K_2026-02-13_annual-report.htm`
- **Source file:** `/tmp/second-corpus/public/company/caterpillar-inc/10-K_2026-02-13_annual-report.htm`
- **Extracted text:** `/tmp/second-corpus/questions/text/company__caterpillar-inc__10-K_2026-02-13_annual-report.htm.txt`
- **Extraction:** `html.parser`; `extract.py`
- **Locator:** Profit (Loss) by Segment table, 2025 column, line 691. The row sits under
  `Profit (Loss) by Segment` and `(Millions of dollars) | 2025 | 2024 | $ Change | % Change`.
  The same figure is stated in prose at line 716.

Exact quote:

> Construction Industries | $ | 4,675 | $ | 6,165 | $ | (1,490) | (24 | %)

The prose confirmation at line 716:

> Construction Industries’ profit was $4.675 billion in 2025, a decrease of $1.490 billion, or 24 percent, compared with $6.165 billion in 2024.

Verify:

```
$ python3 verify.py company/caterpillar-inc/10-K_2026-02-13_annual-report.htm \
    'Construction Industries | $ | 4,675 | $ | 6,165 | $ | (1,490) | (24 | %)'
OK company/caterpillar-inc/10-K_2026-02-13_annual-report.htm:691: Construction Industries | $ | 4,675 | $ | 6,165 | $ | (1,490) | (24 | %)
```

## N03 — DaVita net income attributable, six months

- **Doc id:** `company/second-public/company/davita-inc/10-Q_2026-08-04_quarterly-report.htm`
- **Source file:** `/tmp/second-corpus/public/company/davita-inc/10-Q_2026-08-04_quarterly-report.htm`
- **Extracted text:** `/tmp/second-corpus/questions/text/company__davita-inc__10-Q_2026-08-04_quarterly-report.htm.txt`
- **Extraction:** `html.parser`; `extract.py`
- **Locator:** Consolidated Statements of Income, six months ended June 30, 2026 column,
  line 108 (repeated at line 459). The table header is
  `Three months ended June 30, | Six months ended June 30,` and `2026 | 2025 | 2026 | 2025`.

Exact quote:

> Net income attributable to DaVita Inc. | $ | 265,397 | $ | 199,337 | $ | 462,927 | $ | 362,254

The question asks for the third value, the six-month 2026 column.

Verify:

```
$ python3 verify.py company/davita-inc/10-Q_2026-08-04_quarterly-report.htm \
    'Net income attributable to DaVita Inc. | $ | 265,397 | $ | 199,337 | $ | 462,927 | $ | 362,254'
OK company/davita-inc/10-Q_2026-08-04_quarterly-report.htm:108: Net income attributable to DaVita Inc. | $ | 265,397 | $ | 199,337 | $ | 462,927 | $ | 362,254
OK company/davita-inc/10-Q_2026-08-04_quarterly-report.htm:459: Net income attributable to DaVita Inc. | $ | 265,397 | $ | 199,337 | $ | 462,927 | $ | 362,254
```

## N04 — C.H. Robinson total revenues, three months

- **Doc id:** `company/second-public/company/c-h-robinson-worldwide-inc/10-Q_2026-07-31_quarterly-report.htm`
- **Source file:** `/tmp/second-corpus/public/company/c-h-robinson-worldwide-inc/10-Q_2026-07-31_quarterly-report.htm`
- **Extracted text:** `/tmp/second-corpus/questions/text/company__c-h-robinson-worldwide-inc__10-Q_2026-07-31_quarterly-report.htm.txt`
- **Extraction:** `html.parser`; `extract.py`
- **Locator:** Condensed Consolidated Statements of Operations and Comprehensive Income,
  three months ended June 30, 2026 column, line 149 (repeated at line 771). The table header
  is `Three Months Ended June 30, | Six Months Ended June 30,` and `2026 | 2025 | 2026 | 2025`.

Exact quote:

> Total revenues | 4,934,098 | 4,136,543 | 8,947,032 | 8,183,283

The question asks for the first value, the three-month 2026 column.

Verify:

```
$ python3 verify.py company/c-h-robinson-worldwide-inc/10-Q_2026-07-31_quarterly-report.htm \
    'Total revenues | 4,934,098 | 4,136,543 | 8,947,032 | 8,183,283'
OK company/c-h-robinson-worldwide-inc/10-Q_2026-07-31_quarterly-report.htm:149: Total revenues | 4,934,098 | 4,136,543 | 8,947,032 | 8,183,283
OK company/c-h-robinson-worldwide-inc/10-Q_2026-07-31_quarterly-report.htm:771: Total revenues | 4,934,098 | 4,136,543 | 8,947,032 | 8,183,283
```

## P01 — Intuit maximum Leverage Ratio

- **Doc id:** `company/second-public/company/intuit-inc/EX-10_2026-01-12_credit-agreement.htm`
- **Source file:** `/tmp/second-corpus/public/company/intuit-inc/EX-10_2026-01-12_credit-agreement.htm`
- **Extracted text:** `/tmp/second-corpus/questions/text/company__intuit-inc__EX-10_2026-01-12_credit-agreement.htm.txt`
- **Extraction:** `html.parser`; `extract.py`
- **Locator:** Section 6.06 Maximum Leverage Ratio, line 863. The agreement is dated
  January 9, 2026 at line 8, and the defined Effective Date is January 9, 2026 at line 317.

Exact quote (the first sentence, before the acquisition proviso):

> The Company will not permit, as of the last day of any period of four consecutive fiscal quarters of the Company for which the financial statements have been, or are required to be, delivered pursuant to Section 5.08(a) or 5.08(b), the Leverage Ratio to be greater than 4.00 to 1.00

The sentence continues with a `provided that` clause allowing a 0.50 increase for four
quarters after a Qualified Material Acquisition, up to two times. The question asks for the
base ratio, so the answer is 4.00 to 1.00.

Verify:

```
$ python3 verify.py company/intuit-inc/EX-10_2026-01-12_credit-agreement.htm \
    'The Company will not permit, as of the last day of any period of four consecutive fiscal quarters of the Company for which the financial statements have been, or are required to be, delivered pursuant to Section 5.08(a) or 5.08(b), the Leverage Ratio to be greater than 4.00 to 1.00'
OK company/intuit-inc/EX-10_2026-01-12_credit-agreement.htm:863: SECTION 6.06. Maximum Leverage Ratio. The Company will not permit, as of the last day of any period of four consecutive fiscal quarters ... the Leverage Ratio to be greater than 4.00 to 1.00; provided that ... (abridged; the full clause continues with the Qualified Material Acquisition proviso)
```

## P02 — HCA hurricanes and the USD 250 million charge

- **Doc id:** `company/second-public/company/hca-healthcare-inc/10-K_2026-02-10_annual-report.htm`
- **Source file:** `/tmp/second-corpus/public/company/hca-healthcare-inc/10-K_2026-02-10_annual-report.htm`
- **Extracted text:** `/tmp/second-corpus/questions/text/company__hca-healthcare-inc__10-K_2026-02-10_annual-report.htm.txt`
- **Extraction:** `html.parser`; `extract.py`
- **Locator:** MD&A results-of-operations paragraph, line 1122 (the same paragraph appears
  again at line 1649).

Exact quote:

> The 2024 results also include additional expenses and losses of revenues estimated at approximately $250 million, or $0.73 per diluted share, related to Hurricanes Helene and Milton, which impacted our facilities in North Carolina and certain facilities in Florida.

Verify:

```
$ python3 verify.py company/hca-healthcare-inc/10-K_2026-02-10_annual-report.htm \
    'The 2024 results also include additional expenses and losses of revenues estimated at approximately $250 million, or $0.73 per diluted share, related to Hurricanes Helene and Milton, which impacted our facilities in North Carolina and certain facilities in Florida.'
OK company/hca-healthcare-inc/10-K_2026-02-10_annual-report.htm:1122: Net income attributable to HCA Healthcare, Inc. totaled $6.784 billion ... The 2024 results also include additional expenses and losses of revenues estimated at approximately $250 million, or $0.73 per diluted share, related to Hurricanes Helene and Milton, which impacted our facilities in North Carolina and certain facilities in Florida. ...
OK company/hca-healthcare-inc/10-K_2026-02-10_annual-report.htm:1649: Net income attributable to HCA Healthcare, Inc. totaled $6.784 billion ... The 2024 results also include additional expenses and losses of revenues estimated at approximately $250 million, or $0.73 per diluted share, related to Hurricanes Helene and Milton, which impacted our facilities in North Carolina and certain facilities in Florida. ...
```

## P03 — GAO finding on the primary procurement database

- **Doc id:** `company/second-public/government/gao/gao-25-107283-defense-industrial-base.pdf`
- **Source file:** `/tmp/second-corpus/public/government/gao/gao-25-107283-defense-industrial-base.pdf`
- **Extracted text:** `/tmp/second-corpus/questions/text/government__gao__gao-25-107283-defense-industrial-base.pdf.txt`
- **Extraction:** `pdftotext -enc UTF-8` (default reading order, no `-layout`)
- **Locator:** GAO-25-107283, "What GAO Found" on the Highlights page, extracted lines 30-32

Exact quote:

> The primary procurement database for the federal government, however, provides little visibility into where these goods are manufactured or whether materials and parts suppliers are domestic or foreign.

The PDF breaks this sentence across three lines. The normalized sentence matches exactly.
`-layout` was not used because the report is two-column and `-layout` interleaves the
columns.

Verify:

```
$ python3 verify.py government/gao/gao-25-107283-defense-industrial-base.pdf \
    'The primary procurement database for the federal government, however, provides little visibility into where these goods are manufactured or whether materials and parts suppliers are domestic or foreign.'
OK government/gao/gao-25-107283-defense-industrial-base.pdf:30-32 (whitespace-normalized): The primary procurement database for the federal government, however, provides little visibility into where these goods are manufactured or whether materials and parts suppliers are domestic or foreign.
```

## X01 — Caterpillar minus Union Pacific, 2025

- **Doc ids:**
  - `company/second-public/company/caterpillar-inc/10-K_2026-02-13_annual-report.htm`
  - `company/second-public/company/union-pacific-corp/10-K_2026-02-06_annual-report.htm`
- **Source files:**
  - `/tmp/second-corpus/public/company/caterpillar-inc/10-K_2026-02-13_annual-report.htm`
  - `/tmp/second-corpus/public/company/union-pacific-corp/10-K_2026-02-06_annual-report.htm`
- **Extraction:** `html.parser`; `extract.py`
- **Locators:** Caterpillar Consolidated Results of Operations, line 1316; Union Pacific
  Consolidated Statements of Income, line 934. Both years end December 31, 2025.

Exact quotes:

> Caterpillar: Total sales and revenues | 67,589 | 64,809 | 67,060
>
> Union Pacific: Total operating revenues | 24,510 | 24,250 | 24,119

Arithmetic:

```
67,589 - 24,510 = 43,079
```

Both figures are in USD millions. The difference is USD 43,079 million.

Verify:

```
$ python3 verify.py company/caterpillar-inc/10-K_2026-02-13_annual-report.htm \
    'Total sales and revenues | 67,589 | 64,809 | 67,060'
OK company/caterpillar-inc/10-K_2026-02-13_annual-report.htm:1316: Total sales and revenues | 67,589 | 64,809 | 67,060

$ python3 verify.py company/union-pacific-corp/10-K_2026-02-06_annual-report.htm \
    'Total operating revenues | 24,510 | 24,250 | 24,119'
OK company/union-pacific-corp/10-K_2026-02-06_annual-report.htm:934: Total operating revenues | 24,510 | 24,250 | 24,119
```

## X02 — C.H. Robinson minus DaVita, six months 2026

- **Doc ids:**
  - `company/second-public/company/c-h-robinson-worldwide-inc/10-Q_2026-07-31_quarterly-report.htm`
  - `company/second-public/company/davita-inc/10-Q_2026-08-04_quarterly-report.htm`
- **Source files:**
  - `/tmp/second-corpus/public/company/c-h-robinson-worldwide-inc/10-Q_2026-07-31_quarterly-report.htm`
  - `/tmp/second-corpus/public/company/davita-inc/10-Q_2026-08-04_quarterly-report.htm`
- **Extraction:** `html.parser`; `extract.py`
- **Locators:** C.H. Robinson statement of operations, six months ended June 30, 2026
  column, line 149; DaVita statement of income, same column, line 93.

Exact quotes:

> C.H. Robinson: Total revenues | 4,934,098 | 4,136,543 | 8,947,032 | 8,183,283
>
> DaVita: Total revenues | 3,554,084 | 3,379,526 | 6,969,632 | 6,603,055

Arithmetic:

```
8,947,032 - 6,969,632 = 1,977,400
```

Both figures are in USD thousands, and both cover the six months ended June 30, 2026.
The difference is USD 1,977,400 thousand.

Verify:

```
$ python3 verify.py company/c-h-robinson-worldwide-inc/10-Q_2026-07-31_quarterly-report.htm \
    'Total revenues | 4,934,098 | 4,136,543 | 8,947,032 | 8,183,283'
OK company/c-h-robinson-worldwide-inc/10-Q_2026-07-31_quarterly-report.htm:149: Total revenues | 4,934,098 | 4,136,543 | 8,947,032 | 8,183,283

$ python3 verify.py company/davita-inc/10-Q_2026-08-04_quarterly-report.htm \
    'Total revenues | 3,554,084 | 3,379,526 | 6,969,632 | 6,603,055'
OK company/davita-inc/10-Q_2026-08-04_quarterly-report.htm:93: Total revenues | 3,554,084 | 3,379,526 | 6,969,632 | 6,603,055
```

## D01 — Xcel 2026 restatement effective date

- **Doc id:** `company/second-public/company/xcel-energy-inc/EX-10_2026-07-30_deferred-compensation-plan.htm`
- **Source file:** `/tmp/second-corpus/public/company/xcel-energy-inc/EX-10_2026-07-30_deferred-compensation-plan.htm`
- **Extracted text:** `/tmp/second-corpus/questions/text/company__xcel-energy-inc__EX-10_2026-07-30_deferred-compensation-plan.htm.txt`
- **Extraction:** `html.parser`; `extract.py`
- **Locator:** Section 1.2.12 Effective Date, line 50. The cover page also states
  "restated effective January 1, 2002, January 1, 2009, and August 1, 2026" at line 10.

Exact quote:

> The Effective Date of this Restatement is August 1, 2026, except as otherwise provided herein.

Verify:

```
$ python3 verify.py company/xcel-energy-inc/EX-10_2026-07-30_deferred-compensation-plan.htm \
    'The Effective Date of this Restatement is August 1, 2026'
OK company/xcel-energy-inc/EX-10_2026-07-30_deferred-compensation-plan.htm:50: 1.2.12Effective Date – January 1, 2002. The Effective Date of this Restatement is August 1, 2026, except as otherwise provided herein.
```

## L01 — Caterpillar Current Termination Date

- **Doc id:** `company/second-public/company/caterpillar-inc/EX-10_2026-09-01_credit-agreement.htm`
- **Source file:** `/tmp/second-corpus/public/company/caterpillar-inc/EX-10_2026-09-01_credit-agreement.htm`
- **Extracted text:** `/tmp/second-corpus/questions/text/company__caterpillar-inc__EX-10_2026-09-01_credit-agreement.htm.txt`
- **Extraction:** `html.parser`; `extract.py`
- **Locator:** Definitions, Current Termination Date, lines 749-751. The agreement cover is
  dated August 27, 2026 at line 12.

Exact quote (extraction line breaks removed):

> “Current Termination Date” means, with respect to any Bank at any time, August 27, 2029, or such later date to which the “Current Termination Date” shall then have been extended with the consent of such Bank pursuant to Section 2.16.

The date is three years after the agreement date, which is consistent with the
"Three-Year Facility" title but is stated explicitly in the definition.

Verify:

```
$ python3 verify.py company/caterpillar-inc/EX-10_2026-09-01_credit-agreement.htm \
    '“Current Termination Date” means, with respect to any Bank at any time, August 27, 2029, or such later date to which the “Current Termination Date” shall then have been extended with the consent of such Bank pursuant to Section 2.16.'
OK company/caterpillar-inc/EX-10_2026-09-01_credit-agreement.htm:749-751 (whitespace-normalized): “Current Termination Date” means, with respect to any Bank at any time, August 27, 2029, or such later date to which the “Current Termination Date” shall then have been extended with the consent of such Bank pursuant to Section 2.16.
```

## L02 — Duke Energy Carolinas December 2026 maturity

- **Doc id:** `company/second-public/company/duke-energy-corp/10-K_2026-02-26_annual-report.htm`
- **Source file:** `/tmp/second-corpus/public/company/duke-energy-corp/10-K_2026-02-26_annual-report.htm`
- **Extracted text:** `/tmp/second-corpus/questions/text/company__duke-energy-corp__10-K_2026-02-26_annual-report.htm.txt`
- **Extraction:** `html.parser`; `extract.py`
- **Locator:** Current Maturities of Long-Term Debt table, First Mortgage Bonds section,
  lines 6180-6183. The table header at line 6172 is
  `(in millions) | Maturity Date | Interest Rate | December 31, 2025`.

Exact quote (the extractor split the row across four lines because of nested table markup):

> First Mortgage Bonds
>
> Duke Energy Carolinas
>
> December 2026
>
> 2.950 | % | 600

The amount is in USD millions, so the answer is USD 600 million. The table is the
"Current Maturities of Long-Term Debt" table, so the question's "classified as a current
maturity" is carried by the table heading.

Verify:

```
$ python3 verify.py --lines 6180-6183 company/duke-energy-corp/10-K_2026-02-26_annual-report.htm
company/duke-energy-corp/10-K_2026-02-26_annual-report.htm:6180: First Mortgage Bonds
company/duke-energy-corp/10-K_2026-02-26_annual-report.htm:6181: Duke Energy Carolinas
company/duke-energy-corp/10-K_2026-02-26_annual-report.htm:6182: December 2026
company/duke-energy-corp/10-K_2026-02-26_annual-report.htm:6183: 2.950 | % | 600
```

---

## Validation

Run from `/tmp/second-corpus/questions`:

```
$ python3 validate.py
questions: 12  ids: N01, N02, N03, N04, P01, P02, P03, X01, X02, D01, L01, L02
categories: {'exact_number': 4, 'contract_clause': 1, 'prose_fact': 1, 'government_finding': 1, 'cross_document': 2, 'date': 1, 'long_document': 2}
...
OK all 12 questions validated against extracted text
```

The validator checks that every doc id resolves to a file under
`/tmp/second-corpus/public`, that every `source_quote` occurs in its extracted text
(whitespace-normalized where needed), and that every `contains` value occurs in the quote
or the arithmetic line. It also recomputes the X01 and X02 subtractions.

## Dropped candidates and reasons

1. **Kroger 10-Q quarterly sales.** The HTML parser put every cell on its own line for this
   filing (for example `Sales`, `$`, `34,621`, `$`, `33,940`), so a table-row quote would
   have been an unreadable vertical stack. DaVita and C.H. Robinson were used instead; their
   income statements extract as readable rows.
2. **Caterpillar total commitment of USD 3,000,000,000.** The Commitments schedule splits the
   total row into `Total`, `$ | 3,000,000,000.00`, and `$ | 2,775,000,000.00`. The figure is
   correct, but the answer is brittle to normalization ("USD 3 billion" versus
   "USD 3,000,000,000"). Replaced with L01, the Current Termination Date, which has a textual
   answer and is still buried in the definitions of the same agreement.
3. **Target versus Caterpillar revenue comparison.** Target's fiscal 2025 ends January 31,
   2026; Caterpillar's 2025 ends December 31, 2025. The subtraction is valid but the periods
   are not like-for-like, so the cross-document question uses Caterpillar and Union Pacific,
   both December 31, 2025.
4. **Deere performance stock unit forfeiture clause.** Considered for the equity-plan version
   of the contract question. Dropped in favor of the Intuit credit agreement, which gives a
   numeric covenant and covers the "credit agreement" side of the requirement. The Deere
   document remains in the corpus and is a good candidate for a future clause question.
5. **HCA hospital count.** Considered as the large-10-K needle ("At December 31, 2025, we
   operated 190 hospitals..."). Dropped because the sentence is in the opening Item 1 summary,
   not buried deep in the document. The Duke current-maturity row is deeper and more specific.
6. **Image-only questions.** None were proposed. All 40 files are text-bearing HTML or PDF per
   the corpus README; every answer above was found in extracted text. No OCR, image reading,
   or visual table parsing was used.

## Reproduce

```
$ cd /tmp/second-corpus/questions
$ python3 extract.py          # writes text/ for all 40 documents
$ python3 validate.py         # checks public.yaml against text/
$ python3 verify.py ...        # per-question exact-string checks above
```

The extraction and verification scripts are in `/tmp/second-corpus/questions/`
(`extract.py`, `verify.py`, `validate.py`). They read only the corpus and write only
under `/tmp/second-corpus/questions/`.
