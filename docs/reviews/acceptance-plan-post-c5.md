# Acceptance plan, after C5 and after the chunking repair

Two panels, run in sequence, on the live development deployment. This is the pass you asked for before C6: the
manual from the start to C5, and the data-specific questions.

## Prerequisites, checked before a panel starts

1. The corpus is loaded: 544 documents, no dead-lettered rows, `chunk_index` count equals the Qdrant point count.
2. `tests/fixtures/gate_check_run.sh` and `--jobs` pass, `scripts/probe.sh activity_probe.py` passes.
3. The full test suite is green on the checkout the box runs.
4. No ingestion cycle is mid-write; the worker can keep cycling, it only reads files.

## Panel one: the manual, start to C5

Three reviewers, the house method:

- **executor** runs every documented procedure against the live box, in order, and records the actual output
  beside the manual's expected output.
- **cold reader** reads the manual as someone who has never built this, flags anything unexplained, ambiguous, or
  quietly wrong, and says where they got stuck.
- **fidelity checker** compares what the manual claims with what the code and the deployment do, section by
  section, and reports claims the system cannot support.

Scope, by section group:

| Group | Manual sections |
|---|---|
| 1 | setup, first boot, status, shorthand, model call, keys and budgets (sections 1 to 12) |
| 2 | ingestion, retrieval, citations (13a to 13c) |
| 3 | access control, redaction and egress, retention (13d to 13f) |
| 4 | audit record, assistant API, routing, client app (13, 13g to 13i) |
| 5 | approvals, the two screens, invoice intake, the workflow engine (13j to 13m) |
| 6 | shipping evidence, routine checks, manifest, roles, model change, second deployment (14 to 19) |

**What is out of scope, deliberately:** rebuilding the box from scratch. The install sections are verified as "the
documented state matches the live deployment", not re-run. A true fresh-install pass is a separate exercise on a
new instance, planned later. Destructive procedures (retention enforcement, deletion, purge) run against a
database snapshot or not at all.

Pass means every documented command produced its documented result. Anything else is a finding with the section,
the command, expected and actual. Findings go in `docs/reviews/manual-round-<n>-<reviewer>.md`, numbered, with a
severity: blocking (a documented operator cannot succeed), misleading (they can succeed and be told the wrong
thing), cosmetic.

## Panel two: the data questions

The forty questions in `docs/eval/data-questions.yaml`, run after panel one. Three reviewers:

- **runner** asks every question through the assistant API as the client would, once as staff and the access
  questions again as executive, and records answer, citations and retrieved documents.
- **answer-key verifier** checks each expected value against the source document before the runner runs, and
  again if the runner disputes one. The answer key is not trusted because it is written down.
- **adversary** tries to make the checks pass when they should fail: a fabricated number, a right answer from the
  wrong document, an executive passage in a staff result, an unanswerable question answered confidently. A check
  that cannot fail is not a check.

Findings go in `docs/reviews/data-round-<n>-<reviewer>.md`. Each round's summary records the score by category and
the failures with their question ids.

## Rounds and fixing

Two rounds normally, three maximum as in the C5 panel. Between rounds the Lead fixes the findings, and the fixes
are what the next round tests. A clean round is a real result and ends the pass. Do not re-run a round that
already passed just to fill the schedule.

## Ownership and money

The Lead runs the panels and does the fixing; reviewers change no code. The answer half makes model calls through
the gateway, so a round costs provider tokens; forty questions per round is deliberate, not a thousand. Retention
and purge tests are excluded because they destroy corpus data and are already covered by probes.

## What has to be true at the end

Every manual section in scope either passed or has a recorded limitation; all forty questions either pass or have a
recorded reason; no fabricated answer, no cross-role leak, no cited page that does not contain the fact. The
result is one summary in `docs/review-log.md` and the two rounds' files under `docs/reviews/`.
