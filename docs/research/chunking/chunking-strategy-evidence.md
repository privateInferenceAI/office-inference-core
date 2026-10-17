# Chunking strategy: what the evidence says

Research date: 2026-10-16. This is a literature review for a pipeline that targets 384 to 512 tokens per chunk, embeds with BAAI/bge-m3 through TEI on CPU (`--max-batch-tokens 4096`, model maximum 8,192 tokens per the [model card](https://huggingface.co/BAAI/bge-m3) and the [M3-Embedding paper](https://arxiv.org/abs/2402.03216)), retrieves hybrid, reranks, and then verifies citations against retrieved passages. I read primary papers and parameter documentation. Vendor claims are labelled as claims. I changed no code.

The short answer. Measured studies support a hard cap in roughly the 200 to 512 token band, zero overlap, and splitting on document structure before splitting on size. They do not support semantic chunking as a reliable improvement. Late chunking needs token-level embeddings that TEI's documented API does not expose. LLM-written chunk context is available in principle, but it needs one generation call per chunk and its measured gains are vendor-reported. For an element larger than the cap, the literature supports splitting it, not embedding it whole, and not summarising it into the citation unit. A whole table is the worst unit of all: at million-token scale, reading the whole table scored 4.6 and 9.1 percent where schema-plus-cell retrieval scored 49.2 and 45.5 percent.

## What I could not verify

These gaps bound every recommendation below.

1. I found no study that measures chunking on small-business office documents. The corpora are Wikipedia, news, finance, chemistry papers, arXiv papers, and synthetic queries. Invoices, contracts and policy manuals are structurally different from all of them.
2. I found no study that measures this exact stack. The retrievers in the literature are Contriever, DPR, GTR, SPLADE, OpenAI embeddings, or LlamaIndex defaults. I found no end-to-end evaluation of bge-m3 plus TEI plus bge-reranker-v2-m3 plus a citation verifier.
3. I could not verify that late chunking is implementable through TEI. The TEI API returns one pooled vector per input. Late chunking needs token-level vectors before pooling. The documentation I read describes no token-embedding endpoint. This is a blocker, not a preference.
4. The defect numbers are taken as given, not independently reproduced: 756 KB prose chunk, 186 KB table chunk, 228,326 estimated tokens for six passages, 81 of 341 documents with a chunk over 4 KB. At roughly four bytes per token, 756 KB is about 189,000 tokens and 186 KB is about 46,500 tokens. Both are far past bge-m3's 8,192-token maximum.
5. Effect sizes are corpus-specific. The studies with the cleanest size ablations use one financial document or five small corpora. Treat their optima as directional, not transferable constants.
6. Several of the most relevant 2025 and 2026 studies are arXiv preprints and have not been peer reviewed. I mark them as preprints.
7. The two vendor marketing sources I use, Anthropic and NVIDIA, publish method summaries and, in Anthropic's case, a cookbook, but not the evaluation data needed to rerun their numbers. Their numbers are claims with a described method, not independent measurements.
8. I did not test what TEI does with a single input of 200,000 tokens. The documentation states the truncation and batching controls. It does not state the per-request failure mode at that size.
9. I found no paper that studies an element as large as 756 KB. The oversized-element advice below is an extrapolation from studies of 128 to 8,192 token units. I say where the extrapolation starts.

## The measured claims

The literature is consistent on four points. In the studies that vary both, size moves the metric more than the boundary method. Very small and very large units both lose. Document structure beats a fixed character count, and semantic similarity is not reliably better than either. The retrieval unit does not have to be the generation unit.

The table lists the studies I weight most. The rest of this section explains each row and the strategies that are not in it.

| Study | Setup | Metric | Result that matters |
|---|---|---|---|
| Searching for Best Practices in RAG, [arXiv 2407.01219](https://arxiv.org/abs/2407.01219), 1 Jul 2024 | One 10-K (lyft_2021), ada-002 embeddings, Zephyr-7B, about 170 synthetic queries, 20-token overlap | Faithfulness and relevancy | 512 tokens: 97.59 / 97.41. 256: 97.22 / 97.78. 1024: 94.26 / 95.56. 2048: 80.37 / 91.11. 128: 95.74 / 97.22 |
| Evaluating Chunking Strategies for Retrieval, [Chroma](https://www.trychroma.com/research/evaluating-chunking), 3 Jul 2024, vendor technical report with released code | Five small corpora, 474 generated queries, text-embedding-3-large, top-5 | Token recall, precision, IoU | Recursive 200/0: recall 88.1, IoU 6.9. Recursive 400/0: 89.5, 3.6. Recursive 800/400: 85.4, 1.5. Cluster semantic 400: 91.3, 4.5 |
| Is Semantic Chunking Worth the Computational Cost?, [arXiv 2410.13070](https://arxiv.org/abs/2410.13070), 16 Oct 2024, [Findings of NAACL 2025](https://aclanthology.org/2025.findings-naacl.114/) | Ten retrieval datasets plus five RAGBench datasets, three chunkers, three embedders | F1@5, BERTScore | Fixed-size wins all four non-stitched document-retrieval sets. Semantic wins only stitched sets with high topic diversity: Miracl 81.89 vs 69.45, NQ 63.93 vs 43.79 |
| Dense X Retrieval, [arXiv 2312.06648](https://arxiv.org/abs/2312.06648), 11 Dec 2023, EMNLP 2024 | English Wikipedia, five QA datasets, four retrievers | Recall@5 and @20 | Propositions average 11.2 words. Contriever R@5: proposition 52.7, sentence 47.3, passage 43.0. GTR: 68.0, 66.7, 65.2 |
| Late Chunking, [arXiv 2409.04701](https://arxiv.org/abs/2409.04701), 7 Sep 2024, v3 7 Jul 2025, Jina AI and Weaviate authors | Four BeIR sets, three long-context embedders | nDCG@10 | +1.5 to +1.9 absolute over naive chunking. 256-token fixed: 52.2 to 54.0 |
| ARAGOG, [arXiv 2404.01037](https://arxiv.org/abs/2404.01037), 1 Apr 2024 | AI arXiv papers, 107 QA pairs, 512-token baseline with 50 overlap | Retrieval precision | Sentence-window retrieval's worst variant beat the best classic variant by 0.1021 mean precision, adjusted p = 0.0000 |
| TableRAG, [arXiv 2410.04739](https://arxiv.org/abs/2410.04739), 7 Oct 2024, NeurIPS 2024 | Million-token tables from Arcade and BIRD-SQL | Exact match | Whole table: 4.6 and 9.1. Schema plus cell retrieval: 49.2 and 45.5, GPT-3.5 |
| Chunking for reliable QA, [arXiv 2601.14123](https://arxiv.org/abs/2601.14123), 20 Jan 2026, preprint | Natural Questions, SPLADE, Ministral-8B, fill-to-budget | BERTScore, EM | 10 to 20 percent overlap: no gain, BERTScore deltas at or below 0.004, EM at or below 0.001. Sentence chunking ties semantic up to about 5k tokens. Recommended size 150 to 300, context about 2.5k |
| 36-method chunking study, [arXiv 2603.06976](https://arxiv.org/abs/2603.06976), 7 Mar 2026, preprint | Six domains, five embedders, 36 segmentation methods | LLM-graded nDCG@5 | Paragraph Group Chunking: 0.459 nDCG@5, 24 percent P@1, 59 percent Hit@5. Fixed-size character chunking: below 0.244 nDCG@5, 2 to 3 percent P@1 |

### Fixed windows: 200 to 512 is the measured band, not a law

The cleanest single-pipeline size ablation is Table 3 of [Searching for Best Practices in RAG](https://arxiv.org/abs/2407.01219) (1 Jul 2024). On one financial document with about 170 generated queries, 512 tokens had the highest faithfulness and near-highest relevancy. 256 was close. 1024 fell back, and 2048 collapsed to 80.37 faithfulness. The finding is limited by one document, one embedding model, and synthetic queries, but it is a controlled comparison across five sizes with everything else fixed.

Chroma's technical report ([3 Jul 2024](https://www.trychroma.com/research/evaluating-chunking)) points the same way with token-level metrics. Recursive 200 with zero overlap had the best token efficiency in the recursive family, 6.9 IoU, and 88.1 recall. Recursive 400 had the best recall in that family, 89.5, but about half the IoU. Recursive 800 with 400 overlap had lower recall, 85.4, and an IoU of 1.5. The report's own summary is that strategy choice moved recall by up to 9 percent, and that the popular 800/400 default had a poor recall-to-efficiency trade-off.

The 2026 Natural Questions study ([20 Jan 2026](https://arxiv.org/abs/2601.14123)) recommends 150 to 300 tokens, but its retrieval policy is fill-to-budget: it keeps adding chunks until the context budget is full. Under that policy small chunks buy more distinct evidence. A pipeline with a fixed top-k and a reranker is a different optimisation, and its optimum can sit higher. That study is still the most direct recent evidence on overlap and on sentence versus semantic chunking, which I cover below.

The 36-method study ([7 Mar 2026](https://arxiv.org/abs/2603.06976)) does not report a single winning size. It reports a single winning family: content-aware segmentation. Paragraph Group Chunking beat fixed-size character chunking by a wide margin. Its absolute numbers are not comparable to the other studies because it uses an LLM grader and six domains, but the direction is consistent with Chroma and with the financial-report work below.

NVIDIA's vendor benchmark ([18 Jun 2025](https://developer.nvidia.com/blog/finding-the-best-chunking-strategy-for-accurate-ai-responses/)) reports that page-level chunking had the best average end-to-end accuracy, 0.648 with standard deviation 0.107, while FinanceBench preferred 1,024 tokens and Earnings preferred 512. It also found that 128 and 2,048 generally underperformed medium sizes. This is a claim from a vendor with a disclosed setup, not an independent study. It agrees with the rest on the shape: the middle is safer than the extremes.

The practical reading is that 384 to 512 is inside the measured band. 512 is at the top of the band, not outside it. There is no measured basis for treating 512 as a universal optimum, and no measured basis for going to 1,024 or 2,048 to reduce chunk count.

### Recursive and structure-aware splitting: the best-supported upgrade

Recursive splitting means trying a hierarchy of separators in order, such as paragraph breaks, then line breaks, then sentence endings, then characters. Structure-aware splitting means using the document's own elements, such as headings, pages, tables, and list blocks, as the boundaries. The literature supports both, and structure-aware more strongly.

Chroma measured `RecursiveCharacterTextSplitter` against `TokenTextSplitter` and found recursive better at 400 tokens or less with zero overlap, on all metrics. At 800 tokens the advantage reversed, which is a reminder that the splitter and the size interact. The same report found semantic cluster chunking competitive, and an LLM chunker had the highest recall, but neither is a structure-aware method.

The financial-report study ([5 Feb 2024](https://arxiv.org/abs/2402.05131)) is the clearest statement of the structural case. It chunks by document element type, using annotations from a document-understanding model, rather than treating all text as equal. It reports that element-type chunking largely improves RAG results on financial reporting and yields a good chunk size without tuning. This is exactly the class of documents the office pipeline sees: reports, schedules, tables, and sections.

The 36-method study reinforces it: paragraph grouping and other content-aware methods beat fixed character windows. The chemistry study ([13 Jun 2025](https://arxiv.org/abs/2506.17277)) finds medium-to-large chunks with fixed-token, recursive-token, or hierarchical-section chunking are a practical starting point, and that low overlap was generally favoured. Its main caution is that embedding choice produced larger differences than chunking.

The caveat is that "structure-aware" is not one method. A page-bounded splitter, a heading-aware splitter, and a table-element splitter are different choices with different failure modes. The measured support is strongest for the general claim that document structure is a better boundary than a fixed character count, and weakest for any specific implementation.

### Semantic chunking: measured, and mostly a null result

The best controlled study is Qu, Tu and Bao ([16 Oct 2024](https://arxiv.org/abs/2410.13070), peer reviewed at [Findings of NAACL 2025](https://aclanthology.org/2025.findings-naacl.114/)). They compare fixed-size, breakpoint semantic, and clustering semantic chunkers on document retrieval, evidence retrieval, and answer generation.

On document retrieval, fixed-size won all four non-stitched datasets: HotpotQA 90.59 against 87.37 and 84.79, MSMARCO 93.58 against 92.23 and 93.18, ConditionalQA 68.11 against 64.44 and 65.94, Qasper 90.99 against 89.27 and 90.77. Semantic chunking won only the stitched datasets, where unrelated short documents were concatenated to make long ones. On Miracl it reached 81.89 against fixed-size 69.45, and on NQ 63.93 against 43.79. The authors' explanation is that artificial stitching creates high topic diversity, which is exactly the condition semantic boundaries are good at finding. Real documents have less of it.

On evidence retrieval the differences were small. Fixed-size was best on three of five datasets, and the spread on ExpertQA was 47.11 against 47.08 and 46.87. On answer generation the BERTScore differences were at most about 0.01. The authors' conclusion is that fixed-size chunking remains the more efficient and reliable choice, and that embedding quality overshadowed chunking strategy.

Chroma found a more favourable result for semantic clustering: `ClusterSemanticChunker` at 400 tokens had the second-highest recall, 91.3, and at 200 had the highest precision and IoU. But Chroma's own summary is that the heuristic recursive splitter with 200 tokens and no overlap performs well and is consistently high across metrics, and the report notes that its LLM chunker can take tens of minutes. The 2026 NQ study found sentence chunking statistically tied with semantic up to about 5,000 tokens.

The measured case for semantic chunking is therefore narrow. It helps when a document is a concatenation of unrelated topics, or when the context budget is very large. It does not reliably beat a tuned sentence or structure-aware splitter on ordinary documents, and it costs an extra embedding pass and a clustering step. For this pipeline it has a second problem the papers do not study: semantic boundaries can cross page and section boundaries, which breaks the page-range citation invariant and can merge content under different access labels.

### Late chunking: real, modest, and blocked here

Late chunking ([7 Sep 2024](https://arxiv.org/abs/2409.04701)) embeds the whole document first, then pools token vectors into chunk vectors. The chunk embedding therefore carries context from the full document. The authors report average nDCG@10 gains of 1.8 absolute for fixed 256-token chunks, 1.9 for sentence chunks, and 1.5 for semantic chunks across four BeIR datasets and three embedders. The method helps most at small chunk sizes, and it does not help on needle-in-a-haystack tasks where the surrounding document is irrelevant. For documents longer than the model's window, the paper proposes long late chunking with 8,192-token macro chunks and overlap.

The paper's authors are at Jina AI and Weaviate, so this is vendor-authored research, though it is a paper with a method and numbers rather than a blog claim. The gains are real but small: about two nDCG points, not a transformation.

The blocker is architectural. Late chunking needs the token-level output of the transformer before mean pooling. TEI's embedding endpoint returns one pooled vector per input ([CLI documentation](https://huggingface.co/docs/text-embeddings-inference/en/cli_arguments), accessed 2026-10-16). The documentation describes pooling options and batching, not token-level outputs. Implementing late chunking would mean running bge-m3 outside TEI, or adding a custom endpoint, and then changing the index schema. That is new inference code, not a chunker setting. The 8,192-token cap also means a 756 KB element still cannot be late-chunked as one unit. It would need macro-chunking first, at which point the context is only the macro chunk, not the document.

Verdict: not adoptable now. Revisit only if TEI exposes token embeddings, or if the team runs a custom inference service and measures a gain on its own corpus.

### Parent-child, small-to-big, and sentence-window: measured, uneven

These are three versions of the same idea: retrieve a small unit, then give the generator a larger one.

Small-to-big appears in the Best Practices paper's Table 4. It used 175-token child chunks and 512-token parent chunks, with 20-token overlap. Against the original chunking it moved faithfulness from 95.74 to 96.67 and left relevancy unchanged at 95.37. A sliding window did better on both: 97.41 faithfulness and 96.85 relevancy. The absolute gains are under two points on one document. That is weak support for parent-child specifically, and stronger support for the general idea of giving the reader neighbouring context.

Sentence-window retrieval has the strongest single result. ARAGOG ([1 Apr 2024](https://arxiv.org/abs/2404.01037)) used a 512-token baseline with 50-token overlap, and a sentence-window parser with a three-sentence window. It found that even the worst sentence-window variant beat the best classic vector-DB variant on retrieval precision, by 0.1021 mean difference with adjusted p = 0.0000. The same paper found answer similarity variable, and its conclusion is cautious: sentence-window retrieval is effective for retrieval precision, not uniformly for answer quality. The study uses 107 QA pairs on one corpus of AI papers.

Hierarchical chunking has a 2025 preprint, HiChunk ([15 Sep 2025](https://arxiv.org/abs/2509.11552)), which builds a multi-level document tree and uses an auto-merge retrieval step. It reports better chunking quality and better RAG performance, but the abstract gives no effect sizes. The 36-method preprint puts content-aware methods, and paragraph grouping in particular, in the top group. Treat hierarchy as promising and under-measured, not settled.

The pattern across these studies: expanding the retrieved unit helps retrieval precision and can help faithfulness, but it spends prompt tokens and does not automatically improve the answer. The expansion has to be measured against the citation verifier, because a larger generation unit makes each citation less precise.

### Contextual chunk enrichment: a vendor claim plus one small demonstration

Anthropic's Contextual Retrieval ([19 Sep 2024](https://www.anthropic.com/engineering/contextual-retrieval)) prepends 50 to 100 tokens of LLM-written context to each chunk before embedding and before building the BM25 index. The post reports a 35 percent reduction in top-20 retrieval failures from contextual embeddings alone, 49 percent when combined with contextual BM25, and 67 percent with reranking. The evaluation covers codebases, fiction, arXiv papers and science papers, uses 800-token chunks in its cost example, and reports 1 minus recall@20. The claimed one-time cost is USD 1.02 per million document tokens with prompt caching.

This is a vendor claim. The method is described well enough to reproduce, but the post does not include the evaluation data needed to rerun its numbers. It is also the largest reported effect in this review, which is a reason to test it rather than dismiss it.

The late-chunking paper's Section 4.5 offers a small independent comparison. On one fictional financial document, both late chunking and Claude-generated context put the correct chunk first, and naive chunking did not. That is a demonstration, not a benchmark. It does suggest the mechanism is real.

A deterministic heading prefix is a cheap subset of the idea: it gives the chunk its section context without a generation call. I found no study that isolates the heading prefix alone. The local chunker already prepends the heading path to prose and to serialised tables, so part of the benefit may already be present. The unmeasured part is the LLM-written sentence that explains what the chunk is about.

### Context budget and the number of passages

Lost in the Middle ([6 Jul 2023](https://arxiv.org/abs/2307.03172), TACL) is the standard warning. Performance is highest when the relevant passage is at the start or end of the context and degrades in the middle. In their multi-document QA experiments, GPT-3.5-Turbo's accuracy with the relevant document in the middle fell below its closed-book accuracy of 56.1 percent. Adding documents is not free even when the context window accepts them.

The RAG-versus-long-context study ([23 Jul 2024](https://arxiv.org/abs/2407.16833), EMNLP 2024 industry track) found that long-context models consistently beat RAG on average when given enough resources, but RAG's lower cost remains its advantage. Their Self-Route method sends a query to one or the other. The relevance here is that a larger retrieved context is a cost and attention decision, not only a recall decision.

Anthropic's post claims the opposite direction on passage count: 20 chunks was more effective than 10 or 5, measured by retrieval failure. That is a retrieval metric, not an answer metric. The 2026 NQ study reports a context cliff beyond about 2,500 tokens, with answer quality falling as more context is added. These results can all be true at once: more retrieved passages improve the chance the evidence is present, and more prompt tokens can degrade the reader's use of it. The mediator is the reader and the task.

Chroma's token-level metrics make the same point from the retrieval side. Larger chunks raise the chance of containing the answer and lower the fraction of retrieved tokens that are relevant. At 800 tokens with 400 overlap, recall was 85.4 and IoU 1.5. At 200 with no overlap, recall was 88.1 and IoU 6.9. The larger configuration was worse on both.

For a 512-token pipeline the arithmetic is simple. Six chunks at 512 tokens is about 3,072 tokens, plus headings and the query. The defect case was about 228,326 tokens for six passages. The smaller budget is not a quality compromise forced by cost. It is small enough that the reader is not being asked to use a 200K-token context, which is the regime where Lost in the Middle and the 2026 context-cliff result show degradation.

### Citation and verification

ALCE ([24 May 2023](https://arxiv.org/abs/2305.14627)) is the benchmark for citation quality. It measures fluency, correctness, and citation precision and recall, and it reports that even the best models lack complete citation support 50 percent of the time on ELI5. The lesson is that citation verification is a real failure mode, not a formality.

A citation verifier needs the claim and its supporting text to be close enough that a checker can decide whether the passage supports the sentence. A 512-token chunk with a page range can do that. A 186 KB table chunk cannot: the citation points at a region no human or model can check at claim granularity, and the verifier has no way to distinguish the supporting row from the other thousands. Chunk size is therefore a citation-quality parameter, not only a retrieval parameter. ALCE does not study chunk size directly, but its metric construction assumes passage-level evidence.

## Where the evidence disagrees

The disagreements are real, and most of them come from different experimental setups rather than from contradictory measurements. I rank evidence in this order: peer-reviewed paired ablations on more than one corpus, then controlled single-corpus studies, then preprints, then vendor benchmarks with a disclosed setup, then vendor blog claims. The ranking matters most where the numbers point in opposite directions.

### 1. The optimum size

Reported operating points in this review: 150 to 300 tokens ([Jan 2026](https://arxiv.org/abs/2601.14123)), 200 to 400 ([Chroma](https://www.trychroma.com/research/evaluating-chunking)), 512 ([Best Practices](https://arxiv.org/abs/2407.01219)), 512 to 1,024 plus page-level ([NVIDIA](https://developer.nvidia.com/blog/finding-the-best-chunking-strategy-for-accurate-ai-responses/)), and 4,096 ([LongRAG](https://arxiv.org/abs/2406.15319)).

The setups explain most of the spread. The 2026 NQ study fills a context budget with as many chunks as fit, so small chunks win. The Best Practices study uses a fixed top-k and a single financial document, so a 512-token chunk gives the reader a coherent block. LongRAG uses a long-context reader and groups related documents into 4K units, then retrieves fewer than eight of them. NVIDIA measures end-to-end accuracy on financial documents and finds page-level or 512 to 1,024 depending on the dataset.

Which evidence is stronger for this pipeline? The controlled same-pipeline size grids, because they isolate the variable. That means Best Practices and Chroma first, then the 2026 NQ study as a check on the lower bound. The LongRAG result is not comparable: its reader is long-context and its units are document groups, not arbitrary text windows. The NVIDIA result is a vendor benchmark and should be treated as a directional claim.

Mix-of-Granularity ([1 Jun 2024](https://arxiv.org/abs/2406.00456), COLING 2025) goes further and trains a router to choose a granularity level per query. It reports gains over a fixed choice. That is evidence that the optimum is query-dependent, not only corpus-dependent, and it means a single global chunk size is a compromise by construction.

### 2. Overlap

The 2026 NQ study is the cleanest overlap ablation. Adding 10 to 20 percent overlap produced no measurable gain in BERTScore or EM, and 20 percent overlap inflates chunk count by 1.25 times. Its recommendation is zero overlap unless the retriever is shown to benefit.

Chroma's results agree for a strong embedder. The cleanest same-size comparison in Chroma is at 400 tokens: 200 overlap had recall 88.1 against 89.5 with no overlap. At 800 tokens the 400-overlap default had recall 85.4. But with all-MiniLM-L6-v2, 250 tokens with 125 overlap had recall 82.4 against 77.1 with no overlap. That is the exception: a weaker, smaller embedder benefited from boundary redundancy.

Best Practices uses 20-token overlap throughout but does not ablate it. Anthropic's post notes that chunk size, boundary and overlap affect retrieval, without reporting an overlap ablation.

Which evidence is stronger? The paired ablations, which are the 2026 study and Chroma. They say zero overlap is safe and cheaper. The all-MiniLM result says test it if the embedder is weak. bge-m3 is not weak. The local preference for zero overlap is better supported than a 10 to 20 percent default.

### 3. Semantic versus sentence versus structure

Qu et al. find semantic chunking does not justify its cost on ordinary documents. Chroma finds semantic clustering competitive and sometimes best on precision. The 2026 NQ study finds sentence and semantic tied up to about 5,000 tokens. The 36-method study finds content-aware methods, especially paragraph grouping, beat fixed windows.

These are not all measuring the same contrast. "Semantic" in Qu et al. and Chroma means boundaries from embedding similarity between sentences. "Structure-aware" in the financial and 36-method studies means boundaries from the document's own elements. The second signal is stronger and cheaper. The first is not a reliable win. A pipeline that already has page, heading and table structure should use it and should not replace it with sentence-embedding clustering.

### 4. Small units versus large units

Dense X ([11 Dec 2023](https://arxiv.org/abs/2312.06648)) is the strongest case for small units. Propositions average 11.2 words against 58.5-word passages, and proposition retrieval beats passage retrieval by 9.7 Recall@5 points with Contriever and 2.8 with GTR. The paper's argument is information density: a fixed token budget carries more answer-bearing facts when the units are fine-grained.

LongRAG ([21 Jun 2024](https://arxiv.org/abs/2406.15319)) is the strongest case for large units. Grouping documents into 4K-token units and retrieving fewer than eight of them reached 62.7 EM on NQ and 64.3 on HotpotQA without training, on par with a fully trained state-of-the-art model. Its argument is retriever burden: bigger units mean fewer needles to search.

Both can be right because they optimise different stages. Dense X optimises what the retriever ranks and how much irrelevant text comes with the answer. LongRAG optimises what the reader sees and how hard the retriever has to work. The resolution in practice is to separate the two units: retrieve small, generate larger. That is what sentence-window and small-to-big do, and it is why their results are positive but modest.

### 5. Summaries as retrieval units

RAPTOR ([31 Jan 2024](https://arxiv.org/abs/2401.18059)) recursively clusters and summarises chunks into a tree and retrieves from multiple levels. It reports a 20 percentage point absolute improvement on QuALITY with GPT-4, and state-of-the-art results on multi-step QA. That is a strong result for summarisation as an additional retrieval layer.

Anthropic's post says it experimented with adding generic document summaries to chunks and saw very limited gains, and that it evaluated summary-based indexing and saw low performance. ARAGOG says the Document Summary Index is a competent retrieval approach. The three results differ in task and implementation: RAPTOR's summaries are hierarchical and retrieved alongside leaves, Anthropic's summary was a prefix, and ARAGOG's index summarises 3,072-token blocks.

Which is stronger? RAPTOR for multi-step reasoning with a strong reader. None of them for citation fidelity, because a summary is not the source text. A summary can be an extra index entry. It should not be the passage a citation points at.

### 6. More context helps or hurts

Anthropic reports 20 chunks better than 10 or 5 on retrieval failure. Lost in the Middle reports answer accuracy degrading when the relevant passage is in the middle, and falling below closed-book in the middle position. The 2026 NQ study reports a context cliff beyond about 2,500 tokens. These are compatible if retrieval recall and reader accuracy are separated: more passages raise recall, and more tokens can lower the reader's accuracy. The practical answer is to retrieve more than you show, rerank, and show a small set.

### 7. Which lever matters most

The chemistry study ([13 Jun 2025](https://arxiv.org/abs/2506.17277)) finds embedding choice is associated with the largest differences in evidence retrieval. Qu et al. conclude that embedding quality overshadowed chunking strategy. The 36-method study finds larger embedding models still remain sensitive to segmentation, and that better chunking and larger embeddings are complementary.

This is a genuine disagreement about where to spend effort. For this pipeline the embedding model is fixed by the constraint, so chunking is the lever that remains. That does not make chunking more important in general. It makes it the available variable here.

## Oversized elements, in the literature

An oversized element is one that cannot be a single chunk under the cap. The defect case has two: a 756 KB prose element and a 186 KB table. At about four bytes per token those are roughly 189,000 and 46,500 tokens. bge-m3's maximum is 8,192 tokens ([model card](https://huggingface.co/BAAI/bge-m3)). Neither fits the model's input window, so neither can be embedded as one unit without truncation.

The literature offers four treatments. They are not equally supported.

### Force-split

This is the best-supported option. The size studies show that boundaries matter less than size: fixed-size chunking is competitive with semantic chunking on ordinary documents (Qu et al.), sentence chunking ties semantic up to about 5,000 tokens (2026 NQ study), and recursive splitting beats token splitting at 400 tokens or less (Chroma). If arbitrary boundaries were catastrophic, fixed-size chunking would not keep winning these comparisons.

The practical form is a hierarchy: split on the document's structure first, then on sentences, then on characters. The character fallback is an engineering necessity rather than a research finding. A run of text with no sentence terminator has no sentence boundary to split on, and the alternative is either a failed embedding or a silent truncation. Neither is acceptable. The literature does not study the character fallback because it studies normal prose, which has punctuation.

For tables, force-splitting means rows, not characters. TableRAG ([7 Oct 2024](https://arxiv.org/abs/2410.04739)) shows why. On million-token tables, reading the whole table scored 4.6 percent on ArcadeQA and 9.1 percent on BirdQA with GPT-3.5. Schema plus cell retrieval scored 49.2 and 45.5. The paper's ablations attribute up to 9.4 points to schema retrieval and up to 11.5 points to cell retrieval. The mechanism is straightforward: a 100-column, 200-row table is over 40,000 tokens, the reader cannot use it well, and retrieval over rows and columns puts the relevant cells in front of the model.

The financial-report study ([5 Feb 2024](https://arxiv.org/abs/2402.05131)) supports element-type splitting for the same reason: tables, sections and other elements carry different kinds of evidence, and treating them as uniform text loses that.

### Summarise

RAPTOR ([31 Jan 2024](https://arxiv.org/abs/2401.18059)) is the strongest evidence that summarisation can help. It builds a tree of recursive summaries and retrieves from it, and reports a 20 point absolute gain on QuALITY with GPT-4. The Document Summary Index is rated competent in ARAGOG ([1 Apr 2024](https://arxiv.org/abs/2404.01037)). Anthropic's post reports the opposite for summary-based indexing, though with a different implementation.

The case against summarising as the primary treatment is citation fidelity. A summary is generated text, not the source. If the citation verifier checks the answer against a summary, it is checking the answer against a model's paraphrase, and the page range no longer points at the evidence. Summaries are defensible as an additional retrieval layer, where they help find the right region, and the citation still resolves to the original element. They are not defensible as a replacement for the cited passage.

There is also a cost. Summarising a 186 KB table with a local model is a generation job per element, and the summary can be wrong. RAPTOR's gains come with a tree-building pipeline that the current ingestion design does not have.

### Treat as its own unit

This is defensible only when the unit fits the model and the prompt. LongRAG's 4K units fit bge-m3's 8,192-token window. A 186 KB table does not. A 756 KB prose element does not.

When the element fits, treating it as one unit preserves its internal structure and its citation range. That is the right choice for a table that fits in 512 tokens, and the local design already does it: the whole table is the citation unit, and rows are separate retrieval units. The design breaks down when the whole table is 186 KB, because the citation unit is then unembeddable and unpromptable. At that point the whole-table entry should be a citation pointer, not an embedding.

### Exclude

I found no measured support for exclusion as a quality strategy. The retrieval literature treats missing evidence as a recall failure, and the defect is a recall problem already. Excluding an oversized element removes the only copy of whatever it contains. The defensible version of exclusion is narrow: skip a file that cannot be parsed at all, record it in the manifest, and surface it to the operator. Do not silently drop an element because it is large.

### What the literature does not cover

I found no paper that studies a 756 KB element, and none studies the exact choice between "split into 400 pieces" and "summarise into one". The recommendation to cap and split is an extrapolation from the size studies and from the embedding model's hard limit. The extrapolation is well grounded at the small end, because a 512-token piece behaves like the chunks in the studies. It is not grounded at the level of the whole 756 KB element, because nothing in the literature operates at that scale.

## What this implies for a 512-token bge-m3 pipeline

The current target is defensible. The defect is not the 512-token target. It is the absence of a hard cap and a fallback, and the decision to serialise a whole table into one chunk.

1. Keep 512 as the target, with 384 as a floor rather than a quota. 512 is inside the measured band and at the top of it. [Best Practices](https://arxiv.org/abs/2407.01219) put 512 at the highest faithfulness in its size table. [Chroma](https://www.trychroma.com/research/evaluating-chunking) puts the efficient range at 200 to 400 but measures 400 recall higher than 200. [NVIDIA](https://developer.nvidia.com/blog/finding-the-best-chunking-strategy-for-accurate-ai-responses/) found 512 best on its earnings dataset. The measured results do not support going to 1,024 as a default. If the evaluation shows precision falling, 384 is the first knob to try, not 1,024.

2. Make the cap hard and tokenizer-true. The current estimate multiplies word count by 1.35. That is a reasonable bound for prose and a poor one for tables, digits and OCR noise. A chunk that estimates at 512 can exceed 512 real bge-m3 tokens, and the reranker card's example truncates pairs at 512 tokens, so if the pipeline follows that example the tail of a longer chunk is unscored. The reranker [config](https://huggingface.co/BAAI/bge-reranker-v2-m3/raw/main/config.json) declares 8,194 position embeddings, so 512 is the card's usage default, not the model's ceiling. Use the real tokenizer for the cap, or leave a safety margin. The chunker already treats the estimate as a bound rather than an assertion, which is the right posture, but the bound needs to be conservative in the direction that matters.

3. Keep zero overlap. The paired ablations say 10 to 20 percent overlap buys nothing and inflates the index. Zero is the measured default. A 32-token maximum is cheap, but there is no evidence it helps bge-m3, and it makes two chunks claim the same sentence, which weakens the citation story.

4. Keep structure-aware, page-bounded splitting. It is the best-supported choice in the review. The [financial-report study](https://arxiv.org/abs/2402.05131) and the [36-method study](https://arxiv.org/abs/2603.06976) both find content-aware segmentation beats fixed windows. Page-bounding also preserves the page range, and every chunk can inherit the document-level access label, which the literature does not study but the product requires.

5. Do not adopt semantic chunking. The [best controlled study](https://arxiv.org/abs/2410.13070) finds no consistent gain on real documents, the [2026 NQ study](https://arxiv.org/abs/2601.14123) finds sentence chunking ties it up to about 5,000 tokens, and it would cross page and section boundaries. The measured upside does not cover the cost or the citation damage.

6. For tables, make the row the retrieval unit and the whole table the citation unit only when it fits. The current design is right in principle. The failure is the unbounded whole-table entry. [TableRAG](https://arxiv.org/abs/2410.04739) shows whole-table prompting collapses at scale, and row and cell retrieval recovers most of the accuracy. For a 186 KB table, serialise rows with the header repeated, give each row its page, and keep the whole-table record as a pointer for citation, not as an embedded chunk.

7. Do not rely on TEI to enforce the limit. Current TEI documentation states that `--auto-truncate` defaults to true ([CLI documentation](https://huggingface.co/docs/text-embeddings-inference/en/cli_arguments), accessed 2026-10-16), which means an oversized input can be silently truncated to the model maximum rather than rejected. Check the deployed version. `--max-batch-tokens 4096` is a batch-level control, not a per-input cap, and a single 8,192-token input would exceed it. The cap has to live in the chunker, before the request reaches TEI. This is also why a 756 KB chunk can be worse than a failed request: it may be embedded as its first 8,192 tokens and nothing in the pipeline would know.

8. Keep the retrieved context small. Six 512-token chunks are about 3,072 tokens. That is small enough that the reader is not being asked to use a 200K-token context, and it is more than seventy times below the defect case. The 2026 NQ study's context cliff at about 2,500 tokens is a warning against growing top-k without measuring. Use the reranker to reduce the set, not to justify a larger one.

9. Treat sentence-window expansion as the one cheap, measured upgrade to pilot. Retrieve the 512-token chunk, then prepend or append the neighbouring chunk at generation time. [ARAGOG](https://arxiv.org/abs/2404.01037) found sentence-window retrieval beat the best classic variant on precision by 0.1021. [Best Practices](https://arxiv.org/abs/2407.01219) found a sliding window improved faithfulness and relevancy where small-to-big did not. The cost is prompt tokens and citation precision. Measure both before enabling it by default.

10. Do not adopt late chunking or LLM contextual enrichment yet. Late chunking needs token-level embeddings that TEI does not return, and its measured gain is about two nDCG points. LLM contextual enrichment has the largest reported gain, 35 to 67 percent fewer retrieval failures, but it is a vendor claim, it costs one generation call per chunk, and it changes the indexed text, which complicates the citation check. A deterministic heading prefix is already a partial version and costs nothing.

### Why not, in one table

| Option | Why not now | What would change it |
|---|---|---|
| Semantic chunking | No consistent measured gain on ordinary documents; crosses page and section boundaries; extra embedding and clustering cost | A within-pipeline evaluation showing a gain on this corpus, with page-range citations preserved |
| Late chunking | Requires token-level embeddings; TEI returns pooled vectors; about two nDCG points at best; 8,192-token cap still forces macro-chunking | TEI exposes token embeddings, or a custom inference service is justified |
| LLM contextual enrichment | Vendor claim, not independently replicated; one generation per chunk; changes indexed text | A measured pilot on this corpus with the local model, including cost and citation impact |
| Parent-child / small-to-big | Weak measured effect (under two points on one document); sliding window did better | A measured gain on this corpus that survives the prompt-token cost |
| Sentence-window | Measured precision gain, but answer similarity variable and prompt cost is real | A measured gain on this corpus, with citation precision held constant |
| Summarise oversized elements | Lossy for citation; summary is not the source; generation cost | Evidence that a summary can serve as the cited passage without losing verification |
| Exclude oversized elements | No measured support; removes recall | Evidence that dropping an element improves answers more than it costs recall |

### What would change this recommendation

The recommendation would change on any of these. A within-pipeline evaluation on the office corpus that shows sentence-window expansion or small-to-big improves answer quality without hurting citation precision. TEI support for token-level embeddings, which would make late chunking testable. A table-heavy corpus where row-level retrieval plus a table-specific reader beats the general chunker, in which case a TableRAG-style schema and cell index is the next step.

Until then, the defensible position is: 512-token ceiling, zero overlap, structure first, sentence then character fallback, rows for tables, and never embed an element that the model cannot see whole.

## Sources

Primary papers and reports.

1. Wang et al., "Searching for Best Practices in Retrieval-Augmented Generation", arXiv:2407.01219, 1 Jul 2024. https://arxiv.org/abs/2407.01219
2. Smith and Troynikov, "Evaluating Chunking Strategies for Retrieval", Chroma technical report, 3 Jul 2024. https://www.trychroma.com/research/evaluating-chunking
3. Qu, Tu and Bao, "Is Semantic Chunking Worth the Computational Cost?", arXiv:2410.13070, 16 Oct 2024; Findings of NAACL 2025. https://arxiv.org/abs/2410.13070 and https://aclanthology.org/2025.findings-naacl.114/
4. Chen et al., "Dense X Retrieval: What Retrieval Granularity Should We Use?", arXiv:2312.06648, 11 Dec 2023; EMNLP 2024. https://arxiv.org/abs/2312.06648
5. Günther et al., "Late Chunking: Contextual Chunk Embeddings Using Long-Context Embedding Models", arXiv:2409.04701, 7 Sep 2024, v3 7 Jul 2025. https://arxiv.org/abs/2409.04701
6. Eibich, Nagpal and Fred-Ojala, "ARAGOG: Advanced RAG Output Grading", arXiv:2404.01037, 1 Apr 2024. https://arxiv.org/abs/2404.01037
7. Jiang, Ma and Chen, "LongRAG: Enhancing Retrieval-Augmented Generation with Long-context LLMs", arXiv:2406.15319, 21 Jun 2024. https://arxiv.org/abs/2406.15319
8. Sarthi et al., "RAPTOR: Recursive Abstractive Processing for Tree-Organized Retrieval", arXiv:2401.18059, 31 Jan 2024. https://arxiv.org/abs/2401.18059
9. Chen et al., "TableRAG: Million-Token Table Understanding with Language Models", arXiv:2410.04739, 7 Oct 2024; NeurIPS 2024. https://arxiv.org/abs/2410.04739
10. Jimeno Yepes et al., "Financial Report Chunking for Effective Retrieval Augmented Generation", arXiv:2402.05131, 5 Feb 2024, v3 16 Mar 2024. https://arxiv.org/abs/2402.05131
11. "A Systematic Analysis of Chunking Strategies for Reliable Question Answering", arXiv:2601.14123, 20 Jan 2026, preprint. https://arxiv.org/abs/2601.14123
12. Shaukat, Adnan and Kuhn, "A Systematic Investigation of Document Chunking Strategies and Embedding Sensitivity", arXiv:2603.06976, 7 Mar 2026, preprint. https://arxiv.org/abs/2603.06976
13. "Chunk Twice, Embed Once: A Systematic Study of Segmentation and Representation Trade-offs in Chemistry-Aware Retrieval-Augmented Generation", arXiv:2506.17277, 13 Jun 2025, v2 17 Sep 2026, preprint. https://arxiv.org/abs/2506.17277
14. Lu et al., "HiChunk: Evaluating and Enhancing Retrieval-Augmented Generation with Hierarchical Chunking", arXiv:2509.11552, 15 Sep 2025, preprint. https://arxiv.org/abs/2509.11552
15. Zhong et al., "Mix-of-Granularity: Optimize the Chunking Granularity for Retrieval-Augmented Generation", arXiv:2406.00456, 1 Jun 2024; COLING 2025. https://arxiv.org/abs/2406.00456
16. Liu et al., "Lost in the Middle: How Language Models Use Long Contexts", arXiv:2307.03172, 6 Jul 2023; TACL. https://arxiv.org/abs/2307.03172
17. Li et al., "Retrieval Augmented Generation or Long-Context LLMs? A Comprehensive Study and Hybrid Approach", arXiv:2407.16833, 23 Jul 2024; EMNLP 2024 industry track. https://arxiv.org/abs/2407.16833
18. Gao et al., "Enabling Large Language Models to Generate Text with Citations", arXiv:2305.14627, 24 May 2023, v2 31 Oct 2023. https://arxiv.org/abs/2305.14627
19. Chen et al., "M3-Embedding: Multi-Linguality, Multi-Functionality, Multi-Granularity Text Embeddings Through Self-Knowledge Distillation", arXiv:2402.03216, 5 Feb 2024, v5 12 Dec 2025. https://arxiv.org/abs/2402.03216

Vendor claims and parameter documentation.

20. Anthropic, "Introducing Contextual Retrieval", 19 Sep 2024, vendor claim. https://www.anthropic.com/engineering/contextual-retrieval
21. NVIDIA, "Finding the Best Chunking Strategy for Accurate AI Responses", 18 Jun 2025, vendor benchmark. https://developer.nvidia.com/blog/finding-the-best-chunking-strategy-for-accurate-ai-responses/
22. Hugging Face, "Text Embeddings Inference: CLI arguments", accessed 2026-10-16. https://huggingface.co/docs/text-embeddings-inference/en/cli_arguments
23. Hugging Face, "BAAI/bge-m3 model card", accessed 2026-10-16. https://huggingface.co/BAAI/bge-m3
24. Hugging Face, "BAAI/bge-reranker-v2-m3 model card", accessed 2026-10-16. https://huggingface.co/BAAI/bge-reranker-v2-m3
25. Hugging Face, "BAAI/bge-reranker-v2-m3 config", accessed 2026-10-16. https://huggingface.co/BAAI/bge-reranker-v2-m3/raw/main/config.json
