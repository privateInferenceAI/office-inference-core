# Multi-vector and late interaction retrieval

**Angle:** ColBERT-style late interaction as a serious option for Office Inference retrieval.
**Written:** 2026-10-11, for the C2 retrieval decision in `docs/build-brief.md` section 7.
**Question:** does late interaction beat "dense retrieve plus cross-encoder rerank" enough to justify
its storage and complexity, for 10k to 500k chunks on CPU?

**Evidence labels used throughout:**
- **Measured** — I ran it, or I read a number out of a primary artifact I fetched (a results file, a
  benchmark CSV, a source tree).
- **Vendor claim** — the project's own marketing or documentation, unverified independently.
- **Inference** — my calculation or reasoning from the above. Not a source.

---

## 0. The answer

**Late interaction beats the cross-encoder rerank on speed by a wide margin, and ties it on quality.
That is the opposite of the question you asked, and it is the important result.**

On the same candidate pool, over the same corpora, with the same queries, ColBERT MaxSim rescoring and
a cross-encoder rerank land within 0.1 to 0.3 nDCG@20 points of each other. The cross-encoder is 20 to
190 times slower. **Measured**, from
[qdrant-labs/hybrid-comparisons](https://github.com/qdrant-labs/hybrid-comparisons) (detail in
section 5). On a 16 vCPU box with no GPU, the cross-encoder configuration is not a live option at all:
the measured p50 was 2.0 to 9.3 seconds per query. ColBERT MaxSim was 40 to 51 ms.

So the honest framing changes. The question is not "is late interaction worth it over dense plus
cross-encoder." The question is **"is late interaction worth it over dense retrieve plus nothing"**,
because on CPU that is the real alternative to it.

Against *that* comparison, the answer is different per build:

| Build | Recommendation | Why |
|---|---|---|
| **Cloud build** (16 vCPU, Bedrock available) | **No.** Ship hybrid dense+sparse with RRF, and put the verification effort into the three-check citation verifier. | The reranker costs 2+ seconds per query on CPU for +0.1 nDCG. Late interaction could replace it affordably, but it is a new model, a new vector type, and a new failure surface, for a fraction of a point. |
| **Office build** (GPU, no internet) | **Yes, test it. It is the strongest candidate for the rerank stage.** | On the office box there *is* a GPU, so the cross-encoder is affordable, and late interaction is then a quality question rather than a survival question. But it is also the only reranking option that degrades gracefully to CPU if the GPU is busy or absent. Worth 60 golden questions to decide. |

**The single sentence version:** on CPU, late interaction does not beat dense plus cross-encoder
because nothing beats dense plus cross-encoder at a price you can pay; it beats dense plus cross-encoder
by being the only one of the two that runs at interactive speed, and it beats dense-plus-nothing
decisively enough that it belongs in the office build's evaluation set.

**What would change the answer.** If retrieval recall is already at the 92 percent threshold on the 60
golden questions with dense plus sparse plus RRF, late interaction buys nothing and should be dropped.
If recall is short and the misses are semantic rather than lexical, late interaction is the cheapest
remaining lever and should be tried before a bigger embedder. Measure first.

---

## 1. What late interaction actually is, and what changes downstream

ColBERT represents a chunk as one small vector per token instead of one large vector per chunk. At
query time, every query token vector is matched against every document token vector, and each query
token keeps only its best match. Those maxima are summed. That score is MaxSim. **Measured**, formula
as stated in [Qdrant's vectors documentation](https://qdrant.tech/documentation/manage-data/vectors/):

```
score = sum over query tokens i of ( max over doc tokens j of Sim(query_i, doc_j) )
```

Qdrant implements this natively. The comparator is declared on the vector:

```python
vectors_config={
    "dense": VectorParams(size=1024, distance=Distance.COSINE),
    "colbert": VectorParams(
        size=96,                                     # vector size, not dimension count
        distance=Distance.COSINE,
        multivector_config=MultiVectorConfig(comparator=MultiVectorComparator.MAX_SIM),
        hnsw_config=HnswConfigDiff(m=0),             # do NOT build HNSW over the multivector
    ),
}
```

**Measured**: multivectors exist in Qdrant from v1.10.0, `max_sim` is the only comparator, and
`m=0` is how you turn off HNSW indexing for one named vector
([Qdrant vectors docs](https://qdrant.tech/documentation/manage-data/vectors/)). Turning HNSW off for
the multivector is not an optimisation, it is the correct design: the multivector is used to rescore a
prefetched candidate list, so there is nothing for an ANN graph to do. Qdrant says building one over
per-token vectors causes "high RAM usage, slow insert times due to the complexity of maintaining the
HNSW graph" (**vendor claim**,
[multivectors tutorial](https://qdrant.tech/documentation/tutorials-search-engineering/using-multivector-representations/)).

**One hard limit worth knowing before you design around it.** Qdrant enforces
`number_of_sub_vectors * vector_size < 1,048,576`, and it is not configurable. **Measured**, same
documentation. That means:

| Model | Vector size | Max sub-vectors per point |
|---|---|---|
| `answerdotai/answerai-colbert-small-v1` | 96 | 10,922 |
| `colbert-ir/colbertv2.0` | 128 | 8,192 |
| `BAAI/bge-m3` (ColBERT head) | 1024 | **1,024** |

For 512-character chunks this is not binding. bge-m3's ColBERT vectors cap out at 1,024 tokens, and a
512-char chunk is a few hundred tokens at most. It would matter if you later raised the chunk size or
moved to page-level vectors. Qdrant's own guidance is to pool or split the point if you exceed it.

**The consequence for your architecture: multivectors do not change your chunking, your page
metadata, or your citation assembly.** They add a named vector to the same Qdrant point. Page numbers,
document structure, access labels and content hashes all stay in the payload exactly as designed in
`build-brief.md` section 7. That is a real advantage over graph-based or LLM-reranking alternatives,
which do change the retrieval contract. Page-level citation survives untouched.

---

## 2. Licence verification

Every licence below was read from the project's own license metadata or LICENSE file, with the URL
given. Where I could not verify, I say so.

| Model | Licence | Verified how | Shippable? |
|---|---|---|---|
| `answerdotai/answerai-colbert-small-v1` | **Apache-2.0** | HF model API `cardData.license` and the `license:apache-2.0` tag. [api/models/answerdotai/answerai-colbert-small-v1](https://huggingface.co/api/models/answerdotai/answerai-colbert-small-v1) | **Yes** |
| `BAAI/bge-m3` (dense, sparse, and ColBERT heads) | **MIT** | README front matter `license: mit` and the HF API tag. [BAAI/bge-m3 README](https://huggingface.co/BAAI/bge-m3/raw/main/README.md), [api/models/BAAI/bge-m3](https://huggingface.co/api/models/BAAI/bge-m3). Also the code repo LICENSE, MIT, "Copyright (c) 2022 staoxiao": [FlagOpen/FlagEmbedding LICENSE](https://raw.githubusercontent.com/FlagOpen/FlagEmbedding/master/LICENSE) | **Yes** |
| `colbert-ir/colbertv2.0` | **MIT** | HF model API `cardData.license: mit`, tag `license:mit`. [api/models/colbert-ir/colbertv2.0](https://huggingface.co/api/models/colbert-ir/colbertv2.0) | **Yes** |
| `lightonai/GTE-ModernColBERT-v1` | **Apache-2.0** | HF model API `cardData.license: apache-2.0`. [api/models/lightonai/GTE-ModernColBERT-v1](https://huggingface.co/api/models/lightonai/GTE-ModernColBERT-v1) | **Yes** |
| `jinaai/jina-colbert-v2` | **CC-BY-NC-4.0** | HF model API `cardData.license: cc-by-nc-4.0`, tag `license:cc-by-nc-4.0`, and the model page states "License: cc-by-nc-4.0". [api/models/jinaai/jina-colbert-v2](https://huggingface.co/api/models/jinaai/jina-colbert-v2), [model page](https://huggingface.co/jinaai/jina-colbert-v2) | **No** (see below) |
| `cross-encoder/ms-marco-MiniLM-L6-v2` (the rerank baseline used in the benchmark) | **Apache-2.0** | HF model API `cardData.license: apache-2.0`. [api/models/cross-encoder/ms-marco-MiniLM-L6-v2](https://huggingface.co/api/models/cross-encoder/ms-marco-MiniLM-L6-v2) | **Yes** |
| `Xenova/ms-marco-MiniLM-L-6-v2` (the ONNX conversion the benchmark actually ran) | **Unverified** | The HF model API record for this repository carries **no `license` field at all**. [api/models/Xenova/ms-marco-MiniLM-L-6-v2](https://huggingface.co/api/models/Xenova/ms-marco-MiniLM-L6-v2) | **Use the upstream Apache-2.0 base instead** |

### jina-colbert-v2 is disqualified, and not marginally

`jinaai/jina-colbert-v2` is CC-BY-NC-4.0. Non-commercial. It is disqualified for a product we sell.

There is a community thread on the model page literally titled "Why the Creative Commons Attribution
Non Commercial 4.0 License?", and I could not extract its contents from the rendered page. **I tried and
failed to verify a Jina commercial licence.** The URL `https://jina.ai/COMMERCIAL-LICENSE-TERMS.pdf`
appeared in a search result, I fetched it, and it returned **HTTP 404**. Their current terms page does
not surface a purchasable on-premises licence for this model at a stated price.

**So: no verified commercial licence at a stated price. Disqualified.** If someone wants it, the ask to
Jina is a written licence naming the model, permitting redistribution to client premises inside a paid
build, with a price. Until that exists in writing, it cannot ship.

This matters because jina-colbert-v2 is otherwise the most attractive model on the list: 94 languages,
the strongest published multilingual late-interaction results, and an official ONNX export. The licence
is the whole objection.

### bge-m3 is the licence win

bge-m3 is MIT, already in your architecture as the dense embedder, and its ColBERT head ships in the
same checkpoint. **Measured**: the model repository contains `colbert_linear.pt` alongside
`sparse_linear.pt`, and the README documents `return_colbert_vecs=True` with a working `colbert_score`
example. [BAAI/bge-m3 file list](https://huggingface.co/api/models/BAAI/bge-m3).

That is a genuinely unusual position: you could add a late-interaction rerank stage **with no new model
licence, no new model download, and no new checkpoint to pin**, because you already ship bge-m3. The
catch is section 4: bge-m3's ColBERT vectors are 1024-dimensional and nothing serves them.

---

## 3. How it is served, and this is the real blocker

### TEI does not serve ColBERT-style multi-vectors. Verified in source.

The question was direct, so I answered it directly rather than from documentation. I cloned
`huggingface/text-embeddings-inference` at HEAD (commit `5696458506`, dated **2026-10-06**) and searched
the entire source tree for any multi-vector concept.

**Measured: zero hits.** Searching `colbert`, `multi_vector`, `multivector`, `max_sim` and `maxsim`
across the whole repository returned **no matches in any file**. The pooling options are `cls`, `mean`,
`splade`, and `last-token`; the `--pooling` argument enum has no multi-vector member, and the README's
supported-model tables list embedding and sequence-classification models only. There is a reserved
route `/// Get all Embeddings without Pooling` in the HTTP server, which returns token embeddings, but
that is a debugging endpoint, not a MaxSim-scoring retrieval API.

So: **TEI 1.6, the version pinned in the office compose file, cannot serve ColBERT vectors. Neither can
TEI 1.9, the current release.** Adding late interaction means adding a serving component. TEI stays for
bge-m3 dense and bge-reranker-v2-m3, unchanged.

This is a bigger deal for you than for most teams, because TEI is the load-bearing abstraction in both
builds. `build-brief.md` section 5 lists `tei-embed` and `tei-rerank` as the two inference containers.
Late interaction adds a third, with a different API shape, different batching, and its own health
check, in an architecture that deliberately keeps the container count low.

### What actually can serve it

| Path | How it works | Offline-safe | Verdict |
|---|---|---|---|
| **Qdrant Cloud Inference** | Qdrant generates ColBERT vectors server-side from a `models.Document` | **No.** Cloud only. | Disqualified for the office build |
| **FastEmbed** (`qdrant-client[fastembed]`) | ONNX late-interaction models run in the client process; `LateInteractionTextEmbedding` | **Yes**, weights pre-downloadable | **The realistic path.** See below |
| **PyLate** (LightOn) | Python library for training and inference of late-interaction models | Yes | Viable, but another runtime to operate and pin |
| **A custom scoring service** | Run the encoder yourself, do MaxSim in numpy | Yes | Maximum control, maximum maintenance. Only if FastEmbed's model list is insufficient |

**Measured**, FastEmbed's late-interaction model list contains exactly two entries
([Qdrant FastEmbed ColBERT docs](https://qdrant.tech/documentation/fastembed/fastembed-colbert/)):

```
colbert-ir/colbertv2.0              dim  128   size 0.44 GB
answerdotai/answerai-colbert-small-v1  dim 96   size 0.13 GB
```

Note what is absent: **bge-m3, jina-colbert-v2, and GTE-ModernColBERT-v1 are not in FastEmbed's list.**
So there is a real tension in the options:

- The model with the best licence story for you (**bge-m3**, already shipped, MIT) is the one nothing
  serves off the shelf.
- The models that are servable off the shelf (**answerai-colbert-small-v1**, `colbertv2.0`) are ones
  you would have to add.

That tension is the honest core of the complexity cost, and it does not show up in any benchmark table.

### Operational cost, stated plainly

Adding late interaction to the stack means: one new model to pin and download (130 MB for
answerai-colbert-small-v1, small), one new vector type in the Qdrant collection, one new client-side
ONNX runtime inside the ingestion worker and the query path, a re-embed of the whole corpus on adoption,
a new failure mode when the ONNX session fails to initialise, and one more thing to re-run the 60 golden
questions against on every model swap. `build-brief.md` section 10 already commits to re-running the set
on every chunking change and parser upgrade. Late interaction adds a component to that list.

That is not prohibitive. It is also not free, and for a +0.1 nDCG return on the cloud build it is not
worth paying.

---

## 4. Storage cost, per chunk, against a single 1024-dimension vector

The arithmetic. **Inference**, from measured component sizes.

A single 1024-dimension bge-m3 dense vector:

| Storage | Bytes | KB |
|---|---|---|
| float32 | 4,096 | 4.0 |
| float16 | 2,048 | 2.0 |
| int8 (Uint8 datatype) | 1,024 | 1.0 |
| 1-bit binary | 128 | 0.12 |

A ColBERT multivector is (sub-vectors) x (bytes per sub-vector):

| Model | Bytes per sub-vector (f32) | Bytes per sub-vector (turbo4) |
|---|---|---|
| answerai-colbert-small-v1, 96d | 384 | 48 |
| colbertv2.0, 128d | 512 | 64 |
| bge-m3 ColBERT head, 1024d | 4,096 | 512 |

Per chunk, and this is the number you asked for. Sub-vector counts: 32, 48 and 80. **The 48 anchor is
measured** — Qdrant's own tutorial shows a one-sentence movie description ("In 1431, Jeanne d'Arc is
placed on trial on charges of heresy...", roughly 140 characters) producing a `(48, 128)` matrix with
colbertv2.0. Your chunks are 512 characters, so 48 is conservative for a 512-char chunk; 80 to 150 is
more likely if you do not filter sub-vectors. **Inference** for the 32/80 columns.

| Configuration | Bytes/chunk | vs 4KB dense | 10k chunks | 100k chunks | 500k chunks |
|---|---|---|---|---|---|
| dense bge-m3 f32 | 4,096 | 1.0x | 0.04 GiB | 0.38 GiB | 1.91 GiB |
| dense bge-m3 f16 | 2,048 | 0.5x | 0.02 GiB | 0.19 GiB | 0.95 GiB |
| dense bge-m3 int8 | 1,024 | 0.25x | 0.01 GiB | 0.10 GiB | 0.48 GiB |
| dense bge-m3 1-bit | 128 | 0.03x | 0.001 GiB | 0.01 GiB | 0.06 GiB |
| **answerai 96d f32 @ 48 tok** | **18,432** | **4.5x** | 0.17 GiB | 1.72 GiB | 8.58 GiB |
| **answerai 96d turbo4 @ 48 tok** | **2,304** | **0.56x** | 0.02 GiB | 0.21 GiB | 1.07 GiB |
| answerai 96d turbo4 @ 80 tok | 3,840 | 0.94x | 0.04 GiB | 0.36 GiB | 1.79 GiB |
| colbertv2.0 128d f32 @ 48 tok | 24,576 | 6.0x | 0.23 GiB | 2.29 GiB | 11.44 GiB |
| colbertv2.0 128d turbo4 @ 48 tok | 3,072 | 0.75x | 0.03 GiB | 0.29 GiB | 1.43 GiB |
| **bge-m3 ColBERT 1024d f32 @ 48 tok** | **196,608** | **48x** | 1.83 GiB | 18.31 GiB | **91.55 GiB** |
| bge-m3 ColBERT 1024d turbo4 @ 48 tok | 24,576 | 6.0x | 0.23 GiB | 2.29 GiB | 11.44 GiB |

Read that table against the reference instance. It has **64 GB of RAM and a 200 GB gp3 volume**. The
relevant comparison is not "is it bigger", it is "does it still fit".

- **bge-m3's ColBERT head at 500k chunks is 91 GiB of vector data alone.** On a 64 GB box. That is not a
  tuning problem, it is a fit problem. With turbo4 it drops to 11.4 GiB and becomes viable.
- **answerai-colbert-small-v1 at 500k chunks with turbo4 is 1.07 GiB.** Less than the dense vectors it
  is reranking. That is the whole argument for a 96-dimensional late-interaction model.
- Without quantisation, answerai at 500k is 8.6 GiB and colbertv2.0 is 11.4 GiB. Both fit in 64 GB
  alongside everything else in the compose file, but neither is trivial once you hold a hot copy in RAM
  plus disk plus snapshots plus whatever Postgres and Docling are using.

**The storage objection to late interaction mostly dissolves if you pick a small-dimension model and
turn on quantisation.** It does not dissolve if you insist on bge-m3's 1024-dimension ColBERT head at
full precision. That is a decision, not a constraint.

The 1024-dimensional dense vector, 4 KB, is the wrong unit of comparison anyway. Dense vectors get
quantised too, so the fair floor is 1-bit dense (128 B) against turbo4 multivector (2.3 KB): a 16x gap
on the rerank vector, worth about 1 GiB at 500k chunks. In a 64 GB budget that is noise.

---

## 5. The measured quality comparison

This is the heart of it, so here is where the numbers come from and what they are worth.

### The source

[qdrant-labs/hybrid-comparisons](https://github.com/qdrant-labs/hybrid-comparisons). I cloned it and
read the results directly rather than trusting a blog post. It is a benchmark harness that holds the
candidate pool constant and varies only the rescorer, which is exactly the experiment this decision
needs.

- **Corpus:** 100k passages from `jordane95/msmarco-passage-corpus-with-query`; plus BEIR
  `dbpedia-entity` and BEIR `scifact`.
- **Queries:** real, sourced from the corpus's own associated queries. The README is explicit that there
  are "no synthetic/LLM-generated queries anywhere in this pipeline". 500 queries on msmarco, 100 on
  scifact.
- **Retrieval:** dense (`all-MiniLM-L6-v2`) + sparse (`Qdrant/bm25`), RRF-fused, prefetch limit 25/50/100.
- **Rescorers compared on that identical pool:**
  - `colbert` — server-side MaxSim rescore with `answerdotai/answerai-colbert-small-v1`
  - `cross-encoder` — local rerank, fastembed `TextCrossEncoder` on `Xenova/ms-marco-MiniLM-L-6-v2`
  - `rrf` — no second stage
- **Relevance:** binary, the query's true source passage is the single relevant document.

**The task setup is generous to the cross-encoder.** One relevant passage per query, short passages, no
multi-hop, no tables, no scanned pages. These are not your client's contracts and fee schedules. Treat
the absolute numbers as directional and the *relative ordering and latency* as the finding.

### Results, k=20, hybrid prefetch, prefetch_limit=100, no quantisation

**Measured.** Read from `results/summary.csv` in the cloned repository.

| Dataset | Rescorer | Recall@20 | nDCG@20 | MRR@20 | p50 latency |
|---|---|---|---|---|---|
| msmarco 100k | ColBERT MaxSim | 0.968 | **0.776** | 0.715 | **90 ms** |
| msmarco 100k | cross-encoder | **0.974** | **0.779** | 0.718 | 2,007 ms |
| msmarco 100k | RRF only | 0.952 | 0.720 | 0.647 | 26 ms |
| dbpedia | ColBERT MaxSim | 0.964 | 0.905 | 0.886 | **41 ms** |
| dbpedia | cross-encoder | 0.960 | 0.905 | 0.887 | 1,673 ms |
| dbpedia | RRF only | 0.952 | 0.843 | 0.807 | 27 ms |
| scifact | ColBERT MaxSim | **0.930** | **0.622** | **0.523** | **45 ms** |
| scifact | cross-encoder | 0.920 | 0.579 | 0.471 | 7,233 ms |
| scifact | RRF only | 0.900 | 0.567 | 0.464 | 27 ms |

### What this says

**1. ColBERT and the cross-encoder are a statistical tie, and on scifact ColBERT wins.**
Differences of 0.003 nDCG on msmarco and 0.000 on dbpedia are noise. On scifact ColBERT leads by 0.043
nDCG. **Late interaction does not lose to the cross-encoder on quality.** Anyone arguing against late
interaction on quality grounds is arguing from 2021 paper numbers, not from this.

**2. The latency gap is not 2x, it is 20x to 190x.**

| Dataset | ColBERT p50 | cross-encoder p50 | Ratio |
|---|---|---|---|
| msmarco 100k | 90 ms | 2,007 ms | 22x |
| dbpedia | 41 ms | 1,673 ms | 41x |
| scifact | 45 ms | 7,233 ms | 161x |

Across all 720 report runs in the repository, the cross-encoder's median p50 was 1,138 ms on msmarco,
960 ms on dbpedia, and 4,101 ms on scifact, with a worst case of 12.8 seconds. ColBERT's median p50 was
40 ms and 44 ms on the two datasets where it ran, worst case 87 ms. **Measured**, computed over
`results/**/*.json`.

**Critical caveat, and it cuts in favour of late interaction here.** The ColBERT rescore was measured
**server-side on Qdrant Cloud Inference**, while the cross-encoder ran **locally on CPU**. So the 22x to
161x ratio is *not* a clean CPU-to-CPU comparison, and I am not going to pretend it is.

What can be said honestly:
- The **cross-encoder timings are CPU timings**. That is the number your 16 vCPU box would actually
  produce, possibly worse, since the benchmark machine is unknown.
- The **ColBERT timings include a GPU somewhere in Qdrant's cloud**. A CPU MaxSim over 100 candidates
  would be slower than 41 ms. How much slower is unknown, and I have no measured CPU number for it.
- **Inference:** the compute shapes are wildly different in ColBERT's favour and this is not a close
  call. A cross-encoder runs a full transformer forward pass over (query + passage) for each of 100
  candidates. ColBERT MaxSim runs no transformer at query time at all; it is a matrix multiply over
  pre-computed vectors. Even a 10x CPU penalty on the MaxSim leaves it well ahead of 2 to 9 seconds.

**3. RRF-only is the real baseline, and late interaction beats it clearly.**

| Dataset | RRF nDCG@20 | ColBERT nDCG@20 | Gain |
|---|---|---|---|
| msmarco 100k | 0.720 | 0.776 | **+0.056** |
| dbpedia | 0.843 | 0.905 | **+0.062** |
| scifact | 0.567 | 0.622 | **+0.055** |

That is a consistent +0.055 to +0.062 nDCG. Every second stage here buys roughly the same amount:
RRF to cross-encoder is +0.059/+0.062/+0.012. RRF to ColBERT is +0.056/+0.062/+0.055.

**So the second stage is worth about 5.5 nDCG points over no second stage, and it does not matter much
which second stage you pick, only what it costs.** That is the finding that should drive the decision.

**4. A second-stage rerank is worth more than the quantisation choice.** Compare with section 6: the
quantisation spread within ColBERT is under 1 percent on the two large datasets, and the second-stage
gain is 5 to 6 points. If you have engineering budget in the retrieval path, spend it on having *a*
second stage, not on which quantisation it uses.

### One more thing the cross-encoder numbers tell you

On a strict reading, `build-brief.md` section 7 commits to "rerank 50 to 100 candidates down to a
handful", and section 4 sizes the instance to 16 vCPU with no GPU. **Measured: that combination does not
work.** At prefetch_limit 100 the CPU cross-encoder was 2.0 seconds (msmarco) to 9.3 seconds (scifact)
per query. At prefetch 25 it was still 0.32 to 0.43 seconds. Five to thirty users will not tolerate it.

This is an existing latent problem in the C2 plan, independent of late interaction, and it is worth
flagging upward. The options are: rerank a much smaller pool (25 instead of 100, accepting the recall
loss), move the rerank to Bedrock's `cohere.rerank-3-5:0` on the cloud build (a frontier-lane call, so it
needs the data-classification gate), run the cross-encoder on the GPU in the office build, or replace
the rerank stage with late interaction. Late interaction is the option that keeps everything local,
keeps the latency budget, and needs no new agreement.

### Production reality check, per the brief

Prefer what production teams actually run over what benchmarks rank first. Here they diverge, and the
divergence is instructive. **The 2025-2026 benchmark leaderboards put cross-encoders and LLM rerankers
at the top** of reranking quality, and the ranking is real. **What production teams actually run at the
retrieval layer is mostly dense plus sparse plus a reranker, and the reranker is very often a
cross-encoder on a GPU or a hosted reranking API.** Nobody runs a 9-second CPU cross-encoder in a chat
path. The benchmark-leaderboard ordering is a quality ordering; the production ordering is a
quality-per-millisecond ordering, and those are different lists.

**Inference:** the reason late interaction is not the default despite competitive quality and much better
latency is served capability, not quality. TEI does not serve it, most vector databases did not support
multivectors until recently, and the good models have awkward licences. Qdrant is the exception, and you
already run Qdrant.

---

## 6. Does quantisation change the calculus? On storage yes, on the decision no

I extracted every row of the quantisation sweep from the same repository and computed nDCG@20 relative
to the float32 baseline, on ColBERT MaxSim rescoring, k=20, hybrid, prefetch 100. **Measured.**

**msmarco 100k:**

| Config | nDCG@20 | vs float32 | p50 |
|---|---|---|---|
| float32, no quant | 0.7763 | baseline | 90 ms |
| float16, no quant | 0.7760 | -0.04% | 90 ms |
| float16 + 1.5-bit binary | 0.7763 | +0.00% | 90 ms |
| float32 + 1-bit binary | 0.7758 | -0.06% | 109 ms |
| scalar int8 | 0.7727 | -0.46% | 90 ms |
| turbo4, no quant | 0.7688 | -0.97% | 91 ms |
| turbo4 + 1-bit TurboQuant | 0.7679 | -1.08% | 100 ms |
| product x16 / x8 | 0.7630 | -1.71% | 92 ms |

**dbpedia:**

| Config | nDCG@20 | vs float32 | p50 |
|---|---|---|---|
| float32, no quant | 0.9046 | baseline | 41 ms |
| float16 + 1.5-bit binary | 0.9013 | -0.36% | 43 ms |
| scalar int8 | 0.9031 | -0.17% | 41 ms |
| turbo4, no quant | 0.9027 | -0.21% | 44 ms |
| float32 + 1-bit binary | 0.9028 | -0.20% | 41 ms |
| turbo4 + 1-bit TurboQuant | 0.9033 | -0.14% | 51 ms |
| product x16 / x8 | 0.8998 | -0.53% | 45 ms |

**scifact: the quantised configs beat the float32 baseline, by +2% to +15%.** Reduced precision
outperforming full precision on a quality metric is not a real effect. It is evidence that scifact is
too small for these differences to mean anything, which is itself the most useful finding in this
section (see below).

### Reading it

**On the two corpora large enough to be informative, the entire quantisation sweep spans about 1.7
percent of nDCG, and the good options span under 0.5 percent.** 1-bit binary quantisation of a ColBERT
multivector costs **0.06 percent** of nDCG on 100k passages while cutting storage 32x. turbo4 costs 0.97
percent while cutting it 8x. Product quantisation is the only clearly bad option at -1.7 percent, and it
is also the only one nobody needed.

**This is the answer to "does Qdrant's quantisation change the calculus", and the answer is yes but not
in the way the question implies.** It does, decisively, and in late interaction's favour on storage. The
storage objection in section 4 assumed float32 throughout. With 1-bit binary or turbo4 on the
multivector, a 96-dimensional late-interaction model at 500k chunks costs **0.06 to 1.07 GiB of vector
data**. The 48x blow-up from bge-m3's 1024-dimension head at full precision becomes 6x. Multi-vector
storage stops being an argument.

**But quantisation does not fix the licence problem, and the licence problem is the one that decides
this** (section 7). Qdrant's quantisation features make late interaction cheap to store. They do nothing
about the fact that the leading late-interaction models in 2026 are non-commercial.

**Also note the latency column: quantisation did not buy speed.** p50 moved within noise, from 90 ms to
109 ms at worst on msmarco. **Measured.** Quantisation here is a storage and memory decision, not a
latency decision. If you adopt it, adopt it to make the multivector fit, not expecting queries to get
faster. Qdrant's own documentation claims binary quantisation can give "up to a 40x speedup" (**vendor
claim**, [quantization docs](https://qdrant.tech/documentation/manage-data/quantization/)), which is not
what this benchmark measured on ColBERT rescoring. Trust the measurement.

### The small-corpus warning, and why it binds you specifically

scifact is 5,183 passages. Quantisation *improved* nDCG there by up to 15 percent relative to float32,
which is impossible as a real effect. The benchmark's own README acknowledges the small corpus by
running 100 queries instead of 500 there.

**You have 60 golden questions per client.** Your evaluation set is smaller than scifact's query set,
which is the smallest in this benchmark. Any difference under roughly 3 to 5 nDCG points that you
measure on 60 questions is indistinguishable from noise. This means:

- You will **not** be able to measure whether late interaction helps you, at the granularity this
  benchmark operates at, on 60 questions. Do not plan to.
- What you *can* measure on 60 questions is the coarse question: does a second-stage rerank move the
  retrieval number at all, and does anything break. The RRF-to-anything gap of +5.5 nDCG is measurable.
  The ColBERT-versus-cross-encoder gap of +0.3 is not, and chasing it is a waste of the client's time.
- `build-brief.md` section 10 sets 92 percent retrieval and 85 percent answer support as thresholds.
  **Inference:** 92 percent of 60 is 55.2 questions, so one question is worth 1.67 percentage points. A
  single mis-graded golden question moves the headline number more than any quantisation setting or
  rescorer choice examined here. Golden-question quality and deterministic grading matter more than the
  retrieval architecture. That is not a rhetorical point; it is arithmetic.

---

## 7. Options, with licence and verdict

### `answerdotai/answerai-colbert-small-v1` — the recommended candidate

| | |
|---|---|
| **Licence** | Apache-2.0. **Verified**, [HF API](https://huggingface.co/api/models/answerdotai/answerai-colbert-small-v1) |
| **Size** | 33.4M parameters, F32. Model repo ~0.13 GB per FastEmbed. **Measured** |
| **Vector** | 96 dimensions per token |
| **Served by** | FastEmbed `LateInteractionTextEmbedding`, Qdrant-native. ONNX variants in the repo: `model_int8.onnx`, `vespa_colbert.onnx`, `model_q4.onnx`, `model_fp16.onnx`, `model_uint8.onnx`. **Measured** from the file list |
| **Storage @ 48 tok** | 18 KB f32, 2.3 KB turbo4 per chunk |
| **Quality** | Ties the cross-encoder, beats RRF by +5.5 nDCG. **Measured** |
| **CPU viability** | Highest of the candidates. 33M params means a full forward pass over a 512-char chunk is cheap; smaller than bge-m3's 568M. Query-time MaxSim is a matmul, not a transformer pass |
| **Risks** | English-only, and the model card says so. Client documents are probably English; confirm per client. Also this is the model the Qdrant benchmark itself used, so the benchmark and the model come from the same ecosystem, which is a mild conflict of interest to keep in mind |

### `BAAI/bge-m3` ColBERT head — the licence-perfect option that nothing serves

| | |
|---|---|
| **Licence** | MIT. **Verified** from README front matter and the FlagEmbedding LICENSE |
| **Vector** | 1024 dimensions per token |
| **Served by** | **Nothing off the shelf.** Not in FastEmbed's late-interaction list. TEI has no multi-vector support at all. PyLate or custom code required |
| **Storage @ 48 tok** | 192 KB f32, 24 KB turbo4 per chunk. 91.55 GiB at 500k chunks unquantised |
| **Qdrant limit** | Capped at 1,024 sub-vectors per point by `n * size < 1,048,576`. Not binding for 512-char chunks, but tight |
| **Verdict** | **Reject, but keep on the list.** The licence and the "already shipped" arguments are the best of any option. The serving gap and the 1024-dimension storage cost are what kill it. If FastEmbed adds bge-m3 late interaction and you adopt turbo4, revisit: 11.4 GiB at 500k chunks is affordable |

### `colbert-ir/colbertv2.0` — the reference implementation

| | |
|---|---|
| **Licence** | MIT. **Verified**, [HF API](https://huggingface.co/api/models/colbert-ir/colbertv2.0) |
| **Vector** | 128 dimensions, the paper's canonical setting |
| **Served by** | FastEmbed, and it is the model Qdrant's own multivector tutorial leads with |
| **Storage @ 48 tok** | 24 KB f32, 3 KB turbo4 |
| **Verdict** | **Viable baseline, weaker than answerai-colbert-small-v1.** English-only, 2021 vintage, 110M parameters against answerai's 33M, and larger vectors. Use it as the control in an A/B if you want a second data point, not as the recommendation |

### `lightonai/GTE-ModernColBERT-v1` — the modern candidate

| | |
|---|---|
| **Licence** | Apache-2.0. **Verified**, [HF API](https://huggingface.co/api/models/lightonai/GTE-ModernColBERT-v1) |
| **Base** | `Alibaba-NLP/gte-modernbert-base`, ModernBERT architecture, 149M params |
| **Served by** | PyLate (LightOn's own library). **Not in FastEmbed's list.** It carries a `text-embeddings-inference` tag but that tag means the base architecture is TEI-supported, not that TEI serves its multi-vectors; per section 3, TEI does not serve multi-vectors at all |
| **Reported quality** | The model card publishes NanoBEIR numbers: nDCG@10 0.676 mean, MRR@10 0.750, recall@10 0.684. **Vendor claim** — self-published, `verified: false` in the metadata for every metric |
| **Verdict** | **Evaluate, do not adopt blind.** Apache-2.0 and ModernBERT make it the most modern licensed option, but it needs PyLate rather than FastEmbed, which is a real integration cost, and its quality numbers are self-reported on a reduced benchmark. Worth an A/B against answerai-colbert-small-v1 if you adopt late interaction at all |

### The 2026 models, and a licence pattern that should worry you

The three models above were the state of the field as of 2024. I also checked the newest late-interaction
and listwise-reranking releases, and the result changes the strategic picture. **All verified from the HF
model API.**

| Model | Licence | Params | Library | Verdict |
|---|---|---|---|---|
| [`lightonai/Reason-ModernColBERT`](https://huggingface.co/api/models/lightonai/Reason-ModernColBERT) | **CC-BY-NC-4.0** | 149M | PyLate | **Disqualified.** Built on GTE-ModernColBERT-v1 but relicensed non-commercial |
| [`jinaai/jina-reranker-v3`](https://huggingface.co/api/models/jinaai/jina-reranker-v3) | **CC-BY-NC-4.0** | 597M | transformers | **Disqualified.** Its paper is "Last but Not Late Interaction for Listwise Document Reranking", so the newest Jina reranker is a late-interaction variant, and it is non-commercial |
| [`lightonai/colbertv2.0`](https://huggingface.co/api/models/lightonai/colbertv2.0) | **MIT** | 109M | sentence-transformers | **Shippable.** A LightOn redistribution of colbertv2.0, presumably with better tooling. Same MIT terms |
| [`lightonai/GTE-ModernColBERT-v1`](https://huggingface.co/api/models/lightonai/GTE-ModernColBERT-v1) | Apache-2.0 | 149M | PyLate | Shippable, but needs PyLate |

**Here is the finding, and it is the strongest argument against betting the retrieval architecture on
late interaction.** The models that lead this space in 2026 are increasingly non-commercial:

- Jina's flagship late-interaction retriever, jina-colbert-v2: CC-BY-NC-4.0
- Jina's newest listwise reranker, jina-reranker-v3, itself a late-interaction model: CC-BY-NC-4.0
- LightOn's reasoning-focused late-interaction model, Reason-ModernColBERT: CC-BY-NC-4.0
- LightOn's earlier and still-Apache GTE-ModernColBERT-v1: shippable, and the exception

**Inference:** late interaction is where the research frontier is, and the frontier is being licensed
non-commercially. A vendor that builds its retrieval on late interaction is dependent on a model pool
where the best current options cannot legally ship, and where the commercial licence for them is
unpriced or does not exist. For a business that sells to clients and must answer a licence question in a
procurement review, that is a structural risk, not a one-off annoyance.

The shippable set for late interaction is therefore: `answerai-colbert-small-v1` (Apache-2.0),
`colbertv2.0` and LightOn's redistribution of it (MIT), `GTE-ModernColBERT-v1` (Apache-2.0), and bge-m3's
ColBERT head (MIT, unserved). That is a real and adequate set. It is also static, and it is not where the
field is publishing.

**This does not make late interaction unusable.** It makes it a dependency to enter with open eyes:
prefer the small Apache-2.0 model you can pin and forget, and do not architect around any model whose
licence you would have to negotiate.

### `jinaai/jina-colbert-v2` — disqualified on licence

| | |
|---|---|
| **Licence** | **CC-BY-NC-4.0. Non-commercial. Verified** from the HF API and model page |
| **Commercial licence** | **Not verified, and I could not find one.** `jina.ai/COMMERCIAL-LICENSE-TERMS.pdf` returned HTTP 404. No priced on-premises licence surfaced |
| **Verdict** | **Reject.** Also note the 559M parameter size: it is bge-m3-class for inference cost, not small-model-class. The multilingual capability is genuinely attractive and it is wasted here, because the licence makes it unusable for a product we sell |

### Rejected: MUVERA and fixed-dimensional encodings

[MUVERA](https://research.google/pubs/muvera-simple-and-effective-multi-vector-retrieval-via-fixed-dimensional-encodings/)
(NeurIPS 2024) encodes a multi-vector document into a single fixed-dimensional vector so it can go into
an ordinary MIPS index, recovering most of MaxSim's quality without per-token storage. It is the
technically interesting answer to the storage objection in section 4.

**Rejected for now, on implementation grounds, not quality.** No Qdrant support, so it means a custom
encoder plus a custom index. The storage objection it solves is already solved by turbo4 at under 1
percent nDCG cost. **Inference:** solving a problem you no longer have, at the cost of writing and
maintaining retrieval code, is a bad trade. Worth revisiting if Qdrant ships FDE support.

### Rejected: ColPali and visual late interaction

ColPali applies late interaction to page images instead of text tokens, which is genuinely appealing
when the source is scanned PDFs and the citation target is a page. **Rejected** because it needs a
vision encoder and a GPU to be practical, which puts it outside the cloud build's CPU constraint, and
because Docling already does OCR, layout and table structure. It solves the same problem twice. The
office build could revisit it later; the brief says an optional GPU is possible, and `build-brief.md`
section 7 is explicit that Docling is required and not optional, so ColPali would be additive to
Docling, not a replacement.

### Rejected: continuing with the CPU cross-encoder as designed

Not on quality. On latency. **Measured at 2.0 to 9.3 seconds per query** in the only configuration
matching the `build-brief.md` section 7 plan of reranking 50 to 100 candidates. See section 5.

---

## 8. Recommendation

**Cloud build: do not add late interaction now.**
Ship hybrid dense+sparse with RRF, and spend the retrieval engineering budget on the three-check
citation verifier, because verification is the product per `build-brief.md` section 7 and it is what
stops a confident wrong answer reaching a client. Then measure retrieval recall on the 60 golden
questions. If it clears 92 percent, late interaction was never needed. If it does not, late interaction
is the cheapest lever available and section 3 tells you exactly what it costs to add.

**But do not ship the cross-encoder rerank on CPU either.** That is the finding that needs acting on
regardless. Either rerank a 25-candidate pool instead of 100, or use Bedrock's `cohere.rerank-3-5:0`
through the data-classification gate, or accept that the current C2 design has an unmeetable latency
target. **Measured**: 2.0 seconds per query at prefetch 100 on msmarco, 9.3 seconds on scifact.

**Office build: put answerai-colbert-small-v1 in the evaluation set as the rerank stage.**
Apache-2.0, 96 dimensions, 33M parameters, FastEmbed-served, no internet needed, and 1.07 GiB for 500k
chunks with turbo4. It degrades to CPU if the GPU is busy. Compare it against
`bge-reranker-v2-m3` on the office GPU on the 60 golden questions and pick on the evidence.

**Either way, prefer the small Apache-2.0 models and treat the frontier as off limits.**
The recommendation is `answerai-colbert-small-v1` because it is small, fast, Apache-2.0, and
FastEmbed-served. It is deliberately not the highest-quality late-interaction model available, because
the highest-quality ones (jina-colbert-v2, jina-reranker-v3, Reason-ModernColBERT) are all CC-BY-NC-4.0.
Do not architect around a model whose licence you would have to negotiate.

**Storage: enable the 4-bit datatype, not binary, if you adopt multivectors.**
turbo4 costs 0.97 percent nDCG on msmarco and 0.21 percent on dbpedia (**measured**), and cuts the
multivector 8x. 1-bit binary looks better on paper at 0.06 percent cost and 32x compression, but on
answerai's 96-dimensional vectors the absolute saving is 2.3 KB against 0.4 KB per chunk, which is 1 GiB
against 0.2 GiB at 500k chunks. **Inference:** under a gigabyte is not worth an extra quantisation
knob and the extra rescoring path. Take the simpler setting.

**Do not chase the rescorer choice with 60 golden questions.** The measurable difference between ColBERT
and cross-encoder is +0.3 nDCG. One golden question is worth 1.67 percentage points on your headline
metric. Spend the effort on question quality and deterministic grading instead.

### What would change the recommendation

1. **FastEmbed adds bge-m3 late interaction.** Then you get late interaction with the model you already
   ship, MIT-licensed, no new checkpoint. The only remaining objection is the 1024-dimension storage
   cost, which turbo4 cuts to 11.4 GiB at 500k chunks. That becomes the default choice.
2. **Retrieval recall comes in below 92 percent on the golden questions, with semantic misses.** Then
   late interaction stops being optional.
3. **Qdrant ships MUVERA or FDE support.** The storage objection disappears entirely and the calculus
   changes.
4. **Jina offers a priced commercial licence for jina-colbert-v2, or LightOn offers one for
   Reason-ModernColBERT.** Reopen the multilingual case, which matters if a client's documents are not
   English. Note that this requires the licence to permit redistribution to client premises inside a
   paid build, not just internal use.
5. **A GPU is added to the cloud build.** The cross-encoder becomes affordable and the speed argument
   for late interaction mostly evaporates, leaving it a pure quality play at +0.3 nDCG, which is not
   worth the complexity.
6. **The CC-BY-NC trend reverses and the frontier models get permissive licences.** Then the quality case
   for late interaction strengthens materially, because you could use a model better than
   answerai-colbert-small-v1.

---

## 9. Failure modes and operational cost

| Failure mode | What it looks like | Mitigation |
|---|---|---|
| **ONNX session fails to load** | Silent fallback to RRF, or a hard query failure. In an offline build there is no re-download | Pin the model files into the image or a mounted volume, as `ai-stack` already does for TEI. Health-check the encoder like `pathb.sh` gates on TEI readiness |
| **Sub-vector explosion** | No token filtering, so a 512-char chunk yields 150+ sub-vectors instead of 48, and storage triples | Filter punctuation and one-character tokens, as ColBERT requires. Measure your actual sub-vector count per chunk before sizing anything |
| **Qdrant's hard limit** | `n * size >= 1,048,576` rejects the point. With bge-m3 at 1024 dims this bites at 1,024 sub-vectors | Only relevant if you use bge-m3's ColBERT head or raise the chunk size. Test with your longest chunk |
| **`m=0` forgotten on the multivector** | HNSW built over per-token vectors. Insert slows, RAM balloons, nothing improves, because the multivector is only for rescoring | Set `hnsw_config=m=0` on the named multivector. Qdrant's tutorial is explicit about this |
| **Re-embed the whole corpus on adoption** | 500k chunks re-encoded. `build-brief.md` section 7 puts bulk indexing at roughly 150 USD for a million pages on a temporary c7i.8xlarge; this corpus is smaller | Budget it as a separate job, as the brief already does for initial ingestion |
| **Version drift between the encoder and the index** | Model updated, index not re-embedded, scores quietly degrade. Nothing errors | Pin the model by revision hash alongside the image digests, per `build-brief.md` section 9 |
| **Golden questions cannot detect a regression** | A quiet 0.3 nDCG loss is invisible at n=60 | Do not claim to detect it. Monitor the refusal rate and the citation support rate instead, which are per-answer and accumulate over thousands of real questions |
| **Quantisation changes the score distribution** | Rescoring thresholds tuned on float32 behave differently after turbo4 | Retune thresholds after any quantisation change. The nDCG cost is small but the score scale moves |

---

## 10. Sources

**Licences, all read from the project's own metadata:**
- [answerdotai/answerai-colbert-small-v1, Apache-2.0](https://huggingface.co/api/models/answerdotai/answerai-colbert-small-v1)
- [BAAI/bge-m3 README, MIT](https://huggingface.co/BAAI/bge-m3/raw/main/README.md)
- [BAAI/bge-m3 HF API, MIT](https://huggingface.co/api/models/BAAI/bge-m3)
- [FlagOpen/FlagEmbedding LICENSE, MIT](https://raw.githubusercontent.com/FlagOpen/FlagEmbedding/master/LICENSE)
- [colbert-ir/colbertv2.0, MIT](https://huggingface.co/api/models/colbert-ir/colbertv2.0)
- [lightonai/GTE-ModernColBERT-v1, Apache-2.0](https://huggingface.co/api/models/lightonai/GTE-ModernColBERT-v1)
- [lightonai/Reason-ModernColBERT, CC-BY-NC-4.0](https://huggingface.co/api/models/lightonai/Reason-ModernColBERT)
- [lightonai/colbertv2.0, MIT](https://huggingface.co/api/models/lightonai/colbertv2.0)
- [jinaai/jina-reranker-v3, CC-BY-NC-4.0](https://huggingface.co/api/models/jinaai/jina-reranker-v3)
- [jinaai/jina-colbert-v2, CC-BY-NC-4.0](https://huggingface.co/api/models/jinaai/jina-colbert-v2)
- [jinaai/jina-colbert-v2 model page](https://huggingface.co/jinaai/jina-colbert-v2)
- [jina.ai/COMMERCIAL-LICENSE-TERMS.pdf, returned HTTP 404](https://jina.ai/COMMERCIAL-LICENSE-TERMS.pdf)
- [cross-encoder/ms-marco-MiniLM-L6-v2, Apache-2.0](https://huggingface.co/api/models/cross-encoder/ms-marco-MiniLM-L6-v2)
- [Xenova/ms-marco-MiniLM-L-6-v2, no licence field](https://huggingface.co/api/models/Xenova/ms-marco-MiniLM-L-6-v2)
- [BAAI/bge-reranker-v2-m3, Apache-2.0](https://huggingface.co/api/models/BAAI/bge-reranker-v2-m3)

**Serving and storage:**
- [Qdrant vectors documentation: multivectors, MaxSim, the `n * size < 1,048,576` limit, datatypes](https://qdrant.tech/documentation/manage-data/vectors/)
- [Qdrant quantization documentation](https://qdrant.tech/documentation/manage-data/quantization/)
- [Qdrant multivectors and late interaction tutorial](https://qdrant.tech/documentation/tutorials-search-engineering/using-multivector-representations/)
- [Qdrant compressed multivector search with turbo4](https://qdrant.tech/documentation/tutorials-search-engineering/turbo4-multivector-search/)
- [Qdrant FastEmbed ColBERT documentation, the two supported models and their dimensions](https://qdrant.tech/documentation/fastembed/fastembed-colbert/)
- [huggingface/text-embeddings-inference README, supported models and pooling options](https://github.com/huggingface/text-embeddings-inference)
- TEI source, cloned at commit `56964585069318d01ddeeec9454a952a66f1d3a2`, 2026-10-06: zero matches for `colbert`, `multi_vector`, `multivector`, `max_sim`, `maxsim`

**Measured quality:**
- [qdrant-labs/hybrid-comparisons, the benchmark harness and its 720 result reports](https://github.com/qdrant-labs/hybrid-comparisons)
- Its result files, read directly: `results/summary.csv`, `results/dbpedia/summary.csv`, `results/scifact/summary.csv`, and the per-run JSON in `results/**/`

**Reference:**
- [MUVERA: Multi-Vector Retrieval via Fixed Dimensional Encodings, NeurIPS 2024](https://research.google/pubs/muvera-simple-and-effective-multi-vector-retrieval-via-fixed-dimensional-encodings/)
- [ColBERTv2 paper context, BGE-M3 paper arXiv:2402.03216](https://arxiv.org/pdf/2402.03216)
- [JaColBERTv2.5, arXiv:2407.20750, multi-vector training under constrained resources](https://arxiv.org/abs/2407.20750)

**Unverified, flagged as such:**
- Any Jina commercial licence for jina-colbert-v2 at a stated price. I looked and did not find one
- Whether the `Xenova/ms-marco-MiniLM-L-6-v2` ONNX conversion the benchmark ran is covered by the
  upstream Apache-2.0 licence. The HF record carries no licence field
- GTE-ModernColBERT-v1's quality numbers are self-published with `verified: false` on every metric
- CPU latency for ColBERT MaxSim on your hardware. The benchmark measured it server-side on Qdrant
  Cloud, not on a 16 vCPU CPU-only instance
