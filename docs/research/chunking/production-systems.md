# How production RAG systems chunk and budget context

Research date: 2026-10-16. No system was installed or tested. Everything below comes from vendor documentation or, where the docs omit a number, the project's source on its main branch. I label those cases.

The prompting defect is arithmetic first. A 756 KB prose chunk is roughly 190,000 tokens at four characters per token. The 186 KB table is roughly 47,000 tokens. Six passages totalling 900 KB are roughly 225,000 tokens. Those are estimates, not measurements, but they are the right order of magnitude, and they explain a provider rejecting the request before any model sees it. The 81 of 341 documents with a chunk over 4 KB are the visible tail of the same failure: 4 KB is already eight times a 500-character chunk and about twice a 512-token chunk.

## What I could not verify, and how to read this

Gaps first, because they bound the conclusions.

- **AWS Bedrock Knowledge Bases** publishes no numeric default for `numberOfResults`, no table-specific chunking behavior, no per-passage cap on what retrieval returns, and no statement about truncation before the model call. Its doc pages carry no last-updated date.
- **Azure AI Search** token-based splitting (`unit: azureOpenAITokens`) is still marked preview. The `pageOverlapLength` default is not stated on the Split skill page. The Document Layout skill's `maximumLength` default is not stated. Azure OpenAI On Your Data's chunk-size setting is preview and ingestion-side. The numeric default for `maxOutputSizeInTokens` in agentic retrieval is not published, only its behavior.
- **Google** RAG Engine's `ChunkingConfig` documents `chunk_size` and `chunk_overlap` but no default and no maximum. Document AI's `ChunkingConfig.chunk_size` is a list of sizes "in order of level" with no default or maximum. No page states what happens when one element exceeds the chunk size. Google doc pages show no last-updated date.
- **LangChain and LlamaIndex** numeric defaults live in source. The doc pages describe the parameters but often show a tutorial value rather than the constructor default. Two documented examples contradict the current source (LlamaIndex overlap, Chonkie overlap), which I flag in place.
- **Qdrant, Weaviate, and pgvector** have no built-in chunker. There is no chunking parameter to compare. Their documented role is storage and retrieval of vectors you produced elsewhere.
- **Docling's** 256-token default is not on its chunking page. It follows from the default tokenizer, `sentence-transformers/all-MiniLM-L6-v2`, whose `max_seq_length` is 256. I label that source-derived.
- **Elasticsearch** `semantic_text` chunking defaults can come from the inference endpoint, so the API defaults (250 words, 1 sentence overlap) are a floor, not a guarantee.
- **OpenAI** publishes chunking numbers in its OpenAPI schema rather than prose. It documents no table handling.
- Nothing here tells you which chunk size retrieves best. These systems disagree because the number is corpus-dependent. What they agree on is that a chunk must have a ceiling and that the ceiling must survive contact with a table.

Evidence labels used below: "docs" means the vendor page states it. "Source" means the project's code states it but the docs page does not. "Inference" is mine.

## The systems, one by one

### AWS Bedrock Knowledge Bases

**What it is.** Managed ingestion from S3 and other sources into a vector store, with retrieval and optional generation through `Retrieve` and `RetrieveAndGenerate`.

**Chunking parameters.** The API's `chunkingConfiguration.chunkingStrategy` accepts `DEFAULT`, `FIXED_SIZE`, `HIERARCHICAL`, `SEMANTIC`, and `NONE`. If you omit it, or pass `DEFAULT`, the service "uses fixed-size chunking with 300 tokens and 20% overlap" ([customize ingestion](https://docs.aws.amazon.com/bedrock/latest/userguide/kb-managed-customize-ingestion.html)).

- `fixedSizeChunkingConfiguration.maxTokens`: 1 to 8192. `overlapPercentage`: 1 to 99. Both required when the strategy is `FIXED_SIZE` ([API reference](https://docs.aws.amazon.com/bedrock/latest/APIReference/API_agent_FixedSizeChunkingConfiguration.html)).
- `hierarchicalChunkingConfiguration.levelConfigurations`: exactly two levels. `overlapTokens`: minimum 1, no documented maximum ([API reference](https://docs.aws.amazon.com/bedrock/latest/APIReference/API_agent_HierarchicalChunkingConfiguration.html)). The launch post recommends a 1,500-token parent, a 300-token child, and overlap at 20 percent of the child size, and says both levels accept 1 to 8,192 tokens ([AWS blog](https://aws.amazon.com/blogs/machine-learning/amazon-bedrock-knowledge-bases-now-supports-advanced-parsing-chunking-and-query-reformulation-giving-greater-control-of-accuracy-in-rag-based-applications/)). The parent is not embedded.
- `semanticChunkingConfiguration.maxTokens`: 1 to 8192. `bufferSize`: 0 or 1. `breakpointPercentileThreshold`: 50 to 99 ([API reference](https://docs.aws.amazon.com/bedrock/latest/APIReference/API_agent_SemanticChunkingConfiguration.html)).
- `NONE` treats each document as one chunk. Bedrock warns that page numbers disappear from citations in that mode ([chunking overview](https://docs.aws.amazon.com/bedrock/latest/userguide/kb-chunking.html)).

**What happens when an element exceeds the ceiling.** Fixed-size chunking is defined by a token window, so an oversized element has to be cut at that window, though Bedrock does not describe the fallback. For semantic chunking the API reference is explicit: the sentence-similarity pass creates chunks, and "these chunks are further split if they exceed the max token size." For parsed content, Bedrock says the chunker "respects logical document boundaries (such as pages or sections) and does not merge content across these boundaries, even when increasing the maximum token size would otherwise allow for larger chunks." That is a page-boundary rule, not a table rule.

**Tables.** Not documented. The advanced parsing doc talks about layout-aware parsing, but the chunking page says nothing about serializing, isolating, or row-splitting a table. A 186 KB table with `NONE` is one 186 KB chunk by design. Even `FIXED_SIZE` only guarantees a token ceiling, not that a table's rows survive a split. This is a real gap, not a claim that Bedrock mishandles tables.

**Answer side.** `KnowledgeBaseVectorSearchConfiguration.numberOfResults` is optional, integer, minimum 1, maximum 100 ([API reference](https://docs.aws.amazon.com/bedrock/latest/APIReference/API_agent-runtime_KnowledgeBaseVectorSearchConfiguration.html)). No default is stated there. AWS examples use 5 ([AWS blog](https://aws.amazon.com/blogs/machine-learning/knowledge-bases-for-amazon-bedrock-now-supports-custom-prompts-for-the-retrieveandgenerate-api-and-configuration-of-the-maximum-number-of-retrieved-results/)). There is no documented per-passage token cap. With hierarchical chunking, child hits are replaced by parents, so "the returned number of results might be less than the requested amount" ([chunking overview](https://docs.aws.amazon.com/bedrock/latest/userguide/kb-chunking.html)). There is no documented disclosure when the assembled prompt is too large. The failure surfaces as a provider error.

**Doc date.** No last-updated date on the AWS pages. Fetched 2026-10-16.

### Azure AI Search, the Split skill, and Azure OpenAI On Your Data

**What it is.** Two layers that are easy to confuse. The Split skill is an indexer-side text chunker. Azure OpenAI On Your Data is an answer service that ingests into Azure AI Search with its own chunk-size setting.

**Split skill parameters.** `textSplitMode` is `pages` or `sentences`. For `pages`, `maximumPageLength` is measured in characters when `unit` is `characters`, with a minimum of 300, a maximum of 50,000, and a default of 5,000. The docs say the algorithm does its best to break on sentence boundaries, so actual chunks can be smaller ([Split skill](https://learn.microsoft.com/en-us/azure/search/cognitive-search-skill-textsplit)). `pageOverlapLength` is the number of characters or tokens repeated from the previous page. The page does not state its default. Setting 0 disables overlap. `maximumPagesToTake` defaults to 0, meaning all pages.

`unit` also accepts `azureOpenAITokens` (preview). In token mode, `maximumPageLength` is the model's token limit, and Azure recommends 512 tokens for text embedding models. `azureOpenAITokenizerParameters.encoderModelName` defaults to `cl100k_base`. The allowed values are `r50k_base`, `p50k_base`, and `p50k_edit`. `o200k_base` is explicitly not supported.

**What happens when an element exceeds the ceiling.** The skill cuts at `maximumPageLength`, preferring sentence boundaries. There is no structural exception. A table exported as a single string is cut at the character or token limit wherever that lands.

**Tables.** The Split skill has no table handling. The Document Intelligence Layout skill can emit Markdown, but its `chunkingProperties` only apply when `outputFormat` is `text`, and `unit` there accepts only `characters`. `maximumLength` is 300 to 50,000 characters, `overlapLength` must be less than half of it, and the page gives an example of 2,000 and 200 but no default ([Document Intelligence Layout skill](https://learn.microsoft.com/en-us/azure/search/cognitive-search-skill-document-intelligence-layout)). Character chunking can split a table mid-row. Azure does not document a row-aware fallback.

**Integrated vectorization.** Azure describes chunking as necessary for embedding token limits and names three chunking choices: the Text Split skill, the Content Understanding skill, or one of the document parsing modes ([integrated vectorization](https://learn.microsoft.com/en-us/azure/search/vector-search-integrated-vectorization)). There is no separate default chunker hiding in the pipeline. Secondary indexes hold the chunks. The primary index can hold the parent document, and a hit in the secondary index can return the parent.

**Answer side, On Your Data.** The ingestion setting "Chunk size (preview)" is "the maximum size in terms of the number of tokens of any chunk in the search index", and its default is 1,024 tokens. Azure states the budget relationship plainly: "the chunk size multiplied by the number of retrieved documents is the total number of tokens sent to the model" ([On Your Data](https://learn.microsoft.com/en-us/azure/ai-services/openai/concepts/use-your-data)).

Runtime parameters are `topNDocuments`, which accepts 3, 5, 10, or 20 and defaults to 5; `strictness`, 1 to 5 with a default of 3; and `inScope`, default true. Azure documents truncation for the question ("If the question exceeds the token limit, it will be truncated") and for the system message ("we truncate the system message after a specific number of tokens"). It does not document truncating an individual retrieved chunk.

**Answer side, agentic retrieval.** Azure AI Search's knowledge base `retrieve` accepts `maxOutputDocuments` and `maxOutputSizeInTokens` (`maxOutputSize` from 2026-05-01-preview). A document that exceeds the output budget "can be omitted from the response", and the activity array includes a warning. If both limits are set, it returns "up to maxOutputDocuments documents or however many documents fit under maxOutputSizeInTokens, whichever limit applies first" ([agentic retrieval](https://learn.microsoft.com/en-us/azure/search/search-agentic-retrieval-how-to-retrieve)). The page does not publish the numeric default for the token budget.

**Doc dates.** Split skill 2026-01-07. Document Layout skill 2026-08-08. Integrated vectorization 2026-01-23. On Your Data 2026-10-14. Agentic retrieval 2026-09-04. These are the `ms.date` values in the page metadata.

### Google Vertex AI RAG Engine, Document AI, and Vertex AI Search

**RAG Engine parameters.** `rag.ChunkingConfig(chunk_size, chunk_overlap)` is expressed in tokens. The API reference says only "Number of tokens each chunk has" and "The overlap between chunks" ([RAG API](https://docs.cloud.google.com/gemini-enterprise-agent-platform/reference/models/rag-api-v1)). No default and no maximum are documented. The layout-parser guide recommends 1024 tokens with 256 overlap and uses that in its sample. The LLM-parser page shows 1024 and 200, both marked optional ([layout parser](https://docs.cloud.google.com/gemini-enterprise-agent-platform/build/rag-engine/layout-parser-integration)). The API reference's sample also uses 1024 and 256.

**What happens when an element exceeds the ceiling.** Not documented. RAG Engine delegates parsing to a parser (layout parser, LLM parser, or the default), then chunks the parsed text. There is no documented structural exception or last-resort splitter description.

**Document AI.** The layout parser produces "context-aware chunks" that include content from ancestral headings and table headers, and annotates figures and tables with textual descriptions ([layout parser overview](https://docs.cloud.google.com/document-ai/docs/layout-parse-chunk)). Its `ChunkingConfig` has `chunk_size`, described as "the chunk sizes to use when splitting documents, in order of level", and `include_ancestor_headings` ([RPC reference](https://docs.cloud.google.com/document-ai/docs/reference/rpc/google.cloud.documentai.v1)). No default or maximum is published. A list of sizes implies hierarchical chunking, which is more than most vendors document.

**Vertex AI Search, a different product.** Its layout-based chunking config caps `chunkSize` at 100 to 500 tokens with a default of 500, and `includeAncestorHeadings` defaults to false ([Python client reference](https://docs.cloud.google.com/python/docs/reference/discoveryengine/latest/google.cloud.discoveryengine_v1.types.DocumentProcessingConfig.ChunkingConfig.LayoutBasedChunkingConfig)). That 500-token cap is tighter than the RAG Engine's recommended 1,024 and the Azure and LlamaIndex defaults, though Bedrock's 300-token default is smaller still. It is not the RAG Engine default.

**Tables.** The layout parser's documented value is that table headers and ancestral headings are included in each chunk. It does not say whether a table is split by row, kept whole, or serialized. The 20 MB file limit and 500-page PDF limit are documented. The table-splitting rule is not.

**Answer side.** `RagRetrievalConfig.top_k` is optional, "the number of contexts to retrieve". No default is stated. Examples use 10 ([RAG API](https://docs.cloud.google.com/gemini-enterprise-agent-platform/reference/models/rag-api-v1)). There is no documented per-passage cap or prompt-budget enforcement.

**Doc dates.** No last-updated dates visible. Fetched 2026-10-16.

### LangChain

**Recursive character splitter.** `RecursiveCharacterTextSplitter` takes `separators`, `keep_separator` (default `True`), and `is_separator_regex` (default `False`). The separator list defaults to `["\n\n", "\n", " ", ""]`. The docs state that list and explain the recursion ([recursive guide](https://docs.langchain.com/oss/python/integrations/splitters/recursive_text_splitter)). The class itself sets no chunk size, so it inherits `TextSplitter`'s defaults: `chunk_size=4000`, `chunk_overlap=200`, `length_function=len` ([base.py](https://github.com/langchain-ai/langchain/blob/master/libs/text-splitters/langchain_text_splitters/base.py)). That is 4,000 characters, not tokens. The recursive guide's example passes `chunk_size=100` explicitly, which is why the default is easy to miss.

**What happens when an element exceeds the ceiling.** The recursion tries `\n\n`, then `\n`, then a space, then the empty string. The empty string makes `list(text)`, so the final fallback is a character split and the ceiling is hard. A table with no paragraph breaks falls through to the empty separator and is cut mid-cell, mid-number, or mid-header. There is no table-aware path.

**Token splitter.** `TokenTextSplitter` defaults to `encoding_name="gpt2"` and inherits the same 4,000-token ceiling and 200-token overlap ([base.py](https://github.com/langchain-ai/langchain/blob/master/libs/text-splitters/langchain_text_splitters/base.py)). The docs recommend `from_tiktoken_encoder` when you want a specific model tokenizer, but the object's own default is GPT-2's tokenizer and 4,000 tokens. That is a large default for a "token splitter".

**Parent-document retriever.** `ParentDocumentRetriever` requires `child_splitter`. `parent_splitter` defaults to `None`, and the docstring says: "If none, then the parent documents will be the raw documents passed in" ([source](https://github.com/langchain-ai/langchain/blob/master/libs/langchain/langchain_classic/retrievers/parent_document_retriever.py)). This is the mechanism that can hand a 756 KB document to the prompt builder: the child chunks retrieve, then the whole raw parent is returned. LangChain documents the mechanism and does not cap the parent.

**Answer side.** `VectorStore.similarity_search` defaults to `k=4` ([source](https://github.com/langchain-ai/langchain/blob/master/libs/core/langchain_core/vectorstores/base.py)). There is no token budget in the retriever. `ContextualCompressionRetriever` and the various document compressors exist, but they are opt-in, and their default behavior is not a cap.

**Doc date.** Docs pages carry no version date. Source is `master` as of 2026-10-16.

### LlamaIndex

**Sentence splitter.** `SentenceSplitter` defaults to `chunk_size=1024` tokens and `chunk_overlap=200` ([constants](https://github.com/run-llama/llama_index/blob/main/llama-index-core/llama_index/core/constants.py), [sentence.py](https://github.com/run-llama/llama_index/blob/main/llama-index-core/llama_index/core/node_parser/text/sentence.py)). The docs page's example passes `chunk_size=1024, chunk_overlap=20` ([node parser modules](https://docs.llamaindex.ai/en/stable/module_guides/loading/node_parsers/modules/)). That 20 does not match the current source default of 200. The example may be deliberately non-default, but the page does not say so, and I would check the installed version before copying it. `TokenTextSplitter` defaults to 1024/200 with `separator=" "`.

**Sentence window.** `SentenceWindowNodeParser` defaults to `window_size=3`, `window_metadata_key="window"`, `original_text_metadata_key="original_sentence"` ([source](https://github.com/run-llama/llama_index/blob/main/llama-index-core/llama_index/core/node_parser/text/sentence_window.py)). Each indexed node is one sentence. At answer time, `MetadataReplacementPostProcessor(target_metadata_key="window")` replaces the sentence with the window, so one hit becomes the sentence plus three on each side, up to seven sentences. That is a documented expansion of the retrieved context, not a cap.

**Semantic and hierarchical.** `SemanticSplitterNodeParser` defaults to `buffer_size=1` and `breakpoint_percentile_threshold=95`. `HierarchicalNodeParser` defaults to `chunk_sizes=[2048, 512, 128]`, paired with `AutoMergingRetriever` ([node parser modules](https://docs.llamaindex.ai/en/stable/module_guides/loading/node_parsers/modules/)). The hierarchy is capped at the largest size you configure, unlike LangChain's raw-parent default.

**Answer side.** `VectorIndexRetriever` defaults to `similarity_top_k=2` ([source](https://github.com/run-llama/llama_index/blob/main/llama-index-core/llama_index/core/indices/vector_store/retrievers/retriever.py), [constants](https://github.com/run-llama/llama_index/blob/main/llama-index-core/llama_index/core/constants.py)). Two hits at 1,024 tokens each is a small prompt by default. Add sentence windows and it grows by a documented factor. There is no token budget in the retriever or the response synthesizer.

**Doc date.** Docs and source fetched 2026-10-16. No version pinned on the page.

### OpenSearch

**The `text_chunking` ingest processor.** It supports `fixed_token_length`, `fixed_char_length`, and `delimiter`, with `fixed_token_length` as the default algorithm. Parameters for `fixed_token_length` ([processor docs](https://docs.opensearch.org/latest/ingest-pipelines/processors/text-chunking/)):

- `token_limit`: default 384, minimum 1. The docs derive it as 512 × 0.75 so passages fit a 512-token embedding model.
- `tokenizer`: default `standard`.
- `overlap_rate`: 0 to 0.5, default 0.
- `max_chunk_limit`: default 100; `-1` disables it.

`fixed_char_length` has `char_limit` default 2,048 (512 × 4) and the same overlap and chunk-limit defaults. `delimiter` defaults to `\n\n`.

**What happens when an element exceeds the ceiling.** It tokenizes and cuts. The important documented edge is the chunk limit: "If the number of passages generated by the processor exceeds the limit, the excess text is added to the last chunk" ([processor docs](https://docs.opensearch.org/latest/ingest-pipelines/processors/text-chunking/)). That is a documented way to produce a chunk larger than `token_limit`, and it is the clearest anti-pattern in this survey. The chunk count limit is enforced by inflating the last chunk, not by dropping text or erroring.

**Tables.** No table handling. A table serialized with newlines can be cut by the delimiter or token window. Nothing documents row preservation.

**Answer side.** Retrieval is the search layer's `size` or the neural query's `k`. The processor itself has no context budget. The ingest-time chunk limit is the only place a count is capped, and it caps chunks per document, not passages per prompt.

**Doc date.** Current `latest` docs. No date shown. Fetched 2026-10-16.

### Elasticsearch

**Two different mechanisms.** The `split` ingest processor only splits a string field on a separator. It has no size parameter and is not a chunker ([split processor](https://www.elastic.co/docs/reference/enrich-processor/split-processor)). Chunking proper lives in the inference endpoint's `chunking_settings` and is applied by `semantic_text`.

**`semantic_text` chunking.** `chunking_settings.strategy` accepts `sentence`, `word`, `none`, or `recursive`. The default strategy is `sentence`. Defaults from the inference API ([PUT inference](https://www.elastic.co/docs/api/doc/elasticsearch/operation/operation-inference-put-elasticsearch)):

- `max_chunk_size`: 250 words. Minimum 20 for sentence strategy, 10 for word strategy.
- `overlap`: 100 words, word strategy only, cannot exceed half of `max_chunk_size`.
- `sentence_overlap`: 0 or 1, default 1, sentence strategy only.
- `recursive` requires `max_chunk_size` plus `separators` or `separator_group`.

The `semantic_text` mapping docs confirm that `chunking_settings` overrides the endpoint and that the default is "the optimal chunking settings for Elastic Rerank" ([semantic text](https://www.elastic.co/docs/reference/elasticsearch/mapping-reference/semantic-text)). The numeric defaults above are the inference API's, so an endpoint can change them.

**What happens when an element exceeds the ceiling.** It splits by sentence or word. With `strategy: none`, there is no chunking: Elastic documents that if the input exceeds the model's token limit, "the elastic and elasticsearch services will automatically truncate the input to fit within the model's limit", while some services such as OpenAI may return an error ([semantic text reference](https://www.elastic.co/docs/reference/elasticsearch/mapping-reference/semantic-text-reference)). That is a documented automatic truncation path, and it applies at indexing time, not prompt time.

**Tables.** No table handling. A table in a `semantic_text` field is text and is chunked by sentence or word boundaries.

**Answer side.** `semantic_text` stores chunk offsets, and a query returns chunks. The app decides how many to send. There is no documented prompt budget.

**Doc date.** Current docs. No version shown on the page. Fetched 2026-10-16.

### Qdrant, Weaviate, and pgvector

These are storage and retrieval engines, not chunkers. Qdrant's search API takes a `limit` and returns points with payloads. The payload is whatever the ingest pipeline stored ([Qdrant search](https://qdrant.tech/documentation/search/search/)). Weaviate's `near_text` takes a `limit`. It does not split documents ([Weaviate similarity search](https://docs.weaviate.io/weaviate/search/similarity)). pgvector's README documents vector columns and distance operators, not text splitting ([pgvector](https://github.com/pgvector/pgvector)). None of them enforces a per-point size or a prompt budget.

The one documented chunker in the Postgres family is Timescale's pgai vectorizer, which is a separate extension. Its `ai.chunking_character_text_splitter` defaults to `chunk_size=800` characters, `chunk_overlap=400`, separator `E'\n\n'`. Its recursive variant defaults to 800 characters, 400 overlap, and separators `["\n\n", "\n", ".", "?", "!", " ", ""]` ([pgai API reference](https://github.com/timescale/pgai/blob/main/docs/vectorizer/api-reference.md)). That is a character-based default, and it is more conservative than LangChain's 4,000.

**Doc dates.** Fetched 2026-10-16. No dates shown.

### Unstructured

**The numbers.** `max_characters` defaults to 500 characters and is a hard maximum: "No chunk will exceed this number of characters. A single element that by itself exceeds this size will be divided into two or more chunks using text-splitting." `new_after_n_chars` defaults to `max_characters` and is the soft maximum. `overlap` defaults to 0 and applies only when splitting an oversized element. `overlap_all` defaults to False. `combine_text_under_n_chars` defaults to `max_characters` ([chunking docs](https://docs.unstructured.io/open-source/core-functionality/chunking)).

**What happens when an element exceeds the ceiling.** It is isolated, never combined with another element, and text-split into two or more chunks. `overlap` can soften the seam. The invariant is explicit and it is exactly the invariant our chunker lacks.

**Tables.** "A Table element is always isolated and never combined with another element. A Table can be oversized, like any other text element, and in that case is divided into two or more TableChunk elements using text-splitting" ([chunking docs](https://docs.unstructured.io/open-source/core-functionality/chunking)). So Unstructured does not produce a 186 KB table chunk. It does produce table chunks cut at arbitrary character positions, which is safer for the provider but bad for reading a row. The `by_title` strategy preserves section boundaries and optionally page boundaries. `multipage_sections` defaults to True.

**Answer side.** Unstructured is an ingestion library and documents no retrieval or prompt side.

**Doc date.** No date shown. Fetched 2026-10-16.

### Chonkie

**The numbers.** `TokenChunker` defaults to `tokenizer="character"`, `chunk_size=2048`, `chunk_overlap=0` ([docs](https://docs.chonkie.ai/oss/chunkers/token-chunker), [source](https://github.com/chonkie-inc/chonkie/blob/main/src/chonkie/chunker/token.py)). The default tokenizer is character-based, so the default ceiling is 2,048 characters, not 2,048 model tokens. If you pass `tokenizer="gpt2"`, the ceiling becomes model tokens.

The docs page's code sample passes `chunk_overlap=128` while calling it "default parameters", and the parameter table on the same page says the default is 0. The source says 0. I would trust the source and treat the sample as misleading.

**What happens when an element exceeds the ceiling.** It encodes and cuts at the token or character window. There is no structural or table-aware path documented.

**Tables.** No documented table handling.

**Doc date.** No date shown. Fetched 2026-10-16.

### Docling

**The numbers.** `HybridChunker` starts from Docling's document hierarchy and applies tokenization-aware refinements: one pass splits chunks that are oversized in tokens, and another merges undersized successive chunks with the same headings and captions. `merge_peers` defaults to True. `repeat_table_header` defaults to True. `omit_header_on_overflow` defaults to False. `always_emit_headings` defaults to False ([chunking docs](https://docling-project.github.io/docling/concepts/chunking/), [source](https://github.com/docling-project/docling-core/blob/main/docling_core/transforms/chunker/hybrid_chunker.py)).

`max_tokens` is not a plain field on the chunker. It comes from the tokenizer, and the default tokenizer is `sentence-transformers/all-MiniLM-L6-v2`, whose sentence-transformers config sets `max_seq_length` to 256 ([source](https://github.com/docling-project/docling-core/blob/main/docling_core/transforms/chunker/tokenizer/huggingface.py)). So the effective default is 256 tokens, but the docs page does not state that number. A caller can pass a different tokenizer or `max_tokens` explicitly.

**What happens when an element exceeds the ceiling.** It splits along document-item boundaries, growing windows up to the token limit and shrinking back when the estimate undercounts ([source](https://github.com/docling-project/docling-core/blob/main/docling_core/transforms/chunker/hybrid_chunker.py)). That is structural splitting first, token refinement second, which is the pattern most other systems approximate with a flat window.

**Tables.** This is the best-documented table behavior in the survey. When a table spans multiple chunks, headers are repeated at the beginning of each chunk (`repeat_table_header=True`). With `omit_header_on_overflow=True`, a row that fits without the header but not with it gets the header omitted, preserving row integrity. The docs describe maximizing token efficiency "while preserving line integrity for structured content" ([chunking docs](https://docling-project.github.io/docling/concepts/chunking/)). A row is still the split unit, not a character.

**Answer side.** Docling is ingestion only. It documents no retrieval or prompt budget.

**Doc date.** No date shown. Fetched 2026-10-16. The 256 default is source-derived, not on the docs page.

### OpenAI File Search, as a production reference

Worth including because it is widely used and its numbers are unambiguous. `auto` chunking uses `max_chunk_size_tokens=800` and `chunk_overlap_tokens=400`. `static` accepts `max_chunk_size_tokens` from 100 to 4,096, default 800, and `chunk_overlap_tokens` default 400 ([OpenAPI schema](https://github.com/openai/openai-openapi/blob/master/openapi.yaml), [retrieval guide](https://platform.openai.com/docs/guides/retrieval)). Search returns at most 10 results by default, and `max_num_results` can raise that to 50. There is no documented table handling, no per-passage cap at answer time, and no documented truncation disclosure. The 50 percent overlap (400 of 800) ties pgai's 800/400 character splitter for the largest default overlap in this survey.

**Doc date.** No date shown. Fetched 2026-10-16.

## What they all do the same way

**1. The chunk ceiling is set at ingestion, and the prompt budget is left implicit.** Every system with a chunker caps the unit it indexes, or offers a mode that does not. Almost none caps the sum it sends. The budget is `k × chunk_size` in practice, which is why a 1,024-token chunk with `topNDocuments=5` is a 5,120-token passage budget before the system prompt. Azure On Your Data is the only one that states that multiplication as a design rule. Azure's knowledge base is the only one that turns it into a request-level token cap.

**2. Token-aware systems cluster at 300 to 1,024 tokens.** Bedrock's default is 300. OpenSearch's default is 384. OpenAI's auto is 800. Azure On Your Data and LlamaIndex default to 1,024. Google's layout-parser guide recommends 1,024. Elastic's 250 words is roughly 330 tokens. Docling resolves to 256. Those are all within a factor of four. Most fit inside a 512-token embedding window; the 1,024-token defaults assume a larger embedding model, which is common for modern embedders. The character-based defaults are the outliers: 500 characters (Unstructured), 4,000 characters (LangChain), 5,000 characters (Azure Split), 2,048 characters (Chonkie).

**3. Overlap is usually small.** Bedrock defaults to 20 percent. OpenSearch defaults to 0 and recommends up to 0.2. Unstructured defaults to 0, and only for oversized splits. LangChain's 200 of 4,000 is 5 percent. LlamaIndex's 200 of 1,024 is about 20 percent. OpenAI's 400 of 800 is the outlier at 50 percent. The consensus is that overlap is a seam repair, not a retrieval strategy.

**4. Sentence boundaries are a preference, not a guarantee.** Every system that mentions sentence splitting also has a cruder fallback. Unstructured says it will text-split. LangChain recurses to the empty string. Bedrock's semantic chunker re-splits over-limit chunks. OpenSearch cuts at the token window. The production norm is a guaranteed ceiling with a best-effort boundary, not a boundary-only splitter.

**5. Tables are treated as an exception, and often a missing one.** Only Docling documents row-aware splitting with repeated headers. Unstructured isolates tables and text-splits oversized ones, which protects the provider but not the row. Bedrock, Azure Split, Google RAG Engine, OpenSearch, Elastic, LangChain, LlamaIndex, Chonkie, and OpenAI document no table-specific behavior at all. None of them promises a table stays whole. The closest to our defect is Unstructured, which at least refuses to emit a 186 KB table chunk, and Docling, which refuses to cut a row.

**6. Retrieval returns whole chunks, and parent substitution is how a chunk gets bigger than its ceiling.** Bedrock's hierarchical retrieval replaces a child with a parent of up to 8,192 tokens. LlamaIndex's AutoMergingRetriever does the same. LangChain's ParentDocumentRetriever replaces a child with the raw parent document if `parent_splitter` is unset. The first two cap the parent. The third does not. This is the single most direct route from a working chunker to a 756 KB prompt.

**7. No one documents a per-passage token cap at answer time.** Azure's `maxOutputSizeInTokens` is a total-output budget, not a per-passage cap, and it omits whole documents rather than truncating them. Azure On Your Data's chunk size is the de facto per-passage cap because it is set at ingestion. Everywhere else, the per-passage size is whatever the chunker wrote, and the answer layer trusts it.

## What they disagree about

**Units.** A "512" means 512 characters in Azure Split's default mode, 512 tokens in Bedrock, 512 words in Elastic's semantic text, and 512 of whatever tokenizer you passed in Chonkie. The same number produces wildly different prompts. Any cross-vendor comparison that ignores the unit is meaningless. Our 4 KB threshold is a character threshold, and 4 KB of a dense table is a very different token count from 4 KB of prose.

**The size itself.** The documented defaults range from 256 tokens (Docling) to 4,000 tokens (LangChain's token splitter). That is more than a factor of fifteen. Even within the token-aware group, 300 versus 1,024 is a factor of 3.4. There is no convergence, which supports the view that chunk size is a corpus property, not a universal constant.

**Hard versus soft enforcement.** Unstructured guarantees that no chunk exceeds `max_characters`. LangChain's recursive splitter also guarantees it, via the empty separator, but does so by cutting characters and never says so as an invariant. OpenSearch's `max_chunk_limit` can produce an oversized last chunk by design. Bedrock's `NONE` strategy produces an arbitrarily large chunk by design. LangChain's `parent_splitter=None` produces an arbitrarily large parent by default. The disagreement is not about the number. It is about whether the number is a promise.

**What to do when one element exceeds the cap.** The options observed are: split by structure and then by token (Docling), isolate and text-split (Unstructured), cut at the token window (OpenSearch, and Bedrock fixed-size by construction), recurse to characters (LangChain), overflow into the last chunk (OpenSearch's chunk count limit), truncate at the model limit (Elastic with `none`), or never cap (Bedrock `NONE`). Only Unstructured states the invariant. Only Docling states the row-level table rule.

**Table policy.** Docling splits tables by row and repeats headers. Unstructured isolates tables and splits them as text. Everyone else is silent. That silence is the gap that produced the 186 KB table chunk: no major vendor documents a rule that would have prevented it, so a chunker author has to invent one.

**Disclosure.** Azure's knowledge base warns in the activity array when a document is omitted for budget. Elastic documents that it automatically truncates when `strategy: none`. Azure On Your Data documents truncating the question and the system message. Bedrock, Google RAG Engine, LangChain, LlamaIndex, OpenSearch, and OpenAI document no answer-side disclosure. In most systems the first symptom of an over-budget prompt is the provider's error, which is exactly what happened here.

**Defaults versus examples.** LangChain's class default is 4,000 characters, while its recursive guide deliberately passes 100 to show the split. LlamaIndex's docs show 20 overlap, while the source says 200. Chonkie's docs show 128 overlap in a "default" example, while the table and source say 0. Anyone who copies the tutorial number instead of checking the constructor gets a different system than the one the docs describe. This is a documentation-quality problem, not a behavior difference, but it matters for a production default.

## The enforcement mechanisms worth copying

**1. Make the ceiling an invariant, not a target.** Unstructured's sentence is the model: "No chunk will exceed this number of characters" ([docs](https://docs.unstructured.io/open-source/core-functionality/chunking)). Our chunker should have the same sentence in its spec, and a test that asserts it over the real corpus. The test should fail the ingest, not log a warning. The 756 KB chunk and the 186 KB chunk both violate the invariant, so both become impossible by construction.

**2. Keep a soft target and a hard maximum.** Unstructured separates `new_after_n_chars` (preferred) from `max_characters` (hard). Docling separates its merge pass from its split pass. Our 384 to 512 target is already a soft range. The missing piece is a hard maximum at 512 tokens, enforced after every split, including the table path. A chunker that only targets 384 to 512 and never asserts it is a hope, not a limit.

**3. Split structurally first, then by token.** Docling's hybrid chunker is the reference: document hierarchy, then a token-aware split of oversized units, then a merge of undersized peers. Bedrock's page-boundary rule is a weaker version of the same idea. The practical rule for us is page, then heading, then table row, then sentence, then token, then character. Every step before the last one preserves meaning. The last one is only there to keep the invariant.

**4. Give tables their own rule.** Docling's is the one to copy: split tables at row boundaries, repeat the header in every chunk, keep rows intact, and allow the header to be omitted when a row would otherwise overflow (`repeat_table_header=True`, `omit_header_on_overflow=True`). Unstructured's isolation rule is the fallback: a table is never combined with prose, and an oversized table becomes table chunks rather than a monster chunk. Our current behavior, serializing the whole table into one chunk, is the one policy no production system documents.

**5. Cap the parent.** Bedrock caps hierarchical parents at 8,192 tokens and warns that parents are not embedded. LlamaIndex's `HierarchicalNodeParser` caps the largest level at whatever you configure. LangChain's parent retriever does not cap by default, and its docstring admits the parent is the raw document. If we add parent or window retrieval, the parent needs its own `max_tokens` and its own test, separate from the child ceiling.

**6. Budget the assembled prompt in tokens, not passages.** Azure's knowledge base is the only surveyed system with a request-level output budget: `maxOutputDocuments` plus `maxOutputSizeInTokens`, with whole documents omitted and a warning in the activity array. That is the right shape. A prompt builder should compute the token count of the final message, drop whole passages in a deterministic order until it fits, and record which passages were dropped. Truncating a passage mid-sentence is worse than dropping it, because a half-table is a wrong answer with a citation.

**7. Never let an overflow path create a bigger chunk.** OpenSearch's rule that "the excess text is added to the last chunk" is the anti-pattern to avoid. If a limit is reached, the correct behaviors are: split the remainder into another chunk (Bedrock semantic), drop the remainder with a recorded reason, or fail the ingest. Adding it to the last chunk defeats the ceiling and hides the failure in the one place nobody measures.

**8. Measure the distribution, not the average.** The corpus signal here is 81 of 341 documents with a chunk over 4 KB. A mean chunk size would have hidden that. The useful metrics are p50, p95, p99, max, and the count over the hard ceiling, per document and per element type. The ingest should emit a histogram and refuse to publish a collection whose max exceeds the ceiling. That is how 81 documents become a gate instead of a surprise.

**9. Disclose truncation in the response.** Azure's activity array is the only documented example of telling the caller that something was omitted. Even a log line is better than silence. If the answer path drops a passage or truncates a table, the response metadata should say so, and the evaluation harness should be able to assert it.

**10. Treat the provider limit as a second line of defense, not the first.** The systems that survive a bad chunker are the ones that also budget at answer time. The systems that fail are the ones that trust ingestion. Our defect happened because the chunker wrote a 756 KB chunk and the prompt builder passed it through. Fixing the chunker is necessary. A token-budget check before the model call is what makes the next chunker bug non-fatal.

### What this means for the 756 KB and 186 KB chunks

The 756 KB prose chunk is larger than every documented ceiling in this survey, including Bedrock's 8,192-token maximum and Docling's 256-token default. At four characters per token it is about 190,000 tokens, roughly 370 chunks at a 512-token ceiling. The 186 KB table is about 47,000 tokens, roughly 90 chunks at 512 tokens, and it is the harder case because a naive character split destroys rows. The production pattern that fits both is: a hard 512-token invariant at the chunker boundary, structure-aware splitting before the token fallback, and a table rule that splits by row and repeats the header. The 4 KB threshold in the corpus is a useful alarm, but it is not the ceiling. 4 KB is about 1,000 tokens, twice the target and still eight times a 500-character chunk. The ceiling should be the 512-token number the chunker already claims to target.

## Sources

All URLs fetched 2026-10-16. Dates in parentheses are the page's own `ms.date` or last-updated value where one exists.

**AWS Bedrock Knowledge Bases**
- How content chunking works for knowledge bases: https://docs.aws.amazon.com/bedrock/latest/userguide/kb-chunking.html (no date shown)
- Customize ingestion for a data source: https://docs.aws.amazon.com/bedrock/latest/userguide/kb-managed-customize-ingestion.html (no date shown)
- `FixedSizeChunkingConfiguration`: https://docs.aws.amazon.com/bedrock/latest/APIReference/API_agent_FixedSizeChunkingConfiguration.html (no date shown)
- `HierarchicalChunkingConfiguration`: https://docs.aws.amazon.com/bedrock/latest/APIReference/API_agent_HierarchicalChunkingConfiguration.html (no date shown)
- `SemanticChunkingConfiguration`: https://docs.aws.amazon.com/bedrock/latest/APIReference/API_agent_SemanticChunkingConfiguration.html (no date shown)
- `KnowledgeBaseVectorSearchConfiguration`: https://docs.aws.amazon.com/bedrock/latest/APIReference/API_agent-runtime_KnowledgeBaseVectorSearchConfiguration.html (no date shown)
- Hierarchical chunking recommendations: https://aws.amazon.com/blogs/machine-learning/amazon-bedrock-knowledge-bases-now-supports-advanced-parsing-chunking-and-query-reformulation-giving-greater-control-of-accuracy-in-rag-based-applications/ (no date shown)
- `numberOfResults` customization: https://aws.amazon.com/blogs/machine-learning/knowledge-bases-for-amazon-bedrock-now-supports-custom-prompts-for-the-retrieveandgenerate-api-and-configuration-of-the-maximum-number-of-retrieved-results/ (no date shown)

**Azure AI Search and Azure OpenAI**
- Text Split skill: https://learn.microsoft.com/en-us/azure/search/cognitive-search-skill-textsplit (2026-01-07)
- Document Intelligence Layout skill: https://learn.microsoft.com/en-us/azure/search/cognitive-search-skill-document-intelligence-layout (2026-08-08)
- Integrated vectorization: https://learn.microsoft.com/en-us/azure/search/vector-search-integrated-vectorization (2026-01-23)
- Azure OpenAI On Your Data: https://learn.microsoft.com/en-us/azure/ai-services/openai/concepts/use-your-data (2026-10-14)
- Agentic retrieval: https://learn.microsoft.com/en-us/azure/search/search-agentic-retrieval-how-to-retrieve (2026-09-04)

**Google**
- Use the layout parser in RAG Engine: https://docs.cloud.google.com/gemini-enterprise-agent-platform/build/rag-engine/layout-parser-integration (no date shown)
- RAG Engine API reference: https://docs.cloud.google.com/gemini-enterprise-agent-platform/reference/models/rag-api-v1 (no date shown)
- Use the LLM parser: https://docs.cloud.google.com/gemini-enterprise-agent-platform/build/rag-engine/llm-parser (no date shown)
- Document AI layout parser overview: https://docs.cloud.google.com/document-ai/docs/layout-parse-chunk (no date shown)
- Document AI RPC reference, `ChunkingConfig`: https://docs.cloud.google.com/document-ai/docs/reference/rpc/google.cloud.documentai.v1 (no date shown)
- Discovery Engine `LayoutBasedChunkingConfig`: https://docs.cloud.google.com/python/docs/reference/discoveryengine/latest/google.cloud.discoveryengine_v1.types.DocumentProcessingConfig.ChunkingConfig.LayoutBasedChunkingConfig (no date shown)

**LangChain**
- Splitting recursively: https://docs.langchain.com/oss/python/integrations/splitters/recursive_text_splitter (no date shown)
- `RecursiveCharacterTextSplitter` reference: https://reference.langchain.com/python/langchain-text-splitters/character/RecursiveCharacterTextSplitter (no date shown)
- `TextSplitter` and `TokenTextSplitter` defaults: https://github.com/langchain-ai/langchain/blob/master/libs/text-splitters/langchain_text_splitters/base.py (master, 2026-10-16)
- `RecursiveCharacterTextSplitter` separators: https://github.com/langchain-ai/langchain/blob/master/libs/text-splitters/langchain_text_splitters/character.py (master, 2026-10-16)
- `ParentDocumentRetriever`: https://github.com/langchain-ai/langchain/blob/master/libs/langchain/langchain_classic/retrievers/parent_document_retriever.py (master, 2026-10-16)
- `VectorStore.similarity_search(k=4)`: https://github.com/langchain-ai/langchain/blob/master/libs/core/langchain_core/vectorstores/base.py (master, 2026-10-16)

**LlamaIndex**
- Node parser modules: https://docs.llamaindex.ai/en/stable/module_guides/loading/node_parsers/modules/ (no date shown)
- `SentenceSplitter` and constants: https://github.com/run-llama/llama_index/blob/main/llama-index-core/llama_index/core/node_parser/text/sentence.py and https://github.com/run-llama/llama_index/blob/main/llama-index-core/llama_index/core/constants.py (main, 2026-10-16)
- `SentenceWindowNodeParser`: https://github.com/run-llama/llama_index/blob/main/llama-index-core/llama_index/core/node_parser/text/sentence_window.py (main, 2026-10-16)
- `SemanticSplitterNodeParser`: https://github.com/run-llama/llama_index/blob/main/llama-index-core/llama_index/core/node_parser/text/semantic_splitter.py (main, 2026-10-16)
- `VectorIndexRetriever`: https://github.com/run-llama/llama_index/blob/main/llama-index-core/llama_index/core/indices/vector_store/retrievers/retriever.py (main, 2026-10-16)

**OpenSearch and Elasticsearch**
- Text chunking processor: https://docs.opensearch.org/latest/ingest-pipelines/processors/text-chunking/ (latest, no date)
- Split processor: https://www.elastic.co/docs/reference/enrich-processor/split-processor (current, no date)
- `semantic_text` mapping: https://www.elastic.co/docs/reference/elasticsearch/mapping-reference/semantic-text (current, no date)
- `semantic_text` reference and chunking behavior: https://www.elastic.co/docs/reference/elasticsearch/mapping-reference/semantic-text-reference (current, no date)
- Inference API chunking settings: https://www.elastic.co/docs/api/doc/elasticsearch/operation/operation-inference-put-elasticsearch (current, no date)

**Vector stores**
- Qdrant search API: https://qdrant.tech/documentation/search/search/ (no date)
- Weaviate similarity search: https://docs.weaviate.io/weaviate/search/similarity (no date)
- pgvector: https://github.com/pgvector/pgvector (no date)
- pgai vectorizer API reference: https://github.com/timescale/pgai/blob/main/docs/vectorizer/api-reference.md (no date)

**Chunkers**
- Unstructured chunking: https://docs.unstructured.io/open-source/core-functionality/chunking (no date)
- Chonkie TokenChunker: https://docs.chonkie.ai/oss/chunkers/token-chunker and https://github.com/chonkie-inc/chonkie/blob/main/src/chonkie/chunker/token.py (no date)
- Docling chunking: https://docling-project.github.io/docling/concepts/chunking/ (no date)
- Docling `HybridChunker`: https://github.com/docling-project/docling-core/blob/main/docling_core/transforms/chunker/hybrid_chunker.py (main, 2026-10-16)
- Docling default tokenizer: https://github.com/docling-project/docling-core/blob/main/docling_core/transforms/chunker/tokenizer/huggingface.py (main, 2026-10-16)

**OpenAI**
- Retrieval guide: https://platform.openai.com/docs/guides/retrieval (no date)
- OpenAPI schema, `AutoChunkingStrategyRequestParam` and `StaticChunkingStrategyRequestParam`: https://github.com/openai/openai-openapi/blob/master/openapi.yaml (master, 2026-10-16)
