# Chunking and context: an options appraisal against this code

Research report, 2026-10-16. Written against `git main` at `963a1ef`. Research only: no code changed,
no migration run, no other file touched. The working tree already carried an uncommitted addition to
`docs/requirements.yaml` (RET-INGEST-002) before I started, and I left it alone.

The finding I did not expect: this is not a chunker bug that one guard fixes. The reranker silently
slices an oversized passage to 8,000 characters (`services/retrieval/rerank.py:120`). The degraded
response slices excerpts to 240 (`services/api/budget.py:228`). The prompt builder slices nothing at all
(`services/api/app.py:989`). And the chunker's stated ceiling is not enforced anywhere the estimator
cannot see (`services/ingestion/chunker.py:64-66`).

My recommendation is option (c): cap the answer path first, because it deploys in an hour and stops a
900 KB prompt today, then cap the chunker on both paths and re-chunk the corpus, which costs a full
re-embed. Details and costs below.

## Where the cap belongs, in the code

### The prose path

`split_oversized` cuts at sentence boundaries and has no fallback (`chunker.py:537-571`). The failure is
in the loop:

```python
sentences = re.split(r"(?<=[.!?])\s+", text)          # chunker.py:559
...
for sentence in sentences:                            # chunker.py:562
    candidate = " ".join(buffer + [sentence])
    if buffer and estimate_tokens(candidate) > MAX_TOKENS:   # chunker.py:564
        pieces.append(" ".join(buffer))
        buffer = [sentence]
    else:
        buffer.append(sentence)
```

A 756 KB run with no `.!?` followed by whitespace is one element of `sentences`. On the first iteration
`buffer` is empty, so the `if` is False and the whole run is appended. There is no second check that
looks at the size of the buffer itself. I ran it on a 756,179-character single sentence and got one piece
back (Commands, C2).

The same function also relies on `estimate_tokens`, which is word count times a constant:

```python
def estimate_tokens(text: str) -> int:                # chunker.py:64
    return max(1, round(len(_WORD.findall(text)) * TOKENS_PER_WORD))
```

A 756,182-character string with no whitespace estimates as **1 token** (Commands, C3). So a cap written
only in estimated tokens cannot see the exact input that broke this, which is why the guard has to have a
character ceiling in it. The module already says the estimate is only a bound and not a measurement
(`chunker.py:31-34`), and the runbook says the real tokenizer belongs to the embedding contract
(`docs/runbook.md:1517-1518`). It is not there yet.

### The table path

The whole-table chunk is built with no size check at all:

```python
whole_text, column_names, rows = serialise_table(group, headings)   # chunker.py:493
...
chunks.append(Chunk(
    text=whole_text, page_start=min(span), page_end=max(span), kind="table",   # chunker.py:496-498
    doc_id=doc_id, source_sha256=source_sha256, table_id=table_id,
    ...
))
```

`split_oversized` is called from exactly one place, the prose loop (`chunker.py:634`). `grep -n
split_oversized services/ingestion/chunker.py` returns lines 537 and 634 and nothing else. I built a
3,000-row table, the shape of the 186 KB one in the index, and `chunk_document` returned a single
170,504-character table chunk at 40,486 estimated tokens (Commands, C4). The module's own docstring gives
the limit that chunk violates: the embedding model takes at most 8,192 tokens (`chunker.py:540-542`).

The row entries have the same gap. Row text is cells joined (`chunker.py:230-239`) and goes straight into
a `Chunk` at `chunker.py:512-517`. A single enormous cell produces an enormous row entry.

The design rule here is "a table that fits is never split" (`chunker.py:15`). It says nothing about a
table that does not fit, because the architecture assumed tables fit. The research already answers the
question the code does not: repeat the header on every piece when a table has to be split
(`docs/research/search-2026-10/07-chunking.md:453-459`).

### The smallest correct change

One character ceiling, enforced where text becomes a chunk, with the existing sentence split as the
preferred cut and a fallback that cannot fail. Concretely:

1. A new constant next to the token budget, for example `MAX_CHUNK_CHARS`, derived from `MAX_TOKENS` the
   same way `TOKENS_PER_WORD` is derived from the word count, and named so it can be replaced when a real
   tokenizer arrives (`chunker.py:46-54`).
2. In `split_oversized`: keep the sentence split. For any single sentence over the ceiling, cut at word
   boundaries into pieces under the ceiling. For any single word over the ceiling, hard-slice it. That is
   roughly ten lines inside the existing loop at `chunker.py:562-570`.
3. In the table loop: pack the rows that `serialise_table` already returns (`chunker.py:493`) into parts
   under the ceiling, repeat the heading prefix and the header row on each part, and take each part's page
   range from its own rows. `rows` already carries `(page_no, text, bbox)` per row
   (`chunker.py:337-342`), so this needs no new parsing. Roughly fifteen lines around `chunker.py:493-501`.
4. In the row loop: apply the same character split to a row that is over the ceiling. Keep `row_index` on
   every part, because it describes the row and the id already separates the parts by content hash
   (`chunker.py:103-105`).
5. In `sealed()`: assert the ceiling. If the assertion ever fires the algorithm has a bug, and the
   document should fail loudly rather than index a chunk nothing downstream can use. The code already
   prefers a failed document to a silent one (`services/ingestion/ingest.py:795-800`).

I would not make the ceiling a reason to drop the whole-table entry when it is over the limit. That
loses the "cite the table" affordance the architecture promises (`chunker.py:17-19`). Splitting with a
repeated header keeps it, and the docs then have to say a table that does not fit becomes several
citation units. That is a documentation change, not a code one.

### The answer path

There are two prompt builders, and neither bounds anything:

```python
context = "\n\n".join(f"[{n + 1}] {p.text}" for n, p in enumerate(passages))   # app.py:989
```
```python
context = "\n\n".join(f"[{n + 1}] {p.text}" for n, p in enumerate(result.passages))   # app.py:1318
```

`PASSAGES` is 6 by default (`app.py:132`). At the observed maximum chunk of 756,182 characters the
worst-case prompt is 6 × 756,182 = 4,537,092 characters, or about 1.13 million tokens at four characters
per token. The observed ~900 KB prompt is about 225,000 tokens. The code's own stated budget for the
question is a 32k-token window (`app.py:105-110`), so the observed prompt is about seven times the
budget the comment claims. The office build's local lane runs at `MODEL_CTX` 8,192
(`deploy/office/compose/docker-compose.yml:298`), where the observed prompt is about 27 times the window.

**The trim does not belong in `_generate`.** It belongs in `answer_module.prepare`, after retrieval
(`services/api/answer.py:196`), because everything downstream reads the same list:

- the citation array in the response is built from `result.passages` (`answer.py:271-273`);
- the audit row records `len(result.passages)` (`answer.py:290`);
- the citation verifier reads `passage.text` from those objects (`services/retrieval/citations.py:266`);
- the degraded response excerpts the same objects (`services/api/budget.py:221-231`).

`_generate` receives `result.passages` and `finish` receives `result` (`app.py:383-384, 398`), so a trim inside
`_generate` would bound the model's view while the response still cited, and the verifier still checked,
passages the model never saw. That is a wrong-answer-shaped bug, not a prompt-shaped one. Each route has to
get the same trimmed list, and today the streaming route builds its own prompt at `app.py:1318`, so a
change to `_generate` alone would miss it.

Two more details constrain the design. First, per-passage truncation must be reported, not silent. The
codebase already treats silent truncation as a defect elsewhere: `documents.py` reports `truncated` rather
than quietly returning the first 200 chunks (`services/api/documents.py:25-27, 105`). Second, the
question cap and the context cap have to be one budget. `MAX_QUESTION_CHARS` is 32,000 (`app.py:110`),
about 8,000 tokens, which fits a 32k window with room for passages and does not fit the office build's
8,192-token window at all.

### Why both, and in which order

The answer-path cap protects the model. It does not repair the index. The 756 KB chunk is still:

- embedded from whatever the embedding service did with 190,000 tokens of text, with no length check
  before the request (`ingest.py:480-485`) and the stated 8,192-token limit never enforced
  (`chunker.py:540-542`);
- sliced to 8,000 characters before reranking (`rerank.py:120`), so the reranker scores about one percent
  of it;
- one citation with a page range that spans far more than the text a person would check;
- one row in the lexical index, where its generated `tsvector` covers the same oversized text
  (`db/migrations/010_chunk_index.sql:41`).

An answer built from that passage is bounded but built on a passage no stage could read whole. So (b)
alone bounds the prompt and leaves the retrieval unit broken. (a) alone leaves the prompt unbounded for
the hours the re-chunk takes, and leaves no defence if a future chunker change reintroduces an oversized
chunk. Ship (b), then (a).

## What a re-chunk actually costs

### The id, and why it makes this expensive

```python
self.chunk_id = str(uuid.uuid5(uuid.NAMESPACE_URL, "|".join((
    self.doc_id, self.source_sha256, CHUNKER_VERSION, str(self.ordinal),
    content_hash(self.text)))))                        # chunker.py:103-105
```

`CHUNKER_VERSION` is `chunker-v2` (`chunker.py:59`) and is also written into the payload and the lexical
row (`chunker.py:113`, `ingest.py:1078`, `010_chunk_index.sql:34`). Ordinals are assigned after the whole
list is built (`chunker.py:528-530`), and the list is tables, then rows, then prose (`chunker.py:489-526`),
so changing one table's split shifts every later ordinal in that document as well as every id.

### A version bump alone does not re-ingest

The manifest never reads `chunker_version`. Its select lists `content_sha256` and `payload_version` and
nothing else that could stand for the chunking rules (`ingest.py:1009-1011`, `1022-1027`), and its upsert
writes the same fields (`ingest.py:1032-1051`). There is no `chunker_version` column: migration 009 adds
`payload_version` only, and its comment says the version is "deliberately separate from the chunker's"
(`db/migrations/009_payload_version.sql:1-20`).

The skip decision is here:

```python
same_bytes = bool(record) and record.get("content_sha256") == content_sha256     # ingest.py:1340
...
if same_bytes and (record.get("payload_version") or "") != PAYLOAD_VERSION:     # ingest.py:1344
    same_bytes = False
...
if same_bytes and failures == 0 and record.get("failure_kind") is None:         # ingest.py:1371
    result.skipped += 1
    continue
```

So a `CHUNKER_VERSION` bump on its own skips every unchanged file. It is worse than a no-op: the
embedding contract includes the chunker version (`services/ingestion/contract.py:45, 121`), and a mismatch
makes `check` raise unless the change is allowed (`contract.py:216-235`). The worker catches that at the
top of `main` and exits 1 before ingesting anything (`ingest.py:1751-1760`). A version bump alone stops
the worker.

The migration is therefore a three-part act, and all three parts are load-bearing:

1. `CHUNKER_VERSION` to `chunker-v3`, which changes every id.
2. `PAYLOAD_VERSION` to `payload-v6`, which is what makes the manifest re-derive documents whose bytes did
   not change (`ingest.py:1344-1348`, and the test at `tests/test_ingest.py:981-1000`).
3. `EMBED_CONTRACT_ALLOW_CHANGE=1` with `EMBED_CONTRACT_REASON="..."` for the first run only
   (`ingest.py:454-461, 1751-1753`), then unset.

### The documented way to invalidate

Corpus-wide, and only corpus-wide. `manual.md:2144-2171` ("Re-ingestion, and `PAYLOAD_VERSION`") and
`operator-guide.md:764-781` describe the bump and say it re-derives the whole corpus once.
`runbook.md:1609-1632` describes both constants together. The only per-document lever is
`--clear-failures` (`ingest.py:1623-1626, 1721-1732`), which resets the failure latch and does not
re-ingest. There is no `--reingest-document` and no documented "delete this manifest row" procedure. The
runbook's "If it breaks" says "clear the manifest or bump the version" (`runbook.md:1633`) without saying
how, which is a gap worth closing.

One documentation defect while I was here. `manual.md:2165-2167` says "a payload bump alone would have
reached nothing because the manifest would have seen identical bytes and skipped every file". That is
backwards. A payload bump is exactly what reaches them. The runbook has it right at
`runbook.md:1610-1614`: a fix without the payload bump reaches nothing.

### The migration set: all 341 documents, not 81

The 81 documents are the repair set, the documents that contain at least one of the 171 oversized chunks.
They are not the migration set, for three reasons that each force the whole corpus:

- the version is inside every id, so every chunk id changes even where the text does not (`chunker.py:103-105`);
- the payload version is per document and the bump marks every document stale (`ingest.py:1344-1348`);
- there is no per-document record of the chunker version to select on (`ingest.py:1009-1011`).

**What breaks, stage by stage.**

- *Citation verifier.* Nothing at verification time. It works on the in-memory passage list
  (`citations.py:203-240`), and the stored citation carries `doc_id`, `acl`, `page_start`, `page_end` and no
  chunk id (`answer.py:271-273`). Old answers stay resolvable. What changes is the text behind a page, and
  the range: a table that used to cite pages 7 to 9 becomes several parts, each citing its own page. An old
  citation's range no longer matches any single chunk, and nothing re-resolves it. If the answer-path cap
  truncates a passage, the verifier must see the truncated object, because it joins `passage.text`
  (`citations.py:266`). That is the argument for trimming in `prepare`.
- *ACL labels.* Nothing. The label is derived from the top-level folder on every ingest
  (`ingest.py:696-697`) and attached per chunk (`ingest.py:820`). Every registry table keys on `doc_id`:
  `document_versions.doc_id` and `duplicate_flags(doc_id, other_doc_id)`
  (`db/migrations/011_document_versions.sql:15-17, 41-54`). The label follows `doc_id`, and `doc_id` does
  not move.
- *The audit record and `payload_sha256`.* Nothing. `audit_events` has no chunk id
  (`db/migrations/002_audit_events.sql:18-38`), and the hash covers the detail payload
  (`services/audit/audit.py:304, 322`), which for a model call is counts, model, lane and redactions
  (`answer.py:289-296`). A re-chunk writes no rows and edits none, so no hash changes. The consequence runs
  the other way and is worse than a break: the record never held the passage text, by design
  (`budget.py:244-246`), so after the re-chunk an old row still verifies and can no longer be tied to the
  text that supported the answer. That is not repaired by a re-chunk.
- *Near-duplicate flags.* These do break, and nobody has flagged it. The signature is `md5(text)` per chunk
  read from `chunk_index` (`ingest.py:1104-1130`), so re-chunking moves every overlap ratio. The pair
  recorded at 90 percent (`docs/requirements.yaml:767-768`) may land on a different number, and a pair
  close to `DUPLICATE_OVERLAP_THRESHOLD` may cross it. Recorded `duplicate_flags` rows keep the old ratio.
  Re-run `--scan-duplicates` after the migration (`ingest.py:1613-1616`).
- *Counts.* `ingest_manifest.chunk_count` rises for the affected documents (`ingest.py:1492-1497`), and the
  split adds chunks. Expect a few hundred more than 21,188, not fewer.

**The partial-state risk.** The contract is recorded at the top of the run, before any document is
ingested (`ingest.py:1748-1760`). If the cycle dies halfway, the contract says `chunker-v3` while half the
corpus is still v2, and nothing detects that afterwards: the reader compares the recorded contract with
the live one (`retriever.py:611` calls `verify_contract`), not with each chunk's own version. So an
interrupted migration is invisible. The mitigation is operational: run it to completion, and verify with a
chunk-size query before re-enabling traffic.

**Failure safety, which is better than I expected.** Parse, chunk and embed all happen before any delete.
`future.result()` is called first, and only then `write_document`, which deletes the old chunks
(`ingest.py:1426-1431, 1451-1453`). So a parse or embedding failure leaves the old chunks in place. A
failure between the delete and the upsert can leave a document empty, and `record_failure` deliberately
stores the old `payload_version` (`ingest.py:1522`), so the next cycle sees the mismatch and retries it.

### Time

I did not have the current ingest log, so these are the measured rates recorded in the docs, and the
arithmetic is shown so it can be redone with the deployment's own numbers. If the log gives a rate of R
chunks per second, the embedding stage is 21,188 / R seconds and the wall clock is roughly double that.

- embedding service: **2.7 chunks/sec at 2,000-character chunks, 5.5 at 200** (`operator-guide.md:405-408`);
- worker end to end on the reference corpus: about **0.8 chunks/sec** (`operator-guide.md:421`);
- whole-corpus re-ingest: **10,026 documents in 1h36m** (`runbook.md:1615`, `manual.md:2155-2156`);
- those reference documents averaged **1.28 chunks each** (`runbook.md:4428`).

The current corpus averages 21,188 / 341 = **62 chunks per document**. That is why the document-rate
figure must not be scaled by document count: 341 documents at 72 documents/min is five minutes, against
two to four and a half hours, so that scaling is wrong by a factor of about 50.

Embedding alone, 21,188 chunks:

- at 2.7 chunks/sec: 7,848 s, about **2 h 11 m**;
- at 5.5 chunks/sec: 3,852 s, about **1 h 04 m**.

Parsing is recorded at 44 to 47 percent of an ingest's wall clock with embedding at 50 to 53
(`ingest.py:52-54`), so wall clock is roughly double the embedding time: **2 to 4.5 hours** for all 341
documents. The split adds chunks, so round upward rather than down.

The surgical alternative, if someone deletes the 81 manifest rows without a version bump, is 81 of 341
documents, roughly 5,000 chunks, about **15 to 31 minutes** of embedding on the same arithmetic. It buys
that time with a lie: those chunks would be produced by the new rules and still carry
`chunker_version = chunker-v2` (`chunker.py:113`, `010_chunk_index.sql:34`), and the contract would stay
truthful only because nobody moved it (`contract.py:45`). I would not do that in a product whose selling
point is a record you can audit.

**The other cost is operational.** The worker will get 429s if the re-chunk runs beside the fifteen-minute
cycle. That is RET-INGEST-002, found on 2026-10-16, still open, with the doc fix declined by the owner
(`docs/requirements.yaml:708-727`). Run it as `oi restart ingestion` or with the timer stopped, one
writer, at `INGEST_CONCURRENCY=1` as the recovery was, and with `INGEST_EMBED_BATCH` below the 16 that
sheds load under contention (`ingest.py:62-64`).

## Tests and checks

**What already guards this, and what it actually proves.**

- `tests/test_chunker.py:236-256` proves an item over the budget is split. It passes because its fixture is
  90 copies of a sentence ending in a period (`test_chunker.py:244`), so the sentence splitter has
  somewhere to cut. It does not test the no-punctuation case.
- `tests/test_chunker.py:259-265` pins the sentence-boundary rule. This stays green after a character
  fallback only because its input has periods. It is the test that would have to be read carefully when the
  rule changes.
- `tests/test_chunker.py:142-163` proves ids are deterministic and that a version change moves them.
- `tests/test_chunker.py:192-198` pins `estimate_tokens` to exact values. A real tokenizer changes this
  test, which is correct.
- `tests/test_ingest.py:981-1000` proves a payload bump re-ingests an unchanged file, and
  `test_ingest.py:1003-1016` proves a pre-version row is treated as stale. These are the tests that make
  the migration mechanism trustworthy.
- `tests/test_embed_contract.py:32` includes `chunker_version` in the contract fixture, so the contract's
  dependence on it is at least represented.
- `tests/test_api_contract.py:290-320` caps the question and asserts both endpoints check it. It is the
  model for a context-cap test, including the ordering argument.

**What is missing.**

- No test that any chunk `chunk_document` returns is under a character ceiling, on any of its paths.
- No test for `split_oversized` on a single sentence with no terminal punctuation (C2 is the demonstration
  that this is the defect).
- No test that `estimate_tokens` is blind to a no-whitespace run. The current test asserts exact values, so
  it documents the estimator rather than bounding its blind spot.
- No test that the prompt is bounded, and no test that the citations match what was actually sent after a
  trim.
- No operator check on index shape. `--status` reports counts, not sizes (`ingest.py:1568-1586`), and the
  healthcheck is exit 0 when no document failed (`services/ingestion/service.yaml:10-16`). Nothing in
  `scripts/` measures chunk length. The 171 chunks were found by querying the index, not by a check.
- No requirement row for "a passage fits the model's window". RET-EMBED-001 covers the embedding model's
  contract, not the chunk's size against the embedder's limit (`requirements.yaml:525-551`). A new row is
  needed, and RET-HEAD-001 is the pattern for one that records a version bump with the fix
  (`requirements.yaml:841-870`).

**What a fix forces.** The docstring at `chunker.py:551-552` says "sentences rather than characters, because
a citation that quotes half a sentence reads as a misquote". That sentence has to be amended, because the
fallback cuts mid-sentence by design and says so. The runbook (`runbook.md:1423, 1519-1530`) and
`docs/search-architecture.md:19` state a 384 to 512 token ceiling as though it were enforced, and both
need to say what actually enforces it.

## Options, scored

Scores are my judgement, 1 to 5, higher is better. Weights: prompt bounded 1, retrieval unit sound 2,
provenance intact 2, interim protection 1, migration cheap 1, deployment risk low 1, engineering small 1.
Maximum 45. The "repairs the index" column is the one I would read first.

| Option | Prompt | Retrieval | Provenance | Interim | Migration | Risk | Effort | Total | Repairs the index |
|---|---|---|---|---|---|---|---|---|---|
| (a) chunker cap plus re-chunk | 4 | 5 | 4 | 1 | 2 | 3 | 4 | **32** | yes |
| (b) answer-path cap only | 5 | 1 | 5 | 5 | 5 | 4 | 4 | **35** | no |
| (c) both, answer path first | 5 | 5 | 4 | 5 | 2 | 3 | 3 | **36** | yes |
| (d) off-the-shelf chunker | 3 | 4 | 3 | 1 | 1 | 2 | 2 | **23** | yes, differently |

(b) scores high because it is cheap, and that is the problem with it, not a virtue. It is the right first
move and the wrong only move.

### (a) Cap in the chunker plus re-chunk

- **Changes:** `chunker.py` (the fallback, the table parts, the row cap, the seal assertion),
  `CHUNKER_VERSION`, `PAYLOAD_VERSION`, tests, runbook, manual 13a, `search-architecture.md:19`.
- **Migration:** all 341 documents, all 21,188 chunks, plus the new parts. 2 to 4.5 hours. One contract
  generation with a reason.
- **Risk:** a mixed-version corpus for the length of the run, no blue/green (see next section), duplicate
  ratios shift, and the 429 hazard if it runs beside the cycle.
- **Still broken:** nothing in the index, but no defence at the model boundary. Six passages at the new
  ceiling are about 3,000 tokens, which fits, so the prompt is bounded by consequence rather than by a
  check.

### (b) Cap in the answer path only

- **Changes:** `answer.py` `prepare`, a per-passage and total limit, a `truncated` fact carried into the
  payload, tests. No schema change, no re-ingest.
- **Migration:** none. Deploy and restart the API.
- **Risk:** low, and the trim is visible to citations and the audit only if it happens in `prepare` rather
  than `_generate`.
- **Still broken:** the retrieval unit itself. The vector for the 756 KB chunk represents whatever the
  embedder did with it, the reranker sees 8,000 characters of it, the lexical arm indexes all of it, the
  document viewer serves all of it, and the citation page range still spans it.

### (c) Both, answer path first

- **Changes:** (b) now, (a) next. Two deploys, one migration.
- **Cost:** the same 2 to 4.5 hours as (a), plus an hour of engineering and a deploy for (b).
- **Risk:** the two limits have to agree. If the answer-path ceiling is smaller than the chunk ceiling the
  trim is silent unless it is reported, and the chunk ceiling is what the index is built to.
- **Still broken:** nothing this defect names. The window before the re-chunk is covered by (b), and the
  index is repaired by (a).

### (d) Replace the chunker with an off-the-shelf one

Docling ships `HybridChunker`, which splits oversized chunks and repeats table headers on every piece
(`docs/research/search-2026-10/07-chunking.md:115, 459`). The repo already has a comparison harness for
Docling's chunkers (`tests/fixtures/chunker_comparison.py`), and the research calls `PageChunker` a
one-line swap (`07-chunking.md:714`).

- **Changes:** replace `chunker.py`, map the library's chunks onto the payload contract
  (`chunker.py:108-133`), and reimplement or lose what the contract promises: `kind` values including
  `table_row`, `table_id`, `row_index`, `page_start`/`page_end`, bbox, `prev_chunk_id`/`next_chunk_id`.
  `DocumentChunk` and `Document` in `services/api/models.py:374-400` are a published contract, so this is
  an API change too. Ids would still be sealed by our own `uuid5` scheme, because that is what makes a
  re-run idempotent (`chunker.py:99-105`), so the sealing code stays either way.
- **Migration:** all 341 documents and all chunks re-embedded, the same 2 to 4.5 hours, plus engineering,
  plus a re-baselined eval set (`RET-EVAL-001`). The comparison harness needs `DoclingDocument` validation,
  and it notes that ours works on the raw JSON while theirs needs the model
  (`tests/fixtures/chunker_comparison.py:91-94`).
- **Risk:** high. It changes the citation unit, the payload contract and the row-level retrieval that
  RET-HYBRID-001 and RET-HEAD-001 depend on (`requirements.yaml:619-644, 841-870`).
- **What it buys:** the size guard, which is about ten lines in our code. The chunking research says there
  is no measured optimum in this range (`chunker.py:546-549`), so there is no quality argument for the
  swap, only a maintenance one. It also fixes nothing in the answer path.

**Verdict: (c).** Ship (b) as a one-hour deploy, then (a). Do not do (d) for this defect.

## What cannot be done here, and why

These are the places where the professional answer needs something the architecture does not have. I am
naming them rather than proposing a workaround for each.

1. **An atomic re-index.** There is one collection for the vector arm (`COLLECTION`,
   `deploy/office/compose/docker-compose.yml:219`), and the lexical arm has no collection column at all
   (`010_chunk_index.sql:22-42`). Its query reads `chunk_index` globally with only an ACL filter
   (`retriever.py:343-370`). Build a second collection and the lexical arm fuses ids from both chunkings,
   the dense arm cannot fetch the old ids, and those hits are counted as withheld (`retriever.py:669-675`).
   Blue/green is expressible for half the retrieval stack and not the other half.
2. **A mixed-version corpus that is honestly described.** The contract records one `chunker_version` per
   collection (`contract.py:45, 121`), and it is written before the cycle starts (`ingest.py:1748-1760`).
   So a partial migration either makes the recorded contract false or is invisible. There is no third
   option without a per-chunk version check on the read path.
3. **Per-document re-chunk provenance.** `ingest_manifest` has no `chunker_version` column
   (`ingest.py:1009-1011`, `009_payload_version.sql`), so the worker cannot answer "was this document
   chunked under the current rules". A version bump is corpus-wide by construction. Adding the column is
   easy. Making the skip decision use it does not help, because the worker cannot know whether a
   document's chunking actually changed without re-chunking it.
4. **A stable chunk identity across chunker versions.** The id includes the version on purpose, and the
   module says why (`chunker.py:56-58, 99-105`). That is right for idempotency and wrong for incremental
   migration. The cheap repair wants the one thing the design refuses: ids that survive a rule change.
5. **Proof of what supported a past answer.** The record holds counts, never passages
   (`budget.py:244-246`, `answer.py:289-296`). After the re-chunk, an old audit row still verifies and can
   no longer be tied to the text behind the answer. No re-chunk fixes that, because the evidence was never
   written. If that evidence is wanted, it is a content-mode decision and a new audit payload, not a
   chunking change.
6. **A token-exact ceiling.** The estimator is words times a constant and is documented as one
   (`chunker.py:31-34, 54, 64-66`). It returns 1 for a 756 KB unbroken run. The real tokenizer is named as
   a future part of the embedding contract (`runbook.md:1517-1518`) and is not there. Every "384 to 512
   tokens" statement in the docs is an approximation today, which is why the new ceiling has to be in
   characters.
## Sources (files, line numbers, commands)

### Code

| Claim | File:line |
|---|---|
| Token budget, 384 to 512, estimate constant | `services/ingestion/chunker.py:46-54` |
| Chunker version is `chunker-v2` | `services/ingestion/chunker.py:59` |
| `estimate_tokens` is whitespace-based | `services/ingestion/chunker.py:64-66` |
| Chunk id covers doc, sha, version, ordinal, text hash | `services/ingestion/chunker.py:103-105` |
| Version is in the payload | `services/ingestion/chunker.py:108-133` |
| "A table that fits is never split" | `services/ingestion/chunker.py:15-19` |
| Whole-table chunk, no size guard | `services/ingestion/chunker.py:493-501` |
| Row entries, no size guard | `services/ingestion/chunker.py:502-517, 230-239` |
| Ordinals assigned after the list is built | `services/ingestion/chunker.py:528-530` |
| `split_oversized`, sentence split only | `services/ingestion/chunker.py:537-571` |
| Oversized item path in prose | `services/ingestion/chunker.py:621-641` |
| `split_oversized` called once, prose only | `services/ingestion/chunker.py:634` |
| Stated embedding limit, 8,192 tokens | `services/ingestion/chunker.py:540-542` |
| Payload version is the manifest lever | `services/ingestion/ingest.py:130, 1344-1348` |
| Manifest reads and writes no chunker version | `services/ingestion/ingest.py:1009-1011, 1029-1051` |
| Skip on same bytes and no failures | `services/ingestion/ingest.py:1340, 1371-1373` |
| Parse and embed before any delete | `services/ingestion/ingest.py:1426-1431, 1451-1453` |
| Failure keeps the old payload version | `services/ingestion/ingest.py:1522` |
| Embedding request has no length check | `services/ingestion/ingest.py:464-490` |
| Embedding runs per document, in batches of 16 | `services/ingestion/ingest.py:49, 810-839` |
| Concurrency 4, batch 16, 429 at 16 | `services/ingestion/ingest.py:49, 62-67` |
| Lexical upsert and delete | `services/ingestion/ingest.py:1070-1097` |
| Near-duplicate signature is `md5(text)` | `services/ingestion/ingest.py:1104-1130` |
| `--clear-failures` is the only per-document lever | `services/ingestion/ingest.py:1623-1626, 1721-1732` |
| Contract check at startup, opt-in for a change | `services/ingestion/ingest.py:454-461, 1743-1760` |
| Contract includes `chunker_version` | `services/ingestion/contract.py:45, 116-121, 169-235` |
| Two prompt builders, no cap | `services/api/app.py:989, 1318` |
| Six passages | `services/api/app.py:132` |
| Question cap sized for 32k | `services/api/app.py:105-110` |
| `_generate` and `finish` take the same list | `services/api/app.py:383-384, 398` |
| Retrieval call, 6 passages | `services/api/answer.py:196` |
| Citations and audit count from `result.passages` | `services/api/answer.py:261, 271-273, 289-296` |
| Verifier joins full passage text | `services/retrieval/citations.py:266, 203-240` |
| Lexical query reads `chunk_index` with no collection filter | `services/retrieval/retriever.py:343-370` |
| Missing payload counted as withheld | `services/retrieval/retriever.py:666-675` |
| Reranker slices to 8,000 chars | `services/retrieval/rerank.py:120` |
| Reranker cost is per token, cap is per count | `services/retrieval/rerank.py:15-17, 32-40` |
| Degraded excerpts sliced to 240 chars | `services/api/budget.py:221-231, 244-246` |
| Document viewer reports truncation | `services/api/documents.py:25-27, 105` |
| Local lane window 8,192 | `deploy/office/compose/docker-compose.yml:298` |
| Chunk index schema, `chunk_id` primary key, `chunker_version` | `db/migrations/010_chunk_index.sql:22-42` |
| Manifest schema, no chunker version | `db/migrations/007_ingest_manifest.sql`, `009_payload_version.sql` |
| Registry tables key on `doc_id` | `db/migrations/011_document_versions.sql:15-17, 41-54` |
| Audit table has no chunk id | `db/migrations/002_audit_events.sql:18-38` |
| `payload_sha256` covers the detail payload | `services/audit/audit.py:304, 322` |

### Docs and register

| Claim | File:line |
|---|---|
| 384 to 512 is an enforced ceiling (it is not) | `docs/search-architecture.md:19` |
| Two constants, both load-bearing | `docs/runbook.md:1609-1632` |
| Whole-corpus re-ingest, 10,026 in 1h36m | `docs/runbook.md:1615`, `docs/manual.md:2155-2156` |
| Reference documents average 1.28 chunks | `docs/runbook.md:4428` |
| Embedding 2.7 chunks/sec at 2,000 chars, 5.5 at 200 | `docs/operator-guide.md:405-408` |
| Worker about 0.8 chunks/sec | `docs/operator-guide.md:421` |
| Re-ingestion is corpus-wide | `docs/manual.md:2144-2171`, `docs/operator-guide.md:764-781` |
| Manual inverts the version mechanism | `docs/manual.md:2165-2167` |
| RET-EMBED-001, the contract | `docs/requirements.yaml:525-551` |
| RET-INGEST-001, idempotency | `docs/requirements.yaml:681-706` |
| RET-INGEST-002, the two-writer 429, open | `docs/requirements.yaml:708-727` |
| RET-HEAD-001, the version-bump precedent | `docs/requirements.yaml:841-870` |
| Table split and repeated headers | `docs/research/search-2026-10/07-chunking.md:453-459` |
| `HybridChunker` splits oversized chunks | `docs/research/search-2026-10/07-chunking.md:115` |
| `PageChunker` as a one-line swap | `docs/research/search-2026-10/07-chunking.md:714` |
| Comparison harness covers two of Docling's chunkers, not `HybridChunker` | `tests/fixtures/chunker_comparison.py:95-96` |

### Commands I ran

C1. The existing chunker tests. All green, which is the point.

```
$ cd /home/anthony/projects/office-inference-core
$ python3 -m pytest tests/test_chunker.py -q
.....................                                                    [100%]
21 passed in 0.06s
```

C2. `split_oversized` on one 756 KB sentence with no terminal punctuation.

```
$ python3 - <<'PY'
import sys; sys.path.insert(0, "services/ingestion"); import chunker
text = ("word " * 151236).strip()
print("chars:", len(text), "estimated tokens:", chunker.estimate_tokens(text))
pieces = chunker.split_oversized(text, 1, {})
print("pieces from split_oversized:", len(pieces))
print("largest piece chars:", max(len(p) for p in pieces))
PY
chars: 756179 estimated tokens: 204169
pieces from split_oversized: 1
largest piece chars: 756179
```

C3. The estimator is blind to a no-whitespace run.

```
$ python3 -c 'import sys; sys.path.insert(0,"services/ingestion"); import chunker; \
print(chunker.estimate_tokens("x" * 756182))'
1
```

C4. An unbounded table chunk from `chunk_document`, on a 3,000-row synthetic table.

```
$ python3 - <<'PY'
import sys; sys.path.insert(0, "services/ingestion"); import chunker
... 3,000 rows x 3 columns, one table ...
chunks = chunker.chunk_document(doc, "doc", "sha")
tables = [c for c in chunks if c.kind == "table"]
print("table-kind chunks:", len(tables))
print("whole-table chars:", len(tables[0].text))
print("whole-table estimated tokens:", chunker.estimate_tokens(tables[0].text))
PY
table-kind chunks: 1
whole-table chars: 170504
whole-table estimated tokens: 40486
```

C5. The prompt arithmetic, from the code's own constants.

```
mean 716 chars, max 756,182 chars, 21,188 chunks
worst case 6 x 756,182 = 4,537,092 chars ~ 1,134,273 tokens at 4 chars/token
observed ~900 KB ~ 225,000 tokens = 7.0 x a 32k window, 27.5 x the office 8,192 window
```

C6. Greps that establish call sites and consumers:

```
$ grep -n split_oversized services/ingestion/chunker.py
537:def split_oversized(text: str, page_no: int, bbox: dict) -> list[str]:
634:            for piece in split_oversized(text, page_no, bbox):
$ grep -rn "chunk_id" services/audit/ services/api/activity.py services/api/approvals.py
(no output: no audit, activity or approval code names a chunk)
$ grep -rn "payload_sha256" services/audit/audit.py db/migrations/*.sql
services/audit/audit.py:304, 322, 374, 550, 580, 700, 704, 705
db/migrations/002_audit_events.sql:27, 35
db/migrations/013_chain_covers_redaction.sql:33
```

### Not run

The index figures. The deployment is remote and this was research only, so nothing here queried it. The
21,188 chunks, 716-character mean, 756,182-character maximum and 171 chunks over 4 KB across 81 of 341
documents are the brief's figures, not mine. These are the queries that would reproduce them, and the
first thing to run before scheduling a migration:

```sql
select count(*), round(avg(length(text))), max(length(text)) from chunk_index;
select count(*) from chunk_index where length(text) > 4096;
select count(distinct doc_id) from chunk_index where length(text) > 4096;
select count(distinct doc_id) from chunk_index;
select doc_id, length(text) from chunk_index order by length(text) desc limit 5;
select payload_version, count(*) from ingest_manifest group by 1;
-- the embedding service's actual limit for this collection:
select contract->>'max_input_length', contract->>'auto_truncate'
  from collection_contracts where collection = 'documents';
```

That last query settles a question I could not answer from the repository: whether the 756 KB chunk was
embedded truncated or accepted whole. Either way it should not exist, but the answer says whether the
index is also silently mis-vectored.
