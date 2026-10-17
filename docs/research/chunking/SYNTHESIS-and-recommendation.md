# Chunking and context: the decision brief

Six independent research reports are in `docs/research/chunking/`, 2,265 lines between them: the measured
evidence, what production systems do, oversized elements and repair, the answer side, an appraisal against this
code, and the 2025–2026 frontier. This file is the synthesis and the recommendation. **No code has been changed.**

## 0. Owner's decision, 2026-10-16

**The builder of the chunker confirmed the diagnosis: this is an implementation gap against our own design, not a
design to keep.** They also answered the eight questions in the defect report, and their distinction is the sentence
to hold onto:

> **Sentence-preferred is right; sentence-only is the bug. Atomic-table-as-citation-unit is right;
> atomic-table-as-unbounded-embedding-unit is the bug.**

**Approved: three repairs and one invariant, not a redesign.**

1. **Move the post-condition from a number to a measurement.** The invariant is not 512. It is: every embedded
   text is measured with the embedding model's own tokenizer, and its count is no greater than
   `min(contract.max_input_length, budget)` minus a margin, on every emission path. The docstring already admits
   512 was never a measured optimum.
2. **Separate the citation unit from the embedding unit.** That conflation is the real design gap. A 40-row fee
   schedule can be one *citable* object and several *embeddable* ones: keep the whole table as the citation
   target, embed bounded text that reliably retrieves it (heading path, column names, row count, the head of the
   table), and let the existing per-row entries do the precise matching.
3. **Turn `auto_truncate` off, or refuse to run while it is true.** A chunker bug is recoverable; a chunker bug
   plus silent truncation is 73 documents that look ingested and are not. The provider's refusal at generation
   time was the last moment the system could shout; the indexed prefixes were the quiet one.

Plus: a hard cut behind the sentence split, the invariant as a test using the two files that broke it,
`CHUNKER_VERSION` to v3, and a re-ingest into a new collection generation.

**What must not weaken:** page ranges on every chunk; zero or near-zero overlap; the deterministic prefix,
heading path and caption; atomic tables as citation units; sentence-preferred splitting.

**The in-flight corpus load is scrapped.** 29,092 points and 29,082 `chunk_index` rows built by a chunker that
truncates its input are a plausible-looking artifact, not a corpus. The ingestion container is stopped, the 341
documents stay on disk untouched, and the index is replaced at chunker-v3. This also settles the migration
question the adversarial review raised: the fix lands as a **new collection generation**, which is the blue/green
path the earlier draft said did not exist.

**What this changes in the brief below.** The answer-path budget stops being the first emergency and becomes a
tripwire: bounded chunks bound the prompt by construction, so the budget is defense in depth rather than the
immediate fix. The off-the-shelf chunker evaluation is no longer a prerequisite — the direction is our own
chunker, repaired. Everything else in the brief that supports the three repairs stands; the parts that assumed a
global re-chunk of the current collection are superseded by the new-generation plan.

## 1. What is actually wrong

Four layers, and only the first one is the chunk size everyone has been looking at.

1. **The estimator is the wrong unit.** `estimate_tokens` in `services/ingestion/chunker.py` is whitespace words
   × 1.35. Measured against Nova Lite on 2026-10-16: prose runs 5.09 bytes per token, a pipe table 1.61, a CSV
   ledger 1.33, OCR'd form text 2.59. The estimator measured 2.8x and 3.2x low on two table samples and about 15x
   low on a 40-row CSV, and on a 756 KB run with no whitespace it returns 1 token, so the ceiling never sees a size
   at all. A 512-token ceiling checked with that estimator is not a ceiling.
2. **There is no post-condition.** Prose is split when it is too big, but `split_oversized` cuts on sentence
   boundaries only, so a 756 KB unbroken run passes through whole. The table path at `chunker.py:496` builds one
   chunk for the whole serialised table and never applies the ceiling at all. A chunk is emitted whatever its
   size.
3. **The answer path has no budget.** `_generate` joins top-k passages with no per-passage or total cap, in two
   places (`app.py:989` for JSON, `app.py:1318` for streaming). `services/retrieval/injection.py` has an
   `assemble()` with delimiters and an injection scan that is tested and never called. A 439 KB table chunk became
   a 228,326-token estimate and a prompt the provider rejected as over 300K. The largest passage retrieved was a
   439,707-character **prose** chunk from `company/group-01/439428.txt`, plus a 186,679-character table chunk; the
   pair alone accounts for the refusal.
4. **The verifier would not notice truncation, and it is a hazard the fix introduces.** `citations.verify_claim`
   checks claims against the full `passage.text`. Today that is correct; once passages are trimmed for the prompt,
   a claim resting on text the model never saw would pass. The change has to move the verifier to the included
   excerpt, not leave it reading the source.
5. **The reranker already slices.** `services/retrieval/rerank.py:120` scores only the first 8,000 characters of a
   candidate. So an oversized chunk is not only embedded as a prefix; it is also *ranked* on a prefix. This was
   missing from the earlier count of layers.

Two facts that make the damage worse than a failed request. TEI's `--auto-truncate` defaults on, so the 756 KB
chunk's vector is the first 4,096 tokens of the text (the contract's recorded limit) and not a representation of
it. Measured on the loaded corpus: **183 chunks over 4 KB, 50 prose chunks and 59 table chunks beyond the
embedding input limit, across 73 of 341 documents.** That is 73 documents, not 21 percent of the chunks: the
failure is concentrated in table- and dump-heavy files, and the counts are a snapshot taken while the load was
still running.

**Defend the post-condition, not the number.** The research does not settle on 512; it says the band is 200 to
1,024 and that there is no universal optimum. What the evidence does support is that every emitted chunk must be
checked after overlap and contextualisation against a hard ceiling tied to the embedding model's input limit. The
number can move; the missing check is the defect.

**The target was never wrong.** 512 tokens is defensible: faithfulness 97.59 at 512 against 94.26 at 1,024 and
80.37 at 2,048 in *Best Practices in RAG* (July 2024); Chroma's 200–400 efficient range; and production defaults
cluster at 256 (Docling), 300 (Bedrock Knowledge Bases), 384 (OpenSearch), 1,024 (Azure On Your Data,
LlamaIndex). The defect is that this system never enforced its own ceiling.

## 2. What the profession does

- **Every production chunker enforces a hard ceiling after overlap and contextualisation.** The failure modes we
  have are known: OpenSearch's `max_chunk_limit` appends overflow to the last chunk, Bedrock's `NONE` strategy
  makes one chunk per document, LangChain's parent retriever with `parent_splitter=None` returns the raw parent.
  Azure's sentence-only Split skill shipped the same 12,684-token chunk from a numeric spreadsheet that our
  sentence splitter produced.
- **Only Docling documents row-boundary splitting with repeated headers.** Unstructured isolates tables and
  text-splits oversized ones. Bedrock, Azure, Google RAG Engine, OpenSearch, Elastic, LangChain, LlamaIndex,
  Chonkie and OpenAI document no table rule, so the 186 KB table chunk is outside every documented production
  behaviour and Docling is the reference to copy.
- **Answer-side budgets are rare, and Azure is the standard.** Azure On Your Data states the rule (chunk size ×
  retrieved documents, default 5 × 1,024 tokens) and documents truncating the question and system message.
  Azure's knowledge base is the only survey system with a request-level output cap that warns when documents were
  omitted. Everyone else relies on top-k defaults and discloses nothing.
- **The measured evidence rejects the fashionable upgrades for this stack.** Fixed-size beat semantic chunking on
  all four non-stitched retrieval sets (Qu et al., NAACL Findings 2025). Zero overlap beat 10–20 percent overlap
  with no quality gain and 25 percent more chunks (January 2026 paired ablation). TableRAG scored whole-table
  prompting 4.6 and 9.1 where schema plus cell retrieval scored 49.2 and 45.5. Late chunking gains 1.5–1.9 nDCG
  but needs token-level mean-pooled embeddings, which TEI's documented API does not expose.

## 3. The recommendation

**Option (c): cap the answer path first, then repair the chunker and re-chunk.** Scored 36 of 45 against 32 for
chunker-only, 35 for answer-path-only and 23 for an off-the-shelf chunker.

The answer-path cap is the small change and it stops the failure today. The chunker repair is the durable one,
because otherwise the corpus still contains chunks that cannot be embedded meaningfully or cited honestly.

### 3a. The answer-path budget

One assembly, used by both prompt builders, replacing the inline construction and wiring `injection.assemble`:

- **Frontier lane:** 6 passages; top 3 expanded to 4,000 bytes, ranks 4–6 capped at 2,000; 18,000 bytes of
  passages; 24,000-byte prompt; 4,000-byte question; 700 output; 1,024 reserve. Worst case 25,724 bytes against a
  300K-token window. **The window is not the budget.**
- **Office lane:** the budget is derived from `MODEL_CTX` and the lane, not fixed at a dev placeholder. The
  research's 3 × 1,600-byte proposal was rejected on review: 1,600 bytes is below the chunker's own 384-token floor
  (1,954 to 2,606 bytes of prose at the measured 5.09 bytes/token), so it would feed the client partial chunks and
  cut office context from up to 3,072 tokens to about 943 on an 8,192 window with room for six. Keep six passages
  with a total byte cap, and measure three against six on the office build before shipping. The 800-byte question
  cap also refuses a pasted contract clause, so it is raised or made configurable.
- **Over budget:** drop whole passages from the lowest rank, window only the top passage, never cut mid-row or
  mid-sentence, and return a degraded payload with `context_over_budget` when nothing fits.
- **Audit:** `context_version`, `prompt_bytes`, per-passage included and source bytes with hashes, and estimate
  versus provider count. No text in the record.
- **The verifier changes with it:** verify against the included excerpt only, add `outside_included_excerpt` and
  `coverage_overclaim`, and put byte offsets in every citation. Without this the trim would make the verifier
  decorative.
- **The client must see what the model saw.** The operator guide promises a citation chip that opens "the text the
  model answered from". After trimming, the chip has to open the **included excerpt**, with the full chunk
  available separately and labelled as not shown to the model. The answer payload therefore carries the excerpt
  offsets, not just doc, label and page, and `outside_included_excerpt` is worded as a coverage note rather than an
  accusation. An audit-only disclosure is not a disclosure to the person reading the answer.

### 3b. The chunker repair

- **A post-condition, not a preference:** every emitted chunk is checked after overlap and contextual
  prefixes, in bytes, with a measured safety factor (the estimator stays as a cheap pre-check only).
- **Fallback order:** page/section boundary, paragraph/line, sentence *only when it actually splits*, clause
  punctuation, whitespace, character window, dead-letter.
- **Tables, two budgets, and one decision left open on purpose:** keep a table whole when it fits; otherwise split
  into **row groups of about 512 tokens**, header repeated on every part, keeping `table_id`, row range and page
  range so the citation stays a range. The row entries the chunker already emits are the natural seed for this. The
  choice between single rows and token-sized row groups is not settled by the evidence cited earlier — TableRAG is
  million-token tables and a full pipeline, and its ReadSchema arm alone scores 43.1 / 40.3 against 49.2 / 45.5, so
  it does not decide our case. Measure single rows against 512-token row groups on this corpus before fixing it.
  Prefer the free caption and heading path to generated summaries, which measured worse.
- **Overlap:** zero at structural boundaries and table rows, 16–32 tokens only at forced prose breaks.
- **Deterministic context before embedding:** title, heading path, page label, table caption and column headers,
  which is the free half of contextual retrieval.
- **Cheap neighbour expansion:** store previous and next chunk ids and expand one neighbour at query time, inside
  the budget. Most of the parent-document benefit without a second copy of the text.

### 3c. The migration, and its cost

**What is actually forced and what is a choice.** The forced part is the embedding contract: `chunker_version` is
one of its fields and the contract is recorded for the whole collection, so the chunker cannot change without the
contract changing. The *repair set* is not forced. The whole-corpus re-chunk is a consequence of bumping
`CHUNKER_VERSION` globally; a targeted repair of the 73 affected documents, with `chunker_version` added to the
manifest key and a generation filter on the read paths, is a separate option that the research reports disagree
about. It is cheaper, and `chunk_index` already has a `chunker_version` column to filter on. **Decide between them
before scheduling anything.** The full path is a three-part act and missing any part wastes the window:
`CHUNKER_VERSION` to v3, `PAYLOAD_VERSION` to v6, and `EMBED_CONTRACT_ALLOW_CHANGE=1` with a reason for the first
run only.

- **Operability, which the first draft got wrong.** The trigger must not be the manual `docker exec` that caused
  the 171-document 429 incident (`RET-INGEST-002`); `INGEST_CONCURRENCY=1` is not in the compose environment, so it
  has to be set deliberately for the window. `write_document` deletes a document's old chunks before writing the
  new ones, so a parse failure during the window removes that document from the index, and after two failures it
  is dead-lettered, drops out of `--status`, and the container still reports healthy. The window therefore needs,
  written down before it starts: a database snapshot; a progress query; `--failures` in the post-checks; a
  documented rollback; and a plain statement to the client of what is unavailable while it runs. "Record the
  counts" is a postmortem aid, not a mitigation.
- **Blue/green is a choice, not an impossibility.** `chunk_index` carries `chunker_version`, so a generation filter
  on the lexical and vector arms would let the old index keep serving while the new one builds. What does not
  exist is a collection-named second vector store; adding a generation column to `chunk_index` and filtering the
  read paths is the work that would buy an atomic cutover. The earlier draft called this impossible and that was
  too strong.
- **Cost:** the full path is 341 documents and roughly 24,000 to 27,000 chunks, 2 to 4.5 hours of wall clock on
  the development corpus; a client's corpus is a different number and the schedule needs its own estimate. The
  targeted path is 73 documents.
- **Read paths:** on the contract bump the read-side check either refuses every search or is bypassed by a process
  cache. That has to be settled before the window, not during it.
- **Afterwards:** re-run `--scan-duplicates` (the signature is `md5(text)` per chunk, and ratios move); record that
  historical answers reference chunk ids from the old generation, so the audit hashes prove what was retrieved
  then, not that today's index still contains it; put `chunker_version` in the manifest key so future chunker
  changes invalidate themselves; and fix `manual.md:2165-2167`, which describes this mechanism backwards (the
  runbook has it right).

## 4. What we are deliberately not doing, and why

The binding constraint is **the audit chain and the access labels, not compute**. The stack can afford a provider
call; it cannot turn every chunk into a copy of the document in the evidence log, or bake a restricted appendix
into an allowed chunk's embedding, or cite a generated summary that has no page.

- **Semantic chunking as the default.** Measured gain is inconsistent, it needs an extra embedding pass, and
  global statistics break incremental ingestion.
- **Late chunking with BGE-M3 through TEI.** The pooling does not match: the stack's `/embed` path returns pooled
  vectors and late chunking needs mean pooling over token spans. A token-level route exists, so this is a custom
  client rather than an impossibility, but the gain is small and task-dependent, and BGE-M3 collapsed in a 2025
  reproduction; a 2026 reproduction found in-document losses of 5 to 53 percent.
- **Contextual retrieval's generated half as a default.** It requires sending the whole document with every
  chunk, which the audit chain would have to store, and which can carry a restricted appendix into an allowed
  chunk's context and embedding. Its advantage over the free deterministic prefix is unmeasured on office
  documents: Anthropic's 35–67 percent retrieval-failure reduction is vendor-run, and the one independent
  comparison measured 0.020 nDCG. Pilot it on 60 questions, with document-level labels, before it becomes
  default.
- **RAPTOR, GraphRAG, unbounded agentic retrieval.** Global summary structures that mix labels by construction,
  have no page ranges, and cost far more than they return at 10 to 200 users. RAPTOR's 20 percent headline is not
  a chunking ablation; the controlled gain is 1.7 points and a 2026 benchmark puts it below plain RAG. GraphRAG
  wins global summarisation, not fact retrieval. Agentic retrieval costs about 2.5 times per correct answer.
- **Long context as a replacement for chunking.** It cannot enforce per-chunk access labels or produce page
  citations, and it costs 100 to 400 times a chunked prompt.
- **An off-the-shelf chunker, until it is actually tested.** The first draft dismissed this on a false claim: the
  comparison harness exists and already covers Docling's `PageChunker` and `HierarchicalChunker`. What was never
  tested is `HybridChunker`, the one that splits oversized chunks and repeats table headers — which is exactly the
  feature we are about to build. **Add `HybridChunker` to the harness before the build-versus-buy decision stands.**
  If it passes, the fix is a smaller change than the plan below.

Could any of these be adopted if we decided to? Contextual retrieval: yes on hardware and licence, blocked by
audit and labels in its current form. Late chunking: yes with a custom token-level client, not worth it for the
gain. ColBERT: reachable with the small Apache-2.0 model, only if provider reranking becomes the problem.
GraphRAG and RAPTOR: reachable but economically irrational at this scale. Long context: reachable but does not
serve the contract.

## 5. What would change this answer

- A 60-question measurement on this corpus showing the deterministic prefix is not enough and the generated
  context is worth the audit volume.
- A provider reranking cost or latency problem that the small ColBERT model solves.
- A client with a measured multi-hop or global-question gap the structure-aware baseline cannot close.
- A TEI path that exposes token-level embeddings cheaper than a custom client, which would reopen late chunking.

## 6. The order of work

1. Record this brief and the six reports; commit them.
2. Answer-path budget and the verifier change, with tests that fail when a passage is trimmed and the verifier
   reads the untrimmed text.
3. Chunker post-condition, fallback order, table policy, deterministic prefixes, neighbour ids, with tests per
   rung of the fallback order.
4. The migration in one single-writer window, with counts recorded before and after.
5. Then the C6 work resumes. The corpus is the reason this was found; keep it alive at every phase exit.
