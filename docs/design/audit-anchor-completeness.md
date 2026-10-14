# Completeness across daily exports: what an anchor can and cannot prove

Design note. 2026-10-13. No code changed.

The verifier accuses an untouched file of losing rows. The file is fine; the check compares a
quantity from one vocabulary against a quantity from another. This note states the question
precisely, recommends a design, and gives a change small enough to ship tomorrow.

Everything below refers to `services/audit/audit.py` (export at line 466, anchor at 516, verify at
546), `tests/test_audit_verify.py`, and the hourly anchor / daily export timers under
`deploy/cloud/systemd/`.

---

## 1. The problem, stated precisely

An export is a **window** on the chain. An anchor is a **point** on it. The verifier asks a
question that mixes the two, and its cheapest-looking test compares incomparable numbers.

The four vocabularies:

| | What it is | How it is expressed | Where it lives |
|---|---|---|---|
| **Row identity** | one event, written once, never updated | `audit_events.id`, `bigserial`, monotone | every row |
| **Chain position** | row *i* commits to row *i−1* via `prev_hash` | `row_hash` and `prev_hash` | every row |
| **Day window** | the rows a calendar query returns | `[first_id, last_id]` where `occurred_at ∈ [D, D+1)` | export + manifest |
| **Anchor** | the chain state at the instant the anchor job ran | `(last_id, last_row_hash, event_count, created_at)` | anchor file, shipped off-box |

`event_count` is the *cumulative* number of rows in the whole table at the instant the anchor job
ran. `len(lines)` is the number of rows in *one day*. They are different units. Comparing them is a
category error, and the error is silent because on a single-day record — which is every unit test —
the day and the whole chain happen to be the same thing.

Three relations are possible between an anchor and a day's export, and the current code collapses
them into one:

| Relation | Meaning | Correct verdict |
|---|---|---|
| `anchor.last_id < export.first_id` | the anchor predates this export's window | nothing to check; the anchor is a *prefix* commitment |
| `export.first_id ≤ anchor.last_id ≤ export.last_id` | the anchored row is inside the window | the anchored row **must** appear, at its position, with that hash |
| `anchor.last_id > export.last_id` | the export does not reach the anchor | this export is short of a state that is known to have existed — the only failing case |

At 49 events with the 2026-10-13 export holding rows 37–49 and an anchor at row 49, the relation is
the second one, `37 ≤ 49 ≤ 49`. The anchored hash *is* in the file, at line 13. The code finds it and
still fails, because `13 < 49`.

### The two false verdicts

1. **Anchor after the window closes.** A day whose traffic stops at lunchtime, exported at 00:00 the
   next day, while the hourly anchor ran at 22:00. `anchor.last_id > export.last_id` legitimately, so
   the membership test fails and the verifier prints *"the anchored row … is missing from this export,
   so rows that existed when the anchor was written are gone"* — the language of tampering applied to
   an intact file.
2. **Anchor inside the window, cumulative count.** The reported failure: the anchored row is present,
   and the count comparison fails anyway.

### The question an anchor should let an auditor answer

> Does this export's window cover the chain state this anchor committed to, and does the file's
> content agree with that commitment at that position?

Not *"does this file hold as many rows as the whole chain"*, and not *"is the anchor's row in this
file"*. Note the three components the question needs: **coverage of a position**, **agreement of a
hash**, and **the absence of a gap between the two**.

### Why position, not count

`id` is the only monotone ordering key in the system. The chain trigger orders by `id`
(`002_audit_events.sql`, `order by e.id desc limit 1`), the export orders by `id`, backfill preserves
`occurred_at` from the gateway and can insert a row whose timestamp is *older* than rows already in the
table, and `bigserial` can skip values if a transaction aborts. Counts are therefore the wrong
identity for a position, and today's code uses `id` everywhere except in the one comparison that
decides whether to accuse someone.

---

## 2. Recommendation

Commit to **positions** in both artifacts, and let the chain supply continuity. Both artifacts already
carry the required field; the work is in the reasoning, in the wording, and in one test.

### 2.1 The export manifest is a window commitment (option D — adopt, it is half-built)

`cmd_export` already writes exactly the right structure:

```json
{ "deployment_id": …, "date": "2026-10-13", "event_count": 13,
  "first_id": 37, "first_prev_hash": "…", "last_id": 49, "last_row_hash": "…",
  "file": "audit-events.jsonl", "file_sha256": "…", "generated_at": "…" }
```

"Rows 37 to 49, chained from X, ending at Y" is the correct self-description. Two things make it
trustworthy rather than self-serving:

- `first_prev_hash` is not taken on faith. Row 37's `row_hash` is computed over its own fields
  *including* `prev_hash`, so an export cannot claim a start hash the chain does not support without
  breaking every row from 37 onward. The start commitment is structural.
- The end commitment is checked by the *next* day's export, which must carry
  `first_prev_hash == 2026-10-13.last_row_hash`. That is the check `docs/runbook.md:563` already tells
  an examiner to make by hand. It should be a verifier check.

So the boundary hash is doing double duty: it is the end of the day's file and the start of the next
one. That is the property to lean on.

### 2.2 The anchor is a point commitment — keep it, stop comparing counts (option A — adopt, minimally)

The anchor already records `last_id`, `last_row_hash`, `event_count`, and `created_at`. That is enough
for the position rule. Do not add a "timestamp is enough" variant: a timestamp cannot order two events
inside the same second or survive clock adjustment, and both artifacts carry a better-ordering key
already.

Proposed rule, replacing the whole `anchor` block:

```text
given an anchored point A = (last_id, last_row_hash) and an export window W = [first_id, last_id]:

if A.last_id is null:                 report: the anchor carries no position; it proves nothing
elif A.last_id < W.first_id:          report: the anchor predates this export's window and covers none
                                              of it; this export makes no completeness claim against it
elif A.last_id > W.last_id:           FAIL:   the export ends at row W.last_id, and the chain is known
                                              to have reached row A.last_id. Rows are missing from the end
else:                                 the anchored row must be present at its position:
                                        - row A.last_id is in the file, and
                                        - its row_hash == A.last_row_hash, and
                                        - row (A.last_id + 1), if present, has prev_hash == A.last_row_hash
                                      otherwise FAIL
```

`event_count` drops out of the decision. Keep it in both files for diagnostics and human reading; stop
letting it decide anything. When the third branch genuinely fires, `A.last_id − W.last_id` is a far
better message than a count difference: *"the chain reached row 49 and this export ends at row 43."*

Note the fourth branch also verifies the *link*, not just membership. Checking that the anchored hash
appears somewhere in the file is weaker than checking the row that follows it chains to it. The link
check is the whole point of a chain and costs one comparison.

### 2.3 Do not move the anchor to the end of the day (option B — reject)

Attractive because "the two align by construction", but it solves the wrong half and costs the right
half:

- The daily export runs at 00:00 for *yesterday* (`oi-core-ship.sh`: `date -u -d yesterday`). An
  end-of-day anchor written at 23:59 is written *before* the export exists and before the last hour's
  gateway rows are backfilled. They do not align by construction unless you also re-sequence the three
  jobs.
- Hourly anchoring exists for a reason, stated in `oi-core-anchor.timer`: the interval is the window in
  which a deletion can happen before a copy lands somewhere the operator cannot reach. Daily anchoring
  widens that window 24×. It trades real detection for tidiness.
- An anchor written by the same job, on the same box, at the same moment as the export adds nothing an
  attacker cannot forge in the same breath.

The genuine requirement behind option B is *"commit to the export boundary"*, and §2.1 gets that
without giving up hourly anchors. Do also have the export register its own checkpoint for shipping —
write the manifest's commitment into the anchor output at export time, so the anchored sequence
includes one checkpoint per day boundary. That is a small addition to `cmd_export`, not a new
artifact class.

### 2.4 The next day's export is the natural completion proof (option D — the part not built)

A day is complete when nothing was removed from the end *of that day*. The artifact that can prove
this is the following day's export, because it starts where this one stopped and was written later
than the thing it certifies. Formally:

```text
verify_adjacent(day[d], day[d+1]):
    day[d].last_row_hash == day[d+1].first_prev_hash   else FAIL: a gap between the days
    day[d].last_id + 1 == day[d+1].first_id            else note: an id discontinuity (purge or gap)
```

An addition *after* the proof obligation is met is fine; that is the normal direction (rows 1..49
today, 50.. tomorrow). The anchor serves a different purpose: it certifies the state as of a moment,
which is what catches truncation of the whole export series at its end. Keep both, and let
`audit verify --anchor` additionally accept the next day's manifest as a completion witness, since
that is what an examiner actually holds.

### 2.5 Merkle tree and incremental commitments (option F — right at scale, wrong for this defect)

Merkle trees with consistency proofs (RFC 9162 §2.1.4) are how Certificate Transparency proves
append-only across versions without shipping everything, and the Go checksum database is the same
design for module hashes. They buy O(log n) inclusion and consistency proofs and cheap per-row audit
paths at scale.

They do not fix this bug. The defect is not that a proof is inefficient; it is that the verifier
compares a day's row count against a tree's total size. A Merkle log with `tree_size` in place of
`event_count` fails identically. A linear hash chain already provides the append-only property for a
bounded window *given the window's rows*, which is what an export is: each row commits to its
predecessor, so inserting, editing, or removing a row in the middle breaks the chain.

Adopt a Merkle log when one of these becomes true: per-day volume makes the O(n) recomputation
painful, an auditor needs per-row inclusion proofs, or a witness/cosignature ecosystem is wanted.
Until then, keep the chain and borrow the vocabulary — the checkpoint, described below.

### 2.6 What the recommendation amounts to

1. The manifest keeps `first_id`, `first_prev_hash`, `last_id`, `last_row_hash` — the day's window
   commitment. Already done.
2. The anchor keeps `last_id`, `last_row_hash`, `created_at` and stops carrying `event_count` as a
   decision input. One-line change of role, not of format.
3. `verify` gains the position rule of §2.2 and the adjacency check of §2.4, and stops treating
   "the anchor is not inside this window" as evidence of anything. This is the actual fix.
4. Optionally, sign the anchor's checkpoint (detached, Ed25519 or HMAC). Be honest about what signing
   buys: it authenticates the artifact to someone who has the public key, and it does not defend
   against an operator who can write both the record and a fresh signature. The defence against a
   forked chain is the anchor leaving the box (`docs/audit-evidence.md` already says so), plus a
   timestamp the operator does not control — an RFC 3161 timestamp from a third party, or a
   monotone sequence number embedded in the object-locked bucket key. Say plainly that nothing today
   is watching for a *fork*, where a truncated chain is anchored as if it were the truth.

---

## 3. The standards, and how each applies

| Scheme | What it does | What it lends here |
|---|---|---|
| **RFC 9162 / RFC 6962, Certificate Transparency** — [rfc9162](https://www.rfc-editor.org/rfc/rfc9162.html) | Merkle log; a Signed Tree Head is a signed `(tree_size, root_hash)`; consistency proofs show tree *n* is a prefix of tree *m* | The vocabulary. Our anchor is a tree head without a tree; our export's start/end pair is a prefix commitment. CT's insight is that append-only is judged by *consistency between two commitments*, never by comparing a count to a record |
| **Transparent logs** — [Russ Cox, design 25530](https://go.googlesource.com/proposal/+/master/design/25530-sumdb.md), [research.swtch.com/tlog](https://research.swtch.com/tlog) | "clients can verify that a particular log record really is in the log and also that the server never removes any log record" | The two claims split cleanly, exactly as §4 does. Also the client-side rule: the client caches the latest signed tree size and hash and refuses to forget it — the verifier must remember a previous commitment, which means the anchor (not the manifest) is what does the work |
| **Go `sumdb/tlog`** — [pkg.go.dev](https://pkg.go.dev/golang.org/x/mod/sumdb/tlog), [checkpoint format](https://github.com/transparency-dev/formats/blob/main/log/README.md) | `CheckTree(p, t, th, n, h)` verifies that the tree of size `t` with hash `th` contains as a prefix the tree of size `n` with hash `h`; the checkpoint is three lines — origin, size, base64 root hash | The concrete shape to copy: a checkpoint names its **origin** (our `deployment_id`), its **size** (our `last_id`), and its **hash**. Three fields, no counts compared across scopes |
| **Signed notes / witness cosignature** — [transparency.dev checkpoint format](https://github.com/transparency-dev/formats/blob/main/log/README.md) | A checkpoint is a signed note; independent witnesses cosign it | The future direction for §2.6 item 4, and the honest answer to the client-owned-bucket problem: the client's own account can hold the anchor |
| **Git's object model** — [gitglossary](https://git-scm.com/docs/gitglossary) “commit objects form a directed acyclic graph … because they have parents” | Each commit names its parent, so history is append-only relative to a remembered commit hash | The same primitive as our `prev_hash`, and the same limitation: nothing about a hash chain stops a *rewrite* from a new root. A rewritten history is detectable only against a remembered hash — the anchor |
| **in-toto attestations / Sigstore Rekor** — [in-toto attestation spec](https://github.com/in-toto/attestation/blob/main/spec/README.md) | Supplies chain a *subject* (a digest) to *predicates*, and Rekor logs those to a Merkle log | Overkill for a single-box deployment; name it if a client's auditor asks for a recognized format, not as a fix |

---

## 4. What the client can be told

Two claims, their honest strength, and what the auditor must be given for each.

### Claim 1 — "The file was not edited"

- What it means: every row's `row_hash` recomputes from its own contents, every `prev_hash` matches
  the row before it, and the whole file matches the manifest's `file_sha256`.
- What the auditor needs: the JSONL file and its `manifest.json`. Nothing else. That is the point of
  shipping both.
- Honest limit: this proves the file was not altered *after it was written*. It says nothing about
  whether the events were true when recorded. Rows can be false and still chained. Do not let
  "tamper-evident" drift into "accurate".
- With an anchor: the anchor adds no strength here beyond what the chain gives, unless it is signed,
  in which case it authenticates the artifact's origin.

### Claim 2 — "No rows were deleted from the end"

- **Can be supported, and needs a third artifact.** The anchor file is that artifact. Without an
  external commitment, the export and its manifest are both produced by the system under audit, so a
  truncation is indistinguishable from a short day — the verifier's own "no external anchor was
  supplied, so completeness cannot be shown" note is the correct and honest behaviour, and should stay.
- What the auditor needs, three things:
  1. the day's export and manifest,
  2. the anchor that covers that day (`last_id` inside or before the window), shipped off the box,
  3. **for the strongest form, the next day's export** — its `first_prev_hash` must equal this day's
     `last_row_hash`. This is what turns "the day's tail is not contradicted" into "the next day's file
     begins exactly where this one ended."
- The accurate sentence, which should go into `docs/audit-evidence.md` verbatim:

  > The export accounts for every row from the anchored chain state through its own declared final
  > row. Together with the next day's export, whose opening hash is this day's closing hash, the
  > sequence has no gap. What is not proved is that an event was recorded at all, and, where the
  > client controls the bucket, that the account's own administrator did not produce a different
  > chain and anchor it. Our record is evidence about us.

- Also state the residual fork gap: the anchor sequence currently has no external monitor or
  third-party timestamp, so a chain rewritten end to end and re-anchored would not be caught by
  anything in this system. That is a design gap, not a defect in the verifier, and it belongs in the
  limitations section rather than in a failure message.

### Where the third artifact genuinely cannot be avoided

If a deployment has no off-box anchor — an offline build with no removable media written — then claim
2 cannot be made at all, for any export. Say so in the artifact the client receives. The current
"cannot be shown" note already does this correctly; keep that behaviour and never upgrade it to a pass.

---

## 5. Backwards compatibility

**Old anchors remain usable. No migration, no re-export, no re-anchoring.** The current anchor format
already carries everything the position rule needs, in `services/audit/audit.py:530`:

```json
{ "deployment_id": …, "created_at": "…", "event_count": 49,
  "last_id": 49, "last_row_hash": "…", "last_occurred_at": "…" }
```

`last_id` has been there since the anchor was written; `cmd_verify` simply never reads it. That is rare
good news: the vocabulary needed for the fix was already being recorded.

For an auditor holding an old anchor:

- The old anchor keeps its full meaning. It commits to a position (`last_id`) and a hash, and the new
  rule evaluates it correctly against any day's export.
- Verdicts change, and the change is the point. Some old `FAIL` results become passes (the false
  accusations). Some old passes become *notes* instead of matches: an anchor that merely predates a
  window never proved anything about it, and the verifier will now say that rather than implying it did.
- Anchors with `last_id: null` or `last_row_hash: null` (the empty-chain case, which the code already
  anticipates at line 534) must keep reporting "this anchor proves nothing" rather than crashing.
- `event_count` stays readable in the file, so an analyst's existing notes survive. Nothing needs to
  be recomputed.
- If anchors are signed later, old unsigned anchors stay valid under a documented rule: an unsigned
  anchor is still a commitment, only an unauthenticated one. Say that in the limitations section.

What *does* become stale is prose. `docs/manual.md` (the "What the anchor check proves, exactly"
paragraph) and `docs/runbook.md` describe the count behaviour. Those sentences must change with the
code, including the honest sentence that an anchor does not prove nothing written after the anchored
row is missing. `scripts/check-doc-sync.py` covers the build docs; the audit prose is manual and easy
to miss.

---

## 6. Tomorrow, under time pressure

The smallest change that removes the false accusation is about six lines: gate the anchor check on
whether the export's window actually covers the anchored point, and report non-coverage as a fact
rather than a failure. Do not touch the anchor format, the export format, the schedule, or the good
branch of the verdict.

Ordered by value per line changed:

1. **Gate both branches on position.** In `cmd_verify`, before the membership test:

   ```python
   anchored_id = anchor.get("last_id")
   export_last_id = manifest.get("last_id") if manifest else None
   ```

   and refuse to run the membership/count test unless `export_last_id is not None and
   anchored_id is not None and anchored_id <= export_last_id`; report otherwise as a note such as
   *"the anchor commits to row 49 and this export ends at row 43, so this export cannot be checked
   against it."*

2. **Replace the count comparison.** `len(lines) < anchored_count` becomes a position comparison —
   fail when the export's `last_id` is *less than* the anchor's `last_id` and the anchored row is
   absent. Never compare a day's length to a cumulative count again.
3. **Reword the missing-row message.** Reserved for the case where the position rule says the row must
   be there. Today's wording asserts intent ("so rows that existed when the anchor was written are
   gone") on evidence that does not support it. State the observation: which rows the export declares,
   which row the anchor commits to, and that they do not line up.
4. **Add the two regression tests that would have caught this.** The suite passes because every
   scenario builds a single-day chain where the export *is* the whole chain. Add:
   - a multi-day chain (rows 1–49) exported as two days, one anchor at row 49 against the day holding
     rows 37–49 — must pass, and must pass with `13` rows against an anchor whose count is `49`;
   - an anchor taken after the day's last event (anchor at row 52, export ending at row 49) — must not
     report tampering, and must not claim completeness either.

   Both belong in `tests/test_audit_verify.py` using the existing `row()`/`chain()` helpers; the
   helpers need only a start index and an id offset. Note that the test `write()` helper's manifest
   omits `last_id` while the production manifest writes it, so the fixtures must gain the field or
   the position rule has nothing to read in CI — worth checking before trusting a green run.

If even that is too much for tomorrow, the one-line version is to stop calling the check when the
anchor's position is outside the export's window:

```python
elif anchored_count is not None and export_last_id < anchored_id:
```

That removes the reported failure and the lunchtime variant at once, leaves the completeness check
working for the case it was written for, and defers the wording. It is worse than the ordered list
above only because it leaves the message text unexamined, and the message is what a client will read.

**What must not be done under time pressure:** deleting the anchor check to silence the failure. It is
the only thing in the system that can catch a deleted tail, and a client whose auditor gets a clean
`ok` on a truncated record has been actively misled. A false accusation is bad. A false acquittal on
the one claim the artifact exists for is worse.
