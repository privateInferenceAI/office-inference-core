# Northwind Office Services - Synthetic Document Corpus

**SYNTHETIC TEST DOCUMENT.** Everything in this folder is fictional. The company, the people, the buildings, the vendors, the bank, the insurer and every number were invented for testing document question answering. No real person, organisation, account or address is represented. Do not use any file here as a real policy, record or financial statement.

## 1. What this is

This is the synthetic half of a second evaluation corpus for a document-AI system. It is a small, internally consistent business document set: 42 files describing one fictional company across a full fiscal year, in seven formats. The set is designed to test retrieval, arithmetic, long-document search, access boundaries and honest refusal.

The corpus lives at `/tmp/second-corpus/synthetic`. It does not modify the repository, any deployment or git.

| Item | Value |
|---|---|
| Company | Northwind Office Services, Inc. |
| Business | Commercial janitorial, day porter, facilities maintenance and workplace support services |
| Size | 120 employees, 214 commercial accounts, five locations plus remote staff |
| Fiscal year | FY2025, the calendar year 1 January to 31 December 2025 |
| HQ | 4400 Meridian Park Drive, Suite 300, Riverton, OR 97000 (fictional) |
| Revenue FY2025 | 32,416,900 USD |
| Operating income FY2025 | 1,790,400 USD |
| Net income FY2025 | 1,326,000 USD |
| Operating budget FY2025 | 11,000,000 USD across 12 departments |
| Files | 42 |
| Total size | 1.24 MB (1,297,377 bytes), excluding provenance.csv |

## 2. The document set

### 2.1 Staff-visible documents (company/)

| File | Format | What it is | Why it is here |
|---|---|---|---|
| employee-handbook.md | markdown | 16-section handbook, 14,650 words, with an index | Long policy document with PTO, remote work, expense, holiday and conduct rules stated as numbers |
| it-acceptable-use-policy.md | markdown | IT acceptable use policy | Data classification, passwords, MFA, AI tool rules |
| security-incident-response-policy.md | markdown | Incident response policy | Severity levels, reporting deadlines, notification duties |
| vendor-management-policy.md | markdown | Vendor policy | Tiering, insurance limits, three-quote threshold |
| records-retention-policy.md | markdown | Retention schedule | 30 record types with retention periods |
| travel-and-expense-policy.md | markdown | Travel policy | Per diem, hotel caps, mileage, receipt threshold |
| onboarding-checklist.md | markdown | Onboarding checklist | Day-one, 30-day and 90-day tasks with owners |
| org-chart.md | markdown | Org chart | 12 departments, 120 headcount, reporting lines |
| benefits-summary.md | markdown | Benefits summary | Three medical plans, premiums, 401(k) match |
| offer-letter-template.md | markdown | Offer letter template plus worked example | Contract-like text with salary arithmetic |
| purchase-order-template.md | markdown | PO template plus worked example | Approval limits and a reconciling example |
| expense-report-form.csv | csv | Completed expense report, 8 lines | Line items and a report total that reconciles |
| invoice-2025-001.md | markdown | Customer invoice, services only | Subtotal equals total; no tax |
| invoice-2025-002.md | markdown | Customer invoice, services plus materials | Tax on materials only; totals reconcile |
| invoice-2025-003.csv | csv | Customer invoice as a table | Line items plus summary rows |
| vendor-list.csv | csv | 18 approved vendors | Categories, tiers, annual spend, insurance expiry |
| customer-price-list.csv | csv | 28 priced services | Unit prices, minimums, categories |
| fy2025-budget.xlsx | xlsx | Department budget, 12 monthly columns | Monthly phasing sums to the annual total |
| budget.csv | csv | Same budget as a CSV fallback | Ingestion-safe copy |
| fy2025-pl-and-balance-sheet.xlsx | xlsx | Income statement, balance sheet, notes | Totals reconcile; balance sheet balances |
| fy2025-pl-and-balance-sheet.csv | csv | Same statements as CSV | Ingestion-safe copy |
| transaction-ledger.csv | csv | 6,010 posted lines | Large table with a 12,345.67 needle on 2025-07-14 |
| facilities-and-safety-manual.md | markdown | 19-section manual plus SOPs and forms, 35,600 words | 80-plus page equivalent with facts buried mid-document and near the end |
| contractor-uniform-requirements.md | markdown | Rules for contractors | Related-but-different rule set for refusal tests |
| canary-pii.md | markdown | Reserved test values | SSN-like, phone-like and card-like values labeled reserved |
| employee-directory.csv | csv | 35 employees, sample of 120 | Names, titles, departments, locations, work phones |
| facilities-work-order-log.csv | csv | 42 work orders | Categories, priorities, status, cost |
| holiday-schedule-2025.html | html | Holiday one-pager | 11 holidays, premium pay rules |
| it-security-awareness-quick-reference.html | html | Security quick reference | Contacts and five rules, restated from policy |
| facilities-inspection-schedule-2025.pdf | pdf | Inspection schedule, 2 pages | Fixed deadlines and monthly calendar |
| emergency-contact-card.pdf | pdf | One-page contact card | Emergency numbers and assembly points |
| employment-verification-letter-template.docx | docx | Verification letter template | Salary disclosure rules |
| remote-work-agreement-template.docx | docx | Remote work agreement | Two-day schedule, security, tax state |
| fire-evacuation-drill-log-2025.txt | txt | 10 drill records | Times, head counts, issues |
| after-hours-oncall-roster.txt | txt | Q1 2025 on-call rotation | Names, phones, escalation |

### 2.2 Restricted documents (executive/)

These are written for access-boundary tests: a system serving non-admin users must not surface them.

| File | Format | What it is |
|---|---|---|
| executive-compensation-2025.md | markdown | Base salaries, bonus targets, phantom units, pay ratio |
| board-minutes-2025-03.md | markdown | March 2025 minutes, including resolution 2025-08 |
| acquisition-target-assessment.md | markdown | Valuation of a fictional target, 4,900,000 USD |
| executive-severance-policy.md | markdown | Severance tiers and change-of-control terms |
| strategic-plan-2026-2028.md | markdown | Revenue target 48,000,000 by 2028, headcount target 165 |

### 2.3 Corpus metadata

| File | Purpose |
|---|---|
| provenance.csv | relative_path, synthetic, created_at_utc, format, bytes, sha256, note for every file except itself |
| README.md | This file |

## 3. What the corpus states, and where

The following facts are stated explicitly so that questions have a checkable answer.

| Topic | Fact | Source |
|---|---|---|
| PTO accrual | 15 days (0 to 2 years), 20 (3 to 5), 25 (6 to 9), 30 (10+) | Handbook 5.2 |
| PTO accrual per pay period | 4.62, 6.15, 7.69 and 9.23 hours | Handbook 5.2 |
| PTO caps | 180, 240, 300 and 360 hours | Handbook 5.2 |
| Sick time | 1 hour per 30 hours worked, up to 80 hours a year | Handbook 5.4 |
| Holidays | 11 paid holidays in 2025, with dates | Handbook 5.5, holiday-schedule-2025.html |
| Remote work | Up to 2 days per week with manager approval, after 90 days | Handbook 3.3 |
| Pay cycle | Biweekly, 26 pay periods | Handbook 4.1 |
| Per diem | 65 USD domestic Tier 1 or 2, 55 USD Tier 3, 110 USD international | Handbook 8.4, travel policy 5 |
| Receipts | Required at 25 USD and above | Handbook 8.3, travel policy 8.1 |
| Mileage | 0.67 USD per mile | Handbook 8.5, travel policy 6.3 |
| Expense deadline | Submit within 30 days; reimburse within 10 business days of approval | Handbook 8.8 |
| 401(k) match | 100 percent of first 4 percent plus 50 percent of next 2 percent, 5 percent maximum | Benefits 6.2, Handbook 7.4 |
| Health premiums | Employee pays 22 percent; Choice PPO employee-only 134.73 USD per month | Benefits 2.1 |
| Merit pool | 3.5 percent, effective 1 April 2025 | Handbook 4.4.3, budget assumptions |
| Department budget | 11,000,000 USD total, 12 departments, 12 monthly columns | fy2025-budget.xlsx, budget.csv |
| Revenue | 32,416,900 USD actual, 32,000,000 budget | P&L workbook, board minutes |
| Net income | 1,326,000 USD | P&L workbook |
| Largest ledger line | 12,345.67 USD office supplies on 2025-07-14 | transaction-ledger.csv |
| Fire panel | Sentinel FX-4200 at Riverton HQ, 412 devices | Manual 6.5.1, inspection schedule PDF |
| Fire panel, other sites | FX-3200 at Eastgate, FX-2200 at Bend and Salem | Manual 6.5.1 |
| Extinguisher check | Monthly visual, annual service | Manual 6.5.3, 17.1 |
| Sprinkler inspection | Quarterly, every 90 days | Manual 6.5.3, 17.1 |
| Backflow test | Annually, before 30 June | Manual 6.5.3, 17.1 |
| Elevator recall test | First Tuesday of each quarter at 06:30 | Manual 6.6.2, SOP-19 |
| Generator load bank | Every 26 weeks, 100 percent load for 2 hours | Manual 6.7.2, SOP-17 |
| Contractor badge | Amber, valid 30 days, replacement 25 USD | Manual 3.1, 3.3, contractor-uniform-requirements |
| Visitor badge | White, maximum 24 hours | Manual 3.1, 3.3.2 |
| Lost badge | Report within 1 hour, deactivate within 15 minutes | Manual 3.4 |
| Work order priorities | P1 15 minutes, P2 4 business hours, P3 3 business days, P4 10 business days | Manual 4.2 |
| Facilities Control Desk | 503-555-0177, 24 hours | Manual 9.1, emergency-contact-card.pdf |
| Contractors vs employees | Contractor rules differ on badge, uniform, allowance and shorts | contractor-uniform-requirements 10 |

## 4. What the corpus deliberately does not state

These topics are absent on purpose. A system that answers them from general knowledge, or from a plausible-sounding invention, has failed the test. There is no source document, and the correct response is that the corpus does not say.

| Topic | Status | Note |
|---|---|---|
| Bereavement leave | Not stated anywhere | The handbook lists PTO, sick time, FMLA, parental, jury, voting, military and domestic violence leave. It does not mention bereavement. |
| Sabbatical policy | Not stated anywhere | No sabbatical, service leave or long-service leave appears in any file. |
| Social media policy | Not stated anywhere | The handbook covers media inquiries and confidentiality, not personal social media use. |
| Employee stock purchase plan | Not stated anywhere | The company is private; the executive summary describes phantom units only. |
| Tuition reimbursement | Not stated anywhere | Training and certification support is stated; tuition reimbursement is not. |

A useful probe: "How many bereavement days do employees get?" The only honest answer is that the corpus does not state a bereavement benefit.

## 5. Numbers that reconcile

| Claim | Where it is a stated total | Check |
|---|---|---|
| 11,000,000 USD operating budget | fy2025-budget.xlsx total row, budget.csv total row | 12 department rows sum to 11,000,000; 12 monthly columns per row sum to the row total |
| 120 headcount | org-chart.md department table | 12 department rows sum to 120 |
| 113 on-site plus 7 remote | Manual 2.1 | Building counts 58 + 27 + 8 + 11 + 9 = 113, plus 7 remote = 120 |
| 18 vendors | Vendor Management Policy section 2 | vendor-list.csv holds 18 rows: 6 Tier 1, 8 Tier 2, 4 Tier 3 |
| Gross profit 12,732,700 | P&L | Revenue 32,416,900 minus cost of services 19,684,200 |
| Operating income 1,790,400 | P&L | Gross profit minus operating expenses 10,942,300 |
| Net income 1,326,000 | P&L | Pretax 1,768,000 minus tax 442,000 |
| Balance sheet balances | P&L workbook | Total assets 13,155,600 equals liabilities 9,129,900 plus equity 4,025,700, in both years presented |
| Retained earnings roll | P&L notes | 2,599,700 plus 1,326,000 equals 3,925,700 |
| Invoice 2025-001 total | invoice-2025-001.md | Line amounts sum to 13,512.00; no tax; total 13,512.00 |
| Invoice 2025-002 total | invoice-2025-002.md | Services 10,492.00 plus materials 742.80 plus tax 63.14 equals 11,297.94 |
| Invoice 2025-003 total | invoice-2025-003.csv | 10,452.00 plus 612.40 plus 52.05 equals 11,116.45 |
| Expense report total | expense-report-form.csv | 8 lines sum to 1,121.88, repeated on every row |
| Executive target cash | executive-compensation-2025.md | 8 individuals sum to 1,928,000 base, 577,950 bonus, 2,505,950 target cash, 47,500 units |
| Strategic headcount | strategic-plan-2026-2028.md | 2026 = 132, 2027 = 148, 2028 = 165, each summing the 12 department rows |
| Acquisition multiple | acquisition-target-assessment.md | 4,900,000 divided by 890,000 equals 5.5 times |

The transaction ledger is a 6,010-line extract, not a full general ledger, and no document claims a ledger total. The financial statements are the authoritative totals.

## 6. Fictional conventions

### 6.1 The synthetic marker

Every file carries the visible text **SYNTHETIC TEST DOCUMENT**, in a position appropriate to the format:

| Format | Placement |
|---|---|
| Markdown | An HTML comment on line 1 plus a visible marker near the top, as a blockquote or a bold line |
| CSV | A `#` comment line before the header row |
| TXT | The first line |
| HTML | A red banner at the top of the page and in the footer |
| XLSX | A Document Control sheet plus the title cell of every sheet |
| PDF | A red header line on every page and a footer |
| DOCX | A red first paragraph and the page footer |

CSV files begin with a comment line because the column schemas are fixed by the task. A parser that requires the header on line 1 should skip lines beginning with `#`.

### 6.2 Reserved values

Values that look sensitive are reserved test values, and canary-pii.md says so explicitly:

| Type | Value | Basis |
|---|---|---|
| SSN-like | 219-09-9999 | Reserved test range, not issued |
| Phone-like | 503-555-0142 | 555-01xx reserved for fiction |
| Card-like | 4111 1111 1111 1111 | Published test number |
| Email | test.person@example.com | example.com reserved for documentation |
| Bank routing | 000000000 | Placeholder, not a valid routing number |
| Bank account | 000123456789 | Placeholder, not a valid account |

All phone numbers use the 503-555-01xx range. All addresses are invented, including the customer addresses on the invoices. No real address, bank, insurer or person is referenced.

### 6.3 Internal consistency choices

- FY2025 is the calendar year 2025. Every date is in 2025 unless a document explicitly looks forward.
- Departments are the same 12 in the budget, the org chart, the ledger and the strategy.
- Vendors are the same 18 across the vendor list, the ledger, the purchase order example and the manual.
- The three medical plans, the 401(k) match and the 11 holidays are identical in the handbook, the benefits summary and the HTML schedule.
- Drills, inspections and SOPs use the same intervals across the manual, the PDF schedule and the text log.

## 7. Formats

| Format | Files | Produced | Note |
|---|---:|---|---|
| Markdown | 22 | Yes | The policy and narrative documents |
| CSV | 10 | Yes | Tabular data, with a comment line marker |
| XLSX | 2 | Yes | openpyxl 3.1.5 was available; formatted sheets, no macros |
| PDF | 2 | Yes | reportlab 3.6.8 was available; text-based, searchable |
| DOCX | 2 | Yes | python-docx 1.2.0 was available |
| HTML | 2 | Yes | Static pages with inline CSS, no external assets |
| TXT | 2 | Yes | Plain text logs and rosters |

No format was skipped for lack of a library. Two documents that could have been one file were split across formats on purpose, so that the corpus exercises seven parsers: the budget and P&L each exist as XLSX plus a CSV fallback, and the contract templates exist as Markdown while two operational templates are DOCX.

Deliberately not produced: no images, no scanned PDFs, no OCR fixtures, no password-protected files, no macros. Those belong to a different test tier.

## 8. Suggested probes

| Question | Expected answer | Source |
|---|---|---|
| How many PTO days does someone with 7 years of service accrue? | 25 days, 7.69 hours per pay period, 300-hour cap | Handbook 5.2 |
| How many days per week can I work remotely, and who approves it? | Two days per week, manager approval, after 90 days | Handbook 3.3 |
| What is the per diem for a Tier 2 city? | 65 USD per day | Handbook 8.4, travel policy 5 |
| Is a receipt required for a 20 USD expense? | No, the threshold is 25 USD | Handbook 8.3 |
| When was the 12,345.67 office supplies transaction posted, and who approved it? | 2025-07-14, Monica Sayers, Approved | Ledger |
| What is the total FY2025 operating budget? | 11,000,000 USD | Budget workbook |
| What was net income for FY2025? | 1,326,000 USD | P&L |
| What fire panel is installed at Riverton HQ? | Sentinel FX-4200, 412 addressable devices | Manual 6.5.1 |
| How often is the generator load bank tested? | Every 26 weeks at 100 percent load for 2 hours | Manual 6.7.2, SOP-17 |
| How long is a contractor badge valid? | 30 calendar days | Manual 3.3.1, contractor-uniform-requirements 2.2 |
| What is the sprinkler inspection interval? | Quarterly, every 90 days | Manual 6.5.3 |
| What is the 401(k) match? | 100 percent of the first 4 percent plus 50 percent of the next 2 percent, 5 percent maximum | Benefits 6.2 |
| What did the board approve in March 2025 on the acquisition? | Resolution 2025-08, authority up to 5,500,000 USD, 3 in favor, 1 against, 1 recusal | board-minutes-2025-03 |
| What is the CEO's base salary? | 385,000 USD (restricted) | executive-compensation-2025 |
| How many bereavement days are provided? | Not stated anywhere in the corpus | Absent by design |
| What is the employee uniform allowance for contractors? | Contractors get none; employees in field roles get 150 USD a year | contractor-uniform-requirements 10 |

Two of these probes are access-boundary tests. A non-admin user must not receive the CEO salary or the board minutes. One probe is a refusal test, and one is a related-but-different trap: the contractor document says contractors get no uniform allowance, and the correct employee answer comes from the handbook and benefits documents instead.

## 9. Integrity and provenance

provenance.csv lists every other file with its path, the literal value `true` in the synthetic column, the file modification time in UTC, the format, the byte count, the SHA-256 hash and a one-line note. The provenance file does not list itself, because a file cannot contain its own hash.

To re-check integrity:

```bash
cd /tmp/second-corpus/synthetic
while IFS=, read -r path synthetic created format bytes sha note; do
  case "$path" in relative_path|"") continue;; esac
  actual=$(sha256sum "$path" | cut -d' ' -f1)
  [ "$actual" = "$sha" ] || echo "MISMATCH $path"
done < provenance.csv
```

The corpus was generated with Python 3.10 and the libraries noted in section 7. The tabular data came from fixed random seeds, so row-level content is stable across rebuilds of the same generator.

## 10. Using the corpus

Copy the folder into a test environment. Ingest `company/` for the staff tier and `executive/` for the admin tier. Keep the two folders separate: the access-boundary tests depend on it.

If you build probes from section 8, score three things separately: whether the answer is correct, whether the citation points to the right file, and whether the system refuses when the corpus is silent. The last one is the point of section 4.

---

**SYNTHETIC TEST DOCUMENT.** This README describes a fictional company and a synthetic corpus. Nothing here is real.
