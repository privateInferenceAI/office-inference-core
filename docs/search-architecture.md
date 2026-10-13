# Retrieval architecture

The decision for C2: what we build, what we do not, and the measurements behind each. Written
2026-10-11 after a research programme of thirteen agents.

**Read this for the decision. Read `docs/research/search-2026-10/` for the evidence.** That directory
holds nine research reports, a synthesis, two adversarial critiques and the final revision. Every
licence claim in them was read from the project's own licence file or model card, with the artifact's
size and hash recorded, because a licence recalled from memory has already been wrong twice on this
project.

## The shape, in one page

Seven stages, and the changes from the build brief are the point of writing it down.

| Stage | What we build | The decision that matters |
|---|---|---|
| 1. Ingest and parse | Docling in its own container, on both builds, layout/table/OCR models on CPU | Everything downstream is arithmetic over what the parser produced. A fee schedule read as a token stream produces a confidently cited wrong number, and no reranker or verifier recovers it. **Per-page OCR confidence is wanted and not yet available**: measured 2026-10-12, the parser returns one confidence report per document, and Docling's own schema is document-level only. See `RET-PARSE-002` |
| 2. Chunk | Page-bounded, structure-aware, **a working ceiling of about 512 tokens** rather than a measured optimum, zero to 32 tokens of overlap and zero preferred | The page boundary beats the heading boundary, and every chunk stores a page **range**, never a single page. Never split a table that fits; a table spanning a page break stays one unit, with its rows excluded from the surrounding page chunks. **A table can also arrive already split**: the parser emits one table per column region, so joining before splitting is part of this stage. **The ceiling is an engineering constraint, not a quality finding**: it keeps a chunk inside the embedding model's input limit and small enough that its embedding matches a question rather than averaging a whole document. It is deliberately **not** justified by a reranking budget: that budget is a local-CPU budget, and the cloud build reranks at Bedrock where a 50-candidate pool costs about a third of a second (measured 2026-10-13). A ceiling of this size is right for the other two reasons and would be right at a larger budget too. The chunking research says there is no universal size and that published optima span 200 to 1,024 tokens, and it calls the page-versus-token accuracy difference noise |
| 3. Contextualise | Deterministic context prepended before embedding: title, full heading path, page label, table caption | The generated half is an experiment, not a default: a four-way ablation decides whether a model adds anything on top |
| 4. Store, embed, govern | `bge-m3` at 1024 dimensions, CLS pooling, no prefix, served by TEI `cpu-1.9`, in Qdrant 1024-dim cosine | A written embedding contract per collection, and a read-path refusal when a query meets a collection built by a different model |
| 5a. Retrieve, dense | Qdrant prefetch, `hnsw_ef` set to hit a recall target and no higher | Recall hits 0.986 at ef=16 and 1.000 at 256, while fused nDCG moves at most 0.0022 for a 49 percent latency rise |
| 5b. Retrieve, lexical | **Postgres native full-text search**, fixed | `tsvector` with `english` for prose and `simple` for identifiers, `setweight`, GIN, `ts_rank` with an **explicit** normalisation argument. Never `ts_rank_cd`, never the default. Plus a regex-extracted identifier column, stored whole and split |
| 6. Fuse | RRF in our own code, `k=60`, equal weights, 500 candidates per arm | Our code, for a deterministic tie-break, so citations do not move between identical runs |
| 7. Verify and refuse | Five layers, the first two deterministic and model-free | The mechanical layers ship **before** the reranker, because they convert our worst failure mode into a refusal |

**The reframe worth carrying:** we thought this was a ranking problem and it is mostly a tokenisation
problem. `INV-2024-04417` shatters in the parser into `inv / -2024 / -04417`, so a user typing the
trailing digits gets nothing, and no BM25 buys that back. The fix is a tokeniser choice plus an
identifier column plus a literal-substring assertion in the verifier. The ranking engines were measured
head to head on a 200,000-chunk corpus, and the licence-clean ones do not beat fixed native Postgres on
our actual failure case.

## What changes from the build brief

| The brief says | We will do | Why |
|---|---|---|
| "Qdrant prefetch plus reciprocal rank fusion" | Fusion of our own, `k=60`, deterministic tie-break | Citation determinism, not a version limit. **The Qdrant pin is `v1.19.2`**, digest `b7b0444c` |
| Rerank 50 to 100 candidates with a cross-encoder | The cloud build reranks 40 to 50 candidates at Bedrock `cohere.rerank-v3-5:0`, with no reranker on the box. The CPU reranker stays as a bench instrument and starts in no build | Measured twice, and the second measurement is the one that matters. On this box, 2026-10-11: roughly 2.5 to 3 ms per token of candidate text, flat in the number of candidates, so a five-second budget buys three or four of our 500-token chunks rather than the 40 to 50 the brief asked for. **At Bedrock, 2026-10-13: a 50-candidate pool at our chunk size returns in about a third of a second**, measured in `docs/runbook.md` step 3.3 with three lengths and three depths. **Depth is not a tuning problem for the cloud build.** The five-second budget that shapes candidate depth is a local-CPU budget and does not apply to us: it appears in this document only as the reason the stage moved. The office build decides its own reranking when it is built |
| Advanced query understanding | A rule-gated rewrite, a corpus-mined acronym glossary, and structured date filters. No HyDE, no multi-query, no decomposition by default | The query-side report labels its own findings as inference, and the latency is ours to pay on a CPU box |
| Hybrid dense and sparse vectors | Hybrid dense plus **Postgres full-text**, with `pg_trgm` reachable for reranking a candidate set | `word_similarity` matched BM25 on trailing-digit queries (189 of 200) but took 13 seconds a query, so it can only rerank. The `%` operator scored 0 of 60: geometrically hopeless against a 1,300-character chunk |
| Sixty golden questions in C8 | A fixture corpus and question set in **C2**, the client's own sixty in C8 | C2's decisions need a yardstick. `RET-HYBRID-001` already assumes one: it verifies hybrid on with the same question off |

## What we are not building, and why

- **Late interaction (ColBERT-style).** It ties the cross-encoder on quality and wins on latency, but it
  needs a third serving component and nothing serves our MIT-licensed candidate. Revisit if FastEmbed
  adds `bge-m3`'s late-interaction head, which we already ship and nothing can serve.
- **A BM25 extension, until the licence question is answered.** ParadeDB is the best-engineered option
  and the only engine that scored 60 of 60 including *partial* identifiers. It is AGPL, which is a
  decision rather than a prohibition — see the licence posture above. Its own measurement is the argument
  for asking: tokeniser choice, not ranking, is what recovered the partials, and that capability is worth
  paying something for.
- **A Lucene sidecar, ever.** A second store that can disagree with Postgres means a reconciliation job,
  a second backup and restore procedure, and a second authentication surface, on a box maintained by one
  person for thirty users.
- **Cutting the reranker, or shrinking it to fit the CPU.** Not dropped and not made smaller. The cloud
  build reranks at Bedrock, where the pool of 40 to 50 is affordable because the silicon is theirs. A
  faster small cross-encoder is the wrong lever: ten times faster still leaves 25 real chunks at 4.5
  seconds, and the accuracy it gives up is the only thing the stage exists to buy. What the office
  build does about reranking is that build's question, on hardware that does not exist yet.
- **Consolidating onto `pgvector` to remove Qdrant.** Qdrant's filtered-search recall and its quantisation
  are features we would have to rebuild, and the measured filter behaviour is a reason to keep it.

## An uncertain read: do it the way the profession does it

Anthony's decision, 2026-10-12, after four research angles: **the standard professional control set, and
not a person looking at every document.** The evidence for it is in `docs/research/`, principally
`ocr-confidence-synthesis.md`, with the four angle reports beside it.

### The failure being answered

OCR misreads a character, so the wrong number is faithful to the document as parsed. Retrieval finds it
because it matches the question. The model cites it. Layer 1 confirms the number is present in the cited
passage, because OCR is what put it there. Every check passes and the client receives a confident wrong
figure with a real citation.

Measured on this system, in `docs/runbook.md` step 3.4: forty trials carving defects into a cell whose
truth is `145.00` produced fifteen wrong readings, and confidence flagged **one**. Seven percent.
Fourteen wrong numbers passed at confidence between 61 and 93, among them `145.60`, `146.00` and
`145.08`. Nine of the fifteen were malformed, a letter in the number or the wrong digit count; six were
plausible numbers with a wrong digit.

### What the research found, and why it settles the design

Nobody solves this by detecting it. No open-source project catches a confidently wrong reading, every
confidence gate in that landscape is a deletion gate below 0.5, and two commercial vendors say in print
that their score is not a probability and should not be read as one. Calibration cannot help, because
rescaling is monotone and therefore cannot make a confidently wrong reading rank as suspicious.

What the trades that need a right number actually do is make the error not matter. A cheque carries its
amount twice and both readings are taken independently, because a defect cannot corrupt both at once.
Accounting reconciles against the bank feed rather than proofreading the invoice. Six of nine commercial
vendors ship an independent constraint that runs whatever the confidence says. Confidence is used to
decide **what a human looks at**, never as evidence that a number is right.

### The controls, in the order they run

1. **Format, range and checksum checks** on every extracted figure. Nearly free, and the citation
   verifier's literal check is already this shape. Honest ceiling: the literature puts only 24.39 percent
   of misreads outside known error patterns, so this catches the malformed quarter and nothing else.
2. **Cross-field arithmetic**, where a document has the relationships to check: line items to subtotal,
   subtotal plus tax to total. This is the invoice world's version of the cheque's two amounts and the
   pattern with the most vendor agreement. A misread digit breaks the equation.
3. **A second independent reading of any figure that gets cited**, as an escalation gate and never as a
   vote. Design constraint from the research: vary the **stage** as well as the engine, because two
   engines over the same preprocessed pixels make correlated errors and will agree on the wrong answer.
   Disagreement asks for a person; it never picks a winner, because in the one measured case 26.8 percent
   of disagreements were both-wrong and the ensemble recovered under 57 percent of them.
4. **Human review of what the controls flag**, on the screen the capture industry has shipped for years:
   one field at a time, the source fragment beside the proposed value, keyboard driven, ordered by risk.
   Risk means a low score, a high value, or a failed check.
5. **Record the override**: machine-proposed value against human-accepted value, who and when. This is
   how the real error rate becomes knowable rather than estimated.
6. **Disclose what passed nothing.** Where a figure came off a picture, passed no arithmetic check and had
   no second source, the citation says it is machine-read. That is the audit-and-sampling pattern
   translated to a business with one person doing the checking, and it is the honest answer to the
   residual rather than a pretence that it was verified.

### What this is not

Not a refusal. A refusal costs the client an answer they could have had, and it fires on correct answers
too, because a badly scanned correct number is indistinguishable from a badly scanned wrong one.

Not a review of every document. Every page is read without a person; most documents get no review at all,
because most numbers are not flawed. What arrives at the client is a short list of figures to confirm,
not a stack of paper.

Not a confident claim of correctness. `145.60` from a clean scan of a cell with no totals to check it
against is not catchable by any mechanism found in the research, and the design says so in the citation
rather than covering for it.

### Two traps the research names

**Do not tune a threshold on the same set the error rate is reported from.** Score-refit leakage made a
reported bound wrong in 95 percent of splits in the one study that diagnosed it, and document clustering
gives an effective sample much smaller than the field count. Split fitting from reporting, and sample per
document rather than per field.

**Do not swap the pipeline to a vision-language model on current evidence.** The comparison that would
justify it, a VLM against Tesseract on genuinely damaged scans of printed numbers, does not appear to
exist; the closest study disclaims simulating real degradation. And fabrication is worse than a misread
digit, because it carries neither a confidence penalty nor a format-check penalty.

### What is still open

- **Whether two independently staged engines disagree usefully on these documents.** The only measurement
  found was on synthetic Telugu characters, so the number for damaged printed English digits does not
  exist. Until it does, the second reading is a design intention rather than a costed feature.
- **How large the review queue actually is.** It decides whether the queue is a database query or a
  product surface with triage and bulk actions. Anthony's position, same day: this is tunable as we go,
  and the first question is how many documents come out needing a person.
- **Whether the client's own records can serve as the second source.** The bank feed is the control that
  dominates accounting practice and this system is connected to nothing. That is later work.
- **Per-page or per-line confidence for triage**, which Docling computes internally and does not put on
  its wire schema. `RET-PARSE-002` carries it. Triage is a weaker job than detection, so this is no
  longer urgent, but it is still the signal that decides what reaches a human.

### Two limits, recorded rather than discovered later

**Confidence cannot see a page that was never scanned.** No image means no recognition and no score, so a
misfeed or a missing page is invisible to every control above and has no check anywhere in this design.
Completeness is a separate gap.

**A confirmation has to be stored**, or the same page is flagged on every question and the client learns
to ignore the flag.

## The reranker runs on someone else's silicon, so it carries two rules

**The data-classification gate.** On the cloud build the reranked passages leave the box, because the
rerank call is a frontier call. Only chunks whose data class permits a frontier call may be sent. When
every candidate for a query is regulated without permission, the reranker is skipped: the fused order
is used as it stands, and the audit row says that no reranker ran. Degrading is acceptable. Recording
a rerank that did not happen is not, because the record is what a client would be shown.

**Degradation, not failure.** Throttling, a Region where the model is unavailable, or any other
transport failure falls back to the fused order and flags it in the audit row. Reranking is an ordering
improvement, so losing it must cost quality and never availability.

Both rules follow from one property: the reranker orders candidates that retrieval already found, so
there is always a valid list to fall back to. Nothing here can lose a document the retriever found.


## Licence posture

**Shippable as-is:** PostgreSQL and `pg_trgm`, Qdrant (Apache-2.0), TEI (Apache-2.0), Docling and its
components (MIT), the OCR stack (Apache-2.0, MPL-2.0), `bge-m3` (MIT), `bge-reranker-v2-m3` (Apache-2.0),
MiniCheck-Flan-T5-Large (MIT), Qwen3-4B and Qwen3-1.7B (Apache-2.0), Phi-4-mini (MIT), and Tiger Data's
`pg_textsearch` (PostgreSQL Licence).

**Flagged, weak artifact.** Almost none of these model repositories contains a `LICENSE` file; the
licence is a README field. At weight-freeze, archive the model card, the frontmatter and the commit hash
for every model we ship, so counsel gets a document rather than a metadata string.

**Do not ship without a decision:** Elasticsearch (triple-licensed, all branches disqualifying),
VectorChord-BM25 (dual AGPL or Elastic Licence v2, and it failed on identifiers anyway), Meilisearch
Enterprise (BUSL-1.1, non-production only), Mistral and Gemma and Llama research or gated terms,
`Qwen2.5-3B` (research licence, unlike the Apache-2.0 Qwen3 models), and every Jina embedding, reranker
and ColBERT model (CC-BY-NC-4.0, no published price).

**Newly flagged 2026-10-12, and all three reachable through Docling's own presets.** That is the
mechanism worth noting rather than the individual cases: a preset list is how an unresolved licence
enters this stack without anyone choosing it. **MinerU**, through the `mineru2_pro` preset, has a
weights card that says `agpl-3.0` and a repository `LICENSE.md` that says Apache-2.0 with a commercial
threshold above 100M monthly active users or USD 20M revenue. Unresolved, and this project rejects AGPL,
so treat it as not cleared until the maintainers confirm in writing. **Nanonets-OCR-s and OCR2**,
through the `nanonets_ocr2` preset, declare no licence on the model card, ship no `LICENSE` file, and
the organisation's repository reports no licence at all: treat as all-rights-reserved. And Docling's
**`deepseek_ocr` preset is documented as API-only**, with Transformers and MLX both unsupported, which
an air-gapped build cannot use at all. The preset list should be pinned rather than left open.

**Corrected: copyleft is a decision, not a prohibition.** An earlier version of this document listed
ParadeDB under "do not ship" on the grounds that AGPL is disqualifying. That was wrong, and it was my
rule rather than a legal conclusion. AGPL-3.0 permits commercial use and redistribution. The obligations
are to pass on the licence, make the source available and preserve notices, and the network clause only
engages if we *modify* it and let users interact with the modified version over a network — which we
would not. Our application talks SQL over a socket, which is the separate-process case.

What actually stands in the way, in order of how much it matters:

1. **Client procurement.** A bank or a health system with a no-copyleft rule refuses it regardless of how
   clean our compliance is. That is a commercial constraint, not a legal one.
2. **Obligations that live in the install.** We would ship ParadeDB's source or a written offer with the
   appliance and preserve notices, on an air-gapped box, as a maintained artifact.
3. **Cost.** 1.1 GB image against 304 MB for plain Postgres, and one ParadeDB index per relation, which
   makes the tokeniser one global decision per table.

The reason this matters enough to write down: **ParadeDB was the only engine measured to score 60 of 60
on partial identifiers, at 3.8 ms.** Our Postgres-core workaround matched BM25 on full identifiers and
needed `word_similarity` for partials, which took 13 seconds a query and can only rerank a small
candidate set. For a client whose documents are full of invoice or part numbers, that gap is the product.

**Anthony is asking for the quote, and the work does not wait for it.** The lexical arm sits behind one
interface with two implementations: ParadeDB where a client's policy allows copyleft, and the
Postgres-core arm (fixed `ts_rank`, an identifier column, `pg_trgm` for reranking a candidate set) where
it does not. **The Postgres-core implementation gets built first**, because it depends on nothing outside
core Postgres, and ParadeDB becomes an upgrade behind the same interface when the number arrives. That
also moves the copyleft question from once per release to once per client, which is where it belongs.

**Two actions.** Ask ParadeDB for an Enterprise quote, because a priced licence ends the question. And ask
a lawyer one bounded question rather than a general one: *we redistribute an unmodified ParadeDB
Community image inside appliances we sell, our application communicates over a SQL socket, what do we owe,
and does it change if we build our own Postgres image with the extension compiled in?*

**The structural warning to carry forward:** the late-interaction and listwise-reranking frontier is
going non-commercial — `jina-colbert-v2`, `jina-reranker-v3` and `Reason-ModernColBERT` are all
CC-BY-NC-4.0. Do not architect around a model whose licence we would have to negotiate. The rule: **a
licence whose price we cannot see is not a licence we can ship on.**

## Measuring it, with sixty questions

The set is stratified, fixed before the first run, and hashed into the manifest: single-lookup factual,
conversational follow-ups with pronouns, identifier and code lookups, date and version constraints,
table-cell and page-straddle questions, and a refusal group.

**The arithmetic that changes how we use it.** At n=60, one question is 1.67 points, and the 95 percent
interval around 90 percent is roughly 82 to 98. The brief's 92 percent gate sits inside that noise band.
So: report counts, compare paired runs on the same questions, use the categories to see where it broke,
and treat fewer than about eight questions switching sides as nothing. The hard-fail list from the brief
is different in kind: an access-control leak, an unredacted frontier call, an invented answer, a numeric
mismatch, or a successful injection is pass or fail regardless of averages, and each gets a mechanism
check rather than a percentage.

**One axis to settle.** The brief splits the sixty as forty general and twenty client-specific; the
research splits them as forty answerable and twenty must-refuse. Those are different axes, and both are
worth having. The proposal is a two-by-two: roughly 27 general answerable, 13 client-specific answerable,
13 general must-refuse, 7 client-specific must-refuse.

## Build order

Accuracy-critical first, and **measurements before code**. The full ordering is in
`docs/research/search-2026-10/final-architecture.md` section 5; the waves are:

0. **Measurements that gate decisions**: the reranker's real latency on our instance at 25, 50 and 100
   candidates, against the five-second budget, using `scripts/bench_rerank.py`. **Read the framing of this
   wave with care**: it measures the box in front of us, and the box in front of us is not the one that
   serves the cloud build. It answered "can a local reranker serve this at all" (no) rather than "how deep
   can the cloud build go" (a question for `scripts/bench_bedrock_rerank.py`, measured 2026-10-13: about a
   third of a second for 50 candidates at our chunk size) (the research reports
   refer to the same tool as `bench_tei2.py`, which was a scratch script on the researcher's machine and
   is not in the repository); whether the pinned TEI serves a CPU reranker and the warmup settings that turn a ten-minute
   first boot into twenty seconds; local model throughput. All four are hours, and each replaces a
   guess in the plan with a number. **The reranker measurement is done and the answer is in
   `docs/runbook.md` step 3.3**: about 2.5 to 3 ms per token of candidate text on this box, so three or
   four of our chunks inside five seconds. That is why the stage moved to a GPU or to Bedrock rather
   than getting a shallower pool.
1. **The spine**: parse with page and table provenance, page-bounded chunking, the golden set, the
   embedding contract, dense retrieval. The gate is a scanned multi-column fee schedule reading back with
   the right numbers on the right page.
2. **The lexical arm**, in the same wave as dense, because hybrid is one component.
3. **Fusion and access control**, with the ACL regression test shipped *before* the feature.
4. **Verification**, which is the product, before the reranker.
5. **Experiments that must earn their place**: the reranker, the contextualisation ablation, fielded search.

## What is still unverified

- **`bge-m3`'s CPU throughput.** The 28-hours-for-50k-pages figure is arithmetic, not measurement, and it
  could be wrong by three times either way. It gates any client-facing throughput or pricing promise.
- **Whether a confidence figure can be had per line.** `RET-PARSE-002`. The review design depends on
  it, and the cheapest route is the OCR engine the container already carries rather than a different
  parser. Worth one measurement before it shapes the ingestion record.
- **Whether reranking improves retrieval on our documents.** The latency question is settled for both
  configurations and recorded in `docs/runbook.md` step 3.3: a local reranker cannot serve this pipeline,
  and Bedrock returns a 50-candidate pool in about a third of a second. What the latency number cannot say is whether reordering the
  fused list actually moves a citation onto the right page, which is the only reason to pay for the
  stage. That belongs to the golden set, per build, before the rerank path is wired.
- **Bedrock's rerank latency and cost from this box.** The API shape is verified and the model is
  ACTIVE in `us-east-1`, but no call has been made from the instance. The permission is not granted
  yet either, so the first live call is also the test of the prerequisite.
- **MiniCheck's false-negative rate.** The headline figure came from 24 of 150 pairs and the report says
  to treat it as a direction. It belongs in the engineering record, not in a client document.
- **Whether hybrid closes the dense-model gap.** `multilingual-e5-large` beats `bge-m3` by 0.109 nDCG@10
  on BEIR, losing on all four subsets. If hybrid, reranking and chunking close that on our documents, the
  embedding question is moot; if they do not, we should know before a client does.
- **Whether Tiger Data's `pg_textsearch` is mature enough.** PostgreSQL-licensed and architecturally
  right, six months old. Revisit at the office-tier build.
- **What ParadeDB charges.** One email, and it is the only thing standing between us and the best
  tokeniser in the comparison.

## Decisions for Anthony

1. **The sixty-question split**: adopt the two-by-two above, or keep the brief's axis alone?
2. **Ask ParadeDB for a price?** I would. The blocker is purely commercial, and knowing the number
   changes the next architecture review.
3. **Does the office build get a GPU?** Open, and deliberately so. It is that build's decision, taken
   when that build starts on hardware that does not exist yet. What is settled is narrower: a CPU
   reranker cannot serve it at our chunk size.
4. **ParadeDB.** Ask for the Enterprise quote, and decide whether the partial-identifier capability is
   worth the copyleft conversation. If it is, it becomes the lexical arm and the Postgres-core work
   becomes the fallback rather than the plan.
5. **`pg_textsearch` now or later?** It would need a Postgres image we build and a
   `shared_preload_libraries` change. The identifier column makes that a later index swap rather than a
   redesign, which is the argument for doing the identifier work first.
