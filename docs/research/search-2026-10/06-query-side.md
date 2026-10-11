# The query side: what happens before retrieval

Research for the Office Inference retrieval architecture decision. Written 2026-10-11.

**Scope.** Everything between the user pressing enter and the retriever being called: query
transformation, expansion, decomposition, metadata extraction, and routing. Also iterative and
agentic retrieval, because the query side and the loop are the same decision (both spend a model
call to change what gets retrieved).

**Out of scope but load-bearing.** The index side. Anthropic's own engineering post says they
tried hypothetical document embedding and summary-based indexing and saw limited or low gains,
while adding context to each chunk cut retrieval failures by 35 to 67 percent
([source](https://www.anthropic.com/engineering/contextual-retrieval)). That is the single most
important finding in this whole brief and it argues for spending effort at index time, not query
time. I state it here so the query-side recommendations are read in the right frame.

---

## 0. How I label evidence

Three labels, used consistently:

- **[measured]** A number from a paper, benchmark, or production report I read, with the URL.
- **[vendor]** A claim from a company selling or promoting the thing.
- **[inference]** My own reasoning from the constraints and the measured evidence. Not a
  measurement.

Where I could not verify something, I say so. I have not invented a number anywhere in this
document. I have also not run any benchmark myself.

---

## 1. The constraint that decides most of this

The office build serves open-weight models on a CPU box. Our own production-readiness document
puts the office reply path at "single-digit tokens per second" on CPU, and says a thirty-token
spoken reply takes four to six seconds
([`docs/production-readiness.md`](/home/anthony/projects/office-inference-core/docs/production-readiness.md#L92),
marked there as "typical figures rather than measured ones").

Every query-side technique in this brief works by generating text before the search runs. That is
the shape of the cost: a rewrite, an expansion, a hypothetical document, or a sub-question is
**decode-bound**. Prompt processing of a short instruction is cheap. Emitting 150 tokens is not.

**[inference]** At 5 to 10 output tokens per second, one 150-token auxiliary generation costs
15 to 30 seconds of added wall clock before retrieval even begins. A three-query expansion costs
three times that unless the variants are generated in one call. A five-step agentic loop costs
five times that. None of these are "a bit more latency." They are the difference between a system
that answers and a system that appears hung.

This one fact rules out more of the candidate list than any quality argument does. It is the reason
my recommendation below is mostly subtractive.

The cloud build does not have this problem: Bedrock answers a rewrite in a few hundred
milliseconds and the cost is rounding error against the token bill. **The two builds should not
share one query pipeline.** They must share the interfaces and the trace format so the parity suite
can compare them, but the office build should run a strictly smaller set of query-side steps.

---

## 2. Can we even measure the difference?

Before evaluating anything, the evaluation has to be able to detect the thing. It mostly cannot.

Sixty golden questions, threshold 85 percent answer support. **[inference]** Standard binomial
arithmetic on 60 items at p = 0.85:

| Quantity | Value |
|---|---|
| Standard error | 4.61 percentage points |
| 95 percent confidence interval half-width | about 9 points |
| Approximate minimum detectable difference, 80 percent power, unpaired | about 13 points |
| Approximate minimum detectable difference, paired on the same 60 questions | about 10 to 14 points, depending on how many questions flip |

So a 60-question suite can tell you a technique is **broken** or **decisive**. It cannot tell you a
technique is worth 4 points. Anything in the single digits, measured on 60 questions, is noise
wearing a number.

That matters because most of the measured gains in the literature are single-digit to
low-double-digit, on corpora and models that are not ours. A technique with a reported 5-point
average gain is, for us, unmeasurable and therefore unfalsifiable. Shipping an unmeasurable
technique is shipping a belief.

The evaluation-framework literature agrees with the sizing. A January 2026 paper on evaluation-driven
iteration recommends golden sets of "50-200 cases" that are "small enough to run on every change"
but "large enough to detect regressions," and stresses that **failure analysis matters more than
aggregate metrics**
([arXiv:2601.22025](https://ar5iv.labs.arxiv.org/html/2601.22025)). Their own RAG demonstration
suite is 15 cases. So 60 questions is a defensible size for regression detection. It is the wrong
instrument for ranking query-side techniques against each other.

### 2.1 What I would actually do about it

Two changes to how the 60 questions get used, both cheap. **[inference]**, but grounded in the
two methods above.

1. **Make the suite paired and per-question, not aggregate.** Run both configurations over the same
   60 questions and report which questions changed. A 62 percent to 70 percent move is unreadable.
   "these seven questions now retrieve the right page, these two stopped" is readable at n = 60. The
   configurations-sensitivity work argues the same thing: record the original query, the rewritten
   query, the pre-rerank candidates, the post-rerank candidates, and the selected context, and
   "if a trace lacks one of these stages, the evaluator should abstain from the corresponding
   diagnosis rather than inventing a cause"
   ([rag-observatory config sensitivity](https://github.com/GioiaZheng/rag-observatory/blob/67baad8af984530dccbd529f72fa0d41f5332ccb/docs/config_sensitivity.md)).
   That is a design instruction for the audit sink: the retrieval trace must carry the original
   query and the transformed query side by side, or the whole class of query-side regressions is
   undiagnosable.
2. **Do not add a query-side step to fix a target the 60 questions cannot resolve.** Instead, write
   the hard questions deliberately. The brief already says we write the questions with the client.
   Biasing them toward multi-hop, acronym-heavy, and date-constrained questions gives those
   categories their own small strata, where a 3-of-10 flip is visible even though 3-of-60 is not.

---

## 3. Technique by technique

### 3.1 HyDE (hypothetical document embeddings)

**What it is.** Generate a fake answer document to the query, embed that instead of the query, and
search with the resulting vector. The original paper's argument is that the encoder's dense
bottleneck filters out the invented details, and the vector lands in the neighbourhood of real
relevant documents ([arXiv:2212.10496](https://arxiv.org/abs/2212.10496)).

**What it actually improves.** In the original setting, a lot. Paired against Contriever, an
*unsupervised* dense retriever, HyDE took TREC DL19 nDCG@10 from 44.5 to 61.3 and DL20 from 42.1
to 57.9, and beat BM25 on both. On six low-resource BEIR sets it improved nDCG@10 on every one and
lost to BM25 on only TREC-COVID, by 0.2 points
([measured, arXiv:2212.10496](https://arxiv.org/html/2212.10496v1)).

**Why that result does not transfer to us.** Read what HyDE was fixing. Contriever was an
*unsupervised* contrastive encoder. The paper says so explicitly, and frames HyDE as solving the
problem that "it remains difficult to create effective fully zero-shot dense retrieval systems
when no relevance label is available." HyDE's whole mechanism is to substitute document-to-document
similarity for query-to-document relevance, because the retriever had no relevance signal to model.

Our retriever is `bge-m3`: a supervised, instruction-free, multilingual model trained on
relevance data, 32.8 million downloads, top of a multilingual embedding benchmark
([BAAI/bge-m3 model card](https://huggingface.co/BAAI/bge-m3), licence MIT, verified from the model
card's `license: mit` field and the repository tags). It already has the query-document relevance
signal that Contriever lacked. HyDE's premise does not hold for it.

There is a direct measured statement of this across eleven expansion techniques, twelve datasets,
and twenty-four retrieval models: "there exists a strong negative correlation between retriever
performance and gains from expansion: expansion improves scores for weaker models, but generally
harms stronger models." The mechanism they hypothesise is that expansion adds information that
helps recall but adds noise that muddies the top of the ranking
([measured, Weller et al., Findings of EACL 2024](https://aclanthology.org/2024.findings-eacl.134/)).

**The two most relevant measurements.**

On a financial text-and-table benchmark, 23,088 queries over 7,318 documents, HyDE *underperformed
vanilla dense retrieval on every metric*: Recall@5 0.544 against 0.587, nDCG@10 0.433 against
0.466, MRR@3 0.318 against 0.351. The authors attribute it to fabricated figures pulling the query
embedding away from the true context, and recommend against HyDE "for domains where factual
precision dominates over semantic similarity"
([measured, arXiv:2604.01733](https://ar5iv.labs.arxiv.org/html/2604.01733)). Read the limitation
section too: whole-document retrieval, one embedding model, all-numerical answers. It is a
special case, not a verdict.

The production case study is stronger evidence for us. A running RAG system over the Danish
National Encyclopedia, about 240,000 curated articles, 20,000 evaluated query-workflow pairs on
real traffic. Always-HyDE did raise coverage from Hybrid's 72.2 percent to 86.4 percent. But:

- Latency 96.2 seconds against 37 seconds, end-to-end including generation. That is 2.6 times.
- On the 722 real queries where Hybrid already returned sources, HyDE scored the same in 59.6
  percent, **lost in 21.2 percent with a mean loss of 2.010 composite points**, and won in only
  19.3 percent with a mean gain of 1.324. The losses are bigger than the wins.
- The authors name the cause: when a direct lexical match already found the target, "the language
  model's hypothetical document introduces semantic drift, steering retrieval toward adjacent, less
  relevant passages rather than the precise target source."

([measured, arXiv:2605.27220](https://ar5iv.labs.arxiv.org/html/2605.27220))

**Verdict for Office Inference: rejected as a default step.** The quality case is contested at
best and negative for precise, entity-heavy, identifier-heavy questions, which is most of what a
contract, a policy, an invoice, or a spreadsheet contains. The latency case is fatal on the office
build. The one place it earns consideration is the cloud build's tail (see section 5).

**What we would lose by rejecting it.** Queries phrased nothing like any passage in the corpus.
"Who do I chase about the thing from last spring" style questions. If the client's questions turn
out to look like that, revisit. The 60 questions will show it as a category, not an average.

---

### 3.2 Multi-query expansion (RAG-Fusion and relatives)

**What it is.** Ask the model for several paraphrases of the query, retrieve for each, fuse the
ranked lists with reciprocal rank fusion.

**Measured gains.** Thin, and negative in the two settings closest to ours.

- Financial text-and-table benchmark: multi-query plus RRF scored Recall@5 0.640 against BM25's
  0.644 and hybrid's 0.695. "Negligible improvement." The authors' reading: the queries were
  already specific and well-formed, so alternative phrasings added nothing
  ([measured, arXiv:2604.01733](https://ar5iv.labs.arxiv.org/html/2604.01733)).
- SemEval-2026 Task 8, multi-turn conversational retrieval, open-weight models, 777 queries,
  four English domains. Multi-query expansion scored nDCG@5 0.350 against a no-rewriting baseline
  of 0.371 and a simple rewrite at 0.422. Not "no gain": **it did worse than doing nothing**. The
  authors' line: "the additional queries diluted the signal from the primary rewrite with noisy
  alternatives" ([measured, arXiv:2605.12028](https://ar5iv.labs.arxiv.org/html/2605.12028)).

**[inference]** Multi-query takes the HyDE cost problem (extra generation) without HyDE's
mechanism (a document-shaped query vector). On our box it is the worst of both: three auxiliary
generations, and the measured effect is neutral-to-negative when the retriever is already good and
the query is already well-formed. Small-business questions are short and often already
keyword-like. "What's our mileage rate." "When does the lease renew."

**Verdict: rejected.**

---

### 3.3 Query rewriting for conversational follow-ups

**What it is.** Rewrite the current turn into a standalone query using conversation history, so a
pronoun or an ellipsis resolves before retrieval sees it.

**This is the one technique on the list with a clear, replicated, measured win, and it is the
only one I would ship unconditionally.**

Evidence:

- SemEval-2026 Task 8, the system I trust most because it is recent, open-weight, and
  CPU-feasible. A Qwen2.5-7B-Instruct rewriter, LoRA fine-tuned on 699 gold rewrite examples,
  moved nDCG@5 from **0.371 to 0.422, a 13.7 percent relative gain**, and the authors call
  rewriting "the single most impactful intervention for multi-turn retrieval." The official test
  score was nDCG@5 0.531, 8th of 38 systems, 10.7 percent above the organiser baseline
  ([measured, arXiv:2605.12028](https://ar5iv.labs.arxiv.org/html/2605.12028)).
- The same paper found that **first-turn queries do not need rewriting.** Skipping the rewriter on
  turns with no history produced identical nDCG@5 of 0.381 while cutting inference time 29 percent
  (32 seconds to 45 seconds per domain across their run). Free win: gate the rewriter on
  "is there history," not on "is this a query."
- NVIDIA's production RAG blueprint ships both a rewritten path and a cheap concatenation path,
  defaults both to off, and documents the tradeoff plainly: rewriting "provides best retrieval
  accuracy" and "adds latency due to additional LLM call," while concatenating previous user
  queries is "lower latency (no additional LLM call)" and "may be less accurate for complex
  conversational references" ([vendor, but a shipped default rather than a marketing claim,
  NVIDIA RAG Blueprint docs](https://github.com/NVIDIA-AI-Blueprints/rag/blob/main/docs/multiturn.md)).

**Two things the source paper got right that I would copy.**

*Deterministic decoding.* They swept temperature 0.0 to 1.0 end-to-end. Overall best was 0.2
(nDCG@5 0.422). Technical documentation peaked at **0.0** (0.473), because "any generation
randomness risks substituting incorrect technical terms, corrupting the lexical signal that BM25
relies on." Financial forum queries peaked at 0.3 (0.346). At temperature 1.0 the financial domain
collapsed to 0.233, **worse than not rewriting at all** (0.275). For contracts, policies, invoice
numbers, and procedure codes, temperature 0.0 is the right default and there is a measured
argument for it, not just a hunch.

*A rewrite prompt that forbids invention.* Their prompt includes "Do not add information not
present in the conversation" and "If the question is already standalone, return it unchanged"
([Appendix A of the same paper](https://ar5iv.labs.arxiv.org/html/2605.12028)). That second rule is
the one that keeps a rewrite from drifting on turns that were already fine.

**Failure modes.**

- *Over-rewriting.* A already-standalone turn gets mangled into something narrower. The
  "return it unchanged" rule and a validation gate (below) are the defences.
- *Wrong referent resolution.* "What about the other one?" with three candidate antecedents. There
  is no measured fix for this. It is a real error mode. **[inference]** The mitigation is that the
  answer is still cited to a passage, so the user sees it answered about the wrong document, which
  is at least legible. An ambiguous follow-up should arguably become a clarifying question.
- *History length.* The SemEval paper notes 10-plus-turn histories exceed a 2048-token rewriter
  window in production, and truncating early turns can drop the referent
  ([measured, same paper](https://ar5iv.labs.arxiv.org/html/2605.12028)). **[inference]** Cap the
  history fed to the rewriter and prefer the nearest turns, since referents are usually recent.

**Cost.** One auxiliary generation per conversational turn, skipped on the first turn. On our office
box that is the whole problem: a 40-token rewrite at single-digit tokens per second is still
several seconds. **[inference, and this is the mitigation I would push]** restrict rewriting to the
turns that need it, by a **rule, not a model**: rewrite only if the turn contains a pronoun,
a demonstrative, an elided verb phrase, or is under some token count with no content noun. That
gate costs nothing, is auditable, and cuts the auxiliary generation rate to the minority of turns
that are actually context-dependent. It is also honest, because the trace records why the rewriter ran.

**Verdict: ship it, gated by a rule, at temperature 0.0, with a validation check.**

---

### 3.4 Decomposition for multi-hop questions

**What it is.** Split a multi-part question into sub-questions, retrieve for each, merge, rerank,
answer.

**Measured gains. These are the largest in this brief, on the task they target.**

- ACL 2025 student research workshop, MultiHop-RAG and HotpotQA: decomposition plus reranking gave
  **MRR@10 +36.7 percent and F1 +11.6 percent** over a standard RAG baseline, with no
  task-specific training. The stated benefit is that decomposition "assembles complementary
  documents" while reranking removes the noise that merging introduces
  ([measured, ACL Anthology](https://aclanthology.org/2025.acl-srw.32/)).
- IRCoT, the established reference: interleaving retrieval with chain-of-thought steps improved
  retrieval by up to 21 points and downstream QA by up to 15 points across HotpotQA,
  WikiMultihopQA, MuSiQue, and IIRC, with gains holding "with much smaller models such as
  Flan-T5-large without additional training"
  ([measured, ACL 2023](https://aclanthology.org/2023.acl-long.557/)).

That last clause is the important one for us. Decomposition is not a frontier-model-only trick.

**What it costs, and what breaks.**

- One generation per decomposition, plus one retrieval per sub-question, plus a merge step. On CPU
  the generation dominates.
- The decomposition itself is a failure point. A diagnostic study of iterative RAG calls out
  query-quality failure classes by name: *vague*, *over-broad*, *fusion* (attempting several hops
  in one query), and *off-topic*, plus a "distractor latch" where the system fixates on a
  near-miss term
  ([measured, arXiv:2601.19827](https://arxiv.org/html/2601.19827v1)).
- **Composition failure.** The same study found "high composition failure rates even with perfect
  retrieval." The model has the right evidence and still produces the wrong answer. This is the
  reason decomposition cannot be justified on retrieval metrics alone. Our brief's hard-fail
  category is "a confident wrong answer," and composition failure is exactly that.

**Verdict: rejected as a default step, kept as a narrow escape hatch with a hard cap.** The
rationale is frequency, not quality. **[inference]** In a 5-to-30-person business document set,
genuine multi-hop questions are rare. "What's our mileage rate" and "when does the lease renew" are
single-lookup. "Which of our vendors raised prices more than inflation last year" is real but
uncommon. Shipping a two-generation decomposition path for every query to serve the uncommon case
is the wrong trade on a CPU box. Shipping it behind an explicit trigger is not.

**The trigger should not be a classifier.** See section 3.7 for the measured failure.

**A cheaper substitute worth testing first.** If the multi-hop category is failing, the first thing
to try is not decomposition but a deeper candidate pool and a stronger rerank before generation.
The financial benchmark found the reranker was worth more than any query-side technique: hybrid
plus cross-encoder rerank reached Recall@5 0.816 against hybrid alone at 0.695 and BM25 at 0.644,
and reranker depth mattered enormously. With only 20 candidates reranking was ineffective
(0.458), rising sharply at 50 (0.826) and 100 (0.888)
([measured, arXiv:2604.01733](https://ar5iv.labs.arxiv.org/html/2604.01733)). The reranker is a
cross-encoder, not a generative model, so it does not pay the decode tax. **[inference]** On the
office box, deepening the rerank pool is the cheapest way to buy multi-hop coverage, and it should
be exhausted before decomposition is considered.

---

### 3.5 Acronym and entity handling

**The problem is real.** SemEval-2026's error analysis names it directly: the financial domain was
consistently hardest "attributable to its informal forum language, domain-specific financial jargon
('401k rollover', 'FIRE movement'), and abbreviations that create vocabulary mismatch for both
lexical and semantic retrievers"
([measured, arXiv:2605.12028](https://ar5iv.labs.arxiv.org/html/2605.12028)). Separately, a paper
on educational retrieval is titled "Closing the acronym gap," which tells you the problem is
recognised enough to have a name
([title only. The ScienceDirect full text is paywalled and I could not read it](https://www.sciencedirect.com/science/article/pii/S002002552600112X)).

**Why the obvious fix is the wrong one.** Query-side expansion means asking a model what an acronym
stands for. In a client's corpus that is a guess. "EOB" is Explanation of Benefits in a medical
practice, and something else in a construction firm. A wrong expansion does not just fail to help.
it actively retrieves documents about the wrong concept, and it does so with the query's authority
behind it.

This is the same mechanism as the HyDE failure, one level down: the model invents plausible content
and the retriever faithfully finds documents matching the invention.

**The fix that fits our architecture [inference], grounded in the measured evidence above.**

Build the glossary from the corpus, at index time, not from the model, at query time.

1. Mine acronym-definition pairs from the ingested documents themselves: the parenthetical form
   ("Explanation of Benefits (EOB)"), the reverse form, and table headers. This is a pattern-matching
   job with no model call, so it costs nothing at query time and is auditable.
2. Store the glossary as a collection beside the main index, one entry per client, built from their
   documents. Terms the corpus never defines do not get an expansion.
3. At query time, expand an acronym **only** if it is in the client's glossary. Otherwise leave it
   alone and let hybrid retrieval's lexical half do its job.
4. Also index the acronym and its expansion as synonyms on the document side, so the expansion path
   exists whether or not the user typed the acronym.

Step 4 matters more than step 3 and it is easy to forget. The measured finding that index-side
enrichment beats query-side transformation (contextual retrieval, 35 to 67 percent failure
reduction) applies here in miniature.

**What this does not solve.** A term the client uses but their documents never define, and a
question that uses the expansion while the document uses the acronym, when the glossary wasn't
built. Both are visible in the golden questions.

**Verdict: ship the corpus-mined glossary, reject model-generated expansion.** The glossary is
deterministic, auditable, costs nothing per query, and cannot hallucinate. It is also the only
version that survives a compliance review, because you can show an examiner where every expansion
came from.

---

### 3.6 Date and metadata extraction into filters

**What it should improve.** Constraining retrieval to a date range, a document type, or a party.
"Clause 4 says the notice period is 30 days": retrieve only from the current contract, not the
superseded one. That is the same problem as our brief's "a superseded document not cited" exit
criterion.

**The failure mode is documented and it is ugly.** RagFlow issue #16832, titled "Elasticsearch
metadata date filtering: exact-match '=' returns 0 and LLM range operators are silently
dropped." Silent is the operative word: the extraction produced a filter, the filter was dropped,
the query ran unfiltered, and the system returned an answer from the wrong date range with no
error anywhere
([measured, a live bug report in a widely used open-source RAG project](https://github.com/infiniflow/ragflow/issues/16832)).

**[inference]** This is the worst failure shape in the whole brief. A dropped filter does not
degrade politely. It retrieves confidently from the superseded document and cites it, which is
indistinguishable to the user from the system working, right up until it matters. And it is
invisible in aggregate metrics because the answer is well-formed and the citation is real.

**How I would build it, and this is mostly de-risking rather than quality.**

Three rules, all of which I would treat as hard requirements rather than guidance:

1. **Never let a dropped filter degrade silently.** If the extractor produces a filter and the
   query planner cannot apply it, the correct behaviour is `insufficient_evidence`, not an
   unfiltered search. A refusal is a valid outcome. A silently-unfiltered answer is not.
2. **Extract from the query with a constrained decoder, not free generation.** A date is a small
   output space. Ask for a structured object with an enum for the field and a strict date format,
   validate it against the schema, and reject rather than guess. **[inference]** This is one of the
   few query-side jobs a small local model can do reliably, because the answer is 20 tokens, not
   200, and it escapes the decode tax that kills the rewriting techniques.
3. **Record the extracted filter in the audit row** alongside the query and the retrieved set, so
   that a dropped or wrong filter is diagnosable after the fact. The config-sensitivity work lists
   exactly this: a trace that lacks the stage should make the evaluator abstain, not invent a cause
   ([source](https://github.com/GioiaZheng/rag-observatory/blob/67baad8af984530dccbd529f72fa0d41f5332ccb/docs/config_sensitivity.md)).

**One more measured point in favour of filtering.** The Qdrant long-context review puts
"different access rules for different users" on the list of things that make retrieval the right
choice over long context, because retrieval "filters by tenant or permission before anything
reaches the model"
([vendor-authored but citing primary sources throughout](https://qdrant.tech/articles/rag-is-dead/index.md)).
Our ACL is already in the payload. Date and type filters are the same mechanism.

**Verdict: ship it, with the refusal rule and the trace requirement as hard constraints.** But
note what it is: this is not a retrieval-quality technique. It is a correctness technique. It
prevents a specific class of confidently-cited wrong answer, and that is a stronger reason to build
it than any average gain.

---

### 3.7 Query routing and classification — the result that should change the plan

**What it would do.** Look at the query, decide where to send it: which index, which lane, whether
to run the expensive path.

**The measured result is that this does not work, and the paper is unusually thorough about it.**

The Danish National Encyclopedia production study tested pre-retrieval routing across four machine
learning paradigms (rule-based heuristics, classification, neural encoder fine-tuning, and
regression) against an oracle that knew the best workflow per query
([measured, arXiv:2605.27220](https://ar5iv.labs.arxiv.org/html/2605.27220)):

| Router | Composite overall | vs always-HyDE | Oracle gap captured |
|---|---|---|---|
| Logistic regression, TF-IDF char 3-5 grams (best) | 4.269 | +0.011 | **2.4 percent** |
| Logistic regression, surface features | 4.260 | +0.002 | — |
| Logistic regression, word n-grams | 4.240 | −0.018 | — |
| Gradient boosting, TF-IDF + surface | 4.177 | −0.081 | — |
| Gradient boosting, multilingual embeddings | 4.184 | −0.074 | — |
| Logistic regression, multilingual embeddings | 3.850 | −0.408 | — |
| Always-HyDE (baseline) | 4.258 | — | — |
| Oracle ceiling | 4.733 | +0.475 | 100 percent |

Every model that used richer features did **worse**. The best model recovered 2.4 percent of the
available improvement. On 5,000 unseen real queries it routed over 90 percent to HyDE, capturing
under 5 percent of the augmentation-free opportunity. Even restricted to one-to-three-word queries,
where keyword matching is strongest, most were still routed to the expensive path, "confirming that
query length is not a reliable routing signal."

The authors' diagnosis is the part worth internalising: **"the need for LLM augmentation is
determined by what the retrieval index contains, not by what the query says."** Whether a query
needs help depends on whether the index happens to hold a directly relevant passage, and that is
unknowable before you search.

**And the thing that does work is a cascade, not a classifier.** Run the cheapest retrieval first,
escalate only when it returns nothing. Binary check, no model call, O(1) on the return value. On
real queries the cascade beat always-HyDE by +0.140 composite points, cut end-to-end latency 31.8
percent (96.2s to 65.6s), and served 72.2 percent of queries with no LLM augmentation at all. It
captured 30 percent of the oracle gap against the best router's 2.4 percent.

Two details in that result that I would copy directly:

- **The stopping condition was empirically optimal as a binary.** Escalating when fewer than two or
  three documents came back, rather than when none did, *reduced* quality from 4.03 to 3.98. Their
  reading: when one well-matched document is already in hand, a hypothetical document adds noise
  rather than coverage. **[inference]** This is the same drift mechanism as HyDE's 21.2 percent loss
  rate, showing up again under a different name. It is a consistent story across two independent
  studies.
- **The cascade dominates the Pareto frontier.** It was the only configuration combining strictly
  higher quality with strictly lower cost. Its quality win over always-HyDE came from two sources:
  avoiding HyDE's degradation on queries that keyword matching handles cleanly, and reserving
  augmentation for the queries that need it.

**Verdict on routing: rejected.** Do not build a query classifier. Do not build a lane chooser that
reads the query. The evidence says the signal is not in the query, and the failure mode is a
confident wrong routing decision that nothing downstream can detect.

**Verdict on cascades: this is the shape to build.** Escalate on a *result*, not on a *prediction*.
It also happens to be the only escalation policy that is cheap on a CPU box, because it is an array
length check.

**One caveat, stated plainly.** The cascade result is from one production system with a
240,000-article entity-heavy reference corpus, and its cascade escalated to HyDE and query
expansion, two techniques I am recommending against. The *finding* is "escalate on retrieval
outcome, not on query features." What we escalate *to* is our choice. **[inference]** For us that
should be: deepen the rerank pool, then add the lexical half if it was off, then (only on the
cloud build) an LLM augmentation step.

---

## 4. Iterative and agentic retrieval

### 4.1 The measured benefit

The strongest positive result I found. A controlled diagnostic study on scientific multi-hop
question answering, eleven state-of-the-art models, three regimes: no context, gold context (all
oracle evidence at once), and iterative RAG. The iterative controller alternates retrieval,
hypothesis refinement, and evidence-aware stopping, under a **hard budget of five retrieval steps**,
top-10 passages per step, with a curated context view of about 18 passages
([measured, arXiv:2601.19827](https://arxiv.org/html/2601.19827v1)).

| Model | No context | Gold context | Iterative RAG |
|---|---|---|---|
| GPT-4o | 32.29 | 56.32 | **81.96** |
| GPT-5 | 45.11 | 71.68 | **80.86** |

Iterative RAG beat gold context by up to 25.6 percentage points, with the largest gains "for
non-reasoning fine-tuned models." The headline claim: "the *process* of staged retrieval is often
more influential than the mere presence of ideal evidence."

**Why this does not license a ReAct loop for us.** Three reasons, all from the same paper or
adjacent ones.

*The token cost is enormous.* GPT-5's average output tokens: 713 with gold context, 5,592 with
iterative RAG. Nearly eight times the output. On our CPU box, output tokens are the scarce
resource. **[inference]** An eightfold increase in generated tokens is not a latency increase we
can absorb. It is a different product.

*The failure modes are the ones we cannot ship.* The paper names them: incomplete hop coverage,
distractor latch trajectories, early-stopping miscalibration, and "high composition failure rates
even with perfect retrieval." It also introduces the *anchor carry-drop* diagnostic. The model
loses the salient entity between hops and the chain quietly derails. And *over-confident finalize*:
stopping before covering 80 percent of hops while believing it has enough. **[inference]** Every one
of those produces a fluent, cited, wrong answer. The brief's hard-fail category.

*The domain is not our domain.* Chemistry nomenclature, 1–4-hop chains over ChemRxiv, PubChem, and
Wikipedia, with a chemistry-specific embedding model. The mechanism findings should transfer. The
effect sizes should not be assumed to.

### 4.2 What the cost actually looks like when someone meters it

A measured cost-per-correct-answer benchmark over 200 MuSiQue queries, four architectures, one
corpus, one model, metering every LLM call. Self-published, MIT licensed, costs estimated rather
than billed because the run executed on a free model. I am labelling it accordingly, and the
author is candid about the limits
([measured but self-reported, github.com/intikhab49/rag-cost-curve](https://github.com/intikhab49/rag-cost-curve)).

| System | EM | USD/query | **USD/correct** | LLM calls | p50 latency |
|---|---|---|---|---|---|
| naive | 0.275 | 0.0043 | **0.0155** | 1.0 | 2,035 ms |
| reranked | 0.290 | 0.0045 | **0.0156** | 1.0 | 7,062 ms |
| iterative | 0.505 | 0.0138 | **0.0273** | 2.9 | 6,962 ms |
| arag (ReAct) | 0.600 | 0.0233 | **0.0389** | 5.0 | 8,704 ms |

Three findings I would carry into the decision:

- **Agentic RAG costs 2.5 times more per correct answer** for +32.5 EM. Both halves are real.
- **The knee is at "retrieve more than once," not at "let the model steer."** A fixed
  code-controlled retrieve-read-retrieve loop reached **84 percent of the agent's gain for 58
  percent of the calls.** The agent still won 99.7 percent of paired resamples, so it is
  diminishing returns rather than ceremony, but if you need accuracy and not the bill, the fixed
  loop is where to look first.
- **Reranking did not help here**: +1.5 EM, winning 73.7 percent of paired resamples, which the
  author correctly calls noise at n = 200. This contradicts the financial-benchmark reranker result
  and the Anthropic contextual-retrieval result. **[inference]** I read the disagreement as
  domain-dependent rather than as one side being wrong, and it is the reason I would measure the
  reranker on our own 60 questions rather than trusting either paper.

And the accounting column that matters for us: the agent resends its context. Unique retrieved
tokens 1,561, billed 2,727. That is **1.75 times over.** Single-shot systems have no gap by construction.

### 4.3 Can it be bounded? Yes, and it must be, structurally

The good news is that everyone who has built this seriously bounds it, and the bounds are cheap.
**[inference]** from the designs above, stated as a specification:

1. **A hard step cap in code, not in the prompt.** The chemistry study used five. The cost benchmark
   used three for the fixed loop. **[inference]** I would use two, and make the second conditional.
2. **A hard token cap per query.** LiteLLM already gives us budgets. Apply one to the query pipeline
   itself, not just the roles. A runaway loop is the alarm the build brief already names.
3. **A per-step context cap, with a curated view rather than full history.** The chemistry study
   passed the current step's passages plus the best two from each previous step, about 18 passages.
   This is the design detail that keeps context from diluting across steps.
4. **Never let the model choose a strategy.** The cost benchmark's most useful result: the fixed
   loop got 84 percent of the benefit for 58 percent of the calls. **[inference]** Let the model
   write the next query, if anything. Do not let it choose whether to retrieve, which index to use,
   or when to stop. That is where the cost and the unpredictability live.
5. **Escalate on retrieval outcome, never on model confidence.** The cascade finding. The chemistry
   study's own "over-confident finalize" and "miscalibrated stopping" failure modes are the
   argument: a model's self-assessment of sufficiency is measurably wrong in both directions.

### 4.4 What I would actually ship

**[inference]** A two-step cascade, triggered by an empty or short result set, with no model in the
decision path:

```
query
  -> (gate) rewrite?        rule-based: pronoun/ellipsis/very short turn
  -> hybrid retrieve        BM25 + dense, RRF
  -> if result set empty:
       deepen rerank pool (free, cross-encoder, no decode)
     if still empty AND cloud build:
       LLM augmentation once (HyDE or expansion, capped)
     if still empty:
       refuse with `no_source`            <- correct outcome, not a failure
  -> rerank
  -> generate with citations
```

That is one optional auxiliary generation on the office build (the rewrite, on conversational turns
only), zero on the first turn of a conversation, and one more on the cloud build's tail. It is
bounded by construction: there is no loop to run away.

---

## 5. Citations when the query is rewritten

This is the question the brief asks directly and it deserves a direct answer: **an answer must be
traceable to the passage the user's actual question asked about, not to the passage the rewritten
query matched.**

**[inference]** The design that achieves it has five parts, and the first is the one that gets
skipped.

1. **The user's question is stored verbatim and never replaced.** Rewriting produces a *retrieval
   key*, not a new question. Both are written to the audit row on every turn. This is also exactly
   what the config-sensitivity trace specification requires: "original query and rewritten query,
   if rewriting is enabled," listed as a required trace field
   ([source](https://github.com/GioiaZheng/rag-observatory/blob/67baad8af984530dccbd529f72fa0d41f5332ccb/docs/config_sensitivity.md)).
2. **The verifier checks entailment against the original question, not the rewrite.** Our
   three-check verifier already checks that "the sentence is entailed by the passage." **[inference]**
   The entailment question must be "does this passage support an answer to *what the user asked*."
   If it is phrased against the rewrite, a drifting rewrite launders itself through verification and
   comes out clean. That would defeat the whole verification layer on the query side.
3. **Both queries go in the trace, and the answer reports which one produced the citation.** If a
   follow-up was rewritten and the answer came from the rewrite's retrieval, that is visible. Not
   necessarily wrong, but visible.
4. **Rewrite validation before use.** After rewriting, check that the rewrite shares at least one
   content entity with the original turn or the conversation history. If it shares none, discard the
   rewrite and retrieve on the raw question. **[inference]** This is a cheap string-level guard
   against the drift the production study measured (21.2 percent of queries degraded, mean loss
   2.010 points). It will not catch every drift, but it catches the gross ones for free.
5. **Keep the rewrite inside the citation boundary.** **[inference]** A rewrite may resolve a
   pronoun using conversation history. It must not introduce facts. A rewrite that adds a document
   number, a date, or a party name that the user did not say and the history does not contain is a
   rewrite that will retrieve a specific real document in answer to a question the user did not ask.
   The SemEval prompt's rule, "do not add information not present in the conversation," is the
   right rule, and it should be enforced by the validator, not only requested in the prompt.

**One structural guarantee worth stating.** Every retrieved chunk carries document id, page, access
label, and content hash. A rewrite changes the query vector. It cannot change what a chunk says or
where it came from. So the citation is always to a real page of a real document at a real location.
The drift risk is *relevance*, not *provenance*. That distinction is worth holding on to: it means
query rewriting cannot produce a fabricated citation, only an irrelevant one, which the refusal
path and the entailment check are positioned to catch.

---

## 6. Recommendation

Ordered by how much I would stake on each. Everything here is **[inference]** applied to the
**[measured]** evidence above.

### Ship

**1. Rule-gated query rewriting for conversational follow-ups, temperature 0.0.** The only
technique with a clear replicated win (+13.7 percent relative nDCG@5 on SemEval-2026 Task 8, 8th of
38 systems with open-weight models). Gate it on a pronoun/ellipsis rule so it fires on the minority
of turns that need it. Skip the first turn: measured identical quality, 29 percent less inference.
Validate the rewrite before use.

**2. Corpus-mined acronym glossary, used deterministically.** Built at index time from the client's
documents, applied at query time only for terms it contains. Index the expansions as synonyms too.
Zero query-time cost, fully auditable, cannot hallucinate. This is the acronym answer.

**3. Date and metadata extraction into filters, as a correctness control.** Constrained structured
extraction, schema-validated, and a **hard rule that a dropped filter refuses rather than searches
unfiltered.** The RagFlow silent-drop bug is the failure this prevents. Cheap on CPU because the
output is about 20 tokens.

**4. A post-retrieval cascade for escalation, with no query classifier anywhere.** Escalate on an
empty result set. Binary check, no model call. This is the measured alternative to routing, and it
captured 30 percent of the oracle gap against the best router's 2.4 percent while cutting latency
31.8 percent.

**5. Original query and rewrite both in the audit trace, and entailment checked against the
original.** The trace field is required by the config-sensitivity specification. The entailment
change is what stops a drifting rewrite from laundering itself through verification.

### Do not ship

**HyDE as a default step.** Contested at best. Negative on precise numerical and entity-heavy
content. Its original advantage came from an unsupervised retriever we do not use. 2.6 times the
latency in a production study. Losses exceeded wins by 2.010 to 1.324 composite points on queries
where keyword matching already worked. Revisit only if the 60 questions show a genuine
"phrased-nothing-like-the-corpus" category, and only on the cloud build.

**Multi-query expansion.** Measured neutral against BM25 and measured *worse than no rewriting at
all* on SemEval-2026 (0.350 against 0.371). Three generations for a negative.

**Query routing or classification.** Four ML paradigms, richest features performed worst, best model
recovered 2.4 percent of the available gain, and the root cause is structural: the signal is in the
index, not the query. On a CPU box this spends a generation to make a decision that is measurably
worse than not deciding.

**Decomposition as a default step.** The gains are the largest in the brief (+36.7 percent MRR@10,
+11.6 percent F1 on multi-hop) and the frequency does not justify the cost, on a CPU box, for a
small business corpus. Keep it as an explicitly triggered, hard-capped path, and exhaust the
rerank pool first, because that is cheaper and measured to matter more.

**Any unbounded agentic loop.** Five calls per query, 1.75 times context resent, eightfold output
tokens, and failure modes that produce confident cited wrong answers. A fixed two-step cascade gets
most of the benefit at a fraction of the cost, and it cannot run away.

### Measure before deciding

**Reranker depth.** Three sources disagree: it was "the single most impactful component" on
financial text-and-table (+12.1 points Recall@5 over unreranked hybrid), it was noise at n = 200 on
MuSiQue (+1.5 EM), and Anthropic measured 67 percent failure reduction when combined with
contextual retrieval. **[inference]** The disagreement is domain-dependent and the technique is
cheap on CPU (no decode), so it should be tuned on our own 60 questions rather than argued about.
Test pool depth specifically. 20 candidates was measured as ineffective where 50 was not.

**Index-side contextual enrichment.** Not this brief's angle, but the strongest evidence in
everything I read points here, and it is a one-time cost rather than a per-query cost. If the
query-side techniques are all marginal at our scale, the budget should move to this.

---

## Appendix A — Specification I would implement

**[inference]** throughout. This is a design, not a measurement.

```
on each turn:

  # 1. classify the turn -- rules only, no model
  needs_rewrite = (history_len > 0) and
                  (has_pronoun or has_demonstrative or is_elliptical or token_len < 6)

  # 2. retrieval key
  retrieval_key = user_question                       # default: verbatim
  if needs_rewrite:
      candidate = rewrite(user_question, last_k_turns, temperature=0.0,
                          prompt="resolve references only. Do not add information.
                                  if already standalone return unchanged")
      if shares_content_entity(candidate, user_question + history)
         and len(candidate) <= 3 * len(user_question):
          retrieval_key = candidate
      else:
          record_rejected_rewrite(candidate, reason)  # audited, not silent

  # 3. deterministic expansion -- glossary only, no generation
  retrieval_key = expand_from_glossary(retrieval_key, client_glossary)

  # 4. structured filter extraction -- constrained, schema-validated
  filt = extract_filter(user_question)                # ~20 output tokens
  if filt is not None and not filter_applies(filt):
      return refuse("insufficient_evidence")          # never search unfiltered

  # 5. cascade -- escalate on RESULT, never on a prediction
  hits = hybrid_search(retrieval_key, filt, k=50)
  if empty(hits):
      hits = hybrid_search(retrieval_key, filt, k=150)     # deepen, no decode
  if empty(hits) and build == "cloud":
      hits = hybrid_search(llm_augment(retrieval_key), filt, k=50)   # once, capped
  if empty(hits):
      return refuse("no_source")                      # a correct outcome

  # 6. rerank, generate, verify
  hits = rerank(retrieval_key, hits)                  # cross-encoder, no decode
  answer = generate(user_question, hits, citations=True)
  verify(answer,
         cited_in_retrieved_set=True,
         page_matches_passage=True,
         entailed_by_passage=True,
         entailment_question=user_question)           # the ORIGINAL, not the key

  # 7. trace -- all of it, or the regressions are undiagnosable
  audit({user_question, retrieval_key, rewrite_rejected_or_none,
         glossary_expansions, filt, retrieved_before_rerank,
         retrieved_after_rerank, selected_context, answer, verdict})
```

Every box that costs a generation is marked. There are three, and only the rewrite runs
unconditionally on conversational turns. Nothing loops.

---

## Appendix B — The 60-question protocol

**[inference]**, sized from the statistical argument in section 2 and the golden-set guidance in
[arXiv:2601.22025](https://ar5iv.labs.arxiv.org/html/2601.22025).

60 is fine for regression detection and too small for ranking techniques. Three adjustments make it
usable:

1. **Stratify, and give the hard categories their own slots.** Something like 25 single-lookup
   factual, 10 conversational follow-ups needing a rewrite, 10 identifier/acronym/code, 8
   date-or-version-constrained, 7 unanswerable-must-refuse. Strata of 8 to 10 make a 3-question
   change legible. An undifferentiated 60 does not.
2. **Report per-question deltas, not aggregates.** "Seven questions now retrieve the right page,
   two regressed" survives n = 60. "70 percent versus 62 percent" does not.
3. **Keep the hard-fail categories absolute and separate.** An access-control leak, an invented
   answer, a numeric mismatch, a successful injection. Each is one question, and one is a failure.
   These do not average.

And one honest limitation: with 60 questions, a technique worth 5 points is unmeasurable. If a
query-side technique cannot be justified by the *mechanism* evidence, as rewriting and the glossary
can be, then 60 questions will never vindicate it, and it should not be shipped on the expectation
that they will.

---

## Appendix C — Options I rejected, and why

| Option | Rejected because | Source |
|---|---|---|
| HyDE as default | Underperformed vanilla dense on all metrics on financial text-and-table; in production, losses exceeded gains (2.010 vs 1.324) on queries keyword matching already handled; 2.6x end-to-end latency | [arXiv:2604.01733](https://ar5iv.labs.arxiv.org/html/2604.01733), [arXiv:2605.27220](https://ar5iv.labs.arxiv.org/html/2605.27220) |
| Multi-query / RAG-Fusion | Recall@5 0.640 vs BM25 0.644 on financial benchmark; nDCG@5 0.350 vs 0.371 no-rewriting baseline on SemEval-2026 | [arXiv:2604.01733](https://ar5iv.labs.arxiv.org/html/2604.01733), [arXiv:2605.12028](https://ar5iv.labs.arxiv.org/html/2605.12028) |
| Domain-aware prompting for the rewriter | nDCG@5 0.350 — below the no-rewriting baseline of 0.371. Domain context made the model over-specialise and inject jargon absent from the question | [arXiv:2605.12028](https://ar5iv.labs.arxiv.org/html/2605.12028) |
| Pre-retrieval query routing (four ML paradigms) | Best model captured 2.4 percent of the oracle gap; richer features performed worse; routed 90 percent of unseen real queries to the expensive path. "The need for augmentation is determined by what the index contains, not what the query says" | [arXiv:2605.27220](https://ar5iv.labs.arxiv.org/html/2605.27220) |
| Model-generated acronym expansion | A wrong expansion retrieves the wrong concept with the query's authority behind it. Same drift mechanism as HyDE. Corpus-mined glossary is deterministic and auditable instead | [inference], from the HyDE drift evidence |
| Decomposition as default | Largest measured gains in the brief, but the frequency in a small-business corpus does not justify two generations per query on CPU; composition failure persists even with perfect retrieval | [ACL 2025](https://aclanthology.org/2025.acl-srw.32/), [arXiv:2601.19827](https://arxiv.org/html/2601.19827v1) |
| Unbounded ReAct / agentic retrieval | 5.0 calls per query, 1.75x context resent, ~8x output tokens; "high composition failure rates even with perfect retrieval"; over-confident finalize and distractor latch both produce confident cited wrong answers | [rag-cost-curve](https://github.com/intikhab49/rag-cost-curve), [arXiv:2601.19827](https://arxiv.org/html/2601.19827v1) |
| A query classifier for lane selection | Same evidence as pre-retrieval routing. A cascade escalating on retrieval outcome is measured strictly better and costs no generation | [arXiv:2605.27220](https://ar5iv.labs.arxiv.org/html/2605.27220) |
| Prefixing chunks with generic document summaries | Anthropic "experimented and saw very limited gains" | [Anthropic](https://www.anthropic.com/engineering/contextual-retrieval) |
| Summary-based indexing | Anthropic "evaluated and saw low performance" | [Anthropic](https://www.anthropic.com/engineering/contextual-retrieval) |
| Rewriting every turn | Measured identical nDCG@5 on first turns with no history, at 29 percent more inference time | [arXiv:2605.12028](https://ar5iv.labs.arxiv.org/html/2605.12028) |

---

## Appendix D — What I could not verify

Stated explicitly, per the brief.

1. **I did not measure anything myself.** Every number in this document is from a source I read
   and cited. Where I have reasoned past the sources, it is labelled **[inference]**.
2. **No CPU throughput figure for our actual hardware.** The "single-digit tokens per second" figure
   is from our own
   [`docs/production-readiness.md`](/home/anthony/projects/office-inference-core/docs/production-readiness.md#L92),
   which labels it a typical figure rather than a measured one. The 15-to-30-second auxiliary
   generation estimates in section 1 are my arithmetic on that unverified figure, not a benchmark.
   **This is the single most useful thing to measure before committing to any query-side technique.**
3. **The acronym-gap paper is paywalled.** I have the title, "Closing the acronym gap in educational
   retrieval with ontology-guided augmentation" (*Information Sciences*), and nothing from its body.
   I cited it as evidence the problem is recognised, not for any number.
4. **The cost benchmark's dollar figures are estimates, not billed.** The run used a free model and
   the author repriced the token buckets at published rates. The ratios between systems are sound
   because one tokenizer and one run produced them. The absolute dollars are illustrative. The
   author says so and I am repeating it.
5. **No measured latency for HyDE or rewriting on CPU, from anyone.** Every latency figure I found
   is from a GPU or API deployment. The office-build latency argument in this document is
   arithmetic on an unverified throughput number.
6. **The MuSiQue reranking null result contradicts two other sources.** +1.5 EM at n = 200 against
   "single most impactful component" on the financial benchmark and 67 percent failure reduction in
   Anthropic's study. I have not resolved this and I do not think it is resolvable from the papers.
   It needs a measurement on our corpus.
7. **Two sources in this brief are preprints**, [arXiv:2601.19827](https://arxiv.org/html/2601.19827v1)
   and [arXiv:2604.01733](https://ar5iv.labs.arxiv.org/html/2604.01733). Neither is peer-reviewed
   as far as I could confirm. Both are recent and both are unusually explicit about their
   limitations, which is why I used them, but they are not the same class of evidence as an ACL
   Anthology paper.

---

## Sources

- Gao, Ma, Lin, Callan. *Precise Zero-Shot Dense Retrieval without Relevance Labels* (HyDE).
  arXiv:2212.10496. https://arxiv.org/abs/2212.10496 (tables read at
  https://arxiv.org/html/2212.10496v1)
- Weller et al. *When do Generative Query and Document Expansions Fail?* Findings of EACL 2024.
  https://aclanthology.org/2024.findings-eacl.134/
- Trivedi, Balasubramanian, Khot, Sabharwal. *Interleaving Retrieval with Chain-of-Thought
  Reasoning* (IRCoT). ACL 2023. https://aclanthology.org/2023.acl-long.557/
- Ammann, Golde, Akbik. *Question Decomposition for Retrieval-Augmented Generation*. ACL 2025 SRW.
  https://aclanthology.org/2025.acl-srw.32/
- Caraman, Silaghi. *Three-Stage Multi-Turn Retrieval with Query Rewriting, Hybrid Search, and
  Cross-Encoder Reranking*. SemEval-2026 Task 8. arXiv:2605.12028.
  https://ar5iv.labs.arxiv.org/html/2605.12028
- Zhang et al. *uircis at SemEval-2026 Task 8: A Unified Lightweight Pipeline for Multi-Turn RAG
  Evaluation*. https://aclanthology.org/2026.semeval-1.394/
- Hussain, Nielbo. *The Coverage Illusion: From Pre-retrieval Routing Failure to Post-retrieval
  Cascades in a Production RAG System*. arXiv:2605.27220.
  https://ar5iv.labs.arxiv.org/html/2605.27220
- Astaraki et al. *When Iterative RAG Beats Ideal Evidence*. arXiv:2601.19827.
  https://arxiv.org/html/2601.19827v1
- Akarsu, Karaman, Mierbach. *From BM25 to Corrective RAG: Benchmarking Retrieval Strategies for
  Text-and-Table Documents*. arXiv:2604.01733. https://ar5iv.labs.arxiv.org/html/2604.01733
- *rag-cost-curve* — measured cost-per-correct-answer benchmark, MuSiQue.
  https://github.com/intikhab49/rag-cost-curve
- Anthropic. *Introducing Contextual Retrieval*. 2024-09-19.
  https://www.anthropic.com/engineering/contextual-retrieval
- Commey. *When "Better" Prompts Hurt: Evaluation-Driven Iteration for LLM Applications*.
  arXiv:2601.22025. https://ar5iv.labs.arxiv.org/html/2601.22025
- *rag-observatory* — configuration sensitivity and required trace fields.
  https://github.com/GioiaZheng/rag-observatory/blob/67baad8af984530dccbd529f72fa0d41f5332ccb/docs/config_sensitivity.md
- NVIDIA. *Multi-Turn Conversation Support for NVIDIA RAG Blueprint*.
  https://github.com/NVIDIA-AI-Blueprints/rag/blob/main/docs/multiturn.md
- *RagFlow issue #16832* — Elasticsearch metadata date filtering, exact-match returns 0 and LLM
  range operators silently dropped. https://github.com/infiniflow/ragflow/issues/16832
- Qdrant. *Is RAG Dead? Long Context Windows versus Retrieval* (secondary, used for the Anthropic
  200,000-token threshold and the Hamilton et al. pointer).
  https://qdrant.tech/articles/rag-is-dead/index.md
- Hamilton et al. *The Token Tax of Epistemic Accuracy*. arXiv:2606.20898.
  https://arxiv.org/abs/2606.20898
- BAAI/bge-m3 model card — licence MIT, verified from the card's `license: mit` field and the
  repository tags. https://huggingface.co/BAAI/bge-m3
- BAAI/bge-reranker-v2-m3 — licence Apache 2.0, verified from the repository's `cardData.license`.
  https://huggingface.co/BAAI/bge-reranker-v2-m3
- Qwen/Qwen3-14B — licence Apache 2.0, verified from the repository's `cardData.license` and the
  linked LICENSE file. https://huggingface.co/Qwen/Qwen3-14B

### Licence verification note

The brief asks for licence claims to be verified from the project's own files, and to say so when
they cannot be. I checked three model repositories directly through the Hugging Face API and model
cards on 2026-10-11:

- `BAAI/bge-m3` — **MIT**. Source: the model card metadata and repository tags both carry
  `license: mit`. https://huggingface.co/BAAI/bge-m3
- `BAAI/bge-reranker-v2-m3` — **Apache 2.0**. Source: repository `cardData.license` and tags.
  https://huggingface.co/BAAI/bge-reranker-v2-m3
- `Qwen/Qwen3-14B` — **Apache 2.0**, with `license_link` pointing at the repository's `LICENSE`
  file. https://huggingface.co/Qwen/Qwen3-14B

All three are shippable in a paid client deployment on the stated terms. I did not separately verify
the licences of any tool mentioned in passing (Docling, Qdrant, and the rest), because they are not
what this brief decides, and I would rather say that than imply a check I did not do.
