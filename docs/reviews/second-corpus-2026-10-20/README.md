# The second corpus: build, questions, and the row-query measurement

**Date:** 2026-10-20. **Deployment:** dedicated EC2 instance `oi-core-second-corpus` (m7i.4xlarge, us-east-1b,
instance i-05fa6edd5284b7881), isolated from the development deployment. **Model:** Claude Haiku 4.5 through
the gateway, tool loop on (six steps, ten calls), honesty gate on, lookup_value off.

## Why

The first corpus could not answer two questions that matter for the product: whether the structured row query
generalises to a different document mix, which is the condition for retiring lookup_value, and whether the
honesty gate answers when a policy is genuinely stated rather than only refusing when one is absent. The second
corpus was shaped to answer both, and the row query was measured on and off across two five-run phases.

## The corpus

80 documents, in two halves. It is not committed to git; provenance for every file is.

| Half | Documents | Size | Content |
|---|---|---|---|
| Public | 40 | 86.6 MB | 14 US issuers, seven industries: 10-K, 10-Q, 8-K, DEF 14A, Exhibit 10 contracts, plus 10 US government PDFs (GAO, EPA, DOL, FTC, USDA, OSHA, FDA, NIST) |
| Synthetic | 40 | 1.3 MB | fictional Northwind Office Services: policy-rich handbook, 35,600-word facilities manual, 6,010-row transaction ledger, financial statements, invoices, forms, canary PII, executive folder |

Public sources are SEC public records and US government works only, with the source URL, retrieval time and
licence recorded per file. Synthetic documents are labelled inside every file, use fictional data, and are
deliberately mixed: stated policies (PTO, remote work, expenses, retention) sit beside deliberately absent ones
(bereavement, sabbatical, social media, ESPP, tuition), and a contractor-uniform document states a rule for
contractors rather than employees, which is the relabelling case the honesty gate exists for. Provenance and
scope are committed under docs/eval/corpora/second-corpus/.

## The deployment

Two full stacks on the development box left about 14 GB of 61 GB available before any second-corpus ingestion,
because each stack holds a parser and an embedding service. Running the measurement there would have put memory
pressure and CPU contention under the numbers the exercise exists to produce, so the second corpus runs on a
dedicated instance with the same AMI, security group, key pair and instance role, its own Postgres, Qdrant,
gateway, API and secrets, and the development deployment untouched.

## Ingestion

80 documents, **24,295 chunks** (21,166 public, 3,129 synthetic), **zero unreadable files and zero failures**.
Two operational notes, neither a product defect: 17 synthetic files arrived mode 600 and were unreadable by the
worker until the permissions were fixed (the public files were 644), and the CSV marker line is preserved as
ordinary text by the parser, which costs nothing.

## The questions

35 questions: 12 on the public half, 20 on the synthetic half, 3 mechanism probes. Every expected value was read
from the documents and shown with the command that proves it in two evidence files. IDs are prefixed because both
authoring passes started at N01, and the probes were renamed to avoid colliding with the runner's built-in probe
IDs.

**Validation.** scripts/validate_data_questions.py checks every question against the production index: documents
exist, quotes appear in them, expected values are in the documents or in a verified derivation, and absent
patterns match zero chunks. First pass: **27 of 35 passed**, 12 schema errors. The failures were question defects,
not product defects: structured evidence where the validator expects text, CSV rows quoted in file notation
rather than the chunker's pipe notation, arithmetic quotes with file prefixes, two expected strings that natural
answers cannot contain, two absent patterns that match public documents, and one probe whose expected values
were not inside its quote. After fixing those, **35 of 35 pass**.

One question had to be retargeted because of a real ingestion rule. The ledger has 6,013 rows, and a table over
5,000 rows is treated as a data dump: the chunker keeps bounded row groups up to its 200-chunk limit and records
the omission in a table_marker chunk. The indexed range is rows 0 to 2,412 (dates 2025-01-02 to 2025-05-28); the
remaining 3,600 rows, including the 12,345.67 row at line 3,062, are not in the index and no retrieval or row
query can reach them. The question now targets an indexed row, and the limit is recorded here because it is a
real product gap for large ledgers.

## The measurement

Five runs per phase, 37 role-runs per run (35 questions, with the two access questions asked as both roles),
185 scored role-runs per phase. Same corpus, model and flags; only OI_TOOL_QUERY changes.

| Phase | Runs | Mean | Latency p50 | Latency p95 | Cost per answer | Calls per answer |
|---|---|---|---|---|---|---|
| Row query off | 30, 32, 31, 31, 31 | **31.0 of 37** | 2,268 ms | 12,254 ms | 0.01471 USD | 2.99 |
| Row query on | 32, 32, 32, 31, 32 | **31.8 of 37** | 2,367 ms | 12,031 ms | 0.01349 USD | 2.77 |

Provider spend for the ten runs: 5.22 USD across 1,065 calls, zero failed calls, zero degraded answers.

**Category results, off then on:**

| Category | Off | On |
|---|---|---|
| unanswerable (refusals) | 20/20 | 20/20 |
| prose_fact | 25/25 | 25/25 |
| date_arithmetic | 10/10 | 10/10 |
| government_finding | 5/5 | 5/5 |
| probe_row_normal | 5/5 | 5/5 |
| probe_policy_absence | 5/5 | 5/5 |
| exact_number | 40/50 | 45/50 |
| cross_document | 19/20 | 18/20 |
| access_boundary | 16/20 | 16/20 |
| long_document | 5/10 | 5/10 |
| long-document probe | 0/5 | 0/5 |
| contract_clause | 0/5 | 0/5 |

## What changed, and what did not

**The row query fixed the plain table number.** PUB_N01 (Target's net sales) failed all five runs with the query
off and passed all five with it on: the embeddings retrieved the wrong table, the row query read the right row.
Overall the query added 0.8 of 37 and removed no correct answer.

**It did not fix the other four failures**, and they are the measurement's real findings:

- PUB_N04: C.H. Robinson quarterly revenue, answered as 896,604 in every run; the model read a different table
  line and no tool call corrected it.
- PUB_P01: Intuit's base maximum Leverage Ratio, answered as 3.50 in every run. The agreement's step-down schedule
  offers a nearby 3.50, so the question is ambiguous between the base maximum and a later step-down; this is a
  question-wording weakness as much as a retrieval failure, and it is recorded as such.
- PUB_L01: Caterpillar's Current Termination Date, refused in every run; the carrying passage was not retrieved.
- SC_PROBE_NEEDLE: a visitor-badge rule in the 35,600-word facilities manual, answered from the handbook's
  shorter rule as end of visit instead of 24 hours. This is the same long-document needle failure the wider set
  found, reproduced on a second corpus with a different long document.
- SYN_A02: the board-authorized acquisition ceiling, failed in the executive role four runs of five in both
  phases; the model often answered the 4,900,000 proposed enterprise value instead of the 5,500,000 authorised
  ceiling. Staff denial held in every run, so the access control is not what failed.

A few results are flaky rather than failing: PUB_X02 (cross-document arithmetic) passed four of five off and
three of five on, and SYN_A02 passed once. Five-run ranges are the reason the report shows ranges.

## Honesty in both directions

Stated policies were answered: PTO accrual, per diem, mileage and receipts, remote work, the fire panel, the
generator schedule, the elevator recall, budget totals and price-list rows all passed in both phases once one
expected string was corrected. Absent policies were refused: bereavement, sabbatical, social media, ESPP and
tuition all passed in both phases, as did the three probes. That is the first measurement of the gate's
answering direction, and it holds.

## What this changes

**The row query generalises.** Two corpora now show the same direction: 43.0 to 46.6 of 52 on the first, 31.0 to
31.8 of 37 on the second, with the gain on the plain table-number question this time. It stays opt-in in code and
is enabled after a deployment measures its own corpus.

**lookup_value was retired on 2026-10-20 (PR #203).** It had been superseded for table rows by the row
query, never measured a gain, and remained off. The second-corpus measurement was the stated condition for
deciding; the owner approved removal, and the tool, tests, flag and prompt references are gone.

**The next instrument target is long-document needle retrieval.** Two needles failed on the second corpus, and
the row query cannot help because the failure is retrieval of a fact inside a long document. A secondary target
is large tables: rows beyond the 5,000-row cap are invisible to search and to the row query.

## Limits

Half the corpus is synthetic, so public and synthetic results can flatter different things; the question set is
balanced across both halves but not separately reported by half in this document. One model, one deployment,
five runs per phase, questions written and verified by the same team. The public documents are filings and
government reports, not a client's day-to-day business documents. The row-query comparison is one corpus pair,
not a guarantee for every corpus.

## Where the numbers come from

- Questions and validator report: docs/eval/corpora/second-corpus/questions.yaml
- Corpus scope and provenance: docs/eval/corpora/second-corpus/
- Raw run files: the instance's /opt/oi-core2/eval-out/off and /opt/oi-core2/eval-out/on
- Panel output: /tmp/panel-off.json and /tmp/panel-on.json on the instance
- The deployment is stopped after this measurement; the corpus and provenance are reproducible from the
  committed manifests and question files.
