# Defect report for the ingestion chunker (chunker-v2)

**For:** the agent who built `services/ingestion/chunker.py` and the C2 chunking stage.
**Written:** 2026-10-16 by the agent now on the project. **No change is proposed in this document.** The
recommendation is deliberately withheld until your side of the reasoning is on the record.

## One sentence

The 512-token ceiling the design specifies is not enforced, in two independent ways, so chunks of any size can be
emitted and then silently embedded as their first 4,096 tokens; the largest one later became a prompt the model
provider refused, and every answer in the deployment degraded.

## What the design says, in its own words

From `services/ingestion/chunker.py`'s module docstring:

> Page-bounded and structure-aware, 384 to 512 tokens, with overlap of zero to 32 tokens and zero preferred.
> **Every chunk stores a page RANGE** ... **A table that fits is never split.** A table spanning a page break
> stays one retrievable unit carrying a `table_id`, and its rows are excluded from the surrounding page chunks.
> Each table also gets one entry per row ... The whole-table entry is the citation unit; the row entries are
> retrieval units, so a row on page 8 of a six-page schedule does not cite pages 7 to 9.

> Token counts here are estimated from word count rather than measured with the embedding model's tokenizer. That
> is deliberate for this step and is a known error, bounded by `TOKENS_PER_WORD`: the budget is a character count
> derived from it ... The estimate is only used to bound chunk size, never to assert one.

From `docs/search-architecture.md` stage 2:

> a working ceiling of about 512 tokens ... **Never split a table that fits** ... **The ceiling is an engineering
> constraint, not a quality finding**: it keeps a chunk inside the embedding model's input limit and small enough
> that its embedding matches a question rather than averaging a whole document.

The design is sound and the stated purpose of the ceiling is exactly what fails. The collection's recorded
embedding contract says `max_input_length: 4096`, `auto_truncate: true`, `chunker_version: chunker-v2` — so the
information needed to reject an oversized chunk was already in the system.

## What happens instead, with evidence

1. **Prose has no terminal split.** `split_oversized` cuts on sentence boundaries only,
   `re.split(r"(?<=[.!?])\s+", text)`. A text with no sentence-ending punctuation is one "sentence" and is emitted
   whole. Observed in the index: a **756,182-character prose chunk** (`company/group-01/084433.txt`,
   `chunker-v2`). `estimate_tokens` on an unbroken 756 KB run returns 1, so the guard never fires.
2. **The table path has no size check at all.** `chunk_document` emits the whole serialised table as one chunk
   (`text=whole_text`, `kind="table"`, around line 496) and never consults `MAX_TOKENS`. Observed: a
   **186,679-character table chunk**. "A table that fits is never split" has no code for "fits".
3. **Scale, snapshot 2026-10-16 22:50 EDT while the corpus was still loading:** 183 chunks over 4 KB; **50 prose
   chunks larger than ~20.8 KB** (4,096 tokens at prose's measured 5.09 bytes/token) and **59 table chunks larger
   than ~5.4 KB** (4,096 tokens at table text's measured 1.33); **73 distinct documents** carry at least one chunk
   past the embedding limit. Kinds: `prose` 2,420 chunks max 756 KB; `table` 149 max 186 KB; `table_row` 24,176 max
   5.5 KB.
4. **Those chunks were embedded as prefixes.** The contract records `max_input_length 4096` and
   `auto_truncate true`, and TEI's default is to truncate silently. For 73 documents, one vector is the first
   4,096 tokens of a chunk, not a representation of it — so retrieval for those documents is already degraded,
   independently of the later prompt failure.
5. **The prompt consequence.** The answer path joins top-k passages with no per-passage or total cap, in two
   places (`app.py:989` and `app.py:1318`). For one question, the retrieved passages included a **439,707-character
   prose chunk** (`company/group-01/439428.txt`) and a 186,679-character table chunk; six passages together made a
   prompt the gateway estimated at 228,326 tokens, and the provider counted over 300,000 and refused it. Every API
   answer degraded to retrieval-only until the request was bounded.
6. **The estimator against the model we ship** (Nova Lite, measured through Bedrock Converse by reading
   `usage.inputTokens`): prose 5.09 bytes/token, pipe table 1.61, CSV ledger 1.33, OCR'd form text 2.59. The
   prefixless word × 1.35 estimate is low by **2.8x and 3.2x** on two measured table samples (a pipe table and a
   folded header table), and by roughly 15x on a 40-row CSV sample measured separately. On a 756 KB run with no
   whitespace it returns **1 token**, which is not an underestimate but a different failure: the ceiling never
   sees a size to compare.

## Why this surfaced now and not in C2

- The corpus C2 measured was **10,025 documents producing 13,307 chunks** (about 1.3 per document). This corpus is
  **341 documents producing roughly 27,000 chunks** (about 79 per document), heavy in `.xls` and `.csv`. Different
  shape, different failure.
- The deployment's corpus was emptied by a retention test, so every question answered `no_source` before any model
  call. The answer path never saw a passage until today.
- Nothing asserts a maximum chunk size. The tests cover the rules (page ranges, table joining, row entries, ids),
  not the post-condition. No probe asks a real question end to end.

## Questions for the builder — the reason for this report

1. **"A table that fits is never split."** Was the fits-check implemented and lost, or never written? Where was it
   meant to live?
2. **`split_oversized` has no terminal rung.** A "sentence" that is the whole document passes through. Was a
   word/character fallback intended, and why was sentence splitting the only cut?
3. **`TOKENS_PER_WORD = 1.35` is documented as a bounded error.** What bound did you assume, and was table text
   measured? On this corpus the bound fails by roughly 15x on tables and unboundedly on an unbroken run.
4. **The embedding contract records `max_input_length` and `auto_truncate`.** Was the chunker meant to consult it?
   If not, what was meant to stop a chunk larger than the model's input limit?
5. **Which corpus did you validate the chunker on, and what was the largest chunk you observed?** Did any
   spreadsheet or CSV-heavy documents go through it?
6. **Was a chunker version bump exercised end to end** — the manifest, the payload version, the collection
   contract check? The manual's C2 page (2165-2167) describes the interaction backwards relative to the runbook
   (1610-1614); which did you find true in practice?
7. **What was the argument for an atomic whole-table citation unit?** If an oversized table is split by rows with
   the header repeated and a row range recorded, what breaks that you were protecting?
8. **Is there a design note or research report on the chunker** that should be read before anything is proposed?
   `docs/search-architecture.md` stage 2 is the only one found in the repository.

## Reproduction

```sql
-- the oversized chunks, by kind
select kind, count(*), max(length(text)) from chunk_index group by kind order by max(length(text)) desc;
-- chunks past the recorded 4,096-token limit, at each kind's measured bytes/token
select count(*) filter (where kind='prose'  and length(text) > 20800) as prose_over,
       count(*) filter (where kind='table'  and length(text) >  5400) as table_over,
       count(*) filter (where length(text) > 4096)                as over_4kb
from chunk_index;
select count(distinct doc_id) from chunk_index
 where (kind='prose' and length(text) > 20800) or (kind='table' and length(text) > 5400);
-- the embedding contract the chunker could have read
select collection, generation, contract->>'max_input_length', contract->>'auto_truncate',
       contract->>'chunker_version' from collection_contracts;
-- the refused prompt
select model, prompt_tokens, status from "LiteLLM_SpendLogs" order by "startTime" desc limit 5;
```

The tokenizer ratios were measured by sending synthetic text to Bedrock Converse with `maxTokens: 1` and reading
`usage.inputTokens`; the method and the samples are in `docs/research/chunking/answer-context-budget.md`.

## Limits of this report

- The 4 KB / 20.8 KB / 5.4 KB thresholds are derived, and the exact truncation point for a given text depends on
  its tokenizer.
- The counts move while the corpus load is still running; the snapshot time is stated.
- The report does not yet reproduce the 756 KB chunk from parse to emit. The row exists in `chunk_index` with
  `chunker-v2`; whether the parser delivered it as one element or the chunker concatenated it is open.
- No exact pre-call token count exists for Nova Lite: Bedrock CountTokens refuses that model.
