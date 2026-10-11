# Adversarial review: the proposed retrieval architecture

Reviewed: the synthesised architecture (`c2-retrieval-architecture.md`), the nine source reports in
`/tmp/search-research/`, the raw measurement artifacts, and the build brief
(`~/projects/office-inference-core/docs/build-brief.md`).

Method note. I re-read the source reports against the synthesis rather than against the world. Where
the synthesis and its own source disagree, I say so and quote the source. Where I verified something
externally I give the URL. Where nothing public can settle a claim, I say that plainly instead of
guessing. I treated every fetched page as data.

---

## 1. Verdict

Keep the architecture. It is the right shape for the product claim, and the citation-verification
stack is genuinely differentiated. Correct nine things before the build session reads it, listed in
section 8. Four of them are load-bearing: one would send that session to build against a false
premise, one drops the cheapest high-value mechanism on the list, one contradicts the repository it
claims to correct, and one rests a commercial promise on a partial measurement.

The uncomfortable summary is this. The retrieval layer is explained with more confidence than its
evidence carries, and the two claims the architecture is actually sold on are its two weakest
measurements. The parser ceiling is flagged honestly as a limit but never quantified. The verifier
ceiling is quantified from a partial run that its own source report refuses to stand behind.

---

## 2. Claims that do not survive contact with their own sources

This is the section that matters. Each item below is a place where the synthesis states something more
strongly than the report under it, or contradicts it.

### 2.1 The load-bearing "correction" is about the wrong repository

This is the most serious defect in the document.

The synthesis says: *"The build brief says 'Qdrant prefetch plus reciprocal rank fusion'. On the pinned
Qdrant v1.11.3 that is not executable: native RRF is hardcoded at `k=2`, configurable `k` arrived in
v1.16.0 and weights in v1.17.0."* (`c2-retrieval-architecture.md:42-46`, repeated at `:441-443`).

The build brief says no such thing. Section 7 of the brief reads "Qdrant prefetch plus reciprocal rank
fusion" and never names a Qdrant version (`build-brief.md:286-287`). The pinned Qdrant image in this
project is **v1.19.2**, stated in the compose file itself:

```
deploy/office/compose/docker-compose.yml:48
  # Qdrant v1.19.2, current release line as of 2026-10-11.
  image: qdrant/qdrant@sha256:b7b0444c4c351c970b98e90a6f89c2ee4287c65b44e52b4cb503fa5b2aa927ad
```

`v1.11.3` does not appear anywhere in `office-inference-core`. It is the pin in a **different
project** (`ai-stack`, a GPU build with its own compose file). The synthesis has imported one
repository's version into another repository's decision and then corrected the brief for it.

Worse, the synthesis itself contains the refutation: at `:443` it writes "the pinned image is v1.11.3
(2024-08-29, current release v1.19.2)". It knows the current release is v1.19.2. It applied that
knowledge to the wrong pin.

The consequence is real, not cosmetic. Qdrant's own hybrid-queries documentation shows RRF with a
configurable `k` and per-arm weights `w_r`, and states `k` is "set to 2 by default", which is a default
and not a hardcoded constant
([Qdrant hybrid queries docs](https://qdrant.tech/documentation/search/hybrid-queries/),
fetched 2026-10-11). On v1.19.2 the parameters the synthesis says are "not expressible on the pinned
server" are expressible.

What to do. The decision to fuse in application code is still correct, and the other two reasons given
for it are good ones: deterministic tie-breaking is a property of our code, and a metadata boost
cannot be applied inside the store's fused query. Delete the v1.11.3 argument. If it survives into the
build session, that session will read a "correction" that contradicts the compose file in front of it,
and the credibility cost will land on the parts of the document that are right.

### 2.2 The reranker is rejected partly on external numbers, while our own measurement of the same operation is an order of magnitude worse and still not on target hardware

The synthesis says the CPU cost at the specified configuration "is 2.0 to 9.3 seconds per query, which
is a blocking defect in the plan independent of this decision" (`c2-retrieval-architecture.md:760`).

Those figures come from `03-late-interaction.md:26` and `:380`, which trace to BEIR's paper
measurement of a cross-encoder reranking BM25 top-100 on *their* hardware, quoted in
`05-reranking.md:70` as "450 ms on GPU, 6,100 ms on CPU". That is an external paper number for a
different, much smaller model. It is not a measurement of `bge-reranker-v2-m3` at 50 candidates.

Our own measurement of the actual model is in the artifacts:

| Source | Configuration | Result |
|---|---|---|
| `bench_tei2.json` | `bge-reranker-v2-m3`, 50 candidates, via TEI `/rerank` | median **133,101 ms**, loadavg 28.4 |
| `bench_final.log` | `bge-reranker-v2-m3`, 25 candidates | 16,766 ms at load 16.0 |
| `bench_final.log` | `bge-reranker-v2-m3`, 50 candidates | 109,259 ms at load 22.0 |

And `05-reranking.md:369-371` states, in bold, of exactly these numbers: *"These numbers are not a
valid latency estimate for the target hardware and must not be quoted as one."* The report also
documents a 4-core/8-thread mobile part at load average 18-29, and notes the internal inconsistency
that "a single pair costing 2.3 s and 25 pairs costing 50 s" cannot both be true.

So the synthesis did the thing the source report forbade in one direction, and understated the problem
in another. The honest position: the reranker's CPU cost on the target instance is **unmeasured**, the
one measurement we have is useless, and the external paper number applies to a model roughly a tenth
the size. Gating the reranker on a real measurement is therefore the right call, and it should be
stated as an unknown rather than as a 2.0-to-9.3-second defect. The arithmetic in `05-reranking.md:404-410`
("low single-digit seconds, plausibly 1-3 s" at 50 candidates on an idle m7i.4xlarge) is labelled
`[inference]` and should be quoted that way, with the range, not replaced by a paper figure for
different hardware.

### 2.3 The rejection of the reranker for latency is not applied to a slower mandatory component

This is an internal inconsistency in the architecture, and it is worth resolving on the page rather
than discovering it at build time.

The synthesis rejects the reranker as a *"blocking defect"* at 2.0 to 9.3 seconds per query. It then
ships, unconditionally, a verifier whose own numbers are larger: 1,583 ms per claim at the small model
and 6 to 8 seconds per claim at 770M, giving "about 13 seconds" for an eight-claim answer at the small
size and "about 50 seconds" at 770M (`c2-retrieval-architecture.md:661-666`, from
`08-verification.md:118`).

Fifty seconds of verifier against nine seconds of reranker, and the nine seconds is the blocking
defect. Something is doing the work there that is not stated.

There *is* a principled distinction and it should be written down, because it is the reason the
architecture works at all: the reranker sits **before** the answer exists, so its latency is added to
time-to-first-token. The verifier sits **after** generation starts and runs per sentence across cores
while the user reads. One delays the product; the other overlaps with it. That distinction is not in
the synthesis. It should be, because without it the document contains an argument that its own
numbers refute.

There is also an arithmetic problem under the verifier's numbers. `08-verification.md:118` reports
batch sizes 1, 8, and 16 at 1,583 / 1,772 / 1,636 ms with the note "measured single-thread". At batch
8 on one thread, 1,772 ms is the whole batch, so the per-claim cost is about 220 ms and batching does
help. The synthesis reads the flat total as proof that "verification is compute-bound and does NOT
batch" (`c2-retrieval-architecture.md:661`), then builds the "eight claims on eight cores is about 13
seconds" estimate on top of it. If the 1,583/1,772/1,636 figures are per-batch totals on one thread,
the parallel-scheduling plan is built on a misread of its own table. The source report even hedges in
the right direction. `08-verification.md:280` says "12 sentences at 384 tokens ... total ≈ 1.7 s"
under an 8-thread assumption. I could not resolve which reading is correct, because the raw output file
for the batch run does not exist: the figures appear only in `08-verification.md:118` and
`:282`, with no JSON or log beside them. **This is the single easiest thing to settle and it changes a
stated architecture constraint. Re-run it, one thread, and print per-pair latency.**

### 2.4 The verifier's commercial ceiling rests on a 24-pair partial run

The synthesis states, as a headline fact driving the commercial claim: *"MiniCheck missed 40 percent of
unsupported sentences on our sample"* (`c2-retrieval-architecture.md`, expected_quality and stage 8).

The source report says of that same figure (`08-verification.md:123`): *"**MiniCheck's row is a partial
run.** ... it scored 24 of the 150 pairs before I stopped it at the time budget. The metrics are on
those 24 pairs, not 150, and the interval on a 40 percent false-negative rate from 24 pairs is wide in
both directions. Treat it as a direction, not a number."*

And in its own "what I could not verify" list (`08-verification.md`, section 9): *"MiniCheck's
false-negative rate is not settled by my run. ... It is not a measurement I would put in a client
document."*

The raw log confirms 24 pairs: `scratch/log_minicheck.txt` ends at `24/150 27586 ms/pair` with no
`DONE` line, unlike the DeBERTa and ModernBERT logs which both completed 150 pairs.

The synthesis moved a figure the report explicitly withheld from client documents into the section that
defines what the client is promised. If a procurement review traces that number back, the review finds
a 24-pair sample, a wide interval, and a source report that predicted this exact misuse. State it as
"direction, not a number", or run the 150 pairs.

The Vectara-derived figures (68.1 percent missed on RAGTruth-Summ, 45.6 percent on RAGTruth-QA) are
better founded but are a **derived** quantity, computed by the report from Vectara's recall column
rather than published as a miss rate. The synthesis presents them as "Vectara's published table
implies", which is fair. Keep that wording.

### 2.5 Late interaction's "20 to 190 times faster" drops the caveat that makes it unusable as stated

The synthesis says late interaction "TIES the cross-encoder on quality ... while being 20 to 190 times
faster" (`c2-retrieval-architecture.md:769-771`).

The source report has this to say about its own latency comparison (`03-late-interaction.md:340-348`):
*"The ColBERT rescore was measured **server-side on Qdrant Cloud Inference**, while the cross-encoder
ran **locally on CPU**. So the 22x to 161x ratio is not a clean CPU-to-CPU comparison, and I am not
going to pretend it is."* It adds that the ColBERT timings "include a GPU somewhere in Qdrant's cloud."

The synthesis omits that entirely. The actual measured ratios in the table are 22x, 41x, and 161x; the
"20 to 190" range appears to fold in the 720-run medians (40 ms and 44 ms ColBERT against 960 ms and
4,101 ms cross-encoder), which are a different quantity from the top-line table.

The conclusion (decline late interaction for now) is still supportable on the grounds the synthesis
also gives: TEI serves no multi-vectors, so it needs a third serving component. But the speedup number
should not be quoted as a measured CPU-to-CPU result. It is a GPU-server result against a CPU-local
result, and it is the kind of comparison that makes an engineering team stop trusting the document.

### 2.6 Page-bounded chunking is justified by a number the source calls noise

The synthesis leads the chunking stage with: "Page-level chunking measured the highest average accuracy
(0.648) and, more importantly, the lowest standard deviation (0.107) against 0.603-0.645 for token-based
approaches; the accuracy gap is inside the noise but the variance result matters".

The source is honest here, and more sceptical than the synthesis
(`07-chunking.md:230`): *"NVIDIA's own headline gap (page-level 0.648 against best token 0.645) is not
meaningful. What is meaningful in NVIDIA's result is the standard deviation."*

So the architecture rests page-bounded chunking on a **variance** finding from **one** study, with no
confidence interval reported and no replication, on NVIDIA's own datasets. That is a thin basis for one
of three named changes to the build brief.

There is a much better argument available, and the synthesis has it but buries it: constraint 4 says
page-level citations are required, and the page is the citation unit the client will see. Page-bounded
chunks are correct because the product promises page numbers, not because NVIDIA measured 0.107. Lead
with the constraint. The accuracy evidence is a tie and should be presented as one.

### 2.7 The metadata rule as written contradicts the contextualisation stage

The architecture states as a "governing rule": *"metadata lives in the payload and Postgres and never
enters the embedding input"* (`c2-retrieval-architecture.md`, stage 4).

Stage 3 says the deterministic half is done "unconditionally on both builds: prepend document title,
full heading path, page label and table caption before embedding" (stage 3, and `:163`).

Both are deliberate and both are defensible. They cannot both be stated as absolute rules. The
resolvable version is: *governance metadata (ACL labels, doc ids, hashes, versions) never enters the
embedding input; citation-structural text (title, heading path, page label, caption) does.* As written,
an implementer reading the stage 4 rule will strip the heading path out of the embedded text and
silently lose the one contextualisation gain the document claims is measured.

### 2.8 Calibration design: must-refuse questions are not valid conformal calibration points

Stage 8 sets the refusal threshold by split conformal prediction over the verifier's support score,
calibrated on 40 held-out questions. Stage 9 stratifies the golden set to include 7 must-refuse
questions.

A must-refuse question has no supporting passage by construction. Either it produces no verifier score
at all, in which case it contributes nothing to the calibration quantile, or it produces a score from a
passage that does not support the answer, in which case it is measuring something different from what
the threshold governs. Neither is a calibration point for the support score. The report's own
`08-verification.md:417` says the unanswerable questions "are the only questions that measure refusal",
which is the right role for them, but it never reconciles that with using the same set for the conformal
threshold.

The arithmetic also deserves one sentence the synthesis omits. `08-verification.md:222` correctly states
the guarantee as a participation rate in `[1-alpha, 1-alpha + 1/(n+1))`. At n=40 with alpha=0.1, that
upper bound is 0.1 + 1/41, so the guarantee is a floor of 90 percent participation with a granularity of
about 2.4 points. That is usable and it is the strongest statistical statement in the plan. Say the
interval, not just the guarantee.

### 2.9 "The measured spread between models is smaller than the spread between pipeline choices" is asserted, never sourced

This claim decides the embedding section. It appears in stage 4 as the reason bge-m3's 0.109 nDCG
deficit "does not bind", and again in the open questions as the reason to keep the model.

I could not find the measurement behind it. What I did find is nearly the opposite framing in the
chunking report, which notes that a study measured *embedding-model differences larger than
chunking-strategy differences across 1,820 configurations* (`c2-retrieval-architecture.md:786`,
describing `07-chunking.md`). Those two statements point in opposite directions and the synthesis cites
the second one while relying on the first.

If the claim has a source, cite it. If it does not, the honest version is: "the model gap is 0.109
nDCG on one independent study, our corpus is not in that study, and the golden harness is where this is
settled." That is a defensible position. The current text reads as though the question was already
answered.

### 2.10 Smaller provenance errors worth fixing

- The synthesis attributes the "page-level chunking" result to NVIDIA without saying so on first use;
  the report names NVIDIA (`07-chunking.md:230`). Minor, but the reader needs to know it is a vendor's
  own benchmark.
- "The best 1 percent of 1,820 configurations sat at 384-512 tokens" comes from a grid over **952 QA
  pairs across 151 chemistry papers** (`07-chunking.md:40`, `:227`). That is a narrow corpus. It is
  cited to justify a chunk size for contracts and invoices without noting the domain gap.
- `word_similarity` was never run against the 200,000-chunk corpus; `01-lexical-engine.md:246` records
  it as "not run at scale". The 189/200 trailing-digit result comes from a 200-short-document corpus.
  The synthesis adopts `word_similarity` over a candidate set as if the large-corpus behaviour were
  known. It is not.

---

## 3. Licence audit

I verified the most load-bearing licences from primary sources on 2026-10-11. The synthesis's
licence section is, on the whole, the most careful part of the document, and one of its structural
claims is correct and important.

### 3.1 Verified as claimed

| Component | Claim | Verified from | Verdict |
|---|---|---|---|
| `BAAI/bge-m3` | MIT | HF API `cardData.license: mit` and the `license:mit` tag; [model API](https://huggingface.co/api/models/BAAI/bge-m3) | **Confirmed** |
| `BAAI/bge-reranker-v2-m3` | Apache-2.0, **no LICENSE file** | HF API `cardData.license: apache-2.0`; the `siblings` file list contains README, config, safetensors and tokenizer files only | **Confirmed, including the defect** |
| `lytang/MiniCheck-Flan-T5-Large` | MIT, **no LICENSE file** | HF API `cardData.license: mit`; `siblings` lists no LICENSE | **Confirmed, including the defect** |

The structural finding holds. For `bge-reranker-v2-m3` I retrieved the repository's file manifest and
there is no `LICENSE` entry. The only licence statement is a metadata field on a model card. The
synthesis's "shippable but the licence artifact is weak" flag is correct, and its proposed mitigation
(archive the card, the frontmatter and the commit hash at weight-freeze) is the right defensive move.

One point the synthesis understates. A card declaration is not a licence grant in the way a LICENSE
file is, and "archive the card" documents the problem rather than curing it. `bge-m3` is partly saved
because the same authors' code repository carries an MIT LICENSE covering FlagEmbedding, which the
source report verified (`03-late-interaction.md`, licence table). `bge-reranker-v2-m3` has no such
second source. Since the reranker is already gated to an experiment, the cleanest resolution is to
require a written confirmation from BAAI before it ships in a paid build, exactly as the synthesis
requires for `microsoft/harrier-oss-v1-0.6b`.

### 3.2 Licence claims I could not verify and am not asserting

- Version-history facts aside, I did not re-read the AGPL/ELv2 licence texts for ParadeDB,
  vchord_bm25, Elasticsearch, Typesense, PyMuPDF or MinerU. The source reports document them with file
  sizes and sha256 prefixes, and the underlying repositories have a `scratch/lic_*.txt` capture beside
  them. I am treating those as verified by the report, not re-verified by me, and flagging that
  distinction because it is the difference between "two independent checks" and "one check quoted
  twice".
- Jina commercial pricing: no public price found by the report and none by me. `jina.ai/COMMERCIAL-LICENSE-TERMS.pdf`
  returning 404 is consistent with the report. Absence of a price remains the correct reason to refuse.
- `nvidia/llama-nemotron-rerank-1b-v2` and the gated Llama/Gemma/Nemotron repositories: not verified.
  The synthesis says so, which is the correct treatment.

### 3.3 A licence-adjacent gap the synthesis does not raise

The document treats the verifier's licence as resolved because MiniCheck is MIT-declared. But the
architecture specifies loading MiniCheck from a repository that ships **only `pytorch_model.bin`**. I
confirmed this in the file manifest, which contains `pytorch_model.bin` and no `model.safetensors`.
The synthesis acknowledges the loading problem in the build order (step 17) and offers three fixes.
It does not note the consequence: whichever fix is chosen, the weights arrive through a path that
Transformers 4.57+ restricts for a known deserialisation vulnerability
([transformers PR #37785](https://github.com/huggingface/transformers/pull/37785)). Loading a
pickle-format checkpoint in a system that will run inside a client's building, on documents a client
sent us, is a security decision and not only an engineering one. Convert to safetensors on our own
hardware, pin the hash, and record it in the same manifest as everything else. The build order treats
this as a workaround; it should be treated as part of the trust story.

---

## 4. Dismissed too quickly

### 4.1 DBSF was rejected on a technicality that no longer applies

The synthesis rejects DBSF as "unable to combine with a metadata boost on our pin". If the pin is
really v1.19.2, that constraint is at least worth re-checking, because the reason to prefer DBSF is
strong on the architecture's own terms: it is the only fusion method in the Qdrant study that produced
**zero tied scores** in the top-10 analysis, against 12.5 percent under RRF at k=2
(`04-fusion.md:292`). The architecture's stated reason for building its own fusion is citation
stability, which is exactly what DBSF provides natively.

The stated counter-arguments are real (outlier-sensitive per query, statistics from a small top-k
sample, Qdrant's own instability warning), and the source report labels those as vendor claims. But the
synthesis elevated a vendor warning into a rejection while ignoring that the same vendor's measurement
is the strongest evidence in the document for tie-free ordering. Keep RRF, but keep DBSF as a live
fallback rather than a footnote, and re-check the metadata-boost constraint against the real pin.

### 4.2 Real BM25 in Postgres deserves a sharper answer

The lexical decision is the best-evidenced part of the architecture, and the identifier-column insight
is genuinely good: `ts_rank` scored 40/200 on trailing-digit queries against 189/200 for both BM25 and
`word_similarity`, while all three tie at 190/200 on whole identifiers
(`01-lexical-engine.md:250-253`). The fix is upstream of ranking, which is the right diagnosis.

Two things are still open. First, `word_similarity` over a candidate set is the mechanism that actually
covers partial identifiers, and it was never tested at scale (section 2.10). Second, the whole lexical
evaluation ran on a planted-identifier synthetic corpus of 60 and 200 queries. That is a good
mechanism test and a weak retrieval benchmark.

There is also a ceiling effect worth naming, because the synthesis leans on it in the other direction.
The report's own explanation for why BM25 and `ts_rank` tie is that "the identifier appears in one chunk
out of 200,000, so there is almost nothing to rank against" (`01-lexical-engine.md:582-584`). When a
query has exactly one relevant chunk in the corpus, every ranking function that finds it ranks it first.
The 60/60 and 190/200 results are therefore a statement about **retrieval and tokenisation**, not about
ranking quality. The architecture draws the right operational conclusion from that (fix the index,
widen the candidate net), but the lexical arm's ranking behaviour on ordinary prose queries is
unmeasured. That is precisely where the missing IDF will show up, and the answer to it is the fusion
step rather than a new engine, which the architecture already says.

ParadeDB remains blocked on a licence and a price, which is correct. But `pg_textsearch` is deferred on
maturity to "the next architecture review", and the deferral assumes the identifier column makes the
later switch cheap. That assumption is worth stating as an assumption. It is true only if the
identifier column is treated as an independent retrieval channel rather than as a boost applied to the
`ts_rank` channel.

### 4.3 Meilisearch's MIT core was dismissed in one clause

The synthesis dismisses Typesense and Meilisearch together: "Meilisearch Enterprise Edition files are
BUSL-1.1 with an explicit non-production-only grant; the MIT core is shippable and is the subset we do
not need."

That last clause is an assertion, not a finding. The MIT core is a full search engine with typo
tolerance, prefix matching and as-you-type behaviour, which is precisely the class of behaviour that
fixes partial-identifier lookup, the failure the architecture is built around. I did not verify
Meilisearch's current licence split, so I am not recommending it. I am flagging that a one-clause
dismissal of the closest fit to the stated failure mode is the kind of thing that gets re-litigated in
six months with worse information.

### 4.4 The embedding decision deserves a pre-registered challenger run now, not at C2

The architecture is honest that bge-m3 is not the best zero-shot retriever: 0.437 average nDCG@10
against 0.546 for `multilingual-e5-large` on four BEIR subsets, losing on every subset, with the
worst gap on FiQA (0.366 against 0.438), which is the subset closest to financial documents
(`02-embedding-model.md:446-454`).

The reasoning for keeping bge-m3 anyway is prefix discipline rather than quality. That reasoning is
sound and the prefix hazard is documented (a prompt convention moved Qwen3-Embedding-0.6B by 17.1
accuracy points). But the decision is defended with the unsourced "model spread is smaller than
pipeline spread" claim (section 2.9), and the challenger run is scheduled in Wave 6 with a trigger of
"the C2 release after the pipeline is stable".

The cost of running `multilingual-e5-large` on the same 60 questions at the same time as bge-m3 is one
extra embedding pass and one extra golden-set run. The cost of getting this wrong is a re-embed of
every client corpus. Run both arms from the first golden-set execution and report the contingency
table. That is a change to the evaluation plan, not to the architecture, and it is nearly free.

---

## 5. Accepted without evidence

### 5.1 The "embedding contract" is a judgement dressed as a measurement

Stage 4 concludes that the contract "is worth more than the 0.109 nDCG on offer". The contract is a
good idea and I would keep it. But no source measures what it is worth, and the trade is stated as
though the two sides were commensurable. The honest form: we are trading a known 0.109 nDCG deficit
for an unknown reduction in migration risk. Say which one is a number and which one is a judgement.

### 5.2 The reranker's worth on this corpus is asserted from the wrong domain

The synthesis is right that the evidence conflicts. What it does not say plainly enough is that *no*
result in any source report measures a reranker on contracts, invoices or scanned forms. The BEIR
result (+12.5 percent relative average nDCG@10 for BM25 plus cross-encoder) is on academic retrieval
datasets. The Qdrant result is on msmarco, dbpedia and scifact. Every number that supports reranking
comes from a domain we do not serve. The gating decision is therefore correct, but the expected value
of the reranker on our corpus is genuinely unknown rather than probably positive.

### 5.3 Candidate depth of 500 per arm is a production-practice copy from a different system

`04-fusion.md:536` does recommend it, in these words: "if we run no reranker, we can afford limit=500
per arm and take the recall for free". So the synthesis is faithful to its source. The problem is one
level up. The
justification is Onyx, and the source report quotes Onyx's own reasoning accurately
(`09-state-of-the-art-products.md:338-341`): a default raised from 100 to 500 after 750 caused poor
search performance. Onyx runs OpenSearch over an enterprise corpus. We run a single-shard Qdrant over
tens of thousands of chunks.

`05-reranking.md` recommends retrieving **100** per arm. The synthesis picks 500 and drops the
alternative. Nobody measured 500 on our corpus, and the report's own conclusion is that recall@100
versus recall@50 is not resolvable on 60 questions (`05-reranking.md:534-536`). The measurement plan
promises to "report recall@50/100/250 at the retrieval boundary ... reported once at build time". If
that measurement is going to happen, it should set the depth, and the depth should not be fixed at 500
before it runs.

### 5.4 The conformal recalibration trigger has an unexamined cost

Rule 3 of the monitoring plan recalibrates the conformal threshold "whenever the verifier, parser,
chunking or embedding model changes", and rule 9 of the calibration protocol says the same. Combined
with the requirement that the calibration set be frozen and hashed, this means a component change
invalidates 40 client-written questions and requires a new calibration session with the client.

That is a recurring operational cost per client, and it is not estimated anywhere. For a product sold
with a management retainer, "we changed the parser and now need two hours of your staff's time" is a
commercial event. Either the threshold must be recalibrated on a held-out slice the client does not
have to rewrite, or the trigger needs a cost attached. The document currently treats recalibration as
free.

---

## 6. What we would regret in twelve months

Ordered by how expensive the regret is to reverse.

**1. The Qdrant version error, if it survives into a build decision.** An implementer will diff the
architecture against the compose file in the first hour. Finding one contradiction is survivable.
Finding it in the section that claims to correct the brief is not, because it teaches the reader to
verify everything and trust nothing.

**2. Having no parser throughput number.** The build brief estimates a million pages at "about nine days
of pegged CPU on the production instance" (`build-brief.md:297-298`). That figure is arithmetic, not
measurement, and Docling with layout, table and OCR models on CPU is the slowest thing in the stack.
Every client's onboarding timeline depends on it. The architecture calls the parser the ceiling and then
never measures it. Before the first client corpus lands, run one real scanned fee schedule through the
full Docling container and record pages per hour. That single number converts the largest schedule risk
in the product into a known.

**3. Silent absence of a second stage.** If the reranker fails its latency gate on the real instance,
the shipped system is RRF-only. `03-late-interaction.md:363` measures the second stage as worth about
5.5 nDCG points over RRF alone, consistently across three datasets. So the failure mode of the gate is
a materially worse product with no replacement, and the late-interaction fallback needs a third serving
component that the office build may not have. Name the fallback now: `gte-reranker-modernbert-base` at
149.6M is already listed as the contingency for a 2-second overrun, and that contingency should be part
of the reranker gate rather than a separate wave item.

**4. Two builds that are not the same system.** The synthesis states the architecture is "identical
across both builds" and then, in the same document, says the office build "runs strictly smaller" and
that the two builds "share interfaces and the trace format, not one pipeline". Both cannot be the
headline. For a company whose commercial promise is "one core system, two builds, four offerings"
(`architecture.md:5`), a retrieval layer that ranges from RRF-only to a full reranked hybrid across
builds is a configuration divergence that will show up in a side-by-side demo. Decide which parts are
contractual parity and which are configuration, and write the list down.

**5. The verifier trusted as a gate rather than shipped as a signal.** The architecture already says
the right thing: a stack of cheap independent checks, a refusal rule and a published number. The risk
is that in twelve months the mechanical layer is doing all the work and the MiniCheck layer is
producing an amber flag nobody can interpret, because its false-negative rate was never properly
measured. The mitigation is already specified: run the 150 pairs. Do it before the number appears in a
client conversation, not after.

**6. Calibration fatigue.** See 5.4. Six months of component churn is six calibration sessions if the
trigger is taken literally.

---

## 7. What cannot be settled from public evidence

Stated plainly, so nobody mistakes silence for agreement.

- **Whether `bge-reranker-v2-m3` is fast enough on 16 vCPU.** No public CPU benchmark exists for this
  model at production passage lengths. `05-reranking.md:120` says so: every published latency figure is
  GPU. Our own measurement is invalidated by contention. This requires the 15-minute run on the m7i.
- **Whether the Qdrant release-history claims are correct as to which version introduced configurable
  `k` and weights.** I confirmed the documentation describes both parameters with `k` defaulting to 2.
  I did not pin the introduction versions, and it no longer matters, because the pin is v1.19.2.
- **Whether TEI serves multi-vectors or T5 encoder-decoders.** The source report claims it verified
  both by reading the tree. I did not re-clone. These two facts gate the late-interaction fallback and
  the MiniCheck serving decision, so they are worth a fresh check before Wave 4.
- **Whether the 1,583/1,772/1,636 ms figures are per-pair or per-batch.** No raw artifact exists. See
  2.3.
- **MiniCheck's true false-negative rate.** 24 pairs, wide interval, report declines to stand behind it.
- **CPU ingestion throughput for Docling.** Arithmetic only, in every source.
- **Whether a small local model generates good chunk context on our corpus.** No published comparison
  exists. The architecture is right that we would be first.
- **The BEIR figures for bge-m3 vs multilingual-e5-large** come from one 2026 paper with four subsets
  and two languages. `02-embedding-model.md:540` labels it "one academic source, external validity
  limited". I did not obtain the paper itself.

---

## 8. Corrections to make before the build session reads this

1. **Delete the v1.11.3 correction.** The pin is v1.19.2. Keep the two good reasons for fusing in
   application code (deterministic tie-break, metadata boost) and drop the false one.
2. **Restate the reranker's cost as unmeasured,** with the BEIR paper figure and the arithmetic range
   labelled as such, and note that our own 50-candidate measurement was taken under a load average of
   28 and must not be quoted.
3. **State why verifier latency is acceptable while reranker latency is not:** the reranker is on the
   critical path before the answer exists, the verifier overlaps with streaming. Then re-run the
   batch-size measurement and fix the per-pair versus per-batch reading.
4. **Downgrade the 40 percent MiniCheck figure** to a direction with n=24, or run the 150 pairs.
5. **Lead the chunking stage with the citation requirement,** not with 0.648.
6. **Reconcile the metadata rule with the contextualisation stage.**
7. **Exclude must-refuse questions from conformal calibration** and state the participation interval.
8. **Make the two builds' parity boundary explicit.**
9. **Add the safetensors conversion of MiniCheck to the trust story, not just the build order.**

If only one thing from this review changes the outcome, make it the parser throughput measurement.
It is the only unknown here that is both on the critical path for every client and cheap to close.
