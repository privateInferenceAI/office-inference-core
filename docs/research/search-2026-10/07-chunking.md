# Chunking, document structure, and page-accurate citations

Research report for the Office Inference retrieval architecture decision.
Angle 07. Written 2026-10-11.

---

## How to read this report

Every claim carries one of four labels.

- **MEASURED**: a number I read in a paper, benchmark, or disclosed method, with the URL given.
- **VENDOR CLAIM**: a vendor's own statement about its own product or its own benchmark. NVIDIA's study below is vendor-run but discloses its method, so it is labelled *vendor-run measurement*.
- **INFERENCE**: my reasoning from other people's numbers. Arithmetic is mine and may be wrong.
- **UNVERIFIED**: I could not confirm it. Do not build on it.

Where a source has a small sample, I say so. Several sources here have sample sizes in the same range as our 60 golden questions, which cuts both ways: the findings are directly relevant, and none of them are statistically strong.

---

## 0. The recommendation in one page

1. **Chunk on document structure, not on token counts.** Headings, sections, and page boundaries. Let oversized tables and elements split only when they must. This is what Docling's chunkers already do and what the strongest recent study found competitive.
2. **Do not split tables.** Keep a table whole as one retrievable unit when it fits, and repeat the header row on every piece when it does not. This is measured practice at NVIDIA and it is built into Docling as a flag.
3. **Use low overlap.** The largest controlled chunking study I found, 1,820 configurations, found overlap "generally neutral to negative". The common 10 to 20 percent default is not supported.
4. **Size for 384 to 512 tokens, not 256.** 128-token chunks clearly underperform. Gains level off in the medium-to-large range.
5. **Prefer page-level or page-bounded chunks where the documents are paginated**, because page-level chunking measured highest average accuracy and, more importantly, the lowest variance across datasets. It also makes page citation exact rather than ambiguous.
6. **Do contextual retrieval, but do the free half first.** Prepend the deterministic context (title, heading path, page, table caption) before embedding. Anthropic's own ablations suggest the gain comes from chunk-specific disambiguation, not from adding words. Then measure whether a local LLM call adds anything on top, because on CPU it is expensive and nobody has published a quality evaluation of small-model-generated chunk context.
7. **Use Anchored chunk IDs and keep access control, versioning, and retention entirely in the vector store payload**, never in the embedding. That is what makes all three changeable without re-ingesting.
8. **Two licence traps to avoid now**: PyMuPDF and pymupdf4llm are AGPL-3.0, and Ministral-8B is under a research-only licence. Both are easy to pull in by accident. Details in section 6.

---

## 1. Chunking strategies: what is actually measured

### 1.1 The uncomfortable headline

Across the most rigorous studies I could find, **the embedding model matters more than the chunking strategy**, and the choice of chunking strategy matters less than the volume of blog posts about it suggests.

The strongest statement is from a 2026 study, "Chunk Twice, Embed Once", which built a controlled grid over 1,820 experimental configurations from 952 question-answer pairs across 151 chemistry papers. Its finding, quoted directly:

> "Across the evaluated configurations, differences among embedding models are larger than differences among chunking strategies."

- Source: arXiv 2506.17277v2, https://arxiv.org/html/2506.17277v2 (retrieved 2026-10-11). **MEASURED**, single domain (chemistry), 952 QA pairs.

A second study reaches the same place from the other direction. Chroma's chunking report concluded that a well-parameterised heuristic splitter is hard to beat:

> "We find that the heuristic `RecursiveCharacterTextSplitter` with chunk size 200 and no overlap performs well. While it does not achieve the best result, it is consistently high performing across all evaluation metrics."

- Source: Smith & Troynikov, "Evaluating Chunking Strategies for Retrieval", Chroma technical report, 3 July 2024, https://www.trychroma.com/research/evaluating-chunking (retrieved 2026-10-11). **MEASURED**.

This has a direct consequence for us. We have 60 golden questions per client. We cannot resolve a two-point difference between chunking strategies with 60 questions, and neither can anyone else. So the architecture should pick the strategy that is *robust and cheap to reason about*, then spend the evaluation budget on the things that move the needle: parser quality, retrieval fusion, reranking, and the citation verifier.

That is not an argument for ignoring chunking. It is an argument for not over-tuning it.

### 1.2 Fixed-size with overlap

**What it is.** Split every N tokens with M tokens of overlap. The default in most frameworks.

**Measured performance.** Chroma's table, using OpenAI's `text-embedding-3-large`, top-5 retrieved chunks, mean over 5 corpora (10,444 to 26,649 tokens each) and ~474 generated queries:

| Strategy | Size | Overlap | Recall | IoU |
|---|---|---|---|---|
| Recursive | 800 (~661) | 400 | 85.4 ± 34.9 | 1.5 ± 1.3 |
| TokenText | 800 | 400 | 87.9 ± 31.7 | 1.4 ± 1.1 |
| Recursive | 400 (~312) | 200 | 88.1 ± 31.6 | 3.3 ± 2.7 |
| Recursive | 400 (~276) | 0 | 89.5 ± 29.7 | 3.6 ± 3.2 |
| Recursive | 200 (~137) | 0 | 88.1 ± 30.1 | 6.9 ± 5.6 |
| TokenText | 200 | 0 | 87.0 ± 30.8 | 5.1 ± 4.1 |

Two things stand out. Recall is flat-to-improving as chunks get *smaller*, and every overlap setting *reduces* the IoU score, which measures how much of what you retrieved was actually relevant. The report says this plainly: "reducing chunk overlap improves IoU scores, as this metric penalizes redundant information."

Chroma also notes that OpenAI's Assistants default of 800-token chunks with 400-token overlap "results in slightly below-average recall and the lowest scores across all other metrics". **MEASURED**.

**On overlap specifically**, the "Chunk Twice, Embed Once" study is more decisive. It swept overlap at 0, 16, 32, 48, and 64 tokens and concluded: "The effect of overlap is generally neutral to negative, with at most small gains in a limited subset of configurations... large overlap is rarely associated with the highest values in the evaluated grid." Every configuration in its top 1 percent used overlap of at most 32 tokens. **MEASURED**.

Where does the 10 to 20 percent overlap rule come from? NVIDIA's study says it tested 10, 15, and 20 percent and found 15 percent best on one dataset, and then notes honestly: "While not a full-grid search, this result aligns with the 10–20% overlap commonly seen in industry practices." **VENDOR-RUN MEASUREMENT**, single dataset, https://developer.nvidia.com/blog/finding-the-best-chunking-strategy-for-accurate-ai-responses/ (retrieved 2026-10-11).

**My read (INFERENCE).** Overlap is a hedge against a bad chunk boundary. If your boundaries come from document structure rather than a token counter, you have less to hedge against, so you need less overlap. Overlap also costs storage, doubles the redundancy the reranker has to sort through, and makes the citation ambiguous, because two chunks claim the same sentence. For a system whose selling point is a verifiable citation, overlap is a liability.

### 1.3 Structure-aware chunking by headings and sections

**What it is.** Use the document's own organisation: split at headings, keep sections intact, keep list items together, keep tables whole. Fall back to size-based splitting only when a single structural element is too big.

**Measured performance.** The Unstructured paper is the one to read here, because it isolates structural chunking against token chunking on the same corpus with the same embedder, and it reports the chunk count as well as the accuracy.

Setup: FinanceBench, 80 SEC reports, 141 questions, 11,787 pages, Weaviate, `multi-qa-mpnet-base-dot-v1`, top-10 retrieved, GPT-4 as generator.

| Chunking strategy | Total chunks | Page accuracy | ROUGE | BLEU |
|---|---|---|---|---|
| Base 128 tokens | 64,058 | 72.34 | 0.383 | 0.181 |
| Base 256 tokens | 32,051 | 73.05 | 0.433 | 0.231 |
| Base 512 tokens | 16,046 | 68.09 | 0.455 | 0.250 |
| Element-based (Chipper) | 20,843 | 67.38 | 0.514 | 0.400 |
| Aggregation of both | 112,155 | 83.69 | 0.536 | 0.277 |
| Element-based aggregation | 62,529 | 84.40 | 0.568 | 0.452 |

Answer accuracy (manual, 141 questions): Base 512 scored 48.23, element-based scored 53.19.

- Source: Jimeno Yepes et al., "Financial Report Chunking for Effective Retrieval Augmented Generation", Unstructured Technologies, arXiv 2402.05131v2, https://arxiv.org/html/2402.05131v2 (retrieved 2026-10-11). **MEASURED**, 141 questions, one domain.

Read the table carefully, because the naive summary is wrong. Element-based chunking did **not** win on page-level retrieval accuracy. It lost to plain 256-token chunks, 67.38 against 73.05. What it won on was paragraph-level retrieval quality (BLEU 0.400 against 0.250) and end-to-end answer accuracy (53.19 against 48.23), and it did so with 20,843 chunks rather than 32,051.

That is the honest result: structural chunking buys answer quality per retrieved chunk, not raw page-hit rate. For us that is the right trade, because we rerank and because a chunk that answers the question is worth more than a chunk on the right page.

The paper also gives the practical recipe it used, which is worth copying:

- If an element's text is shorter than 2,048 characters, merge it with the next element.
- Keep merging until the target length is reached, without breaking an element.
- A title element starts a new chunk.
- A table element starts a new chunk and the whole table is preserved.

Note the second finding in that table: aggregating several chunking strategies at query time gave the best retrieval numbers of all (84.40 page accuracy) but blew the generator's token limit. Averaging strategies is not free.

**Production convergence.** Docling, the parser already chosen in `docs/build-plan.md`, ships this as its default path. Its `HybridChunker` "starts from the result of the hierarchical chunker" and only splits chunks that are oversized and merges chunks that are undersized with the same headings. Its `HierarchicalChunker` "uses the document structure information from the `DoclingDocument` to create one chunk for each individual detected document element... It also takes care of attaching all relevant document metadata, including headers and captions."

- Source: Docling documentation, "Chunking", https://docling-project.github.io/docling/concepts/chunking/ (retrieved 2026-10-11). **VENDOR CLAIM** about mechanism, verified against source code where noted below.

### 1.4 Semantic chunking by embedding similarity

**What it is.** Embed sentences or small windows, find the points where consecutive similarity drops, cut there.

**Measured performance, and it is not good.** The best-controlled study is Qu, Tu & Bao, "Is Semantic Chunking Worth the Computational Cost?", Vectara and University of Wisconsin-Madison, published in NAACL 2025 Findings. Abstract, quoted directly:

> "The results show that the computational costs associated with semantic chunking are not justified by consistent performance gains. These findings challenge the previous assumptions about semantic chunking... On non-synthetic datasets that better reflect real-world documents, fixed-size chunking often performed better."

Their evidence-retrieval results (F1@5, best hyperparameters per chunker) put fixed-size ahead on 3 of 5 datasets, with differences inside a point or two:

| Dataset | Fixed-size | Breakpoint | Clustering |
|---|---|---|---|
| ExpertQA | 47.11 | 47.08 | 46.87 |
| DelucionQA | 43.05 | 43.24 | 43.36 |
| TechQA | 28.98 | 28.49 | 27.96 |
| ConditionalQA | 18.23 | 19.83 | 19.14 |
| Qasper | 8.66 | 8.16 | 8.50 |

Answer generation, measured by BERTScore, is a near-tie across all five datasets.

Their explanation is worth internalising: "the top-k retrieved chunks frequently contained the same evidence sentences, explaining the minimal performance differences... the performance of the chunkers largely depends on how effectively the embedding models capture the semantic richness of individual sentences, rather than the chunking strategy itself."

- Source: arXiv 2410.13070, HTML at https://arxiv.org/html/2410.13070v1 (retrieved 2026-10-11). **MEASURED**.

**Counter-evidence, and it is genuinely mixed.** Chroma found their `ClusterSemanticChunker` at 200 tokens achieved the best precision and IoU in their evaluation (8.0 ± 6.0 precision, 34.0 ± 19.7 Precision_Ω), and the LLM-based chunker achieved the highest recall (0.919). So semantic chunking is not worthless. It is expensive and inconsistent, and Chroma's own limitations section notes that the work "does not take into account the time required to run these chunking methods, which can vary from almost instantaneous to tens of minutes".

**Verdict for us (INFERENCE).** Reject semantic chunking as the default. It costs an embedding pass over every sentence at ingest, it needs a global view of the corpus that fights incremental re-ingestion, and it buys a point or two at best. The `ClusterSemanticChunker` has a limitation Chroma states outright that is fatal for our incremental design: "the globally optimal packing relies on the global statistics of the corpus, requiring chunks to be re-computed as data is added."

### 1.5 Sentence-window and parent-document retrieval (small-to-big)

**What it is.** Embed something small for precision, hand the generator something large for context. Three variants:

- **Sentence-window**: embed individual sentences, store a window of neighbouring sentences with each, substitute the window at generation time.
- **Parent-document**: embed child chunks, store a pointer to the parent, fetch the parent at generation time.
- **Auto-merging**: a chunk hierarchy where, if enough siblings of a parent are retrieved, the parent replaces them.

**Mechanism, verified.** LlamaIndex documents `SentenceWindowNodeParser` (single sentence per node plus a surrounding window) and `AutoMergingRetriever` (a hierarchy of chunks linked to parents, with merging above a threshold). LangChain documents the equivalent as `ParentDocumentRetriever`. The LlamaIndex AutoMergingRetriever documentation page contains an "Evaluation" section claiming a quantitative comparison against a baseline retriever.

- Sources: https://developers.llamaindex.ai/python/framework/integrations/retrievers/auto_merging_retriever/ (retrieved 2026-10-11). **I could not retrieve the evaluation numbers**; the page body did not render past navigation in my fetch, and the public notebook URL I tried returned a 14-byte placeholder rather than content. **UNVERIFIED**. Anyone citing a specific number from that notebook is citing something I could not read.

**Why I still recommend the pattern (INFERENCE, strongly reasoned).** It is the right shape for our two hard requirements at once.

- Page-accurate citation wants *small* retrieval units, because a small unit has a definite page.
- Answer quality wants the *surrounding* structure, because a single sentence often does not answer anything.
- Decoupling the two means the citation points at the small unit's page and bounding box while the generator sees the parent.

It also has a property the other strategies lack: **you can change the window size or the parent size at query time without re-embedding anything**, because the small unit's embedding is unchanged. That is a large operational win given requirement 4 in our brief.

The cost is real. You store the small unit, the parent, and the mapping, so the index grows. Every retrieved hit is expanded, which means the generator's context fills with overlapping text and the token bill rises. And the citation can become ambiguous if the parent spans pages, which is exactly the problem section 3.3 addresses.

**Chroma's related finding supports the small-unit half.** Their `Cluster` chunker at 200 tokens had the best precision and IoU but below-average recall, and they speculate why: "recall could reach a maximum before relevant information is diluted within chunks, making it more difficult to retrieve, while chunks which are too small fail to capture necessary context within a single unit." That is a description of the problem small-to-big solves.

### 1.6 Summary indexing

**What it is.** Generate a summary per document or per section, embed the summaries as an additional index, retrieve summaries first and then drill in. RAPTOR (arXiv 2401.18059) is the recursive-tree version.

**The most relevant evidence is negative, and it is from Anthropic.** In the contextual retrieval post they write:

> "Other proposals include: adding generic document summaries to chunks (we experimented and saw very limited gains), hypothetical document embedding, and summary-based indexing (we evaluated and saw low performance)."

- Source: https://www.anthropic.com/engineering/contextual-retrieval (retrieved 2026-10-11). **MEASURED**, but they report no numbers for this ablation, only the conclusion.

**Independent measured evidence for the summary variant of tables.** The Unstructured paper tested three metadata variants on the same element-based chunks. A GPT-4-generated summary of each chunk scored *worse* than a naive prefix on both retrieval and answer accuracy:

| Metadata variant | Page accuracy | BLEU | Manual answer accuracy |
|---|---|---|---|
| Keywords (up to 6, GPT-4) | 46.10 | 0.315 | 53.19 |
| Summary paragraph (GPT-4) | 62.41 | 0.350 | 51.77 |
| Naive prefix (first 2 sentences) + table description | 67.38 | 0.400 | 53.19 |

That is a direct, controlled result on our exact problem: a generated summary of a chunk is a *worse* retrieval aid than a cheap deterministic prefix. **MEASURED**, 141 questions.

**Verdict (INFERENCE).** Reject summary indexing as the primary retrieval path. RAPTOR's tree needs an LLM pass over the whole corpus, the tree is corpus-global so incremental ingestion is awkward, and the two independent measurements above say the simpler prefix beats it. If we want a document-level entry point for "what is this contract about" questions, a document title and a first-page extract are cheaper and, on the evidence, better.

### 1.7 Size trade-offs at 256, 512, and 1024

Pulling the measured evidence together:

| Source | What it found about size |
|---|---|
| Chroma (2024) | Recall flat or slightly better as chunks shrink from 800 to 200. Precision/IoU improves roughly 4x from 800 to 200 tokens. 200 with no overlap was the consistent heuristic winner. |
| Unstructured / FinanceBench (2024) | Page-level retrieval accuracy: 128 → 72.34, 256 → 73.05, 512 → 68.09. Non-monotonic. Paragraph retrieval and answer accuracy were best at 512 among the baselines (BLEU 0.250, accuracy 48.23). |
| "Chunk Twice, Embed Once" (2026) | "Performance generally improves as chunk size increases from 128 tokens toward medium-to-large chunk sizes, after which the gains tend to level off. Small chunks, especially 128-token chunks, underperform across most overlap settings." Top 1 percent used 384 to 512 tokens. |
| NVIDIA (2025) | "Extreme chunk sizes of 128 and 2,048 tokens generally underperformed medium-sized chunks." Optimal varied by dataset: FinanceBench best at 1,024, Earnings at 512. |

The sources disagree, which is itself the finding. Unstructured saw 512 *worse* than 256 for page retrieval. Chroma saw smaller is better for precision. The 2026 study saw 128 clearly bad and 384 to 512 best. NVIDIA saw the optimum move between 512 and 1,024 depending on the dataset.

**There is no universal chunk size, and the published range of optima spans 200 to 1,024 tokens.** Anyone who tells you 512 with 50 overlap is the answer is repeating a default, not a finding.

**What I would actually do (INFERENCE).**

- **Target 384 to 512 tokens for prose chunks.** It sits in the middle of every published optimum except Chroma's, and it is large enough to hold a paragraph plus its heading.
- **Do not go below 256 except where structure forces it.** The 128-token result is the most consistent negative finding across three independent studies.
- **Allow chunks above 512 when the structure demands it**, specifically for tables and for headed sections that would otherwise be cut mid-argument. The 512 ceiling is a tokenizer convenience, not a retrieval fact.
- **Treat 1024 as a ceiling for an entire table, not a target for prose.** NVIDIA's FinanceBench optimum at 1,024 is a financial-document result where the semantic unit really is about a thousand tokens.

### 1.8 The small-sample problem, stated plainly

Our evaluation is 60 questions per client, 40 general and 20 client-specific.

For comparison, here is what the studies above rested on:

| Source | Sample size |
|---|---|
| Chroma chunking report | ~474 generated queries over 5 small corpora (7,727 to 26,649 tokens) |
| Unstructured FinanceBench | 141 questions |
| Qu et al. semantic chunking | 100 queries per dataset for document retrieval; RAGBench evidence sets |
| Anthropic contextual retrieval cookbook | 248 queries |
| "Chunk Twice, Embed Once" | 952 QA pairs, 1,820 configurations |
| NVIDIA | 991 + 600 + FinanceBench + KG-RAG + RAGBattlePacket questions |

Only the 2026 study and NVIDIA are operating at a scale where a two-point difference is meaningful, and NVIDIA's own headline gap (page-level 0.648 against best token 0.645) is *not* meaningful. What is meaningful in NVIDIA's result is the standard deviation: 0.107 for page-level against visibly wider bars for the others.

**Consequence for our golden set.** With 60 questions, the 95 percent confidence interval on a proportion is roughly ±12 points at the 50 percent mark and ±7 points at the 90 percent mark. Do not build a release gate on a chunking A/B test. The gates in `docs/build-brief.md` section 10 (92 percent retrieval, 85 percent support) are themselves noisy at n=60, and the five binary blockers (access leak, boundary breach, invented answer, numeric mismatch, injection) are the right design precisely because they are binary and do not need statistics.

**What to do instead (INFERENCE).** Evaluate chunking with the two or three questions you know are the hard ones, and check the *mechanism* rather than the score. Does the retrieved chunk contain the whole table row? Does its recorded page match the page the number is on? Is the heading in the chunk text? These are pass/fail checks on individual questions, and 60 is plenty for those.

---

## 2. Contextual retrieval

This is the part of the brief with the clearest measured evidence, and also the part where the offline build makes the answer different from the cloud build.

### 2.1 What Anthropic published, exactly

The method: prepend 50 to 100 tokens of LLM-generated, chunk-specific context to each chunk, then embed the contextualised chunk and use it for the BM25 index too.

The prompt, quoted from the post:

```
<document>
{{WHOLE_DOCUMENT}}
</document>
Here is the chunk we want to situate within the whole document
<chunk>
{{CHUNK_CONTENT}}
</chunk>
Please give a short succinct context to situate this chunk within the overall document
for the purposes of improving search retrieval of the chunk. Answer only with the
succinct context and nothing else.
```

The measured results, as published:

| Configuration | Top-20 retrieval failure rate | Reduction |
|---|---|---|
| Baseline (embeddings only) | 5.7% | — |
| Contextual embeddings | 3.7% | 35% |
| Contextual embeddings + contextual BM25 | 2.9% | 49% |
| Plus reranking | 1.9% | 67% |

Method notes they give: evaluated across codebases, fiction, ArXiv papers, and science papers. Embedding configuration was Gemini Text 004, the best-performing one they tested. Metric is 1 minus recall@20. Retrieval was top-150, reranked to top-20 with the Cohere reranker. They also found that passing 20 chunks beat 10 and 5.

- Source: Ford, "Introducing Contextual Retrieval", Anthropic, 19 September 2024, https://www.anthropic.com/engineering/contextual-retrieval (retrieved 2026-10-11). **VENDOR-RUN MEASUREMENT** with disclosed method. Their per-dataset breakdown is in an appendix image I could not read as text, so I can only quote the averages.

**Independent confirmation at small n, from Anthropic's own cookbook**: on 9 codebases with 248 queries and a "golden chunk" per query, "Contextual Embeddings in this case helped us to improve Pass@10 performance from ~87% --> ~95%". That is an 8-point gain on 248 queries. **MEASURED**, vendor-run, https://platform.claude.com/cookbook/capabilities-contextual-embeddings-guide (retrieved 2026-10-11).

**One external comparison, competitor-published.** Voyage AI states that their hosted `voyage-context-3` model "outperformed Jina-v3 late chunking and Anthropic contextual retrieval by 23.66% and 6.76% on chunk-level" retrieval. This is a competitor benchmarking a competitor, so treat the ranking as marketing. It is useful only as a signal that the technique has real effect at all. **VENDOR CLAIM**, and `voyage-context-3` is a hosted API, therefore disqualified for the office build by constraint 3.

### 2.2 The cost, and why the published figure does not transfer

Anthropic quotes "the one-time cost to generate contextualized chunks is $1.02 per million document tokens", under these assumptions: 800-token chunks, 8k-token documents, 50-token context instructions, 100 tokens of context per chunk, with prompt caching. **VENDOR-RUN MEASUREMENT**, and the number is specific to Claude 3 Haiku pricing in September 2024 plus prompt caching. It will not be the number for a current model and it definitely will not be the number for a local one.

The transferable insight is the **token amplification factor**, and this is my arithmetic, so treat it as INFERENCE:

- An 8k-token document at 800 tokens per chunk produces 10 chunks.
- Every contextualisation call carries the whole document as context.
- So the model processes roughly 10 × (8,000 + 800 + 50) ≈ 88,500 input tokens to contextualise one 8,000-token document.
- That is about **11 times the document's own token count** flowing through the model, before any caching.

Cross-check: Anthropic's $1.02 per million document tokens implies roughly 11.2 million processed tokens per million document tokens at Haiku's September 2024 rates, which is the right order of magnitude. I am not claiming their figure reproduces exactly from published prices, because their cache-write assumptions are not fully stated. Do not quote my arithmetic as their number.

### 2.3 Can it be done locally, cheaply enough?

The answer has three parts, and only the first is comfortable.

**(a) The architecture works offline.** Nothing in the method needs the internet. You need a model, a prompt, and a document. A local model serves.

**(b) The naive cost is bad, and the naive cost is what you get if you do not think about caching.** Recompute the amplification for a small local model on CPU. This is my model, not a measurement.

Assume an 8k-token document, 10 chunks of 800 tokens, a 100-token system prompt, and 100 output tokens per chunk.

| Approach | Input tokens processed | Output tokens generated |
|---|---|---|
| No prefix reuse | 10 × 8,900 = 89,000 | 1,000 |
| Prefix KV reuse across the 10 calls | 8,000 + 10 × 900 = 17,000 | 1,000 |

Prefix reuse is worth about 5x on input. This is the same trick as Anthropic's prompt caching, implemented differently: a local inference server that reuses the KV cache for a shared prefix across sequential requests gets the same effect for free. **I did not verify that the specific local server we would deploy does this correctly under our request pattern. Verify it before relying on it.** That verification is a bounded experiment and should be part of the spike.

**(c) The generation, not the prompt reading, is the CPU bottleneck.** This is the finding that should change the plan.

Prompt processing on CPU is reasonably fast because it is a matrix-matrix operation. Token generation is memory-bandwidth-bound and much slower, typically by an order of magnitude or more. If you must *generate* 100 tokens per chunk, that generation dominates. My model, clearly labelled INFERENCE, with the throughput as a parameter you must measure:

For a corpus of 50,000 chunks at 100 generated tokens each, that is 5 million output tokens:

| CPU generation speed | Time to contextualise 50,000 chunks |
|---|---|
| 5 tokens/sec | 278 hours (11.6 days) |
| 10 tokens/sec | 139 hours (5.8 days) |
| 20 tokens/sec | 69 hours (2.9 days) |
| 50 tokens/sec | 28 hours |

And the input side, with prefix reuse, is roughly 17,000 input tokens per 8,000-token document. At 50,000 chunks the corpus is roughly 40 million document tokens, so about 85 million input tokens of prompt processing:

| CPU prompt processing | Time for 85M input tokens |
|---|---|
| 100 tokens/sec | 236 hours |
| 200 tokens/sec | 118 hours |
| 500 tokens/sec | 47 hours |

**I could not verify a CPU prompt-processing or generation figure for the reference instance.** I looked for one and the benchmark aggregator I found had withdrawn its comparison table for exactly the reason I would have distrusted it. So these are parameterised models, not predictions. Measure with `llama-bench` on the m7i.4xlarge before committing to any of this.

**What the numbers mean anyway (INFERENCE).** Even at favourable rates, contextualising a large corpus on CPU is a multi-day batch job. That is not automatically disqualifying, because the brief already says bulk indexing is a separate job on a temporary rented instance. But it changes the arithmetic of that job: you are no longer just embedding, you are running generation. Adding contextualisation to a 150-dollar indexing run will multiply it, and the multiplier is set by generation speed, which is the worst-performing number on the box.

### 2.4 The cheaper alternatives, and why I would start there

Anthropic's own ablations are the reason to be sceptical of the "just add an LLM call" framing. They tried the two obvious cheap-ish alternatives and reported: generic document summaries gave "very limited gains", and summary-based indexing gave "low performance". **The gain is not from adding more text. It is from adding text that disambiguates this chunk from its neighbours.** That is a different problem, and part of it can be solved for free.

Deterministic context you can prepend without any model call:

- **Document title.** Cheap, and it fixes the "which company is this about" failure from Anthropic's example.
- **Heading path.** "Master Services Agreement > 7. Fees > 7.3 Late payment". Docling's `DocMeta` carries this as a first-class field (see section 4.3). This is the highest-value free signal, because it is exactly the disambiguation a heading provides.
- **Page number and label.** Cheap and needed for citation anyway.
- **Table caption and column headers.** Measured to help; see section 3.
- **Effective date or document version** where the document has one.

**Then measure the delta from the LLM call on top of that.** The honest position is:

> Nobody has published a quality evaluation of small-model-generated chunk context against deterministic heading-path context. I looked and did not find one. Until someone measures it on our corpus, the LLM call is an unproven increment over a free baseline.

If it turns out the deterministic version captures most of the gain, we have saved the entire ingest cost. If it does not, we have a measured reason to spend it. Either way the test is cheap: it is the same 60 questions, run twice.

**A third path worth a spike: late chunking.** Jina AI's method embeds all tokens of a long document first, then applies chunk boundaries *after* the transformer and just before pooling, so each chunk embedding carries the full document context. The paper reports "superior results across various retrieval tasks" without additional training.

- Source: Günther et al., "Late Chunking: Contextual Chunk Embeddings Using Long-Context Embedding Models", arXiv 2409.04701, https://arxiv.org/abs/2409.04701 (retrieved 2026-10-11). **MEASURED**, but I read the abstract only and **did not read the result tables**, so I am not quoting a gain figure.

**Operational blocker, verified.** `bge-m3` is a suitable model on paper: 1024 dimensions, 8192 max tokens, and the model card describes it as multi-granularity, able to "process inputs of different granularities, spanning from short sentences to long documents of up to 8192 tokens" (https://huggingface.co/BAAI/bge-m3, retrieved 2026-10-11). But late chunking needs **token-level** embeddings to pool over chunk spans. Text Embeddings Inference, which is what the stack runs for embeddings, returns pooled embeddings, and its CLI exposes pooling as a per-model setting (`mean`, `splade` are the modes I read in the README). **VERIFIED** as a limitation of the serving layer, from https://raw.githubusercontent.com/huggingface/text-embeddings-inference/main/README.md (retrieved 2026-10-11). So late chunking would mean running the embedding model outside TEI, or adding a code path to it. That is a real engineering cost and it breaks the clean service boundary. Defer it.

### 2.5 Verdict on contextual retrieval

| Build | Recommendation |
|---|---|
| **Cloud (Bedrock)** | **Do it.** The gains are measured, Bedrock's Claude models support prompt caching, and there are published implementation paths including AWS's own Lambda for Bedrock Knowledge Bases. Ingest cost is small relative to the value. Run the deterministic baseline too, so you know the delta. |
| **Office (no internet)** | **Do the deterministic half unconditionally. Spike the LLM half.** Budget it as a batch job on rented hardware, not as an inline ingest step. Measure generation throughput on the target CPU first, because that single number decides whether it is hours or weeks. |

Reject for both builds: Voyage's contextualized chunk embeddings (hosted API, disqualified offline and a dependency in the cloud).

---

## 3. Tables and scans

This section matters more than the chunking-strategy question, because a fee schedule read as a token stream produces a confidently cited wrong number, which the brief correctly identifies as the failure that loses a client.

### 3.1 The mechanism, verified in Docling's source

I read the dataclasses rather than the marketing. Docling gives you everything needed for both the table problem and the bounding-box problem.

**Provenance, from `docling_core/types/doc/common/reference.py`:**

```python
class ProvenanceItem(BaseModel):
    """Provenance information for elements extracted from a textual document.

    A `ProvenanceItem` object acts as a lightweight pointer back into the original
    document for an extracted element. It applies to documents with an explicit
    or implicit layout, such as PDF, HTML, docx, or pptx.
    """
    page_no: Annotated[int, Field(description="Page number")]
    bbox: Annotated[BoundingBox, Field(description="Bounding box")]
    charspan: CharSpan
```

**The bounding box, from `docling_core/types/doc/base.py`:**

```python
class BoundingBox(BaseModel):
    l: float  # left
    t: float  # top
    r: float  # right
    b: float  # bottom
    coord_origin: CoordOrigin = CoordOrigin.TOPLEFT

class CoordOrigin(str, Enum):
    TOPLEFT = "TOPLEFT"
    BOTTOMLEFT = "BOTTOMLEFT"
```

Note `coord_origin`. Getting this wrong flips your highlight vertically. It defaults to `TOPLEFT` in current Docling.

**Every document item carries provenance, from `docling_core/types/doc/items/node.py`:** `prov: list[ProvenanceItem] = []` on `NodeItem`. Since `DocItem` extends `NodeItem`, a paragraph, a heading, and a table each carry a list of provenance records.

So the extraction path is: chunk → `chunk.meta.doc_items` → each item's `.prov` → `page_no`, `bbox`, `charspan`. **VERIFIED** against source at
https://raw.githubusercontent.com/docling-project/docling-core/main/docling_core/types/doc/common/reference.py and
https://raw.githubusercontent.com/docling-project/docling-core/main/docling_core/types/doc/items/node.py (retrieved 2026-10-11).

**Tables, from `docling_core/types/doc/items/table/table_data.py`:**

```python
class TableCell(BaseModel):
    bbox: BoundingBox | None = None
    row_span: int = 1
    col_span: int = 1
    start_row_offset_idx: int
    end_row_offset_idx: int
    start_col_offset_idx: int
    end_col_offset_idx: int
    text: str
    column_header: bool = False
    row_header: bool = False
    row_section: bool = False
    fillable: bool = False

class TableData(BaseModel):
    table_cells: list[AnyTableCell] = []
    num_rows: int = 0
    num_cols: int = 0
    orientation: Orientation = Orientation.ROT_0

    @computed_field
    @property
    def grid(self) -> list[list[TableCell]]: ...
```

This is the answer to "how do I chunk a table without destroying the row and column relationship". You do not need to parse markdown and hope. You have the row index, the column index, the span, an explicit flag for which cells are headers, and a bounding box per cell. **VERIFIED** against source at
https://raw.githubusercontent.com/docling-project/docling-core/main/docling_core/types/doc/items/table/table_data.py (retrieved 2026-10-11).

Three details that matter operationally:

- `row_header` and `column_header` tell you which cells are labels. Without this you cannot reconstruct "the 2024 mileage rate is 67 cents" from a grid.
- `row_section` exists, so tables with internal section breaks are representable.
- `fillable` exists, which matters for the forms and invoices in that client document list.

### 3.2 How to chunk a table

Four rules, in priority order.

**Rule 1: never split a table that fits.** NVIDIA states its practice directly: "tables, and charts were extracted as separate entities. These elements were not split or chunked, but rather preserved as complete units to maintain their integrity and context." **VENDOR-RUN MEASUREMENT** with disclosed method. The Unstructured paper's recipe does the same: "if a table element is found, a new chunk is started, preserving the entire table." **MEASURED.**

**Rule 2: repeat the header on every piece when it does not fit.** Both Chroma's and NVIDIA's reports note the expectation that repeated headers help, and NVIDIA flags it as a limitation of *not* doing it in their study. The strongest statement is from the table-format benchmark's limitations: "We'd expect smaller tables and/or repeated header rows to lead to higher accuracy, particularly for CSV, HTML and markdown table formats (the ones that involve header rows)." That is a stated expectation, not a measurement, so **VENDOR CLAIM**.

What *is* verified is that the mechanism exists and is a first-class option in Docling. Its `HybridChunker` has `repeat_table_header` defaulting to `True`: "table headers are repeated at the beginning of each chunk when a table spans multiple chunks. This ensures each chunk maintains context about the table structure." There is also `omit_header_on_overflow` (default `False`) for wide tables where a row fits without the header but not with it. **VERIFIED** from the Docling chunking documentation, https://docling-project.github.io/docling/concepts/chunking/.

**Rule 3: include the caption and the surrounding sentence.** This is the one table-handling decision with a clean measured result behind it. In the Unstructured experiment, the winning metadata variant was "Prefix & Table Description": a naive prefix of the first two sentences plus, for tables, the description from the table caption. It beat both GPT-4-generated keywords and GPT-4-generated summaries on every metric.

| Metadata | Page accuracy | BLEU | Manual accuracy |
|---|---|---|---|
| Keywords (GPT-4) | 46.10 | 0.315 | 53.19 |
| Summary (GPT-4) | 62.41 | 0.350 | 51.77 |
| **Prefix + table description** | **67.38** | **0.400** | **53.19** |

**MEASURED**, 141 questions. A caption is free. Generate nothing.

**Rule 4: serialize for the embedding model you actually run, and measure.** This is where the evidence is weakest and I want to be clear about it.

What is measured, and it is about *table retrieval* rather than generation: Gomm & Hulsebos, "Metadata Matters in Dense Table Retrieval" (ELLIS workshop 2025). Using the TARGET benchmark on FeTaQA:

> "Including metadata increases the average recall@3 by 0.42."

and

> "The number of rows included in the serialization has a large impact... Most models show little improvement when including more than 10 rows."

and, most importantly:

> "There is no single 'best' parameter combination that generalizes across all models. Achieving the best performance requires thorough experimentation with each embedding model."

- Sources: https://www.daniel-gomm.com/blog/2025/Table-Serialization-Kitchen/ and the extended abstract at https://openreview.net/forum?id=rELWIvq2Qy. **MEASURED**, workshop paper, two datasets, several embedding models. I could not retrieve the OpenReview abstract body via the API, so I am quoting the blog post's summary of the same work by the same authors.

Read the first finding again, because it is the measured version of the contextual-retrieval argument applied to tables: **putting the table's own title and section into the serialization improved recall@3 by 0.42 absolute.** Free, deterministic context. Same lesson as section 2.4.

**The weak spot.** The widely-cited "which format do LLMs understand best" comparison (markdown-KV 60.7 percent, XML 56.0, INI 55.7, YAML 54.7, HTML 53.6, JSON 52.3, markdown-table 51.9, natural language 49.6, JSONL 45.0, CSV 44.3, pipe-delimited 41.1) is **about the generator reading a table already in its context, not about retrieval**, it used **one model** (GPT-4.1-nano), on **synthetic data**, with **confidence intervals around ±3 points** that overlap for most of the ranking.

- Source: https://www.improvingagents.com/blog/best-input-data-format-for-llms/ (retrieved 2026-10-11). **MEASURED but narrow.** Do not treat it as a licence to pick markdown-KV and stop thinking. The top five formats span 7 points with overlapping intervals.

**My recommendation (INFERENCE, given the above).**

1. Store the table twice. Keep the structured `TableData` (cells, spans, header flags, cell bboxes) as the canonical record, and store a **row-wise serialization** for embedding, where each row is a self-contained statement with the header names folded in, plus the caption and heading path as a preamble.
2. Repeat the header periodically in long serializations. Gomm's finding that most models stop improving past 10 rows is consistent with the model losing track of which column is which.
3. Test the serialization against `bge-m3` specifically, because Gomm's central finding is that the optimum is model-dependent and `bge-m3` is not one of the models he tested.
4. Do not split a table across chunks unless it genuinely exceeds the token budget, and when you do, repeat the header and record the page range.

### 3.3 Keeping a page and a bounding box on every chunk

**The core design decision: record a page range, not a page number.**

A chunk that spans pages 4 and 5 has no single page. If you store one integer you will silently mis-cite on every page-straddling chunk, and under requirement 5 that is a quiet correctness bug. Store:

- `page_start` and `page_end`, both integers.
- `bbox_start` and `bbox_end`, the boxes on the first and last page.
- Optionally, a list of per-page boxes: `[{page: 4, bbox: ...}, {page: 5, bbox: ...}]`.

Then the citation rule is deterministic, and this is the part to write into the verifier:

- If `page_start == page_end`, cite that page with `bbox_start` for the highlight.
- If they differ, cite the range, and highlight only the portion on the page the answer's sentence was drawn from. Resolve which page that is with the `charspan` offsets, which Docling gives you per item.
- If you cannot resolve it, cite the range without a highlight, and mark the answer amber rather than guessing. Requirement 5 says refusal beats a confident wrong answer; the same logic applies to a confident wrong highlight.

**Prefer page-bounded chunks in the first place.** This is where the NVIDIA result earns its place. Page-level chunking achieved the highest average accuracy (0.648) and the lowest standard deviation (0.107) across their datasets, against token-based approaches spanning 0.603 to 0.645. The accuracy gap is inside the noise. The **variance** result is the real finding, and for a product sold on citation accuracy, consistent matters more than a fraction of a point.

**NVIDIA also found page-level beat section-level** using the same parser for both, which is worth pausing on. Section-level chunking follows the document's logical structure and should be better on the merits. It was not. Their suggested reason elsewhere is that the optimum is query-dependent: factoid questions in DigitalCorpora767 and Earnings favoured 256 to 512 token chunks, while complex analytical queries in FinanceBench and RAGBattlePacket benefited from larger chunks or page boundaries.

**The synthesis I would implement (INFERENCE).** Chunk on structure, but **treat the page boundary as a hard boundary that wins over the heading boundary.** If a section runs from the middle of page 4 to the middle of page 5, cut it at the page break and repeat the heading path on both pieces. You get structure, you get an unambiguous page for every chunk, and you get the low-variance behaviour NVIDIA measured. The cost is that you sometimes split a section, which you mitigate by repeating the heading path so the second half is still retrievable.

**Multi-page tables** are the exception to the page-boundary rule. A table that spans a page break should stay one retrievable unit, because splitting it destroys the row relationship. Record the page range and cite the range. This is a deliberate trade: better retrieval, slightly less precise citation, and the verifier should be told that a ranged citation from a table is expected rather than a defect.

**Spreadsheets have no pages, and this needs an explicit decision.** `xlsx` and `csv` have sheets and cell addresses, not pages. The substitute metadata is a sheet name plus a cell range, and the named location should be the first column's value on that row, because that is what a human would say out loud. "Expense Policy, sheet 'Rates', row 14" is a usable citation; "page 1" is a lie. Docling's `PageChunker` source shows the fallback built in: "if no pages, treat whole document as single chunk". That fallback would put an entire sheet in one chunk and is not good enough for a rate table. Handle spreadsheets as a distinct ingestion path with row-group chunking and cell-range provenance.

**The other parsers agree on the field names.** Unstructured's element metadata is a flat dict carrying `page_number` alongside `filename`, `filetype`, `file_directory`, `languages`, `parent_id`, and `last_modified`, and it has dedicated table metadata and Excel-specific metadata sections. Its chunking model is element-preserving: "all chunks contain one or more whole elements, preserving the coherence of semantic units established during partitioning", with chunk types `CompositeElement`, `Table`, and `TableChunk`, and parameters `max_characters` (default 500), `new_after_n_chars`, `overlap`, and `overlap_all`.

- Sources: https://docs.unstructured.io/open-source/concepts/document-elements and https://docs.unstructured.io/open-source/core-functionality/chunking (retrieved 2026-10-11). **VENDOR CLAIM** about mechanism, verified from official docs.

The names differ between tools, the model is the same: a provenance record on every element, propagated to every chunk.

### 3.4 Scanned documents and OCR

The honest limitation first: **OCR gives you a bounding box for a line of recognised text, not a semantic paragraph.** Everything downstream that assumes paragraph-level provenance degrades. A highlight on a scanned invoice will be a box around a run of text of the OCR engine's choosing.

What is available, all of it CPU-runnable and all of it permissively licensed (licences verified in section 6):

- **Tesseract**, which emits hOCR and TSV with per-word and per-line boxes. Apache-2.0.
- **RapidOCR**, ONNX-based, Apache-2.0. Docling documents it as a pluggable OCR engine.
- **PaddleOCR**, Apache-2.0.
- **EasyOCR**, Apache-2.0. This is Docling's default OCR engine.
- **OCRmyPDF**, which produces a searchable PDF: the original page image plus an invisible text layer with positions. MPL-2.0, which is file-level copyleft and fine for shipping in a container.
- **Docling's own OCR path**, which routes recognised cells back through the same `ProvenanceItem` structure, so the chunk-level API is identical whether the page was text or scan.

**The practical recommendation (INFERENCE).** For a scan, accept line-level or word-level highlighting and say so in the interface. Do not attempt to reconstruct paragraph boxes from OCR output; the reading-order model that does that is the same layout model you already ran, so use its output rather than building a second one. Record `is_ocr: true` on the chunk so the verifier and the UI can set expectations.

Also worth flagging: the Chroma report notes that in their experiments, token entropy rose "at the beginning and towards the end of sentences" and they "did not find that there was any clear structure in the attention values which could be used for chunking". **MEASURED, negative result.** There is no free signal in model internals for where to cut.

---

## 4. Chunk metadata without re-ingestion

The requirement is that access control, versioning, retention, and citation all work **without re-ingesting**. That single constraint decides most of this section, and it leads to one rule.

### 4.1 The governing rule

> **Metadata lives in the vector store payload and in Postgres. It never enters the embedding input.**

If a chunk's text contains its access label, or its version, or its retention class, then changing any of those means re-embedding. If the text contains only content plus the contextualisation you have deliberately chosen, then access control, versioning, and retention become payload updates and database rows, at zero embedding cost.

This is why the contextualisation design in section 2.4 is a metadata decision as much as a retrieval decision. Prepend the heading path, the title, and the caption. **Do not prepend the access label, the version number, or the ingest timestamp.** Those are filters and bookkeeping; in the embedding they are noise that also makes the embedding stale.

There is a cost to admitting this, and it should be stated: because the contextualisation is baked into the embedding, changing *it* does require re-embedding. That is unavoidable and it is why the contextualisation choice deserves the measurement in section 2.4 rather than a guess.

### 4.2 The schema

Three layers. This is a design proposal, **INFERENCE** synthesised from the verified field names in sections 3.1 and 4.3.

**Identity and citation** (needed for every answer):

| Field | Type | Note |
|---|---|---|
| `chunk_id` | string | Stable, derived. See 4.4. |
| `content_sha256` | string | Hash of the chunk text. Detects drift, supports the audit chain. |
| `doc_id` | string | Stable document identifier, independent of filename. |
| `doc_version` | int | Monotonic per document. |
| `source_path` | string | Where it came from. |
| `source_format` | string | `pdf`, `docx`, `xlsx`, `eml`, ... |
| `page_start`, `page_end` | int | The citation. Both, always. |
| `bbox_start`, `bbox_end` | float[4] or null | `[l, t, r, b]` with `coord_origin` recorded. Null for formats without layout. |
| `page_boxes` | jsonb or null | Per-page boxes for multi-page chunks. |
| `charspan_start`, `charspan_end` | int | Offsets into the normalised page text. Resolves the straddle case. |
| `is_ocr` | bool | Set expectations for highlight precision. |
| `sheet_name`, `cell_range` | string | The spreadsheet substitute for page and bbox. |

**Structure and retrieval:**

| Field | Type | Note |
|---|---|---|
| `heading_path` | text[] | `["MSA", "7. Fees", "7.3 Late payment"]`. Indexed for filtering and used as contextualisation. |
| `content_type` | enum | `prose`, `table`, `list`, `figure_caption`, `form_field`, ... |
| `token_count` | int | For budget accounting. |
| `parent_chunk_id` | string or null | The small-to-big link. |
| `table_id` | string or null | Groups the pieces of one split table. |
| `is_table_header_repeat` | bool | So the verifier knows a repeated header is not a duplicate. |

**Governance** (never enters the embedding):

| Field | Type | Note |
|---|---|---|
| `acl_labels` | text[] | Role or group labels. The filter key. |
| `doc_class` | string | Maps to the retention rule. |
| `superseded_by` | string or null | Points at the replacement `doc_version`. |
| `is_current` | bool | The fast filter for "do not cite a superseded document". |
| `effective_date`, `expiry_date` | date or null | Document semantics, not ingest time. |
| `legal_hold` | bool | Blocks the purge job. |
| `retain_until` | date or null | Computed, not free-typed. |
| `ingest_ts` | timestamptz | When this row was written. |
| `parser_version`, `chunker_version`, `embedding_model`, `embedding_model_version` | string | Reproducibility. Answers "what would I have to re-run if I change X". |
| `deleted_at` | timestamptz or null | Soft delete, so the audit record survives the purge. |

### 4.3 Qdrant specifics, verified

**Payload indexes.** Qdrant indexes payload fields to make filtering fast. For the tenant or ACL field, the documented pattern is a keyword index with `is_tenant: true`:

```json
PUT /collections/{collection_name}/index
{
    "field_name": "group_id",
    "field_schema": { "type": "keyword", "is_tenant": true }
}
```

The documentation is explicit that the flag has a performance purpose beyond correct filtering: "When set, Qdrant organizes the storage structure to co-locate vectors of the same tenant together, which can significantly improve performance by utilizing sequential reads during queries. Instead of many random disk seeks across the segment, the data of one tenant can be read in a single sequential pass."

It also states the version floor: "The `is_tenant` parameter is available as of v1.11.0."

- Source: https://qdrant.tech/documentation/manage-data/multitenancy/ (retrieved 2026-10-11). **VENDOR CLAIM**, from official documentation, with the API shape and version floor verified in the docs.

For our deployment this is the right mechanism for the ACL field, even though each client is a single deployment with a handful of roles. The reason is not multi-tenancy; it is that `is_tenant` gives the storage layout that makes a role-filtered query read sequentially. With 5 to 30 users and a role filter on every query, that is free performance.

Index the fields you filter on: `acl_labels`, `is_current`, `doc_id`, `doc_class`, `source_format`. Do not index the fields you only display, because each payload index costs memory and write time.

**Versioning and deletion.** Two mechanisms, both needed:

- **Upsert by deterministic `chunk_id`** replaces a chunk in place. This is the incremental re-ingest path.
- **Delete by filter** (`FilterSelector`) removes every chunk of a superseded document version. This is the reconciliation path, and it is what makes "a superseded document is not cited" enforceable rather than aspirational.

**Payload size.** A bounding box is four floats. Even as a JSON blob, a chunk's payload is on the order of a kilobyte against a 1024-dimensional `bge-m3` vector, which is 4 KB as float32 before quantization. So **the payload is not the storage problem; the vector is.** Storing per-cell table boxes is the one case that can grow the payload materially, and it should go in Postgres keyed by `table_id` rather than into every chunk payload, with Qdrant carrying only the chunk's own `bbox_start`/`bbox_end`.

**Whether the bbox survives a re-parse is a real question.** It does not, reliably. A parser version change can move a box or renumber a page, and parse output is not byte-stable across versions. Which is why `parser_version` is in the schema: when it changes, you know which chunks' provenance is suspect and can prioritise re-ingestion of those documents instead of the whole corpus.

### 4.4 Stable chunk identifiers

The problem: make a `chunk_id` that survives re-ingestion so you update in place rather than delete-and-recreate. Delete-and-recreate loses the audit trail's ability to point at a chunk, and it makes the "did this chunk change?" question unanswerable.

Two candidate schemes, and the choice depends on whether the chunker is deterministic.

**Deterministic chunker (recommended):** if the chunk boundaries are a pure function of the document content and the chunker version, then

```
chunk_id = uuid5(NAMESPACE_OI, f"{doc_id}:{doc_version}:{chunker_version}:{ordinal}:{content_sha256}")
```

The `content_sha256` term is deliberate. It makes the ID change when the *content* changes, so a re-ingest that produces an identical chunk produces an identical ID and is a no-op, while a chunk whose text changed gets a new ID and the old one becomes an orphan that the reconciliation pass deletes. The `ordinal` term stops two identical chunks in one document from colliding.

**Non-deterministic chunker:** if the chunker depends on corpus-global state (semantic clustering, RAPTOR trees), the ID cannot be stable and you must fall back to delete-by-document-version then re-insert. This is a second, independent reason to reject `ClusterSemanticChunker`, whose own documentation admits that chunks "must be re-computed as data is added".

**What production tools recommend, and what I could verify.** The existing `ai-stack` ingestion already does this correctly: it uses "uuid5 point IDs on relpath:chunk", which is the same family of scheme. I did **not** find a published Qdrant, Weaviate, or Chroma recommendation document that specifies a stable-chunk-ID algorithm. **UNVERIFIED** as an industry convention; the scheme above is my design.

### 4.5 ACL: where the filter belongs

Put the filter **in the vector query**, not after retrieval.

Filtering after retrieval means an unauthorised chunk occupied a result slot that an authorised chunk should have had, so you silently degrade recall for restricted users. It also means unauthorised content is in the process's memory, which weakens the story you tell an examiner even if it never reaches the model.

Two hard rules from the existing architecture carry over unchanged and should be restated in the chunker's contract:

- The role comes from the authenticated session, never from a parameter. `docs/runbook.md` already records that the tools currently take a hand-typed `--actor` and that this stops before a client sees it.
- A missing or empty `acl_labels` on a chunk must **deny**, not allow. The failure mode of a bug in the ingestion path should be an unreadable document, not a readable one.

### 4.6 Retention and legal hold

The pattern that works, and it is **INFERENCE** rather than a published recommendation, because I could not find a published vector-store retention pattern I would cite:

1. **Soft delete.** `deleted_at` is set, `is_current` goes false, and the retrieval filter excludes it. The chunk stays.
2. **Tombstone in the audit chain.** The purge writes an event carrying the hash before and after, so the gap is visible. `docs/build-brief.md` section 8 already specifies this and notes the escape hatch that lets anyone with database access flip `oi.allow_audit_maintenance` leaving no trace. That defect is not fixed and it undermines the tombstone.
3. **Hard delete only when `legal_hold` is false and `retain_until` has passed**, and only through a job that writes the tombstone first.
4. **`legal_hold` blocks step 3 unconditionally** and must itself be an audited event.

The reason the vector store makes this harder than a database is that Qdrant does not have a legal-hold primitive. You enforce it in the purge job and you make the job's correctness a test, the same way `tests/test_audit_verify.py` replays attacks against the audit verifier.

### 4.7 Versioning, and the one field that does the work

`is_current` plus `superseded_by` is the whole mechanism, and the retrieval filter is `is_current == true`.

The subtlety worth writing down: **"current" is a property of the document, not of the chunk**, so it is denormalised onto every chunk and must be updated on every chunk of a superseded version atomically. Qdrant's set-payload-by-filter operation does this in one call. If it is not atomic, a query in flight can retrieve a mix of current and superseded chunks from the same document, which is exactly the "confidently cited old number" failure.

`effective_date` is separate and is not the same as ingest time. A contract signed in March and ingested in October has an effective date of March. Getting this wrong makes "what was the policy in April" unanswerable.

### 4.8 Docling's own chunk metadata, as a starting point

Docling ships a metadata model you can adopt or extend. From `docling_core/transforms/chunker/doc_chunk.py`:

```python
class DocMeta(BaseMeta):
    schema_name: Literal["docling_core.transforms.chunker.DocMeta"]
    version: str
    doc_items: list[DocItem]          # carries prov: page_no, bbox, charspan
    headings: list[str] | None        # the heading path
    captions: list[str] | None        # deprecated
    origin: DocumentOrigin | None

    excluded_embed: ClassVar[list[str]] = ["schema_name","version","doc_items","origin"]
    excluded_llm:   ClassVar[list[str]] = ["schema_name","version","doc_items","origin"]
```

Three things to take from this.

1. **`doc_items` is excluded from both the embedding and the LLM text.** That is exactly the "governance out of the embedding" principle, implemented by the library authors. The provenance travels with the chunk as an object, not as prompt text.
2. **`headings` is included**, so the heading path is what the model sees. This is the deterministic contextualisation of section 2.4, already wired in.
3. `captions` is marked deprecated, so read the current field name from the version you pin before building on it.

And `PageChunker`, from the same package, is "a chunker implementation that yields one chunk per page", which is the low-variance option from section 3.3 available as a one-line swap. That matters, because it means we can A/B page chunking against hybrid chunking **without changing the ingestion pipeline**, only the chunker class.

---

## 5. Options rejected, and why

| Rejected | Reason | Source |
|---|---|---|
| **Semantic chunking as the default** | Measured benefit is inconsistent and does not justify the cost. Fixed-size beat it on 3 of 5 evidence-retrieval datasets. Also needs corpus-global statistics, which breaks incremental ingestion. | arXiv 2410.13070; Chroma limitation section |
| **`ClusterSemanticChunker`** | Same, plus an explicit statement that chunks "must be re-computed as data is added", which is incompatible with our stable-chunk-ID design. | Chroma report, limitations |
| **`LLMSemanticChunker`** | Highest recall in Chroma's evaluation (0.919), so it works, but it is an LLM pass over every 50-token window. Chroma notes it "can vary from almost instantaneous to tens of minutes" and that cost was not measured. | Chroma report |
| **Summary indexing / RAPTOR as the primary path** | Anthropic "saw low performance". Unstructured measured a generated summary as worse than a free prefix on both retrieval and answer accuracy. | Anthropic post; arXiv 2402.05131 |
| **Generated chunk summaries as table metadata** | Directly measured: page accuracy 62.41 against 67.38 for the free prefix, and *worse* answer accuracy (51.77 against 53.19). | arXiv 2402.05131 |
| **High overlap (10 to 20 percent)** | "Generally neutral to negative" across 1,820 configurations. Every top-1-percent configuration used at most 32 tokens of overlap. | arXiv 2506.17277v2 |
| **256-token chunks as a universal default** | 128 underperforms consistently, and the 2026 study puts the top range at 384 to 512. Unstructured's 256 win was page-hit rate only and did not carry through to answer quality. | arXiv 2506.17277v2; arXiv 2402.05131 |
| **`voyage-context-3` / `voyage-context-4`** | Hosted API. Disqualified for the office build by constraint 3, and an external dependency in the cloud build for no reason we can justify. | Voyage blog |
| **Late chunking, for now** | The idea is sound and `bge-m3` has the 8192-token context for it, but it needs token-level embeddings and TEI returns pooled embeddings. Deferred pending a serving-layer change. | arXiv 2409.04701; TEI README |
| **PyMuPDF and pymupdf4llm** | AGPL-3.0. Commercial licence exists via Artifex with **no published price**, so it cannot be costed. This is the single easiest licence mistake to make in a Python PDF stack. | GitHub licence API; PyMuPDF docs |
| **MinerU** | Apache-2.0 but wrapped in additional terms: an attribution obligation when providing online services to third parties, and a commercial licence required above 100M MAU or USD 20M monthly revenue. The thresholds do not bind us and the attribution does not either, but a licence with bespoke clauses is a legal review we do not need when MIT and plain Apache-2.0 alternatives exist. | `LICENSE.md`, raw.githubusercontent.com/opendatalab/MinerU/master/LICENSE.md |
| **Ministral-8B-Instruct-2410** | Mistral AI Research License. Non-commercial. Disqualified outright. | HF model card, `license_name: mrl` |

**Not rejected, but noted:**

- **`marker` and `surya`** now read plain Apache-2.0 in their LICENSE files with no additional terms, a change from their earlier GPL-3.0. They are usable. I checked the LICENSE files directly because these projects have changed licence before and a stale blog post would give the wrong answer in either direction.
- **Page-level chunking** is not rejected; it is the low-variance baseline we should measure against, and Docling's `PageChunker` makes that a one-line swap.

---

## 6. Licence verification

Read from the project's own LICENSE file or repository, on 2026-10-11. Where I used the GitHub licence API I say so and give the file path. Where the sidebar label and the file could disagree, I read the file.

### Parsers, OCR, and the retrieval layer

| Component | Licence | Verified at | Verdict |
|---|---|---|---|
| Docling | MIT | raw.githubusercontent.com/docling-project/docling/main/LICENSE | OK |
| docling-core | MIT ("Copyright (c) 2024 International Business Machines") | raw.githubusercontent.com/docling-project/docling-core/main/LICENSE | OK |
| docling-ibm-models | MIT | raw.githubusercontent.com/docling-project/docling-ibm-models/main/LICENSE | OK |
| docling-parse | MIT | GitHub licence API, `pymupdf`-style check on docling-project/docling-parse | OK |
| **PyMuPDF** | **AGPL-3.0** (file is `COPYING`) | GitHub licence API returns `AGPL-3.0`; docs confirm dual AGPL/commercial with Artifex as agent, no price shown | **DISQUALIFIED** unless a priced commercial licence is obtained |
| **pymupdf4llm** | **AGPL-3.0** | GitHub licence API on pymupdf/pymupdf4llm | **DISQUALIFIED** |
| Unstructured | Apache-2.0 | raw.githubusercontent.com/Unstructured-IO/unstructured/main/LICENSE.md | OK |
| marker | Apache-2.0, no additional terms found | raw.githubusercontent.com/datalab-to/marker/master/LICENSE | OK |
| surya | Apache-2.0, no additional terms found | raw.githubusercontent.com/datalab-to/surya/master/LICENSE | OK |
| MinerU | Apache-2.0 **plus additional terms** | raw.githubusercontent.com/opendatalab/MinerU/master/LICENSE.md | Caution, see rejected table |
| PaddleOCR | Apache-2.0 | raw.githubusercontent.com/PaddlePaddle/PaddleOCR/main/LICENSE | OK |
| Tesseract | Apache-2.0 | raw.githubusercontent.com/tesseract-ocr/tesseract/main/LICENSE | OK |
| RapidOCR | Apache-2.0 | raw.githubusercontent.com/RapidAI/RapidOCR/main/LICENSE | OK |
| EasyOCR | Apache-2.0 | GitHub licence API | OK |
| pdfplumber | MIT | raw.githubusercontent.com/jsvine/pdfplumber/stable/LICENSE.txt | OK |
| pypdf | BSD (3-clause style) | raw.githubusercontent.com/py-pdf/pypdf/main/LICENSE | OK |
| pdfminer.six | MIT | raw.githubusercontent.com/pdfminer/pdfminer.six/master/LICENSE | OK |
| Camelot | MIT | raw.githubusercontent.com/camelot-dev/camelot/master/LICENSE | OK |
| OCRmyPDF | MPL-2.0 | raw.githubusercontent.com/ocrmypdf/OCRmyPDF/main/LICENSE | OK, file-level copyleft |
| Qdrant | Apache-2.0 | raw.githubusercontent.com/qdrant/qdrant/master/LICENSE | OK |
| TEI | Apache-2.0 | raw.githubusercontent.com/huggingface/text-embeddings-inference/main/LICENSE | OK |
| onnxruntime | MIT | GitHub licence API | OK |
| PyTorch | BSD-3-Clause style (GitHub reports NOASSERTION because of the multi-copyright header) | raw.githubusercontent.com/pytorch/pytorch/main/LICENSE | OK |
| LangChain | MIT | GitHub licence API | OK |
| LlamaIndex | MIT | GitHub licence API | OK |
| Haystack | Apache-2.0 | GitHub licence API | OK |
| table-serialization-kitchen | MIT | raw.githubusercontent.com/daniel-gomm/table-serialization-kitchen/main/LICENSE | OK |

### Models for local contextualisation and embeddings

From the Hugging Face model API, which reports the model card's declared licence.

| Model | Licence | Gated | Verdict |
|---|---|---|---|
| Qwen/Qwen3-4B | Apache-2.0 | no | **OK, recommended** |
| Qwen/Qwen3-1.7B | Apache-2.0 | no | OK |
| microsoft/Phi-4-mini-instruct | MIT | no | OK |
| mistralai/Mistral-Small-3.2-24B-Instruct-2506 | Apache-2.0 | no | OK |
| mistralai/Mistral-7B-Instruct-v0.3 | Apache-2.0 | no | OK |
| **mistralai/Ministral-8B-Instruct-2410** | **Mistral AI Research License (`license_name: mrl`)** | no | **DISQUALIFIED**, non-commercial |
| google/gemma-3-4b-it | Gemma Terms (`license:gemma`) | manual | Not OSI. Use restrictions and a manual gate. Not recommended for a product we ship. |
| meta-llama/Llama-3.2-3B-Instruct | Llama 3.2 Community Licence | manual | Not OSI. Acceptable-use policy and a 700M MAU clause. Not recommended when Apache-2.0 alternatives exist. |
| BAAI/bge-m3 | MIT | no | OK |
| BAAI/bge-reranker-v2-m3 | Apache-2.0 | no | OK |
| ibm-granite/granite-docling-258M | Apache-2.0 | no | OK |
| ds4sd/CodeFormula (now docling-project/CodeFormula) | MIT | no | OK |

**The clear choice for a local contextualiser is Qwen3-4B**, because it is Apache-2.0, ungated, and has a documented small sibling at 1.7B if 4B is too slow on CPU. Phi-4-mini-instruct at MIT is the credible alternative.

**Two things I did not verify and they matter:**

1. **TEI's complete pooling-mode list.** I read `mean` and `splade` in the README's CLI help excerpt. The full list may be longer. This affects only the late-chunking question, so it does not block anything else. **UNVERIFIED.**
2. **Whether a small local model produces *good* chunk context.** No source I found evaluates small-model-generated chunk context against frontier-generated context or against a deterministic heading path. If we deploy this, we are the first to measure it in our setting. **UNVERIFIED, and this is the largest open question in the report.**

---

## 7. Failure modes and operational cost

Named, because a recommendation that only lists quality is half a recommendation.

**1. The confidently-cited wrong number from a mangled table.** The failure the brief calls out. Mitigation: preserve tables whole, keep `TableData` with cell spans and header flags, serialise row-wise with headers folded in, and make the citation verifier's numeric check compare the answer's number against the cell text rather than the chunk text. Residual risk: a scanned fee schedule where OCR misreads a digit. There is no retrieval fix for that, only a confidence threshold.

**2. The page-straddling chunk cited to the wrong page.** Mitigation: `page_start`/`page_end` plus `charspan`, and the deterministic resolution rule in 3.3. This is cheap to get right at design time and expensive to retrofit, because it needs provenance that only exists if you captured it.

**3. The superseded document still cited.** Mitigation: `is_current` on every chunk, atomic set-payload-by-filter on supersede, and a reconciliation pass. The trap is that ingest time and effective date are different things.

**4. Ingest cost explosion from contextualisation.** On CPU this is the largest single cost in the pipeline, and it lands on generation, which is the slowest operation. Mitigation: prefix KV reuse, batch on rented hardware, and measure before committing. If the measurement is bad, the deterministic contextualisation is the fallback and it costs nothing.

**5. Re-ingestion triggered by a metadata change.** Prevented by the governing rule in 4.1. The trap is a well-meaning engineer prefixing the access label "so the model understands permissions".

**6. Citation highlight that points at the wrong place.** Almost always a coordinate-origin bug. `coord_origin` is `TOPLEFT` by default in current Docling, and PDF space is historically bottom-left. Store the origin with the box.

**7. Chunker version drift silently changing provenance.** Mitigation: `chunker_version` and `parser_version` on every chunk, and treat a version bump as a re-ingestion trigger for the affected documents only.

**8. Overlap-induced duplicate citations.** Two chunks claiming the same sentence means two citations for one fact, which looks like corroboration and is not. Low overlap reduces this. The verifier should deduplicate by `charspan` overlap, not by text equality, because a repeated table header will produce text-identical chunks by design.

**9. The small-to-big expansion blowing the context budget.** If a parent is 4,000 tokens and you retrieve 20 children, you have a 80,000-token prompt. Mitigation: cap parent expansion by a token budget, and prefer the nearest common ancestor over the whole document.

**Operational cost summary, INFERENCE:**

| Item | Cost |
|---|---|
| Storage per chunk | Dominated by the vector (4 KB at 1024 dims float32, less quantized), not the metadata |
| Per-cell table boxes | Move to Postgres. Keeping every cell box in every chunk payload is the one payload that grows without bound. |
| Contextualisation, cloud | Small. Anthropic's published model is about 11x document token amplification before caching. |
| Contextualisation, office | Large. Days, not hours, for a big corpus. Parameterised in section 2.3. |
| Small-to-big index growth | Roughly 2x if parent text is stored in addition to child text. Avoidable by storing parents once and referencing them. |
| Deterministic contextualisation | Free. A few hundred bytes per chunk. |

---

## 8. What to measure with 60 questions

Given section 1.8, do not spend the budget on strategy A/B tests. Spend it on pass/fail mechanism checks, which is what 60 questions can support.

Five checks, each a single golden question with a known right answer:

1. **Table cell retrieval.** A question whose answer is one cell in a table. Pass if the retrieved chunk contains the whole row with its header, and its recorded `page_start` equals the page the table is on.
2. **Page straddle.** A question whose evidence sits near a page break. Pass if the recorded page range contains the evidence page and the highlight lands on the right page.
3. **Superseded document.** A question whose answer changed between two document versions. Pass if the answer cites the current version and the old version is never retrieved.
4. **Scanned page.** A question from a scanned document. Pass if the answer is correct and the highlight is at least on the correct page, with `is_ocr: true` recorded.
5. **Heading disambiguation.** Two sections in one document use the same words for different things. Pass if the retrieved chunk's `heading_path` is the one the question meant.

Plus the one measurement worth doing properly, because it decides real money:

**Contextualisation ablation.** Run the golden set four times: no contextualisation; deterministic only (title + heading path + page + caption); deterministic plus generated context from a local model; deterministic plus generated context from a frontier model. Four numbers, 60 questions each. That tells us whether the local LLM call earns its ingest cost, and it is a measurement nobody has published.

---

## 9. Open questions

1. **Does a small local model generate good enough chunk context?** Unverified. No published evaluation exists that I could find. This is the report's biggest gap and the ablation in section 8 is how to close it.
2. **What is the actual CPU prompt-processing and generation throughput on the m7i.4xlarge?** I could not find a primary source and did not measure. Run `llama-bench` before committing to contextualisation. Everything in section 2.3 is a parameterised model, not a prediction.
3. **Does our local inference server reuse the KV cache for a shared prefix across sequential requests, and by how much?** The whole local cost case rests on this. Verify, do not assume.
4. **What is the optimum table serialization for `bge-m3` specifically?** Gomm's central finding is that there is no universal optimum and the choice is embedding-model-dependent. He did not test `bge-m3`.
5. **`bge-m3`'s behaviour on 512-token chunks against 256.** The 2026 study's 384-to-512 recommendation came from `e5-large-v2` and `bge-m3` on chemistry papers. It should transfer; it is not verified for our corpora.
6. **TEI's complete pooling-mode list**, which decides whether late chunking needs a serving-layer change. Only relevant if we want to pursue late chunking.
7. **Does the LlamaIndex AutoMergingRetriever evaluation show a real gain?** I could not read it. Small-to-big is recommended here on mechanism and on Chroma's precision/recall trade-off finding, not on a head-to-head benchmark. Anyone who wants a number should read that notebook first.
8. **The compliance question for MinerU-style bespoke licence terms.** Not legal advice and I am not giving any. If MinerU is ever wanted, the additional terms need a lawyer, not an engineer.

---

## Sources

All retrieved 2026-10-11.

**Measured studies and benchmarks**

1. Smith, B. & Troynikov, A. "Evaluating Chunking Strategies for Retrieval." Chroma technical report, 3 July 2024. https://www.trychroma.com/research/evaluating-chunking
2. Hong, K., Troynikov, A. & Huber, J. "Context Rot: How Increasing Input Tokens Impacts LLM Performance." Chroma technical report, 14 July 2025. https://www.trychroma.com/research/context-rot
3. Jimeno Yepes, A. et al. "Financial Report Chunking for Effective Retrieval Augmented Generation." Unstructured Technologies, arXiv 2402.05131v2. https://arxiv.org/html/2402.05131v2
4. Qu, R., Tu, R. & Bao, F. "Is Semantic Chunking Worth the Computational Cost?" arXiv 2410.13070, NAACL 2025 Findings. https://arxiv.org/html/2410.13070v1
5. Chen, T. et al. "Dense X Retrieval: What Retrieval Granularity Should We Use?" arXiv 2312.06648, EMNLP 2024. https://arxiv.org/html/2312.06648v3
6. Amiri, M. & Bocklitz, T. "Chunk Twice, Embed Once: A Systematic Study of Segmentation and Representation Trade-offs in Chemistry-Aware RAG." arXiv 2506.17277v2. https://arxiv.org/html/2506.17277v2
7. Günther, M. et al. "Late Chunking: Contextual Chunk Embeddings Using Long-Context Embedding Models." arXiv 2409.04701. https://arxiv.org/abs/2409.04701
8. Gomm, D. & Hulsebos, M. "Metadata Matters in Dense Table Retrieval." ELLIS workshop 2025. https://www.daniel-gomm.com/blog/2025/Table-Serialization-Kitchen/ and https://openreview.net/forum?id=rELWIvq2Qy
9. "Which Table Format Do LLMs Understand Best? (Results for 11 Formats)." Improving Agents, 30 September 2025. https://www.improvingagents.com/blog/best-input-data-format-for-llms/

**Vendor-run measurements with disclosed method**

10. Ford, D. "Introducing Contextual Retrieval." Anthropic, 19 September 2024. https://www.anthropic.com/engineering/contextual-retrieval
11. "Enhancing RAG with contextual retrieval." Anthropic Claude Cookbook. https://platform.claude.com/cookbook/capabilities-contextual-embeddings-guide
12. Han, S. "Finding the Best Chunking Strategy for Accurate AI Responses." NVIDIA Technical Blog, 18 June 2025. https://developer.nvidia.com/blog/finding-the-best-chunking-strategy-for-accurate-ai-responses/

**Official documentation, mechanism verified**

13. Docling, "Chunking". https://docling-project.github.io/docling/concepts/chunking/
14. Docling, "Docling document". https://docling-project.github.io/docling/concepts/docling_document/
15. docling-core source, `docling_core/types/doc/common/reference.py` (ProvenanceItem), `.../items/node.py` (prov on NodeItem), `.../items/table/table_data.py` (TableData, TableCell), `.../transforms/chunker/doc_chunk.py` (DocMeta), `.../transforms/chunker/page_chunker.py` (PageChunker). https://github.com/docling-project/docling-core
16. Qdrant, "Configure Multitenancy". https://qdrant.tech/documentation/manage-data/multitenancy/
17. Unstructured, "Document elements and metadata". https://docs.unstructured.io/open-source/concepts/document-elements
18. Unstructured, "Chunking". https://docs.unstructured.io/open-source/core-functionality/chunking
19. LlamaIndex, "Auto Merging Retriever". https://developers.llamaindex.ai/python/framework/integrations/retrievers/auto_merging_retriever/ (evaluation section could not be read; see open question 7)
20. Hugging Face Text Embeddings Inference README. https://raw.githubusercontent.com/huggingface/text-embeddings-inference/main/README.md

**Licence sources**

21. Raw LICENSE files, listed with URLs in section 6.
22. GitHub licence API, `https://api.github.com/repos/{owner}/{repo}/license`, for PyMuPDF, pymupdf4llm, docling-parse, EasyOCR, onnxruntime, PyTorch, LangChain, LlamaIndex, Haystack.
23. PyMuPDF licensing statement. https://pymupdf.readthedocs.io/en/latest/about.html
24. Hugging Face model API, `https://huggingface.co/api/models/{model_id}`, for all model licence tags and gating status.
25. MinerU additional terms. https://raw.githubusercontent.com/opendatalab/MinerU/master/LICENSE.md
26. Mistral AI Research License reference from the Ministral-8B model card. https://mistral.ai/licenses/MRL-0.1.md

**Internal context (read-only)**

27. `office-inference-core/docs/build-brief.md`, sections 4, 7, 10, 13.
28. `office-inference-core/docs/architecture.md`, lanes and profiles.
