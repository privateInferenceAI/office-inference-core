# Multi-hop candidates: evidence

All queries were run read-only on 2026-10-19 against `chunk_index` on the deployment:

    ssh -i /home/anthony/ai-stack/ai-stack-key.pem ubuntu@52.204.202.66
    cd /opt/oi-core
    USER=$(grep -E "^POSTGRES_USER=" rendered/env | cut -d= -f2)
    DB=$(grep -E "^POSTGRES_DB=" rendered/env | cut -d= -f2)
    docker exec postgres psql -U "$USER" -d "$DB" -At -F'~' -c "<query>"

Each candidate names two documents. The full carrying passage from each document is
quoted, followed by the one query that returns both rows and the arithmetic or bridge
that joins them. The retrieval check at the end of each section is the same command the
README gives, with `--role staff` and the default limit of five.

## CAND_multi_hop_01

**Question.** According to the FCC's license renewal guide, broadcast station licenses are
granted for eight years. The FCC's 2004 digital LPTV order allows three years from permit
issuance to complete construction. If a station used both periods in full, how many years
would they total?

**Document 1.** `company/group-02/069856.doc`, chunk
`469d54db-5469-5a9d-bd1d-76d3d70fa517`.

    Last Revised: 10/03
    Public Participation in the License Renewal Process
    All broadcast station licenses are granted by the Commission for eight years.  Currently, all radio station licenses are scheduled to expire between 2003 and 2006. All television station licenses are scheduled to expire between 2004 and 2007. Each station must file an application for license renewal four months prior to the expiration date of the station's license, in accordance with a schedule set forth in the Commission’s Rules.  Instructions on obtaining Internet access to that schedule are set forth below.  A paper copy of the renewal schedule is included in the public handout for this hearing.  Stations also must file a Broadcast Equal Employment Opportunity Program Report (FCC Form 396) at the same time.

The passage continues with the renewal process; the eight-year term is the first sentence.

**Document 2.** `company/group-02/545688.doc`, chunk
`dd376df0-215e-59ca-ba10-c98b11a62c60`.

    5 ORDERING CLAUSES >
    (2)  Other facilities changes will be considered minor.

    Section 74.788 is added to read as follows:

    § 74.788 – Digital construction period.

    (a)  Each original construction permit for the construction of a new digital low power television or television translator station shall specify a period of three years from the date of issuance of the original construction permit within which construction shall be completed and application for license filed.

    (b) Any construction permit for which construction has not been completed and for which an application for license or extension of time has not been filed, shall be automatically forfeited upon expiration without any further affirmative cancellation by the Commission.

The chunk continues with the extension provision in (c); the base construction period is
three years.

**SQL and result.**

```sql
select doc_id, chunk_id, left(text, 420)
from chunk_index
where doc_id in ('company/group-02/545688.doc','company/group-02/069856.doc')
  and (text ilike '%period of three years from the date of issuance%'
       or text ilike '%granted by the Commission for eight years%')
order by doc_id;
```

Result (two rows; passage truncated by `left(text, 420)`):

    Row 1
    doc_id:    company/group-02/069856.doc
    chunk_id:  469d54db-5469-5a9d-bd1d-76d3d70fa517
    passage:   Last Revised: 10/03
               Public Participation in the License Renewal Process
               All broadcast station licenses are granted by the Commission for eight years.  Currently, all radio station licenses are scheduled to expire between 2003 and 2006. All television station licenses are scheduled to expire between 2004 and 2007. Each station must file an application for license renewal four months prior to the expiration date of the

    Row 2
    doc_id:    company/group-02/545688.doc
    chunk_id:  dd376df0-215e-59ca-ba10-c98b11a62c60
    passage:   5 ORDERING CLAUSES >
               (2)  Other facilities changes will be considered minor.

               Section 74.788 is added to read as follows:

               § 74.788 – Digital construction period.

               (a)  Each original construction permit for the construction of a new digital low power television or television translator station shall specify a period of three years from the date of issuance of the original construction permit within which construction

**Arithmetic.** 3 years + 8 years = 11 years.

**Retrieval check.** Staff, limit 5: 069856, 545688, 012148, 545688, 545688. Both named
documents are in the top five.

**Why this is not a duplicate.** No live question asks for an FCC construction period or a
broadcast license term. The X-series questions are numeric differences over census tables,
not prose. A05's "five years" is the NESHAP measurement-file retention period in
`executive/group-03/543276.doc`, a different agency, document and fact. PR1-PR4 are table
rows and a refusal probe.

## CAND_multi_hop_02

**Question.** According to the FCC's license renewal guide, all broadcast station licenses
are granted for eight years. NASA's Space Act Agreements Guide limits Space Act Agreements
to 5-year terms in all but very few cases. How many years longer is the FCC license term
than the NASA limit?

**Document 1.** `company/group-02/173609.doc`, chunk
`6f04f6ae-3f9b-504c-92da-847a9ba52caa`.

    NASA limits its SAAs to 5-year terms in all but very few cases because any commitment of resources far into the future may be problematic since budgets and program objectives may change.  For the same reason, use of an automatic renewal provision may be problematic.  Where a commitment exceeding five years is essential to the fundamental objectives of the SAA, early consultation with the Office of the General Counsel or Chief Counsel, as appropriate, is essential.

**Document 2.** `company/group-02/069856.doc`, chunk
`469d54db-5469-5a9d-bd1d-76d3d70fa517`.

    Last Revised: 10/03
    Public Participation in the License Renewal Process
    All broadcast station licenses are granted by the Commission for eight years.  Currently, all radio station licenses are scheduled to expire between 2003 and 2006. All television station licenses are scheduled to expire between 2004 and 2007. Each station must file an application for license renewal four months prior to the expiration date of the station's license, in accordance with a schedule set forth in the Commission’s Rules.

**SQL and result.**

```sql
select doc_id, chunk_id, left(text, 380)
from chunk_index
where doc_id in ('company/group-02/173609.doc','company/group-02/069856.doc')
  and (text ilike '%5-year terms%' or text ilike '%granted by the Commission for eight years%')
order by doc_id;
```

Result (two rows; passage truncated by `left(text, 380)`):

    Row 1
    doc_id:    company/group-02/069856.doc
    chunk_id:  469d54db-5469-5a9d-bd1d-76d3d70fa517
    passage:   Last Revised: 10/03
               Public Participation in the License Renewal Process
               All broadcast station licenses are granted by the Commission for eight years.  Currently, all radio station licenses are scheduled to expire between 2003 and 2006. All television station licenses are scheduled to expire between 2004 and 2007. Each station must file an application for license renewal four mo

    Row 2
    doc_id:    company/group-02/173609.doc
    chunk_id:  6f04f6ae-3f9b-504c-92da-847a9ba52caa
    passage:   NASA limits its SAAs to 5-year terms in all but very few cases because any commitment of resources far into the future may be problematic since budgets and program objectives may change.  For the same reason, use of an automatic renewal provision may be problematic.  Where a commitment exceeding five years is essential to the fundamental objectives of the SAA, early consultatio

**Arithmetic.** 8 years - 5 years = 3 years.

**Retrieval check.** Staff, limit 5: 173609, 069856, 012148, 173609, 173609. Both named
documents are in the top five.

**Why this is not a duplicate.** `company/group-02/173609.doc` is used by P03 and P04,
but those ask for the numbered reimbursable-travel agreement and the name/title
verification requirement; neither asks for the 5-year term. A05's five years is NESHAP
record retention in a different, executive-only document. No live question combines a NASA
term with an FCC license term.

## CAND_multi_hop_03

**Question.** In Exhibit 10.19 to Apple's 2024 Form 10-K, the restricted stock unit award
agreement's Notice of Grant includes a Vesting Commencement Date. According to the 2024
Form 10-K, over how many years do RSUs granted under the 2022 Employee Stock Plan generally
vest?

**Document 1.** `company/apple-ex10-1-2024.htm`, chunk
`e14f9473-cdd0-5517-88bc-a55e2f10c763`.

    Document
    Exhibit 10.19
    APPLE INC.
    2022 EMPLOYEE STOCK PLAN
    RESTRICTED STOCK UNIT AWARD AGREEMENT
    NOTICE OF GRANT
    Name: (the "Participant")
    Employee ID:
    Grant Number:
    No. of Units Subject to Award:
    Award Date: (the "Award Date")
    Vesting Commencement Date: (the "Vesting Commencement Date")
    Vesting Schedule:
    This restricted stock unit award (the "Award") is granted under and governed by the terms and conditions of the Apple Inc. 2022 Employee Stock Plan and the Terms and Conditions of Restricted Stock Unit Award, which are incorporated herein by reference.

**Document 2.** `company/apple-10k-2024.htm`, chunk
`da1703f5-b03b-5a3b-8c5a-16897dfca81b`.

    aapl-20240928
    The Apple Inc. 2022 Employee Stock Plan (the "2022 Plan") is a shareholder-approved plan that provides for broad-based equity grants to employees, including executive officers, and permits the granting of RSUs, stock grants, performance-based awards, stock options and stock appreciation rights. RSUs granted under the 2022 Plan generally vest over four years , based on continued employment, and are settled upon vesting in shares of the Company's common stock on a one -for-one basis. All RSUs granted under the 2022 Plan have dividend equivalent rights, which entitle holders of RSUs to the same dividend value per share as holders of common stock. A maximum of approximately 1.3 billion shares were authorized for issuance pursuant to 2022 Plan awards at the time the plan was approved on March 4, 2022.

**SQL and result.**

```sql
select doc_id, chunk_id, left(text, 300)
from chunk_index
where (doc_id='company/apple-ex10-1-2024.htm' and text ilike '%Exhibit 10.19%')
   or (doc_id='company/apple-10k-2024.htm' and text ilike '%generally vest over four years%');
```

Result (two rows; passage truncated by `left(text, 300)`):

    Row 1
    doc_id:    company/apple-10k-2024.htm
    chunk_id:  da1703f5-b03b-5a3b-8c5a-16897dfca81b
    passage:   aapl-20240928
               The Apple Inc. 2022 Employee Stock Plan (the "2022 Plan") is a shareholder-approved plan that provides for broad-based equity grants to employees, including executive officers, and permits the granting of RSUs, stock grants, performance-based awards, stock options and stock appreciatio

    Row 2
    doc_id:    company/apple-ex10-1-2024.htm
    chunk_id:  e14f9473-cdd0-5517-88bc-a55e2f10c763
    passage:   Document
               Exhibit 10.19
               APPLE INC.
               2022 EMPLOYEE STOCK PLAN
               RESTRICTED STOCK UNIT AWARD AGREEMENT
               NOTICE OF GRANT
               Name: (the "Participant")
               Employee ID:
               Grant Number:
               No. of Units Subject to Award:
               Award Date: (the "Award Date")
               Vesting Commencement Date: (the "Vesting Commencement Date")
               Vesting Sch

**Bridge.** The exhibit's title and Notice of Grant identify the governing plan as the
Apple Inc. 2022 Employee Stock Plan. The 10-K paragraph for that plan states that RSUs
granted under it generally vest over four years.

**Retrieval check.** Staff, limit 5: apple-10k-2024.htm, apple-10k-2024.htm,
apple-ex10-1-2024.htm, apple-10k-2024.htm, company/group-01/439428.txt. Both named
documents are in the top five.

**Why this is not a duplicate.** No live question uses the Apple 10-K, the 2022 Employee
Stock Plan, or Exhibit 10.19. P08 asks for the Acurx offering's share count; it is a
different company, document and fact.

## CAND_multi_hop_04

**Question.** Apple's 2024 Form 10-K states that RSUs granted under the 2022 Employee Stock
Plan generally vest over four years. Acurx's S-1 states that shares registered in the
offering may be sold to Lincoln Park over a period of up to 24 months. How many months
longer is Apple's standard RSU vesting period than Acurx's purchase period?

**Document 1.** `company/apple-10k-2024.htm`, chunk
`da1703f5-b03b-5a3b-8c5a-16897dfca81b`.

    aapl-20240928
    The Apple Inc. 2022 Employee Stock Plan (the "2022 Plan") is a shareholder-approved plan that provides for broad-based equity grants to employees, including executive officers, and permits the granting of RSUs, stock grants, performance-based awards, stock options and stock appreciation rights. RSUs granted under the 2022 Plan generally vest over four years , based on continued employment, and are settled upon vesting in shares of the Company's common stock on a one -for-one basis. All RSUs granted under the 2022 Plan have dividend equivalent rights, which entitle holders of RSUs to the same dividend value per share as holders of common stock. A maximum of approximately 1.3 billion shares were authorized for issuance pursuant to 2022 Plan awards at the time the plan was approved on March 4, 2022.

**Document 2.** `company/group-01/acurx-pharmaceuticals-inc__S-1__2026-09-08.htm`, chunk
`d9bbc041-6714-5eee-a4dc-580fab7410ff`.

    tm2624622-1_s1 - none - 4.0937768s
    All shares registered in this offering which have been or may be issued or sold by us to Lincoln Park under the Purchase Agreement are expected to be freely tradable. Other than the Commitment Shares, which were issued to Lincoln Park on May 8, 2025, the shares registered in this offering may be sold to Lincoln Park at our discretion over a period of up to 24 months beginning on the Commencement Date. The sale by Lincoln Park of a significant amount of the shares registered in this offering at any given time could cause the market price of our common stock to decline and to be highly volatile.

**SQL and result.**

```sql
select doc_id, chunk_id, left(text, 420)
from chunk_index
where doc_id in ('company/apple-10k-2024.htm','company/group-01/acurx-pharmaceuticals-inc__S-1__2026-09-08.htm')
  and (text ilike '%generally vest over four years%' or text ilike '%period of up to 24 months%')
order by doc_id;
```

Result (three rows; passage truncated by `left(text, 420)`):

    Row 1
    doc_id:    company/apple-10k-2024.htm
    chunk_id:  da1703f5-b03b-5a3b-8c5a-16897dfca81b
    passage:   aapl-20240928
               The Apple Inc. 2022 Employee Stock Plan (the "2022 Plan") is a shareholder-approved plan that provides for broad-based equity grants to employees, including executive officers, and permits the granting of RSUs, stock grants, performance-based awards, stock options and stock appreciation rights. RSUs granted under the 2022 Plan generally vest over four years , based on continued employment, and are settl

    Row 2
    doc_id:    company/group-01/acurx-pharmaceuticals-inc__S-1__2026-09-08.htm
    chunk_id:  a85f13fb-1cf2-5e87-91c7-32a4d94b16a7
    passage:   tm2624622-1_s1 - none - 4.0937768s
               We did not have the right to commence any sales of our common stock to Lincoln Park under the Purchase Agreement until all of the conditions set forth in the Purchase Agreement were satisfied, which occurred on the Commencement Date. From and after the Commencement Date, from time to time, at our sole discretion for a period of up to 24 months, we may direct Lincoln Park to purchase

    Row 3
    doc_id:    company/group-01/acurx-pharmaceuticals-inc__S-1__2026-09-08.htm
    chunk_id:  d9bbc041-6714-5eee-a4dc-580fab7410ff
    passage:   tm2624622-1_s1 - none - 4.0937768s
               All shares registered in this offering which have been or may be issued or sold by us to Lincoln Park under the Purchase Agreement are expected to be freely tradable. Other than the Commitment Shares, which were issued to Lincoln Park on May 8, 2025, the shares registered in this offering may be sold to Lincoln Park at our discretion over a period of up to 24 months beginning on the

**Arithmetic.** 4 years = 48 months. 48 months - 24 months = 24 months.

**Retrieval check.** Staff, limit 5: apple-10k-2024.htm, Acurx S-1, Acurx S-1,
apple-10k-2024.htm, Acurx S-1. Both named documents are in the top five.

**Why this is not a duplicate.** P07 uses the Acurx S-1's filing date and P08 uses its
1,500,000 offered shares. This candidate uses a different Acurx fact, the 24-month purchase
period, and pairs it with Apple's four-year RSU vesting. No live question compares those
periods or asks for a cross-company duration.
