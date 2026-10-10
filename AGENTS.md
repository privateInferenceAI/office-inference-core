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

## Conventions

- Work on feature branches. Do not commit to `main` directly.
- Secrets never enter the repo. Office builds use SOPS with age keys or systemd credentials.
  Cloud builds use Secrets Manager.
- Non-GPU validation on a laptop is limited to schema validation, `docker compose config`, and
  linting. Never bring the stack up on a machine without a GPU.
- Prose in this repo follows Anthony's style guide from the ai-stack repo: plain, direct,
  contractions, no hype adjectives, no em-dash chains.
