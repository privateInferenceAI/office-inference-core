# Medical golden questions, draft

**Status:** draft. Not loaded into the deployment and not approved as a client acceptance set.

**Vertical:** a synthetic medical clinic. The source is fixtures/corpus/medical/clinic-policy.md, a fictional
Northwind Medical Group policy. It is not real clinical guidance and must not be used with real patient data.

**Count:** 60 questions across clinic hours, appointments, insurance, billing, records, privacy, prescriptions,
referrals, telehealth and emergencies.

**Each question carries:**

- id MED-001 through MED-060;
- category and the role allowed to see the source;
- the question and expected answer;
- the source document and section;
- a hard-fail category: fabricated number, wrong document, cross-role leak or unanswerable.

**What is still required before QLT-GOLDEN-001 can close:**

1. Clinical and compliance review of the source and answers.
2. Load the source into a medical test deployment and ingest it.
3. Run the suite and prove a hard-fail category blocks a release.
4. Replace the synthetic clinic with the client's real documents and answer key for the first client.

The draft is docs/eval/medical-golden-questions.draft.yaml. The source is fixtures/corpus/medical/clinic-policy.md.
