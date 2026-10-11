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
| 1. Ingest and parse | Docling in its own container, on both builds, layout/table/OCR models on CPU | Everything downstream is arithmetic over what the parser produced. A fee schedule read as a token stream produces a confidently cited wrong number, and no reranker or verifier recovers it. Record per-page OCR confidence |
| 2. Chunk | Page-bounded, structure-aware, 384 to 512 tokens, zero to 32 tokens of overlap | The page boundary beats the heading boundary, and every chunk stores a page **range**, never a single page. Never split a table that fits; a table spanning a page break stays one unit, with its rows excluded from the surrounding page chunks |
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
| Rerank 50 to 100 candidates with a cross-encoder | `bge-reranker-v2-m3`, **on by default**, depth set by a latency budget of about five seconds | Anthony's decision, 2026-10-11. It moves the question from "does it earn its CPU" to "what depth fits the budget". The published CPU figures came from other hardware and our own came from a contended laptop, so the depth is measured on ours: `scripts/bench_rerank.py` reports milliseconds per candidate and prints the largest depth that fits. The named fallbacks if no depth fits are a shallower pool, Bedrock's `cohere.rerank-3-5:0` on the cloud build, or a GPU |
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
- **Cutting the reranker permanently.** The office build has a GPU in its roadmap, which changes the
  arithmetic. It is deferred, not deleted.
- **Consolidating onto `pgvector` to remove Qdrant.** Qdrant's filtered-search recall and its quantisation
  are features we would have to rebuild, and the measured filter behaviour is a reason to keep it.

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
   candidates, against the five-second budget, using `scripts/bench_rerank.py` (the research reports
   refer to the same tool as `bench_tei2.py`, which was a scratch script on the researcher's machine and
   is not in the repository); whether the pinned TEI serves a CPU reranker and the warmup settings that turn a ten-minute
   first boot into twenty seconds; local model throughput. All four are hours, and each replaces a
   guess in the plan with a number.
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
- **The reranker's latency on our hardware.** The numbers quoted are from BEIR, not from us. Fifteen
  minutes on the instance settles it, which is why it is Wave 0.
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
3. **Does the office build get a GPU?** It changes the reranker from a flag to a default, and it changes
   the late-interaction calculus.
4. **ParadeDB.** Ask for the Enterprise quote, and decide whether the partial-identifier capability is
   worth the copyleft conversation. If it is, it becomes the lexical arm and the Postgres-core work
   becomes the fallback rather than the plan.
5. **`pg_textsearch` now or later?** It would need a Postgres image we build and a
   `shared_preload_libraries` change. The identifier column makes that a later index swap rather than a
   redesign, which is the argument for doing the identifier work first.
