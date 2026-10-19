# Pollution probe candidates (PR4 replacement)

Status: draft probe candidates, not part of the suite. Nothing here is scored until a reviewer accepts it and
the suite changes. Prepared 2026-10-19.

The deployment work was read-only: chunk_index queries and ask.py retrieval runs against /opt/oi-core. No
corpus, deployment, configuration, suite, or git change was made.

## Candidates at a glance

| id | question | fact document | value | dump slots in top 5 |
|---|---|---|---|---|
| POL_01 | How many high school students did Broste study? | company/group-01/007357.txt | 872 | 4 of 5 |
| POL_02 | By June 2000, what percentage of TV households were served by cable television systems? | company/group-01/012148.doc | 67.4 | 3 of 5 |
| POL_03 | What was the percentage decrease in the SE isolation rate from 1996 to 1998? | company/group-02/330712.txt | 44 | 2 of 5 |

The dump count is the number of table_row or table passages in the top five. The exact dump documents vary
between runs; the fact document is present in every run and the dump count is stable. The strongest candidate
is POL_01.

## Method

The SQL below ran through the documented SSH wrapper. The wrapper I used is:

~~~bash
ssh -i /home/anthony/ai-stack/ai-stack-key.pem -o StrictHostKeyChecking=no ubuntu@52.204.202.66 'bash -s' <<'EOF'
cd /opt/oi-core
USER=$(grep -E "^POSTGRES_USER=" rendered/env | cut -d= -f2)
DB=$(grep -E "^POSTGRES_DB=" rendered/env | cut -d= -f2)
docker exec postgres psql -U "$USER" -d "$DB" -P pager=off -A -F ' | ' -c "<query>" < /dev/null
EOF
~~~

The < /dev/null keeps psql from reading the rest of the heredoc as SQL. Each candidate shows the exact query
string substituted for <query>.

Retrieval ran through the real retriever, role staff, default limit 5, dense and lexical arms, access filter,
and reranker. The shell function used for every run is:

~~~bash
ask_staff () {
  ssh -i /home/anthony/ai-stack/ai-stack-key.pem -o StrictHostKeyChecking=no ubuntu@52.204.202.66 "cd /opt/oi-core && docker compose -f deploy/office/compose/docker-compose.yml --env-file rendered/env run -T --rm --no-deps --entrypoint python -v /opt/oi-core:/w:ro -w /w -e ACCESS_REGISTRY=/w/rendered/access.json eval /w/tests/fixtures/ask.py --role staff --json --question '$1'"
}
~~~

The output in each section is trimmed from that JSON. The fields shown are n, doc_id, kind, score, pages,
chunk_id, and the first line of text. The score is the dense cosine similarity. A null score means the passage
came from the lexical arm only. The passage order is the final reranked order, so scores are not monotonic.
The arms field is the retriever's count of dense and lexical candidates. Denied is the access-boundary flag.

All three candidates are company ACL documents, visible to staff and executive. The retrieval runs below are
staff runs. Each returned denied false. The suite runs non-access probes for staff only, so the executive
expectation is recorded for completeness.

## Why PR4 cannot stay

PR4 and N06 ask for the same fact, from the same document, with the same expected value.

- N06: "In the census table that reports male median earnings of 33,270 dollars, what is the overall median earnings figure for workers 16 years and over?" Expected: company/group-02/294613.csv, contains 31,638.
- PR4: "What is the median earnings figure for workers 16 years and over?" Expected: company/group-02/294613.csv, contains 31,638.

The carrying chunk for 31,638 is chunk 2a56cc02-2004-5039-baf0-d79d8aeb3953, kind table_row, doc
company/group-02/294613.csv. It is the S0802 37.0 median earnings row.

Retrieval for both questions:

~~~bash
ask_staff 'What is the median earnings figure for workers 16 years and over?'
ask_staff 'In the census table that reports male median earnings of 33,270 dollars, what is the overall median earnings figure for workers 16 years and over?'
~~~

Trimmed output:

~~~text
PR4: denied false, arms {"dense": 15, "lexical": 0}
1 company/group-02/479434.csv table_row "S0802 | 28.0 | Workers 16 years and over with earnings | 64,652 ..."
2 company/group-02/294613.csv table_row "S0802 | 28.0 | Workers 16 years and over with earnings | 28,455,423 ..."
3 company/group-02/479434.csv table_row "S2409 | 1.0 | Full-time, year-round ..."
4 company/group-02/479434.csv table_row "S2404 | 1.0 | Full-time, year-round ..."
5 company/group-02/479434.csv table_row "S2402 | 1.0 | Full-time, year-round ..."

N06: denied false, arms {"dense": 15, "lexical": 0}
1 company/group-02/774677.xls table_row "Median earnings for male full-time, year-round workers (dollars) | 34250 ..."
2 company/group-02/294613.csv table_row "S2408 | 1.0 | Civilian employed population 16 years and over with earnings | 29,216,166 ..."
3 company/group-02/527124.csv table_row "DP03 | 94.0 | Median earnings for male full-time, year-round workers (dollars) | 32,497 ..."
4 company/group-02/479434.csv table_row "S2402 | 1.0 | Full-time, year-round ..."
5 company/group-02/574787.csv table_row "DP03 | 94.0 | Median earnings for male full-time, year-round workers (dollars) | 45,159 ..."
~~~

Both runs return only table_row chunks, and neither returns the carrying chunk. PR4's second passage is
294613.csv, but the S0802 28.0 row, not the S0802 37.0 row. N06's second passage is also 294613.csv, but the
S2408 1.0 row. The scoping phrase changes the dump rows, not the outcome.

The Phase B/C review already calls PR4 one of the two bad question-set probes
(docs/reviews/phase-b-c-2026-10-18.md, "Acceptance probes, final configuration"). The retrieval above shows why.
PR4 repeats N06's fact and N06's retrieval failure. It cannot measure an independent pollution effect. A
replacement needs a clean prose fact whose carrying chunk is actually retrieved while dumps still compete.

## POL_01 - Broste's high school students (872)

**Proposed probe**

~~~yaml
- id: POL_01
  category: probe_pollution
  question: "How many high school students did Broste study?"
  expected:
    staff: {answered: true, docs: ["company/group-01/007357.txt"], contains: ["872"]}
    executive: {answered: true, docs: ["company/group-01/007357.txt"], contains: ["872"]}
  source_quote: "He studied 872 high school students from Wisconsin to determine whether students engaged in farming had evidence for more hearing loss than their non-farming peers."
  evidence: "pollution-probes.md, POL_01; the carrying chunk is rank 1 and four dump chunks follow"
  what: "a clean prose count in a hearing-loss report; program and demographic tables compete in retrieval"
~~~

**Passage** (page 1, kind prose, chunk bb3f9e1c-3453-5e59-a43b-61673c105978)

~~~text
Broste, et al., in a 1989 publication, exam- ined an even younger group for evidence of noise-induced hearing loss.8 He studied 872 high school students from Wisconsin to determine whether students engaged in farming had evidence for more hearing loss than their non-farming peers. He found that about twice as many students involved in farming had evidence of early noise-induced hearing loss as compared to non-farmers.
~~~

The chunk continues with other figures: 49 dairy farmers and non-farmers, sixty-seven percent, 37 percent, and
12 percent. The expected
value is the 872 in the sentence above.

**SQL and result**

~~~sql
select doc_id, page_start, kind, chunk_id, left(text, 500)
from chunk_index
where doc_id = 'company/group-01/007357.txt'
  and text ilike '%872 high school students%';
~~~

Result: one row.

~~~text
company/group-01/007357.txt | 1 | prose | bb3f9e1c-3453-5e59-a43b-61673c105978 | Broste, et al., in a 1989 publication, ... He studied 872 high school students ...
~~~

Uniqueness:

~~~sql
select doc_id, count(*) from chunk_index
where text ilike '%872 high school students%'
group by doc_id;
~~~

Result: company/group-01/007357.txt | 1. The phrase exists only in this document.

**Retrieval evidence**

~~~bash
ask_staff 'How many high school students did Broste study?'
~~~

Trimmed output, representative run:

~~~text
denied false, arms {"dense": 15, "lexical": 1}
n doc_id                        kind      score      pages chunk
1 company/group-01/007357.txt   prose     0.51336634 1-1   bb3f9e1c-3453-5e59-a43b-61673c105978
2 company/group-02/957302.doc   table_row 0.46415886 1-1   4d2574c7-cf75-5107-8781-27dd2eeac459  "What is the number of student attendees who ..."
3 company/group-02/957302.doc   table_row 0.46388388 1-1   33c16e1e-7e6d-52a5-8d58-ac334628ae65  "High school students"
4 company/group-02/957302.doc   table_row 0.44838673 1-1   17281000-760e-5911-a28e-47100ebf7a36  "Do you study hard for tests?"
5 company/group-02/957302.doc   table_row 0.43075484 1-1   500ba679-0325-517c-8397-b9416d583734  "All students (total)"
~~~

The fact document is rank 1, and the rank-1 chunk is the carrying chunk. Repeat runs kept that rank. The other
four slots were dumps in every run. The dump set varied between 957302.doc alone and a mix of 466445.csv,
957302.doc, and 770592.csv. The repeatability section records the runs.

**What competes**

The four dump passages in the representative run are table rows from company/group-02/957302.doc, a program
form with 206 table_row chunks, 8 table chunks, and 29 prose chunks. Its retrieved rows are form questions and
categories ("High school students", "All students (total)", "Do you study hard for tests?"). In the mixed runs,
company/group-02/466445.csv (430 table_row chunks of demographic bands) and company/group-02/770592.csv (101
table_row chunks of county population rows) also appeared. None of these is a school-enrollment table. The
form rows match "high school" and "students"; the demographic and population tables compete on the question's
numeric shape. The fact document has 49 prose chunks and no table chunks.

**Fair probe expectation**

A fair run returns answered true, cites company/group-01/007357.txt, and contains 872. Retrieval includes the
fact document and the carrying chunk. The probe should not require a particular dump set, because the dump
order changes between runs.

**Failing behavior**

One failure is to cite 957302.doc, 466445.csv, or 770592.csv and answer a number from those rows, such as 1,720,
675, or 92. Another is to refuse and say the passages do not contain the student count, because the top dump
rows are bare table cells. A third is to answer 49 (the dairy farmers in the same chunk) or 1989 (the
publication year) instead of 872.

**Why not a duplicate**

No question in docs/eval/data-questions.yaml or the four probes uses 007357.txt, Broste, high school students,
or 872. The nearest existing questions are the census earnings ones, N06 and PR4, which this file is replacing.
The document is new to the suite.

## POL_02 - Cable television reach in June 2000 (67.4%)

**Proposed probe**

~~~yaml
- id: POL_02
  category: probe_pollution
  question: "By June 2000, what percentage of TV households were served by cable television systems?"
  expected:
    staff: {answered: true, docs: ["company/group-01/012148.doc"], contains: ["67.4"]}
    executive: {answered: true, docs: ["company/group-01/012148.doc"], contains: ["67.4"]}
  source_quote: "By June 2000, they served 67.4% of TV households, or 67.7 million people."
  evidence: "pollution-probes.md, POL_02; the fact document is rank 1 or 2 and three date-keyed table rows follow"
  what: "a clean prose percentage in an FCC notice; a date-keyed telephone-line workbook and a census table compete in retrieval"
~~~

**Passage** (page 1, kind prose, chunk 83fa736f-4f50-5131-9ba0-f80cb095fc14)

~~~text
Besides the changes in the broadcast and newspaper industries, there has been a proliferation of other outlets in the local media marketplace.  In 1975, cable television systems served only 13% of TV households. By June 2000, they served 67.4% of TV households, or 67.7 million people.
~~~

The chunk continues with 99%, 68%, 48%, 15%, 84%, and 56% figures. The expected value is the 67.4% in the June
2000 sentence.

**SQL and result**

~~~sql
select doc_id, page_start, kind, chunk_id, left(text, 500)
from chunk_index
where doc_id = 'company/group-01/012148.doc'
  and text ilike '%67.4% of TV households%';
~~~

Result: one row.

~~~text
company/group-01/012148.doc | 1 | prose | 83fa736f-4f50-5131-9ba0-f80cb095fc14 | Besides the changes ... By June 2000, they served 67.4% of TV households ...
~~~

Uniqueness:

~~~sql
select doc_id, count(*) from chunk_index
where text ilike '%67.4% of TV households%'
group by doc_id;
~~~

Result: company/group-01/012148.doc | 1. The phrase exists only in this document.

**Retrieval evidence**

~~~bash
ask_staff 'By June 2000, what percentage of TV households were served by cable television systems?'
~~~

Trimmed output, representative run:

~~~text
denied false, arms {"dense": 15, "lexical": 257}
n doc_id                        kind      score      pages chunk
1 company/group-01/012148.doc   prose     0.6566041  1-1   83fa736f-4f50-5131-9ba0-f80cb095fc14
2 company/group-02/615647.xls   table_row null       4-4   0a0676b0-fb86-518a-bcd9-a61b194c4062  "Jun 2000 | 159 | 188058.188 | 179648.725 | 5097.705 | 1695.652 | 1616.106"
3 company/group-02/225965.doc   prose     0.54240406 1-1   45abd303-2653-51bd-b37a-363600f6eec3  "FEDERAL COMMUNICATIONS COMMISSION ... COMMUNITY SERVED BY CABLEVISION ..."
4 company/group-02/615647.xls   table     null       2-2   1c8fc746-ec81-5175-8c35-df1650ff3e31  "65.6538151736743 64.9667140172165 ..."
5 company/group-02/615647.xls   table_row null       5-5   0797697e-97f8-5a9e-a915-91e3b99dba15  "Dec 2000 | 1124.99492 | 13746.41408"
~~~

The fact document is rank 1, and the rank-1 chunk is the carrying chunk. The June 2000 dump row is rank 2, on
page 4 of the workbook. Repeat runs put the fact document at rank 1 or rank 2, and kept three dump slots in the
top five. The dump set was the telephone-line workbook 615647.xls in the runs with the fact document at rank 1,
and 574787.csv plus 892488.doc in the runs with 225965.doc at rank 1. The repeatability section records the runs.

**What competes**

company/group-02/615647.xls is a telephone-line data workbook with 1,182 table_row chunks and 106 table chunks.
Its header is Date | ILEC Lines | CLEC Lines | Total | CLEC Share. The Jun 2000 row at rank 2 and the Dec 2000
row at rank 5 are date rows from that workbook. Neither row is cable TV data. In the variant,
company/group-02/574787.csv (607 table_row chunks of census DP02/DP03/DP04/DP05 rows) and
company/group-02/892488.doc (74 table_row chunks, 8 table chunks, 5 prose chunks of SAND publication rows)
supply the dump slots. company/group-02/225965.doc is a competing prose FCC order (15 prose chunks, 1 table
chunk), not a dump. The fact document has 67 prose chunks, 1 table chunk, and 1 table_row chunk.

**Fair probe expectation**

A fair run returns answered true, cites company/group-01/012148.doc, and contains 67.4. Retrieval includes the
fact document and the carrying chunk. The probe should not require a particular dump set, because the dump
order changes between runs.

**Failing behavior**

One failure is to cite 615647.xls and answer a number from its rows, such as 159, 188058.188, or 1124.99492.
Another is to cite 225965.doc and answer a different cable figure from that order. A third is to answer 67.7
(million people) instead of 67.4%, or to refuse because the workbook rows look like the only numeric evidence.

**Why not a duplicate**

No question in docs/eval/data-questions.yaml or the four probes uses 012148.doc, cable television, TV
households, or 67.4. The 40 questions have no media-ownership or cable question. The document is new to the
suite.

## POL_03 - SE isolation rate decrease, 1996 to 1998 (44%)

**Proposed probe**

~~~yaml
- id: POL_03
  category: probe_pollution
  question: "What was the percentage decrease in the SE isolation rate from 1996 to 1998?"
  expected:
    staff: {answered: true, docs: ["company/group-02/330712.txt"], contains: ["44"]}
    executive: {answered: true, docs: ["company/group-02/330712.txt"], contains: ["44"]}
  source_quote: "recently reported a 44 percent decrease in the isolation rate for SE (2.5 to 1.4 per 100,000 U.S. population) from 1996 to 1998 (Ref. 14B)."
  evidence: "pollution-probes.md, POL_03; the fact document occupies the top three slots and two dump rows follow"
  what: "a clean prose percentage in an FDA food-safety report; date-keyed airline and state tables compete in retrieval"
~~~

**Passage** (page 1, kind prose, chunk 000fead3-0302-5a6d-aecc-0e846676fb8b)

~~~text
an active surveillance system for foodborne pathogens, recently reported a 44 percent decrease in the isolation rate for SE (2.5 to 1.4 per 100,000 U.S. population) from 1996 to 1998 (Ref. 14B).
~~~

The chunk begins lowercase, so the sentence is a fragment at the chunk boundary. The expected value is 44.

**SQL and result**

~~~sql
select doc_id, page_start, kind, chunk_id, left(text, 500)
from chunk_index
where doc_id = 'company/group-02/330712.txt'
  and text ilike '%44 percent decrease%';
~~~

Result: one row.

~~~text
company/group-02/330712.txt | 1 | prose | 000fead3-0302-5a6d-aecc-0e846676fb8b | an active surveillance system ... recently reported a 44 percent decrease ...
~~~

Uniqueness:

~~~sql
select doc_id, count(*) from chunk_index
where text ilike '%44 percent decrease%'
group by doc_id;
~~~

Result: company/group-02/330712.txt | 1. The phrase exists only in this document.

**Retrieval evidence**

~~~bash
ask_staff 'What was the percentage decrease in the SE isolation rate from 1996 to 1998?'
~~~

Trimmed output, representative run:

~~~text
denied false, arms {"dense": 15, "lexical": 0}
n doc_id                        kind      score      pages chunk
1 company/group-02/330712.txt   prose     0.5216619  1-1   000fead3-0302-5a6d-aecc-0e846676fb8b
2 company/group-02/330712.txt   prose     0.51318336 1-1   0c08f546-b07f-5852-91d7-b30551c086f8  "occurrences of 2 or more cases ... In 1996, there were 51 reported SE outbreaks ..."
3 company/group-02/330712.txt   prose     0.5720137  1-1   a960bef8-b410-54c5-8a0a-a84d9e4d7772  "causing illness are Salmonella Enteritidis (SE) ..."
4 company/group-02/479170.csv   table_row 0.46947497 1-1   c3291866-67ab-5063-bbb5-401561bd8287  "Dec-96 | 0.80 | 0.89"
5 company/group-02/479170.csv   table_row 0.4681738  1-1   f79ea98a-dd73-5cc8-9a4f-0ef4f1677657  "Oct-98 | 0.52 | 0.64"
~~~

The fact document occupies the top three slots, and the carrying chunk is rank 1. The two dump rows are rank 4
and rank 5. Repeat runs kept the fact document in the top three and the two dump slots. The carrying chunk was
rank 1 or rank 2, and the second dump was 479170.csv or 856706.xls. The repeatability section records the runs.

**What competes**

company/group-02/479170.csv is an airline fuel table with 155 table_row chunks and 4 table chunks. Its header is
Date | Scheduled | Non-Scheduled, and its source line names the U.S. Department of Transportation, Office of
Airline Information. The Dec-96 row at rank 4 and the Oct-98 row at rank 5 are date rows from that table.
Neither row is SE data. In one repeat, company/group-01/856706.xls (440 table_row chunks, 30 table chunks of
state-level rows) supplied the second dump. The fact document has 134 prose chunks and no table chunks.

**Fair probe expectation**

A fair run returns answered true, cites company/group-02/330712.txt, and contains 44. Retrieval includes the
fact document and the carrying chunk. The probe should not require a particular dump set, because the dump
order changes between runs.

**Failing behavior**

One failure is to cite 479170.csv and answer 0.80, 0.89, 0.52, or 0.64. Another is to compute a decrease from
those rows, such as 0.28 or 35%, and present it as the answer. A third is to refuse because the prose sentence
is a fragment at the chunk boundary.

**Why not a duplicate**

No question in docs/eval/data-questions.yaml or the four probes uses 330712.txt, SE, isolation rate, Salmonella,
or 44. The 40 questions have no food-safety question. The document is new to the suite.

## Repeatability

Retrieval is not deterministic at the passage level. The dense and lexical arm counts were stable for each
question, but the order of the dump candidates varied between runs. The table records the patterns seen across
repeated runs.

| Candidate | Runs | Fact document rank | Dump slots | Dump documents seen |
|---|---|---|---|---|
| POL_01 | 6 | 1 in every run | 4 in every run | 957302.doc alone in 4 runs; 466445.csv + 957302.doc + 770592.csv in 2 runs |
| POL_02 | 5 | 1 in 2 runs; 2 in 3 runs | 3 in every run | 615647.xls in 2 runs; 574787.csv + 892488.doc in 3 runs |
| POL_03 | 5 | 1 in every run (the document holds slots 1-3; the carrying chunk is 1 or 2) | 2 in every run | 479170.csv twice in 2 runs; 479170.csv + 856706.xls in 3 runs |

The stable facts are the ones the probe should assert: the fact document, the carrying chunk in the top five,
and the value. The dump documents are not stable enough to name in an expectation.

## Recommendation

| Candidate | Fact value | Fact document rank | Dump slots | Main risk |
|---|---|---|---|---|
| POL_01 | 872 | 1 every run | 4 every run | the carrying chunk also contains 49, 67, 37, and 12 |
| POL_02 | 67.4 | 1 or 2 | 3 every run | a competing prose FCC order and a date-keyed workbook |
| POL_03 | 44 | 1 (document top three) | 2 every run | the weakest competition of the three |

If one probe replaces PR4, use POL_01. It is the only candidate where the fact document is rank 1 in every run
and four of the five retrieved passages are numeric dumps. The carrying chunk is rank 1, so a fair system can
answer. The dump-shaped majority is what a failing system follows.

If the suite can carry two pollution probes, keep POL_02 as the percentage case and POL_03 as the clean fallback.
Do not keep PR4. It repeats N06, and its carrying chunk is not retrieved.

## Also tested, not chosen

These ran with the same ask_staff command. The summaries are from the outputs, not from a different instrument.

| Fact and question | Document | Retrieval | Why not chosen |
|---|---|---|---|
| "What percentage of noise-exposed males reported consistent use of hearing-protective devices?" (25 percent) | 007357.txt | fact document rank 1 and rank 5; dumps rank 2 to 4 | same document as POL_01; a percentage alternative |
| "How many employees at Virginia Mason left over the mandatory vaccination policy?" (six) | 543588.doc | fact document rank 1 and 2; dumps rank 3 to 5 | the answer is the word six, the chunk also contains 5,000, and the normalizer does not equate six and 6 |
| "What percentage of TV households did cable television systems serve in 1975?" (13 percent) | 012148.doc | fact document rank 1; dumps rank 3 to 5 | 13 is a substring of 13 million in the same chunk; 67.4 is cleaner |
| "What percentage of Americans had access to the Internet from home in November 2000?" (56 percent) | 012148.doc | fact document rank 1; dumps rank 2 to 5; denied true | a restricted passage outranked the permitted one, so the probe would measure the access boundary, not pollution |
| "By what percentage did the isolation rate for Salmonella enteritidis decrease between 1996 and 1998?" (44 percent) | 330712.txt | dumps rank 1 to 5; fact document absent; denied true | not fair; the acronym wording is the fair version |
| "What percentage of women in the HPV-008 trial had normal cytologies?" (90.5 percent) | 543588.doc | all prose | no dumps in the top five |
| "What percentage of the pediatric age group reports were asthma-wheezing?" (6 percent) | 543588.doc | all prose | no dumps in the top five |
| "How many radio stations had the Commission licensed as of June 30, 2001?" (12,932) | 012148.doc | all prose | no dumps in the top five |

## Notes

- The queries and runs above are the evidence. The full JSON was not saved; the commands can be rerun.
- No corpus, deployment, configuration, suite, or git change was made.
- The proposed IDs are provisional. If accepted, POL_01 would take PR4's slot and keep the category probe_pollution.
