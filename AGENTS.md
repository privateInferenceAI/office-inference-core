# AGENTS.md — office-inference-core

## What this repository is

The single core system for Office Inference, deployed two ways: into a client's cloud account,
or onto hardware in a client's building. Read `docs/architecture.md` before changing anything,
then `docs/service-inventory.md` for the service list and its location tags.

### A step is not done until every requirement it names is verified

The register is the authority, not the step list. A step plan is written from the architecture's
build sequence and it does not necessarily cover every requirement in a phase. Phase 3 reached
"3.14 complete" with seven of its eighteen requirements still open, and every one of them was
discoverable in `docs/requirements.yaml` the whole time.

So, before starting a step and before declaring one finished:

    python3 -c "
    import yaml
    d = yaml.safe_load(open('docs/requirements.yaml'))
    for i in d['items']:
        if i.get('phase') == '<the phase>':
            print(f\"  {i['status']:<9} {i['id']:<17} {i['title']}\")
    "

**When asked to move to the next step, say whether the current one is finished first.** If any
requirement in the phase is still open, say so with the list before moving. The failure this
prevents is not a missed requirement; it is reporting progress against a plan and calling it
progress against the phase.

## Rules that are not negotiable

1. **Nothing client-specific in this repo.** A client's differences live in a manifest under
   `manifests/`, never in code, never in a branch. If a client needs something new, it becomes a
   manifest field or a service, not an edit to a running box.
2. **Application code never asks where it is running.** No "am I in the cloud" branches. Substrate
   differences are resolved through configuration and adapters.
3. **Every service declares a location tag**: `office`, `cloud`, or `either`. That tag is what
   makes "the office build has more of the core locally" a mechanical decision.
4. **Digest-pin every image**, with the version recorded in a comment beside the pin. This exists
   because a previous build pinned an image with no version recorded and nobody could answer the
   first question a security review asks.
5. **Capabilities are sold as packs, not toggles.** Add capability by adding a pack to
   `packs/catalog.yaml`, not by inventing a new setting.
6. **The frontier lane is off by default and every outbound call is logged.** Any change that
   could send client content off the box needs an explicit gate and an audit record.

## What "finished" means

A phase is not finished when the code works. It is finished when someone who did not write it can
run it from the documentation, and that has been checked rather than assumed. The order:

1. **Build it**, one step at a time, with the step written into `docs/runbook.md` first: the goal,
   the commands, the expected output, what it proves, and how it breaks.
2. **Write the operating page** for anything new, in `docs/manual.md`: what it is for, every command
   with a real example and its real output, and how to extend it.
3. **Have it reviewed by agents that did not write it**, at the end of the phase:
   - an **executor**, which runs every command on the live box and reports where reality differs;
   - a **cold reader**, which reads the manual with no access to the code and reports what a new
     operator would get wrong;
   - a **fidelity checker**, when code changed, which reads the claims against the source.
4. **Fix everything they find, then have the fixes checked again.** Cap it at three rounds, and stop
   when a round returns only cosmetics. Require each reviewer to state a verdict, so stopping is a
   decision rather than a feeling.
5. **Close the register items** in `docs/requirements.yaml` with the evidence, at the same time.

Within a phase, review only what changed; the full panel runs once, at the end.

**Before a decision that is expensive to reverse, run the decision loop instead.** Several independent
agents research one angle each, one synthesises, two attack the result — one on evidence, one on client
reality — and one revises. The C2 retrieval architecture was decided that way: thirteen agents, and among
what they caught were a constraint one researcher had carried across from a sibling project, a component
whose CPU cost made the brief's plan unshippable, and a licence that I had treated as a prohibition when
it is a decision.

**Record every round in `docs/review-log.md`**, naming what was covered and the findings that changed
something. A round that found nothing worth changing is the signal that the phase is done.

Two things this process has already caught, out of many: a documented command that reloaded nothing
(`up -d` against a mounted config), and an export job that shipped the wrong day. Both were invisible
to reading and obvious to running.

## How we choose a component

**Ask what a competent production team would deploy for this job, and let that decide. Never let our
own test instance decide.** If the right component needs more machine, the machine is what changes:
the build's hardware is part of the quote, and a component chosen because the development box was small
bakes the development box into the product.

Three qualifications, because the rule stated alone can be misread two ways.

**Professional is not the same as newest or largest.** It means a licence we can sell, a maintainer
with a track record, documentation, a supported serving path, and enough adoption that a client is not
the first to hit its bugs. A leaderboard leader with a research-only licence is the wrong production
choice, and so is a model nothing serves in a container.

**On copyleft, be accurate rather than reflexively strict.** AGPL and GPL permit commercial use and
redistribution. The obligations are to pass on the licence, make the source available and preserve
notices, and AGPL's network clause engages only if we modify the software and let users interact with
the modified version over a network. So copyleft is a decision with a price and a process, not a
prohibition. What rules a component out is a licence that forbids commercial use outright, a licence
whose price we cannot see, or a client's own procurement policy. When a copyleft component is the best
technical answer, say so, name the obligations, and put the decision to Anthony rather than deciding it
by default.

**The client's deployment profile is a real constraint; ours is not.** For an In-Office build the
hardware is what the client buys, so a component whose appetite matches that box is good engineering
rather than a compromise. The distinction to keep: never choose because *our* instance is small, and
always be able to say which of the two you are doing.

**Prefer the choice you can reverse.** The cost of a wrong component is not the component, it is the
migration away from it. For anything whose replacement means redoing work, store the identifier and
the version beside the data, keep the intermediate artifact, and version the store, so changing it is
a background job rather than a re-ingest. The embedding model is the clearest case: changing it means
re-embedding every chunk, so the model id and its dimension belong in the collection's metadata from
the first ingest.

**Write the comparison down before choosing.** For a choice that is expensive to reverse, the step
that installs it starts with a short note in `docs/`: the candidates, their licences, their resource
needs, the serving path, and what being wrong costs. A choice made from memory is a default, not a
decision.

## Conventions

- Work on feature branches. Do not commit to `main` directly. Create the branch **before** the first
  commit, because committing on `main` and moving the commit afterwards has happened twice.
- **Anthony merges pull requests**, with one exception: while an end-of-phase review round is running,
  the agent may merge its own pull requests from that session, because a reviewer executing on the box
  needs the box to be running the merged code. The agent says which ones it merged and why. Nothing
  else is ever pushed to `main`.
- Secrets never enter the repo. Office builds use SOPS with age keys or systemd credentials.
  Cloud builds use Secrets Manager.
- Non-GPU validation on a laptop is limited to schema validation, `docker compose config`, and
  linting. Never bring the stack up on a machine without a GPU.
- Prose in this repo follows Anthony's style guide from the ai-stack repo: plain, direct,
  contractions, no hype adjectives, no em-dash chains.
