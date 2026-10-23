# C6–C8 docs panel, round 2: fidelity checker

**Date:** 2026-10-23. **Reviewer:** fidelity checker. **Round:** 2, verification only, branch
`docs/panel-round-1`. Scope and method: docs/reviews/acceptance-plan-post-c5.md. This round checks whether the
round-one findings (docs/reviews/c6-c7-round-1-fidelity.md) are fixed. No code or doc was changed other than this
file.

**Live targets:** build box `i-02aba2feef08f8e7b` at 52.204.204.133, repo /opt/oi-core; client tenant
`i-0b6aa00095ae40e54` at 3.85.130.96, public https://3-85-130-96.sslip.io. Checks were read-only; the only
writes were to /tmp.

**Verdict: 9 of 10 fixed, 1 partially fixed.** Every blocking finding is fixed and verified by running the
documented command. The one residual is F6: the evidence READMEs were corrected but runbook C7.4 still describes
the TLS job without its failed final state.

| # | Round-one finding | Status | Evidence |
|---|---|---|---|
| F1 | Drift command cannot run (no `expected-state.json`) | **Fixed** | `deploy/office/expected-state.json` is committed (`git ls-files` shows it) and all four commands now use it: current-deployment "Operate it", manual §26.4, runbook C8.3, OG Stage 17d. On the box: `cd /opt/oi-core && python3 scripts/deployment_state.py --compose /opt/oi-core/deploy/office/compose/docker-compose.yml --manifest /opt/oi-core/rendered/manifest.json --compare deploy/office/expected-state.json` → `no drift`, exit 0. |
| F2 | Endpoint ownership check not in the deployment checkout | **Fixed** | `/opt/oi-core/scripts/check_client_endpoint.py` present (synced 2026-10-23 10:44); `python3 scripts/check_client_endpoint.py https://3-85-130-96.sslip.io` → `client-owned: https://3-85-130-96.sslip.io`, exit 0. |
| F3 | Image-scan command cannot write its root-owned output dir | **Fixed** | All four pages now show `sudo bash scripts/scan_images.sh /var/log/oi-image-scan` (manual §26.4, current-deployment, runbook C8.2, OG Stage 17d). The output dir is still root-owned; sudo is the correct control. |
| F4 | Build-box instance id stale | **Fixed** | current-deployment "Where the pieces run" and manual §3 now name `i-02aba2feef08f8e7b` at 52.204.204.133; the stopped `i-096bebd46308d1673` is no longer claimed as the live box. The rest of the §7 stop/start examples also use the new id. |
| F5 | "Loopback only" wrong for connector and Mailpit | **Fixed** | Tenant bindings changed: connector `127.0.0.1:8094`, Mailpit `127.0.0.1:1025` and `127.0.0.1:8025`, gateway `127.0.0.1:8080` (`ss -ltnp`). The docs' "Loopback only" rows are now true, and manual §26.5 adds the explicit sentence that the binding is loopback and the security group is the outer control. |
| F6 | C7 TLS evidence job presented without its failed final state | **Partially fixed, still open** | Evidence files are corrected: `docs/reviews/sec-tls/README.md` now says "its final state was failed verification, but audit row 1242 records `sent: true`"; `docs/reviews/mcp-owned/README.md` says the same for job `43dfdbbd…`. **But docs/runbook.md C7.4 (line 5744) still reads "A job sent through the HTTPS gateway; audit row 1242." with no failed state.** The live job `57309daac87244d9acea35f4f65dbffb` is still `failed / verification`. One sentence in C7.4 fixes it. |
| F7 | Migration 027 absent from deployment migrations and ledger | **Fixed** | `/opt/oi-core/db/migrations/027_the_prompt_and_completion_are_in_the_chain.sql` now present (5,148 bytes); `schema_migrations` newest row is `027_the_prompt_and_completion_are_in_the_chain.sql`. The docs' claim that 027 added chain version 4 is now true on the deployment. Also newly disclosed, consistent with round-one method: current-deployment and manual §26.2 now say the tenant gateway's refusals are not written to the Office chain (no audit route). |
| F8 | Build box runs undocumented extra services | **Fixed** | `docker ps` on the build box now shows exactly 8 containers: `api app embeddings ingestion litellm parser postgres vector`. `mcp-gateway` and the build-box Mailpit are gone; no listeners on 8090–8093, 8025 or 1025 (`ss -ltnp` → NO_EXTRA_LISTENERS). Manual §26.7/26.8 documents the host services as on-demand and stopped. |
| F9 | Session secret named differently on the two sides | **Fixed** | Manual §26.1 and runbook C6.4 now say explicitly: `OI_MCP_SESSION_SECRET` in the API, `MCP_SESSION_SECRET` in the tenant gateway, names differ and the value must match. |
| F10 | Operator guide cross-references Stage 17 for a model list it lacks | **Fixed** | OG "What you need" now says "The manual's section 3 lists them". The tenant's unrelated `step0-hello-indexer` is now documented (current-deployment "Unrelated workload" row; manual §26.5). |

## Additional verifications this round

- **Registry sync (lead fix list).** The tenant registry equals the rendered registry byte for byte:
  `diff /opt/oi-core/rendered/mcp_tools.json <tenant /home/ubuntu/client/mcp_tools.json>` → no output; roles
  `viewer, analyst, admin` and all four data classes match. The §26.2 claim "reads the tool registry rendered
  from the manifest's mcp.tools block" is supported on the tenant side as well as the build box.
- **Tenant exposure unchanged and still correct.** SG ingress 22/80/443; external scan still closed on
  3000/4000/5432/8025/8080/8088/8090/8094; HTTPS health 200 `{"status":"ok"}`, HTTP 308, Let's Encrypt SAN
  `3-85-130-96.sslip.io`. Loopback rebinding did not change the public surface.
- **Chain v4 live.** Same as round one: v4 trigger and canonical function present, v4 rows from id 1250, old
  rows keep versions 2/3, cited rows 1070–1242 present. Migration 027 is now in the ledger, so this is
  reproducible from the deployment checkout.
- **No regressions observed** in the four in-scope docs beyond F6; current-deployment, manual §3a/§26, runbook
  C6–C8 and OG Stage 17 otherwise read consistently with the live box and tenant as verified above.

## Not re-run in this timebox (unchanged from round one)

- Full test suite: round-one count check (1,356 collected = 1,355 + 1 skipped) stands; no full run was made.
- Live connector-token rotation replay and the EIP inventory in account 426; both rest on the recorded
  evidence in docs/reviews/sec-secret and are unaffected by the round-one fixes.

## Required before sign-off

1. Fix F6 in docs/runbook.md C7.4: add "the job's final state was failed verification" to the row-1242
   sentence, as the two evidence READMEs already do. Nothing else is needed.

**Round-two verdict:** a competent operator can now run every documented C6–C8 command checked here; nine of
ten round-one findings are demonstrably fixed. F6 is a one-line documentation correction.
