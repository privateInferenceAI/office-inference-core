# Can OCR confidence detect a wrong-but-plausible reading? The research literature

Research date 2026-10-12. Companion to `ocr-confidence-prior-art.md` in this directory, which
covers what shipped open-source stacks do. This file covers the papers.

Labels: **(a) documented** = stated by the source · **(b) code read** = read out of published
source · **(c) inference** = my reading, needs testing on your own data.

---

## 0. The direct answer

Can a confidence signal be made to detect wrong-but-plausible readings? Partly, and the part
it cannot do is the part that matters to you.

- Confidence is **good at ranking** and **bad at being a probability**. Every credible
  measurement I found agrees on this split.
- The one paper that directly tested "threshold Tesseract confidence, even at very high
  values" found residual errors and stated so in its abstract. That is your observation,
  published in 2009.
- Post-hoc calibration (temperature scaling, isotonic regression) **cannot** fix it, because
  calibration is a monotone rescaling of the score. It moves the numbers onto a probability
  axis and leaves the ranking, and therefore the error-detection power, untouched. Two
  independent 2026 sources say this explicitly.
- What actually detects a confidently-wrong digit is **redundancy that the defect cannot
  corrupt identically twice**: check digits, cross-field arithmetic, and independent re-reads
  that differ in stage.

A confidence score is a self-report. A check digit is evidence.

---

## 1. What Tesseract's confidence actually is

### 1.1 The formula, from source **(b) code read**

`LTRResultIterator::Confidence(level)` returns, for a word:

```
ClipToRange(100 + 5 * mean_certainty, 0.0, 100.0)
```

Source: <https://github.com/tesseract-ocr/tesseract/blob/main/src/ccmain/ltrresultiterator.cpp#L93>
(read at commit `b2544450ca01407259ed53c870b7e5e4c67f7789`)

So the 0-100 number you see in TSV/hOCR is `100 + 5*c` where `c` is the word certainty.

The doc comment on that function reads, verbatim:

> "Returns the mean confidence of the current object at the given level. The number should be
> interpreted as a percent probability. (0.0f-100.0f)"

That comment is Tesseract asserting the number is a percent probability. The code does not
make it one.

### 1.2 What `certainty` is defined to be **(b) code read**

```cpp
// Certainty is a number in [-20, 0] indicating the classifier certainty
// of the choice. In terms of probability, certainty = 20 (k log p) where
// k is defined as above to normalize -klog p to the range [0, 1].
float certainty_; // absolute
```

Source: <https://github.com/tesseract-ocr/tesseract/blob/main/src/ccstruct/ratngs.h#L213-L216>

Three things follow.

1. The range `[-20, 0]` is not arbitrary — it is `-certainty_scale`, and `certainty_scale`
   is a Tesseract parameter with default `20.0`:
   <https://github.com/tesseract-ocr/tesseract/blob/main/src/dict/dict.cpp#L106>
2. Because the confidence is `100 + 5*c` with `c ∈ [-20, 0]`, the mapping compresses the whole
   certainty range into 0-100 and **clips**. A word sitting at the `c = 0` ceiling and a word
   genuinely at `c = 0` are indistinguishable. The scale is a range convention, not a fitted
   calibration.
3. The `k` in `certainty = 20 (k log p)` is described as "defined as above", and the header
   text above it does not give a numeric value or an estimation procedure that I could locate.
   Whether `k` is a constant, a per-word quantity, or a fit is **unverified**. **(c) inference**:
   the structure reads like a per-blob length normalisation (`Rating *= rating_scale *
   Results->BlobLength` nearby), not a probability calibration. I could not confirm this.

### 1.3 The LSTM path reports differently **(b) code read**

If only LSTM `traineddata` is in use — which is the normal case for Tesseract 4/5 — the
per-symbol choice confidence is computed by a different expression:

```cpp
confidence = 100 - rating_coefficient_ * choice.second;
```

Source: <https://github.com/tesseract-ocr/tesseract/blob/main/src/ccmain/ltrresultiterator.cpp#L450>

That `rating_coefficient_` is a per-model constant, not a probability. This is why Tesseract's
own source comment for `ChoiceIterator::Confidence()` says the values only "roughly add up to
1.0f" for LSTM models. Two different code paths, two different meanings, one output field name.

### 1.4 What reaches your pipeline

Docling's Tesseract model takes `MeanTextConf()` and scales it:

```python
# tesserocr reports 0-100; cell confidences are 0-1
confidence = local_reader.MeanTextConf() / 100.0
```

Source: <https://github.com/docling-project/docling/blob/main/docling/models/stages/ocr/tesseract_ocr_model.py#L260-L261>
(introduced by <https://github.com/docling-project/docling/pull/4534>)

So your Docling `ocr_score` is `MeanTextConf()/100`, i.e. `(100 + 5c)/100` averaged over text
lines — a rescaled affine function of unnormalised certainty. **(c) inference**: dividing by
100 does not add probabilistic meaning. It only changes the units.

RapidOCR's cell confidence is PaddleOCR's CTC recognition score, a different quantity again:
<https://github.com/docling-project/docling/blob/main/docling/models/stages/ocr/rapid_ocr_model.py#L790-L796>

**Practical consequence for your two engines.** You are averaging two numbers that are not the
same kind of measurement. Tesseract's is an affine warp of a legacy/LSTM score; RapidOCR's is a
CTC sequence score. Comparing them or averaging them across engines has no calibration basis
that I could find in either project's documentation. **(c) inference.**

---

## 2. Is a Tesseract confidence of 90 a 90 percent chance of being correct?

No. Two lines of evidence, and they disagree in an informative way.

### 2.1 The direct test: thresholding high confidence still produces errors

**Kae, Huang, Learned-Miller, "Bounding the Probability of Error for High Precision
Recognition", Technical Report UM-CS-2009-031, arXiv:0907.0418 (2009).**
<https://arxiv.org/abs/0907.0418>

Abstract, verbatim:

> "While many current OCR systems produce measures of confidence for the identity of each
> letter or word, thresholding these confidence values, even at very high values, still
> produces some errors."

Their body text is blunter, and this is the single most on-point sentence I found for your
problem: "there is no threshold on this confidence value that produces clean word lists for
most documents without either an unacceptably small number of correct words (usually 0), or an
unacceptably large percentage of errors."
Source: <https://people.cs.umass.edu/~elm/papers/ocr_tech.pdf>, p. 3

This is a 2009 technical report, not a peer-reviewed venue paper, and the sample is small: 10
documents. It predates Tesseract 4's LSTM engine, so it tests the legacy classifier. Treat it
as strong qualitative confirmation of your observation, not as a calibration curve for
Tesseract 5.

Their alternative method is worth knowing because it is the *right shape* of answer: instead of
estimating P(correct), they form a null hypothesis per word and reject it under a worst-case
bound. Result: on 10 documents they identify about 6 percent of words with zero errors. **6
percent coverage.** For your use case — a numeric field you must get right — a method that
certifies 6 percent of tokens is not a solution, and it requires a lexicon, which numeric
fields do not have.

### 2.2 The large-scale measurement: strong rank correlation, never reaching zero

**Bubula, Baierer, Lehmann, Neudecker, Rezanezhad, Škarić (Berlin State Library), "How
Scalable is Quality Assessment of Text Recognition? A Combination of Ground Truth and
Confidence Scores", Computational Humanities Research 2025, Anthology of Computers and the
Humanities Vol. 3.**
<https://anthology.ach.org/volumes/vol0003/how-scalable-is-quality-assessment-of-text-of/10.63744@GR59c1iXu6Wj.pdf>

This is the best large-scale Tesseract confidence study I found. Scale: analysis over
approximately 47,000 historical documents, described as a heterogeneous corpus of almost 5
million pages (1456-2000); the confidence-vs-error regression is fit on three ground-truth
datasets (an unreleased Vlaamse Erfgoedbibliotheken set of 75 pages, OCR-D-GT, and
OCR-D-GT-VD-SBB of 348 pages).

They regress word error rate on word confidence. Their Table 1:

| Dataset | Pearson r | Spearman rho | Linear R-squared | Linear MSE |
|---|---|---|---|---|
| OCR-D-GT | -0.942 | -0.914 | 0.877 | 0.002 |
| VEB | -0.949 | -0.909 | 0.900 | 0.002 |
| OCR-D-GT-VD-SBB | -0.838 | -0.674 | 0.649 | 0.003 |

Their Table 2 intercepts (`Lin. beta_0`, the predicted WER at confidence 0; all p = .000):

| Dataset | Intercept beta_0 | Slope beta_1 |
|---|---|---|
| OCR-D-GT | 0.892 | -1.034 |
| VEB | 1.045 | -1.234 |
| OCR-D-GT-VD-SBB | 1.565 | -1.652 |

**The line does not pass through the origin, and that is the finding.** Solving
`WER = beta_1 * WC + beta_0` at `WC = 1.0`:

- OCR-D-GT: `1.0 - 1.034 * 1.0 = -0.034`
- VEB: `1.045 - 1.234 * 1.0 = -0.189`
- OCR-D-GT-VD-SBB: `1.565 - 1.652 * 1.0 = -0.087`

All three fitted lines predict a *negative* error rate at perfect confidence, which is
impossible. **(c) inference**: a linear fit forced through data where confidence is bounded
above is mis-specified at the ceiling, and the quadratic term they tried was not significant
(p = .115, .001, .538). The honest reading is not "confidence 1.0 means -19 percent errors".
It is that **the relationship bends and cannot reach zero error at maximum confidence** — the
linear extrapolation is the model telling you the same thing Kae et al. said in words.

The paper's authors propose exactly the fix you would want: "If this discrepancy remains
relatively stable across different inputs, the actual quality of OCR output may be approximated
by appropriately calibrating the confidence scores."

**Important limitation, stated plainly.** Their regression is at the **page or work level**
("each PPN was weighted by its total word count"), not per individual word. It measures whether
a page with high mean confidence has a low error rate. That is a *document triage* signal. It
does not establish that an individual word at confidence 90 is correct 90 percent of the time,
in either direction. Do not cite it as a per-word calibration curve.

Their corpus-level finding is also useful as a prior: mean word confidence across the corpus
was **0.785 ± 0.004**, mean textline confidence **0.791 ± 0.004**.

### 2.3 The ranking signal is real, and that is what you should use

Combining 2.1 and 2.2: Tesseract confidence orders outputs usefully (r near -0.94) and does not
quantify correctness. **Confidence is a triage and ranking tool, not a correctness test.** This
is consistent with your engineer's measurement: 145.60 / 146.00 / 145.08 all landed at 71-93
because they are all *plausible to the recogniser*, and confidence measures recogniser comfort,
not truth.

---

## 3. Confidence calibration for OCR: what the methods can and cannot buy

### 3.1 The canonical result

**Guo, Pleiss, Sun, Weinberger, "On Calibration of Modern Neural Networks", ICML 2017,
arXiv:1706.04599.** <https://arxiv.org/abs/1706.04599>

Temperature scaling — divide logits by a single scalar fit on held-out data — is introduced
here as a post-hoc fix for overconfidence. Widely replicated.

**Minderer, Djolonga, Romijnders, Hubis, Zhai, Houlsby, Tran, Lucic, "Revisiting the
Calibration of Modern Neural Networks", NeurIPS 2021, arXiv:2106.07998.**
<https://arxiv.org/abs/2106.07998>

Two findings that matter for you:

1. "The classification accuracy of the model is not affected by temperature scaling." It moves
   confidence only.
2. Their Figure 7 models selective prediction directly and concludes that for abstention rates
   up to 70 percent, "the model with better accuracy has a lower overall cost than the model
   with better ECE", across all families tested. **Better calibration is usually worth less than
   better accuracy.** They also warn that ECE estimators are biased and that the *sign* of the
   accuracy/calibration relationship can flip depending on bin count.

### 3.2 The 2026 result: calibration cannot improve error detection at all

**Roy, Martin, Rostami, Romo, Xue, Strahan, Socolinsky, Xie, Islam (Amazon Web Services), "Can
You Trust the Confidence? ConfBench for Vision-Language Models on Document Extraction",
arXiv:2608.01792.** <https://arxiv.org/abs/2608.01792>

This is a preprint, not peer-reviewed, and it evaluates hosted models. I include it because it
is the most careful measurement of the exact quantity you are asking about, and because its
negative result is structural rather than empirical.

Benchmark construction **(a) documented**: built on RealKIE-FCC-Verified (75 real-world FCC
invoice documents with manually verified ground truth), augmented with 20 controlled
degradation pipelines, yielding **1,346 variants and over 70,000 entity-level evaluations**.
Entities are invoice numbers, dates, and line items with five sub-fields. Degradation tiers are
reported with measured accuracy impact (e.g. "Custom22 (21 %): dithering + dot-matrix degrade
text regions").

Their finding, verbatim from the abstract:

> "calibration quality varies widely across models, from near-perfect to severely overconfident,
> and per-model post-hoc correction rescales these absolute confidence values for
> threshold-based routing **without altering ranking-based operational metrics**"

Expanded in the body:

> "These diagrams also bound what post-hoc calibration can buy: rescaling improves ECE, which
> matters for absolute-threshold routing, but it is monotone and so leaves AUROC and ECARB
> essentially unchanged."

**This is the load-bearing claim for your decision.** AUROC is the ability to separate correct
from incorrect. ECARB is error capture at a review budget. Both are monotone-invariant.
Temperature scaling, Platt scaling, isotonic regression are all monotone. Therefore **no
post-hoc calibration method can make a confidently-wrong reading rank as suspicious.**

**(c) inference:** this is a general property, not specific to their models. It applies to
Tesseract's `100 + 5c` too. If you isotonic-regress Tesseract confidence onto empirical
accuracy, you get a nicer-looking number and exactly the same review queue.

### 3.3 Measured numbers worth having

ConfBench Table 3, OCR+Image modality, at a 30 percent review budget (ECARB = error-capture
gain over random sampling):

| Model | Acc. | F1 | AUROC | ECE | Brier | ECARB |
|---|---|---|---|---|---|---|
| Claude Opus 4.6 (verbalized) | 0.75 | 0.42 | **0.84** | **0.05** | 0.12 | **2.43** |
| Claude Sonnet 4.5 (verbalized) | 0.77 | 0.51 | 0.77 | 0.13 | 0.16 | 2.24 |
| Claude Haiku 4.5 (verbalized) | 0.71 | 0.38 | 0.74 | 0.17 | 0.19 | 1.96 |
| Qwen 3.6-27B (verbalized) | 0.74 | 0.41 | 0.72 | 0.22 | 0.23 | 1.93 |
| Kimi K2.5 (verbalized) | 0.76 | 0.46 | 0.67 | 0.20 | 0.22 | 1.68 |
| Qwen 3 VL-235B (verbalized) | 0.64 | 0.41 | 0.62 | 0.25 | 0.26 | 1.53 |
| Gemma 3-12B (logprob, first-token) | 0.63 | 0.38 | 0.64 | 0.36 | 0.36 | 1.45 |
| Qwen 3.6-27B (logprob, first-token) | 0.75 | 0.70 | 0.62 | 0.21 | 0.21 | 1.41 |
| Gemma 3-12B (verbalized) | 0.65 | 0.28 | 0.58 | 0.31 | 0.31 | 1.39 |

Read the ECARB column as: **reviewing the lowest-confidence 30 percent of fields catches 2.43x
as many errors as reviewing 30 percent at random for the best model, and 1.39x for the weakest.**
Confidence-guided review works. It is not a detector.

Two secondary findings:

- **OCR+Image beat image-only for every model.** Gemma 3-12B gained 6 AUROC points from having
  the OCR text plus its confidence scores alongside the image; Opus 4.6 gained 3. Their
  conclusion is that OCR text *with its confidence scores* is a useful confidence input.
- **Logprob vs verbalized is not settled.** For Qwen 3.6-27B verbalized won (0.72 vs 0.62
  AUROC); for Gemma 3-12B logprob first-token won (0.64 vs 0.58). Among logprob aggregations,
  first-token beat mean-token and margin consistently.

### 3.4 HYCEDIS: a trained confidence engine for document extraction

**Nguyen, Tran, Dang, Nguyen, Le, "HYCEDIS: HYbrid Confidence Engine for Deep Document
Intelligence System", Document Intelligence @ KDD 2021 Workshop, arXiv:2206.02628.**
<https://arxiv.org/abs/2206.02628>

Architecture **(a) documented**: a Multi-modal Conformal Predictor plus a Variational
Cluster-oriented Anomaly Detector, trained to estimate confidence on an existing extractor's
outputs "without the need of host models modification". Evaluated on real-world datasets, with
claimed large margins over competing confidence estimators and generalisation to
out-of-distribution data.

**What I could not verify:** the abstract asserts the margin but I did not retrieve the
per-metric numbers, and I found no released code or weights. Whether it is reproducible on
client hardware is **unverified**. The paper is a workshop paper (non-archival venue), so treat
the framing as a preprint.

The reason to mention it: it is the clearest statement in the literature that **reliable
confidence for document extraction is a trained model, not a free by-product of the OCR
engine.** That is a build cost, and it needs labelled data you may not have.

---

## 4. Detecting OCR errors after the fact

### 4.1 The standard survey

**Nguyen, Jatowt, Coustaty, Doucet, "Survey of Post-OCR Processing Approaches", ACM Computing
Surveys, doi:10.1145/3453476 (2021).** <https://dl.acm.org/doi/10.1145/3453476>

Peer-reviewed survey. Covers the taxonomy from rule-based pattern matching to neural
sequence-to-sequence correction. This is the reference to cite for the field's shape. Access is
paywalled; I read the abstract and secondary descriptions, not the full text.

A related 2026 survey adds a useful datapoint on method effectiveness:

**Beyene, Dancy, "A Survey of OCR Evaluation Methods and Metrics and the Invisibility of
Historical Documents", ACM FAccT 2026, arXiv:2603.25761.** <https://arxiv.org/abs/2603.25761>

It reports, citing Nguyen et al., that "T5-based approaches achiev[e] 28-40 % improvement in
error reduction on historical materials", and separately that "previous OCR error rates for
these collections averaged 18 %". It also records that GOT-OCR 2.0 "achieves only 22.1 % on the
same olmOCR-Bench" and that olmOCR 2 scores "82.3 % (Math) / 47.7 % (General)" on historical
scans. Note this survey is about *historical* documents; the numbers do not transfer to invoice
scans without re-measurement.

### 4.2 Dictionary and n-gram checks: the false-positive problem

The Berlin State Library paper (section 2.2 above) documents why dictionary and n-gram methods
struggle, and its reasoning extends directly to numeric fields:

> "One such method leverages lexicons to identify out-of-vocabulary words, which are then
> flagged as potential errors. While theoretically promising, this approach is often
> insufficient for historical documents, which frequently exhibit extensive orthographic
> variation over time... Another method proposed in [19] uses character-level N-gram models,
> which must also be trained per-language, exhibiting similar problems as dictionary-based
> methods."

**(c) inference, and it is the crux for your use case:** `145.60` is a perfectly well-formed
number. It is in-vocabulary for any numeric grammar. A character n-gram model trained on
financial documents will score it as *plausible*, because it is plausible. Dictionary and n-gram
checks detect the malformed case (`145.0C`, `"45.00`) and are blind to your actual defect.

I found **no** study that measures dictionary/n-gram detection specifically on numeric fields.
That is a genuine gap, not something I am eliding. Any claim that n-gram plausibility catches
wrong-but-valid digits is **unverified**.

### 4.3 The one measurement I found on rule-based field validation

**Freitter, "Automating ID card verification leveraging deep learning", MSc thesis, TU Wien
(2025).** <https://repositum.tuwien.at/handle/20.500.12708/213271>

This is a master's thesis, not peer-reviewed, but it measured exactly the thing you are asking
about: how many real OCR field errors survive format/validity rules.

Table 4.11, distribution of all field errors by manifestation:

| Error category | Share |
|---|---|
| Separating dots | 12.20 % |
| Separating slash | 7.32 % |
| Umlaut | 9.76 % |
| One letter | 29.27 % |
| One digit | 17.07 % |
| Others | **24.39 %** |

And the text:

> "Table 4.11 illustrates that only 24.39 % of the incorrectly read fields fall outside the
> identified patterns. Consequently, it is feasible to establish rules that tolerate a certain
> error margin."

**Read this carefully, because it cuts both ways.**

- **75.61 percent of misread fields were format-conforming.** Format validation as an error
  *detector* therefore has a ceiling of roughly a quarter of errors. Your malformed example
  (`145.0C`, `"45.00`) is in that quarter. Your plausible example (`145.60`) is in the other
  three quarters.
- But their conclusion is about *tolerance*: because errors fall into a few knowable buckets,
  you can write rules that accept the harmless deviations. That is a different and also useful
  goal.

Caveats: single thesis, ID cards not invoices, and the "one digit" category at 17.07 percent is
almost certainly a *digit substitution* — the shape of your defect. Their category list does not
tell you whether the substituted digit produced a valid-looking value, so I cannot derive your
specific detection rate from it.

---

## 5. Numeric-specific checks

### 5.1 Check digits: the only approach with a mathematical guarantee

Reference for the algorithms and their properties:
<https://en.wikipedia.org/wiki/Check_digit> (encyclopaedia, not a paper — treat as background,
not evidence).

**This is the important distinction, and it matches your defect exactly.**

Check digit systems are explicitly "designed to capture *human* transcription errors", and the
listed error classes begin with "letter/digit errors, such as l → 1 or O → 0" and "single-digit
errors, such as 1 → 2". A digit substitution is the first thing these algorithms were built for.

Guarantees, as stated:

- **Weighted mod-10 with weights coprime to 10** (UPC/EAN/GS1, ABA routing transit numbers with
  371 371 371 weights): "detects all single-digit errors and around 90 % of transposition
  errors".
- **Luhn (mod 10)**: "captures 98 % of single-digit transposition errors (it does not detect
  90 ↔ 09)". Luhn detects all single-digit substitutions.
- **Verhoeff**: "catches all single-digit substitution and transposition errors, and many (but
  not all) more complex errors."
- **Damm**: "detects all single-digit errors and all adjacent transposition errors."
- **ISBN-10 (mod 11, prime)**: "detects all single-digit substitution and transposition errors
  (including jump transpositions)".
- **IBAN (mod 97, two check digits)**: uses two check digits precisely because one leaves
  "around 10 %" of more complex errors uncaptured.

**(c) inference, and it is the strongest claim I can make in this document:** if your numeric
field carries a check digit — invoice number, account number, IBAN, ISBN, GTIN, EAN, routing
number, IMEI, VIN, national ID — then a single misread digit produces an invalid check value
with probability 1 for a mod-11/Verhoeff/Damm scheme and probability 1 for a mod-10
single-substitution. The defect **cannot** produce a valid value. That is categorically
different from a confidence score.

**What I could not find.** I found no study measuring check-digit detection rates on *actual
OCR output*. All the figures above are the algorithms' theoretical properties under the
independent-random-error model. OCR errors are not independent — a systematic defect that
corrupts the same glyph in the same way every time, or a defect that hits the check digit
itself, behaves differently. Treat the guarantee as "detects substitution with probability 1
*when applied to the digits*", and measure your own hit rate.

### 5.2 Cross-field arithmetic

Arithmetic consistency (line items sum to subtotal, subtotal plus tax equals total) is the
second structural check. It is a *system of equations* rather than a single-value check.

I found **no peer-reviewed paper measuring arithmetic-consistency detection rates on real OCR
invoice output.** The sources that discuss it are vendor blog posts (e.g.
<https://imagetotable.ai/blog/line-items-quantity-price-mismatch-extraction>) and patents. Those
are not evidence. **Unverified** as a measured technique.

**(c) inference on its power, stated as reasoning rather than measurement:** a cross-field check
is strictly more informative than a format check, because a single wrong digit nearly always
breaks the arithmetic, and the residual is *localising* — it tells you which line is suspect,
not just that the document is wrong. Its cost is coverage: it only applies to fields that
participate in an accounting identity, and it fails on documents where the arithmetic is
already inconsistent (rounding, discounts, multi-currency, handwritten adjustments). You would
need to measure the false-positive rate on a sample of known-good invoices before trusting it.

### 5.3 Format validation

Useful for the malformed class, with the 24.39 percent ceiling from section 4.3. Cheap, zero
latency, zero false positives when the grammar is tight. Keep it. Do not mistake it for a
detector of plausible errors.

---

## 6. Multi-pass and ensemble reading: does disagreement predict error?

There is a name for the method, and it is recent.

### 6.1 Consensus Entropy (CVPR 2026) — peer-reviewed

**Zhang, Liang, Cui, Wang, Guo, Li, Liu, "Consensus Entropy: Harnessing Multi-VLM Agreement
for Self-Verifying and Self-Improving OCR", CVPR 2026, pp. 11643-11653.**
<https://openaccess.thecvf.com/content/CVPR2026/html/Zhang_Consensus_Entropy_Harnessing_Multi-VLM_Agreement_for_Self-Verifying_and_Self-Improving_OCR_CVPR_2026_paper.html>

Code: <https://github.com/Aslan-yulong/consensus-entropy> — **MIT licence** (verified via GitHub
API).

The mechanism, verbatim from the abstract: "correct predictions converge in output space, while
errors diverge." They compute pairwise similarity between multiple models' outputs on the same
image, take the entropy of that distribution, and call it Consensus Entropy. For
"character-level precision (e.g., standard OCR, mathematical expressions)" they use **edit
distance** as the similarity; for semantic tasks, embedding cosine similarity.

Measured results:

| Result | Number | Where |
|---|---|---|
| Quality-verification F1 vs VLM-as-Judge | +15.2 F1, a **42.1 %** gain | Abstract |
| CE vs VLM-J on Qwen2-VL-7B | 51.3 vs 36.1 overall F1 | Table 1 |
| Human-annotated eval set | **1,000 PDF pages**, multi-language | Section 4.2 |
| CE-Ensemble on OCRBench | 24 models, 55,154 combinations (3-5 model sets) | Section 4.3 |
| CE always avoids the worst prediction | ∆+%max = **100 %** at ensemble sizes 3, 4, 5 | Table 2 |
| CE-OCR accuracy gain | **+8.2 %**, routing only **7.3 %** of samples | Abstract, Table 5 |
| Math category gain over plain ensemble | +6.0 % relative | Table 4 |
| Self-consistency comparison | SC@3 average 828 vs Routing average 907 (+6.5 %) | Table 5 |

**Why `max = 100 %` matters to you.** CE never picked the worst member of the ensemble. That is
a floor guarantee on quality, obtained with no training and no labels.

**The cost, stated plainly.** CE-OCR needs **multiple models** and routes hard cases to a
*stronger* model. In their experiments that means GPT-4o for rephrasing, and ensembles of
Qwen2.5-VL-72B, InternVL2.5-78B, Qwen2-VL-72B. **The headline configuration requires hosted
models.** Their Table 3 does show a small-model configuration — `Ovis2-1B, Ovis2-4B, Qwen2VL-7B,
Qwen2.5VL-7B` scoring 933 against a best-single 909 — so a fully open-weight, self-hosted
ensemble is demonstrated, but every model is still a full VLM forward pass. Latency and VRAM
scale with ensemble size.

They do not report per-field numeric accuracy, and their benchmarks are OCRBench and
OCRBench-V2, which are not degraded-scan benchmarks. **Whether CE separates correct from
incorrect on a damaged numeric field is unverified.** The mechanism is encouraging; the specific
measurement is missing.

### 6.2 Two-engine disagreement, measured

**Duggempudi, "Towards Ensemble OCR For Printed Telugu", MSc thesis, Ohio University (May
2026).** <https://etd.ohiolink.edu/acprod/odb_etd/ws/send_file/send?accession=ohiou1776476252506892&disposition=inline>

Tesseract versus EasyOCR, word-level alignment, hierarchical resolution. Synthetic Telugu
corpus, so the language does not transfer. The structure of the result does.

Table 4.1 — ground-truth-aligned disagreement outcomes:

| Case | Share of disagreement tokens | Ensemble accuracy |
|---|---|---|
| Tesseract correct, EasyOCR wrong | 60.8 % | 96.0 % |
| Tesseract wrong, EasyOCR correct | 12.4 % | 96.2 % |
| **Both wrong** | **26.8 %** | **56.9 %** |

**This is the number that should shape your design.** Disagreement between two engines is a real
error signal, but more than a quarter of disagreements are cases where **neither engine is
right**. In that bucket the ensemble only recovers to 56.9 percent accuracy. So disagreement is
not a correction signal — it is an **escalation** signal. Roughly: agreement means probably
fine, disagreement means a human or a third reader must look, and picking the more confident of
two wrong readings is not recovery.

Also worth noting from the same thesis: the contextual (bigram) stage resolved 88.4 percent of
disagreements, while "confidence-based preference and lexicon tie-breaking each account for
approximately 1.7 percent". **The confidence field did almost none of the work.**

Sample-size caveat: single MSc thesis, synthetic images, one non-Latin script, two engines. The
60.8/12.4/26.8 split is not a constant you should hard-code. **(c) inference**: it is a strong
hint that you should measure your own split before deciding whether to escalate or auto-resolve.
For **numeric fields specifically, the both-wrong rate is likely worse**, because digits give
the contextual layer nothing to work with — a bigram model can suggest a word from its
neighbours, but it cannot suggest a cheque amount.

### 6.3 The known method name

To answer the question directly: the umbrella practice is **multi-engine / multi-pass OCR
ensemble with agreement-based verification**. The specific recent named metric for
disagreement-as-error-signal over VLM outputs is **Consensus Entropy (CE)**, from the CVPR 2026
paper above. The older, simpler form is **decision-based ensemble with voting/alignment** (the
Telugu thesis is a clear recent example; the thesis itself cites a prior result claiming
"accuracy ratings of 99 % with a voting system between different OCR engines" — I did not
verify that primary source, so treat the 99 percent as unverified).

---

## 7. Selective prediction and abstention

### 7.1 The framework

The relevant literature is *selective prediction* / *learning to abstain*, evaluated with
**risk-coverage curves**: choose a threshold, abstain below it, plot error rate among accepted
predictions against the fraction accepted. ConfBench's ECARB (section 3.3) is exactly this
metric applied to document fields.

- **Geifman, El-Yaniv, "SelectiveNet: A Deep Neural Network with an Integrated Reject Option",
  ICML 2019.** <https://arxiv.org/abs/1901.09192> — trains the abstention into the model.
- **Conformal prediction** gives distribution-free finite-sample coverage guarantees. For
  documents specifically: **"Beyond Accuracy: Understanding Model Confidence in Key Information
  Extraction with Conformal Prediction"**, IJDAR, doi:10.1007/s10032-026-00572-y (2026).
  <https://link.springer.com/article/10.1007/s10032-026-00572-y> — I hit a paywall redirect and
  **could not read it**; title, venue and topic verified, contents **unverified**.

### 7.2 The result you should not skip

**Gurram, "Valid Per-Field Selective Risk Control for Document Extraction: Three Failure Modes,
a Validity Ladder, and When Conditioning Pays", arXiv:2608.14639 (28 July 2026).**
<https://arxiv.org/abs/2608.14639>
Code: <https://github.com/bhaskargurram-ai/verifydoc> — **Apache-2.0** (verified via GitHub API).

Preprint (single author), and it uses hosted models, but its **negative findings are the most
actionable thing in this whole briefing**, because they are about how per-field risk control
silently fails.

Setup: 13,859 genuine `claude-sonnet-5` fields from 800 CORD receipts, 49.0 percent correct.

Three diagnosed failure modes **(a) documented**:

1. **Document clustering.** Fields cluster within documents, so the i.i.d. assumption is
   violated; they measure a design effect of **1.84-2.45**.
2. **Score-refit leakage.** Fitting the acceptance threshold and reporting risk on the same data
   gives "coverage 0.416 at risk 0.127, **violating alpha = 0.10 in 95 % of splits**". You must
   split fit from report.
3. **Tie-mass pathology.** A degenerate score "collapses the threshold grid, 0.030 to 0.001".

After fixing these with a fit/val split, a learned fusion reaches "coverage 0.318 at risk 0.096
at nominal alpha = 0.10", which is an on-average point, "not a certificate" — realized risk
exceeds alpha in 47.5 percent of resplits. Their Mondrian Learn-then-Test tier gives real PAC
certificates but at "cluster-corrected 0.140" coverage, described as "honestly near-vacuous
today".

And a result that should stop you choosing a scheme on theory: "support-bin, the pre-specified
provenance taxonomy, wins every rigor tier on the sonnet CORD capture (p<1e-4,
Bonferroni-corrected) — **a win that does not replicate** on the same documents under haiku or
qwen".

**Translation for your project (c) inference.** If you build a thresholded "accept / send to
review" gate on a confidence score, and you tune the threshold on the same documents you
report the error rate from, **you will report a number that is wrong most of the time.** And
because fields within one invoice are correlated, your effective sample size is much smaller
than your field count — an invoice with a bad scan tends to have several bad fields. Sample per
*document*, not per field, when you validate.

### 7.3 Is abstention applied per-word or per-field in OCR?

Per-field, in the document-extraction literature. That is where the 2026 work sits (ConfBench
performs entity-level evaluation; Gurram is explicitly per-field). **I found no paper applying
selective prediction per *word* inside OCR**, which is the granularity you would need to gate a
single amount. The nearest thing at sub-document granularity is Kae et al.'s per-word clean-list
construction from 2009 (section 2.1), which achieves 6 percent coverage.

**(c) inference:** treat the OCR word as the unit of *evidence* and the field as the unit of
*decision*. Abstain on fields.

---

## 8. Modern alternatives: do VLMs read damaged numbers better?

The honest answer is that the evidence is thinner and less favourable than the marketing, and
the best study of it is explicitly not a real-degradation study.

### 8.1 Degradation robustness, measured with controlled perturbations

**Cheng, Wu, Chang, "How Robust is OCR-Reasoning? Evaluating OCR-Reasoning Robustness of
Vision-Language Models under Visual Perturbations", arXiv:2606.26041 (24 June 2026).**
<https://arxiv.org/abs/2606.26041>
Code: <https://github.com/pasterinjlu/OCR-Reasoning-Robust>

Preprint. Also on OpenReview: <https://openreview.net/forum?id=8DaL0Tapq9>

Benchmark: **OCR-Robust**, 812 samples. OCR1.0 = 482 samples (documents 50, scene text 52,
infographics 40, Chinese ads 63, **receipts 75**, screenshots 30, handwriting 30, mathematical
100, Chinese documents 42). OCR2.0 = 330 samples (charts 150, geometry 80, tables 100). Five
perturbations x 3 severities = 15 conditions, selected from 18 candidates by a pilot over 6
models and 100 questions. 18 models evaluated.

Metrics: clean accuracy, Relative Corruption Retention (RCR), Worst-Case Retention (WCR),
Corruption Robustness Index (CRI).

| Model | OCR1.0 Clean | RCR | WCR | OCR2.0 Clean | RCR | WCR |
|---|---|---|---|---|---|---|
| Gemini-3-Flash-Thinking | 79.05 | .998 | .932 | 69.70 | .977 | .870 |
| Gemini-3-Flash | 80.71 | .986 | .920 | 71.52 | .980 | .860 |
| GPT-5.2 | 74.69 | .911 | .799 | 71.82 | .876 | .717 |
| Qwen3-VL-8B-Thinking | 79.67 | .961 | .875 | 78.18 | .892 | **.624** |
| Qwen3-VL-32B-Instruct | 72.82 | .954 | .826 | 57.27 | .928 | .640 |
| Qwen3-VL-8B-Instruct | 54.15 | .903 | .770 | 49.39 | .921 | .632 |
| InternVL3.5-14B | 55.81 | .939 | .838 | 55.45 | .912 | .738 |
| InternVL3.5-8B | 57.26 | .941 | .835 | 56.67 | .892 | .631 |
| PaddleOCR-VL-1.5 + GPT-5.1 | 52.90 | .902 | .753 | 26.36 | .982 | .805 |

Key findings **(a) documented**:

- "Higher clean accuracy does not necessarily imply stronger robustness." Qwen3-VL-8B-Thinking
  tops OCR2.0 clean accuracy (78.18) and collapses to WCR 0.624, while Gemini-3-Flash at 71.52
  keeps WCR 0.860.
- "Structured visual content... appears substantially more fragile", average WCR dropping from
  0.826 (OCR1.0) to 0.676 (OCR2.0).
- "Larger scale does not monotonically improve robustness": InternVL3.5-14B beats 38B on
  worst-case retention.
- "OCR + LLM pipelines are sensitive to OCR extraction quality."
- Prompt-induced chain-of-thought "improves clean accuracy but does not reliably prevent worst
  case failures".

**Crucially, the authors bound their own claim**, verbatim: "We do not aim to simulate
real-world image degradation, and our compound perturbation setting should be viewed as an
initial attempt to probe interactions among controlled visual corruptions."

Their perturbations are synthetic (glass blur, motion blur, elastic deformation, colour shift,
snow). Snow is not a scanning artefact. **So: this study is not evidence about your scanned
invoices.** It is evidence that VLM robustness is not implied by VLM accuracy, which is a
reason for caution, not a reason to switch.

**What I could not find: a study that compares a VLM against Tesseract on per-field numeric
accuracy on genuinely damaged scans.** That specific experiment appears not to have been
published. If it exists I did not locate it, and I am not going to assert its conclusion.

### 8.2 Do VLMs report usable uncertainty?

Partly, and the answer depends on model size in a way that matters for on-prem.

From ConfBench (section 3.3): confidence quality spans "near-perfect to severely
overconfident". Opus 4.6 reached AUROC 0.84 / ECE 0.05 with no post-hoc adjustment, tracking the
diagonal — but that is a hosted frontier model. The **open-weight** models measured were far
worse: Gemma 3-12B at AUROC 0.58 verbalized / 0.64 logprob with ECE 0.31-0.36, and Qwen 3 VL-235B
at AUROC 0.62 / ECE 0.25. **(c) inference:** the models you can actually self-host are in the
"overconfident" end of that range, which is the same regime as Tesseract. A VLM does not
automatically give you a better uncertainty signal.

Also from ConfBench, a finding worth internalising: "within the Claude family confidence quality
scales monotonically with capability... but across families parameter count is a poor predictor".
Qwen 3.6-27B (0.72) beat Qwen 3 VL-235B (0.62) at one-eighth the parameters. Do not select by
size.

One more result, on hallucination in VLM OCR — the failure mode that is *worse* than a
misread digit because a format check cannot catch it. The 2026 FAccT survey reports that a study
of GPT-4o Vision on 18th-19th century Russian manuscripts "discovered that **59 % of errors
involved anachronistic character insertions**, implying that the models learn to perform
'historicalness' instead of accurately transcribing historical documents", and calls the
phenomenon "over-historicization". Source: <https://arxiv.org/abs/2603.25761> (reporting on
another paper, which I did not read directly). For modern invoices the analogue is a
plausible-but-invented digit sequence. **A generative reader can fail by fabrication, and
fabrication has no confidence penalty.**

### 8.3 Model licences, verified

Checked against the HuggingFace API on 2026-10-12 and project licence files.

**Acceptable (permissive, weights downloadable, not gated):**

| Model | HF licence tag | Params |
|---|---|---|
| `deepseek-ai/DeepSeek-OCR` | **mit** | 3B (per Docling catalog) |
| `rednote-hilab/dots.ocr` | **mit** | — |
| `ibm-granite/granite-docling-258M` | **apache-2.0** | 258M |
| `PaddlePaddle/PaddleOCR-VL` | **apache-2.0** | ~0.9B |
| `allenai/olmOCR-2-7B-1025` | **apache-2.0** | 7B |
| `stepfun-ai/GOT-OCR2_0` | **apache-2.0** | ~580M |
| `Qwen/Qwen3-VL-8B-Instruct` | **apache-2.0** | 8B |
| `OpenGVLab/InternVL3_5-8B` | **apache-2.0** | 8B |
| `naver-clova-ix/donut-base` | **mit** | ~200M |
| `docling-project/docling-models` (layout + TableFormer) | **cdla-permissive-2.0 + apache-2.0** | — |
| `ds4sd/CodeFormula` | **mit** | — |

**Flag — MinerU.** The HuggingFace model card for `opendatalab/MinerU2.5-2509-1.2B` carries
`license: agpl-3.0`: <https://huggingface.co/opendatalab/MinerU2.5-2509-1.2B/raw/main/README.md>.
The **repository** is different: `MinerU/LICENSE.md` reads "MinerU is licensed under Apache
License 2.0 and is subject to the additional terms below", with a commercial-licence threshold
at >100M MAU or >USD 20M monthly revenue, and an attribution obligation for online services:
<https://github.com/opendatalab/MinerU/blob/master/LICENSE.md>. There is an open licensing
clarification thread at <https://github.com/opendatalab/MinerU/discussions/2863> which I could
not load. **Which licence governs the weights is unresolved.** Given this project rejects AGPL,
**treat MinerU weights as not cleared** until that is resolved with the maintainers in writing.
Note Docling ships a MinerU preset (`mineru2_pro`), so this can enter your stack through
Docling.

**Flag — Nanonets-OCR-s / Nanonets-OCR2.** No `license:` field in the model card and no LICENSE
file; the GitHub org repo reports no licence.
<https://huggingface.co/nanonets/Nanonets-OCR-s> — **licence unverified, treat as
all-rights-reserved by default.**

**Flag — Docling's `deepseek_ocr` preset.** The Docling catalog lists DeepSeek-OCR-3B as
"(Markdown, API-only)" with Transformers and MLX both unsupported. The weights are MIT but the
preset as documented goes through an OpenAI-compatible endpoint. Self-hostable in principle,
not integrated natively. Verify before assuming offline operation.

**Docling's supported VLM presets** (per the model catalog), all of which can run through local
Transformers or MLX: `granite_docling` (258M), `smoldocling` (256M), `granite_vision` (2B),
`got_ocr`, `qwen` (Qwen2.5-VL-3B), `nanonets_ocr2` (3B, licence unclear), `pixtral` (12B),
`gemma_12b`/`gemma_27b` (MLX only), `dolphin`, `phi4`, `nemotron_parse_v2` (0.9B),
`mineru2_pro` (license flagged), `unlimited_ocr`.
Source: <https://docling-project.github.io/docling/usage/model_catalog/>

**(c) inference:** `granite_docling` (258M, Apache-2.0) is the cheapest thing to put in a
second reading position and it is already integrated with the Docling you run. Its size is also
its limitation — a 258M model is not a stronger reader than Tesseract, so as an *independent
re-reader* its value is disagreement signal, not accuracy.

---

## 9. Cost table

Latency figures are **not measured by me** and are not in most of these papers. Where I give a
number it is my estimate **(c) inference**, labelled. Treat the cost column as relative effort,
not as a benchmark.

| Approach | Detects your `145.60` case? | Evidence | Accuracy cost | Latency cost | Complexity |
|---|---|---|---|---|---|
| Raise confidence threshold | **No** | §2.1, §3.2 | Discards correct words; Kae gets only 6 % coverage at high precision | None | Trivial |
| Temperature / isotonic calibration | **No** — monotone, ranking unchanged | §3.2 verbatim | None | None | Low |
| Format / grammar validation | **No** (catches only malformed, ~24 % ceiling) | §4.3 | None if grammar tight | None | Low |
| Character n-gram / LM plausibility | **No** for valid-looking numbers | §4.2 — no numeric-field measurement exists | Unknown false-positive rate | Low | Medium (needs corpus) |
| **Check digit** | **Yes**, where the field has one | §5.1 theoretical | None — defect cannot produce valid value | None | Low per scheme |
| **Cross-field arithmetic** | **Yes**, for fields in an identity | §5.2 — **unverified**, no paper found | False positives on legitimately inconsistent docs | None | Medium |
| **Two-engine disagreement** | Yes, as an escalation trigger | §6.2 — 26.8 % both-wrong | Escalates ~a quarter of disagreements needlessly | 2x OCR | Low (you already have both engines) |
| **Consensus Entropy ensemble** | Yes, as a reliability rank | §6.1 — CVPR 2026, peer-reviewed | None; +8.2 % accuracy reported | N model passes | Medium-high |
| Selective prediction gate | Ranks only | §7.2 | Coverage loss; naive tuning violates the guarantee | None | Medium (needs labelled data + split discipline) |
| VLM as second reader | Unknown — **not measured on damaged numeric fields** | §8.1 | Unknown | 1-12B VLM forward pass | Medium |
| VLM uncertainty | Weak on open-weight models | §8.2 — AUROC 0.58-0.72 | — | Already paying it | Low |

---

## 10. What the evidence actually supports

Ordered by evidence strength per unit of effort, strongest first.

**1. Check digits and cross-field arithmetic, where the field supports them.** These are the
only checks in this document that the defect *cannot* corrupt into a valid value. The
mathematics is a century old and not in dispute. Cost: minutes of code, zero latency. The
limitation is coverage — it only works on fields that carry redundancy already. **This is the
highest-value item and it should be first.**

**2. Format and grammar validation.** Already doing it, keep it. Understand its ceiling: about a
quarter of errors (section 4.3). It handles your `145.0C` case and not your `145.60` case.

**3. Multi-pass reading as an escalation trigger, not a correction.** This is the honest form of
the idea, and the Telugu thesis is the reason: 26.8 percent of disagreements were cases where
both engines were wrong and ensemble recovery was 56.9 percent. So route disagreement to a human
or a third reader; do not let a vote silently pick a value. To make this work on numeric fields,
vary the *stage* as well as the model — different binarisation, different PSM, different engine —
because two passes that share a failure mode will agree on the wrong answer and produce false
confidence. Consensus Entropy (MIT-licensed, peer-reviewed) is the named, current method for
scoring multi-model agreement, and its "never picks the worst" result is worth reproducing on
your own data.

**4. Calibrate the confidence, but only to triage documents.** Iso-tonic or linear regression of
page-mean confidence onto measured page error rate (the Berlin State Library approach, r near
-0.94) gives you a usable "which pages should a human skim" signal. **Do not expect it to gate a
single amount.** Section 3.2 is the reason: it is monotone and cannot reorder anything.

**5. Do not switch the pipeline to a VLM on the strength of existing benchmarks.** I could not
find a published measurement of VLM versus classic OCR numeric-field accuracy on genuinely
degraded scans. The closest degradation study is explicit that it does not simulate real
degradation. The open-weight VLMs measured for confidence calibration are overconfident, at
AUROC 0.58-0.72, which is the same regime as the confidence you already distrust. A VLM is
plausibly worth adding as an *independent second reader* (a different architecture is a genuinely
independent failure mode, which is the property that matters). It is not established as a more
accurate *primary* reader for your defect.

**6. If you build an accept/review gate, budget for validating it properly.** Section 7.2 is the
warning: fitting the threshold on the same data you report from violates the target error rate in
95 percent of splits, and fields cluster within documents so your effective sample size is
smaller than your field count. Sample per document, split fit from report.

### The one-line version

A Tesseract confidence of 90 does not mean a 90 percent chance of being correct — Tesseract's own
source says to read it as a percent probability, the code computes `100 + 5c` from an
unnormalised score in `[-20, 0]`, and the 2009 paper that tested high-confidence thresholding
found residual errors and said so in its abstract. The 2026 calibration literature shows why you
cannot fix this by rescaling: post-hoc calibration is monotone, so it moves the numbers without
reordering them. What detects a confidently-wrong digit is redundancy the defect cannot corrupt
twice — a check digit, an arithmetic identity, or an independent re-read that disagrees.

---

## Open questions I could not close

1. **The value and estimation procedure for `k`** in Tesseract's `certainty = 20 (k log p)`.
   Unverified from source and documentation.
2. **Any study measuring check-digit detection rates on actual OCR output.** The figures in
   section 5.1 are theoretical properties under an independent-error model.
3. **Any study of cross-field arithmetic consistency detection rates on real OCR invoices.**
   Only vendor blogs found.
4. **Any study of VLM versus Tesseract per-field numeric accuracy on damaged scans.** Not found.
5. **HYCEDIS measured results and whether code or weights exist.** Abstract only.
6. **"Beyond Accuracy: Understanding Model Confidence in Key Information Extraction with
   Conformal Prediction"** (IJDAR 2026) — paywalled, contents unverified.
7. **Which licence governs MinerU model weights** (HF says AGPL-3.0, repo says Apache-2.0 with
   thresholds). Unresolved.
8. **Nanonets-OCR-s / OCR2 licence.** No licence declared anywhere I could find.
9. **Whether Consensus Entropy separates correct from incorrect on a damaged numeric field.**
   The mechanism is promising and the paper does not test it.
