# Date arithmetic candidates: evidence

All queries were run read-only on 2026-10-19 against `chunk_index` on the deployment:

    ssh -i /home/anthony/ai-stack/ai-stack-key.pem ubuntu@52.204.202.66
    cd /opt/oi-core
    USER=$(grep -E "^POSTGRES_USER=" rendered/env | cut -d= -f2)
    DB=$(grep -E "^POSTGRES_DB=" rendered/env | cut -d= -f2)
    docker exec postgres psql -U "$USER" -d "$DB" -At -F'~' -c "<query>"

The `~`-separated result blocks below are the actual output, with the passage column
truncated by the `left(text, n)` in the query shown. The full carrying passage is quoted
above each result.

## CAND_date_arithmetic_01

**Question.** How many days elapsed between the adoption and release of the FCC's Report
and Order in MB Docket No. 03-185 on digital low power television stations?

**Document and chunk.** `company/group-02/545688.doc`, chunk
`4ff40c77-1297-537c-bed3-4891104c47ef`, page 1.

**Full carrying passage.**

    Before the
    Federal Communications Commission
    Washington, D.C. 20554
    In the matter of
    Amendment of Parts 73 and 74 of the Commission’s Rules to Establish Rules for Digital Low Power Television, Television Translator, and Television Booster Stations and to Amend Rules for Digital Class A Television Stations
    )
    )
    )
    )
    )
    )
    )
    )
    MB Docket No. 03-185
    REPORT AND ORDER
    Adopted:  September 9, 2004	Released:  September 30, 2004
    By the Commission:  Chairman Powell issuing a statement; Commissioners Copps and Adelstein
    approving in part, concurring in part and issuing separate statements.

The chunk continues with the table of contents; the dates are on the order's face.

**SQL and result.**

```sql
select doc_id, chunk_id, page_start, left(text, 450)
from chunk_index
where doc_id='company/group-02/545688.doc' and text ilike '%Adopted:%';
```

    company/group-02/545688.doc~4ff40c77-1297-537c-bed3-4891104c47ef~1~Before the
    Federal Communications Commission
    Washington, D.C. 20554
    In the matter of
    Amendment of Parts 73 and 74 of the Commission’s Rules to Establish Rules for Digital Low Power Television, Television Translator, and Television Booster Stations and to Amend Rules for Digital Class A Television Stations
    )
    )
    )
    )
    )
    )
    )
    )
    MB Docket No. 03-185
    REPORT AND ORDER
    Adopted:  September 9, 2004	Released:  September 30, 2004
    By the Commission:  Chairman Po

The query returns one row. `Adopted:%` appears once in the document, so there is no
second adoption/release pair.

**Arithmetic.** 30 September 2004 - 9 September 2004 = 21 days.

**Why this is not a duplicate.** The live suite has no question about the FCC, MB Docket
No. 03-185, or an adoption/release pair. The only existing date question is P07 (the Acurx
S-1 filing date), a single date from a different document. PR1-PR4 are census-table rows
and a refusal probe. This candidate asks for a computed interval, not a date lookup.

## CAND_date_arithmetic_02

**Question.** The FCC's order and notice of proposed rulemaking in MM Docket No. 96-197
lists an adoption date, a release date, a comment date, and a reply comment date. Which of
those four dates is the latest?

**Document and chunk.** `company/group-01/012148.doc`, chunk
`695cc2f4-ff80-5db2-9844-4d9f038e286e`, page 1.

**Full carrying passage.**

    Before the

    Federal Communications Commission

    Washington, D.C. 20554

    In the Matter of

    Cross-Ownership of Broadcast Stations and Newspapers

    Newspaper/Radio Cross-Ownership

    Waiver Policy

    )

    )

    )

    )

    )

    )

    )

    MM Docket No. 96-197

    ORDER AND

    NOTICE OF PROPOSED RULE MAKING

    Adopted:

    September 13, 2001

    Released:

    September 20, 2001

    Comment Date:  December 3, 2001

    Reply Comment Date: January 7, 2002

    By the Commission:  Commissioner Copps issuing a statement.

**SQL and result.**

```sql
select doc_id, chunk_id, page_start, left(text, 450)
from chunk_index
where doc_id='company/group-01/012148.doc' and text ilike '%Reply Comment Date%';
```

    company/group-01/012148.doc~695cc2f4-ff80-5db2-9844-4d9f038e286e~1~Before the

    Federal Communications Commission

    Washington, D.C. 20554

    In the Matter of

    Cross-Ownership of Broadcast Stations and Newspapers

    Newspaper/Radio Cross-Ownership

    Waiver Policy

    )

    )

    )

    )

    )

    )

    )

    MM Docket No. 96-197

    ORDER AND

    NOTICE OF PROPOSED RULE MAKING

    Adopted:

    September 13, 2001

    Released:

    September 20, 2001

    Comment Date:  December 3, 2001

    Reply Comment Date: January 7, 2002

    By the Commission:  Commissioner Copps iss

The query returns one row. The question is scoped to those four dates on the order's
face.

**Comparison.** Adoption 13 September 2001; release 20 September 2001; comment 3 December
2001; reply comment 7 January 2002. The latest is 7 January 2002.

**Why this is not a duplicate.** No live question names MM Docket No. 96-197 or the
cross-ownership order. P07 is the only existing date question and asks for one filing date
from the Acurx S-1. This candidate asks which of four dates on a different document's face
is latest.

## CAND_date_arithmetic_03

**Question.** Apple's 2024 Form 10-K exhibit index lists the Amended and Restated Bylaws as
effective as of August 20, 2024, filed on Form 8-K. According to the filing date in that
exhibit index, how many days after the effective date was the 8-K filed?

**Document and chunks.** `company/apple-10k-2024.htm`, row chunk
`0bc82e9b-6118-513b-b129-3edcb3dc7cc1`; column header chunk
`7ae45cc9-b3c8-5064-be84-90238cdfe9f4`.

**Full carrying passages.**

Header:

    Exhibit Number | Exhibit Description | Form | Exhibit | Filing Date/
    Period End Date

Row:

    aapl-20240928
    3.1 | Restated Articles of Incorporation of the Registrant filed on August 3, 2020. | 8-K | 3.1 | 8/7/20
    3.2 | Amended and Restated Bylaws of the Registrant effective as of August 20, 2024. | 8-K | 3.2 | 8/23/24
    4.1** | Description of Securities of the Registrant.
    4.2 | Indenture, dated as of April 29, 2013, between the Registrant and The Bank of New York Mellon Trust Company, N.A., as Trustee. | S-3 | 4.1 | 4/29/13

The table continues with the remaining exhibits.

**SQL and result.**

```sql
select chunk_id, left(text, 260)
from chunk_index
where doc_id='company/apple-10k-2024.htm'
  and text ilike '%Amended and Restated Bylaws%'
  and text like '%8/23/24%'
limit 1;
```

    0bc82e9b-6118-513b-b129-3edcb3dc7cc1~aapl-20240928
    3.1 | Restated Articles of Incorporation of the Registrant filed on August 3, 2020. | 8-K | 3.1 | 8/7/20
    3.2 | Amended and Restated Bylaws of the Registrant effective as of August 20, 2024. | 8-K | 3.2 | 8/23/24
    4.1** | Description of Securities of the Registrant.
    4.2 | Indenture, date

```sql
select chunk_id, text
from chunk_index
where chunk_id='7ae45cc9-b3c8-5064-be84-90238cdfe9f4';
```

    7ae45cc9-b3c8-5064-be84-90238cdfe9f4~Exhibit Number | Exhibit Description | Form | Exhibit | Filing Date/
    Period End Date

The row query returns the Bylaws row. The header query confirms that the last column is
"Filing Date/ Period End Date"; the row's form is 8-K, which has no period end date, so
8/23/24 is the filing date.

**Arithmetic.** 23 August 2024 - 20 August 2024 = 3 days.

**Why this is not a duplicate.** No live question uses the Apple 10-K or its exhibit index.
P08 asks for the Acurx offering's share count, not Apple. This candidate is a
filing-to-effective-date interval, a new document and a new fact.

## CAND_date_arithmetic_04

**Question.** Under IRS Publication 15 for 2026, how many days after the standard deadline
for filing the 2025 Form 940 may an employer that deposited all FUTA tax when due file it?

**Document and chunk.** `company/irs-p15.pdf`, chunk
`da2efe60-226b-5e65-8050-7fb66a698b7f`, page 44.

**Full carrying passage.**

    3. Farmworkers test.
    Reporting FUTA tax. Use Form 940 to report FUTA tax. File your 2025 Form 940 by February 2, 2026. However, if you deposited all FUTA tax when due, you may file on or before February 10, 2026.

    44

    Publication 15 (2026)

**SQL and result.**

```sql
select doc_id, chunk_id, page_start, text
from chunk_index
where doc_id='company/irs-p15.pdf' and text ilike '%2025 Form 940%';
```

    company/irs-p15.pdf~da2efe60-226b-5e65-8050-7fb66a698b7f~44~3. Farmworkers test.
    Reporting FUTA tax. Use Form 940 to report FUTA tax. File your 2025 Form 940 by February 2, 2026. However, if you deposited all FUTA tax when due, you may file on or before February 10, 2026.

    44

    Publication 15 (2026)

The query returns one row. A second query for Form 940 rows containing a February 2026
date returns only this chunk, so the February 2/February 10 pair is not duplicated
elsewhere in the publication:

```sql
select chunk_id, page_start, substring(text from position('Form 940' in text) for 80)
from chunk_index
where doc_id='company/irs-p15.pdf'
  and text ilike '%Form 940%'
  and text ~* 'February [0-9]+, 2026';
```

    da2efe60-226b-5e65-8050-7fb66a698b7f~44~Form 940 to report FUTA tax. File your 2025 Form 940 by February 2, 2026. Howeve

**Arithmetic.** 10 February 2026 - 2 February 2026 = 8 days.

**Why this is not a duplicate.** No live question uses IRS Publication 15 or Form 940. P07
is the only date question and is a different document and a single date. This candidate
asks for the gap between a standard deadline and its extension.
