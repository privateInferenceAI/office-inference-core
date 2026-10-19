# Aggregation and multi-row reasoning - evidence

Eight candidates for the aggregation axis. Each answer is arithmetic over several rows of one indexed table,
not a single-cell lookup. All four source documents are staff-visible company/ files, so each candidate
carries only the staff expectation.

Ground truth was read from chunk_index on the deployment, which is the text the retriever sees. The passages
below are verbatim, and every operand is quoted. The SQL shown is the exact query run against the deployment's
Postgres, with its output. I also ran the documented retrieval-only check for each question
(tests/fixtures/ask.py --role staff --limit 5), which stops before generation; the result is recorded so a
reviewer can see that the carrying passage is reachable.

**Duplicate checks.** None of the four documents appears in docs/eval/data-questions.yaml or in
tests/fixtures/data_suite_run.py; grep returns 0 matches for each doc id in each file. The two excluded
documents, company/group-02/410027.txt and executive/group-03/176020.xls, are not used. The nearest existing
questions are named under each candidate.

**How to check one in under a minute.** Read the passage, do the arithmetic on the quoted operands, compare
with the SQL result. The query can be pasted into psql on the box and reads only chunk_index.

**Column positions used below.**

- 770592.csv: 1 county, 2 state, 3 April 1, 1990 estimates base, 4 through 13 July 1, 1990 through July 1,
  1999 estimates, 14 April 1, 2000 census.
- 970969.csv: 1 carrier, 2 full time, 3 part time, 4 total.
- 468293.csv: 1 age, 2 total in public school, 3 nursery/preschool, 4 kindergarten, then the grade bands.
- 159927.xls: 1 merchant, 2 state, 3 city or phone, 4 amount for the 4th quarter of 1996 through the 3rd
  quarter of 1997.

---

## CAND_AGGREGATION_01 - sum of three county rows - company/group-02/770592.csv

**Question:** In the Nebraska county population table, what was the combined April 1, 2000 census population
of Adams, Antelope, and Arthur counties?

**Expected:** staff answered; docs ["company/group-02/770592.csv"]; contains ["39,047"].

### Passage (page 1, chunk 64e809d6-1257-5969-a2d2-58642b6f2d1f, kind table)

~~~text
Table with row headers in column A and column headers in row 3.
Time Series of Nebraska Intercensal Population Estimates by County: April 1, 1990 to April 1, 2000
County | State | April 1, 1990 Population Estimates Base | July 1, 1990 Estimate | July 1, 1991 Estimate | July 1, 1992 Estimate | July 1, 1993 Estimate | July 1, 1994 Estimate | July 1, 1995 Estimate | July 1, 1996 Estimate | July 1, 1997 Estimate | July 1, 1998 Estimate | July 1, 1999 Estimate | April 1, 2000 Census
Nebraska | 1,578,417 | 1,581,660 | 1,595,919 | 1,611,687 | 1,625,590 | 1,639,041 | 1,656,992 | 1,673,740 | 1,686,418 | 1,695,816 | 1,704,764 | 1,711,263
Adams | Nebraska | 29,625 | 29,639 | 29,918 | 30,047 | 30,320 | 30,508 | 30,783 | 30,890 | 31,050 | 31,054 | 31,106 | 31,151
Antelope | Nebraska | 7,965 | 7,942 | 7,941 | 7,879 | 7,793 | 7,733 | 7,717 | 7,639 | 7,645 | 7,604 | 7,493 | 7,452
Arthur | Nebraska | 462 | 468 | 445 | 462 | 472 | 441 | 434 | 429 | 440 | 447 | 445 | 444
Banner | Nebraska | 852 | 860 | 822 | 833 | 835 | 835 | 823 | 851 | 855 | 856 | 804 | 819
~~~

The chunk continues with the next counties. The state total row is not an operand; it is shown because it is
part of the passage.

### Arithmetic

31,151 + 7,452 + 444 = 39,047.

### SQL and result

~~~sql
select sum(replace(split_part(text,' | ',14),',','')::numeric) as result
from chunk_index
where doc_id='company/group-02/770592.csv'
  and kind='table_row'
  and split_part(text,' | ',1) in ('Adams','Antelope','Arthur');
~~~

~~~text
result
39047
~~~

### Source-table uniqueness

~~~sql
select doc_id, count(*) from chunk_index
where text like '%Nebraska Intercensal Population%'
group by doc_id;
~~~

Result: company/group-02/770592.csv | 4. The table exists only in the named document.

### Retrieval check

ask.py returned five passages, all from 770592.csv. Passage 1 is chunk 64e809d6, which carries the year header
and all three rows; passages 2, 4 and 5 are the Adams, Arthur and Antelope row chunks. No other document was
retrieved.

### Why not a duplicate

No suite question or probe names 770592.csv. The nearest are N01 through N07, each a single DP03 or S0802 row
in 471787.csv, 048411.csv or 294613.csv, and X01 through X08, each a difference between two named census
values. This is a sum of three county rows in a Nebraska time series that neither set touches. PR1 and PR4
use 048411.csv and 294613.csv, PR2 uses 410027.txt, and PR3 is the MARS refusal probe.

---

## CAND_AGGREGATION_02 - difference between two county rows - company/group-02/770592.csv

**Question:** In the Nebraska county population table, how many more residents did Boone County have than
Boyd County on April 1, 2000?

**Expected:** staff answered; docs ["company/group-02/770592.csv"]; contains ["3,821"].

### Passage (page 1, chunk 917d5df0-400e-5c5f-84aa-f4dced16b135, kind table)

The year labels are in the header chunk quoted for CAND_AGGREGATION_01. The rows below are verbatim from the
table chunk; Box Butte, Brown, Buffalo, Burt and Butler also sit in this chunk and are not operands.

~~~text
Boone | Nebraska | 6,667 | 6,668 | 6,675 | 6,592 | 6,578 | 6,517 | 6,506 | 6,526 | 6,465 | 6,400 | 6,376 | 6,259
Boyd | Nebraska | 2,835 | 2,830 | 2,769 | 2,732 | 2,726 | 2,707 | 2,705 | 2,666 | 2,608 | 2,508 | 2,458 | 2,438
~~~

### Arithmetic

6,259 - 2,438 = 3,821.

### SQL and result

~~~sql
select max(x)-min(x) as result, max(x) as hi, min(x) as lo
from (
  select replace(split_part(text,' | ',14),',','')::numeric as x
  from chunk_index
  where doc_id='company/group-02/770592.csv'
    and kind='table_row'
    and split_part(text,' | ',1) in ('Boone','Boyd')
) v;
~~~

~~~text
result | hi   | lo
3821   | 6259 | 2438
~~~

### Retrieval check

ask.py returned chunk 64e809d6 (header and first counties) as passage 1, the Boone row as passage 2, chunk
917d5df0 as passage 4 and the Boyd row as passage 5. Both operands and the year header are in the retrieved set.

### Why not a duplicate

Same document, which no suite question or probe names. X01 through X08 are also two-value differences, but
all compare census income, earnings or household-count figures in group-02 DP03 and S0802 files; none compares
two rows of the Nebraska county series. The Boone-minus-Boyd fact is not the answer or the premise of any
existing question.

---

## CAND_AGGREGATION_03 - range across three county rows - company/group-02/770592.csv

**Question:** Among Boyd, Brown, and Buffalo counties in the Nebraska population table, what is the difference
between the largest and smallest April 1, 2000 populations?

**Expected:** staff answered; docs ["company/group-02/770592.csv"]; contains ["39,821"].

### Passage (page 1, chunk 917d5df0-400e-5c5f-84aa-f4dced16b135, kind table)

The year labels are in the header chunk quoted for CAND_AGGREGATION_01.

~~~text
Boyd | Nebraska | 2,835 | 2,830 | 2,769 | 2,732 | 2,726 | 2,707 | 2,705 | 2,666 | 2,608 | 2,508 | 2,458 | 2,438
Brown | Nebraska | 3,657 | 3,656 | 3,676 | 3,676 | 3,673 | 3,662 | 3,664 | 3,642 | 3,631 | 3,574 | 3,559 | 3,525
Buffalo | Nebraska | 37,447 | 37,603 | 38,195 | 38,774 | 39,515 | 39,834 | 40,381 | 41,084 | 41,310 | 41,716 | 41,904 | 42,259
~~~

### Arithmetic

Buffalo is the largest at 42,259; Boyd is the smallest at 2,438. 42,259 - 2,438 = 39,821.

### SQL and result

~~~sql
select max(x)-min(x) as result, max(x) as hi, min(x) as lo
from (
  select replace(split_part(text,' | ',14),',','')::numeric as x
  from chunk_index
  where doc_id='company/group-02/770592.csv'
    and kind='table_row'
    and split_part(text,' | ',1) in ('Boyd','Brown','Buffalo')
) v;
~~~

~~~text
result | hi    | lo
39821  | 42259 | 2438
~~~

### Retrieval check

ask.py returned chunk 64e809d6 as passage 1 and chunk 917d5df0 as passage 2, which carries all three rows; the
Boyd row also came back as passage 4. All three operands are in the retrieved set.

### Why not a duplicate

Same document. The suite has no maximum-or-minimum question at all: X06 compares two named values and asks
which is higher, while this asks for the range across three rows. No question names Boyd, Brown, Buffalo or
770592.csv, and the fact is not a premise elsewhere.

---

## CAND_AGGREGATION_04 - sum of three airline rows - company/group-01/970969.csv

**Question:** In the 1993 year-end certificated carrier employment table, how many full-time employees did
American, Delta, and United have combined?

**Expected:** staff answered; docs ["company/group-01/970969.csv"]; contains ["222,108"].

### Passage (page 1, chunk c764997d-9e4e-5b43-8350-9a44ff66fe8a, kind table)

~~~text
Number of Employees
Certificated Carriers
Year End Data
1993
CARRIER GROUP | FULL | PART
NAME | TIME | TIME | TOTAL
MAJORS
America West | 8,102 | 3,117 | 11,219
American | 83,047 | 12,901 | 95,948
Continental | 32,068 | 6,068 | 38,136
Delta | 65,212 | 5,916 | 71,128
Federal Express | 67,726 | 32,610 | 100,336
Northwest | 41,605 | 1,795 | 43,400
Southwest | 13,232 | 52 | 13,284
Trans World | 25,180 | 2,620 | 27,800
United | 73,849 | 7,662 | 81,511
United Parcel | 3,382 | 209 | 3,591
USAIR | 43,888 | 2,779 | 46,667
Total Majors | 457291 | 75729 | 533020
~~~

The chunk continues with the NATIONALS, Large Regionals and Medium Regionals groups; they are not operands.

### Arithmetic

83,047 + 65,212 + 73,849 = 222,108.

### SQL and result

~~~sql
select sum(replace(split_part(text,' | ',2),',','')::numeric) as result
from chunk_index
where doc_id='company/group-01/970969.csv'
  and kind='table_row'
  and split_part(text,' | ',1) in ('American','Delta','United');
~~~

~~~text
result
222108
~~~

### Source-table uniqueness

~~~sql
select doc_id, count(*) from chunk_index
where text like '%Total Majors%'
group by doc_id;
~~~

Result: company/group-01/970969.csv | 2. The carrier table exists only in the named document.

### Retrieval check

ask.py returned chunk c764997d (header, all eleven majors and the Total Majors row) as passage 1. The
remaining passages were a later chunk of the same carrier table, an unrelated census table and two prose
passages; all operands were in passage 1.

### Why not a duplicate

No suite question or probe names 970969.csv. The nearest are the census number questions N01 through N07 and
the cross-document differences X01 through X08; none concerns airline employment or sums a column. PR3 asks
about a MARS run and is a refusal probe on a data dump, not this table.

---

## CAND_AGGREGATION_05 - difference between two airline rows - company/group-01/970969.csv

**Question:** In the 1993 year-end certificated carrier employment table, how many more total employees did
Federal Express have than United?

**Expected:** staff answered; docs ["company/group-01/970969.csv"]; contains ["18,825"].

### Passage (page 1, chunk c764997d-9e4e-5b43-8350-9a44ff66fe8a, kind table)

The header is quoted in CAND_AGGREGATION_04; the two rows are verbatim:

~~~text
Federal Express | 67,726 | 32,610 | 100,336
United | 73,849 | 7,662 | 81,511
~~~

### Arithmetic

100,336 - 81,511 = 18,825. The last field in each row is TOTAL in the header quoted above.

### SQL and result

~~~sql
select max(x)-min(x) as result, max(x) as hi, min(x) as lo
from (
  select replace(split_part(text,' | ',4),',','')::numeric as x
  from chunk_index
  where doc_id='company/group-01/970969.csv'
    and kind='table_row'
    and split_part(text,' | ',1) in ('Federal Express','United')
) v;
~~~

~~~text
result | hi     | lo
18825  | 100336 | 81511
~~~

### Retrieval check

ask.py returned chunk c764997d as passage 1, which carries both rows; the remaining passages were a title row
from the same document and unrelated tables and prose. Both operands are in passage 1.

### Why not a duplicate

Same document. No existing question uses the carrier table's TOTAL column or compares Federal Express with
United. X01 through X08 subtract named census values; this subtracts two airline totals.

---

## CAND_AGGREGATION_06 - printed total minus three rows - company/group-01/970969.csv

**Question:** In the 1993 year-end certificated carrier employment table, how many employees in total,
full-time plus part-time, did the major carriers other than American, Delta, and United have?

**Expected:** staff answered; docs ["company/group-01/970969.csv"]; contains ["284,433"].

### Passage (page 1, chunk c764997d-9e4e-5b43-8350-9a44ff66fe8a, kind table)

The header and the full majors block are quoted in CAND_AGGREGATION_04. The operands are:

~~~text
Total Majors | 457291 | 75729 | 533020
American | 83,047 | 12,901 | 95,948
Delta | 65,212 | 5,916 | 71,128
United | 73,849 | 7,662 | 81,511
~~~

### Arithmetic

533,020 - 95,948 - 71,128 - 81,511 = 284,433.

Cross-check from the other eight rows: 11,219 + 38,136 + 100,336 + 43,400 + 13,284 + 27,800 + 3,591 +
46,667 = 284,433.

### SQL and result

~~~sql
select
  (select replace(split_part(text,' | ',4),',','')::numeric
     from chunk_index
    where doc_id='company/group-01/970969.csv'
      and kind='table_row'
      and split_part(text,' | ',1)='Total Majors')
  - sum(replace(split_part(text,' | ',4),',','')::numeric) as result
from chunk_index
where doc_id='company/group-01/970969.csv'
  and kind='table_row'
  and split_part(text,' | ',1) in ('American','Delta','United');
~~~

~~~text
result
284433
~~~

Cross-check query:

~~~sql
select sum(replace(split_part(text,' | ',4),',','')::numeric) as result
from chunk_index
where doc_id='company/group-01/970969.csv'
  and kind='table_row'
  and split_part(text,' | ',1) in
      ('America West','Continental','Federal Express','Northwest','Southwest',
       'Trans World','United Parcel','USAIR');
~~~

~~~text
result
284433
~~~

### Retrieval check

ask.py returned chunk c764997d as passage 1, which carries the Total Majors row and all three named rows.

### Why not a duplicate

Same document. No existing question uses a printed table total as a subtrahend or asks for the complement of
named rows. X07 and X08 subtract one named count from another; this subtracts three named rows from a printed
total and cross-checks the answer against the remaining eight rows.

---

## CAND_AGGREGATION_07 - sum of three age rows - company/group-01/468293.csv

**Question:** In the Census 2000 school enrollment table that reports 1,230,550 people enrolled in public
school, how many people aged 3, 4, and 5 were enrolled in total?

**Expected:** staff answered; docs ["company/group-01/468293.csv"]; contains ["86,294"].

### Passage (page 1, chunk 39095b2c-819e-522f-a68c-5282609f3944, kind table)

The header line is quoted through Kindergarten; the remaining column headers are Grade 1 to 4, Grade 5 to 8,
Grade 9 to 12, College, and Graduate/professional school.

~~~text
(Data based on a sample.  For information on confidentiality protection, sampling error, nonsampling error and definitions, see www.census.gov/prod/cen2000/doc/sf3.pdf.)

Total in public school | Nursery school, preschool | Kindergarten
..Total | 1,230,550 | 56,045 | 66,597 | 278,685 | 280,868 | 279,656 | 225,950 | 42,749
3 years | 14,455 | 14,176 | 279 | - | - | - | - | -
4 years | 26,599 | 25,258 | 1,341 | - | - | - | - | -
5 years | 45,240 | 16,611 | 27,021 | 1,608 | - | - | - | -
6 years | 63,955 | - | 35,917 | 28,038 | - | - | - | -
~~~

The chunk continues with ages 7 through 13, which are not operands.

### Arithmetic

14,455 + 26,599 + 45,240 = 86,294.

### SQL and result

~~~sql
select sum(replace(split_part(line,' | ',2),',','')::numeric) as result
from (
  select regexp_split_to_table(text, E'\n') as line
  from chunk_index
  where chunk_id='39095b2c-819e-522f-a68c-5282609f3944'
) s
where split_part(line,' | ',1) in ('3 years','4 years','5 years');
~~~

~~~text
result
86294
~~~

### Source-table uniqueness

~~~sql
select doc_id, count(*) from chunk_index
where text like '%1,230,550%'
group by doc_id;
~~~

Result: company/group-01/468293.csv | 2. The 1,230,550 total exists only in the named document, which anchors
the question to this table rather than the other blocks in the same workbook.

### Retrieval check

ask.py returned chunk 39095b2c as passage 1, with the header, the total and the 3, 4 and 5 year rows.

### Why not a duplicate

No suite question or probe names 468293.csv. N01 through N07 and X01 through X08 use the group-02 census
files; P01 through P08 are prose facts. No question asks for a sum over age rows, and the 1,230,550 anchor is
unique to this document.

---

## CAND_AGGREGATION_08 - difference between two merchant rows - company/group-02/159927.xls

**Question:** In the merchant acceptance list covering the 4th quarter of 1996 through the 3rd quarter of 1997,
how much more was the merchant acceptance amount for GSA/CUST SUPPLY CTR 84 than for JOHNSON & JOHNSON?

**Expected:** staff answered; docs ["company/group-02/159927.xls"]; contains ["9,076,788"].

### Passage (page 1, chunk d11dcba3-f778-5b0e-b3b6-be6d37cf4987, kind table)

~~~text
Merchant Name | Merchant State | Merchant City/Phone | 4th Qtr 96 - 3rd Qtr 97 | Merchant Acceptance
AMERISOURCE CORPORATION | NJ | 609-3842182 | 118700887.82
GTSI | VA | CHANTILLY | 35183546.51
GATEWAY 2000 INC | SD | 800-846-2000 | 29048650.67
GSA/CUST SUPPLY CTR 84 | TX | FT WORTH | 26649830.44
DELL COMPUTERS | TX | 800-274-1140 | 25404702.33
CDW* COMPUTER CENTERS | IL | 800-800-4239 | 17736854.38
JOHNSON & JOHNSON | NJ | PISCATAWAY | 17573042.44
~~~

The chunk continues down the merchant list; the rows shown are from the top of the table, and the rows after
J&J are not operands.

### Arithmetic

26,649,830.44 - 17,573,042.44 = 9,076,788.00. The difference is exact to the cent, so the answer can be
written with or without the .00.

### SQL and result

~~~sql
select max(x)-min(x) as result, max(x) as hi, min(x) as lo
from (
  select replace(split_part(text,' | ',4),',','')::numeric as x
  from chunk_index
  where doc_id='company/group-02/159927.xls'
    and kind='table_row'
    and split_part(text,' | ',1) in ('GSA/CUST SUPPLY CTR 84','JOHNSON & JOHNSON')
) v;
~~~

~~~text
result     | hi            | lo
9076788.00 | 26649830.44   | 17573042.44
~~~

### Source-table uniqueness

~~~sql
select doc_id, count(*) from chunk_index
where text like '%Merchant Acceptance%'
group by doc_id;
~~~

Result: company/group-02/159927.xls | 2. The merchant acceptance table exists only in the named document.

### Retrieval check

ask.py returned the GSA/CUST SUPPLY CTR 84 row as passage 1 and chunk d11dcba3, which carries the header and
both operands, as passage 2.

### Why not a duplicate

No suite question or probe names 159927.xls. The nearest single-value money question is N08, the Acurx
assumed public offering price of 1.45 dollars in an S-1; the census questions N01, N02, N05, N06 and X01
through X05 are dollar figures from a different document family. This is a two-row difference in a merchant
acceptance table.

---

## Candidates considered and rejected

1. **Count of major carriers with more than 5,000 part-time employees (970969.csv).** The count is five. A
   contains check on "5" would also match the threshold "5,000" in the answer, and "five" versus "5" makes
   the expected string brittle. Not submitted.
2. **Douglas County's share of Nebraska's April 1, 2000 population (770592.csv).** 463,585 / 1,711,263 is
   27.09 percent. Rounding to 27, 27.1 or 27.09 are all defensible, so a single expected string would punish
   a correct calculation. Not submitted.
3. **Box Butte compared with Boone (770592.csv).** The indexed text has a non-breaking space inside "Box
   Butte" (bytes c2 a0), so the exact row cannot be reproduced with a normal space in a quote or a question.
   Replaced by Boone and Boyd. Not submitted.
4. **Indiana population by age (company/group-02/722731.xls).** The same age label appears three times for
   male, female and both sexes, and the row text does not carry the sex. A question would be ambiguous
   without the surrounding section header. Not submitted.
5. **Department of Defense TOA by title (company/group-02/552515.xls).** The title rows are clean, but the
   year labels for the eleven numeric columns are not in the indexed chunks, so a column cannot be named
   unambiguously. Not submitted.

Draft status: these are candidates for review, not the live suite.
