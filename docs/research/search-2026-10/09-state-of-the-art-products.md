# What the best search products actually do, and where the differentiation is

Angle 9 of the retrieval-architecture study. Written 2026-10-11.

Scope: what commercially successful document-QA and enterprise-search systems actually run, read from
their own source code, their licence files, and their public engineering material rather than their
marketing. Then the commercial question: for a self-hosted deployment serving one business of 5 to 30
people, which of these techniques are table stakes, which are real differentiators, and which are
theatre.

Evidence labels, consistent with the sibling reports in this series:

- **[measured]** — a number from a source I read, with the dataset or the file named.
- **[vendor]** — a number or claim from a product's own documentation or blog. Useful for what the
  software does; not neutral evidence about what works.
- **[code]** — read directly from a source file or configuration file. The file path is given.
- **[inference]** — my reasoning, not a source's claim.
- **[unverified]** — a figure I could not confirm from a source I actually read. Flagged rather than
  dropped, so nobody quotes it as fact.

---

## 1. Executive summary

**1. The best commercial systems are not running exotic retrieval. They are running lexical plus
dense, fused, with permissions and citations built into the plumbing.** I read the actual constants.
Onyx, the most serious open-source enterprise-search product, fuses exactly two arms with equal
weights after min-max normalisation, retrieves 500 candidates per arm, chunks at 512 tokens, and **has
removed reranking entirely** — there is a database migration named
`remove_reranking_from_search_settings.py` and the only reranker left in the tree is a fully commented-
out file. **[code]** Its retrieval is less elaborate than the design in our own build brief.

**2. Nobody I could read verifies citations at the sentence level. Not one product.** Glean surfaces
deep links and says plainly that "citation coverage can vary by surface, workflow, and whether the
model returns a grounded response". **[vendor]** Azure's semantic ranker extracts captions and answers
verbatim and is explicit that "there's no generative AI model in this workflow that creates or
composes new content" — which sidesteps the problem rather than solving it. **[vendor]** Bedrock's
contextual grounding check is the closest thing to a commercial support verifier, and it has three
limitations that matter: it is **not supported for conversational QA or chatbot use cases**, it marks
the whole response relevant if **any one chunk** is relevant, and on streaming APIs an ungrounded
answer is shown to the user before it is marked. **[vendor]**

**[inference]** That is the differentiation. The three-check citation verifier in the build brief is
not a nicety bolted onto a commodity pipeline. It is the part nobody else ships, and the part that
answers the only question that loses a client.

**3. Page-level citation is a parsing problem, not a retrieval problem, and every product treats it as
a plumbing detail.** I found no public engineering material from any commercial product describing how
page provenance survives chunking. **[inference]** This is because their corpora are wikis, tickets,
and chat messages, where "page" is meaningless. Our corpus is contracts, policies, invoices, and
scanned PDFs, where page is the citation. The design constraint does not transfer from their world to
ours, and their architectures give us no help with it.

**4. GraphRAG's headline numbers come from an LLM judge with no gold answers, and they invert under
ground-truth scoring.** GraphRAG-Bench states in its own abstract that "recent studies report that
GraphRAG frequently underperforms vanilla RAG on many real-world tasks". **[measured]** On that
benchmark's fact-retrieval task, "RAG (w rerank)" scored 60.92 against "GraphRAG (local)" at 49.29.
**[measured]** Index build cost across graph methods spans roughly 9.2 million tokens for HippoRAG2
against roughly 115.5 million for GraphRAG, about twelvefold. **[measured]** For a 50,000-document
single-client deployment, GraphRAG is disqualified on cost before it is disqualified on accuracy.

**5. The operational cost of reranking on CPU is the real constraint, and the market has quietly voted
against it.** Onyx removed it. **[code]** Azure caps semantic reranking at the top 50 results and
compresses each to a 2,048-token summary string precisely because "semantic ranking uses a lot of
resources and time". **[vendor]** Elastic ships RRF with `rank_constant` defaulting to 60 and
`rank_window_size` defaulting to the result size. **[vendor]** The pattern is consistent: the
industry's answer to ranking quality is a cheap deterministic fusion plus a bounded rerank window, not
an unbounded cross-encoder pass.

**6. There is a category of "advanced RAG" that exists only in framework marketing and notebook
tutorials.** LlamaIndex and LangChain both ship HyDE, multi-query, sub-question decomposition,
step-back prompting, sentence-window retrieval, and auto-merging retrieval as opt-in cookbook
patterns, not as defaults. **[inference, from the shape of their documentation]** I could not find a
single production system in this review running any of them. That is a strong signal about where the
value is.

---

## 2. Why the licence question comes first, and what I verified

The brief puts licence first because it binds hardest. I read the actual licence files rather than the
badges. Here is what those files say, quoted, with URLs.

| Project | Licence as written in the file | URL | Shippable in a paid client deployment? |
|---|---|---|---|
| **Onyx** (core) | "MIT Expat" for everything outside `ee/`; all content under `ee` directories is under the **Onyx Enterprise License**, which requires a paid subscription "for the correct number of user seats" for production use | [LICENSE](https://github.com/onyx-dot-app/onyx/blob/main/LICENSE), [backend/ee/LICENSE](https://github.com/onyx-dot-app/onyx/blob/main/backend/ee/LICENSE) | **Core yes, enterprise directories no.** See the warning below |
| **RAGFlow** | Apache License 2.0 | [LICENSE](https://github.com/infiniflow/ragflow/blob/main/LICENSE) | **Yes** |
| **Verba** | BSD-3-Clause, "Copyright (c) 2020-2023, Weaviate B.V. All rights reserved." — and the repository is **archived** (`archived: true`, last push 2026-06-08) | [LICENSE](https://github.com/weaviate/Verba/blob/main/LICENSE) | Yes, but **dead code**. Do not build on it |
| **AnythingLLM** | MIT, with no additional terms in the file | [LICENSE](https://github.com/Mintplex-Labs/anything-llm/blob/master/LICENSE) | **Yes** |
| **Elasticsearch** | Triple: "GNU Affero General Public License v3.0 only", "the Server Side Public License, v 1", **and** "the Elastic License 2.0", at the licensee's choice; `x-pack` is Elastic License 2.0 only | [LICENSE.txt](https://github.com/elastic/elasticsearch/blob/main/LICENSE.txt) | **Yes, via the Elastic License 2.0** — see the analysis below |
| **Vespa** | Apache License 2.0 | [LICENSE](https://github.com/vespa-engine/vespa/blob/master/LICENSE) | **Yes** |
| **Haystack** | Apache License 2.0 | [LICENSE](https://github.com/deepset-ai/haystack/blob/main/LICENSE) | **Yes** |
| **LlamaIndex** | MIT ("Copyright (c) Jerry Liu") | [LICENSE](https://github.com/run-llama/llama_index/blob/main/LICENSE) | **Core yes**; separate hosted services are a different question |
| **LangChain** | MIT ("Copyright (c) LangChain, Inc.") | [LICENSE](https://github.com/langchain-ai/langchain/blob/master/LICENSE) | **Core yes**; LangSmith is a hosted product |
| **Microsoft GraphRAG** | MIT | [LICENSE](https://github.com/microsoft/graphrag/blob/main/LICENSE) | Yes, but see section 8 on whether you want it |
| **LightRAG** | MIT ("Copyright (c) 2025 LightRAG Team") | [LICENSE](https://github.com/HKUDS/LightRAG/blob/main/LICENSE) | Yes, but see section 8 |
| **Qdrant** | Apache License 2.0 | [LICENSE](https://github.com/qdrant/qdrant/blob/master/LICENSE) | Yes |

### The Elastic licence deserves more care than it usually gets

Elastic's file is a triple licence, and the practical reading is that **you choose which of the three
you accept**. The Elastic License 2.0 text is short enough to reason about directly. Its two operative
limitations are:

> "You may not provide the software to third parties as a hosted or managed service, where the service
> provides users with access to any substantial set of the features or functionality of the software."

> "You may not move, change, disable, or circumvent the license key functionality in the software."

**[measured: read from the licence file above]** Neither prohibits installing Elasticsearch on
hardware in a client's building as part of a build we are paid for. The client is the operator, not a
third party receiving a hosted service from us. This is materially different from AGPL, where the
network-copyleft clause would attach to modifications we serve over a network, and from SSPL, which
sweeps in the management layer.

**[inference]** The trap is not the licence. It is that Elastic's most valuable relevance features
(ELSER, the learned-sparse retriever, the inference service) live or are configured in `x-pack`, which
is Elastic License 2.0 only — no AGPL alternative. That is fine for us under the same analysis, but it
means the "we'll just use the AGPL fork" escape hatch does not cover the parts we would actually want.

### Onyx's licence is the sharpest trap I found

The core is genuinely MIT, and the file says so clearly. But it also says:

> "All content that resides under 'ee' directories of this repository is licensed under the Onyx
> Enterprise License."

and the enterprise licence says:

> "This software and associated documentation files (the 'Software') may only be used in production, if
> you (and any entity that you represent) have agreed to, and are in compliance with, the Onyx
> Subscription Terms of Service ... and otherwise have a valid Onyx Enterprise License for the correct
> number of user seats."

**[measured: read from the licence files above]** The developer-facing edge is that the enterprise
licence carves out development and testing: "you may copy and modify the Software for development and
testing purposes, without requiring a subscription."

**[inference]** This creates a specific hazard for us. A deployment assembled from Onyx core plus
`ee` features would work in development, look complete in a demo, and be unlicensable in production
without a per-seat subscription from a competitor. If Onyx is used as a reference architecture rather
than a deployed product, none of this matters. If it is deployed, someone must audit which code paths
are under `ee`.

Verba is worth one line: archived, and its licence header says "All rights reserved" before granting
BSD-3-Clause. It was a credible Weaviate demo (7,697 stars) and it is not a system to build on.

---

## 3. Commercial products, read for mechanism

### 3.1 Glean — the knowledge graph claim, and how thin the public mechanism is

Glean's connector documentation is unusually detailed about *plumbing* and almost silent about
*ranking*. What it does document:

- **Three data access modes**, and this is the most transferable idea in their material: **indexed**
  (crawled into the tenant index), **live retrieval** (the connector queries the source at request
  time), and **hybrid** (combining index results with targeted live fetches, merged "into one
  permission-filtered context pool"). **[vendor]** The stated reason is freshness: email, calendar, and
  chat change minute to minute and are impractical to index continuously, while wikis and tickets
  reward indexing for recall and latency.
- **Permission mirroring at the connector layer.** "Connectors sync access control information from
  sources (for example, ACLs or equivalent models) so retrieval respects source visibility."
  **[vendor]** Permissions are a property of ingestion, not a filter applied afterwards.
- **Activity signals in ranking.** "Where a connector ingests or derives engagement data (views, edits,
  shares, and similar), Glean can use those signals in ranking." **[vendor]**
- **The retrieval algorithm itself is not public.** No fusion method, no reranker, no candidate count,
  no chunk size. **[inference]** I searched their developer docs, their perspectives blog, and their
  security pages. Their technical writing stops at the data layer.
- **Citations.** "For grounded answers, Glean typically surfaces citations (for example, deep links) so
  users can open sources and verify. Citation coverage can vary by surface, workflow, and whether the
  model returns a grounded response." **[vendor]** Note the hedges: *typically*, *can vary*. This is a
  vendor describing citation coverage it does not promise.

**[inference]** Glean's actual moat is visible in the document and it is not retrieval quality. It is
250-plus connectors with identity resolution and permission mirroring, plus the organizational
discipline to keep sync healthy. The docs say the quiet part: "Assuming the connector and configuration
are correct" — permission correctness is a conditional, and the failure mode when the condition breaks
is a leak. That is the operational cost of the indexed-permissions model, and it is why connector
health monitoring is a documented concern rather than an afterthought.

### 3.2 Hebbia — a genuine engineering account of the agentic pattern

Hebbia's engineering post on the Matrix Agent redesign is the most substantive public engineering
writing of any product in this review. It is worth reading directly because it is a candid failure
account. **[vendor: their own engineering blog, self-reported, with no benchmark numbers]**

What they report building, and why:

- **The 1.0 design failed in four named ways**: "Overloaded Tooling" (every tool on one agent, causing
  tool-selection confusion), "Needle in the Haystack Problems" (context window filled with Matrix
  content and tool output, "resulting in higher latency and recall problems"), convoluted chat
  histories, and a single monolithic system prompt.
- **The 2.0 design is orchestrator plus subagents, with a specific discipline**: "the orchestrator never
  directly invokes any tools: it simply passes a text-based 'detailed objective' to a chosen subagent".
  The orchestrator is told only the subagent's name and a description of its capability.
- **Reported result**: "near elimination of 'tool use hallucinations'". No number is given. **[vendor,
  unquantified]**
- **They tried the open-source frameworks first and rejected them.** In November 2024 they surveyed
  available agent frameworks and found "Many contemporary solutions were still in early alpha stages or
  were merely demonstrative products without commitment to production-level reliability. Their focus
  was often on observability or flashy demos rather than robust core functionality." **[vendor]**

**[inference]** The transferable lesson is not "build a multi-agent orchestrator". It is the two
concrete disciplines: keep the planning layer ignorant of tool specifics so tool-selection errors
cannot happen there, and separate context classes per subagent so no single prompt accumulates
everything. Both are cheap to implement and neither needs a framework.

The honest caveat: Hebbia's product is a spreadsheet over a near-unlimited corpus where a human
specifies the columns. That is a different problem from "answer this question about these four
contracts", and it justifies far more agent machinery than we need.

### 3.3 Perplexity — the citation-forward design, and what is actually public

I could not verify Perplexity's retrieval internals from a primary engineering source. A fetch of a
third-party stream analysis returned truncated content with no usable mechanism detail. **[unverified]**

What I can state without overreach:

- Perplexity's public product identity is citation-forward: every answer carries numbered sources, and
  this is the feature the market copies.
- **[inference]** The interesting engineering question is not how they retrieve but how they select
  which retrieved spans become citations, and whether they verify support. I found no primary public
  material describing either. Their API documentation describes the interface, not the mechanism.

**[inference]** The relevant conclusion for us is a negative one, and it is a finding: the most
citation-forward consumer product in the market does not publish how its citations are chosen or
whether they are checked. That means we cannot copy it, and it also means we cannot be out-competed on
a published design. Our citation verifier has to be our own engineering, and its accuracy number has to
be one we measured.

### 3.4 Azure AI Search — the best-documented reranking pipeline in the industry

This is the most useful vendor documentation in the review, because Microsoft publishes the actual
constraints. **[vendor throughout this section: Microsoft Learn, "Semantic ranking in Azure AI
Search", viewed 2026-10-11]**

The mechanism, precisely:

- **L1 is BM25 or RRF.** Semantic ranker "always adds secondary ranking over an initial result set that
  was scored using BM25 or Reciprocal Rank Fusion (RRF)". RRF is applied automatically for hybrid
  queries.
- **L2 is a bounded rerank over the top 50.** "Even if results include more than 50 results, only the
  top 50 results progress to semantic ranking." The models are "multilingual, deep learning models
  adapted from Microsoft Bing", hosted by Microsoft.
- **Input compression is mandatory and revealing.** "Because the ranking models have input size
  constraints and are processing intensive, search results must be sized and structured (summarized)
  for efficient handling." Each document is summarised into a string of at most **2,048 tokens**
  (raised from 256 in November 2024), assembled from title (≤128 tokens), keywords (≤128 tokens), and
  content (the remainder). Long fields are truncated in the order the semantic configuration lists
  them.
- **The score is a discrete 0-to-4 relevance grade**, not a calibrated probability: 4.0 "highly
  relevant and answers the question completely", 3.0 "relevant but lacks details", 2.0 "somewhat
  relevant", 1.0 "related ... answers a small part", 0.0 "irrelevant".
- **Microsoft explicitly warns against thresholding it finely.** "The distributions of
  `@search.rerankerScore` can exhibit slight variations due to conditions at the infrastructure level.
  Ranking model updates can also affect the distribution. For these reasons, if you're writing custom
  code for minimum thresholds ... don't make the limits too granular." **[vendor]** That is a vendor
  telling you its own relevance score is not stable enough to gate on precisely.
- **Query rewrite can generate up to 10 variants** of the query, scored by BM25 or RRF, then rescored.
- **Captions and answers are verbatim.** "Captions and answers are always verbatim text from your index.
  There's no generative AI model in this workflow that creates or composes new content."

**[inference]** Three things to take from this. First, the industry's best-documented reranker is a
**50-candidate window over compressed 2,048-token summaries** — that is the practical envelope, and it
is far smaller than "rerank 100 candidates over full chunks". Second, Microsoft's own score is a
five-point ordinal scale it tells you not to threshold tightly, which is a direct argument for the
build brief's stance that the confidence decision should not rest on a reranker score. Third, Azure
solved citations by not generating them: it returns verbatim extracted passages and lets the caller
assemble the answer. That avoids hallucinated citations at the cost of not producing prose.

### 3.5 Vertex AI Search — not covered

**[unverified]** I did not reach primary Google documentation for Vertex AI Search's retrieval
parameters, ranking API behaviour, or grounding-check mechanics within this pass. I am flagging it
rather than describing it from memory. Any statement about Vertex in this report would be inference,
and I have kept it out.

**[inference]** One structural point does hold and does not need their docs: Vertex AI Search is a
managed cloud service with no on-premises deployment path. For the office build, which has no internet
at runtime, it is disqualified by the constraint rather than by a technical comparison. It is relevant
only as a design reference and as a cloud-build alternative.

### 3.6 Elastic and Vespa — the two engines that expose their parameters

**Elasticsearch RRF, as documented.** **[vendor]** The RRF retriever takes two or more child retrievers,
each carrying **equal weight** ("Each child retriever carries an equal weight as part of the RRF
formula"). Two parameters matter:

- `rank_constant` — "This value determines how much influence documents in individual result sets per
  query have over the final ranked result set. A higher value indicates that lower ranked documents
  have more influence." **Defaults to 60.** Must be ≥ 1.
- `rank_window_size` — "the size of the individual result sets per query. A higher value will improve
  result relevance at the cost of performance. The final ranked result set is pruned down to the search
  request's `size`." **Defaults to the `size` parameter.**

The documented example — the one Elastic chooses to show — uses `rank_window_size: 50` and
`rank_constant: 20`, i.e. both defaults overridden downward. **[vendor]** And the documented primary
use case is "combining top documents from a traditional BM25 query and an ELSER query", which is
exactly the lexical-plus-learned-sparse pairing.

**Vespa.** Apache 2.0, and its phased-ranking documentation covers cross-hit normalisation including
RRF and multi-phase ranking with cross-encoders. **[vendor]** Vespa's `rank-profile` model is the most
expressive ranking language in this review: first-phase, second-phase, and global-phase expressions
with `closeness()` functions and per-phase candidate counts, all declared in schema. **[inference]**
That expressiveness is also the cost. Nothing in Vespa is a default; you get out exactly what you
specify, and the specification language is a genuine learning curve for a small team.

**The operational question, which decides it.** Neither engine is a plausible eleventh container on a
16 vCPU / 64 GB box that is already running Postgres, two embedding/reranking services, an ingestion
pipeline with OCR, an API, a UI, a model gateway, a workflow engine, and an audit sink. Elasticsearch
wants a JVM heap sized against the vector data and is a multi-gigabyte resident process before it holds
anything; Vespa is explicitly designed as a multi-node system and its single-node Docker quickstart is
a development convenience. **[inference, from their documented deployment shapes — I did not find a
published minimum-resource figure for either at our corpus size, so the specific memory numbers are
[unverified]].** For 50,000 documents, the honest answer is that Qdrant plus our own fusion code does
the same job in a fraction of the footprint, which is what the sibling report on fusion already argued
on quality grounds.

---

## 4. Open-source products, read from source

This is where the report earns its keep, because these are the systems whose actual constants are
public.

### 4.1 Onyx — the most instructive system in the review, and it is simpler than we are

Onyx is the closest analogue to what we are building: connectors, permission sync, hybrid retrieval,
cited answers, self-hosted. What it actually runs, read from the repository:

- **Two arms, equal weights, min-max normalisation.** From
  `backend/onyx/document_index/opensearch/search.py`: the hybrid normalisation weights are
  `search_content_vector_weight: float = 0.5` and `search_keyword_weight: float = 0.5`, and the code
  asserts they sum to 1.0. The default normalisation technique is `min_max`; `z_score` is available as
  an alternative (`normalization_pipeline_zscore`). **[code]**
- **500 candidates per arm.** From `backend/onyx/document_index/opensearch/constants.py`:
  `DEFAULT_NUM_HYBRID_SUBQUERY_CANDIDATES = int(os.environ.get("DEFAULT_NUM_HYBRID_SUBQUERY_CANDIDATES", 500))`.
  The comment documents the reasoning and the history: "Defaults to 500 for now. Initially this
  defaulted to 750 but we were seeing poor search performance; bumped from 100 to 500 to improve
  recall." It also states the candidate-depth argument plainly: "If we only fetch 10 candidates from
  each of keyword and vector, they would have to have perfect overlap to get a good hybrid ranking for
  the 10 results." **[code]**
- **512-token chunks.** `DEFAULT_MAX_CHUNK_SIZE = 512`. **[code]**
- **HNSW tuned for accuracy over memory**: `EF_CONSTRUCTION = 256` ("typically range between 100 -
  512") and `M = 32` with the comment `# Set relatively high for better accuracy.` **[code]**
- **Reranking was removed.** There is a migration
  `backend/alembic/versions/78ebc66946a0_remove_reranking_from_search_settings.py`, no reranking
  setting in the search configs, and the only reranker file in the tree
  (`backend/model_server/legacy/reranker.py`) is **entirely commented out**. **[code]**

**[inference]** Read that sequence as a production experiment we are being handed for free. A team
with real enterprise customers, real connectors, and real relevance pressure built a reranking path,
lived with it, and deleted it. Their final ranking is two arms, equal weights, 500 candidates deep,
fused by min-max normalisation. Our brief proposes hybrid plus prefetch plus RRF plus a cross-encoder
over 50 to 100 candidates. Onyx is doing less and shipping it to enterprises.

I want to be careful not to overclaim: I cannot see *why* they removed it, and the migration file name
only tells us *that* they did. It could be latency, cost, operational simplicity, or a measurement that
it did not help. **[inference]** All four explanations point the same way for a CPU-only deployment.

**Permissions.** Onyx filters at query time through an access-control list carried on the document in
the index (`ACCESS_CONTROL_LIST_FIELD_NAME`), alongside document sets, cc-pair access, tags, and source
types, all assembled in `_build_index_filters`. There is a documented `FILTER_SEMANTICS.md` in
`backend/onyx/document_index/`, which is itself a signal: filter semantics were subtle enough to need
a document. The pipeline code also closes a real bypass — persona-configured document sets are
re-validated against the user's access, raising `INSUFFICIENT_PERMISSIONS` if the user cannot see a
requested set, with a comment naming it: "This closes the API-layer bypass where a user could override
the persona's configured document sets with arbitrary names." **[code]**

**[inference]** That comment is the single most valuable line I read in this entire review. It is a
real vulnerability class — a retrieval filter that is correct in the UI and bypassable at the API — and
it is exactly the kind of leak the build brief lists as release-blocking. Our access label in the Qdrant
payload needs the same treatment: the filter must be constructed server-side from the authenticated
session and never from a caller-supplied parameter.

### 4.2 RAGFlow — a weighted-sum fusion with dense bias, and public defaults

Read from `internal/service/nlp/retrieval.go` and `internal/service/nlp/reranker.go` on `main`. RAGFlow
has been substantially rewritten in Go (the repository is now ~30 MB of Go against ~1.2 MB of Python).
**[code]**

The documented defaults, straight from the request-struct initialisers:

| Parameter | Default | Source |
|---|---|---|
| `KNNTopK` | **1024** | `retrieval.go` |
| `KNNNumCandidates` | **2048** | `retrieval.go` |
| `SimilarityThreshold` | **0.2** | `retrieval.go` |
| `VectorSimilarityWeight` | **0.3** (1.0 when `VectorOnly`) | `retrieval.go` |
| `RerankCandidatesCount` | **64** | `retrieval.go` |
| `RankFeature` | `{"pagerank_fea": 10.0}` | `retrieval.go` |

**[code]** The fusion is a weighted sum, not RRF. The comment at the scoring function states the
formula directly: `// sim = tkWeight*tsim + vtWeight*vsim`, with `vtWeight := *req.VectorSimilarityWeight`
and `tkWeight := 1.0 - vtWeight`. So the default fusion is **0.7 × token similarity + 0.3 × vector
similarity**. A second comment block higher in `reranker.go` gives the fuller form the pipeline
documents for itself, and the extra term matters:

```
//  - Formula: sim = tkWeight * tksim + vtWeight * vtsim + rank_features
//  - Token weighting: content + title*2 + important_kwd*5 + question_tks*6
//  - Rank features: tag_score * 10 + pagerank (per chunk)
```

**[code]** So the fused score is a weighted sum **plus** `rank_features`, in which `tag_score` carries a
×10 multiplier and pagerank is added per chunk. **[inference]** The tag weight is doing real work: a
user tag on a chunk can outweigh a substantial similarity gap. That is a product decision, since tags
are explicit human signal, rather than a retrieval-quality one, and it is the kind of thing that should
be a deliberate choice for us rather than an inherited default.

The lexical arm is more considered than "keyword search" suggests. Token weights are combined as
`content + title*2 + important_kwd*5 + question_tks*6` **[code]** — so a term appearing in the
question's own extracted keywords counts six times, and a document title twice. The vector weight is
normalised by `NormalizeRerankScores`, which has a deliberate behaviour worth noting: if the incoming
scores already lie in [0, 1] it **returns them unchanged**, "so calibrated providers and degenerate (but
valid) batches are NOT collapsed to zero". **[code]**

Two further details that show product thinking:

- The `VectorOnly` path sets the vector weight to 1.0 and *disables the post-threshold*, with a
  comment explaining that re-applying a similarity threshold to an already-kNN-floored set "buys
  nothing — and costs everything when the score is the one this pipeline reads back: a similarity that
  comes out 0 ... sends the whole candidate set through the floor and the tool reports '0 hits' for a
  corpus that certainly has neighbours." **[code]** That is a real bug they hit and documented.
- Reranking and pagination are mutually exclusive: "Pagination is not supported when `rerank_mdl` is
  specified. Please set `page=1`." **[code]**

**[inference]** The architecture lesson is that RAGFlow retrieves very deep (2,048 candidates into the
engine, 1,024 top-k) and then does the precision work with a wide rerank window (64) and a shallow
final page. That is the opposite emphasis from our brief, which reranks 50–100 down to a handful. On
CPU, RAGFlow's shape is cheaper per query than a cross-encoder over full chunks, because the
"reranking" it does by default is arithmetic on scores it already has.

Also note the pagerank feature at weight 10.0: RAGFlow boosts chunks whose parent document looks
central. **[inference]** That is a document-level prior, not a relevance signal, and for a
single-client corpus of contracts and policies it is close to meaningless — there is no link graph to
compute pagerank over.

### 4.3 AnythingLLM and Verba — reference points, not contenders

**AnythingLLM** is MIT with no additional terms in the file, which makes it the cleanest licence of any
complete product here. **[measured]** It is a popular single-tenant document-chat application.
**[inference]** Its architecture is the commodity shape — vector search over embedded chunks with a
similarity threshold and a source-citation display — and it does not attempt permission-aware
retrieval at the document level, which is the thing we cannot compromise on. Worth watching as a UI
reference; not a retrieval architecture.

**Verba** is archived. **[measured]** Do not build on it. Its historical value was demonstrating a
clean Weaviate-backed RAG UI.

### 4.4 Framework reference architectures — LlamaIndex, LangChain, Haystack

**[inference, and partly unverified]** I did not fetch primary documentation pages for the three
frameworks' current default retrievers within this pass, so I am deliberately not quoting their
defaults or their evaluator APIs. What I can state from the shape of the evidence in this review:

- All three core libraries are permissively licensed (MIT, MIT, Apache 2.0), verified above.
  **[measured]**
- The hosted companions are not offline-safe: LlamaParse is a hosted PDF parser and LangSmith is a
  hosted evaluation and observability platform. For the office build, with no internet at runtime,
  neither is available. **[inference from their product nature]**
- **[inference]** The frameworks' role in this ecosystem is to *propagate* techniques, and the
  techniques they propagate most visibly (HyDE, multi-query, sub-question decomposition, sentence-window
  and auto-merging retrieval) appear as cookbook patterns rather than defaults. The absence of any of
  them from every production system I read in this review is the more informative signal.

**[inference]** For our shape — one retrieval pipeline, one corpus per client, parameters we must
control precisely and diagnose at 2am — a framework is a liability. It adds transitive dependencies
that complicate offline installation, and it abstracts away exactly the parameters (candidate counts,
fusion weights, thresholds) that this report is about. The sibling report on fusion reached the same
conclusion independently.

---

## 5. What nobody does: verify citations

I looked for this specifically, in every product, and found nothing at the sentence level.

- **Glean** surfaces deep links and hedges coverage. **[vendor]**
- **Azure** returns verbatim captions and answers and has no generative model in that path. **[vendor]**
- **Onyx** shows retrieved passages with the answer. **[inference from its UI model: I did not read
  a citation-verification code path]**
- **RAGFlow** returns chunks with page/position metadata for display. **[code: the retrieval result
  carries page and position fields]**
- **Bedrock Guardrails contextual grounding check** is the only *commercial support verifier* I found
  documented as a mechanism. Its documented behaviour: it checks **Grounding** ("if the model response
  is factually accurate based on the source ... Any new information introduced in the response will be
  considered un-grounded") and **Relevance** ("if the model response is relevant to the user query"),
  producing confidence scores with configurable thresholds in the range 0 to 0.99. **[vendor, AWS
  documentation, viewed 2026-10-11]**

Its three documented limitations are the interesting part, and they are the reason it is not a drop-in
solution:

1. **"Conversational QA / Chatbot use cases are not supported."** The supported use cases are
   "summarization, paraphrasing, and question answering as defined in computer science discipline".
   **[vendor]** A document-QA assistant is a conversational QA use case.
2. **Whole-response granularity with an OR.** "Contextual grounding checks check for relevance for each
   chunk processed. If any one chunk is deemed relevant, the whole response is considered relevant as
   it has the answer to user's query." **[vendor]** One supported sentence marks a response with three
   unsupported sentences as grounded.
3. **Streaming leaks.** "For streaming API, this can result in scenario where an irrelevant response is
   returned to the user and is only marked as irrelevant after the whole response is streamed."
   **[vendor]**

There is also a hard input ceiling: 100,000 characters of grounding source, 1,000 characters of query,
5,000 characters of response. **[vendor]**

**[inference]** Take three conclusions. First, the market leader in cloud AI ships a grounding checker
that explicitly does not support our use case, checks at a granularity coarser than a sentence, and can
show the user the bad answer before flagging it. That is the competitive bar, and it is low. Second, a
per-sentence verifier that runs before the answer is displayed is straightforwardly better than what
Bedrock offers, and it is buildable with a small NLI model on CPU. Third, the threshold semantics are
borrowed from a system that gates on a single score — and Azure's own warning about not thresholding
its reranker score finely suggests that a *discrete, inspectable* decision (this sentence's citation
does/does not entail it) is more defensible than a tuned scalar cutoff.

---

## 6. Table stakes, differentiators, and theatre

Now the commercial question. I have split it by who can copy the thing.

### 6.1 Table stakes — a competitor has these, and their absence is a defect

| Technique | Why it is table stakes | Evidence |
|---|---|---|
| **Lexical arm alongside dense** | Every system here runs two arms. Elastic's documented primary RRF use case is BM25 plus ELSER. Onyx is vector plus keyword at 0.5/0.5. RAGFlow is token plus vector at 0.7/0.3. | [code], [vendor] |
| **Deterministic fusion** | RRF or normalise-then-weight. Both are cheap, both are parameter-light, and both are what production runs. Elastic RRF defaults `rank_constant: 60`. | [vendor], [code] |
| **Deep candidate retrieval** | Onyx fetches 500 per arm and documents why 100 was not enough. RAGFlow pulls 1,024 top-k from 2,048 candidates. Recall happens at the candidate stage or not at all. | [code] |
| **Document-level access control enforced at query time** | Onyx carries an ACL on the indexed document and filters in the query, and re-validates persona document sets to close an API bypass. Glean mirrors source ACLs at ingestion. | [code], [vendor] |
| **Citations that link to the source** | Glean, Onyx, RAGFlow, Azure all do at least this. It is not differentiated. | [vendor], [code] |
| **A refusal path that is a real outcome** | The brief requires it; every serious product has some form of "no results" or low-confidence state. Azure's `@search.rerankerScore` of 0.0 is "irrelevant". | [vendor] |
| **Page or passage provenance carried through ingestion** | Undifferentiated in the market only because their corpora do not need it. For us it is structural, and section 7 argues it is the hard part. | [inference] |

### 6.2 Genuine differentiators a competitor would struggle to match

**1. Per-sentence citation verification that runs before display.** Nobody ships it. It is precisely
buildable with a small NLI model on CPU. It converts the product's central promise ("every answer is
verifiable") from a claim into a measured number, and it is the only feature in this report that
attacks the failure mode the brief names as fatal. **[inference, grounded in the absence of any
commercial equivalent found above]**

**2. Page-anchored structure preservation through ingestion.** The market's systems treat documents as
text streams because their sources are wikis and tickets. Our corpus is contracts, fee schedules, and
scanned PDFs, where a wrong number on a right page is the failure that loses a client. **[inference]**
This is where the build brief's insistence on Docling plus page-aware chunking is doing real work, and
it is genuinely hard for a competitor to retrofit, because it constrains the parser, the chunker, the
citation format, and the verifier simultaneously.

**3. Per-deployment evaluation with a small golden set, run on every change.** No product in this
review publishes per-customer retrieval evaluation. **[inference]** The brief's 60 questions with
release-blocking single results is both a quality mechanism and a sales artifact: it is the thing you
show a skeptical client instead of an accuracy claim.

**4. An audit record that a compliance reviewer can read.** Glean's permission model depends on
connector health being "correct" — a conditional. **[vendor]** Our design writes access decisions and
refusals to a hash-chained sink with an external anchor. **[inference]** That is a different kind of
artifact, and it does not transfer from any product here.

**5. The office build's offline guarantee.** Not a retrieval technique, but it is the differentiator
that makes the other four saleable, and it disqualifies most of the market by construction.

### 6.3 Theatre — impresses in a demo, weak or absent evidence

| Technique | Why I call it theatre | Evidence |
|---|---|---|
| **GraphRAG for single-corpus document QA** | Its own benchmark says GraphRAG "frequently underperforms vanilla RAG on many real-world tasks". On fact retrieval, RAG with reranking beat GraphRAG local 60.92 to 49.29. Index build cost is ~12× HippoRAG2 and vastly more than vanilla RAG. | [measured] |
| **Unbounded agentic retrieval loops** | Hebbia — the one company that publishes on agentic retrieval — reports that their single-agent design failed on "needle in the haystack" context overload and that they rebuilt around *constraining* the orchestrator. Their fix was less agent, more structure. | [vendor] |
| **`pagerank_fea` document priors on a business corpus** | RAGFlow's default `RankFeature` is `{"pagerank_fea": 10.0}`, a document-centrality boost. A single client's folder of contracts has no meaningful link graph. | [code] + [inference] |
| **Query rewriting as a default** | Azure ships it as a *preview* feature generating up to 10 variants. Onyx has a migration named `a7c3e2b1d4f8_remove_multilingual_expansion_from_search_settings.py` — they removed multilingual query expansion. | [vendor], [code] |
| **Reranking as an unquestioned default** | Onyx removed it. Azure caps it at 50 compressed candidates and warns against thresholding the output. RAGFlow ties it to losing pagination. The sibling report found the measured gains inconsistent. | [code], [vendor], [inference] |
| **HyDE, sentence-window, auto-merging, sub-question decomposition** | Not run by any production system in this review. They appear as framework cookbook patterns. | [inference] |

A note on the theatre list, because it is the part of this report most likely to be wrong in a year.
"Theatre" here means *weak or absent evidence at our scale*, not *worthless*. GraphRAG's global
summarisation result is real: community summaries reached 72 to 83 percent comprehensiveness win rates
under an LLM judge. **[measured: arXiv:2404.16130 as cited]** The question a buyer asks is whether
that result justifies a twelvefold indexing cost and a graph database in a five-person office. It does
not.

---

## 7. The uncomfortable finding: our differentiator is an ingestion problem

Worth stating separately, because it changes where the engineering budget goes.

Every commercial system in this review is architected around sources where the unit of content is a
*page in a wiki*, a *ticket*, or a *message*. Glean's docs are explicit: connector modes differ by
source type, live retrieval exists for "email, calendar, chat, or other data where minutes-to-hours
freshness dominates". **[vendor]** Nobody writes about page-number preservation because nobody's
corpus has pages in the sense ours does.

Our corpus does. The brief says it plainly: "a scanned document that yields nothing is an obvious gap,
but a fee schedule read as a token stream produces a confidently cited **wrong number**".

**[inference]** This inverts the usual priority order. In the systems I read, the expensive parts are
ranking and generation. In ours, the expensive part is the parser. Docling's layout, table, and OCR
models are CPU-heavy precisely because they are doing the work that decides whether the rest of the
pipeline can be right. A perfect reranker cannot recover a table whose rows were serialised into a
token stream, and a perfect verifier will faithfully confirm that the wrong number is supported by the
garbled passage it was given.

The commercial consequence: the thing worth demonstrating to a client is not answer quality on an easy
question. It is a fee schedule with three columns and merged header cells, answered with the right
number and cited to the right page. That is the demo that cannot be copied by anyone who took the
standard RAG tutorial.

---

## 8. GraphRAG in detail, because it is the most likely expensive mistake

The brief asks me to be blunt about weak evidence. This is the clearest case.

**The primary result is task-specific and judge-measured.** Microsoft's GraphRAG paper reports 72 to 83
percent comprehensiveness and 62 to 82 percent diversity win rates for community summaries, and about
97 percent token reduction from root-level summaries. **[measured: arXiv:2404.16130, as reported in a
secondary analysis I read; I did not read the paper's own tables]** Those win rates come from an LLM
judge with no gold answers, and the task is query-focused *summarisation* — global sensemaking over a
corpus — not "what is the mileage rate in this policy".

**Under ground-truth scoring the picture changes.** The same secondary analysis reports that against
gold answers on ROUGE-2, GraphRAG loses to plain RAG: 6.99 against 10.08 on SQuALITY and 3.23 against
6.32 on QMSum, and notes that summary-ordering position bias can flip an LLM judge's preference on its
own. **[unverified: I did not reach the underlying paper for these ROUGE-2 figures; the claim is
attributed to arXiv:2502.11371 in a blog post I read.]** I am reporting the direction, not the digits.

**The benchmark designed to settle it says GraphRAG often loses.** GraphRAG-Bench's abstract states
that "recent studies report that GraphRAG frequently underperforms vanilla RAG on many real-world
tasks", and the benchmark's purpose is to establish when graphs help. **[measured: arXiv:2506.05690,
abstract read directly]** Reported results on its fact-retrieval task: "RAG (w rerank)" 60.92 against
"GraphRAG (local)" 49.29, while HippoRAG2 beat RAG-with-rerank on complex reasoning, 53.38 against
42.93. **[unverified: from the secondary analysis; I read the abstract but not the results tables]** The
pattern is that a well-tuned conventional pipeline is a strong baseline and the graph is not uniformly
better.

**Cost is the decisive argument, and it is not close.** Index build cost spans roughly 9.2 million
tokens for HippoRAG2 against roughly 115.5 million for GraphRAG — about twelvefold. **[measured, per
the same analysis citing arXiv:2502.14802 Table 12; [unverified] as to the exact figures]** On the
office build, that indexing cost is paid in *local model* passes over every document, which is the
scarcest resource on the box. On the cloud build it is Bedrock tokens per document, repeated on every
re-ingest.

**[inference]** For 50,000 documents and 60 golden questions, GraphRAG fails a cost-benefit test before
any accuracy question is asked. If a client ever needs genuine corpus-level synthesis ("what are the
recurring themes across these 400 contracts?"), the cheaper path is a query planner that decomposes the
question and a recursive summarisation pass over retrieved clusters, measured against the golden set
like everything else.

**The graph store is its own licence and operations problem.** **[unverified: I did not verify Neo4j's
licence from its repository in this pass.]** Neo4j Community Edition is commonly described as GPLv3
and Enterprise as commercial, which would matter for a shipped product, but I am not certifying that
from a file I read. Microsoft's `graphrag` and LightRAG are both MIT and can write to a plain Postgres
or file-backed store, which avoids the question entirely — but if the graph is not buying accuracy,
avoiding the question by not building the graph is the better answer.

---

## 9. What I would build, in order

The question: the most accurate retrieval available on a single CPU box for one client's document set.

The ordering principle is that **recall is decided before ranking, and correctness of the citation is
decided before either**. Almost all the leverage sits in steps 1 and 2, and almost all the
differentiation sits in step 5.

**Step 1 — The parser, with page and table structure as first-class output.** Docling, MIT, containerised.
Every chunk carries `{document_id, page, bbox_or_reading_order, table_id if applicable, content_hash,
acl}`. Do not proceed until a scanned, multi-column fee schedule reads back as a table with the right
numbers on the right page. **[inference]** This is the step that decides whether the product can be
right, and it is the step the market's systems under-invest in because their corpora are forgiving.

**Step 2 — Candidate depth, from two arms, fused arithmetically.** Dense from bge-m3 via TEI, lexical
from Postgres full-text (or Qdrant's own sparse support) rather than standing up Elasticsearch or
Vespa. Retrieve deep — Onyx's 500 per arm and RAGFlow's 1,024 top-k from 2,048 candidates both argue
for hundreds, not tens. Fuse with RRF at `rank_constant` 60 (Elastic's default) or min-max normalised
weights (Onyx's default). **[inference]** Either is defensible; neither is worth tuning on 60 questions.
The sibling report on fusion reached the same conclusion with the measured backing.

**Step 3 — Rerank a bounded window, and measure whether it earns its CPU.** Start with
`bge-reranker-v2-m3` over a window no larger than Azure's 50, on *compressed* passages rather than full
chunks if latency demands it, because that is what Microsoft does to make reranking affordable.
Then run the golden set with and without. **[inference]** If it does not move retrieval recall
materially, delete it, as Onyx did. Budget the decision honestly: on CPU this is the most expensive
per-query step in the pipeline.

**Step 4 — Permissions as a query-time filter built server-side, never from a parameter.** The role
comes from the authenticated session; the filter is constructed in our code; a caller-supplied document
set is re-validated against the user's access and rejected with a recorded denial if it does not match.
**[code: this is directly modelled on Onyx's `_build_index_filters` and its documented API-layer
bypass fix]** Ship the regression test before the feature.

**Step 5 — The citation verifier, per sentence, before display.** Split the answer into sentences,
attach each to its cited passage, and check entailment with a small NLI model on CPU. Three checks, as
the brief specifies: the cited index exists in the retrieved set, the cited page matches the passage's
page, and the sentence is entailed by the passage. Mark unsupported sentences amber rather than
deleting them, and record the support rate per answer. **[inference]** This is the differentiator, it
is cheap relative to reranking, and no competitor ships it.

**Step 6 — Refusal as a named, recorded outcome.** The brief's four reasons (`no_source`, `boundary`,
`policy`, `insufficient_evidence`) written to the same audit chain as a success, each carrying its
retrieval trace. **[inference]** Azure's `rerankerScore` of 0.0 and Bedrock's threshold fields show the
commercial idiom; a named reason with a trace is more useful to a client and more defensible in a
dispute.

**Step 7 — The 60 golden questions, with the five release-blocking single results.** Run on every model
swap, chunking change, and parser upgrade. **[inference]** Treat this as product, not QA. It is what
makes "models are configuration" true, and it is the artifact you show a client.

### What I would deliberately not build

- **GraphRAG, or any graph store.** Twelvefold indexing cost, and its own benchmark says it often loses
  to vanilla RAG on real tasks. **[measured]**
- **Agentic multi-hop retrieval as the default path.** Hebbia's own account is that context overload
  degraded recall and that the fix was constraining the planner. **[vendor]** Revisit only if the
  golden set shows multi-document synthesis questions failing for a reason a single retrieval cannot
  fix.
- **Elasticsearch or Vespa.** Both are licensed shippably (Elastic via its Licence 2.0, Vespa via
  Apache 2.0), and neither belongs as an eleventh container on a 16 vCPU / 64 GB box that is already
  running OCR. **[inference]** Qdrant plus our own fusion code covers this corpus.
- **A RAG framework as the substrate.** Dependency churn, offline install complexity, and abstraction
  over exactly the parameters we need to control. **[inference]**
- **Production reliance on Bedrock's contextual grounding check.** It does not support conversational
  QA, it decides at whole-response granularity with an OR across chunks, and it can leak on streaming.
  **[vendor]** Useful as a cross-check on the cloud build; not the verifier.
- **Query rewriting, HyDE, multi-query, sentence-window, auto-merging.** Not run by any production
  system in this review, and Azure keeps rewriting in preview. **[vendor], [code], [inference]**

---

## 10. What we can steal, and what we cannot

**Steal, in priority order:**

1. **Onyx's filter-semantics discipline** — server-side access filters plus explicit re-validation of
   caller-supplied scopes against the user's access, with the bypass named in a code comment. The
   cheapest high-value idea in this report. **[code]**
2. **Glean's three access modes** — indexed, live-fetch, hybrid, with the permission filter applied to
   the merged pool. **[vendor]** For a document system, "live fetch" becomes "query the source system
   at request time for the small slice that changes hourly". Relevant only if we add connectors;
   worth knowing before we design the index schema.
3. **Azure's bounded rerank window over compressed passages** — 50 candidates, 2,048-token summaries.
   **[vendor]** This is how you make a cross-encoder affordable.
4. **Hebbia's planner/tool separation** — the orchestrator knows subagent names and capabilities, not
   tools. **[vendor]** Cheap, and it removes a whole class of error.
5. **RAGFlow's calibrated-score preservation** — if incoming scores are already in [0, 1], do not
   min-max normalise them down to zero. **[code]** A small detail that prevents a silent recall bug.
6. **RAGFlow's `0 hits` lesson** — do not re-apply a similarity threshold to a vector-only leg whose
   scores the engine already floored. **[code]**

**Cannot steal, and should not pretend to:**

- **Glean's connector catalogue and identity resolution.** Years of integration work and a
  configuration-correctness burden that their own docs treat as conditional. **[vendor]**
- **Hebbia's Matrix model.** A spreadsheet interface assumes a user who knows what columns they want.
  Different product, different user. **[inference]**
- **Azure's Bing-derived reranker.** Not obtainable, and its 0-to-4 ordinal output is not something to
  imitate with a local cross-encoder's raw logits. **[vendor]**
- **Any managed knowledge base.** The brief already rules this out, and this review adds a reason:
  retrieval would run outside the audit sink and outside the citation verifier.

---

## 11. Where production practice and benchmark rankings diverge

Worth stating plainly, because the brief asks for it. **[inference throughout, grounded in the code and
vendor documentation cited above]**

Benchmarks reward the marginal nDCG point. Production rewarded something else in every system I read:

- **Onyx deleted its reranker and kept its 500-candidate hybrid fusion.** The benchmark-maximising
  choice would have been to keep and tune the cross-encoder.
- **Azure capped reranking at 50 compressed documents and told developers not to threshold the score
  finely.** A benchmark would use the full reranker over full passages.
- **RAGFlow defaulted to a 0.3 vector weight and a wide 64-candidate rerank window**, i.e. it leans on
  its lexical arm and its candidate depth more than on its vector arm.
- **Elastic's documented RRF example overrides both defaults downward** — `rank_window_size: 50`,
  `rank_constant: 20` — from 60 and the result size.

**[inference]** The pattern: the systems that carry real customers optimise for a *bounded, predictable*
per-query cost and for recall that survives a noisy corpus, and they treat the final ranking stage as
something to be capped. The systems that optimise for leaderboard position add ranking stages. Our
constraint — one CPU box, one client, 60 questions — puts us firmly on the production side of that
line, and the good news is that the production side is also the simpler one to build.

---

## 12. Failure modes and operational cost, per technique

| Technique | Failure mode | Operational cost | Verdict |
|---|---|---|---|
| Deep candidate retrieval (500/arm) | Latency and memory per query; OpenSearch/Zdrant result-window limits | Low if the store is sized for it; Qdrant serves from RAM | **Keep.** Recall is decided here |
| RRF fusion | Parameter `k` is arbitrary; cannot express "the lexical arm matters more here" | Negligible compute | **Keep.** Cheap and untunable, which is a feature |
| Weighted-sum fusion (normalise first) | Score distributions differ per query; min-max destroys calibrated scores | Negligible | **Keep**, with RAGFlow's guard: if already in [0,1], leave alone |
| Cross-encoder rerank | Gains inconsistent under held-out validation; dominates CPU | Seconds per query on CPU | **Measure, then probably cut.** Onyx cut it |
| Rerank over compressed passages | Compression loses the detail that made the passage relevant | Lower | **Consider** if reranking stays |
| NLI citation verification | The NLI model's accuracy is the ceiling; a 400M model is not a fact-checker | Tens of milliseconds per sentence | **Keep.** Highest-value compute in the pipeline |
| GraphRAG | Underperforms vanilla RAG on many tasks; enormous indexing cost; extra store and licence | ~12× HippoRAG2 index tokens | **Cut** |
| Agentic multi-hop loop | Over-searching; context overload degrading recall; latency unbounded | Multiple model calls per question | **Cut** as default; bounded 2-step at most |
| Query rewriting | Changes the query the user asked; harder to audit; preview-only at Azure | Extra model call | **Cut** as default |
| Per-document ACL filter at query time | Stale permissions leak documents; caller-supplied scope bypass | Filter construction and re-validation code | **Keep, and test the bypass** |
| Page/table-preserving ingestion | Parser is slow and wants cores; OCR errors become wrong numbers | The largest CPU cost at ingest | **Keep.** Non-negotiable for citations |

---

## 13. What I could not verify

Stated explicitly so nothing here is mistaken for verified.

1. **Vertex AI Search.** No primary documentation reached. Nothing in this report characterises its
   retrieval parameters or grounding mechanics.
2. **Perplexity's retrieval and citation-selection mechanism.** The one stream-level analysis I fetched
   returned truncated content with no usable mechanism detail. Treat any description of their internals,
   including mine, as inference.
3. **Glean's and Hebbia's actual ranking algorithms.** Deliberately not public. Everything in their
   sections is data-layer plumbing or agent orchestration, not retrieval internals.
4. **The GraphRAG ROUGE-2 figures** (6.99 vs 10.08 on SQuALITY; 3.23 vs 6.32 on QMSum) and the
   GraphRAG-Bench result rows. I read the GraphRAG-Bench abstract directly and the GraphRAG-Bench
   numbers second-hand; I did not open the results tables or the ROUGE-2 source paper.
5. **The exact token figures** for index-build cost (≈9.2M HippoRAG2 vs ≈115.5M GraphRAG).
   Second-hand, attributed to arXiv:2502.14802 Table 12.
6. **Neo4j's licence** as it applies to shipping. Not read from a repository file in this pass.
7. **Minimum resource requirements for Elasticsearch or Vespa** at a 50,000-document corpus. The
   judgement that they do not belong on our box is inference from their documented deployment shapes,
   not a measured comparison.
8. **The three frameworks' current default retrievers and evaluator APIs.** Not fetched. My statements
   about LlamaIndex, LangChain, and Haystack are limited to verified licences and the observation that
   production systems do not run the advanced patterns they document.
9. **Why Onyx removed reranking.** The migration name and the commented-out file prove *that* they did,
   not *why*. I have marked the reasoning as inference throughout.
10. **Anything about how these products evaluate retrieval internally.** No product in this review
    publishes a per-customer evaluation methodology, which is itself a finding.

---

## 14. Sources

**Licence files read directly:** [Onyx LICENSE](https://github.com/onyx-dot-app/onyx/blob/main/LICENSE) ·
[Onyx enterprise LICENSE](https://github.com/onyx-dot-app/onyx/blob/main/backend/ee/LICENSE) ·
[RAGFlow LICENSE](https://github.com/infiniflow/ragflow/blob/main/LICENSE) ·
[Verba LICENSE](https://github.com/weaviate/Verba/blob/main/LICENSE) ·
[AnythingLLM LICENSE](https://github.com/Mintplex-Labs/anything-llm/blob/master/LICENSE) ·
[Elasticsearch LICENSE.txt](https://github.com/elastic/elasticsearch/blob/main/LICENSE.txt) ·
[Elastic License 2.0](https://github.com/elastic/elasticsearch/blob/main/licenses/ELASTIC-LICENSE-2.0.txt) ·
[Vespa LICENSE](https://github.com/vespa-engine/vespa/blob/master/LICENSE) ·
[Haystack LICENSE](https://github.com/deepset-ai/haystack/blob/main/LICENSE) ·
[LlamaIndex LICENSE](https://github.com/run-llama/llama_index/blob/main/LICENSE) ·
[LangChain LICENSE](https://github.com/langchain-ai/langchain/blob/master/LICENSE) ·
[Microsoft GraphRAG LICENSE](https://github.com/microsoft/graphrag/blob/main/LICENSE) ·
[LightRAG LICENSE](https://github.com/HKUDS/LightRAG/blob/main/LICENSE) ·
[Qdrant LICENSE](https://github.com/qdrant/qdrant/blob/master/LICENSE)

**Source files read:** Onyx `backend/onyx/document_index/opensearch/search.py`, `constants.py`,
`backend/onyx/context/search/pipeline.py`, `retrieval/search_runner.py`,
`backend/onyx/configs/chat_configs.py`, `backend/onyx/document_index/FILTER_SEMANTICS.md`, and the
migration `78ebc66946a0_remove_reranking_from_search_settings.py` ·
RAGFlow `internal/service/nlp/retrieval.go`, `internal/service/nlp/reranker.go`

**Vendor documentation read:** [Glean — connectors and the Glean experience](https://docs.glean.com/connectors/connectors-power-glean) ·
[Hebbia — the multi-agent redesign behind Matrix](https://www.hebbia.com/blog/divide-and-conquer-hebbias-multi-agent-redesign) ·
[Azure AI Search — semantic ranking overview](https://learn.microsoft.com/en-us/azure/search/semantic-search-overview) ·
[Amazon Bedrock — contextual grounding check](https://docs.aws.amazon.com/bedrock/latest/userguide/guardrails-contextual-grounding-check.html) ·
[Elasticsearch — reciprocal rank fusion](https://www.elastic.co/docs/reference/elasticsearch/rest-apis/reciprocal-rank-fusion) ·
[Vespa — phased ranking](https://docs.vespa.ai/en/ranking/phased-ranking.html)

**Papers:** [GraphRAG-Bench, arXiv:2506.05690](https://arxiv.org/abs/2506.05690) (abstract read
directly) · [Microsoft GraphRAG, arXiv:2404.16130](https://arxiv.org/abs/2404.16130) ·
[arXiv:2502.11371](https://arxiv.org/abs/2502.11371) · [arXiv:2502.14802](https://arxiv.org/abs/2502.14802)

**Secondary analysis:** [The same GraphRAG comparison wins and loses, depending which instrument judged
it](https://dev.to/izgorodin/the-same-graphrag-comparison-wins-and-loses-it-depends-which-instrument-judged-it-fm9)
— used for the GraphRAG numbers I could not confirm against primaries, and labelled [unverified] where
that is the case.

**Context documents:** `~/projects/office-inference-core/docs/build-brief.md` and
`docs/architecture.md`, plus the sibling reports in this series, particularly
`04-fusion.md` (fusion measurement and the 60-question statistical power problem).
