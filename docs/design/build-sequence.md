# Build sequence

Eleven phases. Each one ends with something you can see working, and each is small enough to finish
in a sitting or two. You type; I write the command sheets and read the output you paste back.

The rule from the friend's build applies here: **a phase is not finished until its verification
step passes.** Detail is written for the phase we are on, plus the next one. Later phases stay as
outlines so they do not go stale.

## The phases

| # | Phase | Ends with | Needs a GPU |
|---|---|---|---|
| 0 | Reference machine | Host ready, repo validated, manifest checks pass | No |
| 1 | Data and gateway | A model call through the gateway, with an audit row written | No |
| 2 | Audit sink | Every call and action recorded, append-only, exportable | No |
| 3 | Documents | A question answered with a page-level citation, and a refusal that is logged | Yes, light |
| 4 | Assistant API | Every capability reachable over one API, with no UI | No |
| 5 | Client app | A non-technical person can ask, approve, and review | No |
| 6 | Workflows and one connector | Invoice intake end to end, with an approval gate | No |
| 7 | Agent runtime and MCP gateway | The gather-reason-draft half of the flagship demo | No |
| 8 | Frontier lane | A test proving nothing leaves when the lane is off, and only permitted classes leave when it is on | No |
| 9 | Office appliance | Backup, restore drill, signed update with rollback, drift report | No |
| 10 | Voice | Talk to it, with the transcript audited | Yes |
| 11 | Flagship demo | The denial appeal agent, end to end | Yes |

**Cloud profile** is a parallel track: the same services, Terraform instead of an installer. It
starts after phase 4, when the assistant API is stable enough to deploy.

---

## Phase 0 — Reference machine

**Goal.** A clean Linux host with Docker, the network, the layout, and this repo, so every later
phase has somewhere to land. No GPU needed, so it can be the office box, a spare machine, or a
small cloud instance.

**Assumed host:** Ubuntu 24.04, sudo access, 4 vCPU and 16 GB RAM minimum for the non-GPU phases.

**Step 0.1 — System update and baseline**

    sudo apt update && sudo apt -y upgrade
    sudo apt -y install curl git python3 python3-pip ufw

Expected: upgrades finish without error. Reboot if the kernel changed.

**Step 0.2 — Docker**

    curl -fsSL https://get.docker.com | sudo sh
    sudo usermod -aG docker "$USER"
    newgrp docker
    docker run --rm hello-world

Expected: the hello-world container prints its message. If permission is denied, the group change
has not taken effect; log out and back in.

**Step 0.3 — The private network**

    docker network create ai-net
    docker network ls | grep ai-net

Expected: the network is listed. This is the only network the services share, and it is why nothing
needs to publish a port except ingress.

**Step 0.4 — Firewall**

    sudo ufw default deny incoming
    sudo ufw default allow outgoing
    sudo ufw allow OpenSSH
    sudo ufw enable
    sudo ufw status verbose

Expected: incoming denied, SSH allowed. Nothing else is reachable from outside the host, which is
the first security boundary in the design.

**Step 0.5 — Layout**

    sudo mkdir -p /opt/oi-core && sudo chown "$USER":"$USER" /opt/oi-core
    git clone https://github.com/privateInferenceAI/office-inference-core.git /opt/oi-core
    cd /opt/oi-core && ls

Expected: the repo contents. All state will live under /opt/oi-core, matching the convention from
the earlier build so backups and restores have one place to look.

**Step 0.6 — Validate the manifests**

    pip install pyyaml --quiet
    python3 scripts/validate_manifests.py

Expected:

    ok   example-compliant-cloud.yaml  (profile=cloud, packs=[...])
    ok   example-hybrid.yaml  (profile=hybrid, packs=[...])
    ok   example-in-office.yaml  (profile=office, packs=[...])
    ok   example-standard-cloud.yaml  (profile=cloud, packs=[...])

    4/4 manifests valid; 8 packs known

**What just happened.** You built the floor: a hardened host, a private container network, one
directory that will hold all state, and a validated description of what a client gets. The
validator is not decoration. It is the thing that will refuse to install a configuration that
breaks a promise, such as turning the frontier lane on in a build that has no cloud leg.

**Verification for the phase.** All four manifests validate, and this returns nothing:

    docker ps -a --format '{{.Names}}' | grep -E 'postgres|litellm' || echo clean

---

## Phase 1 — Data and gateway

**Goal.** Postgres and the gateway running, a manifest rendered into configuration, and one model
call that leaves a row in the database.

**Step 1.1 — Render the manifest into config.** Write the renderer first, so configuration is
never hand-edited.

**Step 1.2 — Postgres.** Start it on ai-net with no published port, create the application
database, and apply the first migration.

**Step 1.3 — Gateway.** LiteLLM pinned by digest **with the version recorded in a comment**, which
is the lesson from the advisory. Point one model role at a local CPU model to prove the path
without a GPU.

**Step 1.4 — First call, first audit row.** Ask a question through the gateway with a virtual key,
then read the row back out of Postgres.

**Verification for the phase.** A model answer comes back, and `select count(*) from audit_events`
returns at least one, with the key alias, the model, and the token count recorded.
