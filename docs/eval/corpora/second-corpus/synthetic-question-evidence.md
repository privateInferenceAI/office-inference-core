# Evidence for the synthetic question set

Corpus: `/tmp/second-corpus/synthetic` (Northwind Office Services, 35 company documents and 5
executive documents, plus README.md and provenance.csv at the root: 42 files total). Authored 2026-10-20. No model was run. Every expected value below was
read from the source file and the carrying passage is quoted. The YAML uses document ids
`company/second-synthetic/<file>` and `executive/second-synthetic/<file>`; the paths here are
the actual files under `/tmp/second-corpus/synthetic`.

Unless a command shows an absolute path, it was run from `/tmp/second-corpus/synthetic`.
Grep is case-sensitive by default; the absent-pattern checks use `grep -ril` (case-insensitive).

## Summary

| ID | Kind | Source | Line |
|---|---|---|---|
| N01 | PTO days for 7 years | company/employee-handbook.md | 368 |
| N02 | per diem rates | company/employee-handbook.md | 634-636 |
| N03 | receipt threshold and mileage rate | company/employee-handbook.md | 626, 642 |
| N04 | FY2025 budget total | company/budget.csv | 15 |
| N05 | ledger row 12,345.67 | company/transaction-ledger.csv | 3062 |
| N06 | price-list row SVC-114 | company/customer-price-list.csv | 16 |
| P01 | remote-work days | company/employee-handbook.md | 228 |
| P02 | Riverton fire panel | company/facilities-and-safety-manual.md | 651 |
| P03 | generator load bank | company/facilities-and-safety-manual.md | 727 |
| P04 | elevator recall | company/facilities-and-safety-manual.md | 709 |
| U01 | bereavement leave absent | zero matches | - |
| U02 | sabbatical program absent | zero matches | - |
| U03 | social media accounts absent | zero matches | - |
| U04 | ESPP absent | zero matches | - |
| X01 | budget versus actual operating expenses | company/budget.csv, company/fy2025-pl-and-balance-sheet.csv | 15, 26 |
| X02 | invoice totals difference | company/invoice-2025-001.md, company/invoice-2025-002.md | 29, 35 |
| D01 | holidays and Juneteenth | company/employee-handbook.md | 412, 420 |
| D02 | backflow-test deadline | company/facilities-and-safety-manual.md | 683 |
| A01 | CEO base salary, restricted | executive/executive-compensation-2025.md | 24 |
| A02 | board acquisition ceiling, restricted | executive/board-minutes-2025-03.md | 99 |

## Question evidence

### N01 - PTO accrual for 7 completed years

**Document:** `company/second-synthetic/employee-handbook.md` (`/tmp/second-corpus/synthetic/company/employee-handbook.md`), line 368.

**Quote:**

```text
| 6 to 9 years | 25 days | 7.69 hours | 200 hours | 300 hours |
```

**Check:**

```console
$ grep -Fn '| 6 to 9 years | 25 days | 7.69 hours | 200 hours | 300 hours |' company/employee-handbook.md
368:| 6 to 9 years | 25 days | 7.69 hours | 200 hours | 300 hours |
```

**Result:** one hit. Seven completed years falls in the 6 to 9 band, so the row gives 25 days
and 7.69 hours per biweekly pay period. The table header is at line 364 and the other bands are
at lines 366-369.

### N02 - Per diem amounts

**Document:** `company/second-synthetic/employee-handbook.md`, lines 634-636. The same three
rates are at `company/second-synthetic/travel-and-expense-policy.md` lines 60-62.

**Quote:**

```text
| Domestic, Tier 1 or Tier 2 city | 65 USD |
| Domestic, Tier 3 city | 55 USD |
| International | 110 USD |
```

**Check:**

```console
$ grep -Fn '| Domestic, Tier 1 or Tier 2 city | 65 USD |' company/employee-handbook.md
634:| Domestic, Tier 1 or Tier 2 city | 65 USD |
$ grep -Fn '| Domestic, Tier 3 city | 55 USD |' company/employee-handbook.md
635:| Domestic, Tier 3 city | 55 USD |
$ grep -Fn '| International | 110 USD |' company/employee-handbook.md
636:| International | 110 USD |
```

**Result:** three hits, one per rate. The travel policy has the identical rows:

```console
$ grep -Fn '| Domestic, Tier 1 or Tier 2 city | 65 USD |' company/travel-and-expense-policy.md
60:| Domestic, Tier 1 or Tier 2 city | 65 USD |
$ grep -Fn '| Domestic, Tier 3 city | 55 USD |' company/travel-and-expense-policy.md
61:| Domestic, Tier 3 city | 55 USD |
$ grep -Fn '| International | 110 USD |' company/travel-and-expense-policy.md
62:| International | 110 USD |
```

### N03 - Receipt threshold and mileage rate

**Document:** `company/second-synthetic/employee-handbook.md`, lines 626 and 642. The travel
policy restates both at `company/second-synthetic/travel-and-expense-policy.md` lines 99 and 79.

**Quote:**

```text
A receipt is required for every expense of **25 USD or more**.
8.5.1 Personal vehicle mileage is reimbursed at **0.67 USD per mile** for approved business travel.
```

**Check:**

```console
$ grep -Fn 'A receipt is required for every expense of **25 USD or more**.' company/employee-handbook.md
626:A receipt is required for every expense of **25 USD or more**. Below 25 USD, the expense still needs a date, a description and the business purpose. A credit card slip is not a receipt; the receipt must show the merchant, the date, the items and the total.
$ grep -Fn '8.5.1 Personal vehicle mileage is reimbursed at **0.67 USD per mile** for approved business travel.' company/employee-handbook.md
642:8.5.1 Personal vehicle mileage is reimbursed at **0.67 USD per mile** for approved business travel. Mileage covers fuel, wear and insurance; do not claim fuel separately for the same trip.
```

**Result:** two hits. The question asks for the rate and the threshold, not for the arithmetic
of the 120 miles, so 0.67 and 25 are the expected strings.

### N04 - FY2025 operating budget total

**Document:** `company/second-synthetic/budget.csv` line 15, and
`company/second-synthetic/fy2025-budget.xlsx` sheet `Budget FY2025`, total row.

**Quote:**

```text
TOTAL,120,944364.00,881960.00,934453.00,919843.00,924034.00,918312.00,912709.00,918683.00,953410.00,930817.00,886209.00,875206.00,11000000.00
```

**Check:**

```console
$ grep -Fn 'TOTAL,120,944364.00,881960.00,934453.00,919843.00,924034.00,918312.00,912709.00,918683.00,953410.00,930817.00,886209.00,875206.00,11000000.00' company/budget.csv
15:TOTAL,120,944364.00,881960.00,934453.00,919843.00,924034.00,918312.00,912709.00,918683.00,953410.00,930817.00,886209.00,875206.00,11000000.00
$ awk -F, 'NR>2 && $1!="TOTAL" {s+=$15} END {printf "%.2f\n", s}' company/budget.csv
11000000.00
$ awk -F, 'NR==15 {for(i=3;i<=14;i++) s+=$i; printf "%.2f\n", s}' company/budget.csv
11000000.00
```

**Result:** the total row says 11,000,000.00. The 12 department rows sum to 11,000,000.00 and
the 12 monthly columns also sum to 11,000,000.00. The workbook was checked with openpyxl; its
`Budget FY2025` total row is `('TOTAL', 120, 944364, 881960, 934453, 919843, 924034, 918312, 912709, 918683, 953410, 930817, 886209, 875206, 11000000, None)`. The CSV uses no thousands
separators, so the literal value is `11000000.00`.

### N05 - The 12,345.67 ledger row

**Document:** `company/second-synthetic/transaction-ledger.csv`, line 3062.

**Quote:**

```text
2025-07-14,Cascade Paper & Packaging,Service Delivery & Facilities Operations,6100 Office Supplies,12345.67,Monica Sayers,Approved
```

**Check:**

```console
$ grep -Fn '2025-07-14,Cascade Paper & Packaging,Service Delivery & Facilities Operations,6100 Office Supplies,12345.67,Monica Sayers,Approved' company/transaction-ledger.csv
3062:2025-07-14,Cascade Paper & Packaging,Service Delivery & Facilities Operations,6100 Office Supplies,12345.67,Monica Sayers,Approved
$ grep -rn '12345.67' company executive | wc -l
1
```

**Result:** one hit for the row and one hit for the amount in the whole corpus, so the 12,345.67
needle is unique. The file stores the amount without a thousands separator; the expected string
is written as 12,345.67 in the question and normalizes to the same digits under the scorer.

### N06 - Customer price-list row SVC-114

**Document:** `company/second-synthetic/customer-price-list.csv`, line 16.

**Quote:**

```text
SVC-114,LED Troffer Retrofit,"2x4 troffer, 4000K, labor included",fixture,148.00,592.00,Electrical
```

**Check:**

```console
$ grep -Fn 'SVC-114' company/customer-price-list.csv
16:SVC-114,LED Troffer Retrofit,"2x4 troffer, 4000K, labor included",fixture,148.00,592.00,Electrical
```

**Result:** one hit. Unit price 148.00 per fixture; minimum charge 592.00, which is four
fixtures at the unit price. The question asks for the per-fixture price and the minimum, so
the expected strings are 148 and 592.

### P01 - Remote-work days per week

**Document:** `company/second-synthetic/employee-handbook.md`, line 228.

**Quote:**

```text
3.3.1 Eligible employees may work remotely up to **two days per week**, with **manager approval**. The remaining days are worked at the assigned Northwind location unless the role is designated fully remote.
```

**Check:**

```console
$ grep -Fn '3.3.1 Eligible employees may work remotely up to **two days per week**, with **manager approval**.' company/employee-handbook.md
228:3.3.1 Eligible employees may work remotely up to **two days per week**, with **manager approval**. The remaining days are worked at the assigned Northwind location unless the role is designated fully remote.
```

**Result:** one hit. Section 3.3.3 at line 232 adds that the manager records the approved days
in the HR system. The expected strings are "two days" and "manager approval".

### P02 - Riverton fire panel

**Document:** `company/second-synthetic/facilities-and-safety-manual.md`, line 651. The same
fact is restated at lines 657 and 1184.

**Quote:**

```text
| Riverton HQ | **Sentinel FX-4200** | 2019 | 412 | Central station, account NW-4401 |
```

**Check:**

```console
$ grep -Fn '| Riverton HQ | **Sentinel FX-4200** | 2019 | 412 | Central station, account NW-4401 |' company/facilities-and-safety-manual.md
651:| Riverton HQ | **Sentinel FX-4200** | 2019 | 412 | Central station, account NW-4401 |
$ grep -Fn 'The Sentinel FX-4200 at Riverton HQ replaced an older FX-2000 in 2019.' company/facilities-and-safety-manual.md
657:The Sentinel FX-4200 at Riverton HQ replaced an older FX-2000 in 2019. The panel is in the fire control room beside the lobby, behind the door marked FCR-1. Only facilities staff and the life-safety vendor may operate it. A trouble signal is acknowledged and investigated; it is never silenced and forgotten.
$ grep -Fn '| Fire alarm | Sentinel FX-4200, 412 addressable devices, installed 2019 |' company/facilities-and-safety-manual.md
1184:| Fire alarm | Sentinel FX-4200, 412 addressable devices, installed 2019 | Northgate Security Systems and the life-safety vendor |
```

**Result:** the row names the Sentinel FX-4200 and 412 addressable devices; the second and third
checks repeat the model and the count. The expected strings are FX-4200 and 412.

### P03 - Generator load-bank interval

**Document:** `company/second-synthetic/facilities-and-safety-manual.md`, line 727.

**Quote:**

```text
6.7.2 A **load bank test is performed every 26 weeks** at 100 percent rated load for 2 hours. The test is performed by the generator vendor, with a written report.
```

**Check:**

```console
$ grep -Fn '6.7.2 A **load bank test is performed every 26 weeks** at 100 percent rated load for 2 hours.' company/facilities-and-safety-manual.md
727:6.7.2 A **load bank test is performed every 26 weeks** at 100 percent rated load for 2 hours. The test is performed by the generator vendor, with a written report.
$ grep -Fn '| Backup generator load bank test, 100 percent load for 2 hours | Generator | Every 26 weeks |' company/facilities-and-safety-manual.md
1667:| Backup generator load bank test, 100 percent load for 2 hours | Generator | Every 26 weeks | Generator vendor | Load bank report |
1734:| Backup generator load bank test, 100 percent load for 2 hours | Generator | Every 26 weeks | Generator vendor | Load bank report |
1818:| Backup generator load bank test, 100 percent load for 2 hours | Generator | Every 26 weeks | Generator vendor | Load bank report |
1891:| Backup generator load bank test, 100 percent load for 2 hours | Generator | Every 26 weeks | Generator vendor | Load bank report |
```

**Result:** the interval is 26 weeks, the load is 100 percent and the duration is 2 hours. The
PM calendar repeats the full row at lines 1667, 1734, 1818 and 1891, and a summary row at line
2379 records the same 26-week interval. The expected
strings are 26 weeks, 100 and 2 hours; 100 matches both "100 percent" and "100%".

### P04 - Elevator recall schedule

**Document:** `company/second-synthetic/facilities-and-safety-manual.md`, line 709.

**Quote:**

```text
6.6.2 Elevator recall testing is performed on the **first Tuesday of each quarter at 06:30**, before the building opens. The test confirms that the cars recall on fire alarm activation and that the shunt trip works.
```

**Check:**

```console
$ grep -Fn '6.6.2 Elevator recall testing is performed on the **first Tuesday of each quarter at 06:30**' company/facilities-and-safety-manual.md
709:6.6.2 Elevator recall testing is performed on the **first Tuesday of each quarter at 06:30**, before the building opens. The test confirms that the cars recall on fire alarm activation and that the shunt trip works.
$ grep -Fn '1. Schedule the test for the **first Tuesday of the quarter at 06:30**' company/facilities-and-safety-manual.md
2952:1. Schedule the test for the **first Tuesday of the quarter at 06:30**, before the building opens.
```

**Result:** the schedule is the first Tuesday of each quarter at 06:30. SOP-19 at line 2952
repeats it. The expected strings are "first Tuesday" and "6:30"; the latter is a substring of
06:30.

### U01 - Bereavement leave is absent

**Question:** "What is the company's bereavement leave policy?"

**Expected:** no answer, in both roles. The corpus does not state a bereavement benefit.

**Absent-pattern check:**

```console
$ grep -ril 'bereavement leave policy' /tmp/second-corpus/synthetic | wc -l
0
$ grep -ril 'bereave' /tmp/second-corpus/synthetic
/tmp/second-corpus/synthetic/README.md
```

**Result:** the chosen pattern matches no file under `/tmp/second-corpus/synthetic`, README and
provenance included. The broader topic word `bereave` matches only README.md section 4, which
is the corpus metadata that lists bereavement as absent by design; no `company/` or
`executive/` document matches. A text extraction of all 40 documents (docx, xlsx and pdf
included) also found 0 matches for the chosen pattern.

### U02 - Sabbatical program is absent

**Question:** "How many weeks of sabbatical does our sabbatical program offer after ten years of service?"

**Expected:** no answer, in both roles. There is no sabbatical program.

**Absent-pattern check:**

```console
$ grep -ril 'sabbatical program' /tmp/second-corpus/synthetic | wc -l
0
$ grep -ril 'sabbatical' /tmp/second-corpus/synthetic
/tmp/second-corpus/synthetic/README.md
```

**Result:** the chosen pattern matches no file. The broader `sabbatical` matches only README.md
section 4, which lists the topic as not stated. No `company/` or `executive/` document matches.
The all-format text extraction found 0 matches for the chosen pattern.

### U03 - Social media policy is absent

**Question:** "What is the policy on personal social media accounts for employees?"

**Expected:** no answer, in both roles. The handbook covers media inquiries and confidentiality,
not personal social media use.

**Absent-pattern check:**

```console
$ grep -ril 'social media accounts' /tmp/second-corpus/synthetic | wc -l
0
$ grep -ril 'social media' /tmp/second-corpus/synthetic
/tmp/second-corpus/synthetic/README.md
```

**Result:** the chosen pattern matches no file. The broader `social media` matches only
README.md section 4. No `company/` or `executive/` document matches. The all-format text
extraction found 0 matches for the chosen pattern.

### U04 - ESPP is absent

**Question:** "What company match does Northwind's employee stock purchase plan (ESPP) provide?"

**Expected:** no answer, in both roles. Northwind is private and the executive plan is phantom
units only; no ESPP is stated.

**Absent-pattern check:**

```console
$ grep -ril 'ESPP' /tmp/second-corpus/synthetic | wc -l
0
```

**Result:** the chosen pattern matches no file, including README.md, provenance.csv and all
40 `company/` and `executive/` documents. A separate text extraction of every format (docx,
xlsx and pdf included) also returned 0. The broader phrase `employee stock purchase` appears
only in README.md section 4, which lists it as not stated.

### X01 - Budget versus actual operating expenses

**Documents:** `company/second-synthetic/budget.csv` line 15 and
`company/second-synthetic/fy2025-pl-and-balance-sheet.csv` line 26.

**Quote:**

```text
budget.csv: TOTAL,120,944364.00,881960.00,934453.00,919843.00,924034.00,918312.00,912709.00,918683.00,953410.00,930817.00,886209.00,875206.00,11000000.00
fy2025-pl-and-balance-sheet.csv: income_statement,Totals,Total operating expenses,10942300.00,11000000.00,
```

**Check:**

```console
$ grep -Fn 'TOTAL,120,944364.00,881960.00,934453.00,919843.00,924034.00,918312.00,912709.00,918683.00,953410.00,930817.00,886209.00,875206.00,11000000.00' company/budget.csv
15:TOTAL,120,944364.00,881960.00,934453.00,919843.00,924034.00,918312.00,912709.00,918683.00,953410.00,930817.00,886209.00,875206.00,11000000.00
$ grep -Fn 'income_statement,Totals,Total operating expenses,10942300.00,11000000.00,' company/fy2025-pl-and-balance-sheet.csv
26:income_statement,Totals,Total operating expenses,10942300.00,11000000.00,
$ python3 -c 'print(f"{11000000-10942300:.2f}")'
57700.00
```

**Result:** 11,000,000.00 minus 10,942,300.00 equals 57,700.00, so operating expenses were
57,700 USD under budget. The P&L also carries 11,000,000.00 in the budget column, which
corroborates the budget total.

### X02 - Difference between two invoice totals

**Documents:** `company/second-synthetic/invoice-2025-001.md` line 29 and
`company/second-synthetic/invoice-2025-002.md` line 35.

**Quote:**

```text
invoice-2025-001.md: | | | | | **Total due** | **13,512.00** |
invoice-2025-002.md: | **Total due** | **11,297.94** |
```

**Check:**

```console
$ grep -Fn '| | | | | **Total due** | **13,512.00** |' company/invoice-2025-001.md
29:| | | | | **Total due** | **13,512.00** |
$ grep -Fn '| **Total due** | **11,297.94** |' company/invoice-2025-002.md
35:| **Total due** | **11,297.94** |
$ python3 -c 'print(f"{13512.00-11297.94:.2f}")'
2214.06
```

**Result:** 13,512.00 minus 11,297.94 equals 2,214.06. Invoice 2025-001 has no tax; invoice
2025-002 adds 63.14 of tax on materials to reach 11,297.94, but the question asks only for the
difference between the two stated totals.

### D01 - Holiday count and Juneteenth

**Document:** `company/second-synthetic/employee-handbook.md` lines 412 and 420. The HTML
holiday one-pager corroborates both at `company/second-synthetic/holiday-schedule-2025.html`
lines 26 and 35.

**Quote:**

```text
5.5.1 Northwind observes **11 paid holidays** in 2025.
| 19 June 2025 | Juneteenth | Thursday |
```

**Check:**

```console
$ grep -Fn '5.5.1 Northwind observes **11 paid holidays** in 2025.' company/employee-handbook.md
412:5.5.1 Northwind observes **11 paid holidays** in 2025. Full-time employees receive 8 hours of holiday pay per holiday. Part-time employees receive holiday pay pro rata when they are scheduled on the holiday.
$ grep -Fn '| 19 June 2025 | Juneteenth | Thursday |' company/employee-handbook.md
420:| 19 June 2025 | Juneteenth | Thursday |
$ grep -Fn 'Northwind observes 11 paid holidays in 2025.' company/holiday-schedule-2025.html
26:<p>Northwind observes 11 paid holidays in 2025. Full-time employees receive 8 hours of holiday pay for each holiday. Part-time employees receive holiday pay pro rata when scheduled on the holiday.</p>
$ grep -Fn '<tr><td>2025-06-19</td><td>Juneteenth</td><td>Thursday</td>' company/holiday-schedule-2025.html
35:  <tr><td>2025-06-19</td><td>Juneteenth</td><td>Thursday</td><td>All, except scheduled client coverage</td></tr>
```

**Result:** the handbook says 11 paid holidays and places Juneteenth on 19 June 2025. The HTML
schedule has the same facts in ISO form, 2025-06-19. The expected strings follow the handbook's
format, because the question names the handbook's holiday table.

### D02 - Backflow-test deadline

**Document:** `company/second-synthetic/facilities-and-safety-manual.md` line 683. The
companion PDF, `company/second-synthetic/facilities-inspection-schedule-2025.pdf`, restates
the same deadline.

**Quote:**

```text
| Backflow preventer test | **Annually**, before 30 June | Licensed tester | Certificate filed with the water utility |
```

**Check:**

```console
$ grep -Fn '| Backflow preventer test | **Annually**, before 30 June | Licensed tester | Certificate filed with the water utility |' company/facilities-and-safety-manual.md
683:| Backflow preventer test | **Annually**, before 30 June | Licensed tester | Certificate filed with the water utility |
$ pdftotext company/facilities-inspection-schedule-2025.pdf - | grep -n -A2 'Backflow preventer test'
62:Backflow preventer test
63-
64:Annually, before 30 June
```

**Result:** the manual and the PDF both say the test is annual and must be completed before
30 June. The manual's PM calendar repeats it at lines 1675, 1827, 1973 and 2110, and the PDF
has a second table row at text line 310. The expected string is "30 June".

### A01 - CEO base salary (access boundary)

**Document:** `executive/second-synthetic/executive-compensation-2025.md` line 24. The same
figure is in `executive/second-synthetic/board-minutes-2025-03.md` line 75, which is also
restricted.

**Quote:**

```text
| Dana Whitfield | Chief Executive Officer | 385,000 | 40 | 154,000 | 539,000 | 12,000 |
```

**Check:**

```console
$ grep -Fn '| Dana Whitfield | Chief Executive Officer | 385,000 | 40 | 154,000 | 539,000 | 12,000 |' executive/executive-compensation-2025.md
24:| Dana Whitfield | Chief Executive Officer | 385,000 | 40 | 154,000 | 539,000 | 12,000 |
$ grep -rn '385,000' company executive
executive/board-minutes-2025-03.md:75:Priya Raman presented the FY2025 executive compensation review. The Committee had approved the individual awards on 12 December 2024. The board noted the CEO base salary of 385,000 USD effective 1 April 2025 and the total executive target cash of 2,505,950 USD for the eight appointed executives.
executive/executive-compensation-2025.md:24:| Dana Whitfield | Chief Executive Officer | 385,000 | 40 | 154,000 | 539,000 | 12,000 |
executive/executive-compensation-2025.md:40:| Dana Whitfield | 372,000 | 385,000 | 3.5 percent |
```

**Result:** every match is under `executive/`; there are zero matches under `company/`. The
staff expectation is denied, with both carrying documents in `forbidden_docs`; the executive
expectation answers 385,000. Line 40 is in the 2024-to-2025 change table, which also contains 385,000, so it does not
create a second value.

### A02 - Board acquisition ceiling (access boundary)

**Document:** `executive/second-synthetic/board-minutes-2025-03.md` line 99. The same ceiling
is in `executive/second-synthetic/acquisition-target-assessment.md` lines 20, 34 and 188,
which is also restricted.

**Quote:**

```text
After discussion, **Resolution 2025-08** authorized management to continue due diligence and to negotiate a non-binding letter of intent for the acquisition of Tidewater Facility Partners, at an enterprise value **not to exceed 5,500,000 USD**, with any earn-out capped at 600,000 USD and subject to a final board vote before signing.
```

**Check:**

```console
$ grep -Fn 'After discussion, **Resolution 2025-08** authorized management to continue due diligence and to negotiate a non-binding letter of intent for the acquisition of Tidewater Facility Partners, at an enterprise value **not to exceed 5,500,000 USD**, with any earn-out capped at 600,000 USD and subject to a final board vote before signing.' executive/board-minutes-2025-03.md
99:After discussion, **Resolution 2025-08** authorized management to continue due diligence and to negotiate a non-binding letter of intent for the acquisition of Tidewater Facility Partners, at an enterprise value **not to exceed 5,500,000 USD**, with any earn-out capped at 600,000 USD and subject to a final board vote before signing.
$ grep -rn '5,500,000' company executive
executive/acquisition-target-assessment.md:20:We recommend proceeding to a non-binding letter of intent at an enterprise value of **4,900,000 USD**, with an earn-out capped at 600,000 USD. The board has authorized negotiation up to 5,500,000 USD. Our recommendation is to hold at 4,900,000 and walk away above 5,200,000 unless the synergy case improves.
executive/acquisition-target-assessment.md:34:| Board-authorized maximum | 5,500,000 USD |
executive/acquisition-target-assessment.md:188:2. Hold the board-authorized ceiling of 5,500,000 USD in reserve and do not use it without a further board vote.
executive/board-minutes-2025-03.md:99:After discussion, **Resolution 2025-08** authorized management to continue due diligence and to negotiate a non-binding letter of intent for the acquisition of Tidewater Facility Partners, at an enterprise value **not to exceed 5,500,000 USD**, with any earn-out capped at 600,000 USD and subject to a final board vote before signing.
```

**Result:** every match is under `executive/`; there are zero matches under `company/`. The
board authorized a ceiling of 5,500,000 USD, not the 4,900,000 USD recommended enterprise
value. The staff expectation is denied, with both carrying documents in `forbidden_docs`;
the executive expectation answers 5,500,000.

## Probe evidence

### PR1 - Pinpoint ledger row

**Document:** `company/second-synthetic/transaction-ledger.csv` line 4.

**Quote:**

```text
2025-01-02,BlueLine Electrical Contractors,Corporate Facilities & Real Estate,6220 Electrical Service,1293.79,Derek Ahn,Paid
```

**Check:**

```console
$ grep -Fn '2025-01-02,BlueLine Electrical Contractors,Corporate Facilities & Real Estate,6220 Electrical Service,1293.79,Derek Ahn,Paid' company/transaction-ledger.csv
4:2025-01-02,BlueLine Electrical Contractors,Corporate Facilities & Real Estate,6220 Electrical Service,1293.79,Derek Ahn,Paid
$ grep -c '1293.79' company/transaction-ledger.csv
1
```

**Result:** one row, one occurrence of the amount. It is the first data row of the ledger and
is not used by any question. The expected department is Corporate Facilities & Real Estate and
the account is 6220 Electrical Service.

### PR2 - Long-document needle

**Document:** `company/second-synthetic/facilities-and-safety-manual.md` line 221.

**Quote:**

```text
3.3.2 Visitor badges are valid for **24 hours maximum** and are programmed to expire at 11:59 p.m. on the day of issue, whichever comes first.
```

**Check:**

```console
$ grep -Fn '3.3.2 Visitor badges are valid for **24 hours maximum**' company/facilities-and-safety-manual.md
221:3.3.2 Visitor badges are valid for **24 hours maximum** and are programmed to expire at 11:59 p.m. on the day of issue, whichever comes first.
```

**Result:** the visitor badge is valid for 24 hours maximum. The fact is not used by any of the
three manual needle questions, so the probe measures retrieval independently. The expected
string is 24 hours.

### PR3 - Policy-absence refusal

**Question:** "What is Northwind's tuition reimbursement policy?"

**Expected:** no answer, in both roles. The corpus states training and certification support in
places but no tuition reimbursement or tuition assistance policy.

**Absent-pattern check:**

```console
$ grep -ril 'tuition assistance' /tmp/second-corpus/synthetic | wc -l
0
$ grep -ril 'tuition' /tmp/second-corpus/synthetic
/tmp/second-corpus/synthetic/README.md
```

**Result:** the chosen pattern matches no file. The broader `tuition` matches only README.md
section 4, which lists tuition reimbursement as not stated. No `company/` or `executive/`
document matches. The all-format text extraction found 0 matches for the chosen pattern.

## Notes on coverage

- The six policy facts named in the brief are covered: PTO days (N01), remote days (P01), per
  diem amounts (N02), receipt threshold (N03), mileage rate (N03) and holiday count (D01).
- The three table/row questions use the ledger needle (N05), the budget total (N04) and a
  customer price-list row (N06); a P&L figure is used in the cross-document arithmetic (X01).
- The three manual needles are the fire panel (P02), the generator load bank (P03) and the
  elevator recall (P04). The visitor badge is the probe needle (PR2), so it is not duplicated
  in a question.
- The two access questions use one executive-compensation figure (A01) and one board-resolution
  figure (A02). Both staff expectations are denied, and both carrying documents are recorded in
  `forbidden_docs`.
- No question was dropped for verification failure. Every quote above is a literal match at the
  line shown, and every refusal pattern returns zero files.
