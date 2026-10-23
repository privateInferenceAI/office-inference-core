# Medical golden set: loaded, validated and hard-fail tested

**Date:** 2026-10-23. **Status:** QLT-GOLDEN-001 verified for the synthetic medical test vertical. Clinical review
was waived by the owner for test use.

## Loaded

- Source: fixtures/corpus/medical/clinic-policy.md
- Deployment path: /opt/oi-core/documents/company/medical/clinic-policy.md
- Manual ingest: 81 documents seen, 1 new, 4 chunks written, 0 failed.
- The document is indexed under the company ACL and is retrievable.

## Ground truth

- Suite: docs/eval/medical-data-questions.yaml, 60 questions.
- Mechanical validator: 60 passed, 0 failed against the live chunk_index.
- The validator checks every expected document, source quote and expected value against the indexed text.

## API evidence

Two live answers:

- Weekday hours: "Monday through Friday, 8:00 a.m. to 6:00 p.m. [1]" with a citation to
  company/medical/clinic-policy.md.
- No-show fee: "The no-show fee is Fifty USD [1]." with a citation to
  company/medical/clinic-policy.md.

## Hard-fail gate

The suite assigns each question one of three answerable hard-fail categories: fabricated number, wrong document or
cross-role leak. The gate also covers unanswerable behavior with a synthetic case.

Self-test output:

    PASS clean baseline: 60 answers, no findings
    PASS seeded fabricated_number was caught: fabricated number 99.99
    PASS seeded wrong_document was caught: expected document missing from citations
    PASS seeded cross_role_leak was caught: executive document leaked into a company answer
    PASS seeded unanswerable was caught: an unanswerable question was answered

The gate is scripts/check_medical_hard_fails.py. It exits non-zero on a finding and passes only when the whole
suite is clean.

## Limits

- This is a synthetic clinic policy, not a real medical client or patient data.
- Clinical review is waived for test use only. It is required before a real medical deployment.
- The hard-fail gate is mechanical over the source and citations. It is not a clinical review.
- The unanswerable case is tested by the gate self-test, not by a fabricated answer in the 60-question set.
