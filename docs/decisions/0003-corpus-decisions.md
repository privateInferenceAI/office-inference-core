# 0003. Corpus decisions: the six rejected files, the data-set cards, and two tool policies

Date: 2026-10-19
Status: accepted by the owner

## Context

The corpus load left six files the parser could not read and six files classified as data sets. The 2026-10-19
hand-off left both lists untouched, because deleting client-like material without the owner is the wrong
direction, and shipped a recommendation rather than a decision. The hand-off's update added two tool questions:
whether the structured row query would become the default and what to do with `lookup_value` now that the row
query covers its intended use. The owner answered all four, plus the deployment's running state, on 2026-10-19.

## Decisions

**1. The six unreadable files are excluded permanently.** They are not deleted, because they are client-like
material and a decision to stop indexing is not a decision to destroy. They are recorded in
`/opt/oi-core/rejected/exclusions.csv`, which the ingestion worker reads (and never writes), so the rejected
worklist stops asking for a decision that has been made and the decision itself stays visible with who made it and
why. A replacement that arrives at any of these paths re-enters ingestion on its own: a failure is keyed on the
bytes that failed, so new bytes are a different document. Reversing the decision is deleting the row from
`exclusions.csv` and running `--clear-failures <relpath>`, or replacing the file.

The six, with why the parser gave up:

| File | Why it failed |
|---|---|
| `company/group-02/000406.xls` | Docling conversion failure, unreadable document |
| `company/group-02/468308.csv` | parser pipeline failed |
| `executive/group-03/024650.xls` | Docling conversion failure, unreadable document |
| `executive/group-03/184459.xls` | Docling conversion failure, unreadable document |
| `executive/group-03/546492.xls` | Docling conversion failure, unreadable document |
| `executive/group-03/828946.xls` | Docling conversion failure, unreadable document |

**2. The six data-set cards stay cards.** They contribute one embeddable card each and are listed in
`/opt/oi-core/rejected/datasets.csv`; they are not converted to searchable rows. Indexing them as documents
would re-create the row explosion chunker-v4 removed, and the structured row query cannot reach them because a
card has no rows. A data path for these files is a separate piece of work, to be revisited only when a real
client requirement asks for it.

| File | Why it is a data set |
|---|---|
| `company/group-02/595758.xls` | 3.2 MB spreadsheet |
| `executive/group-03/042246.txt` | 3.4 MB text dump |
| `executive/group-03/058658.txt` | 6.8 MB text dump |
| `executive/group-03/084615.txt` | 5.3 MB text dump |
| `executive/group-03/594185.xls` | 9.0 MB spreadsheet |
| `executive/group-03/594706.xls` | 5.2 MB spreadsheet |

**3. The development box stays running while work continues.** About 0.81 USD an hour, stopped when the milestone
pauses rather than between sessions. The box was running when the decision was taken.

**4. The structured row query stays opt-in in code.** `OI_TOOL_QUERY` defaults off and a deployment turns it on
after that deployment's own five-run measurement. The development deployment has it on. The evidence is one
corpus, so the measurement is the gate, not the code default.

**5. `lookup_value` stays implemented, tested and off.** It is superseded for table rows by `query_rows` and is
not deleted. Retirement is planned after the row query has been measured on a second corpus; until then the tool
is the fallback instrument for a future provider layer and its tests protect the literal-match behaviour.

## Consequences

- The rejected worklist now has two outcomes: pending dead letters in `rejected.csv`, and decided exclusions in
  `exclusions.csv` (read) and `rejected.json`'s `excluded` list (reported, with the decision). The CSV an
  operator hands to a client is the pending list.
- No corpus file is deleted by this decision, and the S3 archive is unchanged.
- The row query's default is unchanged, so no deployment behaves differently from the measured configuration.
- The data-set path remains open work, recorded in the register rather than as a promise in this record.
