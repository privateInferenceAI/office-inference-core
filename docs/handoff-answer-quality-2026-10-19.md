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
| Answer path | Tool loop on, six steps, ten tool calls. `search_documents`, `read_passages` and the row query are offered; `lookup_value` is off. The gateway fails over to the same Haiku from us-east-2 and then to Nova Pro if the primary deployment fails |
| Flags | `OI_TOOL_LOOP=on`, `OI_TOOL_MAX_STEPS=6`, `OI_TOOL_MAX_CALLS=10`, `OI_TOOL_QUERY=on`, `OI_TOOL_LOOKUP=off`, `OI_TOOL_HONESTY=on` |
| Corpus | 539 documents, 100,478 chunks, six data-set cards, six dead letters excluded by owner decision (0003). Frozen since 2026-10-18 |
| Measured quality | On the corrected instrument (2026-10-19 evening): **50.6 of 52** (52, 50, 51, 51, 49). The row-query milestone measured 46.6 against a same-day control of 43.0 on the old instrument; the two are not comparable because three questions were corrected (section 2c) |
| Measurement set | 40 questions + 4 probes, 52 role-runs per suite run |
| Development budget | Analyst team ceiling 100 USD per 30 days, about 0.4 USD per suite run |
| Repo state | `main` at `30b0100` plus this update; 974 tests pass, zero open PRs |

The one-line finding, updated: **retrieval works; the answer layer was the problem; the tool loop fixed about five
points of it, and the structured row query built on 2026-10-19 added another 3.6 on a same-day control. The
remaining points are concentrated in four specific questions, provider reliability and a harder evaluation set.**

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

## 2b. Update 2026-10-19 afternoon: the row query tool

A second session read this hand-off and built the item that was first in section 6: a read-only structured row
query (`query_rows`, PR #183, one-line evidence fix in #184). The tool takes one to four literal strings and returns
the exact table rows containing all of them, with document, page and row ordinal, numbered into the evidence the
citation verifier checks. One parameterised SELECT over `chunk_index` with the session's access roots in the where
clause; constants bound the scan, rows and bytes; a match spanning more than three documents without a `doc_id` is
refused with examples instead of anchored. Every call is an audited `tool_call`. Off by default behind
`OI_TOOL_QUERY`; the flag is the rollback.

| Configuration | Totals | Mean | Range | Degraded |
|---|---|---|---|---|
| Query off (same-day control) | 43, 44, 43, 43, 42 | 43.0 | 42 to 44 | 7, all unsupported number |
| Query on (measurement of record) | 47, 45, 47, 47, 47 | **46.6** | 45 to 47 | 11, all provider unavailable |

Ranges do not overlap. Exact-number questions went from 4.8 to 7.4 of 8; the large-table pinpoint probe from 0 to
4 of 5; the control's seven unsupported-number refusals became zero. Details and raw runs:
`docs/reviews/query-tool-2026-10-19/`. A first treatment (46.4) exposed a tool defect where a full evidence
namespace silently swallowed the queried row; it was fixed, re-measured, and both result sets are kept in the
evidence folder.

**The control measured 43.0 while the frozen mean for the same configuration was 40.2.** Same flags, same model,
one day apart, so the environment drifted. That is why the comparison is against a same-day control; absolute
scores from different days are not comparable. The treatment window also carried eleven `provider_unavailable`
degradations, which understate the treatment rather than overstate it.

**What still fails, counted from the five treatment runs (27 failing runs of 260, 89.6 percent pass):**

| Failure | Runs | What it is |
|---|---|---|
| `provider_unavailable` blips | 5 | A04 exec, A07 staff, N02, N03, PR2; infrastructure, not answers |
| A06 executive | 5/5 | Persistent partial answer on the SDWIS legal-entity question; a real answer-quality gap |
| P05 | 5/5 | Persistent wrong content on the post-orders question; retrieval/answer gap |
| P06 | 5/5 | Persistent wrong content on the post-orders question; retrieval/answer gap |
| U08 | 4/5 | Answers instead of refusing; the honesty trade |
| PR4 | 2/5 | Known-imperfect probe; not clean evidence |
| N04 | 1/5 | One refusal despite the value being in context |

So 22 of the 27 remaining failures are deterministic on four questions, and five are infrastructure. The immediate
next work is not another prompt: it is those four questions, provider fallback, and a fairer probe set.

**Is 46.6 good?** On this instrument, yes: 89.6 percent, against 35.0 single-shot and 40–41 before the tool. It is
not product accuracy, and it is close to the instrument's ceiling, so do not chase the last few points here by
tuning. The defensible target after the four specific failures are diagnosed and provider fallback exists is about
48 to 50 of 52 on this suite, and the number that matters more is how a widened evaluation set scores, because the
current one cannot distinguish 46 from 48.

**Acceptance probes on the live build are now evidenced**: `docs/reviews/query-tool-2026-10-19/probes/` holds the
interface (8 rights), activity (8), access (4) and stream (4) outputs, captured against the deployed code.

## 2c. Update 2026-10-19 evening: the four deterministic failures diagnosed

The four questions that failed in every run were diagnosed against the indexed source text. Three were defects
in the instrument, not the product: **A06** asked an ambiguous question (the SDWIS document states three
uniqueness rules and the model answered one of them correctly), **P05** asked for a telephone number that the
answer redaction masks as [REDACTED-PHONE] by policy, and **P06** asked for a single FPCON condition in a
sample-post-orders template that lists all four. The three were corrected in `docs/eval/data-questions.yaml`;
the question count and categories are unchanged.

The fourth, **U08**, is a genuine honesty failure: the corpus contains no parental-leave policy, and the model
assembles one out of generic employment law in an instructions document. It is not fixed here. The prompt is
frozen until the row query is measured on a second corpus, and no deterministic check for this class of claim
exists in the current architecture. The retest is defined: when the prompt unfreezes, add "a policy, procedure
or plan" to the honesty rule and measure five runs.

Five runs on the corrected instrument: **52, 50, 51, 51, 49, a mean of 50.6**, with the three corrected
questions passing in every run and U08 failing four of five. This is a new baseline, not a product improvement.
The diagnosis, the evidence and the five raw runs are in `docs/reviews/deterministic-failures-2026-10-19.md`.

**The instrument is now close to its ceiling.** The remaining section 6 items need a wider instrument to be
measurable, which is why the probe redesign and the wider evaluation set come before any further score claim.
Provider fallback can be verified deterministically instead of statistically: make the primary model unavailable
and check that the fallback answers.

## 2d. Update 2026-10-19 evening: provider fallback

A role may now declare a second remote model in the manifest. Rendering emits a second gateway group and a
`litellm_settings.fallbacks` mapping, so one failover covers the question path, the stream and every tool-loop
call; retries stay at zero, so the gateway makes one attempt at the primary and then one at the fallback. It is
cloud-only and enforced by the validator, because on an office build a local primary failing over to a remote
model would be an undeclared egress path. The lifecycle check reads the fallback too.

**Measured with a deliberately broken primary**, which is the only deterministic way to test it: the repository's
fault stub returned 503 on every request to the primary, and five runs scored **49, 49, 49, 48, 49 (mean 48.8)
with zero degraded answers**, 847 failed primary attempts all answered by the fallback. The control, the same
broken primary with no fallback, degraded every answer. Healthy traffic is unchanged: five runs with the fallback
configured but inert scored 51, 51, 51, 51, 49 (mean 50.6), the same as before the change. The development
deployment runs Haiku with a Sonnet fallback. Evidence: `docs/reviews/provider-fallback-2026-10-19.md`.

**The answering-model gap is closed (same day, follow-up PR).** The gateway sets the routing decision on every
response — `x-litellm-model-group`, `x-litellm-model-name`,
`x-litellm-attempted-fallbacks` — and the API now reads it on the question path, the tool loop, the job path
and both stream paths. The answer carries `resolved_model` (what answered), `configured_model` (what
the manifest names), `model_group` and `fallback_used`; the `model_call` row's `model`
column is the model that answered and its payload keeps the role as `model_role`. Verified by failing the
primary on purpose: the answer reported `bedrock/us.anthropic.claude-sonnet-4-5-20250929-v1:0` with
`model_group: analyst-fallback` and `fallback_used: true`, and the row said the same.

## 2e. Update 2026-10-19 night: the chain, the tracked model, and the instrument

**The fallback is a chain now, and the deployment renders from a tracked manifest.** `models.<role>.fallback`
takes a list tried in order, and an entry may carry `@<region>`. The development deployment's analyst is
`manifests/dev-cloud-claude.yaml`: Claude Haiku 4.5 through the us profile, the same Haiku from us-east-2,
then Nova Pro. The model choice no longer lives in the untracked `rendered/` override, so a re-render cannot
revert it. **A client's account must allow the fallback Region's inference-profile ARN**: the credits account's
`oi-core-dev-bedrock-invoke` policy went to v6 for exactly that, and the runbook's installation section tells
a client to copy the shape. Without it the second entry fails AccessDenied when it is needed.

**A client who is not serviced can change a model.** `scripts/model_admin.py` lists the lifecycle table and
what a deployment names, checks a manifest with the same two gates CI runs, and rewrites one role's model and chain
only when the whole candidate manifest passes; it writes only the models block and never calls AWS.
`docs/model-lifecycle.md` carries the procedure. The lifecycle refresh recorded a real finding: Sonnet 4.5,
the previous fallback, is LEGACY; Sonnet 5 is ACTIVE in the catalog but AccessDenied for this account, so the
chain's model entry is Nova Pro.

**The instrument was redesigned and measured (PR #192).** PR1 and PR4 are independent probes now (they had
duplicated N07's premise and N06's fact), PR5 measures the PII gate end to end, and unanswerable questions are
re-checked against the index on every run. That check found U05's stale pattern (the topic word matched
cafeteria-plan tax text); it now reads "cafeteria menu". Five runs on the new instrument: **52, 52, 52, 51, 52 of
53 (mean 51.8)**, all five probes passing in every run, all eight absent patterns at zero, U08 failing in all five
and N04 in one. This is a new baseline, not an improvement claim, because the run set changed.

**A wider evaluation set is ready (PR #193).** 66 questions: the live 40 plus 26 authored from index-verified facts
(aggregation, date arithmetic, multi-hop, untouched-document coverage), with a validator that proves computed
answers through their operands. It is not the scored instrument yet.

**Chain measurement** (primary failed on purpose, instrument = the redesigned probes): **(pending)**.

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
| #183 | `query_rows` structured row query, safe bounds, audit, tests, docs | The 3.6-point milestone; off by default behind `OI_TOOL_QUERY` |
| #184 | Evidence record names the build each configuration ran on | Makes the comparison auditable |
| #185 | This update, probe evidence, hand-off corrections | The starting point for the next agent |

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
are listed in `/opt/oi-core/rejected/datasets.csv` and stay cards. The six genuinely unreadable files are
excluded permanently by owner decision (`docs/decisions/0003-corpus-decisions.md`) and listed in
`/opt/oi-core/rejected/exclusions.csv`; they remain on disk and a replacement re-ingests.

**The answer path, in order:** client app container (`app`, built from the separate `oic-ui` checkout) → `api`
(FastAPI, `services/api/app.py`) → retrieval (`services/retrieval/retriever.py`: TEI embeddings container,
Qdrant `vector`, Postgres lexical arm, Cohere rerank on Bedrock) → tool loop (`services/api/tool_loop.py`) →
LiteLLM gateway (`litellm` container, `services/gateway/custom_auth.py`) → Bedrock → post-checks
(`services/api/answer_contract.py`, `services/retrieval/citations.py`) → audit (`services/audit/audit.py`) →
response.

**Documentation map:** `docs/reviews/` holds every measurement. Start with:
`experiment-three-configs-2026-10-19.md` (the five-run configuration comparison), `query-tool-2026-10-19/`
(the row query: report, `compare.py`, raw runs and now the probe evidence), `phase-b-c-2026-10-18.md` (what was
built and the limits), `answer-quality-day-2026-10-18.md` (the single-day progression), and the audit bundle for
per-question evidence. The operator docs are `docs/manual.md` section 13n and `docs/runbook.md` section 6.7.

## 5. How to measure anything (do this before and after any change)

The suite runner is `tests/fixtures/data_suite_run.py`; it runs the 40 questions in `docs/eval/data-questions.yaml`
plus four probes embedded in the runner. From `/opt/oi-core`:

```bash
# from /opt/oi-core, five runs (the minimum for a configuration claim)
scripts/run_answer_suite.sh 5 /tmp/oic-suite
```

The script runs the committed runner (`tests/fixtures/data_suite_run.py`) and writes one JSON per run. There is no
staged copy in `/tmp` any more; an earlier measurement depended on one and could not be regenerated from the
repository alone.

Rules established from experience:

- **Five runs minimum per configuration.** Three-run differences of three to five points are noise. The spread
  across five identical runs was 33 to 45 of 52.
- **Freeze everything else.** Corpus, model, gateway image, flags. Change one thing at a time.
- **Keep the raw JSON.** The audit generator reads frozen result files and produces a per-question table; it never
  edits answers. Do not regenerate numbers by re-running retrieval for a comparison; re-retrieval is a different
  measurement.
- **Use a same-day control.** The same configuration measured 40.2 one day and 43.0 the next. A comparison
  across days is not a comparison; run the control beside the treatment.
- **Probe evidence lives with the milestone.** The four acceptance probes were run against this build and their
  outputs are in `docs/reviews/query-tool-2026-10-19/probes/`.
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

The structured row query is **done and measured** (section 2b). The priorities below replace the old list.

0. **Order of work and expected effect.** Diagnose the four deterministic failures (about two to three points), then
   provider fallback (one to two), then probe redesign and the wider evaluation set (measurement quality, not score),
   then the acceptance panel. Do not start another item before the five-run comparison of the previous one.
1. **The four deterministic question failures are diagnosed.** Three were instrument defects and their questions
   were corrected (A06, P05, P06); U08 is a genuine honesty failure, deferred with a defined retest. See section 2c
   and `docs/reviews/deterministic-failures-2026-10-19.md`. The corrected instrument measures 50.6 of 52, so the
   next score claim needs a wider instrument rather than another prompt or tool change.
2. **Provider reliability is built, measured, and credited.** The fallback is a chain: the same model from
   another Region first, then a different model (section 2e). The answer and its audit row credit the model that
   answered (section 2d). The deployment renders from `manifests/dev-cloud-claude.yaml`, and
   `scripts/model_admin.py` is the client's supported way to change a model after installation. Evidence:
   `docs/reviews/regional-fallback-2026-10-19.md`.
3. **Done: the bad probes are redesigned (PR #192).** PR1 and PR4 are independent of the question set, PR5 was
   added for the PII gate, and unanswerable questions are re-verified against the index every run. All five probes
   passed in every run of the new baseline.
4. **Done: a wider evaluation set is ready (PR #193).** 66 questions, 26 of them new, every one mechanically
   validated against the index. It is not the scored instrument yet; measuring it is the next work.
5. **The acceptance panel.** Latency is now captured per answer and summarised (p50, p95) by the runner, and
   `scripts/acceptance_panel.py` joins the runs to the gateway's spend records for cost per answer,
   `docs/reviews/acceptance-panel-2026-10-19.md` carries the numbers.
6. **Row-query default policy.** Keep it opt-in in code; a deployment turns it on after measuring its own corpus
   (the development deployment has it on; its evidence is one corpus).
7. **`lookup_value` is superseded for table rows.** Keep it implemented and off; plan retirement once `query_rows`
   has been measured on a second corpus.
8. **Provider layer (`INF-PROVIDER-001`) remains parked.** The six rejected files are now excluded by owner
   decision and need no further action unless a replacement arrives. The data-set cards stay cards: they are not
   reachable by the row query because they have no rows, and a data path is a separate piece of work with no client
   requirement behind it yet.

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
- **The test suite is hermetic now, and must stay that way.** One red test on 2026-10-18
  (`test_an_identifier_the_vector_arm_missed_is_found_by_the_lexical_arm`, `fused_score 0.0`) looked like a
  retriever bug. It was the suite calling the live Bedrock reranker because the workstation had credentials:
  tests behaved differently on every machine, cost money, and failed only where the network answered. An autouse
  fixture in `tests/conftest.py` disables the reranker outside the rerank tests. If a unit test starts reaching the
  network, fix the test, not the component.
- **The four probes are not equal.** `probe_row_normal` (PR1) and `probe_pollution` (PR4) are known imperfect
  probes; their failures are not clean system evidence and should be redesigned before they are used to judge a
  change.
- **Absolute scores drift between days.** The same build measured 40.2 and 43.0 on consecutive days. Use a
  same-day control; never compare a number in an old report to a number today and call it a regression.
- **Two infrastructure defects were found by the second session, both fixed.** The committed suite script could
  not write its own output directory as the eval image's non-root user, and the API never passed `--tool` to
  the audit tool, so every tool-call row had an empty `tool` column. If you see either symptom again, check the
  current code before diagnosing the component.
- **A tool that returns no rendered lines is not absence.** The first row-query treatment lost N06 because a
  full evidence namespace silently swallowed a matched row. Rows now get reserve slots and an explicit error.
- **Do not rewrite the prompt without measuring.** Prompt changes moved the suite by seven points in both
  directions on three-run evidence and netted nothing on five. The prompt currently in main is the one that
  measured 40.2; treat it as frozen until the query tool has been measured on a second corpus.

## 8. What not to misrepresent

- The tool loop is worth about **five points**, not eight: 40.2–41.2 against 35.0, five runs each.
- The Phase A build and current main are **equivalent**; there is no evidence that either is better.
- **No configuration has reproduced 44, 44, 42 as a mean.** That triple was the top of a distribution.
- `lookup_value` is implemented, tested, and **default off** because it measured neutral to negative on this
  corpus of duplicated census figures. It is not evidence that exact lookups are impossible: the structured row
  query (`query_rows`) is the answer that worked, and it is now the measured tool for table rows.
- The cross-document category is genuinely fixed (2–4 of 8 to 6–8 of 8). The honesty categories are slightly worse
  with the loop, which is a real trade rather than a rounding error.
- The row query is worth **3.6 points against a same-day control** (46.6 against 43.0), not 3.6 against the
  frozen 40.2. It removes the unsupported-number failure class on this corpus and brings exact-number questions
  to 7.4 of 8, while the remaining failures are concentrated on four specific questions.
- **46.6 of 52 is 89.6 percent on this instrument, not product accuracy.** One corpus, five runs, and 22 of the
  27 remaining failing runs are deterministic on four questions. Do not quote it as a general capability number.
- The corpus is complete by policy: 539 indexed, six data-set cards, six rejected files for a human. It is not
  "544 of 544 indexed".

## 9. First tasks for the next agent

1. Read this document, section 2b, and `docs/reviews/query-tool-2026-10-19/README.md`. Spot-check the query-tool
   numbers with `python3 docs/reviews/query-tool-2026-10-19/compare.py` and the probe evidence in
   `docs/reviews/query-tool-2026-10-19/probes/`.
2. Verify the live configuration matches section 1, including `OI_TOOL_QUERY=on`, and ask one question that needs
   a table row to see the tool in the audit chain (`select tool, count(*) from audit_events group by tool`).
3. Diagnose the four deterministic failures in section 6 item 1, one at a time, with five-run before and after.
   Do not bundle them with a prompt rewrite.
4. Add the provider fallback and measure it (section 6 item 2), then redesign the two bad probes and run the
   acceptance panel with latency and cost.
5. Extend the evaluation set (section 6 item 4) before claiming further improvement.
6. Keep the PR discipline and evidence rules from section 5.

## 10. Environment facts worth writing down

- Box SSH: `ssh -i /home/anthony/ai-stack/ai-stack-key.pem ubuntu@52.204.202.66`; cost about 0.81 USD/hour; the
  owner decides when to stop it.
- Claude access commands (render, override, restart) are in `docs/runbook.md` section 6.7.
- Analyst team ceiling: `update "LiteLLM_TeamTable" set max_budget = 100 where team_id = 'analyst'` (and the
  manifest `budgets` block says 100 for dev). Development spend was about 34 USD when this update was written.
- The row query flag is `OI_TOOL_QUERY`; the development deployment has it on, the code default is off.
- Tool-call audit rows now carry the tool name: `select tool, count(*) from audit_events group by tool`.
- Acceptance-probe evidence for the current build is committed under
  `docs/reviews/query-tool-2026-10-19/probes/`.
- The data suite output files used for this hand-off are also committed under
  `docs/reviews/experiment-answers-2026-10-19/raw/` so the experiment is reproducible without the box.
- Repo: `github.com/privateInferenceAI/office-inference-core`, `gh` authenticated as `anthonystachowitz-wq`.

## 11. Decisions and their state

Decisions taken while the owner was away on 2026-10-19, updated after the row-query milestone:

1. **Box running.** Still running; the next pieces of work (reliability, the four failures, acceptance panel) need
   it. Cost about 0.81 USD per hour; the owner decides when to pause.
2. **Deployment frozen after each measurement.** It is now on the measured row-query build: main `30b0100`,
   query on, loop on, Claude Haiku, six steps, ten calls, lookup off, honesty on, ceiling 100.
3. **Corpus decisions were taken by the owner on 2026-10-19 and are recorded in
   `docs/decisions/0003-corpus-decisions.md`.** The six unreadable files are **excluded permanently**, not
   deleted: they are listed in `rejected/exclusions.csv`, which the worker reads and never writes, so they leave
   the worklist and appear in `rejected.json`'s `excluded` list with the reason. A replacement at any of those
   paths re-enters on its own because a failure is keyed on the bytes that failed. The six data-set cards
   **stay cards**: converting them to searchable rows would re-create the row explosion chunker-v4 removed, and
   the row query cannot reach a card with no rows. Revisit only with a real client data-path requirement.
4. **Test suite was fixed to be hermetic** (the red test was tests calling the live reranker). It is now 974 passed,
   22 skipped, 0 failed; keep it that way.
5. **The suite runner is reproducible** (`scripts/run_answer_suite.sh`; the second session fixed a permission bug in
   it). Five runs minimum, same-day control.
6. **A new agent can start from this document**, as recommended. The first tasks are section 9.
7. **Row query remains opt-in by default in code**; the development deployment has it on. Promotion to default is a
   per-deployment decision after that deployment's own five-run measurement. **Confirmed by the owner 2026-10-19.**
8. **`lookup_value` remains implemented, tested and off**, superseded for table rows by `query_rows`; retirement is
   planned after the row query is measured on a second corpus. **Confirmed by the owner 2026-10-19; do not delete
   it yet.**
9. **No prompt tuning is planned.** The remaining points are in specific failures, reliability and a harder
   evaluation set, not in prompt wording.

## 12. What to give the next agent

Give these, in this order. Nothing else is required; the repository, the accounts and the box are described above.

1. This document, as the first read. It is self-contained for state, method, traps and next work.
2. The three evidence locations, linked from section 4:
   - `docs/reviews/query-tool-2026-10-19/` (report, `compare.py`, raw runs, probe outputs)
   - `docs/reviews/experiment-three-configs-2026-10-19.md` (the five-run comparison)
   - `docs/reviews/experiment-answers-2026-10-19/` (the per-question audit and raw runs)
3. Repository access: `github.com/privateInferenceAI/office-inference-core`, currently main `fa792e5` or later.
4. Box access as described in section 10, and the instruction that the deployment is frozen until a five-run
   comparison justifies a change.
5. The kick-off message below, or an equivalent in the owner's own words.

Kick-off message:

> Start by reading `docs/handoff-answer-quality-2026-10-19.md` in the repository. It is the complete state of the
> work: what was built, the correct numbers and where they came from, the measurement rules (five runs minimum,
> same-day control, frozen configuration), the traps that already caused wrong conclusions, and the first tasks.
> Verify the live deployment against section 1 before changing anything. The next work is in section 6, in order:
> diagnose the four deterministic failures (A06 executive, P05, P06, U08), add provider fallback, redesign the two
> bad probes and widen the evaluation set, then run the acceptance panel. Keep changes to one logical unit per PR
> with the five-run before and after. The row query is on for the development deployment and opt-in by default in
> code; do not change its default. Ask the owner before touching the rejected files, the data-set cards, or the
> corpus.
