# Handover: finishing C2

Written to start a fresh session with no access to the conversation that produced it. Read this first, then
`AGENTS.md`, then `docs/reviews/c2-round-1.md`.

## The thing that went wrong, so it does not happen again

`AGENTS.md` says what finished means. The order is not a suggestion and it is not a summary of what usually
happens:

1. **Build it**, one step at a time, with the step written into `docs/runbook.md` first.
2. **Write the operating page** for anything new, in `docs/manual.md`.
3. **Have it reviewed by agents that did not write it**, at the end of the phase.
4. **Fix everything they find, then have the fixes checked again.** Cap it at three rounds, and stop when a round
   returns only cosmetics. Require each reviewer to state a verdict.
5. **Close the register items** at the same time.

**C2 did 1 and then jumped to 5.** All 18 requirements were marked verified while step 2 had not happened: there
was no operating page for any of the six things C2 built. The panel could not run because there was nothing to
read. When the pages were written and the panel finally ran, it found **four blocking defects and seven blocking
documentation gaps**, and three of the four defects were behind commands the manual tells a reader to run.

**The register reading 18 of 18 verified was true about the code and false about the phase.** Step 5 is tied to
steps 1 to 4 for exactly this reason. Do not close a register item until its operating page exists and the panel
has passed it.

**The specific failure inside the failure, which is the one to watch for.** Every acceptance probe passed while
the documented commands were broken:

| The probe did | The reader was told to do |
|---|---|
| `policy.enforce(dry_run=True)`, direct | `retention --enforce documents --dry-run` |
| ran from the mounted checkout | `oi run --rm retention ...`, from the image |
| passed a `Passage` carrying `parse_score` | whatever the retriever actually returns |

**The probes tested the modules. The documentation told readers to use the commands.** Anything a fresh session
writes must be run through the interface the manual names, from the image the manual names, before it counts as
verified.

## Where C2 actually stands

**Requirements: 18 of 18 verified against the deployed store.** That part is real and was measured. What is not
done is step 4, so the phase is not finished.

**Round 1 is complete.** Three agents, all of which returned **no** on "could a competent stranger run this from
the documentation alone". Their findings are consolidated in `docs/reviews/c2-round-1.md`, numbered 1 to 42.

**Fixed during round 1** (the register and the runbook carry the detail):

- The confidence gate could never fire. `citations.py` reads `parse_score`; `Passage.from_payload` was not
  copying it, so the gate skipped every passage on the real path while its unit tests passed.
- `retention --dry-run` was never defined and `--enforce` dereferenced it, so the documented rehearsal was
  impossible and the real run died with `AttributeError`.
- `services/retention/Dockerfile` did not copy `policy.py`, so `--set-retention` and `--enforce` died with
  `ModuleNotFoundError` on a fresh build.
- A held purge's refusal wrote no record at all, while the code comment and the manual both said it did.
- The vector store's per-store count was a hard-coded 1. A purge of a nonexistent document reported one object
  removed against a collection that never changed.
- `docs/runbook.md` carried committed merge-conflict markers.
- Four register items had duplicate `note` keys, so YAML silently discarded their verification text while the
  status read verified.
- The `/v1/capabilities` claim in the manual, the runbook and `ingest.py`'s own error text; the parser serves no
  such endpoint.
- The destruction page said stored payloads are cleared when the code counts and keeps them.
- `RET-PARSE-002`'s recorded `is_ocr` gap was not real. The column would be redundant.
- The manual's front matter, contents and section 6 still said C2 had not happened.

**Open and blocking. These are the round 2 work.** All from `docs/reviews/c2-round-1.md`:

- **No host path for the documents root.** 13a says the worker walks "the documents directory" and never
  resolves `/documents`. Section 23 does not list it. A reader cannot put a file where the worker will find it.
- **`eval` and `retention` are invoked and never introduced.** Neither appears in section 3, section 9's expected
  `ps`, section 20, section 22 or section 23.
- **`staff` and `executive` are defined nowhere.** Used throughout 13b to 13e and in every measurement table,
  with no key, no model, no budget and no stated location for the role map. The glossary describes a different
  set of four roles, so the manual uses one word for two things.
- **No procedure for asking a question against the client's documents.** There is no request shape and no entry
  point. The only paths are fixture probes.
- **The C2 manifest fields are outside the field table that claims to be authoritative.** `access.denial` and
  `access.egress.frontier_classes` are absent from section 16.
- **No procedure for adding a migration**, though section 13 tells the reader to write one.
- **No corpus onboarding.** Section 19's six steps never mention documents, folders, formats, or that the folder
  name is the access label.

**Open and not blocking:** five stale sample outputs (13b eval, 13c figures, 13e content mode, 13d denial mode,
13a cycle log); nine undocumented knobs; the documented hold sequence leaves a deployment-wide hold in force;
`--supersede` and `--distinct` have no existence check; two licence assertions cite nothing.

## The decision that is not a fix

`lanes.frontier.data_classes_allowed` and `access.egress.frontier_classes` are two manifest fields with the
**same enum and the same meaning**. The gate reads the second. Section 16 said the first is read by nothing at
call time, which C2 made true in reverse.

**This is an architecture decision and it is Anthony's.** Do not resolve it by deleting a row. It is documented
as an open overlap in `docs/manual.md` section 16 and it was raised with him. Round 2 should leave it as it
stands unless he answers.

## Round 2, and the prompts that produced round 1

Fix the seven blocking documentation gaps, the five stale outputs and the two confusing items. Then re-run a
smaller panel: **an executor for anything behavioural and a fidelity checker for anything factual.** A cold
reader is not needed in round 2 because the gaps it found are already listed.

**Fences.** These are not negotiable and they were kept in round 1:

- No purge without `--dry-run` unless a probe explicitly requires it and the corpus is disposable.
- Nothing deleted from S3. No bucket touched at all.
- The instance is not stopped.
- Every state change is disclosed and reverted, or left deliberately with a note saying why.
- **Only one agent may change state on the box at a time.** Round 1 ran the executor alone for this reason and
  the other two did not touch the box.

**The executor prompt.** It must run every command the C2 pages tell a reader to run, on the live box, and
compare the actual output to the promised output. It must not trust the sample outputs, because those were
written by whoever wrote the code. It should report where reality differs in behaviour rather than wording. It
also runs the four adversarial probes:

1. Retrieve what you should not, as a role whose labels exclude it. One obvious path and one indirect one.
2. Cite a page that does not exist. Check whether the citation points at the page or merely the document.
3. Answer from a superseded document. Check which version is cited and how it is marked.
4. Purge against a hold. Confirm it refuses, with the refusal recorded.

**The fidelity checker prompt.** It reads every claim against the source and reports wrong claims, undocumented
options, over-claims, and anything asserted as measured that is inferred. **Licence claims get special
attention**: every one must cite the file or page it was read from, with a URL. A licence recalled from memory
has been wrong twice on this project.

**Both must return findings in this shape, one block per issue**, and then a one-line verdict:

```
ISSUE: <one line>
WHERE: <document and section>
COMMAND: <what was run>
EXPECTED: <what the documentation promised>
ACTUAL: <what happened, verbatim enough to check>
SEVERITY: blocking / wrong / confusing / nitpick
```

**A vague "looks good" is worthless and so is inventing problems.** If a reviewer finds nothing that changes
anything, it must say that plainly, because that is the signal to stop.

## Then, in order

1. **The review-log row.** Append to `docs/review-log.md` under "The deliverable loop": what was covered, how many
   agents, and the findings that changed something. One row per round. The log already explains what does not go
   in it.
2. **The register.** Clear the C1-and-C2 line. If round 2 passes, the 18 verified items stand. If it does not, the
   items whose documentation is still broken go back to open with a note saying why.
3. **The over-engineering review.** Anthony asked for one, separately: an agent that reads C2 and asks whether
   what was built is right for the purpose or over-engineered for it. This is a judgement call about scope, not a
   correctness check, and it has not been run.
4. **The end-to-end test.** `docs/runbook.md` step 3.15. **It has never been run and it is not to be started
   until round 2 passes.** The plan: a fresh deployment from zero (clone, `validate_manifests.py`,
   `render_manifest.py`, `compose up`, `audit migrate`), then the graded question set, then every step's
   acceptance probe in sequence on that fresh box.

**The end-to-end run is where the retention image bug would have been caught for free.** Every migration so far
ran incrementally onto a database that already had tables, and every probe ran against a box that accumulated
state across eighteen steps. A build from zero is the only thing that tests the images as a reader receives
them.

## The environment

**The repo.** `github.com/privateInferenceAI/office-inference-core`, private. Local clone at
`~/projects/office-inference-core`. Feature branch and a green PR per change; **check whether a PR is still open
before pushing to its branch**; never commit to `main`.

**The box.** `ssh -i ~/ai-stack/ai-stack-key.pem ubuntu@52.204.202.66`, an **m7i.4xlarge** in the credits
account `878451098165` — instance `i-096bebd46308d1673`, us-east-1b, 16 vCPU / 61 GiB, 200 GB gp3, IAM role
`oi-core-dev-instance`. Repo at `/opt/oi-core`. The helper `/tmp/oibox '<command>'` runs a shell command there.

This page said `t3.large` until it was corrected against the instance metadata. **That was the retired box:**
`i-0887564c9fe06b0b5` was terminated on 2026-10-10 and `docs/build-brief.md` records it. The difference matters
when reading the older steps in `docs/runbook.md`, where phases 0 to 2 were first run on the t3.large and say
so. Everything from that point runs on the m7i, and `nproc` plus `free -g` on the box settle it rather than any
document.

**All testing is on the cloud build.** The office build is a documented target and not a test environment. No
GPU anywhere in this phase.

**The tools**, invoked as `oi run --rm <tool> <args>` from `/opt/oi-core`, where `oi` is the shorthand for the
compose command documented in manual section 10:

| Tool | Image | Notes |
|---|---|---|
| `audit` | `oi-core-audit:latest` | migrations, records, export, anchor, verify |
| `retention` | `oi-core/retention:local` | purge, holds, retention policy |
| `eval` | `oi-core/eval:local` | the graded question set; carries botocore |
| `keys` | from compose | mint, probe and revoke role keys |

Probes under `tests/fixtures/` are run with `--entrypoint python <image> /w/tests/fixtures/<name>.py`. Some need
a specific image: the egress probe needs `audit` because it imports psycopg 3, and the rerank probe needs `eval`
because it imports botocore. **Reading `rendered/env` from inside a container needs
`--user "$(id -u):$(id -g)"`**, or it fails with `PermissionError`.

**Local checks, all fast:** `python3 -m pytest tests/ -q`, `python3 scripts/check_requirements.py`,
`python3 scripts/validate_manifests.py`, `python3 fixtures/corpus/validate_corpus.py`. The suite is at **387
passing**.

**Measured ground truth on the deployment**, so a fresh session does not have to rediscover it:
`CONTENT_MODE_INTERNAL=redacted`, `CONTENT_MODE_PUBLIC=full`, `CONTENT_DEFAULT=hash`, `FRONTIER_CLASSES=public,internal`,
`EGRESS_REDACTION=on`, `ACCESS_DENIAL_MODE=counted`, retention policy documents 365 days. The corpus is 1131
vector points and 12 documents. The contract in force is `model_id BAAI/bge-m3`, `pooling cls`, `dimension 1024`,
`chunker_version chunker-v1`.

**The reranker needs a paid AWS Marketplace subscription**, 0.002 dollars per search unit, which is one query
against up to 100 documents. Anthony completed it. It is an agreement and not a permission: no IAM change
substitutes for it, and a second deployment will be silently missing a reranker without one.

## Branches and PRs open at handover

| PR | Branch | State |
|---|---|---|
| 141 | `fix/c2-panel-round-1` | open: the section 16 correction, front matter, contents |
| 142 | `fix/c2-fidelity-blocking` | open: the four blocking code defects and the doc claims |

`docs/reviews/c2-round-1.md` exists on both. **Check whether each is still open before pushing to it**; if a
branch is already merged, the commit lands on whatever is checked out, which has happened twice on this project.
