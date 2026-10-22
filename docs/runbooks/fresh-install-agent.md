# Fresh install of Office Inference Core, run by an agent

**Status:** the procedure below was run end to end on 2026-10-22. The measurements from that run are in
`docs/reviews/fresh-install-2026-10-22/README.md`. This document is the repeatable version: a second agent
starts from the prompt at the top and finishes with a verified deployment and the same evidence set.

**What a pass looks like.** A new instance is running the pinned code archive; the corpus-2 documents are in
Postgres and Qdrant with counts matching the baseline; Sonnet 4.6 answers through the API with
`resolved_model=bedrock/us.anthropic.claude-sonnet-4-6` and `fallback_used=false`; the four acceptance probes
pass; the five-question corpus-2 check scores 5 of 5; the evidence directory is copied off the box; the instance
is stopped, not terminated.

## The prompt

This is the brief handed to the install agent. Everything after it is the procedure that brief refers to.

````text
# Task: fresh end-to-end install and acceptance of Office Inference Core, then document it as a repeatable process

You are the install and acceptance agent for a brand-new deployment of `office-inference-core`. Do the whole job end to end, capture evidence, and write a repeatable runbook that can be handed to the next agent as a single prompt. Do not stop after planning. Do not ask questions you can answer with a command.

## Deliverables

1. A brand-new EC2 instance in AWS account 878451098165, us-east-1, running the stack from a pinned archive.
2. The corpus-2 documents ingested, with counts verified against the expected baseline.
3. Sonnet 4.6 answering through the deployment, verified with the acceptance probes and a small corpus-2 answer check. If Bedrock quota blocks it, capture the exact evidence and report a partial acceptance; never silently substitute a different model.
4. `docs/runbooks/fresh-install-agent.md`: the repeatable process, with this prompt saved verbatim as the agent-facing section, prerequisites, asset preparation (including how to build a corpus archive and upload it to S3), exact commands with expected outputs, acceptance criteria, timing, failure modes, and cleanup.
5. `docs/reviews/fresh-install-2026-10-22/README.md`: what actually happened, with timings, counts, probe results, resolved model, fallback status, instance id and evidence paths.
6. A git branch `docs/fresh-install-2026-10-22` with both documents committed and pushed, and an open PR to main. Do not commit secrets or rendered/env.

## Environment facts

- AWS credentials: use the aws-credits MCP tool (assumed role DSHAgentRole, account 878451098165, us-east-1). If your catalog exposes `mcp__aws-credits__aws___run_script`, call boto3 through it. Do not use the default AWS account for anything.
- SSH key: `/home/anthony/ai-stack/ai-stack-key.pem`. SSH options: `-i /home/anthony/ai-stack/ai-stack-key.pem -o StrictHostKeyChecking=no -o ConnectTimeout=15`.
- Network: security group `sg-09d9bfffbd28014c9` allows SSH from the current workstation IP. Subnet `subnet-0b4ff3cef07463cf9`, key pair `ai-stack-key`, instance profile `arn:aws:iam::878451098165:instance-profile/oi-core-dev-instance`.
- Instance template: m7i.4xlarge, 200 GB gp3 root volume on /dev/sda1, Ubuntu 24.04 AMI `ami-02a9cdd4e98bc3c78` (verify it still exists; if not, resolve the current Canonical Ubuntu 24.04 amd64 gp3 AMI via SSM parameter `/aws/service/canonical/ubuntu/server/24.04/stable/current/amd64/hvm/ebs-gp3/ami-id`).
- Tags: Name=fresh-install-2026-10-22, project=office-inference, purpose=fresh-install-acceptance.
- Do not modify IAM, the security group, the VPC, or existing instances. Do not create an Elastic IP.

### Assets already staged

S3 bucket `officeinference-corpus-878451098165`, both objects uploaded and checksummed:

- Code archive: key `fresh-install/oi-core-795b9a6.tar.gz`, sha256 `28c24862da6abdec3061f2f5dd3412dc8dee4f4866635822f230dd9f1684b70c`. It is `git archive` of commit `795b9a6` on main. There is no .git directory, by design; document the archive checksum and commit id.
- Corpus archive: key `second-corpus/second-corpus-2026-10-22.tar.gz`, sha256 `0b154bcb452af0789cdc34a6453b3806924fb22cdbf15a217eb73175072a88a1`. It contains a top-level `documents/` directory with the 80 corpus-2 documents.

Generate presigned download URLs yourself with the AWS MCP tool:

```
mcp__aws-credits__aws___get_presigned_url
{"requests":[{"bucket":"officeinference-corpus-878451098165","key":"<key>","operation":"download"}]}
```

The URL is valid for about 15 minutes. Download promptly with `curl -L -o <file> "<url>"` on the new instance (or on the workstation and scp across), then verify sha256 before use.

## Procedure

### 1. Launch and prepare the host

- Launch the instance with RunInstances using the template above. Record instance id and public IP. Poll DescribeInstances until running and SSH answers.
- On the host: `sudo apt-get update`, then install `ca-certificates curl gnupg git unzip jq`. Install Docker with `curl -fsSL https://get.docker.com | sudo sh`, add ubuntu to the docker group, and verify `docker --version` and `docker compose version` (re-login or use sudo for the first compose call if group membership has not taken effect).
- Confirm the instance role: `curl -s http://169.254.169.254/latest/meta-data/iam/info` must show `oi-core-dev-instance`.

### 2. Code and manifest

- `sudo mkdir -p /opt/oi-core && sudo chown ubuntu:ubuntu /opt/oi-core`; download and extract the code archive there; verify the sha256; confirm `test -f services/api/app.py`.
- Create a fresh deployment manifest from the tracked accuracy manifest:
  `cp manifests/dev-cloud-accuracy.yaml manifests/fresh-install-2026-10-22.yaml`, then change `deployment_id` to `oi-core-fresh-2026-10-22` and `client.name` to `Office Inference Fresh Install`. Keep the analyst on Claude Sonnet 4.6 with Haiku fallback exactly as the tracked manifest has it.
- Render: `python3 scripts/render_manifest.py --manifest manifests/fresh-install-2026-10-22.yaml --root /opt/oi-core`.
- Set `OI_ANSWER_PROFILE=accuracy` in `rendered/env` and confirm it.
- Validate the manifest: `python3 scripts/validate_manifests.py manifests/fresh-install-2026-10-22.yaml` and `python3 scripts/check_model_lifecycle.py manifests/fresh-install-2026-10-22.yaml`.

### 3. Corpus before the stack

- Download and extract the corpus archive to `/opt/oi-core` so `/opt/oi-core/documents` exists. Verify the expected 80 files with `find documents -type f | wc -l`. `chmod -R a+rX documents`.

### 4. Build, start, provision

- `docker compose -f deploy/office/compose/docker-compose.yml --env-file rendered/env --profile tools build`
- `docker compose ... up -d`, wait for postgres healthy and api ready. The API applies migrations at startup; record the migration count or the relevant log line as evidence.
- `docker compose ... --profile tools run --rm keys --provision-teams` and record the key aliases it prints (do not print secret values).
- `docker compose ... ps` and save the output.

### 5. Ingest, and wait

- The ingestion worker starts with the stack and discovers the corpus. Follow its log until the retryable set is empty and ingestion reports completion. This is CPU-bound and may take a while; poll every few minutes, do not restart it in a loop.
- Verify counts through the deployment's postgres:
  - `select count(distinct doc_id), count(*) from chunk_index;` expected for corpus 2: 80 | 24296. If the counts differ, record the actual numbers and investigate before acceptance; do not adjust the baseline.
  - `select count(*) from ingest_manifest where failure_count >= 2;` record the value.
  - `select count(*) from chunk_index where kind = 'dataset';` record the value.
- Record the ingestion start and end timestamps.

### 6. Acceptance with Sonnet 4.6

- Confirm the gateway's analyst is Sonnet 4.6: inspect `rendered/litellm.yaml`.
- Ask one smoke question through the API and confirm the audit row's resolved_model is `bedrock/us.anthropic.claude-sonnet-4-6` and `fallback_used` is false. Use the eval image's ask_client or the API directly; record the exact command and output.
- Run the four acceptance probes exactly as `docs/runbook.md` section 6.7 specifies. Save every output. All four should pass.
- Run a bounded corpus-2 answer check: five representative questions (PUB_N01, PUB_P01, PUB_L01, SYN_A01 staff, SYN_A01 executive) through `tests/fixtures/data_suite_run.py` with `DATA_SUITE_QUESTIONS=/w/eval-questions/second-corpus.yaml` (copy the eval-questions directory into the install root if it is not already there; it is not in the code archive, so obtain it from the repository working tree on the workstation and copy it to the new box). Expect 5 of 5. Record resolved model and fallback counts from `audit_events` for the run window.
- If Bedrock returns `ThrottlingException: Too many tokens per day` or the gateway falls back, stop the model-dependent acceptance, capture the exact response and the audit rows, mark acceptance partial, and continue with non-model checks. Do not switch models silently.

### 7. Document, archive, stop

- Save all command outputs, counts, logs and JSON results under `/opt/oi-core/evidence/fresh-install/`, then copy the directory to `/home/anthony/oi-eval-runs/fresh-install-2026-10-22/` on the workstation (scp with the key above).
- Write the two documents named in Deliverables. The runbook must be usable by a future agent with only this prompt: include asset preparation (how the corpus archive was built from the existing deployment, the S3 keys and checksums, how to generate presigned URLs), every command with expected output, acceptance criteria, timings, and failure modes. Include this prompt verbatim in a fenced block at the top.
- Commit both documents on branch `docs/fresh-install-2026-10-22`, push, and open a PR to main. Keep the working tree limited to those two files plus the fresh manifest if you decide to commit it; do not commit rendered/env or any secret.
- Stop the instance (do not terminate) after evidence is copied. Report: instance id, IP, state, install result, corpus counts, probe results, answer-check result, resolved model, fallback status, timings, evidence paths, and PR URL.

## Rules

- Verify with commands; never assume a step succeeded.
- Do not touch existing instances, IAM policies, security groups, VPCs or S3 bucket policies.
- Do not use the external DeepSeek API or any credential that is not native to the instance role.
- If something fails, record the command, the output and the smallest fix that made it work. That failure and fix belongs in the runbook.
- Keep the final report under 500 words plus links and evidence paths.
````

## 1. Prerequisites

| Thing | Value used in the reference run | How to check |
|---|---|---|
| AWS account | 878451098165, assumed role `DSHAgentRole`, us-east-1 | `sts:GetCallerIdentity` through the aws-credits MCP tool |
| AMI | `ami-02a9cdd4e98bc3c78`, Ubuntu 24.04 amd64 gp3, 20261017 | `ec2:DescribeImages`; if absent, read SSM `/aws/service/canonical/ubuntu/server/24.04/stable/current/amd64/hvm/ebs-gp3/ami-id` |
| Instance type | m7i.4xlarge, 16 vCPU, 64 GB, no GPU | `ec2:DescribeInstanceTypes` |
| Root volume | 200 GB gp3 on `/dev/sda1`, encrypted, deleted on termination | launch parameters |
| Subnet | `subnet-0b4ff3cef07463cf9`, us-east-1b, public IP on launch | `ec2:DescribeSubnets` |
| Security group | `sg-09d9bfffbd28014c9`, SSH from the workstation IP only | `ec2:DescribeSecurityGroups`; compare with `curl -s https://checkip.amazonaws.com` |
| Key pair | `ai-stack-key` | `ec2:DescribeKeyPairs` |
| Instance profile | `arn:aws:iam::878451098165:instance-profile/oi-core-dev-instance` | `iam:GetInstanceProfile`; the role carries the Bedrock invoke policy |
| S3 bucket | `officeinference-corpus-878451098165` | `s3:HeadObject` on each key |
| Workstation tools | `ssh`, `scp`, `curl`, `git`, `gh`, `python3` | `command -v` each |
| Repo checkout | `~/projects/office-inference-core` at `795b9a6` | `git rev-parse HEAD` |
| UI checkout | `~/projects/oic-ui` at `6d28c3c` | `git rev-parse HEAD` |

**Bedrock access is a prerequisite, not a step.** The account must have the Anthropic use-case form submitted and
model access for `us.anthropic.claude-sonnet-4-6` and `us.anthropic.claude-haiku-4-5-20251001-v1:0`. Check it
before launching, because a failure at acceptance costs an hour:

```
mcp__aws-credits__aws___run_script
bedrock:ListInferenceProfiles
```

Expected: both `us.anthropic.claude-sonnet-4-6` and `us.anthropic.claude-haiku-4-5-20251001-v1:0` in the list.

## 2. Assets

Three archives. The code and corpus were staged before the reference run; the UI archive was built during it
because the compose file needs a second checkout (see below). Every asset is identified by a sha256 that the
install verifies before use.

### 2.1 Code archive

```
cd ~/projects/office-inference-core
git archive --format=tar.gz -o /tmp/oi-core-795b9a6.tar.gz 795b9a6
sha256sum /tmp/oi-core-795b9a6.tar.gz
```

Expected: `28c24862da6abdec3061f2f5dd3412dc8dee4f4866635822f230dd9f1684b70c`.

The archive has no `.git` directory by design. The commit id is recorded in this document and in the evidence,
because the archive cannot report it.

### 2.2 UI archive

The compose `app` service builds from `OI_UI_ROOT`, default `/opt/oic-ui`, and that source lives in the separate
`oic-ui` repository. A code-archive-only install fails at build with:

```
unable to prepare context: path "/opt/oic-ui" not found
```

```
cd ~/projects/oic-ui
git archive --format=tar.gz --prefix=oic-ui/ -o /tmp/oic-ui-6d28c3c.tar.gz HEAD
sha256sum /tmp/oic-ui-6d28c3c.tar.gz
```

Expected: `4609bdb30bbd6438ac1a77f8fd8e0cb5795e977b211a0c7f6f5725450df72a2b` (148,271 bytes).

### 2.3 Corpus archive

Built on the deployment that holds corpus 2, from the live documents directory. The archive contains one
top-level `documents/` directory. Access labels come from the first path segment under `documents/`
(`company`, `executive`), so the directory shape is part of the data contract, not packaging.

```
cd /opt/oi-core
tar -czf /tmp/second-corpus-2026-10-22.tar.gz documents
sha256sum /tmp/second-corpus-2026-10-22.tar.gz
tar -tzf /tmp/second-corpus-2026-10-22.tar.gz | head
```

Expected: `0b154bcb452af0789cdc34a6453b3806924fb22cdbf15a217eb73175072a88a1`, 21,613,548 bytes, and the first
entry `documents/`.

Corpus 2 is 80 documents: 40 public (SEC filings and US government PDFs) and 40 synthetic Northwind documents.
Its provenance and question set are committed under `docs/eval/corpora/second-corpus/`.

### 2.4 Upload, and presigned URLs

Upload with a presigned PUT, download with a presigned GET. The MCP tool returns a URL valid for about 15
minutes, so upload or download promptly.

```
mcp__aws-credits__aws___get_presigned_url
{"requests":[{"bucket":"officeinference-corpus-878451098165","key":"<key>","operation":"upload"}]}

curl -sS -X PUT --upload-file /tmp/<file> "<url>"
```

```
mcp__aws-credits__aws___get_presigned_url
{"requests":[
  {"bucket":"officeinference-corpus-878451098165","key":"fresh-install/oi-core-795b9a6.tar.gz","operation":"download"},
  {"bucket":"officeinference-corpus-878451098165","key":"fresh-install/oic-ui-6d28c3c.tar.gz","operation":"download"},
  {"bucket":"officeinference-corpus-878451098165","key":"second-corpus/second-corpus-2026-10-22.tar.gz","operation":"download"}
]}
```

| Key | sha256 | Bytes |
|---|---|---|
| `fresh-install/oi-core-795b9a6.tar.gz` | `28c24862…b70c` | 3,606,299 |
| `fresh-install/oic-ui-6d28c3c.tar.gz` | `4609bdb3…72a2b` | 148,271 |
| `second-corpus/second-corpus-2026-10-22.tar.gz` | `0b154bcb…a88a1` | 21,613,548 |

## 3. Launch

```
mcp__aws-credits__aws___run_script
ec2:RunInstances
  ImageId            ami-02a9cdd4e98bc3c78
  InstanceType       m7i.4xlarge
  KeyName            ai-stack-key
  SecurityGroupIds   [sg-09d9bfffbd28014c9]
  SubnetId           subnet-0b4ff3cef07463cf9
  IamInstanceProfile arn:aws:iam::878451098165:instance-profile/oi-core-dev-instance
  BlockDeviceMappings[/dev/sda1] 200 GB gp3, Encrypted, DeleteOnTermination
  MetadataOptions    HttpTokens=required, HttpPutResponseHopLimit=2
  Tags               Name=fresh-install-2026-10-22, project=office-inference,
                     purpose=fresh-install-acceptance (instance and volume)
```

Expected: `State=pending`, an instance id, and a private IP. Then poll:

```
ec2:DescribeInstances {"InstanceIds":["<id>"]}
```

until `State.Name=running`. Record the launch time and public IP. SSH answers within about 20 seconds of the
instance reaching running:

```
for i in $(seq 1 30); do
  ssh -i /home/anthony/ai-stack/ai-stack-key.pem -o StrictHostKeyChecking=no \
      -o ConnectTimeout=10 ubuntu@<public-ip> 'echo SSH_OK; date -u' && break
  sleep 10
done
```

**Do not create an Elastic IP.** The public IP is ephemeral and the instance is stopped at the end, so an EIP
would be a bill with no purpose. If the instance is stopped and restarted later, its public IP changes and the
security group's SSH rule still admits the workstation, not the instance.

## 4. Host preparation

```
sudo apt-get update -y
sudo DEBIAN_FRONTEND=noninteractive apt-get install -y ca-certificates curl gnupg git unzip jq
curl -fsSL https://get.docker.com | sudo sh
sudo usermod -aG docker ubuntu
docker --version
docker compose version
```

Expected on 2026-10-22: `Docker version 29.9.2`, `Docker Compose version v5.6.1`. Group membership applies to
new SSH sessions, so the first `docker` call after this either works in a fresh session or needs `sudo`.

```
id -nG                      # expect docker in the list
docker ps                   # expect no permission error
```

**The role check needs an IMDSv2 token, because the launch enforces IMDSv2.** The bare metadata path returns
empty, which reads like a missing role:

```
TOKEN=$(curl -s -X PUT "http://169.254.169.254/latest/api/token" \
  -H "X-aws-ec2-metadata-token-ttl-seconds: 60")
curl -s -H "X-aws-ec2-metadata-token: $TOKEN" \
  http://169.254.169.254/latest/meta-data/iam/info
```

Expected: `InstanceProfileArn: arn:aws:iam::878451098165:instance-profile/oi-core-dev-instance`.

The hop limit of 2 is not optional: a container reaches IMDS through the host, and the default hop limit of 1
refuses it. Without it, LiteLLM reports `Unable to locate credentials`.

## 5. Code, UI, and the manifest

```
sudo mkdir -p /opt/oi-core && sudo chown ubuntu:ubuntu /opt/oi-core
sudo mkdir -p /opt/oic-ui && sudo chown ubuntu:ubuntu /opt/oic-ui
cd /opt/oi-core
mkdir -p evidence/fresh-install

curl -fsSL -o /tmp/oi-core-795b9a6.tar.gz "<code-url>"
sha256sum /tmp/oi-core-795b9a6.tar.gz        # expect 28c24862…b70c
tar -xzf /tmp/oi-core-795b9a6.tar.gz -C /opt/oi-core
test -f services/api/app.py && echo "app.py present"

curl -fsSL -o /tmp/oic-ui-6d28c3c.tar.gz "<ui-url>"
sha256sum /tmp/oic-ui-6d28c3c.tar.gz          # expect 4609bdb3…72a2b
tar -xzf /tmp/oic-ui-6d28c3c.tar.gz --strip-components=1 -C /opt/oic-ui
test -f /opt/oic-ui/docs/openapi.json && echo "openapi contract present"
```

The manifest is a copy of the tracked accuracy manifest with two fields changed. The analyst stays on Sonnet
4.6 and the fallback chain stays exactly as tracked: Haiku 4.5, then Nova Pro.

```
cp manifests/dev-cloud-accuracy.yaml manifests/fresh-install-2026-10-22.yaml
sed -i 's/^  deployment_id: .*/  deployment_id: oi-core-fresh-2026-10-22/' \
  manifests/fresh-install-2026-10-22.yaml
sed -i 's/^  name: .*/  name: Office Inference Fresh Install/' \
  manifests/fresh-install-2026-10-22.yaml
grep -nE 'deployment_id|  name:|frontier: bedrock/us.anthropic.claude-sonnet' \
  manifests/fresh-install-2026-10-22.yaml
```

Expected: `deployment_id: oi-core-fresh-2026-10-22`, `name: Office Inference Fresh Install`, and
`frontier: bedrock/us.anthropic.claude-sonnet-4-6`.

```
python3 scripts/render_manifest.py --manifest manifests/fresh-install-2026-10-22.yaml \
  --root /opt/oi-core
```

Expected:

```
rendered fresh-install-2026-10-22.yaml -> /opt/oi-core/rendered
  profile      cloud
  packs        core
  local models (none)
  gateway      6 model entries
  access       declared: 2 group(s), 2 user(s)
  secrets      generated
  env file     /opt/oi-core/rendered/env (mode 600)
```

```
grep -q '^OI_ANSWER_PROFILE=' rendered/env \
  && sed -i 's/^OI_ANSWER_PROFILE=.*/OI_ANSWER_PROFILE=accuracy/' rendered/env \
  || echo 'OI_ANSWER_PROFILE=accuracy' >> rendered/env
grep '^OI_ANSWER_PROFILE=' rendered/env          # expect OI_ANSWER_PROFILE=accuracy

python3 scripts/validate_manifests.py manifests/fresh-install-2026-10-22.yaml
python3 scripts/check_model_lifecycle.py manifests/fresh-install-2026-10-22.yaml
```

Expected: `1/1 manifests valid; 8 packs known` and `1/1 manifests clean; 6 remote model reference(s) checked`.

```
grep -E 'model_name|model:' rendered/litellm.yaml
```

Expected: `analyst` maps to `bedrock/us.anthropic.claude-sonnet-4-6`, with `analyst-fallback` on Haiku 4.5 and
`analyst-fallback-2` on Nova Pro.

## 6. Corpus, then the runtime directories

```
cd /opt/oi-core
curl -fsSL -o /tmp/second-corpus.tar.gz "<corpus-url>"
sha256sum /tmp/second-corpus.tar.gz          # expect 0b154bcb…a88a1
tar -xzf /tmp/second-corpus.tar.gz -C /opt/oi-core
find documents -type f | wc -l               # expect 80
chmod -R a+rX documents
```

**Create these before compose ever sees them.** Docker creates a missing bind-mount path as root, and the
resulting ownership problems surface much later as permission errors:

```
mkdir -p data exports rejected
sudo chown -R 10001:10001 rejected
sudo chmod 777 rejected
ls -ld data exports rejected
```

`rejected` must be writable by the ingestion worker, uid 10001. `exports` must be writable by the operator,
because the audit tool runs as the host user. Do not chown the whole tree.

## 7. Build, start, provision

The compose file joins an external network. A fresh host does not have it, and `up` fails with
`network ai-net declared as external, but could not be found`:

```
docker network create ai-net
docker network ls | grep ai-net
```

```
cd /opt/oi-core
docker compose -f deploy/office/compose/docker-compose.yml --env-file rendered/env \
  --profile tools build
```

Expected: ten images built, including `oi-core/parser:local` (about 8.6 GB), `oi-core/litellm:local` (1.7 GB),
and `oi-core/app:local`. The reference run took 3 minutes 7 seconds.

**Start the gateway before the API.** LiteLLM's Prisma migrations refuse a database whose schema they did not
create, and the API applies the audit migrations at startup. If both start together the API usually wins, and
LiteLLM restart-loops with:

```
LiteLLM Proxy: Database migration cannot proceed. Cannot automatically baseline this database:
its schema has not been verified to match this build.
```

```
docker compose -f deploy/office/compose/docker-compose.yml --env-file rendered/env \
  up -d postgres litellm
for i in $(seq 1 40); do
  docker run --rm --network ai-net curlimages/curl -sf -m 5 \
    http://litellm:4000/health/liveliness && break
  sleep 5
done
```

Expected: `"I'm alive!"` within about 20 seconds, and `docker inspect -f '{{.RestartCount}}' litellm` at 0.

```
docker compose -f deploy/office/compose/docker-compose.yml --env-file rendered/env up -d
sleep 25
docker compose -f deploy/office/compose/docker-compose.yml --env-file rendered/env ps
```

Expected: eight services (`postgres`, `litellm`, `embeddings`, `parser`, `vector`, `api`, `app`,
`ingestion`), postgres and parser healthy, and the API logging:

```
api ready: registry=/app/rendered/access.json model_role_default=analyst answers_with=analyst jobs=durable
```

```
docker exec postgres psql -U oicore -d oicore -tAc \
  "select count(*), max(filename) from schema_migrations;"
```

Expected: `23|024_the_row_knows_its_table.sql`. Record this line as the migration evidence.

```
docker compose -f deploy/office/compose/docker-compose.yml --env-file rendered/env \
  --profile tools run --rm keys --provision-teams
```

Expected, one line per role plus a closing sentence:

```
teams for oi-core-fresh-2026-10-22:
  analyst    members=[] models=[analyst]  200 USD per 30d
  coder      members=[] models=[coder]  5 USD per 30d
  front_desk members=[] models=[front_desk]  2 USD per 30d
  reader     members=[] models=[reader]  10 USD per 30d
budgets are enforced per team, which is the path a signed assertion takes
```

## 8. Ingestion

The worker starts with the stack. On a fresh install it can lose two startup races, and both are visible in
`docker logs ingestion`:

- `could not add the table-context columns (UndefinedTable: relation "chunk_index" does not exist)` means it
  started before the API applied the migrations.
- `could not measure the embedding dimension: no endpoint returned a vector` means the embeddings service was
  still loading bge-m3.

The worker then sleeps 15 minutes, so the fast fix is one restart after the API and embeddings are ready:

```
docker restart ingestion
```

Expected on the first good cycle:

```
created collection documents at 1024 dimensions, cosine distance
embedding contract recorded at generation 1: BAAI/bge-m3, cls pooling, 1024 dimensions
  ingesting company/second-public/company/autodesk-inc/10-Q_2026-08-28_quarterly-report.htm
    (new, 1916813 bytes)
    769 chunks, ? pages
```

Then wait. This is CPU-bound: parsing plus embedding on 16 vCPU. Poll every few minutes, and do not restart the
worker in a loop.

```
tail -3 evidence/fresh-install/ingestion-progress.log
docker logs ingestion 2>&1 | grep -E "ingesting |chunks,|seen " | tail -5
```

Completion is one line from the worker, `seen 80, new 80, changed 0, skipped 0, failed 0`, followed by
`chunks written N, chunks removed 0, documents removed 0`. The worker's healthcheck reads the last cycle, so
`docker ps` showing `ingestion (healthy)` means the last cycle finished with no failures.

```
docker exec postgres psql -U oicore -d oicore -tAc \
  "select count(distinct doc_id), count(*) from chunk_index;"
docker exec postgres psql -U oicore -d oicore -tAc \
  "select count(*) from ingest_manifest where failure_count >= 2;"
docker exec postgres psql -U oicore -d oicore -tAc \
  "select count(*) from chunk_index where kind = 'dataset';"
```

Expected for corpus 2: `80|24296`, then `0` failures at the dead-letter threshold, then the dataset count. If the
chunk count differs from 24,296, record the actual number and stop before acceptance. The baseline is not
adjusted to match a run.

## 9. Acceptance

**The gateway's analyst.** `grep -A2 'model_name: analyst$' rendered/litellm.yaml` shows
`bedrock/us.anthropic.claude-sonnet-4-6`.

**One smoke question.** Through the eval image, which carries the client:

```
docker compose -f deploy/office/compose/docker-compose.yml --env-file rendered/env \
  run -T --rm --no-deps --entrypoint python -v /opt/oi-core:/w -w /w \
  -e OI_API_URL=http://api:8088 -e ACCESS_REGISTRY=/w/rendered/access.json \
  eval /w/tests/fixtures/ask_client.py -q "What is the mileage rate?" --json \
  | tee evidence/fresh-install/smoke-answer.json | grep resolved_model
```

The audit row is the evidence that the model named is the model that ran:

```
docker exec postgres psql -U oicore -d oicore -c \
  "select id, event_type, model, payload->>'resolved_model' as resolved,
          payload->>'fallback_used' as fallback
   from audit_events where event_type='model_call' order by id desc limit 3;"
```

Expected: `resolved = bedrock/us.anthropic.claude-sonnet-4-6` and `fallback = false`.

**Provoke one degradation before the probes.** The `interface_probe` asserts that a model failure produces a
degraded answer, and it reads the last `stage=degraded` row rather than forcing one, because forcing one needs a
gateway restart a container cannot perform. A brand-new deployment has no such row and the probe reports DEGRADED
as unproven. Remove the provider for one question:

```
docker stop litellm
docker compose -f deploy/office/compose/docker-compose.yml --env-file rendered/env \
  run -T --rm --no-deps --entrypoint python -v /opt/oi-core:/w -w /w \
  -e OI_API_URL=http://api:8088 -e ACCESS_REGISTRY=/w/rendered/access.json \
  eval /w/tests/fixtures/ask_client.py -q "What is the mileage rate for reimbursement?" --json \
  > evidence/fresh-install/degraded-answer.json
docker start litellm
for i in $(seq 1 30); do
  docker run --rm --network ai-net curlimages/curl -sf -m 5 \
    http://litellm:4000/health/liveliness >/dev/null 2>&1 && break
  sleep 3
done
```

Expected: the answer's `kind` is `degraded`, it still carries passages, and one row lands in `audit_events`
with `outcome='denied'` and `payload->>'stage'='degraded'`. The reference run recorded 12 passages and reason
`unclassified`, which is what the taxonomy says for a failure it does not recognise.

**Do not try an oversized `OI_MAX_TOKENS` for this.** Setting it on the compose command line does not reach the
container, because the `api` service does not list it in its `environment:` block, so `up --force-recreate`
succeeds and changes nothing. That attempt cost one run. Removing the gateway is deterministic and takes 30
seconds.

**The four probes**, exactly as `docs/runbook.md` section 6.7 specifies:

```
for probe in interface_probe activity_probe; do
  docker run --rm --network ai-net --user "$(id -u):$(id -g)" -v /opt/oi-core:/w -w /w \
    --env-file rendered/env --entrypoint python oi-core/session:local \
    /w/tests/fixtures/$probe.py
done
docker run --rm --network ai-net --user "$(id -u):$(id -g)" -v /opt/oi-core:/w -w /w \
  --env-file rendered/env -e AUDIT_TOOL=/w/services/audit/audit.py --entrypoint python \
  oi-core/eval:local /w/tests/fixtures/access_probe.py
docker run --rm --network ai-net --user "$(id -u):$(id -g)" -v /opt/oi-core:/w -w /w \
  --env-file rendered/env --entrypoint python oi-core/session:local \
  /w/tests/fixtures/stream_probe.py
```

Each probe prints one line per right and exits non-zero if a right fails. Expected: four `PASSED: N right(s)
held` lines, with no `FAIL`.

**The five-question corpus-2 check.** The question set is not in the code archive; copy it from the repository
checkout first:

```
scp -i /home/anthony/ai-stack/ai-stack-key.pem \
  docs/eval/corpora/second-corpus/questions.yaml \
  ubuntu@<ip>:/opt/oi-core/eval-questions/second-corpus.yaml
```

```
mkdir -p /tmp/oi-evidence && chmod 777 /tmp/oi-evidence
docker compose -f deploy/office/compose/docker-compose.yml --env-file rendered/env \
  run -T --rm --no-deps --entrypoint python -v /opt/oi-core:/w -v /tmp/oi-evidence:/out -w /w \
  -e DATA_SUITE_QUESTIONS=/w/eval-questions/second-corpus.yaml \
  eval /w/tests/fixtures/data_suite_run.py --json-out /out/corpus2-check.json \
  --only PUB_N01,PUB_P01,PUB_L01,SYN_A01
cp /tmp/oi-evidence/corpus2-check.json evidence/fresh-install/
```

The eval image runs as an unprivileged user, so `--json-out` has to point at a directory it can write. Pointing
it at the operator-owned checkout prints five passes and then exits 1 with `PermissionError`. The `chmod 777`
is on a throwaway directory under `/tmp`.

`--only` drops the runner's built-in probes, so the run is exactly the four ids named. `SYN_A01` is an access
question and runs once per role, which is where the fifth result comes from. Expected: five `PASS` lines and a
summary where every category has `runs` equal to `passed`, with `PUB_N01` on the Target 10-K, `PUB_P01` on a
public filing, `PUB_L01` on a public legal document, and `SYN_A01` passing for staff (refusal) and executive
(385,000).

Then record the model facts for the run window:

```
docker exec postgres psql -U oicore -d oicore -c \
  "select payload->>'resolved_model' as resolved,
          payload->>'fallback_used' as fallback, count(*)
   from audit_events
   where event_type='model_call' and occurred_at > now() - interval '2 hours'
   group by 1,2 order by 3 desc;"
```

**If Bedrock throttles.** `ThrottlingException: Too many tokens per day` or a fallback in the audit rows ends
model-dependent acceptance. Capture the exact response and the rows, mark acceptance partial, and keep going
with the non-model checks. Do not switch models to make the number pass.

## 10. Evidence, commit, stop

```
cd /opt/oi-core
mkdir -p evidence/fresh-install
# every command's output, the counts, the probe transcripts, the JSON results
tar -czf /tmp/evidence-fresh-install.tar.gz evidence/fresh-install
sudo chown ubuntu:ubuntu /tmp/evidence-fresh-install.tar.gz
```

```
rm -rf /tmp/fi-evidence && mkdir -p /tmp/fi-evidence
scp -i /home/anthony/ai-stack/ai-stack-key.pem -r \
  ubuntu@<ip>:/opt/oi-core/evidence/fresh-install /tmp/fi-evidence/
mkdir -p /home/anthony/oi-eval-runs/fresh-install-2026-10-22
cp -a /tmp/fi-evidence/fresh-install/. /home/anthony/oi-eval-runs/fresh-install-2026-10-22/
```

`scp -r host:/path/. dest` fails with `unexpected filename: .`, which is why this copies the directory into a
temporary one and then moves the contents.

Write the two documents, commit them on `docs/fresh-install-2026-10-22`, push, and open a PR. The tree holds
the two documents and the fresh manifest, nothing rendered. Copy `manifests/fresh-install-2026-10-22.yaml` back
from the box first: it is the record of the deployment the run produced, and it contains no secret.

```
git checkout -b docs/fresh-install-2026-10-22
git add docs/runbooks/fresh-install-agent.md \
        docs/reviews/fresh-install-2026-10-22/README.md \
        manifests/fresh-install-2026-10-22.yaml
git commit -m "Document the fresh install and its acceptance"
git push -u origin docs/fresh-install-2026-10-22
gh pr create --base main --head docs/fresh-install-2026-10-22 \
  --title "Fresh install runbook and 2026-10-22 acceptance record" --body "<body>"
```

```
mcp__aws-credits__aws___run_script
ec2:StopInstances {"InstanceIds":["<id>"]}
```

Stop, do not terminate. The root volume holds the install and the evidence until the evidence has been copied.
The instance is stopped only after `scp` reports success.

## 11. Timing

Reference run, 2026-10-22, all times UTC.

| Step | Start | End | Duration |
|---|---|---|---|
| Launch to SSH | 09:13:21 | 09:13:40 | 19 s |
| Host packages and Docker | 09:13:40 | 09:14:15 | 35 s |
| Archives, render, validation | 09:14:54 | 09:14:55 | under a minute |
| Image build | 09:15:47 | 09:18:54 | 3 min 07 s |
| First `up` (failed, no ai-net) | 09:19:01 | 09:19:16 | 15 s |
| Gateway-first start | 09:21:04 | 09:21:28 | 24 s |
| Remaining services | 09:21:35 | 09:22:00 | 25 s |
| Ingestion start | 09:22:18 | | |
| Ingestion finish (80 documents, 24,296 chunks) | 10:46:47 | 84 min 29 s |
| Acceptance script | 10:49:02 | 10:54:42 (5 min 40 s) |
| Clean probe and suite reruns | 10:52 | 10:55 (about 3 min) |

Budget about 15 minutes for the host and the build, then ingestion, which dominates. Corpus 2 is 80 documents
and 24,296 chunks; the reference run took 84 minutes to ingest and 1 hour 41 minutes from launch to accepted.
The public filings are the cost: a 16 MB 10-K parsed and embedded for several minutes, while the 40 synthetic
documents together took the last 15 minutes.

## 12. Failure modes

| Symptom | Cause | Smallest fix |
|---|---|---|
| `network ai-net declared as external, but could not be found` | Compose expects a network nobody created | `docker network create ai-net` |
| `LiteLLM Proxy: Database migration cannot proceed…` | The API applied audit migrations before Prisma baselined | `docker compose down`, remove `data/postgres`, start `postgres litellm` first, wait for `/health/liveliness`, then `up -d` |
| `relation "chunk_index" does not exist` in ingestion logs | Worker started before the API's migrations | `docker restart ingestion` after the API logs `api ready` |
| `could not measure the embedding dimension` | Embeddings service still loading bge-m3 | Wait for `embeddings (healthy)`, then `docker restart ingestion` |
| `unable to prepare context: path "/opt/oic-ui" not found` | UI checkout missing; it is a separate repo | Extract the UI archive to `/opt/oic-ui` and rebuild |
| `Permission denied` writing `rejected/` | Directory owned by root, or not writable by uid 10001 | `sudo chown -R 10001:10001 rejected && sudo chmod 777 rejected` |
| `curl …/iam/info` returns nothing | IMDSv2 enforced, IMDSv1 call refused | Request a token first, then send `X-aws-ec2-metadata-token` |
| `AccessDeniedException` from Bedrock | Model id and instance policy disagree | Compare `rendered/litellm.yaml` with the role's resource list |
| `Unable to locate credentials` in LiteLLM | IMDS hop limit 1, or no instance profile | Launch with `HttpPutResponseHopLimit=2`; attach `oi-core-dev-instance` |
| `ThrottlingException: Too many tokens per day` | Bedrock daily allotment exhausted | Capture the response and audit rows, mark acceptance partial, do not switch models |
| Chunk count below 24,296 | Documents unreadable, or a cycle failed | Read `rejected/rejected.csv`, fix permissions with `chmod -R a+rX documents`, `--clear-failures` |
| Suite prints five passes, exits 1, `PermissionError: /out/…json` | The eval image runs unprivileged and cannot write the operator-owned mount | Mount a writable directory (`chmod 777`), or write to `/tmp` and copy out |
| `interface_probe` FAILs DEGRADED on a new deployment | No model failure has been recorded yet | Force one by stopping the gateway for one question, then rerun |
| `stream_probe` reports REDACTED unproven right after an API recreate | The canary question retrieved no passage carrying a reserved value | Rerun on a warm stack; the reference run passed on the rerun with `redactions {'phone': 1}` |

## 13. Cleanup

The reference deployment is stopped, not terminated, so the evidence and the install can be inspected. To
remove it entirely:

```
ec2:TerminateInstances {"InstanceIds":["<id>"]}
```

The root volume is deleted on termination. The S3 objects stay: they are the assets the next install starts
from, and they cost cents. Do not delete the corpus bucket.

## 14. Evidence layout

```
/opt/oi-core/evidence/fresh-install/
  code-archive.sha256
  ui-archive.sha256
  corpus-archive.sha256
  compose-services.txt
  compose-ps.txt
  schema-migrations-count.txt
  litellm-version.txt
  litellm-models.txt
  keys-provision-teams.txt
  ingestion-start-utc.txt
  ingestion-progress.log
  smoke-answer.json
  probe-interface.txt
  probe-activity.txt
  probe-access.txt
  probe-stream.txt
  probe-exits.txt
  degraded-answer.json
  degradation.txt
  corpus2-check.json
  corpus2-check.txt
  counts.txt
  audit-answer-window.txt
  audit-smoke.txt
```

The copy on the workstation is the durable one.
