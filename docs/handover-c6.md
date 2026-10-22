# Handover: Office Inference Core, C6 agent runtime and MCP gateway

**Written 2026-10-22 by the agent who finished the answer-quality and simplification work.** Read the whole
document before you touch anything. It is long on purpose. Anthony asked the previous handover to be long because
a handover that leaves you guessing costs the first hour of the engagement.

**Target update, 2026-10-22. The cloud build is the Cloud Workspace.** A company does not pay a build fee and a
retainer for document chat. The cloud product is a workspace agent: it researches, writes files, runs code, uses
approved web access and client systems, produces artifacts, and stops for approval before anything irreversible.
The design is `docs/design/cloud-workspace.md`; the phase is `WS` in `docs/requirements.yaml`; the runtime
shape is `docs/decisions/0009-agent-runtime-shape.md`. C6 is the execution core of that product, not the whole
product. The office build remains a separate product, and its rule that nothing leaves the building is not a rule
for this build.

**State update, 2026-10-22.** C6 Section 1, the eight C5 approval rows, is built and in PR #239. It makes the
decision and its chain row one transaction, records refused attempts, covers the approval column by chain version
3, and adds the re-attestation command. Merge it before building Section 2; the runtime gate depends on it. Section
2, the runtime, is the next piece and its design record is 0009.

---

## 0. Read this first

You are picking up a real product with a client promise. The stack installs, ingests a corpus, answers with
citations, enforces access boundaries, and records an audit chain. C5, workflows and approvals, is built. C6 is in
progress: Section 1, the approval rows, is built in PR #239, and Section 2, the agent runtime, is next.

**Three things before anything technical.**

**The product standard is a sentence.** A permission, a redaction, an audit record or a data boundary has to read
as enforced. If a control only exists on paper it is not a control. That sentence has settled every design
argument in this repository.

**The governing constraint is in the owner's words:** nothing the client depends on may live on infrastructure
Office Inference operates after handover. The client owns the account, the quota, the bill and the audit record.
When you design a connector, a credential store or a gateway, ask whose infrastructure runs it after we leave.

**Verify, do not recall.** Every defect worth finding here was found by running something. If you believe a page,
run it. If you believe a check passes, run it. If you believe a file is committed, ask git. In the last few days
alone, silent model fallbacks made four measurement runs look clean when they were not, a migration comment with
an apostrophe broke the migration, and a "fresh" image ran old code until it was rebuilt. All three were found by
commands, none by reading.

---

## 1. Who Anthony is, and how he works with you

He is the owner and the decision-maker. He is not a passive approver and does not want to be one.

**He works with you as a co-pilot.** He rides along, asks hard questions, and expects pushback with evidence. When
he says something that contradicts your plan, he is usually right; find out why before defending the plan.

**The operating model: owner with a threshold.** You decide the small things. Bring him anything that changes
scope, price or a client-facing promise.

**On big decisions, research first, then bring findings and a recommendation.** He said explicitly that he wants
research agents used before big decisions so the stack is built the way professional stacks are. The Bedrock
capacity work at the end of this session is the pattern: three research agents, a recorded comparison, a
recommendation, then a decision.

**He is currently pushing hard on simplicity and maintainability.** His words: he is not worried about model cost,
he is worried about code bloat and stack bloat, because every moving part is another thing to check when something
breaks. Simple is better. Do not add a subsystem for one requirement if a small check will do. Do not add a flag
without a measured reason and a plan to remove it.

**What he cannot stand:** over-explaining, hype, being asked a question you could answer with one command, large
files pasted into chat. Be concise and specific.

**Never use the dollar sign character in a reply.** The Web GUI treats paired dollar signs as math delimiters and
turns the surrounding text into vertical gibberish. Write USD 5,000, 5,000 dollars, or 5K.

**Match his writing style** in anything written for him: plain, direct, first person, contractions, short
paragraphs, numbers over claims, no em-dash chains, no rule-of-three lists, no quotable epigram sentences, no hype
adjectives, no exclamation marks. Read it aloud and cut anything he would not say.

**Context and honesty about it.** Tell him the truth about how much room you have left. He plans around it.

---

## 2. The product and where it stands

Office Inference builds a self-hosted stack for small businesses, five to fifty people. Four offerings: Standard
Cloud and Compliant Cloud in the client's own cloud account, Standard Cloud Hosted in our account, and In-Office
on hardware in their building. One core stack, four deployments. The cloud tiers differ legally, not physically.

The repository is office-inference-core, private, at github.com/privateInferenceAI/office-inference-core, cloned at
/home/anthony/projects/office-inference-core.

The stack is Docker Compose: FastAPI assistant API, LiteLLM gateway, Qdrant vectors, Docling parser and CPU
embeddings, Postgres, a React client in a separate repository, and the audit, intake and workflow services. The
office build uses local models; the cloud build uses Bedrock.

**State at this handover:**

- C0 through C5 are built and deployed. C6 is the next build phase.
- The answer path is at a measured 37 of 37 on the second corpus with Claude Sonnet 4.6 and the accuracy answer
  profile, five consecutive runs. The earlier clean Bedrock run had 185 Sonnet calls and zero fallbacks.
- The answer path was simplified in the last two days: one answer profile setting replaces ten environment
  switches, the tool loop and three tools are constants, and dead payload fields and dead branches were removed.
- A clean end-to-end install was executed and accepted on 2026-10-22. It started from a bare Ubuntu instance and a
  pinned archive, ingested corpus 2 to 80 documents and 24,296 chunks, passed the four acceptance probes and a
  5 of 5 corpus-2 answer check, and made 42 Sonnet calls with no fallback. Instance i-02aba2feef08f8e7b is
  stopped. The runbook is docs/runbooks/fresh-install-agent.md; the evidence report is
  docs/reviews/fresh-install-2026-10-22/. That runbook is the fastest way to bring up a box you can test C6 on.
- Bedrock capacity is the current external constraint. The account has a 150,000,000 cross-model daily token pool
  while the published default is 700,000,000, Sonnet 4.6 has no separate daily allowance, and a day of heavy
  measurement can exhaust the usable allowance after roughly two dozen Sonnet calls. The gateway silently falls
  back to Haiku or Nova, so measurement runs now refuse any run containing a fallback call.

---

## 3. Where everything lives

**Workstation:** /home/anthony/projects/office-inference-core. SSH key /home/anthony/ai-stack/ai-stack-key.pem.

**AWS credits account 878451098165**, us-east-1. Use the aws-credits MCP tools, assumed role DSHAgentRole, for AWS
work. Do not use the default account for this product.

- Dev box: instance i-096bebd46308d1673, m7i.4xlarge, Elastic IP 52.204.202.66, deployment root /opt/oi-core.
  Stopped between tasks. Its volumes survive a stop; /tmp may not.
- Corpus-2 box: instance i-05fa6edd5284b7881, m7i.4xlarge, deployment root /opt/oi-core2, no Elastic IP, so the
  public IP changes on every start. Stopped at handover.
- Fresh-install box: instance i-02aba2feef08f8e7b, m7i.4xlarge, us-east-1b, 200 GB gp3, deployment root
  /opt/oi-core, instance profile oi-core-dev-instance, no Elastic IP. Stopped, not terminated. Its root volume
  holds /opt/oi-core/evidence/fresh-install/. The accepted install is documented in the runbook and evidence
  report named above.
- Security group sg-09d9bfffbd28014c9 allows SSH from 47.164.45.109/32. Instance profile oi-core-dev-instance
  carries the Bedrock permissions and SSM.

**S3 bucket officeinference-corpus-878451098165** holds corpus assets. Keys at handover include
fresh-install/oi-core-795b9a6.tar.gz, code archive at commit 795b9a6, and
second-corpus/second-corpus-2026-10-22.tar.gz, 80 documents, sha256 in the fresh-install runbook.

**Run archives:** /home/anthony/oi-eval-runs on the workstation, including accuracy-runs.tgz,
confirmation-runs.tgz, deepseek-refactor-runs.tgz, dsbedrock-runs.tgz and REINDEX-STATUS.md.

**The documents that matter, in reading order:**

- docs/requirements.yaml: the register and source of truth for what C6 must verify. Search for "phase: C6".
- docs/lab/FINISH-SCOPE.md: what finished means and the section plan. Sections 2, 3 and 4 are the agent runtime,
  MCP gateway and provider layer.
- docs/decisions/0002-agent-topology.md: the agent topology decision.
- docs/build-plan.md section 8: C6 ends with an agent task with a sub-agent and a checkpoint.
- docs/decisions/0005-measurement-hygiene.md and scripts/suite_hygiene.py: the measurement rules, including the
  fallback guard added on 2026-10-22.
- docs/reviews/model-candidates-2026-10-22/: model results and the rule for changing the preferred model.
- docs/reviews/bedrock-capacity-2026-10-22/: the quota research and its product implications.
- docs/handover-ingestion-2026-10-22.md: ingestion, retrieval and measurement, written for the side ingestion
  project. C6 depends on the same tools and evidence, so read it too.

---

## 4. How this project builds a thing

1. **One logical change per pull request.** No sweeping PRs. A PR carries code, tests, docs and an evidence record
   under docs/decisions or docs/reviews.
2. **Measure before you claim.** Answer-path changes get five serial runs on the same corpus and question set,
   with a same-day control. A single run is a smoke test, never a result. The suite wrapper refuses concurrent
   runs, records load and memory, enforces a daily token budget, and refuses runs containing model fallbacks.
3. **Validate the configuration.** Manifests go through scripts/validate_manifests.py and
   scripts/check_model_lifecycle.py. A manifest naming a deprecated model fails on purpose.
4. **Rebuild the image, not just git pull.** The previous handover's signature failure was a deployment running
   code nobody could point at. Fetch first, count commits behind, pull, rebuild, verify the running image.
5. **Write the limit in the note.** If a check ran against a stand-in, say so. If a measurement was contaminated,
   say so. An honest gap is worth more than a clean-sounding claim.
6. **Prefer the smaller, honest fix.** If a test can only pass against a fake, it is a test of the fake. Revert a
   half-fix and write down the options before leaving the tree red or green for the wrong reason.
7. **Subagent briefs need absolute paths.** A subagent inherits a session directory; two research reports once
   landed in the wrong repository. State the target repository and write scope explicitly, then check.

---

## 5. What happened since C5, and what C6 inherits

The last few days were the answer-quality program on a second, harder corpus. The parts C6 inherits and must not
break:

- The tool loop in services/api/tool_loop.py with search_documents, read_passages and query_rows. A measured run
  made 345 searches, 143 row queries and 65 passage reads across 185 answers.
- The exact-phrase seed: a question with a cue phrase has its carrying passages searched literally and placed in
  the seed before the model answers. Removing it costs a question, and raising the seed without it makes the score
  worse.
- Table evidence at render time and table identity in the index: rows carry their header and caption, and the
  chunk index carries table_id, row_index and headings. The answer path reads those fields, so do not remove them.
- The honesty gate: questions about the company's own attribute are refused when no passage states it. It is part
  of the answer path now, not a switch.
- One answer profile, OI_ANSWER_PROFILE, accuracy or economy. Values live in services/api/answer_profile.py.
- Provider fallback, model attribution in the answer and the audit row, and the rule that a fallback is visible.
- The measurement harness: scripts/run_answer_suite.sh, scripts/suite_hygiene.py, tests/fixtures/data_suite_run.py,
  the corpus-2 question set at docs/eval/corpora/second-corpus/questions.yaml, and the acceptance probes.

C6's tool and model audit will read the same audit chain. Do not introduce a second way of recording a model call
or a tool call.

---

## 6. C6 scope, in practical terms

C6 is the execution core: an agent runtime and an MCP gateway. Read the C6 requirements in
docs/requirements.yaml and FINISH-SCOPE sections 2 and 3 before designing anything. The workspace layer is the
separate `WS` phase described in `docs/design/cloud-workspace.md`; it is not part of C6, but the runtime's tool
interface must be able to host it.

**Agent runtime.** The acceptance line is an agent task with a sub-agent and a checkpoint. The runtime is one per
deployment. It needs a task loop with checkpoints, two modes, a queue, context control, verification, sub-agents
with child rules, the approval gate, and definitions loaded from packs. The C5 workflow engine stays: C6 replaces
the plan, not the engine. Decision 0009 makes the loop a dynamic plan whose iterations are engine steps; the
shorter "the loop is one step" reading does not satisfy AG-LOOP-001 or AG-CHECKPOINT-001. The transaction, approval
gate, event log and job states already exist.

**MCP gateway.** One per deployment. It needs a tool registry, per-connector credentials, gateway policy, estate
ownership and egress gating. Credentials must not land in git or a client-shared document.

**Provider layer.** INF-PROVIDER-001: a manifest can name an OpenAI-compatible provider for a role, the rendered
gateway reaches it, the key lives only in the rendered environment, the in-office profile refuses remote
providers, and a suite run against the alternate provider is measured and recorded. Model choice stays a manifest
value. The current candidates and the admission rule are in docs/reviews/model-candidates-2026-10-22/.

**Audit and security requirements** that travel with C6: tool audit, span audit, checkpoint audit, model audit,
agent security and MCP security. They are the reason the gateway must be the only path to a tool or a model.

**What C6 is not.** It is not a second workflow engine. It is not a place to add model-specific branching. It is
not a reason to replace the answer path. If an agent task needs a tool, it calls the same toolbox and the same
access enforcement.

---

## 7. How to start

1. Read docs/requirements.yaml C6 rows, docs/lab/FINISH-SCOPE.md sections 2, 3 and 4, docs/decisions/0009,
   and docs/design/cloud-workspace.md. The cloud workspace is the target; the office build is a different product.
   Write your own section plan and bring it to Anthony before building.
2. Bring up a working box with docs/runbooks/fresh-install-agent.md once it lands. Do not build on the old corpus
   box; its state is evidence, not a clean starting line.
3. Run the acceptance probes and one corpus-2 smoke run before changing anything, so you know the baseline.
4. Design the runtime around the existing jobs and approval tables, not beside them. The existing seam is
   documented in docs/review-log.md around the C5 entries and the workflow engine.
5. For each requirement, write the verify block as a runnable check first. If you cannot run it, the requirement
   is not ready to build.
6. Measure the answer path only if C6 changes it. If it does, five runs, same corpus, same day, fallback guard
   clean. If it does not, say so and leave the benchmark alone.

---

## 8. The traps, in the order they will bite you

**A fresh host fails in three specific places, and the runbook has all three.** The external ai-net network has
to be created by hand. LiteLLM's Prisma refuses a database the API has already migrated, so the gateway starts
before the API on a new volume. Ingestion loses two startup races, against the API migrations and against the
embeddings model load, and needs one restart. Read the failure table in the fresh-install runbook before you
blame a service.

**The deployment runs code nobody can point at.** The box can be five commits behind while git status is clean
because origin/main is stale. Fetch, count, pull, rebuild, verify. The previous handover has six examples.

**The rendered environment drifts from the manifest.** Changing rendered/litellm.yaml by hand is a measurement
trap. The manifest is the source; render it. The accuracy manifest is tracked; a hand-edited rendered file is not.

**The image carries the migration.** A migration checksum mismatch makes the API crash-loop after a reindex or a
migration edit. Rebuild the API image when migrations change, and never edit an applied migration.

**A silent fallback is a different model.** The gateway answers from Haiku or Nova when Sonnet throttles. The score
can be fine and the measurement wrong. suite_hygiene now refuses a run with fallback calls; use it.

**The quota is account- and model-scoped and published defaults are not granted quotas.** Sonnet 4.6 draws from a
shared daily pool. A full measurement can exhaust a small account. Plan model-dependent acceptance accordingly;
the fallback guard is not optional.

**A mixed index is not a measurement.** Corpus 1 is mixed chunker-v4/v5 after a stopped reindex. Do not compare
against it. Corpus 2 is complete at 24,296 chunks.

**The corpus archive and the question set are separate inputs.** The code archive does not contain the documents or
the eval questions directory. The fresh-install runbook records both, with checksums.

**A subagent can write into the wrong repository.** Absolute paths, explicit write scopes, then check.

**Reporting success on partial work.** Count what moved. If ingestion says 18 of 539, say 18 of 539.

---

## 9. How to be useful here

- Run things. Verify, do not recall.
- Report what you did not do first. The most valuable sentence in a report is "this path has never run".
- Distinguish a decision from evidence. If the owner closed a blocker, record how it closed.
- Put the limit in the note, not only the result.
- Push back with evidence, especially when a requirement is unachievable as written.
- Prefer the smaller, honest fix.
- Research before a big decision, then bring a recommendation.
- Do not narrate confidence. Show commands and output.
- Keep the humour dry.

---

## 10. If you read nothing else

    git fetch
    git log --oneline -20
    read docs/requirements.yaml C6 rows
    read docs/lab/FINISH-SCOPE.md sections 2, 3, 4
    read docs/decisions/0002-agent-topology.md

    bring up a clean box with docs/runbooks/fresh-install-agent.md
    run the acceptance probes
    write your C6 section plan and get it approved

    then build the agent runtime and the MCP gateway on the existing engine,
    with tool and model audit going through the existing audit chain.

The answer path is good and measured. Do not break it while building C6. The next handover should be able to say
the same thing about your work.
