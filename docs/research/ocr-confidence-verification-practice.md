# How document-heavy operations verify a machine-read figure

Research date 2026-10-12. Claims are labelled **(a)** documented practice, **(b)** reported practice,
**(c)** inference. Every claim carries a source URL. Written for the decision recorded in
`RET-PARSE-002`.

## Why this was researched

A measurement on this system's own parser found that OCR reads a wrong digit with high confidence:
`145.00` came back as `145.60`, `146.00` and `145.08` at confidence between 71 and 93 out of 100, and the
confidence score flagged one of fifteen such errors. A first pass over open-source prior art found no
project that catches this. The question put to this angle was what document-heavy operations do about it
in practice, on the grounds that cheques, invoices and claims have needed a right number for decades.

## Headline

Across every domain that could be verified, the control that catches a wrong number is not a more
careful re-reading of the same pixels. It is one of three things:

1. **A second, independent reading of the same value** — cheque courtesy amount against legal amount.
2. **A cross-check against a different system's copy of the number** — bank feed against invoice total,
   purchase order against receipt against invoice.
3. **A human comparing the image against the extracted value**, reserved for exceptions.

Confidence scores decide *what* a human sees. Nowhere in the sources examined are they treated as
evidence that a number is right.

## The operator's own vendor puts a financial figure at 90 percent or higher

**(a) Documented.** Amazon Textract's best-practices page is the citable threshold source, and the
sentences are worth having exactly:

> "A confidence score is a number between 0 and 100 that indicates the probability that a given
> prediction is correct."

> "In applications that are sensitive to detection errors (false positives), enforce a minimum
> confidence score threshold. The application should discard results below that threshold or flag
> situations as requiring a higher level of human scrutiny."

> "The optimal threshold depends on the application. For archival purposes, such as documenting
> handwritten notes, it might be as low as 50%. **Business processes involving financial decisions might
> require thresholds of 90% or higher.**"

<https://docs.aws.amazon.com/textract/latest/dg/textract-best-practices.html> — verified by reading the
page directly on 2026-10-12.

Two things follow for this system. A rate read at 88 is below the vendor's own band for a financial
figure, even though 88 looks high. And the routing AWS describes is **per field**, not per page: "if
inference confidence for that word (or form key) falls below your confidence threshold, the form and
prediction go for human review" (<https://aws.amazon.com/blogs/machine-learning/using-amazon-textract-with-amazon-augmented-ai-for-processing-critical-documents/>).

## Cheque processing: the closest and strongest prior art

**(a) Documented.** A cheque carries its amount twice, once in digits and once in words, and the
industry reads both independently. CAR is Courtesy Amount Recognition, the digits; LAR is Legal Amount
Recognition, the words. Alogent's definition: CAR "recognizes the numerical value of the check's amount,
regardless of any discrepancies between the written (legal) amount and the numerical (courtesy)
amount", and "checks with significant discrepancies between the written and numerical amounts will
typically trigger a review process." Under the UCC, when the two disagree, the written amount is the
legal amount. <https://www.alogent.com/banking-definitions/courtesy-amount-recognition>

The control is stated outright in government procedure. IRS Internal Revenue Manual, Manual Deposit
Process: "For manual deposit checks, verify Magnetic-Ink Character Recognition (MICR) encoded amount
agrees with written amount of remittances." <https://www.irs.gov/irm/part3/irm_03-008-045r> — snippet
level only; the page is very large and could not be read in full, so treat this as snippet-verified.

**A checksum catches a single wrong digit with no reference value.** The routing transit number's ninth
digit is a checksum over the other eight with weights 3-7-1:
`(3(d1+d4+d7) + 7(d2+d5+d8) + (d3+d6+d9)) mod 10 = 0`. "High-speed check-sorting equipment will
typically verify the checksum and if it fails, route the item to a reject pocket for manual examination,
repair, and re-sorting." It catches any single-digit error and most transpositions, and it does **not**
catch transpositions of two digits differing by five, which are 0/5, 1/6, 2/7, 3/8 and 4/9.
<https://en.wikipedia.org/wiki/Routing_transit_number> — secondary source. The authoritative ANSI X9 text
was not retrieved, so the weighting should be re-verified before it is cited in writing.

**Two people, neither knowing the other's answer.** City of Anaheim cash-handling procedure:
"Dual control means that two associates must verify that the dollar amount is correct. **Blind** means
that the second associate does not know."
<https://local.anaheim.net/docs_agend/questys_pub/MG37952/AS37991/AS37994/AI39402/DO39404/DO_39404.pdf>
— snippet level, PDF not fully readable.

**What a second check is worth.** ISMP/ECRI, on independent double checks of high-alert medication, which
is the same arithmetic in a different domain: "Numerous studies have demonstrated the ability of
independent double checks to detect up to 95% of errors. Based on this, an error rate of 10% (1 in 10)
can be reduced to 0.5% (1 in 200)." The same source is careful about the limit: it is "a weaker
error-reduction strategy, particularly if this is the only safeguard in place", and the guidance is to
apply it selectively rather than to everything.
<https://home.ecri.org/blogs/guidance-insights-tools/independent-double-checks-worth-the-effort-if-used-judiciously-and-properly>

**A vendor's accuracy claim, read carefully.** OrboGraph claims "99%+ read rates and 99.5%+ accuracy on
checks" <https://orbograph.com/anywhere-recognition/> — **(b)** vendor marketing, snippet level. The
useful reading is that 99.5 percent accuracy on a field still means about one cheque in two hundred has
a wrong amount. That is the number that justifies an entire exception-review apparatus in an industry
that has spent decades on the problem.

## Accounting: the control is matching, not proofreading

**(a) Documented.** Xero's bank reconciliation is a matching operation: "In Xero, this is done on the
Reconcile tab, by matching bank statement lines from your bank account on the left against transactions
in Xero on the right." The feed is privileged over manual entry: "Only manually mark a transaction as
reconciled if you can't import the original bank statement data from the bank."
<https://central.xero.com/0/article/Bank-reconciliation-in-Xero>

**(b) Reported, vendor product blog, but unusually specific.** Expensify describes a layered system that
matches this system's problem closely:

> "even the best OCR in the world only gets a receipt right around 85% of the time... there is nothing
> that reliably gets real-world receipts correct above 85% of the time."

> "After the receipts go through OCR/parsers, if we're not incredibly confident the receipt is correct,
> we have a human check the inputs to make sure they match what our OCR technology has."

They also match receipts against bank feeds, and they record the distinction this system needs:

> "we take the receipt scanning a step further and let the admins know which receipts have been manually
> changed vs what our receipt scanning technology produces."

<https://use.expensify.com/blog/mastering-accuracy-for-receipts>

**Three-way matching, productised.** Ramp's Bill Pay matches bills against purchase orders and item
receipts and renders the verdict on the bill's own line items: either a green "All billed units have been
received", or an explicit "Bill showing 25 billed units have not been received" with the item receipt
openable from the bill. <https://support.ramp.com/3-way-match-with-ramp-procurement> **(a)** documented
product behaviour. BILL announced automated two- and three-way matching along the same lines.
<https://www.bill.com/blog/announcing-automated-three-way-matching-with-bill>

**What was not found.** Hubdoc publishes "docs and their data" to Xero, QuickBooks Online and BILL and
documents supplier rules, but no per-field confidence display and no verify-against-image step in its
public documentation <https://www.hubdoc.com/how-it-works> — a reported absence, not proof of absence.
Dext's confidence mechanics could not be verified; only a marketing claim of "99% accuracy" was
retrievable. QuickBooks Online's receipt-capture verification behaviour was **not researched**. Do not
treat either as covered.

## Medical records and claims

**(a) Documented.** Independent double check is the named control for numbers that matter, and the design
detail is that the two checkers must not know each other's result, because a second person who can see
the first person's answer is not an independent check.
<https://home.ecri.org/blogs/guidance-insights-tools/independent-double-checks-worth-the-effort-if-used-judiciously-and-properly>

Where the machine cannot read a value, a named human attests, and the attestation is a formal signed and
dated artifact rather than a checkbox. CMS: "We accept a signature attestation for all medical
documentation except orders when required. The attestation must be associated with a medical record..."
and it "must be signed and dated by the author of the medical record."
<https://www.cms.gov/files/document/mln905364-complying-medicare-signature-requirements.pdf> — snippet
level, PDF. The consequence of a missing signature is severe: "If a signature is missing from an order,
claims reviewers will disregard the order during the review of the claim."
<https://www.cms.gov/Outreach-and-Education/Medicare-Learning-Network-MLN/MLNMattersArticles/downloads/mm6698.pdf>
— snippet level, PDF.

**The regulation requires logging, not verification.** HIPAA Security Rule technical safeguards, 45 CFR
164.312(b), audit controls: implement mechanisms that record and examine activity in systems containing
ePHI. <https://www.govinfo.gov/app/details/CFR-2024-title45-vol2/CFR-2024-title45-vol2-sec164-312>
**(c)** The regulation appears to give the logging obligation, and the clinical-safety literature gives
the double-check practice. They are separate sources of obligation.

## Legal e-discovery: a negative finding

**This is a negative finding and should be treated as one.** No Sedona Conference publication, EDRM
document or court rule was found in this session that requires verifying machine-read text against the
original image, or disclosing that text was machine-generated.

The Sedona Conference does publish a "Best Practices Commentary on the Use of Search and Information
Retrieval Methods in E-Discovery"
<https://www.thesedonaconference.org/publication/Commentary_on_Search_and_Retrieval_Methods>, and EDRM
publishes an OCR explainer <https://edrm.net/2021/11/what-is-ocr-or-optical-character-recognition/>, but
both are PDFs or unreadable page bodies to the tools used, so their content on this question is
**unverified**.

FRCP 34(b)(2)(E)(ii) requires production in a form in which the information is "reasonably usable"
<https://www.govinfo.gov/app/details/USCODE-2024-title28/USCODE-2024-title28-app-federalru-dup1-rule34>,
which governs the form of production rather than the accuracy of any particular field.

**(c)** The legal domain's controls appear to aim at production form, search defensibility and privilege
review, not at whether a specific number was read correctly. If a citable rule mandating OCR-versus-image
verification exists, it was not found here.

## The UX of asking a human to confirm a value

There is an established name and an established shape for the screen, which is worth having because it
means this is not a design this project has to invent.

**The screen is called "key from image".** In document-services vocabulary: "Key-from-image, or KFI, uses
operators to enter index values while viewing scanned images. Double-key verification has two operators
independently enter the..."
<https://umbrex.com/resources/industry-lingo/business-services-lingo/records-management-document-services-lingo/>
— snippet level. Commercial capture products ship it as a named window: Hyland's AnyDoc documents a "Key
From Image (KFI) Verification Window" <https://docs.hyland.com/r/IcLDRwoIInnBUlShuzwNOA/39jdUw1aROmn__V1EbJdqg>,
and Impression Technology distinguishes "Context Editor: correct low confidence OCR-recognized data"
from "Key From Image: heads-up data entry from scanned..."
<http://www.impression-tech.com/Products/index.asp>. The vendor vocabulary itself separates low-confidence
correction from blind keying.

**Side by side is the documented arrangement.** ScryAI, on cheque exceptions: discrepancies "are flagged
and routed for review through a human-in-the-loop workflow, where both values and the full context are
displayed side by side" <https://scryai.com/collatio/workflows/check-processing-software/> — **(b)**
vendor marketing.

**Confirm and correct are different actions, and the difference has to be stored.** The one concrete
citable instance found is Expensify's, quoted above: admins are told which receipts were manually changed
against what the scanning produced. **(c)** The generalisable rule from that plus the KFI practice is
that a review action must record *which* value the machine proposed and *which* value the human accepted.
A "Confirmed" button that stores only the final value destroys both the audit trail and the ability to
measure the machine's real accuracy later.

## The answer, in the terms this system would use

Automate the confident majority. Route exceptions to a human who sees the source image beside the
proposed value. Separately cross-check the number against an independent source.

- **Automated:** extraction, confidence scoring, and the decision about what needs review. Scale comes
  more from arithmetic and structural validation than from review: does the total equal the sum of the
  line items, is the account number checksum-valid, does the date parse, does this invoice number already
  exist. Check digits cost nothing and catch single-digit errors and most transpositions.
- **Cross-checked rather than reviewed:** wherever the same number exists in a second system, compare
  them. This is what dominates real accounting practice. A bank feed is a different reading of the
  number, so agreement between them is evidence in a way that a confidence score is not. Where no second
  source exists, say so in the answer rather than implying the figure is verified.
- **Human, on exception only:** one field at a time, the source image fragment adjacent, keyboard-driven,
  ordered by risk rather than sequentially. Risk means low confidence **or** high value **or** a failed
  cross-check.
- **Record the override:** machine-proposed value, human-accepted value, who and when. This is the
  Expensify pattern and the audit-trail obligation, and it is the only way to learn this system's real
  error rate.

**(c)** No source found reviews 100 percent of fields. The design goal is a small exception rate and a
fast, auditable exception path.

## The correction to this project's framing

Confidence cannot be the only signal, and the reason is structural rather than a matter of tuning: a
confidence score is a self-report by the same process that made the error. AWS defines it as the
probability that the prediction is correct, and it is produced by the model that read `145.60`. The
cheque industry settled this decades ago by refusing to trust one reading of an amount at any
confidence, requiring two, and escalating on disagreement.

The cheapest high-value move for this system is therefore not a better threshold. It is finding a second,
independent reading of any figure that gets cited.

## Unverified, and not to be cited as fact

- Sedona Conference and EDRM positions on OCR accuracy, and any requirement to verify text against the
  image or disclose machine-generated text. PDFs were not readable.
- QuickBooks Online receipt-capture verification behaviour. Not researched.
- Xero's own handling of PDF bank statements and extraction errors.
- Dext's confidence mechanics and low-confidence queue.
- Authoritative ANSI X9 or ABA text for the routing-number checksum weighting.
- OrboGraph's accuracy and error-rate figures. Vendor claims, snippet level only.
- Several government procedure documents (IRS IRM, North Carolina Treasurer, City of Anaheim, CMS) were
  readable only as search snippets. Each is flagged inline above.
