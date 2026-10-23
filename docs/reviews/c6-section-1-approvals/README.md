# C6 Section 1: approvals

**Status:** gate passed, awaiting owner acceptance. **Date:** 2026-10-22. **Branch:** `c6/section-1-approvals`. **Scope:** the eight open C5 approval rows.

| Row | What it requires |
|---|---|
| `APR-STATE-001` | a gate waits, and does not expire into a failure |
| `APR-DECIDE-001` | each decision is recorded, and a denial carries a reason |
| `APR-RESUME-001` | approve resumes where it stopped; deny runs nothing after the gate |
| `APR-HONEST-001` | the record never claims a verified identity it cannot verify |
| `APR-IRREVERSIBLE-001` | nothing irreversible happens before approval, and it happens once |
| `APR-UPGRADE-001` | a re-attestation is appended, never an edit, and the original link is tamper-evident |
| `AUD-APPROVAL-001` | every meaningful audit column is inside the chain or covered by a content hash |
| `SEC-APR-001` | a caller who supplies their own role is refused and the attempt is recorded |

## Why Section 1 comes before the runtime

C6's `AG-GATE-001` reuses this gate, this route and this record rather than adding a second one. If the gate's
decision and its chain row are not one transaction, an agent's irreversible tool call inherits that gap. If a
refused attempt leaves no row, the runtime's security claim is a sentence rather than a control.

## What changed

- The decision and its chain row are written through the workflow engine's `PostgresUnit`, so both commit or
  neither does. The route no longer calls `jobs.decide()` and then the audit tool in a second transaction.
- A refused approval attempt is recorded before the 401 or 403 returns. The row carries the claimed role as a
  claim, never as the actor's role.
- A caller-supplied authority field is refused on the approval route, with the same guard `/ask` and `/jobs`
  use.
- `audit_events.approval` is covered by chain version 3: the writer stores `sha256(canonical_json(approval))`,
  the trigger refuses an approval without it, and the verifier checks the content against the digest.
- A `reattest` command appends a statement that links the original event by id and row hash, states the original
  assurance, and requires the limits of the new claim. It never updates the original row.
- The approval identity carries the session id, and the record keeps the unverified basis.

## Test layers

| Layer | File | What it catches |
|---|---|---|
| Unit | `tests/test_approvals.py` | record shape, assurance resolution, digest, sentence |
| HTTP | `tests/test_approval_http.py` | 200/400/401/403/409, route reachability, refusal rows |
| Durable | `tests/test_approval_durable.py` | transaction rollback, restart/resume, effect once, chain |
| Audit chain | `tests/test_audit_approval_chain.py` | v3 digest, tamper detection, column coverage |
| Re-attestation | `tests/test_audit_reattestation.py` | append-only link, original unchanged, wrong hash refused |
| Live | `tests/fixtures/approval_probe.py` | the real invoice job through the API: gate, list, approve, deny, refuse, honest, chain |

## Gate results

| Check | Result |
|---|---|
| Targeted pytest suite, real Postgres | **126 passed** in 3.8 s. Covers the approval record, HTTP route, durable transaction, audit chain v3, re-attestation, store parity, workflow chain and audit purge. |
| Full pytest suite | **1216 passed, 37 skipped** from the audit workstream's run. The retrieval half is not re-run on this workstation because it has no embeddings service; the live probes below cover the service path. |
| `workflow_probe.py` | **13 rights held.** The engine, the gate and the chain still behave after chain v3. |
| `approval_probe.py` | **7 rights held. 7 of 7.** GATED, LISTED, APPROVED, DENIED, REFUSED, HONEST, CHAIN. |
| `interface_probe.py` | **8 rights held on rerun.** The first run failed STREAM with 2 frames and no done frame; the separate stream probe and the rerun both passed. The transient is recorded, not hidden. |
| `activity_probe.py` | **8 rights held.** |
| `access_probe.py` | **4 rights held.** |
| `stream_probe.py` | **4 rights held.** |
| Audit export and verify | **334 events, chain intact**, last row hash `3df8a6c8…0558`. 177 rows are chain version 3. No external anchor, so completeness is not shown. |
| Live re-attestation | Event **334**, supersedes approval **246** by id and row hash. The original row was not edited. |
| Migrations | **24 applied**, last `025_the_approval_is_in_the_chain.sql`. |
| Register | The eight rows are `verified` with the evidence path `docs/reviews/c6-section-1-approvals/README.md`. `scripts/check_requirements.py` now recognises `retired` and `experimental`, the two statuses the register already used. |

### What the probe showed

The approval probe drove the real invoice job through the API:

- **GATED:** the job stopped at `awaiting_approval` with a digest, and no post-gate step had a row.
- **LISTED:** the gate appeared in `GET /approvals`.
- **APPROVED:** the decision returned 200, the job succeeded, `post_entry` had one row, and the effect ledger
  had one transactional and one external effect. The approval row is in the chain.
- **DENIED:** the second job failed with `denied_at_the_gate` and no post-gate rows.
- **REFUSED:** no session returned 401, a forged role returned 403, and both attempts left chain rows.
- **HONEST:** the approval record says `unverified` with basis `session_token`, and the feed sentence names no
  person.
- **CHAIN:** the export verified after all of it.

### Evidence

- Workstation archive: `/home/anthony/oi-eval-runs/c6-section-1-2026-10-22/`
  - `section1-evidence.tar.gz`, sha256 `76797c96…0e61`
  - `section1-evidence/approval_probe.txt`, `workflow_probe_section1.txt`, the four probe transcripts
  - `section1-evidence/audit-export/` the 334-event export and its manifest
  - `section1-evidence/section1-reattest.txt`
  - `pytest-section1.txt` the 126-test run
- On the box: `/opt/oi-core/exports/2026-10-22/` and `/opt/oi-core/evidence/fresh-install/`.

### Limits stated

- The first interface probe run failed its STREAM right and passed on rerun. Both transcripts are in the
  evidence. The separate stream probe passed on the same deployment.
- The live re-attestation used `--dev`, because the deployment has no identity provider until C7. The record
  says that in its actor and is a lab demonstration, not a client claim.
- `reattest --assertion` is covered by the shared actor guard and by the test suite's record path, not by a live
  signed statement.
- No five-run answer benchmark was run. Section 1 does not touch the answer path; the acceptance probes are the
  regression check.
- No external anchor exists, so the chain proves internal consistency, not completeness.

## What is intentionally not in this section

The agent runtime, the MCP gateway, the provider layer, the client app, and any answer-path change. If a
Section 1 fix starts to touch those, it stops and becomes a separate decision.
