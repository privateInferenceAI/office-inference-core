# Medical golden questions

**Status:** loaded, validated and hard-fail tested for the synthetic test vertical. Clinical review was waived by
the owner for test use. QLT-GOLDEN-001 is verified for this test set; a real medical client still needs clinical
and compliance review.

**Vertical:** a synthetic medical clinic. The source is fixtures/corpus/medical/clinic-policy.md, a fictional
Northwind Medical Group policy. It is not real clinical guidance and must not be used with real patient data.

**Count:** 60 questions across clinic hours, appointments, insurance, billing, records, privacy, prescriptions,
referrals, telehealth and emergencies.

**Each question carries:**

- id MED-001 through MED-060;
- category and the role allowed to see the source;
- the question and expected answer;
- the source document and section;
- a hard-fail category: fabricated number, wrong document or cross-role leak.

**Evidence:**

- The source is indexed at company/medical/clinic-policy.md.
- The mechanical validator passes 60 of 60 questions against the live chunk_index.
- The live API answers medical questions with citations to the policy.
- The hard-fail gate catches seeded fabricated-number, wrong-document, cross-role-leak and unanswerable cases.

See docs/reviews/medical-golden/README.md.

**What is still required for a real client:**

1. Clinical and compliance review of the real source and answers.
2. Replace the synthetic clinic with the client's documents and answer key.
3. Re-run the validator and hard-fail gate on the real corpus.

The suite is docs/eval/medical-data-questions.yaml. The draft and the source are kept for the synthetic test.
