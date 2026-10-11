# Reranking, and how much accuracy lives here

**Angle 5 of the retrieval-architecture pass.** Written 2026-10-11 for Office Inference, against the
constraints in `docs/build-brief.md` and `docs/architecture.md` (office-inference-core).

**Evidence labels used throughout.** `[measured]` = I ran it on this machine and report the number
with its conditions. `[paper]` = a published result I read in the source. `[vendor]` = the model
owner's or project's own claim, unverified by me. `[primary-verified]` = I read the licence text or
the config myself. `[inference]` = my reasoning, not evidence. `[unverified]` = I could not confirm it.

---

## 0. The answer in six lines

1. **Reranking is the highest-value component in this stack after ingestion quality.** BEIR measured
   a cross-encoder over BM25 as the single best zero-shot system of ten evaluated, and the only one
   that beat BM25 on 16 of 18 datasets. `[paper]` The gain is not marginal.
2. **Use `BAAI/bge-reranker-v2-m3`.** Apache-2.0 verified, 567.8 M params, XLM-RoBERTa
   sequence-classification, multilingual, and already the model in the existing ai-stack. `[primary-verified]`
3. **Jina is out on licence.** Every Jina reranker and ColBERT model is `cc-by-nc-4.0`, and Jina's own
   deployment FAQ says on-premises commercial use needs a paid licence with no public price. `[primary-verified]`
4. **Qwen3-Reranker and mxbai-rerank-v2 cannot be served by TEI at all.** They are causal LMs, not
   sequence classifiers. TEI's CPU image serves rerankers, but only XLM-RoBERTa/Camembert/RoBERTa/GTE/ModernBERT. `[primary-verified]`
5. **Retrieve ~100, rerank 40 to 50.** Reranking beyond 50 buys about one point while latency grows
   linearly, and when first-stage recall is already ~99% more candidates actively hurt. `[paper]`
6. **Do not use a small local model as a listwise reranker.** Zero-shot listwise 3B/7B/14B models
   score *below* BM25 on TREC-DL. It is the one option with measured evidence against it. `[paper]`

---

## 1. What reranking is worth: the measured quality gain

### 1.1 BEIR, the strongest available evidence

Thakur et al., *BEIR: A Heterogeneous Benchmark for Zero-shot Evaluation of Information Retrieval
Models* (NeurIPS 2021, [arXiv:2104.08663](https://arxiv.org/abs/2104.08663)). I read the paper text,
including Table 2 and Table 3. `[paper]`

Ten systems across five architectures, 18 datasets, zero-shot, nDCG@10, all neural models truncated
at 512 wordpieces. The two relevant rows:

| System | Architecture | Avg nDCG@10 (my mean over the 18 zero-shot datasets) | vs BM25 | Datasets beating BM25 |
|---|---|---|---|---|
| BM25 (Anserini) | lexical | 0.423 | baseline | — |
| **BM25 + cross-encoder** (6-layer, 384-dim MiniLM, top-100 reranked) | re-ranking | **0.476** | **+12.5% relative** | 16/18 (paper's count) |
| ColBERT | late interaction | 0.431 | +1.7% relative | 9/18 |

**Reconciling my number with theirs, because it matters.** Table 2 in the paper has 19 rows: the 18
BEIR datasets plus MS MARCO, which is marked as in-domain (the cross-encoder was trained on it). I
excluded MS MARCO to get the zero-shot average above, because including it understates reranking —
BM25 is already at 0.658 there and reranking does nothing. Including MS MARCO the averages are
BM25 0.413, BM25+CE 0.473, i.e. **+14.5%**. The paper's own summary row states **"+11%"** for
BM25+CE, which I could not reproduce from any subset of Table 2 I can construct; the aggregation
almost certainly folds in the additional in-domain MS MARCO variants they evaluated. **All three
figures are above. The direction and rough size are the same under every choice, and I am not going
to quote only the most flattering one.** `[paper]` + `[inference]`

One more number from that table worth carrying: **BM25+CE beats ColBERT on 18 of the 19 rows**, so the
cross-encoder's advantage over late interaction is not a single-dataset artefact. `[paper]`

The three findings from that paper that matter to us:

- **Cross-attention generalises, bi-encoders do not.** "The cross-attentional re-ranking model
  (BM25+CE) performs the best and is able to outperform BM25 on almost all (16/18) datasets. It only
  fails on ArguAna and Touché-2020." Dense retrievers (DPR, ANCE, TAS-B) underperformed BM25 on many
  datasets despite winning in-domain. `[paper]`
- **Late interaction is weaker than a cross-encoder.** ColBERT landed ~0.044 nDCG@10 below BM25+CE
  and beat BM25 on only 9 of 18 datasets. `[paper]`
- **The cost is real and they measured it.** Table 3, 1 M DBPedia documents, one query:
  BM25+CE (top-100 rerank) **450 ms on GPU, 6,100 ms on CPU**. BM25 itself is 20 µs-class.
  ColBERT is 350 ms GPU and needs the largest index of any system tested (~900 GB for BioASQ's
  15 M documents vs 18 GB for BM25). `[paper]`

That 6,100 ms CPU figure is the single most important number in this report. BEIR measured the best
system in their study at over six seconds per query on CPU. **This entire architecture lives or dies
on that number**, and it is why §5 exists.

### 1.2 The reranker vendors' own numbers

`[vendor]` unless noted. All read from the model cards.

| Model | Params | BEIR avg | Multilingual | Notes |
|---|---|---|---|---|
| nvidia/llama-nemotron-rerank-1b-v2 | 1,236 M | — | — | card read, no BEIR figure |
| mxbai-rerank-large-v2 | 1,544 M | 57.49 | 29.79 | latency 0.89 s *on A100* |
| mxbai-rerank-base-v2 | 494 M | 55.57 | 28.56 | latency 0.67 s *on A100* |
| mxbai-rerank-large-v1 | 435 M | 49.32 | 21.88 | latency 2.24 s *on A100* |

The A100 annotation matters: **a 1.5 B reranker takes ~0.9 s for a batch on an A100.** Transposed to
CPU that is not a small number, and it is the strongest argument against the large models in either
build.

Jina's card carries a fuller comparison, and it is the one place the models appear side by side on the
same harness. `[vendor]`

| Model | Size | BEIR | MIRACL | MKQA | CoIR |
|---|---|---|---|---|---|
| jina-reranker-v3 (listwise) | 0.6 B | 61.94 | 66.83 | 67.92 | 70.64 |
| mxbai-rerank-large-v2 | 1.5 B | 61.44 | 57.94 | 67.06 | 70.87 |
| Qwen3-Reranker-4B | 4.0 B | 61.16 | 67.52 | 67.52 | 73.91 |
| jina-reranker-m0 | 2.4 B | 58.95 | 66.75 | 68.19 | 63.55 |
| mxbai-rerank-base-v2 | 0.5 B | 58.40 | 55.32 | 64.24 | 65.71 |
| jina-reranker-v2 | 0.3 B | 57.06 | 63.65 | 67.90 | 56.14 |
| **bge-reranker-v2-m3** | **0.6 B** | **56.51** | **69.32** | **67.88** | **36.28** |
| Qwen3-Reranker-0.6B | 0.6 B | 56.28 | 57.70 | 65.34 | 65.18 |

Read this table carefully, because it cuts against the obvious recommendation. **A competitor's
benchmark says bge-reranker-v2-m3 is beaten on BEIR by four models and is worst-in-class on code
retrieval (CoIR 36.28).** But it is *best on MIRACL*, the multilingual retrieval set, and roughly tied
on MKQA. Jina has no incentive to flatter bge. `[vendor]` + `[inference]`

Three things follow. First, at the 0.5–0.6 B tier all of these are within ~6 nDCG points of each other;
the ordering is not stable across datasets. Second, the competitors that beat bge on BEIR are licensed
CC-BY-NC (jina) or cannot be served on CPU by TEI (Qwen3, mxbai-v2) — so the licence and serving
constraints, not quality, decide this. Third, **treat any single benchmark ranking here as noise.**
The 60-question golden set is the only measurement that will settle it for a given client.

### 1.3 What I could NOT find

- No independent, non-vendor head-to-head of these rerankers run on **CPU at production passage
  lengths**. Every latency figure published by a vendor is GPU. `[unverified]`
- No published, reproducible measurement of the *end-to-end answer* improvement from reranking in a
  citation-required RAG system, as opposed to retrieval nDCG. The 92%/85% gates in the build brief
  have no external comparandum. `[unverified]`

---

## 2. Licences, verified from the source

This is the constraint that binds first, and it eliminates two of the five named candidates.

### 2.1 Important structural finding

**Almost none of these model repositories contain a LICENSE file.** I listed the full file manifest of
each repo via the Hugging Face API. For `BAAI/bge-reranker-v2-m3`, `BAAI/bge-reranker-base`,
`jinaai/jina-reranker-v2-base-multilingual`, `jinaai/jina-reranker-m0`, `Qwen/Qwen3-Reranker-0.6B`,
`Alibaba-NLP/gte-reranker-modernbert-base`, `colbert-ir/colbertv2.0`, `jinaai/jina-colbert-v2` and
`BAAI/bge-m3` the only licence statement is the `license:` field in the README YAML frontmatter.
Only the two mixedbread-v2 models and `mxbai-rerank-base-v2` ship an actual `LICENSE` file
(Apache-2.0, 10,763 bytes, read directly). `[primary-verified]`

That matters commercially: **the licence we can point a client's counsel at is, for most of these, a
metadata field, not a document.** For anything we ship, archive the card frontmatter and the commit
hash at the time we pin the weights.

### 2.2 The table

All licence strings read from the model card frontmatter or the repo `LICENSE` on 2026-10-11.
Parameter counts are the total tensor element count from the safetensors metadata. `[primary-verified]`

| Model | Params | Licence (exact string) | Shippable? | Head type |
|---|---|---|---|---|
| BAAI/bge-reranker-v2-m3 | 567.8 M | `apache-2.0` | **Yes** | XLMRobertaForSequenceClassification |
| BAAI/bge-reranker-base | 278.0 M | `mit` | **Yes** | XLMRobertaForSequenceClassification |
| BAAI/bge-reranker-large | 559.9 M | `mit` | **Yes** | XLMRobertaForSequenceClassification |
| BAAI/bge-reranker-v2-gemma | 2,506 M | `apache-2.0` | Yes, but 2.5 B | classifier over Gemma-2B |
| BAAI/bge-reranker-v2-minicpm-layerwise | 2,725 M | `apache-2.0` | Yes, but 2.7 B | layerwise, needs custom code |
| Qwen/Qwen3-Reranker-0.6B | 595.8 M | `apache-2.0` | Yes | **Qwen3ForCausalLM** (generative) |
| Qwen/Qwen3-Reranker-4B | 4,022 M | `apache-2.0` | Yes, but 4 B | Qwen3ForCausalLM |
| Qwen/Qwen3-Reranker-8B | 8,189 M | `apache-2.0` | Yes, but 8 B | Qwen3ForCausalLM |
| mxbai-rerank-base-v2 | 494.0 M | `apache-2.0` | **Yes** | **Qwen2ForCausalLM** (generative) |
| mxbai-rerank-large-v2 | 1,544 M | `apache-2.0` | Yes | Qwen2ForCausalLM |
| mxbai-rerank-base-v1 | 184.4 M | `apache-2.0` | **Yes** | BERT cross-encoder |
| mxbai-rerank-large-v1 | 435.1 M | `apache-2.0` | Yes | BERT cross-encoder |
| gte-reranker-modernbert-base | 149.6 M | `apache-2.0` | **Yes** | ModernBertForSequenceClassification |
| gte-multilingual-reranker-base | 306.0 M | `apache-2.0` | **Yes** | GTE, needs trust_remote_code |
| ibm-granite/granite-embedding-reranker-english-r2 | 149.6 M | `apache-2.0` | Yes (English only) | ModernBERT |
| nvidia/llama-nemotron-rerank-1b-v2 | 1,236 M | `other` / `openmdw-1.1` | **Needs review** | see below |
| jinaai/jina-reranker-v2-base-multilingual | 278.4 M | **`cc-by-nc-4.0`** | **NO** | XLMRobertaForSequenceClassification |
| jinaai/jina-reranker-m0 | 2,445 M | **`cc-by-nc-4.0`** | **NO** | JinaVLForRanking (Qwen2-VL) |
| jinaai/jina-reranker-v3 | 596.8 M | **`cc-by-nc-4.0`** | **NO** | listwise, Qwen3-0.6B base |
| jinaai/jina-colbert-v2 | 559.5 M | **`cc-by-nc-4.0`** | **NO** | HF_ColBERT |
| jinaai/jina-reranker-v1-turbo-en | 37.8 M | `apache-2.0` | **Yes** | JinaBertModel, 6 layers |
| lightonai/GTE-ModernColBERT-v1 | 149.0 M | `apache-2.0` | **Yes** | HF_ColBERT (late interaction) |
| lightonai/Reason-ModernColBERT | 149.0 M | **`cc-by-nc-4.0`** | **NO** | fine-tune of the above |
| answerdotai/answerai-colbert-small-v1 | 33.4 M | `apache-2.0` | **Yes** | HF_ColBERT |
| colbert-ir/colbertv2.0 | 109.6 M | `mit` | **Yes** | HF_ColBERT |
| BAAI/bge-m3 | — | `mit` | **Yes** | XLM-RoBERTa; also does sparse + ColBERT |

**Serving software, licences read from the LICENSE file:** Text Embeddings Inference `Apache-2.0`,
FlashRank `Apache-2.0`, Infinity `MIT`, PyLate `MIT`. All four are shippable. `[primary-verified]`

### 2.3 The Jina disqualification, in their own words

This is worth quoting because it is unusually explicit about exactly our deployment model. From
`jina-ai/jina-on-prem`, `docs/FAQ.md`, read directly: `[primary-verified]`

> **Do we need to buy a license?** For most production use yes - Jina v5 and v3 models are
> CC-BY-NC-4.0, whose "NC" forbids commercial use without a license, and v4 is under the Qwen
> Research License. The Apache-2.0 models, free for any use, are the v2 text embeddings
> (`jina-embeddings-v2-base-*`), `jina-clip-v1`, `jina-colbert-v1-en` and the v1 embedding and
> reranker models; the License column of the Model Catalog is authoritative. Contact
> [Elastic sales](https://www.elastic.co/contact).

And from the `jina-reranker-v3` model card §License: `[primary-verified]`

> `jina-reranker-v3` is listed on AWS & Azure. If you need to use it beyond those platforms or
> **on-premises within your company**, note that the model is licensed under CC BY-NC 4.0. For
> commercial usage inquiries, feel free to [contact us](https://jina.ai/contact-sales/).

That is our office build, described by the vendor and refused. And `jina-reranker-v2-base-multilingual`,
the model named in our brief, has no LICENSE file at all — only `license: cc-by-nc-4.0` in frontmatter.

**No public price exists.** The FAQ routes to a sales contact. The commercial-terms URL historically
referenced by search results, `https://jina.ai/COMMERCIAL-LICENSE-TERMS.pdf`, returns **HTTP 404** —
I fetched it. So the brief's test, "no AGPL unless a commercial licence is available and priced",
fails on the *priced* half: a licence may be available, but it is not publicly priced and I cannot
verify its terms. `[primary-verified]` for the 404 and the FAQ; `[unverified]` for whether a licence
could be negotiated.

**Consequence:** jina-reranker-v2/v3 and jina-colbert-v2 are rejected. The only shippable Jina models
are the v1 generation, of which `jina-reranker-v1-turbo-en` (37.8 M, Apache-2.0) is interesting purely
as a latency play, not a quality one.

**One external fact worth flagging:** search results indicate Elastic completed an acquisition of Jina
AI in 2025 ([Elastic IR](https://ir.elastic.co/News--Events/news/news-details/2025/Elastic-Completes-Acquisition-of-Jina-AI-a-Leader-in-Frontier-Models-for-Multimodal-and-Multilingual-Search/default.aspx)).
I could not read that page — it returned HTTP 403 to my fetch. It is corroborated by the Jina FAQ
sending commercial enquiries to "Elastic sales", which I did read. `[unverified]` for the acquisition
itself, `[primary-verified]` for the Elastic sales routing. Either way the licence position is the
same, and a licence negotiation now runs through Elastic's procurement rather than a small vendor's.

### 2.4 The one that needs a lawyer: OpenMDW-1.1

`nvidia/llama-nemotron-rerank-1b-v2` (1,236 M) declares `license: other` with
`license_name: openmdw-1.1` and `license_link: https://openmdw.ai/license/1-1/`. OpenMDW is a
model-weights licence published by the Linux Foundation. I did **not** read the licence text, so I am
not asserting it is permissive. It is not Apache-2.0 and not MIT, which means it is not covered by
the "obviously fine" bucket. `[unverified]` — flag for review before adopting, and note that at
1.2 B it is an unlikely CPU choice regardless.

### 2.5 The Qwen3-Reranker generation trap

Licence is fine (Apache-2.0), but the architecture is not what the name suggests.
`Qwen/Qwen3-Reranker-0.6B/config.json` declares `architectures: ["Qwen3ForCausalLM"]`,
`model_type: qwen3`, 28 layers, 1024 hidden, bf16. `[primary-verified]`

Scoring is **generative**: you build the chat-templated prompt, read the logits at the final position,
and take `log_softmax([logit("no"), logit("yes")])[1]`. The exact system prompt, from the model card,
is:

> `Judge whether the Document meets the requirements based on the Query and the Instruct provided. Note that the answer can only be "yes" or "no".`

with the user turn `<Instruct>: {i}\n\n<Query>: {q}\n\n<Document>: {d}` and the assistant turn opened
with `<|im_start|>assistant\n`. `[primary-verified]` — I re-read my own first implementation against
this and it was wrong on the system prompt and the double newlines; the card is the authority.

Two consequences. It cannot be served by TEI (§3). And the card states that using a task-specific
instruction is worth "an improvement of 1% to 5%" over not using one — so the instruction is a
tuning parameter we would have to own, per client, and test with 60 questions. `[vendor]`

---

## 3. Serving on CPU: TEI, and what it will and will not load

### 3.1 The architecture gate — this is the finding that decides the shortlist

From the TEI README on `main` (workspace version 1.9.4 in `Cargo.toml`), section
*Sequence Classification and Re-Ranking*, read directly: `[primary-verified]`

> Text Embeddings Inference currently supports **CamemBERT, and XLM-RoBERTa Sequence Classification
> models with absolute positions.**

and section *Using Re-rankers models*:

> `text-embeddings-inference` v0.4.0 added support for CamemBERT, RoBERTa, XLM-RoBERTa, and GTE
> Sequence Classification models. Re-rankers models are Sequence Classification cross-encoders models
> with a single class that scores the similarity between a query and a text.

The supported-model table lists `bge-reranker-large`, `bge-reranker-base`,
`gte-multilingual-reranker-base` and `gte-reranker-modernbert-base`.

So TEI **can** serve: bge-reranker-v2-m3, bge-reranker-base/large, gte-reranker-modernbert-base,
gte-multilingual-reranker-base, jina-reranker-v2 (architecturally), mxbai-rerank-v1.

TEI **cannot** serve, at any version: `Qwen3-Reranker-*` and `mxbai-rerank-v2`, because they are
causal LMs. There is no `/rerank` path for a generative model in TEI, and no feature flag for one.
`[inference]`, from the architecture list being exhaustive for the sequence-classification task.

Partial evidence for ModernBERT specifically: the README's sequence-classification table names
`gte-reranker-modernbert-base` as supported, while the surrounding prose lists only CamemBERT,
RoBERTa, XLM-RoBERTa and GTE. The table is more specific and more recent; I treat ModernBERT as
supported but **note the README is internally inconsistent on this line.** `[primary-verified]` for
the inconsistency.

Also relevant: `BAAI/bge-reranker-v2-m3` carries the `text-embeddings-inference` tag on its model card,
i.e. upstream lists it as TEI-compatible. `[primary-verified]`

### 3.2 I ran the CPU image and served the reranker

This is the check that would have caught the empty-model-list class of defect, so I did it rather than
reasoning from the README. `[measured]`

```
docker run -d --name tei-rerank-test -p 18080:80 -v <cache>:/data \
  ghcr.io/huggingface/text-embeddings-inference:cpu-1.9 \
  --model-id BAAI/bge-reranker-v2-m3 --max-batch-tokens 1024 --max-client-batch-size 128
```

`GET /info` returned:

```json
{"model_id":"BAAI/bge-reranker-v2-m3","model_dtype":"float32",
 "model_type":{"reranker":{"id2label":{"0":"LABEL_0"},"label2id":{"LABEL_0":0}}},
 "max_batch_tokens":1024,"max_batch_requests":4,"max_client_batch_size":128,
 "max_concurrent_requests":512,"max_input_length":1024,
 "tokenization_workers":5,"version":"1.9.4","docker_label":"sha-e80ef22"}
```

**Verdict: the TEI CPU 1.9 image serves `bge-reranker-v2-m3` as a reranker, in float32, and `/rerank`
works.** `model_type` is `reranker`, so this is not an embedding fallback. `[measured]`

Four operational findings from doing it, all of which belong in the runbook:

1. **Backend selection is ORT → Candle → Python, with no CPU gate.** The image has
   `ort,candle,mkl,static-linking` compiled in (`Dockerfile`, read directly). `bge-reranker-v2-m3`
   ships no ONNX weights, so the ORT backend fails with *"Model ONNX files not found in the
   repository"* and TEI falls through to Candle. That is normal, not an error to chase. `[primary-verified]`
   for the code path, `[measured]` for the observed fallthrough.
2. **Startup is dominated by a warmup sweep that is pathological on CPU.** TEI warms up over
   sequence-length buckets up to `max_input_length` (8192 for this model) at `max_batch_tokens`
   (default 16384). On CPU that is a 16,384-token forward pass through a 568 M model. My first two
   attempts sat in `Warming up model` for **more than 10 minutes each** without completing.
   Setting `MAX_WARMUP_SEQUENCE_LENGTH=256` and `MAX_WARMUP_BATCH_SIZE=4` brought startup to
   **20 seconds**. `[measured]`
   **This is a first-boot and restart-time landmine**, and the AI-stack compose pins TEI 1.6 with
   neither variable set.
3. **`max_batch_requests` is 4 on the Candle CPU backend.** The log emits
   *"Backend does not support a batch size > 4. forcing `max_batch_requests=4`"*. So 100 candidates
   are at minimum 25 sequential forward passes. The batch ceiling, not the FLOPs, sets the shape of
   the latency curve. `[measured]`
4. **float32 only.** `model_dtype: float32` on CPU; there is no fp16. `[measured]`

Also worth recording: `RAYON_NUM_THREADS=8` is in the image environment and the Candle backend uses it;
`--cpus=6` in my run therefore oversubscribed rather than constrained, which contributes to the
variance below. `[measured]`

### 3.3 Version discrepancy to resolve

The existing `ai-stack` compose pins `ghcr.io/huggingface/text-embeddings-inference:1.6`. Current TEI
is **1.9.4**, and the CPU tag is `cpu-1.9`. I verified the CPU reranker path on **1.9 only**; I did not
test whether 1.6's CPU image serves a reranker, and the sequence-classification support note is
versioned from v0.4.0, so it probably does. `[unverified]` for 1.6. Given the ai-stack repo's own
standing rule about recording versions beside pins, this is a pin to refresh and record.

### 3.4 Alternatives, if TEI is rejected

| Option | Licence | CPU reranker | Verdict |
|---|---|---|---|
| **TEI cpu-1.9** | Apache-2.0 (verified) | **Yes, demonstrated** | **Keep.** One image, both arms, already in the stack |
| FlashRank | Apache-2.0 (verified) | ONNX, CPU-first by design | Best latency fallback; tiny models (TinyBERT/MiniLM class). Quality ceiling is lower |
| Infinity | MIT (verified) | Supports rerankers | Viable, but adds a second serving framework for no clear gain over TEI |
| sentence-transformers + FastAPI | Apache-2.0 | Works, fully under our control | The escape hatch if TEI's batch ceiling binds. We own the batching and the memory |
| vLLM | Apache-2.0 | Needs a GPU | Disqualified for the cloud build; revisit only for the office build's optional GPU |
| ONNX Runtime int8 | Apache-2.0 | Yes | **Untested here.** The obvious lever if latency binds: dynamic int8 on a cross-encoder typically cuts CPU time several-fold. Measure the quality cost on the 60 questions before shipping |

`[inference]` for the verdicts; licences `[primary-verified]`.

---

## 4. CPU latency: what I measured, and what it is worth

### 4.1 The measurement, stated honestly

I measured `bge-reranker-v2-m3` through TEI's `/rerank` with `[measured]`:

| Candidates | Median wall time | Per candidate | Load average at the time |
|---|---|---|---|
| 1 (single pair) | **2,299 ms** | 2,299 ms | 25.8 |
| 25 | **50,506 ms** (43.8 / 50.5 / 65.3 s over 3 reps) | 2,020 ms | 18.6 |
| 50 | **133,101 ms** (121.3 / 144.9 s over 2 reps) | 2,662 ms | 28.4 |
| 100 | **267,453 ms** (1 rep) | 2,675 ms | 25.8 |

**These numbers are not a valid latency estimate for the target hardware and must not be quoted as
one.** The machine is a 4-core/8-thread mobile i7-11375H with 15 GB RAM that was running at **load
average 18–29** throughout, because sibling research agents were benchmarking on it concurrently. A
single pair costing 2.3 s and 25 pairs costing 50 s is internally inconsistent — the per-request
overhead is clearly being drowned by CPU starvation, not by the model. `[measured]` + `[inference]`

What the run *does* establish, and what it does not:

- **Establishes:** the TEI CPU path works end-to-end at 25, 50 and 100 candidates without error or
  OOM; latency grows roughly linearly with candidate count; the Candle CPU `max_batch_requests=4`
  ceiling is real. `[measured]`
- **Does not establish:** any usable absolute per-candidate cost. A contended 4-core mobile part is
  not the m7i.4xlarge. `[inference]`

I also attempted a clean in-process `transformers` baseline to isolate TEI's overhead, and **it failed
the same way** — the results were not monotonic in model size (`gte-reranker-modernbert-base`, 149 M,
measured 2,352 ms/candidate at 25 while `bge-reranker-base`, 278 M, measured 517 ms/candidate at the
same point), which is only explicable by contention. I am reporting this rather than picking the
flattering row. `[measured]`

### 4.2 The scaling estimate, clearly labelled

Since measurement was unavailable, the honest substitute is arithmetic with stated assumptions.
`[inference]`, no source.

A cross-encoder forward pass is ~2 × params × tokens FLOPs. For `bge-reranker-v2-m3`
(567.8 M) at ~120 passage tokens + ~14 query tokens, that is ~1.5 × 10¹¹ FLOPs per candidate.
The relevant throughput is memory-bandwidth-bound fp32 GEMM, not peak FLOPS.

| Machine | Cores | Rough fp32 GEMM throughput | 50 candidates | 100 candidates |
|---|---|---|---|---|
| This dev box (contended) | 4c/8t, mobile | starved | observed 133 s | observed 267 s |
| **m7i.4xlarge (target)** | 16 vCPU, Sapphire Rapids | ~3–5× this box per-core, plus 2× cores | **low single-digit seconds, plausibly 1–3 s** | **2–6 s** |
| c7i.8xlarge (bulk indexing shape) | 32 vCPU | ~2× the above | sub-second | ~1–2 s |

**The load-bearing conclusion is not the exact figure, it is the order of magnitude: reranking 50–100
candidates with a 568 M cross-encoder on CPU costs seconds, not milliseconds.** BEIR's independently
measured CPU figure for the same operation — cross-encoder reranking of BM25 top-100 — was
**6,100 ms on their hardware**, and that was a 6-layer 384-dim MiniLM, roughly a tenth the size of
`bge-reranker-v2-m3`. `[paper]` A 568 M model costing an order of magnitude more than that is the
expected outcome, not an anomaly. This is why §5 recommends capping rerank depth rather than
maximising it.

**Stated assumptions for the arithmetic, so it can be checked:** I assume a forward pass of
~2·N·T FLOPs (the standard transformer estimate, ignoring attention's T² term, which is minor at
T≈134), and I assume per-core throughput on the 16-vCPU m7i.4xlarge is 3–5× this 11th-gen mobile
part's. Both are inferences. The second is the weaker one and the reason the range spans 3×. `[inference]`

**Action before C2 ships:** run this exact benchmark on the m7i.4xlarge with nothing else running.
It is a 10-minute job and it replaces the whole of this section with a real number. The script is at
`/tmp/search-research/bench_tei2.py`.

### 4.3 Smaller models are the latency lever, not a better server

If the target-hardware measurement lands badly, the fix is a smaller reranker, not a bigger box. Ranked
by parameter count among shippable options: `answerai-colbert-small-v1` (33 M),
`jina-reranker-v1-turbo-en` (38 M), `mxbai-rerank-base-v1` (184 M), `GTE-ModernColBERT-v1` (149 M),
`gte-reranker-modernbert-base` (150 M), `bge-reranker-base` (278 M), `bge-reranker-v2-m3` (568 M).
`[primary-verified]` for the counts.

`gte-reranker-modernbert-base` at **149.6 M** is the most interesting: ModernBERT is designed for
efficient long-context CPU inference, it is Apache-2.0, and TEI lists it. `[inference]` — it is the
first thing I would benchmark against bge-reranker-v2-m3 on the real box, and the 60-question set is
the only thing that can choose between them.

---

## 5. The design questions

### 5.1 How many candidates should we retrieve?

**Retrieve ~100 per arm before fusion; treat 100 as a floor on the lexical arm's recall, not a target
for reranking depth.** Evidence and reasoning, mixed and labelled:

- **The binding constraint is first-stage recall, and BEIR shows BM25 is the right thing to lean on.**
  "Overall, BM25 remains a strong baseline for zero-shot text retrieval." `[paper]` The build brief's
  hybrid densen+keyword fused list is the correct shape.
- **The direct experiment.** Luo, *Neural Retriever-Reader for Information Retrieval and Question
  Answering*, ASU 2023, §4.3 "How Many Candidates are Needed for Re-ranking?" — I read the dissertation
  text. Re-ranking 50/100/150/200 candidates: SQuAD improved **~1%** going from 50 to 200; NQ improved
  **~1%** going from 50 to 100; and on HotpotQA **50 was best, ~0.5% better than 200**, because BM25's
  recall was already 99% at 50 and extra candidates "rather introduce more distracting candidates in
  the re-ranking time". Inference time was **0.49 / 0.85 / 1.24 / 1.63 s** for 50/100/150/200.
  `[paper]`
  The stated rule: *"if the recall of fewer candidates is already high enough (e.g. 99%), then using
  fewer candidates in re-ranking is time efficient and gets the best performance."* `[paper]`
- **Consequence for the 60-question set.** Because that rule is recall-conditional, the design must
  let us measure our own recall@K. At 60 questions, recall@50 has a resolution of about ±1 question
  (±1.7 points); recall@100 about the same in absolute terms. Small-number statistics mean
  **recall@100 vs recall@50 will often be indistinguishable on our own set**, so we should not tune
  the retrieve depth on it. Measure recall@100 once at build time with a larger synthetic set, or
  accept 100 and spend the 60 questions on rerank depth and the refusal threshold. `[inference]`

### 5.2 How many should we rerank?

**Rerank 40–50, not 100. Retrieve 100, fuse, then cut to the top ~50 by fused score and rerank those.**

Reasoning, all `[inference]` built on `[paper]`:

- The Luo results show the marginal quality of candidates 50→200 is ~1 point at best, and negative
  when recall is already saturated. We are paying linearly in latency for it.
- BEIR reranked 100 and got its result; 50 is not shown to be worse in any source I found.
- The 0.49 s → 1.63 s spread at 50→200 in Luo is the shape of the trade, and on our CPU the absolute
  numbers are 3–10× worse.
- **Where the quality actually lives is the top of the list.** Going from a fused top-50 to a reranked
  top-5 changes the answer; going from reranking 50 to reranking 100 changes it rarely. Since the
  answer step only consumes ~5–10 passages, rerank depth beyond that is insurance against a retrieval
  miss near the boundary, and the license to buy it is recall@50, not rerank depth.

**A cheaper design worth testing:** rerank a fixed 50, but if the top-5 reranker scores are all below
a calibrated floor, expand and rerank the next 50. That spends latency only on hard queries. Needs
calibration against the golden set, so it is a C2-stretch idea, not a C2 requirement. `[inference]`

### 5.3 Does reranking change what the lexical arm needs to be good at?

**No — and this is the most important negative finding for the retrieval design.** `[inference]`

A reranker can only reorder. It cannot retrieve. If BM25's fused list does not contain the passage, no
cross-encoder downstream can recover it, so:

- **The lexical arm's job stays recall on exact tokens.** Procedure codes, statute sections, invoice
  and account numbers, names, dates — the identifiers the build brief already names. Reranking does
  not dilute that requirement at all.
- **What reranking removes is the lexical arm's precision requirement.** This is the useful part. A
  first-stage arm may now be tuned aggressively for recall (looser BM25 k1/b, synonym expansion,
  lower the fusion weight on the dense arm) and let the cross-encoder clean up the false positives.
  That is the actual change: **reranking buys the freedom to be sloppy in the first stage on precision
  and greedy on recall**, which is precisely the right posture for a small-business corpus where one
  missed invoice number is a wrong answer.
- **It also changes the fusion weights.** Reciprocal rank fusion's `k` parameter and the per-arm
  weights should be tuned for recall@50, not for nDCG@10, once a reranker is downstream. Do not
  evaluate the fusion arm with the metric the reranker is about to optimise. `[inference]`
- **Corollary: reranking does not make the lexical arm less necessary.** The build brief already
  argues hybrid search is required for exact identifiers. BEIR confirms the general point from the
  other direction: dense retrievers "often underperform traditional lexical models like BM25"
  out of distribution. `[paper]` Nothing about reranking changes that.

---

## 6. The three non-cross-encoder options

### 6.1 Late-interaction reranking (ColBERT-style)

**Viable when the licence is checked model by model, but it is a re-architecture, not a drop-in
reranker.**

The important licence split, all `[primary-verified]`:

| Model | Licence | Shippable |
|---|---|---|
| colbert-ir/colbertv2.0 | `mit` | Yes |
| answerdotai/answerai-colbert-small-v1 | `apache-2.0` | Yes (33.4 M — tiny) |
| **lightonai/GTE-ModernColBERT-v1** | `apache-2.0` | **Yes (149.0 M)** |
| lightonai/Reason-ModernColBERT | `cc-by-nc-4.0` | **NO** |
| jinaai/jina-colbert-v2 | `cc-by-nc-4.0` | **NO** |

Note the trap: GTE-ModernColBERT-v1 is Apache-2.0, but its *reasoning fine-tune* is CC-BY-NC. A
"ModernColBERT" search returns both. Pick the base.

Quality, from BEIR: ColBERT averaged **0.429** nDCG@10 vs BM25+CE's **0.473**, beating BM25 on 9/18
datasets vs 16/18. `[paper]` So on the best available head-to-head, late interaction is meaningfully
**worse** than a cross-encoder reranker for the job we care about.

Cost, from BEIR: ColBERT needs the **largest index of any system tested**, ~900 GB for BioASQ's 15 M
documents against 18 GB for BM25, because it stores a 128-dim vector per token. Latency 350 ms GPU.
`[paper]` At our scale (a 10-user, 50,000-document deployment) that is survivable, but it is one to
two orders of magnitude more vector storage than the dense arm.

The reason to still care: late interaction is computed **at query time against precomputed document
vectors**, so it needs no second forward pass over passages. That is a fundamentally cheaper way to
get cross-attention-like scoring than a cross-encoder, and it is the natural answer if the CPU
reranker measurement in §4 comes back unacceptable. **The office build has an optional GPU later;
late interaction is the option that most benefits.** `[inference]`

Also worth noting: `BAAI/bge-m3` — already our embedding model, `mit` licence `[primary-verified]` —
natively produces dense, sparse **and** ColBERT multi-vector representations in one forward pass. So a
late-interaction arm may cost no additional model at all; it costs storage and query latency.
Whether Qdrant's multivector/MaxSim support is adequate for our corpus size is a question for the
vector-store angle, not this one. `[inference]` — I did not test it.

### 6.2 LLM-as-reranker, listwise, through our gateway

**Recommendation: do not ship this as the reranker.** It is the one option with measured evidence
against it, and the evidence is unusually direct.

Kong et al., *Rank-R1: Enhancing Reasoning in LLM-based Document Rerankers via Reinforcement
Learning* ([arXiv:2503.06034](https://arxiv.org/abs/2503.06034)). I read the paper. All methods rerank
BM25 top-100. TREC-DL nDCG@10: `[paper]`

| Reranker | Backbone | Training | DL19 | DL20 |
|---|---|---|---|---|
| BM25 (no reranking) | — | — | 0.506 | 0.480 |
| RankGPT | **GPT-4** | zero-shot listwise | 0.756 | 0.706 |
| RankZephyr | 7 B | GPT-4-distilled | 0.739 | 0.706 |
| Setwise | 14 B | **zero-shot** | 0.677 | 0.648 |
| Setwise | 7 B | **zero-shot** | 0.675 | 0.636 |
| Setwise | 3 B | zero-shot | 0.371 | 0.317 |
| Rank-R1 | 3 B | zero-shot | 0.605 | 0.538 |
| Setwise | 7 B | SFT | 0.738 | 0.692 |
| Rank-R1 | 7 B | GRPO | 0.727 | 0.685 |
| Rank-R1 | 14 B | GRPO | 0.714 | 0.691 |

Read the zero-shot rows — that is exactly the configuration in our brief ("asking a small model to
order 30 passages"). `[paper]` + `[inference]`

- **Zero-shot Setwise at 7 B (DL19 0.675, DL20 0.636) and 14 B (0.677 / 0.648) beat BM25 (0.506 /
  0.480) on both datasets.** So the naive "it's worse than BM25" claim is **false** and I want to be
  precise: prompting a 7–14 B model to order passages does improve on the unranked BM25 list.
- **But it does not come close to a trained reranker, and on DL20 the 3 B zero-shot model collapses
  below BM25** (0.317 vs 0.480). A 3 B local model — the size that would be cheap enough to sit behind
  our gateway — is *worse than not reranking at all* on one of the two standard in-domain sets.
- **Fine-tuning is what makes LLM rerankers work, and fine-tuning is out of scope.** Setwise-7B SFT
  reaches 0.738/0.692; the zero-shot version is 0.675/0.636. Every competitive LLM reranker in this
  table is either GPT-4-class or fine-tuned, and Rank-R1's 14 B GRPO run needed "roughly three (3B and
  7B models) to five (14B model) days of training on four H100 GPUs". We are not doing that per client.
- **The BRIGHT (out-of-domain, reasoning-heavy) results are worse.** RankZephyr-7B scores **0.130**
  against BM25's **0.137** — i.e. a state-of-the-art listwise LLM reranker *loses to BM25* on a
  benchmark built to need reasoning. Rank-R1-14B-GRPO reaches 0.205. `[paper]`
- **A listwise LLM reranker is sequential by construction.** Rank-R1 uses Setwise prompting with a
  heapify/heapsort pass over the candidates, meaning the number of LLM calls grows with candidate
  count. Thirty passages is not one call; it is a tournament. Against a 0.57 B cross-encoder doing 50
  passages in one batched pass, on a CPU box, this is not a close race. `[inference]`

#### The cost/benefit note the brief asked for

**Is asking a small model to order 30 passages competitive with a dedicated cross-encoder?**
On the evidence: **no, not at any model size we would run locally, and not at any price.**

| | Cross-encoder `bge-reranker-v2-m3` | Listwise LLM, 7–8 B zero-shot |
|---|---|---|
| Parameters | 567.8 M | ~7,000 M (12×) |
| Calls per query | 1 batched pass | sequential tournament (heapify over candidates) |
| Measured TREC-DL, zero-shot | not in this table | 0.675 / 0.636 (Setwise-7B) vs BM25 0.506 / 0.480 |
| Measured TREC-DL, trained | trained CE class reaches 0.69–0.74 | 0.738 / 0.692 (Setwise-7B SFT) |
| Training needed | none (pretrained, zero-shot) | **required** to reach parity; Rank-R1 14B needed ~4×H100 × 3–5 days |
| Licence | Apache-2.0 | depends on the local model |
| Marginal cost in our stack | one container already running | **zero marginal money** — we own the GPU and the gateway |

The genuinely interesting column is the last one. **In the office build the small model is already
resident and already behind our gateway, so the LLM reranker's marginal cost is not money, it is
latency and attention.** That is a real argument, and it is the strongest one available for this
option. But it is defeated by the quality evidence: a 7 B zero-shot listwise reranker lands roughly
where a trained *base-size* cross-encoder lands, while costing an order of magnitude more compute per
query and consuming the GPU that the answer model needs. We would be trading the fast, correct, cheap
component for a slow, worse, expensive one because it happens to be already loaded.

**Where it is worth keeping:** not as the ranker, but as a **verifier**. The build brief already makes
verification first-class, and the natural LLM job in this architecture is the third citation check —
"is this sentence entailed by this passage?" — not ordering candidates. That is one call per cited
sentence against a handful of passages, it produces a checkable yes/no, and Rank-R1's own paper notes
that reasoning traces make LLM relevance judgements more explainable, which is exactly what the
Review screen and the audit trail want. `[paper]` + `[inference]`

One more caveat worth recording: an LLM asked to order passages can be steered by text *inside* those
passages. A cross-encoder scores a fixed function of the pair and has no instruction channel. For a
system whose acceptance criteria include "a successful prompt injection from document content",
the cross-encoder is the structurally safer component. `[inference]`

### 6.3 Hybrid: cross-encoder primary, LLM as escalation

If the office build's GPU has headroom, the defensible hybrid is: cross-encoder reranks 50 always;
on the small fraction of queries where the answer step records `insufficient_evidence` or the
support rate falls below the 85% gate, escalate that query's context selection to the LLM
listwise reranker and log it. That spends the expensive component exactly where the cheap one
failed, which is the only place the Rank-R1 evidence supports using it. `[inference]`

---

## 7. Options rejected, with reasons

| Rejected | Reason | Source |
|---|---|---|
| **jina-reranker-v2-base-multilingual** | `cc-by-nc-4.0`; on-premises commercial use explicitly requires a paid licence; no public price; no LICENSE file, frontmatter only | `[primary-verified]` Jina FAQ, model card |
| **jina-reranker-v3** | Same; plus card says on-premises within a company needs commercial licence | `[primary-verified]` model card §License |
| **jina-reranker-m0** | Same licence | `[primary-verified]` |
| **jinaai/jina-colbert-v2** | Same licence | `[primary-verified]` |
| **Qwen3-Reranker-0.6B/4B/8B** | Licence fine, but `Qwen3ForCausalLM` — cannot be served by TEI at any version; instruction is a per-client tuning parameter; 4B/8B are 7–14× the parameters for ~5 BEIR points | `[primary-verified]` config.json, TEI README |
| **mxbai-rerank-base-v2 / large-v2** | Licence fine, but `Qwen2ForCausalLM` — same TEI problem; large-v2 is 1.5 B (2.7× bge-v2-m3) and its own card puts a batch at 0.89 s on an **A100** | `[primary-verified]` config.json, `[vendor]` card |
| **bge-reranker-v2-gemma (2.5 B) / minicpm-layerwise (2.7 B)** | ~4.5× the parameters of v2-m3 for a reranking task; layerwise needs custom code; unlikely to be viable on CPU | `[inference]` |
| **Reason-ModernColBERT** | `cc-by-nc-4.0` despite its Apache-2.0 base — easy to pick up by mistake | `[primary-verified]` |
| **nvidia/llama-nemotron-rerank-1b-v2** | OpenMDW-1.1, not a licence I have read; 1.2 B; needs review before it can be considered | `[unverified]` |
| **Pure dense retrieval, no hybrid** | BEIR: dense retrievers "often underperform traditional lexical models like BM25" out of distribution | `[paper]` |
| **Late interaction as the primary reranker** | Measurably worse than a cross-encoder (0.429 vs 0.473 avg nDCG@10) and largest index by far | `[paper]` |
| **LLM-as-reranker as the primary ranker** | Zero-shot 3 B falls below BM25; 7–14 B zero-shot reaches only trained-base-CE class; needs fine-tuning we will not do | `[paper]` |
| **Any GPU-only reranker for the cloud build** | Cloud build is CPU-only by decision; a GPU recommendation must be priced and is out of scope here | build brief §4, §15 |

---

## 8. Recommendations

**R1. Ship `BAAI/bge-reranker-v2-m3` as the reranker.** Apache-2.0 verified against the model card,
567.8 M parameters, XLM-RoBERTa sequence classification with a single class, multilingual, already the
model in the existing ai-stack, already listed as TEI-compatible by upstream, and I confirmed it
serves on the TEI CPU image as a `reranker`. No other option satisfies the licence constraint *and*
the CPU-serving constraint *and* the parameter budget at once. `[primary-verified]` + `[measured]`

**R2. Retrieve 100 per arm, fuse, rerank the top 40–50, keep 5–10 for the answer.** Retrieve depth
buys recall; rerank depth buys almost nothing past ~50 and costs linearly. `[paper]` + `[inference]`

**R3. Do not use an LLM as the reranker.** Use it as the entailment verifier on cited sentences,
which is where the build brief already needs it and where one call per citation is affordable.
`[paper]`

**R4. Pin TEI 1.9, not 1.6, and set the warmup variables.** `MAX_WARMUP_SEQUENCE_LENGTH=256` and
`MAX_WARMUP_BATCH_SIZE=4` turn a 10-minute-plus first boot into 20 seconds. Record the version beside
the digest, per the ai-stack repo's own standing rule. `[measured]`

**R5. Benchmark on the real box before C2 closes.** `/tmp/search-research/bench_tei2.py` against the
m7i.4xlarge, nothing else running, 25/50/100 candidates. Replace §4 of this report with the result.
If 50 candidates lands above ~2 s, evaluate `gte-reranker-modernbert-base` (149.6 M) and ONNX int8
before considering a bigger instance. `[inference]`

**R6. Add three things to the golden question set.** The 60 questions should include: (a) questions
whose answer requires an exact identifier, to prove the lexical arm and reranker interact correctly;
(b) questions whose answer is *not* in the corpus, to exercise refusal, because a reranker will
happily return a plausible passage for a question with no answer; (c) at least 10 questions where the
correct passage is retrieved at fused rank 20–50, because that band is the only place rerank depth
could possibly matter and it is where a 60-question set can still detect a difference. `[inference]`

---

## 9. What is still unknown, and what would settle it

| Open question | How to settle it | Cost |
|---|---|---|
| Real CPU latency for 50-candidate reranking on m7i.4xlarge | Run `bench_tei2.py` on the box, idle | 15 minutes |
| Is 40–50 the right rerank depth for *our* corpus? | Requires recall@K at K=25/50/100 on a set bigger than 60; the golden set cannot resolve it | needs a synthetic set |
| Does `gte-reranker-modernbert-base` match bge-reranker-v2-m3 at 3.8× fewer parameters? | Head-to-head on the 60 questions | 1 day |
| What does ONNX int8 cost in quality for 3–5× the speed? | Export, measure both | 1–2 days |
| Is OpenMDW-1.1 shippable? | Read the licence; if it matters, counsel | depends |
| Does TEI 1.6 (the pinned version) serve a CPU reranker? | One `docker run` with the 1.6 cpu tag | 20 minutes |
| Does Qdrant's multivector MaxSim hold up at 50k documents? | Vector-store angle, not this one | — |

---

## Appendix A: primary sources read

| Source | URL | What it gave |
|---|---|---|
| BEIR paper | https://arxiv.org/abs/2104.08663 | Table 2 nDCG@10, Table 3 latency/index sizes, cross-encoder vs BM25 vs ColBERT |
| Luo dissertation (SCONER) | https://cogintlab-asu.github.io/files/paper/2023/ManLuo.pdf | §4.3 candidates-for-reranking experiment and its inference times |
| Rank-R1 | https://ar5iv.labs.arxiv.org/html/2503.06034 | TREC-DL and BRIGHT tables for zero-shot vs fine-tuned listwise rerankers |
| TEI README | https://github.com/huggingface/text-embeddings-inference `README.md` | Supported reranker architectures, CPU image tags, `/rerank` schema, air-gapped pattern |
| TEI Dockerfile | same repo, `Dockerfile` | `--features ort,candle,mkl,static-linking` |
| TEI backend selection | same repo, `backends/src/lib.rs` | ORT → Candle → Python fallthrough, `max_batch_size` warning |
| Jina on-prem FAQ | https://github.com/jina-ai/jina-on-prem/blob/main/docs/FAQ.md | Jina's own statement of which models need a commercial licence |
| jina-reranker-v3 card | https://huggingface.co/jinaai/jina-reranker-v3 | CC-BY-NC-4.0, on-premises requires licence, BEIR comparison table |
| mxbai-rerank-base-v2 card | https://huggingface.co/mixedbread-ai/mxbai-rerank-base-v2 | BEIR numbers, A100 latency, three-step training |
| Qwen3-Reranker-0.6B card | https://huggingface.co/Qwen/Qwen3-Reranker-0.6B | Exact scoring template, system prompt, instruction worth 1–5% |
| Hugging Face model API | `https://huggingface.co/api/models/<repo>` | Parameter counts, licence fields, `?blobs=true` file manifests |
| Model `config.json` files | `https://huggingface.co/<repo>/raw/main/config.json` | Architecture and head type for every candidate |
| Project LICENSE files | `raw.githubusercontent.com/.../LICENSE` | TEI Apache-2.0, FlashRank Apache-2.0, Infinity MIT, PyLate MIT |

## Appendix B: measurement artefacts

| File | Contents |
|---|---|
| `/tmp/search-research/bench_tei2.py` | The TEI `/rerank` latency harness (25/50/100) |
| `/tmp/search-research/bench_tei2.json` | Contended results, §4.1 — **do not quote as production latency** |
| `/tmp/search-research/bench_final.json` | In-process attempt, discarded for non-monotonic results; kept as evidence of contention |
| `/tmp/search-research/tei_readme.md` | Snapshot of the TEI README as read |
| `/tmp/search-research/hf_api.json` | Raw Hugging Face API metadata for every candidate |
| `/tmp/search-research/luo.txt`, `beir.txt` | Extracted text of the two papers |

**Conditions for every number labelled `[measured]` in this report:** 4-core/8-thread Intel i7-11375H,
15 GB RAM, GPU unusable (NVML driver mismatch), Docker 29.1.3, TEI `cpu-1.9` (1.9.4) with
`--cpus=6`, `bge-reranker-v2-m3` fp32, passages generated to ~114 wordpiece tokens to match the
product's 512-character chunks, **host load average 16–29 from concurrent sibling benchmarks**.
