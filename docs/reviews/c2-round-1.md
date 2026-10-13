# C2 deliverable review, round 1

Three agents, run against the six C2 operating pages in `docs/manual.md` (13a to 13f) and the code behind them.
The process is in `AGENTS.md` under "What finished means". This file holds the findings; `docs/review-log.md`
holds the summary row.

The panel was: an **executor** which ran every documented command on the live box and also ran four adversarial
probes, a **cold reader** which read only `docs/manual.md` and could not check anything, and a **fidelity
checker** which read the claims against the source.

## Cold reader

Verdict: **no. A competent stranger can run the C1 manual and cannot run the C2 half of it.**

What they could do: stand up and check the stack, mint and probe role keys, apply migrations, write and read
audit events, export, anchor, verify, ship evidence, purge on retention, and read the manifest field table.

### Blocking

1. **The documents root has no host path.** 13a says the worker walks "the documents directory" and never
   resolves `/documents` to a host path. Section 23, "What lives where on the machine", does not list it. The
   reader cannot put a file where the worker will see it.
2. **`eval` and `retention` are invoked and never introduced.** Neither is in section 3's "what runs where",
   section 9's expected `ps`, section 20's service convention, section 22's command table or section 23's paths.
   `retention` is the entire 13f page and will not start for a reader.
3. **`staff` and `executive` are defined nowhere.** Used throughout 13b to 13e and in every measurement table.
   No key, no model, no budget, and the role map has no stated location or format. Section 12 and the glossary
   describe a different set of four roles, so the manual uses one word for two things.
4. **No procedure for asking a question against the client's documents.** Six sections about reading documents
   and no request shape or entry point. The only paths are fixture probes.
5. **The C2 manifest fields are outside the field table that claims to be authoritative.** `access.denial` and
   `access.egress.frontier_classes` are absent from section 16. Worse, the second overlaps
   `lanes.frontier.data_classes_allowed`, which section 16 says is read by nothing at call time.
6. **No procedure for adding a migration**, though section 13 tells the reader to write one.
7. **No way to onboard a client's corpus.** Section 19's six steps never mention documents, folders, formats, or
   that the folder name is the access label.

### Wrong, and these mislead rather than stall

8. **The front matter and the closing paragraph still say C2 does not exist** while six sections describe it.
9. **Section 16 says nothing redacts on the way out** against 13e's working redactor and its rendered variable.
10. **The glossary's chained-column list is incomplete.** 13e adds `chain_version` 2 covering four more columns,
    so the chained set is not thirteen and the named four are not the whole exclusion list. 13f's argument that
    stored content cannot be deleted rests on that list and does not follow from it.
11. **Retention scheduling.** Section 15 says "nothing schedules it" and 13f documents a policy mechanism whose
    example is labelled "what a scheduled purge would do", with no statement of which is true.
12. **The measured corpus is not the documented one.** 13a's inventory is four files; 13b to 13d measure against
    ten questions, a superseded fee schedule and an executive memorandum that are not in it.

### Confusing

13. Section 22's command table contains no C2 command, and the contents page does not list 13a to 13f.
14. Nine configuration knobs are named with no default, no manifest field and no location: `RERANK_DEPTH`,
    `INGEST_MAX_FAILURES`, `DUPLICATE_MIN_SHARED_CHARS`, `FRONTIER_CLASSES`, `EGRESS_REDACTION`,
    `ACCESS_DENIAL_MODE`, `PAYLOAD_VERSION` and others.
15. **The reranker is a paid subscription the manual never tells you to establish**, so on a second deployment
    it is silently absent and the reader finds out from a 403.
16. The confidence gate is listed in 13c's "seven checks" table as a live control while a paragraph further down
    says it refuses nothing on the retrieve-and-verify path. **This one was a real defect and is now fixed**:
    the retriever was dropping `parse_score` at the last step.
17. Section 9's health sample has two rows and does not know about `ingestion`, and neither the vector index nor
    the lexical index is in any inventory.

### Nitpick

18. Section 5 and section 22 name different sets of four rendered files.
19. `--store-payload` is referenced twice and never tabulated.
20. Two sections present the same `-s` versus `-sS` observation as different findings.
21. 13c's "three rules" is a list of four.
22. 13a to 13f are numbered as subsections of 13 but sit after section 20, absent from the contents.

## Fidelity checker

Read 13a to 13f against the services, the migrations, the schemas, the renderer, the compose file, the register
and the runbook. Non-GPU checks only. Method: reading, two local test runs, one CLI invocation with a stubbed
connection.

Verdict: **no, not yet, and narrowly.** 13b, 13d and 13e hold up almost as written, and so do the bounds 13c and
13f carry for entailment, the ACL false negative and the figure-check ceiling. But 13f's time-based retention
cannot be run and its recorded block is a fixture's output presented as the CLI's, its destruction page says
stored payloads are cleared when the code keeps them, 13a points at a 404, and 13c's confidence-gate bound
described the opposite of what the code did.

### Blocking

23. **The confidence gate was dead on the production path.** `citations.verify_claim` reads
    `getattr(passage, "parse_score", None)` and continues when it is None. `Passage.from_payload` copied
    `is_ocr`, `page_is_nominal`, `table_id` and `row_index` but **not `parse_score`**, and `ingest.py` does write
    it into the payload. The value was in the store, in the payload, in the dictionary the retriever held, and
    dropped at the last step. **Fixed this round**, with a test.
24. **`--dry-run` is not an argument**, and `--enforce` dereferences `args.dry_run`. So the documented rehearsal
    was impossible and the real run died with `AttributeError`. The recorded block under those commands is
    `tests/fixtures/retention_policy_acceptance.py` output. **Fixed this round.**
25. **`policy.py` is not in the retention image.** `--set-retention` and `--enforce` die with
    `ModuleNotFoundError` on a fresh build, because the Dockerfile copies `retention.py cli.py` and not the
    module the CLI imports. **Fixed this round.**

### Wrong

26. **13a sends the reader to `/v1/capabilities`, which the parser does not serve.** It serves health, ready,
    convert, poll, result and confidence. The authority is its own 422. The same stale advice was in the manual,
    the runbook and `ingest.py`'s error text. **Fixed this round** in all three.
27. **13c says the verifier's false-negative rate is published. Nothing publishes it.** The research says it
    must be, which is an obligation, and the sentence turned that into a fact. **Fixed this round.**
28. **13a's first-cycle output is shown under `docker logs`, but the container runs `--json`** and prints a JSON
    object. The prose lines appear only when the cycle is run by hand.
29. **Nine knobs are named nowhere**: `EMBED_CONTRACT_ALLOW_CHANGE`, `EMBED_CONTRACT_REASON`, `RERANK_ENABLED`,
    `RERANK_DEPTH`, `RERANK_BUDGET_MS`, `RERANK_MODEL`, `INGEST_PARSE_TIMEOUT`, `--documents-root`. An operator
    who has decided to move the embedding model has no documented path out of the refusal.
30. **The register's `is_ocr` gap was not real.** `lexical_search` returns ids only, and every id the dense arm
    did not return has its payload fetched from Qdrant with `with_payload: True`, so `is_ocr` reaches the passage
    on both arms. The column would be redundant. **Corrected this round** in the register, the runbook and the
    probe that printed it.
31. **Quoted output in 13b and 13c is abridged and once relabelled**, presented as real output.
32. **Two licence assertions cite nothing**: `docker-compose.yml` line 48 (TEI, Apache-2.0) and line 118
    (Docling, MIT). `docs/search-architecture.md`'s licence section gives no URLs either. The URLs exist in
    `docs/research/search-2026-10/`. Reported, not researched: the checker did not verify either licence.

### Sound, so nobody re-checks it

13a's chunk counts and the 1183 first-run figure; acl is the top-level folder; remove-before-write; dead-letter
at `INGEST_MAX_FAILURES` 2; `PAYLOAD_VERSION payload-v4` and `chunker-v1`; the contract fields. 13b's RRF k=60,
tie-break, arm counts, `OVERFETCH 3`, the ACL clause in both arms, `RERANK_DEPTH` 50, the no-silent-fallback
reporting. 13c's seven-check order, supersession rule, normalisation, splitter, `check_page`, the figure checks
and their ceiling. 13d's role map and refusal, the denial modes, the dev cloud manifest declaring `counted`, the
ACL table and the severance false negative, the registry commands. 13e's two rendered variables, the four
refusals and the fail-closed direction, the pattern list, `unverified_card_shapes`, the four audit columns and
`chain_version` 2. 13f's purge store list, per-store results, hold-before-any-store, deployment-scope holds, age
from `ingested_at`, no policy destroys nothing, stores before file, and the destruction record's self-hash.

## Executor

Ran every documented command on the live box plus the four adversarial probes. All state changes disclosed and
reverted where revertible; no bucket touched; instance never stopped.

Verdict: **no, not from the documentation alone, and the gap is concentrated in 13f.** 13a to 13d run and
behave, and the uid substitution works in both directions. But 13f's two consequential command groups cannot be
executed and its central safety rehearsal does not exist. The executable core is sound; **the failure is that
exactly the irreversible operations are the ones the manual cannot demonstrate.**

### Blocking

33. **The retention image is missing `policy.py`** (same as 25, found independently).
34. **`--dry-run` does not exist** (same as 24, found independently).
35. **A held purge's refusal is not recorded.** `purge` returns at the hold check, before the
    `insert into purge_records`, and writes no audit event. Measured: `purge_records` 2 before and 2 after, 0
    rows naming the document, `audit_events` 40 before and 40 after. The code comment says the row exists and
    the manual says the refusal is recorded. **Both were false. Fixed this round.**

### Wrong

36. **13e's audit record is stored as `redacted`, not as a hash.** The manual says hash and
    `content mode stored the payload as: None`. `manifests/dev-cloud.yaml` sets `internal: redacted` and the
    probe uses that class. The security claim survives, the sentence describing the run is false.
37. **13d's ACL probe shows the library's default, not the deployment's.** It prints `denied (configured
    denied)` with no count, though the manifest and `rendered/env` say `counted`, because the command passes no
    `ACCESS_DENIAL_MODE`.
38. **13b's eval output is stale**: the fixture holds 10 questions in 8 categories, the manual quotes 8 in 5
    with different score lists. The rates and the no-separation conclusion still hold.
39. **13c's figures output is stale** and omits two substantive results.
40. **The vector store's per-store count is a constant.** A purge of a document that does not exist reported
    `{"vector": {"deleted": 1}}` against a collection that stayed at 1131 points. **Fixed this round.**

### Confusing

41. **The documented hold sequence leaves a deployment-wide hold in force.** `--release-hold 1` releases
    nothing because the ids are 3 and 4, and exits 1.
42. **The 13d examples name documents that do not exist and succeed anyway.** No existence check on
    `--supersede` or `--distinct`.

### The four probes

1. **No leak.** Nine staff queries, direct, unique-term and lexical. Every result company-only, `withheld=0`,
   none of nine restricted strings present. The restricted chunk has `identifiers = {}`, so the lexical arm
   structurally cannot return it.
2. **Page 29 is real but ranks [4].** Ranks 1 to 3 are the page-39 prior-year wage bases. `check_page` catches a
   wrong page when given the passage's own range; nothing calls it outside tests. **An answer built from rank 1
   states the wrong year's figure from the same document and no check catches it.**
3. **The known result flipped.** The current 2026 schedule now ranks first at 0.6029 against the superseded
   2025 at 0.5992, marked `is_current=False`, and a claim resting on it alone is refused `superseded`.
4. **Refused and untouched.** sha256, manifest row, lexical row and vector point all unchanged; hold released.
