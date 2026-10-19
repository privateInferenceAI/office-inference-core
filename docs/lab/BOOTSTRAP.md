# Lab bootstrap: a second instance with the reference stack running

This gets a fresh EC2 instance to the same starting line as the working deployment: the same repository, the same
corpus ingested, the same answer configuration, and a green baseline. It stops there. Building the rest of the
product is the lab work, governed by `CHARTER.md`, and it does not start until the owner has accepted a plan.

The result is a **separate deployment**: its own instance, its own database and vector store, its own audit chain,
its own deployment id. It is not connected to the working box in any way.

## What the lab agent is given

- **Repository**: `github.com/privateInferenceAI/office-inference-core`, pinned at the commit in the kick-off
  prompt (at minimum `a886050`).
- **Corpus**: `s3://officeinference-corpus-878451098165/lab-baseline/lab-corpus-2026-10-19.tar.gz`,
  50,652,986 bytes, sha256
  `cc36924a730eb566bf5cd006d480965fc07af413d9710d37a7a4f03cbcb0c90d`.
  It contains `documents/` (the 545 files the reference deployment has, including the redaction canary) and
  `rejected/` (the reference worklists, for information; the lab regenerates its own).
- **The documents that were ingested are in that package.** Restoring it and running ingestion reproduces the
  reference corpus: 539 indexed documents, 100,478 chunks, six data-set cards and six dead letters. Ingestion is
  deterministic (chunk ids derive from content and chunker version), so the starting index matches the reference.

## Target instance

| | |
|---|---|
| Type | `m7i.4xlarge` (16 vCPU, 64 GB), same as the reference box |
| Disk | 200 GB gp3 |
| OS | Ubuntu 24.04 LTS, x86_64 |
| Region/AZ | `us-east-1` (same as the reference; Bedrock is called in us-east-1) |
| Key pair | the owner's existing key (`ai-stack-key`); SSH from the owner's IP |
| Instance profile | `oi-core-dev-instance` in account `878451098165` — it carries the Bedrock invoke permissions for Nova, DeepSeek and Claude, and SSM |
| Egress | normal internet egress at deploy time (image pulls, TEI model download, Bedrock) |
| Tags | `purpose=lab`, `owner=anthony`, plus a name tag for the lab |
| Deployment id | `lab-1` (reflected in the manifest, the audit rows and the key aliases) |

No GPU. The reference stack runs embeddings and the parser on CPU; the GPU tiers are separate work.
The instance costs about 0.81 USD an hour while it runs. Stop it when nobody is working; do not terminate it while
lab work is in progress.

## Step 1 — provision the instance and base host

Use the owner's AWS access. If the agent has AWS tooling, launch the instance per the table and record its id and
public IP. If it does not, ask the owner to launch it and to paste back the instance id and IP.

On the instance, as `ubuntu`:

```bash
sudo apt-get update
sudo apt-get install -y ca-certificates curl gnupg git unzip
curl -fsSL https://get.docker.com | sudo sh
sudo usermod -aG docker ubuntu
# log out and back in, then:
docker --version && docker compose version
```

Every Docker volume and database for the lab is created by the compose file below; nothing is copied from the
reference instance.

Confirm the instance role before going further:

```bash
curl -s http://169.254.169.254/latest/meta-data/iam/info
# expect an InstanceProfileArn in account 878451098165 for oi-core-dev-instance
```

The AWS CLI is optional on the lab instance. If you want it for the snapshot upload in step 8,
install the official v2 bundle; the role already carries the permissions.

## Step 2 — the repository

Preferred, if the instance can reach the private repository (deploy key or owner-provided credentials):

```bash
sudo mkdir -p /opt/oi-core && sudo chown ubuntu:ubuntu /opt/oi-core
git clone git@github.com:privateInferenceAI/office-inference-core.git /opt/oi-core
cd /opt/oi-core && git checkout <the pin in the kick-off prompt>
```

If the instance cannot reach GitHub, copy the checkout from the reference box. This copies code only; the corpus
comes from the package in step 3.

```bash
# from the workstation, with the reference box and lab IP substituted
rsync -az --exclude rendered --exclude documents --exclude backups --exclude '.git' \
  -e "ssh -i ~/ai-stack/ai-stack-key.pem" \
  ubuntu@<reference-ip>:/opt/oi-core/ ubuntu@<lab-ip>:/opt/oi-core/
```

The lab repo should be treated as its own checkout; the `.git` directory may be present or absent depending on the
copy path, but changes are committed to branches and pushed as for the reference.

## Step 3 — restore the corpus

From the workstation, with the `oi-credits` AWS profile:

```bash
aws s3 cp s3://officeinference-corpus-878451098165/lab-baseline/lab-corpus-2026-10-19.tar.gz \
  /tmp/lab-corpus-2026-10-19.tar.gz --profile oi-credits
sha256sum /tmp/lab-corpus-2026-10-19.tar.gz
# expect cc36924a730eb566bf5cd006d480965fc07af413d9710d37a7a4f03cbcb0c90d
scp -i ~/ai-stack/ai-stack-key.pem /tmp/lab-corpus-2026-10-19.tar.gz ubuntu@<lab-ip>:/tmp/
```

On the lab instance:

```bash
cd /opt/oi-core
tar -xzf /tmp/lab-corpus-2026-10-19.tar.gz
sudo chown -R ubuntu:ubuntu documents rejected
chmod -R a+rX documents rejected
find documents -type f | wc -l          # expect 545
ls documents/company | head             # expect canary-pii.md and group-01, group-02
```

Restore the corpus **before** starting the stack, so the first ingestion cycle sees a complete corpus rather than
half of one.

## Step 4 — render the lab manifest and the answer configuration

Create the lab's own manifest so its deployment id and audit records cannot be confused with the reference:

```bash
cd /opt/oi-core
cp manifests/dev-cloud.yaml manifests/lab-1.yaml
python3 - <<'PY'
import pathlib
p = pathlib.Path("manifests/lab-1.yaml"); s = p.read_text()
s = s.replace("deployment_id: oi-core-dev-cloud", "deployment_id: lab-1")
s = s.replace("name: Office Inference Dev", "name: Office Inference Lab")
p.write_text(s)
PY
grep -n "deployment_id" manifests/lab-1.yaml          # expect: deployment_id: lab-1
python3 scripts/render_manifest.py --manifest manifests/lab-1.yaml --out rendered
```

Do not install the S3 evidence-ship timers on the lab. The audit chain lives in the lab database; that keeps the
lab's record separate from the reference's bucket, and the lab is not a deployment that ships evidence anywhere.

Point the answer path at the same measured configuration as the reference: Claude Haiku through the `us.` inference
profile, tool loop on, row query on, lookup off, honesty on, six steps, ten calls.

```bash
python3 - <<'PY'
import json, pathlib
p = pathlib.Path("rendered/manifest.json"); m = json.loads(p.read_text())
m["models"]["analyst"]["frontier"] = "bedrock/us.anthropic.claude-haiku-4-5-20251001-v1:0"
p.write_text(json.dumps(m, indent=2, sort_keys=True) + "\n")
l = pathlib.Path("rendered/litellm.yaml"); t = l.read_text()
t = t.replace("bedrock/amazon.nova-lite-v1:0",
              "bedrock/us.anthropic.claude-haiku-4-5-20251001-v1:0", 1)
l.write_text(t)
PY

for pair in OI_TOOL_LOOP=on OI_TOOL_QUERY=on OI_TOOL_LOOKUP=off OI_TOOL_HONESTY=on \
            OI_TOOL_MAX_STEPS=6 OI_TOOL_MAX_CALLS=10; do
  k=${pair%%=*}; v=${pair#*=}
  grep -q "^$k=" rendered/env && sed -i "s/^$k=.*/$k=$v/" rendered/env || echo "$k=$v" >> rendered/env
done
```

## Step 5 — build, start, provision teams

```bash
cd /opt/oi-core
docker compose -f deploy/office/compose/docker-compose.yml --env-file rendered/env --profile tools build
docker compose -f deploy/office/compose/docker-compose.yml --env-file rendered/env up -d
sleep 30
docker compose -f deploy/office/compose/docker-compose.yml --env-file rendered/env \
  --profile tools run --rm keys --provision-teams
docker compose -f deploy/office/compose/docker-compose.yml --env-file rendered/env ps
```

The first boot pulls the TEI embedding image and downloads its model, so it needs internet egress and a few
minutes. If `api` is unhealthy, read its log before changing anything.

## Step 6 — ingest, and wait

The ingestion worker discovers the corpus and builds the index. On the reference hardware this took several hours
for the same 545 files, because parsing and CPU embedding are the cost. Start it and poll; do not restart it in a
loop.

```bash
docker compose -f deploy/office/compose/docker-compose.yml --env-file rendered/env logs -f ingestion
```

Completion means the retryable set is empty. Check the counts against the reference baseline:

```bash
docker exec postgres psql -U oicore -d oicore -tAc "select count(distinct doc_id), count(*) from chunk_index"
# expect: 539 | 100478

docker exec postgres psql -U oicore -d oicore -tAc "select count(*) from ingest_manifest where failure_count >= 2"
# expect: 6   (the genuinely unreadable files, listed in rejected/rejected.csv)

docker exec postgres psql -U oicore -d oicore -tAc "select count(*) from chunk_index where kind = 'dataset'"
# expect: 6
```

If the counts differ, stop and report before starting any lab section. A baseline that does not match is not a
baseline.

## Step 7 — verify the starting line

One smoke run of the answer suite, then the four acceptance probes. A single suite run is a smoke test, not a
measurement: do not report it as a result.

```bash
scripts/run_answer_suite.sh 1 /tmp/lab-baseline-smoke
# one run, expect roughly 40 to 47 of 52; run-to-run variance is wide, so this only proves the pipeline works

# resolved model
docker compose -f deploy/office/compose/docker-compose.yml --env-file rendered/env run -T --rm --no-deps \
  --entrypoint python -v /opt/oi-core:/w -w /w --env-file rendered/env \
  eval /w/tests/fixtures/ask_client.py -q "What is the mileage rate for reimbursement?" --json \
  | grep -E '"(answer|resolved_model)"'
```

Run the four probes exactly as in `docs/runbook.md` section 6.7; all four should pass. Record the outputs.

## Step 8 — snapshot the baseline, then stop and report

So the lab can always return to this line:

```bash
# Qdrant snapshot: create it, copy it out of the container, record the checksum
SNAP=$(docker run --rm --network ai-net curlimages/curl -sS \
  -X POST http://vector:6333/collections/documents/snapshots \
  | python3 -c 'import json,sys; print(json.load(sys.stdin)["result"]["name"])')
docker cp "vector:/qdrant/snapshots/documents/$SNAP" "/tmp/$SNAP"
sha256sum "/tmp/$SNAP"

# Postgres manifest dump
docker exec postgres pg_dump -U oicore -d oicore -Fc \
  -t ingest_manifest -t chunk_index -t collection_contracts -t document_versions \
  -f /tmp/lab-1-manifest.dump
docker cp postgres:/tmp/lab-1-manifest.dump /tmp/lab-1-manifest.dump
```

Then from the workstation:

```bash
# substitute the snapshot name the first command printed
scp -i ~/ai-stack/ai-stack-key.pem "ubuntu@<lab-ip>:/tmp/<snapshot-name>" /tmp/
scp -i ~/ai-stack/ai-stack-key.pem ubuntu@<lab-ip>:/tmp/lab-1-manifest.dump /tmp/
aws s3 cp "/tmp/<snapshot-name>" s3://officeinference-corpus-878451098165/lab-baseline/lab-1-initial/ --profile oi-credits
aws s3 cp /tmp/lab-1-manifest.dump s3://officeinference-corpus-878451098165/lab-baseline/lab-1-initial/ --profile oi-credits
aws s3 ls s3://officeinference-corpus-878451098165/lab-baseline/lab-1-initial/ --profile oi-credits
```

Report to the owner: instance id and IP, deployment id, corpus counts, resolved model, the smoke run and probe
results, the raw paths, and the spend so far. Then **stop**. Do not start section 1 until the owner accepts the
plan from `CHARTER.md`.

## Cleanup and cost

- Stop the instance when no one is working (`aws ec2 stop-instances`); starting it again preserves the volumes.
- Do not terminate the instance while lab work is in progress without taking the snapshots above and confirming
  with the owner.
- The lab has no backup, no monitoring and no alarms. That is deliberate; it is a lab, not a client deployment.
