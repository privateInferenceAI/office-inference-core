# C6-C8 deliverable review, round 2: cold reader verification

Verdict: the round-one blocking findings are fixed. Eight of the nine items the Lead listed now work for a first-time
reader on the live deployment; one is partly fixed. No blocking finding remains. Three findings below: one misleading,
two cosmetic.

**Revision reviewed.** Branch `docs/panel-round-1` (commit `b627ac2`), re-read after the Lead's concurrent fixes
landed in the working tree. Final state checked: `manual.md` md5 `bcc419812a2a0335f117beaab57ec8da`, `runbook.md`
`45200c6089964792d572deac79ae1109`, `operator-guide.md` `4263f3c7e6a1afb67d1c41f01dffdfb6`,
`current-deployment.md` `e2300f3c1d78c719caf45ee175553e07`, read 2026-10-23 06:50-07:10 EDT. Live: build box
`i-02aba2feef08f8e7b` at 52.204.204.133 (confirmed from instance metadata), tenant 3.85.130.96. Read-only checks only.

## Verification of the nine round-one items

| Round-one item | State | Evidence |
|---|---|---|
| 1. Drift path | **Fixed** | `deploy/office/expected-state.json` is now in the repository. Manual 26.4:5913, runbook C8.3:5789 and guide 17d:3549 all end `--compare deploy/office/expected-state.json`; current-deployment.md:81 likewise. Ran the documented manual command from `/opt/oi-core` on the live box: `no drift`, exit 0. |
| 2. SSH key flags | **Fixed** | Every C6-C8 SSH command now carries `-i ~/ai-stack/ai-stack-key.pem`: manual 26.4:5910 and 26.7, runbook C6.3:5685 and C7.3:5735, guide 17a:3524, 3525 and 17b:3532 and 17c:3540-3541, current-deployment.md:71, 74. Both hosts accept that key and the commands reach the right unit and container. |
| 3. Sudo for the image scan | **Fixed** | `sudo bash scripts/scan_images.sh` at manual 26.4:5916, runbook C8.2:5781, guide 17d:3551, current-deployment.md:84. On the box `sudo -n true` succeeds and `/var/log` is writable as root, so the documented manual run no longer fails on `mkdir -p`. |
| 4. C6 runtime / workspace section | **Fixed** | Manual 26.7 "Running an agent job" (5933-5944) gives the operator path: shell into the API container, `curl http://127.0.0.1:8088/health`, then the Stage 13j job commands with a live session token, declared task and input, stopping at the approval gate; 26.8 (5946-5951) names the WS workspace, egress proxy and old host gateway as stopped on-demand host processes and says not to expose 8090-8093. Live confirms: no listeners on 8090-8093 and only eight containers run (the dev `mcp-gateway` and `oi-mailpit` are gone), so the guide's "Eight containers" at 49-51 is true again. Residual: 26.7 says "Stage 13j" inside the manual, where the section is "13j", and it stops short of pasting the two POST commands. |
| 5. Tenant audit gap | **Fixed** | Manual 26.2:5887-5890 now says refusals on the build box are in the chain and the tenant gateway's are not; manual 26.5:5929-5931, runbook C6.2:5666-5667, guide 17f:3568-3571 and current-deployment.md:39-42 repeat the gap. Live tenant gateway still has no `AUDIT_TOOL` or `DATABASE_URL`, which now matches the text instead of contradicting it. |
| 6. Registry claim | **Fixed for content, mechanism still undescribed** | The tenant file `/home/ubuntu/client/mcp_tools.json` and the copy inside `client-mcp-gateway` both now allow `[viewer, analyst, admin]` over `[public, internal, confidential, regulated]`, matching manifests/dev-cloud-workspace.yaml:206-213 and the build box's `rendered/mcp_tools.json`. Manual 26.2:5885 and runbook C6.2:5668 still say "rendered from the manifest's mcp.tools block", but no page says the tenant copy is baked into its image by `COPY mcp_tools.json` and needs an image rebuild to change. |
| 7. Loopback bindings | **Fixed** | Live tenant `ss -ltnp`: connector `127.0.0.1:8094`, Mailpit `127.0.0.1:1025` and `127.0.0.1:8025`. Manual 3a:203-206, 26.1:5872-5873 and 26.5:5926, runbook C6.3:5682-5683 and current-deployment.md:24-25 now say loopback and are correct. The external port scan is unchanged: 22, 80, 443 open, the rest closed. |
| 8. Failed-job wording | **Fixed** | Runbook C7.4:5744 now reads "went through the HTTPS gateway; its final state was failed verification, but audit row 1242 records sent: true"; `docs/reviews/sec-tls/README.md:18` had the same correction, and `docs/reviews/mcp-owned/README.md:23` now records the same for job `43dfdbbd…`. |
| 9. step0 indexer | **Fixed** | current-deployment.md:26 adds the row "Unrelated workload … step0-hello-indexer (Midnight indexer) on port 8088 … Not part of this deployment; left running"; manual 26.5:5931 names it too. Live tenant still runs `step0-hello-indexer`, so the disclosure matches. |

Also confirmed during the re-run: the build-box instance id is now `i-02aba2feef08f8e7b` everywhere
(current-deployment.md:22), `scripts/check_client_endpoint.py` now exists on the box and prints
`client-owned: https://3-85-130-96.sslip.io`, exit 0 (manual 26.4:5919), and manual 26.3:5900-5902 now scopes the
three-column tamper claim to version 4 rows.

## Findings still open

**1. The runbook and guide still state the chain-version-4 tamper property without the version qualifier —
runbook.md:5766-5770, operator-guide.md:3543-3544.** Manual 26.3 was corrected to "For a version 4 row …" and "Version 3
rows cover the approval digest only, so their prompt and completion columns were never in their hash"
(manual.md:5900-5902). The runbook and guide still say that editing approval_sha256, prompt_sha256 or completion_sha256
in a copy "makes the verifier fail", which is only true for rows written since migration 027. Measured in round one: a
version-3 row edited in the prompt column verifies with respect to its row hash (only the export file hash fails), and
version 4 is one row of 1,285. A reader of the runbook alone overstates what the chain proves. **Misleading.**

**2. Section 26 is numbered out of order — manual.md:5933-5953.** The new subsections land as 26.7 "Running an agent
job" (5933) and 26.8 "Workspace host services" (5946), but "26.6 What is still open" is at 5953, after both. A cold
reader following the sequence has to jump back, and the table of contents is wrong. **Cosmetic.**

**3. Two commands still depend on an unstated working directory, and the tenant key is never named — manual.md:5913,
5916; runbook.md:5789.** The drift and scan forms mix absolute compose and manifest paths with a relative compare file
and a relative script path, so they work only from the repository root; the guide states `cd /opt/oi-core`
(operator-guide.md:3548) and the manual and runbook do not. The tenant rotation and check commands now carry the build
box's `-i` key inline, which works, but no page says the tenant is reached with the build box's key. Both are
recoverable by a reader who guesses the repository root and the key; neither is stated. **Cosmetic.**

## What I did not re-check

The parts of the round-one report the Lead did not list were not re-verified in the timebox: the three evidence-page
mismatches (aud-hash/README cites `verify-clean.txt` and `row.json`, which are absent; sec-least cites `.txt` files
that are `.json`; ops-drift cites `drift.txt` that is `drift.json`), the "badj.ai"/"rows"/"lane" jargon, and the
runbook C6 intro still pointing at `ws-gate`, whose own README says the full C6 MCP section was not done at that gate.
None blocks an operator.
