# The retrieval architecture in the field

**A support engineer's walk-through of the proposed design, installed in a six-person accounting firm in rural Connecticut, on one box, supported by phone for three years.**

Written 2026-10-11. This is an adversarial operational review, not a second opinion on retrieval quality. The retrieval choices are mostly sound and several are better than what production systems do. The question here is whether the thing survives contact with a real office: an office manager who calls at 4:55pm on 14 April, a scanner that drops 300-page PDFs onto a Windows share, a power cut in a thunderstorm, and nobody on site who can open a terminal.

Evidence labels used throughout:

- **[measured]** a number produced by instrument, either in the sibling reports in this study or in a published benchmark.
- **[primary]** read from the project's own documentation, source, or licence file.
- **[vendor claim]** the project's own assertion about its behaviour, not independently checked.
- **[inference]** my reasoning on top of the above.
- **[unverified]** a figure I could not check, stated so it does not get quoted as fact.

---

## 0. The verdict, up front

The retrieval design is the least of the problems. Dense plus lexical plus rank fusion at this corpus size is correct, cheap, and unexciting, which is what you want. Page-anchored provenance and the mechanical citation verifier are genuinely good ideas and I would ship both before anything else in the plan.

What I would change is the shape of the product around the retrieval. Three findings dominate.

**First, ingestion is the install, and the install is measured in weeks, not days.** Docling's own technical report measures 3.1 seconds per page on an 8-thread x86 CPU for a mixed corpus, and 13 seconds per page where OCR engages [published measurement, Docling technical report]. A CPA firm's archive is mostly scans. My arithmetic on their measured number says a 200,000-page archive costs 15 to 30 days of a pegged 16-core box, not the "nine days" the build brief assumes for a million pages. The office build has no cloud escape hatch for bulk indexing, so this is a multi-week on-site job during which the box is also supposed to answer questions. Start with one folder, not the archive.

**Second, the headline feature is the part that needs the GPU we said we would not need.** Verification is compute-bound on CPU and does not batch: 1.58 s per claim for a 184M model on one core, flat at batch 8 and batch 16, and roughly 6 to 8 s per claim for the 770M MiniCheck on one idle core by extrapolation [measured, sibling report 08]. A twelve-sentence answer needs roughly 20 seconds of wall clock on 4 dedicated cores, and the same cores are also running Docling, the embedder, and the answering model. If the office box has a GPU anyway (and per the company's own product spec it does, since 14B Q4 is spec'd on an A10G 24GB), then this architecture's CPU-only framing describes the cloud build and not the office build. Say that out loud rather than discovering it in the first client's office.

**Third, the plan verifies the wrong thing.** Every layer checks whether the answer matches the source. Nothing checks whether the source is the right source, or whether the source was read correctly. The two highest-probability wrong answers in an accounting office are a real number from a superseded document, and a real number from a misread scan. Both pass a mechanical verifier and both pass an entailment model, because the passage really does say what the answer says. The verifier makes them look certified. Section 2.1 and section 3.1 are where I would spend the next engineering day.

The three-year support answer follows from those. Reduce the service count, make the failure visible without a terminal, and put a person on site twice a year. The In-Office entry tier at 12 percent year-one margin (their own figure, build brief section 2) cannot fund a rural Connecticut support model where every visit is a half-day of driving. Either the retainer includes the visits or the tier does not ship in this geography.

---

## 1. The site, before any of the software matters

The proposal is a document about retrieval. The install is a document about a closet.

### 1.1 Where the box goes, and what that decides

A six-person firm does not have a server room. The box goes in the closet with the router, or on a shelf behind the office manager's desk. That decides four things the architecture does not mention.

**Heat and dust.** A CPU-pegged ingest job runs for weeks at 100 percent. In an unventilated closet in July, the box throttles or shuts down. The failure is not "the AI is slow", it is "the AI stopped at 11am and nobody told us". [inference]

**Power.** Rural Connecticut loses power. A mid-ingest power cut leaves Postgres in recovery and Qdrant replaying its write-ahead log. Both usually recover. Sometimes the vector collection needs a snapshot restore, and the snapshot is a day old. A UPS is 150 to 400 USD [unverified estimate] and it is the single highest-value item in the hardware quote. Without one, I will be on the phone at 6pm on a Friday walking a non-technical person through a restore. With one, the box rides out the outage and comes back on its own.

**Disk encryption, and the unattended boot problem.** The box holds client tax returns. If it is stolen, the firm has a breach notification problem, and their cyber insurer will ask about encryption at rest. Nobody in the proposal addresses this. Full-disk encryption is the right answer, but a boot passphrase means the box sits at a prompt after every power event until a human types it. The workable options are a TPM-bound key with automatic unlock, or a key file on a USB stick left in the box (which weakens the theft case), or no encryption and a written risk acceptance. This is a deliberate decision with a support consequence either way, and it should be in the install document rather than discovered by the first MSP who asks. [inference]

**Who restarts it.** The answer at a six-person firm is "the office manager, if she is in". So the box must come back from a hard power cut with no intervention, and it must display its own state without a terminal. That means restart policies on every container (the ai-stack repo currently has healthchecks on two of ten services and its own tracker lists the missing ones), a boot-time self-test, and a physical or on-screen indicator a receptionist can read: green for working, amber for degraded, red for call someone. [inference, grounded in the ai-stack repo's own known-issues list]

### 1.2 The install bundle, because the office build cannot fix itself

"No internet at runtime" has an install-time consequence the plan does not carry. You cannot pull an image, fetch a model, or correct a wrong tag on site. Everything ships on the truck: container images as `docker save` tarballs, model weights pre-fetched into mounted caches, OS packages, and the pinned digests. Then it is validated before it leaves, because a two-hour drive each way to discover a typo is a lost day.

The build brief already contains a defect of exactly this class: the stack pins `ghcr.io/huggingface/text-embeddings-inference:1.6` with no `cpu-` prefix, and the bare tag is the CUDA image. On a CPU-only box that is a deployment failure, not a slowdown. The sibling report verified that the CPU tag is `cpu-1.9` and that it serves the reranker [measured, sibling report 05]. In a client's office with no internet, that one-character class of mistake is unrecoverable on the day.

So the deliverable list for an office install is: a signed bundle, a digest manifest, an offline install script, a smoke test that runs without network, and a rollback bundle for the previous version. None of that is in the architecture proposal, and it is a week of work per release.

### 1.3 The documents, as they actually arrive

The corpus in the proposal is contracts, policies, invoices, emails, spreadsheets, scanned PDFs. The corpus in a CPA firm is messier, and each item below is a support call I expect in year one.

| What arrives | What happens in the pipeline | The call it causes |
|---|---|---|
| Password-protected bank statements and portal exports (very common) | Docling raises a conversion error. The worker retries twice and dead-letters the file. Nothing in the UI says so. | "It can't find the March statement and I know it's there." Diagnosis takes 40 minutes because the dead-letter queue is a log file nobody reads. |
| Files dropped by the copier onto a Windows share while still being written | The watcher sees a new file, starts parsing a partial PDF, records the truncated text and the (final) filename. Change detection by mtime and size then reports "no changes" on the next cycle. | "The last page of the invoice isn't in there." Silent, permanent, and invisible to every metric in the plan. |
| Photos of receipts, faxes, skewed scans | OCR engages on nearly every page. Ingestion throughput drops by roughly 60 percent relative to the digital-PDF path [published measurement, Docling report: disabling OCR saves 60 percent of runtime on x86 CPU]. | "It's been indexing for two weeks and it's still going." |
| A new vendor's statement format with 9-point tables | TableFormer mis-detects the structure, the fee schedule becomes a token stream, and a number is cited confidently from a mangled row. | The worst call in the product: "the AI said the fee is 1,250 and the schedule says 12,500" (or worse, it does not say, and nobody notices). |
| A 400-page scanned return | Docling's page-image handling is memory-hungry on large files; the project has open reports of the process consuming all available memory and being killed [project issue reports, #2779 and #3345]. A `SIGKILL` mid-run leaves no partial progress and no error in the UI. | "It says it's still working on the Henderson return." Meanwhile the OOM killer has been recycling the worker every twenty minutes. |
| The document management system the firm already runs | Two copies of every client document now exist, one in the DMS and one in Postgres and Qdrant. Retention, legal hold, and deletion must cover both, and nobody has written down which is authoritative. | "We deleted that client's file and the AI still cites it." Or the reverse, an auditor asking why client data exists in a second store. |

That last row is the one I would raise with the client before install, not after. The retrieval architecture turns the firm's document store into one of at least three copies (file share, Postgres full-text, Qdrant payload), and the plan's own governance section mentions retention without resolving it. Page-anchored retrieval needs the text. There is no version of this where Postgres does not hold the firm's words in lexeme form. That is fine, but it has to be a stated design fact with a deletion procedure that covers all three stores, not an accident.

### 1.4 The support channel, which currently cannot exist

Two of the company's own documents contradict each other on how support reaches a client box.

The build brief says client boxes use SSM only, never SSH keys. The architecture document says the office implementation is a WireGuard or Tailscale tunnel with consent. SSM requires an outbound internet connection, an IAM role, and the SSM agent. The office build's defining constraint is that it does not depend on the internet. Those cannot both be true.

What I would build instead, and insist on before the first install:

1. **A client-initiated reverse tunnel.** The office manager clicks "start support session" in the app, and the box opens an outbound connection to our relay for a fixed window, say 60 minutes, renewable only by the client. It writes an audit row with who started it and when. No standing inbound path, nothing listening, nothing to explain to the firm's insurer except a screenshot.
2. **An offline triage bundle.** A button that writes a zip the client can email or put on a USB stick: last ingest cycle, dead-letter list with reasons, queue depths, model cache status, disk, last 100 refusals with reasons, and the last ten slow queries. Without this, every call starts with "can you open a terminal", and the answer at a CPA firm is no.
3. **A health page in the product, not in Grafana.** One screen, four states. "Indexed 41,208 pages, last update 6 minutes ago, 3 files need attention, verification is 2 seconds behind." The plan's own alarm list pages on five things, and for this client exactly one of them will ever fire (disk at 90 percent). The failures that will actually happen to this firm are not on that list. Section 3.4 is the list I would use.
4. **A printed card taped to the box.** Three steps for a non-technical person: unplug nothing, press the restart button, wait five minutes, look at the light. Then a phone number. This sounds trivial and it saves calls.

### 1.5 The answering model is missing from the proposal and it decides everything

The proposal specifies retrieval, chunking, verification, and refusal. It never names the model that writes the answer. In the office build that model is local, and the plan's own open questions state that the office reply path is single-digit tokens per second on CPU, a figure their production-readiness document labels typical rather than measured [their own statement, unverified].

Three consequences, all operational:

- A 400-token answer at 8 tokens per second is 50 seconds of generation before verification even starts. That is not a chat product, it is a research request. It is tolerable for "what does the engagement letter say about scope", and it is not tolerable for the ten follow-ups after it.
- The verifier needs the answerer to emit structured claims with citation indices, page numbers, and quotes. A small local model is much worse at that discipline than a frontier model. When it emits a malformed citation, Layer 1 fails closed and the user sees a refusal. So the practical experience of a strict verifier behind a 4B answerer is a system that refuses a lot. Nobody has measured that refusal rate, and it is the number that decides whether the firm keeps using the thing.
- Per-claim verification needs a second model load. If Layer 4 (the generative judge on a different model) also ships, that is a third. On one GPU, they queue behind each other.

My recommendation, before any retrieval work: measure end-to-end refusal and support rates with the actual local answerer on the client's actual documents, not with a frontier model. If the local answerer's citation discipline is poor, the fix is a bigger local model or a grammar-constrained citation format, not more verification.

---

## 2. Walking the architecture as built and operated

### 2.1 Stage 1, ingest and parse

**The design is right and it is the highest-value stage.** A fee schedule read as a token stream produces a confidently cited wrong number, and nothing downstream recovers it. The evidence for that claim is not a benchmark, it is arithmetic: the verifier compares the claim to the passage it was given, and if the passage is wrong, the verifier confirms the wrongness.

**Measured cost, from the parser's own authors.** For a mixed 4,008-page benchmark on an 8-thread x86 CPU: 0.79 s per page at the median, 3.1 s per page on average, 16.3 s at the 95th percentile; OCR engages on 578 of those pages and costs 13 s per page on average where it runs; the layout model costs 633 ms per page and TableFormer 1.74 s per table [published measurement, Docling technical report, section 5.4].

**My arithmetic on those numbers, clearly labelled as arithmetic.** A scanned accounting archive is close to the worst case, because OCR engages on most pages. At 13 s per page and two concurrent workers on a 16-vCPU box (16 usable threads), throughput is roughly 0.15 pages per second, so:

| Archive size | At 0.15 pages/s | At the plan's assumed 1.29 pages/s |
|---|---|---|
| 20,000 pages (one busy season) | ~1.5 days | ~4 hours |
| 200,000 pages (a decade of a small firm) | ~15 days | ~2 days |
| 1,000,000 pages | ~77 days | ~9 days |

The plan's "nine days for a million pages" is 1.29 pages per second. That is plausible for a corpus of digitally-native PDFs where the median applies and pages parallelise cleanly. It is optimistic by an order of magnitude for a box of scans. Treat it as unverified either way, because it is arithmetic rather than a measurement on the target hardware, and the plan itself says so.

**Failure: the misread digit, which no layer catches.** A scanned fee schedule, one digit wrong, cited confidently. The verifier confirms it. This is the failure the whole product is judged on and the architecture's stated mitigation is "a confidence threshold plus the is_ocr flag setting expectations in the UI". That is too weak, and there is a cheap fix: Docling exposes per-component confidence (layout, OCR, parse, table) at page and document level, with documented quality grades and workflow-routing use cases, since v2.34.0 [primary, Docling confidence-scores documentation]. So at ingest, store the page's OCR and table scores. Then make it mechanical in Layer 1: **any number in a claim whose supporting token comes from a page below the OCR confidence threshold is refused, not marked amber.** And show the page image next to the number in the citation panel, so the human checks the image rather than our text. That converts the worst failure into a refusal, which is the product's stated preference, and it costs one field and one rule.

**Failure: the memory kill.** Docling on a large scanned PDF is memory-hungry and the project has open reports of the process consuming all available memory and being killed [project issues #2779, #3345]. Operationally the fix is boring and must be explicit: run the parser in its own container with a memory limit, parse one document at a time, insert a checkpoint after every N pages so a kill loses one document rather than the run, and treat a `SIGKILL` as an ingest event with a reason, not a silent retry. The plan says "Docling in its own container", which is half of this.

**Failure: the dead-letter queue that nobody sees.** The ai-stack repo already dead-letters corrupt files after two failed cycles. That is the right policy and the wrong interface. In the field, the dead-letter queue is the product's "we couldn't read this" list, and it belongs in the UI with the filename, the reason in plain words, and an action ("this file needs a password", "this file is still being written", "this scan is too poor to read"). Without it, every unreadable document becomes a phone call, and the client's mental model becomes "the AI doesn't know about my documents", which is the trust failure the whole architecture exists to prevent.

### 2.2 Stage 2, chunk

**Page-bounded structure-aware chunking at 384 to 512 tokens with zero overlap is the right call** and I would not relitigate it. The variance argument is the strong one, and the overlap argument is right for the stated reason: overlapping chunks make the same sentence appear as two sources, and a citation panel that shows two hits looks like corroboration when it is duplication.

Two operational problems the proposal does not resolve.

**A table that spans six pages breaks the page citation.** The plan keeps a spanning table as one retrievable unit with a page range, and excludes its rows from the surrounding page chunks. Then a question about a row on page 8 cites "pages 7 to 9", and the mechanical rule "cited page falls within the chunk's page range" passes. The citation requirement is satisfied on paper and defeated in practice, and the architecture's own verifier cannot catch it. Fix: index table rows individually with their own page and the header names folded in, alongside the whole-table entry which stays for display and for questions about the table as a whole. A depreciation schedule is not a retrievable unit, it is a set of rows with a caption. [inference]

**Chunk identity is stable, but the meaning behind it is not.** The uuid5 scheme keyed on parser version and chunker version is good engineering. It also means a parser upgrade re-chunks and re-embeds everything, which on this hardware is days. So parser version bumps become a scheduled on-site event with a cost, not a routine dependency update. That needs to be in the maintenance contract, not discovered when a CVE forces a Docling upgrade during tax season.

### 2.3 Stage 3, contextualise

**The deterministic half is free and correct.** Title, heading path, page label, table caption prepended before embedding. Docling already carries the heading path. No argument.

**The generated half is where I would cut.** The plan itself lists it as an open question, with the cost arithmetic: 50,000 chunks at 100 generated tokens is 5M output tokens, or 139 hours at 10 tokens per second. On the office build that is a batch job on rented hardware, which means shipping the client's chunk text off site or running a local model for a week. Both are worse than not doing it. The measured gain in the source the plan cites comes from chunk-specific disambiguation, and a heading path plus a table caption is already a disambiguation. Ship the deterministic half. Keep the four-way ablation as a cloud-build experiment only, and do not let an unmeasured mechanism become a stage in a client install. [inference, with the plan's own arithmetic]

### 2.4 Stage 4, store, embed, govern

**bge-m3 with no prefix is the right choice for the wrong-looking reason, and the reason survives field support.** The measured quality gap against multilingual-e5-large (0.437 against 0.546 average nDCG@10 on BEIR) is real. What decides it in the field is that a prefix-discipline model has a silent failure mode: TEI exposes one server-wide default prompt rather than separate query and passage prompts (the proposal's own finding), so setting it for queries corrupts every stored document vector, and the symptom is quietly worse retrieval with no error anywhere. A zero-prefix model cannot be misconfigured that way. For a box supported by phone, that property is worth more than the nDCG. I would keep the embedding contract as designed, and I would add one operational detail the plan does not mention: **the contract must be checked at startup, not only at write and read.** A box that restarts with a mismatched collection should refuse to serve and say so on the health page, rather than serve worse answers.

**The operational cost the plan understates: a re-embed is a site visit.** Swapping the embedding model means re-embedding the whole corpus, which on a CPU box is hours to weeks depending on size, on hardware that is also answering questions. The contract makes it resumable, which is good. It does not make it free, and it does not make it remote. Price it.

**Qdrant's telemetry is on by default.** The open-source container collects anonymised usage statistics unless `QDRANT__TELEMETRY_DISABLED=true` or `--disable-telemetry` is set [primary, Qdrant usage-statistics documentation]. An office build sold on "no internet at runtime" must set this on day one and prove it in the install smoke test, along with the Open WebUI telemetry setting the ai-stack repo already handles and a documented check that no container has an outbound connection. The plan's licence section is thorough and its egress section is thin. A one-line test that fails the install if any container dials out is worth more than a policy paragraph.

**Disk planning.** Models are 20 to 30 GB (embedder 2.3 GB, reranker 2.3 GB, Docling models, MiniCheck, plus a 9 to 20 GB answerer). Full-text search adds roughly 20 percent of the text volume as a GIN index (measured at 51 MB of index for 254 MB of corpus [measured, sibling report 01]) and the tsvector itself is another copy. Qdrant holds vectors, which for 1024 dimensions is 4 KB per chunk, so 200,000 chunks is about 800 MB plus graph overhead, which is not the problem. Snapshots, backups, and the raw documents are. **A 2 TB NVMe is the honest minimum for a firm with 200 GB of documents**, and the plan should say that instead of leaving it to the integrator. [inference]

### 2.5 Stage 5a, dense retrieval

**Not the bottleneck at this scale, and the hnsw_ef guidance is correct.** Qdrant's own measurement (recall 0.986 at ef=16, 1.000 at 256, with fused nDCG moving at most 0.0022 while latency rose up to 49 percent) is the right way to set it, and the guidance to set it to a recall target and stop is right.

The field caveat is that "this scale" is defined by the corpus, and the corpus grows. Vectors stay in RAM by default in Qdrant. At 200,000 chunks that is under 2 GB, fine on a 64 GB box and tight on a 16 GB office mini-PC once Postgres, Docling, and three models are resident. The silent degradation is not recall, it is the box starting to swap. Put a memory floor in the hardware spec and a swap alarm on the health page. [inference]

### 2.6 Stage 5b, lexical arm

**This is the strongest part of the proposal and I would ship it essentially as written.** The reasoning holds: the failure being fixed is tokenisation, upstream of ranking, and the identifier column plus the literal assertion fixes it without a new dependency or a licence conversation.

Four field problems.

**1. Ranking cost grows with the corpus, and Postgres says so.** The manual is explicit: ranking "requires consulting the `tsvector` of each matching document, which can be I/O bound and therefore slow", and it is unavoidable because practical queries return many matches [primary, PostgreSQL 18 documentation, section 12.3.3]. The identifier case is fine because the identifier is rare. The common-word case is not. In a CPA corpus, "invoice", "engagement letter", "bank statement", and a client's surname all match thousands of chunks, and every one of them gets ranked before the limit applies. So the system is fast in month one and unreliable in month eighteen, with no configuration change to blame.

Detection: log the lexical arm's latency per query and alert on the 95th percentile, not the mean. Mitigation: cap the candidate set with a recency or ACL pre-filter, and know that a cap is a recall ceiling (the plan already says this correctly for metadata filters). If it gets bad, the honest fix is a real BM25 engine, which the plan has deferred on licence and maturity grounds. Put the trigger in writing now: if the lexical arm's 95th percentile exceeds one second on a corpus this size, that is the signal to revisit. [inference on a primary source]

**2. The identifier column is a PII index.** A regex that extracts identifiers from tax documents will extract SSNs, EINs, account numbers, and check numbers, and store them lowercased in a GIN-indexed column, along with copies of the surrounding text in the tsvector. That is a defensible design and it changes the data-at-rest story: the Postgres volume is now sensitive in its own right, its backups are sensitive, and deletion requests must cover it. Two concrete requirements: encrypt the Postgres volume (and resolve the unattended-boot question from section 1.1), and write the deletion procedure for all three stores before the first client document is ingested. [inference]

**3. OCR noise in the lexical index.** `to_tsvector('english', ...)` over OCR output produces plausible-looking lexemes for garbage tokens. The lexical arm will match them. That is acceptable for recall and it is one more reason the answer's evidence should be the page image rather than our extracted text on any page flagged `is_ocr`.

**4. The generated-column staleness trap the plan already names.** Good. Add the test to the release suite, because the failure mode is zero errors and zero results, which is exactly the kind of bug that survives to a client.

### 2.7 Stage 5c, candidate depth

500 per arm is defensible at this corpus size. Two operational notes. First, the depth is a latency multiplier on the lexical arm, per the ranking-cost point above, so depth and corpus size interact and the plan treats them separately. Second, the proposal's reasoning about metadata pre-filtering being a recall ceiling, never a recall source, is correct and under-used: it is also the explanation for the one access-control failure that will end a client relationship. See section 3.3.

### 2.8 Stage 6, fusion

**Doing RRF in our own code at k=60 with a deterministic tie-break is correct**, and the version audit behind it (native RRF hardcoded at k=2 on the pinned Qdrant v1.11.3, configurable k in v1.16.0, weights in v1.17.0) is exactly the kind of check that should happen before a build rather than during one.

The field risk is arithmetic drift. The plan says to reproduce the Qdrant client formula `1 / ((rank + 1) / weight + k - 1)` so it can be verified against a reference implementation. That is good practice and it decays: the reference implementation is a Python client on a laptop, the shipped code is in the box, and in two years nobody remembers which one was right. Put the reference arithmetic in the test suite as a table of expected scores, so the invariant is executable. [inference]

### 2.9 Stage 6b, reranking, gated

**In the office build I would not ship it, and in the cloud build I would keep the gate and make it stricter.** The measured evidence on the reference shape:

| Model | Candidates | Wall clock | Per candidate | Box load |
|---|---|---|---|---|
| bge-reranker-v2-m3, 568M, TEI cpu-1.9 | 25 | 16.8 s | 671 ms | 16.0 |
| bge-reranker-v2-m3 | 50 | 109.3 s | 2,185 ms | 22.0 |
| gte-reranker-modernbert-base, 150M | 25 | 58.8 s | 2,352 ms | 27.9 |

[measured, `/tmp/search-research/bench_final.log`, taken on the reference instance while sibling agents were benchmarking concurrently. The contention caveat is real and is why the sibling report estimates 1 to 3 s at 50 candidates on an idle m7i.4xlarge [inference]. Even the optimistic end is a multi-second addition to every query, and the measured end is a minute and a half.]

The operational reading is the one that matters: **reranker latency on a shared CPU box is not a property of the reranker, it is a property of what else the box is doing.** It will be fast on a quiet Tuesday and unusable while Docling chews a 300-page scan. A component whose latency varies by 30x with unrelated system state cannot be part of a promise, and it cannot be debugged over the phone, because by the time the client calls, the ingest job has finished and it is fast again.

If it ships at all, the gates should include one the plan does not have: **the reranker must be measured while an ingest job is running**, because that is the state the box will be in during February.

### 2.10 Stage 7, query side

**The four shipped items are the right four, and the filter rule is the best idea in the stage.** "A filter which cannot be applied produces a refusal rather than an unfiltered search" is exactly right. A dropped date or ACL filter does not degrade politely: it answers confidently from the wrong year or the wrong client, cites a real page, and looks fine in aggregate metrics.

Three field notes. The conversational rewrite is fine in the cloud build and marginal in the office build, because it is a generation before the retrieval, and a 150-token rewrite at single-digit tokens per second is 15 to 30 seconds of dead air before anything starts. The acronym glossary is cheap and worth it. Mine it from the client's own documents during install, and expect a low yield on a firm whose documents are scans. The post-retrieval cascade that escalates on an empty result set is where complexity creeps in, and a cascade is hard to explain on the phone when it produces a different answer to the same question. Bound it: at most one escalation, logged with its reason, shown in the answer's trace. [inference]

### 2.11 Stage 8, answer, verify, refuse

This is the product claim and it is also the biggest support surface. The design is largely right and the honest treatment of the verifier's ceiling is unusually good. Four operational realities.

**Verification does not batch and it is the capacity limit.** Measured: 1,583 ms, 1,772 ms, and 1,636 ms per pair at batch 1, 8, and 16 for a 184M NLI model on one core [measured, sibling report 08]. The 770M MiniCheck is roughly 4x that, so 6 to 8 s per claim on one idle core by extrapolation [the sibling report labels this an extrapolation; treat the absolute as unverified, the ratio as solid]. Twelve claims on four dedicated cores is 20 to 25 seconds, and it is worse in practice because the same cores are running the answerer, the embedder, and any ingest job.

So the architecture needs three things the proposal implies but does not specify: a **bounded verification queue** with a visible position ("verifying 5 of 12"), a **hard concurrency cap** per user so six people cannot each launch twelve claims, and a **fail-closed timeout** that says "I could not verify this answer, here are the sources I used" rather than hanging or, worse, showing an unverified answer. That last one is the single most important line of code in the product, because the alternative is a confident wrong answer with a certificate attached.

**The verifier's measured error rate is a support and sales problem, not just a quality one.** MiniCheck-Flan-T5-Large missed 40 percent of unsupported sentences on a 24-pair sample [measured, sibling report 08, small sample], and the published table for that class implies 46 to 68 percent missed-hallucination rates [vendor-published numbers]. That is fine as long as it is published to the client in plain words. It is not fine when a sales conversation says "every answer is verified". The mechanical layer catches fabricated citations, wrong pages, missing quotes, numeric substitution, and identifier substitution, deterministically. The entailment layer is a signal. Say it that way.

**Layer 3 and Layer 4 are where I would cut.** Claim decomposition on a structural trigger is defensible in the cloud build. The generative judge on a different model is not, in the office build: it is another model load competing for the same GPU or the same cores, it adds a second prompt to version and validate, and it only fires on the cases the classifier is least sure about, which are exactly the cases where a judge's own error rate is highest. The measured ceiling is the small verifier's false-negative rate, and no judge restricted to a band fixes that. Keep Layer 1 and Layer 2, keep refusal per claim, and cut Layers 3 and 4 from the office build until a measured experiment earns them. [inference]

**The conformal claim must be stated honestly.** Split conformal gives a valid finite-sample guarantee on the participation rate, which is genuinely useful at n=40. It cannot guarantee that what the system says is supported, because that is bounded by the verifier's own false-negative rate. The plan says this correctly in one place and then uses the word "guarantee" loosely elsewhere. One sentence in the client-facing document: "the threshold is calibrated so that at most one answer in ten is refused when the evidence is present; the false-negative rate of the checker is X percent and it sets the ceiling on what we can promise." That sentence is the difference between a good product and a liability.

### 2.12 Stage 9, evaluation

The 60-question design is sound and the statistics are honestly presented. The field problem is that it is frozen, and this deployment runs for three years.

**The calibration set decays.** Exchangeability is the assumption behind split conformal, and it breaks the moment the corpus grows, the question mix shifts toward the new client work, or the documents the questions were written against get superseded. Reusing the same 40 questions forever gives a threshold that is right for a corpus that no longer exists.

The fix is cheap and it uses the product's own telemetry: **sample real questions continuously, have a human label ten a month, and recalibrate quarterly.** Over a year that is 120 labelled real questions, which is a better calibration set than 40 written in the first month, and it costs the client an hour a quarter rather than a day. Keep the 60 as the frozen regression set, exactly as designed, and add the live loop for calibration. [inference]

**The golden questions have to be re-written when documents change**, which the plan acknowledges in the monitoring rules. In practice, the trigger should be an event, not a calendar: any time a document that a golden question cites is superseded or deleted, that question goes on a review list. Automate that link, because otherwise it is discovered when the set goes quiet. [inference]

---

## 3. Cross-cutting failure modes

### 3.1 What needs a GPU we said we would not need

| Component | CPU verdict | Evidence |
|---|---|---|
| Embedding, dense retrieval, lexical, fusion | Fine on CPU | 30.9 ms median query embedding [measured]; keyhole-scale vector search |
| Docling parse, layout, tables | Fine, but slow on scans | 3.1 s per page average, 13 s per page with OCR [published measurement] |
| Reranker | Marginal to unusable | 16.8 s at 25 candidates, 109 s at 50 under load [measured]; 1 to 3 s inferred on an idle box [inference] |
| MiniCheck verification | Usable with a core budget, and it is the throughput ceiling | 1.6 s per claim at 184M, no batching benefit [measured]; 6 to 8 s inferred at 770M [inference] |
| The answering model | Not viable on CPU for interactive use | The plan's own single-digit tokens per second figure, and their own product spec puts 14B on a 24 GB GPU and 32B on a 48 GB GPU [their repo] |

So the honest sentence is: **the retrieval architecture is CPU-clean; the product is not.** Verification, reranking, and generation all want the same accelerator, and the office build's own existing product specification already assumes one. The recommendation that follows is not "add a GPU to the retrieval plan", it is "stop describing the office build as a CPU box". A CPU-only office build is a product that takes 50 seconds to answer and cannot rerank. If the constraint is genuinely no GPU, then the answer is a smaller scope: fewer documents, a retrieval-only "find me the page" product with no generated answer, which is honestly useful in an accounting office and much easier to support. [inference]

### 3.2 What needs an internet connection the office build does not have

| Need | When it bites | Fix |
|---|---|---|
| Qdrant usage statistics, on by default | Every start, silently | `QDRANT__TELEMETRY_DISABLED=true`, plus an install test that fails if any container dials out [primary, Qdrant docs] |
| TEI and Docling model weights | First start after a cache wipe or a container recreate | Pre-fetch into a mounted cache, set the offline flag, and make a missing cache a startup refusal with a readable message |
| Container image pulls | Every restart if images were pruned, and every update | Ship `docker save` tarballs and pin digests; never rely on a registry at the client site |
| OS and package updates, CVE patching | Continuously | An annual maintenance window with a bundle, or accept drift in writing |
| Time synchronisation | Clock drift over months; affects log ordering and date filters | Local time source or a documented tolerance; the audit chain does not depend on the clock, the date filters do |
| Remote support | The moment anything breaks | Client-initiated reverse tunnel, plus the offline triage bundle from section 1.4 |
| The audit anchor | Continuously, if it is meant to leave the machine | In an offline build the anchor goes to removable media. That still detects a deleted tail, and it is weaker against an attacker with access to the drawer. Say which threat it covers |

The pattern is that the office build's "no internet" is a runtime constraint that quietly becomes a maintenance constraint. The fix is not to break the constraint, it is to plan the truck.

### 3.3 What silently degrades as the corpus grows

Ordered by how likely it is to actually happen, with the detection I would build.

| Mechanism | Symptom | Why metrics stay green | Detection |
|---|---|---|---|
| Superseded documents both indexed | A real number from last year's schedule, correctly cited | Every retrieval metric is fine; the retrieved page does exist | Version detection at ingest plus a "these two documents look like versions of each other" review queue in the UI |
| OCR digit errors on scans | A wrong number, faithfully confirmed | The verifier compares to the text it was given | Docling confidence propagated to the citation, numeric claims refused below threshold, page image shown |
| Partial or racing file ingestion | Missing last pages, permanently | The manifest says the file is indexed | Hash-based change detection, a file-stability check before parse, and a page-count check against the PDF |
| Encrypted PDFs dead-lettered | "It doesn't know about my statements" | Nothing in the UI reflects the dead-letter queue | An exceptions queue in the product, with the reason |
| Common-term lexical queries slow down | Search feels unreliable, users stop trusting it | The mean latency looks acceptable | Per-arm 95th percentile latency, alerted |
| Dense-filter recall collapse | A partner gets a refusal where a non-admin gets an answer | Aggregate recall looks fine | Explicit filter-recall measurement for the admin path, which the plan already specifies; make it a release gate |
| Identifier regex drift | New document types stop being findable by their numbers | Zero errors, zero results, no log line | A coverage report: what fraction of ingested pages produced at least one identifier |
| Qdrant segment optimisation after bulk load | Latency and recall shift with no config change | Nothing changed on paper | Record hnsw_ef and measure recall against exact after every bulk load, not once at install |
| Payload and snapshot growth | The box starts swapping, then the disk fills | Search still works until it does not | Memory floor alarm and the disk alarm, both on the health page |
| The golden set going stale | Nobody notices the product got worse | The set keeps passing | Link questions to the documents they cite and flag on document change |

The one that matters most is the first. Two versions of a fee schedule in the same folder is not a retrieval failure, it is a records-management failure, and it produces the confident wrong answer the whole product exists to avoid. The mitigation is not more retrieval, it is a version-confirmation step in the ingest UI and a rule that when two near-identical documents match a query and disagree on a number, the system shows both and says so. [inference]

### 3.4 What will page us at 6pm on a Friday

The plan's five alarms (chain head moved backwards, no audit event in business hours, disk 90 percent, database down, spend at cap) are the wrong five for a six-person office. Chain tampering is not a Friday event. Spend at cap is a cloud-build concern. Here is the list I would actually run, with what the client says and the first move.

| Alarm | What the client says | First move | Fix that prevents the repeat |
|---|---|---|---|
| Box did not come back after a power event | "Nobody can log in" | Confirm the box is on and at a prompt (encryption), then containers | UPS, restart policies, TPM unlock or no encryption with a written acceptance |
| Ingest stale for 24 hours | "It hasn't learned the new files" | Check the share mount, then the worker | Mount health in the product, staleness on the health page |
| Verification queue over threshold for 15 minutes | "It's just spinning" | Check for an ingest job eating the cores | Cgroup caps on the ingest worker, per-user concurrency cap, fail-closed timeout |
| Disk above 85 percent | "It's slow and then it stopped" | Identify the growth: snapshots, WAL, tsvector, or logs | Retention policy for snapshots, log rotation, and a disk plan in the quote |
| Model cache missing on a container start | "It worked yesterday" | Restore the cache from the bundle | Cache on a mounted volume, offline flag, cache presence in the boot self-test |
| Postgres lexical 95th percentile over one second | "Search takes forever now" | Confirm corpus growth, not a plan flip | Depth cap, recency pre-filter, or revisit the BM25 decision |
| Backup or anchor job failed | Nobody says anything until they need it | Re-run from the console, verify the anchor | A job that reports its own failure to the health page, not to a log file |
| A release left the box unhealthy | "It's different now" | Roll back to the previous bundle | Signed bundles with automatic rollback on failed health check |

Two of these deserve a comment. The verification queue alarm is the one that a growing firm will hit first, because it is the only component whose cost scales with answer length and user count at the same time. And the model cache one is an air-gap speciality: a container recreate plus a missing cache equals a box that cannot start the thing it needs and cannot download it either. In an offline build, the model cache is infrastructure, not a cache. [inference]

### 3.5 What is too complex to support remotely

Ranked by how much pain each causes on a phone call where the other end cannot open a terminal.

1. **Anything with a queue.** If the answer is "it depends how busy the box is", the call cannot be resolved remotely, because the symptom is gone by the time you look. Instrumentation first, component second.
2. **Anything with a model version.** "Which embedding model is loaded" must be on the health page. It is one line and it prevents the worst class of misdiagnosis.
3. **Anything with three stores.** Deletion, retention, and restore all span documents, Postgres, and Qdrant. Every one of those is a multi-step manual procedure that has to be scripted, and scripted means tested, and tested means a drill.
4. **The reranker, if it ships.** A latency-sensitive component whose performance depends on unrelated load is the definition of a component you cannot debug at a distance.
5. **The post-retrieval cascade.** Non-deterministic escalation paths are hard to explain to a partner who asked the same question twice and got two answers.
6. **Layer 4, the generative judge.** Second model, second prompt, versioned separately, and it changes the answer. On the phone, it is unexplainable.

### 3.6 Three years, which is the actual contract

The plan is written for a build. The contract is for a support period. Four things change over three years and none of them are in the architecture.

**The corpus grows and the model set freezes.** Realistically the box will not be re-embedded on a schedule. So plan for a frozen box with an annual maintenance visit: bundle, update, re-run the golden set, re-embed if the embedding model changed, re-verify the restore. Price it. The alternative is a box that drifts into an unpatched state and a CVE conversation you cannot act on quickly, in a building you cannot enter remotely.

**The staff turn over.** The office manager who knows how to drop files in the folder leaves. The seasonal admin who was trained on the health page leaves. So the product needs a one-page operating guide written for a new employee, and the ingest folder conventions need to survive a change of personnel. This is documentation work, not software, and it is the difference between a system still in use in year three and one that quietly stopped being fed new documents in month eight.

**The documents change and the questions change.** The 60-question set is a regression detector for the corpus it was written against. After two busy seasons the corpus is materially different and the set needs a refresh, which means client time, which means it needs to be in the contract.

**The box reaches end of life.** A three-year-old desktop with a consumer GPU is out of warranty and its NVMe has three years of writes. The replacement should be a documented, tested procedure that runs off a restore bundle on new hardware, because the alternative is a data-recovery exercise during tax season. A cold spare on site is the only thing that gives a same-day recovery in rural Connecticut, and it costs a few thousand dollars that the 12 percent In-Office margin does not obviously cover. [inference]

---

## 4. What I would cut

Each cut is a support surface removed, with what it costs to keep.

1. **The generated half of contextualisation in the office build.** Keep the deterministic prefix. The measured cost is 139 hours at 10 tokens per second for a corpus of 50,000 chunks by the plan's own arithmetic, it needs either a local model for a week or the client's chunks on rented hardware, and nobody has published a quality comparison. Do it as a one-off study if curiosity demands it, not as a pipeline stage.
2. **The reranker in the office build.** Measured at 16.8 seconds for 25 candidates and 109 seconds for 50 under load. Keep the flag in the cloud build and add a gate that measures it under ingest load, which is the real production condition.
3. **Layers 3 and 4 of the verifier in the office build.** Claim decomposition and the band-limited generative judge add two failure modes and one more model load for a benefit bounded by the small verifier's false-negative rate. Keep the mechanical layer and the entailment layer, keep refusal per claim, and publish the false-negative rate.
4. **The pg_trgm third arm and the Docling heading-text arm.** Both are unmeasured, both add an index or a stage, and neither addresses a failure the identifier column has not already fixed. A support surface should be earned by a measurement.
5. **The LLM query rewrite in the office build.** 15 to 30 seconds of pre-roll at single-digit tokens per second for a 13.7 percent relative gain measured in the cloud build. Keep the deterministic acronym glossary and the filter extraction, and keep the rule that an unappliable filter refuses rather than degrades.
6. **The 500-per-arm depth in the office build** until the lexical latency curve is measured on the client's own corpus. Depth is free until it is not, and on Postgres it is not free for common terms.
7. **Any promise of a 24x7 response.** The build brief already flags that 30-minute SEV1 acknowledgement rests on one person. A six-person firm does not need the AI at 2am, and a contract that says otherwise is a liability. Business-hours response, a documented degraded mode, and a printed card. The degraded mode has a name every accountant already understands: the shared drive still exists.

---

## 5. What I would insist on

1. **The mechanical verifier, fail-closed, with the literal identifier assertion.** It is deterministic, it needs no model, it is roughly 150 lines, and it converts the worst failure in the product into a refusal. It ships before the reranker and before any query-side work. With one addition: the numeric rule must consult the source page's OCR and table confidence, not just the text.
2. **Docling's confidence scores stored per page and surfaced in the citation.** The scores exist in the library since v2.34.0 [primary, Docling docs]. Without them, "the parser is the ceiling" is a statement of faith. With them, the system can say "this number came from a page I read at 62 percent confidence, check the image" and show the image.
3. **Page anchoring that survives tables.** Rows indexed with their own page, not just a table with a page range. Otherwise the citation requirement is met on paper and defeated on page 8.
4. **The Postgres lexical arm with the regex identifier column**, exactly as designed. Cheapest quality in the whole document, no dependency, no licence.
5. **Deterministic tie-breaking in fusion**, with the arithmetic in the test suite so it stays true.
6. **Server-side ACL from the authenticated session, and a release-blocking leak test.** This is the one failure that ends a client relationship on the spot, and it is the one the plan already lists as release-blocking. Keep it binary.
7. **Refusal per claim with the four named reasons and a visible trace**, an amber state that shows the page image, and no path in the product that displays an unverified sentence as if it were verified.
8. **A bounded verification queue, a per-user concurrency cap, and a fail-closed timeout.** Without these, one long answer from one user degrades the experience for the other five, and the failure looks like "the AI is broken" rather than "the AI is busy".
9. **A health page and an offline triage bundle designed for a non-technical reader.** This is what makes three years of phone support possible at all, and it is a week of work.
10. **Disk encryption, a UPS, restart policies, and a restore drill in the acceptance test.** Operationally boring and each one prevents a specific Friday-night call. The restore drill is the only way the recovery promise becomes a number rather than an adjective.
11. **An ingest exceptions queue in the UI**, with the filename and the reason. Encrypted files, unreadable scans, and still-being-written files are normal, not exceptional, in an accounting office.
12. **A live question-sampling loop for recalibration.** Ten labelled real questions a month, quarterly recalibration, and the frozen 60 as the regression set. This is the difference between a threshold that means something in year three and one that was true in week one.

---

## 6. What I keep from the rejections, and what I would add

### 6.1 Rejections from the proposal that I would keep

I reviewed the proposal's rejection lists against the operational test, and I agree with almost all of them. Sources for each rejection are in the proposal's own licence and evidence sections, and I did not find one I would overturn on merit.

The licence rejections are correct (PyMuPDF, MinerU's bespoke terms, ParadeDB's unpriced AGPL, vchord, Elasticsearch's triple licence, Typesense, the Jina and Nemotron family). The second-system rejections are also correct (Unstructured as a second parser, OpenSearch, Quickwit, Tantivy, a sidecar BM25 engine), and the operational argument is stronger on a client site than in the proposal, because each additional service is another thing to explain on the phone.

The chunking rejections hold (semantic chunking, summary indexing, RAPTOR, high overlap, small-to-big as a stage). Semantic chunking's corpus-global statistics would break incremental ingestion, which is the mechanism the office build depends on.

The query-side rejections hold (HyDE, multi-query expansion, learned routing, decomposition by default, unbounded agentic loops), and the office build's generation latency makes them worse than they look in the cloud build. The weak-verifier rejections are correct and are the part of the proposal I would defend hardest (a general NLI model as the gate, the reranker as the verifier, self-verification, pairwise judging, Bedrock's grounding check, semantic entropy).

Three I would re-open or extend:

- **Late interaction.** The proposal defers it with a trigger on semantic misses. I would add a second trigger: the office build's GPU decision. If the box has a 24 GB GPU, a ColBERT-style rerank stage is the natural use of it, and the measured latency advantage over a cross-encoder is the reason. The blocker was TEI serving no multi-vectors, not the method.
- **The multilingual-e5-large challenger.** Agreed as the pre-registered comparison, with one change: run it on the client's own documents as part of install acceptance, not only on the 60 golden questions. The prefix-discipline risk is exactly the kind of thing that shows up as a slow quality drift rather than an error.
- **The BM25 revisit (pg_textsearch).** The proposal triggers this on an architecture review. I would trigger it on a measurement: the lexical arm's 95th percentile latency, or the first time a user says search got slow. A calendar review will not happen on a box nobody visits.

### 6.2 Alternatives worth costing

These are the alternatives I would put on the table, with the reason each one is worth costing.

**One datastore instead of two, for the office build.** The proposal never considers running vectors in Postgres. At a six-user firm with a few hundred thousand chunks, `pgvector` with an exact or HNSW scan is plausible, and it collapses the operational surface: one store to back up, one to restore, one to upgrade, one to encrypt, one deletion procedure, and no Qdrant telemetry to disable. It also makes filtered recall a SQL problem rather than a Qdrant prefetch subtlety, and at this scale an exact scan is affordable, which removes the filter-recall ceiling entirely. The costs are real and must be measured: HNSW or exact-scan latency at the client's corpus size, the memory Postgres needs for the index, and the loss of Qdrant's segment and payload tooling. I would cost this before the first office install. Licence: `pgvector` is distributed under the PostgreSQL licence, and I did not re-read the LICENSE file for this report, so treat that as unverified until someone does, at https://github.com/pgvector/pgvector. [inference]

**Index a narrow scope first.** The proposal implicitly assumes the whole archive gets indexed. The measured ingest arithmetic says the whole archive is a multi-week job that makes the box unusable for questions. Starting with the firm's own policies, engagement letters, and one client folder gives a working, trustworthy system in a weekend, proves the mechanism on real scans, and defers the archive until the second visit. It also gives the golden set something concrete to be written against. [inference]

**A retrieval-only product for the office build.** If the client will not fund a GPU, ship "find the page and show it to me" with no generated answer. The mechanical layer still works, the citations are still pages, refusals are unnecessary because nothing is asserted, and the support surface halves. It is a worse product and a much better promise.

**A bigger answering model instead of more retrieval machinery.** Every quality lever in the proposal is on the retrieval side. The measured spread between retrieval configurations is smaller than the spread between answerers, and the plan's own golden-set design would show it in one afternoon. If the choice is a reranker or a 14B instead of a 4B, I would take the 14B.

**The smaller MiniCheck variants.** `MiniCheck-RoBERTa-Large` and `MiniCheck-DeBERTa-v3-Large` are MIT and roughly half the size of the Flan-T5 checkpoint, which on a compute-bound, non-batching workload is roughly half the latency. The sibling report flagged them as untested. On a CPU box, that is the difference between a 25-second verification tail and a 12-second one. Test them before shipping the 770M model.

**ONNX int8 for the reranker.** The sibling report names it as the obvious latency lever and notes the quality cost is unmeasured. If the reranker is ever going to ship on CPU, this is the path, and the measurement is cheap.

---

## 7. What I could not verify, and what would change my mind

| Claim | Status | What settles it |
|---|---|---|
| Initial ingest of a real accounting archive takes 15 to 30 days | My arithmetic on Docling's published per-page measurements, not a measurement on the target box | Run Docling on 200 pages of the client's actual scans and measure |
| The answering model's citation discipline behind a strict verifier | Unknown; nobody has measured refusal rate with a small local answerer | End-to-end run of the golden set with the actual local model |
| Reranker latency on an idle reference instance (1 to 3 s at 50 candidates) | Inference from a contended measurement | Fifteen minutes of benchmarking with nothing else running |
| Office hardware cost, UPS cost, GPU cost | Not verified in this report | A quote |
| The client's corpus size, scan quality, and document mix | Unknown | A sample of the actual share |
| Whether the firm will permit any remote access path | Unknown, and it is a contractual question, not a technical one | Ask before the install, in writing |
| `pgvector` licence | Not verified here | Read the LICENSE file |
| Full-text ranking cost at the client's corpus size | Mechanism is documented by Postgres; the magnitude is not measured at this scale | Measure the lexical arm's 95th percentile as the corpus grows |

What would change my recommendation most: if the client's corpus turns out to be mostly digitally-native PDFs rather than scans, the ingest arithmetic improves by roughly 4x and the install story becomes a long weekend instead of a month. If it turns out to be mostly scans of poor quality, the product's honest scope shrinks to "find the page" and the generated-answer feature should wait for a second season of document quality work. [inference]

---

## 8. Sources

**Measured in this study** (`/tmp/search-research/`):

- `bench_final.log`: bge-reranker-v2-m3 at 25 and 50 candidates, gte-reranker-modernbert-base, bge-reranker-base, on the reference instance under concurrent load.
- `05-reranking.md`: TEI cpu-1.9 verification, the bare-tag CUDA trap, CPU latency inference for m7i.4xlarge.
- `08-verification.md`: MiniCheck false-negative rate, per-pair latency at batch 1, 8, and 16, the 770M extrapolation, Vectara's published missed-hallucination rates.
- `01-lexical-engine.md`: identifier tokenisation results, `ts_rank` behaviour, GIN and trigram index sizes measured on a 254 MB corpus.
- `07-chunking.md`: chunk-size evidence, table handling, Docling provenance and confidence fields.

**Primary and published sources:**

- Docling Technical Report, arxiv.org/abs/2408.09869 (v4 HTML read 2026-10-11). Per-page conversion times, OCR cost, model breakdowns.
- Docling OCR configuration and confidence-score documentation, docling-project.github.io/docling (read via a generated mirror of the docs repository at commit 752f81b, 2026-10-11).
- Docling issues #2779 and #3345, memory exhaustion on large PDFs, github.com/docling-project/docling.
- PostgreSQL 18 documentation, section 12.3.3, Ranking Search Results, postgresql.org/docs/current/textsearch-controls.html.
- Qdrant usage statistics documentation, qdrant.tech/documentation/ops-configuration/usage-statistics/.
- Text Embeddings Inference README, github.com/huggingface/text-embeddings-inference (local copy at `/tmp/search-research/tei_readme.md`).

**Internal documents read for this review:**

- `office-inference-core/docs/build-brief.md` and `docs/architecture.md`.
- `ai-stack/AGENTS.md` and `AGENTS.local.md` for the existing product's model tiers, container count, and known issues.
