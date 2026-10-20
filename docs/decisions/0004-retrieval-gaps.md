# 0004. Retrieval-gap decisions: needles first, dataset sidecar for rows, minimal gate

**Date:** 2026-10-20. **Owner decision.** **Status:** accepted.

## Background

The second corpus measured two retrieval gaps: long-document needles (2 of 2 needles failed; the wider set
missed the Apple headcount in 4 of 5 runs) and rows beyond the large-table cap (6,010-row ledger; 3,600 rows
unreachable by retrieval or the row query). The plan is docs/design/retrieval-gaps-plan-2026-10-20.md.

## Decisions

1. **Needles first, then rows.** The needle failure reproduced on two corpora and its fixes are additive. The
   row work is approved and comes second.
2. **The dataset sidecar is the approved data path for large tables.** Ingestion writes large tables into a
   bounded dataset table (document id, row number, cells, access label); a bounded, parameterised, ACL-filtered
   tool reads it with the same properties as the row query. The raw-file tool is not chosen. Raising the caps
   stays rejected: the row explosion behind chunker-v4 is the reason the cap exists.
3. **The regression gate is the least strictness that protects the result, with room to tighten.** A change is
   accepted when:
   - the target set improves (needle set, or the previously unreachable row set), and
   - the five-run aggregate on neither corpus decreases, and
   - no access-boundary, refusal, or PII probe regresses (these are controls, not scores).
   Per-question regressions are tracked and reported; they do not automatically block. If a per-question
   regression turns out to hide a real loss, the gate tightens to no category may lose a pass, then to no
   question may regress.
4. **Additive-first stays a design rule, not a preference.** New lanes add candidates and get their own evidence
   allowance; global ranking changes come last and require a same-day A/B control.
5. **Latency and cost are reported, not gated, for now.** The owner accepts 2 to 12 seconds while answers hold.

## Consequences

- The first experiment is the needle diagnosis and the exact-phrase search mode, behind its own flag.
- The second experiment is the dataset sidecar behind its own flag.
- Each lands as its own PR with its own evidence document and five-run pair on both corpora.
- Escalation of strictness is a documented decision, not a silent change: if the minimal gate lets a real
  regression through, the decision record is amended and the affected experiment is re-measured.
