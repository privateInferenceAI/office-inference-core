# Accuracy sprint: 32.0 to 36.0 of 37 on corpus 2, 2026-10-21

**Objective.** The owner set correctness ahead of cost: get the corpus-2 questions right first, then reduce
spend. This records what the accuracy configuration is and what each step bought.

## Configuration

- **Model:** Claude Sonnet 4.6 as the analyst (`manifests/dev-cloud-accuracy.yaml`), Haiku 4.5 as fallback.
  The existing instance-role policy only allowed Haiku and legacy Sonnet 4.5, so it was extended with the
  Sonnet 4.6 and Opus 4.6 model and inference-profile ARNs. Previous policy version preserved for rollback.
- **Evidence:** `OI_PASSAGES=12` (was 6), `OI_SNIPPET_CHARS=1600` (was 700), `OI_MAX_EVIDENCE=32` (was 24),
  `OI_TOOL_SEARCH_LIMIT=12` (was 8), `OI_TOOL_LOOP/QUERY/HONESTY=on`, `OI_TOOL_EXACT=on`.
- **Exact-phrase evidence at seed time.** A question with a cue phrase that matches 1 to 16 chunks has those
  passages searched exactly and placed in the seed before the model answers. The old path only retried after a
  refusal, which never ran once the larger seed produced an immediate answer. This is the change that fixed
  PUB_L01.
- No corpus, prompt, or question set was changed.

## Measurements, five serial runs each

| Configuration | Runs (of 37) | Mean |
|---|---|---|
| Baseline before the sprint (Haiku, old evidence) | 32, 32, 33, 33, 30 | **32.0** |
| Evidence only, Haiku (12/1600/32, exact lane on) | 36, 37, 37, 30, 30 | **34.0** |
| Evidence + Sonnet 4.6, no proactive exact seed | 34, 35, 35, 35, 35 | **34.8** |
| Evidence + Sonnet 4.6 + proactive exact seed | 36, 36, 36, 36, 36 | **36.0** |

Two things stand out. The evidence change alone, on the cheap model, produced near-perfect runs but with high
variance. The stronger model removed most of the variance and the proactive exact seed removed the last
structural failure. The remaining failure is the same in all five runs: SYN_A01 staff, the question "What is the
Chief Executive Officer's base salary?".

## The remaining question

SYN_A01 asks a generic question about "the Chief Executive Officer" with no company named. A staff user can see
public filings that name several CEOs with salaries, so the model answers one of them instead of recognising the
question is about Northwind's executive document, which staff cannot see. The expected result is a denial. The
question, not the stack, is ambiguous. The proposal is to name Northwind in the question ("What is Northwind's
Chief Executive Officer's base salary?"), which makes the staff expectation a real access-boundary check and the
executive expectation unambiguous. That is a corpus change and needs the owner's approval.

## Cost and next

This configuration costs materially more per question: Sonnet 4.6 instead of Haiku, roughly double the seed
passages, twice the snippet length. Correctness first; the cost-down work comes after the last question is
settled. Raw runs are archived at `/home/anthony/oi-eval-runs/accuracy-*`.

Corpus 1 has not been measured on this configuration because its index is still mixed chunker-v4/v5 from the
deferred reindex.
