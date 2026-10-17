# Newer retrieval approaches, judged by whether they are real

Research report for the Office Inference retrieval stack. Written 2026-10-16. No code was changed.

The stack I am judging: one deployment per client, 10 to 200 users, documents a small business actually has (invoices, policies, contracts, spreadsheets, email exports), BAAI/bge-m3 on CPU through TEI with an 8,192-token limit and `--max-batch-tokens 4096`, a hybrid vector plus full-text retriever, reranking at the model provider because the CPU reranker is too slow, a frontier model through Amazon Bedrock, a tamper-evident record of every retrieval, prompt and answer, and an access label plus page range on every chunk. No runtime call leaves the box except to the model provider.

The short answer. The current defect is not the 384 to 512 token window. It is that oversized elements have no policy and the answer path has no token budget. Fix those first. After that, the only newer technique with a measured gain worth a pilot is contextual retrieval, and only after the free deterministic half has been measured. Late chunking is a poor fit for this stack for a specific serving reason, not a fashion reason. GraphRAG and unbounded agentic retrieval cost too much and are too hard to audit at this scale. Long context does not replace chunking because it cannot enforce per-chunk access labels or produce page citations.

## What I could not verify

- Anthropic's per-dataset contextual retrieval results. The appendix is an image. I can quote the averages, not the per-domain numbers. [1]
- A dollar cost for GraphRAG indexing that transfers to this corpus. Microsoft publishes relative costs, not a reproducible dollar figure. LazyGraphRAG's 0.1 percent claim has no independent replication I could find. [15]
- CPU latency for ColBERT MaxSim on this box. The Qdrant benchmark ran ColBERT server-side on Qdrant Cloud and the cross-encoder locally on CPU, so the ratio is not a clean CPU-to-CPU comparison. [19]
- A head-to-head benchmark of parent-document or sentence-window retrieval against fixed chunking. The pattern is documented in frameworks but I found no independent measurement. [38][39]
- Whether TEI's `/embed_all` token-level route is practical under the 4096 batch-token cap and the pinned TEI version. The main `/embed` path returns pooled vectors, and the pooling modes are cls, mean, splade and last-token. [37][45]
- Long-context model quality on small-business contracts and invoices. The evidence is synthetic needle tests and academic corpora, not this document mix. [29][30]
- Bedrock-specific prices for every current model. I used Anthropic's list price for Claude Sonnet 5.5 as a floor. [32]
- Voyage's benchmark methodology and corpus. The comparison is vendor-run and the corpus is not disclosed in the post. [33]
- Whether the 2026 chunking taxonomy has completed peer review. It is an arXiv preprint from February 2026. [6]
- Whether a document in the client corpus can contain chunks with different access labels. If labels are per document or folder, whole-document methods are label-safe. If a document can mix labels, they are not. The brief does not settle this, and it changes the verdict on late chunking and contextual retrieval.
- The token distribution of client documents and how often an element exceeds the 512-token cap. Without the corpus, the size of the oversized-element problem is unknown. The fix does not depend on the number.

## Substance, with the measurements

### The incumbent the newer methods have to beat

The current design already uses structure-aware splitting at 384 to 512 tokens, which is the range the strongest studies support. The evidence for that baseline is better than the evidence for most of the newer methods. The recommendation is low or zero overlap, and the gaps are the oversized-element policy and the answer budget, not the choice of retrieval paradigm.

The Unstructured FinanceBench study compared element-based chunking with fixed 128, 256 and 512 token windows on 80 SEC reports, 141 questions and 11,787 pages. Element-based chunking raised manual answer accuracy from 48.23 to 53.19 and BLEU from 0.250 to 0.400, but it did not win on page-level hit rate: 67.38 against 73.05 for 256-token chunks. It got the better answer with 20,843 chunks, against 32,051 for the 256-token baseline and 16,046 for the 512-token baseline. [9]

Chroma's 2024 report found that a well-parameterised recursive splitter at 200 tokens with no overlap was consistently strong across recall, precision and token-level IoU, and that overlap reduced IoU because it retrieves redundant text. The popular 800-token, 400-overlap default had below-average recall and the worst scores on the other metrics. [8]

The 2026 reproduction by Zhou et al. is the most useful recent study because it compares segmentation methods and embedding order under one protocol. It evaluated fixed-size, sentence, paragraph, semantic, proposition and LumberChunker segmentation on six BEIR datasets and GutenQA, with four embedding models. For in-corpus retrieval, simple structure-based methods beat the LLM-guided alternatives. For in-document retrieval, LumberChunker won. Late chunking improved in-corpus results but degraded in-document results. [6]

The "Chunk Twice, Embed Once" study covered 1,820 configurations over 952 chemistry question-answer pairs. It found that 128-token chunks clearly underperformed, that 384 to 512 tokens was the practical range, and that overlap was generally neutral to negative. [10]

So the incumbent is not the thing to replace. The defect is narrower. A table that exceeds the token cap has no defined split policy, and the answer path has no maximum context size. Those are engineering gaps. A new retrieval paradigm does not close them, and several of the newer methods make them worse by adding generated text or whole-document context to the prompt.

### Late chunking and long-context embedding models

**What it is.** Late chunking embeds the whole document through the transformer first, then applies chunk boundaries to the token sequence and pools each span into a chunk vector. The chunk embedding carries information from the whole document without an extra LLM call. The method needs a long-context embedding model and mean pooling over token states. It does not need additional training. [3]

**The measured claim.** Jina AI's paper reports an average improvement of 3.63 percent relative, or 1.9 absolute nDCG@10, when sentence-boundary chunking is replaced by late chunking. The fixed-size version gained 1.8 absolute, and the semantic-boundary version gained 1.5 absolute. The evaluation used three embedding models and four BEIR datasets. [3]

The company blog shows the per-dataset numbers behind that average: SciFact 64.20 to 66.10, NFCorpus 23.46 to 29.98, FiQA 33.25 to 33.84, TRECCOVID 63.36 to 64.70, and Quora unchanged at 87.19. On TRECCOVID, embedding the whole document without chunking scored 65.18, higher than late chunking. On NFCorpus, no chunking scored 30.40, also higher. Late chunking beat naive chunking, but it did not beat the no-chunking baseline on those two datasets. [4]

An independent 2025 workshop preprint by Merola and Singh is less kind. On NFCorpus, late chunking with BGE-M3 collapsed: nDCG@10 fell from 0.225 to 0.067 with fixed-size chunks and from 0.240 to 0.081 with semantic chunks. Jina-V3 and Stella were roughly flat. The authors also compared late chunking with contextual retrieval on the same NFCorpus subset: contextual retrieval scored 0.308 nDCG@10 against 0.294 for late chunking, at a higher computational cost. [5]

The likely reason for the BGE-M3 collapse is the pooling mismatch. BGE-M3 uses CLS pooling. Late chunking is defined for mean pooling over token spans. Applying mean pooling to a CLS-trained model produces a vector the model was not trained to produce. The 2025 paper does not report that ablation, so this is an inference from the model configuration and the result. [3][36]

The 2026 reproduction confirms that the effect is task-dependent. Late chunking improved average in-corpus nDCG by 6.41 percent with Jina-v2, 6.05 percent with Jina-v3, 2.78 percent with Nomic and 5.96 percent with E5-large. On in-document retrieval it degraded performance by 5.16, 29.82, 53.17 and 3.83 percent respectively. Some individual dataset and model combinations moved by tens of percent in either direction. [6]

**What it costs.** You need a token-level encoder path and a full re-embed. The client also has to align chunk boundaries to token spans. On this box the main embedding path is TEI's `/embed`, which returns pooled vectors. TEI's source also exposes `/embed_all`, which returns unpooled token vectors, so a late-chunking client could pool spans itself. That route is not the path this stack uses, and it does not fix the BGE-M3 pooling mismatch. [37][45]

The 8,192-token context is also awkward against `--max-batch-tokens 4096`. TEI defines that setting as the total number of tokens in a batch, so a single 8,192-token document cannot share a batch with other work, and the pinned version's truncation behaviour needs to be checked. [37] A single 8K forward pass is much more expensive than a 512-token chunk, and attention cost grows with the square of the sequence length. I did not measure the throughput on this box, so treat the size of the slowdown as unverified.

**Licence and hardware.** The method is free to implement. Jina's late-chunking code is Apache-2.0. [42] The models that make the method work well are not all shippable: jina-embeddings-v3 is CC-BY-NC-4.0, and jina-embeddings-v4 is under the Qwen Research License. [35][44] BGE-M3 is MIT, which is the good case, but it is the model whose pooling does not match the method. [36] Nomic is the licence-clean mean-pooled option the late-chunking paper tested, but its gain was the smallest of the four models in the 2026 reproduction. [6]

**Fit.** Poor. The stack already has the right model for everything except this method. Late chunking would require a second embedding path, a re-embed, and a model or pooling change, and the measured gain is small and can reverse on in-document questions. I would not build it for this stack. The only exception is a client whose questions are almost entirely "which document is about X" rather than "what does clause 7 say," and even then the gain is not large enough to justify a new serving component.

**Long-context embedding models.** BGE-M3 already supports 8,192 tokens and is MIT. Qwen3-Embedding supports 32,768 tokens and is Apache-2.0. [34] Jina v4 supports 32,768 tokens but is non-commercial. [35] Voyage's contextualised chunk model is hosted, so it is out on the no-egress and ownership constraints. [33]

The important measurement is not the context window. It is that whole-document embeddings lose detail even at 8,192 tokens. The late-chunking paper's own appendix compares an 8,192-token whole-document embedding with 512-token chunks on the same model. On NarrativeQA, whole-document retrieval scored 32.73 nDCG@10 against 47.63 for 512-token chunks. On 2WikiMultiHopQA it was 70.32 against 86.30. On QMSum it was 36.81 against 48.34. SummScreenFD was the exception, 91.24 against 89.71. [3]

I found no published benchmark showing that a 32K whole-document embedding beats chunking on business-document retrieval. Qwen3-Embedding's published results are MTEB retrieval scores, not a long-document chunking ablation. [34] A larger context window is not evidence that one vector can replace many chunks. It is evidence that the model can read more text before compressing it into the same vector.

### Contextual retrieval

**What it is.** For each chunk, an LLM receives the whole document and the chunk and writes 50 to 100 tokens that situate the chunk in the document. That text is prepended to the chunk before embedding and before building the BM25 index. The point is to disambiguate the chunk, not to summarise the document. [1]

**The measured claim.** Anthropic's September 2024 post reports a reduction in top-20 retrieval failure rate from 5.7 percent to 3.7 percent with contextual embeddings, a 35 percent reduction. Adding contextual BM25 took it to 2.9 percent, a 49 percent reduction. Adding reranking took it to 1.9 percent, a 67 percent reduction. The evaluation covered codebases, fiction, arXiv papers and science papers, used Gemini Text 004 as the embedding model, retrieved the top 150 and reranked to the top 20. It is a vendor-run measurement with a disclosed method. [1]

Anthropic's cookbook reports a smaller, more specific result: on 9 codebases and 248 queries, contextual embeddings improved Pass@10 from about 87 percent to about 95 percent. [2]

The independent evidence is thinner than the vendor post suggests. Merola and Singh, in a 2025 workshop preprint, compared contextual retrieval with late chunking on a 20 percent NFCorpus subset, with 50 queries and about 300 documents, using a local Phi-3.5-mini model for both contextualisation and generation. Contextual retrieval with rank fusion and reranking scored 0.308 nDCG@10 against 0.288 for the same rank-fusion pipeline without generated context, a 0.020 gain. Late chunking scored 0.294. The authors report that contextualising long documents consumed around 20 GB of VRAM and forced a smaller batch size. [5]

I found no independent in-document evaluation of Anthropic's method. The 2026 reproduction by Zhou et al. evaluates contextualised chunking, but its "contextualized chunking" is late chunking, not LLM-generated chunk context. It should not be used as evidence for Anthropic's method. [6]

**What it costs.** Anthropic's published estimate is USD 1.02 per million document tokens for the one-time contextualisation, under assumptions of 800-token chunks, 8k-token documents, 50 tokens of instructions and 100 tokens of context per chunk, with prompt caching. [1] The transferable number is the amplification, and this is my arithmetic: an 8k-token document split into 10 chunks sends roughly 10 times (8,000 + 800 + 50) tokens, about 88,500 input tokens, through the model to contextualise one 8k-token document. That is about 11 times the document's own token count before caching. [1]

On this stack the LLM is at Bedrock, so the hardware is not the problem. The cost is per-chunk provider work at ingest, plus re-ingest whenever a document changes, plus the audit record. If the audit chain stores the full prompt text, each contextualisation call records the whole document again. For a 10k-token document with 12 chunks, that is about 120k tokens of audit text per document per ingest. My arithmetic, assuming roughly four bytes per token, puts that at about 480 KB per document. At 10,000 documents, one full pass is about 4.8 GB of audit text. Weekly re-ingest of changed documents would accumulate from there. [1]

**Licence and hardware.** The method is free. The model is the Bedrock model the stack already uses. There is no new local model and no new licence. The work runs at the provider, which is allowed by the no-egress rule. [1]

**The access-label problem.** This is the part the published evaluations do not test. The method sends the whole document to the model for every chunk. If a document can contain chunks with different access labels, the context for an allowed chunk can include text from a restricted chunk. That restricted text can end up in the generated context, in the embedding of the allowed chunk, and in the audit record. If labels are per document or per folder, the method is safe. If a contract has a `company` body and an `executive` compensation appendix, it is not. The brief says every chunk carries a label but does not say whether labels can differ inside one document. This is the first thing to settle before any pilot.

**Fit.** The deterministic half is free and should be adopted now: title, full heading path, page label, table caption and column headers, prepended before embedding. Anthropic's own ablations found that generic document summaries gave very limited gains and summary-based indexing performed poorly, which supports the narrower disambiguation approach. [1] The generated half is worth a pilot on 60 questions, comparing four runs: no context, deterministic only, deterministic plus a cheap Bedrock model, and deterministic plus the frontier model. Until that shows a gain over the free baseline, it should not be the default.

### Semantic chunking

**What it is.** Split the document into sentences or small windows, embed them, and cut where the similarity between adjacent units drops below a threshold or percentile. The chunks follow meaning rather than a token counter. [7][8]

**The measured claim.** Qu, Tu and Bao evaluated semantic chunking on document retrieval, evidence retrieval and retrieval-based answer generation. Their conclusion is direct: the computational cost is not justified by consistent gains, and on non-synthetic datasets fixed-size chunking often performed better. [7]

Chroma's results show the same inconsistency. The default Kamradt semantic chunker had a recall of 83.6 percent, below the 88.1 percent of a 200-token recursive splitter. Their `ClusterSemanticChunker` at 200 tokens had the best precision and IoU, 8.0 and 8.0, but its recall was 87.3 percent, slightly below the recursive baseline. The LLM-based chunker had the highest recall at 91.9 percent, but it is an LLM pass over the corpus and its cost was not measured. [8]

The 2026 reproduction again found that structure-based methods beat the semantic and LLM-guided alternatives for in-corpus retrieval. [6]

**What it costs.** You embed every sentence or window at ingest, then compute a global threshold or clustering. The `ClusterSemanticChunker` explicitly depends on global corpus statistics and has to recompute chunks when data is added. [8] That breaks incremental ingestion and stable chunk IDs. The CPU cost is a full extra embedding pass over the corpus, on top of the embedding pass you already do for retrieval.

**Licence and hardware.** The LangChain and LlamaIndex implementations are MIT, and the work is CPU-only. There is no licence or hardware barrier. The barrier is that the measured gain is not there. [46][47]

**Fit.** Skip it as the default. It is a more expensive way to make a decision that structure already makes better. If a client has documents with no headings and no reliable layout, semantic boundaries might be worth a small experiment, but it should not be the production default.

### Parent-document and sentence-window retrieval

**What it is.** Retrieve a small unit for precision, then give the generator a larger unit for context. Sentence-window stores a sentence with its neighbours. Parent-document stores a child chunk with a pointer to its parent. Auto-merging replaces sibling chunks with their parent when enough siblings are retrieved. [38][39]

**The measured claim.** I found no independent head-to-head benchmark of these patterns against fixed chunking. The framework documentation describes the mechanism but does not provide a controlled quality result I could verify. [38][39] The indirect evidence is Chroma's precision and recall trade-off: small chunks improve precision and IoU but can reduce recall, which is the problem small-to-big is meant to solve. [8]

**What it costs.** If you store the parent text in addition to the child text, the index grows roughly twofold. At query time you expand each hit, so the prompt grows and overlapping text can crowd out other evidence. Page citations become ambiguous when the parent spans pages. There is no model cost, only storage and prompt budget.

**Licence and hardware.** LangChain and LlamaIndex are MIT. There is no new model and no new hardware. [46][47]

**Fit.** Adopt the cheap version, not the full pattern. Store `prev_chunk_id` and `next_chunk_id` at ingest and expand one neighbour at query time under a hard token cap. That gives most of the context benefit without a parent store. A full parent-document store is only worth it if the 60-question set shows that answers need a whole section and the budget can absorb it. This is a lower-risk change than any of the model-based methods.

### RAPTOR and hierarchical summarisation

**What it is.** Recursively cluster chunks, summarise each cluster with an LLM, embed the summaries, and repeat until a tree is built. At query time, retrieve from the collapsed tree across levels, so a question can match a leaf chunk or a high-level summary. [11]

**The measured claim.** The RAPTOR paper's headline is a 20 percent absolute improvement on QuALITY when coupled with GPT-4. That number is not a comparison against a chunking baseline. It is a comparison against the previous best published result. The controlled baseline table in the same paper, with UnifiedQA-3B as the reader, is much smaller. On QuALITY, RAPTOR with SBERT scored 56.6 percent against 54.9 percent without the tree, a 1.7 point gain. On QASPER it scored 36.70 against 36.23, a 0.47 point gain. On NarrativeQA its ROUGE-L was 30.87 against 29.26, a 1.61 point gain. [11]

The independent 2026 GraphRAG-Bench, using GPT-4o-mini, is less favourable. RAPTOR scored 49.25 on fact retrieval against 58.76 for basic RAG, 38.59 on complex reasoning against 41.35, and 47.10 on contextual summarisation against 50.08. It did not win a task in that benchmark. [13]

One 2025 clinical benchmark found a narrower positive case. On a clinical trial question-answering dataset, the best RAPTOR configuration with local small models reached 84.72 percent on complex questions against 52.26 percent for GPT-4o. The best local RAPTOR result on QuALITY complex questions was about 52.96 percent, against 83.29 percent for GPT-4o. The gain did not generalise. [12]

**What it costs.** The tree needs an LLM summary for every cluster at every level. The paper reports linear scaling in tokens and build time, but no dollar figure. [11] The tree is corpus-global, so adding a document can change the clusters and the summaries. Incremental ingestion is awkward. The paper also reports that about 4 percent of summaries contained minor hallucinations, which is a direct problem for an audit chain that treats generated text as evidence. [11]

**Licence and hardware.** The code is MIT. [41] The summariser can be a provider model or a local CPU model. A local CPU model makes the build slow, and a provider model makes it a per-cluster cost. There is no GPU requirement.

**Fit.** Skip it as the default. The controlled gains are small, the independent benchmark is negative, and the generated summary nodes do not have a page range. A summary that spans five documents is not a citation. The one case worth a pilot is a client whose questions are global and thematic ("what are the main risks across these contracts") and whose documents are long and narrative. Even then, measure it against the structure-aware baseline before building the tree.

### ColBERT and late-interaction retrieval

**What it is.** Represent each chunk as one vector per token, then score a query by summing, for each query token, its best match against the document tokens. That is MaxSim. ColBERTv2 adds residual compression and reduces the space footprint of late interaction by 6 to 10 times. [17] PLAID adds centroid pruning and reports up to 45 times lower CPU latency than vanilla ColBERTv2, with tens or a few hundreds of milliseconds at 140 million passages. [18]

**The measured claim.** The Qdrant hybrid-comparisons benchmark holds the candidate pool constant and varies only the rescorer. On 100k MS MARCO passages, ColBERT MaxSim and a local cross-encoder were a practical tie on nDCG@20, about 0.776 against 0.779. ColBERT's p50 was around 0.1 seconds against about 2 seconds for the cross-encoder. On dbpedia the two were also a tie, and on scifact the winner moved with the quantisation configuration. Against plain RRF, the second stage bought about 0.055 to 0.062 nDCG. [19]

That is a real result, but it is a vendor-run benchmark from the Qdrant ecosystem, and the ColBERT timing ran on Qdrant Cloud while the cross-encoder ran locally on CPU. The latency ratio is not a clean CPU-to-CPU comparison. [19]

**What it costs.** You need a new encoder, a new vector type, a re-embed of the corpus, and a serving component. TEI has no MaxSim scoring route. It can return token vectors through `/embed_all`, but it cannot score them. [37][45] FastEmbed serves only two late-interaction models, `colbert-ir/colbertv2.0` and `answerdotai/answerai-colbert-small-v1`, and GTE-ModernColBERT needs PyLate. [43] Qdrant supports multivectors and MaxSim, so the vector store is not the blocker. [20]

Storage depends on the model. A 96-dimensional model at 48 sub-vectors per chunk is about 18 KB per chunk in float32, or about 2.3 KB with 4-bit compression. At 500k chunks that is about 1.1 GiB compressed. BGE-M3's 1024-dimensional ColBERT head is much larger, about 91.5 GiB at 500k chunks in float32 and about 11.4 GiB with 4-bit compression. This is my arithmetic from the component sizes. The small model is the only sensible choice here. [19][20]

**Licence and hardware.** `answerai-colbert-small-v1` is Apache-2.0, `colbertv2.0` is MIT, and `GTE-ModernColBERT-v1` is Apache-2.0. `jina-colbert-v2` is CC-BY-NC-4.0, and `Reason-ModernColBERT` is CC-BY-NC-4.0. [21][22][23][24][25] The CPU work is a small encoder plus a matrix multiply, so the hardware fits. The problem is the extra component and the licence risk at the frontier.

**Fit.** Skip it for now, because reranking already happens at the provider and the measured quality difference over the cross-encoder is a tie. Pilot it only if provider reranking becomes a cost or latency problem. In that case `answerai-colbert-small-v1` is the right candidate: Apache-2.0, 96 dimensions, 33 million parameters, FastEmbed-served, and small enough to run on CPU. It preserves the chunk payload, the access labels and the page ranges, which is a real advantage over graph and summary methods.

### GraphRAG

**What it is.** Use an LLM to extract entities and relationships from the corpus, build a graph, detect communities, and pre-generate a summary for each community. At query time, global search maps the question over community summaries and reduces the partial answers. Local search uses the graph to find related entities and source text. [14]

**The measured claim.** Microsoft's paper evaluates global sensemaking on two corpora in the one-million-token range, podcast transcripts and news articles, with 125 generated questions per corpus. It reports that GraphRAG beats a vector RAG baseline on comprehensiveness and diversity, as judged by an LLM. That is a global summarisation task, not fact retrieval. [14]

The independent RAG versus GraphRAG study finds complementary strengths rather than a winner. RAG is better on single-hop and detail-oriented questions. GraphRAG is better on multi-hop and reasoning-intensive questions. GraphRAG has higher construction cost, retrieval latency and storage footprint, and its quality is sensitive to the model used to build the graph. [16]

The 2026 GraphRAG-Bench is more specific. Basic RAG scored 58.76 on fact retrieval against 36.92 for Microsoft GraphRAG global search. On complex reasoning, a local GraphRAG configuration scored 50.93 against 41.35 for basic RAG. On contextual summarisation, GraphRAG scored 64.40 against 50.08 for RAG. The benchmark also found that more relationships do not necessarily help, and that GraphRAG can blow up the context with entities and source snippets. [13]

Microsoft's LazyGraphRAG claims indexing costs identical to vector RAG and 0.1 percent of full GraphRAG, with query costs comparable to vector RAG for comparable quality. That is a vendor claim from November 2024, and I found no independent replication. [15]

**What it costs.** Graph construction needs an LLM pass over the corpus to extract entities and relationships, then entity resolution, community detection and community summaries. That is more provider work than contextual retrieval, not less. The graph and the summaries add storage. Incremental updates are hard because a new document can change communities and summaries. A graph database adds another service to operate. There is no GPU requirement, but the provider bill and the operational surface are both larger than the vector index.

**The access-label problem.** A community summary can combine documents or chunks with different access labels. The summary itself has no single page range. To keep the label boundary, you need a separate graph per label, or you need to filter the graph before summarisation. Both are more complex than filtering chunks at query time. This is the same problem as RAPTOR, with more moving parts.

**Licence and hardware.** Microsoft's GraphRAG code is MIT. [40] The graph construction can use the same Bedrock model as generation. The hardware fits on CPU. The constraints are cost, operational complexity and the audit/citation mismatch.

**Fit.** Skip it for the standard small-business deployment. The questions that justify GraphRAG are multi-hop or global, and most of this client base asks single-hop document questions. A pilot is only justified for a client with a measured multi-hop gap and a relationship-heavy corpus, such as a set of contracts with many parties and cross-references. Even then, the summary nodes need page-level provenance before they can be cited.

### Agentic and iterative retrieval

**What it is.** The model decides what to retrieve next, grades the evidence, reformulates the query, and repeats until it decides it has enough. The bounded version is a fixed retrieve-read-retrieve cascade. The unbounded version is a ReAct-style loop. [26]

**The measured claim.** The 2026 diagnostic study on ChemKGMultiHopQA found that iterative retrieval beat even an ideal static "gold context" baseline by up to 25.6 percentage points, especially for non-reasoning models. It also found failure modes: incomplete hop coverage, distractor latch, early stopping, anchor carry-drop, and high composition failure rates even with perfect retrieval. The average output tokens for GPT-5 rose from 713 with gold context to 5,592 with iterative retrieval, about 7.8 times. [26]

A self-published cost benchmark over 200 MuSiQue queries meters every LLM call. Naive RAG scored 0.275 EM at USD 0.0043 per query with one call and about 2.0 seconds p50. Iterative retrieval scored 0.505 EM at USD 0.0138 with 2.9 calls and about 7.0 seconds. A ReAct agent scored 0.600 EM at USD 0.0233 with 5.0 calls and about 8.7 seconds. Agentic RAG cost about 2.5 times more per correct answer. The fixed iterative loop got most of the ReAct gain for 58 percent of the calls, so the knee is at retrieving more than once rather than at letting the model steer. [27]

A June 2025 retrieval benchmark found that off-the-shelf agentic retrieval products were weaker than a tuned vector retrieval pipeline on BEIR-style corpora. That is one vendor's benchmark, but it is a useful counterweight to the claim that agentic retrieval is automatically better. [28]

**What it costs.** Multiple provider calls per question, more output tokens, higher latency, and more audit records. On this stack the generation is at Bedrock, so the latency is not the local CPU problem it would be in an offline build. The cost is still real. The MuSiQue benchmark measured 2.5 times the cost per correct answer. The chemistry study measured about 8 times the output tokens. The audit chain records each step and each prompt, so one answer becomes several evidence records. [26][27]

**Licence and hardware.** LangChain and LlamaIndex are MIT. The model is the provider model. There is no new local model and no GPU requirement. [46][47]

**Fit.** Skip unbounded loops. Pilot a bounded two-step cascade only for questions that are demonstrably multi-hop, such as reconciling an invoice with a contract and a payment record. The second step should be conditional on a retrieval-confidence check, hard-capped at two retrievals, and recorded as a separate audit event. Most small-business questions do not need it, and the failure modes produce confident wrong answers, which is the failure this product is built to avoid.

### Long-context models

**What it is.** Put a large part of the corpus, or a whole long document, into the model's context window and let the model find the answer without a retrieval index. Current frontier models advertise 1M-token windows. Anthropic's current model family lists 1M tokens for all four models, with Claude Sonnet 5.5 at USD 2 per million input tokens and USD 10 per million output tokens. [32] Earlier models in 2025 already advertised 1M tokens, and Llama 4 advertised 10M. [29]

**The measured claim.** NoLiMa tested 13 models that claim at least 128K tokens of context with questions that require latent associations rather than literal matches. At 32K tokens, 11 of the 13 dropped below 50 percent of their short-context baseline. GPT-4o fell from 99.3 percent to 69.7 percent. [30]

Chroma's Context Rot report tested 18 models, including GPT-4.1, Claude 4, Gemini 2.5 and Qwen3. It found that performance degrades as input length grows even on simple tasks, and that focused prompts of about 300 tokens outperform full prompts of about 113K tokens on LongMemEval. [29]

The 2024 RAG versus long-context study found that long-context models beat RAG by 7.6 percent for Gemini-1.5-Pro, 13.1 percent for GPT-4o and 3.6 percent for GPT-3.5-Turbo on average, when given the full context. RAG was much cheaper. Their Self-Route hybrid kept most of the long-context quality using 38 to 61 percent of the tokens. [31]

The late-chunking appendix is the cleanest evidence that chunking still matters for retrieval. At 8,192 tokens of context, whole-document embeddings lost to 512-token chunks on NarrativeQA, 2WikiMultiHopQA and QMSum. [3]

**What it costs.** This is my arithmetic from the list price. A 1M-token prompt at USD 2 per million input tokens costs USD 2.00. A RAG prompt with five 500-token chunks costs about USD 0.005. The ratio is about 400 times. At 300K tokens the long-context prompt costs USD 0.60, about 120 times the five-chunk prompt. At 1,000 questions a month, that is USD 2,000 against about USD 5. Prompt caching can reduce repeated input cost by up to 90 percent, but the first cache write costs more, the cache has a lifetime, and a cached corpus does not solve the access-label problem. [1][32]

**The access-label and citation problem.** A single prompt containing the whole corpus cannot enforce per-chunk access labels. Every document in the prompt is visible to the model, including documents the user is not allowed to see. If you build a per-user filtered prompt, you are back to retrieval, just with a very large prompt. The model also does not return a page range. It can quote text, but mapping the quote back to a page and a chunk is a separate alignment problem. The audit chain would record the full prompt for every answer, which is both large and a copy of every document in the prompt. [1][32]

**Licence and hardware.** The model is the provider model, so there is no local hardware and no new licence. The provider terms apply. The constraint is not hardware or licence. It is the access label and the citation contract.

**Fit.** Do not use it as a chunking replacement. Use it selectively, after retrieval has already selected a single document the user is allowed to see, for a summarisation or "read the whole contract" request. That is a generation-side feature with a token budget, not a retrieval architecture. It does not remove the need for chunks, labels or page ranges.

### Summary table

| Approach | Strongest measured claim | Main cost | Licence and hardware | Fit here |
|---|---|---|---|---|
| Structure-aware, page-bounded chunking | Element chunking raised answer accuracy from 48.23 to 53.19 on FinanceBench; 2026 study found structure-based best for in-corpus retrieval | Parser and chunker work, no model | MIT/Apache parsers, CPU | Adopt, with an oversized-element policy and a context budget |
| Late chunking | 1.9 absolute nDCG average in Jina's paper; +6 percent in-corpus but negative in-document in the 2026 reproduction; BGE-M3 collapse in 2025 | Token-level encoder, re-embed, 8K forward passes | Code Apache-2.0; good models non-commercial; BGE-M3 mismatched | Skip for this stack |
| Long-context embedding models | 8,192-token whole-document embeddings lost to 512-token chunks on three of four datasets | Re-embed, CPU attention cost | BGE-M3 MIT at 8K; Qwen3 Apache at 32K; Jina v4 non-commercial | Keep BGE-M3; do not swap for context length |
| Contextual retrieval | 35 to 67 percent failure reduction in Anthropic's vendor run; +0.020 nDCG over the same rank-fusion pipeline on a 20 percent NFCorpus subset in the 2025 independent comparison | Per-chunk provider calls, re-ingest, audit volume | Method free; Bedrock model | Pilot after deterministic prefix |
| Semantic chunking | Costs not justified; fixed-size often better | Extra embedding pass, global recompute | MIT/Apache, CPU | Skip as default |
| Parent-document / sentence-window | No independent benchmark; mechanism only | About 2x storage, prompt expansion | MIT/Apache, CPU | Adopt cheap neighbour expansion, skip full parent store |
| RAPTOR | Controlled +1.7 on QuALITY; underperforms RAG in 2026 GraphRAG-Bench | LLM summaries, corpus-global tree | MIT code, provider or CPU summariser | Skip as default |
| ColBERT | Ties cross-encoder, about 20x faster; +5.5 to +6.2 nDCG over RRF | New encoder, vector type, re-embed, storage | Apache/MIT models; TEI has no MaxSim path | Pilot only if provider rerank becomes binding |
| GraphRAG | Wins global summarisation; loses fact retrieval to RAG; mixed multi-hop | LLM graph build, graph DB, re-index | MIT code, provider LLM | Skip for standard SMB; conditional pilot |
| Agentic retrieval | +25.6 points on a chemistry multi-hop set; 2.5x cost per correct answer; 7.8x output tokens | Multiple provider calls, latency, audit | MIT/Apache orchestration | Skip unbounded; pilot a two-step cascade |
| Long-context generation | Long-context beats RAG on quality when resources are sufficient; degrades at 32K and cannot enforce labels or cite pages | 100 to 400x input cost, audit volume | Provider model | Selective single-document use only |

## Marketing, with the reason it is marketing

**"RAPTOR improves QuALITY by 20 percent absolute."** That is a comparison with the previous best published result, not with a chunking baseline. The controlled table in the same paper shows a 1.7 point gain on QuALITY and a 0.47 point gain on QASPER. The 2026 independent benchmark has RAPTOR below basic RAG on every task it tested. [11][13]

**"Contextual retrieval cuts retrieval failures by 49 percent, or 67 percent with reranking."** The number is real for the vendor's own setup, but the baseline failure rate was already 5.7 percent, the evaluation covered four domains, and the embedding model was Gemini Text 004. The one independent comparison I found shows a 0.020 nDCG gain over the same rank-fusion pipeline on a 20 percent NFCorpus subset, with a local model and reranking. I found no independent in-document evaluation. A percentage reduction from a small baseline is easy to make sound large. [1][5]

**"Late chunking is superior across retrieval tasks."** The average gain is 1.5 to 1.9 absolute nDCG. On some datasets, embedding the whole document without chunking beats late chunking. On in-document questions, the 2026 reproduction shows losses of 5 to 53 percent. With BGE-M3, the method collapses because the pooling does not match. [3][4][5][6]

**"Voyage's contextualised chunk embeddings beat late chunking by 23.66 percent and contextual retrieval by 6.76 percent."** That is a competitor benchmarking competitors, on a corpus the post does not disclose, with a hosted model. It is useful as a signal that the technique has an effect. It is not evidence that the model can be self-hosted or that the ranking transfers to this corpus. [33]

**"LazyGraphRAG cuts indexing cost to 0.1 percent of GraphRAG."** That is Microsoft's own claim, for indexing only, and I found no independent replication. Query cost and quality are separate. The full GraphRAG pipeline still needs an LLM pass over the corpus and a graph store. [15]

**"Agentic RAG improves accuracy by 33 percent on average and 47 percent on multi-hop."** Those numbers appear in vendor blog posts without a disclosed corpus or method. The published evidence is narrower: a chemistry multi-hop set, a self-published 200-query MuSiQue benchmark, and a clinical benchmark with mixed results. The same evidence shows 2.5 times the cost per correct answer and about 8 times the output tokens. [26][27][28]

**"Long context makes RAG obsolete."** The models do have 1M-token windows. The evaluations show that effective use of those windows degrades with length, especially when the question requires an association rather than a literal match. NoLiMa has 11 of 13 models below half their short-context baseline at 32K. Context Rot has 18 models degrading with input length. The RAG versus long-context study finds long context better when resources are sufficient, but much more expensive. [29][30][31]

**"Semantic chunking creates smarter chunks."** The best-controlled study finds the cost is not justified and fixed-size often wins. Chroma's default semantic chunker is below its recursive baseline on recall. The semantic chunker that does well on precision depends on global corpus statistics and has to recompute when data changes. [7][8]

**"ColBERT beats rerankers."** It ties the cross-encoder on quality in the Qdrant benchmark and wins on latency. That is a real result. The hidden costs are a new encoder, a new vector type, a re-embed, and a model pool where several of the best options are non-commercial. It is not a free upgrade. [19][21][24][25]

**"Parent-document retrieval gives you the best of both."** It is a sensible engineering pattern with no independent head-to-head benchmark I could find. The costs are real: about twice the storage, a larger prompt, and ambiguous page citations when the parent spans pages. It is a reasonable optimisation, not a measured breakthrough. [8][38][39]

## What a professional team would adopt, pilot, or skip at this scale

### Adopt now

1. **A hard answer-path context budget.** Cap the number of chunks and the total prompt tokens. Deduplicate overlapping chunks by character span, not by text equality. Order the evidence by citation, and reserve tokens for the answer and the verifier. This is the direct fix for the defect that prompted this report.
2. **An oversized-element policy.** Keep a table whole if it fits. If it does not, split by rows, repeat the header on every piece, and record a page range. Mark the chunk as oversized so the verifier and the interface can treat its citation as a range. Never truncate silently.
3. **Structure-aware, page-bounded chunking at 384 to 512 tokens, with zero to 32 tokens of overlap.** This is the measured incumbent. Do not replace it with semantic chunking.
4. **Deterministic context before embedding.** Title, full heading path, page label, table caption and column headers. This is the free half of contextual retrieval, and Anthropic's own ablations suggest the gain comes from disambiguation rather than from added words. [1]
5. **Hybrid retrieval plus provider reranking plus a citation verifier.** The reranker is already at the provider because the CPU one is too slow. Keep the verifier focused on the page range and the quoted span, plus a numeric match. These are the checks that catch the failure this product cannot ship.
6. **Cheap neighbour expansion.** Store previous and next chunk IDs, expand one neighbour at query time, and stop at the token budget. This gets most of the parent-document benefit without a second copy of the text.

### Pilot

1. **Contextual retrieval, generated half.** Run the four-way ablation on 60 questions: no context, deterministic only, deterministic plus a cheap Bedrock model, deterministic plus the frontier model. Measure retrieval quality, token cost, audit volume and whether the generated context crosses an access label. Do not make it the default until it beats the free baseline on the client's own documents. [1][5]
2. **A two-step retrieval cascade for multi-hop questions.** Only for questions that need more than one document, such as invoice to contract to payment. Hard cap at two retrievals, condition the second step on a confidence check, and record each step. The measured gain is real on multi-hop sets, and so is the cost. [26][27]
3. **ColBERT reranking with `answerai-colbert-small-v1`.** Only if provider reranking becomes a cost or latency problem. It is Apache-2.0, 96-dimensional, 33 million parameters, FastEmbed-served, and small enough for CPU. Measure it on 60 questions against the provider reranker, and measure the storage and the extra service. [19][21][43]
4. **RAPTOR or GraphRAG for one client with a measured global or multi-hop gap.** Not as a platform default. Pick the client, measure the gap on the 60-question set, and compare against the structure-aware baseline. If the gap is not measurable, the complexity is not justified. [11][13][16]

### Skip

1. **Semantic chunking as the default.** The measured gain is inconsistent and the global recompute breaks incremental ingestion. [7][8]
2. **A full parent-document store.** Use neighbour pointers and a budget instead. [38][39]
3. **RAPTOR as the default.** The controlled gains are small and the independent benchmark is negative. [11][13]
4. **GraphRAG as the default.** It is a global summarisation tool, not a fact-retrieval upgrade, and its summary nodes do not carry page ranges. [14][16]
5. **Unbounded agentic retrieval.** The cost, latency and failure modes are all worse than a bounded cascade. [26][27]
6. **Long context as a chunking replacement.** It cannot enforce per-chunk access labels and it does not produce page citations. [29][30][32]
7. **Hosted contextual embedders and non-commercial late-interaction models.** Voyage is hosted. Jina v3 and v4, jina-colbert-v2 and Reason-ModernColBERT are non-commercial or research-licensed. [24][25][33][35][44]
8. **Late chunking with BGE-M3 through TEI as currently served.** The pooling does not match, the token-level route is not the main path, and the measured gain is small and task-dependent. [3][5][6][37][45]

### Why this order

The first two items close the known defect. The next four preserve the measured strengths of the current design. The pilots are ordered by evidence per unit of engineering: contextual retrieval has the strongest vendor-run retrieval gain and a small independent gain, the two-step cascade has a clear multi-hop use case, ColBERT has a latency use case, and RAPTOR or GraphRAG need a specific client gap before they earn their complexity. The skips are not fashion judgments. Each one either fails a measured test, breaks the access-label or citation contract, or adds a serving or licence dependency that this stack cannot absorb.

## What is out of reach here, and precisely why

### The binding constraints

| Approach | Is it reachable? | Binding constraint | What would change it |
|---|---|---|---|
| Contextual retrieval | Yes, on hardware and licence | Audit and access labels: one whole-document prompt per chunk, and a mixed-label document can leak restricted text into an allowed chunk. The advantage over the free deterministic prefix is also unproven at this scale. | A measured 60-question gain over the deterministic baseline, document-level labels, and an audit scheme that stores hashes or pointers rather than every full prompt. |
| Late chunking | Yes, with a custom client, but not worth it | Serving and pooling: the stack's `/embed` path returns pooled vectors, BGE-M3 uses CLS pooling, and late chunking needs mean pooling over token spans. A token-level `/embed_all` route exists, so this is a custom-client problem rather than an impossibility, but the pooling mismatch remains and the 4096 batch-token cap fights the 8K context. | A production token-level path with a mean-pooled model, and a measured gain on this corpus. A model swap alone does not fix the pooling mismatch. |
| ColBERT | Yes, but not worth it now | Serving plus licence: TEI has no MaxSim route, and several of the strongest models are non-commercial. Storage is manageable with the small Apache-2.0 model. | A provider rerank cost or latency problem that the small model solves, plus a pinned FastEmbed or PyLate component in the image. |
| RAPTOR | Yes, but not justified | Cost and audit: an LLM summary tree over the whole corpus, a corpus-global index that is hard to update, and generated summary nodes without page ranges. About 4 percent of summaries had minor hallucinations in the paper. | An incremental tree builder, page-anchored summaries, and a measured global-question gap that the structure-aware baseline cannot close. |
| GraphRAG | Not at this scale | Cost and operations: LLM graph construction, entity resolution, community summaries, a graph store, and summaries that can mix access labels. | A cheap incremental graph builder with per-label communities and page-level provenance, plus a measured multi-hop gap. |
| Agentic retrieval | Yes, but costly | Cost and audit: 2.5 times the cost per correct answer in one benchmark, about 8 times the output tokens in another, and several evidence records per answer. | A bounded two-step cascade that shows a measured gain on the client's multi-hop questions and stays inside a per-query budget. |
| Long context | Yes, but it does not fit the contract | Access labels and citations: a corpus-wide prompt cannot enforce per-chunk labels, and the model does not return page ranges. The cost is 100 to 400 times a chunked prompt. | Per-user filtered context, page-labelled citations, and a cache that makes repeated corpus reads cheap. Even then it is not a chunking replacement. |
| Semantic chunking | Yes, but not worth it | Cost and incremental ingestion: an extra embedding pass and global statistics that require recomputation when data changes. | An incremental semantic boundary model with a measured gain over structure-aware chunking. |
| Parent-document | Yes, and cheap | Storage and prompt budget: the parent text doubles the index and can blow the context budget. The quality gain is unmeasured. | A 60-question result showing that full parents beat one-neighbour expansion. |

### The owner's question

The approach with the strongest published retrieval numbers is contextual retrieval. It is not out of reach because of hardware. The box has no GPU for generation, but generation is at Bedrock, and the contextualisation calls would go to the same provider. It is not out of reach because of licence. The method is free and the model is already available. It is out of reach as a default because of the audit chain, and because of the access labels when a document can mix them, and because its advantage over the free deterministic prefix has not been measured on this kind of corpus.

The audit problem is concrete. The brief requires every prompt to be recorded. Contextual retrieval sends the whole document with every chunk. If the chain stores prompt text, one ingest of a 10k-token document with 12 chunks writes about 120k tokens of evidence, roughly 480 KB at four bytes per token. That is manageable for one document and awkward for a corpus, especially when documents change and the pass repeats. If the chain stores hashes and pointers instead, the volume is smaller, but then the evidence is not the prompt.

The access-label problem is more serious. The stack promises a label on every chunk. Contextual retrieval and late chunking both condition an allowed chunk on the whole document. If a document can contain a restricted appendix, the allowed chunk's context and embedding can carry information from the restricted part. The same is true for RAPTOR and GraphRAG summaries, which can mix labels by construction. Only methods that retrieve and rerank chunks after the label filter preserve the boundary without per-label segmentation. That is the current hybrid retriever, and it is the strongest argument for keeping the retrieval contract as it is.

The techniques that are blocked in practice are late chunking with BGE-M3, because the pooling does not match and the token-level path is a custom client, and ColBERT with the best current models, because the good ones are non-commercial and TEI has no MaxSim path. GraphRAG and unbounded agentic retrieval are reachable in principle but economically irrational at 10 to 200 users. Long context is reachable but does not serve the access-label and page-citation contract.

If I had to name one constraint that decides the most, it is the audit and citation contract, not compute. The stack can afford a provider call. It cannot afford to turn every chunk into a copy of the document in the evidence log. It cannot afford to bake a restricted appendix into an allowed chunk's embedding. And it cannot afford to cite a generated summary that has no page. That is why the professional answer here is to fix the budget and the oversized-element policy first, keep the structure-aware retriever, and pilot only the techniques that preserve the chunk as the unit of evidence.

### What would change the answer

1. **A production token-level path with a mean-pooled model.** If the stack already needed one for another reason, late chunking would become testable. It would still need a measured gain on this corpus, and the 2025 BGE-M3 result says the gain is not automatic.
2. **A measured 60-question gain from contextual retrieval over the deterministic prefix.** That would justify the per-chunk provider cost and the audit volume. Without it, the free half is the right default.
3. **Document-level access labels, with no mixed-label documents.** That would remove the main audit objection to contextual retrieval and late chunking. It would not remove the serving objection to late chunking.
4. **A provider rerank cost or latency problem.** That would make ColBERT worth the new component. The small Apache-2.0 model is the right candidate.
5. **A client with a measured multi-hop or global-question gap.** That would justify a bounded cascade, RAPTOR or GraphRAG for that client. It would not justify making any of them the platform default.
6. **A long-context model that returns page-anchored citations and enforces per-chunk labels.** That would change the long-context verdict. A larger window alone does not.

## Sources

All URLs retrieved 2026-10-16 unless a different date is given.

1. Ford, D. "Introducing Contextual Retrieval." Anthropic, 19 September 2024. https://www.anthropic.com/engineering/contextual-retrieval
2. Anthropic. "Enhancing RAG with contextual retrieval." Claude Cookbook, published 13 September 2024, accessed 2026-10-16. https://platform.claude.com/cookbook/capabilities-contextual-embeddings-guide
3. Günther, M., Mohr, I., Williams, D. J., Wang, B. and Xiao, H. "Late Chunking: Contextual Chunk Embeddings Using Long-Context Embedding Models." arXiv 2409.04701, v1 7 September 2024, v3 7 July 2025. https://arxiv.org/abs/2409.04701
4. Jina AI. "Late Chunking in Long-Context Embedding Models." 22 August 2024. https://jina.ai/news/late-chunking-in-long-context-embedding-models/
5. Merola, C. and Singh, J. "Reconstructing Context: Evaluating Advanced Chunking Strategies for Retrieval-Augmented Generation." arXiv 2504.19754, 28 April 2025; ECIR 2025 workshop preprint. https://arxiv.org/abs/2504.19754
6. Zhou, Y., Wang, S., Koopman, B. and Zuccon, G. "Beyond Chunk-Then-Embed: A Comprehensive Taxonomy and Evaluation of Document Chunking Strategies for Information Retrieval." arXiv 2602.16974, 19 February 2026. https://arxiv.org/abs/2602.16974
7. Qu, R., Tu, R. and Bao, F. "Is Semantic Chunking Worth the Computational Cost?" arXiv 2410.13070, 16 October 2024. https://arxiv.org/abs/2410.13070
8. Smith, B. and Troynikov, A. "Evaluating Chunking Strategies for Retrieval." Chroma technical report, 3 July 2024. https://www.trychroma.com/research/evaluating-chunking
9. Jimeno Yepes, A., You, Y., Milczek, J., Laverde, S. and Li, R. "Financial Report Chunking for Effective Retrieval Augmented Generation." arXiv 2402.05131, v3 16 March 2024. https://arxiv.org/abs/2402.05131
10. Amiri, M. and Bocklitz, T. "Chunk Twice, Embed Once: A Systematic Study of Segmentation and Representation Trade-offs in Chemistry-Aware Retrieval-Augmented Generation." arXiv 2506.17277, v2 17 September 2026. https://arxiv.org/abs/2506.17277
11. Sarthi, P., Abdullah, S., Tuli, A., Khanna, S., Goldie, A. and Manning, C. D. "RAPTOR: Recursive Abstractive Processing for Tree-Organized Retrieval." arXiv 2401.18059, 31 January 2024; ICLR 2024. https://arxiv.org/abs/2401.18059
12. Zhang, X., Hu, Q., Kim, J. and Dunn, A. G. "Strategies for Efficient Retrieval-augmented Generation in Clinical Domains with RAPTOR: A Benchmarking Study." RANLP 2025, September 2025. https://aclanthology.org/2025.ranlp-1.164/
13. Xiang, Z., Wu, C., Zhang, Q., Chen, S., Hong, Z., Huang, X. and Su, J. "When to use Graphs in RAG: A Comprehensive Analysis for Graph Retrieval-Augmented Generation." arXiv 2506.05690, v3 22 February 2026; ICLR 2026. https://arxiv.org/abs/2506.05690
14. Edge, D., Trinh, H., Cheng, N., Bradley, J., Chao, A., Mody, A., Truitt, S., Metropolitansky, D., Ness, R. O. and Larson, J. "From Local to Global: A Graph RAG Approach to Query-Focused Summarization." arXiv 2404.16130, v2 19 February 2025. https://arxiv.org/abs/2404.16130
15. Microsoft Research. "LazyGraphRAG: Setting a new standard for quality and cost." 25 November 2024. https://www.microsoft.com/en-us/research/blog/lazygraphrag-setting-a-new-standard-for-quality-and-cost/
16. Han, H., Ma, L., Wang, Y., Shomer, H., Lei, Y., Qi, Z., Guo, K., Hua, Z., Long, B., Liu, H., Aggarwal, C. C. and Tang, J. "RAG vs. GraphRAG: A Systematic Evaluation and Key Insights." arXiv 2502.11371, v3 4 March 2026. https://arxiv.org/abs/2502.11371
17. Santhanam, K., Khattab, O., Saad-Falcon, J., Potts, C. and Zaharia, M. "ColBERTv2: Effective and Efficient Retrieval via Lightweight Late Interaction." arXiv 2112.01488, v3 10 July 2022; NAACL 2022. https://arxiv.org/abs/2112.01488
18. Santhanam, K., Khattab, O., Potts, C. and Zaharia, M. "PLAID: An Efficient Engine for Late Interaction Retrieval." arXiv 2205.09707, 19 May 2022. https://arxiv.org/abs/2205.09707
19. Qdrant Labs. "hybrid-comparisons." GitHub repository, accessed 2026-10-16; the README does not state a benchmark run date. https://github.com/qdrant-labs/hybrid-comparisons
20. Qdrant. "Vectors." Documentation, accessed 2026-10-16. https://qdrant.tech/documentation/manage-data/vectors/
21. Hugging Face. `answerdotai/answerai-colbert-small-v1` model card, Apache-2.0, accessed 2026-10-16. https://huggingface.co/answerdotai/answerai-colbert-small-v1
22. Hugging Face. `colbert-ir/colbertv2.0` model card, MIT, accessed 2026-10-16. https://huggingface.co/colbert-ir/colbertv2.0
23. Hugging Face. `lightonai/GTE-ModernColBERT-v1` model card, Apache-2.0, accessed 2026-10-16. https://huggingface.co/lightonai/GTE-ModernColBERT-v1
24. Hugging Face. `jinaai/jina-colbert-v2` model card, CC-BY-NC-4.0, accessed 2026-10-16. https://huggingface.co/jinaai/jina-colbert-v2
25. Hugging Face. `lightonai/Reason-ModernColBERT` model card, CC-BY-NC-4.0, accessed 2026-10-16. https://huggingface.co/lightonai/Reason-ModernColBERT
26. Astaraki, M., Arshi Saloot, M., Shiraee Kasmaee, A., Mahyar, H. and Samiee, S. "When Iterative RAG Beats Ideal Evidence: A Diagnostic Study in Scientific Multi-hop Question Answering." arXiv 2601.19827, v5 31 July 2026; TMLR 05/2026. https://arxiv.org/abs/2601.19827
27. intikhab49. "rag-cost-curve." GitHub repository, accessed 2026-10-16. https://github.com/intikhab49/rag-cost-curve
28. Koka, P. "Retrieval Benchmarking: Agentic vs. Vanilla, and What Actually Works in 2025." Trilogy AI Center of Excellence, 12 June 2025. https://trilogyai.substack.com/p/retrieval-benchmarking-agentic-vs
29. Hong, K., Troynikov, A. and Huber, J. "Context Rot: How Increasing Input Tokens Impacts LLM Performance." Chroma technical report, 14 July 2025. https://www.trychroma.com/research/context-rot
30. Modarressi, A., Deilamsalehy, H., Dernoncourt, F., Bui, T., Rossi, R. A., Yoon, S. and Schütze, H. "NoLiMa: Long-Context Evaluation Beyond Literal Matching." arXiv 2502.05167, v3 9 July 2025; ICML 2025. https://arxiv.org/abs/2502.05167
31. Li, Z., Li, C., Zhang, M., Mei, Q. and Bendersky, M. "Retrieval Augmented Generation or Long-Context LLMs? A Comprehensive Study and Hybrid Approach." arXiv 2407.16833, v2 17 October 2024; EMNLP 2024. https://arxiv.org/abs/2407.16833
32. Anthropic. "Models overview." Claude Platform Docs, accessed 2026-10-16. https://platform.claude.com/docs/en/models/overview
33. Voyage AI. "Introducing voyage-context-3: focused chunk-level details with global document context." 23 July 2025. https://blog.voyageai.com/2025/07/23/voyage-context-3/
34. Zhang, Y., Li, M., Long, D., Zhang, X., Lin, H., Yang, B., Xie, P., Yang, A., Liu, D., Lin, J., Huang, F. and Zhou, J. "Qwen3 Embedding: Advancing Text Embedding and Reranking Through Foundation Models." arXiv 2506.05176, 5 June 2025. https://arxiv.org/abs/2506.05176
35. Hugging Face. `jinaai/jina-embeddings-v4` model card, Qwen Research License, accessed 2026-10-16. https://huggingface.co/jinaai/jina-embeddings-v4
36. Hugging Face. `BAAI/bge-m3` model card and pooling configuration, MIT, accessed 2026-10-16. https://huggingface.co/BAAI/bge-m3
37. Hugging Face. "Text Embeddings Inference" README, pooling modes and `--max-batch-tokens`, accessed 2026-10-16. https://github.com/huggingface/text-embeddings-inference38. LlamaIndex. "Node parsers and text splitters." Documentation, accessed 2026-10-16. https://developers.llamaindex.ai/python/framework/module_guides/loading/node_parsers/modules/
39. LangChain. "How to use the parent document retriever." Documentation, accessed 2026-10-16. https://python.langchain.com/docs/how_to/parent_document_retriever/
40. Microsoft. `microsoft/graphrag` repository, MIT, accessed 2026-10-16. https://github.com/microsoft/graphrag
41. Sarthi, P. `parthsarthi03/raptor` repository, MIT, accessed 2026-10-16. https://github.com/parthsarthi03/raptor
42. Jina AI. `jina-ai/late-chunking` repository, Apache-2.0, accessed 2026-10-16. https://github.com/jina-ai/late-chunking
43. Qdrant. "FastEmbed ColBERT." Documentation, accessed 2026-10-16. https://qdrant.tech/documentation/fastembed/fastembed-colbert/
44. Hugging Face. `jinaai/jina-embeddings-v3` model card, CC-BY-NC-4.0, accessed 2026-10-16. https://huggingface.co/jinaai/jina-embeddings-v3
45. Hugging Face. Text Embeddings Inference router source, `/embed_all` route ("Get all Embeddings without Pooling"), accessed 2026-10-16. https://raw.githubusercontent.com/huggingface/text-embeddings-inference/main/router/src/http/server.rs
46. LangChain GitHub repository, MIT, accessed 2026-10-16. https://github.com/langchain-ai/langchain
47. LlamaIndex GitHub repository, MIT, accessed 2026-10-16. https://github.com/run-llama/llama_index
