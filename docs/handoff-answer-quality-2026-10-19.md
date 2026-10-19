# Hand-off: answer quality on the office-inference-core RAG stack

Written 2026-10-19 for the next agent. Everything here was produced by measurement on the box. Where a number in an
older report disagrees with this document, this document and the two review documents it points at are the
authoritative ones. The explicit corrections are in the last section.

## 1. The current state, in one screen

The development deployment of `office-inference-core` (cloud profile) runs on an m7i.4xlarge at `/opt/oi-core` and
answers questions through a bounded tool loop rather than a single retrieval-and-generate call.

| Thing | State |
|---|---|
| Model answering questions | Claude Haiku 4.5 on Bedrock, inference profile `us.anthropic.claude-haiku-4-5-20251001-v1:0` |
| Answer path | Tool loop on, six steps, ten tool calls, three tools available but lookup disabled |
| Flags | `OI_TOOL_LOOP=on`, `OI_TOOL_MAX_STEPS=6`, `OI_TOOL_MAX_CALLS=10`, `OI_TOOL_LOOKUP=off`, `OI_TOOL_HONESTY=on` |
| Corpus | 539 documents, 100,477 chunks, six data-set cards, six dead letters. Frozen since 2026-10-18 |
| Measured quality | Tool loop **40.2–41.2 of 52** depending on build; single-shot **35.0 of 52**. Five runs each |
| Measurement set | 40 questions + 4 probes, 52 role-runs per suite run |
| Development budget | Analyst team ceiling 100 USD per 30 days, about 0.4 USD per suite run |
| Repo state | `main` at the commit that merged this hand-off; see `git log --oneline -10` |

The one-line finding: **retrieval works; the answer layer was the problem; the loop fixed about five points of it,
and the remaining gap is that the model cannot query structured data, only read search results.**

## 2. The problem, and the numbers that define it

Retrieval finds the right document about 89 percent of the time. The chunk holding the exact value was in the
retrieved context only 58 percent of the time, and of 32 failing question-runs across a four-run baseline, 18 had
the value in context and still failed. That profile is what motivated everything below: more retrieval would not
fix it; the model needed to look further, and the answer contract needed to reject unverified numbers.

Measurement sequence, each step measured before the next:

| Configuration | Runs | Mean of 52 |
|---|---|---|
| Baseline: single-shot, Nova Lite, chunker-v3 | four runs | 29.2 |
| Single-shot, Claude Haiku, multi-passage rule | three runs | 35.3 |
| Tool loop Phase A (search + read), Haiku | 44, 44, 42 (three) then five runs: 37, 43, 40, 44, 42 | 41.2 |
| Tool loop current main, Haiku, five runs | 33, 45, 44, 36, 43 | 40.2 |
| Single-shot control, Haiku, five runs | 36, 33, 34, 35, 37 | 35.0 |

The full per-question audit is in `docs/reviews/experiment-answers-2026-10-19/` (README.md for reading, answers.csv
for checking, raw/ for the fifteen frozen result files, evidence.json for the passages retrieval offered). The
analysis is `docs/reviews/experiment-three-configs-2026-10-19.md`.

**Read this before quoting a number:** a three-run comparison looked like a regression and was not. Five runs are
now the minimum for any configuration claim. Runs vary between 33 and 45 out of 52 for the same build.

## 3. What was built, in order, and why

All pull requests are in `privateInferenceAI/office-inference-core`.

| PR | What | Why it matters to the next agent |
|---|---|---|
| #153–#158 | Chunker v3 repair, upsert batching, rejected worklist | Chunks are page-bounded, 384–512 tokens; rows above 5,000 lose per-row entries in v4 |
| #159 | Baseline report and runner | `docs/reviews/baseline-v3.md`, `tests/fixtures/data_suite_run.py` |
| #160 | Chunker v4: 5,000-row cap, 200 chunk cap per table, omission marker, data-set cards | Fixed the row explosion. Ordinary tables keep row entries |
| #161 | Sealed data-set cards, 3 MB text threshold | Cards had an empty id and Qdrant rejected them |
| #162 | After report: pollution 15 runs to 0, retrieval 30/34 to 33/34 | Corpus corpus rebuilt under v4 |
| #163 | Answer contract: value first, cite, refuse only when absent; temperature 0 | Neutral on its own but the right baseline for later work |
| #164 | Multi-passage rule | Both Claude models refused every cross-document question until this |
| #165 | Tool loop Phase A: `search_documents`, `read_passages`, numbered evidence, caps, fallback | The five-point win lives here |
| #166 | Measurement record for 2026-10-18 | Superseded numbers; historical |
| #167 | `lookup_value`, honesty rule | Lookup later defaulted off; honesty rule kept |
| #168 | Opt-in tool streaming with progress frames | `/ask/stream` with `"tools": true`; no change for normal clients |
| #169–#171 | Stream done-frame fields; two-figure lookup; ambiguity guard | Lookup refuses to anchor on duplicated figures |
| #172, #174, #176, #178, #179 | Loop budget, automatic lookup before refusal, measured-best settings, prompt/tool consistency, thoroughness rule | Prompt history; see the method failure note below |
| #173 | Gateway identity budget fix: one identity per role, ceiling not remainder | Real bug; the acceptance run had looked like a provider outage |
| #175 | Development analyst ceiling 100 USD | Still in force |
| #177 | Phase B/C report and register rows | `docs/reviews/phase-b-c-2026-10-18.md` |
| #180 | Five-run experiment and corrections | `docs/reviews/experiment-three-configs-2026-10-19.md` |
| this hand-off | Answer audit bundle, generator, hand-off | `docs/reviews/experiment-answers-2026-10-19/`, `scripts/build_answer_audit.py` |

## 4. Where everything lives

**Box (the development deployment):** `ssh -i /home/anthony/ai-stack/ai-stack-key.pem ubuntu@52.204.202.66`,
repository at `/opt/oi-core`, compose file `deploy/office/compose/docker-compose.yml`, rendered configuration in
`/opt/oi-core/rendered/` (gitignored, regenerated by the renderer).

**Accounts and access:** the box and its Bedrock calls are in the credits-holding account `878451098165`
(AgentTarget: AWS MCP `aws-credits`). Anthropic access there was enabled on 2026-10-18: use-case form submitted,
and IAM policy `oi-core-dev-bedrock-invoke` version **v5** carries the Claude Haiku/Sonnet inference profiles and
their foundation-model ARNs in us-east-1, us-east-2, us-west-2. Claude needs the `us.` inference profile as the
model id; the raw model id fails with a validation error. The 5,000 USD Activate credits are in that account.

**Corpus:** documents on the box at `/opt/oi-core/documents/`, archived in S3
`s3://officeinference-corpus-878451098165/` (the corpus is not in the repository; it is 15 GB). The six data sets
are listed in `/opt/oi-core/rejected/datasets.csv`; the six genuinely unreadable files are in
`/opt/oi-core/rejected/rejected.csv` and are waiting for a human to replace or exclude them.

**The answer path, in order:** client app container (`app`, built from the separate `oic-ui` checkout) → `api`
(FastAPI, `services/api/app.py`) → retrieval (`services/retrieval/retriever.py`: TEI embeddings container,
Qdrant `vector`, Postgres lexical arm, Cohere rerank on Bedrock) → tool loop (`services/api/tool_loop.py`) →
LiteLLM gateway (`litellm` container, `services/gateway/custom_auth.py`) → Bedrock → post-checks
(`services/api/answer_contract.py`, `services/retrieval/citations.py`) → audit (`services/audit/audit.py`) →
response.

**Documentation map:** `docs/reviews/` holds every measurement. Start with:
`experiment-three-configs-2026-10-19.md` (what the numbers are), `phase-b-c-2026-10-18.md` (what was built and the
limits), `answer-quality-day-2026-10-18.md` (the single-day progression), and the audit bundle for evidence. The
operator docs are `docs/manual.md` section 13n and `docs/runbook.md` section 6.7.

## 5. How to measure anything (do this before and after any change)

The suite runner is `tests/fixtures/data_suite_run.py`; it runs the 40 questions in `docs/eval/data-questions.yaml`
plus four probes embedded in the runner. From `/opt/oi-core`:

```bash
for n in 1 2 3 4 5; do
  docker compose -f deploy/office/compose/docker-compose.yml --env-file rendered/env run -T --rm --no-deps \
    --entrypoint python -v /opt/oi-core:/w:ro -v /tmp/data_suite_run.py:/suite.py:ro -v /tmp/oic-eval-out:/out \
    -e OI_API_URL=http://api:8088 -e ACCESS_REGISTRY=/w/rendered/access.json \
    eval /suite.py --json-out /out/run-$n.json < /dev/null > /dev/null 2>&1
done
```

Rules established from experience:

- **Five runs minimum per configuration.** Three-run differences of three to five points are noise. The spread
  across five identical runs was 33 to 45 of 52.
- **Freeze everything else.** Corpus, model, gateway image, flags. Change one thing at a time.
- **Keep the raw JSON.** The audit generator reads frozen result files and produces a per-question table; it never
  edits answers. Do not regenerate numbers by re-running retrieval for a comparison; re-retrieval is a different
  measurement.
- **Check for degraded runs.** A budget or provider failure turns answers into `degraded` and deflates totals. The
  suite records `kind`, and the acceptance run that looked like an outage was an exhausted team budget on
  2026-10-18. Check `analyst` team spend against its ceiling before blaming the model.
- **Scoring is strict and literal**: exact values, expected documents cited for answered questions, refusals must
  refuse. The audit CSV carries the pass recorded at run time and a recomputation by the generator; they agreed on
  all 780 rows on 2026-10-19.

Acceptance probes, all passing on this configuration: interface probe 8 rights, activity probe 8, access probe 4,
stream probe 4. Run commands are in `docs/runbook.md` section 6.7. The stream probe's REDACTED right needs
`company/pii-new-starter.md` in the corpus; the file is restored at `/opt/oi-core/documents/company/`.

## 6. Known limits and open work, in priority order

1. **Structured data questions cannot be answered well.** This is the biggest remaining gap and the reason the
   owner can answer the questions by hand but the stack cannot. The model sees at most a few passages per search,
   cannot query rows by column, cannot verify a figure with a second independent query, and has no way to write a
   filter. The proposed next milestone is a **read-only table-query tool**: a constrained query interface over
   `chunk_index` (and any future row store) scoped by the session's access roots, with row caps and audit, plus a
   verification step that re-checks a figure before answering. Expected effect: exact-number rows from about 5–6
   of 8 to 7–8 of 8, and the large-table probe to start passing. Design constraints: read-only, ACL-filtered,
   max rows and bytes, no arbitrary SQL, every call audited.
2. **A pinpoint row inside a very large table still fails.** Rows above the v4 cap are not individually indexed;
   the model reads a bounded group and can pick the wrong line. Possible fixes: store the row ordinal in
   `chunk_index` (it exists in the payload but is not a column) so a row can be fetched exactly, or a table-aware
   read tool. The evidence file for the probe shows the row text the model needed; it was in the group.
3. **Honesty trade.** Single-shot scores marginally better on the answer-that-should-be-a-refusal categories
   (unanswerable 8.0 vs 7.0–7.4; access 15.0 vs 14.4–14.8). The loop answers a little more, including a little
   more than it should. The honesty rule in the loop prompt is the current mitigation; it is a prompt rule, not a
   machine check.
4. **Token streaming with tools is not implemented.** The opt-in tool stream trades token streaming for progress
   frames because the loop needs complete messages to parse tool calls. Normal clients are unaffected.
5. **A provider layer other than Bedrock is parked** as `INF-PROVIDER-001` in `docs/requirements.yaml`. Direction
   approved, not built. LiteLLM is the abstraction; the work is manifest provider blocks, renderer support, key
   handling in `rendered/env`, and a profile gate that refuses external providers on the office profile.
6. **The data path for the six data-set cards is C6 work.** A file classified as a data set contributes one card
   and is not searchable; asking its contents needs the structured data path above.
7. **The six unreadable files** in `rejected/rejected.csv` need a human decision to replace or exclude.

## 7. Traps, written down so they are not repeated

- **Do not conclude from three runs.** The 2026-10-18 phase report treated a 44, 44, 42 triple as a peak, declared
  the final configuration worse, and shipped two prompt changes. The five-run mean of that same build was 41.2, and
  the two configurations were 1.0 point apart with overlapping ranges. The prompt changes were noise chasing. That
  correction is in `experiment-three-configs-2026-10-19.md` and is the most important method lesson here.
- **The deployment override is in `rendered/`.** The model is pointed at Claude by editing
  `rendered/manifest.json` and `rendered/litellm.yaml` after rendering; the tracked manifest still names Nova Lite
  because that is the repository default. Re-rendering overwrites the override, so re-apply it (the exact commands
  are in runbook 6.7) whenever you render. The box's checkout was also dirty on 2026-10-18 because of this; those
  changes were reverted before pulling. Prefer the rendered override over editing the tracked manifest on the box.
- **Budget failures look like outages.** Over-exhausted analyst budget returns `degraded` with reason `budget`; the
  gateway identity bug (#173) made it fire at half the ceiling and all roles shared one spend counter. Both are
  fixed; check team spend and ceiling before diagnosing anything as a provider problem.
- **An evidence run is not a result run.** Re-running retrieval for an audit can reorder reranking. The recorded
  retrieval in the frozen JSON is authoritative; the evidence capture is for human reading.
- **The corpus is not in git.** Do not assume `documents/` is committed. The box has it; S3 has the archive; the
  rejected and data-set lists live beside it.
- **One pre-existing test failure** exists on main and has existed all day:
  `tests/test_retrieval.py::test_an_identifier_the_vector_arm_missed_is_found_by_the_lexical_arm` fails with
  `fused_score 0.0`. It is unrelated to the answer path work and should be investigated separately.
- **The four probes are not equal.** `probe_row_normal` (PR1) and `probe_pollution` (PR4) are known imperfect
  probes; their failures are not clean system evidence and should be redesigned before they are used to judge a
  change.
- **Do not rewrite the prompt without measuring.** Prompt changes moved the suite by seven points in both
  directions on three-run evidence and netted nothing on five. The prompt currently in main is the one that
  measured 40.2; treat it as frozen until the query tool exists.

## 8. What not to misrepresent

- The tool loop is worth about **five points**, not eight: 40.2–41.2 against 35.0, five runs each.
- The Phase A build and current main are **equivalent**; there is no evidence that either is better.
- **No configuration has reproduced 44, 44, 42 as a mean.** That triple was the top of a distribution.
- `lookup_value` is implemented, tested, and **default off** because it measured neutral to negative on this
  corpus of duplicated census figures. It is not evidence that exact lookups are impossible; the structured query
  tool is the proposed answer.
- The cross-document category is genuinely fixed (2–4 of 8 to 6–8 of 8). The honesty categories are slightly worse
  with the loop, which is a real trade rather than a rounding error.
- The corpus is complete by policy: 539 indexed, six data-set cards, six rejected files for a human. It is not
  "544 of 544 indexed".

## 9. First tasks for the next agent

1. Read this document and the three reports named in section 4. Open the audit bundle and spot-check five
   questions against `answers.csv`; confirm the numbers reproduce from the raw files with
   `python3 scripts/build_answer_audit.py`.
2. Verify the live configuration matches the table in section 1 (`docker exec api printenv`, `docker logs api |
   grep tool-loop`, one question through `ask_client.py`). If it does not, restore it before changing anything.
3. Design the structured query tool (section 6.1) as one PR: tool schema, executor with ACL and row caps, audit
   events, tests, and a five-run comparison against the current configuration. Do not retune the prompt in the
   same change.
4. Keep the PR discipline: branch, why/what/how-checked/risk in the body, merge, squash; evidence in
   `docs/reviews/`.
5. Ask the owner about the six rejected files and the six data-set cards when the corpus comes up; both are
   waiting on a human decision.

## 10. Environment facts worth writing down

- Box SSH: `ssh -i /home/anthony/ai-stack/ai-stack-key.pem ubuntu@52.204.202.66`; cost about 0.81 USD/hour; the
  owner decides when to stop it.
- Claude access commands (render, override, restart) are in `docs/runbook.md` section 6.7.
- Analyst team ceiling: `update "LiteLLM_TeamTable" set max_budget = 100 where team_id = 'analyst'` (and the
  manifest `budgets` block says 100 for dev).
- The data suite output files used for this hand-off are also committed under
  `docs/reviews/experiment-answers-2026-10-19/raw/` so the experiment is reproducible without the box.
- Repo: `github.com/privateInferenceAI/office-inference-core`, `gh` authenticated as `anthonystachowitz-wq`.
