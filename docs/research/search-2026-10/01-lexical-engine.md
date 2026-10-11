# The lexical half of hybrid search, run in Postgres

**Angle:** where the keyword side of hybrid retrieval should live, given that page-level citations
are required, identifiers are the failure we are fixing, and the office build ships to client
hardware with no internet.

Written 2026-10-11. Every number marked **measured** was produced on this machine during this
research; the commands and the harness are in section 11. Everything else is
labelled **vendor claim**, **published**, or **my inference**.

---

## 0. The answer

**Build the lexical arm on Postgres native full-text search, fix the tokenizer, and add an exact
string check in the citation verifier. Do not add a BM25 extension to the office build yet, and do
not add a Lucene sidecar at all.**

Three findings drive that, and only the first is the one we expected.

**1. BM25 does not beat `ts_rank` on the failure we are actually fixing.** On 60 planted identifiers
in a 200,000-chunk corpus, `ts_rank` found the correct chunk first 60 times out of 60. ParadeDB's
BM25 found it 60 out of 60. vchord_bm25 found it 60 out of 60. **measured**. When the query is the
whole identifier, the ranking function is not the bottleneck, because the identifier is rare enough
that almost nothing else matches.

**2. The bottleneck is tokenisation, and it is upstream of the ranking function.** Postgres' default
parser splits `INV-2024-04417` into `inv`, `-2024`, `-04417`. The identifier is destroyed before any
ranking function sees it. The consequence is specific and bad: a user who types the trailing digits
`04417` gets **zero hits**, because the indexed lexeme is `-04417` with the hyphen attached.
**measured**. ParadeDB is the only engine tested that lets you choose a tokenizer that keeps
`INV-2024-04417` as one token, and it is the only engine that scored 60 out of 60 on the partial
identifier. **measured**.

**3. The trigram result is not what the folklore says.** The `%` operator, which compares two whole
strings, scored **0 out of 60** on full identifiers and **0 out of 200** on short documents, because
a 12-character query against a 1,300-character chunk has almost no trigram overlap. But
`word_similarity`, which compares the query to the best-matching word-boundary window inside the
document, scored **189 out of 200 at rank 1** on trailing-digit queries, exactly matching BM25.
**measured**. The trigram index is more useful than the standard advice suggests, but only through
the operator nobody uses, and it costs 13 seconds per query on 1,300-character chunks. **measured**.

**What to ship:**

- `tsvector` with the `simple` configuration (no stemming), weighted with `setweight`, GIN-indexed.
  That is the lexical arm.
- A **trigger-maintained identifier column**: extract identifiers with a regex during ingestion,
  store them lowercased and separated, keep a second GIN index on that column. This is the fix for
  the tokenisation problem, it is cheap, and it makes partial identifiers work without a BM25
  extension. **my inference, from the tokenisation measurements in §2.5**.
- A **literal-substring assertion** in the citation verifier: if the answer cites an identifier,
  check the characters exist in the retrieved page text. This turns the whole class of identifier
  hallucination into a refusal, and it is the only mechanism here that is deterministic rather than
  scored. **my inference**.
- `pg_trgm` installed, with `word_similarity` available, reserved for a **reduced candidate set**
  after the lexical and vector arms have run. Not as a first-pass index scan.

**Revisit BM25 when a licence-clean extension is worth the container.** `pg_textsearch`
(Tiger Data) is PostgreSQL-licensed, which is the cleanest licence in this entire document, and it
is the right answer the moment we want real BM25. It is new, it needs a `shared_preload_libraries`
entry, and it supports Postgres 17 and 18 only. Details in §4.3.

---

## 1. How the options were assessed

Each candidate was installed in a container and put through the same three things.

**A corpus.** 200,000 chunks, 254 MB of synthetic business text, six chunks per document, pages 1 to
400, with a Zipf-skewed legal and commercial vocabulary and real identifier shapes embedded in every
chunk: `INV-2026-55431`, `74LS00`, `3:15-bk-62666`, `ACCT-1987-9177`, `6744-8680-3265`, and surnames.
Sizes: 271 MB of heap, 1,187 bytes of stored `tsvector` per chunk.

**An identifier test.** 60 identifiers planted into 60 known chunks, then four query styles. The
metric is where the known-correct chunk lands: hits@1, hits@10, MRR@10. A miss means not in the top
ten at all.

**A controlled ranking test.** Hand-built tables isolating one property at a time, so the difference
between `ts_rank` and BM25 can be attributed rather than asserted.

**Host.** 8 vCPU, 16 GB RAM, four Postgres 18.6 containers running concurrently. Absolute timings are
therefore pessimistic, roughly a factor of two to four on a dedicated 16 vCPU box. Relative timings
are meaningful. Say so in the decision document.

**The test harness is wrong in one specific way, and it matters.** The synthetic corpus repeats a
small vocabulary far more than real prose does, so any single term matches thousands of chunks. That
is what produced the surname result in §7, where every engine failed and the failure was the
corpus, not the engine. Real client documents are far more diverse. Treat the identifier numbers as
sound and the surname numbers as an artefact.

---

## 2. Postgres native full-text search

### 2.1 What it actually is

`to_tsvector` runs a parser that classifies tokens, then a dictionary chain that stems and removes
stop words, producing a sorted list of lexemes with positions and a weight letter (A/B/C/D).
`to_tsquery` and its friends build the query. A GIN index inverts the lexemes. `ts_rank` and
`ts_rank_cd` score.

### 2.2 Licence

The PostgreSQL License, from the project's own `COPYRIGHT` file. Permissive, MIT-like, no copyleft,
no network clause, no commercial restriction. Verified by fetching
<https://raw.githubusercontent.com/postgres/postgres/master/COPYRIGHT>, HTTP 200, 1,198 bytes,
sha256 prefix `3d6af92ff8a4c2cd`. `pg_trgm` ships in core `contrib` under the same terms; its
control file is at
<https://raw.githubusercontent.com/postgres/postgres/master/contrib/pg_trgm/pg_trgm.control>.

**Nothing else in this document has a licence this clean.** That is a real advantage and it should
carry weight.

### 2.3 What `ts_rank` computes, from the source

From `src/backend/utils/adt/tsrank.c` in PostgreSQL, `REL_18_STABLE`
(<https://raw.githubusercontent.com/postgres/postgres/REL_18_STABLE/src/backend/utils/adt/tsrank.c>),
the default weights are:

```c
static const float default_weights[NUM_WEIGHTS] = {0.1f, 0.2f, 0.4f, 1.0f};
```

`calc_rank_or` computes, per query term:

```
resj = sum over occurrences j of  w[weight(position j)] / (j+1)^2
res += (w_max + resj - w_max/(jm+1)^2) / 1.64493406685      /* pi^2/6 */
res = res / number_of_query_terms
```

`calc_rank_and` combines matched terms with `1 - prod(1 - sqrt(w_i w_j word_distance(d)))`.

Read the function signature and the missing input is obvious: **it takes a `TSVector` and a `TSQuery`
and nothing else.** There is no corpus argument, so there is no document frequency, so **there is no
IDF.** `ts_rank_cd` is the same story: it computes cover density over positions and calls the same
normalisation helper. Neither has document-length normalisation by default; `DEF_NORM_METHOD` is
`RANK_NO_NORM`. Normalisation is opt-in through the `normalization` integer argument.

Two consequences, both measured.

### 2.4 Term-frequency saturation and length normalisation

Saturation: **BM25 and `ts_rank` are close enough to be indistinguishable for ranking**, and
`ts_rank_cd` is far worse. 50 documents, `widget` repeated 1, 2, 5, 20 and 50 times in otherwise
identical text. **measured**:

| term frequency | `ts_rank` | `ts_rank_cd` | BM25 (pg_search) |
|---|---|---|---|
| 1 | 0.06079 | 0.10000 | 0.01101 |
| 2 | 0.07599 | 0.20000 | 0.01461 |
| 5 | 0.08898 | 0.50000 | 0.01797 |
| 20 | 0.09704 | 2.00000 | 0.02040 |
| 50 | 0.09880 | 5.00000 | 0.02096 |

`ts_rank`'s 1-to-50 ratio is 1.626; BM25's is 1.904. Both saturate. I verified the `ts_rank` column
arithmetically against the source formula: for one occurrence the prediction is
`1/1.64493406685 = 0.60792710`, and with one weight letter applied that is `0.6079271 × 0.1 =
0.06079271`, which is the measured 0.06079. The formula reproduces exactly.

**On saturation, `ts_rank_cd` is the trap.** It scales linearly with term frequency, 0.1 to 5.0 across the same
range, and its top result is a fifty-repetition document. Do not use `ts_rank_cd` for ranking. This
is a correction to the common advice that `ts_rank_cd` is the higher-quality of the two.

Length normalisation, 50 short documents against 50 long ones, each mentioning `escrow` once.
**measured**:

| document length | `ts_rank` default | `ts_rank` normalization=1 | BM25 |
|---|---|---|---|
| ~5 words | 0.06079271 | 0.03039636 | 0.00836966 |
| ~1,205 words | 0.06079271 | 0.00758316 | 0.00357356 |
| ratio | **1.00** | 4.01 | 2.34 |

The default `ts_rank` cannot tell a two-word document from a twelve-hundred-word one. This is a real
defect and it is why a 400-word policy paragraph can outrank a four-word table row that contains the
answer. **Pass `normalization`, or normalise outside SQL.** Do not leave it at the default.

### 2.5 What it does to identifiers, which is the actual problem

Tokenising `Invoice INV-2024-04417 for part 74LS00 and case 3:21-cv-01234`, **measured**:

```
english : '-01234':13 '-04417':4 '-2024':3 '21':11 '3':10 '74ls00':7 'case':9 'cv':12 'inv':2 'invoic':1 'part':6
```

The hyphens migrate onto the following numeric token. `INV-2024-04417` is not in the index. Its
fragments are. That yields four distinct failure modes.

| User types | Result | Evidence |
|---|---|---|
| `INV-2024-04417` | Matches, because `to_tsquery` builds a phrase `'inv' <-> '-2024' <-> '-04417'` and all three are present | **measured**: `plainto_tsquery` and `phraseto_tsquery` both match |
| `04417` (trailing digits, no hyphen) | **No match at all.** The lexeme is `-04417` | **measured**, `plainto_tsquery('english','04417')` against that tsvector returns false |
| `74LS` for part `74LS00` | No match. Prefix matching is only available with an explicit `:*`, and only from the start of a lexeme | **measured**: `74LS` false, `74LS:*` true |
| `Stachow` for `Stachowitz` | No match, same reason | **measured** |

This is the failure we are fixing, and it lives in the parser, not in the ranking function. No BM25
extension fixes it by itself; §4.1 shows the same truncation failure in a BM25 engine.

### 2.6 Cost, measured

| | 200k chunks | extrapolated 1M |
|---|---|---|
| Heap, text only | 271 MB | 1.35 GB |
| Stored `tsvector` column, 1,187 B/chunk | +237 MB | +1.19 GB |
| GIN index on the stored `tsvector` | 51 MB, 34.5 s to build | ~255 MB, ~3 min |
| GIN trigram index on `body` | 146 MB, 91.2 s to build | ~730 MB, ~8 min |
| Table total with column and both indexes | 670 MB | ~3.5 GB |

A 16 vCPU, 64 GB box carries this with room to spare. Ingest cost is a `to_tsvector` call per chunk
inside the existing Docling pipeline, which is CPU work already budgeted.

### 2.7 Verdict

**Adopt, with three changes.** Use the `simple` configuration for the identifier column so nothing
gets stemmed away. Pass `normalization`. Add the identifier column and the exact-match assertion
described in §0. Do not use `ts_rank_cd`.

---

## 3. `pg_trgm` trigram matching

### 3.1 What it actually is

`pg_trgm` decomposes text into trigrams, padded so that short strings still produce a few, and
indexes them with GIN or GiST. Similarity is `shared trigrams / total distinct trigrams`. The `%`
operator filters on `pg_trgm.similarity_threshold`, default 0.3. `word_similarity` compares the
query against the best-matching word-boundary window inside the document, with its own threshold
`pg_trgm.word_similarity_threshold`, default 0.6.

### 3.2 Licence

PostgreSQL License, same as core. Verified above.

### 3.3 The `%` operator is the wrong tool, and the failure is total

This is the headline correction to the folklore that "trigram fixes identifier search."

Against the 200,000-chunk corpus, 60 planted identifiers, **measured**:

| Operator | hits@1 | hits@10 | median latency |
|---|---|---|---|
| `body % query`, ordered by `similarity` | **0/60** | **0/60** | 739 ms |
| `query <% body`, ordered by `word_similarity` | not run at scale | — | see below |

Against 200 short documents each containing one identifier, **measured**:

| Operator | full identifier, hits@1 | trailing digits, hits@1 |
|---|---|---|
| `similarity` via `%` | **0/200** | **0/200** |
| `word_similarity` | 190/200 | **189/200** |
| `strict_word_similarity` | 190/200 | 189/200 |
| `ts_rank` | 190/200 | 40/200 |
| BM25 (pg_search) | 190/200 | 189/200 |

Read the table carefully, because it is the most useful single result here.

`similarity` compares the whole query to the whole document. A 12-character query against a
1,300-character chunk shares almost no trigrams: **`similarity('INV-2024-04417', <1,300-char chunk>)`
can never reach 0.3, so the chunk is not a candidate at all.** The index returns nothing. This is
geometric, not tunable. Lowering the threshold to catch it would make the index useless.

`word_similarity` fixes exactly that by scoring the best word-boundary window. It matches BM25
**exactly** on trailing-digit queries, 189/200 at rank 1. That is a genuine finding and it changes
the recommendation for trigram from "don't bother" to "use it, but only through `<<%`".

### 3.4 What trigram costs, measured

Short strings are the weak case. `04417` yields six trigrams: `{"  0"," 04",044,"17 ",417,441}`.
Three of the six are padding. `74LS` yields five, three of them padding. So a five-character
identifier is compared on roughly two or three real trigrams, and near-misses score close to exact
matches. **measured**:

| a | b | similarity |
|---|---|---|
| `INV-2024-04417` | `INV-2024-04418` | 0.7647 |
| `A-1204` | `A-1205` | 0.5556 |
| `A-1204` | **`B-1204`** | **0.5556** |
| `Smith` | `Smyth` | 0.3333 |
| `O'Brien` | `OBrien` | 0.5000 |

Note the third row. Trigram cannot distinguish "last digit differs" from "first letter differs". If
you rank on trigram similarity in an identifier-heavy corpus, you will confidently surface the wrong
invoice. Trigram is a recall mechanism. It is not a precision mechanism, and it must never be the
thing that decides which identifier is the answer.

Cost at 200k chunks: the GIN trigram index is 146 MB and took 91.2 seconds to build, roughly
2.6 times the `tsvector` index and three times the build time. On the 1,300-character corpus,
`word_similarity` queries ran at **13.0 seconds median** and trailing-digit queries at **3.3
seconds**, because the operator must compute the window for every candidate row. **measured.**
That is unusable as a first-pass retrieval arm on this box and perfectly fine over a 50-row
candidate set.

### 3.5 Verdict

**Install it. Use `word_similarity` and `strict_word_similarity`, never `%` with the default
ordering. Restrict it to reranking a candidate set produced by something cheaper.** Storage is
affordable; the query cost is not, at first-pass scale.

---

## 4. BM25 through a Postgres extension

### 4.1 VectorChord-BM25 (`vchord_bm25`)

**What it is.** A Postgres extension implementing BM25 over a custom `bm25vector` type with a
custom index access method, using the Block-WeakAND algorithm. Requires a separate extension,
`pg_tokenizer`, to turn text into vectors; `pg_tokenizer` is Apache-2.0 and pulls HuggingFace
tokenizers.

**Licence.** Dual: AGPL-3.0 **or** Elastic License v2, at your option. Verified from
<https://raw.githubusercontent.com/tensorchord/VectorChord-bm25/main/LICENSE>, HTTP 200, 1,271
bytes. The file's own words: *"This software is licensed under a dual license model. You may choose
to use this software under one of the following licenses: 1. GNU Affero General Public License v3
(AGPLv3) … 2. Elastic License v2 (ELv2)"*. The README adds: *"We welcome any commercial
collaboration or support, so please email us <vectorchord-inquiry@tensorchord.ai>"*.

**Both options are disqualifying as they stand.** AGPL-3.0 is what our licence rule forbids. ELv2
prohibits providing the software as a service and restricts commercial redistribution, which is
closer to what we do than AGPL is: we ship it inside a paid build. There is a stated willingness to
negotiate a commercial licence but **no published price**, so under our own rule ("no AGPL unless a
commercial licence is available and priced") this is **not shippable today**.

**What it does to identifiers: the worst result of anything tested.** The recommended tokenizer is
BPE, and BPE tokenisation is **context-dependent**, which breaks substring matching. **measured**:

```
tokenize('366332',          'tk_bpe') = {6, 110144, 123128}
tokenize('ZQX00-366332',    'tk_bpe') = {567, 2737, 1542, 7049, 9, 110144, 123128}
tokenize('04417',           'tk_bpe') = {6, 159937, 2489}
tokenize('INV-2024-04417',  'tk_bpe') = {5881, 856, 11033, 2357, 9, 159937, 2489}
```

The same six digits tokenise differently depending on what precedes them. A query for the trailing
digits therefore does not share the document's token ids. On the 200k corpus, trailing-digit queries
scored **0 hits@1 and 10 hits@10 out of 60, MRR 0.045**, against 60/60 for both competitors.
**measured.** Full identifiers scored 60/60, so the engine is not broken; its tokenizer is wrong for
our case.

There is a second problem for us. `CREATE INDEX` on a generated `bm25vector` column fails with
`generation expression is not immutable`, because `tokenize()` is not immutable. That means the
vector must be materialised with a trigger or an `UPDATE`, on every ingestion. **measured**: the
`UPDATE` that materialised 200,000 vectors took **219 seconds** of wall time, on top of the
`tsvector`, with a 1,151-byte column that added about 230 MB. Tokenising is not free and it is not
incremental.

**Cost, measured.** Index 70 MB on 200k chunks, built in 8.6 seconds. Image
`tensorchord/vchord-suite:pg18-latest`, 488 MB. Query latency 8.8 ms median, which is good.
Note the index must be rebuilt whenever the `bv` column changes, so the 8.6 seconds recurs on
every ingestion batch in proportion.

**Verdict: reject on licence, and reject a second time on tokenisation.** Even with a commercial
licence in hand, the BPE tokenizer is the wrong tokenizer for identifiers, and you would be paying
for a ranking improvement the measurements do not show.

### 4.2 ParadeDB `pg_search`

**What it is.** A Postgres extension embedding Tantivy, the Rust port of Lucene, as a Postgres index
access method. Exposes BM25 with tunable `k1` and `b` through `pdb.score`, an Elasticsearch-shaped
query surface (`|||` for match, plus phrase, fuzzy, proximity, regex), highlighting, facets, and
vector search. Index lives in Postgres pages and participates in WAL, which is a real operational
advantage over the older external-file design.

**Licence.** AGPL-3.0 only, for the Community edition. Verified three ways:

- `https://raw.githubusercontent.com/paradedb/paradedb/main/LICENSE` is the full AGPL-3.0 text, HTTP
  200, 34,523 bytes, sha256 prefix `8486a10c4393cee1`. No commercial dual-licence clause in the file.
- `Cargo.toml` at the repository root, `[workspace.package]`, reads `license = "AGPL-3.0"`.
- The Enterprise documentation page says plainly: *"ParadeDB Community is the open source edition,
  licensed under AGPL-3.0. It supports single-node deployments. ParadeDB Enterprise adds commercial
  licensing, support, and physical replication… To self-host it, contact sales."*

There is a commercial licence. It is not published, it is bundled with ParadeDB Cloud, and
self-hosting it is "contact sales". **No price, therefore not decidable, therefore not shippable
under our rule.** The GitHub repository carries the `AGPL-3.0` badge.

**What it does to identifiers: the best result tested, but only because of a configuration choice.**
ParadeDB is the only engine here that lets you pick a tokenizer per indexed column. The default
`pdb.unicode_words` behaves like everyone else's default and splits the identifier. **measured**:

```
'INV-2024-04417 74LS00 Stachowitz 3:21-cv-01234'::pdb.unicode_words = {inv,2024,04417,74ls00,stachowitz,3,21,cv,01234}
'INV-2024-04417'::pdb.simple                                         = {inv,2024,04417}
'INV-2024-04417'::pdb.whitespace                                     = {inv-2024-04417}
'INV-2024-04417'::pdb.ngram(3,5)                                     = {inv, inv-, inv-2, nv-, … , 04417, 4417, 417}
```

`pdb.whitespace` keeps the whole identifier as one token. `pdb.ngram(3,5)` keeps every 3-, 4- and
5-gram, which is what makes partial identifiers work. That is the mechanism behind the 60/60 on
trailing digits. **measured**, via `CREATE INDEX … USING bm25 (id, (body::pdb.ngram(3,5)))`.

Two operational notes. **You may only have one ParadeDB index per relation** — `CREATE INDEX` fails
with `a relation may only have one ParadeDB index` — so the tokenizer choice is a single global
decision per table and you cannot have a whitespace index and an ngram index side by side.
And `key_field` is deprecated as of 0.26.0 and is a no-op; older tutorials will mislead you.

**Cost, measured.** Index 96 MB on 200k chunks, built in 16.8 seconds. Image
`paradedb/paradedb:latest`, **1.1 GB**, against 304 MB for `postgres:18-alpine`. Query latency
3.8 ms median on full identifiers, the fastest of the three. The larger image is a real cost on an
air-gapped client box where every image has to be delivered and stored.

**Verdict: the best-engineered option and the wrong one for now, on licence.** If a commercial
licence can be bought at a price that fits a build fee, this becomes the leading candidate on
merit, because it solves the tokenisation problem properly rather than working around it. Get the
price before the next architecture review.

### 4.3 Tiger Data `pg_textsearch`

**This one was not in the brief. It is the most important find in this research, because it is the
only licence-clean way to get real BM25 into Postgres.**

**What it is.** A BM25 index written from scratch in C on Postgres' own storage layer, by the
Timescale team (Tiger Data). Uses an LSM-style memtable plus on-disk segments, Block-Max WAND for
top-k, SIMD-accelerated posting-list decoding, parallel index builds. Because it uses Postgres
pages, it participates in WAL, works with `pg_dump`, and works with streaming replication, which
none of the external-file designs do. Query surface is `ORDER BY content <@> 'search terms'` with
configurable `k1` and `b`. Uses standard Postgres text search configurations for tokenisation,
which means it inherits `simple`, `english`, and custom configurations rather than inventing its
own tokenizer model.

**Licence: the PostgreSQL License.** Verified from
<https://raw.githubusercontent.com/timescale/pg_textsearch/main/LICENSE>, HTTP 200, 910 bytes,
sha256 prefix `d33de21a123ce25b`. The file is the standard PostgreSQL License with "TIGER DATA"
substituted for the copyright holder. Permissive, no copyleft, no network clause, no service
restriction. **This is shippable as-is, with no legal conversation.** The repository description
states it directly: *"PostgreSQL extension for BM25 relevance-ranked full-text search. Postgres OSS
licensed."*

**Cost.** Pre-built binaries for Linux amd64 and arm64 on the Releases page, so no Rust or pgrx
toolchain in the build. Requires an entry in `shared_preload_libraries`, which means a Postgres
restart on install. Image: uses a standard Postgres image plus the extension, so far smaller than
ParadeDB's 1.1 GB.

**The caveats, and they are real.**

- **Version support is narrow: Postgres 17 and 18 only.** Postgres 19 beta is best-effort with CI
  allowed to fail. We should be on 18, so this is fine today, but it constrains upgrades.
- **It is young.** The first production release is dated March 2026, six months before this report.
  There is a full benchmark suite and CI, and it is used in Tiger Cloud, which is meaningful
  evidence of production exposure. But it has not been through a decade of hardening.
- **`shared_preload_libraries` is a heavier integration** than a plain extension. It needs the
  Postgres image to be ours or to be layered, and it changes the restart story.
- **The vendor's own benchmark numbers are a vendor claim.** Tiger Data reports 2.4x to 6.5x faster
  than ParadeDB/Tantivy for 2-to-4-term queries at 138M scale, and 8.7x higher concurrent
  throughput, and 138M documents indexed in under 18 minutes. Those are **vendor claims** published
  on <https://www.tigerdata.com/blog/pg-textsearch-bm25-full-text-search-postgres>. I did not
  reproduce them. Their own post also says candidly where ParadeDB is faster, which is a point in
  the vendor's favour.
- **It does not solve the tokenisation problem either.** It uses Postgres text search
  configurations, so if you point it at `english` you get `inv`, `-2024`, `-04417` exactly as
  before. Point it at a custom configuration, or index the identifier column described in §0.

**Verdict: not this build, but keep it as the designated path to BM25.** It removes the licence
objection entirely and it is architecturally the right shape for us — Postgres pages, WAL, `pg_dump`,
standard configurations. The reason to wait is maturity, not licence. When we want BM25, this is the
one to evaluate first, and the identifier column in §0 makes the tokenisation question independent
of the engine choice.

### 4.4 Anything else

I did not find another BM25 implementation for Postgres with a permissive licence. `pg_trgm` and
`tsvector` are the permissive in-core options; `vchord_bm25` and `pg_search` are the two serious BM25
extensions and both are AGPL-3.0-based; `pg_textsearch` is the permissive newcomer. If a fourth
exists it is obscure enough that I would want to see its release history before relying on it.

---

## 5. A separate Lucene-based search service as a sidecar

Full profiles for each were not produced to the same depth as the Postgres options, because they all
fail on the same two constraints, so the licence and operational summarises follow and the reasoning
is stated once.

### 5.1 The licences

| Project | Licence | Verified from | Shippable? |
|---|---|---|---|
| **OpenSearch** | Apache-2.0 | `raw.githubusercontent.com/opensearch-project/OpenSearch/main/LICENSE.txt`, HTTP 200, 11,357 B | Yes |
| **Elasticsearch** | Triple: AGPL-3.0-only, SSPL v1, Elastic License 2.0; `x-pack` is ELv2-only | `raw.githubusercontent.com/elastic/elasticsearch/main/LICENSE.txt`, HTTP 200, 669 B | **No** |
| **Quickwit** | Apache-2.0 | `raw.githubusercontent.com/quickwit-oss/quickwit/main/LICENSE`, HTTP 200, 11,351 B | Yes |
| **Tantivy** | MIT | `raw.githubusercontent.com/quickwit-oss/tantivy/main/LICENSE`, HTTP 200, 1,099 B | Yes |
| **Meilisearch** | Core MIT; Enterprise Edition files under BUSL-1.1 | `raw.githubusercontent.com/meilisearch/meilisearch/main/LICENSE`, 354 B, and `LICENSE-EE` | Core yes, EE no |
| **Typesense** | GPL-3.0 | `raw.githubusercontent.com/typesense/typesense/master/LICENSE.txt`, HTTP 200; repo metadata `spdx_id: GPL-3.0` | Complicated, see below |

Two of these deserve a specific sentence.

**Elasticsearch is out on licence, unambiguously.** Its `LICENSE.txt` reads: *"The default throughout
the repository is a triple license under the 'GNU Affero General Public License v3.0 only', 'the
Server Side Public License, v 1', and the 'Elastic License 2.0'"*. All three branches are
disqualifying under our rule. There is no configuration of self-hosted Elasticsearch that we can
ship to a client. This is not a close call and it does not need a lawyer.

**Typesense is GPL-3.0, and the project has published its own reasoning about what that permits.**
From its README: *"Typesense Server is server software and we expect users to typically run it as a
separate daemon, and not integrate it with their own code. GPL covers and allows for this use case
generously (eg: Linux is GPL licensed)."* **vendor claim**, but a well-argued one, and it matches my
reading: running a GPL daemon as a separate container does not make our code a derivative work.
**However**, distributing the container to a client is conveyance, so we inherit GPL obligations for
Typesense itself — we must supply its source or a written offer, and preserve notices. That is
manageable but it is a new obligation in a business that currently has none, and it is not obviously
worth paying for the privilege.

**Meilisearch is MIT for the core**, which is fine, but the Enterprise Edition files are BUSL-1.1
with an explicit non-production-only grant: *"You may use, modify, and distribute the Licensed Work
for non-production purposes only… Production use of the Licensed Work requires a commercial license
agreement with Meilisearch."* Anything EE is therefore out unless bought.

### 5.2 Why none of them belong in this stack, regardless of licence

**RAM and CPU on a box that is already full.** Every one of these is a second datastore with its own
memory model. The reference instance is 16 vCPU and 64 GB and already runs Postgres, Qdrant, two
Text-Embeddings-Inference containers, Docling, LiteLLM, the assistant API, the audit sink, and the
client app. OpenSearch and Elasticsearch are JVM services with heap sizing to tune and a documented
tendency to consume whatever you give them. Quickwit is Rust and lighter, but it is architected for
log and trace search at high volume, not for a few hundred thousand document chunks, and its
resource profile is aimed at a different problem. **my inference from their architectures**, not a
measurement I performed.

**They break the backup and consistency story we already have.** Today, Postgres is the record and
`pg_dump` plus WAL archiving is the backup. Add a sidecar index and there are two stores that can
disagree, an ingestion path that has to write to both, a reconciliation job for when the second
write fails, and a second backup and restore procedure to write, test and document. On a 5-to-30
user deployment maintained by one person, that is the cost that actually hurts. Note that both BM25
extensions avoid it: `pg_search` and `pg_textsearch` both live in Postgres pages and participate in
WAL. That is the single strongest architectural argument for an extension over a sidecar.

**They add a network listener and an authentication surface** to a box whose security posture is
currently "one database, not published". OpenSearch and Elasticsearch ship with security
configuration that has to be done deliberately; getting it wrong puts an unauthenticated search
endpoint on the client's LAN.

**And they cannot help with the office build's air gap any more than Postgres can.** Every one of
them would need its image and any models baked into the release. That part is a wash.

### 5.3 Verdict

**Reject Elasticsearch on licence. Reject all of them on operational cost.** The one honest argument
for a sidecar is that a real search engine gives you a real tokenizer configuration story, which is
the thing ParadeDB gets right. But we can get the same outcome inside Postgres, on the licence we
already ship, by choosing the tokenizer ourselves at ingestion time. §0 describes how.

That is a genuinely better trade for a business with one maintainer and no legal budget.

---

## 6. Does BM25 beat `ts_rank`, and by how much?

This is where the brief's premise and the measurements part company, so it is worth being precise
about what is true and what is not.

### 6.1 What is true, and measured

**`ts_rank` has no IDF. This is structural, not a quality opinion.** `ts_rank(tsvector, tsquery)`
has no corpus argument, so it cannot know how rare a term is. Asked to score the same
single-occurrence document against a term appearing in 5,000 of 5,000 documents and against a term
appearing in 5 of 5,000, it returns **identical** scores: 0.060793 both times. **measured.**
`ts_rank_cd` returns identical scores too.

**On a realistic mixed query, BM25's separation is roughly two orders of magnitude and `ts_rank`'s
is roughly a hundredth of a point.** 100 documents, all containing the common term `quarterly`, half
also containing the rare term `escrow`. Query `quarterly escrow`, **measured**:

| | documents with the rare term | documents without | ratio |
|---|---|---|---|
| `ts_rank` | 0.00002030 | 0.00000000 | — |
| BM25 (pg_search) | 0.689810 | 0.005023 | **137x** |

Both rank correctly. Only one of them has a margin that survives being combined with a dense
retrieval score in a fusion step. That is the real cost of missing IDF, and it is a fusion problem
more than a ranking problem.

**`ts_rank_cd` is worse than `ts_rank`, and it is the one people recommend.** Linear in term
frequency, 0.1 to 5.0, unsaturating. **measured** in §2.4. On a corpus where one word repeats, it
will return that document first. Use `ts_rank`.

**Default `ts_rank` has no length normalisation.** Identical scores for a 5-word and a 1,205-word
document. **measured** in §2.4. This is a fixable configuration error, not a defect in the function.

### 6.2 What is not true, and this is the important part

**BM25 did not beat `ts_rank` on the identifier task. They tied, at the ceiling, 60 out of 60 and
190 out of 200.** **measured**, two corpora, two document lengths, both engines. The reason is
simple: when the query is a whole identifier, the identifier appears in one chunk out of 200,000,
so there is almost nothing to rank against. IDF has no work to do. The ranking function is not what
was failing.

The published evidence on BM25 versus `ts_rank` is thinner than the confidence of the advice.
Tiger Data's launch post says: *"If you have used Postgres's built-in ts_rank for full-text search
at any meaningful scale, you already know the limitations. Ranking quality degrades as your corpus
grows. There is no inverse document frequency… There is no term frequency saturation… There is no
efficient top-k path."* That is a **vendor claim** from a company selling the alternative, and the
measurements here show that **two of those three claims need qualification**: `ts_rank` *does*
saturate, within 17 percent of BM25's ratio, and the top-k point is about latency rather than
quality and is fair. I found no independent, reproducible benchmark comparing `ts_rank` to BM25 on a
shared corpus with shared queries. **That gap is worth stating plainly in the decision document
rather than papering over with a citation to BEIR, which measures neither.**

**BEIR is not evidence for this decision.** It is a zero-shot retrieval benchmark, nDCG@10 across
many datasets, and BM25 is the standard baseline in it. It says nothing about which Postgres ranking
function to use, and nothing about 60 golden questions. Citing it here would be a category error.
**my inference.**

**The honest summary:** real BM25 gives you IDF and length normalisation, which `ts_rank` genuinely
lacks by default, and IDF matters when you fuse lexical scores with dense scores. That is a real
reason to want BM25 eventually. It is not a reason to conclude that `ts_rank` is why your identifier
search is broken, because it is not. **Tokenisation is.**

### 6.3 What this means for 60 golden questions

At n=60, the 95 percent confidence interval around an observed proportion is wide enough to swallow
most of the differences being discussed.

Arithmetic, and it is the whole argument: the standard error of a proportion at p=0.9, n=60 is
`sqrt(0.9 × 0.1 / 60) = 0.0387`, so the 95 percent interval is roughly `0.9 ± 0.076`, that is
**82 percent to 98 percent**. At p=0.5 the standard error is 0.0646 and the interval is 0.5 ± 0.127,
so **37 percent to 63 percent**. The minimum difference you can detect with any confidence at n=60
is on the order of **13 to 15 percentage points**, and that is a difference between two independent
samples; comparing two engines on the *same* 60 questions is a paired test and is substantially more
sensitive, which is the one thing working in our favour.

Practical consequences.

- A 2-point difference between two retrieval configurations is **not measurable with 60 questions**.
  Anyone who reports one is reporting noise.
- The thresholds in the build brief — 92 percent retrieval, 85 percent answer support — are
  reasonable as gates, because a gate only has to be cleared, not compared. At n=60 a pass at 55/60
  is 91.7 percent and a fail at 54/60 is 90 percent; the gate sits inside the noise band, so
  **decide the pass mark before running, and do not re-run to get a better draw.**
- **The five blocking results in the build brief are the right instrument**, and they are far better
  suited to n=60 than any average. "An invented answer to a question the documents do not cover" and
  "a numeric mismatch against the source" are binary events, and a single occurrence is decisive at
  any sample size. Build the identifier questions as blocking tests, not as scored ones.
- **Paired comparison on the same 60 questions, reported as a contingency table, not two
  percentages.** "Engine A got 52, engine B got 54" is uninterpretable. "Of the 60 questions, 51
  were correct under both, 3 correct under B only, 1 correct under A only, 5 wrong under both" tells
  you exactly where they differ, and at those counts you can read the answer off the table without
  a statistician.

**my inference throughout this section**, from the arithmetic above.

---

## 7. What the identifier test could not tell us, and the one result to distrust

Every engine failed the surname test utterly: 1 hit out of 400 for `ts_rank` on the 200k corpus, and
for a single worked example, `Zielinski`, all four engines missed the target chunk entirely.

**Do not conclude that surname search is broken. Conclude that the test corpus is.**
`Zielinski` appears in **8,174 chunks**, because the generator drew 24 surnames uniformly, so one in
24 of 200,000 chunks carries any given name, and every one of those chunks is 400-odd words of
near-identical filler. There is no signal to rank on. A real corpus has surname distributions and
surrounding text that differ per document. **measured, and the measurement is an artefact.**

There is a real lesson buried in it though, and it is worth carrying into the design: **ranking
cannot separate candidates when the corpus has nothing to separate them by, and no ranking function
fixes that.** If a client asks "what did we agree with Zielinski", the right retrieval path is a
metadata filter on the party field, or a boolean combination with a date or a matter number, not a
better relevance score. This is an argument for structure and filters, and it is another argument
against adding a search engine on the theory that its ranking function is cleverer.

---

## 8. Recommendation

### Ship

**Postgres native full-text search as the lexical arm, configured properly.**

1. `tsvector` with the `simple` configuration for the identifier column, so nothing is stemmed or
   discarded. Keep `english` for the prose column if stemming helps there.
2. `setweight` to mark title, heading and table-row lexemes above body lexemes. The default weight
   ramp is `{0.1, 0.2, 0.4, 1.0}` for D, C, B, A **measured from the source**, so `A` is worth ten
   times `D`. Use it.
3. GIN indexes on both the prose `tsvector` and the identifier column. 51 MB per 200,000 chunks for
   the prose index; budget about 255 MB at a million.
4. `ts_rank` with an explicit `normalization`, never the default and never `ts_rank_cd`.
5. A regex-extracted identifier column populated at ingestion: lowercased, and stored both whole and
   as separated components, so `INV-2024-04417` is findable as itself and as `04417`. This is the
   fix for the actual failure and it needs no new dependency.
6. An exact substring assertion in the citation verifier for anything that looks like an identifier.
   If the answer contains an identifier, it must appear literally in the retrieved page text, or
   the answer is refused. **This is the highest-value item in the whole recommendation**, because it
   is deterministic and it converts our worst failure mode into a refusal.
7. `pg_trgm` installed and available, used through `word_similarity` on a candidate set, never
   through `%` at first pass.

### Buy, when the price is known

**ParadeDB `pg_search`**, if a commercial licence can be bought at a price that fits a build fee.
It is the best-engineered option, it solves tokenisation with per-column clarity, and its query
latency was the best measured. The blocker is purely licence-and-price. **Ask for the number.**
A 1.1 GB image and a one-ParadeDB-index-per-table limit are the technical costs.

### Revisit

**`pg_textsearch`.** Licence-clean, architecturally right, young. Put it on the list for the next
architecture review with a note to check its release cadence and its Postgres 17/18 constraint.
The identifier column from §8.1 makes the switch an index change rather than a redesign, which is
the point of doing the identifier work first.

### Reject, with reasons

| Option | Why |
|---|---|
| `ts_rank_cd` | Linear in term frequency, 0.1 to 5.0, unsaturating. **measured**. Actively harmful |
| `%` / `similarity()` for identifiers | 0/60 and 0/200. Geometrically cannot work on short-query-long-document. **measured** |
| `vchord_bm25` | AGPL-3.0 or ELv2, no published commercial price. BPE tokenisation is context-dependent and breaks partial identifiers. **measured** |
| Elasticsearch | AGPL-3.0 / SSPL / ELv2. No shippable configuration |
| OpenSearch | Licence is fine, Apache-2.0. Rejected on RAM, a second store to keep in sync, a second backup path, and an added network listener |
| Quickwit | Licence is fine, Apache-2.0. Built for log and trace volumes, wrong shape and wrong resource profile for a few hundred thousand document chunks |
| Tantivy standalone | Licence is fine, MIT, and it is what ParadeDB embeds. Wrapping it ourselves means building a service, a client, and a consistency story that ParadeDB has already built and that we would then have to maintain |
| Meilisearch | MIT core is shippable, EE is BUSL-1.1 non-production-only. A second datastore for a feature we already own in Postgres |
| Typesense | GPL-3.0. Defensible as a separate daemon, but we would take on GPL conveyance obligations for a second datastore we do not need |

---

## 9. Open questions for the decision document

1. **What does ParadeDB charge for a self-hosted commercial licence?** Everything about that option
   is contingent on a number nobody has. Ask before the next review.
2. **Is `pg_textsearch` mature enough by the time we build the office tier?** It needs a release
   history and a Postgres-version commitment, not a benchmark.
3. **Does the identifier column belong in the schema, or in the ingestion output?** It affects
   whether re-extraction needs a reindex or just a rebuild of one column. My preference is a
   generated column where the expression is immutable, and a trigger where it is not.
4. **What regex defines an identifier?** `INV-2024-04417`, `74LS00`, `3:21-cv-01234` and
   `ACCT-1987-9177` are four different shapes and a client will have a fifth. This needs to be
   configurable per deployment, not hard-coded, and it needs a test.
5. **Does the verifier's substring assertion have a false-refusal rate we can tolerate?** An OCR
   error in a scanned invoice number would make the assertion fail on a correct answer. Measure it
   on the golden set before making it blocking.
6. **Is one `tsvector` per chunk, or one per chunk plus one per identifier?** The second is cheaper
   to keep clean and easier to reason about; it is also a second index to maintain.

---

## 10. What I could not verify

- **No published, independent, reproducible benchmark compares `ts_rank` to BM25 on a shared corpus
  with shared queries.** Tiger Data's claims are vendor claims; I did not reproduce them. The
  comparisons in §6 are mine, on a synthetic corpus, and should be reproduced on real client
  documents before they carry weight in a contract.
- **RAM floors for OpenSearch.** The sidecar profiles in §5 are architectural reasoning, not
  measurements. I did not install OpenSearch or Quickwit and did not measure their memory.
- **I did not measure `pg_textsearch`.** It is not in any image I pulled, and building it needs a
  Postgres 17/18 base plus a release binary. Its licence is verified from its own file; its
  performance and its behaviour on identifiers are **unverified**.
- **ParadeDB's commercial licence price.** The Enterprise page exists and says to contact sales.
  There is no published number, so "available and priced" is not satisfied as of today.
- **Whether ParadeDB Enterprise self-hosting is offered at all** at our scale, as opposed to being
  effectively Cloud-only.
- **The absolute latencies.** Measured on 8 vCPU with four Postgres containers running
  concurrently. Expect two to four times better on a dedicated 16 vCPU instance. The ratios and the
  hits@1 figures are the durable part.

---

## 11. Reproducing this

Everything below ran on this machine on 2026-10-11. Rebuild the containers and the harness to
re-check any number.

```bash
# probes, all throwaway
docker run -d --name oi-lex-probe -e POSTGRES_PASSWORD=probe -e POSTGRES_DB=probe \
  -p 55432:5432 postgres:18-alpine \
  -c shared_buffers=1GB -c work_mem=256MB -c maintenance_work_mem=512MB

docker run -d --name oi-paradedb -m 4g -e POSTGRES_PASSWORD=probe -e POSTGRES_DB=probe \
  -p 55434:5432 paradedb/paradedb:latest

docker run -d --name oi-vchord -m 4g -e POSTGRES_PASSWORD=probe -e POSTGRES_DB=probe \
  -p 55435:5432 tensorchord/vchord-suite:pg18-latest
```

Files used, all under `/tmp`:

| File | What it does |
|---|---|
| `gen_corpus.py` | Generates the 200,000-chunk synthetic corpus with embedded identifiers |
| `eval_lexical.py` | Plants 60 identifiers and runs the four-engine head-to-head |
| `reset_probe.py` | Strips the planted identifiers and refreshes every derived structure |
| `trgm_fair.py` | Trigram versus `ts_rank` versus BM25 on 200 short documents |
| `sat_tsrank.sql`, `sat_pdb.sql` | Term-frequency saturation |
| `idf2.sql`, `idf2p.sql` | IDF separation, `ts_rank` versus BM25 |
| `len.sql`, `lenp.sql` | Document-length normalisation |
| `eval_run4.log` | Full output of the head-to-head |

One trap worth recording: the first version of the harness updated `body` on a table with a
`GENERATED ALWAYS AS` `tsvector` column, and Postgres marked the generated column stale without
recomputing it, so every planted identifier was invisible and every engine scored zero. The fix is
the redundant `UPDATE chunks SET body = body`. **The same trap will bite the real ingestion pipeline
if the identifier column is generated.** That is a defect that produces zero errors and zero
results.

**Sources.** Postgres `COPYRIGHT` and `tsrank.c`; Postgres documentation chapter 12; `pg_trgm`
control file; ParadeDB `LICENSE`, `Cargo.toml`, and Enterprise documentation page; VectorChord-BM25
`LICENSE` and README; `pg_tokenizer` `LICENSE`; `pg_textsearch` `LICENSE` and Tiger Data's launch
post; OpenSearch, Elasticsearch, Quickwit, Tantivy, Meilisearch and Typesense licence files; Typesense
README's licensing section. URLs are inline throughout.

---

## 12. Licence verification appendix

Every licence claim in this document, with the URL it was read from, the HTTP status, and the byte
count or hash where I recorded one. All fetched 2026-10-11.

| Project | Licence | URL read | Evidence |
|---|---|---|---|
| PostgreSQL | PostgreSQL License | `https://raw.githubusercontent.com/postgres/postgres/master/COPYRIGHT` | HTTP 200, 1,198 B, sha256 prefix `3d6af92ff8a4c2cd` |
| `pg_trgm` | PostgreSQL License (in core `contrib`) | `https://raw.githubusercontent.com/postgres/postgres/master/contrib/pg_trgm/pg_trgm.control` | HTTP 200, 192 B |
| ParadeDB `pg_search` (Community) | **AGPL-3.0 only** | `https://raw.githubusercontent.com/paradedb/paradedb/main/LICENSE` | HTTP 200, 34,523 B, sha256 prefix `8486a10c4393cee1`; also `[workspace.package] license = "AGPL-3.0"` in the root `Cargo.toml` |
| ParadeDB Enterprise | Commercial, price not published | `https://www.paradedb.com/docs/operate/deploy/enterprise` | Page states Community is AGPL-3.0 and Enterprise "adds commercial licensing… To self-host it, contact sales" |
| VectorChord-BM25 | **AGPL-3.0 or Elastic License v2** (dual) | `https://raw.githubusercontent.com/tensorchord/VectorChord-bm25/main/LICENSE` | HTTP 200, 1,271 B, sha256 prefix `3183d831d200a728` |
| `pg_tokenizer` | Apache-2.0 | `https://raw.githubusercontent.com/tensorchord/pg_tokenizer.rs/main/LICENSE` | HTTP 200, Apache-2.0 header |
| Tiger Data `pg_textsearch` | **PostgreSQL License** | `https://raw.githubusercontent.com/timescale/pg_textsearch/main/LICENSE` | HTTP 200, 910 B, sha256 prefix `d33de21a123ce25b` |
| OpenSearch | Apache-2.0 | `https://raw.githubusercontent.com/opensearch-project/OpenSearch/main/LICENSE.txt` | HTTP 200, 11,357 B, sha256 prefix `c71d239df91726fc` |
| Elasticsearch | **AGPL-3.0-only / SSPL v1 / Elastic License 2.0** (triple); `x-pack` is ELv2-only | `https://raw.githubusercontent.com/elastic/elasticsearch/main/LICENSE.txt` | HTTP 200, 669 B, sha256 prefix `09979a6b0701e166` |
| Quickwit | Apache-2.0 | `https://raw.githubusercontent.com/quickwit-oss/quickwit/main/LICENSE` | HTTP 200, 11,351 B, sha256 prefix `0ce820f3fbed6c5e` |
| Tantivy | MIT | `https://raw.githubusercontent.com/quickwit-oss/tantivy/main/LICENSE` | HTTP 200, 1,099 B, sha256 prefix `acacd14bebbffdb3` |
| Meilisearch | Core MIT; Enterprise Edition files BUSL-1.1, non-production only | `https://raw.githubusercontent.com/meilisearch/meilisearch/main/LICENSE` (354 B) and `…/LICENSE-EE` | `SPDX-License-Identifier: MIT AND BUSL-1.1`; the EE file grants production use only under a commercial agreement |
| Typesense | GPL-3.0 | `https://raw.githubusercontent.com/typesense/typesense/master/LICENSE.txt` | HTTP 200, GNU GPL v3 text; repository metadata `spdx_id: GPL-3.0` |

**Not verified, and flagged as such.** I found no published price for a self-hosted ParadeDB
Enterprise licence, and no published price for a VectorChord-BM25 commercial licence. Both projects
invite contact. Under the licence rule as written — "no AGPL unless a commercial licence is available
and priced" — neither satisfies the condition today.

---
