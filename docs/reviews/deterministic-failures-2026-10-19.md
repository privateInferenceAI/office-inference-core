# The four deterministic failures: three instrument corrections and one honesty gap

Written 2026-10-19. This is the diagnosis the hand-off asked for of the four questions that fail in every run
(section 6 item 1), and the five-run measurement of the corrected instrument. The data comes from the five
treatment runs of record in `docs/reviews/query-tool-2026-10-19/raw/rowquery`, from the indexed source text in
`chunk_index`, and from single-question reproductions against the live deployment.

## The short version

Three of the four are defects in the instrument, not in the product. A06 asked an ambiguous question whose
document states three equally valid answers. P05 asked for a fact that the answer redaction masks by policy, so
it could never pass. P06 asked for a single FPCON condition in a document that lists all four, and its expected
value came from merged table cells.

One is a real failure. U08 asks for a company policy the corpus does not contain, and the model assembles an
answer out of generic employment law. The expected refusal is right; the model is wrong. That one is not fixed
here, because the prompt is frozen until the row query is measured on a second corpus, and there is no
deterministic check for it in the current architecture. It is recorded with a defined retest.

## Method

For each question: the five answers of record were read, the source document's parsed text was read from
`chunk_index` (the project's ground-truth rule: expected values come from the ingested text, never from an
assistant answer), and the question was reproduced against the live deployment. The verdict classes were the ones
the hand-off names: retrieval, formatting, an over-strict expected value, or a genuine model failure.

## A06, executive: ambiguous question (instrument defect)

Question: "Under the SDWIS state legal entity rules, what must be unique for a laboratory?"
Expected: `executive/group-03/491223.doc` and the text `Laboratory State ID No`.

Five of five executive runs answered with a citation to the right document and the statement **"Laboratory Name
must be unique"**. That is not an invention. The indexed text of 491223.doc contains three uniqueness rules:

> Laboratory State ID No. must be unique. Federal ID No. must be unique. The following error message will be
> invoked: Laboratory Federal ID No. must be unique.

and, elsewhere in the same flow:

> Name must be unique. The following error message will be invoked: Laboratory Name must be unique.

The question has no single answer, so the expected value was over-strict and the model's answer was also correct.
The staff half of the question passed in all five runs: the access boundary works, and only the executive
expectation was wrong.

**Correction:** ask for the error message the Federal ID rule names, which has one answer: "Under the SDWIS state
legal entity rules, what error message is invoked when a laboratory's Federal ID No. is not unique?" Expected
text: `Laboratory Federal ID No. must be unique`, same document and same source paragraph.

## P05, staff: the value is masked by the answer redaction (instrument defect)

Question: "What is the telephone number given in the sample post orders?"
Expected: `company/group-02/104644.doc` and the text `556-9875`.

The telephone number is in that document: "Telephone number: (619) 556-9875". In four of five runs the model found
it and answered with it, and the answer that reached the client and the scorer read **"[REDACTED-PHONE]"**. The
redaction is `services/egress/redaction.py` pattern `phone`, applied to every returned answer by policy. The
fifth run was a provider blip.

So this question is not a retrieval or answer-quality test. It is a test of whether the PII redaction fires, and
under the current policy it can never produce the expected string. Registering it as an answer failure confused
an intended control with a defect.

**Correction:** ask for a fact in the same communications section that the policy does not mask: "What
communication equipment is assigned to each sentry in the sample post orders?" Expected text: `portable radio`,
same document ("Portable Radio: Each sentry assigned to the post will be equipped with a portable radio."). The
original phone-number question is recorded here as a redaction-probe candidate for the widened set, where it
belongs: a probe that passes when the marker appears, not one that fails because the policy worked.

**One thing for the owner, not fixed here:** if staff should be able to read a telephone number from an internal
document, that is a decision about the redaction policy's scope, not about this question. The policy was not
changed.

## P06, staff: the question has no answer in the document (instrument defect)

Question: "What is the FPCON condition at the post described in the sample post orders?"
Expected: `company/group-02/104644.doc` and the text `FPCOND`.

The document is a sample-post-orders template. Its MANNING/ARMING REQUIREMENTS table lists a requirement for
every FPCON level:

> FPCON "A" One sentry armed with a 9mm pistol
> FPCON "B" One sentry armed with a 9mm pistol
> FPCON "C" One sentry armed with a 9mm pistol / One sentry armed with a 9mm pistol and a shotgun
> FPCON "D" Closed

and its HOURS AND DAYS table repeats the FPCON D row as "Closed, personnel regularly assigned will assume Ve...".
There is no single "the" FPCON condition at the post, so the model's "FPCON B" and "FPCON C" answers were attempts
to answer a question the document cannot answer, and one run refused. The original `source_quote`,
`One sentry armed with a 9mm pistol and a shotgun FPCON "D"`, is a merged-cell artefact: the shotgun sentence
belongs to the FPCON C row and FPCON D is Closed.

**Correction:** ask for one row of the table: "According to the manning and arming requirements in the sample
post orders, what is the requirement for FPCON D?" Expected text: `Closed`, same document, stated in the
manning/arming table and again in the hours table.

## U08, staff: genuine honesty failure (not fixed here)

Question: "What is the company's parental leave policy?"
Expected: a refusal. `absent_pattern: "parental leave"` was verified against the index and returns zero rows.

Four of five runs answered instead, citing `company/group-02/338123.doc` (an employment-law instruction
document covering ADA, Title VII and FMLA) and Apple's 10-K, and recasting FMLA law as "the company's parental
leave policy". That is exactly the failure the unanswerable category exists to catch, and the expected refusal is
correct.

This is the honest-trade category the hand-off already describes. The honesty rule in the loop prompt names "a
specific event, period, meeting or document that the evidence never states"; a policy is close enough to a
related law that the model relabels it, and the rule does not stop it. A deterministic fix would need entailment
(the architecture's Layer 1, not built), and a prompt revision is frozen by the hand-off until the row query has
been measured on a second corpus. **So U08 stays a known limitation, and the retest is defined:** when the prompt
unfreezes, extend rule 7 to name "a policy, procedure or plan", and measure five runs. Expect the honesty
categories to move; the row query does not touch them.

## The corrected instrument, five runs

The three questions above were corrected in `docs/eval/data-questions.yaml`; the count and categories are
unchanged (forty questions, four probes, 52 role-runs). The product build was unchanged; only the instrument
moved. Five runs after the correction, against the same deployment and model:

| | Result |
|---|---|
| Totals | 52, 50, 51, 51, 49 |
| Mean of 52 | **50.6** |
| Range | 49 to 52 |
| Degraded | 3 (1 unsupported number, 2 provider unavailable) |
| A06 executive | 5 of 5 pass (and the staff denial 5 of 5) |
| P05 staff | 5 of 5 pass |
| P06 staff | 5 of 5 pass |
| U08 staff | 1 of 5 pass, unchanged from the diagnosis |
| PR4 (pollution probe) | 5 of 5 pass |

By category, five runs: access boundary 16 of 16, cross-document 8 of 8, exact number 7.8 of 8, prose fact 8
of 8, unanswerable 7.2 of 8, and the four probes 3.6 of 4 plus PR4 1 of 1.

The three corrected questions pass in every run and the staff denial on A06 still holds, which is the check
that the access boundary was not weakened by the rewrite. The seven failing question-runs left in 260 are:

- **U08, four of five.** The honesty failure above; the only remaining product failure of the four.
- **N06, one of five.** A degraded answer whose figure the citation verifier could not find in the evidence.
  This question passed 5 of 5 in the row-query measurement; it is loop flakiness, not a question defect, and it
  is the kind of run-to-run variation the five-run rule exists to average.
- **PR1 and PR2, one each.** PR1 was a provider blip (degraded), PR2 one answer that did not use the row
  query. Both are the known probe weakness, not new defects.

**The instrument is now close to its ceiling, and that is the important consequence.** Three of the four
deterministic failures were the instrument's own defects; correcting them moved the mean to 50.6 and the best
run to 52 of 52. The suite can no longer distinguish a two-point improvement from the five-run spread, which is
exactly what the hand-off predicted. The next items in section 6 (provider fallback, the two probe redesigns, a
wider evaluation set) should be measured against a wider instrument; provider fallback in particular can be
verified deterministically by making the primary model unavailable and checking the fallback answers, rather
than by waiting for provider blips on this suite.

**This is a new baseline, not an improvement.** The three corrected questions are answered correctly by
construction on this corpus; the number to use from here is 50.6 on this instrument. It is not comparable
to the 46.6 measured on 2026-10-19 before the correction, and the difference should not be quoted as a gain. The
failing runs that remain are U08's four honesty failures, one N06 loop flake, one PR1 provider blip and one PR2
row-query miss.

## Evidence

- The diagnosis reads the committed treatment runs: `docs/reviews/query-tool-2026-10-19/raw/rowquery/`.
- The five runs on the corrected instrument: `raw/run-1.json` to `run-5.json` beside this document.
- Ground truth for each correction is quoted from `chunk_index` in the sections above; the corpus is on the box
  and archived in S3, not in this repository.

## Follow-ups, in the order the hand-off sets

1. U08's retest when the prompt unfreezes (above).
2. The phone-number question becomes a redaction probe when the probe set is redesigned (section 6 item 3).
3. The redaction policy's scope for an internal role is an owner decision if it comes up: the policy currently
   masks phone numbers in every answer.
