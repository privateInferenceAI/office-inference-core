# Oversized elements, tables, and repairing the index

The chunker has a target size and no enforced ceiling. A sentence with no boundary goes through whole at 756 KB. A serialised table goes through whole at 186 KB. Six retrieved passages then exceed the provider's input limit. The fix has two parts. The chunker needs a fallback ladder that ends in a hard cap and a table path that splits by rows before characters. The index needs a repair path that changes only the documents that need it.

Labels used below:

- [VERIFIED] read in a primary document or source file.
- [MEASURED] a number from a paper, benchmark, or disclosed method.
- [INFERENCE] my reasoning. It may be wrong.
- [UNVERIFIED] I could not confirm it.

Sources were retrieved on 2026-10-16 unless stated otherwise.

## What I could not verify

- The provider and the exact input limit. I did not make a request or read the provider configuration. The arithmetic below treats the limit as a variable.
- Whether the running TEI service uses `--auto-truncate`. The TEI README says truncation defaults to true [33]. If it is true, the 756 KB chunk's vector covers only a prefix, and the tail cannot be retrieved. I did not inspect the running flags. The interaction between the 8,192-token model limit and `--max-batch-tokens 4096` is also unmeasured.
- Whether Qdrant payloads store the full chunk text, a token count, and the page range in queryable fields. I did not read the schema. Detection and quarantine depend on it.
- The 81 of 341 count and the 4 KB threshold come from the brief. I did not re-measure them.
- Gomm and Hulsebos is a workshop paper. I read the authors' blog summary, not the full paper [14].
- The table-format ranking is one model, synthetic data, 1,000 field-lookup questions, and answering rather than retrieval. It is directional only [17].
- The Azure SplitSkill failure is a support thread, not a specification. The moderator's `textSplitMode: "lines"` suggestion contradicts the documented `pages` and `sentences` enum, so I treat the failure as observed behavior, not documented behavior [7][8].
- I did not benchmark a proposed split on this corpus. The recommendations are design choices, not measured wins.

## The fallback order production systems use

The systems I read share a shape: try structural boundaries first, then finer text boundaries, and stop at a hard cap. They differ in where the ladder ends and whether the cap is enforced.

| System | Order it tries | Enforced cap? | Overlap |
|---|---|---|---|
| LlamaIndex `SentenceSplitter` | paragraph, sentence, clause punctuation, space, character [1] | Yes. Raises `ValueError("Single token exceeded chunk size")` [1] | 200 tokens by default [1] |
| LangChain `RecursiveCharacterTextSplitter` | blank line, newline, space, character [2] | Not guaranteed. Appends a piece when no separator remains [2] | 200 characters by default [3] |
| Unstructured | whole elements to soft limit, then spaces or newlines, tables by rows [4] | Yes. "No chunk will exceed the max characters limit" [4] | Only on oversized-element splits by default [4][5] |
| Docling `HybridChunker` / `LineBasedTokenChunker` | document structure, then lines; a line is split only if it alone is too large [6] | Token-aware, but I did not read the line-splitting code path [6] | None documented for the hybrid chunker [6] |
| Azure AI Search Split skill | pages or sentences; "does its best" on sentence boundaries [7] | No documented character fallback [7] | `pageOverlapLength` [7] |
| Bedrock Knowledge Bases | fixed token size, or sentence-honoring default, or semantic maximum [9] | Fixed size has a token maximum. Pathological fallback not documented [9] | Percentage on fixed size; token count on hierarchical [9] |
| OpenAI vector stores | fixed token window only [10] | Yes. 100 to 4,096 tokens per chunk, overlap at most half [10] | 400 tokens by default, 800-token chunk [10] |

LlamaIndex is the clearest example because its source spells out the ladder. `SentenceSplitter` tries the paragraph separator, then the sentence tokenizer, then a secondary regex that splits on commas, periods, semicolons and CJK punctuation, then spaces, then characters. If a single indivisible unit is still larger than `chunk_size`, `_merge` raises `ValueError("Single token exceeded chunk size")`. That is a fail-closed chunker: it would rather fail the document than emit a chunk above the limit [1].

LangChain's `RecursiveCharacterTextSplitter` uses the separator list `["\n\n", "\n", " ", ""]` and recurses into the next separator when a piece is too large. The empty string is the character rung. If no separator remains, the code appends the piece, so the cap is a convention rather than a guaranteed invariant. The caller should still assert it [2][3].

Unstructured chunks whole elements up to a soft limit and a hard `max_characters`. Its docs say no chunk will exceed the hard limit. An element larger than the limit is split at spaces or new lines to avoid cutting words. Tables are always standalone and are chunked by rows when too large. Overlap applies to oversized-element splits by default. `overlap_all` extends it to normal chunks and the docs warn that this pollutes semantic boundaries [4][5].

Docling's `HybridChunker` starts from the document structure, splits only chunks that are oversized for the tokenizer, and merges undersized neighbours with the same headings. Its `LineBasedTokenChunker` keeps lines intact and splits a line only when the line alone exceeds the maximum. It also repeats table headers on continuation chunks [6].

Azure AI Search's Split skill has two modes, pages and sentences. It tries to break on sentence boundaries and the resulting chunk can be shorter than `maximumPageLength`. The docs do not describe a character fallback. A user report shows the consequence: a numeric spreadsheet with no sentence boundaries produced a 12,684-token chunk, which the embedding skill rejected at its 8,000-token limit [7][8].

Bedrock Knowledge Bases offer fixed-size token chunks with an overlap percentage, a default 300-token strategy that honors sentence boundaries, semantic chunking with a maximum token count that honors sentence boundaries, and hierarchical parent and child chunks. The docs describe the boundary preference but not a pathological fallback [9].

OpenAI vector stores are the other end of the spectrum: a fixed token window, default 800 tokens with 400 tokens of overlap, maximum 4,096, and overlap capped at half the chunk size. There is no semantic boundary in the documented strategy [10].

Put together, the order the systems converge on is: structural boundary, paragraph or line, sentence, clause punctuation, word, character or token window. No single system I read implements all of it. LlamaIndex implements the full ladder and fails closed. Unstructured implements the word and row rungs and a hard cap. Docling implements the line rung and table headers. LangChain implements the character rung. Copying one system is not enough.

Two details decide whether the ladder works. First, the cap must be a post-condition on the emitted chunk, measured after overlap and after any prepended context. If the chunker counts only element text and the contextualiser later prepends a title, heading path, page label and table caption, the emitted chunk can exceed the cap even when the splitter did its job [INFERENCE]. Second, the counter must be the embedding model's tokenizer. A character cap and a token cap diverge on tables, identifiers and non-English text. The target in this system is 384 to 512 tokens, so the counter is bge-m3's tokenizer, not `len(text)` [34].

Preferred break points: sentence before word, word before character, character before a raw token window. A token window is last because it splits words and can split a multi-byte character. For tables, row before cell, cell before character, and do not split a row that fits with a repeated header. If a single token still exceeds the cap, the document should fail to a dead-letter queue. That is LlamaIndex's behavior and it is the right default [1][INFERENCE].

Overlap is where the systems disagree. LlamaIndex defaults to 200 tokens [1]. LangChain defaults to 200 characters [3]. OpenAI defaults to 400 tokens, half the chunk [10]. Unstructured applies overlap only to oversized-element splits unless `overlap_all` is set, and warns that `overlap_all` pollutes clean boundaries [4]. NVIDIA found 15 percent overlap best on FinanceBench at 1,024-token chunks, among 10, 15 and 20 percent. That is a vendor-run result on one dataset [12]. A 1,820-configuration study found low overlap generally favored where overlap was varied [11]. For this index, overlap at structural boundaries buys little and costs tokens. Keep zero there. If a forced break cuts a sentence or a word, 16 to 32 tokens of overlap is enough to preserve the join, and it must be counted inside the cap. Use zero at table row boundaries [INFERENCE].

The size arithmetic is worth stating plainly. A 756 KB ASCII passage is roughly 190,000 tokens at four characters per token, more than 20 times bge-m3's 8,192-token maximum. A 186 KB table is roughly 47,000 tokens. Six passages of that size are far beyond any provider input limit. Even the 4 KB chunks are about 1,000 tokens each, so six of them are about 6,000 tokens before the prompt. That may fit a large context, but it is not the design target and it leaves no margin for a bigger table [INFERENCE arithmetic].

| Item | Size | Approximate tokens | Against bge-m3's 8,192 limit |
|---|---|---|---|
| The 756 KB sentence | 756 KB | 190,000 | 23 times |
| The 186 KB table | 186 KB | 47,000 | 5.8 times |
| A 4 KB chunk | 4 KB | 1,000 | One eighth |
| The 512-token target | about 2 KB | 512 | One sixteenth |

## Tables: finding them versus answering from them

The table measurements separate two jobs. Finding a table is a retrieval problem: can the embedding for a chunk match a question about the table? Answering from a table is a generation problem: does the model see the cells and headers it needs to compute the answer? A serialisation that is good at one is not automatically good at the other.

### What the measurements say

Gomm and Hulsebos tested table serialisations on the TARGET benchmark using FeTaQA. Including the table's page title and section title in the serialisation raised average recall@3 by 0.42 absolute. Row count mattered: most embedding models showed little improvement beyond about 10 rows, while one long-context model kept improving and a weaker model degraded. The authors' conclusion is that there is no single best serialisation and the choice has to be measured per embedding model [14][15]. This is the strongest measured argument for putting the caption and heading path into the table chunk. It is not an argument for putting the whole table into every chunk.

The Unstructured paper preserved every table as a whole. On FinanceBench, its element-based chunks scored 67.38 page accuracy against 73.05 for plain 256-token chunks, but 53.19 manual answer accuracy against 36.88. Whole-table element chunks retrieved the right page less often and answered more often. Aggregating several chunking strategies produced the best retrieval score, 84.40 page accuracy, and then exceeded GPT-4's token limit, so the best retrieval configuration was not usable for generation [13]. That is the same failure as ours, at a smaller scale.

NVIDIA preserved tables and charts as complete units and reported 0.648 average end-to-end accuracy for page-level chunking, the best average in its test. The practice assumes the table fits. Without a size guard it is the defect [12].

TableRAG starts from the other end. It argues that passing the entire table to a language model runs into positional bias and context length limits, and it retrieves the schema and the relevant cells instead. It reports state-of-the-art results on two million-token table benchmarks [16]. For a table that does not fit, retrieve the schema and the cells, not the whole grid.

The format benchmark is often quoted as "markdown beats CSV". The actual test used 1,000 synthetic employee records, 1,000 field-lookup questions, one model (GPT-4.1-nano), and no header repetition. Markdown-KV scored 60.7 percent, markdown table 51.9, CSV 44.3, with confidence intervals of about three points. It measured answering from a table already in context, not retrieval, and the authors say repeated headers would likely help the header-based formats [17]. The top format, row-wise `key: value`, is also the most self-contained shape for a row chunk. That is suggestive, not proof for bge-m3.

### Which representation to use

| Representation | Strength | Weakness | Where it fits |
|---|---|---|---|
| Whole table, serialised once | Preserves row and column relationships. Best when the question is about the table as a whole. | One large vector averages many rows. A large table blows the cap and the embedding window. | Only when the serialised table fits the cap. |
| Row groups with repeated headers | Self-contained rows. Header meaning survives the split. Citation can name a row range. | Repeating headers costs tokens, and a wide header can crowd out the body. | The default for tables that do not fit. |
| Markdown table | Readable. Header row is visible. | A chunk taken mid-table loses the header unless it is repeated. | Human display and small tables. |
| CSV | Compact. Cheap in tokens. | Header meaning is lost when a row is isolated. Near the bottom of the format test. | Export and tooling, not embedding. |
| Row-wise `Column: value` | Each row stands alone. Header names travel with the values. | High token cost. Repetitive. | Embedding a table that must be split by rows. |

My recommendation is to keep the structured table as the canonical record, embed a row-aware serialisation with the caption and heading path, and use row groups with repeated headers when the table does not fit [INFERENCE]. The measured parts are the metadata gain, the header expectation, and the failure of whole-table aggregation. The exact serialisation for bge-m3 has not been measured here.

### The table path I would build

1. Keep the structured table. Docling's `TableData` carries cell text, row and column indices, spans, header flags, and per-cell provenance. The prior chunking report verified this against the source [35]. Unstructured's `text_as_html` is the fallback when only HTML exists [5].
2. If the serialised table fits the cap, emit it whole with its caption and heading path. This follows the Unstructured and NVIDIA practice and preserves row relationships [12][13].
3. If it does not fit, split by rows into row groups. Never split by characters mid-row. Repeat the header rows on every group. Docling exposes `repeat_table_header` (default true) and `omit_header_on_overflow`. Unstructured's `TableChunk` carries `table_id`, `chunk_index`, `is_continuation`, and `num_carried_over_header_rows` so the original table can be reconstructed [5][6].
4. If a single row or cell exceeds the cap, split that cell's text at word or character boundaries, mark the chunk as a partial row, and repeat the row's key column. Docling's `omit_header_on_overflow` drops the header for a row that would otherwise overflow. Unstructured disables header repetition when the header itself would consume too much of the window [5][6]. Both make the trade-off explicit: dropping the header for one row is better than dropping the row.
5. Do not generate a table summary with an LLM by default. In the Unstructured experiment, the GPT-4 summary variant scored worse than the free prefix plus caption on page accuracy and answer accuracy [13]. The deterministic caption and heading path is the measured win [14].
6. Serialise each row as `Column: value`, not as a bare CSV line. The header-repetition evidence and the format benchmark's limitation point the same way, but this is an inference for bge-m3, not a measured result [17][INFERENCE].

### Keeping a citation to a row meaningful

A table citation needs the table and the row range. The page is needed for display. Store `table_id`, `row_start`, `row_end`, `header_repeated`, `caption`, and `page_range` on every table chunk. Every split also inherits the parent's access label. A row group inherits the table's ACL and page range, and a cell split inherits the row's [INFERENCE]. The verifier can then check that the cited value appears in the visible row group, not just somewhere in the table [INFERENCE].

If a row group repeats headers, the repeated header is synthetic. Mark it `header_repeated=true` so the verifier and the UI do not treat it as source text. Unstructured carries `num_carried_over_header_rows` for exactly this purpose [5]. If a table spans pages, cite the range, not a single page. Docling's provenance gives per-cell page and bounding box [35], and the existing architecture already says a page range, never a single page.

## Citation integrity when text is capped

A citation is honest when it points at text the model actually received. That is a data-model requirement before it is a prompt requirement.

The strongest version of this is Anthropic's citation API. A plain-text document returns `cited_text` plus a 0-indexed character range with an exclusive end. A PDF returns a 1-indexed page range with an exclusive end. A custom content document returns block indices. The API guarantees that citations contain valid pointers to the provided documents [18]. Its search-result blocks go further for RAG: each result carries a `source`, a `title`, and an array of content blocks, and the citation returns `search_result_index` plus block indices. The docs recommend breaking long content into logical text blocks "to give Claude finer citation boundaries" [19]. If the content block is the truncated excerpt, the citation cannot point outside it. That is the property to copy.

The practical design is:

1. Make the visible passage the citable unit. If the prompt contains a 400-token excerpt, the citation should resolve inside that excerpt, not to the 186 KB stored chunk [19][INFERENCE].
2. Store character offsets within the document, not only the page range. Azure's Split skill already emits offsets in UTF-8, UTF-16 and code points, plus lengths and ordinal positions [7]. With offsets, a truncated view maps back to the source exactly, and the UI can expand the full passage at display time [INFERENCE].
3. Retrieve small, answer from the parent only when it fits. LangChain's `ParentDocumentRetriever` embeds child chunks and returns the parent document or a larger parent chunk [20]. LlamaIndex's `AutoMergingRetriever` merges children into the parent only when more than half of the parent's children are retrieved, which bounds the expansion [21]. Both are small-to-big retrieval. Neither removes the need for a size check on the parent.
4. If the parent does not fit, expand at display time, not in the prompt. Send the child, cite the child's span, and show the parent with the span highlighted. The model's context stays bounded and the citation stays exact [INFERENCE].
5. Disclose truncation in the payload and in the prompt. Add `visible_chars`, `source_chars`, `truncated`, and `truncation_reason`. Tell the model "excerpt, first N of M characters". The verifier checks citations against the visible span, and a quote outside it is unsupported [INFERENCE]. I found no production document that standardises this disclosure. The citation APIs avoid the problem by making the visible block the unit, which is a better answer than a disclosure convention [UNVERIFIED].
6. Verify mechanically. ALCE measures citation quality and found that even the best models lacked complete citation support 50 percent of the time on the ELI5 dataset [22]. That is the argument for a verifier and for treating a citation as a pointer to check, not a claim to trust. The check is mechanical. The quoted text must be a substring of the visible passage after whitespace normalisation. The passage ID must be in the retrieved set. The page or row range must match [INFERENCE]. The existing verifier's literal-substring check is the first part. The new requirement is that it runs against the visible passage, not the stored chunk.
7. Do not let the model cite a chunk it did not see. Resolve source IDs server-side from the visible set and reject citations whose ID is outside it. Anthropic's `document_index` and `search_result_index` are server-checkable pointers, not free text [18][19].
8. Long context is not a substitute for honest citations. Lost in the Middle found that accuracy is highest when relevant information sits at the beginning or end of a long context and degrades when it sits in the middle, even for long-context models [23]. Sending one 186 KB table because the model accepts 200,000 tokens is not a repair.

The truncation policy follows from this. Prefer splitting before retrieval over truncating after retrieval. If the chunker enforces the cap, the retrieval path never needs to truncate. Truncation is a last resort for a passage that cannot be split, and it must be visible to the model and to the verifier [INFERENCE].

## Re-chunking and re-embedding without a full rebuild

The hard part is not the new chunker. It is making the old index notice that the chunker changed. The current ingestion is idempotent by file hash, so a chunker fix will no-op unless the manifest key changes. Eighty-one of 341 documents carry at least one chunk over 4 KB, so the repair is a minority of the corpus. That is the argument for a targeted re-chunk rather than a full rebuild.

### How real systems version a chunker

| System | Version marker | What happens on a change | What it does not solve |
|---|---|---|---|
| Qdrant | collection metadata `embedding_model` and `pipeline_version` [25] | The application's gate refuses to run on a mismatch and requires a full re-embed into a fresh collection [25] | It re-embeds everything, including unchanged text |
| LlamaIndex ingestion | docstore maps `doc_id` to a document hash [30] | Unchanged hash means skip; changed hash means reprocess and upsert [30] | The document hash does not include the chunker configuration |
| LangChain indexing | RecordManager hashes documents; cleanup modes `incremental`, `full`, `scoped_full`; `force_update` for updated embeddings [31] | Upserts changed documents and deletes stale ones [31] | Same gap: a transformation change alone is invisible |
| Bedrock Knowledge Bases | chunking configuration is immutable [29] | Recreate the data source [29] | No partial path at all |
| Azure AI Search | reset indexer, reset documents, reset skills, resync [28] | Reprocess all, a list of keys, or one skill and its downstream skills [28] | Reset is passive, needs a run, and does not delete orphans [28] |
| Vespa | reindexing over stored documents [27] | Visits all documents of a type and re-feeds them through the indexing chain; only document fields are re-fed and derived fields are recomputed [27] | It repairs derived fields, not a changed source parse |

The common pattern is a pipeline version plus a content hash. The pipeline version decides whether to reprocess. The content hash decides whether to re-embed. Qdrant's incremental tutorial is the clearest statement: unchanged chunks stay untouched, changed text is re-embedded, a vector is reused when text only moves, and text that disappears is deleted. The same tutorial warns that over-window input is silently truncated [25]. That is the failure we are repairing.

The last column for LlamaIndex and LangChain is my reading of their hash inputs. Neither doc says what happens when the transformation changes but the document does not. I treat that as a gap to close in our own code, not as a documented behavior [INFERENCE].

### The repair sequence for this index

The repair runs on the box. The tokenizer, TEI, Qdrant, and Postgres are all local. No external embedding API or managed reindex is involved, which keeps the work inside the no-egress constraint.

1. Quarantine. Add `oversized=true` to every chunk above the generation budget and add a `must_not` filter on that field to the retrieval query. This is a payload update and needs no embedding. It stops the request failure before the re-chunk starts. A document whose chunks are all quarantined disappears from retrieval until it is repaired, which is better than returning a request error [INFERENCE].
2. Measure with the right counter. Count bge-m3 tokens, not characters. The budget is `min(embedding_limit, provider_input_limit - prompt_tokens - output_tokens) / retrieval_depth`, with the contextualisation text and overlap inside it. With six passages and a 512-token ceiling the passages are 3,072 tokens. With six 186 KB tables they are roughly 280,000 tokens. The second number is the defect [INFERENCE arithmetic].
3. Re-chunk from the parse tree, not from the stored chunk text. Stored chunks have overlap and table chunks have lost cell structure. Re-parse the 81 documents from the original files, which are already keyed by hash, then apply the new ladder [INFERENCE].
4. Keep unaffected chunks. If the new code only changes the pathological path, normal chunks should be byte-identical and keep their UUIDs. Re-embed only text whose hash changed. LangChain's `CacheBackedEmbeddings` hashes the text and uses that hash as the cache key [32]. LlamaIndex hashes the node and the transformation [30]. Use `sha256(text) + model + revision` as the key, not the document hash, so two identical chunks in different documents share one vector [INFERENCE applied].
5. Write new points with `chunker_version`, `parser_version`, `embedding_model`, `text_sha256`, and `is_oversized` in the payload. Keep the old points until the new ones pass. Delete old points by `(doc_id, chunker_version < new)` after verification [INFERENCE].
6. Backfill in token-budgeted batches. `--max-batch-tokens 4096` means batch by tokens, not documents. Keep each request well below the limit so a single long chunk cannot force truncation. The same limit is a second reason to keep chunks near 512 tokens: up to eight 512-token chunks fit a 4,096-token batch before padding [33][INFERENCE].
7. Verify before cutover. Check the maximum token count per chunk, zero chunks over the cap, no empty chunks, a page range on every chunk, the access label inherited, table rows reconstructable, the golden questions unchanged, and the citation verifier passing [INFERENCE].
8. Cut over and roll back. For 81 documents, same-collection versioned points plus a filter is enough. For an atomic cutover, build a second collection and flip a Qdrant alias. Aliases switch atomically and concurrent requests are not affected [26]. The migration tutorial's dual writes and background scroll are for a full model change, which is more machinery than a chunker-only repair needs [24].
9. Change the manifest key. Use `(file_hash, parser_version, chunker_version, embedding_model, embedding_revision, context_template_version)`. Without this, the next ingestion run will say no document changes and the repair will not happen. The current idempotency is correct for file edits and wrong for pipeline edits [INFERENCE, from the described design and the LlamaIndex and LangChain hash behavior in 30 and 31].

### Avoiding a second full embedding

A chunker-only change does not need a second collection. A second collection with full re-embedding is for an embedding model change. Qdrant's migration tutorial uses blue-green collections or named vectors for that case, not for a chunker change [24][25].

Reuse is real only when the text is identical. If the fix changes every boundary, cache hits will be low, and a full re-embed may be cheaper than reasoning about reuse. Measure the hash-hit rate on a sample before choosing [INFERENCE].

At 341 documents, storage is not the constraint. CPU embedding time is. The repository's own figure of 28 hours for 50,000 pages is arithmetic, not a measurement, so measure the 81-document batch before scheduling it [INFERENCE, from `docs/search-architecture.md`].

Do not re-embed the quarantined text. The old vectors for oversized chunks are worthless if TEI truncated them. Delete them after the replacement is verified [INFERENCE].

### Documents whose existing chunks exceed the limit

If the source file is available, re-parse and re-chunk. That is the only path that preserves table structure and page provenance.

If the source is missing, split the stored text as an emergency measure, mark `derived_from_chunk_id` and `is_emergency_split=true`, and keep the page range. Do not present an emergency split as a normal chunk [INFERENCE].

If the document cannot be split under the cap even at character level, dead-letter it with the failure reason. LlamaIndex's fail-closed behavior is the model: a `ValueError` is better than a silent 756 KB chunk [1][INFERENCE].

## What applies here, and what would not

| Applies | Why |
|---|---|
| A hard cap as a post-condition on every emitted chunk, checked after context and overlap | The current target is not enforced. This is the defect. |
| The full fallback ladder, ending in a character window and failing closed | A sentence-only splitter is one rung, not a fallback order. Numeric tables and OCR text have no sentence boundaries [8]. |
| A table guard: whole if it fits, row groups with repeated headers if it does not, partial row only for a pathological cell | The measured table practice assumes tables fit. The 186 KB table is the counterexample [12][13]. |
| Table citation metadata: `table_id`, row range, header flag, page range, caption | A row citation is not meaningful without the table and the row [5][6][35]. |
| Visible-passage citation, with the verifier checking the visible span | A citation that points outside what the model saw is not honest [18][19][22]. |
| Quarantine, targeted re-chunk, content-hash cache, versioned points, manifest key | The 81 documents can be repaired without re-embedding the other 260 [INFERENCE]. |
| A Qdrant alias if the cutover must be atomic | Aliases switch atomically and do not disturb concurrent requests [26]. |

| Does not apply | Why |
|---|---|
| Bedrock's recreate-the-data-source path | It is the vendor's only option for an immutable chunker [29]. It is too blunt for 81 documents on CPU. |
| OpenAI's 50 percent overlap default | It doubles embedded text and the low-overlap evidence does not support it [10][11]. |
| NVIDIA's preserve-whole-table rule without a size guard | It is the same defect at a larger scale [12]. |
| Azure's sentence-only split | It is the exact failure mode with numeric tables [8]. |
| A full blue-green re-embed for a chunker-only change | Reserve it for a model change [24]. |
| LLM-generated table summaries as the default | The measured summary variant lost to the free caption and prefix [13]. |
| A second vector per table without measurement | The deterministic metadata gain is already large and the extra embedding cost is real on CPU [14][INFERENCE]. |
| Treating a long context as the fix | Lost in the Middle and the provider limit both argue against it [23]. |

### Recommended fallback order

1. Page or section boundary.
2. Paragraph or line boundary.
3. Sentence boundary, only when it actually splits the text.
4. Clause boundary at comma, semicolon, colon, or the CJK equivalent.
5. Word boundary at whitespace.
6. Character window, sized in bge-m3 tokens and decoded to valid UTF-8.
7. Dead-letter the element if a single token still exceeds the cap.

### Table order

1. Whole table with caption and heading path if it fits.
2. Row groups with repeated headers.
3. Single row, dropping the repeated header only if the header makes the row overflow.
4. Cell text at a word or character boundary, with the key column repeated.
5. Dead-letter if a single cell cannot fit.

### Overlap

Zero at structural boundaries and table rows. Sixteen to 32 tokens at forced prose breaks, counted inside the cap.

### Post-condition

Every emitted chunk, after contextualisation and overlap, is at or below 512 bge-m3 tokens. If it is not, the document goes to the dead-letter queue. The check belongs in code, not in a configuration comment.

## Sources


1. [LlamaIndex, `SentenceSplitter` source, `llama-index-core/llama_index/core/node_parser/text/sentence.py`](https://raw.githubusercontent.com/run-llama/llama_index/main/llama-index-core/llama_index/core/node_parser/text/sentence.py) (retrieved 2026-10-16). [VERIFIED]
2. [LangChain, `RecursiveCharacterTextSplitter` source, `libs/text-splitters/langchain_text_splitters/character.py`](https://raw.githubusercontent.com/langchain-ai/langchain/master/libs/text-splitters/langchain_text_splitters/character.py) (retrieved 2026-10-16). [VERIFIED]
3. [LangChain, `TextSplitter` base source, `libs/text-splitters/langchain_text_splitters/base.py`](https://raw.githubusercontent.com/langchain-ai/langchain/master/libs/text-splitters/langchain_text_splitters/base.py) (retrieved 2026-10-16). [VERIFIED]
4. [Unstructured, "Unstructured chunking"](https://docs.unstructured.io/concepts/chunking) (retrieved 2026-10-16). [VERIFIED]
5. [Unstructured, chunking base source, `unstructured/chunking/base.py`](https://raw.githubusercontent.com/Unstructured-IO/unstructured/main/unstructured/chunking/base.py) (retrieved 2026-10-16). [VERIFIED]
6. [Docling, "Chunking"](https://docling-project.github.io/docling/concepts/chunking/) (retrieved 2026-10-16). [VERIFIED]
7. [Microsoft Learn, "Text Split skill"](https://learn.microsoft.com/en-us/azure/search/cognitive-search-skill-textsplit) (retrieved 2026-10-16). [VERIFIED]
8. [Microsoft Q&A, "Azure AI Search Split Skill not splitting chunks accordingly"](https://learn.microsoft.com/en-gb/answers/questions/2147329/azure-ai-search-split-skill-not-splitting-chunks-a) (retrieved 2026-10-16). [VERIFIED as a user report, not a specification]
9. [Amazon Web Services, "How content chunking works for knowledge bases"](https://docs.aws.amazon.com/bedrock/latest/userguide/kb-chunking.html) (retrieved 2026-10-16). [VERIFIED]
10. [OpenAI, "Create vector store" API reference](https://developers.openai.com/api/reference/resources/vector_stores/methods/create) (retrieved 2026-10-16). [VERIFIED]
11. ["Chunk Twice, Embed Once" (arXiv 2506.17277)](https://arxiv.org/abs/2506.17277) (retrieved 2026-10-16). [MEASURED, chemistry corpus, 1,820 configurations]
12. [NVIDIA, "Finding the Best Chunking Strategy for Accurate AI Responses", 18 June 2025](https://developer.nvidia.com/blog/finding-the-best-chunking-strategy-for-accurate-ai-responses/) (retrieved 2026-10-16). [VENDOR-RUN MEASUREMENT]
13. [Jimeno Yepes et al., "Financial Report Chunking for Effective Retrieval Augmented Generation" (arXiv 2402.05131)](https://arxiv.org/abs/2402.05131) (retrieved 2026-10-16). [MEASURED, 141 questions]
14. [Daniel Gomm, "Table Serialization Kitchen", 4 March 2025](https://www.daniel-gomm.com/blog/2025/Table-Serialization-Kitchen/) (retrieved 2026-10-16). [MEASURED workshop work, read as the authors' summary]
15. [Gomm and Hulsebos, "Metadata Matters in Dense Table Retrieval", OpenReview](https://openreview.net/forum?id=rELWIvq2Qy) (retrieved 2026-10-16). [Reference for source 14; abstract body not re-read]
16. [Chen et al., "TableRAG: Million-Token Table Understanding with Language Models" (arXiv 2410.04739)](https://arxiv.org/abs/2410.04739) (retrieved 2026-10-16). [MEASURED, NeurIPS 2024]
17. [Improving Agents, "Which Table Format Do LLMs Understand Best?", 30 September 2025](https://www.improvingagents.com/blog/best-input-data-format-for-llms/) (retrieved 2026-10-16). [MEASURED but narrow: one model, synthetic data, answering only]
18. [Anthropic, "Citations"](https://platform.claude.com/docs/en/build-with-claude/citations) (retrieved 2026-10-16). [VERIFIED]
19. [Anthropic, "Search results"](https://platform.claude.com/docs/en/build-with-claude/search-results) (retrieved 2026-10-16). [VERIFIED]
20. [LangChain, `ParentDocumentRetriever` source, `langchain_classic/retrievers/parent_document_retriever.py`](https://raw.githubusercontent.com/langchain-ai/langchain/master/libs/langchain/langchain_classic/retrievers/parent_document_retriever.py) (retrieved 2026-10-16). [VERIFIED]
21. [LlamaIndex, `AutoMergingRetriever` source, `llama-index-core/llama_index/core/retrievers/auto_merging_retriever.py`](https://raw.githubusercontent.com/run-llama/llama_index/main/llama-index-core/llama_index/core/retrievers/auto_merging_retriever.py) (retrieved 2026-10-16). [VERIFIED]
22. [Gao et al., "Enabling Large Language Models to Generate Text with Citations" (arXiv 2305.14627)](https://arxiv.org/abs/2305.14627) (retrieved 2026-10-16). [MEASURED, ALCE benchmark]
23. [Liu et al., "Lost in the Middle: How Language Models Use Long Contexts" (arXiv 2307.03172)](https://arxiv.org/abs/2307.03172) (retrieved 2026-10-16). [MEASURED, TACL]
24. [Qdrant, "Migrate to a New Embedding Model with Zero Downtime"](https://qdrant.tech/documentation/tutorials-operations/embedding-model-migration/) (retrieved 2026-10-16). [VERIFIED]
25. [Qdrant, "Incremental Embedding Updates"](https://qdrant.tech/documentation/tutorials-operations/incremental-embedding-updates/) (retrieved 2026-10-16). [VERIFIED]
26. [Qdrant, "Collections" (aliases and named vectors)](https://qdrant.tech/documentation/manage-data/collections/) (retrieved 2026-10-16). [VERIFIED]
27. [Vespa, "Reindexing"](https://docs.vespa.ai/en/operations/reindexing.html) (retrieved 2026-10-16). [VERIFIED]
28. [Microsoft Learn, "Run or reset indexers, skills, or documents"](https://learn.microsoft.com/en-us/azure/search/search-howto-run-reset-indexers) (retrieved 2026-10-16). [VERIFIED]
29. [Amazon Web Services, "Modify a data source for your Amazon Bedrock knowledge base"](https://docs.aws.amazon.com/bedrock/latest/userguide/kb-ds-update.html) (retrieved 2026-10-16). [VERIFIED]
30. [LlamaIndex, "Ingestion Pipeline"](https://developers.llamaindex.ai/python/framework/module_guides/loading/ingestion_pipeline/) (retrieved 2026-10-16). [VERIFIED]
31. [LangChain, indexing API source, `libs/core/langchain_core/indexing/api.py`](https://raw.githubusercontent.com/langchain-ai/langchain/master/libs/core/langchain_core/indexing/api.py) (retrieved 2026-10-16). [VERIFIED]
32. [LangChain, `CacheBackedEmbeddings` source, `langchain_classic/embeddings/cache.py`](https://raw.githubusercontent.com/langchain-ai/langchain/master/libs/langchain/langchain_classic/embeddings/cache.py) (retrieved 2026-10-16). [VERIFIED]
33. [Hugging Face, Text Embeddings Inference README](https://raw.githubusercontent.com/huggingface/text-embeddings-inference/main/README.md) (retrieved 2026-10-16). The local copy at `docs/research/search-2026-10/tei_readme.md` records `--auto-truncate` defaulting to true and `--max-batch-tokens`. [VERIFIED]
34. [BAAI, `bge-m3` model card](https://huggingface.co/BAAI/bge-m3) (retrieved 2026-10-16). [VERIFIED, 8,192-token maximum]
35. [Docling, table data source, `docling_core/types/doc/items/table/table_data.py`](https://raw.githubusercontent.com/docling-project/docling-core/main/docling_core/types/doc/items/table/table_data.py) (verified in the prior chunking report; not re-read on 2026-10-16).