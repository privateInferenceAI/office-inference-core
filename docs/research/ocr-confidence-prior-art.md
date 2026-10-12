# OCR confidence and confidently-wrong readings: what open-source, self-hostable stacks actually do

Research date 2026-10-12. Every claim carries a source URL. Findings are labelled
**(a) documented**, **(b) code read**, **(c) inference**.

---

## 1. Docling — the confidence report, exactly

### The grade thresholds (b) code read

`docling/datamodel/base_models.py`, `PageConfidenceScores._score_to_grade`:

```python
def _score_to_grade(self, score: ScoreValue) -> QualityGrade:
    if score < 0.5:      return QualityGrade.POOR
    elif score < 0.8:    return QualityGrade.FAIR
    elif score < 0.9:    return QualityGrade.GOOD
    elif score >= 0.9:   return QualityGrade.EXCELLENT
    return QualityGrade.UNSPECIFIED
```

Source: <https://github.com/docling-project/docling/blob/main/docling/datamodel/base_models.py#L655-L665>
Grades enum `QualityGrade`: `poor`/`fair`/`good`/`excellent`/`unspecified` — same file, lines 629-634.

These are the **only** numeric cutoffs the project defines. They are a *reporting*
vocabulary, not a suggested review threshold.

### The four component scores (b) code read

| Score | How it is computed | Source |
|---|---|---|
| `ocr_score` | `np.mean([c.confidence for c in final_cells if c.from_ocr])` — the arithmetic mean of per-**line** OCR confidences on the page | [base_ocr_model.py#L631-L634](https://github.com/docling-project/docling/blob/main/docling/models/base_ocr_model.py#L631-L634) |
| `parse_score` | 10th percentile of `rate_text_quality(cell.text)` over the page's text cells | [page_preprocessing_model.py#L106-L121](https://github.com/docling-project/docling/blob/main/docling/models/stages/page_preprocessing/page_preprocessing_model.py#L106-L121) |
| `layout_score` | `np.mean([c.confidence for c in clusters])` — layout-model cluster confidences | [layout_postprocessing_model.py#L87-L93](https://github.com/docling-project/docling/blob/main/docling/models/stages/layout/layout_postprocessing_model.py#L87-L93) |
| `table_score` | **never assigned anywhere in the repo.** Declared and averaged, but no writer | declared [base_models.py#L640](https://github.com/docling-project/docling/blob/main/docling/datamodel/base_models.py#L640); only aggregation at [standard_pdf_pipeline.py#L1137-L1141](https://github.com/docling-project/docling/blob/main/docling/pipeline/standard_pdf_pipeline.py#L1137-L1141); docs admit "not yet implemented" |

`table_score` staying `NaN` is confirmed by the docs calling it *"Table extraction
quality (not yet implemented)"*:
<https://github.com/docling-project/docling/blob/main/docs/concepts/confidence_scores.md>

### Document-level roll-up (b) code read

`np.nanmean` over pages for `layout_score`, `table_score`, `ocr_score`;
`np.nanquantile(..., q=0.1)` over pages for `parse_score`
("parse score should relate to worst 10% of pages" — their comment).
`ConfidenceReport.mean_score` = `np.nanmean` of the 4 components;
`ConfidenceReport.low_score` = `np.nanquantile(values, q=0.05)`.
Source: <https://github.com/docling-project/docling/blob/main/docling/datamodel/base_models.py#L748-L772>

Because `table_score` is always `NaN`, the 4-component mean is effectively a
**3-component** mean. (c) inference from the missing writer.

### What Docling itself says to do with these — (a) documented

> "Users can and should safely focus on the document-level grade fields —
> `mean_grade` and `low_grade` — to assess overall conversion quality.
> Numerical scores are used internally and are for informational purposes only;
> their computation and weighting may change in the future."

> Use cases … "Set confidence thresholds for unattended batch conversions"

Source: <https://github.com/docling-project/docling/blob/main/docs/concepts/confidence_scores.md>

So the project **suggests** thresholding for unattended batch work but **ships no
threshold value** and no code that thresholds. The grades were introduced in
v2.34.0.

### Does anything in Docling act on them? (b) code read — no

- `docling-serve` only forwards the object: `confidence=task_result.result.confidence`
  in `response_preparation.py:42`, and no `confidence` reference in its `docs/usage.md`
  except picture-classification. Source: <https://github.com/docling-project/docling-serve>
- The layout post-processor **does** gate layout clusters on confidence, which is a
  different quantity from text correctness:
  `CONFIDENCE_THRESHOLDS = {CAPTION: 0.5, FOOTNOTE: 0.5, ... SECTION_HEADER: 0.45,
  TITLE: 0.45, CODE: 0.45, ...}` and `if c.confidence >= self.CONFIDENCE_THRESHOLDS[c.label]`.
  Source: <https://github.com/docling-project/docling/blob/main/docling/utils/layout_postprocessor.py#L129-L147>
  That drops low-confidence *layout regions*, not misread digits.
- The only OCR-side threshold that exists is EasyOCR-specific, and it **deletes text**:
  `EasyOcrOptions.confidence_threshold` default `0.5`
  ("Text with confidence below this threshold is filtered out"),
  applied at `if line[2] >= self.options.confidence_threshold` in
  [easyocr_model.py#L364](https://github.com/docling-project/docling/blob/main/docling/models/stages/ocr/easyocr_model.py#L364).
  Also `RapidOcrOptions.text_score` default `0.5` (detection-side).
  Source: <https://github.com/docling-project/docling/blob/main/docling/datamodel/pipeline_options.py>

### What the Tesseract path actually stores (b) code read

```python
text = local_reader.GetUTF8Text().strip()
# tesserocr reports 0-100; cell confidences are 0-1
confidence = local_reader.MeanTextConf() / 100.0
```

Source: <https://github.com/docling-project/docling/blob/main/docling/models/stages/ocr/tesseract_ocr_model.py#L258-L261>

Note this is per **text line** (`GetComponentImages(TEXTLINE)`), and `MeanTextConf()`
on a rectangle-scoped reader averages the words in that rectangle. So `ocr_score` is
a mean of line-level Tesseract means.

### Where the numbers can reach a consumer (b) code read

- `ConversionResult.confidence` → written to `confidence.json` in the docling archive
  (<https://github.com/docling-project/docling/blob/main/docling/datamodel/document.py#L493>)
- `docling-serve` HTTP response body
- **Not** in `DoclingDocument` JSON/Markdown export. `export_to_dict()` /
  `export_to_markdown()` carry no confidence, and no Docling item carries a
  per-item or per-span confidence provenance link. (b) reading `document.py` and the
  serializers; no serializer references `ConfidenceReport` at all.

**Licence:** MIT (`license = "MIT"` in `pyproject.toml`; LICENSE file "MIT License,
Copyright The Docling Contributors"). docling-serve also MIT.
**Air-gap:** no telemetry found in the package. Offline operation is supported via
`artifacts_path` (pre-downloaded checkpoints), `EasyOcrOptions.download_enabled`
(default `True` — set `False`), `allow_external_plugins`, and `enable_remote_services`.
Nothing calls out at inference time.

### (c) inference — why Docling cannot catch your case

`ocr_score` is an unweighted mean over every text line on the page. One misread
character inside one line among dozens moves it by a fraction of a point. In your
measurement the misread line itself still scored 71-93/100, so the page mean is
comfortably "excellent" (≥0.9) or "good". `parse_score` measures whether the
*extracted string looks like text* (`rate_text_quality` penalises `` and glyph
garbage and fragmented words — see
[page_preprocessing_model.py#L179-L204](https://github.com/docling-project/docling/blob/main/docling/models/stages/page_preprocessing/page_preprocessing_model.py#L179-L204)).
`145.60` is perfectly well-formed text, so it scores 1.0. **Both Docling scores are
structurally blind to a confidently-wrong digit.** Docling gives you a good
triage signal for "this scan is bad", not for "this number is wrong".

---

## 2. Tesseract — what the per-word confidence actually is

**Licence:** Apache-2.0 (<https://github.com/tesseract-ocr/tesseract/blob/main/LICENSE>).
Version read: 5.5.3. Fully offline.

### The formula, verbatim from source (b) code read

```cpp
int *TessBaseAPI::AllWordConfidences() {
  ...
    WERD_CHOICE *choice = word->best_choice;
    int w_conf = static_cast<int>(100 + 5 * choice->certainty());
    // This is the eq for converting Tesseract confidence to 1..100
    if (w_conf < 0)   { w_conf = 0; }
    if (w_conf > 100) { w_conf = 100; }
```

Source: <https://github.com/tesseract-ocr/tesseract/blob/main/src/api/baseapi.cpp#L1737-L1760>

That is the whole definition. Confidence is a **linear rescale of an internal
`certainty` value, then clamped to [0, 100]**. `certainty` is documented in-source as
living on roughly `[0, -certainty_scale]` where `certainty_scale` defaults to **20.0**:

- `<https://github.com/tesseract-ocr/tesseract/blob/main/src/wordrec/language_model.h#L106-L115>` — "cert is assumed to be between 0 and -dict_->certainty_scale"
- `<https://github.com/tesseract-ocr/tesseract/blob/main/src/dict/dict.cpp#L106>` — `double_MEMBER(certainty_scale, 20.0, "Certainty scaling factor", ...)`

So the arithmetic is `conf = 100 + 5 * certainty`, i.e. `certainty = (conf - 100) / 5`,
with `certainty ∈ [0, -20]`. Mapping your measurements:

| conf | implied certainty |
|---|---|
| 71 | −5.8 |
| 80 | −4.0 |
| 93 | −1.4 |

**The usable dynamic range of the number is about 20 units mapped onto 0-100.**
There is no probability anywhere in this path. (c) inference: this is why a wrong
digit prints as 71-93 — those are just ordinary middle-of-the-scale certainty values.

### It is explicitly not a probability

- The sigmoid path exists but is **off by default**. `CertaintyScore` has a
  `language_model_use_sigmoidal_certainty` branch returning `1/(1+exp(10*cert))`
  (a real probability-like transform), and the flag is initialised **`false`**:
  <https://github.com/tesseract-ocr/tesseract/blob/main/src/wordrec/language_model.cpp#L115-L116>
  Its own comment warns you must re-tune `language_model_ngram_nonmatch_score` if you
  enable it. (b) code read.
- The LSTM path multiplies certainty by a constant before it is used:
  `word_certainty = std::min(word->space_certainty, word->best_choice->certainty()); word_certainty *= kCertaintyScale;`
  <https://github.com/tesseract-ocr/tesseract/blob/main/src/ccmain/linerec.cpp#L293-L298>
- Confidence is **user-tunable**, which by itself proves it is not calibrated:
  `lstm_rating_coefficient` (default `5`) — *"Sets the rating coefficient for the lstm
  choices. The smaller the value, the better the rating"*, and in the hOCR renderer
  `float conf = 100 - tesseract_->lstm_rating_coefficient * j.second;`
  <https://github.com/tesseract-ocr/tesseract/blob/main/src/ccmain/tesseractclass.cpp#L440-L443>
  and <https://github.com/tesseract-ocr/tesseract/blob/main/src/api/hocrrenderer.cpp#L419>
  **Changing that one parameter changes your printed confidence without changing the image.**
- The man page documents `invert_threshold` (default `0.7`) as operating on a
  *different* scale — "Mean confidence threshold below which Tesseract will also
  attempt OCR on the inverted image" — while the LSTM code compares it to
  `pos_mean` from `OutputStats()`, a network output statistic. Source:
  <https://github.com/tesseract-ocr/tesseract/blob/main/doc/tesseract.1.asc>
  and <https://github.com/tesseract-ocr/tesseract/blob/main/src/lstm/lstmrecognizer.cpp#L348-L353>.
  Two different "confidences" in one project; treat any single number as scale-ambiguous.

### (a) documented — the maintainers' own framing

I did **not** find a maintainer statement saying "the confidence is calibrated" or
"threshold it at N". What the project ships instead is: the number is described
purely as *"average word confidence"*
(<https://github.com/tesseract-ocr/tesseract/blob/main/src/api/baseapi.cpp#L1719>) and
the unit is called *"arbitrary"* where it is used for a decision — OCRmyPDF's
`--rotate-pages-threshold` help text:

> "Only rotate pages when confidence is above this value (arbitrary units, default 14.0)"

<https://github.com/ocrmypdf/OCRmyPDF/blob/main/src/ocrmypdf/cli.py#L493-L494>

**No recommended threshold exists in Tesseract.** There is no `min_confidence`
parameter; `--print-parameters` exposes no OCR-accuracy cutoff. (b) code read +
(a) documented.

### Tesseract's own defences, and their limits (b) code read

Tesseract does two things that are directly relevant to your failure:

1. **Digit/letter disambiguation, opt-in via `classify_bln_numeric_mode`.** In
   `RemoveBadMatches` it explicitly rewrites confusable characters by score:
   ```cpp
   } else if (unicharset.eq(match.unichar_id, "l") && scored_one < BadMatchThreshold) {
     Results->match[Next].unichar_id = unichar_id_one;
   } else if (unicharset.eq(match.unichar_id, "O") && scored_zero < BadMatchThreshold) {
     Results->match[Next].unichar_id = unichar_id_zero;
   ```
   <https://github.com/tesseract-ocr/tesseract/blob/main/src/classify/adaptmatch.cpp>
   This is the project's own admission that `1`/`l` and `0`/`O` are the dangerous
   pairs, and that a *relative score comparison* is needed to pick between them.
   It does **not** cover `0`↔`6`, `5`↔`8`, or the `145.00` → `145.60` substitution.
2. **Confidence-gated re-read, but only for inversion.** `invert_threshold > 0` makes
   Tesseract re-run OCR on the inverted image and keep the better result. The trigger
   is `pos_mean < invert_threshold`, and it compares *network output statistics*, not
   the printed confidence.

**No threshold anywhere in Tesseract can express "re-read this line because a digit
is suspect but the score is high."** By construction, a high score means no re-read.
(c) inference.

### Character-level confidence needs a non-default flag (a) documented

Per-character confidences only appear in hOCR when `hocr_char_boxes` is enabled
(default `0`), and the docs warn the positions are approximate under LSTM:
*"character-level bounding boxes may be less precise when using the LSTM engine,
because LSTM operates at the line level."*
<https://github.com/tesseract-ocr/tesseract/blob/main/doc/tesseract.1.asc>

### Calibration research

I found **no** peer-reviewed measurement of Tesseract confidence calibration (no
reliability diagram, no ECE figure) that I could verify. **Unverified.** The nearest
substantive work located is on the general problem of unsupervised OCR output
evaluation: <https://theses.hal.science/tel-04700035> (Tanguy, PhD thesis) — I did not
read it closely enough to quote a number, so treat any figure from it as unverified.

---

## 3. The other OCR engines

| Engine | Licence | Exposes confidence | Documented threshold | Documented calibration |
|---|---|---|---|---|
| **PaddleOCR** | Apache-2.0 | yes — recognition score in the `(box, (text, score))` result | **yes, `drop_score` default `0.5`** | none |
| **EasyOCR** | Apache-2.0 | yes — third element of `readtext()` | none in-project | none |
| **Surya** | code Apache-2.0, **weights OpenRAIL-M with a commercial threshold** | yes — `confidence`, "mean per-token probability across the block's decode (0-1)" | none | none |
| **Kraken** | Apache-2.0 | yes — recognition confidence | none found | none |

### PaddleOCR — (a) documented

> `drop_score` | float | **0.5** | Results with a recognition score less than this
> value will be discarded and will not be returned as results

<https://github.com/PaddlePaddle/PaddleOCR/blob/d28d9e704a676dbf179520230be06f7a9c2b3cde/doc/doc_en/inference_args_en.md>

PaddleOCR's licence: Apache-2.0 (<https://github.com/PaddlePaddle/PaddleOCR/blob/main/LICENSE>).
Models are downloaded at runtime and can be pre-staged for offline use; no telemetry
found.

### Surya — (a) documented, and a licensing trap you must not step in

> `confidence` — mean per-token probability across the block's decode (0-1)

<https://github.com/datalab-to/surya>

So Surya's score *is* a probability, unlike Tesseract's.

Licence wording, verbatim:

> "The Surya code is licensed under Apache 2.0. **The model weights use a modified AI
> Pubs Open Rail-M license (free for research, personal use, and startups under \$5M
> funding/revenue).** For broader commercial licensing of the model weights, visit our
> pricing page here."

<https://github.com/datalab-to/surya>

This is a non-commercial-with-threshold licence on the **weights**. For a business
selling installed systems to small businesses, that is a live risk — it is exactly the
class of licence the project has been burned by. **Recommend against Surya unless
reviewed by a lawyer.** Also note Surya v2 needs a running vLLM or llama.cpp server
(auto-spawned or attached via `SURYA_INFERENCE_URL`) and the README pushes
`datalab.to` hosted credits — the hosted platform is optional, the local path exists,
but the local path is what you would be relying on.

### EasyOCR / Kraken

- EasyOCR's threshold is exposed *through Docling* (`confidence_threshold`, 0.5) but I
  did not verify a documented threshold in EasyOCR itself. **Unverified.**
- Kraken: I did not clone or read Kraken source within this session. **Unverified** on
  everything except that I found no documented threshold. Do not quote a Kraken
  threshold on my authority.

---

## 4. Document-RAG and EDMS stacks: what they do with the signal

Every one of these was checked by cloning and grepping its source, not from docs.

| System | Licence | OCR engine | Reads confidence? | Acts on it? | What it actually does |
|---|---|---|---|---|---|
| **Onyx** (ex-Danswer) | MIT outside `ee/`; **`ee/` dirs are Onyx Enterprise License** | none in-repo | **no** | **no** | no OCR in the repo at all; no tesseract/paddleocr/ocrmypdf reference |
| **Unstructured.io** | Apache-2.0 | Tesseract, PaddleOCR | **yes**, per character | yes — **deletes characters** | `TESSERACT_CHARACTER_CONFIDENCE_THRESHOLD` |
| **LlamaIndex ingestion** | MIT | delegates | no | no | no confidence handling in readers |
| **Haystack** | Apache-2.0 | delegates | no | no | no OCR/confidence handling found |
| **RAGFlow** | Apache-2.0 | own DeepDoc (PaddleOCR-lineage, now Go) | **yes**, per line | yes — **blanks text** | `DEEPDOC_DROP_SCORE` default `0.5`, plus a `0.4` layout garbage gate |
| **paperless-ngx** | **GPL-3.0** | OCRmyPDF | no | no | its `CLASSIFIER_MATCH_THRESHOLD` is about the ML metadata classifier, not OCR |
| **Mayan EDMS** | Apache-2.0 | Tesseract CLI | no | no | runs `tesseract - -` (plain text, no hOCR/TSV) → confidence never produced |
| **Open Semantic Search** | not checked | — | **unverified** | **unverified** | not investigated this session |

### Onyx — (b) code read

Grepping the whole repo for confidence returns only: OpenSearch scalar-quantization
`confidence_interval`
(<https://github.com/onyx-dot-app/onyx/blob/main/backend/onyx/document_index/opensearch/constants.py>),
the ML intent classifier's thresholds, and prompt text. There is **no OCR pipeline in
the repository**; the file connector passes a `pdf_password` and stops.
`grep -rn -i "tesseract\|paddleocr\|ocrmypdf" --include=*.py` returns **nothing**.
Confidence verdict: **not applicable — the signal is never produced.**
Licence: <https://github.com/onyx-dot-app/onyx/blob/main/LICENSE> — MIT except `ee/`.

### Unstructured.io — (b) code read, and the mechanism matters

```python
def extract_word_from_hocr(self, word, character_confidence_threshold: float = 0.0) -> str:
    """Extracts a word from an hOCR word tag, filtering out characters with low confidence."""
    ...
        character_probability = float(conf_match.group(1)) / 100
        if character_probability >= character_confidence_threshold:
            chars.append(char)
    return "".join(chars)
```

Source: <https://github.com/Unstructured-IO/unstructured/blob/main/unstructured/partition/utils/ocr_models/tesseract_ocr.py#L149-L174>

Default, verbatim: `return self._get_float("TESSERACT_CHARACTER_CONFIDENCE_THRESHOLD", 0.0)`
with docstring *"Tesseract predictions with confidence below this threshold are ignored"*.
Source: <https://github.com/Unstructured-IO/unstructured/blob/main/unstructured/partition/utils/config.py#L107-L110>

Two things follow. First, **the default is 0.0, so out of the box nothing is filtered**
(the config property is annotated `-> int` while `_get_float` is called — a type
annotation bug, harmless). Second, and (c) inference worth flagging: raising it
**deletes the character from the string**. A misread low-confidence `0` would vanish,
turning `145.00` into `145.0` — silently. That is a worse failure than the one you are
defending against. This knob is for cleaning noise, not for correcting numbers.

### RAGFlow — (b) code read, the most substantive handling found

RAGFlow has been substantially rewritten; DeepDoc now lives in Go under
`internal/deepdoc/`, and the recogniser score is a genuine probability:

```go
// Emit the real recognition confidence (mean per-char softmax prob from
// ocrRecCTCDecode) so the wire schema matches ocr.py's
// recognize_batch_with_score, 4-level nesting.
```

<https://github.com/infiniflow/ragflow/blob/main/internal/deepdoc/native/ocr_rec.go#L275-L285>

and it explicitly fixed a bug where an unreadable region scored 1.0:

> "an empty decode (no characters recognized) yields confidence 0.0, not 1.0. The old
> 1.0 sentinel made an unreadable/blank orientation outscore a real reading, which
> corrupted rotation selection and any max-confidence picker."

<https://github.com/infiniflow/ragflow/blob/main/internal/deepdoc/native/ocr_rec.go#L262-L272>

The threshold, and what it does:

```go
const DefaultDropScore = 0.5   // mirrors deepdoc/vision/ocr.py's Recognizer.drop_score
...
// Mirror the Python inference service contract: blank text whose score is
// below drop_score but preserve the real confidence
if float64(res.Score) < a.dropScore {
    return []deepdoctype.OCRText{{Text: "", Confidence: float64(res.Score)}}, nil
}
```

<https://github.com/infiniflow/ragflow/blob/main/internal/deepdoc/parser/pdf/inference/native_analyzer/native_analyzer.go#L36-L40>
and <https://github.com/infiniflow/ragflow/blob/main/internal/deepdoc/parser/pdf/inference/native_analyzer/native_analyzer.go#L185-L201>

Configurable: `DEEPDOC_DROP_SCORE` — *"overrides the confidence threshold below which
the in-process (Go) DeepDoc backend blanks recognized text while preserving the real
score. It defaults to 0.5."*
<https://github.com/infiniflow/ragflow/blob/main/internal/common/environments.go#L261-L267>

Second gate, layout-side:

```go
const GarbageLayoutScoreThreshold = 0.4
var GarbageLayoutTypes = map[string]bool{ LayoutTypeFooter: true, LayoutTypeHeader: true, LayoutTypeReference: true }
```

<https://github.com/infiniflow/ragflow/blob/main/internal/deepdoc/parser/type/types.go#L306-L320>

**Verdict: RAGFlow blanks low-confidence lines and drops low-confidence
header/footer/reference regions. Confidence is preserved alongside the blanked text,
so a caller *could* act on it — but the built-in pipeline's only action is deletion.**
A reading at 0.71 passes `drop_score` untouched. (c) inference: same blind spot as
everything else.

### paperless-ngx — (b) code read

GPL-3.0 (<https://github.com/paperless-ngx/paperless-ngx/blob/main/LICENSE>) — **note
the licence**. Its confidence machinery is entirely about the *document metadata
classifier*, never OCR:

> "Minimum confidence (0.0-1.0) for the ML classifier to assign a correspondent, ..."

`src/paperless/settings/__init__.py:98`, and `src/documents/classifier.py:53` —
*"Using predict_proba() instead of predict() lets us apply a minimum-confidence ..."*.
No `x_wconf`, no word confidence, no OCR-quality gate anywhere in `src/`.

### Mayan EDMS — (b) code read

Apache-2.0. Its Tesseract backend shells out with `arguments = ['-', '-']`
(stdin → stdout, **plain text output**), optionally adding `-l <language>`:

<https://github.com/mayan-edms/mayan-edms/blob/main/mayan/apps/ocr/backends/tesseract.py>

Plain-text output carries no confidence. To get `x_wconf` you need `hocr` or `tsv`
output configs, which Mayan never requests. **Confidence is never produced, therefore
never used.** No `confidence` matches anywhere under `mayan/`.

### OCRmyPDF — the shared layer, and it only uses confidence for rotation

MPL-2.0 (`license = "MPL-2.0"` in `pyproject.toml`). It parses `x_wconf` into
`OCRConfidence` on its element model
(<https://github.com/ocrmypdf/OCRmyPDF/blob/main/src/ocrmypdf/hocrtransform/hocr_parser.py#L341-L353>)
but the only decision it drives is page rotation, with the help text calling the units
**"arbitrary"** (<https://github.com/ocrmypdf/OCRmyPDF/blob/main/src/ocrmypdf/cli.py#L493-L494>).
It does not flag, quarantine, or re-read a page based on text confidence. (b) code read.

### LlamaIndex and Haystack

Greps for `confidence` and OCR-engine names across the reader/converter sources
returned nothing relevant. Both delegate OCR to external libraries and carry no
confidence through their document abstractions. (b) code read. Do not cite this as
"documented" — it is an absence-of-evidence finding from a shallow clone of default
branches.

---

## 5. Numeric-specific validation

**Short answer: it does not exist as a shipped, general capability in any of the
stacks above.** What exists is identifier checksums, which are genuinely strong, and
schema/type validation, which cannot help at all.

### Identifier checksums — real, offline, and the strongest thing available

**`python-stdnum`** — LGPL-2.1
(<https://github.com/arthurdejong/python-stdnum/blob/master/COPYING>), 36 top-level
module groups, pure-Python, **verified offline**: the only `http` strings in the
package are citation URLs in docstrings (the separate `online_check/` directory is
optional validation against live registries; do not use it air-gapped).

Generic API is uniform and simple:

```python
stdnum.iban.validate(number, check_country=True)   # mod-97
stdnum.isbn.validate(number, convert=False)
stdnum.ean.validate(number)                          # EAN/UPC check digit
```

<https://github.com/arthurdejong/python-stdnum/blob/master/stdnum/iban.py#L99>
<https://github.com/arthurdejong/python-stdnum/blob/master/stdnum/isbn.py#L90>
<https://github.com/arthurdejong/python-stdnum/blob/master/stdnum/ean.py#L50>

Also present: `issn`, `luhn`, `cusip`, `bic`, `bitcoin`, `casrn`, `cfi`, and country
subpackages (`us/`, `de/`, `nl/`). Note **LGPL-2.1** — fine for import-as-a-library in
a proprietary product, but it is a copyleft licence and your legal posture should
confirm that before shipping.

(c) inference: a checksum is the *only* mechanism found anywhere in this research that
can catch a single-digit OCR error **without a reference value**, because the error
detection is built into the number itself. This is the one place where "the field's
answer" is better than "route low confidence to a human".

### Invoice extraction — no arithmetic validation

**invoice2data** (MIT, `pyproject.toml:22`). Its `validate_output()` checks **field
names against a canonical vocabulary**, and nothing else:

```python
def validate_output(output, extra_fields=()):
    """Find unrecognized field names in an extracted output.
    Checks top-level fields against INVOICE_FIELDS, ``lines`` items
    against LINE_FIELDS, and ``tax_lines`` items against TAX_LINE_FIELDS.
```

<https://github.com/invoice-x/invoice2data/blob/master/src/invoice2data/extract/schema.py#L163-L190>

Module docstring: *"Validation is intentionally conservative: templates may
legitimately emit custom fields, so validate_output only flags an unrecognized field
when it looks like a typo of a canonical name."*

It **computes** `line_tax_amount` from `price_subtotal` when missing
(`invoice_template.py:466-478`) but that is derivation, not verification — there is no
check that line items sum to a subtotal, no `subtotal + tax == total` assertion, no
tolerance band. (b) code read. Note it operates on text-layer or already-extracted
text, not on OCR confidence at all.

### The structured-extraction frameworks validate syntax, not truth

(c) inference, but a load-bearing one: JSON-Schema / Pydantic validation on
LLM-extracted fields enforces *type and shape*. `145.60` and `145.00` are both valid
decimals. **A schema validator cannot distinguish them.** This is a structural
property of schema validation, not a gap in any particular library. I did not find a
mainstream framework that ships range, referential, or cross-field consistency checks
for document extraction — so this is a genuine hole rather than something I failed to
find. Treat "no such shipped capability" as **unverified-but-probable**; I checked
invoice2data and the RAG stacks directly, not every framework in this space.

---

## 6. Direct answer to the question

### Is there a standard self-hostable pattern?

Yes, and it is thin. Every system studied that handles confidence at all does one of
exactly three things:

1. **Nothing.** Confidence is never parsed. Onyx (no OCR at all), Mayan (plain-text
   Tesseract output), paperless-ngx, LlamaIndex, Haystack, OCRmyPDF (except page rotation).
2. **Delete the text.** Below a threshold, remove it. PaddleOCR `drop_score=0.5`
   ("discarded and will not be returned as results"), RAGFlow `DEEPDOC_DROP_SCORE=0.5`
   (blanks the string, keeps the score, and drops footer/header/reference regions below
   `0.4`), Unstructured `TESSERACT_CHARACTER_CONFIDENCE_THRESHOLD` (default `0.0` =
   off; drops individual characters).
3. **Report a document-level score and let the integrator decide.** Docling's
   `ConfidenceReport` with `poor`/`fair`/`good`/`excellent` grades at 0.5/0.8/0.9, and
   its advice to "set confidence thresholds for unattended batch conversions" — with
   the threshold left entirely to you.

Tesseract, the engine under most of this, provides no threshold and no calibration
guidance; its per-word number is a linearly rescaled internal certainty
(`100 + 5 * certainty`, `certainty ∈ [0, -20]`), tunable by a parameter that changes
the printed confidence without touching the image.

### Does anything catch a confidently wrong reading?

**No — not one system studied.** Nothing in this research flags, quarantines, or
re-reads a page based on OCR confidence in a way that survives a high score. The only
two mechanisms that gate on confidence (RAGFlow's drop score, Unstructured's character
filter) are **deletion** gates sitting below the score your failure produces. A
reading at 71-93 passes all of them.

The field's answer, stated honestly, is:

> **Route low confidence to a human. Accept that high-confidence errors get through.**

Docling's own documentation is the clearest expression of this: it hands you a grade
and tells you to build the unattended-batch threshold yourself. Nobody in the
open-source landscape has built the thing you would actually want — a per-number
plausibility check.

### (c) inference — where the leverage actually is

The confidence signal is not merely weak here; it is **the wrong signal**. Three
verified facts make that concrete:

1. Tesseract's number is a rescaled certainty with ~20 units of usable range spread
   over 0-100. Wrong digits land mid-scale. There is no scale position that means
   "I substituted a glyph that looks like this one".
2. Docling's `ocr_score` is a mean over all text lines on the page, so a single digit
   is diluted to irrelevance. `parse_score` asks only whether the string looks like
   text, and `145.60` does.
3. Every threshold anyone ships is a *deletion* threshold below 0.5-0.6, i.e. tuned to
   discard noise, not to flag plausible-but-wrong content.

So the defensible pattern for your stack is not "add a confidence threshold". The
things that would actually catch `145.00` → `145.60`, in descending order of strength:

- **Checksums where the document gives you one** — IBAN mod-97, EAN/ISBN/UPC check
  digits, VAT/EIN formats. `python-stdnum` (LGPL-2.1) does this offline. This is the
  only mechanism that catches a single-digit error with no reference value.
- **Cross-field arithmetic on structured documents.** Line items summing to a
  subtotal, subtotal + tax = total, a stated rate × base = tax. Nothing open source
  ships this for invoices; it has to be written against your document types. The
  tolerance band is what makes it work: a real invoice balances to the cent, so a
  one-digit OCR error breaks the identity loudly.
- **Field-format and range constraints derived from the business domain** — a mileage
  rate, a tax percentage, an invoice total within an order of magnitude of the
  vendor's history. Pydantic can express these; nobody ships them for you.
- **Two-engine agreement on the numbers only.** Docling with Tesseract and Docling
  with RapidOCR both run locally; you already have tesserocr, RapidOCR, EasyOCR and
  Tesseract available. Where the two agree, confidence is high. Where they disagree on
  a digit, you have a genuine flag — and it does not depend on either engine's score
  being meaningful. I found **no shipped project that does this**; treat it as your
  own design, not prior art. **Unverified** as an established pattern.

And keep using Docling's `low_grade` for what it is good at: triaging *bad scans*
(a page where the mean is poor is a page worth re-scanning). Just do not expect it to
catch a good scan with one wrong digit.

---

## 7. Licence summary for anything recommendable

| Component | Licence | Safe here? |
|---|---|---|
| Docling, docling-serve | MIT | yes |
| Tesseract, tesseract / tesserocr | Apache-2.0 | yes |
| RapidOCR (ONNX) | Apache-2.0 | yes (verified in Docling's dependency set; licence not independently re-read) |
| PaddleOCR | Apache-2.0 | yes |
| EasyOCR | Apache-2.0 | yes |
| Unstructured.io | Apache-2.0 | yes |
| RAGFlow | Apache-2.0 | yes |
| Haystack, LlamaIndex | Apache-2.0 / MIT | yes |
| Onyx | MIT **except `ee/` dirs = Onyx Enterprise License** | partial — avoid `ee/` |
| paperless-ngx | **GPL-3.0** | copyleft; review |
| Mayan EDMS | Apache-2.0 | yes |
| OCRmyPDF | MPL-2.0 | yes |
| python-stdnum | **LGPL-2.1** | copyleft, library use; review |
| invoice2data | MIT | yes |
| **Surya** | code Apache-2.0, **weights OpenRAIL-M, free only under \$5M funding/revenue** | **no — non-commercial threshold on weights** |

Rejected on your existing precedent: PyMuPDF (AGPL). Nothing studied here is AGPL —
the near-misses are Surya's weight licence (the important one), paperless-ngx's GPL-3.0,
and python-stdnum's LGPL-2.1.

## 8. Air-gap check

All of the following run fully offline once models are staged: Tesseract (Apache,
self-contained `traineddata`), Docling (`artifacts_path` + `download_enabled=False` for
EasyOCR; no telemetry found), PaddleOCR (runtime model download, pre-stageable),
EasyOCR (same), RAGFlow (in-process ONNX DeepDoc, `DEEPDOC_MODEL_DIR`), OCRmyPDF,
Mayan, paperless-ngx, python-stdnum (pure-Python, offline — verified), invoice2data.

Flagged: **Surya** auto-spawns a vLLM/llama.cpp server and its README steers to
`datalab.to` hosted credits (the local path exists, but the commercial posture is the
problem, not the network). **Onyx** core function is fine but is a full web platform
with connectors that assume SaaS sources; and the brief's scope here was OCR only, so
I did not assess its egress in general.

## 9. What I could not verify

State these as unknown rather than asserting them:

- **Kraken** — not cloned, not read. No licence, confidence API, or threshold claim is verified here.
- **Open Semantic Search** — not investigated at all.
- **EasyOCR's own documented threshold** — only Docling's wrapper of it is verified.
- **Tesseract confidence calibration research** — no rigorous ECE/reliability-diagram study located.
- **`llama_index` / `haystack` no-confidence findings** — from grepping shallow clones of default branches, not from exhaustive reading.
- Whether Unstructured's per-character filter is reachable through its current high-level `partition_pdf` path, or only the lower-level OCR-agent interface. Both call sites pass the threshold through (`tesseract_ocr.py:58` and `:87`), but I did not trace the full call chain.
