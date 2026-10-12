# An uncertain read: what the research says to build

Synthesis of four research angles, 2026-10-12. The four reports are in this directory:
`ocr-confidence-prior-art.md` (open-source and self-hostable stacks), `ocr-confidence-commercial-services.md`
(nine commercial vendors), `ocr-confidence-verification-practice.md` (cheques, accounting, medical,
legal, and the review screen), and `ocr-confidence-literature.md` (the papers).

This document states the conclusion and the evidence for it. It exists because the design in
`docs/search-architecture.md` rested on confidence as the trigger for human review, and the measurement
in `docs/runbook.md` step 3.4 took that away.

## The conclusion in one line

A Tesseract confidence of 90 is not a 90 percent chance of being correct, calibration cannot fix that
because it is monotone, and what catches a confidently wrong digit is redundancy the defect cannot
corrupt twice.

## What was measured here, first

`docs/runbook.md` step 3.4. Forty trials carved defects into the cell whose truth is `145.00`. Fifteen
readings changed the value and confidence flagged one, seven percent. Fourteen wrong numbers passed at
confidence 61 to 93, among them `145.60`, `146.00` and `145.08`. Nine were malformed, a letter in the
number or the wrong digit count. Six were plausible numbers with a wrong digit.

## Four independent lines of evidence agree

**No open-source project catches it.** Every confidence gate in the self-hostable landscape is a deletion
gate below 0.5 or 0.6, far under the 61 to 93 measured here. Unstructured deletes the character and
RAGFlow blanks the line, which for a number is worse than doing nothing: delete a low-confidence zero and
`145.00` silently becomes `145.0`. Docling computes a page-mean OCR score, acts on none of it, and cannot
see the failure structurally, because one misread digit in one line among dozens barely moves a mean.

**No vendor claims to catch it.** Hyperscience states that confidence "is not directly equivalent to a
percentage and should not be interpreted as an exact probability". Rossum claims calibration and then
concedes in the same document that "estimating this accuracy correctly in all cases is still an unsolved
problem in Artificial Intelligence". Microsoft's own accuracy table names this exact case, Low accuracy
with High confidence, calls it "most unlikely", and offers retraining rather than detection.

**The industries that need a right number never trusted one reading.** A cheque carries its amount twice,
in digits and in words, and the two are read independently with disagreement triggering review. The
Comptroller's cash-handling procedure specifies blind dual control, where the second checker does not
know the first answer. Six of the nine commercial vendors now ship the same shape: an independent
constraint that runs whatever the score says. Rossum puts the mechanism in one line, that a failed
validation blocks automation "for both confident and always levels".

**The literature says the fix was never available in the confidence number.** Tesseract's value is
`100 + 5 × certainty` for a certainty in `[-20, 0]`, clipped at the ceiling, and the LSTM path computes
it differently under the same field name. Kae, Huang and Learned-Miller published this system's finding
in 2009: "thresholding these confidence values, even at very high values, still produces some errors",
with no threshold producing a clean word list. And the structural reason calibration is not the answer,
from ConfBench on 1,346 degraded invoice variants: rescaling "is monotone and so leaves AUROC and ECARB
essentially unchanged". Temperature scaling, isotonic regression and Platt scaling are all monotone, so
none can make a confidently wrong reading rank as suspicious.

## What that leaves, ranked by evidence and cost

**1. Format, range and checksum checks.** Nearly free, and already the shape of the citation verifier's
literal check. Measured ceiling from the literature: only 24.39 percent of misread fields fall outside
known error patterns, so this catches the malformed nine of our fifteen and nothing else. Worth having
and worth being honest about. Where a field carries a check digit, this becomes stronger than a
heuristic: weighted mod-10 with weights coprime to ten detects every single-digit substitution, and
Verhoeff and Damm also catch adjacent transpositions, as a mathematical property rather than a
statistical one. No study has measured this on real OCR output, so the guarantee is theoretical.

**2. Cross-field arithmetic.** Line items summing to a subtotal, subtotal plus tax equal to the total.
This is the pattern with the most vendor agreement and the one Google documents explicitly for exactly
this purpose. It catches a wrong digit only when the misread breaks an equation, and the residual
localises which line is bad. Flagged as unverified: no peer-reviewed measurement of the detection rate
on real invoices was found, only vendor documentation.

**3. A second independent reading, used as an escalation gate and not as a vote.** This is the cheque
answer and the only mechanism found that can touch the plausible wrong number. The design constraint is
sharp and comes from the literature: vary the *stage* as well as the engine, because two engines over the
same preprocessed pixels make correlated errors and will agree on the wrong answer. And treat disagreement
as a request for a person, never as a correction, because in the one measured case 26.8 percent of
disagreements were both-wrong and the ensemble recovered only 56.9 percent of those.

That measurement came from a thesis on synthetic Telugu images, so it does not transfer to this system's
documents. **The equivalent measurement does not exist for damaged printed English digits, and finding it
is this system's own work.** Which makes the honest next step an experiment rather than an implementation:
re-run the substitution probe with two engines staged differently, and report how often they disagree on
a number, how often they disagree *and* the value is wrong, and how often they agree *and* it is wrong.

**4. Human review of the residue, with the image beside the value.** The screen has an established name
in the capture industry, key from image, and an established shape: one field at a time, the source
fragment adjacent, keyboard driven, ordered by risk. Risk means low confidence, or high value, or a failed
constraint. **5. Record the override**, machine-proposed against human-accepted, which is the Expensify
pattern and the only way the real error rate becomes knowable.

## Two methods to avoid

**Do not tune a threshold on the same set you report the error rate from.** Diagnosed at length in the
literature: score-refit leakage produced a reported coverage of 0.416 at a risk of 0.127 while violating
the intended bound in 95 percent of splits, and document clustering gives an effective sample far smaller
than the field count. Split fitting from reporting, and sample per document rather than per field.

**Do not swap the pipeline to a vision-language model on current evidence.** The comparison this would
need, VLM against Tesseract on genuinely damaged scans of printed numbers, does not appear to exist. The
closest degradation study explicitly disclaims simulating real degradation, and its synthetic
perturbations include snow. Open-weight model uncertainty is in the same overconfident regime as
Tesseract, AUROC 0.58 to 0.72. And the failure mode is worse than a misread digit: fabrication carries no
confidence penalty and no format-check penalty. If a VLM is added at all, it belongs as an independent
second reader, because a different architecture is a genuinely independent failure mode.

## Three licence findings to record

Docling ships presets for models whose licences are not clear, which is a way for an unresolved licence to
enter this stack without anyone choosing it.

- **MinerU**, through Docling's `mineru2_pro` preset. The weights card says `agpl-3.0`; the repository
  `LICENSE.md` says Apache-2.0 with a commercial threshold above 100M monthly active users or USD 20M
  revenue. Unresolved, and this project rejects AGPL. Treat as not cleared until confirmed with the
  maintainers in writing.
- **Nanonets-OCR-s and OCR2**, through Docling's `nanonets_ocr2` preset. No licence declared on the
  model card, no LICENSE file, and the organisation's repository reports no licence. Treat as
  all-rights-reserved.
- **Docling's `deepseek_ocr` preset** is documented as API-only, with Transformers and MLX both
  unsupported. Verify offline operation before assuming it, because an API-only preset in an air-gapped
  build is a defect waiting to happen.

Cleared with permissive licences, for the record: DeepSeek-OCR (MIT), dots.ocr (MIT),
Granite-Docling-258M (Apache-2.0), PaddleOCR-VL (Apache-2.0), olmOCR-2-7B (Apache-2.0), GOT-OCR2_0
(Apache-2.0), Qwen3-VL-8B (Apache-2.0), InternVL3.5-8B (Apache-2.0), Donut (MIT), and docling-models
(CDLA-Permissive-2.0 and Apache-2.0).

## What is still unknown, and would change the recommendation

- Whether two independently staged engines disagree usefully on this system's documents. The experiment
  above answers it, and it is the only item here that changes what gets built.
- Whether cross-field arithmetic catches a useful share of wrong invoice figures in practice. No
  published measurement exists.
- Whether check-digit validation ever applies to this system's fields at all. Fee schedules, policies and
  correspondence rarely carry check digits, so this may be an option for nothing. Invoice and account
  numbers are where it would live.
- Chapter and verse on Tesseract's certainty constant `k`, which its own header never gives a value for.
