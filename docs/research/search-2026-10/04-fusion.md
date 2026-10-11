# Combining searches: fusion, and whether hybrid is worth it

Angle 4 of the retrieval-architecture study. Written 2026-10-11.

Scope: reciprocal rank fusion and its variants, weighted and distribution-based score fusion,
learned/reranker-based fusion, plain concatenation, candidate counts, parameter behaviour, and where
the fusion should physically happen (vector store, Postgres, our own code). Then the uncomfortable
question: what does the evidence actually say about hybrid versus dense-only. Finally, the wider
"combine different searches" idea — routing by document type, fielded search, and metadata
pre-filtering as a recall mechanism.

Every claim below carries a label:

- **[measured]** — a number from a source I read, with the corpus it was measured on.
- **[vendor]** — a claim from a product's own documentation or blog. Product docs are useful for
  what the software does; they are not neutral evidence about what works.
- **[inference]** — my reasoning, not a source's claim.
- **[unverified]** — a figure I could not confirm from a source I actually read.

---

## 1. Executive summary

Five findings drive everything else.

**1. Hybrid's benefit is real but small, and it is not universal.** Qdrant's own tuning study
measured hybrid RRF against the better single retriever on five public datasets: it won on four and
lost on one (DBPedia-entity, −0.0039 nDCG@10). The four wins were +0.0156 to +0.0311. **[measured]**
The original RRF paper claimed 4 to 5 percent average improvement over the best individual system,
measured in MAP, but that was fusion across 30 *systems*, not two retrieval arms. **[measured]** The
honest summary is: expect a few points of nDCG, sometimes nothing, occasionally a small loss.

**2. The single largest quality lever in this literature is not fusion and not the reranker. It is
whether the relevant document reaches the candidate list at all.** Three independent sources say
this. Microsoft: "The L2 ranker can only reorder what the L1 already found — if the L1 missed an
ideal document, the L2 can't fix that." **[vendor]** Qdrant: at 200 candidates the gap between the
best possible ordering of the retrieved candidates and the score actually returned ran 0.247 to 0.487
nDCG@10 — a huge amount of recoverable quality sitting *above* the ranking stage. **[measured]**
And the production case study of the Danish National Encyclopedia found that retrieval coverage, not
answer synthesis, explained 69 to 83 percent of overall quality variance. **[measured]**

**3. Reranking on CPU is expensive, and its measured benefit is much weaker than the project brief
assumes.** Qdrant measured four cross-encoders across five datasets and found that only two of five
gains survived held-out validation; on one dataset (WANDS) tuned fusion beat all four models at every
candidate count. **[measured]** A separate hybrid-retrieval system measured cross-encoders *hurting*
NDCG by 0.3 to 3.1 percent while adding 560 to 2100 ms per query. **[measured]** On CPU, reranking 100
candidates costs roughly 0.5 to 5 seconds per query with small models and under 0.02 queries per
second with a larger multilingual one. **[measured]** Against which, the second prefetch that enables
hybrid costs 0.6 to 1.5 milliseconds. **[measured]** That is a factor of roughly 3,000.

**4. The brief's stated design — "prefetch plus reciprocal rank fusion, then rerank 50 to 100
candidates down to a handful" — is not implementable on the pinned Qdrant.** Qdrant's native RRF
hardcodes `k=2`, not 60. Configurable `k` arrived in v1.16.0 and weights in v1.17.0; the current
release is v1.19.2. The pinned image in the ai-stack compose file is v1.11.3 (released 2024-08-29),
which has RRF at `k=2`, DBSF, and no way to change either parameter. **[measured — version
history from the GitHub releases API; parameter support from the vendor docs]** Either the pin moves
or the fusion moves into our code. Section 6 covers this.

**5. With 60 golden questions we cannot discriminate between fusion methods, so we should stop trying
to.** Qdrant ran the same tuning exercise on SciFact's 300 queries and found that *nothing they tried*
had a 95 percent interval excluding zero. **[measured]** The vstash ablation found their corpus
insensitive to fusion on 170 of 200 probes. **[measured]** The IR evaluation literature puts the
requirement for detecting a 0.02 nDCG@10 difference at roughly 100 queries, and 0.01 at over 400.
**[measured — from a curriculum document citing Sakai]** Sixty questions can reliably detect a large
effect and nothing else. Section 9 says what to do about that.

**[inference]** The recommendation that follows: keep hybrid, but change what it is for. Do not treat
fusion as a quality engine to be tuned. Treat the lexical arm as a **recall net for exact strings**
that dense retrieval provably cannot handle, use the simplest defensible fusion, spend the complexity
budget on candidate depth and on the citation verifier, and *measure* whether the reranker earns its
CPU rather than assuming it.

---

## 2. The corpus we are actually designing for

Worth stating before the literature, because it determines which results transfer. **[inference, from
the build brief and architecture doc]**

| Property | Our system | Why it matters here |
|---|---|---|
| Corpus | Contracts, policies, invoices, emails, spreadsheets, scanned PDFs | Heterogeneous. BEIR showed no single retriever wins across domains |
| Exact identifiers | Invoice numbers, account numbers, clause numbers, procedure codes, names, dates | The documented failure mode of dense retrieval |
| Query style | Short business questions, sometimes a bare identifier | The regime where lexical wins hardest |
| Page citations | Required | Forces page-anchored chunks, which affects the lexical arm's doc length statistics |
| Scale | 5 to 30 users, tens of thousands of documents | Small. Filtering and sharding costs that dominate web-scale benchmarks do not apply |
| Eval budget | 60 golden questions | Underpowered by roughly 2x for medium effects |
| Runtime | CPU. Office build has no internet at all | Rules out hosted rerankers and any API-based fusion |

The "short keyword queries" row is the one the literature is most consistent about, and it happens to
be our row. Hold onto it.

---

## 3. Reciprocal rank fusion

### 3.1 What the paper actually says

I read the original. Cormack, Clarke and Büttcher, *Reciprocal Rank Fusion outperforms Condorcet and
individual Rank Learning Methods*, SIGIR 2009
([PDF](https://plg.uwaterloo.ca/~gvcormac/cormacksigir09-rrf.pdf),
[DOI](https://dl.acm.org/doi/10.1145/1571941.1572114)). **[measured]**

The formula, with `r(d)` the rank of document `d` in ranking `r`, **one-based** (they write
`r : D → 1..|D|`):

```
RRFscore(d ∈ D) = Σ_{r ∈ R} 1 / (k + r(d))
```

Their own words on `k`: "k = 60 was fixed during a pilot investigation and not altered during
subsequent validation." And: "The constant k mitigates the impact of high rankings by outlier
systems." **[measured]**

The value of that second sentence is easy to miss. RRF's `k` is a dampener against a *single bad
system* among many. It was not selected as a relevance parameter, and the sentence nobody quotes is
the one that matters: "the results of the first [pilot], shown in table 1, indicated that k = 60 was
near-optimal, **but that the choice was not critical**." **[measured]**

Their pilot table, MAP over TREC topics 351–400, 30 systems fused **[measured]**:

| k | 0 | 10 | 20 | 30 | 40 | 50 | 60 | 70 | 80 | 90 | 100 | 500 |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| MAP | .2072 | .2123 | .2134 | .2139 | .2138 | .2144 | .2145 | .2146 | .2147 | .2145 | .2142 | .2098 |

The entire range from k=10 to k=500 is 0.2123 to 0.2098 — about 1.2 percent relative. The curve is
flat from 30 to 100. So `k=60` is a reasonable default with weak justification, and the paper says so.

Their headline claims, which are frequently cited without their context **[measured]**:

- RRF beat Condorcet Fuse in all 7 non-pilot experiments (p ≈ 0.008).
- RRF beat CombMNZ in 6 of 7 (p ≈ 0.04).
- RRF beat the best individual result in 6 or 7 of the cases, "with the exception of TREC 9, where
  the best ranking was derived using a human-in-the-loop."
- On the LETOR 3 dataset, CombMNZ *edged* RRF (0.6107 vs 0.6051, p ≈ 0.2 — not significant).
- "RRF outperforms Condorcet, CombMNZ and the best system by 4% to 5% on average."

Two caveats this report must carry forward. First, that 4 to 5 percent is **fusion across 30
comparable systems** on TREC collections — Condorcet and CombMNZ exist precisely because fusing many
rankers is a different problem from fusing two retrieval arms. Second, RRF's distinctive property in
their framing is that it "combines ranks without regard to the arbitrary scores returned by
particular ranking methods" — the immunity to score scale is the point, not the ranking formula.
**[measured]**

### 3.2 The `k` parameter in practice

Qdrant measured `k` sensitivity directly, on five public datasets with 200 candidates per prefetch,
all-MiniLM-L6-v2 dense and Qdrant core BM25 sparse. nDCG@10, equal weights **[measured — vendor-run
but with published methodology, numbers and caveats]**:

| Dataset | Queries | Relevant per query | k=1 | k=2 | k=5 | k=20 | k=61 |
|---|---|---|---|---|---|---|---|
| ArguAna | 1,401 | 1.0 | 0.5171 | 0.5216 | **0.5304** | 0.5269 | 0.5207 |
| CodeSearchNet | 1,000 | 1.0 | 0.6501 | 0.6555 | **0.6580** | 0.6511 | 0.6258 |
| SciFact | 300 | 1.1 | 0.7117 | **0.7175** | 0.7154 | 0.7122 | 0.7067 |
| DBPedia-entity | 400 | 38.2 | 0.4625 | 0.4638 | 0.4641 | **0.4682** | 0.4606 |
| WANDS | 480 | 358.9 | 0.7232 | 0.7254 | 0.7336 | 0.7571 | **0.7614** |

Their proposed rule: about one relevant document per query favours k=2 to 5; tens to hundreds of
relevant documents favour k=20 to 61. **[measured]**

That is a coherent story, and it is worth noting that it contradicts the original paper's "choice was
not critical" only in magnitude — the spreads here (0.6258 to 0.6580 on CodeSearchNet) are similar in
size to Cormack's. The practical difference is that Cormack averaged over 50 topics and a fused
system; Qdrant reports that **on WANDS, k=2 and k=61 chose a different top result for 42 percent of
queries** while nDCG@10 moved by 0.036. **[measured]** Small aggregate changes can still mean the
first thing a user sees changes almost half the time.

Also worth knowing, because it affects repeatability: at low `k` many results tie. Qdrant measured
that on SciFact, averaged over queries, 12.5 percent of default-RRF (k=2) top-10 results share a
score with their neighbour, against 2.8 percent at k=61 and zero under DBSF. Fusion sorts on score
alone, so tied groups come back in whatever order storage produced and the same query can return a
different rank-10 document. **[measured]** Their fix: over-fetch, sort client-side on
(score descending, id ascending), then truncate. **[vendor]** This is a real citation-stability issue
for us, since a citation that moves between runs is exactly the kind of thing that erodes trust.

**[inference]** Note the trap for our corpus. "Relevant documents per query" is near 1 for us: a
question about a mileage rate has one right answer. By Qdrant's rule that argues for low `k`. But
their low-relevant-count datasets have completely different query shapes from ours, and our 60
questions cannot tell us which side of the flat region we are on. The defensible choice is **leave
`k` at 60 and treat the ties as the thing to handle** (deterministic tie-break, over-fetch, sort).
Do not tune `k` on 60 questions.

### 3.3 Weighted RRF

The variant is a per-arm weight: `Σ w_r / (k + r(d))`. Qdrant shipped it in v1.17.0. **[vendor]**

Their measured findings **[measured]**:

- Weights are **absolute, not ratios**. Qdrant's formula divides the zero-based position by the
  weight before adding `k`. So `(1, 2)` and `(2, 4)` are different settings, and scaling both changes
  every score. On WANDS at k=5: `(1, 2)` scores 0.7390, `(2, 4)` scores 0.7508.
- Order matters: settle `k` first, because a weight pair is only valid for the `k` it was tested at.
  On WANDS, `(2, 4)` beats equal weights at k=5, but at k=61 equal weights win (0.7614 vs 0.7567).
- **The better retriever should not necessarily get the higher weight.** On DBPedia-entity, dense
  scored 0.4677 against sparse's 0.3857, yet the winning pair `(1, 3)` gives *sparse* three times the
  dense weight, gaining 0.0060. CodeSearchNet leans the other way, `(2, 1)`, gaining 0.0096.
- Equal weights won outright on two of the five datasets. ArguAna's best pair gained 0.0029 with a
  confidence interval crossing zero.
- A weight of 0.0 does not remove an arm. Every document from that prefetch scores 0.0 and sits at
  the bottom of the fused list. Use a `limit` of 0 to actually drop an arm. **[measured]** — this one
  is a genuine foot-gun.

Their operational advice is worth quoting because it is the correct discipline and it is directly
applicable: with an eval set, split queries in two, tune weights on the first half, measure on the
second; without an eval set, leave weights at `(1.0, 1.0)`, because "hand-tuned weights without
measurement are unlikely to beat the default reliably." **[vendor]**

**[inference]** With 60 questions, splitting gives 30 and 30. That is not enough to tune two
parameters and confirm the result. See section 9.

### 3.4 Relative score fusion (Weaviate's default)

Weaviate documents two strategies. **[vendor]**

- `rankedFusion` — the original RRF, explicitly `1/(RANK + 60)`, which is the one-based Cormack
  convention. Objects score by position only; "the results for each rank are identical, regardless of
  the input score."
- `relativeScoreFusion` — the default since v1.24. Each arm's scores are min-max normalised across
  that arm's result set (highest becomes 1, lowest becomes 0), and the normalised scores are summed.

Their worked example is a good illustration of the difference. Five objects, keyword scores
5 / 2.6 / 2.3 / 0.2 / 0.09 and vector scores 0.6 / 0.598 / 0.596 / 0.594 / 0.009. Under
`rankedFusion` the top result is object 2. Under `relativeScoreFusion` it is object 1, because object
1 dominated the keyword arm by a wide margin while the top four vector scores were nearly
indistinguishable. **[vendor]**

Weaviate's justification for defaulting to relative score fusion is that it "retains more information
from the original searches than `rankedFusion`, which only retains the rankings." **[vendor]** That is
a plausible claim and it is stated as a belief, not a measurement — Weaviate publish no numbers
alongside it.

I want to note a documentation inconsistency rather than a behaviour: the official W3C-style name for
this normalisation is min-max, and Qdrant's and Weaviate's own prose variously calls the same thing
"relative score fusion", "normalised score fusion", and, in Qdrant's case, describes Weaviate's method
as using a 1-based rank. The invocation of rank there is confusing in the docs and I could not resolve
it to source. **[unverified]** What is verifiable is the arithmetic: min-max over each arm's returned
set, then sum. That is what both descriptions compute.

Weaviate's own alpha parameter is a related design: `alpha` blends keyword and vector, defaulting to
0.75 in favour of vector. Their docs warn that the default "applies only when a request reaches
Weaviate with no `alpha` value," and that client libraries differ in whether they leave it unset, so
"in some cases [it] can be a pure keyword search." **[vendor]** Worth knowing as a class of bug:
a fusion weight that is applied differently depending on which client you call from.

### 3.5 Distribution-based score fusion (DBSF)

DBSF normalises each arm using that arm's own score distribution, then sums. Qdrant's formula
**[vendor]**:

```
ŝ = (s − (μ − 3σ)) / 6σ
```

where μ and σ are the mean and **sample** standard deviation of that arm's returned scores. Qdrant
shipped it in v1.11.0.

Qdrant's own caveats are the important part and I'll reproduce them because they are unusually candid
**[vendor]**:

- The statistics come from the prefetch top-k, which is a small sample.
- A single dominant outlier in that top-k can skew normalisation for that query.
- Scores are **not** clipped to [0, 1]; values outside the 3σ range stay outside it after the remap.
- If all returned scores are identical, or only one point is returned, DBSF emits 0.5 rather than
  dividing by zero.
- It is stateless per query — normalisation limits come from that query's returned points, not from
  anything learned across queries.

The client implementation confirms both the formula and the degenerate cases, reading the sample
standard deviation with `n−1` in the denominator and emitting 0.5 on zero variance or a single-point
response ([qdrant_client/hybrid/fusion.py](https://github.com/qdrant/qdrant-client/blob/master/qdrant_client/hybrid/fusion.py)).
**[measured — I read the source]**

Measured comparison against default RRF (k=2, equal weights) on the same five datasets, 200 candidates
per arm **[measured]**:

| Dataset | DBSF | vs. default RRF | Interval excludes zero? |
|---|---|---|---|
| ArguAna | 0.5171 | −0.0045 | no |
| CodeSearchNet | 0.6716 | +0.0161 | yes |
| SciFact | 0.7323 | +0.0148 | no |
| DBPedia-entity | 0.4822 | +0.0184 | yes |
| WANDS | 0.7637 | +0.0383 | yes |

Qdrant's own summary: DBSF and weighted RRF each win in different situations and "neither dominates
the other in general." **[vendor]** Their decision table is: have an eval set, use weighted RRF tuned
on a train/val split; trust your retrievers' raw scores and have no eval set, use DBSF; have neither,
use plain RRF as the safe default. **[vendor]**

There is one further measured property that matters more for us than the nDCG columns. Qdrant
recorded that **DBSF produced zero tied scores** in their SciFact top-10 analysis, against 12.5
percent under RRF at k=2. **[measured]** For a citation product, a fusion method that produces a
deterministic, tie-free ordering is worth something that nDCG does not capture.

The original DBSF write-up is a Medium article by the same author
([link from Qdrant's docs](https://medium.com/plain-simple-software/distribution-based-score-fusion-dbsf-a-new-approach-to-vector-search-ranking-f87c37488b18)).
I did not read it; I am relying on Qdrant's implementation and documentation, which is the artefact
that would actually run. **[unverified — the original article]**

### 3.6 Naive linear combination of raw scores

Worth naming so we can reject it explicitly. Qdrant's docs say plainly: "A common request is
'alpha-weighted linear combination of dense and sparse scores.' This is unreliable without first
normalizing the scores: dense (cosine, bounded) and sparse (BM25, unbounded) scores live on different
scales that also shift per query, so a fixed alpha over raw scores tends to be dominated by whichever
retriever has larger raw magnitudes on a given query." **[vendor]** Weaviate's docs make the same
point from the other side: BM25 scores "are not normalized or bounded like vector distances, making a
universal threshold less meaningful." **[vendor]** This is consistent and I consider it settled.

### 3.7 Plain concatenation

Is there any evidence for just unioning the two arms' candidates and letting the reranker sort it out?

I did not find a paper that measures concatenation as a named baseline against RRF. What I found
instead is the observation that makes concatenation coherent, stated by Microsoft in the L1/L2
framing: "The L2 ranker can only reorder what the L1 already found — if the L1 missed an ideal
document, the L2 can't fix that." **[vendor]** If the reranker is doing the ranking anyway, fusion is
only deciding *which candidates get reranked*, and the correct objective for that stage is recall, not
ordering.

Qdrant's candidate-depth study supports this reading structurally: they recommend scoring the
**union** of the dense and sparse prefetches as if perfectly ordered, and measuring the gap to what
the pipeline returns, precisely because the union is the ceiling any later stage can reach.
**[measured]** Concatenation is a degenerate fusion that maximises nothing in particular, but it does
not discard anything either.

There is also a measured result in the same study that reads against the fusion-first instinct.
Raising the prefetch limit could *lower* the fused score: CodeSearchNet peaked at limit=200 and was
lower at 500; DBPedia-entity peaked at 50. **[measured]** Under rank fusion the tail contributes
little, so extra tail candidates mostly add noise. Under concatenation-plus-rerank they are just more
work for the cross-encoder.

**[inference]** My verdict: concatenation is a defensible *debugging* baseline and a reasonable
fallback, and it has one real advantage — it is score-scale agnostic by construction and has no
parameters to tune. But it makes the reranker mandatory rather than optional, and given finding 3
(reranker benefit is weaker and far more expensive than assumed), betting the pipeline on the
reranker is the wrong bet for a CPU deployment. Keep fusion. Use concatenation as the control
condition in the golden-set harness so we can see whether fusion is earning its place.

---

## 4. Learned and reranker-based fusion

### 4.1 The cross-encoder as the fusion mechanism

This is the "skip fusion, take the union, rerank the union" architecture. It is what the brief's
"rerank 50 to 100 candidates down to a handful" is really proposing.

Qdrant measured it carefully: four cross-encoders (`ms-marco-MiniLM-L-6-v2`, `MiniLM-L-12-v2`,
`bge-reranker-base`, `jina-reranker-v2-base-multilingual`), reranking fused candidates at counts 10,
25, 50, 100 and 200, over 200 queries per dataset across five datasets **[measured]**:

| Dataset | Best model | vs. default RRF | vs. **tuned** fusion | Held out |
|---|---|---|---|---|
| SciFact | jina-reranker-v2 @ 200 | +0.057 | +0.033 | no, 37% of splits |
| ArguAna | jina-reranker-v2 @ 25 | +0.031 | +0.017 | no, 2.5% |
| WANDS | MiniLM-L-6 @ 200 | +0.039 | **−0.008** | no, 0% |
| CodeSearchNet | jina-reranker-v2 @ 200 | +0.169 | +0.135 | yes, 100% |
| DBPedia-entity | jina-reranker-v2 @ 200 | +0.137 | +0.115 | yes, 100% |

Three things to take from this table.

**The comparison that matters is against tuned fusion, not default fusion.** The "vs. default RRF"
column is the flattering one. Two of the five datasets show the reranker adding nothing over simply
tuning the fusion weights, and on WANDS it actively lost. **[measured]**

**Held-out validation kills three of the five gains.** On SciFact, the +0.033 survived on only 37
percent of 200 split-half draws; on ArguAna, 2.5 percent. Qdrant's guidance: "Ship a reranker gain
only when it survives held-out validation." **[measured]**

**A deeper candidate list does not rescue a losing reranker.** "In these five datasets, every
configuration that trailed tuned fusion at 10 candidates still trailed it at 200." The authors
correctly flag this as their data and not a guarantee. **[measured]**

The cost side, measured on one process, Apple M5 Pro, 15 threads, FastEmbed 0.8.x **[measured]**:

| Model | Size | Docs/sec | Queries/sec at 100 candidates |
|---|---|---|---|
| `Xenova/ms-marco-MiniLM-L-6-v2` | 0.08 GB | 64–212 | 0.6–2.1 |
| `Xenova/ms-marco-MiniLM-L-12-v2` | 0.12 GB | 34–117 | 0.3–1.2 |
| `BAAI/bge-reranker-base` | 1.04 GB | 16–45 | 0.2–0.5 |
| `jinaai/jina-reranker-v2-base-multilingual` | 1.11 GB | under 2 | under 0.02 |

At 10 candidates the test itself stays fast even on CPU — 47 to 156 ms per query with the smallest
model. At 100 candidates, one CPU process spends half a second to five seconds per query with the
three smaller models. **[measured]** The second prefetch that makes hybrid possible added 0.6 to 1.5 ms
in the same setup. **[measured]**

And one licensing landmine found in the same table: `jina-reranker-v2-base-multilingual` "ships under
a CC-BY-NC-4.0 license" — non-commercial. **[measured — Qdrant's own documentation]** It is also the
model that produced both confirmed wins. The two best-performing rerankers in this study are therefore
not shippable, and the shippable alternatives did worse.

### 4.2 Where the reranker is the wrong tool

A second, independent negative result. The vstash paper is a single-author preprint (arXiv
[2604.15484](https://arxiv.org/abs/2604.15484), CC BY 4.0), so it is weaker evidence than the Qdrant
study, but its negative result is specific and its methodology is published **[measured — preprint]**:

> "Off-the-shelf cross-encoders (ms-marco-MiniLM, BGE-reranker-base) degraded NDCG by −0.3% to −3.1%
> while adding 560–2100 ms latency. The cross-encoders were trained on web search distributions that
> do not transfer well to the technical and scientific corpora evaluated."

Its conclusion: "The hybrid RRF pipeline with adaptive IDF weighting appears to be at its ceiling for
the BGE-small embedding model. Gains come from improving the embedding, not from post-hoc reranking.
We recommend investing in better embeddings over more complex scoring."

Their candidate retrieval was dense plus SQLite FTS5, BGE-small-en-v1.5 (33M params), same-family
cross-encoders. The finding is that on a small technical corpus, a 33M-parameter embedding plus
hybrid RRF beat adding a cross-encoder.

**[inference]** For us the mechanism is plausible and relevant. A cross-encoder trained on web search
has to decide whether a passage answers a natural-language question. Our hard cases are "which
contract has clause 7.3(b)" and "what is the mileage rate in the 2024 policy" — matching a string and
reading a number. Neither is a task that web-search training data would emphasise, and the query-doc
token overlap is often already high, which is where cross-encoders add least.

### 4.3 Listwise / LLM reranking

RankGPT-style listwise reranking and MonoT5/DuoT5 were in scope. I did not find measured comparisons
of them against tuned fusion on corpora comparable to ours, and I have no verified numbers to report.
**[unverified]** There is an obvious structural reason to expect they do not fit here: an LLM listwise
reranker on CPU, in a system whose whole point is that the answer must cite a page, would put a
generative model inside the retrieval path. That adds a second place where a confident wrong ordering
can enter, and it doubles the latency budget. I would rule it out on architecture grounds rather than
wait for a benchmark.

### 4.4 The honest reconciliation

Qdrant found cross-encoders helpful on two of five datasets. vstash found them harmful. Microsoft
report a large gain from their semantic ranker — hybrid 48.4 to hybrid + semantic ranker 50.0 nDCG@10
on BEIR, and 48.4 to 60.1 on their customer datasets (NDCG@3). **[vendor]**

These are not contradictory once the setup is read carefully.

- Microsoft's ranker is a Bing-derived multilingual model ranking the top 50, on corpora the
  company selected, measured by Microsoft. **[vendor]** It is the strongest evidence for reranking
  and it is the least independent evidence.
- Qdrant's wins came from the one model "whose window and training data fit the corpus" —
  `jina-reranker-v2` on code, where the other three had never seen code. **[measured]** The lesson is
  fit, not reranking.
- The vstash losses were on technical and scientific corpora with web-search-trained models.
  **[measured — preprint]** Same lesson, opposite outcome.

**[inference]** So the rule is: a reranker helps when its training distribution and context window
match your documents, and it costs a second to five seconds per query on CPU when they match weakly.
None of the four models Qdrant tested was trained on contracts, invoices or scanned forms, and we
have no GPU in the cloud build. That makes the reranker an experiment to run against the golden set,
not a component to specify in the architecture.

### 4.5 One measured result that argues for the opposite of "rerank harder"

The coverage decomposition from the Danish National Encyclopedia study (arXiv
[2605.27220](https://arxiv.org/abs/2605.27220), 20,000 query-workflow pairs, production traffic,
~240,000 articles, judged by Qwen3-32B with a human-annotated validation on 100 pairs) **[measured]**:

| Workflow | Composite overall | Composite when answered | Coverage | End-to-end latency |
|---|---|---|---|---|
| Semantic (dense only) | 3.058 | 4.287 | 62.6% | 33 s |
| Semantic + cross-encoder | 3.086 | 4.321 | 62.8% | 33 s |
| Hybrid (BM25 + dense + RRF) | 3.408 | 4.335 | 72.2% | 37 s |
| LLM expansion + CE | 3.601 | 4.338 | 77.9% | 60 s |
| HyDE | 3.944 | 4.407 | 86.4% | 96 s |
| Oracle | 4.404 | 4.940 | — | — |

Read the first two rows together. Adding a cross-encoder to dense retrieval moved coverage from 62.6
to 62.8 percent and composite from 3.058 to 3.086. Adding BM25 and fusing with RRF moved coverage to
72.2 percent and composite to 3.408. **The cross-encoder was worth 0.028; the lexical arm was worth
0.320** — an order of magnitude more, at 4 seconds of latency instead of 0. **[measured]**

The authors' decomposition attributes 1.020 of the 1.748-point HyDE advantage to coverage expansion
and 0.076 to improved synthesis. Coverage, not ranking quality, is the dominant term: the binary
has-sources flag "explains 69–83% of overall quality variance." **[measured]**

This is the strongest single piece of evidence in this report, and it points away from reranking and
toward retrieval breadth.

---

## 5. Candidate counts

### 5.1 How deep, per arm

Qdrant's guidance: start `limit` around 100 to 200 per prefetch and test larger values on your own
labels. At 200 they measured that the best-possible-ordering gap over the actual fused score ran 0.247
to 0.487 nDCG@10 across five datasets. **[measured]** That gap is the recoverable headroom; the deeper
you fetch, the more of it exists, and the more of it a reranker could in principle recover.

Their measured curvature **[measured]**:

- The "best possible" score climbs at every depth step on every dataset, from limit=10 to 500. The
  current fused score stays almost flat. The gap widens monotonically.
- The fused score is **not** monotone. CodeSearchNet peaks at limit=200 and is lower at 500;
  DBPedia-entity peaks at 50.
- Raising limit from 10 to 500 increased median latency 37 to 43 percent in their single-shard tests.
- Depth is **per shard**. On 12 shards, limit=200 means the collection-level stage can receive up to
  2,400 candidates. Only root-level fusion (fusion as the top-level query, not nested in a prefetch)
  merges across shards. **[vendor]** We run one shard, so this does not bite, but it would if a
  client corpus got large.

The reranker study adds: DBPedia-entity reached 96 percent of its eventual reranker gain by 50
candidates, so going to 200 quadrupled the reranking work for the last 4 percent. And on ArguAna,
where each query has one relevant document, 90 percent of queries already had it within 25 candidates;
raising to 200 turned a gain into a loss. **[measured]**

There is a clean diagnostic buried in this that we should adopt regardless of which fusion we pick.
Qdrant's candidate-depth article proposes scoring the candidate set as if perfectly ordered and
comparing with what the pipeline returns. A large gap means ranking is the problem; a small gap means
retrieval is. **[measured]** For a 60-question golden set this is a handful of lines of code and it
tells us where to spend effort. It is the single most useful idea in this whole literature for our
situation.

### 5.2 Is a fixed depth the right shape at all?

There is a 2026 paper that argues no. *Exact Adaptive Hybrid Retrieval Without Fixed Top-L Cutoffs*
(Zhang, arXiv [2608.07152](https://arxiv.org/abs/2608.07152)) **[measured — preprint, single author]**:

> "Because channel rankings vary across queries and corpus updates, a depth selected from historical
> queries may not transfer reliably. ... Across five test collections and five temporal corpus
> snapshots, complete-list weighted RRF remained competitive, whereas fixed depths selected from
> historical queries did not transfer reliably. EAHR reproduced the complete-list ordered Top-20 in
> all 150 query-snapshot combinations."

Their method bounds the unread contribution of each arm and requests further ranks only while they can
change the top-K. Paired geometric-mean latency ratios of exhaustive execution to adaptive were 23.35
on TREC-DL 2019 and 30.28 on TREC-DL 2020. **[measured]**

Two conclusions. First, this is independent confirmation that a depth tuned on one set of queries is
a weak thing to rely on. Second, the *shape* of the answer — fetch deeper lazily and stop when more
depth cannot change the result — is better than any fixed number, and matters more at scale than at
ours.

**[inference]** For tens of thousands of chunks on one shard, exhaustive or near-exhaustive retrieval
is affordable and the whole question dissolves. Our candidate budget should be set by the reranker's
CPU cost, not by recall. Which is why the reranker being optional matters so much: if we do not run a
reranker, we can afford limit=500 per arm and take the recall for free.

### 5.3 The other depth knob, briefly

`hnsw_ef` controls how wide the HNSW traversal searches, not how many results come back. Qdrant
measured it saturating fast: recall against exact was 0.986 at ef=16, 0.999 at ef=128, 1.000 at 256,
with time per query moving from 1.98 to 2.45 ms. On their five datasets, raising ef through 16/64/
128/512 at depth 200 moved fused nDCG@10 by at most 0.0022 and relevant-document recall in the
candidate union by at most 0.0040, while median latency rose 4 to 49 percent. **[measured]** Their
one caution: on the full 4.6M-document DBPedia-entity, a fixed query returned 0.957 of the exact top
10, so about 4 percent of true nearest neighbours never came back at that scale. **[measured]**

For a small collection this is a non-issue; set `hnsw_ef` to hit a recall target and stop.

### 5.4 Zero-variance check

One measured result worth carrying as a caution about our own evaluation design. In the mempalace
fusion A/B — 200 probes, vector plus tsvector-BM25 over a 3,413-drawer corpus — 170 of 200 probes were
**ties** between the two fusion methods. Only 30 probes distinguished them: 10 improved, 20 regressed.
**[measured — an engineering report, not peer reviewed, but with published per-probe data and a
reproduction script]**

That means roughly 85 percent of a queryset can be inert for the question being asked. If 85 percent
of our 60 golden questions are ties between two fusion configurations, we have roughly 9 discriminating
questions. Which brings section 9 forward: **build the golden set so that questions are known to
discriminate.** A question that every configuration answers correctly, and that every configuration
cites identically, is not measuring anything.

---

## 6. Where the fusion should happen: Qdrant, Postgres, or our code

### 6.1 The version constraint that decides it

The brief says: "Qdrant prefetch plus reciprocal rank fusion, then rerank 50 to 100 candidates down to
a handful." The ai-stack compose file pins `qdrant/qdrant:v1.11.3`. Here is what that version can and
cannot do. **[measured — version dates from the GitHub releases API, feature availability from the
vendor's own version annotations]**

| Feature | Introduced | In v1.11.3? |
|---|---|---|
| Query API, prefetch, fusion | v1.10.0 | **yes** |
| RRF | v1.10.0 | **yes, hardcoded k=2** |
| DBSF | v1.11.0 | **yes** |
| Grouping | v1.11.0 | yes |
| Configurable RRF `k` | v1.16.0 | no |
| Weighted RRF | v1.17.0 | no |
| Native BM25 sparse model, IDF modifier | after v1.11 | no |
| Formula Query (custom scoring from payload) | v1.14.0 | no |
| Language-neutral BM25 text processing | v1.19.0 | no |

Version context: v1.11.3 was published 2024-08-29. The current release is v1.19.2 (2026-10-05).
**[measured]**

So on the pinned version we have exactly two native options and neither is what the brief describes:
RRF at `k=2`, or DBSF. Both are usable. Neither is tunable. And Qdrant's own documentation says
plainly that to reproduce Cormack's `k=60` convention with one-based ranks you would use `k=61` —
which the pinned server cannot express. **[vendor]**

The client library is a separate matter and does implement both, with configurable `k` and weights,
in `qdrant_client/hybrid/fusion.py`. **[measured — I read the source]** That is the escape hatch:
run the two prefetches, pull the candidates, fuse client-side.

### 6.2 The three placements, assessed

**In the vector store (Qdrant native fusion).**
*For:* one round trip; fusion across shards for free at root level; no candidate payload crosses the
network; DBSF's tie-free output is available today.
*Against:* on the pinned version, `k` and weights are frozen; upgrading the pin is a change to a
production service in a system that has already been bitten by an unrecorded pin ("we pinned an image,
recorded no version, and could not answer the first question a security review asks" — build brief
section 9). Row-level fusion at root level also cannot be combined with a Formula Query rescore:
"a main query is a single operation, so it cannot be both a fusion and a formula." **[vendor]**
At 60 questions we cannot tune `k` anyway, so the loss is smaller than it looks.

**In Postgres.**
*For:* Postgres 16 is already in the stack for keys, spend, audit and application state. It has
`tsvector`/`ts_rank` for the lexical arm and, with pgvector, a dense arm; both licenses are permissive
(PostgreSQL License for both — verified from [postgresql.org/about/licence](https://www.postgresql.org/about/licence/)
and the [pgvector LICENSE](https://github.com/pgvector/pgvector/blob/master/LICENSE)). Fusion in SQL
is a `FULL OUTER JOIN` on the two ranked subqueries plus a `1/(k+rank)` expression, and it is trivially
auditable, backable-up, and version-controlled as a view. It also puts the fusion next to the ACL
predicate, which matters for section 8.
*Against:* it means running and maintaining a second retrieval system alongside Qdrant, which is the
worst of both worlds. Postgres `ts_rank` is not BM25 and its defaults are poor out of the box; a
well-known Postgres performance vendor's write-up of "Elasticsearch's hybrid search, now in Postgres"
returned no readable article body when fetched, so I cannot cite its claims. **[unverified]** If we
were to move the lexical arm to Postgres we would be choosing it *instead of* Qdrant, not as well as.

**In our own code.**
*For:* full control of `k`, weights, tie-breaking and normalisation, including options the servers do
not implement. Deterministic and unit-testable, which matters for citation stability. No service
version dependency for a ranking decision. We already own the ingestion, the citation assembly and the
verifier, so there is no new component.
*Against:* we own the correctness of the arithmetic; an extra round trip or a larger result payload;
we must handle the distributed case ourselves if we ever shard.

**[inference] My recommendation: fuse in our own code, with the two arms executed as Qdrant
prefetches.** Reasons, in order. (1) The parameters we care about — `k`, weights, and deterministic
tie-breaking — are not expressible on the pinned server, and pinning an image is a deliberate policy in
this project that I am not going to argue around. (2) The tie problem is a citation-stability problem,
and deterministic tie-breaking is a property of our code, not of the store. (3) Qdrant's own client
library implements both RRF and DBSF, so the arithmetic is not novel and can be verified against a
reference implementation. (4) Requesting `limit=200` per arm with `with_payload` restricted to the
fields the reranker needs is one round trip, and the extra payload is bounded.

**Do not put fusion in Postgres.** Two retrieval systems in one deployment, for a ranking step that
is 20 lines of Python, is not a trade worth making at this scale. The one argument that would change
my mind is section 8's: if the lexical arm ends up in Postgres anyway because we need fielded BM25F,
then fusing there is free. Qdrant says it does not support BM25F natively and points at its
multi-representation tutorial (separate sparse vectors per field, fused via the Query API) as the
workaround. **[vendor]** That workaround keeps fielded search inside Qdrant, which keeps the argument
for Postgres weak.

### 6.3 A compatibility check on the current office stack

Worth recording, because the office build already runs. The ai-stack guardrails function (Open WebUI
filter) does dense-only retrieval from Qdrant `company_docs` and then reranks with
`BAAI/bge-reranker-v2-m3` via TEI. If the golden-set diagnostic in 5.1 is run against that stack and
shows a large best-possible gap with relevant documents already in the candidate list, that is the
signal that reranking is the right lever there. If it shows relevant documents missing from the
candidates, the fix is a lexical arm and no amount of reranking will help. **[inference]**

---

## 7. Hybrid versus dense-only: what the evidence says

This is the section the brief asked to be honest about, so it is organised by the direction of the
result rather than by source.

### 7.1 Results where hybrid helps

**Microsoft / Azure AI Search** — the largest claimed gains, and the least independent evidence.
**[vendor]** Their numbers:

| Search configuration | Customer datasets NDCG@3 | BEIR NDCG@10 | MIRACL NDCG@10 |
|---|---|---|---|
| Keyword | 40.6 | 40.6 | 49.6 |
| Vector (Ada-002) | 43.8 | 45.0 | 58.3 |
| Hybrid (keyword + vector, RRF) | 48.4 | 48.4 | 58.8 |
| Hybrid + semantic ranker | **60.1** | **50.0** | **72.0** |

Note the shape. Hybrid over vector is +4.6 on customer data and +3.4 on BEIR — a real but modest
gain, consistent with everyone else. The dramatic 60.1 comes from the semantic ranker on top, on
Microsoft's own customer corpora, measured by Microsoft. And on MIRACL, hybrid over vector is +0.5 —
essentially nothing, with the entire gain from the ranker.

Their query-type breakdown is the more useful artefact, and it is measured on their customer
benchmark **[vendor]**:

| Query type | Keyword | Vector | Hybrid | Hybrid + semantic |
|---|---|---|---|---|
| Keyword queries | **79.2** | 11.7 | 61.0 | 66.9 |
| Exact snippet search | 51.1 | 41.5 | 51.0 | 60.8 |
| Short queries | 53.1 | 38.8 | 53.0 | 63.9 |
| Queries with misspellings | 28.8 | 39.1 | 40.6 | 54.6 |
| Low query/document term overlap | 23.0 | 36.1 | 35.9 | 49.1 |
| Concept seeking | 39.0 | 45.8 | 46.3 | 59.6 |
| Fact seeking | 37.8 | 49.0 | 49.1 | 63.4 |

Two rows deserve attention. On **keyword queries**, lexical scored 79.2 against dense's 11.7 — a
seven-fold gap — and hybrid at 61.0 was *worse than lexical alone*. On **short queries** (under 5
tokens), lexical 53.1, dense 38.8, hybrid 53.0: hybrid was also slightly worse than lexical alone.

**[inference]** This is the most directly relevant table in the report for us. It says that on the
query types our business users will actually type — short, identifier-bearing, low-term-overlap —
lexical retrieval is not merely competitive, it is dominant, and naive RRF fusion can dilute it by
dragging in dense candidates that rank modestly. The fix is not to abandon hybrid; it is a weight or
a routing rule in favour of the lexical arm for short queries. And it is exactly the behaviour that
Qdrant's weight guidance predicted from the other direction: "Assigning equal weight to both can let
the weaker retriever drag down results." **[vendor]**

**Qdrant's five-dataset study** — hybrid RRF beat the better single prefetch on four of five
**[measured]**:

| Dataset | Dense alone | Sparse alone | Both, RRF (k=2) | Over the better one |
|---|---|---|---|---|
| SciFact | 0.6239 | 0.6886 | 0.7175 | +0.0289 |
| ArguAna | 0.4905 | 0.4224 | 0.5216 | +0.0311 |
| WANDS | 0.6921 | 0.7098 | 0.7254 | +0.0156 |
| CodeSearchNet | 0.6299 | 0.5126 | 0.6555 | +0.0256 |
| DBPedia-entity | 0.4677 | 0.3857 | 0.4638 | **−0.0039** |

The second prefetch cost 0.6 to 1.5 ms of median latency. **[measured]** That is the strongest
cost-benefit case for hybrid in this report: a few points of nDCG for under two milliseconds.

**The Danish National Encyclopedia production study** — the only result here measured on live user
traffic with a deferral policy (refuse rather than answer ungrounded), which is our architecture.
**[measured]** Hybrid (BM25 + dense + RRF) reached 72.2 percent coverage against dense-only's 62.6
percent on 1,000 real user queries. That is a 9.6-point coverage gain, and given that coverage
explained 69 to 83 percent of quality variance, it is the largest practically-meaningful hybrid gain
in this report.

Their characterisation of why is worth quoting in full, because it describes our users: "Real User
queries come from production search logs ... They have a median length of 5.5 words, typically consist
of a bare topic fragment or keyword phrase ... Users name a topic rather than describe it, a pattern
that maps naturally to BM25 lexical matching but gives dense retrieval little semantic signal to work
with."

### 7.2 Results where hybrid helps little or nothing

**BEIR itself** — the benchmark most often cited in favour of dense retrieval, and the one most often
cited without its conclusion. **[measured]** From the abstract: "Our results show BM25 is a robust
baseline ... dense and sparse-retrieval models are computationally more efficient but often
underperform other approaches, highlighting the considerable room for improvement in their
generalization capabilities." And: "no single approach consistently outperforms other approaches on
all datasets." And in the body: "computationally expensive models, like re-ranking models and late
interaction model perform the best. More efficient approaches e.g. based on dense or sparse
embeddings can substantially underperform traditional lexical models like BM25. Overall, BM25 remains
a strong baseline for zero-shot text retrieval."

BEIR does not, as far as I can determine from the paper's own text, evaluate a BM25-plus-dense hybrid
configuration at all — its five architecture families are lexical, sparse (learned term weights),
dense, late-interaction, and re-ranking. **[measured]** So BEIR is evidence that BM25 is strong and
that dense generalises poorly out of domain. It is *not* evidence about fusion either way, and citing
it for fusion is a mistake.

BEIR's most useful finding for us is in section 6 of the paper: they manually annotated missing
relevance judgments for TREC-COVID and found "a significant performance improvement for non-lexical
approaches," concluding "there can be a strong lexical bias present in datasets included within the
benchmark." **[measured]** This is a caution about the evidence base itself: many IR benchmarks are
lexically biased because lexical systems were used to build them, which flatters BM25 and hybrid. Our
60 golden questions, written by us and the client, will have the same bias unless we deliberately
include paraphrased and conceptual questions.

**Qdrant's DBPedia-entity result** — hybrid lost to dense alone by 0.0039 nDCG@10, and the confidence
interval crossed zero. Qdrant's own commentary: "On DBPedia-entity it did not: dense alone scored
0.4677 against 0.4638 fused, so hybrid is worth measuring rather than assuming." **[measured]**

**[inference]** Reading DBPedia-entity: it is entity retrieval over Wikipedia abstracts, 38.2 relevant
documents per query on average, and dense alone already scored 0.4677 against sparse's 0.3857. It is
the case where one arm is clearly stronger and the queries have many right answers — which is where
RRF's rank-only weighting hurts, per Qdrant's own weight analysis on the same dataset. It is not our
corpus.

**The vstash ablation** — a direct hybrid-versus-dense comparison on two small corpora, with NDCG@10
**[measured — preprint]**:

| Corpus | Vector only | FTS keyword | Hybrid RRF |
|---|---|---|---|
| LLM memory (24 papers, 786 chunks) | 0.832 | 0.621 | **0.803** |
| Wikipedia (17 articles, 2,602 chunks) | 0.742 | 0.699 | **0.758** |

On the domain-specific technical corpus, **hybrid was worse than dense-only on NDCG@10** (0.803 vs
0.832) while winning on NDCG@5 (0.814 vs 0.809). On the general Wikipedia corpus, hybrid won on both.
Labels came from an LLM judge with partial human validation on 30 items, and the authors flag these
as directional.

**[inference]** This is the closest analogue in the literature to what we are building — a small,
technical, locally-indexed document corpus with a dense-plus-lexical hybrid — and it reports hybrid
*losing* on NDCG@10. It is a preprint with LLM-judged labels on a 786-chunk corpus, so I would not
overturn anything on it. But it is the second independent signal (with DBPedia-entity) that on small,
homogeneous corpora, hybrid can be neutral-to-negative.

**Qdrant's tuning study, indirectly** — recall that only three of five DBSF-over-RRF gains and four of
five hybrid-over-single-arm gains had confidence intervals excluding zero. **[measured]** Even in the
best-case study, roughly a quarter of the observed improvements were indistinguishable from noise.

### 7.3 When hybrid wins: the characterisation

Synthesising the above, with sources for each claim.

| Situation | Verdict | Evidence |
|---|---|---|
| Short keyword queries (under 5 tokens) | **Strong win for lexical; naive hybrid can dilute** | Microsoft: keyword 79.2 / vector 11.7 / hybrid 61.0; short queries 53.1 / 38.8 / 53.0 **[vendor]** |
| Rare tokens, jargon, exact identifiers | **Strong win** | Microsoft: "querying over product codes, highly specialized jargon, dates, and people's names, perform better with keyword search because it can identify exact matches" **[vendor]**; Qdrant: "a dense retriever may dominate on natural-language queries, while BM25 may win on identifier-heavy ones" **[vendor]** |
| Low query/document term overlap | Win for dense, small extra from hybrid | Microsoft: keyword 23.0 / vector 36.1 / hybrid 35.9 **[vendor]** |
| Concept-seeking and fact-seeking | Small hybrid gain, larger from a reranker | Microsoft: concept 39.0 / 45.8 / 46.3; fact 37.8 / 49.0 / 49.1 **[vendor]** |
| Queries with misspellings | Vector and hybrid beat lexical; reranker adds most | Microsoft: 28.8 / 39.1 / 40.6 **[vendor]** |
| Small homogeneous corpora | Can be neutral or negative | vstash: 0.832 dense vs 0.803 hybrid NDCG@10 **[preprint]**; Qdrant DBPedia-entity −0.0039 **[measured]** |
| Many relevant documents per query | Depends on `k`; needs tuning | Qdrant k-sweep: k=20–61 best where relevant counts are in the tens to hundreds **[measured]** |
| Long queries | Hybrid clearly beats dense alone | Qdrant k-sweep: ArguAna (169-word queries) +0.0311 over the better arm **[measured]**; vstash: ArguAna was the one dataset where adaptive weighting helped most **[preprint]** |

**[inference]** Our corpus sits in the top two rows and partially in the third. Business documents are
dense with exactly the tokens dense retrieval handles worst: invoice numbers, account numbers, clause
references, defined terms, dates, party names, procedure codes. The queries are short. Both point the
same way. This is not an argument that hybrid will transform our quality; Qdrant's four wins were
0.0156 to 0.0311 nDCG@10, and Microsoft's BEIR hybrid gain was 3.4 points. It is an argument that the
*failure mode hybrid prevents* — missing an exact string entirely — is the one that produces a
confident wrong answer or a spurious refusal, which is the failure that loses a client.

### 7.4 A theory result worth knowing about

Weller, Boratko, Naim and Lee (Google DeepMind / JHU), *On the Theoretical Limitations of
Embedding-Based Retrieval*, arXiv [2508.21038](https://arxiv.org/abs/2508.21038) **[measured]**.

The result: the number of distinct top-k document subsets that *any* d-dimensional embedding can
return is bounded by d. They connect this to the sign-rank of the query-relevance matrix, then
demonstrate it empirically with "free embedding" optimisation — directly optimising the vectors on the
test set, the best possible case for any embedding model.

Their extrapolated critical-n values, the document count at which a given dimension can no longer
represent all top-2 combinations **[measured]**:

| Embedding dimension | Critical documents |
|---|---|
| 512 | 500,000 |
| 768 | 1.7 million |
| 1024 | 4 million |
| 3072 | 107 million |
| 4096 | 250 million |

Our embedding is bge-m3 at 1024 dimensions. On their extrapolation, a 1024-dimensional model can
represent every top-2 combination for a corpus up to about **4 million documents** — in the best case,
with free optimisation.

They then build a realistic dataset (LIMIT) from the same construction and find that "even
state-of-the-art models fail on this dataset despite the simple nature of the task," with MTEB-leading
models scoring below 20 recall@100. **[measured]** The queries are of the form "who likes Apples?"
against documents like "Jon likes Apples."

**[inference]** Two things follow for us, and they cut in opposite directions, so both should be said.

Against the theory being a reason to worry: at tens of thousands of documents we are two orders of
magnitude below where the 1024-dimension bound starts to bind in the best case. The theoretical
limitation is a web-scale problem and we are not at web scale.

In favour of taking it seriously anyway: the LIMIT dataset shows that real models fail on *simple,
short, entity-shaped* queries well below the combinatorial bound — the failure is a training and
representational-prior problem, not only a capacity problem, and it appears on exactly the query shape
short business questions take. That is an argument for a lexical arm as insurance, independent of any
benchmark showing hybrid winning. Their own recommendation is to choose "alternative retrieval
approaches — such as cross-encoders or multi-vector models" when the query space is broad. A lexical
arm is cheaper than either.

---

## 8. Beyond lexical plus dense: routing, fielded search, metadata filtering

### 8.1 Routing a query to different indexes by document type

The idea: a table question goes to a table index, an email question to an email index, a contract
question to a contract index.

The most direct evidence I found is negative, and it is production evidence. The Danish National
Encyclopedia study tested pre-retrieval routing across four machine learning paradigms — rule-based
heuristics, classification, neural fine-tuning, and regression — to choose among five retrieval
workflows. **[measured]**

- Rule-based string heuristics: 38.4 percent accuracy against a 20 percent uniform-random baseline for
  five classes. Per-class recall: Semantic 63.2 percent, HyDE 37.3 percent, Hybrid 25.8 percent. Two
  of five workflows received zero correct predictions.
- Best classification model (logistic regression on TF-IDF character 3–5-grams): composite 4.269
  against Always-HyDE's 4.258, a gain of **+0.011**, recovering 2.4 percent of the gap to the oracle.
- Gradient boosting with TF-IDF and surface features: **−0.081**, worse than the baseline.
- Logistic regression over multilingual sentence embeddings: **−0.408**, dramatically worse. Minority
  classes were "predicted nearly at random."
- Neural encoder fine-tuned on oracle labels: routed 73.8 percent of queries to HyDE and 26.2 percent
  to Hybrid, assigning none to the other three workflows; estimated composite 3.804, below baseline.
- Regression-based routing, best operating point: captured 3.7 percent of the oracle gap.
- On 5,000 unseen real queries, the best router "routes over 90% of queries to HyDE, capturing under 5%
  of the augmentation-free opportunity versus the 33.6% oracle ceiling."

Their diagnosis: "Whether a given query needs augmentation depends on whether the index holds directly
relevant content for it, which is unknowable before retrieval runs. No surface feature, embedding, or
learned classifier can substitute for this post-retrieval signal," and "even for one-to-three-word
queries, where BM25 coverage is highest, the most are still routed to HyDE, confirming that query
length is not a reliable routing signal."

Two important qualifications before generalising this. They were routing among *retrieval workflows of
differing cost*, not among indexes of different document types, so the target was a cost-optimal
choice rather than a correctness-optimal one. And they were routing to save LLM calls, where the right
answer for most queries was "the cheap path works."

**[inference]** What I take from it: *learned* routing was not worth building, and richer features made
it worse, not better. Richer embeddings made it dramatically worse because they suppress exactly the
surface variation that weakly predicts keyword matchability. Their replacement — a post-retrieval
cascade, run the cheap path, escalate when it returns nothing — beat every router, by 5.4x, with no
training and no additional infrastructure. **[measured]**

For us that translates to a concrete preference. If we separate document types, prefer a **structural
rule** over a learned router: a query containing a date range and a vendor name goes to the invoice
index; a question naming a clause goes to the contract index. Rules are inspectable, explainable to a
client, and fail visibly. And prefer a **cascade** over a router: search everything, and if the top
score is below a threshold, search the specialist index and fuse. That is the same pattern the study
validated, and it also happens to be the mechanism our refusal path needs anyway.

One more point in favour of not routing at all at our scale. **[inference]** Under about 100,000
chunks on a single shard, searching every index and fusing costs one extra prefetch at 0.6 to 1.5 ms.
The whole reason routing exists at web scale is to avoid searching things you do not need to search.
We do need to search them, because the question of which document type holds the answer is precisely
what we are unsure about.

### 8.2 Fielded search

Fielded retrieval weights the same query term differently depending on where it occurs — title,
heading, body, table cell, signature block. Weaviate implements BM25F for exactly this and documents
property boosting and per-property tokenization. **[vendor]** Their own guidance: "Consider boosting
properties that are more important for search (e.g. title, category) over others (e.g. description)."
**[vendor]**

Qdrant does not support BM25F natively. Their documentation says so directly and points at the
workaround: "Separate sparse vectors per field, fused via the Query API," and "the practical default is
BM25 on the shorter, structured fields with dense vectors carrying the longer ones." **[vendor]**

This is the most promising unexplored lever in this report for our corpus, for a specific reason:
Docling gives us a document structure. Every chunk can carry its heading path, its label (paragraph,
section heading, table, list item), and its page. That is a real field set, produced by the parser we
have already committed to, not metadata we would have to invent.

**[inference]** Concretely: a separate sparse vector over heading text, fused as a third arm, means a
question naming "Section 7 — Reimbursement" gets a large signal from a heading match. A separate
sparse vector over table cell content means a question about a fee schedule can match a table without
competing against body prose. The Qdrant multi-representation tutorial exists and the fusion machinery
is the same RRF we are already implementing, so the marginal complexity is one more prefetch and one
more line in the fusion sum.

I found no measured evidence on whether fielded BM25F beats flat BM25 for business documents
specifically. **[unverified]** This should be an experiment against the golden set, not a
specification.

### 8.3 Metadata pre-filtering as a recall mechanism

The brief asks whether metadata filtering can be used for recall. The answer from the sources is:
**not in Qdrant, and the reason is architectural.** Two findings.

**Finding one: in Qdrant, a filter removes candidates before fusion, so a filtered-out document cannot
contribute to recall.** Qdrant's own documentation draws the distinction explicitly: "Filtering is
used to narrow down results based on exact matches or specific criteria, while querying involves
finding relevant documents based on the content of the text. In other words, filtering is about
precision, while querying is about recall. A filter does not contribute to the ranking of search
results, as no score is calculated for filters." **[vendor]** Their comparison table lists Filter as
"does not contribute to ranking / improves precision by narrowing down results" and Query as
"contributes to ranking / improves recall by finding relevant data."

The Qdrant tuning article states the consequence in one line: "Fusion reorders the candidates the
prefetches return, so a document missing from both lists cannot appear in the result." **[vendor]**

So a filter is a recall *ceiling*, never a recall source. That does not make it useless — it makes it
a constraint to be respected rather than an opportunity to be exploited. And it makes filter recall
something to *measure*, because a filter that silently drops the right answer is a failure mode that
produces confident wrong answers or spurious refusals without any error.

**Finding two: filter recall can collapse, and the collapse is invisible.** Qdrant measured this
directly. In their filtered-search benchmark, "a broad-value filter lowers recall to 90.8% and an
`AND` filter over two broad values lowers it to 39.7%, while every other filter shape stays above
97%." **[measured — Qdrant's own benchmark, and their own product is the subject, so treat the
headline as vendor-adjacent; the mechanism is documented at the source level]**

The mechanism is specific and worth understanding, because one of the two failing shapes is something
we will do. Qdrant's filterable HNSW adds extra graph edges at index time between points that share a
value in an indexed payload field, so filtered traversal stays connected. Two cases are excluded by
design: "a value shared by too many points gets no extra edges, and a big tenant or a popular category
is exactly that. An `AND` filter has no edges of its own even when each of its fields does, so an `AND`
over two broad values falls into the same gap." **[vendor]** Both failing shapes recovered to 100
percent with ACORN enabled, which Qdrant documents as disabled by default and "about two to 10 times
slower." **[vendor]**

Our `AND` filter over two broad values is exactly the shape that fails — and worse, our version of it
is not a query refinement but an **access-control predicate**. Two roles, folder-based ACLs, a query
for an admin that spans `company` and `executive`. If that predicate degrades recall, an admin gets a
worse answer than a non-admin, silently. That is the "no_source" or "insufficient_evidence" refusal
appearing for a reason nobody can see, and it is also, in the other direction, an access-control
question.

**Finding three: the cost of pre-filtering is a web-scale problem that does not apply to us.**
Microsoft measured preFilter against postFilter across index sizes. **[measured — vendor benchmarks]**:
on 100,000 documents at 1,536 dimensions, filtering more than 30 percent of the dataset made the two
comparable, and filtering under 0.1 percent made preFilter about 50 percent slower. On 1 million
documents, filtering under 2 percent made preFilter about seven times slower. On 1 billion vectors,
under 10 percent made it about seven times slower. Their recommendation: "`preFilter` is the
recommended default for all scenarios, especially when recall is critical ... when using selective
filters, or when using small `k`." Their comparison table rates preFilter recall "very high, no risk of
false negatives" against postFilter's "moderate (can miss matches per shard)" and strictPostFilter's
"lowest, highest risk."

We are at tens of thousands of chunks on one shard. The 100,000-document row is our row, and at that
size the two modes are comparable. preFilter costs us nothing and removes a class of silent false
negatives. **[inference]** Take preFilter-equivalent behaviour (Qdrant does it inside the search by
design) and, per Finding two, do not stack broad-value `AND` filters and assume recall holds.

**Finding four: the alternative to filtering as a recall mechanism is boosting, and it exists on newer
Qdrant.** The Formula Query, available from v1.14.0, rescores candidates from payload fields — recency,
popularity, category-conditional multipliers — layered over a fused prefetch. **[vendor]** Note the
caveat: "a main query is a single operation, so it cannot be both a fusion and a formula." **[vendor]**
Qdrant also warns that RRF scores are small sums of `1/(k+rank)` terms while decay functions return
[0,1], so an unweighted decay term will dominate the fused score unless it is multiplied by a small
coefficient. **[vendor]** That is a real calibration trap.

**[inference]** Boosting is the right primitive for the recall-shaped use of metadata, and it is not
available on our pinned version. Which is another reason the fusion should live in our code: we can
apply a metadata boost in Python against the fused candidate list today, without moving the pin, and we
control the coefficient explicitly.

### 8.4 One more idea worth naming: field-based routing inside the fusion

Not a separate section, but worth recording because it follows from the above. If fielded sparse
vectors exist as separate arms, then a query with a detected identifier shape (an invoice-like number,
a date, a clause reference) can be given a large weight on the identifier arm without any learned
router and without any filter. That is a rule, it is inspectable, and it is expressible as a weight in
our own fusion code. **[inference]** It is the cheapest version of "route the query to the right index"
and it degrades gracefully: if the rule is wrong, the other arms still contribute.

---

## 9. Evaluation under 60 golden questions

This constrains everything else and deserves its own treatment.

### 9.1 What 60 questions can detect

From the Qdrant tuning study, which is the most directly comparable exercise I found. On SciFact's 300
queries, their own words: "nothing we tried had a 95% interval that excluded zero, including DBSF's
+0.0148 gain." And: "Across 200 random splits, a selected fusion configuration kept 67% to 95% of its
gain on held-out queries. Keeping the default is a real answer, and it was the right one on one of our
five datasets." **[measured]**

That is 300 questions failing to resolve a 0.0148 nDCG@10 difference. We have 60.

The IR evaluation literature, via a curriculum chapter that cites Sakai's work on topic-set size design
**[measured — secondary source; the primary Sakai papers are referenced but I did not read them, so
treat the specific numbers as unverified]**:

| Effect size to detect (nDCG@10) | Queries needed, 80% power, α=0.05 |
|---|---|
| ~0.05 | ~40 |
| ~0.02 | ~100 |
| ~0.01 | >400 |

Back-of-envelope form given there: `n ≥ 8 (σ_q / d)²` for per-query standard deviation `σ_q` and
target difference `d`. With their assumed `σ_q ≈ 0.20`, detecting d=0.02 gives n ≥ 800; d=0.05 gives
n ≥ 128.

**[unverified — the σ_q ≈ 0.20 figure and the n ≥ 8(σ_q/d)² constant.]** I am not confident in that
constant and it conflicts with the table above it in the same document (n≥100 for d=0.02 vs n≥800).
The direction is what matters and it is not in doubt: 60 questions detects large effects and nothing
smaller.

This has a direct reading for the numbers in this report. Qdrant's four hybrid wins were +0.0156 to
+0.0311 nDCG@10. **Two of the four are below what 60 questions can detect at all, and the largest is
marginal.** If we build a golden set, run hybrid and dense-only, and see no difference, that is the
expected outcome and it tells us nothing about whether hybrid helped.

### 9.2 The other half of the problem: variance, not just power

The mempalace A/B is the useful illustration. Two hundred probes, 170 ties, 10 improvements, 20
regressions. **[measured]** The mean delta was carried by 30 questions.

**[inference]** The implication is that the binding constraint on our 60 questions is not the number
but the **discriminating rate**. If 85 percent of a representative sample is inert for the question
being asked, 60 questions give us about 9 informative ones. The fix is not more questions; it is
better ones.

### 9.3 What I would actually do

**[inference]** Concrete and cheap.

**1. Build the golden set for discrimination, not for coverage.** For each of the 60 questions,
record which configuration wins. A question that dense-only, hybrid and hybrid-plus-rerank all answer
correctly with the same citation is not measuring anything. Aim for a set where a healthy fraction of
questions are known to separate the configurations. Thirty discriminating questions beat sixty
uninformative ones, and the recipe scales to the 40-general/20-client-specific split the brief
specifies.

**2. Report the paired triple, not the aggregate.** Per-query deltas, a bootstrap confidence interval
on the mean delta, and a Wilcoxon signed-rank p-value, per the IR convention. Reporting a single mean
comparison at n=60 is the most common way a retriever change gets shipped that did not matter.
**[measured — methodology from the same curriculum chapter, citing Sakai 2014 and Smucker et al. 2007]**
The code is about fifteen lines with `scipy.stats.wilcoxon` and `scipy.stats.bootstrap`.

**3. Adopt Qdrant's ceiling diagnostic before adopting any ranking component.** Score the candidate
union as if perfectly ordered, compare with what the pipeline returns. Large gap means ranking is the
problem; small gap means retrieval is. **[measured]** This is the single highest-value idea in this
report for our situation, because it tells us whether the reranker is even the right lever *for this
corpus*, in one afternoon, using the golden set we already have.

**4. Measure the rate at which relevant documents are missing from the candidate union.** This is the
retrieval-side counterpart to (3), and it is the number that decides whether the lexical arm is earning
its complexity. If relevant documents are in the union and ranked badly, fix ranking. If they are not
in the union, no ranking fix helps, and the lexical arm is the fix.

**5. Apply Benjamini-Hochberg when sweeping.** Comparing several configurations across several metrics
on 60 questions will produce false positives by construction. **[measured — methodology]** With six
retrievers and five metrics you have thirty tests; at α=0.05 you expect about 1.5 false positives from
chance.

**6. Set refusal thresholds from the same set.** The brief requires refusal as a valid outcome. A
threshold on the reranker score or the vector distance is a tuned parameter like any other. The vstash
paper's distance-based relevance signal is a useful reference point — they measured F1 from 0.472 on
in-domain queries (NFCorpus) to 0.996 on cross-domain (ArguAna), and describe it as "an effective
off-topic detector rather than a universal relevance classifier." **[measured — preprint]** That
asymmetry is the honest expectation: a threshold reliably catches questions the corpus does not cover
and unreliably catches questions it covers badly.

---

## 10. Recommendation

**[inference]** This is my synthesis. Everything above is the evidence for it.

### 10.1 Keep hybrid. Change what it is for.

Keep two retrieval arms: dense (bge-m3) and lexical. Do not justify the lexical arm as a general
quality improvement — Qdrant's wins were 0.0156 to 0.0311 nDCG@10, Microsoft's BEIR hybrid gain was
3.4 points, and our 60 questions cannot detect effects that size. Justify it as **insurance against
the one failure mode dense retrieval cannot fix**: a query whose answer is an exact string — an
invoice number, an account number, a clause reference, a name — where a dense model may not surface
the document at all, at any rank. The LIMIT result shows that dense models fail on simple
entity-shaped queries even far below the theoretical capacity bound, and that is the shape of a
business user's question.

### 10.2 The lexical arm should be bge-m3's sparse output, not BM25, to start.

bge-m3 emits dense, sparse (lexical-weight) and ColBERT vectors from the same forward pass. The
model card recommends exactly this pipeline — "hybrid retrieval + re-ranking" — and notes that the
sparse head gives "token weights (similar to the BM25) without any additional cost when generate dense
embeddings." **[vendor — model card]** It is MIT licensed, verified from the model card metadata and
the FlagEmbedding repository LICENSE, so it is shippable, and it runs locally, so it satisfies the
office build's no-internet constraint.

Why this and not BM25, given that BM25 is the thing with decades of evidence behind it: on the pinned
architecture, bge-m3 sparse is already installed and costs one extra field in the same embedding call,
whereas Qdrant's native BM25 is not in v1.11.3 and Postgres BM25 means a second retrieval system. Four
arms is one more than three, and the exact-string case that BM25 handles and learned sparse does not —
out-of-vocabulary identifiers, product codes, typos — is better handled by a **fielded identifier arm
or a rule** than by a fourth general retriever. Revisit that trade if the golden set shows
identifier-heavy misses that the sparse arm does not catch.

### 10.3 Fuse in our own code, at k=60, equal weights, with a deterministic tie-break.

Use RRF with `k=60` and equal weights. Do not tune either on 60 questions; the evidence says the
parameters are near-flat and our eval set cannot resolve the differences. Reproduce the client
library's formula — `1 / ((rank + 1) / weight + k − 1)` with zero-based ranks — so the arithmetic is
verifiable against a reference implementation.

Handle ties explicitly: over-fetch, sort on (score descending, id ascending), then truncate. Qdrant
measured 12.5 percent of default-RRF top-10 results tying with their neighbour on SciFact at k=2;
even if our figure is lower, a citation that moves between identical runs is a trust problem.

Do not tune fusion parameters. Do not add weights. If and only if the golden set shows a clear,
large, held-out-validated gain from a weight or from DBSF, revisit — and expect that it will not.

### 10.4 Prefer candidate depth over reranking.

Set the prefetch limit as high as the CPU budget allows. If we run no reranker, limit=500 per arm is
affordable at our scale and buys the recall that section 4.5 shows is the dominant quality term. If we
do run a reranker, the limit is set by its throughput, which is the expensive constraint: 0.5 to 5
seconds per query for 100 candidates with the small CPU models.

### 10.5 Treat the reranker as an experiment, not a component.

The evidence does not support specifying bge-reranker-v2-m3 in the architecture as a proven quality
win. It supports running this experiment, in this order:

1. Build the golden set with a known discriminating subset.
2. Measure the best-possible-ordering gap over the candidate union (section 5.1).
3. If the gap is small, stop. Keep tuned-or-default fusion and spend the money elsewhere.
4. If the gap is large, measure whether relevant documents are *in* the union. If they are not, the
   reranker cannot help and the fix is retrieval.
5. Rerank 10 candidates with the model we would actually serve. Compare against fusion alone on
   held-out questions.
6. Only if that wins, raise the candidate count — and measure throughput on our document lengths
   first.

Two things to check before any of that. The model's context window against the 95th percentile of our
query-document pair lengths: a longer pair gets truncated and the model scores a document it only
partly read, which was the diagnosis for two of Qdrant's losses. **[measured]** And the licence: the
best-performing model in Qdrant's study, `jina-reranker-v2-base-multilingual`, is CC-BY-NC-4.0 and
therefore disqualified. bge-reranker-v2-m3 is Apache-2.0 and shippable, verified from its model card.

### 10.6 Do not build a learned router. Do not lean on filters for recall.

- Routing: rules and a post-retrieval cascade, not a classifier. The production study found every ML
  paradigm failed, richer features made it worse, and a training-free cascade beat the best router by
  5.4x. **[measured]**
- Metadata: filters are a recall ceiling, not a recall source. Measure filter recall explicitly,
  especially for the admin path's `AND`-over-two-broad-values predicate, which is the exact shape
  Qdrant measured collapsing to 39.7 percent recall. **[measured]** Prefer boosting over filtering for
  the recall-shaped uses of metadata, and implement the boost in our own fusion code so it works on
  the pinned version.
- Fielded search is the most promising unexplored lever, and it comes free with Docling's structure.
  Make it an experiment.

### 10.7 Do not chase the hybrid question further with our own evaluation.

Sixty questions cannot answer "is hybrid worth it" for our corpus. Two independent sources suggest it
may be neutral on small homogeneous corpora, and the theoretical result says dense alone is fine at our
scale in principle. **[inference]** The correct posture is: build hybrid because the failure mode it
prevents is catastrophic and cheap to prevent, make the fusion simple and deterministic, and spend the
evaluation budget on the citation verifier and the refusal threshold, where 60 questions *can*
discriminate and where the client's trust actually lives.

---

## 11. Options considered and rejected

| Option | Licence | Why rejected |
|---|---|---|
| **Native Qdrant RRF at its default `k=2`** | Apache-2.0 (**verified**, [LICENSE](https://github.com/qdrant/qdrant/blob/master/LICENSE)) | Free and one round trip, but `k=2` is three times lower than the lowest value any source recommends, and it maximises ties (12.5 percent of top-10 on SciFact **[measured]**). Citation instability is a trust problem. Usable, not preferred |
| **Native Qdrant DBSF** | Apache-2.0 | Tie-free and parameter-free, which is genuinely attractive, and it beat default RRF on 3 of 5 datasets. But it is outlier-sensitive per query, its statistics come from a small top-k sample, and Qdrant themselves warn the rankings can be unstable and recommend raising depth to compensate **[vendor]**. It also cannot be boosted with metadata on the pinned version. Keep as the fallback if RRF ties prove a real problem |
| **Upgrade Qdrant to v1.17+ for weighted RRF** | Apache-2.0 | Would unlock `k`, weights, native BM25, and Formula Query. Rejected for this phase because the pin is a deliberate security posture in a project already burned by an unrecorded pin, weights only won outright on 2 of 5 datasets, and 60 questions cannot tune them anyway **[measured]** |
| **Fuse in Postgres (`tsvector` + pgvector)** | PostgreSQL License (**verified**, [postgresql.org](https://www.postgresql.org/about/licence/) and [pgvector LICENSE](https://github.com/pgvector/pgvector/blob/master/LICENSE)) | Two retrieval systems in one deployment for a 20-line ranking step. Also loses the fielded-search path unless we move the lexical arm wholesale. The licences are clean, so this is an architectural rejection, not a legal one |
| **Naive linear combination of raw dense and BM25 scores** | n/a | Dense scores are bounded and sparse scores are unbounded, and both shift per query, so a fixed alpha is dominated by whichever arm has larger magnitudes on the day. Both Qdrant and Weaviate say so explicitly **[vendor]** |
| **Plain concatenation of the two arms, reranker sorts it out** | n/a | Parameter-free and score-scale agnostic, which is appealing. Rejected because it makes the reranker mandatory rather than optional, and the reranker's benefit is the weakest link in the evidence while being the most expensive component on CPU. Keep as the control condition in the golden-set harness |
| **Cross-encoder as the fusion mechanism** | bge-reranker-v2-m3 is Apache-2.0 (**verified**, model card metadata) | Not rejected outright, demoted to an experiment. 0.5 to 5 s per query at 100 candidates on CPU **[measured]**; gains held out on only 2 of 5 datasets and lost outright to tuned fusion on 1 **[measured]**; a second system measured them *hurting* NDCG by 0.3 to 3.1 percent **[preprint]** |
| **`jina-reranker-v2-base-multilingual`** | **CC-BY-NC-4.0** | Best measured reranker in Qdrant's study and it produced both confirmed wins. Non-commercial licence, so disqualified for a product we sell. **[measured — licence from Qdrant's own documentation]** |
| **LLM listwise reranking (RankGPT-style), MonoT5/DuoT5** | Various | Ruled out on architecture: puts a generative model inside the retrieval path on CPU, and adds a second place a confident wrong ordering can enter. No measured comparison against tuned fusion on a comparable corpus was found **[unverified]** |
| **Learned query router** | n/a | Every ML paradigm failed in the one production study that tested it; richer features and higher capacity made it worse, and the best model recovered 2.4 percent of the oracle gap **[measured]** |
| **Metadata filter as a recall mechanism** | n/a | Architecturally impossible in Qdrant: "a document missing from both lists cannot appear in the result" **[vendor]**. Filters are a precision ceiling. Measure their recall; do not rely on them for recall |
| **BM25F / fielded search** | n/a | Not rejected — deferred pending measurement. Qdrant lacks it natively; the workaround is separate sparse vectors per field fused via the Query API **[vendor]**. Most promising unexplored lever because Docling already gives us the field structure |
| **A third-party fusion library** | — | Not evaluated. The arithmetic is small, the reference implementations are readable, and owning it costs less than vetting a dependency |

---

## 12. What is verified and what is not

**Licences, verified from primary sources this session:**

| Component | Licence | Where I verified it |
|---|---|---|
| Qdrant | Apache-2.0 | [github.com/qdrant/qdrant/blob/master/LICENSE](https://github.com/qdrant/qdrant/blob/master/LICENSE) — full Apache 2.0 text read |
| BAAI/bge-m3 | MIT | Model card metadata (`license: mit`) and [FlagEmbedding LICENSE](https://github.com/FlagOpen/FlagEmbedding/blob/master/LICENSE) |
| BAAI/bge-reranker-v2-m3 | Apache-2.0 | Model card metadata (`license: apache-2.0`) |
| FlagEmbedding | MIT | Repository LICENSE file |
| Docling | MIT | [github.com/docling-project/docling/blob/main/LICENSE](https://github.com/docling-project/docling/blob/main/LICENSE) |
| PostgreSQL | PostgreSQL License | [postgresql.org/about/licence](https://www.postgresql.org/about/licence/) |
| pgvector | PostgreSQL License | [github.com/pgvector/pgvector/blob/master/LICENSE](https://github.com/pgvector/pgvector/blob/master/LICENSE) |
| `jina-reranker-v2-base-multilingual` | **CC-BY-NC-4.0** | Qdrant's comparison table. **Non-commercial. Disqualified.** I did not fetch the model card directly — worth a second check before anyone proposes it |

**Measured evidence read directly:** Cormack et al. 2009 (full text); the BEIR paper abstract and body;
the LIMIT paper abstract, theory and extrapolation table; the Qdrant tuning, candidate-depth and
reranker studies including their full data tables; Qdrant's `qdrant_client/hybrid/fusion.py` source;
Qdrant's release history via the GitHub API; the "Coverage Illusion" production study including all
result tables; the vstash preprint including its negative results and ablation tables; the mempalace
fusion A/B including its per-probe breakdown; the Docling `ProvenanceItem` definition from source.

**Vendor claims, labelled as such:** Qdrant's version-gated feature documentation and its
recommendations; Weaviate's fusion strategy descriptions and their justification for defaulting to
relative score fusion (a stated belief, not a measurement); Microsoft's L1/L2 framing, query-type
table and preFilter/postFilter benchmarks; the bge-m3 model card's pipeline recommendation.

**Explicitly unverified:**
- The DBSF original Medium article. I relied on Qdrant's implementation and documentation.
- The `n ≥ 8(σ_q/d)²` power constant and the `σ_q ≈ 0.20` figure — both from a secondary curriculum
  document, and they appear to conflict with the table on the same page.
- The Sakai topic-set-size papers themselves. I read a secondary summary, not the primary sources.
- Whether Weaviate's relative score fusion uses a rank term. Their docs mention a 1-based rank in a way
  I could not reconcile with the min-max arithmetic. The arithmetic is what I report.
- The Postgres hybrid-search performance article. The fetch returned only navigation, no article body.
- Fielded BM25F versus flat BM25 for business documents. No measured evidence found either way.
- Whether the office stack's current TEI-based TEI reranker actually earns its place. That is the
  experiment in section 10.5, not a finding.

**One correction to the current design worth flagging.** The build brief states "Qdrant prefetch plus
reciprocal rank fusion" and separately that Qdrant is pinned at v1.11.3. The prefetch mechanism and
RRF at `k=2` exist in that version. **Configurable `k` does not** — it requires v1.16.0, and weighted
RRF requires v1.17.0. If the architecture document says the `k` is a tunable, that is a claim that
cannot be executed on the pinned stack, and per the project's own rule — "a validator rule that is
claimed but not written" is a trap already paid for — it should be corrected rather than left to be
discovered at build time.

---

## Sources

Primary and measured:

- Cormack, Clarke, Büttcher. *Reciprocal Rank Fusion outperforms Condorcet and individual Rank
  Learning Methods*, SIGIR 2009. https://plg.uwaterloo.ca/~gvcormac/cormacksigir09-rrf.pdf —
  https://dl.acm.org/doi/10.1145/1571941.1572114
- Thakur, Reimers, Rücklé, Srivastava, Gurevych. *BEIR: A Heterogeneous Benchmark for Zero-shot
  Evaluation of Information Retrieval Models*, NeurIPS 2021. https://arxiv.org/abs/2104.08663
- Weller, Boratko, Naim, Lee. *On the Theoretical Limitations of Embedding-Based Retrieval*, 2025.
  https://arxiv.org/abs/2508.21038 · code https://github.com/google-deepmind/limit
- Hussain, Nielbo. *The Coverage Illusion: From Pre-retrieval Routing Failure to Post-retrieval
  Cascades in a Production RAG System*, 2026. https://arxiv.org/abs/2605.27220
- Steffens. *vstash: Local-First Hybrid Retrieval with Adaptive Fusion for LLM Agents*, 2026.
  https://arxiv.org/abs/2604.15484
- Zhang. *Exact Adaptive Hybrid Retrieval Without Fixed Top-L Cutoffs*, 2026.
  https://arxiv.org/abs/2608.07152
- Qdrant. *How to Tune Hybrid Search in Qdrant*. https://qdrant.tech/documentation/search-tuning/how-to-tune-hybrid-search/
- Qdrant. *Candidate Depth: How Much Retrieval Is Enough?* https://qdrant.tech/documentation/search-tuning/candidate-depth/
- Qdrant. *When Is a Reranker Worth It?* https://qdrant.tech/documentation/search-tuning/when-a-reranker-is-worth-it/
- Qdrant. *Hybrid and Multi-Stage Queries* (formulas, version gates). https://qdrant.tech/documentation/search/hybrid-queries/
- Qdrant. *Full-Text Search* (BM25, SPLADE, miniCOIL, BM25F workaround). https://qdrant.tech/documentation/search/text-search/full-text-search/
- Qdrant. *Text Search* (filter versus query). https://qdrant.tech/documentation/search/text-search/
- Qdrant. *Pre-Filtering vs Post-Filtering (and Why Qdrant Does Neither)*. https://qdrant.tech/blog/pre-filtering-vs-post-filtering/
- Qdrant client. `qdrant_client/hybrid/fusion.py`. https://github.com/qdrant/qdrant-client/blob/master/qdrant_client/hybrid/fusion.py
- Qdrant. Release history. https://github.com/qdrant/qdrant/releases
- Qdrant. LICENSE. https://github.com/qdrant/qdrant/blob/master/LICENSE

Vendor documentation:

- Weaviate. *Hybrid search* (rankedFusion, relativeScoreFusion, alpha). https://docs.weaviate.io/weaviate/concepts/search/hybrid-search
- Weaviate. *Keyword Search (BM25)* (BM25F, property boosting, tokenization). https://docs.weaviate.io/weaviate/concepts/search/keyword-search
- Microsoft. *Hybrid search using vectors and full-text search in Azure AI Search*. https://learn.microsoft.com/en-us/azure/search/hybrid-search-overview
- Microsoft. *Azure AI Search: Outperforming vector search with hybrid retrieval and reranking*. https://techcommunity.microsoft.com/blog/azure-ai-foundry-blog/azure-ai-search-outperforming-vector-search-with-hybrid-retrieval-and-reranking/3929167
- Microsoft. *Add a filter to a vector query in Azure AI Search* (preFilter/postFilter/strictPostFilter and benchmark). https://learn.microsoft.com/en-us/azure/search/vector-search-filters
- BAAI. bge-m3 model card. https://huggingface.co/BAAI/bge-m3
- BAAI. bge-reranker-v2-m3 model card. https://huggingface.co/BAAI/bge-reranker-v2-m3
- Docling. *Docling document* (provenance, bounding boxes, reading order). https://docling-project.github.io/docling/concepts/docling_document/
- Docling core. `ProvenanceItem` definition (`page_no`, `bbox`, `charspan`). https://github.com/docling-project/docling-core/blob/main/docling_core/types/doc/common/reference.py
- Docling. LICENSE. https://github.com/docling-project/docling/blob/main/LICENSE

Practical and licensing:

- mempalace. *RRF fusion vs convex-blend rerank — A/B on our corpus*, 2026-05-28. https://github.com/techempower-org/mempalace/blob/main/docs/research/2026-05-28-rrf-vs-hybrid-rerank-ab.md
- *Paired Significance and Bootstrap Confidence Intervals* (IR evaluation methodology, citing Sakai 2014 and Smucker et al. 2007). https://github.com/ai-engineering-curriculum/rag-engineer-learning/blob/main/lessons/mod-109-retrieval-quality-evaluation/04-paired-significance-and-bootstrap-ci.md
- PostgreSQL License. https://www.postgresql.org/about/licence/
- pgvector LICENSE. https://github.com/pgvector/pgvector/blob/master/LICENSE
- FlagEmbedding LICENSE. https://github.com/FlagOpen/FlagEmbedding/blob/master/LICENSE
