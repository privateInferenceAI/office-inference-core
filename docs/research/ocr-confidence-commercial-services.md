# Commercial OCR: does anything catch a confidently wrong reading

Research date 2026-10-12. Nine vendors, all read from primary sources. Claims are labelled **[V]**
documented by the vendor, **[3P]** third party only, **[inf]** inference. Every claim carries a URL.

Scope: AWS Textract, Azure AI Document Intelligence, Google Cloud Document AI, ABBYY Vantage and
FineReader, Tungsten (formerly Kofax) TotalAgility, Hyperscience, Rossum, Instabase, UiPath Document
Understanding.

## Headline

No vendor ships a confidence score that catches the failure this system measured, where a wrong digit is
read at 71 to 93 out of 100. Not one documents a calibrated, trustworthy per-word probability.

But six of the nine document a **second, independent constraint that runs regardless of confidence**,
and that is the actual pattern in the space. Confidence triages. Independent constraints catch the
checkable subset. A human catches the rest.

## The finding that matters most: nobody claims to catch it from the reading alone

**[V]** Hyperscience states the limit of the number outright, and it is the most defensible support for
this system's own measurement:

> "The confidence score is used internally to compare predictions. It is not directly equivalent to a
> percentage and should not be interpreted as an exact probability."

<https://help.hyperscience.ai/v42/docs/transcription-models-overview>

**[V]** Rossum claims calibration and then concedes it is unsolved in the same document:

> "We calibrate the confidence scores to correspond to the probability of a given value being right...
> In practice, estimating this accuracy correctly in all cases is still an unsolved problem in
> Artificial Intelligence. Our research team is working hard to improve the accuracy of our estimates.
> Nevertheless, we recommend you conduct experiments on your particular data set to ensure the
> confidence scores match your needs."

<https://knowledge-base.rossum.ai/docs/using-ai-confidence-thresholds-for-automation-in-rossum>

**[V]** Microsoft names this system's exact failure mode in a table and offers no runtime defence. The
Low-accuracy / High-confidence cell reads:

> "This result is most unlikely. • For low accuracy scores, add more labeled data or split visually
> distinct documents into multiple models."

<https://learn.microsoft.com/en-us/azure/ai-services/document-intelligence/concept/accuracy-confidence?view=doc-intel-4.0.0>

So a system that reads `145.00` as `145.60` and reports high confidence is, per the largest vendor in
the space, an unlikely result with a retraining remedy rather than a detection one.

## The pattern six of nine document: a constraint that must hold regardless of the score

| Vendor | Mechanism | Source |
|---|---|---|
| Google Document AI | CEL validation rules over final entities. "check if the sum of line item prices equals the total value"; a boolean result per rule | <https://docs.cloud.google.com/document-ai/docs/ce-validation> |
| Rossum | Five parallel `validation_sources`, confidence among them. "Validation error will always stop a document from being automated for both confident and always levels of automation" | <https://knowledge-base.rossum.ai/docs/automation-rules-overview> |
| ABBYY | Manual Review carries a `rulesReport` pane of rule errors beside the page image and the field list | <https://abbyy.mintlify.app/vantage/developer/integrating-manual-review> |
| Tungsten | Four parallel invalidity reasons. "the extracted result may not meet the configured criteria, such as being a valid data type, or being within a numerical range" | <https://docshield.tungstenautomation.com/KTA/en_US/8.1.0-rmx0b1ux3q/help/CaptureClient/ValidationActivity/CEBPM_validationactivity_help/t_validatingdocuments.html> |
| Instabase | Checkpoint validations: type and format, comparison equations, external Python. "trigger a review for failed validations" | <https://docs.instabase.com/flow/step-config-reference/creating-validation-checkpoints> |
| AWS Textract | `Sampling` for audit. "Audit your ML model by randomly sampling all forms analyzed by your model and sending a specified percentage to humans" | <https://docs.aws.amazon.com/sagemaker/latest/dg/a2i-json-humantaskactivationconditions-textract-example.html> |

The Rossum sentence is the mechanism in one line: a failed validation blocks automation whether the
confidence is 0.99 or not. The inverse is the limit, and it applies to this system directly. A misread
that converts cleanly produces no validation error, and `145.60` is a valid decimal. So a
format-validity rule does not touch this system's failure at all. Only a rule that ties the number to
something else does: the line items summing to the subtotal, the amount appearing again elsewhere, the
value falling inside a plausible range.

## Three other findings worth keeping

**A pre-OCR scan-quality signal, which is the only axis not produced by the model that made the error.**
**[V]** Google's `ImageQualityScores` carries a `qualityScore` and a `detectedDefects[]` list with named
defect types: `quality/defect_blurry`, `quality/defect_noisy`, `quality/defect_dark`,
`quality/defect_faint`, `quality/defect_text_too_small`, `quality/defect_document_cutoff`,
`quality/defect_text_cutoff`, `quality/defect_glare`.
<https://docs.cloud.google.com/document-ai/docs/reference/rest/v1/Document> Hyperscience documents an
"Image Readability" setting along the same lines
<https://help.hyperscience.ai/v42/docs/identification-settings>. **[inf]** This is the same axis this
system's blur probe measured by hand, which found blur moving the score and speckle not moving it. It
cannot catch a clean scan of a misread character, but it is orthogonal and cheap, and it is the only
signal class here that does not come from the recogniser itself.

**Aggregate with the minimum, not the mean.** **[V]** Instabase: "In multi-word results, the lowest
confidence score across all words is used. For example, if Amy returns a confidence score of 95 percent,
but Cooper returns a confidence score of 65 percent, the reported OCR confidence score for the account
holder field is 65 percent."
<https://docs.instabase.com/automate/validating-documents> **[inf]** This is the correct aggregation for
a number and the opposite of what a page mean does, which is why Docling's page-mean `ocr_score` could
never have caught a single bad digit.

**Two scores, not one, and they diverge.** **[3P]** UiPath's `FieldValue` carries `Confidence` and
`OcrConfidence` as siblings, on the third-party API mirror, so verify the field names before designing
to them: <https://raw.githubusercontent.com/api-evangelist/uipath/refs/heads/main/openapi/uipath-validation-api-openapi.yml>
**[V]** Azure says the same in prose: "The confidence of the read results doesn't affect the confidence
of the key/value extraction results, so you should check both."
<https://learn.microsoft.com/en-us/azure/ai-services/document-intelligence/concept/accuracy-confidence?view=doc-intel-4.0.0>

**A threshold design that sidesteps the uncalibrated number entirely.** **[V]** Hyperscience discards the
confidence threshold once a model is trained and has the operator set a target accuracy instead: the
system "uses the Field Identification Target Accuracy and the Table Identification Target Accuracy to
calculate the optimal confidence threshold that will allow the system to reach the target accuracy with
the minimum amount of manual effort", with defaults of 95 percent for fields and 96 percent for tables.
<https://help.hyperscience.ai/v42/docs/identification-settings> It needs labelled history to do it,
minimum 5,000 QA records for structured text
<https://help.hyperscience.ai/v42/docs/transcription-accuracy-and-automation>, so it is not available to
this system on day one. It is the right shape for year two.

**And a scoring rule worth stealing.** **[V]** Hyperscience scores a field as correct only when the whole
field is correct: an SSN transcribed as `123-45-678` is "0% accurate, even though 8 of the 9 digits are
correctly transcribed". Its confidence gate also decides what gets scored at all, so a confidently wrong
value is exactly the one that is measured against human QA and fed back.
<https://help.hyperscience.ai/v42/docs/scoring-transcription-accuracy> **[inf]** That is a measurement
design rather than a runtime detector, and it is what this system would need to know its own real error
rate rather than the rate a probe guesses at.

## On-premises and air-gap, which one half of this product depends on

Ranked by the quality of the documented evidence.

- **Hyperscience**: **[V]** on-premises or private cloud on Linux VMs with Docker or Podman, documented
  OS support and sizing, and an explicit note that the single-VM deployment is for evaluation only and
  production needs HA/DR. <https://help.hyperscience.ai/deployment/docs/tve-poc-infrastructure-requirements>
- **UiPath**: **[V]** "For offline (air-gapped) installations, the downloaded bundle must be renamed to
  `du-ondemand.tar.gz` before uploading it to your private container registry."
  <https://docs.uipath.com/document-understanding/automation-suite/2024.10/user-guide/offline-bundles-installation>
- **ABBYY**: **[V]** self-hosted on Kubernetes with install-time registry access and then offline. 3.0
  pushes real operating burden onto the customer: "ArgoCD, the service mesh, ingress controller, secrets
  integration, and monitoring stack are third-party prerequisites that you install and maintain."
  <https://abbyy.mintlify.app/vantage/self-hosted/v3.0/introduction>
- **Azure**: **[V]** disconnected containers exist and are intended for this, but need a commitment plan
  and a licence file minted in connected mode that "contains an expiration date, after which the license
  file is invalid".
  <https://learn.microsoft.com/en-us/azure/ai-services/document-intelligence/containers/disconnected>
  **[inf]** Recurring touchpoint rather than permanent isolation.
- **Instabase**: **[V]** documented as blocked when egress is blocked. Its network requirements table
  marks usage metering, Azure Cognitive Services billing and LLM provider access as "Critical",
  with "blocking this connection blocks digitization of new files."
  <https://docs.instabase.com/admin/network-architecture> **Fails the constraint.**
- **Textract, Google Document AI, Rossum**: no on-premises option found. Excluded for the in-office
  build.

## Unverified, and not to be cited as fact

- Whether Tungsten's multiple recognition engines use disagreement between them as an error signal. The
  docs list the engines but describe no voting or ensemble mechanism, and the "Conflict management" page
  body was not readable. This is the one page worth reading by hand, because a shipped multi-engine
  disagreement check would be directly applicable.
- Whether ABBYY's `IsSuspicious` per-character flag fires on a `0` to `6` misread. The property is
  documented; its detection logic is not.
- Whether UiPath's "generative validation" is a true second-model cross-check. The blog body did not
  extract and the product docs do not describe it as an error detector.
- The UiPath `FieldValue.Confidence` and `OcrConfidence` names come from a third-party API mirror rather
  than UiPath's own documentation. Verify in their Swagger before designing to them.
- Microsoft's calibration claim, that a field confidence of 0.95 means the prediction is right 19 times
  in 20, is stated without evidence or benchmark. Treat as a claim.
- Instabase's "in internal testing, this method proves reliable", which cites no benchmark.

## What this means for this system

Three independent lines of research now agree. The open-source survey found no project that catches a
confidently wrong reading. This survey finds no vendor that claims to either. The verification-practice
survey found the cheque industry has never trusted a single reading at any confidence, and requires two.

So the direction is not a better threshold. It is: triage with confidence and scan quality, catch what a
constraint can catch, and get a human in front of the rest with the image beside the value.

Ranked by what this system can actually do, cheapest first:

1. **Format and range checks** on extracted figures. Nearly free, already the shape of the citation
   verifier's literal check, and they cover the nine of fifteen malformed readings the substitution
   probe produced. They do not touch a plausible wrong number.
2. **Arithmetic and cross-field rules**, which is what Google, Rossum, ABBYY, Tungsten and Instabase all
   ship in some form. Line items summing to a subtotal, subtotal plus tax equal to the total. This is
   the pattern with the most vendor agreement behind it.
3. **A second, independent reading of any cited figure**, which the cheque world has done for decades and
   no open-source project ships. This system already carries Tesseract and RapidOCR, so the cost is a
   re-crop and a comparison.
4. **Human review of the residue**, with the page image beside the proposed value, which is the "key from
   image" screen the capture industry has shipped for years.
5. **Record the override**, machine-proposed against human-accepted, which is how the real error rate
   becomes knowable at all.
