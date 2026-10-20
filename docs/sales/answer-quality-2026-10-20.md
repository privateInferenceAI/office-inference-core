# Answer quality: what we measured, October 2026

Prepared 2026-10-20. This document reports measured results of the Office Inference core stack on our own
acceptance corpus. It is written so that a client, an auditor or a salesperson reads the same numbers and the
same method.

## The short version

On our acceptance corpus of 539 documents and 100,478 indexed passages, the current configuration answered
**53 of 53 expected outcomes in each of five repeat runs** of our live acceptance set. On a wider, harder set it
answered **84.2 of 85 on average** (range 84 to 85) across five runs.

Every answer is grounded in retrieved documents and carries a source citation with document and page. Answers in
which a quoted figure cannot be found in the retrieved evidence are sent back or refused rather than returned as
fact. When the documents do not contain the answer, the system is required to say so; a deterministic check
refuses questions about the company's own policies when no retrieved passage states them.

Median response time is about 3 seconds and the 95th percentile is under 14 seconds on the measured deployment.
Provider spend is about 1.2 to 1.3 US cents per answer at about 2.5 to 3 provider calls per answer. Infrastructure
is priced separately.

## What was measured

| Item | Detail |
|---|---|
| Deployment | Office Inference cloud build (`oi-core-dev-cloud`), single box, Docker Compose |
| Model | Claude Haiku 4.5 on Amazon Bedrock, through the LiteLLM gateway, tool loop enabled |
| Corpus | 539 documents, 100,478 indexed passages; public-domain filings, tables and synthetic company documents; frozen for the measurements |
| Live acceptance set | 40 questions plus 5 mechanism probes; scored as 53 role-runs per run (8 exact-number, 8 prose, 8 unanswerable, 8 cross-document, 8 access-boundary asked as both roles, 5 probes) |
| Wider set | 72 questions plus the same 5 probes; scored as 85 role-runs per run |
| Repeats | Five runs per configuration, same corpus, same model, same flags |
| What counts as correct | The expected value is in the answer, the expected document is cited, and the answer is not a fallback. For a refusal question, the system must decline and must not return a value it was told to withhold. For an access question, staff must be refused and executive must answer |

Both sets are measured through the assistant API exactly as a client would call it, including retrieval, access
filtering, the tool loop, answer verification and the audit record.

## Results: live acceptance set

Five repeat runs on the current configuration:

| Run | Score |
|---|---|
| 1 | 53 of 53 |
| 2 | 53 of 53 |
| 3 | 53 of 53 |
| 4 | 53 of 53 |
| 5 | 53 of 53 |

Every category passed in every run: exact numbers from tables, prose facts, unanswerable questions,
cross-document arithmetic, access boundaries for two roles, and the five mechanism probes (pinpoint row in an
ordinary table, pinpoint row in a 41,000-row table, a refusal for a question only an unindexed data dump answers,
a numeric-dump competition case, and the PII redaction gate).

The immediately preceding batch scored 53, 53, 53, 52, 53. The single failure was one access-boundary run where
the executive session said it could not find a figure it had answered in the other four runs. That is the known
run-to-run flake class described under limits.

## Results: wider, harder set

This set exists to test what the live set cannot: aggregation over several table rows, date arithmetic,
multi-document reasoning, documents no earlier question touched, and policy questions in several phrasings.

| Category | Passed over five runs |
|---|---|
| Aggregation and multi-row arithmetic (8 questions) | 40 of 40 |
| Date arithmetic (4 questions) | 20 of 20 |
| Multi-document reasoning (4 questions) | 20 of 20 |
| Untouched-document coverage (10 questions) | 46 of 50 |
| Unanswerable and policy-absence questions (14) | 70 of 70 |
| Access boundary, both roles (16 role-runs) | 80 of 80 |
| Live-set categories, repeated inside this set | 100 percent |
| Five mechanism probes | 25 of 25 |

**Overall: 84, 84, 85, 84, 84 of 85 (mean 84.2).** The only failure is one question about Apple's employee
headcount in four of five runs. The passage that states the figure is indexed, but a very long HTML annual report
offers many similar-looking financial passages and retrieval missed the headcount paragraph in those runs. It is
the first measurement of a real limit (needle retrieval in very long documents), and it is the kind of thing the
next test corpus is being built to explore.

## Results: the second corpus

A second corpus was built to test whether the first corpus's results generalise, and to settle the row-query
question on a different document mix: 40 real public documents (14 US public companies across seven industries,
filings and contracts, plus 10 US government PDFs) and 40 labelled synthetic small-business documents, including a
policy-rich handbook, a 35,600-word facilities manual and a 6,010-row transaction ledger. 80 documents, 24,295
passages.

35 questions, every value read from the documents and mechanically validated against the index. Five repeat runs
with the structured row query off and five with it on, same configuration otherwise:

| Phase | Five runs | Mean | Latency p50 | Latency p95 | Cost per answer |
|---|---|---|---|---|---|
| Row query off | 30, 32, 31, 31, 31 | **31.0 of 37** | 2.27 s | 12.25 s | 0.01471 USD |
| Row query on | 32, 32, 32, 31, 32 | **31.8 of 37** | 2.37 s | 12.03 s | 0.01349 USD |

The questions are deliberately harder than the acceptance set, so the score is not comparable to the 53 of 53
figure. What matters is the difference: the row query fixed the plain table-number question in all five runs and
removed no correct answer. It did not fix the long-document needles, which reproduced the wider set's finding on
a different long document. The honesty check held in both directions: stated policies were answered, and absent
policies were refused in all 20 refusal runs.

One measured limit came out of the corpus: a table over 5,000 rows keeps bounded row groups and records the
omission, so on the 6,010-row ledger rows 0 to 2,412 are searchable and the remaining 3,600 are not reachable by
retrieval or by the row query. That is recorded as the next data-path question, not hidden in an average.

## Search and retrieval quality

The questions above are answered through retrieval. Three measurements describe the search behaviour directly:

- **Citations are checked, not just attached.** Every numeric claim in an answer is verified against the
  retrieved passages. An answer stating a figure that appears nowhere in the evidence is rejected before it is
  returned.
- **The structured row query improves exact-number accuracy.** In a five-run comparison measured on the earlier
  instrument, enabling the row query raised the score from **43.0 to 46.6 of 52**, exact-number questions from
  **4.8 to 7.4 of 8**, and a large-table pinpoint probe from **0 to 4 of 5 runs**. Unsupported-number refusals fell
  from **7 to 0** in the treatment window. The row query is one parameterised SELECT with the session's access
  roots in its WHERE clause; it reads table rows directly instead of hoping an embedding retrieves the right row.
- **Access filtering is applied in the retrieval path.** In five runs of the access-boundary questions, staff
  never received a passage from an executive-only document and never received the withheld figure; the executive
  session answered the same questions. The wider set repeated this on documents the earlier set never touched:
  80 of 80 role-runs passed.

## Reliability and controls

**A provider outage does not become a blank answer.** In five runs with the primary model provider failed on
purpose, **all 265 answers came through the fallback** and none degraded: the same model, invoked through a second
Amazon Bedrock region, answered with the same score as healthy traffic (52 of 53 in every run). With healthy
traffic, **0 of 492 model calls failed over**. When both regions were failed in a small demonstration, a different
model answered every question (4 of 4 answers, 2 of 4 exact-number answers correct), so the system degrades in
quality rather than failing.

**PII is redacted before an answer leaves the system.** A canary document with reserved test values is used as a
probe: an answer that quotes the value must return it masked, and it does in every run. Phone numbers, card
numbers and social security numbers are matched and replaced with markers.

**The system refuses when the documents do not answer.** A deterministic honesty check refuses questions about
the company's own attributes (its leave policy, its dress code, the policy on a topic) when no retrieved passage
states them. It covers the phrasings measured: "the company's", "our", and "the policy on X". Before the check
existed, a question about parental leave was answered from general knowledge instead of refused. Seven
policy-absence probes refuse as required, and the control is in every run of the acceptance sets.

**Every answer is recorded.** The audit chain records what was asked, what was retrieved, which model answered,
what was cited, what was redacted and whether the answer was verified. The model that answered is recorded even
when a fallback answers.

## Speed and cost

Measured on five-run sets through the API, client wait including retrieval and the tool loop:

| Metric | Value |
|---|---|
| Median response time | 2.8 to 3.4 seconds |
| 95th percentile | 11.8 to 13.6 seconds |
| Provider calls per answer | 2.5 to 3 |
| Provider spend per answer | 0.0118 to 0.0134 USD |

These are provider costs only. The hardware, the database, the vector store and support are deployment costs and
are quoted separately.

## What this does not claim

- **One corpus, one deployment, one model.** These are our numbers on our acceptance corpus, not a benchmark of
  all documents or all configurations. A client corpus can behave differently, which is why the next step is a
  second, differently shaped corpus and then a client's own documents.
- **The questions are ours.** They were written from the indexed text, with the carrying passage quoted and the
  expected value verified, and they are reviewed. They are a fair test, not an independent third-party benchmark.
- **Small samples.** Five runs per configuration give a range, which is why ranges are shown. Read the range, not
  a single number.
- **Known flake classes.** Occasional run-to-run retrieval or tool-loop variation can cost a question in one run
  (one access question in one of the recent five runs; one long-document needle in four of five wider runs). The
  system degrades to a refusal or a fallback answer, never to a fabricated figure for numeric questions.
- **Not every question shape has been measured.** The honesty check covers the phrasings tested; a differently
  worded policy question may still be answered from general knowledge, and the wider set now keeps that boundary
  measured as we widen it.

## How a client should read this

The honest summary is: on our test corpus, simple factual, numeric and cross-document questions from the
documents are answered correctly and cited, access boundaries hold, PII is masked, provider outages are covered,
and the failure modes we find are long-document needle retrieval and occasional run variance. The next proof is
the same instrument pointed at your documents, not ours.

## Where the numbers come from (internal)

Every figure above is reproducible from committed evidence:

- `docs/reviews/wider-set-2026-10-19/README.md` (wider set, latency, cost)
- `docs/reviews/gate-phrasings-2026-10-19/README.md` (current five-run acceptance result)
- `docs/reviews/honesty-gate-2026-10-19/README.md` (honesty control, previous batch)
- `docs/reviews/regional-fallback-2026-10-19/README.md` (provider failure and fallback chain)
- `docs/reviews/query-tool-2026-10-19/README.md` (row query comparison)
- `docs/reviews/acceptance-panel-2026-10-19/README.md` (latency and cost method)

Raw run files live on the deployment box under `/tmp/oic-*` and are the source of the committed summaries.
