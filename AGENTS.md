# AGENTS.md — office-inference-core

## What this repository is

The single core system for Office Inference, deployed two ways: into a client's cloud account,
or onto hardware in a client's building. Read `docs/architecture.md` before changing anything,
then `docs/service-inventory.md` for the service list and its location tags.

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

Two things this process has already caught, out of many: a documented command that reloaded nothing
(`up -d` against a mounted config), and an export job that shipped the wrong day. Both were invisible
to reading and obvious to running.

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
