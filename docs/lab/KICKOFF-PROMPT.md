# Kick-off prompt: get the lab instance to the baseline

Paste this to the agent that will provision the second instance. It is scoped to setup only. Building the product
comes after the owner accepts the plan, and is governed by `CHARTER.md`.

---

You are setting up a **separate lab instance** for a parallel implementation of an existing, working RAG stack. Your
job in this task is only to get that instance to the same starting line as the working deployment and prove it
with evidence. Do not design or build new product capability in this task.

Read these first, in this order, from the repository:

1. `docs/lab/CHARTER.md` — the constraints and how the lab is run. Everything here applies to you from now on.
2. `docs/lab/BOOTSTRAP.md` — the setup procedure you are executing, step by step.
3. `docs/handoff-answer-quality-2026-10-19.md` — the state of the reference stack, for context. It is a reference,
   not a mandate: the lab's architecture choices are yours, within the charter's fixed constraints.

Then do the bootstrap exactly as written in `BOOTSTRAP.md`:

1. Provision the instance per its target table (or ask the owner to launch it if you cannot create AWS resources,
   then continue from step 2). Tag it `purpose=lab`, `owner=anthony`. Record the instance id and public IP.
2. Install Docker and confirm the instance profile is `oi-core-dev-instance` in account `878451098165`.
3. Get the repository to `/opt/oi-core` at the commit pinned in this prompt.
4. Restore the corpus package from
   `s3://officeinference-corpus-878451098165/lab-baseline/lab-corpus-2026-10-19.tar.gz` (size 50,652,986 bytes,
   sha256 `cc36924a730eb566bf5cd006d480965fc07af413d9710d37a7a4f03cbcb0c90d`) and verify the hash and the file
   count (545 documents).
5. Create `manifests/lab-1.yaml` with deployment id `lab-1`, render it, point the analyst model at Claude Haiku's
   `us.` inference profile, set the four answer-path flags to the reference configuration, and do **not** install
   the S3 evidence-ship timers.
6. Build the images, start the stack, provision the teams, and let ingestion run to completion. This takes hours
   on the same hardware; poll it, do not restart it in a loop.
7. Verify the baseline: 539 documents indexed, 100,478 chunks, 6 dead letters, 6 data-set cards; one smoke suite
   run completes; the four acceptance probes pass; a real question returns Claude Haiku as the resolved model.
8. Snapshot the baseline (Qdrant snapshot and the Postgres manifest dump) to
   `s3://officeinference-corpus-878451098165/lab-baseline/lab-1-initial/`, then **stop and report**.

What you must not do in this task:

- Do not touch the reference deployment, its instance, buckets, database or audit chain.
- Do not put real client data on the lab instance.
- Do not install the evidence-ship timers, backups, alerts or client onboarding.
- Do not change the answer path, the gateway, the access model or the audit mechanism.
- Do not start building product capability. The next task starts only after the owner accepts the plan requested
  by the charter.

Report back with:

- instance id, public IP, and the commit deployed;
- corpus counts and the evidence that ingestion completed;
- the smoke run totals with the raw file path;
- the four probe results with their output paths;
- the snapshot locations;
- the spend so far (instance hours and model spend);
- anything that deviated from `BOOTSTRAP.md` and why.

If a step fails, stop at that step and report the exact command and error. Do not improvise around a failed
verification.

---

Pin to use in this prompt: replace `<the commit pin>` with the commit the owner wants, at least `a886050`.
