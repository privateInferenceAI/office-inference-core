# Retrieval architecture, final version for the decision document

Supersedes `c2-retrieval-architecture.md` where the two disagree. Written 2026-10-11.
Reviewed against two critiques: `office-inference-retrieval-adversarial-review.md` (evidence) and the
client-reality review.

---

## 1. Verdict in one page

The shape of the architecture survives both critiques. Eight things change, and four of them are
load-bearing.

**Accepted, and these four change what we build.**

1. **The Qdrant version correction is deleted.** The synthesis claimed the build brief pins
   Qdrant v1.11.3 and that native RRF is therefore stuck at k=2. That is wrong for this repository.
   `deploy/office/compose/docker-compose.yml:48` says "Qdrant v1.19.2, current release line as of
   2026-10-11", and the string `1.11.3` appears nowhere in `office-inference-core`. I verified both.
   The v1.11.3 pin belongs to `ai-stack`, an unrelated GPU build, and it appears in
   `04-fusion.md:54` and `:577` where the researcher was explicitly reading the ai-stack compose file.
   The synthesis copied that constraint across projects. **Fusion still moves into our own code, but
   the reason is citation determinism, not a version limit.** This is also the class of error the build
   brief already lists as a trap it paid for once, so the decision document should record the correct
   pin explicitly rather than leave it to be inferred from either document.

2. **The MiniCheck false-negative rate drops from a headline number to a directional finding.** The
   40 percent figure comes from 24 of 150 pairs. `08-verification.md:106` says "The metrics are on
   those 24 pairs, not 150", "Treat it as a direction, not a number", and section 9 says it is not a
   measurement to put in a client document. I confirmed the artifact: `scratch/log_minicheck.txt` ends
   at `24/150`, while `log_deberta.txt` and `log_modernbert.txt` both carry a `DONE ... n=150` line.
   The number stays in the engineering record. It does not go in front of a client as a measurement.

3. **The reranker's CPU cost is restated as unmeasured.** The synthesis quoted "2.0 to 9.3 seconds per
   query", which is BEIR's paper measurement of a cross-encoder over BM25 top-100 on BEIR's hardware,
   for a model roughly a tenth the size. Our own measurement is
   `bench_tei2.json`: 133,101 ms median at 50 candidates, at load average 28.4 on 8 threads, and
   `05-reranking.md:373` says of exactly those figures that they "are not a valid latency estimate for
   the target hardware and must not be quoted as one." The synthesis quoted the paper number and
   ignored its own invalid one, which understates the uncertainty in both directions.

4. **The conformal calibration set excludes the must-refuse questions.** A question with no supporting
   passage produces no support score, so it cannot be an exchangeability unit. The synthesis counts
   all 40 calibration questions as usable. Seven of them are not. This is a real design defect and the
   fix is a paragraph, not a rebuild.

**Accepted, and they make the product better.**

5. **Nothing in the four verification layers checks that the source is the right source or that it was
   read correctly.** Every layer checks the answer against the passage. A misread scan and a superseded
   fee schedule both pass all four. A fifth layer sits at the bottom, costs no model, and ships in
   Wave 4.

6. **The answering model was never named, and it competes for the same cores.** This is the largest
   gap in the synthesis. The retrieval path is CPU-clean. The product is not. The office build needs
   an explicit decision with a licence-checked default.

7. **Ingestion throughput is unmeasured and is the schedule risk.** Docling's own report gives 3.1 s
   per page average and 13 s per page where OCR runs. The arithmetic for a scanned archive is weeks,
   not days. One sampling run against the client's own share converts the largest commitment we make
   into a known quantity.

8. **Fusion rank inputs must be logged per query.** The lexical arm's justification rests on a
   comparison the synthesis misquoted, and the fix is instrumentation, not a different engine.

**Rejected, with reasons, in section 3.** The short version: the critiques' two best suggestions,
consolidating onto pgvector and dropping the reranker entirely, are both wrong for reasons I give
below. Meilisearch, which one critique floats, fails the licence test in the `SPDX-License-Identifier`
line itself.

**Correctness of the citation is decided before ranking, and recall is decided before ranking.** That
ordering is unchanged and it is what the build sequence follows.

---

## 2. The corrections, with the check that settles each

### 2.1 Qdrant: the pin is v1.19.2 and the correction was wrong

| Claim | Status |
|---|---|
| Build brief pins Qdrant v1.11.3 | **False.** No version in the brief; compose says v1.19.2 |
| `1.11.3` appears anywhere in office-inference-core | **False.** Zero matches |
| `1.11.3` is the ai-stack pin | **True.** `04-fusion.md:54` states it while reading the ai-stack compose file |
| Native RRF at k=2 is the only option on our pin | **False.** Configurable k is v1.16.0, weights v1.17.0, Formula Query v1.14.0, all below our pin |

What actually changes as a result:

- The DBSF rejection reason "cannot be combined with a metadata boost on our pin" was a v1.11.3 fact.
  On v1.19.2 this needs re-checking, and I have not verified it. It stays a live fallback rather than
  a footnote, which is what the critique asked for.
- Native weighted RRF becomes available. It is still not the right choice, for the reason in 2.3.
- The "upgrade Qdrant for weighted RRF" line in the rejected list is deleted. There is nothing to
  upgrade to.

*Evidence: measured. I read the compose file and grepped the repository.*

### 2.2 The lexical arm: a misquote that cuts against ts_rank, and the fix

This is the most consequential correction and it is my own error, not the critique's.

The synthesis writes that "ts_rank found the correct chunk first 60/60 on planted identifiers in a
200,000-chunk corpus, as did ParadeDB BM25 and vchord_bm25, and 190/200 on short documents." That
conflates tests. The actual measured table, `01-lexical-engine.md:247-253`, over 200 short documents
each containing one identifier, is:

| Operator | Full identifier, hits@1 | Trailing digits, hits@1 |
|---|---|---|
| `similarity` via `%` | 0/200 | 0/200 |
| `word_similarity` | 190/200 | **189/200** |
| `strict_word_similarity` | 190/200 | 189/200 |
| `ts_rank` | 190/200 | **40/200** |
| BM25 (pg_search) | 190/200 | **189/200** |

The 60/60 figures belong to a different test, the large-corpus planted-identifier run where
ParadeDB was the only engine that scored 60/60 on the *partial* identifier
(`01-lexical-engine.md:32-33`) and vchord scored 0 hits@1 on trailing digits
(`01-lexical-engine.md:335`).

So on trailing-digit queries ts_rank scores 40/200 against BM25's 189/200. **The synthesis used a
tie between ts_rank and BM25 as the reason the ranking function does not matter. There is no tie.**
BM25 is materially better on exactly the failure mode the architecture is built around.

**Why the architecture does not change anyway.** The measured failure is tokenisation, upstream of
ranking: `INV-2024-04417` is split into `inv`, `-2024`, `-04417`, so a user typing `04417` gets zero
hits because the indexed lexeme is `-04417` with the hyphen attached
(`01-lexical-engine.md:27-30`). The identifier column fixes that and BM25 does not, on its own. Once
the identifier is extracted whole and lowercased into its own GIN-indexed column, the trailing-digit
query matches a lexeme that exists, and the ranking function has almost nothing to do. That reasoning
is sound and it was labelled as inference in the source.

**What I am adding, because the misquote means the claim must now be measured rather than asserted.**
A query-time rank log. Every query records, per arm and per fused position: the chunk id, the raw arm
score, the arm rank, whether the identifier column produced the match, and the fused rank. Two
readings follow from it:

- If `identifier_column_hit` is true for the queries that previously failed, the diagnosis was right
  and the column is doing the work.
- If ts_rank is losing to the identifier column on an appreciable share of queries, the fix is a
  ranking arm, not a tokenisation arm, and the options are pg_textsearch or ParadeDB, not ts_rank.

Without this log, the lexical decision cannot be defended past a review. It is roughly 30 lines and
it belongs in Wave 2, not Wave 6.

*Evidence: measured. Table read from `01-lexical-engine.md:247-253`.*

### 2.3 Fusion stays in our code, but not for the stated reason

The synthesis's two reasons were both artifacts of the wrong pin. The correct reason, and it is
enough:

**Deterministic tie-breaking is a property of our code, not of the store.** Qdrant measured 12.5
percent of default-RRF top-10 results tying with their neighbour, against 2.8 percent at k=61 and zero
under DBSF (`04-fusion.md:169`, `:291`). Tied groups return in storage order, so a citation can move
between two identical runs of the same question. In a product whose entire claim is that a citation
is checkable, a citation that changes between runs is a trust defect regardless of which server
version we run.

Native weighted RRF on v1.19.2 would let us express k and weights. It would not give us a stable
order for tied scores, and it cannot be combined with a row-level Formula Query rescore because "a
main query is a single operation, so it cannot be both a fusion and a formula"
(`04-fusion.md:610-612`). So the metadata boost the synthesis wanted also lives in our code.

Keep: RRF in Python, k=60, equal weights, 500 candidates per arm, over-fetch then sort on
(score descending, id ascending) then truncate, arithmetic verified against
`qdrant_client/hybrid/fusion.py`. Delete the version argument from the document.

*Evidence: measured.*

### 2.4 Conformal calibration cannot include the must-refuse questions

The critique is right and this is a genuine design defect.

Split conformal prediction calibrates a threshold on nonconformity scores. A must-refuse question has
no supporting passage by construction, so no verifier support score exists for it. Including it in the
calibration set is undefined at best and breaks exchangeability at worst.

**The fix, stated precisely.**

- The 20 unanswerable questions measure a **retrieval** property: did the system return nothing, or
  did it return something and answer anyway. That is a binary mechanism check, counted and reported,
  not a calibration point.
- 13 of the 20 are answerable-but-absent. Those are the calibration set, at n=13, or n=20 if the
  seven must-refuse questions are replaced. n=13 is too small and I say so.
- Better: raise the answerable calibration count to 40 by adding 20 client-specific answerable
  questions, keeping the frozen 60 as the regression set. That is the honest way to get a threshold
  with any resolution, and it costs client time at install, which is the real price.

**And downgrade the guarantee's framing.** The synthesis says the conformal guarantee "is exact for
any n, which is precisely why it fits a 60-question world." The guarantee is distribution-free and
finite-sample valid, which is true. What it does not tell you is the *resolution* of the threshold.
For split conformal at coverage 1-alpha, the attainable coverage levels are multiples of 1/(n+1), so
at n=40 and alpha=0.10 the coverage floor sits at roughly 0.902 with about 2.4 points of granularity,
and the achievable quantile index lands on a coarse grid. State the threshold, the calibration-set
hash, the interval [1-alpha, 1-alpha + 1/(n+1)), and the granularity. Do not say "exact at n=60" to a
client without the second half of that sentence.

*Evidence: production practice for the method; the granularity arithmetic is mine.*

### 2.5 Layer 0: source correctness, which nothing currently checks

The client-reality critique's best finding, and I am conceding it in full.

Every verification layer checks whether the answer matches the source. Nothing checks whether the
source is the right source or was read correctly. The two most likely wrong answers in a small
business are a real number from a superseded document, and a real number from a misread scan. Both
pass the mechanical layer, both pass entailment, and both come out certified.

**Layer 0, deterministic, no model, ships with Layer 1:**

- **Supersession.** A document version registry with an explicit `is_current` flag, set at ingest and
  reviewable by a human. A claim whose only support is a non-current document is refused with the
  reason `superseded`, not answered quietly. Where two current-looking documents disagree on the same
  field, show both rather than picking one.
- **OCR confidence gate.** Docling exposes per-page confidence. A numeric or identifier claim whose
  supporting token comes from a page below threshold is refused with the reason `low_confidence_scan`,
  and the page image is shown beside the number so the user can read it themselves.
- **The literal identifier assertion.** If the answer cites an identifier or a number, it must appear
  literally in the retrieved page text after normalisation. This is the cheapest item in the entire
  architecture and the highest value.

Layer 0 converts the two worst failure modes from confident wrong answers into refusals. It needs
Docling's confidence field, a version table, and about 150 lines.

**[VERIFIED 2026-10-11]** Docling exposes confidence at the conversion result:
`ConversionResult.confidence`, documented in the Python API reference at
`https://docling-project.github.io/docling/reference/document_converter/`, with a dedicated concept
page at `/concepts/confidence_scores/`. So the field exists and the API path is real. **Still to
confirm at implementation time:** that it is populated per page for scanned input in the version we
pin, and what the value distribution looks like on the client's actual scans. A field that exists but
returns a constant is not a gate, and the threshold has to come from the corpus rather than from the
documentation. Check it in Wave 0 step 4.

*Evidence: the failure modes are production practice and are named in the client critique. The
confidence API is verified from Docling's own published reference. The per-page population on scanned
input is unverified.*

### 2.6 The answering model, which was missing

The synthesis specifies the retriever, the parser, the verifier and the refusal rule, and never names
the model that writes the answer. In the office build that model is local, it runs on the same cores
as the verifier and the parser, and its citation discipline decides the refusal rate more than any
retrieval parameter does.

**Decision to make explicitly, not by omission.** The office build ships a local instruct model behind
llama.cpp, and the choice is recorded in the manifest like every other pin.

**Default candidate, licence verified:** `Qwen/Qwen3-4B`, Apache-2.0, with the LICENSE read from the
repository root. **Second candidate:** `microsoft/Phi-4-mini-instruct`, MIT.

**The honest constraint.** The proposal's own open questions state that the office reply path is
single-digit tokens per second on CPU, and that figure is labelled typical rather than measured in
our own production-readiness document. I did not measure it. On that figure a 200-token answer is 20
to 40 seconds of generation, before verification. That is a different product from the cloud build and
it must be stated to the client in those terms.

**Which means the office build is a GPU build or a smaller product.** The company's own product
specification already puts 14B on a 24 GB GPU and 32B on a 48 GB GPU. The retrieval path described
here is CPU-clean. The product is not. The three options, in the order I would present them:

1. **Office build ships with a 24 GB GPU.** The retrieval architecture is unchanged, verification
   gets four to eight times faster, and the answering model is a 14B. This is the recommended shape.
2. **CPU-only, retrieval-first.** Answer generation is capped at a short extractive answer with
   citations and an explicit "here are the pages" mode. Honest, useful, and much cheaper to support.
3. **CPU-only, full generation.** Ship it only with a measured tokens-per-second number and a stated
   wall-clock expectation per answer, and do not sell it as conversational.

This is a scope decision for the decision document, not a retrieval decision. It is the one place
where the critiques changed the product rather than the plan.

*Evidence: the licensing claims are verified from repository LICENSE files. The throughput figure is
our own unverified statement. The options are inference.*

### 2.7 Ingestion throughput is the schedule risk

The parser is the correctness ceiling and it is also the schedule ceiling. Docling's own technical
report measures 3.1 s per page average on 8 x86 threads for a mixed 4,008-page corpus, and 13 s per
page where OCR engages. The build brief's "about nine days for a million pages" is 1.29 pages per
second, which is plausible for digitally-native PDFs and optimistic by roughly an order of magnitude
for a box of scans.

**Before the first client corpus:** run Docling over 200 pages sampled from the client's own share,
with their real scan quality and document mix, and record pages per hour. This closes the largest
commitment we make. Note in the runbook that the sampling run and the bulk run compete for the same
cores, so the number is a floor.

**And change the install shape.** First corpus is one folder, not the archive. That is the client
critique's recommendation and I am taking it: it makes the system trustworthy in a weekend and defers
the archive to a second visit.

*Evidence: the per-page figures are published measurements from Docling's technical report. The
archive arithmetic is arithmetic and labelled as such.*

### 2.8 Evidence downgrades

Four claims in the synthesis are weaker than it implied. Each is restated here in the form it can be
defended.

| Claim as written | Downgraded to |
|---|---|
| "The measured spread between models is smaller than the spread between pipeline choices" | **Delete.** The claim is asserted and its two cited sources do not support it. `02-embedding-model.md` 5.2 shows dataset-dependent ordering, and 5.3 is a 59-chunk, 30-query benchmark. `07-chunking.md:40-42` contains the opposite finding from a larger study: "differences among embedding models are larger than differences among chunking strategies", over 1,820 configurations. The pipeline-spread claim is true for dense-vs-hybrid differences and false as a general statement |
| Page-level chunking "measured the highest average accuracy (0.648)" | **Present as a tie and lead with the citation requirement.** `07-chunking.md:230` says the 0.648 against 0.645 gap "is *not* meaningful". The variance result (0.107) is supporting evidence from one vendor's datasets. The reason to chunk on pages is that the product promises page numbers |
| MiniCheck "missed 40 percent of unsupported sentences on our sample" | **Directional, n=24.** Carries the source's own "treat it as a direction, not a number". The number that goes in front of a client is Vectara's published range, 31 to 68 percent for this class, which is the source's own stated preference |
| The reranker's "blocking defect" latency | **Unmeasured.** BEIR's paper number for a smaller model is 2.0 to 9.3 s; our own 50-candidate run was 133,101 ms at load average 28.4 and must not be quoted. Measure idle before deciding |

**One more number the synthesis withheld, and it is client-facing.** MiniCheck's full row in
`08-verification.md:100` is 55.0 balanced accuracy, 40.0 false-negative rate, **50.0 false-alarm rate**,
27.6 s per pair. At its default 0.5 threshold, half of correct claims get flagged. That is a product
experience problem on the same order as the miss rate, and it is what the amber-versus-refusal
threshold has to be tuned against. Both numbers go in the acceptance test.

---

## 3. What I rejected from the critiques, and why

### 3.1 Consolidating onto pgvector to remove Qdrant

Rejected, and it is the client critique's headline infrastructure suggestion.

- **pgvector is PostgreSQL-licensed and shippable.** That part is true and I have no objection to the
  licence.
- **It does not remove the second store, it moves it.** The audit sink, the ACL predicates, the
  lexical arm and the spend tables already live in Postgres. What the office build actually has is one
  datastore plus one vector store, and the vector store is already backed up by a directory tar in the
  existing backup script.
- **The reason it is rejected is recall, not ops.** Qdrant measured the best-possible-ordering gap over
  retrieved candidates at 200 depth running 0.247 to 0.487 nDCG@10. Recall is decided before ranking,
  and it is the dominant quality term. pgvector's HNSW is fine and its filtered-recall behaviour under
  a two-value AND predicate is less well characterised than Qdrant's, which Qdrant itself measured
  collapsing to 39.7 percent on exactly that filter shape. Trading a measured recall characteristic
  for one fewer backup path is the wrong direction for a product sold on citations.
- **Telemetry.** The client critique objects to Qdrant's default-on telemetry. That is a real and
  cheap fix, and the office compose file already sets `QDRANT__TELEMETRY_DISABLED: "true"` at
  `deploy/office/compose/docker-compose.yml`. It needs a test that proves it, not a different store.

*Evidence: the telemetry line is verified from the compose file. The recall gap is measured. The
filter-collapse figure is measured by Qdrant.*

### 3.2 Cutting the reranker from the office build permanently

Rejected as a permanent cut, accepted as a gate. The synthesis already demotes it to a gated
experiment, and the gate is the right instrument.

- The evidence genuinely conflicts: BEIR measures BM25 plus a cross-encoder as the best of ten systems
  and the only one beating BM25 on 16 of 18 datasets, while only two of five reranker gains survived
  held-out validation in Qdrant's study and one preprint measured cross-encoders *hurting* nDCG by 0.3
  to 3.1 percent.
- The reconciliation is fit, not quality: none of the four models tested was trained on contracts,
  invoices or scanned forms. That is an argument about our corpus, and it is testable on our corpus.
- Cutting it before measuring leaves the product with no second stage in the case where the ceiling
  diagnostic says ranking is the bottleneck. The gate costs one afternoon.

**What the critique got right and I am adopting:** the fallback belongs *inside* the gate, not in
Wave 6. If the latency measurement fails at 50 candidates, the gate's outcome is
`gte-reranker-modernbert-base` at 149.6M with ONNX int8, measured on the same 60 questions, or no
reranker. Name that outcome before running the gate, so a failed gate does not become a shipped
product with no second stage.

**What the synthesis got wrong about the verifier comparison.** One critique notes that the document
rejects a 2-to-9-second reranker while shipping a verifier whose figures are comparable or larger, and
says the distinction is never stated. The distinction is real and it is this: the reranker sits on the
critical path *before the answer exists*, and it is optional. The verifier overlaps with streaming
generation, and it is the product. Same order of latency, completely different position in the
request. That sentence goes in the document.

*Evidence: the conflict is measured across sources. The distinction is inference.*

### 3.3 Meilisearch, floated as the nearest functional match

Rejected, and the licence check settles it in one line.

I fetched `https://raw.githubusercontent.com/meilisearch/meilisearch/main/LICENSE`. It reads
`SPDX-License-Identifier: MIT AND BUSL-1.1`, with the engine split between `LICENSE-MIT` and
`LICENSE-EE` under the Business Source License 1.1. A BUSL component carries a production-use
restriction, and `AND` in an SPDX expression means both sets of obligations apply. The official
container would pull the BUSL parts. So the licence split is not a detail to be tidied up later, it is
the answer: **Meilisearch is not shippable in a build we sell without reading `LICENSE-EE` in full and
confirming which components a default container runs.** I verified the top-level LICENSE only, and I
am not asserting more than that. It stays out.

*Evidence: licence text fetched from the repository today. The consequence of `AND` is my reading.*

### 3.4 The critique's claim that the verifier's batch numbers may be "per batch of 8"

Rejected. The source is unambiguous and its own second measurement confirms it.
`08-verification.md:114-120` presents a table headed "Per-pair latency" under "Measured single-thread
with a 1,200-character premise at 384 tokens", with rows 1 / 8 / 16 giving 1,583 / 1,772 / 1,636 ms.
The report then states that a second run with 6 threads available, while three scoring jobs ran
concurrently, gave 3.5 to 4.0 seconds per pair, which is the same compute divided across fewer cores.
A per-batch-of-8 reading would make that second measurement incoherent. So "batching does not help" is
correct, the per-claim figures stand, and the parallel-verification design does not need to change.

**What the critique got right underneath the objection:** there is no raw artifact. The reranker has
`bench_tei2.json`, the NLI sweep has `log_deberta.txt` with per-pair timings, and the batch figures
exist only as a table inside a report. That is a fair reproducibility complaint and the fix is one
script. It does not change the conclusion.

*Evidence: measured, read from the source and corroborated by its second measurement.*

---

## 4. The architecture

Unchanged from the synthesis except where marked **[CHANGED]** or **[ADDED]**.

### Stage 1. Ingest and parse
**Choice:** Docling in its own container on both builds, layout, table and OCR models on CPU. Per item:
page, bbox including coord_origin, charspan. TableData kept with cell spans and explicit
column_header / row_header / row_section / fillable flags. **[ADDED]** per-page OCR confidence
recorded, verified present before Layer 0 depends on it. **[ADDED]** `RESOURCE_LIMITS` set on the
container, because the project has open reports of the process consuming all available memory on
large scanned files and being killed mid-run with no partial progress.

**Why:** This stage decides whether the product can be right. A fee schedule read as a token stream
produces a confidently cited wrong number, and no downstream component recovers it. It is also the
hardest part for a competitor to copy, because the market's systems treat documents as text streams.

**Rejected:** PyMuPDF and pymupdf4llm, AGPL-3.0 with an unpriced Artifex commercial licence. MinerU,
Apache-2.0 wrapped in bespoke terms including a revenue threshold. Unstructured, a second heavy parser
for no measured gain. marker and surya, a second parser rather than a better one.

**Evidence: production practice.**

### Stage 2. Chunk
**Choice:** Page-bounded structure-aware chunking at 384-512 tokens, zero to 32 tokens of overlap and
zero preferred. Never split a table that fits. A table spanning a page break stays one retrievable unit
with a page range and a table_id, and its rows are excluded from the surrounding page chunks.
**[CHANGED]** Each table also gets per-row entries carrying the row's own page and header names,
alongside the whole-table entry, so a row on page 8 of a six-page schedule does not cite pages 7 to 9.
The whole-table entry stays the citation unit; the row entries are retrieval units. Store page_start,
page_end, bbox_start, bbox_end, charspan, is_ocr, coord_origin, plus sheet_name and cell_range for
spreadsheets. Row-wise table serialisation with header names folded in, preceded by caption and
heading path. `chunk_id = uuid5(doc_id, doc_version, chunker_version, ordinal, content_sha256)`.

**Why:** **[CHANGED]** The reason is the product requirement, not the benchmark. Page-level citations
are mandatory, so the page is the citation unit and chunking must respect it. NVIDIA measured page
chunking at 0.648 average accuracy with the lowest standard deviation across its datasets, against
0.603 to 0.645 for token-based approaches, and the accuracy gap is inside the noise by the source's own
statement. The variance result is supporting evidence from one vendor's datasets. 128-token chunks
underperform across three independent studies. Overlap is generally neutral to negative and makes two
chunks claim the same sentence, which reads as corroboration in a citation panel and is not. The
deterministic prefix plus table caption beat GPT-4-generated keywords and summaries on page accuracy,
67.38 against 46.10 and 62.41.

**Rejected:** Semantic chunking, whose best-controlled study concludes the cost is not justified and
which needs corpus-global statistics, breaking incremental ingestion and stable IDs. Summary indexing
and RAPTOR, evaluated at low performance by Anthropic and measured worse than a free prefix. High
overlap. Small-to-big as a stage: the page is already a definite citation unit, so a parent pointer
adds index growth and context-budget risk for an unmeasured gain. The escape hatch is prev_chunk_id /
next_chunk_id at ingest, which needs no re-embed.

**Evidence: measured for the component results, with the accuracy comparison presented as a tie;
production practice for the ordering.**

### Stage 3. Contextualise
**Choice:** The deterministic half unconditionally on both builds: prepend document title, full
heading path, page label and table caption before embedding. Then a four-way ablation, none /
deterministic / deterministic plus local model / deterministic plus frontier model, to decide whether
generated context adds anything on top. Cloud build may generate the second half on Bedrock at ingest.
The office build treats it as an offline batch job, never inline, and **[CHANGED]** the office build
ships without it until the ablation earns it.

**[CHANGED] The metadata rule, restated so it does not contradict this stage.** Governance metadata
(ACL labels, document ids, hashes, versions, tenant field) never enters the embedding input.
Citation-structural text (title, heading path, page label, table caption) does. The synthesis stated
the first as an absolute rule while this stage does the second, and an implementer following the
absolute reading would strip the heading path and lose the one contextualisation gain the document
claims is measured.

**Why:** The deterministic half is free, measured to help, and Docling already implements it:
`DocMeta.headings` is included while `doc_items` and `origin` sit in `excluded_embed`. The generated
half is a genuine open question. Anthropic measured 35 percent fewer top-20 retrieval failures from
contextual embeddings rising to 67 percent with reranking, and their own ablations show the gain comes
from chunk-specific disambiguation rather than added words. Nobody has published a comparison of
small-model-generated context against a deterministic heading path. The cost lands on generation, the
slowest operation on CPU: 50,000 chunks at 100 generated tokens is 5M output tokens, 139 hours at 10
tokens per second.

**Rejected:** Voyage contextualized chunk embeddings, a hosted API, disqualified offline and an
unnecessary dependency in the cloud build. Summary-based indexing and generic document summaries, per
Anthropic's own ablations. Late chunking deferred rather than rejected: the method is sound and bge-m3
has the 8,192-token context for it, but it needs token-level embeddings before pooling and TEI returns
pooled vectors.

**Evidence: measured for the deterministic half; open question for the generated half.**

### Stage 4. Store, embed and govern
**Choice:** `BAAI/bge-m3` at 1024 dimensions, CLS pooling, no prefix, MIT, ungated, pinned by revision
and mounted for air-gapped service. TEI `cpu-1.9` with `MAX_WARMUP_SEQUENCE_LENGTH=256`,
`MAX_WARMUP_BATCH_SIZE=4`, `--max-batch-tokens 2048-4096`, `--max-concurrent-requests 8-16`. A hard
pre-send token assertion in the ingestion worker. Qdrant 1024-dim cosine with M=32 and
`EF_CONSTRUCTION=256`, a keyword `is_tenant` index on the ACL field, plus `is_current`, `doc_id`,
`doc_class`, `source_format`. Three-layer metadata schema. A per-collection embedding contract storing
model id, revision, sha256, dimension, pooling, normalisation, both prefixes, both prompt names,
truncate_dim, max_tokens, chunker_version, parser_version and schema_version, stamped on write and
refused on mismatch at read.

**Why, restated honestly.** bge-m3 is not the strongest zero-shot retriever on the evidence. Cirillo et
al. measured 0.437 average nDCG@10 on four BEIR subsets against 0.546 for `multilingual-e5-large`,
losing on every subset, and their stated mechanism is a multi-task capacity tax from heads we cannot
use through TEI. **[CHANGED]** The synthesis defended this by claiming the spread between models is
smaller than the spread between pipeline choices. That claim has no source and the largest controlled
chunking study found the opposite. So the defence is now stated as what it actually is:

- **A risk decision, not a quality one.** `multilingual-e5-large` requires mandatory `query: ` and
  `passage: ` prefix discipline. TEI exposes one server-wide default prompt, not separate query and
  passage prompts, so setting the server default to help the query path silently corrupts every stored
  document vector. A prompt convention moved Qwen3-Embedding-0.6B by 17.1 accuracy points in a
  documented case. A zero-prefix model cannot be misconfigured that way.
- **A contract that makes the swap cheap.** The embedding contract turns a future model change into a
  resumable re-embed with one-command rollback rather than a migration.
- **A known deficit we say out loud.** 0.109 nDCG on one external study, on a corpus that is not ours,
  with the challenger pre-registered.

**[CHANGED] The e5-large challenger moves from Wave 6 to the first golden-set execution.** One extra
embedding pass and one extra golden run, and the contingency table is recorded alongside bge-m3's. The
downside it protects against is re-embedding every client corpus. Deferring it behind a trigger when it
costs one pass is the wrong trade.

**Rejected:** `intfloat/multilingual-e5-large`, the pre-registered challenger, held back by the prefix
discipline and 512-token context. `microsoft/harrier-oss-v1-0.6b`, the pick if a corpus is materially
CJK, but no production history and its licence is a card declaration with no LICENSE file. Qwen3-
Embedding-4B and 8B, Apache-2.0 and TEI-served but 28x and 57x the per-token CPU cost. Rejected on
licence: all Jina v3/v4/v5 embeddings (CC-BY-NC-4.0, or the Qwen Research License for v4),
`nvidia/llama-embed-nemotron-8b` (non-commercial), `google/embeddinggemma-300m` (Gemma terms plus a
manual gate, a second independent disqualifier for a no-internet install).

**Evidence: measured for the BEIR deficit; inference for the risk weighting.**

### Stage 5a. Retrieve, dense
**Choice:** Qdrant prefetch of the dense arm, `hnsw_ef` set to hit a recall target and no higher.

**Why:** Qdrant measured recall against exact at 0.986 at ef=16 rising to 1.000 at 256, with raising it
further moving fused nDCG@10 by at most 0.0022 while latency rose up to 49 percent. At our scale this
is a non-issue.

**Rejected:** Raising `hnsw_ef` for quality. A managed knowledge base, which would run retrieval
outside the audit sink and outside the verifier.

**Evidence: measured.**

### Stage 5b. Retrieve, lexical
**Choice:** Postgres native full-text search as the lexical arm. `tsvector` with `english` for prose
and `simple` for identifiers, `setweight` using the default ramp {0.1, 0.2, 0.4, 1.0} for D/C/B/A, GIN
indexes, `ts_rank` with an explicit normalization argument. A regex-extracted identifier column
populated at ingestion, lowercased, stored whole and as separated components, GIN-indexed. `pg_trgm`
installed and reached only through `word_similarity` / `strict_word_similarity` over a candidate set.
**[ADDED]** the per-query rank log from 2.2, recorded in the audit row.

**Why, corrected.** The measured failure is tokenisation, upstream of ranking: the default parser
splits `INV-2024-04417` into `inv` / `-2024` / `-04417`, so a user typing the trailing digits gets zero
hits, and `74LS` misses `74LS00`. The identifier column fixes that and needs no new dependency. The
missing IDF does not bind as hard as it looks because rank fusion discards score magnitude. **[CHANGED]
But the claim that ts_rank ties BM25 is false.** On trailing-digit queries ts_rank scores 40/200
against BM25's 189/200, and `word_similarity` matches BM25 exactly at 189/200. So ts_rank is the
weakest of the viable arms on our own measured failure case, the identifier column is what rescues it,
and the rank log decides whether that rescue is complete. If it is not, pg_textsearch is an index
change rather than a redesign. Two configuration traps stand as originally written: `ts_rank`'s default
has no length normalization and returns an identical 0.06079271 for a 5-word and a 1,205-word
document, and `ts_rank_cd` scales linearly with term frequency and returns a fifty-repetition document
first.

**Rejected:** ParadeDB pg_search, the best-engineered option and the only engine scoring 60/60 on
partial identifiers, blocked on licence and price: AGPL-3.0 only, verified three ways, with a
commercial licence that is not published. Get the number before the next review. `tensorchord/
vchord_bm25`, dual AGPL-3.0 or Elastic License v2, and independently disqualified on tokenisation with
0 hits@1 and 10 hits@10 out of 60 on trailing digits. Elasticsearch, triple-licensed
AGPL-3.0-only / SSPL v1 / ELv2, no shippable self-hosted configuration. OpenSearch, Quickwit and
Tantivy, licence-clean and rejected on operational cost. Typesense, GPL-3.0, where distributing the
container is conveyance. Meilisearch, **[CHANGED]** now rejected on verified licence text:
`MIT AND BUSL-1.1`, not "the subset we do not need". Tiger Data pg_textsearch, PostgreSQL-licensed,
architecturally right with WAL and pg_dump support, deferred on maturity and now the named fallback if
the rank log shows ts_rank losing.

**Evidence: measured on our own hardware.**

### Stage 5c. Candidate depth
**Choice:** 500 candidates per arm before fusion as the default, **[CHANGED]** with an adaptive cap on
the lexical arm driven by its measured latency. Depth is set by the reranker's CPU cost if a reranker
runs, and by measured arm latency otherwise.

**Why:** Recall is decided before ranking and it is the dominant quality term. Onyx documents raising
its default from 100 to 500, after 750 caused poor search performance, and states that fetching only
10 from each arm would need perfect overlap to produce a good hybrid ranking. Qdrant measured the
best-possible-ordering gap over retrieved candidates at 200 depth running 0.247 to 0.487 nDCG@10.
**[CHANGED]** The fixed-500 rule is right for the dense arm and wrong for Postgres full-text, because
`ts_rank` consults every matching `tsvector` and common terms in a business corpus match thousands of
chunks. Cap the lexical arm on measured latency and record the cap in the audit row.

**Rejected:** A fixed depth tuned on historical queries, which a study found did not transfer across
five collections and five temporal snapshots. Deep retrieval only when a reranker is downstream.
Metadata pre-filtering as a recall mechanism: in Qdrant a document missing from both prefetch lists
cannot appear in the result, so a filter is a recall ceiling, never a recall source.

**Evidence: production practice, plus one measured gap.**

### Stage 6. Fuse
**Choice:** RRF in our own code, k=60, equal weights, over the per-arm candidate lists, with an
explicit deterministic tie-break: over-fetch, sort on (score descending, id ascending), then truncate.
Reproduce the Qdrant client formula `1 / ((rank + 1) / weight + k - 1)` with one-based ranks so the
arithmetic is verifiable against a reference implementation. Do not tune k or the weights on 60
questions. **[CHANGED]** The justification is citation determinism and the metadata boost, not a
version limitation.

**Why:** Qdrant measured 12.5 percent of default-RRF top-10 results tying with their neighbour against
2.8 percent at k=61 and zero under DBSF, and tied groups return in storage order, so a citation can
move between two identical runs. Cormack's k=60 is a weak default and he says so: fixed during a
pilot, not altered during validation, with MAP running 0.2123 at k=10 to 0.2098 at k=500 and flat from
30 to 100. Fusion in our code is also the only way to apply a metadata boost without a server-side
Formula Query, which cannot be combined with fusion in one operation.

**Rejected:** Native Qdrant RRF at its default, citation-unstable. Native weighted RRF on our actual
pin, which is expressible but gives no tie stability. Native DBSF, tie-free and beating default RRF on
three of five datasets, but outlier-sensitive per query, computed from a small top-k sample, warned
about as unstable by Qdrant themselves, and kept as the live fallback if RRF ties prove real.
**[CHANGED]** DBSF's metadata-boost objection was a v1.11.3 fact and needs re-checking on v1.19.2;
until it is checked, DBSF is a fallback and not a rejection. Fusing in Postgres, which would mean a
second retrieval system for a 20-line ranking step. Naive linear combination of raw scores. Plain
concatenation, kept as the CONTROL CONDITION in the harness.

**Evidence: measured.**

### Stage 6b. Rerank, gated
**Choice:** `BAAI/bge-reranker-v2-m3` (Apache-2.0, 568M) via TEI `cpu-1.9`, over the top 40-50 of the
fused list, behind a flag, shipped only if it survives three gates: an idle latency measurement on the
reference instance, the ceiling diagnostic, and a held-out comparison against fusion alone. Bounded at
Azure's 50. **[CHANGED]** The fallback is named inside the gate: if the latency gate fails,
`gte-reranker-modernbert-base` at 149.6M with ONNX int8 is measured on the same 60 questions, or no
reranker ships.

**Why:** The evidence conflicts and the constraint binds. BEIR measured BM25 plus a cross-encoder as
the best of ten systems and the only one beating BM25 on 16 of 18 datasets, +12.5 percent relative
average nDCG@10, with reranking worth more than late interaction. But only two of five reranker gains
survived held-out validation in Qdrant's study, on one dataset tuned fusion beat all four
cross-encoders at every candidate count, and a separate preprint measured cross-encoders hurting NDCG
by 0.3 to 3.1 percent while adding 560-2100 ms. **[CHANGED] And the CPU cost is unmeasured, not a
blocking defect.** BEIR's 2.0 to 9.3 seconds is their hardware and a smaller model; our 50-candidate run
was 133,101 ms at load average 28.4 and must not be quoted. The reconciliation is fit: a reranker helps
when its training distribution and context window match the corpus, and none of the four models tested
was trained on contracts, invoices or scanned forms. So it becomes an experiment whose first gate is a
measurement.

**Rejected:** Late interaction (`answerai-colbert-small-v1`, `colbertv2.0`, `GTE-ModernColBERT-v1`),
which ties the cross-encoder on quality while being faster, and is rejected because TEI serves no
multi-vectors at all, verified by cloning at HEAD with zero matches for colbert, multi_vector, max_sim
or maxsim anywhere in the tree, so it needs a third serving component, and because a 0.003 nDCG
difference is unmeasurable at n=60. **[CHANGED]** The speed comparison is not quoted as a CPU
advantage: the ColBERT rescore ran server-side on Qdrant Cloud Inference while the cross-encoder ran
locally on CPU, so the ratio is GPU-server against CPU-local and the source says so explicitly.
Trigger to revisit: recall below target with SEMANTIC rather than lexical misses, office build only.
LLM-as-reranker, the one option with measured evidence against it: zero-shot Setwise-3B scores
0.371/0.317 against BM25's 0.506/0.480 on TREC-DL, worse than not reranking, while 7B and 14B
zero-shot only reach trained-base-cross-encoder class. Qwen3-Reranker and mxbai-rerank-v2, licences
fine but causal LMs TEI cannot serve. All Jina rerankers, CC-BY-NC-4.0 with no published price, and
jina-reranker-v2 is the model that produced both of Qdrant's confirmed wins.

**Evidence: inference, as the source itself labels it.**

### Stage 7. Query side
**Choice:** Four things ship. (1) Rule-gated query rewriting for conversational follow-ups at
temperature 0.0, skipped on the first turn, validated by entity overlap before use. **[CHANGED]** cloud
build only; the office build does not run it. (2) A corpus-mined acronym glossary applied
deterministically, with expansions also indexed as document-side synonyms. (3) Structured date and
metadata extraction into filters, with the hard rule that a filter which cannot be applied produces a
refusal rather than an unfiltered search, and the extracted filter recorded in the audit row.
(4) A post-retrieval cascade that escalates on an empty result set, **[CHANGED]** bounded at one
escalation, logged with its reason and shown in the answer's trace. The user's question is stored
verbatim and never replaced, and entailment is checked against the ORIGINAL question.

**Why:** Rewriting is the only query-side technique with a clear replicated win, +13.7 percent relative
nDCG@5 on SemEval-2026 Task 8, and the same paper measured that first turns need no rewriting at all,
identical nDCG@5 at 29 percent less inference, and that technical domains want temperature 0.0 while
at 1.0 the financial domain scored below the no-rewriting baseline. A rule gate rather than a model
gate is what makes it affordable. **[CHANGED]** In the office build it is not affordable at all: the
reply path is single-digit tokens per second, so a 150-token rewrite is 15 to 30 seconds of dead air
before retrieval starts. The glossary is corpus-mined because a model-generated expansion is a guess
that retrieves the wrong concept with the query's authority behind it. The filter rule exists because a
dropped filter retrieves confidently from a superseded document, cites a real page, and is invisible in
aggregate metrics because the answer is well-formed. The entailment-against-the-original rule stops a
drifting rewrite laundering itself through the verifier.

**Rejected:** HyDE as a default, whose original advantage came from substituting document-to-document
similarity for relevance because the retriever was an unsupervised Contriever, which ours is not;
across eleven expansion techniques, twelve datasets and twenty-four models, expansion helps weak
retrievers and generally harms strong ones; it underperformed vanilla dense on every metric on a
23,088-query financial benchmark; and in production it cost 2.6x latency with losses exceeding wins.
Multi-query expansion, 0.350 against a no-rewriting baseline of 0.371. Learned routing, which failed
in every paradigm tested. Decomposition by default, kept explicitly triggered and hard-capped. Any
unbounded agentic loop: 5.0 calls per query, 1.75x context resent, roughly eightfold output tokens.

**Evidence: measured.**

### Stage 8. Answer, verify, refuse
**Choice:** **[CHANGED] Five layers, in this build order.** LAYER 0, mechanical, deterministic, no
model, ships always: the supersession check and the OCR-confidence gate from 2.5, plus the
literal-identifier assertion. LAYER 1, mechanical, deterministic, no model, ships always: cited index
exists in the retrieved set; cited page falls within the chunk's page range with the straddle resolved
by charspan; quoted spans present verbatim after normalisation; every number in the claim present in
the cited passage. LAYER 2, entailment with `lytang/MiniCheck-Flan-T5-Large` (MIT, 770M) in its own
container, chunking the document and taking the max support probability, because TEI cannot serve a
T5 encoder-decoder. LAYER 3, claim decomposition only on a structural trigger, more than one numeric
token or more than about 40 words, never for display. LAYER 4, a generative judge restricted to the
borderline band, on a DIFFERENT model from the answerer, obligated to produce a supporting span that
Layer 1 then checks. Refuse per CLAIM not per answer, with the five named reasons kept distinct and
each carrying its retrieval trace. Threshold from split conformal prediction over the verifier's
support score, calibrated on the answerable calibration split only, with the threshold, the
calibration-set hash and the granularity recorded in the audit record.

**Why:** Not one commercial system verifies citations at the sentence level. Glean surfaces deep links
and hedges coverage; Azure returns verbatim captions with no generative model in the path; Bedrock's
contextual grounding check explicitly does not support conversational QA, marks the whole response
relevant if any ONE chunk is relevant, and on streaming APIs shows the user the bad answer before
flagging it. That is the competitive bar and it is low. Measured on our own hardware, a general NLI
cross-encoder is at chance as a verifier: 49.4 and 52.6 balanced accuracy with false-alarm rates of
90.0 and 88.6 percent, so nine in ten correct sentences get flagged amber. Windowing the premise to 900
characters halved latency and moved balanced accuracy by half a point, so the failure is not
truncation. Verification is compute-bound and does not batch: 1,583 ms per pair at batch 1, 1,772 at
batch 8, 1,636 at batch 16, all single-thread, so eight claims on eight cores is about 13 seconds at
the small size and about 50 seconds at 770M. The consequence is architectural: stream the answer and
verify per sentence across cores as sentences complete.

**[CHANGED] The ceiling, stated in the defensible form.** MiniCheck missed 40 percent of unsupported
sentences on 24 scored pairs, which is a direction and not a number. Vectara's published table implies
missed-hallucination rates of 68.1 percent on RAGTruth-Summ and 45.6 percent on RAGTruth-QA, and the
source prefers that figure. **And MiniCheck's false-alarm rate is 50.0 percent at its default
threshold**, so half of correct claims flag amber. One small classifier is a signal, not a verdict, and
both of its error rates go in the acceptance test.

**[CHANGED] The conformal guarantee, stated in the defensible form.** Split conformal gives a
distribution-free finite-sample guarantee on the participation rate. It does not give a fine-grained
threshold at n=40, because attainable coverage levels sit on a grid of 1/(n+1) and the [1-alpha,
1-alpha + 1/(n+1)) interval at alpha=0.10 spans roughly 2.4 points. State the interval, the
granularity, and the fact that the guarantee covers participation and not support.

**Rejected:** **[ADDED]** any four-layer design that stops at "does the answer match the passage",
because it certifies a misread scan and a superseded schedule with equal confidence. A general NLI
model as the verifier, measured at chance with a 90 percent false-alarm rate. Any single small
classifier as the gate, given missed-hallucination rates of 31 to 68 percent. Reusing the cross-encoder
reranker as the verifier, which is trained on query-passage relevance rather than premise-hypothesis
entailment. Self-verification by the answering model. Pairwise LLM judging, which imports position
bias. Bespoke-MiniCheck-7B, unpriced and on request. HHEM-2.1 non-Open, cloud-only and so disqualified
for the office build regardless of quality. Bedrock's contextual grounding check as the verifier.
Semantic entropy or repeated sampling as the abstention signal. Claim decomposition for every
sentence. A reranker score as the refusal threshold.

**Evidence: measured, with the miss rate downgraded to directional.**

### Stage 9. Evaluate
**Choice:** 60 golden questions per client: 40 general and 20 client-specific, 40 answerable and 20
deliberately not, split for conformal calibration and for reporting, fixed before the first run and
hashed into the manifest. Stratified: 20 single-lookup factual, 10 conversational follow-ups, 10
identifier/code/acronym, 8 date-or-version-constrained, 7 must-refuse, 5 table-cell or page-straddle.
Hand-written, never auto-generated for the acceptance set. Paired per-question comparison reported as a
contingency table, McNemar on the discordant pairs, exactly ONE pre-registered primary comparison per
release, Wilson intervals always, and the five release-blocking results kept binary and absolute.
**[CHANGED]** The calibration split contains only questions that can produce a verifier support score.
The must-refuse and absent-document questions are counted as retrieval mechanism checks, separately and
by hand. **[CHANGED]** `multilingual-e5-large` runs alongside bge-m3 from the first execution and its
contingency table is recorded.

**Why:** The arithmetic is unforgiving and it changes what the set is FOR. Wilson intervals at n=60 are
18 to 23 points wide in the range that matters, so the difference between an 85 percent threshold and a
92 percent threshold cannot be resolved, and the 92 percent gate sits inside the noise band. The
McNemar minimum detectable difference at 80 percent power is 10 points at 10 percent discordance rising
to 20.4 at 30 percent. Uncorrected, the chance of at least one false win is 22.6 percent for five
variants and 64.2 percent for twenty, and Bonferroni correction takes power at a true 10-point
improvement from 0.40 to 0.13 with five variants, so a ten-variant bake-off cannot work. Roughly 85
percent of a query set can be inert for the question being asked. Many IR benchmarks carry a lexical
bias because lexical systems built them, which our own hand-written set will inherit unless paraphrased
and conceptual questions are included deliberately. So the set is a regression detector and a mechanism
checker, not a ranking instrument. The three things it CAN answer properly are the ceiling diagnostic,
the contextualisation ablation, and the reranker decision. Hard fails stay binary because a single
invented answer or numeric mismatch is decisive at any sample size.

**Rejected:** Tuning fusion parameters on 60 questions. A multi-variant bake-off. Auto-generated golden
questions for the acceptance set. Reporting a single aggregate percentage instead of the paired
contingency table. Reporting a percentage from 60 questions without n and without the interval.
Reporting a judge-assisted number without the judge's measured false-negative rate beside it. Reusing
the calibration set after a model, parser, chunking or embedding change. **[ADDED]** Counting
must-refuse questions as calibration points.

**Evidence: measured, and I re-derived the Wilson and McNemar arithmetic independently.**

---

## 5. Expected quality

**What we can defend with a number.** On structured, digitally-native documents this architecture
should be strong, and the strongest single claim is the mechanical layer, which now covers fabricated
citations, page drift, missing quotes, numeric substitution, identifier substitution, superseded
sources and low-confidence scans. The 92 percent retrieval and 85 percent support gates are measurable
but not provable at n=60: the interval around 55/60 is 0.80 to 0.95, so we will be able to say we
passed the gate and not that we are at 92 percent. Say it that way to a client.

**Where the ceiling sits.** In the parser, and in what the parser cannot see. A fee schedule whose OCR
misreads a digit produces a wrong number, and Layer 0's confidence gate is what converts it to a
refusal rather than a certified answer. That mitigation depends on Docling exposing a usable per-page
confidence score, which is unverified and is the first thing to check in Wave 4.

**The verifier's own ceiling, which must be published.** A purpose-built fact-checker missed 40 percent
of unsupported sentences on 24 scored pairs, and Vectara's published table implies 31 to 68 percent
missed-hallucination rates for this class. Its false-alarm rate is 50 percent. So the architecture is
not "an entailment model decides". It is a stack of cheap independent checks, a refusal rule and a
published number. The expected quality is: unsupported claims the mechanical layer catches become
refusals; unsupported claims only the entailment layer would catch will sometimes escape, at a rate we
measure and disclose. A refusal threshold tuned on a verifier with a high miss rate produces confident
wrong answers with a certificate attached, which is worse than no verification because a client will
believe it.

**Office build specifically.** Same architecture, every generated context step becomes an offline
batch job, and the query pipeline runs strictly smaller than the cloud build's. The retrieval layer
ranges from RRF-only to a full reranked hybrid. **[CHANGED] That divergence is a stated configuration
boundary, not an accident:** the parity contract covers the interfaces, the trace format, the citation
verifier, Layer 0 and the ACL rules. It does not cover candidate depth, reranking, or query-side
rewriting, and the office build's answers are not expected to be identical.

**Where the evidence is weakest.** Reranker benefit on this corpus, because the sources conflict and
none of the tested models was trained on contracts or invoices. Contextualisation gain from a local
model, because nobody has published that comparison. CPU ingestion throughput, because every figure is
arithmetic. Cross-encoder latency on the target instance while idle, because our measurement was taken
at load average 28. And the answering model's citation discipline behind a strict verifier, because
nobody has measured refusal rate with a small local answerer.

---

## 6. Licence status

**Shippable as-is, no legal conversation.** PostgreSQL and pg_trgm (PostgreSQL License). Qdrant
(Apache-2.0). Hugging Face TEI (Apache-2.0). Docling, docling-core, docling-ibm-models, docling-parse
(MIT). Tesseract, RapidOCR, PaddleOCR, EasyOCR (Apache-2.0). OCRmyPDF (MPL-2.0). pdfplumber (MIT).
pypdf (BSD-3-Clause style). `BAAI/bge-m3` (MIT). `BAAI/bge-reranker-v2-m3` (Apache-2.0).
`lytang/MiniCheck-Flan-T5-Large` (MIT; base `flan-t5-large` is Apache-2.0). `Qwen/Qwen3-4B` (Apache-2.0,
LICENSE read from the repository root). `microsoft/Phi-4-mini-instruct` (MIT).
`Alibaba-NLP/gte-reranker-modernbert-base` (Apache-2.0). `tasksource/ModernBERT-base-nli`
(Apache-2.0). Tiger Data pg_textsearch (PostgreSQL License).

**Do not ship.** ParadeDB pg_search Community, AGPL-3.0 only, verified three ways, commercial licence
unpriced, so it fails the available-and-priced test. `tensorchord/vchord_bm25`, dual AGPL-3.0 or
Elastic License v2. Elasticsearch, triple-licensed AGPL-3.0-only / SSPL v1 / ELv2. Typesense (GPL-3.0).
PyMuPDF and pymupdf4llm, AGPL-3.0 with no published commercial price. MinerU, Apache-2.0 wrapped in
bespoke terms. Onyx `ee/` directories under the Onyx Enterprise License, used as reference
architecture only. All Jina rerankers, ColBERT models and v3/v4/v5 embeddings, CC-BY-NC-4.0 or the
Qwen Research License, no published price, with `jina.ai/COMMERCIAL-LICENSE-TERMS.pdf` returning 404
when fetched. `lightonai/Reason-ModernColBERT`, CC-BY-NC-4.0 despite its Apache-2.0 base.
`nvidia/llama-embed-nemotron-8b`, non-commercial. `google/embeddinggemma-300m`, Gemma terms plus a
manual gate. `bespokelabs/Bespoke-MiniCheck-7B`, unpriced on request. `Qwen/Qwen2.5-3B-Instruct`,
research licence. `mistralai/Ministral-8B-Instruct-2410`, research licence. `google/gemma-3-4b-it` and
`meta-llama/Llama-3.2-3B-Instruct`, not OSI licences, gated, with use restrictions.
`Xenova/ms-marco-MiniLM-L-6-v2`, no licence field at all.

**Added to the do-not-ship list by this review.** **[NEW]** Meilisearch. Top-level LICENSE,
fetched today from the repository, carries `SPDX-License-Identifier: MIT AND BUSL-1.1`, with the engine
split between `LICENSE-MIT` and `LICENSE-EE` under the Business Source License 1.1. A BUSL component
carries a production-use restriction and `AND` means both obligation sets apply. I read the top-level
LICENSE only. Before this becomes a permanent verdict, read `LICENSE-EE` in full and determine whether
the default container starts any EE component. Until then it is out, and it is out on a stronger basis
than the synthesis's one-clause dismissal.

**Must be verified before we commit, in this order.**

1. **Docling per-page confidence.** Layer 0's OCR gate depends on it. Confirm the field exists and is
   populated in the version we pin, from Docling's own release notes or source. Unverified.
2. **The office answering model's licence artifact.** `Qwen/Qwen3-4B` and `microsoft/Phi-4-mini-instruct`
   are declared permissive, but almost no model repository in this stack contains a LICENSE file.
   BAAI/bge-m3, BAAI/bge-reranker-v2-m3, Qwen3-Reranker, gte-reranker-modernbert-base, colbertv2.0,
   jina-colbert-v2 and MiniCheck all declare their licence ONLY as a README frontmatter field. At
   weight-freeze time, archive the model card, the frontmatter and the commit hash for every model we
   ship, so counsel has a document rather than a metadata string.
3. **`microsoft/harrier-oss-v1-0.6b`** declares `license: mit` in the card and has NO LICENSE file. It
   is a challenger and blocks nothing, but confirm with Microsoft before shipping it in a paid build.
4. **Qdrant v1.19.2 DBSF plus metadata boost.** The rejection reason was a v1.11.3 fact. Check on the
   actual pin before DBSF is discarded permanently.
5. **ParadeDB self-hosted price at our scale.** The only option here blocked on a number rather than a
   fact, and the best-engineered engine tested. Get it before the next architecture review.
6. **`nvidia/llama-nemotron-rerank-1b-v2`** declares `license: other / openmdw-1.1`. OpenMDW is a Linux
   Foundation model-weights licence and its text was not read. Nothing asserted either way.
7. **n8n's Sustainable Use License**, still open from the build brief: it does not clearly permit
   deploying n8n into a client's building as part of a paid build. Get it in writing or replace it.
8. **The generated-column staleness trap** in the identifier column. A stale generated column produces
   zero errors and zero results. Test it explicitly.

**Structural risk to carry forward.** The late-interaction and listwise-reranking frontier is going
non-commercial. The shippable set in that family is real but static and is not where the field
publishes. Do not architect around a model whose licence we would have to negotiate. The general test
for any future component: a licence we cannot see the price of is not a licence we can ship on.

---

## 7. Build order

**WAVE 0, measurements that gate decisions before retrieval code.**
1. Run `bench_tei2.py` against the reference instance with nothing else running, at 25/50/100
   candidates. Fifteen minutes, and it replaces a section of the reranking report with a real number.
   Also run it once while an ingest job runs, because that is the production condition.
2. Confirm whether the pinned TEI serves a CPU reranker at all, and fix the image regardless: the CPU
   tag is `cpu-1.9`, not the bare `1.9` which is the Ampere CUDA image.
3. **[ADDED]** Sample 200 pages from the client's own share and time Docling end to end. Produces pages
   per hour and closes the largest schedule commitment in the product.
4. **[ADDED]** Verify the Docling per-page confidence field before Layer 0 depends on it.
5. Run `llama-bench` on the reference instance for prompt-processing and generation throughput, and
   verify prefix KV reuse across sequential requests with a shared prefix. The local contextualisation
   cost case rests on both numbers, and so does the office build's answering model decision.
6. Mine the acronym glossary from the fixture corpus and measure the yield.

**WAVE 1, the spine.**
7. Docling ingestion with page, table, bbox and confidence provenance. Hard gate: do not proceed until
   a scanned, multi-column fee schedule reads back with the right numbers on the right page.
   RET-PARSE-001.
8. Page-bounded structure-aware chunking at 384-512 tokens with tables whole, per-row entries, page
   ranges, coord_origin, is_ocr, and stable uuid5 chunk IDs. RET-PARSE-001.
9. The golden set, written with the client, stratified, 20 unanswerable, **[CHANGED]** with the
   calibration split containing only score-producing questions. Hashed into the manifest.
10. The embedding contract as a Postgres row, plus the collection-generation scheme and the read-path
    refusal on mismatch.
11. Dense retrieval: TEI `cpu-1.9` with bge-m3, Qdrant with M=32 and EF_CONSTRUCTION=256, the
    is_tenant ACL index, is_current, and hnsw_ef set to a recall target. RET-VERSION-001,
    RET-DUPE-001.

**WAVE 2, the lexical arm, same wave as dense.**
12. tsvector with english for prose and simple for identifiers, setweight, GIN indexes, and ts_rank
    with an EXPLICIT normalization argument; never ts_rank_cd and never the default normalization.
13. The regex-extracted identifier column, whole plus separated components, populated at ingestion,
    with the generated-column staleness test. RET-HYBRID-001.
14. **[ADDED]** The per-query rank log: per arm and per fused position, the chunk id, raw score, arm
    rank, identifier-column hit flag and fused rank, written to the audit row. This is what makes the
    lexical decision defensible after the misquote in 2.2.
15. pg_trgm installed, reached through `word_similarity` and `strict_word_similarity` over a candidate
    set, never through `%` at first pass.

**WAVE 3, fusion and access control.**
16. RRF in our own code at k=60 with equal weights and the deterministic tie-break, arithmetic verified
    against the Qdrant client implementation, candidate cap on the lexical arm from measured latency.
17. ACL as a query-time filter constructed SERVER-SIDE FROM THE AUTHENTICATED SESSION, with any
    caller-supplied scope re-validated and rejected with a recorded denial. Ship the regression test
    BEFORE the feature. SEC-ACL-001.
18. Measure filter recall explicitly for the admin path's AND-over-two-broad-values predicate, the
    exact shape Qdrant measured collapsing to 39.7 percent.

**WAVE 4, verification. This is the product and it ships before the reranker.**
19. **[ADDED]** LAYER 0: the supersession registry with `is_current`, the OCR-confidence gate, and the
    literal identifier assertion with its false-refusal rate measured on the golden set. The
    false-refusal risk is real: an OCR error in a scanned invoice number fails the assertion on a
    correct answer, so measure it before making it blocking.
20. LAYER 1, the remaining mechanical checks, plus four unit tests against known-bad answers that must
    fail closed. RET-CITE-001.
21. LAYER 2, MiniCheck in its own container, with the `.bin`-loading workaround handled deliberately:
    the repository ships `pytorch_model.bin` only and Transformers 4.57 refuses to load it below torch
    2.6 for a known vulnerability. **[CHANGED]** Convert to safetensors and record the converted hash
    in the same manifest as every other pinned artifact. This is a security decision, not a build
    workaround, because the code runs inside client buildings on client documents.
22. LAYER 3 decomposition triggers and LAYER 4 the borderline judge on a DIFFERENT model from the
    answerer.
23. Refusal as five named reasons with traces, refusal per claim, amber marking, streaming verification
    across cores, and a bounded verification queue with a per-user concurrency cap and a fail-closed
    timeout that returns "I could not verify this, here are the sources" rather than hanging or showing
    an unverified answer.
24. **[CHANGED]** Conformal calibration on the answerable calibration split only, with the threshold,
    the calibration-set hash, the exact interval [1-alpha, 1-alpha + 1/(n+1)) and the granularity
    recorded in the audit record. RET-CITE-001.

**WAVE 5, experiments that must earn their place.**
25. The ceiling diagnostic first, then the reranker comparison, then the reranker decision, with
    `gte-reranker-modernbert-base` as the named fallback inside the same gate.
26. The contextualisation ablation, four runs.
27. **[CHANGED]** `multilingual-e5-large` alongside bge-m3 on the same golden questions, from the first
    execution rather than deferred.
28. Fielded search as a third arm over Docling's heading text, which comes free with the parser and has
    no measured evidence either way.
29. The office build's answering model, measured on the golden set end to end with refusal rate split
    by reason and support rate beside it.

**WAVE 6, contingencies with named triggers.**
30. pg_textsearch when the rank log shows ts_rank losing; trigger is the rank log, not a calendar.
31. `answerai-colbert-small-v1` as a late-interaction rerank stage, office build only; trigger is
    retrieval recall below target with SEMANTIC rather than lexical misses.
32. The spatial infrastructure the architecture omits: UPS, full-disk encryption with a resolved
    unattended-boot story, restart policies on every container, a boot self-test, a restore drill in
    the acceptance test, and a cold spare or a documented same-day replacement.
33. The support channel: client-initiated time-boxed reverse tunnel with an audit row, an offline
    triage bundle, and a four-state health page readable by a non-technical person. The build brief
    says SSM only and the architecture document says WireGuard or Tailscale, and the office build has
    no runtime internet, so those cannot both be true. Resolve it in the decision document.

**The ordering principle.** Recall is decided before ranking, and correctness of the citation is
decided before either. That is why the parser is step 7 and not step 15, why the mechanical verifier at
step 19 ships before the reranker at step 25, and why the five release-blocking binary results outrank
every average in the acceptance test. If the schedule compresses, cut Wave 5 and Wave 6 entirely. Do
not cut Wave 4, and do not reorder Waves 1 through 3.

---

## 8. Open questions

1. **Six conflicts between the researchers were resolved rather than averaged**, and one of those
   resolutions was wrong. The bge-m3 sparse-head versus Postgres full-text conflict was decided by
   verifying from TEI's source that TEI serves only the dense head, which holds. The BM25 versus
   ts_rank conflict was decided by a misquote, and it is now decided by the rank log instead.
2. **Two defects in the current plan that survive.** The TEI image tag must be `cpu-1.9`, not the bare
   `1.9`. The Qdrant fusion claim in the build brief is wrong for a different reason than the
   synthesis gave: not because the pin is old, but because fusion belongs in our code for citation
   determinism. Correct the brief to say that.
3. **The highest-value item is still the cheapest.** The literal identifier assertion combined with the
   identifier column converts the worst failure mode in the product into a refusal, needs no new
   dependency, no model and no licence conversation, and is on the order of 150 lines. It has a
   measured false-refusal risk on OCR errors, so measure that on the golden set before making it
   blocking.
4. **The uncomfortable number to put in front of a client.** Our chosen embedding model is not the
   strongest zero-shot retriever: 0.437 average nDCG@10 on BEIR against 0.546 for
   multilingual-e5-large, losing on all four subsets. The decision rests on prefix-discipline risk and
   on the embedding contract making the swap cheap, and the challenger now runs from the first golden
   execution. Say the number rather than let a procurement review find it.
5. **The verifier's ceiling drives the commercial claim, and it is stated in two parts.** Split
   conformal guarantees the participation rate, with the granularity caveat in 2.4. It cannot guarantee
   that what is said is supported, because the verifier's false-negative rate sets that bound. Both
   numbers go in the acceptance test.
6. **The office build's scope decision is the one open product question.** It is a GPU build, a
   retrieval-only product, or a full-generation product with a published wall-clock expectation. The
   retrieval architecture is the same in all three.
7. **Sixteen items remain unverified**, each with a measurement attached rather than left as an
   assumption. The three that most change what we can promise: CPU throughput for both embedding and
   generation on the reference instance, whether a small local model generates GOOD chunk context, and
   the office build's tokens-per-second figure, which our own production-readiness document labels
   typical rather than measured.
