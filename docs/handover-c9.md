# Handover: Office Inference Core, C9 demo rehearsal

**Current document, 2026-10-23.** C8 is complete enough to hand over. C9 is not started. The next agent owns the
demo rehearsal and the clean demo-deployment setup. Read this whole document before touching anything.

**State update, 2026-10-23.** The register has 168 items: 148 verified, 12 open, 6 waived, 1 retired and 1
experimental. The 12 open items are OPS-SUPPORT-001, which Anthony deliberately left open by keeping SSH, and the
11 BIZ rows. No C9 row exists in the register; C9 is the demo gate in docs/build-plan.md, not a code phase.

---

## 0. Read this first

You are picking up a working product with a real client promise. The stack installs, ingests corpus 2, answers
with citations, enforces access boundaries, records a hash-chained audit record, pages on five alarms, backs up
and restores, signs releases, runs a 20-concurrent load test at worst p95 7.8 seconds across five runs, and has a
fresh-install acceptance path. The medical golden set and its hard-fail gate are built and tested.

**Three things before anything technical.**

**The product standard is a sentence.** A permission, a redaction, an audit record or a data boundary has to read
as enforced. If a control only exists on paper it is not a control. That sentence has settled every design
argument in this repository.

**The governing constraint is in the owner's words:** nothing the client depends on may live on infrastructure
Office Inference operates after handover. The client owns the account, the quota, the bill and the audit record.

**Verify, do not recall.** Every defect worth finding here was found by running something. If you believe a page,
run it. If you believe a check passes, run it. If you believe a file is committed, ask git.

The demo is the last gate. Do not start it by rehearsing a story on the live box and calling it finished. The
brief says the JSONL artifact handed to the audience has to come from a deployment configured for the demo, not a
scratch box carrying test events. The live box currently carries a lot of test events.

---

## 1. Who Anthony is, and how he works with you

He is the owner and the decision-maker. He is not a passive approver and does not want to be one.

**He works with you as a co-pilot.** He rides along, asks hard questions, and expects pushback with evidence. When
he says something that contradicts your plan, he is usually right; find out why before defending the plan.

**The operating model: owner with a threshold.** You decide the small things. Bring him anything that changes
scope, price or a client-facing promise.

**On big decisions, research first, then bring findings and a recommendation.** He wants the demo built the way a
professional sales engineer would build it: clear scenes, known numbers, a clean deployment, a rehearsed timing.

**He is pushing hard on simplicity and maintainability.** His words: model cost is not the worry; code and stack
bloat are. Do not add a subsystem for one requirement. Do not add a flag without a measured reason and a plan to
remove it.

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

The stack is Docker Compose: FastAPI assistant API, LiteLLM gateway, Qdrant vectors, Docling parser, CPU
embeddings, Postgres, a React client in a separate repository, and the audit, intake and workflow services. The
office build uses local models; the cloud build uses Bedrock.

**State at this handover:**

- C0 through C8 are built. C8 has 148 verified rows. The only open C8 row is OPS-SUPPORT-001, retained by Anthony
  because he wants SSH for now. The 11 BIZ rows need counsel, contracts, insurance, on-call, subprocessors and
  pricing decisions.
- C9 has not started. The build plan says: demo rehearsal, twelve minutes, eight scenes. There is no C9 script,
  no demo deployment and no rehearsal evidence.
- Corpus 2 is complete on the live box: 80 documents, 24,296 chunks. The medical policy adds one document and four
  chunks, so the live box now has 81 documents and 24,300 chunks.
- The live box is on the latest code and images. The audit chain is at 1,550 events and verifies. The full suite is
  1,357 passed, 1 skipped.
- Five alarms publish to a confirmed SNS email subscription. The 20-concurrent load test has a worst p95 of 7.8
  seconds over five runs. Backup and restore are verified with a 48-second state restore and a 15-minute 42-second
  full drill. Signed releases and rollback are verified. The critical image CVEs are patched; high findings remain.
- The client tenant at badj.ai is stopped to save cost. Its Elastic IP and mcp.badj.ai DNS are retained. SES is
  still in the sandbox and only awstest@officeinference.com receives mail. Do not thaw the tenant without asking.
- The fresh-install acceptance ran on a blank Spot box with a 40-document subset and passed all four probes. The
  full 80-document fresh baseline was not reproduced on that small box. The earlier full-corpus acceptance ran on
  a restored scratch deployment. Both limits are documented.

---

## 3. Where everything lives

**Workstation:** /home/anthony/projects/office-inference-core. SSH key
/home/anthony/ai-stack/ai-stack-key.pem.

**AWS credits account 878451098165**, us-east-1. Use the aws-credits MCP tools, assumed role DSHAgentRole, for AWS
work. Do not use the default account for this product.

- Live box: instance i-02aba2feef08f8e7b, m7i.4xlarge, public IP 52.204.204.133, deployment root /opt/oi-core.
  It has corpus 2 fully ingested and is the functional/regression test target. Its audit record contains test
  events, so it is not the clean demo deployment.
- Second build box: instance i-096bebd46308d1673, m7i.4xlarge, Elastic IP 52.204.202.66. It is running the latest
  code and images from /opt/oi-core, but its rendered environment and data are the old development deployment.
  The old restore stack at /opt/oi-restore is stopped and its data is preserved. Do not use it as the clean demo
  deployment.
- Second-corpus box: instance i-05fa6edd5284b7881, m7i.4xlarge, public IP 54.166.99.46. This is a different
  project. Do not touch it.
- badj.ai client tenant: instance i-0b6aa00095ae40e54, stopped. Elastic IP 34.225.62.245 and mcp.badj.ai DNS
  retained. Do not restart it without owner approval.
- The Spot fresh-install instance from 2026-10-23 is terminated.
- Quota: the account's On-Demand vCPU limit is 48. Two m7i.4xlarge boxes are running, so do not launch another
  16-vCPU box without checking the limit.
- Security group sg-09d9bfffbd28014c9 allows SSH from 47.164.45.109/32. The instance profile is
  oi-core-dev-instance. SSH is retained by owner decision; OPS-SUPPORT-001 remains open.

**S3 bucket officeinference-corpus-878451098165** holds corpus assets. The current code archive is
fresh-install/oi-core-current-ed1f540.tar.gz. The corpus archive is
second-corpus/second-corpus-2026-10-22.tar.gz. Checksums are in the fresh-install evidence.

**The documents that matter, in reading order:**

- docs/requirements.yaml: the register. Read the C8 and BIZ rows and the current counts.
- docs/current-deployment.md: the authoritative box, endpoint, caveat and open-row snapshot.
- docs/build-plan.md section 8 and the C9 line: twelve minutes, eight scenes.
- docs/runbooks/fresh-install-agent.md: how a blank install is brought up. Read the findings section added on
  2026-10-23 before starting any new box.
- docs/reviews/fresh-install-2026-10-23/README.md: the last blank-install acceptance and its partial-corpus limit.
- docs/reviews/acceptance-2026-10-23/README.md: the four-probe acceptance on the restored scratch deployment.
- docs/manual.md sections 26.8 to 26.11: current deployment, alarms, cost, medical, fresh-install findings.
- docs/runbook.md C8.6 to C8.8: ingest lock, medical golden, fresh-install findings.
- docs/operator-guide.md section 13e to 13g: ingest lock, cost/alarms, fresh-install pitfalls.
- docs/handover-c6.md: the previous handover. It explains how this project builds and the traps that still apply.

---

## 4. How this project builds a thing

1. **One logical change per pull request.** No sweeping PRs. A PR carries code, tests, docs and an evidence record
   under docs/decisions or docs/reviews.
2. **Measure before you claim.** A single run is a smoke test. Five serial runs on the same corpus, same day, with
   the fallback guard clean, are a result.
3. **Update the manual, runbook and operator guide as you go.** Do not leave documentation for the end. If you add
   a scene, a demo dependency or a clean-deployment step, write it in the manual and the runbook in the same PR.
   The current docs were reviewed together; keep them that way.
4. **Validate configuration.** Manifests go through scripts/validate_manifests.py and
   scripts/check_model_lifecycle.py.
5. **Rebuild the image, not just git pull.** A running container must be built from the code you think it has.
   Check the image id and the source hash.
6. **Write the limit in the note.** If a check ran against a subset, say so. If a demo scene depends on cached data,
   say so.
7. **Subagent briefs need absolute paths.** Two research reports once landed in the wrong repository. State the
   target repository and write scope explicitly.

### PR and merge routine

Anthony granted permission to create, push and merge PRs. Use it, but keep the bar:

- Branch from current main. Never commit directly to main.
- Run the full suite before pushing. The suite was 1,357 passed, 1 skipped at handover.
- Run scripts/check_requirements.py. It fails a malformed register.
- Run git diff --check.
- Write a PR body with what changed, what was verified, and what was not.
- Merge with a merge commit, then switch to main and pull.
- Leave no open PRs at handover unless the next agent explicitly needs one.

The demo work should use the same habit: one PR for the scene script and evidence, another for a deployment or
config change if one is needed.

---

## 5. What C8 leaves and what C9 inherits

**C8 leaves:**

- Five alarms with a confirmed email subscription: core health, ingestion health, backup age, audit chain and
  disk. The publisher runs every five minutes. The audit check runs daily.
- Backup and restore, RTO and RPO. The backup includes Postgres, rendered config, manifests, documents and Qdrant.
- Signed releases and a tested rollback, using KMS alias oi-key and role oi-release-signer.
- Cost reporting from Cost Explorer, including the Claude and Cohere provider lines.
- Critical CVE patches: Postgres gosu, LiteLLM certifi and parser certifi/Pillow. High findings remain.
- The medical golden set: 60 questions validated 60 of 60 against the live chunk index, a hard-fail gate that
  catches fabricated number, wrong document, cross-role leak and unanswerable, and clinical review waived by the
  owner for test use.
- The ingest cycle lock: scheduled and manual ingests never overlap.
- The load fix: blocking work in the API moved to the thread pool and a Bedrock 429 sentence is retried. Five
  runs, worst p95 7.8 seconds.
- The audit chain writer lock. An earlier concurrent run forked the chain; the writer now takes the chain lock
  before insert and the dev chain was repaired. Do not reintroduce synchronous chain writes outside that lock.

**C9 inherits a good answer path and a lot of test events.** The live box is the functional target, not the clean
demo target. The brief's demo flow is: ask a question, show the cited page, ask something the role may not read
and show the denial in the audit log, hand over real JSONL, tamper with a copy in front of the audience, and show
verification fail. The demo is twelve minutes, eight scenes.

The previous handover's answer-quality rules still apply: do not change the answer path for the demo, do not use a
silent fallback, and do not quote a measurement taken on a different model.

---

## 6. C9 scope, in practical terms

C9 is a rehearsal and a demonstration, not a new product phase. The acceptance for C9 is that a person can run the
eight scenes in twelve minutes on a deployment configured for the demo, with the artifacts and numbers in hand.

The next agent should:

1. Write the eight-scene script with timings and exact commands or UI actions, plus the expected output of each
   scene.
2. Choose the demo deployment. The brief says it must be a deployment configured for the demo, not a scratch box
   carrying test events. The live box is full of test events. A clean demo deployment is likely a new box, or a
   freshly reset deployment, with its own audit chain and export.
3. Run the four acceptance probes and one known answer before rehearsing, so the baseline is known.
4. Rehearse the eight scenes end to end and record the elapsed time. If it exceeds twelve minutes, cut, do not
   speed up by skipping an audit proof.
5. Preserve the export, the tampered copy and the verification failure as evidence under docs/reviews/c9/.
6. Put the scene script in the manual or a demo runbook, and put the rehearsal timings and evidence in the review
   record.
7. Update the operator guide with the exact pre-demo checks: health, alarms, audit chain, model, corpus, tenant
   state and email.

Do not start the rehearsal by changing the answer path. C9 is a demonstration of what exists, not a phase for new
features. If a scene needs a feature that does not exist, write down the gap and bring it to Anthony before
building it.

---

## 7. How to start

1. Read the C6 handover's sections 0 to 4 and this document.
2. Read docs/build-plan.md C9 and the demo paragraph in docs/build-brief.md.
3. Read docs/current-deployment.md and the fresh-install and acceptance evidence.
4. Check the boxes. Confirm i-02 is healthy, i-096 is current build with old config, i-05fa is untouched and the
   badj tenant is stopped.
5. Decide the clean demo deployment. Write the plan and bring it to Anthony before creating a box. A new box needs
   a cost decision and a quota check.
6. Bring up the deployment with the fresh-install runbook. Remember the startup order: LiteLLM before the API, and
   ingestion after the database, parser and embeddings are healthy.
7. Run the four acceptance probes and a known cited-answer check.
8. Write the eight-scene script and get it approved.
9. Rehearse, record the timings, and commit the evidence and docs in a PR.

---

## 8. The traps, in the order they will bite you

**The live box is not clean.** It has 1,550 audit events and many test exports. A demo JSONL from it is not the
artifact the brief asks for. Use a clean deployment or a deliberately reset demo deployment.

**Do not touch the second-corpus box.** i-05fa6edd5284b7881 is a different project owned by another line of work.

**The badj tenant is stopped.** Its Elastic IP and DNS are retained. Starting it without approval creates cost and
may expose port 22, which is currently open to 0.0.0.0/0. Do not thaw it for a demo unless Anthony asks.

**The fresh install has four known startup defects.** The deployment id is client.deployment_id. Ingestion needs
postgres, parser and embeddings health. LiteLLM must start before the API on a blank database. The client app needs
the separate /opt/oic-ui archive.

**The full fresh baseline is not proven.** The last blank install used a 40-document subset. Do not claim
80 documents and 24,296 chunks on a fresh box unless you ran it.

**The audit chain has a writer lock for a reason.** Concurrent inserts outside insert_event can fork the chain.
The repair was a development action; production repair would need a disclosed incident note.

**The tenant gateway has no database route.** Refusals are now recorded by the API as denied tool_call rows. The
live tenant 403 path has not been re-exercised since the tenant was stopped.

**SSH is intentionally retained.** OPS-SUPPORT-001 stays open. The tenant security group still has SSH open to the
world while stopped. Restrict it before any thaw.

**SES is in the sandbox.** Only awstest@officeinference.com receives mail. The tenant is stopped anyway.

**High CVE findings remain.** The criticals are patched. Do not say the images are fully clean.

**The prompt cost is dominated by repeated context.** A long autonomous session can spend most of its tokens
re-reading history. Keep a step budget and return a checkpoint.

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
- Build the manual, runbook and operator guide as you go. A feature that works but is undocumented is not done.

---

## 10. If you read nothing else

    git fetch
    git log --oneline -20
    read docs/current-deployment.md
    read docs/build-plan.md C9
    read docs/reviews/fresh-install-2026-10-23/README.md
    read docs/reviews/acceptance-2026-10-23/README.md

    confirm the box state
    decide the clean demo deployment
    write the eight-scene plan and get it approved

    then rehearse the twelve-minute demo, preserve the JSONL and the tamper-failure evidence,
    update the manual and runbook, and merge the PR.

The answer path is good and measured. The operations are built. Do not break either while building the demo. The
next handover should be able to say the same thing about your work.
