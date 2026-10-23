# C6-C8 deliverable review, round 1: cold reader

Verdict: not yet. The tenant-side commands mostly work and match the pages. The C8 operating sections do not: two of
the five commands in manual 26.4 fail as written, and the C6 agent runtime has no operating procedure in either the
manual or the guide even though section 25 sends the reader to section 26 for it. Sixteen findings below; four are
blocking, ten misleading, two cosmetic. I read this as someone who had never seen the system and had only the three
pages plus docs/current-deployment.md.

**Revision reviewed.** Citations are against `manual.md` md5 `d28f18e6f3dcd161eaaf637c962c7b53`, `runbook.md` md5
`6e4346b038a7b9476b2935b5614af227`, `operator-guide.md` md5 `636ea769baf123ffa637d2d6f89d9ca6`, and
`docs/current-deployment.md` md5 `a1a3c8240e0d97f74dfb0558daca9cf1`, read 2026-10-23 10:30-11:05 EDT. Lines are from
that revision. Live checks ran that morning against the build box (52.204.204.133, `/opt/oi-core`) and the client tenant
(3.85.130.96). Nothing destructive was run.

## Where I could not proceed

**1. The drift command fails in all three documents, and the file it wants is not where any of them say — manual.md:5911,
runbook.md:5787, operator-guide.md:3549.** All three end with `--compare expected-state.json`, a relative path. Measured
on the build box: from `/opt/oi-core` the command exits 1 with
`FileNotFoundError: [Errno 2] No such file or directory: 'expected-state.json'`. The file exists at
`/home/ubuntu/expected-state.json` (997 bytes, written 05:31 the same morning) and from there the command prints
`no drift` and exits 0. The operator guide is the worst case because 17d:3548 explicitly does `cd /opt/oi-core` first,
so its own command cannot succeed. No page says the expected copy lives in the operator's home directory, or how it was
made. The script's own docstring (scripts/deployment_state.py:7-12) says only "keep the expected copy in the repository",
and it is not in the repository. A reader cannot follow C8.3 as written. **Blocking.**

**2. Every C6-C8 command that opens an SSH session fails from a workstation as written — operator-guide.md:3524, 3525,
3532, 3540, 3541; manual.md:5908.** All use bare `ssh ubuntu@3.85.130.96` or `ssh ubuntu@52.204.204.133`. Measured
from this workstation, which holds the documented project key at `~/ai-stack/ai-stack-key.pem` and has no
`~/.ssh/config` entry: `ubuntu@3.85.130.96: Permission denied (publickey)` and the same for the build box; adding
`-i ~/ai-stack/ai-stack-key.pem` connects to both. Manual section 7:343 shows the key on the build-box command, so the
pages contradict each other, and no page says how the tenant is reached at all (which key, whose account, which source
address). Stage 17a is the first thing Stage 17 asks you to run. **Blocking.**

**3. The image-scan command in manual 26.4, runbook C8.2 and guide 17d needs root and does not say so — manual.md:5914,
runbook.md:5779, operator-guide.md:3551.** `bash scripts/scan_images.sh /var/log/oi-image-scan` as the `ubuntu` user:
`/var/log` is not writable by that user, `/var/log/oi-image-scan` does not exist, and the script starts with
`mkdir -p "$OUT"` (scripts/scan_images.sh:8), so it fails with a permission error before scanning. The systemd unit does
not set `User=`, so the weekly timer runs as root and works; the documented manual run does not. The script also needs
the current directory to be the repository, and no page says that. **Blocking.**

**4. The C6 agent runtime is named but never documented; section 25 sends you to section 26 and section 26 does not
contain it — manual.md:5757 against manual.md:5861-5933, operator-guide.md:15-19 and 3512-3575.** Section 25's C6 row
reads "The agent runtime and the MCP gateway | Built and verified. Section 26". Section 26 has 26.1-26.6 and covers the
tenant, the gateway, chain version 4, commands, caveats and open rows: no agent runtime, no workspace service, no egress
proxy, no child tasks, no 8090-8093. The operator guide's current-state banner says the C6 additions are "in Stage 17 at
the end of this guide"; Stage 17 covers the tenant and the C8 rows only. Runbook C6.1:5646-5654 is one paragraph and
points at `tests/test_agent_runtime.py` and `docs/reviews/ws-acceptance/`, not at the gate that actually holds the
AG-* rows, `docs/reviews/c6-section-2-runtime/GATE.md` (as `docs/requirements.yaml` does). A reader cannot start, stop,
watch or diagnose the runtime from any page. On the live box it is four host processes, not containers, which no page
mentions: `services/workspace/service.py` on 0.0.0.0:8090, `services/egress/proxy.py` on 0.0.0.0:8091, the dev
connector on 0.0.0.0:8092 and a dev gateway on 0.0.0.0:8093. **Blocking.**

## Where I would have acted wrongly

**5. "Eight containers" is not the live state, and the containment sentence in manual 26.1 is false on the box —
operator-guide.md:49-51, manual.md:187-193 and 5868-5869.** The guide says "Eight containers run all the time on the build
box"; `docker ps` shows eleven that morning, including `mcp-gateway` and `oi-mailpit`, plus one stray container
(`frosty_booth`). Manual 26.1 says the build box "publishes no port"; `docker ps` shows `oi-mailpit` publishing
127.0.0.1:1025 and 127.0.0.1:8025, `ss -ltn` shows four host processes listening on 0.0.0.0 at 8090-8093, and manual
section 3:190-193's "the only thing listening is the door you came through" is therefore not true either. The security
group does block the four ports from outside, so the exposure is small; the statements are still wrong, and a reader
who checks them stops trusting the surrounding claims. **Misleading.**

**6. "Each refusal is written to the audit chain" is not true of the gateway section 26 describes — manual.md:5887-5888.**
The tenant gateway container has no `AUDIT_TOOL` and no `DATABASE_URL` (`docker exec client-mcp-gateway env`:
`CONNECTOR_URL`, `MCP_GATEWAY_TOKEN`, `MCP_SESSION_SECRET`, `MCP_TOOLS_FILE`, `MCP_HOST`, `MCP_PORT`), and
`gateway.py:196-207,134-139` returns silently when the tool is unset and swallows every audit exception. So a refusal
through the public HTTPS gateway is written nowhere. The refusal rows the C6 evidence cites (1070-1091, runbook
C6.2:5668-5671) came from the dev gateway on the build box, which has `AUDIT_TOOL=/app/audit.py` and a database; they do
not demonstrate the tenant behaviour the manual claims. **Misleading.**

**7. The tenant's tool registry is not rendered from the manifest, and it disagrees with the repository's — manual.md:5885,
runbook.md:5662.** The manifest's `mcp.tools` block (manifests/dev-cloud-workspace.yaml:206-213) renders to
`allowed_roles: [viewer, analyst, admin]` and `data_classes: [public, internal, confidential, regulated]`; the build
box's `rendered/mcp_tools.json` matches it. The tenant runs a different file: the Dockerfile does
`COPY mcp_tools.json /app/mcp_tools.json`, and that copied file (`/home/ubuntu/client/mcp_tools.json`) allows only
`viewer` and only `internal`. Nothing in the three pages describes how the tenant registry is produced or updated, so a
reader who edits the manifest and re-renders changes the build box and not the gateway the client's traffic uses. It also
means the manual's "a role the tool does not allow" and "a data class the tool does not accept" refusals (5886-5887) can
only be provoked by values the deployed registry happens to reject (analyst/admin roles; confidential/regulated classes),
and the gateway's egress check (gateway.py:110-115) can never fire on the tenant because internal is not one of the two
classes it gates. **Misleading.**

**8. The "editing any of those three columns" claim is only true for rows written since the migration — manual.md:5895-5900,
runbook.md:5759-5770, docs/reviews/aud-hash/README.md.** I exported the chain, found a chain-version-3 row with a
`prompt_sha256` (id 622), set it to 64 zeroes, and verified: the only failure was
`the file hash does not match the manifest`; there was no `row_hash does not match the row contents`, which is the
sentence the pages promise. The row hash for versions 1-3 covers thirteen fields plus redactions plus the approval digest,
not the prompt or completion hash (db/migrations/027...sql, services/audit/audit.py:828-879). The live chain had 1,285 rows
and version 4 is new, so almost every row behaves like row 622: the manifest catches an accidental edit, but a writer who
regenerates the manifest (the same export job that wrote it) is not caught. Editing a version-4 row does produce the
promised message (row 1250). The pages should say "rows written since migration 027". **Misleading.**

**9. The recorded containment of the connector and Mailpit is not what is running — manual.md:203-206 (section 3a) and
current-deployment.md:24-25 against the live tenant.** Both say loopback: connector 8094, Mailpit 1025. Measured with
`sudo ss -ltnp` on the tenant: `0.0.0.0:8094` (python3, the connector), `*:1025` and `*:8025` (Mailpit, which runs with
`network_mode: host`). The security group closes 8094 and 8025 from outside, so they are not internet-reachable; the
sentences still state a socket-level restriction the services do not have, and section 3a calls it out as the tenant's
"Exposure". A reader auditing C7 from the docs would mark that check as passed. **Misleading.**

**10. The evidence the runbook points at says C6 is not finished — runbook.md:5641-5644, 5656-5671.** C6 opens "C6 is
built and verified. The gate and evidence are in docs/reviews/ws-gate/ and the MCP gateway review is in
docs/reviews/mcp-gateway/". `ws-gate/README.md` says in its second paragraph "This is a phase gate for the workspace
build, not a claim that the full C6 MCP section is done", then lists registry/session policy, per-connector credential
rotation, data-class egress, ownership checks and SEC-MCP as open. `mcp-gateway/README.md` says "MCP-EGRESS-001 and
MCP-OWNED-001 remain open", while `mcp-egress/README.md` and `mcp-owned/README.md` say both are verified and
`docs/requirements.yaml` marks them verified. Three of the four pages are dated the same day and none says which is
current. A cold reader following the citations concludes C6 is partially open. **Misleading.**

**11. The runbook's own test command cannot run on the box — runbook.md:5666 against 4680-4682 and 5403-5404.** C6.2 says
`python3 -m pytest tests/test_mcp_gateway.py -q`. The runbook twice warns, in its own words, that the bare `python3` has
no pytest: "the system Python on the reference machine has no pytest, so the bare form fails with `No module named
pytest` before it runs a single test." Measured: `python3 -m pytest --version` on the build box returns exactly that, and
the only venv present is `~/oic-venv`, which also has no pytest. Every other test command in the runbook uses
`~/venv/bin/python`, which does not exist on this box now either. The step the page gives cannot be run without first
building a Python environment no C6-C8 page tells you to build. **Misleading.**

**12. Manual section 25's "Section 26" pointers promise coverage section 26 does not provide — manual.md:5757-5759.**
C6 points to section 26 for the agent runtime (absent, finding 4). C7 points to section 26 for "least privilege" and
"Object Lock"; 26.1-26.6 mention neither. The least-privilege support role was created, proved and then deleted
(docs/reviews/sec-least/README.md:22-28), and OPS-SUPPORT-001 (SSM-only cutover) is open in current-deployment.md:106, but
section 25's "Built and verified" and current-deployment.md:12 ("C7 ... least-privilege support role") read as though a
support role exists now; a reader would look for a role that is not there. Object Lock is documented only in
docs/contracts/audit-evidence-retention.md and aud-lock/README.md, which no C6-C8 page links for the operator.
**Misleading.**

**13. Stage 17b's verification and failure remedy cannot be performed from the page — operator-guide.md:3530-3536,
manual.md:5908, runbook.md:5731-5736.** The rotate command matches the script's four flags and the script exists, imports
boto3 (1.34.46 on the tenant) and sudo is passwordless, so the happy path should work. But the page then claims "The old
token returns 401 and the new token returns 200" with no command, no URL and no way to hold the old token after rotation,
and its `Address already in use` remedy is "stop it and restart the unit" with no command to find or stop the process.
Runbook C7.3 gives the same claim with a job and audit row but still no reproduction. **Misleading.**

**14. The endpoint ownership check is unexplained and its script is missing from the live checkout — manual.md:5917.** The
line is `python3 scripts/check_client_endpoint.py https://3-85-130-96.sslip.io` with no expected output. The script
(scripts/check_client_endpoint.py) only compares the URL's hostname suffix against `officeinference.com` and
`privateinferenceai.com`; it does not contact the endpoint, so "endpoint ownership check" overstates it. On the build box
the file does not exist at all (`python3: can't open file '/opt/oi-core/scripts/check_client_endpoint.py'`), because
`/opt/oi-core` is not a git checkout and its copy is older than the repository (the box's `manual.md` md5 is
`6809e0a462215acb8cef5262f29e1f97`, not the reviewed one). No page says how the deployment's scripts are kept current.
**Misleading.**

**15. "badj.ai", "rows" and "lane" are used as if the reader knows them — manual.md:198, 5871; runbook.md:5641-5757;
operator-guide.md:15-19.** No page defines "badj.ai"; it appears in section 3a, section 26, Stage 17 and the current
deployment page as the client tenant's name, and a reader has to guess whether it is the client, the account alias or a
test fixture (the account has no alias; the instance is tagged `midnight`). "Rows" (C6/C7/C8 rows, "the side that answers
the C6 and C7 rows", "four C8 rows are verified") is never linked to the register that actually lists them,
`docs/requirements.yaml`; the only pointer in the three pages is runbook.md:678, a C1 section. "Lane" is used in 26.2
("with the lane off/on") without a definition or a pointer; the nearest explanation is the egress sections much earlier.
**Cosmetic.**

**16. The reviewed pages are not the pages on the machine.** The build box's copies are older than the repository
(`manual.md` md5 `6809e0a4...`, `runbook.md` `a808e10b...`, `operator-guide.md` `79689e28...`) and `/opt/oi-core`
is not a git repository, so "the checkout on the box" that current-deployment.md:75 and manual 26.4 name cannot be
identified or updated by a documented step. Every command in this review had to be typed from the repository copy. This
is the mechanism behind findings 1, 3 and 14, and C8.3's drift report cannot see it because it compares only compose
image pins and the manifest hash. **Cosmetic.**

## What worked

Every live check that matched, matched exactly:

- **Public surface.** `https://3-85-130-96.sslip.io/health` returns `{"status": "ok"}`; `http://` returns 308 with the
  HTTPS Location; ports 22, 80 and 443 open, and 3000, 4000, 5432, 8025, 8080, 8088, 8090, 8094 closed from outside.
- **Tenant.** `client-connector` is active, `client-mcp-gateway` and `caddy` containers are up, the gateway listens on
  127.0.0.1:8080 and returns `{"status":"ok"}`, requires a bearer token on `/tools` (401 without), mounts
  `connector.token` read-only at mode 600, and Caddy's Caddyfile proxies `https://3-85-130-96.sslip.io` to
  `127.0.0.1:8080`. Mailpit held ten captured messages addressed to `anthony@badj.ai`, so "captures, does not deliver"
  is demonstrated. The contract's Object Lock mode and period match `docs/contracts/audit-evidence-retention.md`.
  The HMAC secret matches on both sides (equal SHA-256 of the values, checked without printing them), though the tenant
  container spells it `MCP_SESSION_SECRET` rather than `OI_MCP_SESSION_SECRET` (manual 26.1:5881 says "on both sides").
- **Audit chain.** Documented export and verify commands (runbook C8.1:5766-5767, guide 17c:3540-3541) run and print
  `ok: 951 event(s), chain intact`, with the honest "no external anchor" note. `max(chain_version)` is 4 over 1,285
  rows. The verifier's message for a tampered version-4 row is exactly `row_hash does not match the row contents`.
- **Drift command itself.** From `/home/ubuntu`, where the expected file is, it prints `no drift` and exits 0.
- **Tenant instance facts.** Account 426063972668, instance `i-0b6aa00095ae40e54` running, t3a.xlarge, public IP
  3.85.130.96, security group ingress 22/80/443 matching the pages (though all three are open to 0.0.0.0/0, which no
  page says; the build box by contrast restricts 22 to one address, manual 7:346-347).
- **Image scan and literals.** LiteLLM reports 1.104.2, matching runbook C8.2; `oi-image-scan.timer` is installed with
  next run Mon 2026-10-26; `/var/log/oi-image-scan` has never been created because the timer has not fired.
- **The rotate script.** Its flags match all three copies of the command; boto3 is installed on the tenant; sudo needs no
  password; the token and env files are mode 600.

## What I could not test

- The agent runtime and workspace pack: starting jobs, child tasks, the approval gate through the tenant gateway. Running
  a job would write audit rows and send mail, so I did not.
- Account 878 (the audit bucket, the build-box instance role) — the AWS tools available in this session act only in
  account 426.
- The suite count in current-deployment.md:16 (1,355 passed, 1 skipped) or the "1,292 passed" in
  docs/reviews/c6-section-2-runtime/GATE.md:51; no runnable Python environment is on the box and the gate report still
  says "awaiting owner acceptance", so I cannot tell whether C6 has passed its owner gate.
- Whether the tenant's instance role can read the rotation secret; the script and its files are there, but I did not
  rotate.
- One unexplained live object: the tenant also runs `step0-hello-indexer` (`midnightntwrk/indexer-standalone:4.3.3`,
  publishing 0.0.0.0:8088) and the instance is tagged `midnight`; no page mentions either. It may be another project's
  container on a shared lab box, but a reader checking "the client tenant runs the MCP gateway, the email connector,
  Caddy and Mailpit" will find more than that and no way to tell allowed from not.
