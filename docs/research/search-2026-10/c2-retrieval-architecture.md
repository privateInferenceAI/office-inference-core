# C2 retrieval architecture: ingest to verified answer

Synthesis of nine research angles into one decision document for Office Inference C2.

Written 2026-10-11. Every claim carries an evidence label: **measured** (a number produced on our
machines or read out of a primary artifact, with the source), **production practice** (what a shipped
system actually runs, read from its source or its own documentation), **vendor claim**, or
**inference**. Where two researchers disagree I say so and name the evidence that decided it.

Sources are the nine reports at `/tmp/search-research/01..09`. Their filenames are cited as
`01-lexical`, `02-embedding`, and so on.

---

## 0. The answer

Seven stages. The short version is that we keep the pipeline the build brief already describes, but we
change three things about it, and we cut two things out of it.

**Changed:**

1. **The lexical arm moves into Postgres native full-text search**, with a dedicated identifier column
   and an explicit normalisation argument on `ts_rank`. We do not add a BM25 extension now and we do
   not add a Lucene sidecar ever. `01-lexical`
2. **Chunks are page-bounded.** The page boundary wins over the heading boundary, and every chunk
   stores a page *range*, never a page number. `07-chunking`
3. **The reranker is demoted from component to experiment.** It sits behind a flag until a latency
   measurement on the real instance and a golden-set comparison say it earns its CPU. `04-fusion`,
   `05-reranking`, `09-products`

**Cut:**

1. **Query-side generation, except a rule-gated rewrite.** No HyDE, no multi-query, no decomposition by
   default, no learned routing. `06-query-side`, `04-fusion`
2. **Late interaction.** It ties the cross-encoder on quality and wins massively on latency, but it
   needs a third serving component and nothing serves our MIT-licensed model. `03-late`

**Kept, and moved earlier than the build brief has it:** the citation verifier. The mechanical layer is
deterministic, has no model and no licence question, is on the order of 150 lines, and it converts our
worst failure mode into a refusal. It ships before the reranker. `08-verification`, `01-lexical`

**One greenfield correction.** The build brief says "Qdrant prefetch plus reciprocal rank fusion". On
the pinned Qdrant v1.11.3 that is not executable: native RRF is hardcoded at `k=2`, configurable `k`
arrived in v1.16.0 and weights in v1.17.0. `04-fusion` measured this from the release history. Fusion
therefore happens in our own code, which also gets us the deterministic tie-break that stops citations
moving between identical runs.

---

## 1. Stage-by-stage architecture

### Stage 1. Ingest and parse

**Choice.** Docling, in its own container, on both builds. Its layout, table-recognition and OCR models
run on CPU, which is why the reference instance is 16 vCPU.

**Why.** This is the stage that decides whether the product can be right, and it is where a competitor
cannot easily follow. Everything downstream is arithmetic over what the parser produced. A fee schedule
read as a token stream produces a confidently cited wrong number, and no reranker or verifier recovers
it. A verifier is worse than useless here: it will faithfully confirm a claim against a mangled source.
`08-verification` says this explicitly, and `09-products` reaches the same conclusion from the market
side, that our differentiator is an ingestion problem.

What Docling gives us, verified from its source rather than its documentation: `ProvenanceItem` carries
`page_no`, a `BoundingBox` and a `charspan` on every `NodeItem`; `TableCell` carries `row_span`,
`col_span`, start and end row and column offsets, plus explicit `column_header`, `row_header`,
`row_section` and `fillable` flags; `BoundingBox` records `coord_origin` as `TOPLEFT` or `BOTTOMLEFT`.
**measured, source read** (`07-chunking` section 3.1). The `coord_origin` field is a trap worth naming:
PDF space is historically bottom-left and getting it wrong flips every highlight vertically.

**Rejected.** PyMuPDF and `pymupdf4llm` are AGPL-3.0 with a commercial licence from Artifex at no
published price, so they cannot be costed, and this is the easiest licence mistake to make in a Python
PDF stack. **licence verified from the GitHub licence API.** MinerU is Apache-2.0 but wrapped in
additional terms including an attribution obligation and a revenue threshold; the thresholds do not
bind us but a bespoke clause is legal review we do not need. Unstructured is Apache-2.0 and its paper
is the best measured evidence on structural chunking, but it is a second heavy dependency beside
Docling for no measured gain. `marker` and `surya` re-read as plain Apache-2.0 and are usable, but they
are a second parser rather than a better one. `07-chunking` section 6.

**Basis.** Production practice plus licence, with the gate being a measurement: do not proceed until a
scanned, multi-column fee schedule reads back with the right numbers on the right page. That is
`RET-PARSE-001`.

---

### Stage 2. Chunk

**Choice.** Page-bounded, structure-aware chunking. Target 384 to 512 tokens. Zero to 32 tokens of
overlap, and prefer zero. Never split a table that fits. Every chunk stores `page_start` and
`page_end`, the bounding boxes on the first and last page, the `charspan`, and `is_ocr`.

**Why.** Three measured results, and they point the same way.

- Page-level chunking measured the highest average end-to-end accuracy (0.648) *and* the lowest
  standard deviation (0.107) across five datasets, against 0.603 to 0.645 for token-based approaches.
  The accuracy gap is inside the noise; the variance result is the real finding. **vendor-run
  measurement with disclosed method** (NVIDIA, via `07-chunking` section 3.3). For a product sold on
  citation accuracy, low variance matters more than a fraction of a point.
- 128-token chunks underperform across three independent studies, and the largest controlled chunking
  study found the top 1 percent of 1,820 configurations at 384 to 512 tokens. **measured**
  (`07-chunking` section 1.7).
- Overlap is "generally neutral to negative", and every top-1-percent configuration used at most 32
  tokens. Overlap also makes two chunks claim the same sentence, which looks like corroboration in a
  citation panel and is not. **measured** (`07-chunking` section 1.2).

The page boundary winning over the heading boundary is an inference, and I want to be honest that it is
one. NVIDIA measured page-level beating section-level *using the same parser for both*, which is
counterintuitive on the merits. The synthesis is: chunk on structure, cut at the page break, repeat the
heading path on both pieces. You get the structure, an unambiguous page for every chunk, and the
low-variance behaviour. **inference**, well-supported.

**The multipage table exception, stated as a build decision.** A table that spans a page break stays
one retrievable unit, because splitting it destroys the row-to-header relationship. It records a page
range and cites a page range. The verifier is told that a ranged citation from a table is expected
rather than a defect. Its rows are excluded from the surrounding page chunks so the same number cannot
be retrieved twice with two different page attributions.

**Table serialisation.** Keep the structured `TableData` as the canonical record in Postgres, and store
a row-wise serialisation for embedding in which each row is a self-contained statement with the header
names folded in, preceded by the caption and heading path. This is measured: a free deterministic
prefix plus the table caption beat GPT-4-generated keywords and GPT-4-generated summaries on page
accuracy (67.38 against 46.10 and 62.41) and on answer accuracy. **measured**, 141 questions
(`07-chunking` section 3.2). Including a table's own title and section in the serialisation raised
average recall@3 by 0.42 on FeTaQA. **measured** (Gomm & Hulsebos, via `07-chunking`).

One open item, flagged rather than assumed: the optimum serialisation is embedding-model dependent, and
the source did not test `bge-m3`. Test it against our own model. `07-chunking` open question 4.

**Rejected.** Semantic chunking: the best-controlled study concludes the cost "is not justified by
consistent performance gains" and that fixed-size "often performed better" on real-world documents,
winning 3 of 5 evidence-retrieval datasets. **measured** (arXiv 2410.13070). It also needs
corpus-global statistics, which Chroma's own documentation admits means chunks "must be re-computed as
data is added", and that breaks incremental ingestion and stable chunk IDs. `ClusterSemanticChunker`
and `LLMSemanticChunker` are rejected for the same reason. Summary indexing and RAPTOR: Anthropic
"evaluated and saw low performance", and Unstructured measured a generated summary as *worse* than a
free prefix on both retrieval and answer accuracy. High overlap (10 to 20 percent) is rejected because
the evidence does not support it and because it makes citations ambiguous. `07-chunking` section 5.

**On small-to-big.** The pattern is right in principle: it decouples the citation unit from the
generation unit, and the window can change at query time without re-embedding. But I am not adopting it
as a stage, for a reason that overrides it. The reports offer three strategies that all reduce chunk
size, and they are not all compatible: small-to-big wants small units plus a parent, the 1,820-
configuration study wants 384 to 512 tokens, and NVIDIA wants page-level. If our chunks are pages with
a 384-to-512-token split inside long pages, the citation unit is already definite and a parent pointer
adds index growth, context-budget risk and citation ambiguity when the parent spans pages, for a
mechanism gain that nobody has published a number for. The citation unit is the page chunk. If the
golden set later shows answers failing for want of surrounding context, the escape hatch is to store a
`prev_chunk_id` and `next_chunk_id` at ingest and expand at read time, which needs no re-embedding.
**inference, explicitly a judgement against a sibling report's recommendation.**

**Basis.** Measured for size, overlap and table handling. Inference for the page-boundary synthesis and
for declining small-to-big.

---

### Stage 3. Contextualise

**Choice.** Do the deterministic half unconditionally on both builds: prepend the document title, the
full heading path, the page label, and the table caption before embedding. Then run an ablation to
decide whether a generated context adds anything on top.

**Why.** The deterministic half is free, it is measured to help, and Docling already implements it.
`DocMeta.headings` is included in the chunk text while `doc_items` and `origin` are in
`excluded_embed`, which is exactly the "governance out of the embedding" principle implemented by the
library authors. **measured, source read** (`07-chunking` section 4.8).

The generated half is a genuine open question and should stay one. Anthropic's measured result is
strong: contextual embeddings cut top-20 retrieval failure 35 percent, plus contextual BM25 49 percent,
plus reranking 67 percent. **vendor-run measurement with disclosed method.** But their own ablations
show the gain comes from chunk-*specific* disambiguation rather than from added words, since generic
summaries gave "very limited gains". And nobody has published a comparison of *small-model*-generated
chunk context against deterministic heading-path context. `07-chunking` calls this the largest open
question in its report and I agree with that assessment.

The cost is what makes it a measurement rather than a default. It is generation, not prompt reading,
and generation is the worst-performing operation on a CPU box: 50,000 chunks at 100 generated tokens is
5 million output tokens, which is 139 hours at 10 tokens per second. **parameterised inference, CPU
throughput unverified and to be measured with `llama-bench` before committing.**

**So:** cloud build does it on Bedrock, where prompt caching makes it cheap, at ingest. Office build
treats it as an offline batch job on rented hardware, never inline, using Qwen3-4B (Apache-2.0,
ungated) as the local contextualiser. If the ablation shows the deterministic half captures most of the
gain, we have spent nothing and learned the answer.

One consequence to state out loud: because this is baked into the embedding, changing it requires
re-embedding. That is why it is the one pre-embedding decision that deserves the ablation.

**Rejected.** Voyage's contextualized chunk embeddings, a hosted API, disqualified for the office build
and an unnecessary dependency in the cloud build. Summary-based indexing and generic document
summaries, per Anthropic's own ablations. Late chunking: the method is sound and `bge-m3` has the 8,192
token context for it, but it needs token-level embeddings before pooling and TEI returns pooled
vectors. Deferred pending a serving-layer change, not rejected on merit. `07-chunking` section 2.4.

**Basis.** Measured for the deterministic half via the table-metadata result. Vendor claim plus
explicitly-open question for the generated half.

---

### Stage 4. Store, embed, and govern

**Dense model.** `BAAI/bge-m3`, 1024 dimensions, CLS pooling, no prefix, served by TEI.

The honest framing: **this is not the strongest zero-shot retriever.** On BEIR it scores 0.437 average
nDCG@10 against 0.546 for `intfloat/multilingual-e5-large`, losing on all four subsets, and the
independent group that measured it attributes the deficit to a multi-task capacity tax from heads we
cannot even use through TEI. **measured**, Cirillo et al. arXiv 2605.23618 (`02-embedding` section 5.2).
That is the most uncomfortable number in the whole study and I am not going to bury it.

**What decided it** is that the gap does not bind, and the alternative carries a failure mode we cannot
see:

- The measured spread between models is smaller than the spread between pipeline choices. Two
  independent sources converge on this, and one of them measured `bge-m3` *beating* an 8B model on MRR
  over a narrow corpus (0.797 against 0.750) while being nine times faster to embed. **measured**
  (`02-embedding` sections 5.2 and 5.3).
- `multilingual-e5-large` requires a mandatory `query: ` / `passage: ` prefix discipline, and a prompt
  convention moved Qwen3-Embedding-0.6B by 17.1 accuracy points in a documented case. **measured**
  (`02-embedding` section 5.1(b)). TEI has one server-wide default prompt, not a query prompt and a
  passage prompt, so setting it to help the query path silently corrupts every stored document vector.
  **verified from TEI source** (`02-embedding` section 3.2). A zero-prefix model cannot be
  misconfigured that way.
- It is already deployed, MIT, ungated, air-gap-mountable, and pinned by revision. Swapping it is a
  re-embed job rather than a migration, and we are making that true by contract.

**The evaluation plan is where this gets settled, not in a leaderboard.** Put `multilingual-e5-large`
in the golden harness as the first challenger, on the same 60 questions. If it wins by more than a
couple of points, take it and pay the prefix discipline through the contract. That is the pre-registered
comparison for the C2 release after the pipeline exists.

**Serving.** TEI `cpu-1.9`, not the pinned `1.6`. Two corrections to the current compose that are
deployment failures rather than slowdowns:

- The bare `1.9` tag is the Ampere 8.0 CUDA image. The CPU image is
  `ghcr.io/huggingface/text-embeddings-inference:cpu-1.9`. **verified** (`02-embedding` section 3.3).
- Startup is a landmine. TEI warms up over sequence-length buckets up to `max_input_length` at
  `max_batch_tokens`, which on CPU is a 16,384-token forward pass through a 568M model. Two attempts sat
  in "Warming up model" for over ten minutes. `MAX_WARMUP_SEQUENCE_LENGTH=256` and
  `MAX_WARMUP_BATCH_SIZE=4` brought startup to 20 seconds. **measured** (`05-reranking` section 3.2).
- Set `--max-batch-tokens` down from its 16384 default to 2048 to 4096 and `--max-concurrent-requests`
  down from 512 to 8 or 16, on a 16-vCPU box. **verified args** (`02-embedding` section 3.3).

**Silent truncation is a correctness bug, not a performance one.** `--auto-truncate` defaults true, so
an over-length chunk yields a valid-looking embedding of part of the text, and we would later cite a
page as supporting a sentence that was never embedded. Mitigation belongs in the ingestion worker as a
hard pre-send assertion against a ceiling well under the model limit, failing the file loudly.
`02-embedding` section 4.4.

**Vector store.** Qdrant, 1024-dim dense, cosine, `M=32`, `EF_CONSTRUCTION=256` following Onyx's
accuracy-over-memory choice. Payload indexes: a keyword index with `is_tenant: true` on the ACL field
(available from v1.11.0, so it works on our pin, and it co-locates a role's vectors for sequential
reads), plus `is_current`, `doc_id`, `doc_class`, `source_format`. **production practice, code read**
(`09-products` section 4.1) plus **vendor docs verified** (`07-chunking` section 4.3).

**Metadata schema.** Three layers, and the governing rule is that **metadata lives in the payload and
in Postgres, and never enters the embedding input.** If a chunk's text contains its access label or its
version, changing either means re-embedding the corpus.

- *Identity and citation:* `chunk_id`, `content_sha256`, `doc_id`, `doc_version`, `source_path`,
  `source_format`, `page_start`, `page_end`, `bbox_start`, `bbox_end`, `charspan_start`,
  `charspan_end`, `is_ocr`, `coord_origin`, and for spreadsheets `sheet_name` plus `cell_range`.
- *Structure and retrieval:* `heading_path`, `content_type`, `token_count`, `prev_chunk_id`,
  `next_chunk_id`, `table_id`, `is_table_header_repeat`.
- *Governance:* `acl_labels`, `doc_class`, `is_current`, `superseded_by`, `effective_date`,
  `expiry_date`, `legal_hold`, `retain_until`, `ingest_ts`, `parser_version`, `chunker_version`,
  `embedding_model`, `embedding_model_version`, `deleted_at`.

Two rules that are not negotiable. **A missing or empty `acl_labels` denies, never allows**, so the
failure mode of an ingestion bug is an unreadable document rather than a readable one. And **`is_current`
is a property of the document denormalised onto every chunk**, updated atomically via
set-payload-by-filter, because a query in flight that sees a mix of current and superseded chunks from
one document is exactly the "confidently cited old number" failure. `07-chunking` section 4.7.

**Stable chunk IDs.**
`uuid5(NAMESPACE_OI, f"{doc_id}:{doc_version}:{chunker_version}:{ordinal}:{content_sha256}")`. The
`content_sha256` term makes an unchanged chunk a no-op while a changed one gets a new ID and the old
becomes an orphan for the reconciliation pass. This is a design rather than a published convention;
`07-chunking` searched and found no vendor recommendation specifying a stable-chunk-ID algorithm.
**inference**, flagged as such.

**The embedding contract, and why it is worth more than the model choice.** Store per collection, as a
row in Postgres rather than a comment in a compose file: `model_id`, `model_revision`, `model_sha256`,
`dimension`, `pooling`, `normalize`, `query_prefix`, `passage_prefix`, `prompt_name_query`,
`prompt_name_passage`, `truncate_dim`, `max_tokens`, `chunker_version`, `parser_version`,
`schema_version`. The write path stamps it and the read path refuses on mismatch. A model change creates
a new collection generation rather than an in-place update, because Qdrant's dimension is immutable, and
re-embedding must be resumable, because at roughly 28 hours per 50,000 pages a job that cannot resume is
not a plan. `02-embedding` section 8.

The failure this prevents is specific and silent: half the corpus embedded with one model and half with
another in the same collection produces cosine similarities between unrelated vector spaces. It does not
error. It returns plausible rubbish.

**Basis.** Measured for the critique of our own model, verified-from-source for the serving corrections,
production practice for the Qdrant parameters, inference for the ID scheme and the contract.

---

### Stage 5. Retrieve

Three arms, all filtering on ACL in the query rather than after it.

#### 5a. Dense arm

Qdrant prefetch, `hnsw_ef` set to hit a recall target and no higher. Qdrant measured recall against
exact at 0.986 at `ef=16` rising to 1.000 at 256, with raising it further moving fused nDCG@10 by at
most 0.0022 while latency rose up to 49 percent. **measured** (`04-fusion` section 5.3).

#### 5b. Lexical arm: Postgres native full-text search

**This is the decision I expect to be argued with, so here is the whole case.**

The build brief's premise was that we need BM25. The lexical researcher tested that premise by
installing every candidate in a container and running the same 60-identifier head-to-head on a
200,000-chunk corpus, then isolating IDF, term-frequency saturation and length normalisation in
controlled single-property experiments. **The premise did not survive.**

- **BM25 did not beat `ts_rank` on the failure we are fixing.** Full identifiers: `ts_rank` 60/60 and
  ParadeDB BM25 60/60 hits@1 at 200k chunks; on 200 short documents, 190/200 each. When the query is a
  whole identifier it appears in one chunk out of 200,000, so IDF has no work to do and the ranking
  function is not the bottleneck. **measured.**
- **The bottleneck is tokenisation, upstream of ranking.** Postgres' default parser splits
  `INV-2024-04417` into `inv` / `-2024` / `-04417`; the hyphen migrates onto the following numeric
  token. Measured consequence: a user who types the trailing digits `04417` gets **zero hits**, because
  the indexed lexeme is `-04417`. Prefix matching fails too, since `74LS` misses `74LS00` and only
  `74LS:*` works. **measured.**

I checked the numbers the fusion report's summary gave for this comparison, because it reported both
engines at 60/60 on trailing-digit queries. That is a conflation of two tests. The lexical report's own
table reads: trailing digits, `ts_rank` 40/200 against BM25's 189/200. So `ts_rank` **is** broken on
partial identifiers, and that is the actual failure. The fix is the identifier column, not a different
ranking function.

**The configuration, all four parts of it:**

1. `tsvector` with the `simple` configuration for the identifier column so nothing is stemmed away, and
   `english` for prose. `setweight` to mark title, heading and table-row lexemes above body lexemes,
   using the default ramp `{0.1, 0.2, 0.4, 1.0}` for D/C/B/A, read from `src/backend/utils/adt/tsrank.c`.
2. `ts_rank` with an **explicit `normalization` argument**. Never the default: it returns an identical
   0.06079271 for a 5-word and a 1,205-word document, so a 400-word policy paragraph outranks a
   four-word table row that contains the answer. `normalization=1` fixes it, measured at a 4.01 ratio
   against BM25's 2.34 on the same data. **measured.**
3. **Never `ts_rank_cd`.** It is the commonly recommended function and it is the wrong one: it scales
   linearly with term frequency, 0.10000 / 0.20000 / 0.50000 / 2.00000 / 5.00000 at tf = 1/2/5/20/50,
   and returns a fifty-repetition document first. **measured.** This is a correction to common advice.
4. **A regex-extracted identifier column**, populated at ingestion, lowercased, stored both whole and as
   separated components, GIN-indexed, so `INV-2024-04417` is findable as itself and as `04417`. This is
   the fix for the actual failure and it needs no new dependency. It is a configurable pattern per
   deployment with a test, because `INV-2024-04417`, `74LS00`, `3:21-cv-01234` and `ACCT-1987-9171` are
   four different shapes and a client will have a fifth.

`pg_trgm` is installed and available, reached through `word_similarity` and **never** through `%`.
Measured at 200 short documents: `%` with `similarity()` scored 0/200 on both full and trailing-digit
queries, because a 12-character query against a 1,300-character chunk cannot reach the 0.3 threshold and
the row is not even a candidate. `word_similarity` scored 189/200 at rank 1 on trailing digits, exactly
matching BM25. But it cannot distinguish "first letter differs" from "last digit differs" (`A-1204` vs
`B-1204` and `A-1204` vs `A-1205` both score 0.5556) and it ran at 13.0 seconds median on
1,300-character chunks. So it is a recall mechanism over a small candidate set and it must never decide
which identifier is the answer. **measured.**

**Why the missing IDF does not sink this, and this is the argument that decides the whole stage.**
`ts_rank(tsvector, tsquery)` has no corpus argument, confirmed from the source, so it has no document
frequency. It returns an identical 0.060793 for a term appearing in 5,000 of 5,000 documents and in 5 of
5,000. On a mixed query the separation is brutal: documents with the rare term scored 0.00002030 against
0.00000000 without it, where BM25 scored 0.689810 against 0.005023. **measured.**

That is a real deficit, and the reason it does not bind here is that **we fuse by rank, not by score.**
RRF uses `1/(k + rank)` and discards magnitude entirely. Cormack's stated point about the method is
exactly this: it "combines ranks without regard to the arbitrary scores returned by particular ranking
methods". The 137x separation that BM25 provides is information that rank fusion throws away on purpose.
If we were fusing by normalised score, I would buy BM25 for the IDF alone.

**Rejected, at this stage.** ParadeDB `pg_search` is the best-engineered option tested and the only
engine that scored 60/60 on *partial* identifiers, because it is the only one that lets you choose the
tokenizer per column. It is **AGPL-3.0 only** for the Community edition, verified three independent
ways including the `Cargo.toml` `[workspace.package]` line, with a commercial licence that is not
priced publicly. Under our rule that is not shippable today. `tensorchord/vchord_bm25` is **dual
AGPL-3.0 or Elastic License v2**, both disqualifying, and independently its recommended BPE tokenizer
is context-dependent, which breaks substring matching outright: `tokenize('366332')` and
`tokenize('ZQX00-366332')` produce different token ids for the same six digits, giving 0 hits@1 and 10
hits@10 out of 60 on trailing digits against 60/60 for both competitors. **measured.** Elasticsearch is
triple-licensed AGPL-3.0-only / SSPL v1 / Elastic License 2.0, so no self-hosted configuration is
shippable. OpenSearch (Apache-2.0), Quickwit (Apache-2.0) and Tantivy (MIT) are all licence-clean and
all rejected on operational cost: a second datastore that can disagree with Postgres, an ingestion path
that writes to both, a reconciliation job for the second write failing, and a second backup and restore
procedure to write and test, on a 5-to-30-user deployment maintained by one person. `01-lexical`
sections 4 and 5.

Note the disagreement I am resolving here. The fusion researcher recommended starting the lexical arm as
`bge-m3`'s sparse output rather than BM25, on the grounds that it is MIT, local, and comes free in the
same embedding call. The embedding researcher found, from TEI's source, that **TEI produces only
`bge-m3`'s dense vector**, and that SPLADE pooling is restricted to Bert and Distilbert `MaskedLM`
architectures which `bge-m3` is not. So the sparse head is unreachable through the serving layer we
have. That evidence decides it and the recommendation does not survive.

**The two things to do next on this stage.** Get the ParadeDB self-hosted commercial price before the
next architecture review, because everything about that option is contingent on a number nobody has.
And put Tiger Data's `pg_textsearch` on the list for the review after: it is **PostgreSQL-licensed**,
verified from its own LICENSE file, written from scratch in C on Postgres pages so it participates in
WAL and works with `pg_dump`, and it removes the licence objection entirely. The reason to wait is
maturity, not licence: its first production release was March 2026, it is Postgres 17 and 18 only, and
it needs `shared_preload_libraries`. The identifier column makes that switch an index change rather than
a redesign, which is the point of doing the identifier work first. Its speed numbers are **vendor
claims** that were not reproduced.

**One trap to write into the ingestion code.** Updating `body` on a table with a `GENERATED ALWAYS AS
tsvector` column leaves the generated column stale: Postgres marks it, it does not recompute it. The
first version of the lexical harness scored zero everywhere because of this, silently and with no error.
The fix is a redundant `UPDATE chunks SET body = body`. The same trap applies to any generated
identifier column, and it produces zero errors and zero results. This is `RET-HYBRID-001`'s silent
failure mode. **measured.**

**Basis.** Measured, on our own hardware, with controlled single-property experiments. This is the
best-evidenced stage in the architecture.

#### 5c. Candidate depth

Retrieve 500 per arm before fusion, following Onyx's own constant and its comment that 100 was not
enough and 750 caused poor search performance. **production practice, code read** (`09-products` section
4.1). Depth is the dominant quality term and it is cheap when no reranker runs: the second arm's
prefetch cost 0.6 to 1.5 ms in Qdrant's measurement. `04-fusion` section 5.1.

The concern that this is unaffordable does not survive our scale. The vstash preprint measured that
raising the fused prefetch could *lower* the fused score, and the adaptive-depth paper found that fixed
depths selected from historical queries "did not transfer reliably", but both are about corpora and
query sets far larger than ours. At tens of thousands of chunks on one shard, near-exhaustive retrieval
is affordable and the question dissolves. The candidate budget is set by the reranker's CPU cost, not by
recall. `04-fusion` section 5.2.

**Basis.** Production practice, corroborated by measurement.

---

### Stage 6. Fuse

**Choice.** RRF in our own code, `k=60`, equal weights, with an explicit deterministic tie-break.

Formula, matching the Qdrant client library's implementation so the arithmetic is verifiable against a
reference: `1 / ((rank + 1) / weight + k − 1)` with one-based ranks. Over-fetch, sort on
`(score descending, id ascending)`, then truncate. Do not tune `k` or the weights.

**Why our own code.** Three reasons, in order. The parameters we need are not expressible on the pinned
server: native RRF in v1.11.3 is hardcoded at `k=2`, configurable `k` arrived in v1.16.0 and weights in
v1.17.0, and the pinned image is v1.11.3 (2024-08-29, current release v1.19.2). **measured** from the
release history and the vendor's own version annotations. Second, the tie problem is a citation-stability
problem and deterministic tie-breaking is a property of our code, not of the store: Qdrant measured
12.5 percent of default-RRF top-10 results tying with their neighbour on SciFact at k=2, against 2.8
percent at k=61 and zero under DBSF, and tied groups return in storage order. A citation that moves
between two identical runs is a trust problem. Third, the client library already implements RRF, so the
arithmetic is not novel.

**Why `k=60`.** Cormack's own words: "k = 60 was fixed during a pilot investigation and not altered
during subsequent validation", and "the choice was not critical". His pilot MAP ran 0.2123 at k=10 to
0.2098 at k=500, flat from 30 to 100. **measured, paper read in full.** Qdrant's sweep found the optimum
moves with the number of relevant documents per query, which is near 1 for us, and their low-relevance
datasets have completely different query shapes. So the defensible choice is the middle of the flat
region plus a tie-break, not a tuned constant on 60 questions.

**Why equal weights.** Qdrant measured that weights are absolute rather than ratios, that a weight pair
is only valid at the `k` it was tested with, and that the better retriever should not necessarily get
the higher weight: on DBPedia-entity dense scored 0.4677 against sparse's 0.3857 and the winning pair
gave *sparse* three times the weight. Equal weights won outright on two of five datasets, and the
largest weighted gain was +0.0096. **measured.** Their own guidance is that without a validated eval set
you leave weights at `(1.0, 1.0)` because "hand-tuned weights without measurement are unlikely to beat
the default reliably". One 60-question set cannot tune two parameters. Also record the foot-gun: a
weight of 0.0 does not drop an arm, it scores every document from that arm at 0.0.

**On the honest size of hybrid's benefit, because the brief asked.** Qdrant measured hybrid RRF against
the better single arm on five datasets: four wins of +0.0156 to +0.0311 nDCG@10 and one loss of −0.0039
with an interval crossing zero. Microsoft measured hybrid over vector at +3.4 on BEIR. Two of the four
wins are below what 60 questions can detect at all. **measured.** So we do not justify the lexical arm
as a general quality improvement. We justify it as insurance against the one failure mode dense
retrieval provably cannot fix, and here is the measurement that makes the case: on **keyword queries**
Microsoft measured lexical at 79.2 against dense's 11.7, with naive hybrid at 61.0, *worse than lexical
alone*; on **short queries** lexical 53.1, dense 38.8, hybrid 53.0. **vendor claim, but a shipped
product's own benchmark.** Our users type short identifier-bearing questions. That is exactly that row.

The theoretical result reinforces it independently: the number of distinct top-k subsets any
d-dimensional embedding can return is bounded by the dimension, and while 1024 dimensions puts the
critical document count at about 4 million so the bound does not bind for us at tens of thousands, real
models fail on simple entity-shaped queries *far below* the bound, scoring under 20 recall@100 on
queries of the form "who likes Apples?". **measured, paper.** That is the shape of a business question
and it is an argument for a lexical arm as insurance regardless of any benchmark.

**Rejected.** Native Qdrant RRF at `k=2`: three times lower than the lowest value any source
recommends, maximising ties. Native DBSF: tie-free and parameter-free, which is genuinely appealing,
and it beat default RRF on three of five datasets with two intervals excluding zero, but the statistics
come from a small top-k sample, one outlier can skew a query, it is not clipped to [0,1], Qdrant
themselves warn the rankings can be unstable, and on our pin it cannot be combined with a metadata
boost. Keep it as the fallback if RRF ties prove to be a real problem. Upgrading Qdrant for weighted
RRF: rejected for this phase, because the pin is a deliberate security posture in a project already
burned by an unrecorded pin, and because 60 questions cannot tune or validate the weights. Fusing in
Postgres: rejected because it would mean maintaining a second retrieval system for a 20-line ranking
step, and because the lexical arm does not need pgvector beside Qdrant. Naive linear combination of raw
scores: dense scores are bounded and BM25 scores are unbounded and both shift per query, so a fixed
alpha is dominated by whichever arm has larger magnitudes on the day; Qdrant and Weaviate both say so
explicitly. Plain concatenation: parameter-free and score-scale agnostic, and kept as the **control
condition** in the golden-set harness so we can see whether fusion earns its place, but not shipped,
because it makes the reranker mandatory and the reranker is the weakest link in the evidence.
`04-fusion` section 11.

**One extra arm to try, and it comes free with Docling.** Qdrant does not support BM25F natively. But
Docling already gives us a real field set: heading path, content type, page. A separate lexical arm over
heading text, fused as a third arm with the same RRF, means a question naming "Section 7 — Reimbursement"
gets a large signal from a heading match. No measured evidence exists on whether fielded search beats
flat search for business documents, so this is an experiment against the golden set rather than a
specification. `04-fusion` section 8.2.

**Basis.** Measured for the parameters, production practice for the depth, inference for the placement.

---

### Stage 7. Route and rewrite the query, minimally

**Choice.** Four things, and nothing else.

1. **Rule-gated rewrite for conversational follow-ups**, temperature 0.0, skipped on the first turn,
   validated before use.
2. **A corpus-mined acronym glossary**, applied deterministically. Expansions also indexed as
   document-side synonyms.
3. **Structured date and metadata extraction into filters**, with the hard rule that a filter which
   cannot be applied produces a refusal rather than an unfiltered search.
4. **A post-retrieval cascade that escalates on the result**, never on a prediction.

**Why the rewrite ships.** It is the only query-side technique with a clear replicated win: a
LoRA-tuned Qwen2.5-7B rewriter moved nDCG@5 from 0.371 to 0.422, a 13.7 percent relative gain, on
SemEval-2026 Task 8, and the authors call it "the single most impactful intervention for multi-turn
retrieval". The same paper measured that first turns need no rewriting at all, identical nDCG@5 at 29
percent less inference, and that technical domains peaked at temperature 0.0 while at temperature 1.0
the financial domain scored 0.233, **below** the no-rewriting baseline of 0.275. **measured.** The gate
is a rule rather than a model, checking for pronouns, demonstratives and ellipsis, so it fires on the
minority of turns that are genuinely context-dependent. On a CPU box that gate is what makes the
technique affordable at all.

The prompt rule to copy verbatim: "Do not add information not present in the conversation", and "If the
question is already standalone, return it unchanged". Enforce the second by validation, not only by
requesting it: if the rewrite shares no content entity with the original turn or the history, discard it
and retrieve on the raw question.

**Why the glossary is corpus-mined.** A model-generated expansion is a guess, and `EOB` means
Explanation of Benefits in a medical practice and something else in a construction firm. A wrong
expansion retrieves the wrong concept **with the query's authority behind it**, which is the same drift
mechanism as HyDE one level down. Mining acronym-definition pairs from the client's own documents is
deterministic, auditable, costs nothing per query, and is the only version that survives a compliance
review, because every expansion can be traced to the document that defined it. `06-query-side` section
3.5.

**Why the filter rule is a refusal.** RagFlow issue #16832 documents the failure live: metadata date
filtering where exact-match returns zero and LLM range operators are silently dropped. A dropped filter
does not degrade politely. It retrieves confidently from a superseded document, cites a real page, and is
indistinguishable from working until it matters. It is also invisible in aggregate metrics, because the
answer is well-formed. Extract with a constrained decoder rather than free generation, because a date is
a small output space and the output is about 20 tokens, so it escapes the decode tax that kills the
other techniques. **measured, live bug report.**

**The structural guarantee that makes rewriting safe.** The user's question is stored verbatim and never
replaced. The rewrite produces a retrieval *key*, not a new question. Both go in the audit row. And the
entailment check runs against the **original** question, not the rewrite, because otherwise a drifting
rewrite launders itself through the verifier and comes out clean. Every chunk already carries document
id, page, access label and content hash, so a rewrite can change the query vector but cannot change what
a chunk says or where it came from. The drift risk is relevance, not provenance.

**Rejected.** HyDE as a default: its original advantage came from substituting document-to-document
similarity for relevance because the retriever was an **unsupervised** Contriever, and ours is a
supervised model that already has that signal. Across eleven expansion techniques, twelve datasets and
twenty-four retrieval models there is a strong negative correlation between retriever strength and
expansion gain. On a 23,088-query financial text-and-table benchmark it underperformed vanilla dense on
every metric (Recall@5 0.544 against 0.587). In production it cost 2.6x latency and on the 722 queries
where keyword matching already worked it lost in 21.2 percent of cases with a mean loss of 2.010 against
a mean win of 1.324. **measured, three sources.** Multi-query expansion scored nDCG@5 0.350 against a
no-rewriting baseline of 0.371: worse than doing nothing, because the extra variants diluted the primary
rewrite's signal. Domain-aware prompting scored the same 0.350, so added complexity did not compensate.
**measured.** Learned routing failed in every paradigm a production study tested: rule-based heuristics
38.4 percent against a 20 percent random baseline, the best classifier recovering 2.4 percent of the
oracle gap, gradient boosting −0.081, and logistic regression over multilingual embeddings −0.408, with
richer features performing monotonically worse. The authors' diagnosis is the reusable part: "the need
for LLM augmentation is determined by what the retrieval index contains, not by what the query says".
The training-free cascade beat the best router by 5.4x. **measured.** Decomposition is rejected as a
default despite having the largest measured gains (+36.7 percent MRR@10 on multi-hop), because the
frequency in a small-business corpus does not justify two generations per query on CPU, and because
composition failure persists even with perfect retrieval. Keep it as an explicitly triggered,
hard-capped path. Any unbounded agentic loop is rejected: 5.0 LLM calls per query, 1.75x context resent,
roughly eightfold output tokens, and failure modes including distractor latch and over-confident
finalize, every one of which produces a fluent cited wrong answer. `06-query-side`.

**Basis.** Measured for every accept and every reject. This is the second-best-evidenced stage.

---

### Stage 8. Answer, verify, and calibrate refusal

This is the product. `09-products` searched every commercial system it could read and found that **not
one verifies citations at the sentence level**: Glean surfaces deep links and hedges coverage, Azure
returns verbatim captions with no generative model in the path, and Bedrock's contextual grounding check
explicitly does not support conversational QA, marks the whole response relevant if any one chunk is
relevant, and on streaming APIs returns the irrelevant response to the user before marking it. That last
one is the competitive bar, and it is low.

**Four layers, in build order.**

**Layer 1: mechanical citation checks. Deterministic, no model, no licence question, ships always.**
This goes first, before any model-based check, because it is cheap, it never degrades, and it covers the
failures a user can see for themselves with one click.

1. **Index exists.** The cited chunk id is in the retrieved set for this answer.
2. **Page matches.** The cited page falls within the chunk's `page_start` to `page_end` range, with the
   straddle resolved deterministically using `charspan`.
3. **Quote present.** A quoted span appears in the chunk text after whitespace and unicode
   normalisation. Full-text search, not fuzzy.
4. **Numeric containment.** Every number in the claim appears in the cited passage in a comparable
   normalised form. This single check catches the numeric-mismatch blocker in the build brief's
   section 10, which is worth more than its implementation cost.
5. **Identifier containment.** Anything that looks like an identifier must appear **literally** in the
   retrieved page text, or the answer is refused. The lexical researcher calls this the highest-value
   item in the whole recommendation and I agree: it is deterministic, it needs no dependency, and it
   converts our worst failure mode into a refusal. It has a measured false-refusal risk too, since an
   OCR error in a scanned invoice number would fail the assertion on a correct answer, so measure its
   false-refusal rate on the golden set before making it blocking.

Known gap to state rather than hide: "sixty-seven cents" and "0.67 USD" will not match a naive rule, and
table cells and unit conversions are where this bites.

**Layer 2: entailment scoring with a purpose-built fact-checker.** Use
`MiniCheck-Flan-T5-Large` (MIT, 770M), chunking the document and taking the maximum support probability
across chunks, reproduced from the project's own inference source. It needs its own small service
container, because TEI's sequence-classification support is CamemBERT / RoBERTa / XLM-RoBERTa / GTE /
ModernBERT and MiniCheck is a T5 encoder-decoder. **verified from the TEI README.**

**The measured reason this is not a preference.** The verification researcher ran both approaches on a
stratified 150-pair sample of real RAGTruth output. General NLI cross-encoders landed at chance:
`nli-deberta-v3-base` 49.4 balanced accuracy and `tasksource/ModernBERT-base-nli` 52.6, with
false-alarm rates of 90.0 and 88.6 percent at threshold 0.5. Their apparently low false-negative rates
are an artefact of flagging almost everything unsupported, and at a 90 percent false-alarm rate a
correct sentence is flagged amber nine times in ten, so the product is unusable and no safer. Windowing
the premise to 900 characters around the best lexical match halved latency and moved balanced accuracy
by half a point, so the failure is not a truncation artefact. **measured on this hardware.**

And the honest ceiling, which has to be stated to the client: this class of model **misses** a large
fraction of hallucinations. Vectara's own published table implies a missed-hallucination rate of 68.1
percent on RAGTruth-Summ, 45.6 percent on RAGTruth-QA and 31.5 percent on AggreFact-SOTA, computed as
100 minus the published recall for the hallucinated class. A dedicated hallucination detector still
beats GPT-4 on the same table. One small classifier is a signal, not a verdict, and three layers plus a
refusal rule is the minimum a client-facing "verified" claim can rest on.

**Layer 3: claim decomposition, only where it pays.** MiniCheck's authors report that decomposing into
atomic facts is **not necessary** to reach their accuracy. So the default is not to decompose. Trigger it
only for sentences with more than one numeric token or more than about 40 words, where the mechanical
numeric check needs to know *which* number failed. Never decompose for display; show the reader the
sentence they can read.

**Layer 4: a local generative judge, restricted to the borderline band.** Only the ambiguous 10 to 20
percent between two thresholds goes to a generative judge, which keeps cost bounded. Four rules, each
from a measured bias: **the judge is a different model from the answerer**, because self-recognition
capability correlates linearly with self-preference bias; **judge one claim against one passage, never a
pair**, because GPT-4 was consistent on only 65 percent of swapped pairs and preferred the first
position in 30 percent of cases; **require the judge to produce a supporting span** and check it
mechanically in Layer 1, which makes a judge that can only say "supported" by quoting real text much
harder to fool; and **version the judge prompt with the results**, because the same model and data moved
GPT-4's failure rate from 14/20 to 6/20 to 3/20 across three prompt styles. `Qwen3-4B` or
`granite-guardian-3.3-8b` (both Apache-2.0) are the office-build candidates; on the cloud build the
answerer is a Bedrock model so the judge is naturally a different model.

**The latency budget, which is a sizing decision and not a tuning knob.** Measured single-thread on a
1,200-character premise: 1,583 ms at batch 1, 1,772 ms at batch 8, 1,636 ms at batch 16. **Batching does
not help at all.** This is compute-bound on CPU, so verification scales with cores rather than with
throughput. A 770M verifier is roughly four times a 184M model, so about 6 to 8 seconds per claim on one
core, extrapolated rather than measured. Eight claims on eight cores in parallel is about 13 seconds at
the small size and about 50 seconds at 770M. Neither is acceptable in a chat interface, and neither is
the model's fault.

The consequence is architectural: **stream the answer, verify per sentence across cores as sentences
complete, and mark the amber ones as they resolve.** The reports disagree on nothing here; this is the
one place where two of them independently reach the same design. It is also the honest argument for a
GPU in the office build, and the honest statement of what a GPU buys: latency and the judge tier, not
accuracy, because the verifier's false-negative rate is a property of the model and its training data.

**Refusal, with a number attached.** Split conformal prediction over the verifier's support score
selects the threshold. From a calibration set of n and a risk level alpha, the threshold is the
`ceil((1-alpha)(n+1))`-th smallest score, and the participation guarantee lies in
`[1-alpha, 1-alpha + 1/(n+1))`. **The guarantee is exact for any n**, which is precisely why it fits a
60-question world where nothing else does. The researcher's simulation reproduces the mechanism:
threshold from 60, measured on a fresh 60, gives mean conditional correctness 0.85 against a nominal
0.80 floor. **measured, simulation.**

Three assumptions, stated because they are easy to break. Exchangeability: the golden set must be
written with the client, not from a template. The threshold must not be tuned on the data you report on,
and the researcher simulated the flattering direction of getting that wrong: calibrating and reporting
on the same 60 gives 1.00 instead of 0.85. And the score must be computed the same way in calibration
and at run time, so a model swap, a chunking change or an embedding change **invalidates the calibration
set** and you recalibrate rather than reuse.

**Refuse per claim, not per answer.** This is the design decision that keeps the system useful, because
a blanket refusal on any unsupported sentence makes the product useless on any answer longer than three
sentences once the verifier's false-positive rate is allowed for.

| Support state | What the client sees |
|---|---|
| All claims certified | Normal answer with page citations |
| Some claims unsupported | The answer, unsupported sentences marked amber, the citation removed from them, and a one-line note: "2 of 7 sentences are not supported by the cited pages" |
| The unsupported claims carry the answer | Refusal with `insufficient_evidence`, retrieved passages still shown |
| No claims certifiable and retrieval weak | Refusal with `no_source` |

The middle row is the one that wins deals. It is honest, it is specific, and it is checkable in ten
seconds by the person reading it.

**Keep the four refusal reasons distinct**, because each fires on a different signal and collapsing them
throws away the operational signal. A rising `insufficient_evidence` rate means ingestion or chunking is
broken. A rising `no_source` rate means the corpus does not cover what people are asking, which is a
conversation with the client about their documents rather than a bug.

**The ceiling, stated plainly to the client in two parts.** Conformal calibration guarantees the
**participation rate**. It cannot guarantee that what is said is supported, because the verifier's own
false-negative rate sets that bound. A refusal threshold tuned on a verifier that misses 40 percent of
hallucinations produces confident wrong answers with a certificate attached, which is worse than no
verification, because it is verification a client will believe. So both numbers go in the acceptance
test: the verifier's measured false-negative rate on human-labelled answers, and the participation
guarantee.

**Rejected.** A general NLI model as the verifier (measured at chance with a 90 percent false-alarm
rate). Any single small classifier as the gate (measured missed-hallucination rates of 40 percent on our
sample and 31 to 68 percent in Vectara's published table). A cross-encoder reranker reused as the
verifier, because it is trained on query-passage relevance rather than premise-hypothesis entailment and
will happily score a passage as highly relevant for sharing vocabulary with a claim the passage
contradicts. Self-verification by the answering model. Pairwise LLM judging. `Bespoke-MiniCheck-7B`,
which is the best-scoring fact-checker on the leaderboard and whose card says to contact the company for
a commercial licence, so it is unpriced. `HHEM-2.1` (the non-Open one), which is cloud-only and
therefore disqualified for the office build regardless of quality. Bedrock's contextual grounding check
as the verifier. Semantic entropy or repeated sampling as the abstention signal, which needs several
samples per query and on a CPU box at single-digit tokens per second is a different product. And claim
decomposition for every sentence. `08-verification` section 6.

**Basis.** Measured on our own hardware for the NLI rejection and the latency wall. Measured simulation
for the statistics. Published measured tables for the ceiling.

---

## 2. Where the researchers disagreed, and what decided it

Six real conflicts. I am naming them because averaging them would have produced a worse architecture.

**1. `bge-m3`'s sparse head as the lexical arm versus Postgres full-text search.**
The fusion researcher recommended starting with `bge-m3`'s sparse output, on the grounds that it is MIT,
local, and free in the same embedding call. The embedding researcher found from TEI's source that TEI
serves only the dense head, and that SPLADE pooling is restricted to Bert and Distilbert `MaskedLM`
architectures that `bge-m3` is not. **Decided by source verification of the serving layer.** The
recommendation does not survive contact with the thing that would have to serve it.

**2. A BM25 extension versus `ts_rank`.** The brief assumed BM25 was needed. Measured head-to-head on
200,000 chunks, the two tied on the failure case at the ceiling, 60/60 each. What actually failed was
tokenisation, upstream of ranking. **Decided by controlled measurement on our own hardware**, plus the
observation that rank fusion discards exactly the score magnitude IDF provides, so the deficit does not
bind. I also corrected an error in one researcher's summary while checking this: the fusion report's
summary gave BM25 and `ts_rank` both 60/60 on trailing-digit queries, but the lexical report's own table
reads 40/200 against 189/200. The body was right and the summary conflated two tests.

**3. Rerank, or not.** The reranking researcher concluded reranking is "the highest-value component
after ingestion", citing BEIR's measured +12.5 percent relative for BM25 plus a cross-encoder, the only
system of ten that beat BM25 on 16 of 18 datasets. The fusion researcher found the same technique's
measured benefit much weaker, with only two of five gains surviving held-out validation and one dataset
where tuned fusion beat all four cross-encoders at every candidate count. The late-interaction
researcher found the CPU cross-encoder at 2.0 to 9.3 seconds per query in the exact configuration the
build brief specifies. **Decided by taking all three as true and letting the constraint bind:** the
quality case is real on a GPU and unproven on our corpus, the cost case is measured, and the two
researchers disagree precisely because they are measuring different things. So the reranker becomes a
flagged experiment whose first gate is a latency measurement on the real instance, which is step 0 of
the build order rather than a stage of the architecture.

**4. Late interaction: recommended for the office build, cut for both here.** The late-interaction
researcher recommended putting `answerai-colbert-small-v1` into the office build's evaluation set, and
their measurement is the best in the study: ColBERT MaxSim ties the cross-encoder on quality (0.776
against 0.779 on msmarco, identical 0.905 on dbpedia, and it *wins* by 0.043 on scifact) while being 20
to 190 times faster. **Decided by what we can measure and what it costs to try.** TEI serves no
multi-vectors at all, verified by cloning it at HEAD and finding zero matches for `colbert`,
`multi_vector`, `max_sim` or `maxsim` anywhere in the tree, so adopting it adds a third serving
component with a different API shape in an architecture that deliberately keeps the container count low.
And a 0.003 nDCG difference is unmeasurable on 60 questions, which the researcher says themselves. The
recommendation is right for a GPU build with a larger eval set. It is not right for this one, and I am
recording what would change it: if retrieval recall comes in below target with *semantic* rather than
lexical misses, late interaction is the cheapest remaining lever.

**5. Small-to-big: recommended on mechanism, declined here.** See Stage 2. Three sibling reports each
recommend a chunk-size strategy and they are not mutually compatible with a page-bounded chunk. I chose
the page as the citation unit and recorded the escape hatch, which needs no re-embedding because
`prev_chunk_id` and `next_chunk_id` are captured at ingest.

**6. Chunk size and the "one embedding model matters most" escape hatch.** The chunking researcher
measured that embedding-model differences are larger than chunking-strategy differences across 1,820
configurations, and used that to argue against over-tuning chunking. True, and it cuts the other way
here too: it is also an argument against spending a re-embed on a 0.109 nDCG model swap. **Decided by
treating both as true**, which is why the embedding contract exists in Stage 4 and why the model
question goes to the golden harness rather than to this document.

---

## 3. Licence flags for anything we would ship

Every licence below was read from the project's own LICENSE file or its model card on 2026-10-11 by one
of the nine researchers. Where the artifact is a metadata field rather than a document, that is stated,
because it matters to a procurement review.

### Shippable as-is, no legal conversation

| Component | Licence | Where verified |
|---|---|---|
| PostgreSQL and `pg_trgm` | PostgreSQL License | `postgres/postgres` `COPYRIGHT`, 1,198 B, sha256 prefix `3d6af92ff8a4c2cd`; `contrib/pg_trgm/pg_trgm.control` |
| Qdrant | Apache-2.0 | repository `LICENSE` |
| Hugging Face TEI | Apache-2.0 | repository `LICENSE` |
| Docling, docling-core, docling-ibm-models, docling-parse | MIT | raw `LICENSE` files |
| Tesseract, RapidOCR, PaddleOCR, EasyOCR | Apache-2.0 | raw `LICENSE` files, GitHub licence API |
| OCRmyPDF | MPL-2.0 | repository `LICENSE`; file-level copyleft, fine in a container |
| pdfplumber | MIT | repository `LICENSE` |
| pypdf | BSD-3-Clause style | repository `LICENSE` |
| `BAAI/bge-m3` | MIT | model card frontmatter **and** FlagEmbedding `LICENSE`, Copyright 2022 staoxiao |
| `BAAI/bge-reranker-v2-m3` | Apache-2.0 | model card frontmatter; **no LICENSE file in the repo** |
| `lytang/MiniCheck-Flan-T5-Large` | MIT | model card frontmatter; base `flan-t5-large` is Apache-2.0; **no LICENSE file** |
| `Qwen/Qwen3-4B`, `Qwen/Qwen3-1.7B` | Apache-2.0 | `LICENSE` read from the repo root |
| `microsoft/Phi-4-mini-instruct` | MIT | `LICENSE` read from the repo root |
| `ibm-granite/granite-guardian-3.3-8b` | Apache-2.0 | model card |
| `tasksource/ModernBERT-base-nli` | Apache-2.0 | model card |
| Tiger Data `pg_textsearch` | PostgreSQL License | `timescale/pg_textsearch` `LICENSE`, 910 B, sha256 prefix `d33de21a123ce25b` |

### Flagged: shippable but the licence artifact is weak

**Almost none of these model repositories contains a LICENSE file.** `bge-m3`, `bge-reranker-v2-m3`,
`Qwen3-Reranker`, `gte-reranker-modernbert-base`, `colbertv2.0` and others declare their licence only as
a README frontmatter field. Only the mixedbread-v2 models ship an actual `LICENSE` file. **Action: at
weight-freeze time, archive the model card, the frontmatter and the commit hash for every model we ship,
so counsel has a document rather than a metadata string.**

**`microsoft/harrier-oss-v1-0.6b`** declares `license: mit` in the card frontmatter and has **no LICENSE
file in the repository**. A card declaration is weaker evidence than a file. It is a challenger model,
not a default, so this does not block anything, but confirm with Microsoft before shipping it in a paid
build.

### Do not ship

| Component | Licence | Why it is out |
|---|---|---|
| ParadeDB `pg_search` Community | **AGPL-3.0 only** | Verified three ways: `LICENSE` is the full AGPL text (34,523 B, sha256 prefix `8486a10c4393cee1`), root `Cargo.toml` `[workspace.package] license = "AGPL-3.0"`, and the Enterprise page says self-hosting is "contact sales". Commercial licence exists but is **not priced**, so it fails the "available **and** priced" test |
| `tensorchord/vchord_bm25` | **AGPL-3.0 or Elastic License v2** (dual) | Both branches disqualifying. ELv2 restricts providing the software as a service and commercial redistribution, which is closer to what we do than AGPL is. No published price |
| Elasticsearch | **AGPL-3.0-only / SSPL v1 / Elastic License 2.0** (triple), `x-pack` ELv2-only | All three default branches disqualify for a self-hosted build we sell. Not a close call and it does not need a lawyer |
| Typesense | **GPL-3.0** | Running a GPL daemon in a separate container is defensible and the project argues so itself, but distributing the container **is** conveyance, so we inherit GPL source-and-notice obligations for a datastore we do not need |
| Meilisearch Enterprise Edition | **BUSL-1.1** | The EE file grants non-production use only; production requires a commercial agreement. The MIT core is shippable and is the subset we do not need |
| `PyMuPDF`, `pymupdf4llm` | **AGPL-3.0** | Commercial licence via Artifex with **no published price**. This is the easiest licence mistake to make in a Python PDF stack |
| `MinerU` | Apache-2.0 **plus additional terms** | Attribution obligation plus a commercial licence above 100M MAU or USD 20M monthly revenue. The thresholds do not bind us; bespoke clauses are legal review we do not need when MIT and plain Apache-2.0 alternatives exist |
| `Onyx` `ee/` directories | **Onyx Enterprise License** | Per-seat subscription required for production; development and testing are carved out, so a build would work in development, look complete in a demo, and be unlicensable in production. Onyx core is MIT and we use it as a **reference architecture only** |
| `mistralai/Ministral-8B-Instruct-2410` | **Mistral AI Research License** | Non-commercial. Disqualified outright |
| `google/gemma-3-4b-it`, `meta-llama/Llama-3.2-3B-Instruct` | Gemma Terms, Llama Community Licence | Not OSI licences, gated, and carry use restrictions on a product we ship. Apache-2.0 alternatives exist for both roles |
| `google/embeddinggemma-300m` | Gemma terms, `gated: manual` | Gated access needs an authenticated account and a click-through, which we should not require in a client's building |
| `nvidia/llama-embed-nemotron-8b` | `customized-nscl-v1` + Llama 3.1 | Card states plainly "for non-commercial/research use only" |
| `Qwen/Qwen2.5-3B-Instruct` | `qwen-research` | Research licence. **Do not substitute it for Qwen3-4B**, which is Apache-2.0 |
| `bespokelabs/Bespoke-MiniCheck-7B` | Commercial, on request | Best-scoring fact-checker on the leaderboard, and unpriced. Worth a quote only if the verifier ever becomes the headline feature |
| All Jina rerankers, ColBERT models and v3/v4/v5 embeddings | **CC-BY-NC-4.0** (v4 is the Qwen Research License) | Jina's own on-premises FAQ states the NC term forbids commercial use without a licence, and the `jina-reranker-v3` card says in terms that on-premises use within a company needs one. **No public price**: the FAQ routes to sales and `jina.ai/COMMERCIAL-LICENSE-TERMS.pdf` returned HTTP 404 when fetched. This matters because `jina-reranker-v2-base-multilingual` is the model that produced both of Qdrant's confirmed reranker wins |
| `lightonai/Reason-ModernColBERT` | **CC-BY-NC-4.0** | Non-commercial **despite its Apache-2.0 base**. Easy to pick up by mistake: a "ModernColBERT" search returns both, and only `GTE-ModernColBERT-v1` is Apache-2.0 |
| `Xenova/ms-marco-MiniLM-L-6-v2` | **No licence field at all** | The ONNX conversion used in the benchmark has no licence. Use the upstream Apache-2.0 `cross-encoder/ms-marco-MiniLM-L6-v2` instead |

### Could not verify, and therefore not to be assumed

- **`nvidia/llama-nemotron-rerank-1b-v2`** declares `license: other` / `openmdw-1.1`. OpenMDW is a Linux
  Foundation model-weights licence and the text was **not read**. It is neither Apache-2.0 nor MIT, so it
  is not in the obviously-fine bucket. At 1.2B it is an unlikely CPU choice regardless.
- **Jina commercial licences.** No priced on-premises licence was found for any Jina model. A licence
  may be negotiable; it is not verifiable.
- **ParadeDB Enterprise self-hosting at our scale**, as opposed to being effectively Cloud-only. The page
  says "contact sales" and there is no number.
- **Llama, Gemma and the Nemotron candidate repos** are gated and returned HTTP 401 to unauthenticated
  reads. Their licence texts were not read.
- **A structural licence risk to carry forward.** The late-interaction and listwise-reranking frontier is
  going non-commercial: `jina-colbert-v2`, `jina-reranker-v3` and `Reason-ModernColBERT` are all
  CC-BY-NC-4.0. The shippable set in that family is real but static. **Do not architect around a model
  whose licence we would have to negotiate.** The same test applies to any future component: a licence we
  cannot see the price of is not a licence we can ship on.
- **Segmentation and LLM API terms.** n8n's Sustainable Use License remains an open blocker in the
  build brief and is not resolved here. It is out of scope for retrieval but it is the same class of
  question and it belongs in the same review.

---

## 4. Measurement plan for 60 golden questions

The constraint is real and it binds harder than any architecture choice in this document. Here is what
the arithmetic actually says, and then what to do about it.

**What 60 questions cannot do.** Wilson 95 percent intervals at n=60 are 18 to 23 points wide in the
range that matters: 48/60 is 0.68 to 0.88, 51/60 is 0.74 to 0.92, 54/60 is 0.80 to 0.95. The normal
approximation gives a zero-width interval at 60/60, which is obviously wrong, so use Wilson. **measured,
simulation.** For a paired comparison, McNemar's minimum detectable difference at 80 percent power is 10
points at 10 percent discordance, 13.9 at 15 percent, 16.3 at 20 percent and 20.4 at 30 percent. At 15
percent discordance, a true 10-point improvement is detected with power 0.40. **measured, simulation.**
And multiple comparisons are the trap: uncorrected, the chance of at least one false win is 22.6 percent
for five variants, 40.1 percent for ten and 64.2 percent for twenty, and Bonferroni correction destroys
what little power there was, taking power at a true 10-point improvement from 0.40 to 0.13 with five
variants and 0.06 with ten. **A ten-variant bake-off on 60 questions cannot work.** The correction is
not the problem; the experiment is.

Two further constraints from the evidence. Roughly 85 percent of a query set can be inert for the
question being asked: the mempalace fusion A/B found 170 of 200 probes were ties between the two methods,
with 10 improvements and 20 regressions carrying the result. **measured.** And many IR benchmarks carry a
**lexical bias** because lexical systems were used to build them, which flatters BM25 and hybrid and
which our own hand-written set will inherit unless we deliberately include paraphrased and conceptual
questions. **measured, BEIR's own section 6.**

**Therefore: the golden set is a regression detector and a mechanism checker, not a ranking instrument.**
It is not used to choose between retrieval variants. The architecture above is chosen on the evidence,
and the questions exist to tell us whether the implementation works and whether it regresses.

### The set

- **60 questions per client, 40 general and 20 specific to their work.**
- **Forty answerable, twenty deliberately not.** The twenty unanswerable ones are the only questions that
  measure refusal, and refusal is the product claim. If the client will not write them, we write them and
  they approve them.
- **Split 40 for conformal calibration and 20 for reporting**, fixed before the first run and recorded in
  the manifest. Scoring metrics can use all 60, because each question is scored independently; the split
  governs only whose score produces the threshold and whose score is reported as a result.
- **Stratify, so a small stratum flip is legible where 3-of-60 is not.** Target distribution:
  20 single-lookup factual; 10 conversational follow-ups needing a rewrite; 10 identifier, code or
  acronym; 8 date- or version-constrained; 7 must-refuse; 5 table-cell or page-straddle.
- **Record per question:** the question, the gold passage ids, the gold page numbers, a one-line gold
  answer, the stratum, answerable true or false, and a note on which configuration it is believed to
  discriminate. Hand-write them. **Do not auto-generate them** for the acceptance set: generated
  questions are answerable by construction in the generator's own words against the chunk the generator
  was shown, so they assume retrieval was correct, leak the document's vocabulary, systematically avoid
  the awkward cases, and never produce an unanswerable question. A generated set measures the plumbing.
  It cannot measure search quality, because it was built by the thing under test.
- **Freeze and hash them into the manifest.** A question changed later invalidates the calibration set.

### Metrics, defined once in code

- `recall@10` = 1 if any gold page is in the top 10, averaged over questions. This is the acceptance
  number. Report `MRR` beside it as a secondary.
- `recall@50/100/250` at the retrieval boundary, to set candidate depth. Report once at build time, not
  tuned per release.
- `support_rate`, both per-sentence and per-answer-then-averaged, with a Wilson interval. The per-answer
  mean is what a client experiences.
- `unsupported_escaped`: unsupported sentences that passed verification, against human labels. **This is
  the verifier's false-negative rate and it is the number the product is sold on.** It needs human
  labels, so it comes from the 60 and from a judge-validation subsample.
- Refusal rate overall and split by the four named reasons.
- The five release-blocking binary results, each with zero tolerance.

**Always report the interval, always report n beside the percentage, and always report the participation
rate next to the support rate.** A system with 100 percent support and 60 percent refusal is not a
product. A system with 70 percent support and 2 percent refusal is a liability. Publish the pair.

### How to use it per release

1. **Pre-register exactly one primary comparison.** Decided before the run, written in the release notes,
   one question: did this change make the primary metric better or worse. Everything else is exploratory
   and labelled as such, generates the hypothesis for the next release, does not justify a change on its
   own, and does not go in a client-facing report.
2. **Run both configurations on the same 60 questions and report a contingency table, not two
   percentages.** "Of the 60, 51 were correct under both, 3 correct under B only, 1 correct under A only,
   5 wrong under both" is readable at n=60. "Engine A got 52, engine B got 54" is not.
3. **Apply McNemar on the discordant pairs** with the exact test as a check; it tracks the asymptotic one
   closely here.
4. **Never re-run for a better draw.** A pass at 55/60 is 91.7 percent and a fail at 54/60 is 90 percent,
   so the 92 percent gate sits inside the noise band. Decide the pass mark before running.
5. **Hold the question set fixed across releases**, which makes the release-to-release comparison paired
   and its discordance smaller.
6. **If a real choice needs more power, buy questions, not statistics.** Growing to about 600 questions
   moves the detectable difference from roughly 15 points to roughly 5. That is a day or two of client
   time and it is the honest price of the decision.
7. **Recalibrate the conformal threshold whenever the verifier, the parser, the chunking or the embedding
   model changes**, and record the threshold and the calibration-set hash in the audit record.
8. **Report the judge's measured false-negative rate beside every judge-assisted number.** A judge that
   misses unsupported claims inflates the reported support rate, which is the dangerous direction because
   it hides the failure that loses clients. With 15 percent truly unsupported and a measured support rate
   of 85 percent, a judge missing 20 percent of unsupported claims reports 88.0 percent. Prefer a human
   check on a subsample.
9. **Re-examine the twenty unanswerable questions every six months or after a corpus change.** Documents
   change, and a question set nobody revisits slowly stops measuring anything.

### The three things actually worth measuring properly

Because the set cannot rank retrieval variants, spend it on the questions it can answer.

**1. The ceiling diagnostic, and it is the highest-value hour in this whole plan.** Score the candidate
union as if perfectly ordered and compare with what the pipeline returns. Qdrant measured that
best-possible-ordering gap at 200 depth running 0.247 to 0.487 nDCG@10 across five datasets, which is a
large amount of recoverable quality sitting *above* the ranking stage. **measured.** A large gap means
ranking is the problem. A small gap means retrieval is, and then the follow-up question is whether
relevant pages are in the union at all, because if they are not, no ranking fix helps. That single
diagnostic tells us whether the reranker is even the right lever for this corpus, in one afternoon,
using the set we already have. `04-fusion` section 5.1.

**2. The contextualisation ablation**, four runs of the 60: none; deterministic only; deterministic plus
a local model; deterministic plus a frontier model. Four numbers tell us whether the local generation
earns its ingest cost, and nobody has published this measurement. It decides days of batch compute per
client deployment.

**3. The reranker decision**, in this order: measure latency on the reference instance at 25, 50 and 100
candidates; measure the ceiling gap; if the gap is large, check whether relevant pages are in the union;
rerank 10 candidates and compare against fusion alone on held-out questions; only then raise the count.
Check the reranker's context window against the 95th percentile of our query-document pair lengths first,
because truncation was the diagnosed cause of two of Qdrant's losses.

### Mechanism checks, because these are pass or fail

Five single questions, one per failure mode, checked by hand rather than scored: **table cell retrieval**
(the retrieved chunk contains the whole row with its header, and its recorded page matches the page the
table is on); **page straddle** (the recorded page range contains the evidence page and the highlight
lands on the right page); **superseded document** (the answer cites the current version and the old
version is never retrieved); **scanned page** (the answer is correct and the highlight is at least on the
correct page, with `is_ocr` recorded); **heading disambiguation** (two sections use the same words for
different things and the retrieved chunk's `heading_path` is the one the question meant).

Plus four unit tests on the mechanical verifier against known-bad answers, which must fail closed: a
fabricated citation index, a wrong page, a missing quote, and a changed number. **Run these on every
release, not only the end-to-end run.** They cost nothing and they are the layer that never degrades.

---

## 5. Build order

Accuracy-critical pieces first. The ordering principle is that **recall is decided before ranking, and
correctness of the citation is decided before either.**

**Wave 0. Measurements that gate decisions, before any retrieval code.**

1. Run `bench_tei2.py` against the m7i.4xlarge with nothing else running, at 25, 50 and 100 candidates.
   Fifteen minutes, and it replaces an entire section of `05-reranking` with a real number. If 50
   candidates lands above about 2 seconds, the reranker decision changes before we write it.
2. Confirm whether TEI 1.6 (the pinned version) serves a CPU reranker, and fix the image regardless: the
   CPU tag is `cpu-1.9`, and `MAX_WARMUP_SEQUENCE_LENGTH=256` with `MAX_WARMUP_BATCH_SIZE=4` turns a
   ten-minute first boot into 20 seconds.
3. Run `llama-bench` on the reference instance for prompt-processing and generation throughput, and verify
   prefix KV reuse across sequential requests with a shared prefix. The whole local contextualisation
   cost case rests on both numbers.
4. Mine the acronym glossary from the fixture corpus and measure the yield. A corpus of scanned invoices
   will produce few pairs, and that is worth knowing before it is a stage.

**Wave 1. The spine. Nothing downstream is trustworthy without it.**

5. Docling ingestion with page, table and bbox provenance. **Gate: do not proceed until a scanned,
   multi-column fee schedule reads back with the right numbers on the right page.** `RET-PARSE-001`
6. Page-bounded, structure-aware chunking at 384 to 512 tokens, tables whole, `page_start`/`page_end`
   stored, `coord_origin` recorded, `is_ocr` set, stable `uuid5` chunk IDs. `RET-PARSE-001`
7. The golden set, written with the client, stratified, twenty unanswerable, hashed into the manifest.
8. The embedding contract as a Postgres row, plus the collection-generation scheme and the read-path
   refusal on mismatch.
9. Dense retrieval: TEI `cpu-1.9` with `bge-m3`, Qdrant with `M=32`, `EF_CONSTRUCTION=256`, the
   `is_tenant` ACL index, `is_current`, `hnsw_ef` set to a recall target. `RET-VERSION-001`,
   `RET-DUPE-001`

**Wave 2. The lexical arm, in the same wave as dense because hybrid is one component.**

10. `tsvector` with `english` for prose and `simple` for identifiers, `setweight`, GIN indexes, `ts_rank`
    with an explicit `normalization`. **Never `ts_rank_cd`, never the default normalisation.**
11. The regex-extracted identifier column, whole plus separated components, populated at ingestion, with
    the generated-column staleness trap handled and a test for it. `RET-HYBRID-001`
12. `pg_trgm` installed, reached through `word_similarity` over a candidate set and never through `%` at
    first pass.

**Wave 3. Fusion and access control.**

13. RRF in our own code at `k=60`, equal weights, over 500 candidates per arm, with the deterministic
    tie-break. Verify the arithmetic against the Qdrant client's implementation.
14. ACL as a query-time filter constructed **server-side from the authenticated session**, with any
    caller-supplied scope re-validated against the user's access and rejected with a recorded denial, and
    a regression test shipped before the feature. **Ship the test before the feature.** `SEC-ACL-001`
15. Measure filter recall explicitly for the admin path's AND-over-two-broad-values predicate, which is
    the exact shape Qdrant measured collapsing to 39.7 percent. An admin getting worse answers than a
    non-admin, silently, is the failure this prevents.

**Wave 4. Verification. This is the product, and it ships before the reranker.**

16. Layer 1, the mechanical checks, including the literal identifier assertion with its false-refusal
    rate measured on the golden set. Four unit tests against known-bad answers. `RET-CITE-001`
17. Layer 2, MiniCheck in its own container, with the `.bin`-loading workaround (`pytorch_model.bin` only,
    and Transformers 4.57 refuses to load it below torch 2.6 for a known vulnerability: load the state
    dict directly, pin torch, or convert to safetensors, and pick one deliberately).
18. Layer 3, decomposition triggers. Layer 4, the borderline judge on a different model from the answerer.
19. Refusal as four named reasons with traces, refusal per claim, amber marking, streaming verification
    across cores. `RET-CITE-001`
20. Conformal calibration on the 40-question calibration split, with the threshold and the calibration-set
    hash recorded in the audit record.

**Wave 5. Experiments that must earn their place, in this order.**

21. The ceiling diagnostic, then the reranker comparison, then the decision. Behind a flag until then.
22. The contextualisation ablation, four runs.
23. Fielded search as a third arm over Docling's heading text.
24. The query-side work: rule-gated rewrite, glossary application, structured filter extraction with the
    refuse-don't-degrade rule, and the post-retrieval cascade.

**Wave 6. Contingencies, with their triggers named.**

25. `multilingual-e5-large` on the same 60 questions, as the pre-registered embedding challenger. Trigger:
    the C2 release after the pipeline is stable.
26. `pg_textsearch`, when we want real BM25. Trigger: the next architecture review, gated on release
    cadence and the Postgres 17/18 constraint.
27. `gte-reranker-modernbert-base` (149.6M) and ONNX int8. Trigger: 50 candidates above about 2 seconds.
28. `answerai-colbert-small-v1` as a late-interaction rerank stage. Trigger: retrieval recall below target
    **with semantic rather than lexical misses**. Office build only.
29. Get the ParadeDB self-hosted price. Trigger: before the next architecture review. It is the only
    option here blocked on a number rather than on a fact, and it is the best-engineered engine tested.

---

## 6. What we are explicitly not building, with the reason

| Not building | Reason | Evidence |
|---|---|---|
| A BM25 extension (this phase) | The measured tie on the failure case, and rank fusion discards the IDF magnitude BM25 provides. Revisit via `pg_textsearch` | measured |
| A Lucene sidecar (ever) | Second datastore, dual writes, a reconciliation job, a second backup path, a new network listener. On a one-maintainer deployment that is the cost that hurts | inference from production practice |
| Late interaction | TEI serves no multi-vectors at all, and a 0.003 nDCG difference is unmeasurable at n=60 | measured + verified source |
| Semantic chunking, summary indexing, RAPTOR | Measured negative or neutral against a free deterministic alternative; corpus-global state breaks incremental ingestion | measured |
| High overlap | "Generally neutral to negative" across 1,820 configurations, and it makes citations ambiguous | measured |
| HyDE, multi-query, decomposition by default | Negative or neutral in the settings closest to ours, at 2.6x latency in production | measured |
| A learned query router | Four paradigms, best recovered 2.4 percent of the oracle gap, richer features performed worse. A cascade beat it 5.4x | measured |
| An unbounded agentic loop | 5.0 calls per query, 1.75x context resent, eightfold output tokens, and failure modes that produce confident cited wrong answers | measured |
| Small-to-big as a stage | The page is already a definite citation unit; the escape hatch needs no re-embedding | inference |
| GraphRAG or any graph store | Own benchmark says it often underperforms vanilla RAG; about twelvefold the index-build tokens | measured (abstract) |
| A RAG framework as the substrate | Dependency churn, offline install complexity, and it abstracts away exactly the parameters we must control | production practice |
| A managed knowledge base | Retrieval would run outside the audit sink and outside the verifier | architecture |
| Elasticsearch, Vespa, or an eleventh container | Licence for Elastic; footprint for both, on a box already running OCR | inference |
| Bedrock's contextual grounding check as the verifier | Does not support conversational QA, decides at whole-response granularity with an OR, and leaks on streaming | vendor |
| A reranker score as the refusal threshold | Microsoft warns against thresholding its own reranker score finely. A discrete, inspectable per-sentence decision is more defensible | vendor |
| Query-side generation as a default | Decode-bound on CPU. The office build's reply path is single-digit tokens per second, so a 150-token auxiliary generation is 15 to 30 seconds before retrieval begins | our own doc, unverified figure |

---

## 7. The differentiator, in one paragraph

Our retrieval is deliberately the same as everyone else's, and that is a decision rather than a
limitation. Onyx fuses two arms at equal weights over 500 candidates and deleted its reranker. RAGFlow
leans on a 0.3 vector weight and a wide rerank window. Elastic's own documented RRF example overrides
both its defaults downward. Azure caps reranking at 50 compressed candidates and tells developers not to
threshold the score finely. The systems with real customers optimise for a bounded, predictable per-query
cost and treat the final ranking stage as something to cap. What none of them ships is **per-claim
verification before display**: a mechanical layer that cannot be argued with, a purpose-built
fact-checker rather than an NLI head, a judge restricted to the band where a judge adds information, and
a refusal threshold with a conformal guarantee attached at n=60. That, plus page-anchored structure
preservation through ingestion, is the part a competitor cannot copy from a tutorial, and it is the only
part that answers the question that loses a client. `09-products` searched for a commercial equivalent
and found none.

---

## 8. What remains unverified, stated plainly

These are the places where the architecture rests on something nobody measured. They are listed so they
are not mistaken for facts, and each one has a measurement attached to it in the build order.

1. **CPU throughput for TEI, for any model, on our hardware.** The TEI documentation publishes only an
   A10 GPU benchmark. Every ingestion-throughput figure in the study is arithmetic. The `llama-bench` and
   `bench_tei2.py` runs are the fix. **unverified.**
2. **Whether a small local model generates *good* chunk context.** No published evaluation compares
   small-model-generated context against a deterministic heading path. `07-chunking` calls this its
   largest open question and the ablation in Wave 5 is how to close it. **unverified.**
3. **Whether our local inference server reuses the KV cache for a shared prefix** across sequential
   requests, and by how much. The entire local contextualisation cost case rests on it. **unverified.**
4. **MiniCheck's false-negative rate is not settled by our own run.** It scored 24 of 150 intended pairs
   before the time budget stopped it. The metrics point the same way as Vectara's published table, and
   that agreement is why a direction is stated at all. It is not a number for a client document.
   **partial measurement.**
5. **MiniCheck's latency is not a clean number.** The 27.6 seconds per pair was measured while another
   process held about 2.5 cores. The size ratio against the 184M model is the transferable part, and the
   6-to-8-second estimate is an extrapolation. **inference from a contended measurement.**
6. **MiniCheck's published configuration was not tested.** The chunk-and-max path is the real design and
   the single-window run is likely worse on quality and on cost, which makes our numbers a floor.
   **unverified.**
7. **The optimum table serialisation for `bge-m3` specifically.** Gomm's central finding is that there is
   no universal optimum and the choice is embedding-model dependent, and `bge-m3` was not tested.
   **unverified.**
8. **No measured CPU figure for the cross-encoder at production passage lengths on our hardware.** The
   measured 2.0-to-9.3-second figures came from a benchmark machine running a local CPU cross-encoder,
   and the ColBERT comparison figures came from server-side GPU inference, so that ratio is not a clean
   CPU-to-CPU comparison either. **unverified, and gated by Wave 0.**
9. **ColBERT MaxSim CPU latency.** No measured CPU number exists. The compute-shape argument says a
   matmul over precomputed vectors beats a full forward pass per candidate even at a 10x CPU penalty,
   but that is inference. **unverified.**
10. **Fielded BM25F versus flat search on business documents.** No measured evidence either way. **unverified.**
11. **No independent benchmark compares `ts_rank` to BM25 on a shared corpus with shared queries.** The
    comparisons here are ours, on a synthetic corpus, and should be reproduced on real client documents
    before they carry weight in a contract. **unverified, and this is why the identifier column matters
    more than the ranking function.**
12. **The lexical test corpus has a known artefact.** Every engine failed the surname test, 1 hit in 400,
    because the generator drew 24 surnames uniformly so one in 24 of 200,000 chunks carries any given
    name and every such chunk is near-identical filler. There is no signal to rank on. The lesson to keep
    is real and separate: ranking cannot separate candidates when the corpus gives it nothing to separate
    them by, so "what did we agree with Zielinski" is a metadata filter or a boolean with a date, not a
    better relevance score. **measured, and the measurement is an artefact.**
13. **Whether the office build's pinned TEI 1.6 serves a CPU reranker at all** was not tested. **unverified.**
14. **The five GraphRAG rejection numbers are second-hand.** The abstract was read directly; the results
    tables were not. The rejection is safe because it is also a cost argument, but do not quote the
    digits. **partially unverified.**
15. **`voyageai/voyage-4-nano`** is listed in TEI's supported-models documentation and requires
    `trust_remote_code` in its card, and TEI has no such flag. TEI may still load it because it ignores
    `auto_map`. **unverified**, and the most interesting single hour of testing in the embedding angle if
    anyone has it spare.
16. **The office build's single-digit-tokens-per-second figure** is from our own production-readiness
    document, which labels it typical rather than measured. The 15-to-30-second estimates for auxiliary
    generation are arithmetic on that unverified number. **unverified.**

---

## 9. Sources

The nine reports, with the primary source for each decision above.

- **Ingest and chunk:** `07-chunking`. NVIDIA chunking study; Unstructured FinanceBench arXiv 2402.05131v2;
  Qu, Tu & Bao arXiv 2410.13070; Amiri & Bocklitz arXiv 2506.17277v2; Chroma chunking report; Gomm &
  Hulsebos; Docling and docling-core source; Qdrant multitenancy docs.
- **Embedding and serving:** `02-embedding`. Cirillo et al. arXiv 2605.23618; MMTEB arXiv 2502.13595;
  lelabdev benchmark; TEI source and `supported_models.md`.
- **Lexical and identifiers:** `01-lexical`. PostgreSQL `tsrank.c` and `COPYRIGHT`; `pg_trgm`; ParadeDB
  `LICENSE`, `Cargo.toml` and Enterprise docs; VectorChord-BM25 `LICENSE`; Tiger Data `pg_textsearch`
  `LICENSE`; OpenSearch, Elasticsearch, Quickwit, Tantivy, Meilisearch and Typesense licence files.
- **Fusion:** `04-fusion`. Cormack, Clarke & Büttcher SIGIR 2009; Qdrant tuning, candidate-depth and
  reranker studies; Qdrant `qdrant_client/hybrid/fusion.py`; Weaviate hybrid-search docs; Microsoft
  Azure AI Search docs; Hussain & Nielbo arXiv 2605.27220; Weller et al. arXiv 2508.21038; Zhang
  arXiv 2608.07152; BEIR arXiv 2104.08663.
- **Reranking:** `05-reranking`. BEIR Table 2 and Table 3; Luo ASU 2023 §4.3; TEI README and Dockerfile;
  Jina on-prem FAQ and model cards; Rank-R1 arXiv:2503.06034.
- **Query side:** `06-query-side`. SemEval-2026 Task 8 arXiv 2605.12028; Hussain & Nielbo arXiv
  2605.27220; Weller et al. Findings of EACL 2024; Akarsu et al. arXiv 2604.01733; IRCoT ACL 2023;
  RagFlow issue #16832; Anthropic contextual retrieval.
- **Late interaction:** `03-late-interaction`. `qdrant-labs/hybrid-comparisons` result files; Qdrant
  vectors, quantization and FastEmbed docs; TEI cloned at commit `56964585069318d01ddeeec9454a952a66f1d3a2`.
- **Verification:** `08-verification`. RAGTruth; MiniCheck arXiv 2404.10774; HHEM-2.1-Open model card;
  LLM-AggreFact leaderboard; JudgeBench; MT-Bench; Panickssery et al. NeurIPS 2024; Geometry-Calibrated
  Conformal Abstention arXiv 2604.27914; the local experiment artifacts under `/tmp/search-research/scratch/`.
- **Production practice:** `09-products`. Onyx source (`search.py`, `constants.py`, the reranking removal
  migration, `LICENSE`, `backend/ee/LICENSE`); RAGFlow `retrieval.go` and `reranker.go`; Azure semantic
  ranking docs; Bedrock contextual grounding check docs; Glean connector docs; Hebbia engineering post;
  GraphRAG-Bench arXiv 2506.05690 abstract.
- **Evaluation data:** `10-eval-corpora`, and `docs/evaluation-corpora.md` in the repo, which already
  records the three licence tiers and the fact that three earlier fixture recommendations turned out to
  be unusable. The Tier 1 fixtures cover every document shape the C2 acceptance test needs.
