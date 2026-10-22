# Handover: ingestion, retrieval and answer measurement

**Written 2026-10-22 for the agent taking the side ingestion project.** This is the current state after the
answer-quality program: what the pipeline does, what changed, what moved the numbers, and what is still open. Read
it before touching ingest, chunking, retrieval or the evaluation harness.

---

## 0. Current state in one page

- The stack is office-inference-core, Docker Compose, in account 878451098165, us-east-1. Parser is Docling, embeddings
  are TEI running on CPU, vectors are Qdrant, the lexical index is Postgres, the model gateway is LiteLLM, the API
  is FastAPI.
- Corpus 2 is the active test corpus: 80 documents, 24,296 chunks, ingested with chunker-v5 and payload-v6. Its
  answer benchmark is 37 of 37 with Claude Sonnet 4.6 and the accuracy answer profile, five consecutive runs.
  A fresh install from a pinned archive on 2026-10-22 reproduced the same 80/24,296 counts, passed the four
  acceptance probes and a 5 of 5 corpus-2 check with no model fallback. The procedure is
  docs/runbooks/fresh-install-agent.md; the evidence is docs/reviews/fresh-install-2026-10-22/.
- Corpus 1 is the older, larger corpus: 539 indexed documents, 100,478 chunks at chunker-v4. Its index is
  **mixed v4/v5** after a reindex was stopped at 18 of 539 documents. Do not measure corpus 1 until a complete
  reindex is run.
- The measured changes that produced the current result are: table evidence rendered at answer time, table
  identity written into the index, exact-phrase evidence seeded before the model answers, the structured row
  query, excerpt centring for search results, loop-cap rescue, and one answer profile.
- The measurement harness now refuses a run that contains a model fallback, records load, memory and token
  budget, and holds a host lock plus a Postgres advisory lock.
- The next approved ingestion work is the dataset sidecar for rows beyond the large-table cap. The design decision
  is docs/decisions/0004-retrieval-gaps.md. It is not built.

---

## 1. Where everything lives

**Repository:** /home/anthony/projects/office-inference-core. Branch main. The current main recorded here was
795b9a6, the state before the fresh-install documents landed.

**Boxes:** dev box i-096bebd46308d1673 with Elastic IP 52.204.202.66 and deployment root /opt/oi-core; corpus-2 box
i-05fa6edd5284b7881, no Elastic IP, deployment root /opt/oi-core2. Both stopped between tasks. Start with the AWS
credit-account tools, assumed role DSHAgentRole. SSH key /home/anthony/ai-stack/ai-stack-key.pem. Security group
sg-09d9bfffbd28014c9, instance profile oi-core-dev-instance.

**Corpus assets:** S3 bucket officeinference-corpus-878451098165. The corpus-2 archive used for the fresh install is
second-corpus/second-corpus-2026-10-22.tar.gz, sha256
0b154bcb452af0789cdc34a6453b3806924fb22cdbf15a217eb73175072a88a1. It contains documents/ for the 80 corpus-2
documents. The code archive for the accepted install is fresh-install/oi-core-795b9a6.tar.gz, sha256
28c24862da6abdec3061f2f5dd3412dc8dee4f4866635822f230dd9f1684b70c. The UI builds from a second repository,
oic-ui; its archive for the accepted install is fresh-install/oic-ui-6d28c3c.tar.gz, sha256
4609bdb30bbd6438ac1a77f8fd8e0cb5795e977b211a0c7f6f5725450df72a2b. The older lab corpus is at
lab-baseline/lab-corpus-2026-10-19.tar.gz.

**Question sets:** docs/eval/corpora/second-corpus/questions.yaml for corpus 2 (35 questions, 37 role-runs because
two run as both staff and executive), and docs/eval/data-questions.yaml for the older 40-question set plus probes.
The wider set is docs/eval/data-questions-wider.yaml.

**Run archives:** /home/anthony/oi-eval-runs on the workstation, including accuracy-runs.tgz,
confirmation-runs.tgz, deepseek-refactor-runs.tgz, dsbedrock-runs.tgz, and REINDEX-STATUS.md.

**The evidence documents to read:**

- docs/decisions/0004-retrieval-gaps.md: needles first, dataset sidecar second, the regression gate.
- docs/decisions/0005-measurement-hygiene.md: locks, load and memory gates, token budget.
- docs/decisions/0007-table-evidence.md and 0008-table-context-in-index.md: why table context is rendered and
  indexed, with the measurements.
- docs/decisions/0006-loop-cap-rescue.md: why a capped loop keeps its evidence.
- docs/reviews/second-corpus-2026-10-20/: how corpus 2 was built, validated and measured.
- docs/reviews/exact-phrase-2026-10-20/, query-tool-2026-10-19/, reindex-2026-10-21/,
  accuracy-sprint-2026-10-21/, simplification-2026-10-21/, model-candidates-2026-10-22/.
- docs/reviews/bedrock-capacity-2026-10-22/: quota facts, because model-dependent measurement is quota-bound.

---

## 2. Pipeline overview

    documents -> parser (Docling) -> chunker -> embeddings (TEI) -> Qdrant vectors
                                              -> chunk_index (Postgres, lexical)
                                              -> ingest_manifest / collection_contracts

1. The ingestion worker scans the documents directory. It skips unchanged files by content hash.
2. A changed or new file is parsed by the parser service into Docling JSON. PDF, DOCX, XLSX, CSV, HTML, PPTX,
   TXT/MD and similar formats are supported. Legacy DOC/XLS are skipped; OCR is used where the parser needs it.
3. The chunker turns the document into Chunk objects. Prose is page-bounded and token-bounded; tables become a
   whole-table chunk plus row entries, or bounded row groups above the cap.
4. Text is embedded through TEI in batches. The embedding model, prefix rules and chunker version are part of the
   collection contract fingerprint, recorded in collection_contracts.
5. Vectors are upserted into Qdrant collection documents with payloads that carry doc_id, acl, kind, page range,
   table_id, row_index and headings. The same chunks are written into Postgres chunk_index for lexical search.
6. The ingest manifest records content hashes, chunker version, payload version, failure counts and dead letters.
7. Deletions are reconciled: chunks of a removed document are removed from both stores.
8. Dataset files are classified and get dataset cards; they are searchable as data sets rather than prose.

The worker runs inside the ingestion container on a schedule. On the dev/corpus boxes there is no resident worker.
The documented one-off invocation is:

    docker compose -p oi2 -f deploy/office/compose/docker-compose.second.yml --env-file rendered/env \
      run --rm --entrypoint python3 ingestion /app/ingest.py --documents /documents

On the main compose file drop the -p oi2 and second file. The documents directory is mounted into the container by
compose. A full corpus-2 reindex at INGEST_CONCURRENCY=1 takes roughly 1.5 hours; large SEC and OSHA filings
dominate. Corpus 1 is much larger.

---

## 3. Chunker v5, the current rules

File: services/ingestion/chunker.py. CHUNKER_VERSION is chunker-v5.

- Prose: target 512 tokens, minimum 384, overlap at most 32 tokens, page-bounded. The estimate is words times a
  constant; the measured token count from the embedding tokenizer is the authority and is stored as token_count.
- Prose chunks carry the heading path in force where the chunk starts, as a prefix. " > ".join(headings). A chunk
  that crosses into a new section belongs to the section it began in.
- Tables: a table that fits is one whole-table chunk. A table that does not fit becomes row groups with a row range.
  Above MAX_TABLE_CHUNKS=200 groups the remainder gets an explicit omission marker rather than a silent truncation.
- Row entries: ordinary tables up to MAX_ROW_ENTRIES=5000 rows also get one row per retrievable row. The row text
  now begins with context: "Table: <caption>. Columns: <header row> <row values>". That change is what makes a
  row like "Total revenues | 4,934,098 | ..." resolvable to a period.
- Table row chunks carry table_id, row_index, row_start, row_end, table_rows and headings in the payload. The
  chunker no longer writes bounding boxes, neighbour chunk ids, a duplicate table_context field or a token estimate;
  those were removed as unused payload bloat in PR #229. The context survives inside the row text.
- The chunker still computes estimate_tokens for boundaries; do not replace it with the measured count without a
  measurement, because the measured path is a post-condition, not the planner.
- Any change to CHUNKER_VERSION changes chunk ids and the collection contract. That means a full reindex and both
  the ingestion and API images rebuilt. Do not bump the version casually.

---

## 4. Index contract and migrations

- PAYLOAD_VERSION in services/ingestion/ingest.py is payload-v6. It records the payload shape a worker wrote so an
  unchanged file can be re-derived when the payload shape changes.
- The chunker version is inside the embedding-contract fingerprint. A change flips the contract. To reindex
  deliberately, set EMBED_CONTRACT_ALLOW_CHANGE=1 and EMBED_CONTRACT_REASON to a sentence, rebuild both images,
  clear or reindex the collection, and record the reason in the evidence. The contract check refuses an unexplained
  flip on purpose.
- Migrations live in db/migrations, numbered, checksummed, applied by audit migrate. The API also checks migration
  checksums at startup, so editing an applied migration crashes the API. Migration 024 added table_id, row_index,
  row_start, row_end, table_rows and headings to chunk_index, and chunker_version to ingest_manifest. Migration 010
  is frozen.
- The manifest records the chunker version so an unchanged document is re-derived after a chunker change. This is
  how a reindex knows what produced a chunk.

---

## 5. Retrieval and the answer path

File: services/retrieval/retriever.py and services/api/tool_loop.py.

- Hybrid retrieval: vector search in Qdrant plus lexical search in chunk_index, fused, then reranked. The reranker
  is Cohere Rerank v3.5 on Bedrock, configured by RERANK_MODEL, RERANK_DEPTH, RERANK_BUDGET_MS and RERANK_ENABLED.
  Access roots are a where clause in both arms; a passage outside the session roots never reaches the model.
- Search results are rendered with a snippet centred on the query terms. The seed evidence (the passages retrieval
  chose before the model speaks) is rendered from the start, not centred. That was measured: centring the seed moved
  windows past carrying lines on three corpus-2 questions.
- Table evidence at render time: for a table-row passage, the renderer reads rows above it in the same Qdrant
  table_id, finds the nearest header-looking row and caption, and prepends it. For a whole-table blob it finds the
  header and caption in the blob's own lines. Cached per row; a slow vector store degrades to the old evidence.
- The tool loop offers search_documents, read_passages and query_rows, bounded to 6 steps and 10 calls, with a
  wall-clock deadline. query_rows is one parameterised SELECT with the access roots in the where clause, scans at
  most 400 chunks and returns at most 8 rows; a match spanning more than three documents without a doc_id is
  refused. Every tool call is a tool_call audit row with counts.
- Exact-phrase seeding: before the first model call, a question with a cue phrase (a capitalised multiword term, a
  hyphenated term or a quoted phrase) has the rarest plausible phrase searched literally. Up to ten excerpts are
  added to the seed and the model answers with them. This is the fix for needles whose carrying chunk ranks below
  the retrieval cut. The phase is unconditional now; the old refusal-triggered retry was dead code and was deleted.
- Honesty gate: a question about the company's own attribute is refused when no retrieved passage states it. It is
  part of the answer path, not a switch. It runs before the JSON answer is returned and before the stream emits.
- Answer profiles: OI_ANSWER_PROFILE=accuracy (12 seed passages, 1,600-character snippets, 32 evidence slots, 12
  search results) or economy (6, 700, 24, 8). The values live in services/api/answer_profile.py. The accuracy
  profile is the measured 37-of-37 configuration.

---

## 6. Measurement and evaluation

- Runner: tests/fixtures/data_suite_run.py. It takes DATA_SUITE_QUESTIONS, calls the API as staff and executive,
  scores answers against the question file, and writes one JSON per run. It holds a Postgres advisory lock keyed
  20261020.
- Wrapper: scripts/run_answer_suite.sh. It holds a host flock, runs suite_hygiene preflight (load and memory), writes
  run-context.json, enforces the daily token budget, and now runs the fallback check after every run. A run with
  model fallback calls is marked CONTAMINATED and the phase exits 6. Unreadable database is a refusal, not a pass.
- Hygiene: scripts/suite_hygiene.py. Read docs/decisions/0005-measurement-hygiene.md. One suite at a time, serial
  runs, five runs for a configuration claim, same-day control for an A/B, raw outputs archived.
- Scoring: a question expecting an answer passes when the expected value is in the answer and a listed document is
  cited. A refusal question passes when the service declined, cited nothing forbidden and did not produce the value
  it was supposed to withhold. Access-boundary probes are controls, not scores.
- Current result: corpus 2, 37 of 37 role-runs, five consecutive runs, Sonnet 4.6, accuracy profile, zero fallbacks.
- The old 40-question set plus probes was the main corpus-1 instrument; docs/runbook.md section 6.7 has the four
  acceptance probes. The acceptance panel is scripts/acceptance_panel.py.

---

## 7. What changed, and what moved the numbers

Chronological, with the measured effect. Read the linked review for the raw evidence.

- Loop-cap rescue (PR #211, decision 0006): a capped loop keeps its evidence and gets one no-tools synthesis turn
  instead of discarding it. It removed a class of flaky refusals under load.
- Excerpt centring for search results (PR #213): search results show the part of the passage around the query.
  Seed evidence was deliberately not centred; that combination was measured and rejected.
- Row query (PR #183): 43.0 of 52 with it off, 46.6 with it on, five runs each. Exact-number questions 4.8 to 7.4
  of 8; the pinpoint probe 0 to 4 of 5; unsupported-number refusals became zero. It is on in the product path.
- Table evidence at answer time (PR #215, decision 0007): corpus-2 live set 29.2 to 31.8, five runs each,
  corpus-1 flat within spread. Best corpus-2 run 34 of 37 before the later work.
- Table context in the index (PR #217, decision 0008) plus a full corpus-2 reindex (PR #220): 31.8 to 32.0, small
  but kept because the index now carries table identity and the dataset sidecar needs it. Corpus-2 reindex: 80
  documents, 24,296 chunks, 0 failures.
- Exact-phrase lane (PRs #209, #222): needles went from 23 of 30 to 30 of 30; the corpus-1 aggregate gate was not
  certified in that window, so it stayed experimental until the seed-time version. The seed-time version is what
  the current accuracy configuration uses.
- Accuracy configuration and model work (PRs #222, #223, #224): 37 of 37 with Sonnet 4.6, larger evidence and the
  fixed SYN_A01 question. The question fix named Northwind, because the old wording had several CEOs in the
  accessible corpus.
- Simplification (PRs #226, #227, #228, #229, #230): one answer profile, dead code and dead payload fields removed,
  operator docs updated, fallback guard added. Current tests around 1179 passed.
- Negatives worth keeping: lookup_value was retired (PR #203) after measuring neutral to negative; the
  query_rows section-row variant was closed unmerged after a measured negative (PR #221); document title and
  heading path at index time made retrieval worse; centring the seed evidence regressed three questions; raising
  seed passages to 20 without the exact lane scored 34 to 36 of 37. These are recorded so they are not retried.

Model candidates, five runs on corpus 2: Sonnet 4.6 on Bedrock 37.0; DeepSeek V4.1 Flash through the external API
36.6, test-only, data leaves AWS; DeepSeek V3.2 on Bedrock 34.6; Haiku 4.5 with the accuracy evidence 34.0. The rule
for changing the preferred model is in docs/reviews/model-candidates-2026-10-22/.

Bedrock quota reality: the credits account has a 150,000,000 cross-model daily token pool, published default
700,000,000, Sonnet 4.6 has no separate published daily allowance, and heavy measurement can exhaust the usable
allowance after roughly two dozen Sonnet calls. The gateway falls back silently. That is why the suite now refuses
fallback runs. Read docs/reviews/bedrock-capacity-2026-10-22/ before designing any measurement that depends on a
frontier Bedrock model.

---

## 8. Known issues and deferred work

1. **Corpus 1 is mixed chunker-v4/v5.** A reindex was stopped at 18 of 539 documents. The index is not a valid
   measurement. A full reindex is hours of work and needs the contract allow-change path. Do not compare corpus 1
   until it is complete.
2. **Dataset sidecar is approved and unbuilt.** Rows beyond the large-table cap are unreachable. The corpus-2
   ledger rows beyond roughly 2,412 (MAX_TABLE_CHUNKS=200) are missing. The design is decision 0004: an ingestion
   table plus a bounded, ACL-filtered tool.
3. **Tables over MAX_ROW_ENTRIES=5000** do not get row entries; they get bounded groups and an omission marker.
4. **Parser coverage.** Scanned and unusual formats can still fail or be dead-lettered. The corpus-2 run had zero
   failures, but do not assume every client corpus will.
5. **Reindex cost and coupling.** A chunker change flips the embedding contract and requires both images rebuilt.
   The API checks migration checksums at startup, so a migration edit without a rebuild crash-loops it.
6. **Quota.** Sonnet measurement is quota-bound; the fallback guard is mandatory. DeepSeek on Bedrock is a
   test-only fallback with lower accuracy.
7. **Box state.** The corpus-2 box has no Elastic IP, so its public IP changes on every start. /tmp may be wiped on
   stop. Archive runs before stopping.
8. **No resident ingestion worker on the corpus box.** Use the one-off command, not a background loop.

---

## 9. Good places to take the side ingestion project

- Build the dataset sidecar (decision 0004). It is the approved next step: ingestion table, bounded tool, ACL
  filtering, audit counts, five-run measurement on corpus 2 and the large-table probes.
- Improve table understanding beyond the cap: a per-table reader that can cite a row range without emitting every
  row as a vector.
- Parent-window retrieval: retrieve a small passage, read its section or page for context. The current table
  evidence renderer is the local version of this idea; a prose version is unmeasured.
- Exact-phrase generalisation: the current lane is one phrase, cue-gated and capped. It could become a broader
  lexical arm with measured controls.
- Parser and OCR coverage, especially legacy formats and scanned filings. Keep the dead-letter set small.
- Ingestion observability: per-document timings, parse failures, chunk counts, reindex estimates. Operators need to
  know what a corpus will cost before it runs.
- Corpus-1 reindex, if the side project needs the second benchmark. Do it as a planned job with snapshots and
  evidence, not incidentally.
- Golden corpora for the two verticals in docs/lab/FINISH-SCOPE.md, once the agent runtime work starts.

---

## 10. Rules for changing ingestion

- Ask the owner before changing a corpus, a question set, an expected answer, or an access boundary. Corpus
  changes are owner decisions.
- Do not bump CHUNKER_VERSION or PAYLOAD_VERSION without the contract allow-change path, both images rebuilt, and a
  planned reindex.
- Five serial runs before a configuration claim, same-day control for an A/B, raw outputs archived. A single run is
  a smoke test.
- Do not measure corpus 1 while its index is mixed.
- Keep the fallback guard. A run with a fallback model call is not a measurement of the configured model.
- Write the evidence under docs/reviews or docs/decisions. Leave negatives recorded so they are not retried.
- Keep tests and the manifests valid: scripts/validate_manifests.py, scripts/check_model_lifecycle.py, pytest.
- Never commit keys, rendered/env, raw client documents or run outputs with content.

---

## 11. If you read nothing else

    corpus 2: 80 documents, 24,296 chunks, chunker-v5, payload-v6
    answer benchmark: 37 of 37, five runs, Sonnet 4.6, accuracy profile, zero fallback
    corpus 1: mixed index, do not measure
    next approved ingestion work: dataset sidecar for rows beyond the table cap
    measurement: one run at a time, five runs for a claim, fallback guard on

    read docs/decisions/0004-retrieval-gaps.md and 0008-table-context-in-index.md first,
    then docs/reviews/reindex-2026-10-21/ and accuracy-sprint-2026-10-21/,
    then run one corpus-2 suite and read the raw JSON before changing anything.

The current result is real and reproducible. Protect it with measurements, not with assumptions.
