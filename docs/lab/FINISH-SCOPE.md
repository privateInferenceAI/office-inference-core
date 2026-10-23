# Scope: what "finish the stack" means for the lab

**Status:** written 2026-10-20. Applies to the lab instance after `BOOTSTRAP.md` verification. The source of truth
for any requirement is `docs/requirements.yaml`; this document maps it to a build plan. If they disagree, the
register wins and this document is corrected.

## Where the reference is at the pin

At the time this was written the register held **158 items: 86 verified, 64 open, 6 waived, 1 retired and 1
experimental.** The open engineering work is concentrated in five phases, including the cloud workspace phase
`WS` added 2026-10-22. The office build is a separate product; its boundary does not apply to the cloud build.

| Phase | Open items | What the phase is |
|---|---|---|
| C5 | 8 | Workflow approvals: state, decision, resume, honest identity, irreversibility, re-attestation, audit coverage, role security |
| C6 | 26 open plus one experimental decision | Agent runtime, MCP gateway, provider layer, tool and model audit |
| C7 | 5 | Security: authenticated sessions, least-privilege role, Secrets Manager, TLS at the edge, Object Lock disclosure |
| C8 | 13 | Operations and quality: hash coverage, alarms, backup restore drill, RTO, signed updates, CVE scanning, drift, SSM support, cost reporting, incident runbook, golden questions, acceptance test, load test |
| WS | 10 | Cloud Workspace: sandbox, approved web egress, artifacts, UI, first connector, credentials, limits, audit, acceptance, profile gate |

Everything earlier is verified: retrieval, the answer path with the tool loop and row query, the honesty gate, the
fallback chain, workflows and intake, the audit chain, and the assistant API. The search work in flight at the time
of writing (the retrieval-gap plan: exact-phrase needles first, the dataset sidecar for rows second) must be merged
and measured before the lab is pinned; the lab does not build on a moving retrieval layer.

The six `NEVER` items are **waived** in the register. They are not hard constraints for the lab. Do not turn a
waiver into a product decision without the owner: pricing, multi-tenancy and tenant isolation are owner calls.

## Definition of finished

The lab is finished when all of the following are true, each with evidence:

1. **Every open C5-C8 requirement is verified.** For each item, the `verify` block in the register is satisfied by a
   test, probe or measurement, the evidence is committed under `docs/reviews/lab-<section>/`, and the register row
   is updated with the evidence path. A requirement is not verified because the code exists; it is verified because
   its check ran and its output is recorded.
2. **The cloud deployment installs into a clean account.** The Terraform in `deploy/cloud` (currently a 44-line
   placeholder with the ECS/EKS/EC2-compose question open) creates a working deployment from a manifest, in a
   client-account-shaped environment, and an acceptance test passes on the result. The install, upgrade and
   rollback paths are documented and exercised.
3. **The client app covers the product.** In the separate `oic-ui` repository: Ask exists; Do, Review, Activity and
   Settings exist per `docs/design/frontend.md`; screens are a function of the manifest and the purchased packs;
   the data-lane indicator and approval queue work. Pack gating is tested.
4. **The agent runtime and MCP gateway exist** as one runtime per deployment and one gateway per deployment, with
   the AG-* and MCP-* requirements verified: loop and checkpoints, two modes, queue, context control, verification,
   sub-agents with the child rules, approval gate, definitions from packs, tool registry, per-connector
   credentials, gateway policy, estate ownership, and egress gating.
5. **Approvals work end to end.** A job stops at `awaiting_approval`, survives a restart, resumes or stops on the
   decision, records who decided honestly (no claimed verified identity that does not exist), refuses a caller who
   supplies their own role, and nothing irreversible happens before the decision.
6. **The provider layer works.** A manifest can name an OpenAI-compatible provider for a role; the rendered gateway
   reaches it; the key lives only in the rendered environment; the in-office profile refuses it; and a suite run
   against the alternate provider succeeds and is measured.
7. **Security (C7) holds.** No token and a forged role are refused and audited; the deployment role is revocable
   and cannot read documents; secrets come from Secrets Manager with a rotation procedure; TLS terminates at the
   edge with nothing else public; the Object Lock retention decision is written down and disclosed.
8. **Operations (C8) hold.** Five alarms and nothing chatty; a backup restores in a drill; RTO is measured; a
   release is signed and a bad one rolls back; CVE scanning runs on a schedule; a deployment reports manifest and
   image digests; support is SSM-only, consented and logged; cost reporting reads the Marketplace line; an incident
   runbook has notification clocks.
9. **Quality gates exist.** Sixty validated golden questions for each of the two approved verticals (medical billing
   denial appeals, accounts payable invoice intake), with hard-fail categories and a suite that blocks a release when
   one triggers; every new deployment runs an acceptance test; the system is load tested with ingestion running.
10. **The offering configurations are real.** Manifests exist and validate for the four offerings: Standard Cloud,
    Compliant Cloud, Standard Cloud Hosted, and In-Office. The cloud offerings are deployable and measured. The
    in-office/hybrid profile is implemented at the configuration, validator and code-path level; GPU-dependent
    hardware verification is out of scope on the lab instance (no GPU, no GPU quota in the account).
11. **The comparison is written.** A final report measures the lab on the same instrument as the reference (same
    corpora, five runs, same-day control), lists every architecture decision that diverged and why, states latency
    and cost, and recommends what the reference stack should adopt or avoid.

## Suggested section plan

The agent proposes its own plan in section 0 and the owner approves it. This is the grouping the dependencies
suggest; sections may be split further but not merged across unrelated requirements.

| Section | Content | Requirements / deliverables |
|---|---|---|
| 0 | Plan, vertical corpus plans, budget, gate calendar | none; verticals already chosen |
| 1 | Approvals | C5: the 8 APR/AUD/SEC items; client-app approval surface works with them |
| 2 | Agent runtime | AG-*: 14 items; checkpoint, resume, modes, queue, context, verify, children, gate, definitions, seam |
| 3 | MCP gateway | MCP-*: 5 items; registry, credentials, policy, estate ownership, egress; plus AUD-TOOL, AUD-SPAN, AUD-CHECKPOINT, AUD-MODEL, SEC-AGENT, SEC-MCP |
| 4 | Provider layer | INF-PROVIDER-001; manifests, renderer, validator, residency gate, measurement |
| 5 | Client app | oic-ui: Do, Review, Activity, Settings, pack gating, lane indicator; tests |
| 6 | Cloud deployment | Terraform path; clean-account install; acceptance test; upgrade and rollback; onboarding checklist |
| 7 | Security | C7: SEC-AUTH, SEC-LEAST, SEC-SECRET, SEC-TLS, AUD-LOCK |
| 8 | Operations | C8 ops: AUD-HASH, OPS-ALARM, OPS-BACKUP, OPS-RTO, OPS-UPDATE, OPS-CVE, OPS-DRIFT, OPS-SUPPORT, OPS-COST, OPS-INCIDENT |
| 9 | Quality and verticals | QLT-GOLDEN, QLT-ACCEPT, QLT-LOAD; the two approved verticals, each with corpus, 60 validated golden questions and a hard-fail gate |
| 10 | Packs, offerings, docs | four offering manifests; agent definitions per pack; manual/runbook/install/onboarding/incident docs |
| 11 | Comparison report | rubric table, divergences, recommendations, what to adopt or avoid |

Dependencies to respect: 4 (provider) can run beside 2 or 3; 5 (app) needs 2 and 3 for Do/Review; 6 (deployment)
needs 7 and 8 to be meaningful for a client account; 9 depends on the two verticals below; 11 is last and needs
every other gate accepted.

## The two verticals (owner decision, 2026-10-20)

The owner chose the pair. The agent does not substitute others; if one turns out to be undeliverable, it stops at a
gate and says why.

| | Vertical 1 | Vertical 2 |
|---|---|---|
| Name | medical billing denial appeals | accounts payable invoice intake |
| Customer | small medical practice or billing service | any small business with an AP inbox |
| Offering and pack | Compliant Cloud candidate, Agentic Desk, approvals | Standard Cloud, Workflow Automation |
| What it proves | long-document research, deadline arithmetic, cited drafting, approval before sending | extraction, purchase-order matching, dedupe, approval before payment, audit chain |
| Corpus | public CMS/Medicare appeals and coverage policy text plus labelled synthetic denial letters, EOBs, practice policies and patient records; no real PHI | labelled synthetic invoices, purchase orders, vendor master data and AP policy, plus public vendor contract exhibits |
| Golden set | 60 validated questions: coverage facts, which policy governs, deadlines, required documentation, refusals, executive boundary, cross-document reconciliation, table extraction | 60 validated questions: amounts and dates, PO and contract matching, policy facts, duplicate detection, due dates, refusals, executive boundary, reconciliation |
| Hard-fail | invented coverage or patient facts; missed deadline; send before approval; policy cited for something it does not state; absence answered from generic law; PHI in the wrong lane | duplicate payment; payment before approval; wrong vendor or amount; ignored sender mismatch; access-boundary breach; workflow step or approval missing from the chain |
| Demo | denial intake, evidence gathering, draft, approval stop, resume, audit chain | clean invoice, duplicate arrival, sender mismatch, approval stop, audit chain |

Each vertical delivers a pack/manifest configuration, committed corpus provenance (the corpus itself stays out of
git), the 60-question YAML with its validator report, the hard-fail map and release gate, five runs on the vertical
set if the answer path changed, a demo script, and a client-facing note on what the vertical does and does not claim.
The healthcare BAA and contract questions stay owner-owned; for this synthetic corpus they are not a blocker.

The two verticals can run in parallel after approvals and the runtime are in place, with one teammate each and
disjoint write scopes (separate corpus and question directories, separate evidence folders).

## Owner-owned items

These are in the register under BIZ and need the owner, not the agent. The agent supplies the engineering evidence
and a draft where useful, and records the dependency at the gate:

`BIZ-LICENSE-001`, `BIZ-BAA-001`, `BIZ-LEGAL-001`, `BIZ-CONTRACT-001`, `BIZ-ONBOARD-001` (engineering can draft
and test the checklist), `BIZ-OFFBOARD-001`, `BIZ-INDEPENDENT-001` (the agent builds the test that cuts every cord
to Office Inference; the owner decides what it means commercially), `BIZ-SUBPROC-001`, `BIZ-INSURANCE-001`,
`BIZ-PRICE-001`, `BIZ-ONCALL-001`.

## Out of scope for the lab

- Real client data. The lab uses the synthetic corpora, including any corpus built for vertical packs, and nothing
  confidential.
- GPU-dependent hardware work: voice (ASR/TTS), local model serving, the in-office appliance on its target hardware.
  The configuration, manifests, validator rules and code paths for those builds are in scope; running them on real
  hardware is not, because the lab instance has no GPU and the account has no GPU quota.
- Legal, contractual, insurance, pricing and on-call decisions (owner-owned above).
- Merging lab work into the reference `main`. The lab targets its own long-lived branch; adoption into the reference
  is a separate owner decision, one PR at a time, and only after the comparison report is accepted.
- Multi-tenancy, usage-based pricing and a managed knowledge base as product choices. They are waived requirements,
  not approvals to build them.

## Measurement and evidence rules for the lab

- Same instrument as the reference: `docs/eval/data-questions.yaml` with the five probes, `scripts/run_answer_suite.sh`
  five runs minimum, `scripts/acceptance_panel.py` for latency and cost. Same-day control for any answer-path
  comparison. Raw result files committed beside the report.
- New capability gets its own acceptance set: the golden questions for a vertical, validated mechanically against
  the index (the second-corpus question file and its validator are the pattern).
- Every section ends with a gate report in the `SECTION-TEMPLATE.md` format: what a person can do, evidence, tests,
  decisions, cost, risks, next, and what the owner must decide.
- Every register row updated only with its evidence path. A requirement without a run is still open.
- Two counter-findings are success: a section that fails its gate and is redesigned with the reason recorded, and a
  decision that turns out worse than the reference's and is reported. The point of the lab is the comparison.

## What the comparison report must contain

- The rubric table from `CHARTER.md`.
- The same five-run acceptance numbers as the reference, on the same corpora, with the same-day control.
- The wider-set and second-corpus results if the lab can run them.
- The two vertical results, their hard-fail categories, and whether the release gate fires correctly on a seeded failure.
- Latency p50/p95 and cost per answer from the gateway records.
- A divergence list: every place the lab chose differently, with the decision record link and the measured result.
- A short "what the reference should adopt" list and a "what not to do" list.
- The state of every open requirement at the end, including the ones not finished, with the reason.
