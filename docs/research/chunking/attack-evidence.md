# Attack: the evidence

Verdict: yes with fixes. The recommendation is directionally right. The code defects it names are real, and the order it proposes is defensible. The brief's confidence is not: its corpus counts are an unverified relay, its migration premise is a choice presented as a necessity, and it dismisses the one off-the-shelf chunker that already implements the table and ceiling policy it recommends. Four findings would change what the team does. The rest are corrections, and a few of them are serious.

I read the six reports, the brief, and the named code. I ran no code and changed nothing. Where a report cites a paper, I checked the paper's table or page when it was reachable.

## Attacks that would change the decision

### A1. The corpus counts are a relay, not a measurement, and "a quarter of the corpus" is a document count, not a chunk count

The brief says:

> 81 of 341 documents carry at least one chunk over 4 KB, so this is a quarter of the corpus, not an edge case.

And:

> All 341 documents and roughly 21,000 to 25,000 chunks, not the 81.

The reports do not support this as measured. `chunking-strategy-evidence.md:14` lists the defect numbers as "taken as given, not independently reproduced." `oversized-elements-and-repair.md:19` says "The 81 of 341 count and the 4 KB threshold come from the brief. I did not re-measure them." `our-architecture-options.md:618-619` says the 21,188 chunks, 716-character mean, 756,182-character maximum and 171 chunks over 4 KB "are the brief's figures, not mine." The same report gives the number the brief leaves out: 171 chunks over 4 KB. Against 21,188 chunks, that is 0.8 percent of chunks, not a quarter. The 24 percent is the share of documents that contain at least one of those 171 chunks.

The 4 KB threshold is also not the 512-token ceiling. At the brief's own measured ratios, 4 KB is about 800 Nova tokens of prose, about 2,500 tokens of pipe table, and about 3,000 tokens of CSV. A chunk over 4 KB is certainly over the ceiling. A chunk between 512 and 4 KB tokens may also be over and is not counted. So 171 is a lower bound at a high threshold, and the true over-ceiling count is unknown. The brief's "a quarter of the corpus" reads as a quarter of the chunks. It is not.

Why this changes the decision: the answer-path cap is still justified by the observed 439 KB prompt and the provider rejection, which do not depend on the counts. The migration scope does. A team that runs the index query may find 171 oversized chunks concentrated in 81 documents and choose the targeted repair, or may find many more over-ceiling chunks at a lower threshold and justify the full re-chunk. The brief's migration set actually depends on the version policy in A2, not on these counts. The counts are being used to establish urgency, and they do not establish the chunk-level damage.

What settles it: the six SQL queries in `our-architecture-options.md:623-633`, especially `select count(*) from chunk_index where length(text) > 4096` and `select count(distinct doc_id) from chunk_index`. Until those run on the deployment, the counts are a hypothesis. The report says as much: "the first thing to run before scheduling a migration."

### A2. The all-341 and no-blue/green migration is a consequence of a global version bump, not a necessity, and one report recommends the opposite

The brief says:

> All 341 documents and roughly 21,000 to 25,000 chunks, not the 81. The version string is inside every chunk id, so every id moves either way.

And:

> No blue/green path. One vector collection, and `chunk_index` has no collection column, so a second collection would be fused by the lexical arm; an interrupted migration is invisible afterwards.

`oversized-elements-and-repair.md` recommends the opposite in four places. Line 134: "Eighty-one of 341 documents carry at least one chunk over 4 KB, so the repair is a minority of the corpus. That is the argument for a targeted re-chunk rather than a full rebuild." Line 158: "Keep unaffected chunks. If the new code only changes the pathological path, normal chunks should be byte-identical and keep their UUIDs. Re-embed only text whose hash changed." Line 162: "For 81 documents, same-collection versioned points plus a filter is enough. For an atomic cutover, build a second collection and flip a Qdrant alias. Aliases switch atomically and concurrent requests are not affected." Line 163: "Change the manifest key. Use `(file_hash, parser_version, chunker_version, embedding_model, embedding_revision, context_template_version)`."

`our-architecture-options.md:231-238` argues the other way: the version is in every id, the payload bump marks every document stale, and there is no per-document chunker version to select on. Both reports are describing the same code. The disagreement is about policy, not fact.

The code makes the choice sharper than the brief admits. `chunk_index` has a `chunker_version` column (`db/migrations/010_chunk_index.sql:34`) even though it has no collection column. The lexical query filters only ACL (`services/retrieval/retriever.py:341-371`). A `chunker_version` predicate plus a Qdrant alias would make a vector and lexical cutover possible with a small query change. The real blocker is the collection-wide contract (`services/ingestion/contract.py:45,121`), which records one `chunker_version` for the whole collection. A mixed corpus either makes the recorded contract false or is invisible on the read path. The brief is right that this is bad. It is not right that there is "no blue/green path" in any absolute sense.

Why this changes the decision: the brief's risk section is built on a 2 to 4.5 hour single-writer window with no rollback and a mixed-version corpus. If a per-document version and a lexical filter are acceptable, the 81-document repair is viable, and the chunker fix can land sooner with less exposure. If the collection-wide contract is non-negotiable, the global bump is the honest choice, but it should be labeled a choice. The brief presents the consequence as forced.

What settles it: decide whether the contract may be per-document or per-generation, and whether `chunker_version` may enter the lexical query. That is an architecture decision, not a measurement. The brief should surface it rather than bury it behind "all 341, not the 81."

### A3. The off-the-shelf chunker was dismissed on a false claim, and the library that does the recommended fix was never tested

The brief says:

> An off-the-shelf chunker. Ten lines of guard at the cost of the citation and payload contract, and Docling's chunker was never in the comparison harness.

The second clause is false. `tests/fixtures/chunker_comparison.py` exists and imports `PageChunker` and `HierarchicalChunker` from Docling. It does not import `HybridChunker`, which is the one that splits oversized chunks and repeats table headers (`our-architecture-options.md:413-416`). The report says exactly this at lines 540-541: the harness "covers two of Docling's chunkers, not `HybridChunker`." The brief turned "the harness does not cover the one Docling chunker that matters" into "Docling's chunker was never in the comparison harness." That is a material overstatement, because the harness is the obvious place to settle the question.

The report's own description of `HybridChunker` matches the defect: structure first, token-aware splitting of oversized chunks, `repeat_table_header=True`, `omit_header_on_overflow` for a row that would otherwise overflow. Those are the two behaviors the brief wants to build by hand. The report's option (d) scores 23 of 45 and says the swap buys only the size guard. That is a judgment, not a measurement. The payload and citation contract is a real cost, but the harness can measure the chunk shapes before any contract work.

Why this changes the decision: the recommendation is to write the fallback ladder, the table row groups, and the header repetition. The existing harness was built to answer "why build a chunker from scratch?" Adding `HybridChunker` to it is a small change. If the library preserves the page and row metadata, the build is smaller and the table behavior comes from a tested implementation. If it does not, the harness produces the evidence for the custom path. The brief should not dismiss the option on a claim about the harness that is wrong.

What settles it: add `HybridChunker` to `tests/fixtures/chunker_comparison.py` and run it on the fee-schedule fixture and one oversized table. Compare chunk count, maximum size, page ranges, row retrievability, and header repetition. The report already notes the harness needs `DoclingDocument` validation. That is the cost of the measurement, not a reason to skip it.

### A4. The table retrieval unit is under-specified, and TableRAG does not support single-row retrieval at this scale

The brief says:

> Table rows are the retrieval unit; a whole table is a citation pointer only when it fits.

And it cites TableRAG: whole-table prompting scored 4.6 and 9.1 where schema plus cell retrieval scored 49.2 and 45.5.

The numbers check out. I read Table 2 of arXiv 2410.04739: `ReadTable` scores 4.6 on ArcadeQA and 9.1 on BirdQA with GPT-3.5 Turbo, and `TableRAG` scores 49.2 and 45.5. But the label "schema plus cell retrieval" is too narrow. TableRAG is query expansion, schema retrieval, cell retrieval, and a program-aided solver. `ReadSchema` alone scores 43.1 and 40.3, close to the full method. The gain is not specifically from cell retrieval. The benchmarks are million-token tables. The 186 KB table in this corpus is about 47,000 tokens, roughly twenty times smaller. At that size, whole-table prompting in a 300K window is a different experiment, and TableRAG does not settle it.

The brief's wording also leaves a design fork open. "Table rows are the retrieval unit" could mean the current one-row-per-chunk entries, or the report's row groups of about 512 tokens with the header repeated (`oversized-elements-and-repair.md:101-103`). The report recommends row groups. The code already emits single rows (`services/ingestion/chunker.py:502-517`). The two choices produce different indexes, different citation granularity, and different retrieval behavior. The brief does not say which it means.

Why this changes the decision: the table path is half the chunker repair. If the team reads "rows are the retrieval unit" as endorsing the current single-row entries, it may keep a granularity that TableRAG does not support at this scale. If it reads "row groups," it needs a different split and a different citation range. The evidence to choose is a retrieval test, not the TableRAG headline.

What settles it: run the 60-question set against three table variants: whole table when it fits, row groups at about 512 tokens with the header repeated, and single rows. Compare retrieval and citation precision. The current code already produces the third, so the test is cheap to set up.

## Attacks that are corrections but not decision-changing

### The number audit

| Brief claim | What the reports say | What the report's source says | Verdict |
|---|---|---|---|
| 512 tokens scores 97.59 faithfulness, against 94.26 at 1,024 and 80.37 at 2,048 | `chunking-strategy-evidence.md:29` gives exactly those numbers, plus 256 at 97.22/97.78 and 128 at 95.74/97.22 | I read Table 3 of arXiv 2407.01219. The numbers are exact. The setup is one 10-K (`lyft_2021`), about 170 synthetic queries, ada-002 embeddings, Zephyr-7B, and 20-token overlap | Numbers hold. The brief omits the single-document setup and the 256 result. "512 is defensible" holds. "The target was never wrong" is stronger than the evidence |
| Chroma's 200 to 400 efficient range | `chunking-strategy-evidence.md:225` says "Chroma puts the efficient range at 200 to 400" | I read the Chroma report. In the recursive family, 200/0 has the best IoU (6.9) and 400/0 has the best recall (89.5). The conclusion does not state a 200 to 400 range | The band is a reasonable inference from the numbers. The attribution "Chroma's" is wrong. It is the research report's synthesis |
| Zero overlap beat 10 to 20 percent overlap, with no quality gain and 25 percent more chunks | `chunking-strategy-evidence.md:143` says adding 10 to 20 percent overlap "produced no measurable gain" and 20 percent overlap inflates chunk count by 1.25 times | The cited study is arXiv 2601.14123, a preprint. The report says BERTScore deltas at or below 0.004 and EM at or below 0.001 | "No measurable gain" is supported. "Beat" is not. The 1.25 times is at 20 percent, not the whole 10 to 20 range. The brief omits "preprint" |
| Fixed-size beat semantic chunking on all four non-stitched retrieval sets (Qu et al., NAACL Findings 2025) | `chunking-strategy-evidence.md:31,67-71` gives the four datasets and the numbers | The ACL Anthology page confirms the paper is Findings of NAACL 2025. The abstract supports "computational costs are not justified by consistent gains" | Holds. The specific per-dataset numbers were not re-read in the paper, but the direction is peer reviewed and the report's summary is consistent with the abstract |
| TableRAG scored 4.6 and 9.1 for whole-table prompting against 49.2 and 45.5 for schema plus cell retrieval | `chunking-strategy-evidence.md:35` and `oversized-elements-and-repair.md:82` say the same | I read Table 2 of arXiv 2410.04739. The numbers are exact for GPT-3.5 Turbo. The method is query expansion plus schema retrieval plus cell retrieval plus a program-aided solver, on million-token tables | Numbers hold. The mechanism label is too narrow and the scale is omitted. The direction holds, the transfer to a 47K-token table does not |
| Late chunking gains 1.5 to 1.9 nDCG | `chunking-strategy-evidence.md:79` and `newer-approaches.md:43` say 1.5 to 1.9 absolute across three models and four datasets | I read arXiv 2409.04701. The numbers are exact: 1.9 sentence, 1.8 fixed, 1.5 semantic | The number is Jina's average, not a BGE-M3 result. `newer-approaches.md:47-49` says late chunking with BGE-M3 collapsed from 0.225 to 0.067 nDCG in an independent 2025 study; the report's likely explanation is the CLS-versus-mean pooling mismatch. The brief uses the wrong gain for this stack |
| Azure On Your Data uses 5 retrieved documents at 1,024 tokens | `production-systems.md:58` says the chunk size default is 1,024, `topNDocuments` defaults to 5, and Azure states that chunk size times retrieved documents is the token total | The report cites the Microsoft page with a 2026-10-14 date. I did not fetch it again | Holds. The "5 x 1,024" is the product of two defaults, not a documented total, and the brief says "default 5 x 1,024" which is fair |
| Production defaults cluster at 256 (Docling), 300 (Bedrock), 384 (OpenSearch), 1,024 (Azure, LlamaIndex) | `production-systems.md:204` gives all four, plus OpenAI 800, Elastic about 330, pgai 800 characters, Unstructured 500 characters, LangChain 4,000 characters | The report cites each vendor page. I spot-checked Docling, OpenSearch, and Azure in the report's own text | Holds as a selected cluster. The brief omits the 800-token defaults and the character-based outliers. "Cluster" is doing more work than the full distribution supports |
| Measurements of 5.09, 1.61, 1.33 and 2.59 bytes per Nova token | `answer-context-budget.md:171-179` gives exactly those ratios | The report measured synthetic strings through Bedrock `Converse`, one call each, and says so at lines 372-380 | Holds as a measurement of synthetic samples. The brief says "measured against Nova Lite" but omits "synthetic." The report says the numbers show direction and size, not the client's exact count |
| 81 of 341 documents, roughly 21,000 to 25,000 chunks, 2 to 4.5 hours | The reports repeat the counts and derive the time from documented rates | No report ran the index query. `our-architecture-options.md:284` says "I did not have the current ingest log." The 21,188 is the brief's number. The 2 to 4.5 hours is arithmetic: 21,188 divided by 2.7 or 5.5 chunks per second, doubled for parsing | Unverified counts and an estimated time, presented as measured. See A1 |
| The estimator is roughly 15x low on a table | No report says 15x. `answer-context-budget.md:61` measures the estimator on two table shapes: 189 estimated against 599 actual (3.2x low) and 162 against 460 (2.8x low). `our-architecture-options.md:48` shows a 756,182-character no-whitespace string estimates as one token | The formula is `words x 1.35` (`chunker.py:64-66`). The measured table samples are 2.8x to 3.2x low. A CSV with one whitespace-separated token per row can produce a much larger error, but the report did not measure that case | Unsupported as stated. The direction is right, the multiplier is not. The real failure is the no-whitespace case, where the estimator is not 15x low but effectively blind |
| A 439 KB table chunk became a 228,326-token estimate | `answer-context-budget.md:5` says the prompt reached about 900 KB and "the gateway estimated 228,326 tokens." The 439 KB chunk alone is estimated at 222K to 311K at lines 191 | The 228,326 is a gateway estimate for the whole prompt, not the chunker's `estimate_tokens` and not the 439 KB chunk alone. The 439 KB chunk is inferred to be a table at line 5, not verified | The brief conflates three quantities. The number is real, the attribution is wrong |
| The 439 KB chunk alone is 222K to 311K Nova tokens | `answer-context-budget.md:191` computes 222K at 1.98 bytes per token and 311K at 1.41 | The measured ratios include 1.33 for CSV, which gives about 330K. The report's own table has 1.33 | The range is directionally right and the upper bound is not the worst measured case. The conclusion (over 300K on its own) is stronger than the brief says |
| The 756 KB prose chunk and the 186 KB table chunk | `chunking-strategy-evidence.md:14` takes them as given. `answer-context-budget.md:9` says it did not have the offending chunk. `our-architecture-options.md:618-619` says they are the brief's figures | No primary measurement in the report set. The 756 KB single-sentence case is reproduced by C2 in `our-architecture-options.md:553-567`; the no-whitespace estimate is reproduced by C3 at 569-575 | The 756 KB behavior is reproducible from the code. The specific corpus sizes are unverified. The 439 KB is inferred to be a table, which is not sound: the prose path also produced a 756 KB chunk, so a 439 KB prose element is possible |
| Option (c) scores 36 of 45, against 32, 35 and 23 | `our-architecture-options.md:369-374` gives the table and the weights at 365-366 | The scores are the author's judgment on a 1 to 5 scale. The weights put provenance at 2 and prompt boundedness at 1 | The arithmetic holds. The score is not evidence. The brief presents it as a reason without saying it is a rubric |
| The 300K window is not the budget; long context costs 100 to 400 times a chunked prompt | `answer-context-budget.md` and `newer-approaches.md:197` support both | The 100 to 400 range is arithmetic from list prices: a 1M-token prompt at USD 2 per million costs USD 2.00 against about USD 0.005 for five 500-token chunks. At 300K it is about 120 times | Holds as arithmetic. The brief's "100 to 400" is the right order of magnitude |
| 8,192-token BGE-M3 limit and 300K Nova window | `chunking-strategy-evidence.md:185` and `answer-context-budget.md:5` | BGE-M3's model card states 8,192. Nova Lite's window is 300K per AWS | Holds. I did not call Bedrock to re-measure |

A few smaller figures in the brief:

| Brief figure | Check | Verdict |
|---|---|---|
| Six reports, 2,265 lines between them | `wc -l` on the six files sums to 2,265 | Holds |
| A 60-question pilot | A plan parameter. Neither the brief nor the reports derive it from a sample-size calculation | Fine as a plan, not as evidence |
| 10 to 200 users | A design target from `newer-approaches.md:5`, not a measured installed base | A scope assumption, not evidence |
| The Azure Split skill's 12,684-token chunk | `oversized-elements-and-repair.md:47` labels it a user report, not a specification | The number is real as a report. The brief's parallel to our sentence splitter is illustrative, not a measured comparison |
| 700 output tokens | `app.py:1003` and `app.py:1329` default `OI_MAX_TOKENS` to 700 | Holds |
| Frontier worst case 25,724 bytes | 24,000-byte prompt plus 700 output plus 1,024 reserve. The prompt includes 18,000 bytes of passages and the 4,000-byte question | Holds as arithmetic, with the same bytes-versus-tokens caveat as the office lane |

### Code claims that need tightening

| Brief claim | Code check | Verdict |
|---|---|---|
| The table path at `chunker.py:496` builds one chunk for the whole serialised table and never applies the ceiling | `chunker.py:496-501` constructs the `kind="table"` chunk with `whole_text` and no size check. `split_oversized` is called only from the prose loop at line 634 | Holds. But the same loop also emits one `table_row` chunk per row (`chunker.py:502-517`), so the code already has row retrieval units. The defect is the unbounded whole-table entry, not the absence of rows |
| Two prompt builders, `app.py:989` for JSON and `app.py:1318` for streaming | Both lines are the inline `context = "\n\n".join(...)` constructions. `_generate` is used by JSON and jobs; `_frames` by streaming | Holds. Only two live builders. `injection.assemble` would be a third if wired |
| `injection.assemble` has delimiters and an injection scan that is tested and never called | `services/retrieval/injection.py:161-191` defines `assemble`; `tests/fixtures/injection_acceptance.py:181` calls it; no code in `services/api/` imports it | Holds. "Tested" means the fixture, not a unit test in the serving path |
| `citations.verify_claim` checks claims against the full `passage.text` | `services/retrieval/citations.py:267` builds the haystack from `passage.text`; `services/api/answer.py:261` passes `result.passages` | Holds. But there is no current truncation. The failure is a hazard introduced by the proposed trim, not a present defect. If the trim happens in the assembler and `finish` still reads `result.passages`, the verifier will check text the model never saw |
| The manifest reads only content sha256 and payload version | `services/ingestion/ingest.py:1009-1011` selects `content_sha256` and `payload_version`; the skip decision at 1340-1371 uses those | Holds. The table also stores counts and failure fields, but the skip decision reads those two |
| The chunker version is in the embedding contract | `services/ingestion/contract.py:45` lists `chunker_version`; line 121 reads `chunker.CHUNKER_VERSION` | Holds. A mismatch raises and `ingest.py:1758-1760` returns 1 before the cycle |
| The version string is inside every chunk id, so every id moves either way | `chunker.py:103-105` hashes `doc_id`, `source_sha256`, `CHUNKER_VERSION`, ordinal and `content_hash` | Holds for a chunker version bump. A payload bump alone does not move ids, because `PAYLOAD_VERSION` is not in the hash. "Either way" is ambiguous and should be tightened |
| Office lane worst case 7,612 bytes against `MODEL_CTX` 8,192 | `deploy/office/compose/docker-compose.yml:298` passes `MODEL_CTX` as llama.cpp `-c`, which is tokens. The budget's 6,400-byte prompt includes the 800-byte question, plus 700 output and 512 reserve | The arithmetic is a conservative bound: for a byte-level BPE tokenizer, a 6,400-byte prompt cannot exceed 6,400 tokens. The brief compares bytes to tokens without stating the inequality, so a reader can think 7,612 bytes equals 7,612 tokens. The bound holds, the presentation does not |
| No blue/green path because `chunk_index` has no collection column | `db/migrations/010_chunk_index.sql:22-42` has `chunker_version`; `retriever.py:341-371` filters only ACL | The column is there. A lexical `chunker_version` filter plus a Qdrant alias is a small change. The collection-wide contract is the real blocker. "No blue/green path" is too absolute. See A2 |
| `CHUNKER_VERSION` to v3, `PAYLOAD_VERSION` to v6, `EMBED_CONTRACT_ALLOW_CHANGE=1` with a reason | Current values are `chunker-v2` (`chunker.py:59`) and `payload-v5` (`ingest.py:130`). The allow-change path reads `EMBED_CONTRACT_REASON` but does not require it | The version targets are right. The reason is optional in code. The brief says "with a reason," which is a process instruction, not an enforced guard |
| A chunker bump alone does not re-ingest, a payload bump alone re-derives with the old chunking | `ingest.py:1340-1348` skips on identical bytes unless the payload version moved. The chunker version is not in the manifest select | Holds. The three-part act is correct |

### The late-chunking correction is the most consequential of the non-decision-changing findings

The brief says late chunking "gains 1.5 to 1.9 nDCG" and concludes "yes with a custom token-level client, not worth it for the gain." The 1.5 to 1.9 is Jina's average across three embedding models and four datasets. It is not the result for BGE-M3. `newer-approaches.md:47-49` reports an independent 2025 study where late chunking with BGE-M3 collapsed from 0.225 to 0.067 nDCG with fixed chunks and from 0.240 to 0.081 with semantic chunks. The likely cause is the pooling mismatch: BGE-M3 uses CLS pooling, and late chunking needs mean pooling over token spans. The brief's own report says this at lines 49 and 283.

The practical consequence is that a custom token-level client would not produce a small gain for this stack. It would likely make retrieval worse unless the model or pooling changed. The brief's "what would change this answer" item, "a TEI path that exposes token-level embeddings cheaper than a custom client," is therefore wrong. A token-level route alone does not reopen late chunking. A mean-pooled model and a measured gain would.

### The dismissed options, one by one

**Semantic chunking.** The dismissal is supported. Qu et al. is peer reviewed and finds no consistent gain on non-stitched documents. Chroma's default semantic chunker had lower recall than its 200-token recursive baseline, 83.6 against 88.1. The 2026 NQ study found sentence chunking tied with semantic up to about 5,000 tokens. No pilot is needed. The report's narrow exception, documents with no headings or reliable layout, does not describe this corpus.

**Late chunking.** The dismissal is supported, but the brief's reason is wrong. With BGE-M3 the measured effect is a collapse, not a 1.5 to 1.9 gain. A token-level TEI route alone does not reopen it. A mean-pooled model and a measured gain would. No pilot with BGE-M3 is justified.

**Contextual retrieval.** This is not dismissed. The brief calls for a 60-question pilot, which matches the evidence. The generated half has the largest vendor-run gain in the review and a 0.020 nDCG independent gain on a 20 percent NFCorpus subset. The audit volume and the mixed-label risk are real. The brief's handling is correct. The 60-question sample is a plan parameter, not a measured design.

**ColBERT.** The deferral is supported. The Qdrant benchmark is a quality tie with the cross-encoder, 0.776 against 0.779 on nDCG@20, with a large latency win. The cost is a new encoder, a new vector type, a re-embed, storage and a serving component. "Only if provider reranking becomes the problem" is the right condition. No pilot now.

**RAPTOR and GraphRAG.** The dismissal as a default is supported. RAPTOR's controlled gain is 1.7 points on QuALITY, and the 2026 GraphRAG-Bench has it below basic RAG on every task. The summary nodes have no page range and can mix access labels. The brief's "cost far more than they return" is a judgment, not a measured cost-benefit. The report allows a client-specific pilot for a measured global or multi-hop gap; the brief does not mention that exception.

**Long context.** The dismissal as a replacement is supported by the access-label and page-citation contract, and by the 100 to 400 times cost arithmetic. The quality advantage in the 2024 RAG-versus-long-context study is real, so the argument has to be the contract, not quality. The brief says this.

**An off-the-shelf chunker.** This is the one dismissal that is not supported. See A3.

### The production-authority claims are overstated, and one is self-contradicted

The brief says:

> Every production chunker enforces a hard ceiling after overlap and contextualisation.

The same bullet lists OpenSearch's `max_chunk_limit` appending overflow to the last chunk and Bedrock's `NONE` making one chunk per document. It also notes LangChain's `parent_splitter=None` returning the raw parent. Those are documented ways to exceed the ceiling. `production-systems.md:222` says the disagreement "is about whether the number is a promise." The accurate claim is: the systems that enforce a ceiling make it an invariant; the systems that do not produce the failures we have. The brief's universal claim is false, and it weakens the otherwise good argument that this defect is common enough to be recognized.

The next sentence is also too strong:

> ... so the 186 KB table chunk is outside every documented production behaviour and Docling is the reference to copy.

`production-systems.md:210,226` says Bedrock's `NONE` and OpenSearch's overflow can produce oversized chunks by design. The precise claim is that no surveyed system documents a whole-table chunk as its default table policy, and Docling is the only one that documents row-aware splitting with repeated headers. That is enough. The universal phrasing is not.

One related claim does hold, and the brief should keep it. `oversized-elements-and-repair.md:43` says Unstructured "chunks tables by rows when too large." I checked Unstructured's current chunking docs. It says an oversized table "is divided into two or more `TableChunk` elements using text-splitting." It does not say row-based splitting. The brief's line, "Unstructured isolates tables and text-splits oversized ones," is the correct one. The report that says rows is wrong. This is worth noting because the brief's own source set contradicts itself, and the brief chose the better source without saying so.

### The verifier claim is a forward-looking hazard, not a current defect

The brief lists "The verifier would not notice truncation" as one of the four things actually wrong. The code has no truncation today. `_generate` joins the full `p.text`, and `finish` verifies against the same `result.passages`. The hazard appears only after the proposed trim. If the assembler is called from `_generate` and `_frames` and returns a new list, `finish` still reads the old list and the verifier will check text the model never saw. The report gets this right: trim in `answer.prepare`, so every downstream consumer sees the trimmed objects (`our-architecture-options.md:127-139`). The brief says "the verifier changes with it" but does not say where the trim lives. As written, the fix can create the defect it warns about.

### The reranker omission changes what "stops the failure today" means

The brief's four layers do not mention that the reranker slices every candidate to 8,000 characters (`services/retrieval/rerank.py:122`), and the degraded response slices excerpts to 240 (`services/api/budget.py:228`). For the 439 KB chunk, the reranker scores about 1.8 percent of the text. The answer-path cap stops the provider rejection. It does not make the retrieval ranking meaningful, because the ranking was never a ranking of the whole chunk. The brief says the index remains broken, so the conclusion is consistent. The mechanism should be named, because it is the strongest argument that the chunker repair is not optional.

### The office-lane question cap is a user-visible change the brief does not flag

The office budget sets an 800-byte question cap. The current `MAX_QUESTION_CHARS` is 32,000 characters (`app.py:110`). The report says to refuse a longer question with a typed 400 rather than truncate it (`answer-context-budget.md:274`). The brief lists the 800-byte number without saying it will reject questions. A normal two-paragraph question can exceed 800 bytes. That is a product decision, not arithmetic, and it should be explicit before the office lane ships.

### `injection.assemble` is not a drop-in

`assemble` returns a `system` string and a `user` string. Both live builders send a single user message. Wiring it changes the message shape. `assemble` also has no budget, and `Assembled.refused` is true when any passage is quarantined. The brief says a quarantined passage is "excluded and recorded," not that the request is refused. The module's docstring says the passage is "excluded from the request and refused." The brief should reconcile those before treating the wiring as a small change.

### The scoring is a rubric, not evidence

The 36/45, 32, 35 and 23 are the report author's judgment. The weights are explicit (`our-architecture-options.md:365-366`), which is good, but the brief presents the score as a reason. The weights put provenance at 2 and prompt boundedness at 1. That is a values choice, and it is why option (c) beats option (b) by one point. The code defects support the recommendation without the score.

### "Six independent research reports" is not accurate about the numbers

The six reports are independent in topic. They are not independent in evidence. The 756 KB, 186 KB, 439 KB, 228,326, 81 of 341 and 21,188 figures all trace to one defect brief. At least three reports say they did not re-measure the counts. The brief uses "independent" to imply corroboration that does not exist for its central facts. The code findings are independently checkable, and they are the part that matters. The corpus counts are not.

## Claims that hold up under checking

These are the parts the brief should defend harder.

1. **The answer path has no byte or token budget.** `_generate` and `_frames` join the full passage text with no cap (`app.py:989,1318`). The only limits are the 700-token output ceiling and a time budget. The 439 KB passage reached the provider. This is the clearest defect and the reason the answer cap is the right first move.

2. **The whole-table chunk has no ceiling.** `chunker.py:496-501` builds `text=whole_text` with no size check. `split_oversized` is called only from the prose loop at line 634. The table path is the one that builds the 186 KB table chunk, and the code confirms it.

3. **The sentence splitter has no fallback and no post-condition.** `split_oversized` splits on `(?<=[.!?])\s+` and nothing else (`chunker.py:559`). A 756 KB run with no sentence terminator returns one piece. `_group_prose` applies the same estimate check to each piece, so a piece that is itself over the ceiling still goes in. The C2 and C3 commands in `our-architecture-options.md:553-575` reproduce both failures.

4. **The estimator is documented as a bound, and it is blind to no-whitespace text.** `estimate_tokens` is `words x 1.35` (`chunker.py:64-66`). A 756,182-character string with no whitespace estimates as 1 token. A 3,000-row table at 170,504 characters estimates at 40,486 tokens and is emitted whole. The defect is not that the estimate is approximate. It is that there is no post-condition that can catch the cases the estimate cannot see.

5. **The manifest and contract mechanics are as the brief describes.** A chunker bump alone does not re-ingest, a payload bump alone re-derives with the old chunking, and a mismatched contract stops the worker before the cycle. The three-part act is correct.

6. **The chunk id formula makes a chunker bump corpus-wide.** `uuid5(doc_id, source_sha256, CHUNKER_VERSION, ordinal, content_hash)` is in `chunker.py:103-105`. Every id moves when the version moves. The brief's "all 341" is a correct consequence of the chosen versioning policy, even if the policy is a choice.

7. **The 512-token target is defensible.** The Wang et al. table is real, the Chroma results point at 200 to 400, and the production defaults cluster at 300 to 1,024. The brief is right that the target is not the defect. It overreaches when it says the target "was never wrong," because the study is one document and the code never measured tokens in the first place. The defensible claim is: 512 is inside the measured band, and the system's failure is the absence of a post-condition, not the choice of 512.

8. **Zero overlap is the better default for this corpus.** The 2026 preprint found no measurable gain from 10 to 20 percent overlap. In Chroma's same-size comparison at 400 tokens, no overlap had recall 89.5 against 88.1 for 200 overlap, with a slightly better IoU (3.6 against 3.3). The direction holds. The word "beat" overstates a small edge.

9. **Semantic chunking is not the upgrade.** Qu et al. is peer reviewed and finds no consistent gain on non-stitched documents. Chroma's default semantic chunker had lower recall than its 200-token recursive baseline (83.6 against 88.1). The 2026 NQ study found sentence chunking tied with semantic up to about 5,000 tokens. The brief's dismissal is supported. The report's narrow exception, documents with no headings or reliable layout, does not apply to this corpus.

10. **The table metadata design is right.** Table id, row range, header flag, page range and caption are the fields a row citation needs. Docling's `repeat_table_header` and `omit_header_on_overflow` are the documented precedents. Unstructured's `TableChunk` carries the equivalent metadata. The brief's `table_id`, row range and page range recommendation is consistent with both.

11. **The answer-side budget shape is right.** Measure the whole prompt, drop whole passages from the bottom, never cut mid-row or mid-sentence, disclose the omission, and verify against what was sent. Azure's `maxOutputDocuments` plus `maxOutputSizeInTokens` and its activity warning are the only surveyed request-level cap. The brief's shape is the same idea.

12. **The audit and access-label constraint is the right frame.** The stack cannot turn every chunk into a copy of the document in the evidence log, and it cannot bake a restricted appendix into an allowed chunk's embedding. This is why contextual retrieval's generated half is a pilot and not a default. It is also why RAPTOR and GraphRAG are out, and why long context is not a replacement. The brief should defend this harder, because it is the reason several apparently attractive options are rejected.

13. **The "window is not the budget" point is well supported.** Lost in the Middle and Chroma's Context Rot both show degradation with input length, and Azure's rule makes the multiplication explicit. The 300K window is headroom, not a target.

14. **TEI's `--auto-truncate` defaults on, and the compose file does not set it.** I checked the TEI CLI docs: the default is true. The office compose passes `--max-batch-tokens=4096` and does not pass `--auto-truncate`. The brief's warning is correct for the deployed default. The actual `/info` value is still worth querying, because the contract records it and a running service can differ from the compose file.

15. **Unstructured text-splits oversized tables, and Docling is the row-aware reference.** I checked Unstructured's current docs. The brief's table-policy claim is the better-sourced one, even though one of its own reports says the opposite.

## What I could not check

- **The index counts.** I did not query Qdrant or Postgres. The 81 of 341, 21,188, 171, 716-character mean, 756,182-character maximum and 439 KB chunk are all from the brief or the reports that repeat it. The SQL to reproduce them is in `our-architecture-options.md:623-633`. Until that runs, the counts are unverified.
- **The 439 KB chunk's kind.** The answer-context report infers it is a table because the table path is the only one that can produce a chunk that large. That inference is not sound: the prose path produced the 756 KB chunk, so a 439 KB prose element is possible. No report read the chunk text.
- **The deployed TEI contract.** The compose file does not set `--auto-truncate`, so the default is on. I did not query `/info` or `collection_contracts` for `auto_truncate` and `max_input_length`. The brief's claim that the 756 KB vector is the first 8,192 tokens is a default, not an observed fact for this deployment.
- **The actual Nova token counts.** The 1.33 to 5.09 bytes per token measurements are synthetic strings, one call each, from the answer-context report. I did not call Bedrock. The report says the numbers show direction and size, not the exact client count.
- **The Wang et al. setup.** I verified Table 3's numbers and the `lyft_2021` label. I did not re-read the query generation or confirm "about 170" queries. The report's caveat about one document and synthetic queries is enough to bound the claim.
- **The Chroma 200 to 400 phrase.** I read the report and could not find a sentence that states that range. The numbers support the band, but the attribution is the research report's synthesis.
- **The ingest log and the real wall-clock rate.** The 2 to 4.5 hours is arithmetic from documented rates, not a measurement. The report says so; the brief does not.
- **The actual question lengths.** The 800-byte office-lane cap is arithmetic. I did not measure the distribution of real questions, so I cannot say how often it would refuse one.
- **The TableRAG ablation details.** I read Table 2 and the method description. I did not read the ablation that attributes 9.4 points to schema retrieval and 11.5 to cell retrieval. The full method includes a program-aided solver, which the brief's "schema plus cell retrieval" label omits.
- **The Qdrant alias and lexical-filter path.** I read the schema and the query. I did not run a deployment, so I cannot say how much work a `chunker_version` predicate and an alias switch would take.
- **The contextual retrieval pilot.** It has not been run. The brief's 60-question pilot is a proposal, not evidence.
- **The off-the-shelf `HybridChunker` behavior on this corpus.** The comparison harness covers `PageChunker` and `HierarchicalChunker`, not `HybridChunker`. I did not run the harness. The claim that `HybridChunker` repeats table headers is from the report and Docling's docs, not from this corpus.
