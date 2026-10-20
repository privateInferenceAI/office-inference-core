# Closing the two measured retrieval gaps: plan and regression controls

**Status:** proposal, 2026-10-20. **Owner decisions needed:** order of work (this plan recommends needles
first), and whether the large-table data path gets built. No code changes are in this document.

## What is broken, measured

| Gap | Evidence | Size |
|---|---|---|
| Needle in a long document | Wider set: one Apple 10-K headcount question missed 4 of 5 runs. Second corpus: Caterpillar Current Termination Date refused 5 of 5 runs; visitor badge answered from a shorter handbook rule 5 of 5 runs | 2 needles failing on two corpora |
| Rows beyond the table cap | Second corpus: 6,010-row ledger indexed to row 2,412 (dates to 2025-05-28); 3,600 rows unreachable by retrieval or by the row query. The chunker records the omission in a table_marker chunk | 1 dataset, 3,600 rows |

Both gaps are genuine product limits, not instrument defects: the questions were validated against the
documents and the failures reproduce.

## Why fixing one thing can hurt another, and what we do about it

The owner's worry is correct. Retrieval is a shared budget: chunks, evidence slots, context characters, tool
calls and steps are all finite, and a change that helps one question shape competes with the shapes that already
work. This repository has a concrete precedent: the first row-query treatment lost N06 because the tool's rows
filled the evidence namespace and a matched row could not be rendered; the fix was a reserved allowance for rows.
That is exactly the trade the owner is asking about.

We do not treat that as unavoidable; we bound it:

1. **Additive before subtractive.** New tools and lanes add candidates; they do not re-rank or remove what
   retrieval already returns. A new tool the model can choose carries far less regression risk than a scoring
   change, because a question that never needs it behaves as before.
2. **Reserved budget, not shared budget.** Any evidence a new lane adds gets its own small allowance, the way
   rows do, instead of shrinking the passages the existing path returns. The N06 failure is the design note for
   this rule.
3. **Two frozen corpora, five runs each, same day.** Corpus 1's 53-run suite and corpus 2's 37-run set are
   measured before and after every change, with the same model and flags. A single corpus can show a gain while
   the other regresses; both must hold.
4. **Pre-registered success and failure criteria.** For needles: the needle set improves, and no category on
   either corpus loses a pass across five runs. For rows: the previously unreachable ledger rows answer, and the
   existing oversized-table probe on corpus 1 does not regress. If a change wins needles but costs a point
   elsewhere, it is rejected, not averaged.
5. **Per-question tracking, not just totals.** The runner records every question; a regression is visible as a
   named question that used to pass. Totals hide exactly the trade the owner is worried about.
6. **Flags and rollback.** Every intervention lands behind its own flag, default off. Shipping to a deployment
   is a separate measured decision, so a bad interaction can be turned off without a revert.
7. **Probes stay independent.** The five probes are not part of either question set and are re-verified against
   the index every run, so they catch a lane that poisons retrieval without any scored question noticing.
8. **No unmetered context growth.** Every new lane has a character cap and an evidence cap, and the acceptance
   panel reports latency and cost per answer next to accuracy.

Residual risk remains: a change can make one long-document question pass and, on a client corpus we have not
built, cost something. That is why the plan's gate is both corpora plus the probes, not one score.

## Long-document needle retrieval

### Diagnose before changing

Classify each failing needle from the run records, because the three causes need different fixes:

1. **Recall:** the carrying chunk was never retrieved. Check the top retrieval results for the chunk id.
2. **Budget:** the chunk was retrieved but dropped from the prompt by the evidence ceiling.
3. **Reading:** the chunk was in the prompt but the model quoted a different passage (the visitor-badge case
   looks like this: it found a related handbook rule and answered from it).

The run records already store the retrieved passage ids and the evidence the model saw, so this is analysis, not
guesswork. The fix is chosen per cause, not per question.

### Candidate interventions, cheapest and lowest risk first

| Order | Intervention | What it changes | Risk | Effort |
|---|---|---|---|---|
| 1 | Exact-phrase lane inside the search tool | The model can ask for a literal phrase (a heading, a defined term, a panel model) and get chunks containing it, ACL-filtered and bounded | Low: new mode on an existing tool, additive results, no ranking change | 1 to 2 days |
| 2 | Document cards in retrieval results | One bounded card per candidate long document (title, length, heading list, doc id) so the model can choose read_passages inside the right document | Low to medium: uses evidence slots; needs a reserve | 2 days |
| 3 | Heading-aware retrieval | Chunk heading path is stored and searchable; scoped search can filter by heading | Medium: chunking and payload change, re-index required | 3 to 4 days |
| 4 | Section read tool | Read one named section of a document instead of a page window | Medium: retrieval-adjacent but additive | 2 days |
| 5 | Retrieval tuning (top-k, reranker) | Changes the ranking every question sees | High: this is the shared-budget change the owner is worried about; requires an A/B control | 1 week of measurement |

The first experiment should be 1 or 2, chosen by the diagnosis. Global tuning is last on purpose.

### Needle acceptance set

Build a 10 to 12 question needle set from documents already in the two corpora (the Caterpillar agreement, the
Apple 10-K, the facilities manual, a Duke 10-K, a proxy statement), each verified against the index, each with a
single carrying chunk. Five runs before, five after. Success: needle set improves and neither corpus loses a
category pass.

## Large-table row access

### The limit

A table over 5,000 rows keeps bounded row groups (200 chunks) and loses per-row retrieval entries; the marker
chunk records total rows and the indexed range. Retrieval and the row query both read the indexed chunks, so
rows outside the range do not exist for either path. The row query's SQL is sound; the rows are simply absent.

### Options

| Option | Shape | Risk | Recommendation |
|---|---|---|---|
| A. Dataset sidecar table | Ingestion writes large tables into a bounded dataset_rows table (doc id, row number, cells, acl); the row query (or a sibling tool query_dataset) reads it with the same parameterised, ACL-filtered, bounded pattern as today | New write path and storage; ingestion changes: medium | Recommended if the owner approves the data path |
| B. Raw-file read tool | Mount documents read-only into the API and let a tool read CSV rows from the file with a guarded path and bounds | Parser dependencies in the API image, file access surface, no SQL: medium to high | Second choice |
| C. Raise the caps | Index more rows or more chunks per dump | High: the 2026-10-17 row explosion that chunker-v4 was built to stop returns | Not recommended |

Option A keeps the properties the row query already has: one parameterised SELECT, access roots in the WHERE
clause, row and scan caps, audit rows, and the dataset card stays the citation unit. It also gives the model a
path to the rows the marker chunk says are missing, which turns an honest limitation into an honest answer.

### Acceptance

- With the flag on, the ledger's 12,345.67 row and two other rows beyond the indexed range answer in five of five
  runs; with it off the same questions fail, which is the control.
- Corpus 1's oversized-table probe and corpus 2's existing row questions do not regress.
- Storage and ingestion-time overhead measured and stated before merging.
- No new tool can write, and every call is audited with candidate and returned-row counts.

## Order of work

1. **Diagnose the four needle failures** from the existing run records (half a day, no model spend).
2. **Needles:** exact-phrase lane, then document cards if needed (about one week including measurements).
3. **Rows:** dataset sidecar behind a flag (about one week including measurements).
4. Each change gets its own PR, its own five-run pair on both corpora, and an evidence document.

## What the owner needs to decide

1. Start with needles, then rows? This plan recommends yes: needles are the failure that reproduced on two
   corpora, and the fix is additive.
2. Approve the dataset sidecar data path, or prefer the raw-file tool, or defer rows until a client corpus needs
   them.
3. Accept the regression gate as stated (needles improve, no category pass lost on either corpus, probes pass). If
   the owner wants a stricter rule, for example no single question anywhere may regress, that changes which
   interventions are viable.
