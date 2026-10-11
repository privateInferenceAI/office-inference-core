# Which embedding model we should self-host

Research pass for the Office Inference retrieval architecture. Written 2026-10-11.

Scope: the dense retriever for document question answering over business documents (contracts,
policies, invoices, emails, spreadsheets, scanned PDFs), with page-level citations, in Docker on
CPU, and — for the office build — with **no internet at runtime**.

---

## 0. The answer, up front

**Keep `BAAI/bge-m3`, 1024 dimensions, CLS pooling, no prefix. Do not change it for a leaderboard
score.**

The reason is not that bge-m3 is the best retriever. The measured evidence says it is not: on
zero-shot BEIR it scores 0.437 average nDCG@10 against 0.546 for `intfloat/multilingual-e5-large`
(Cirillo et al., arXiv 2605.23618, Table II). That is a real 0.109 gap and it is the single most
uncomfortable number in this report.

The reason is that **the embedding model is not where our retrieval quality comes from, and
changing it is the change with the worst ratio of risk to gain.** Four things dominate bge-m3's
ranking in our architecture:

1. **Hybrid search does the work that bge-m3's dense vector fails at.** Vector search is weak on
   exact identifiers — invoice numbers, procedure codes, statute sections, account numbers, names.
   That is why the build already specifies Qdrant prefetch plus reciprocal rank fusion. A dense
   retriever swap does not improve exact-identifier retrieval at all.
2. **The reranker is where the quality is bought.** `bge-reranker-v2-m3` over 50 to 100 candidates
   is a larger quality lever than any dense-model swap in this report, and it is already specified.
3. **bge-m3 is the only serious candidate that needs no query/passage asymmetry and no instruction
   prefix** — which removes an entire class of silent, ranking-destroying configuration bug. This is
   not a minor convenience; §5 shows a case where getting the prompt convention wrong costs 17
   accuracy points, more than any model in this report gains over any other.
4. **bge-m3 is already running.** It is in `docker-compose.yml` on `main`, in the ingestion
   pipeline, and in Qdrant at 1024 dimensions. Every alternative is a re-embed plus a re-validated
   pipeline, spent on a component that is not the bottleneck.

**The professional position is: bge-m3 now, spend the next engineering hour on the reranker, the
chunking, and the citation verifier, and make the embedding choice reversible so a swap is a
re-embed job rather than a migration.** §8 specifies exactly what to store to make that true.

**One exception worth holding open.** If we learn that a client needs **reliable Japanese, Korean,
or Chinese retrieval** — not "100 languages supported" but actually tested — then
`microsoft/harrier-oss-v1-0.6b` is the model to evaluate first. It is MIT, it is exactly as cheap
to serve as Qwen3-Embedding-0.6B, it scores 69.0 on Multilingual MTEB v2 against 61.15 for
EmbeddingGemma-1 and 61.36 for EmbeddingGemma 2, and it is served by TEI through the `Qwen3`
architecture. But it has no production history and no Japanese-specific published numbers, so it is
a candidate for the golden-question harness, not a default.

**The office build's no-internet constraint is what makes most of this list easy to reject**, and
it is the single most under-weighted fact in this decision. See §6.1.

---

## 1. What the constraints actually rule out

Applying the brief's constraints in order, before any quality argument:

| Constraint | What it kills |
|---|---|
| Licence must be shippable | `jinaai/jina-embeddings-v3` and `v5` (CC-BY-NC-4.0), `jinaai/jina-embeddings-v4` (Qwen **Research** License, non-commercial), `nvidia/llama-embed-nemotron-8b` (card says plainly "for non-commercial/research use only"), `google/embeddinggemma-300m` (Gemma terms + gated) |
| No internet at runtime (office build) | Anything **gated** on HuggingFace, because a gated repo needs an authenticated account and a click-through, not just a download. This kills `google/embeddinggemma-300m` and `google/embeddinggemma-2` for the office build on operational grounds even where the licence is clean |
| TEI must serve it | Anything needing `trust_remote_code`. This kills `voyageai/voyage-4-nano` and `NovaSearch/stella_en_1.5B_v5` as off-the-shelf choices. See §5.4 |
| CPU-only, 16 vCPU / 64 GB | The 7B–27B tier: `Qwen3-Embedding-8B`, `gte-Qwen2-7B-instruct`, `e5-mistral-7b-instruct`, `harrier-oss-v1-27b`. Viable on an L4/A10, not on our reference instance |
| Page-level citations | Nothing here is disqualifying — all candidates are dense text encoders and page numbers live in the chunk payload, not the embedding. But it means the retriever must not be replaced by a page-image retriever (ColPali/ColQwen) unless multi-vector storage and MaxSim scoring are also added |

That leaves a shortlist. The rest of this report is about that shortlist.

---

## 2. The shortlist, verified from source

Everything in this table was read from the model's own `config.json`, `1_Pooling/config.json`, and
model card. Licences were verified from the model card frontmatter or the repository `LICENSE` file.
Where I could not verify from a licence artifact, I say so.

| Model | Params (computed) | Dim | MRL / truncatable | Max ctx | TEI pooling | TEI arch | Licence (verified) |
|---|---|---|---|---|---|---|---|
| `BAAI/bge-m3` | 568M | 1024 | **No** | 8192 | **cls** | XLM-RoBERTa → `BertModel` | **MIT** |
| `intfloat/multilingual-e5-large` | 560M | 1024 | **No** | 512 | **mean** | XLM-RoBERTa → `BertModel` | **MIT** |
| `intfloat/multilingual-e5-small` | 118M | 384 | **No** | 512 | mean | BERT | **MIT** |
| `Qwen/Qwen3-Embedding-0.6B` | 596M | 1024 | **Yes**, 32–1024 | 32768 | **last-token** | Qwen3 | **Apache-2.0** |
| `Qwen/Qwen3-Embedding-4B` | 4.0B | 2560 | **Yes** | 32768 (cfg 40960) | last-token | Qwen3 | **Apache-2.0** |
| `microsoft/harrier-oss-v1-0.6b` | 596M | 1024 | not stated | 32768 | **last-token** | Qwen3 | **MIT** |
| `microsoft/harrier-oss-v1-270m` | 262M | 640 | not stated | 32768 | last-token | Gemma3 | **MIT** |
| `google/embeddinggemma-2` | 740M total, 270M text | 768 | **Yes**, 128/256/512 | 8192 | mean | **`EmbeddingGemma2Model` — NOT in TEI** | **Apache-2.0**, ungated |
| `Snowflake/snowflake-arctic-embed-l-v2.0` | 568M | 1024 | **Yes** (MRL to 256) | 8192 | cls | XLM-RoBERTa | **Apache-2.0** |
| `Alibaba-NLP/gte-multilingual-base` | 305M | 768 | not stated | 8192 | cls | `NewModel` (custom) | **Apache-2.0** |
| `nomic-ai/nomic-embed-text-v1.5` | 137M | 768 | **Yes** (64–768) | 2048 (cfg) / 8192 (ST) | mean | NomicBERT | **Apache-2.0** |
| `nomic-ai/nomic-embed-text-v2-moe` | 475M total, 305M active | 768 | **Yes** (to 256) | card says 512 | mean | NomicBERT (MoE) | **Apache-2.0** |
| `Snowflake/snowflake-arctic-embed-m-v2.0` | 305M | 768 | not stated | 8192 | cls | GTE | **Apache-2.0** |
| `mixedbread-ai/mxbai-embed-large-v1` | 335M | 1024 | **Yes** (to 512) | 512 | cls | BERT | **Apache-2.0** |
| `NovaSearch/stella_en_400M_v5` | 459M + Dense proj | 1024 (also 256–8192) | **Yes**, via `2_Dense_<dims>` modules | 8192 | **mean** | `NewModel` (custom, gte) | **MIT** |
| `voyageai/voyage-4-nano` | 319M | 2048 | **Yes**, 256/512/1024/2048 | 32768 | mean | Qwen3 + `auto_map` | **Apache-2.0** |

Rejected on licence and **not** in the table: `jinaai/jina-embeddings-v3` (CC-BY-NC-4.0),
`jinaai/jina-embeddings-v5-text-small` (CC-BY-NC-4.0), `jinaai/jina-embeddings-v4` (Qwen Research
License), `nvidia/llama-embed-nemotron-8b` (non-commercial), `google/embeddinggemma-300m`
(Gemma terms, gated).

### 2.1 Parameter counts: how I got them

`config.json` does not carry a parameter count, and model cards sometimes round. I computed these
from the architecture configs. Two caveats so nobody treats them as vendor numbers:

- The XLM-RoBERTa family has a 250,002-token vocabulary with **untied** embeddings, so the
  embedding matrix alone is about 512M parameters. That is why bge-m3's `hidden_size` is 1024 and
  its layer count is 24 — identical to `multilingual-e5-large` — yet it is not the "335M model"
  people assume. My computed 568M for bge-m3 matches the figure used in the lelabdev benchmark
  (568M) and in the TEI docs' "568M" label for `snowflake-arctic-embed-l-v2.0`, so I treat the
  method as sound. **These are my arithmetic, not vendor figures.**
- For Qwen3-Embedding-0.6B and harrier-0.6b I compute 537M (tied embeddings) against a card claim of
  596M / "0.6B". The gap is my bias and norming approximations. Use the card figure for tiering and
  mine only for *relative* CPU cost.

The relative CPU cost is the useful part, and it is roughly linear in non-embedding parameters:

| Model | Non-embedding params (approx) | Relative CPU cost per token |
|---|---|---|
| harrier-oss-v1-270m | ~130M | 1.0× |
| nomic-embed-text-v1.5 | ~85M | 0.7× |
| multilingual-e5-small | ~118M | 0.9× |
| bge-m3 | ~302M | 2.3× |
| multilingual-e5-large | ~302M | 2.3× |
| Qwen3-Embedding-0.6B | ~447M | 3.4× |
| harrier-oss-v1-0.6b | ~447M | 3.4× |
| Qwen3-Embedding-4B | ~3.7B | 28× |

**bge-m3 is not the expensive choice.** Qwen3-Embedding-0.6B costs about 1.5× more per token on CPU
despite the smaller headline number, because its embedding matrix is tied and small while its
transformer stack is 28 layers deep. That inverts the naive "0.6B is smaller than 568M" reading and
it matters for ingestion on a CPU-only box.

> **Evidence label:** parameter counts and the relative-cost table are **my arithmetic** from
> `config.json`. They are not measured throughput.

---

## 3. TEI: what it will and will not do

This is the deployment reality and it eliminates several candidates on its own.

**Licence.** `text-embeddings-inference` is **Apache-2.0** —
<https://raw.githubusercontent.com/huggingface/text-embeddings-inference/main/LICENSE>. Latest
release **v1.9.4, published 2026-09-15**. Our stack pins
`ghcr.io/huggingface/text-embeddings-inference:1.6`, which is now three minor versions behind.

**Architectures TEI supports** (from the repo README and `backends/candle/src/models/`, verified):
Nomic, BERT, CamemBERT, XLM-RoBERTa with absolute positions; JinaBERT with Alibi; Mistral,
Alibaba GTE, Qwen2 and Qwen3 with Rope; MPNet, ModernBERT, Gemma3. Source:
<https://github.com/huggingface/text-embeddings-inference/blob/main/docs/source/en/supported_models.md>
and the directory listing at
<https://github.com/huggingface/text-embeddings-inference/tree/main/backends/candle/src/models>.

That list decides the following:

- **bge-m3 works.** Its `config.json` gives `model_type: xlm-roberta`. TEI's dispatch maps
  `Config::XlmRoberta` onto `BertModel::load_roberta` (`backends/candle/src/lib.rs`, around line 308).
  Nothing custom, nothing exotic.
- **Qwen3-Embedding and harrier-oss-v1-0.6b work** (`qwen3.rs` / `flash_qwen3.rs`, and `Qwen3Model`
  is also what `pplx1.rs` wraps).
- **harrier-oss-v1-270m works** through `gemma3.rs`. The 27B variant is `gemma3_text` too, and TEI
  **forces fp32 for Gemma3 on CPU** (`backends/candle/src/lib.rs`: "Gemma3 is only supported in fp32
  precision"). On CPU that is a hard no for a 27B model.
- **`google/embeddinggemma-2` does not work.** Its architecture is `EmbeddingGemma2Model` with
  `model_type: embedding_gemma2`, which is not in TEI's dispatch. TEI supports *Gemma3*, which is
  what EmbeddingGemma **1** is. EmbeddingGemma 2 is a Gemma **4** derivative. Anyone reading
  "TEI supports Gemma3" and assuming the Gemma embedding line is covered will be wrong.
- **`voyageai/voyage-4-nano` is listed in the TEI docs** as a supported Qwen3 model, but its
  `config.json` carries `auto_map: {"AutoModel": "modeling_qwen3_bidirectional.Qwen3BidirectionalModel"}`
  and the card's usage requires `trust_remote_code=True`. TEI has **no `--trust-remote-code` flag** —
  I enumerated the full `Args` struct in `router/src/main.rs` and it is not there. Because TEI loads
  the architecture itself and ignores `auto_map`, it may still work; **I could not verify this
  without running it, and I have not.** Treat it as unverified.
- **`NovaSearch/stella_en_1.5B_v5` will not work off the shelf.** Its `auto_map` points at a local
  `modeling_qwen.Qwen2Model`, and TEI has no remote-code path.

### 3.1 Pooling: TEI resolves it correctly, and the mechanism matters

TEI does **not** default to CLS. From `router/src/lib.rs`: if `--pooling` is unset, TEI reads
`1_Pooling/config.json` and maps the first true flag, in the order `cls` → `mean` → `lasttoken`
(`PoolConfig::try_from`). Only when that file is missing *and* `model_type` contains "bert" does it
warn and fall back to CLS. So for every model in §2 the correct pooling is picked up automatically,
which I verified against each model's own `1_Pooling/config.json`:

- bge-m3 → `pooling_mode_cls_token: true` → **cls**. Confirmed as what our stack already gets right.
- multilingual-e5-large → `pooling_mode_mean_tokens: true` → **mean**.
- Qwen3-Embedding-0.6B → `pooling_mode_lasttoken: true` → **last-token**.
- harrier-oss-v1-0.6b → `pooling_mode_lasttoken: true` → **last-token**.
- stella_en_400M_v5 → `pooling_mode_mean_tokens: true` → **mean**.
- voyage-4-nano → `pooling_mode_mean_tokens: true` → **mean**.

**The one thing TEI does not read is `include_prompt`.** Qwen3-Embedding, harrier, stella, and
voyage-4-nano all set `"include_prompt": true`, meaning the instruction prefix must be included in
the pooled representation. TEI's `PoolConfig` deserialises only the three pooling flags and drops
`include_prompt` on the floor. Whether that is a real defect depends on whether TEI prepends the
prompt before pooling, which I did not establish. **Flag this as a known unknown to test, not as a
known bug.**

### 3.2 Asymmetric prefixes: a per-request, client-side convention

TEI's `/embed` endpoint accepts `prompt_name` per request (verified in `router/src/http/types.rs`,
`EmbedRequest`). It must be a key in the model's `config_sentence_transformers.json` `prompts`
dictionary. There is also a single server-wide `--default-prompt` / `--default-prompt-name`.

The operational consequence is important and easy to get wrong:

- **TEI has one default prompt, not a query prompt and a passage prompt.** You cannot configure
  "`query: ` for queries and `passage: ` for documents" server-side.
- Therefore for e5, stella, harrier, and Qwen3-Embedding, **the calling service must send
  `prompt_name`** — or prepend the prefix itself — and it must send the right one on each side.
- This makes the prefix convention **part of the data contract**, not part of the server config. It
  belongs in the same table as the model id and the dimension. See §8.
- A concrete temptation to avoid: setting `--default-prompt "query: "` to make e5 look good at
  query time silently corrupts **every stored document vector**, because the ingestion worker and
  the query path share one endpoint. That is a re-embed of the whole corpus to fix.

Read the prompt definitions for yourself rather than trusting a blog: e5 is `query: ` / `passage: `;
harrier's `config_sentence_transformers.json` defines `web_search_query`, `sts_query`,
`bitext_query`; Qwen3-Embedding and voyage-4-nano use an `Instruct: <task>\nQuery: <q>` form;
bge-m3 uses **no prefix**.

### 3.3 CPU tuning knobs that exist

From the enumerated `Args` struct, the flags that matter on CPU:

| Flag | Default | CPU note |
|---|---|---|
| `--max-batch-tokens` | 16384 | The one critical control. On 16 CPU vCPU, 16384 tokens in one batch will wreck per-request latency. Start at 2048–4096 |
| `--max-client-batch-size` | 32 | Cap on inputs per request. Fine for ingestion |
| `--max-concurrent-requests` | 512 | Far too high for CPU. Start at 8–16 |
| `--max-batch-requests` | unset | Set it to bound batch memory |
| `--tokenization-workers` | = CPU cores | Leave at default |
| `--dtype` | model default | `float32` / `float16` only |
| `--auto-truncate` | true | Leave on, but see the silent-truncation trap in §4.3 |

**There is no `--backend` flag.** The backend is chosen by image tag, not at runtime. The correct CPU
image is `ghcr.io/huggingface/text-embeddings-inference:cpu-1.9` (and `cpu-arm64-1.9` for aarch64),
per the supported-models doc. The bare `1.9` tag is the **Ampere 8.0 CUDA** image. This matters:
**our stack currently pins `:1.6` with no `cpu-` prefix.** On a GPU host that is correct; on the
CPU-only cloud reference instance it is the wrong image. Getting this wrong is a deployment failure,
not a slowdown.

### 3.4 Matryoshka: TEI will not truncate for you

I searched the router source for Matryoshka and `truncate_dim` handling and found only a doc comment
referring to `2_Dense_<dims>` module paths. **TEI has no server-side Matryoshka truncation on the
`/embed` path.** If we want 512-dimensional vectors out of a 1024-dimensional model, the application
truncates the returned vector and re-normalises. That is cheap and correct, but it means the
dimension is decided in **our** code, not in the model server — which is another reason it belongs in
the stored contract.

### 3.5 Sparse and hybrid: bge-m3 gives TEI only its dense head

bge-m3's selling point is one checkpoint producing dense, sparse (lexical), and multi-vector
(ColBERT) representations. **TEI produces only the dense vector.** SPLADE pooling in TEI is limited
to Bert and Distilbert `MaskedLM` architectures (README, "Using SPLADE pooling"), which bge-m3 is
not. There is a long-standing issue asking for bge-m3 sparse output:
<https://github.com/huggingface/text-embeddings-inference/issues/141>.

So the hybrid leg must come from elsewhere. In our stack that is the right answer anyway: Qdrant
prefetch with **BM25 / sparse-vector fusion**, which needs no model at all, is deterministic, and
is unaffected by embedding-model swaps. Do not architect around bge-m3's sparse head while TEI
cannot serve it. This also means one of the usual arguments *for* bge-m3 does not apply to us — the
case for bge-m3 here rests on breadth, licence, zero-prefix simplicity and reversibility, not on
three-in-one heads.

---

## 4. CPU throughput: what is actually known

### 4.1 There are no credible published CPU throughput numbers for TEI

I looked specifically and found none. The TEI docs' only benchmark is `BAAI/bge-base-en-v1.5` on an
**NVIDIA A10** at 512 tokens, presented as images with no tabulated figures
(<https://github.com/huggingface/text-embeddings-inference/blob/main/docs/source/en/index.md>). No
CPU benchmark table exists in the TEI docs. The vendor model cards (Qwen3-Embedding, bge-m3,
harrier) publish **GPU** latency/throughput and no CPU figures at all.

**Stating a CPU throughput number for TEI would be inventing it.** I am not going to. What follows
is the honest substitute: measured *latency* from one academic source, plus clearly-labelled
arithmetic, plus a measurement plan.

### 4.2 Measured: per-query latency on commodity CPU

The only measured CPU latency figures I found for these models come from Cirillo et al.,
arXiv 2605.23618 (May 2026), Table IV. They measured median per-query latency over 50 runs,
including embedding and FAISS retrieval, single query, no batching, on Apple M-series CPU.

| Model | Median | Std | p95 |
|---|---|---|---|
| `BAAI/bge-m3` (1024-dim) | **30.9 ms** | 0.7 | 32.1 |
| `intfloat/e5-large` (1024-dim) | 30.9 ms | 0.2 | 31.4 |
| `intfloat/multilingual-e5-large` (1024-dim) | 31.0 ms | 3.5 | 31.8 |
| `LaBSE` (768-dim) | 16.6 ms | 0.2 | 16.9 |
| `paraphrase-multilingual-mpnet-base-v2` (768-dim) | 16.6 ms | 0.2 | 17.0 |
| Google Embeddings 2 (API) | 231.6 ms | 102.6 | 575.5 |

**How to read this.** The paper's own conclusion is the useful part: "Both clusters are comfortably
under 100 ms on CPU without batching, meaning that for the vast majority of latency-constrained RAG
deployments, any of the five open-source models is acceptable." For the query path — one short query
embedded once — **CPU embedding latency is not our problem and no candidate in this report is
disqualified by it.** The 1024-dim vs 768-dim difference is about 2×, which is real but is dwarfed
by the reranker and the LLM call in the same request.

Caveats I am not going to hide: Apple M-series is not a 16-vCPU x86 instance, the hardware is
described as "commodity CPU" without a model number, and single-query latency says nothing about
bulk ingestion throughput.

### 4.3 The real CPU question is bulk ingestion, and it is unbounded by measurement

Query latency is ~31 ms. **Ingestion is where CPU hurts**, and no source measures it. So here is the
arithmetic, labelled as arithmetic, so we can size the risk before we measure it.

A forward pass costs roughly `2 × N × P` floating-point operations (the attention term is small at
short sequence lengths). For a 150-token chunk — a reasonable page-aware business chunk — at bge-m3's
568M parameters:

```
2 × 150 × 568e6 ≈ 170 GFLOP per chunk
```

A 16-vCPU modern x86 core with AVX-512 can retire on the order of 100 GFLOP/s of peak FMA. Real
efficiency with MKL/oneDNN on a transformer is 25–40 percent. That gives:

| Scenario | Effective | bge-m3 chunks/s | 10k pages | 50k pages |
|---|---|---|---|---|
| Conservative | 25% of 16 cores | ~2.3 | ~8.9 h | ~44 h |
| Well-tuned MKL/oneDNN | 40% of 16 cores | ~3.8 | ~5.6 h | ~28 h |
| int8 quantised path | ~35% of 2× peak | ~6.6 | ~3.2 h | ~16 h |

**This is my arithmetic, not a measurement, and it could be off by 3× in either direction.** But the
shape of the conclusion is robust and it is the one that should drive decisions:

- **Query-time CPU embedding is fine.** 31 ms measured.
- **Bulk ingestion on the production instance is a multi-hour-to-multi-day job for a realistic
  corpus.** The build brief's estimate of "about nine days of pegged CPU on the production instance"
  for a million pages is consistent with the middle row here, which is a useful cross-check.
- **Therefore bulk indexing belongs on a temporary big instance, as the build brief already says**,
  and the embedding model's CPU cost per token is a real budget line — about 2,300 USD-equivalent of
  temporary c7i.8xlarge time per the brief's figure.

Two consequences that are not obvious:

1. **Do not pick the model with the most parameters for CPU ingestion.** Qwen3-Embedding-0.6B costs
   ~1.5× bge-m3 per token on CPU (§2.1) while buying nothing on our documents.
2. **Long-context embedding is a trap on CPU.** Attention cost grows quadratically, and every
   candidate's "32K context" is a GPU-era claim. We chunk at 512 characters with structure awareness
   precisely so we never exercise the long-context path. Do not let a 32K-context model tempt us into
   embedding whole documents: it will be dramatically slower and it will destroy page-level citation
   granularity.

### 4.4 The silent-truncation trap

TEI's `--auto-truncate` defaults to true and `--max-batch-tokens` defaults to 16384. bge-m3's config
declares `max_position_embeddings: 8194`, i.e. 8192 usable tokens, and its
`sentence_bert_config.json` says `max_seq_length: 8192`. If a chunk ever exceeds the effective
limit, **TEI truncates silently rather than erroring**, and the resulting vector is a valid-looking
embedding of part of the text. For a system whose whole value proposition is that a citation is
true, silent truncation is a correctness bug: we would cite a page as supporting a sentence that was
never embedded.

Mitigation is cheap and should be in the ingestion worker, not in TEI: assert chunk token count
against a hard ceiling well under the model limit before sending, and fail the file loudly. This is
a property of every model in this report, not a reason to prefer one.

---

## 5. The judgement: what the benchmark evidence actually supports

### 5.1 The MTEB leaderboard cannot answer our question

This is not a generic complaint. The specific, verifiable problems:

**(a) The leaderboard is a moving target and is not a single scale.** MTEB was replaced by MMTEB
("Massive Multilingual Text Embedding Benchmark", arXiv 2502.13595, ICLR-accepted, covering 500+
tasks across 250+ languages) and the leaderboard now shows MTEB v2. Model cards quoting "MTEB
multilingual 70.58" (Qwen3-Embedding-8B, June 2025), "MTEB v2 69.0" (harrier-0.6b, March 2026), and
"MTEB English v2 69.67" (EmbeddingGemma-1) are **three different scales**. A vendor-comparison
article from September 2026 put it plainly: "Do not compare a 74.3 to a 71.7 as if they were one
scale." Any decision framed as "pick the top of the leaderboard" is comparing incomparable numbers.

**(b) Ranking is fragile to protocol choices that have nothing to do with model quality.** The
best-documented case is a practitioner's Bible-verse retrieval benchmark (532 queries, one or more
expected verses each) published on the HuggingFace forums, September 2025. Their result:

- On **their corpus**, `text-embedding-3-large` was first at 89.3 percent accuracy and
  `text-embedding-3-small` second at 81 percent.
- On **MTEB retrieval at the same time**, Qwen3-Embedding-8B and 4B were ranked 1 and 2, while the
  OpenAI models sat at 14 and 40.
- On their corpus, Qwen3-Embedding-8B was 8th at 71.5 percent and Qwen3-Embedding-4B was 20th at
  58.9 percent — **behind `text-embedding-ada-002`.**

Then the diagnosis, which is the important part. The models were being encoded with no query prompt.
Re-running with `prompt="query"` and nothing else changed:

| Model | Without prompt | With `prompt="query"` |
|---|---|---|
| Qwen3-Embedding-0.6B | 56.8% | **73.9%** |
| Qwen3-Embedding-4B | 58.9% | **72.4%** |
| Qwen3-Embedding-8B | 71.5% | **82.8%** |

**A prompt convention moved the 0.6B model 17.1 accuracy points — larger than the entire spread
between the best and worst serious model in this report.** The accompanying methodology note
(<https://huggingface.co/datasets/John6666/forum1/blob/main/mteb_dif.md>) enumerates the full list of
ways such a comparison goes wrong: different metric (3/2/1 ranking vs nDCG@10), different corpus
domain, missing prompts, missing L2 normalisation, different chunk granularity, different
leaderboard filters, vendor-side API options, and ANN approximation where MTEB scores exact top-k.

That note is also the most useful single artifact for our golden-question harness, because it is
essentially a checklist of how to *not* fool ourselves.

**(c) The leaderboard measures zero-shot generalisation, which is the opposite of our situation.**
Every dataset on it is out-of-domain by construction (BEIR's zero-shot protocol). We are deploying
into one client's narrow corpus and have 60 labelled questions to tune against. A model that wins on
breadth across 500 tasks is not thereby better on one client's invoices. Cirillo et al. say the quiet
part out loud in their own results: models "whose strong in-domain training prevents generalization"
are exactly the ones the zero-shot protocol punishes, and the ranking they produce is a ranking of
generalisation, not of fitness.

**(d) Models have converged, so the top of the board is mostly noise for our purposes.** A
November 2025 practitioner study over 8 datasets (2 private, 2 multilingual, 4 public) and 13 models,
scoring with an LLM judge on ELO, reported that about **85 percent of models fall in the same narrow
50-ELO band**, the top four are ~23.5 ELO apart, and rank 1 to rank 10 is roughly a 3 percent
difference. The poster's conclusion — "the real difference comes from the other parts of the
pipeline" — is the same conclusion the measured evidence in §5.2 pushes us toward.

**What I am *not* claiming.** Leaderboard saturation is not the same as contamination, and I did not
find a specific contamination result for any model here. I also did not find a clean study
quantifying how much of MTEB's retrieval subset leaks into training data for the Qwen3 or harrier
families. Treat "contamination" as a live concern and an unproven one, not as an established fact.
The defensible claims are the four above: incomparable scales, protocol fragility, wrong
distribution, and convergence.

### 5.2 The strongest measured counter-evidence, and it points away from bge-m3

Cirillo et al. (arXiv 2605.23618, May 2026) is the best evidence I found because it runs
**independently**, on **CPU**, with the **correct prefixes applied** ("for the E5 model variants, we
prepend instruction prefixes"), and publishes a reproducible pipeline. nDCG@10 on four BEIR
subsets:

| Model | FiQA-2018 | NFCorpus | SciFact | TREC-COVID | **Average** |
|---|---|---|---|---|---|
| Google Embeddings 2 (API) | 0.582 | 0.409 | 0.762 | 0.799 | **0.638** |
| `intfloat/multilingual-e5-large` | 0.438 | 0.341 | 0.704 | 0.702 | **0.546** |
| `intfloat/e5-large-v2` | 0.411 | 0.374 | 0.722 | 0.646 | **0.538** |
| **`BAAI/bge-m3`** | **0.366** | **0.294** | **0.650** | n/a | **0.437** |
| `paraphrase-multilingual-mpnet-base-v2` | 0.174 | 0.172 | 0.317 | 0.308 | 0.243 |
| `LaBSE` | 0.069 | 0.155 | 0.378 | 0.151 | 0.188 |

Read this carefully, because it cuts three different ways.

1. **bge-m3 loses to multilingual-e5-large by 0.109 nDCG@10 average, and loses on every single
   subset.** Their explanation: BGE-M3's multi-task training (dense + sparse + multi-vector) "does
   not yield superior zero-shot passage retrieval performance compared with the simpler,
   retrieval-focused contrastive objective used in E5... models trained with additional supervision
   for auxiliary, non-retrieval tasks can incur a retrieval-specific performance penalty." That is a
   direct hit on the main architectural reason people pick bge-m3.
2. **It also says the small model sometimes wins, in a way that is not a gimmick.** `e5-large-v2` is
   English-only and beats multilingual-e5-large on NFCorpus and SciFact. Just five models in, the
   ordering is already dataset-dependent. This is the paper undercutting its own headline finding,
   which is a mark in its favour.
3. **It simultaneously weakens the "bigger is better" instinct.** GE2 wins everywhere, but the
   authors' own practical conclusion is that multilingual-e5-large "reaches within 0.003 nDCG of GE2
   on Italian at 31 ms, making it the preferred option when sub-100 ms SLAs matter." A 560M local
   model tied a frontier hosted model on an in-domain corpus.

**How much weight to give this for our decision.** It is the most rigorous thing I found, and it
argues bge-m3 is a weaker *zero-shot English/multilingual retriever* than multilingual-e5-large. I
take that seriously. What it does **not** show:

- It does not test our domain. FiQA (financial QA) is the closest, and is the one where bge-m3 does
  worst (0.366 against 0.438 — a 0.072 gap). That is the number that should worry us most and the
  reason this report does not simply wave the evidence away.
- It is five models, four datasets, one corpus, one language pair, one CPU. Small-N.
- It does not test hybrid search, reranking, or page-aware chunking — the pipeline that actually
  determines our answer quality.
- It is a single research group, not a production report.

### 5.3 The measured evidence where a smaller model wins on a narrow corpus

The lelabdev multilingual benchmark (FR/EN/JP, OpenRouter-hosted, 500-char chunks, 30 golden
queries over 59 chunks) is small-N and I will not over-claim it. But it is a *direct* measurement of
the effect we care about — a narrow corpus with domain vocabulary, not a leaderboard:

| Model | Params | Hit Rate @5 | MRR | Embed time (59 chunks, hosted API) |
|---|---|---|---|---|
| `mistral-embed-2312` | ~335M | **100.0%** | **0.836** | 20.3 s |
| **`BAAI/bge-m3`** | 568M | 96.7% | **0.797** | 32.7 s |
| `Qwen3-Embedding-8B` @ 1024d | 8B | 100.0% | 0.750 | 298.4 s |
| `Qwen3-Embedding-8B` @ 512d | 8B (MRL) | 100.0% | 0.736 | 212.4 s |
| `Qwen3-Embedding-8B` @ 4096d | 8B | 100.0% | 0.739 | 263.7 s |

**bge-m3 beat an 8B model on MRR on this corpus, at roughly one-fourteenth the parameters, and it was
9× faster to embed.** Note also: MRL truncation of the 8B to 512d cost 0.014 MRR while cutting
vector size 8× — a good illustration that dimension reduction is usually the cheapest lever
available, and that it is worth measuring rather than assuming.

The caveats are real: 59 chunks and 30 queries, Wikipedia articles rather than business documents,
timings through a hosted API so the "embed time" mostly measures OpenRouter's serving, not CPU. I
present it because it is **measured**, it is a **narrow-corpus result**, and it points the opposite
way from the leaderboard — which is precisely the phenomenon the brief asked me to address.

### 5.4 Why a smaller model sometimes wins, mechanically

Four mechanisms, so the pattern is predictable rather than mysterious:

1. **Domain shift cuts both ways.** A model trained on heterogeneous web data has to spread its
   capacity across every domain. A model whose training mixture happens to resemble the target
   distributes more capacity where we need it. FiQA is finance; FiQA is where bge-m3 does worst and
   where the paper attributes GE2's win partly to asymmetric conditioning. A legal or medical corpus
   would reshuffle the same table.
2. **Lexical overlap dominates in narrow corpora.** Business questions about a specific contract
   share surface vocabulary with the answer ("what is the notice period in the Acme MSA" → the
   passage says "notice period" and "Acme MSA"). When lexical overlap is high, BM25/hybrid search
   already nails it, and the dense model's contribution shrinks. This is the strongest argument that
   our hybrid-search design matters more than the dense model, and the strongest argument against
   spending effort on the dense model.
3. **Asymmetric/instruction conditioning interacts with query-document surface divergence.** The
   forum case in §5.1(b) is exactly this: Qwen3's advantage over its own ablated self was 17 points,
   all of it from using the query instruction. A model with no asymmetry requirement (bge-m3) has a
   lower ceiling but a lower floor and no way to be silently misconfigured.
4. **Multi-task capacity tax.** bge-m3's own evidence, from an independent group, that its sparse and
   multi-vector heads cost it dense retrieval accuracy. We do not use those heads anyway (§3.5), so
   we are paying the tax without collecting the benefit — unless hybrid search recovers more than
   the 0.109 gap. **That is an open, testable question and it is the single most valuable experiment
   to run in the golden-question harness.**

### 5.5 The recommendation, with its evidence separated

**Recommendation: keep `BAAI/bge-m3` at 1024 dimensions, CLS pooling, no prefix, as the default dense
retriever.**

| Claim | Evidence class |
|---|---|
| Licence is MIT and shippable | **Verified** from model card frontmatter and FlagEmbedding `LICENSE` |
| TEI serves it as `BertModel` with correct `cls` pooling auto-detected | **Verified** from `config.json`, `1_Pooling/config.json`, TEI source |
| No prefix and no query/passage asymmetry, so it cannot be silently misconfigured | **Verified** from model card and TEI source; failure cost of getting it wrong is **measured** at up to 17 points |
| It is not the best zero-shot retriever; multilingual-e5-large beats it by 0.109 nDCG@10 avg on BEIR | **Measured**, one academic source, external validity limited |
| A smaller/older model can and does beat larger ones on narrow corpora | **Measured**, two independent sources (lelabdev; the Bible-verse study and its prompt ablation) |
| Query-time CPU latency ~31 ms, not a bottleneck | **Measured**, one source, Apple M-series |
| Bulk CPU ingestion is hours-to-days | **My arithmetic only** — must be measured on m7i.4xlarge before any client commitment |
| Hybrid + rerank contributes more than the dense-model choice | **My inference**, well-supported but not directly measured for our stack |
| Staying put avoids a re-embed, a re-validation, and a class of configuration bug | **My judgement** |

**What would change my mind, in priority order:**

1. **The golden-question harness says bge-m3 loses to multilingual-e5-large by more than a couple of
   points on retrieval.** The BEIR gap is large enough that this is plausible. e5 is MIT, 512-token
   context, mean pooling, needs `query: `/`passage: ` prefixes, and is fully TEI-served — a
   drop-in with a prefix discipline added. If it wins, take it, and treat the prefix convention as
   the §8 contract requires.
2. **A client's corpus is materially non-English.** Then the candidates are harrier-oss-v1-0.6b
   (MIT, MTEB v2 69.0, TEI-served) and multilingual-e5-large. Test, do not assume from `language:`
   metadata.
3. **Ingestion throughput turns out to be the binding constraint** on the office build, where there
   is no temporary big instance to rent. Then harrier-oss-v1-270m at ~262M params is the interesting
   lever, at the cost of introducing a model with no production history.

**What I would not do:** switch to `google/embeddinggemma-2`. It is Apache-2.0 and ungated, which is
genuinely attractive, its Matryoshka support down to 128 dimensions is real, and Google reports
MTEB v2 multilingual 61.36 against EmbeddingGemma-1's 61.15 — **a generational improvement of 0.21
points, which is not an improvement.** Its own card shows the win is in code (78.68 vs 68.76) and in
multimodality, neither of which is our problem. It is also `EmbeddingGemma2Model`, which TEI does not
support (§3), and EmbeddingGemma-1's own measured behaviour on the Bible corpus was 46.4 percent,
24th of 27 — *worse than `bge-small-en`*. Trading a verified, deployed, MIT, TEI-supported model for
a 0.21-point claim on a different MTEB scale and an unsupported architecture is not a professional
move.

---

## 6. Options rejected, and why

### 6.1 The office build's offline constraint is a first-class filter

Worth stating separately because it is under-weighted in most comparison articles. The office build
has **no internet at runtime**. That means:

- The model must be **vendored** — downloaded at deploy time, mounted as a local path, and served
  with `--model-id /data/<dir>`. TEI documents exactly this: "To deploy Text Embeddings Inference in
  an air-gapped environment, first download the weights and then mount them inside the container
  using a volume."
- **Gated models are a poor fit** even when the licence is acceptable, because access requires a
  HuggingFace account and a click-through. `google/embeddinggemma-300m` is `gated: manual`. For a
  product we install in a client's building, "our installer needs an HF token tied to a personal
  account" is an operational and privacy liability we should not accept. `google/embeddinggemma-2` is
  ungated, which removes this objection but not the TEI objection.
- Any model whose TEI support depends on `trust_remote_code` is out (§3).
- Whatever we choose must be **pinned by revision**, not by `main`, so a reinstall in three years
  reproduces the same vectors.

bge-m3 satisfies all four. It is also already downloaded and pinned in the existing stack.

### 6.2 Rejected, with reasons

| Option | Why rejected |
|---|---|
| `jinaai/jina-embeddings-v3` | **CC-BY-NC-4.0.** Non-commercial. Verified from model card frontmatter: `license: cc-by-nc-4.0`. Jina sells a commercial licence but I could not find a published price — Jina's own model page for v4 states it is under the Qwen Research License with no commercial option for that model. **A licence we cannot see the price of is not a licence we can ship on.** Their on-prem toolkit (`jina-ai/jina-on-prem`) is real and air-gap-oriented, but the licence gate comes first |
| `jinaai/jina-embeddings-v4` | Ships the **Qwen Research License Agreement** as its `LICENSE` file. I read it: "FOR NON-COMMERCIAL PURPOSES ONLY. If you are commercially using the Materials, you shall request a license from us." Disqualified. Also GPU-oriented, multimodal, and it is the reason I checked every other Jina artefact rather than trusting the family reputation |
| `jinaai/jina-embeddings-v5-text-small` | CC-BY-NC-4.0 weights per the model card, despite being built on Apache-2.0 Qwen3-0.6B-Base. Good model, unusable licence |
| `nvidia/llama-embed-nemotron-8b` | Card states plainly: "This model is for non-commercial/research use only", licence `customized-nscl-v1` plus the Llama 3.1 Community License. Disqualified on both counts. (Also 8B — wrong CPU tier.) Worth naming because it tops multilingual MTEB and would otherwise be a tempting pick |
| `google/embeddinggemma-300m` | Gemma terms of use, `gated: manual`. Also 2K context, and its measured behaviour on a narrow corpus was poor (§5.5). Double-disqualified |
| `google/embeddinggemma-2` | Apache-2.0 and ungated — **the licence is fine**. Rejected on architecture (`EmbeddingGemma2Model` is not in TEI's dispatch) and on the absence of any measured retrieval advantage over its predecessor (61.36 vs 61.15 MTEB v2) |
| `Qwen/Qwen3-Embedding-4B` / `-8B` | Apache-2.0 and TEI-served, so no licence objection. Rejected on **CPU cost**: 28× and 57× bge-m3's per-token cost, on a 16-vCPU instance, for a gain that the lelabdev measurement did not show on a narrow corpus (8B lost to bge-m3 on MRR) |
| `Alibaba-NLP/gte-Qwen2-7B-instruct`, `gte-Qwen2-1.5B-instruct` | Apache-2.0, 131072-token configs, last-token pooling. Rejected: 7B is wrong for CPU; both carry `auto_map` custom code and, for the 1.5B, the same practical role as Qwen3-Embedding which supersedes it (Qwen3-Embedding's own paper positions it as the successor to GTE-Qwen) |
| `intfloat/e5-mistral-7b-instruct` | MIT, but 7B. Wrong CPU tier. Instruct-formatted, so also carries the prefix-misconfiguration risk without the reward |
| `intfloat/multilingual-e5-large` | **Not rejected — the leading alternative.** MIT, TEI-served, mean pooling, and it beats bge-m3 by 0.109 nDCG@10 on BEIR. Held back only by: 512-token context, a hard prefix requirement that can silently destroy the index if the ingestion and query paths disagree, and 2.3× bge-m3's per-token CPU cost. **This is the model to put in the harness against bge-m3** |
| `nomic-ai/nomic-embed-text-v1.5` / `v2-moe` | Apache-2.0, TEI-served, MRL, genuinely cheap on CPU (137M / 305M active). Rejected on: 512-token context in practice (v2-moe's card says 512 despite an 8192 `max_position_embeddings`), a `search_query:`/`search_document:` prefix requirement, and a 30528-token English-centric vocab. Fine model, wrong cross-lingual business-document fit |
| `Snowflake/snowflake-arctic-embed-l-v2.0` | Apache-2.0, 8192 context, cls pooling, MRL to 256, TEI-served, 568M — a genuinely credible drop-in. Rejected on absence of evidence: no MRL/quality numbers I could verify for our domain beyond a vendor BEIR 55.6 / MIRACL 55.8, and Snowflake's own release material is not independent. **Second reserve option after e5** |
| `mixedbread-ai/mxbai-embed-large-v1` | Apache-2.0, 1024-dim, MRL to 512, TEI-served. Rejected on 512-token context and English-centric behaviour; it is a 2024 English retriever and we need multilingual breadth |
| `NovaSearch/stella_en_400M_v5` | MIT — the licence is clean. But its `auto_map` points at custom `modeling.NewModel`, and its `modules.json` requires a `2_Dense_1024` projection. TEI *can* handle the Dense module via `modules.json`/`--dense-path`, but the custom architecture is the blocker. Also requires a query-side instruction (`s2p_query`) while passages take none — the asymmetry risk again. Worth noting: in Superlinked's measurements, stella beat Qwen3-Embedding-0.6B on FiQA (0.559 vs 0.473), NFCorpus (0.413 vs 0.369), SciFact (0.784 vs 0.695) and SCIDocs (0.250 vs 0.244). A genuinely strong small model that I am rejecting on toolchain compatibility, not quality |
| `voyageai/voyage-4-nano` | Apache-2.0, ungated, MRL 256–2048, 32K context, listed in TEI's supported-models doc. **Exciting and unverified.** `trust_remote_code` is required by its card and TEI has no such flag; TEI might still load it. 319M params, Qwen3 12-layer bidirectional. **If someone has an hour, this is the most interesting thing to test** — it is a 2026 model with a clean licence and a plausible CPU profile |
| `microsoft/harrier-oss-v1-0.6b` | **Not rejected — the leading alternative for non-English.** MIT (card frontmatter `license: mit`; **there is no `LICENSE` file in the repo** — the card is the only artifact, which is weaker than I would like). MTEB v2 multilingual 69.0 vs EmbeddingGemma's 61.15/61.36. Qwen3 architecture, last-token pooling, `include_prompt: true`, TEI-served. Rejected as the *default* only on: no production history, no Japanese/Chinese-specific published numbers, an `Instruct:` prefix convention, and 1.5× bge-m3's CPU cost per token |
| `microsoft/harrier-oss-v1-27b` | MIT and tops MTEB v2 at 74.3, but `gemma3_text` forces fp32 on CPU in TEI and 5376 dimensions. Multi-GPU territory |
| `sentence-transformers/all-MiniLM-L6-v2` | Apache-2.0 and still the most-downloaded embedding model on HuggingFace by a wide margin (223M downloads). Rejected: 512-token English-only 22M-parameter model from 2021, and the measured evidence shows it near the bottom of a narrow-corpus retrieval task. Its download count reflects tutorials, not fitness — a useful reminder that adoption is not evidence |
| `paraphrase-multilingual-MiniLM-L12-v2`, `paraphrase-multilingual-mpnet-base-v2` | Apache-2.0 and TEI-served, but paraphrase/STS objectives are the wrong inductive bias for retrieval. Measured at 0.243 average nDCG@10 on BEIR (mMPNet), well below every dedicated retriever |
| `sentence-transformers/LaBSE` | Apache-2.0, and a useful cautionary tale rather than an option: **0.188 average nDCG@10 on BEIR**, with 0.069 on FiQA, "below what one would expect from a random baseline". It has >10M downloads and is routinely recommended for multilingual retrieval. It was trained for cross-lingual sentence alignment, a symmetric objective, and retrieval is asymmetric. **Named here because it is the clearest evidence that popularity and leaderboard adjacency are not fitness** |
| `answerdotai/answerai-colbert-small-v1`, `jinaai/jina-colbert-v2`, ColBERT/ColPali family | Late-interaction and page-image retrieval is genuinely attractive for scanned, visually rich documents — that is our corpus type. Rejected for now because it requires multi-vector storage and MaxSim scoring in Qdrant (Qdrant supports it, but the pipeline changes), it does not use TEI, and it is a larger architectural change than an embedding swap. **Worth a separate evaluation for the scanned-PDF/table problem, which is where the build brief already says the real risk is** |
| `ibm-granite/granite-embedding-278m-multilingual` | Apache-2.0, 514-token context. Measured at 64.7 percent on the Bible corpus, mid-table. No advantage over bge-m3 |
| Alternative **servers**: infinity, fastembed, ONNX Runtime | TEI is Apache-2.0, is already in our compose, and handles every candidate in §2. Switching servers is a larger change than switching models and would not fix the licence or the offline constraints. Not evaluated further because it is out of scope for this angle |

### 6.3 One more option considered and rejected on cost

**Fine-tuning a small embedding model on the client's own corpus.** This is the highest-ceiling
option: it is exactly how a domain-specific model beats a general one, and it is why narrow-corpus
results in §5.3 look the way they do. Rejected as a **default** for three reasons: we have 60 golden
questions per client, which is not enough to train on without overfitting; it makes each deployment
a bespoke artifact we must maintain; and it destroys the "models are configuration" property that
makes the two builds shareable. It is, however, the right answer for a client willing to pay for it,
and the §8 contract is what keeps that door open.

---

## 7. Benchmark limitations: the summary the decision document needs

For the decision record, stated as plainly as I can:

1. **MTEB is no longer one benchmark.** MMTEB/MTEB v2 replaced it. Scores quoted from different
   months are on different scales and are not comparable. Never put a Qwen3-Embedding-8B "70.58"
   next to a harrier "74.3" in a table without a footnote.
2. **Leaderboard ranking is a zero-shot generalisation ranking.** We deploy to one corpus with
   labelled examples. The benchmark's design goal is the opposite of our situation.
3. **Small protocol changes flip rankings.** 17 accuracy points from adding a query instruction is
   the documented case. Metric choice, normalisation, chunk size, and ANN-vs-exact all move the
   order too.
4. **Convergence is real for the practical shortlist.** ~85 percent of models inside a 50-ELO band;
   3 percent between rank 1 and rank 10 on an independent multi-dataset study.
5. **Domain adaptation beats size.** The mechanisms in §5.4 are predictable, and both the
   narrow-corpus measurements I found showed a smaller or older model winning.
6. **Contamination is a live concern and I did not verify it.** I found no clean study for these
   models. Do not assert contamination; do not assume cleanliness either.
7. **What generalises across all the evidence I read:** the retrieval *pipeline* — chunking, hybrid
   fusion, reranking, and verification — dominates the embedding-model choice. Cirillo et al.'s
   chunking ablation is a good example of how much the non-model design choices move the number.
8. **The correct process for us is the harness.** 60 golden questions per client, run on every model
   swap, every chunking change, every parser upgrade — which is already the build brief's §10 rule.
   This report's job is to set the default and make the swap cheap; the harness decides.

---

## 8. Reversibility: what to store so a model change is a re-embed, not a migration

This is the part that makes the recommendation safe, and it should be built regardless of which model
wins.

### 8.1 The embedding contract

The failure mode we are preventing is specific and common: **half the corpus embedded with one
model, half with another, in the same collection, producing cosine similarities between unrelated
vector spaces.** That does not error. It returns plausible rubbish. For a system whose selling point
is that answers are verifiable, it is the worst possible failure.

Store, per collection, as a first-class record (a row in Postgres, not a comment in a compose file):

| Field | Why | Example |
|---|---|---|
| `model_id` | Identifies the weights | `BAAI/bge-m3` |
| `model_revision` | A branch name is not a pin. `main` moves and silently changes vectors | the commit sha |
| `model_sha256` | Proves the weights are the ones validated | hash of the safetensors |
| `dimension` | Qdrant collection is fixed at creation | `1024` |
| `pooling` | TEI auto-detects, but record what was *validated* so a TEI upgrade can't silently change it | `cls` |
| `normalize` | Cosine on unit vectors. TEI defaults true; record it | `true` |
| `query_prefix` | **The one that bites** | `""` |
| `passage_prefix` | For bge-m3 both are empty. For e5 they are `query: ` and `passage: ` | `""` |
| `prompt_name_query` / `prompt_name_passage` | The TEI `prompt_name` keys, when the model defines a `prompts` dict | `null` for bge-m3 |
| `truncate_dim` | Matryoshka target, applied client-side because TEI will not do it | `1024` (= native) |
| `max_tokens` | The hard ceiling for the pre-send assertion (§4.4). Must be under the model's real limit | `8192` |
| `chunker_version` | Chunk boundaries change which vectors exist | `docling-structure-v1` |
| `parser_version` | Docling upgrade changes extracted text, hence vectors | `docling-x.y.z` |
| `schema_version` | Which of these fields existed when the vectors were written | `1` |

### 8.2 Three enforcement rules

1. **The write path stamps it; the read path checks it.** Ingestion writes the contract into the
   collection metadata (and into every chunk's payload, cheaply, or at least into a collection-level
   record keyed by a collection-generation id). Retrieval reads it and **refuses to serve** on
   mismatch. A refusal is a valid outcome for this system; a wrong answer is not.
2. **A model change creates a new collection generation, never an in-place update.** Qdrant
   collections are immutable in dimension. The pattern is: build `company_docs_v2` alongside
   `company_docs_v1`, verify with the golden questions, flip an alias, drop v1 later. That makes
   rollback a pointer change and makes the swap safe to do on a Tuesday.
3. **Re-embedding is idempotent and resumable.** The ingestion worker already does delta re-embeds
   and uuid5 point ids on `relpath:chunk`; extend that so a generation id is part of the id, and a
   crash mid-re-embed resumes rather than restarting. At ~28 hours per 50k pages of CPU time (§4.3),
   a re-embed that cannot resume is not a plan.

### 8.3 Why this makes the choice genuinely reversible

With that contract in place, changing the embedding model is:

1. Add the new model to compose with its own TEI service (pin `cpu-1.9` or the GPU tag per build).
2. Render the new extraction config from the manifest — no code change, because the prefix
   convention and dimension come from config.
3. Run ingestion into a new collection generation. On a temporary big instance for anything over a
   few thousand pages.
4. Run the 60 golden questions against the new generation. Compare against the recorded baseline.
5. Flip the alias, or don't.

**No schema migration, no application change, no data loss, and a one-command rollback.** That is the
property that lets us pick bge-m3 today without betting the product on it, and it is worth more than
the 0.109 nDCG@10 that multilingual-e5-large is currently offering.

### 8.4 What is *not* reversible, and should be decided with more care

- **The vector dimension is baked into the Qdrant collection.** Changing 1024 → 768 is a re-embed of
  everything. This is why the default should be the model's native dimension and MRL should be used
  only with a measured reason.
- **Layout and structure decisions in the chunker** are as consequential as the model and much harder
  to change, because they change what text a page number refers to. Page-aware, table-preserving
  chunking (§7 of the build brief) is the right call and should not be traded away for embedding
  convenience.
- **The reranker choice** interacts with the dense model. If we ever swap to a reranker trained on a
  different query distribution, the dense/rerank pair should be re-tuned together.

---

## 9. What I could not verify

Stated explicitly, per the brief's standard:

- **No measured CPU throughput figure for TEI, for any model.** The TEI docs publish only an
  A10 GPU benchmark. §4.3 is arithmetic and is labelled as such. **This must be measured on
  m7i.4xlarge before any client-facing throughput promise.**
- **No measured figure for our own domain.** Every number in §5.2 and §5.3 is from a different
  corpus. None of them tested contracts, policies, invoices, or spreadsheets.
- **`voyageai/voyage-4-nano` under TEI is unverified.** It is listed in TEI's docs, but its
  `remote_code` requirement and the absence of a `--trust-remote-code` flag leave it genuinely
  unclear. I did not run it.
- **`microsoft/harrier-oss-v1-0.6b` has no `LICENSE` file in its repository.** The `license: mit`
  declaration is in the model card frontmatter only. I would want that confirmed from Microsoft
  before shipping it in a paid build — a card declaration is weaker evidence than a licence file.
- **Jina's commercial licence price is not published** anywhere I could find. Their on-prem toolkit
  is real, but I cannot tell you what the licence costs, so I cannot evaluate it as an option.
- **`include_prompt` handling in TEI is unclear.** Qwen3-Embedding, harrier, stella, and
  voyage-4-nano all set `include_prompt: true`; TEI's `PoolConfig` does not deserialise the field.
  Whether TEI prepends the prompt before pooling I did not establish. Test it if we ever adopt one
  of those models.
- **No contamination study** for Qwen3-Embedding, harrier, or EmbeddingGemma on MTEB retrieval
  subsets. Saturation and protocol fragility are demonstrated; contamination is not.
- **The `lelabdev` benchmark is hosted-API-only**, so its "embed time" column measures OpenRouter's
  serving rather than local CPU. I used it for retrieval quality only.
- **Dates and version numbers** are as of 2026-10-11. This field moves monthly; TEI 1.9.4 shipped
  2026-09-15 and EmbeddingGemma 2 shipped 2026-10-06, five days before this was written.

---

## 10. Sources

Licences, configs, pooling and TEI behaviour — all fetched 2026-10-11.

- TEI licence (Apache-2.0): <https://raw.githubusercontent.com/huggingface/text-embeddings-inference/main/LICENSE>
- TEI supported models: <https://github.com/huggingface/text-embeddings-inference/blob/main/docs/source/en/supported_models.md>
- TEI CLI arguments: <https://github.com/huggingface/text-embeddings-inference/blob/main/docs/source/en/cli_arguments.md>
- TEI model dispatch and pooling resolution: <https://github.com/huggingface/text-embeddings-inference/blob/main/backends/candle/src/lib.rs> and <https://github.com/huggingface/text-embeddings-inference/blob/main/router/src/lib.rs>
- TEI request schema (`prompt_name`, `normalize`): <https://github.com/huggingface/text-embeddings-inference/blob/main/router/src/http/types.rs>
- TEI v1.9.4 release: <https://github.com/huggingface/text-embeddings-inference/releases/tag/v1.9.4>
- TEI bge-m3 sparse support issue: <https://github.com/huggingface/text-embeddings-inference/issues/141>
- `BAAI/bge-m3`: <https://huggingface.co/BAAI/bge-m3> · FlagEmbedding MIT licence: <https://raw.githubusercontent.com/FlagOpen/FlagEmbedding/master/LICENSE>
- `Qwen/Qwen3-Embedding-0.6B`: <https://huggingface.co/Qwen/Qwen3-Embedding-0.6B> · 8B Apache-2.0 LICENSE: <https://huggingface.co/Qwen/Qwen3-Embedding-8B/raw/main/LICENSE>
- Qwen3 Embedding paper: <https://arxiv.org/abs/2506.05176>
- `microsoft/harrier-oss-v1-0.6b`: <https://huggingface.co/microsoft/harrier-oss-v1-0.6b>
- `intfloat/multilingual-e5-large`: <https://huggingface.co/intfloat/multilingual-e5-large>
- `google/embeddinggemma-2`: <https://huggingface.co/google/embeddinggemma-2>
- `google/embeddinggemma-300m` (gated, Gemma terms): <https://huggingface.co/google/embeddinggemma-300m>
- `jinaai/jina-embeddings-v3` (CC-BY-NC-4.0): <https://huggingface.co/jinaai/jina-embeddings-v3>
- `jinaai/jina-embeddings-v4` (Qwen Research License): <https://huggingface.co/jinaai/jina-embeddings-v4> · licence text: <https://huggingface.co/jinaai/jina-embeddings-v4/raw/main/LICENSE> · model page: <https://jina.ai/models/jina-embeddings-v4/>
- `nvidia/llama-embed-nemotron-8b` (non-commercial): <https://huggingface.co/nvidia/llama-embed-nemotron-8b>
- `Snowflake/snowflake-arctic-embed-l-v2.0`: <https://huggingface.co/Snowflake/snowflake-arctic-embed-l-v2.0>
- `nomic-ai/nomic-embed-text-v1.5`: <https://huggingface.co/nomic-ai/nomic-embed-text-v1.5>
- `NovaSearch/stella_en_400M_v5`: <https://huggingface.co/NovaSearch/stella_en_400M_v5>
- `voyageai/voyage-4-nano`: <https://huggingface.co/voyageai/voyage-4-nano>
- `mixedbread-ai/mxbai-embed-large-v1`: <https://huggingface.co/mixedbread-ai/mxbai-embed-large-v1>

Measured evidence.

- Cirillo, Desiato, Polese, Solimando, "Benchmarking Google Embeddings 2 against Open-Source Models for Multilingual Dense Retrieval and RAG Systems", arXiv:2605.23618, May 2026 — BEIR nDCG@10, Italian RAG benchmark, CPU latency, chunking ablation: <https://arxiv.org/html/2605.23618v1>
- Enevoldsen et al., "MMTEB: Massive Multilingual Text Embedding Benchmark", arXiv:2502.13595, ICLR — 500+ tasks, 250+ languages, and the finding that a 560M model topped the public leaderboard: <https://arxiv.org/abs/2502.13595>
- HuggingFace forum thread, "Why are my benchmark results so different from the MTEB leaderboard?", September 2025 — the 532-query Bible retrieval benchmark and the 17-point prompt ablation: <https://discuss.huggingface.co/t/why-are-my-benchmark-results-so-different-from-the-mteb-leaderboard/168305>
- Methodology note accompanying that thread (metric, prompt, normalisation and leaderboard-scope differences): <https://huggingface.co/datasets/John6666/forum1/blob/main/mteb_dif.md>
- HuggingFace forum thread, "RAG: Embedding models have converged", November 2025 — 13 models, 8 datasets, ELO: <https://discuss.huggingface.co/t/rag-embedding-models-have-converged/170555>
- lelabdev multilingual embedding benchmark (FR/EN/JP, bge-m3 vs Qwen3-Embedding-8B at three MRL dimensions): <https://github.com/lelabdev/embedding-benchmark>

Vendor and secondary claims, labelled as such where used.

- Superlinked, "Qwen3 embeddings and rerankers: 0.6B vs 4B", August 2026 — NDCG@10 rows where stella beats Qwen3-Embedding-0.6B, and the 1–5% instruction-ablation figure: <https://superlinked.com/blog/qwen3-embedding-reranker-guide>
- AY Automate, "12 Best Embedding Models for RAG (2026)", September 2026 — useful for the observation that MTEB v2, MTEB multilingual and MTEB English v2 scores are being quoted as if comparable: <https://www.ayautomate.com/blog/best-embedding-models>
- Elastic Search Labs, "On-premises AI: Jina embedding models", July 2026 — Jina commercial licences exist, priced proportionately, no published figure: <https://www.elastic.co/search-labs/blog/on-prem-ai-jina-embedding-models>
- Jina on-prem air-gapped toolkit (rejected on licence, not on capability): <https://github.com/jina-ai/jina-on-prem>
