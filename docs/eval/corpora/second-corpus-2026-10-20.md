# The second corpus: scope, sources and provenance rules

Prepared 2026-10-20. This is the plan the second-corpus work is measured against. The corpus itself is not
committed to git; its provenance manifests are.

## Why a second corpus

Every result in `docs/sales/answer-quality-2026-10-20.md` was measured on one corpus of 539 documents. One corpus
cannot tell a client two things that matter:

1. whether the row query and the tool loop generalise, which is the condition for retiring `lookup_value`;
2. whether the honesty gate answers correctly when a policy **is** stated, as well as refusing when it is absent.

The second corpus exists to answer both, on a document mix deliberately different from the first.

## Composition

About 180 to 250 documents, 25,000 to 40,000 chunks, in the shape a small business actually hands over.

| Share | Content | Source | Why |
|---|---|---|---|
| 40 percent | Filings, contracts, reports, government documents | SEC EDGAR public filings (10-K, 10-Q, 8-K, DEF 14A, Exhibit 10 contracts) and US federal government documents | Realistic business prose and contracts, real tables, license-clean |
| 30 percent | Synthetic small-business documents | Authored for this corpus, labelled synthetic | Policies that are genuinely stated, so the honesty gate can be tested in both directions, plus a coherent company world for questions |
| 20 percent | Tables | Synthetic financial statements, budget, price list, vendor list, and a 6,000-row transaction ledger; government tables in the public set | Row query and chunking under a different table mix, including a table over the per-row-entry cap |
| 10 percent | Mixed formats | PDF (government), HTML (filings), CSV, XLSX, MD, TXT | Parser coverage the first corpus underrepresents |

Plus a small `executive/` folder for access-boundary questions, and deliberately long documents so the long-document
needle limit recorded from the wider set is measured on purpose.

## Provenance rules

- Every document has a provenance row: path, source URL (public set) or authoring note (synthetic set), retrieval
  or creation time, origin, license status, content type, bytes and sha256.
- Public documents are either SEC public filings ("public record") or US government works ("public domain").
  Nothing with unclear reuse rights, nothing paywalled, nothing scraped from a private source.
- Synthetic documents are labelled synthetic inside the file, use fictional data only, and use reserved test
  values for anything that looks personal (SSN-like, card-like, phone-like). No real personal data.
- The corpus files stay out of git. The provenance manifests, this document and the question file are committed.

## What the synthetic half states, and what it deliberately omits

Stated, so the gate must answer: PTO accrual by tenure, remote-work policy, expense and travel policy, IT
acceptable use, vendor management, records retention, holiday schedule, benefits and 401k match, code of conduct.

Deliberately absent, so the gate must refuse: bereavement leave, sabbatical policy, social media policy. A
contractor-uniform document states a rule for contractors and not employees, which is the relabelling failure the
gate was built for.

## Deployment and ingestion

A **dedicated EC2 instance** (m7i.4xlarge, same AMI, security group, key pair and Bedrock instance profile as the
development box; tagged `oi-core-second-corpus`, instance `i-05fa6edd5284b7881`), not a second stack on the
development box. Two full stacks on the development box left about 14 GB available before any second-corpus
ingestion, because each stack holds an embedding service and a parser (46 GB used of 61 GB), and running the
second-corpus measurement there would have put memory pressure and CPU contention under the very latency and
accuracy numbers the exercise exists to produce.

The new instance has its own root (`/opt/oi-core2`), Postgres, Qdrant, gateway, API and rendered secrets, on the
network `ai-net2`. The development deployment stays untouched and available. The instance costs about 0.81 USD an
hour while it runs and is stopped or terminated when the second-corpus work is finished and its evidence captured.

Ingestion goes through the same worker and chunker as the development corpus. The pipeline was smoke-tested on a
single synthetic document before the bulk load: parse, chunk, embed, write, then an answer through the second API
with a citation.

## Questions and measurement

About 28 to 32 questions plus the five mechanism probes, authored from the indexed text with a source quote and a
SQL check, validated by `scripts/validate_data_questions.py` before any run:

- exact numbers from tables and the 6,000-row ledger
- cross-document arithmetic and aggregation
- date arithmetic
- policy questions that must be answered (from the synthetic handbook and policies)
- policy questions that must be refused, including the "our" and "policy on" phrasings
- two long-document needle questions
- rows in ordinary and oversized tables
- access-boundary pairs in the executive folder

Five runs with the row query on and five with it off, same configuration otherwise. That pair is the second-corpus
evidence for the row-report and for the `lookup_value` retirement decision. Then the honesty probes in both
directions and the acceptance panel for latency and cost.

## Acceptance criteria

- The corpus ingests with zero unreadable documents and no failures.
- Every question validates mechanically before a run.
- The row query result is reported as a five-run comparison with a range, not a single number.
- Any question the system fails is examined and recorded as a product limit or a question defect, the way the
  four deterministic failures were handled.
- The write-up states the corpus's limits, including that 30 percent of it is synthetic.

## Risks

- **Synthetic documents can flatter the system.** Mitigation: keep them a minority, label them, and report public
  and synthetic subsets separately where the question set allows.
- **Licensing.** Public-domain and public-record sources only, provenance recorded per file.
- **Ingestion time.** The embeddings run on CPU; a corpus of this size will take hours in the background, which is
  why ingestion is started before question authoring.
- **A second stack costs memory and disk.** Measured before starting: the box has about 29 GB available and 158 GB
  free, and the second stack stays a subset of services; it can be stopped between phases.
