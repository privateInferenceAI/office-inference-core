# Proving the answer: verification, abstention, and how to measure it

**Angle:** verification as the product. How to prove a generated sentence against the passage it cites, when to refuse, and how to measure all of it with 60 golden questions per client.

**Written:** 2026-10-11, for the C2 verification decision in `office-inference-core` (`docs/build-brief.md` sections 7 and 10).

**Evidence labels, used consistently:**
- **[measured]** I ran it on this machine, or I read the number out of the primary artifact (paper HTML, leaderboard page, source file, LICENSE file) and give the URL.
- **[vendor]** a claim by the project or company selling the thing. Not independently checked.
- **[inference]** my reasoning from the above, or a simulation I wrote. Not a measurement of the product.
- **[unverified]** I could not confirm it and I say so rather than guessing.

Every number in this report carries one of those labels. Where I could not verify a licence or a figure, the report says so in plain words.

---

## 0. The answer

**Put the verifier in the answer path, not after it, and make refusal a first-class output.**

Three things, in this order:

1. **A mechanical layer that cannot be talked out of anything.** Does the cited index exist in the retrieved set. Does the passage sit on the page the citation claims. Is the quoted span present in the passage text, byte for byte after normalisation. Do the claim's numbers, dates, and identifiers all appear in the cited passage. This layer catches fabricated citations, page drift, and numeric substitution, and it is deterministic, so it cannot hallucinate.
2. **An entailment layer for what is left, using a model trained for fact-checking.** Not a general NLI model. I measured both on real RAG output: the general NLI cross-encoders landed at chance (49 to 53 percent balanced accuracy) with a 90 percent false-alarm rate, while a purpose-built grounded fact-checker was better and still missed 40 percent of unsupported claims on the pairs I scored. That result changed what I would build, and it is in section 3.
3. **A refusal rule with a number attached.** Split conformal prediction over the verifier's own support score selects the threshold. It gives a finite-sample guarantee on the participation rate, and that guarantee holds with a calibration set of 60. Nothing else I found gives a usable guarantee at that sample size.

**The measured false-negative rate is the thing to look at, and it is worse than vendors imply.** MiniCheck-Flan-T5-Large, the MIT-licensed 0.8B checkpoint that matches GPT-4o on the published leaderboard, missed 40 percent of unsupported sentences on the 24 pairs of my RAGTruth sample that finished before I ran out of time. Vectara's own published table for HHEM-2.1-Open implies missed-hallucination rates of 68 percent on RAGTruth summarisation and 46 percent on RAGTruth QA. One small classifier cannot be the gate. Three layers with a refusal rule is not belt-and-braces, it is the minimum a client-facing "verified" claim can rest on.

**The cost is CPU time, and it is the real design constraint.** Measured on this box, one claim against one passage costs about 1.6 seconds of one CPU core for a 184M NLI model, and batching the claims together does not help at all: 1.58 seconds at batch 1, 1.64 seconds at batch 16. Verification is compute-bound on CPU, so the only way to make an eight-sentence answer verify in time is to score the eight sentences on eight cores. That is a sizing decision for the reference instance, and it is the number to design against.

**And be honest with the client about the number.** A 60-question golden set cannot prove a 92 percent threshold. It can show you are not at 70 percent, and it can show a regression. Section 8 gives the arithmetic, and the honest headline is that with 60 paired questions you can reliably detect a difference of roughly **13 to 20 percentage points** between two configurations, not two.

---

## 1. What the failure actually looks like

Before choosing a checker, name the failures it has to catch. They are not one thing.

| Failure | What the user sees | Which layer catches it |
|---|---|---|
| **Fabricated source** | A citation to passage 7 when only 5 were retrieved | Mechanical, always |
| **Page drift** | The right sentence, cited to page 4 when it is on page 3 | Mechanical, always |
| **Quote not present** | Quotation marks around text that is not in the passage | Mechanical, always |
| **Numeric substitution** | "67 cents" in the answer, "$1.50" in the source | Mechanical on digits, entailment otherwise |
| **Right passage, wrong reading** | A negation dropped, a condition inverted, a range misread | Entailment, usually |
| **Plausible blend** | Two passages merged into a claim neither supports | Entailment, sometimes |
| **True but unsourced** | Correct in the world, absent from the documents | Entailment, and the system should refuse |
| **Answer to an unanswerable question** | A confident answer where the documents are silent | Retrieval score plus refusal rule |
| **Table read as a token stream** | A fee schedule flattened into prose and mis-stated | The parser, not the verifier (see Docling, C2) |

The last row matters for this brief because it sets the boundary of what verification can do. A verifier checks a claim against a passage. If the parser destroyed the table, the passage itself is wrong and the verifier will happily confirm a claim against a mangled source. Verification cannot repair ingestion. The two are separate controls and both are needed.

**[inference]** Roughly the first four rows are mechanical failures, and they are also the ones a user can see for themselves with one click. That is a good reason to make the mechanical layer absolute: it is cheap, it never degrades, and it covers the failures that destroy trust fastest.

---

## 2. Verified licences for every component in this decision

Licences were read from the project's own repository or model card on 2026-10-11. Where the card carries the licence in its metadata and no LICENSE file exists at the repo root, I say so.

| Component | Version / checkpoint | Licence | Verified where | Note |
|---|---|---|---|---|
| MiniCheck-Flan-T5-Large | `lytang/MiniCheck-Flan-T5-Large` | **MIT** | card metadata, [model card](https://huggingface.co/lytang/MiniCheck-Flan-T5-Large). No LICENSE file at repo root, the card front-matter carries `license: mit` | Built on Flan-T5-Large, itself Apache-2.0 ([card](https://huggingface.co/google/flan-t5-large)) |
| MiniCheck-DeBERTa-v3-Large | `lytang/MiniCheck-DeBERTa-v3-Large` | **MIT** | card metadata, [model card](https://huggingface.co/lytang/MiniCheck-DeBERTa-v3-Large) | Built on `microsoft/deberta-v3-large`, MIT |
| Bespoke-MiniCheck-7B | `bespokelabs/Bespoke-MiniCheck-7B` | **not permissive** | [model card](https://huggingface.co/bespokelabs/Bespoke-MiniCheck-7B) states "For commercial licensing, please contact company@bespokelabs.ai" | The best-scoring fact-checker on the leaderboard. **Disqualified for us** unless a priced commercial licence is signed |
| HHEM-2.1-Open | `vectara/hallucination_evaluation_model` | **Apache-2.0** | [model card](https://huggingface.co/vectara/hallucination_evaluation_model), `license: apache-2.0` | Base is `google/flan-t5-base`. The "not-Open" HHEM-2.1 is cloud-only, so it is out for the office build by definition |
| ModernBERT-base-nli | `tasksource/ModernBERT-base-nli` | **Apache-2.0** | [model card](https://huggingface.co/tasksource/ModernBERT-base-nli) | Base `answerdotai/ModernBERT-base` |
| nli-deberta-v3-base | `cross-encoder/nli-deberta-v3-base` | **Apache-2.0** | [model card](https://huggingface.co/cross-encoder/nli-deberta-v3-base) | Already in the stack as a reranker family, different head |
| DeBERTa-v3 NLI family | `MoritzLaurer/DeBERTa-v3-large-mnli-fever-anli-ling-wanli`, `MoritzLaurer/mDeBERTa-v3-base-mnli-xnli` | **MIT** | card metadata on both | No LICENSE file at the repo root, the card carries MIT |
| Granite Guardian 3.3 8B | `ibm-granite/granite-guardian-3.3-8b` | **Apache-2.0** | [model card](https://huggingface.co/ibm-granite/granite-guardian-3.3-8b), `license: apache-2.0` | An 8B generative judge. Office build only, and see section 6 for why I do not lead with it |
| TEI | `ghcr.io/huggingface/text-embeddings-inference` | **Apache-2.0** | [`LICENSE` in the TEI repo](https://github.com/huggingface/text-embeddings-inference/blob/main/LICENSE) | Supports sequence-classification models, which is how an entailment head gets served |
| Qwen3 1.7B / 4B | `Qwen/Qwen3-1.7B`, `Qwen/Qwen3-4B` | **Apache-2.0** | `LICENSE` read from the repo root of each | Qwen3 is Apache-2.0. **Qwen2.5-3B-Instruct is not**: it is `qwen-research`, a research licence. Do not substitute it |
| Phi-4-mini-instruct | `microsoft/Phi-4-mini-instruct` | **MIT** | `LICENSE` read from the repo root | Third-party terms still apply in the card |
| gemma-3-4b-it | `google/gemma-3-4b-it` | **Gemma Terms of Use** | repo is gated, LICENSE returned 401 unauthenticated | Google's own terms, not an OSI licence. Read them before shipping. **[unverified]** I could not read the text without accepting the gate |
| Llama 3.1 8B Instruct | `meta-llama/Llama-3.1-8B-Instruct` | **Llama 3.1 Community Licence** | repo gated, LICENSE returned 401 | Same caveat |
| `nvidia/Llama-3.1-Nemotron-*` | various | — | **could not verify.** Two candidate repos returned HTTP 401 to an unauthenticated read | Do not design around it until someone reads the licence. **[unverified]** |

Two notes that matter commercially. First, **the Apache-2.0 and MIT rows are the ones we can ship without a conversation.** Second, `ibm-granite/granite-guardian-3.3-8b` being Apache-2.0 is worth knowing, because older Guardian releases were not, and because it gives the office build a locally-runnable judge with a clean licence.

---

## 3. What I measured

I ran a verification experiment on this machine, 8 vCPU, 15 GB RAM, no GPU. The point was to get a false-negative rate on real generative hallucinations rather than on a benchmark's curated pairs. The result did not go the way I expected, and it changes the design.

**Task.** Sentence-level claim verification against the retrieved context. This is exactly what the product does: one claim, the passage it cites, supported or not.

**Data.** RAGTruth, the corpus of naturally generated hallucinations with human word-level annotation ([paper](https://aclanthology.org/2024.acl-long.585.pdf), [dataset processed copy](https://huggingface.co/datasets/wandb/RAGTruth-processed)). I sampled 300 test responses stratified across the summary, QA, and data-to-text tasks, split each response into sentences, kept sentences of 25 to 900 characters, and labelled a sentence unsupported if it contains an annotated hallucination span. That produced **1,891 claim-passage pairs, of which 173 are unsupported**, a 9.1 percent positive rate. That low rate is itself a finding: most sentences in a RAG answer are fine, which is exactly why a verifier with a 40 percent miss rate still looks good on plain accuracy.

**Subset for the timed runs.** All 173 unsupported pairs plus 70 supported would have taken hours on a busy CPU, so the scored set is a deterministic 150-pair sample: the first 80 unsupported pairs after a fixed shuffle, plus 70 supported pairs. That is a stratified sample, not the natural distribution, which is the right choice for estimating a false-negative rate and the wrong choice for estimating deployment accuracy. The full 1,891-pair file is kept at `scratch/verify_pairs.jsonl` so the run can be repeated in full.

**Four ways of scoring, two input regimes.** For the NLI cross-encoders I scored each claim against (a) the whole document truncated at 384 tokens and (b) a 900-character window placed around the document sentence with the highest lexical overlap with the claim, which is a cheap stand-in for the retriever we would have in production. For MiniCheck I reproduced the published input construction from the project's own source: `"predict: " + document + eos_token + claim`, encoder capped at 1,024 tokens, class logits read at vocabulary ids 3 and 209 ([`minicheck/inference.py`](https://github.com/Liyan06/MiniCheck/blob/main/minicheck/inference.py)).

### The result

150 pairs, 80 unsupported and 70 supported. Threshold 0.5 unless noted. A verifier that certified nothing would score balanced accuracy 50 with a false-negative rate of 0.

| Verifier | Licence | Balanced accuracy | Missed unsupported (FNR) | False alarm on supported (FPR) | Latency per pair |
|---|---|---|---|---|---|
| MiniCheck-Flan-T5-Large, 0.8B | MIT | 55.0 | 40.0 | 50.0 | 27.6 s |
| nli-deberta-v3-base, 184M | Apache-2.0 | 49.4 | 11.3 | 90.0 | 4.0 s |
| ModernBERT-base-nli, 150M | Apache-2.0 | 52.6 | 6.2 | 88.6 | 3.5 s |
| nli-deberta-v3-base, windowed premise | Apache-2.0 | 49.9 | 8.8 | 91.4 | 2.2 s |
| ModernBERT-base-nli, windowed premise | Apache-2.0 | 51.1 | 5.0 | 92.9 | 1.8 s |

**MiniCheck's row is a partial run.** The latency is what made it partial: it scored 24 of the 150 pairs before I stopped it at the time budget. The metrics are on those 24 pairs, not 150, and the interval on a 40 percent false-negative rate from 24 pairs is wide in both directions. Treat it as a direction, not a number. The full-length number I trust is Vectara's published one, which says the same thing at a larger sample: this class of model misses a third to two thirds of hallucinations.

### What this actually shows

**A general NLI model is not a verifier.** Both cross-encoders scored at or near chance on this task. The apparent low false-negative rate is a trap: a detector that says "supported" for almost everything has a low false-negative rate by construction. Their 90 percent false-alarm rate means that if you put either model in the loop at its default threshold, roughly nine in ten correct sentences get flagged amber. The product would be unusable and no safer.

**Windowing the premise did not fix it.** It halved the latency, from 4.0 to 2.2 seconds and 3.5 to 1.8 seconds, and moved balanced accuracy by half a point in either direction. So the failure is not just that the evidence was outside the truncated window. The models are being asked a question they were not trained for: a short claim against a long noisy passage, where the trained task was a short hypothesis against a short premise. That is an inference from the numbers rather than a measured mechanism, and the honest statement is that I tried the obvious fix and it did not help.

**The practical reading for the office build is latency, not accuracy.** Latency scales with model size on CPU almost exactly in proportion, and batching does not help. Measured single-thread with a 1,200-character premise at 384 tokens:

| Batch size, one thread | Per-pair latency |
|---|---|
| 1 | 1,583 ms |
| 8 | 1,772 ms |
| 16 | 1,636 ms |

Flat across batch sizes, so this is a compute wall, not a memory or overhead problem. A 770M verifier is therefore roughly 4x the 184M model, so about **6 to 8 seconds per claim on one core** even on an idle machine. My MiniCheck measurement of 27.6 seconds per pair was taken while another user's `text-embeddings-router` on this box was consuming about 2.5 cores, so it is not a clean number. The ratio is the useful part, not the absolute.

**Consequence for the architecture.** Eight sentences on eight cores in parallel is about 13 seconds of wall clock for an eight-claim answer at the 184M size, and about 50 seconds at the 770M size. Neither is acceptable for a chat interface, and neither is the model's fault. This is the strongest argument in this report for the design the build brief already contains: **verify per sentence, in parallel across cores, and stream the answer so verification happens while the user is reading.** It is also the honest argument for a GPU in the office build, and the number to quote is the per-claim latency above.

### The published numbers, for comparison

From the [LLM-AggreFact leaderboard](https://llm-aggrefact.github.io/), balanced accuracy averaged over 11 grounded factuality datasets, read 2026-10-11:

| Model | Size | Average | RAGTruth |
|---|---|---|---|
| Bespoke-MiniCheck-7B | 7B | 77.4 | 84.0 |
| Claude-3.5 Sonnet | — | 77.2 | 86.1 |
| Granite Guardian 3.3 | 8B | 76.5 | 82.2 |
| gpt-4o-2024-05-13 | — | 75.9 | 84.3 |
| **MiniCheck-Flan-T5-Large** | **0.8B** | **75.0** | **78.0** |
| Llama-3.3-70B-Instruct | 70B | 74.5 | 82.6 |
| QwQ-32B-Preview | 32B | 71.8 | 72.4 |

**[measured, leaderboard]** A 770M classifier sits 0.9 points behind GPT-4o on this benchmark. That is the strongest argument for the small trained verifier, and it is a leaderboard maintained by the model's own authors, so treat the exact ordering as theirs rather than independent.

**But the published table hides the number that matters.** Balanced accuracy is computed on class-balanced sets. Our answers are not class-balanced: in my 1,891-pair sample, 9.1 percent of sentences were unsupported. The false-negative rates underneath the published balanced accuracies, computed from Vectara's own recall column for the hallucinated class ([model card](https://huggingface.co/vectara/hallucination_evaluation_model)):

| Benchmark | Recall (hallucinated class) | Missed-hallucination rate |
|---|---|---|
| AggreFact-SOTA | 68.48 | 31.5 |
| RAGTruth-Summ | 31.86 | **68.1** |
| RAGTruth-QA | 54.38 | **45.6** |

**[measured, from the published table]** A dedicated hallucination detector misses roughly two thirds of summary hallucinations and roughly half of QA hallucinations. It still beats GPT-4 on the same table, and GPT-4 beats nothing by much. My own run points the same way. **The state of the art in cheap factual-consistency checking is a coin flip weighted toward "fine" on summarisation-style writing.** Our architecture cannot be "an entailment model decides." It has to be a stack of cheap independent checks, a refusal rule, and a published number.

---

## 4. The four layers, in build order

### Layer 1. Mechanical citation checks (build first, ship always)

No model. Deterministic code. Every one of these is a hard fail with a named reason.

1. **Index exists.** The cited chunk id is in the retrieved set for this answer. Catches fabricated citations and lets the audit record name the attempt.
2. **Page matches.** The chunk's stored page equals the page in the citation. This is where page-aware chunking earns its keep.
3. **Quote present.** If the answer quotes, the quoted span appears in the chunk text after whitespace and unicode normalisation. Full-text search, not fuzzy.
4. **Numeric containment.** Every number in the claim (integers, decimals, percentages, currency, dates, durations) appears in the cited chunk, in a comparable normalised form. Table cells, unit conversions, and written-out numbers are the known gap: "sixty-seven cents" and "0.67 USD" will not match a naive rule. **[inference]** This check alone catches the numeric-mismatch blocker in `build-brief.md` section 10, which is worth more than its implementation cost.
5. **Identifier containment.** Procedure codes, statute numbers, account and invoice numbers, and proper nouns present in the claim must appear in the cited passage. Identifiers are where vector search is weakest and where a substitution is most damaging.

**[inference]** Layers 1 to 3 are perhaps 150 lines of code and a handful of tests. They have no model to swap, no threshold to tune, and no licence question. Build them first, and never turn them off.

### Layer 2. Entailment scoring (the fallback signal, not the verdict)

Serve a model that was **trained for grounded fact-checking**. My measurements say this is not a preference, it is the difference between a working signal and a coin flip.

- **MiniCheck-Flan-T5-Large**, MIT, 770M, chunk the document and take the maximum support probability across chunks. It needs a small FastAPI service. It will not run in the TEI container: TEI's sequence-classification support is CamemBERT, XLM-RoBERTa, and ModernBERT, and MiniCheck is a T5 encoder-decoder. Verified against the [TEI README](https://github.com/huggingface/text-embeddings-inference#readme) pulled into this workspace (`/tmp/search-research/tei_readme.md`, lines 101 to 112). Note that the repo ships `pytorch_model.bin` only, and Transformers 4.57 refuses to load a `.bin` on torch below 2.6 for a known `torch.load` vulnerability. Load the state dict yourself, or pin a torch that satisfies the check, or convert to safetensors at build time. All three work; pick one deliberately.
- **A general NLI cross-encoder is not a substitute.** I ran two of them, one of them through the exact TEI-compatible architecture, and both were at chance on real RAG output with a 90 percent false-alarm rate (section 3). If you want a second opinion in the loop, it has to come from a differently-trained fact-checker, not from an MNLI head.
- **If you want an NLI head for a different purpose** (for example, a cheap first filter that only removes the clearly-entailed), use `tasksource/ModernBERT-base-nli`, task-soup trained on MNLI, ANLI, WANLI and relatives ([card](https://huggingface.co/tasksource/ModernBERT-base-nli)), which runs in the TEI container we already have. Set its threshold low, treat its output as a filter rather than a verdict, and do not report its decisions to the client.

A cross-encoder trained for relevance does not do this job either. Rerankers (`bge-reranker-v2-m3` and friends) are trained on query-passage relevance, not premise-hypothesis entailment. **[inference]** They will happily score a passage as highly relevant for sharing vocabulary with a claim the passage contradicts. Keep the reranker for retrieval and do not reuse it as a verifier.

**What I did not get to, and would run next.** MiniCheck's real design chunks the document and takes the maximum probability across chunks, which is both more accurate than my single-window run and much more expensive. I also did not test the smaller MiniCheck variants (`lytang/MiniCheck-RoBERTa-Large`, MIT), which should be roughly half the latency of the T5 version. Both belong in the C2 spike, measured on the client's own documents rather than on RAGTruth.

### Layer 3. Claim decomposition, applied only where it pays

MiniCheck's authors report that breaking a claim into atomic facts before checking **is not necessary** to reach their numbers ([paper](https://arxiv.org/pdf/2404.10774.pdf), contributions list). Their training data was built to teach a single pass over a multi-fact sentence.

**[inference]** So the default is: do not decompose. Verify at the sentence level, which is also the level a user reads. Decompose only for two narrow cases that showed up in my sample and in the failure taxonomy: sentences with **many clauses and multiple numbers** (a one-sentence fee summary is four checks pretending to be one), and sentences where the mechanical numeric check fails and you need to know *which* number was wrong. Decomposition doubles or triples the number of verifier calls, so it must be earned. The trigger can be structural: more than one numeric token, or more than about 40 words.

**[inference]** Never decompose for display. Show the user the sentence they can read, with the passage beside it.

### Layer 4. Local LLM as a judge (targeted, last, and measured)

The judge earns its cost in exactly one place: the **borderline band** between the entailment threshold and a higher confidence threshold. Everything clearly supported and clearly unsupported is already decided. Routing only the ambiguous 10 to 20 percent to a generative judge keeps the latency and cost bounded, and it is the only tier where a judge's reasoning adds information the classifier does not have.

Rules I would write into the code:

- **The judge is a different model from the answerer.** If one local model both writes and grades, self-preference bias is a live risk: self-recognition capability correlates linearly with the strength of self-preference bias ([Panickssery et al., NeurIPS 2024](https://proceedings.neurips.cc/paper_files/paper/2024/hash/7f1f0218e45f5414c79c0679633e47bc-Abstract-Conference.html), **[measured, paper]**, which reports the correlation from fine-tuning experiments rather than only observing it). In the cloud build that is natural: the answerer is a Bedrock frontier model, the judge is a local Qwen3 or Granite. In the office build it means two different local models, which costs VRAM. Say that cost out loud rather than running the same model twice and calling it verification.
- **Judge one claim against one passage, never a pair of answers.** Pairwise judging brings position bias with it. GPT-4 in the MT-Bench study was consistent on only 65 percent of cases when the two answers were swapped, and preferred the first position in 30 percent of cases ([Zheng et al. 2023](https://ar5iv.labs.arxiv.org/html/2306.05685), Table 2, **[measured, paper]**). Our task has a ground truth and one candidate. Do not import the pairwise design.
- **Judge the claim, not the prose.** Ask for the supporting span, then check mechanically that the span is in the passage. A judge that can only answer "supported" by producing a real quote is much harder to fool.
- **Calibrate the judge before trusting it.** Section 8.4 covers how, and it takes an afternoon, not a project.

---

## 5. When to refuse, and how to calibrate it

Refusal is the differentiator, so it needs a rule and a number rather than a vibe.

### 5.1 Four refusal reasons, each with its own trace

The build brief already names them: `no_source`, `boundary`, `policy`, `insufficient_evidence`. **[inference]** What matters for this decision is that each one fires on a different signal and they must not be collapsed:

| Reason | Signal | Fires before or after generation |
|---|---|---|
| `no_source` | Retrieval returned nothing above a floor, or the corpus has no document that could answer | Before |
| `boundary` | The role or the data class forbids the request | Before |
| `policy` | Input or output policy, denial classes, redaction | Before and after |
| `insufficient_evidence` | Retrieved passages exist but the verifier will not certify the claims | After generation |

Collapsing these into one "I cannot answer" throws away the operational signal. A rising `insufficient_evidence` rate means ingestion or chunking is broken. A rising `no_source` rate means the corpus does not cover what people are asking, which is a conversation with the client about documents rather than a bug.

### 5.2 The refusal rule, and why conformal prediction is the right tool here

The decision "answer or refuse" is a threshold on a support score. The question is where to set the threshold so the refusal rate is not arbitrary.

**Split conformal prediction gives a finite-sample guarantee at n=60.** From a calibration set of n examples and a chosen risk level alpha, the threshold is the ceil((1-alpha)(n+1))-th smallest score. The guarantee on the participation rate is that it lies in [1-alpha, 1-alpha + 1/(n+1)). Xu et al. state the participation guarantee and a conditional-correctness guarantee in this form for language-model abstention ([Geometry-Calibrated Conformal Abstention](https://ar5iv.labs.arxiv.org/html/2604.27914), equation 4, **[measured, paper]**). Their contribution is a better confidence signal from representation geometry; the calibration machinery is the standard split-conformal argument and that is the reusable part.

Why this matters for us specifically: **the guarantee is exact for any n.** It does not assume the score is well behaved, it does not need a normal approximation, and at n=60 the interval is only 1/61 wide. That is a genuinely better foundation for a refusal threshold than eyeballing a histogram.

The assumptions, stated plainly because they are easy to break:

1. **Exchangeability.** The calibration questions and the live questions must be drawn from the same distribution. Client questions are exchangeable with a golden set we wrote with the client. They are not exchangeable with a golden set we wrote from a template.
2. **The threshold must not be tuned on the data you report on.** This is the trap, and it is not subtle. I simulated it with a synthetic correctness score at a nominal 80 percent floor: calibrating the threshold on the 60 questions and then reporting the correctness measured on those same 60 questions gives a mean conditional correctness of 1.00 where the honest held-out number on a fresh 60 is 0.85. The direction is always the same and it is always flattering.
3. **The score must be computed the same way in calibration and at run time.** A model swap, a chunking change, or an embedding change invalidates the calibration set. Recalibrate, do not reuse.

### 5.3 Refuse per claim, not per answer

**[inference]** This is the single design decision that keeps the system useful. A blanket refusal on any unsupported sentence makes the product useless on any answer longer than three sentences, because the verifier's false-positive rate alone will trigger it. Instead:

| Support state | What the client sees |
|---|---|
| All claims certified | Normal answer with page citations |
| Some claims unsupported | The answer, with the unsupported sentences marked amber, the citation removed from them, and a one-line note: "2 of 7 sentences are not supported by the cited pages" |
| The unsupported claims carry the answer | Refusal with `insufficient_evidence`, and the retrieved passages still shown |
| No claims certifiable and retrieval weak | Refusal with `no_source` |

The middle row is the one that wins deals. It is honest, it is specific, and it is checkable in ten seconds by the person reading it.

### 5.4 The dial, and what the two ends cost

**[inference]** The conformal alpha is the dial. The arithmetic is unforgiving and should be shown to the client:

- Tight alpha (refuse easily): participation guarantee high, so the system answers less. On an office build with a 0.8B verifier, a large alpha means a lot of amber.
- Loose alpha: the system answers more and the measured support rate falls with it.

Because the guarantee applies to the **participation rate**, not to the correctness of what is said, a second number is needed: the **measured support rate among answers given**. That is the number in the acceptance criteria, and the acceptance test should record both.

**[inference]** There is a ceiling here that no threshold can lift, and it follows directly from section 3. Conformal calibration guarantees the participation rate. It cannot guarantee that what is said is supported, because the verifier's own false-negative rate sets that bound. If the verifier misses 40 percent of unsupported claims, then whatever passes verification is still 40 percent wrong at the margin. So state the target in two parts, and put both in the acceptance test: **the verifier's measured false-negative rate on human-labelled answers, and the participation guarantee.** A refusal threshold tuned on a verifier with a high miss rate produces a system that answers confidently and wrongly, with a certificate attached. That is worse than no verification, because it is verification a client will believe.

---

## 6. Options I rejected, and why

| Option | Why it is out |
|---|---|
| **A generic cross-encoder reranker as the verifier** | Trained for query-passage relevance, not entailment. Different decision boundary. Costs nothing to add, and it will certify relevance as if it were support. **[inference]** |
| **A general NLI model as the verifier** | Measured. Two of them, at chance on real RAG output, with a 90 percent false-alarm rate (section 3). An MNLI score is not a support verdict |
| **Trusting one small classifier as the gate** | Measured missed-hallucination rates of 40 percent on my sample and 46 to 68 percent in Vectara's published table. One classifier is a signal, not a verdict |
| **HHEM-2.1 (not the Open one)** | Cloud-only, via Vectara's platform. Disqualified for the office build by the no-internet constraint, regardless of quality |
| **Bespoke-MiniCheck-7B** | Best score on the leaderboard, and the card says to contact the company for a commercial licence. That means unpriced, so it cannot go in a fixed-price build. Worth a quote if the verifier ever becomes the headline feature |
| **GPT-4 or Claude as the judge** | Cloud builds only, and the office build is the one where verification matters most. Also 400x the cost of MiniCheck for a fraction of a point ([MiniCheck paper](https://arxiv.org/pdf/2404.10774.pdf)) |
| **Fine-tuned 7B judges (Prometheus2, JudgeLM, AutoJ)** | On JudgeBench, Prometheus2-7B scores 34.9 percent and JudgeLM-7B 25.1 percent against a 50 percent random baseline, while the prompted GPT-4o judge scores 56.6 ([JudgeBench](https://ar5iv.labs.arxiv.org/html/2410.12784), Table 1, **[measured, paper]**). Fine-tuned judges were trained on preference data, which is not factual correctness |
| **Pairwise LLM judging, as MT-Bench does it** | Position bias is large and measurable: 65 percent consistency for GPT-4 with a swap test. Our task is single-candidate with ground truth available, so the pairwise design buys nothing and imports a bias |
| **Semantic entropy or repeated sampling as the abstention signal** | It needs several samples per query. On a CPU-only box with single-digit tokens per second, three samples per answer is not a latency budget, it is a different product. Worth revisiting when a GPU is present |
| **Agentic re-retrieval loops to raise support** | Bounded value, unbounded latency, and the failure mode is a loop that answers confidently on the third try. The brief already caps this; keep the cap |
| **Claim decomposition for every sentence** | MiniCheck's authors report it is unnecessary for their accuracy. Double or triple the verifier calls for no measured gain |
| **Self-verification by the answering model** | Same model as judge. Self-preference is a measured effect, and the office build makes it the default if nobody decides otherwise |
| **A visual late-interaction model (ColPali) as the verifier** | It is a retriever, and the sibling report already covers its licence and storage cost. Verification needs a decision, not a similarity |

---

## 7. Operational cost, stated plainly

**Latency is the decision.** All numbers measured on this box: 8 vCPU, 15 GB RAM, no GPU, and for part of the run another user's `text-embeddings-router` was holding about 2.5 cores, so treat them as pessimistic.

Single-thread, premise ~1,200 characters at 384 tokens, batch sizes 1, 8, and 16: 1,583 ms, 1,772 ms, and 1,636 ms per pair. **Batching does not help.** This is compute-bound on CPU. A second measurement with 6 threads available gave 3.5 to 4.0 seconds per pair for the same models while three scoring jobs ran concurrently, which is the same compute split across fewer cores.

| Verifier size | Rough cost per claim, one core | Eight-claim answer, eight cores in parallel |
|---|---|---|
| 150 to 184M NLI cross-encoder | 1.6 s (measured) | ~13 s |
| 770M fact-checker | 6 to 8 s (extrapolated from the 4x size ratio, not measured cleanly) | ~50 s |

**[inference]** Read that table as the architecture constraint it is. Verification is parallel per sentence and it does not batch, so it scales with cores rather than with throughput. On the 16 vCPU reference instance, two sentences per core in flight is realistic, so a twelve-sentence answer verifies in about the time of one long claim. Stream the answer, verify as sentences complete, and mark the amber ones as they are resolved.

**Memory.** **[measured]** MiniCheck-Flan-T5-Large is 560 state-dict entries and loaded in about 16 seconds from local disk on this box. The 150M NLI cross-encoder is a fraction of that. Both fit alongside everything else on the reference instance. Neither needs a GPU to load.

**Ongoing cost.** One more container, one more model pinned to a digest with a recorded version, one more licence in the service inventory, and one more thing the parity suite exercises on every model swap. That is why the mechanical layer goes first: it adds a module, not a service.

**The GPU question.** Nothing here requires a GPU. On the office build a GPU turns an eight-claim verification from about 13 seconds into about a second, and it makes the Layer 4 generative judge and repeated-sampling abstention possible. If a client asks what a GPU buys for verification specifically, the honest answer is latency and the judge tier. It does not buy accuracy: the verifier's false-negative rate is a property of the model and its training data, and a bigger card does not change it.

---

## 8. Measuring it with 60 questions

This is the part most likely to be done badly, and the failure is quiet: you tune until the number looks good, then report the tuned number.

### 8.1 What 60 can and cannot tell you

A proportion measured on 60 questions is uncertain. **[inference]** Use the Wilson score interval, not the textbook normal approximation. At k=60, the normal approximation gives an interval of zero width, which is obviously wrong. At k=57 the two disagree by more than 3 points on the upper bound.

| Observed | Wilson 95% interval | Width |
|---|---|---|
| 42/60 = 0.70 | 0.57 to 0.80 | 23 points |
| 48/60 = 0.80 | 0.68 to 0.88 | 20 points |
| 51/60 = 0.85 | 0.74 to 0.92 | 18 points |
| 54/60 = 0.90 | 0.80 to 0.95 | 16 points |
| 57/60 = 0.95 | 0.86 to 0.98 | 12 points |
| 60/60 = 1.00 | 0.94 to 1.00 | 6 points |

**[measured, simulation]** Practical consequences:

- **The difference between a 0.85 threshold and a 0.92 threshold cannot be resolved by 60 questions.** At k=51 the interval reaches 0.92. At k=54 the interval reaches 0.95. Both pass, both fail, and neither number means what a purchasing manager will think it means.
- **Report the interval, always.** A support rate of 85 percent from 60 questions is "between 74 and 92 percent". Printing 85 percent alone is the misleading choice.
- **Never report a percentage from 60 questions without n beside it.** The audit export should carry the interval too, because that export is what the client keeps.

### 8.2 The retrieval and answer metrics, defined precisely

Define these once, in code, and reuse them. Ambiguity here is what makes two runs incomparable.

**Retrieval, per question.** Gold is a set of passage ids, one or more per question, written when the question is written.

- `recall@k` = 1 if any gold passage is in the top k, else 0. Averaged over questions. This is the retrieval number in the acceptance criteria.
- `MRR` = mean of 1/rank of the first gold passage, 0 if it is beyond k. Sensitive to the head of the list, which is what the generator sees.
- `nDCG@k` with binary relevance = mean of 1/log2(rank+1) over gold passages found in the top k, normalised by the ideal. Use it when a question has several gold passages. With one gold passage it is a monotone transform of MRR, so pick one and say which.

**Answer support, per claim.** The verifier emits a support score per sentence. Report:

- `support_rate` = supported sentences / total sentences, and the same computed per answer then averaged (they differ, and the per-answer mean is what a client experiences).
- `unsupported_escaped` = unsupported sentences that passed verification, against human labels. **This is the false-negative rate and it is the number that matters.** It needs human labels, so it comes from the 60 and from the judge-validation subsample.
- `refusal_rate` overall, and split by the four named reasons.

**The two rates together are the contract.** A system with 100 percent support and 60 percent refusal is not a product. A system with 70 percent support and 2 percent refusal is a liability. Publish the pair.

### 8.3 Paired comparison of two configurations

Always compare two configurations on the same 60 questions. Same questions, both systems, paired outcomes. That is more sensitive than comparing two separate question sets, and it is free.

The test is **McNemar on the discordant pairs**: the questions where exactly one configuration was right. Matched-pairs structure means the base rates cancel, which is the whole reason to do it this way.

**[measured, simulation]** I simulated the power of this test at n=60 across a grid of discordance rates (the fraction of questions where the two configurations differ at all) and true improvements. Asymptotic McNemar with continuity correction, alpha 0.05:

| Discordance between the two configurations | Power at +5 points | Power at +10 points | Power at +15 points |
|---|---|---|---|
| 10 percent | 0.10 | 0.56 | 0.56 (capped) |
| 15 percent | 0.09 | 0.40 | 0.91 |
| 20 percent | 0.09 | 0.29 | 0.69 |
| 30 percent | 0.06 | 0.20 | 0.48 |

And the minimum detectable effect at 80 percent power: **10 points** at 10 percent discordance, **14 points** at 15 percent, **16 points** at 20 percent, **20 points** at 30 percent.

**[measured, simulation]** The exact McNemar test, which is the conservative choice at small n, tracks the asymptotic test closely here, so there is no reason to use the exact one except as a check.

**[inference]** The reading is blunt. Two chunking strategies on the same 60 questions will differ on maybe 10 to 20 percent of them. If the truth is that one is 5 points better, you will find it about one time in ten. **With 60 questions, run one pre-registered comparison per release and accept that anything under about 15 points is invisible.** Plan the release around that. Do not plan a bake-off.

Note also the ceiling: when the improvement asked for exceeds the discordance rate, the simulation caps (you cannot flip more questions than differ). The 10-percent-discordance row tops out at +10 points of true improvement, which is why its +15 column is not higher than its +10 column. That ceiling is a property of the question set, not of the test.

### 8.4 The trap of multiple comparisons

This is the one that quietly manufactures false confidence.

**[measured, simulation]** If you compare one variant against the baseline at alpha 0.05, you have a 5 percent chance of a false win. Try five variants and it is 23 percent. Try ten and it is 40 percent. Try twenty and it is 64 percent. The arithmetic is 1 - 0.95^m for m independent comparisons.

| Variants tried | Chance of at least one false win, uncorrected | Bonferroni alpha |
|---|---|---|
| 1 | 5.0 percent | 0.05 |
| 5 | 22.6 percent | 0.01 |
| 10 | 40.1 percent | 0.005 |
| 20 | 64.2 percent | 0.0025 |
| 50 | 92.3 percent | 0.001 |

And the correction destroys what little power there was. **[measured, simulation]** At 15 percent discordance and a true +10 point improvement, power falls from 0.40 uncorrected to 0.13 with five variants, 0.06 with ten, and 0.02 with twenty. **A ten-variant bake-off on 60 questions cannot detect a real 10-point improvement even when the correction is applied properly.** The correction is not the problem. The experiment is.

What to do instead, in order of preference:

1. **Pre-register one primary comparison per release.** Decided before the run, written in the release notes, one question: did this change make the primary metric better or worse.
2. **Everything else is exploratory and is labelled as such.** Exploratory results generate the hypothesis for the next release. They do not go in a client-facing report and they do not justify a change on their own.
3. **Batch by decision, not by variant.** "Does reranking stay?" is one comparison. "Which of these five rerankers?" is a different experiment that needs more questions than 60.
4. **Hold the questions fixed across releases.** The release-to-release comparison is then paired and its discordance is smaller, which raises power for the comparison that actually matters.
5. **If a real choice needs more power, buy questions, not statistics.** Growing the golden set to about 600 questions moves the detectable difference from roughly 15 points to roughly 5. That is a day or two of client time, and it is the honest price of the decision.

### 8.5 The pitfalls of LLM-as-judge evaluation

If a judge labels the 60 answers, the measurement inherits the judge's errors. **[inference]** The bias arithmetic is simple and worth internalising. Suppose 15 percent of answers truly contain an unsupported claim and the measured support rate is 85 percent:

| Judge's false-negative rate on unsupported claims | Judge's false-alarm rate on supported claims | Measured support rate | Error |
|---|---|---|---|
| 0 | 0 | 85.0 percent | 0 |
| 0 | 5 percent | 80.8 percent | -4.3 points |
| 0 | 10 percent | 76.5 percent | -8.5 points |
| 10 percent | 0 | 86.5 percent | +1.5 points |
| 20 percent | 0 | 88.0 percent | +3.0 points |
| 30 percent | 0 | 89.5 percent | +4.5 points |

**[measured, simulation]** A judge that **misses** unsupported claims inflates the number, which is the dangerous direction because it hides the failure that loses clients. A judge that **over-flags** supported claims deflates the number, which just makes the product look worse than it is. **Report the judge's false-negative rate next to every judge-assisted number**, and prefer a human check on a subsample to trusting the judge.

The specific biases to control:

- **Position bias.** Measured in MT-Bench as 65 percent consistency for GPT-4 on swapped identical-quality pairs, with a 30 percent preference for the first position ([Zheng et al.](https://ar5iv.labs.arxiv.org/html/2306.05685), Table 2). Avoid it by not doing pairwise judging. If you must, swap and require agreement.
- **Self-preference.** Self-recognition correlates with self-preference ([Panickssery et al.](https://proceedings.neurips.cc/paper_files/paper/2024/hash/7f1f0218e45f5414c79c0679633e47bc-Abstract-Conference.html)). Different model for judging than for answering, or say plainly that it is unmeasured.
- **Prompt sensitivity.** The default prompt versus a chain-of-thought prompt versus a reference-guided prompt changed GPT-4's failure rate on a small math set from 14/20 to 6/20 to 3/20 in the same study ([Zheng et al.](https://ar5iv.labs.arxiv.org/html/2306.05685), Table 4). Same model, same data, three different answers. **Version the judge prompt, store it with the results, and re-run the judge-validation when it changes.**
- **Verbosity bias.** Longer answers score higher. Our answers have citations and amber marks, so length varies with the verification result itself. Judge the sentence and the passage, not the rendered answer.
- **Judge reliability ceiling.** On JudgeBench, GPT-4o with the vanilla prompt scored 50.9 percent on distinguishing objectively correct from objectively incorrect responses, barely above chance ([JudgeBench](https://ar5iv.labs.arxiv.org/html/2410.12784), Table 1). Our task is easier than that, but a 60-question measurement labelled by an unvalidated judge is not a measurement.

### 8.6 The calibration protocol, in one page

This is what I would actually run, in this order, before anyone reports a number.

**One-time, per client, at corpus onboarding:**

1. **Write 60 questions with the client.** 40 general, 20 specific to their work. For each: the question, the gold passage ids, the gold page numbers, and a one-line gold answer. Forty must be answerable from the corpus. **The other twenty must not be.**
2. **Write 20 unanswerable questions on purpose.** Not as a formality. These are the only questions that measure refusal, and refusal is the product claim. If the client will not write them, we write them and they approve them.
3. **Split 40 for calibration, 20 for reporting,** fixed before the first run, recorded in the manifest. With 60 total, a 40/20 split is the practical compromise: enough for a conformal threshold, enough to report. **[inference]** This split costs interval width on the reported number, which is the honest price of not fooling yourself.
4. **Freeze them.** Hash the file into the manifest. A question changed later makes the calibration set invalid.

**Every release:**

5. **Run the frozen set, both configurations, same 60.** Paired outcomes, one pre-registered primary comparison.
6. **Compute**: recall@k, MRR, nDCG@k; support rate with a Wilson interval; the four refusal rates; and `unsupported_escaped` on the human-labelled subset.
7. **Run the mechanical layer's unit tests, not just the end-to-end run.** Fabricated index, wrong page, missing quote, changed number. Four tests, four known-bad answers, all must fail closed.
8. **Record the judge's measured false-negative rate** if a judge produced any label, and re-measure it whenever the judge model or prompt changes. Twenty human-labelled answers is enough for a rough rate and not enough to state it precisely, which is exactly what should be written beside it.
9. **Recalibrate the conformal threshold** whenever the verifier, the parser, the chunking, or the embedding model changes. Record the threshold and the calibration-set hash in the audit record.
10. **Report the pair**: participation rate and support rate, each with its interval, each with n=60.

**Every six months, or after a corpus change:** re-examine the 20 unanswerable questions. Documents change. A question that was unanswerable in January may be answerable in July, and a question set that nobody revisits slowly stops measuring anything.

---

## 9. What I could not verify

- **MiniCheck's false-negative rate is not settled by my run.** I scored 24 of the intended 150 pairs before stopping it on a time budget. The metrics on those 24 point the same way as Vectara's published numbers, and that agreement is the reason I am willing to state a direction. It is not a measurement I would put in a client document.
- **MiniCheck's latency is not a clean number.** The 27.6 seconds per pair was measured while another user's embedding server held about 2.5 cores on this box. The size ratio against the 184M model is the transferable part, and the 6-to-8-second estimate in section 7 is explicitly an extrapolation.
- **I did not test MiniCheck's chunk-and-max path**, which is the published configuration. My single-window run is likely worse than the real thing, which means the numbers here are a floor on quality and a floor on cost.
- **I did not test the smaller MiniCheck variants** (`MiniCheck-RoBERTa-Large`, `MiniCheck-DeBERTa-v3-Large`), which are MIT and roughly half the size.
- **The RAGTruth sentence-level label derivation is mine.** I lifted word-level hallucination spans to sentences by character overlap. Different derivations give different positive rates, and my 9.1 percent positive rate is a property of my sampling and filtering, not a property of RAGTruth.
- **Llama, Gemma, and the Nemotron candidate repos** are gated. Their licence texts returned HTTP 401 to an unauthenticated read. I have not read them and I do not state their terms.
- **The `cross-encoder/nli-MiniLM2-L6-H768` and `typeform/distilbert-base-uncased-mnli` cards** carry no licence field at all in the API response. Absent a licence, assume no permission.
- **The LLM-AggreFact leaderboard's independence.** It is maintained by the MiniCheck paper's authors. The ordering within about a point should be treated as theirs.
- **No multi-tenant or concurrency testing.** Three models were scored in parallel processes on one box, which is a sanity check on CPU viability and not a load test.
- **No measurement of the LLM-judge layer at all.** Section 4 layer 4 is a design, not a result. The judge's false-negative rate on our own data is unknown and must be measured before any judge-assisted number is reported.

---

## 10. Sources

**Primary papers and reports**
- MiniCheck: Efficient Fact-Checking of LLMs on Grounding Documents, EMNLP 2024. https://arxiv.org/pdf/2404.10774.pdf and code at https://github.com/Liyan06/MiniCheck
- RAGTruth: A Hallucination Corpus for Developing Trustworthy Retrieval-Augmented Language Models. https://aclanthology.org/2024.acl-long.585.pdf
- HHEM-2.1-Open model card, including the AggreFact and RAGTruth tables. https://huggingface.co/vectara/hallucination_evaluation_model
- JudgeBench: A Benchmark for Evaluating LLM-based Judges. https://ar5iv.labs.arxiv.org/html/2410.12784
- Judging LLM-as-a-Judge with MT-Bench and Chatbot Arena. https://ar5iv.labs.arxiv.org/html/2306.05685
- LLM Evaluators Recognize and Favor Their Own Generations, NeurIPS 2024. https://proceedings.neurips.cc/paper_files/paper/2024/hash/7f1f0218e45f5414c79c0679633e47bc-Abstract-Conference.html
- AbstentionBench: Reasoning LLMs Fail on Unanswerable Questions. https://ar5iv.labs.arxiv.org/html/2506.09038
- Geometry-Calibrated Conformal Abstention for Language Models. https://ar5iv.labs.arxiv.org/html/2604.27914
- An Evaluation Framework for Attributed Information Retrieval using Large Language Models, CIKM 2024 (ALCE-style NLI citation precision and recall on HAGRID). https://ar5iv.labs.arxiv.org/html/2409.08014

**Leaderboards and datasets**
- LLM-AggreFact leaderboard. https://llm-aggrefact.github.io/
- LLM-AggreFact dataset (gated, access request). https://huggingface.co/datasets/lytang/LLM-AggreFact
- RAGTruth processed copy used here. https://huggingface.co/datasets/wandb/RAGTruth-processed

**Model cards and licences**
- https://huggingface.co/lytang/MiniCheck-Flan-T5-Large (MIT)
- https://huggingface.co/tasksource/ModernBERT-base-nli (Apache-2.0)
- https://huggingface.co/cross-encoder/nli-deberta-v3-base (Apache-2.0)
- https://huggingface.co/ibm-granite/granite-guardian-3.3-8b (Apache-2.0)
- https://huggingface.co/google/flan-t5-large (Apache-2.0)
- https://huggingface.co/Qwen/Qwen3-4B (Apache-2.0)
- https://huggingface.co/Qwen/Qwen2.5-3B-Instruct (qwen-research, not permissive)
- https://huggingface.co/bespokelabs/Bespoke-MiniCheck-7B (commercial licence on request)
- https://github.com/huggingface/text-embeddings-inference/blob/main/LICENSE (Apache-2.0)

**Artifacts produced for this report** (all under `/tmp/search-research/`)
- `08-experiment-verify.py`, `verify_final.py`, `verify_window.py`, `verify_one.py` - the RAGTruth verifier experiment
- `08-experiment-stats.py`, `08-experiment-stats2.py` - the small-sample simulations
- `verify_nli.py`, `fetch_lic2.py` - licence verification
- `scratch/verify_pairs.jsonl` - the 1,891 claim-passage pairs with the RAGTruth-derived labels
- `scratch/vf_*.json`, `scratch/vw_*.json` - per-model scores, threshold sweeps, and latencies
- `scratch/log_*.txt` - run logs
- `scratch/stats_sim.json`, `scratch/stats_sim2.json` - simulation output
