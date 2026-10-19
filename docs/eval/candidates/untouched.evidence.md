# Untouched-document coverage — evidence

Ten candidates, each from a document that docs/eval/data-questions.yaml and the four probes in
tests/fixtures/data_suite_run.py do not name. All are staff-visible company/ documents, so each candidate
carries only the staff expectation. File types: two .doc, two .txt, one .csv, one .xls, one .htm, one .pdf,
one .md canary, one .rtf.

Every value was read from chunk_index on the deployment. The SQL shown is the exact query run against the
deployment's Postgres; the result gives doc_id, chunk_id, page_start and kind, and the text column is the
passage quoted above it. Passage blocks preserve the chunk wording; long lines are wrapped for
readability, and the source_quote field in the YAML is the exact substring the SQL checks.

## CAND_UNTOUCHED_01 — .doc — company/group-02/592778.doc

**Question:** In customs ruling NY F86667, what is the applicable HTS subheading for style #20320?

**Expected:** staff answered, docs ["company/group-02/592778.doc"], contains ["6117.10.1010"].

### Passage (page 1, chunk d7250a86-5ced-595e-a03a-8f93bb259f1f)

```text
The applicable subheading for style #20320 will be 6117.10.1010, Harmonized Tariff Schedule of the United
States (HTS), which provides for “Other made up clothing accessories, knitted or crocheted; knitted or
crocheted parts of garments or of clothing accessories: Shawls, scarves, mufflers, mufflers, mantillas,
veils and the like: Of wool or fine animal hair.”  The duty rate will be 9.8% ad valorem.
```

The same chunk begins with the ruling number and the classification line:

```text
NY F86667
May 8,  2000
CLA-2-61:RR:NC:3:353 F86667
CATEGORY:	Classification
TARIFF NO.: 	6117.10.1010
```

### SQL and result

```sql
select doc_id, chunk_id, page_start, kind, text
from chunk_index
where doc_id = 'company/group-02/592778.doc'
order by chunk_id;
```

Result: 2 rows, both page 1, kind prose. The carrying row is
company/group-02/592778.doc | d7250a86-5ced-595e-a03a-8f93bb259f1f | 1 | prose.
The second row (dcbe6662-d48c-5be2-b91c-424631a7dbe0) is the closing paragraph.

```sql
select doc_id, count(*) from chunk_index
where text ilike '%6117.10.1010%'
group by doc_id;
```

Result: company/group-02/592778.doc | 1. The value exists only in the named document.

### Why not a duplicate

No reference to company/group-02/592778.doc in docs/eval/data-questions.yaml or
tests/fixtures/data_suite_run.py (grep: 0 matches in each). No existing question is about a customs ruling
or a tariff classification; the nearest are N08/P07/P08 on the Acurx S-1 and P01/P02 on the Native Hawaiian
grant application, both different documents and different facts. File type: .doc.

## CAND_UNTOUCHED_02 — .doc — company/group-01/251719.doc

**Question:** How many commercial poultry farms are in the area of Torun County, Poland?

**Expected:** staff answered, docs ["company/group-01/251719.doc"], contains ["six commercial poultry farms"].

### Passage (page 1, chunk 7405cff4-194a-5eb0-b8e2-5f58e50a04c6)

```text
There are six commercial poultry farms and four poultry slaughter and processing plants in the area of Torun
County.  One plant is located within the Intensive Surveillance area.  Like nearly everywhere in Poland,
there are also numerous small, back-yard flocks in the area.  Post will continue to provide updates of
developments.

GAIN Report - PL6010	Page 2 of 2

UNCLASSIFIED	USDA Foreign Agricultural Service
```

### SQL and result

```sql
select doc_id, chunk_id, page_start, kind, text
from chunk_index
where doc_id = 'company/group-01/251719.doc'
  and text ilike '%six commercial poultry farms%';
```

Result: 1 row, company/group-01/251719.doc | 7405cff4-194a-5eb0-b8e2-5f58e50a04c6 | 1 | prose.

```sql
select doc_id, count(*) from chunk_index
where text ilike '%six commercial poultry farms%'
group by doc_id;
```

Result: company/group-01/251719.doc | 1. The value exists only in the named document.

### Why not a duplicate

No reference to company/group-01/251719.doc in the suite or the four probes (grep: 0 matches in each). No
existing question concerns poultry, avian influenza, or a GAIN report; P01/P02 also use a USDA document
(956808.doc) but ask about a CFDA number and Grants.gov, not farm counts. File type: .doc.

## CAND_UNTOUCHED_03 — .txt — company/group-02/003549.txt

**Question:** What is the subject of GCN Circular 7186?

**Expected:** staff answered, docs ["company/group-02/003549.txt"], contains ["NGC 2770B", "same redshift"].

### Passage (page 1, chunk 5bd3f226-21ad-5845-ac63-46b70795daca)

```text
TITLE:   GCN CIRCULAR NUMBER:  7186 SUBJECT: NGC 2770B has the same redshift as NGC 2770 DATE:    08/01/15
22:42:35 GMT FROM:    Daniele Malesani at Niels Bohr Inst,Dark Cosmology Center

malesani@astro.ku.dk

Johan P.U. Fynbo, Daniele Malesani (DARK), Thomas Augusteijn, and Sami-Matias Niemi (NOT), report:

We acquired a spectrum of the galaxy (or galaxy pair) NGC 2770B, located ~3 arcmin West of NGC 2770, the
host galaxy of XRF 080109 / SN 2008D (Berger & Soderberg, ATel 1353; Deng & Zhu, GCN 7160). Observations
were carried out with the Nordic Optical Telescope, starting on 2007 Jan 15.213 UT, for a total exposure
time of 10 min.

From the detection of prominent nebular lines, including Balmer features, [OII], [OIII], [SII], [NII] and
others, we determine the redshift to be z = 0.0064 +- 0.0003. This is very similar to the redshift of NGC
2770 (z=0.0065 according to NED). At the corresponding distance (25.7 Mpc), the angular separation of ~3
arcmin corresponds to only 22 kpc in projection.

Similarly to NGC 2770, NGC 2770B is undergoing intense star formation, as implied by its blue colors and the
large Halpha equivalent width. Although there is not much evidence for morphological distorsions, a past
interaction might have triggered the strong starburst activity in both objects.
```

### SQL and result

```sql
select doc_id, chunk_id, page_start, kind, text
from chunk_index
where doc_id = 'company/group-02/003549.txt';
```

Result: 1 row, company/group-02/003549.txt | 5bd3f226-21ad-5845-ac63-46b70795daca | 1 | prose.

```sql
select doc_id, count(*) from chunk_index
where text ilike '%NGC 2770B%'
group by doc_id;
```

Result: company/group-02/003549.txt | 1. The value exists only in the named document.

### Why not a duplicate

No reference to company/group-02/003549.txt in the suite or the four probes (grep: 0 matches in each). No
existing question is about an astronomy circular, a redshift, or a GCN report. The only existing
science-adjacent questions are P03/P04 on the NASA foreign agreements chapter (173609.doc), which ask about
a numbered agreement and signatory verification. File type: .txt.

## CAND_UNTOUCHED_04 — .txt — company/group-02/228801.txt

**Question:** When emailing comments on the FEMA draft environmental assessment for the E.H. Burton
property, what site number should be entered in the subject field?

**Expected:** staff answered, docs ["company/group-02/228801.txt"], contains ["OR-205"].

### Passage (page 1, chunk 3a7977ff-5c7e-59bd-a5cf-af898ad99019)

```text
Due to the emergency nature of this action, the public comment period will be brief – March 10, 2006, to
March 12, 2006.  Written comments on the draft EA can be faxed to FEMA’s Joint Field Office in Baton Rouge at
(225) 346-5848; or sent via email to eacomments@dhs.gov. When responding by email, enter site number OR-205
in the subject field. Verbal comments will be accepted at (225) 376-5137 and TTY for hearing or
speech-impaired at 800-462-7585; between 8:00 A.M. and 5:00 P.M. The Environmental Assessment can be viewed
and downloaded from FEMA’s website at http://www.fema.gov/ehp/docs.shtm. The draft EA is also available for
public review at the FEMA Disaster Recovery Center (DRC) located at 11700 Chef Menteur Highway New Orleans,
and the Orleans Parish Library, Hubbell (Algiers Point) Branch, located at 725 Pelican Avenue, New Orleans.
The DRC hours are 9:00 AM to 5:00 PM, Monday through Saturday.
```

### SQL and result

```sql
select doc_id, chunk_id, page_start, kind, text
from chunk_index
where doc_id = 'company/group-02/228801.txt'
  and text ilike '%OR-205%';
```

Result: 1 row, company/group-02/228801.txt | 3a7977ff-5c7e-59bd-a5cf-af898ad99019 | 1 | prose.

```sql
select doc_id, count(*) from chunk_index
where text ilike '%OR-205%'
group by doc_id;
```

Result: company/group-02/228801.txt | 1. The value exists only in the named document.

### Why not a duplicate

No reference to company/group-02/228801.txt in the suite or the four probes (grep: 0 matches in each). No
existing question is about a FEMA notice, Hurricane Katrina housing, or a comment-period instruction. The
access questions A07/A08 use the sample post orders and the proceeding notice, not this notice. File type: .txt.

## CAND_UNTOUCHED_05 — .csv — company/group-02/059190.csv

**Question:** In the airline on-time performance summary, how many operations were reported for 2003?

**Expected:** staff answered, docs ["company/group-02/059190.csv"], contains ["552,109"].

### Passage (page 1, chunk e49ba066-13aa-5b75-a421-c8027eb59a83, with the header from chunk 7c1f879c)

```text
Table 1
Summary of Airline On-Time Performance Year-to-date through January 2008
Year-to-date numbers for all years
Year | Operations | Late Arrivals | Late Departures | Cancelled | Diverted | Percent On-Time Arrivals |
Percent Late Arrivals | Percent Late Departures | Percent Cancelled | Percent Diverted

2003 | 552,109 | 82,900 | 66,453 | 8,341 | 847 | 83.32 | 15.02 | 12.04 | 1.51 | 0.15
2004 | 583,987 | 128,236 | 101,072 | 17,611 | 1,015 | 74.85 | 21.96 | 17.31 | 3.02 | 0.17
2005 | 594,924 | 143,316 | 122,637 | 25,084 | 1,809 | 71.39 | 24.09 | 20.61 | 4.22 | 0.30
2006 | 581,287 | 112,299 | 100,690 | 9,787 | 1,370 | 78.76 | 19.32 | 17.32 | 1.68 | 0.24
2007 | 621,559 | 150,144 | 128,259 | 15,777 | 1,200 | 73.11 | 24.16 | 20.64 | 2.54 | 0.19
2008 | 605,448 | 148,725 | 128,009 | 17,293 | 1,325 | 72.36 | 24.56 | 21.14 | 2.86 | 0.22
```

### SQL and result

```sql
select doc_id, chunk_id, page_start, kind, text
from chunk_index
where doc_id = 'company/group-02/059190.csv'
  and (text ilike '%Summary of Airline%' or text ilike '2003 |%')
order by page_start;
```

Result: 3 rows, all page 1. The header is in 7c1f879c-2750-5733-b098-b1524c5bc85a (kind table, with the
1995-2002 rows); the 2003-2008 block is in e49ba066-13aa-5b75-a421-c8027eb59a83 (kind table); the 2003 row
alone is also in 7ce2e026-2334-5ea3-b904-9a374b6b0470 (kind table_row).

```sql
select doc_id, count(*) from chunk_index
where text ilike '%552,109%'
group by doc_id;
```

Result: company/group-02/059190.csv | 1. The value exists only in the named document.

### Why not a duplicate

No reference to company/group-02/059190.csv in the suite or the four probes (grep: 0 matches in each). The
existing table questions N01-N08 and X01-X08 use the census DP03/S0802 CSVs (471787, 048411, 294613); none
uses the Bureau of Transportation Statistics on-time table or asks for an operations count. File type: .csv.

## CAND_UNTOUCHED_06 — .xls — company/group-02/016886.xls

**Question:** According to the person-month conversion guidance, what monthly salary corresponds to an
academic year appointment with a salary of 63,000 dollars?

**Expected:** staff answered, docs ["company/group-02/016886.xls"], contains ["7,000"].

### Passage (page 1, chunk dff5935a-d6f9-58c7-ab05-f38ffdbb7b52, with the neighbouring examples from chunk df5526d3)

```text
A PI on an AY appointment at a salary of $63,000 will have a monthly salary of $7,000 (one-ninth of the AY).
25% of AY effort would equate to 2.25 person months (9x.25=2.25).  The Budget figure for that effort would be
$15,750 (7,000 multiplied by 2.25 AY months).
A PI on a CY appointment at a salary of $72,000 will have a monthly salary of $6,000 (one-twelfth of total CY
salary).
```

The sheet is titled "Percent of Time & Effort to Person Months (PM)" and states the three bases:
"Calendar Year, Academic Year and Summer Term." The AY case is the 63,000/7,000 pair; the CY case is the
72,000/6,000 pair.

### SQL and result

```sql
select doc_id, chunk_id, page_start, kind, text
from chunk_index
where doc_id = 'company/group-02/016886.xls'
  and text ilike '%63,000%';
```

Result: 2 rows, both page 1. The carrying row is
company/group-02/016886.xls | dff5935a-d6f9-58c7-ab05-f38ffdbb7b52 | 1 | table_row.
The other row is df5526d3-9d5b-55a5-9ddf-80a96a809a08 (kind table), the assembled sheet with both examples.

### Why not a duplicate

No reference to company/group-02/016886.xls in the suite or the four probes (grep: 0 matches in each). No
existing question is about person-month conversion, academic-year appointments, or a spreadsheet budget
example. The nearest numeric questions are N01-N08 on census tables; the access questions A01-A04 concern
executive compensation. File type: .xls.

## CAND_UNTOUCHED_07 — .htm — company/apple-10k-2024.htm

**Question:** How many full-time equivalent employees did Apple report as of September 28, 2024?

**Expected:** staff answered, docs ["company/apple-10k-2024.htm"], contains ["164,000"].

### Passage (page 1, chunk bac48a4a-eb6c-5ce3-ab3e-abe2eb8c7a86)

```text
Human Capital

The Company believes that its people play an important role in its success, and strives to attract, develop
and retain the best talent. The Company works to create an inclusive, safe and supportive environment for
all of its team members, so that its people can do the best work of their lives. As of September 28, 2024,
the Company had approximately 164,000 full-time equivalent employees.

Compensation and Benefits

The Company believes that compensation should be competitive and equitable, and should enable employees to
share in the Company's success.
```

### SQL and result

```sql
select doc_id, chunk_id, page_start, kind, text
from chunk_index
where doc_id = 'company/apple-10k-2024.htm'
  and text ilike '%164,000%';
```

Result: 1 row, company/apple-10k-2024.htm | bac48a4a-eb6c-5ce3-ab3e-abe2eb8c7a86 | 1 | prose.

```sql
select doc_id, count(*) from chunk_index
where text ilike '%164,000 full-time equivalent%'
group by doc_id;
```

Result: company/apple-10k-2024.htm | 1. The value exists only in the named document.

### Why not a duplicate

No reference to company/apple-10k-2024.htm in docs/eval/data-questions.yaml or
tests/fixtures/data_suite_run.py (grep: 0 matches in each). No existing question is about Apple, a 10-K, or
headcount. Note: tests/test_eval_harness.py uses this doc_id only as the carrier for a synthetic net-sales
figure in grader unit tests; it is not a suite question or one of the four data probes, and this candidate
asks a different fact. File type: .htm.

## CAND_UNTOUCHED_08 — .pdf — company/irs-p15.pdf

**Question:** For calendar year 2025, what is the social security wage base limit for each employee?

**Expected:** staff answered, docs ["company/irs-p15.pdf"], contains ["176,100"].

### Passage (page 39, chunk 263d3bf7-6336-5a79-a33e-2766cfe76d9f, row also in 8ecaf791-af1f-5354-a2bc-25d43e68b5a7)

```text
12. Filing Forms 941, Form 943, Form 944, or Form 945
Calendar year: 2025-social security; Wage base limit (each employee): $176,100; Tax rate on taxable wages and
tips: 12.4%
Calendar year: 2025-Medicare; Wage base limit (each employee): All wages; Tax rate on taxable wages and tips:
2.9%
Calendar year: 2024-social security; Wage base limit (each employee): $168,600; Tax rate on taxable wages and
tips: 12.4%
Calendar year: 2024-Medicare; Wage base limit (each employee): All wages; Tax rate on taxable wages and tips:
2.9%
Calendar year: 2023-social security; Wage base limit (each employee): $160,200; Tax rate on taxable wages and
tips: 12.4%*
Calendar year: 2023-Medicare; Wage base limit (each employee): All wages; Tax rate on taxable wages and tips:
2.9%
```

### SQL and result

```sql
select doc_id, chunk_id, page_start, kind, text
from chunk_index
where doc_id = 'company/irs-p15.pdf'
  and text ilike '%176,100%';
```

Result: 2 rows, both page 39. The assembled table is
company/irs-p15.pdf | 263d3bf7-6336-5a79-a33e-2766cfe76d9f | 39 | table; the single 2025 social security
row is 8ecaf791-af1f-5354-a2bc-25d43e68b5a7 | 39 | table_row.

```sql
select doc_id, count(*) from chunk_index
where text ilike '%176,100%'
group by doc_id;
```

Result: company/irs-p15.pdf | 2. Both rows are in the named document; no other document carries the value.

### Why not a duplicate

No reference to company/irs-p15.pdf in docs/eval/data-questions.yaml or tests/fixtures/data_suite_run.py
(grep: 0 matches in each). No existing question is about employment tax, IRS Circular E, or a wage base.
Note: tests/test_eval_harness.py uses this doc_id with a synthetic wage-base passage, not the indexed text;
that is a grader unit test, not a suite question or a data probe. File type: .pdf.

## CAND_UNTOUCHED_09 — .md canary — company/canary-pii.md

**Question:** According to the vendor onboarding note, where is the primary contact reference for the Acme
engagement stored?

**Expected:** staff answered, docs ["company/canary-pii.md"], contains ["vendor record"].

### Passage (page 1, chunk 13af6c6a-4f32-5919-98f2-af5bd3c13332, the whole chunk)

```text
Vendor onboarding reference
This internal note records the reference numbers the onboarding team uses.

The primary contact reference for the Acme engagement is 219-09-9999 and it is stored in the vendor record.
The escalation reference is 078-05-1120.

These are reserved test values and are not real identifiers.

219-09-9999 and 078-05-1120 are the social security numbers the United States uses for advertising and
documentation, and they belong to nobody. The file exists so that the streaming redaction has something real
to catch: a question about the Acme engagement retrieves this file, the model repeats the number, and the
response has to reach the client with it redacted.

If this canary ever appears in a response, the redaction is not working and the deployment is not safe to
show a client.

The probe asserts over every frame the client received rather than over the assembled answer, because an
answer assembled from a stream is exactly what an audit-only control redacts.
```

### SQL and result

```sql
select doc_id, chunk_id, page_start, kind, text
from chunk_index
where doc_id = 'company/canary-pii.md';
```

Result: 1 row, company/canary-pii.md | 13af6c6a-4f32-5919-98f2-af5bd3c13332 | 1 | prose.

```sql
select doc_id, count(*) from chunk_index
where text ilike '%vendor record%'
group by doc_id;
```

Result: company/canary-pii.md | 1. The value exists only in the named document.

### Why not a duplicate

No reference to company/canary-pii.md in docs/eval/data-questions.yaml or
tests/fixtures/data_suite_run.py (grep: 0 matches in each). tests/fixtures/stream_probe.py names
company/pii-new-starter.md as its current canary and mentions the old fixtures/corpus/canary-pii.md path in
its docstring; it does not name company/canary-pii.md as the deployed canary. This candidate also asks
where the reference is stored, not for the reserved number the redaction gate strips, so it does not
duplicate the stream probe's redaction assertion. File type: .md.

## CAND_UNTOUCHED_10 — .rtf — company/group-01/380391.rtf

**Question:** Under the ACORP Appendix 8 request form, where must procedures involving an explosive agent be
performed?

**Expected:** staff answered, docs ["company/group-01/380391.rtf"], contains ["properly operating,
ventilated safety hood"].

### Passage (page 1, chunk 0fe13431-86f5-5a4a-b3ac-0791d64c75d0)

```text
9. Give a detailed description of the procedure(s) involving the explosive agent(s) including assurances
that:  a) procedures are performed within a properly operating, ventilated safety hood, b) all electrical
equipment used with the agent are placed and powered outside the hood, c) once the seal is broken on
containers of ether or other explosive anesthetic agents, they will be placed into a safety hood throughout
use, stored in an explosion proof refrigerator or other approved storage area, and discarded properly once
completely used, and d) that proper disposal procedures for items (including carcasses) containing traces of
the agent will be safe and appropriate.  (When finished, proceed to item Y on the ACORP.)
```

The document opens with "ACORP Appendix 8 / Request to Use Explosive Agent in the Animal Facility or in
Animals / Version 3" and ends with "Reviewed/Updated Date:  February 25, 2005."

### SQL and result

```sql
select doc_id, chunk_id, page_start, kind, text
from chunk_index
where doc_id = 'company/group-01/380391.rtf'
  and text ilike '%properly operating, ventilated safety hood%';
```

Result: 1 row, company/group-01/380391.rtf | 0fe13431-86f5-5a4a-b3ac-0791d64c75d0 | 1 | prose.

```sql
select doc_id, count(*) from chunk_index
where text ilike '%properly operating, ventilated safety hood%'
group by doc_id;
```

Result: company/group-01/380391.rtf | 1. The value exists only in the named document.

### Why not a duplicate

No reference to company/group-01/380391.rtf in the suite or the four probes (grep: 0 matches in each). No
existing question is about an animal-facility form, explosive agents, or a safety hood. The nearest
procedural questions are P05/P06 on the sample post orders (104644.doc), a different document and subject.
File type: .rtf.
