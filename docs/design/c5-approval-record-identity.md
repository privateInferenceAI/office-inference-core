# The approval record when there is no verified identity

A decision document for C5, with the migration path for C7.

Written 2026-10-16. Sources are cited inline with URLs. Where a standard is paywalled or I could not
reach the text, I say so in section 9 rather than paraphrasing something I did not read.

---

## 1. The short answer

**Do not present the C5 approval as a signature, and do not write a sentence that names a person.**

Record the approval as what it observably is: an authorisation made by the bearer of a session
credential, at a time, whose identity was not verified. Put that fact in the record as a field, not as
prose in a footnote, so that every reader (the UI, the export, a future auditor, a future migration)
has to confront it.

Three findings drive this, and the first one is decisive.

**1. Identity verification is a precondition, not a nice-to-have.** 21 CFR 11.100(b): *"Before an
organization establishes, assigns, certifies, or otherwise sanctions an individual's electronic
signature, or any element of such electronic signature, the organization shall verify the identity of
the individual."* That is the FDA rule on electronic records and electronic signatures, and it says the
organisation must verify identity before the signature exists at all. A role-mapped development token
cannot satisfy it.

**2. The FDA dropped its enforcement of audit trails but kept electronic signatures.** The 2003
guidance *Part 11, Electronic Records; Electronic Signatures — Scope and Application* says FDA intends
to exercise enforcement discretion on validation, audit trails, record retention and record copying,
and then lists what it still intends to enforce. Electronic signatures are on that list: *"requirements
related to electronic signatures (e.g., §§ 11.50, 11.70, 11.100, 11.200, and 11.300)"*. So the part of
Part 11 that C5 is closest to is precisely the part that was never relaxed.

**3. A role is not a person, and FDA says so.** The 2018 guidance *Data Integrity and Compliance With
Drug CGMP* asks why shared logins are a problem, and answers: *"Shared, read-only user accounts that do
not allow the user to modify data or settings are acceptable for viewing data, but they do not conform
with the part 211 and 212 requirements for actions, such as second person review, to be attributable to
a specific individual."* An approval is a second-person review. A session that maps to "executive"
rather than to a named human is, in the terms of that guidance, not attributable.

The honest conclusion is uncomfortable and worth stating plainly: **an approval recorded today cannot be
made compliant with §11.50 by better wording.** The wording can be made true, which is a different and
achievable goal, and it is the one C5 should take.

---

## 2. What the standards actually require of an approval record

### 2.1 21 CFR Part 11 — the most specific source

Part 11 is short and unusually concrete. Its definitions matter first, because they decide which rules
apply.

**§11.3(4) defines a closed system** as *"an environment in which system access is controlled by persons
who are responsible for the content of electronic records that are on the system."* The on-premises
deployment is a closed system, which is the good case. §11.30's extra controls for open systems
(encryption, digital signature standards) do not bind in the same way.

**§11.50 is the field list for an approval record:**

> (a) Signed electronic records shall contain information associated with the signing that clearly
> indicates all of the following:
> (1) The printed name of the signer;
> (2) The date and time when the signature was executed; and
> (3) The meaning (such as review, approval, responsibility, or authorship) associated with the
> signature.
> (b) The items identified in paragraphs (a)(1), (a)(2), and (a)(3) of this section shall be subject to
> the same controls as for electronic records and shall be included as part of any human readable form
> of the electronic record (such as electronic display or printout).

Three elements: the printed name, the timestamp, and the meaning. C5 can establish two of the three
honestly. The first is the one it cannot.

**§11.70 requires the signature to be bound to the record:** *"Electronic signatures and handwritten
signatures executed to electronic records shall be linked to their respective electronic records to
ensure that the signatures cannot be excised, copied, or otherwise transferred to falsify an electronic
record by ordinary means."* The hash chain is a good answer to this and already exists.

**§11.100(a)** requires each signature to be *"unique to one individual and shall not be reused by, or
reassigned to, anyone else."* A role token is explicitly reassignable, so it fails this too.

**§11.200(a)(1)** requires *"at least two distinct identification components such as an identification
code and password."* One bearer token is one component.

**§11.10(e)** governs corrections, and is the basis for the migration design in section 5: audit trails
must *"not obscure previously recorded information."*

**§11.10(d)**, limiting system access to authorised individuals, and **§11.10(g)**, authority checks,
are both in the enforced set rather than the discretionary one.

### 2.2 SOC 2 and ISO 27001 — what they say, and the paywall

Both are weaker sources for this question than Part 11, and for the same structural reason: neither is
written to address identity proofing of a human. They address *accounts*.

**AICPA Trust Services Criteria** is copyrighted and sold by AICPA; I could not read the normative
control text, so I am not quoting it. What is publicly described is the shape: logical access controls
(CC6.x), the requirement that access be granted based on the principle of least privilege, and
monitoring of anomalies (CC7.2). SOC 2 asks whether access is *authorised and logged*. It does not, as
far as the public material indicates, ask whether the human behind an authenticated account was
identity-proofed. **That gap is a finding, not an oversight in my research**, and it is why a SOC 2
report will not catch this problem. If a client's auditor is a SOC 2 auditor, the misleading record
would likely pass.

**ISO/IEC 27001:2022 Annex A** was renumbered into 93 controls across four themes. The relevant ones,
by number and title, are A.5.15 Access control, A.5.16 Identity management, A.5.17 Authentication
information, A.5.18 Access rights, A.8.2 Privileged access rights, A.8.15 Logging, A.8.16 Monitoring
activities, and A.8.17 Clock synchronization. The titles are widely republished and consistent across
sources; **the normative text is behind the ISO paywall and I did not read it.** ISO/IEC 27002:2022
holds the implementation guidance and is also paywalled. A.5.16 (identity management) is the control
that would carry an identity-proofing requirement if one exists, and I cannot confirm from primary
text that it does.

### 2.3 The standards that do address the gap

Where the identity question is addressed directly, it is addressed by requiring attributability and by
refusing to bless the alternative.

**NIST SP 800-53 Rev. 5** is explicit and free to read. The control text below is from the official
OSCAL catalogue.

- **AU-3 (Content of Audit Records)** requires records to establish what, when, where, source,
  outcome, and *"Identity of any individuals, subjects, or objects/entities associated with the
  event."*
- **AU-3(1) (Additional Audit Information)** names the exact case at issue. Its guidance says
  organisations may consider additional content *"including, but not limited to, access control or flow
  control rules invoked and individual identities of group account users."* So NIST treats "which
  individual, inside this group account, did it" as an extra that must be captured when it can be.
- **AU-10 (Non-repudiation)** requires *"irrefutable evidence that an individual (or process acting on
  behalf of an individual) has performed"* a defined action. Its guidance states the scope: *"Types of
  individual actions covered by non-repudiation include creating information, sending and receiving
  messages, and approving information."* An approval is named.
- **AU-9** protects audit information from modification and deletion. **AU-9(3) (Cryptographic
  Protection)** requires cryptographic mechanisms, and its guidance names the technique: *"signed hash
  functions using asymmetric cryptography."* Note the asymmetry with the current design in section 4.
- **IA-4 (Identifier Management)** guidance says *"The management of individual identifiers is not
  applicable to shared system accounts."* A role token is a shared system account.
- **IA-5(a) (Authenticator Management)** requires *"Verifying, as part of the initial authenticator
  distribution, the identity of the individual, group, role, service, or device receiving the
  authenticator."* This is NIST's version of §11.100(b).
- **AC-2(9) (Restrictions on Use of Shared and Group Accounts)** guidance: *"organizations consider the
  increased risk due to the lack of accountability with such accounts."*

**HIPAA** is a useful public analogue because it separates the two ideas in one section. 45 CFR
164.312(a)(2)(i), *Unique user identification*, is marked **Required** and says *"Assign a unique name
and/or number for identifying and tracking user identity."* 164.312(b), *Audit controls*, requires
mechanisms that *"record and examine activity."* And 164.312(d), *Person or entity authentication*,
requires procedures *"to verify that a person or entity seeking access to electronic protected health
information is the one claimed."* The Required/Addressable distinction matters commercially: unique user
ID is Required, so it is not a judgement call a vendor gets to make.

**MHRA GXP Data Integrity Guidance and Definitions (Revision 1, March 2018)** is the most practically
useful document I found, because it describes the correction behaviour C5's migration needs. Section
6.13, on audit trails, requires that they show *"all changes to, or deletion of data while retaining
previous and original data"*, that *"It should be possible to associate all data and changes to data
with the persons making those changes"*, and that *"The reason for any change, should also be
recorded."* Section 6.14, on electronic signatures, lists what must be controlled, beginning with
*"How the signature is attributable to an individual."*

**PIC/S PI 041-1** (1 July 2021) states the same rule for corrections: *"The audit trail should include
all previous values and record changes should not overwrite or obscure previously recorded
information."*

**eIDAS** is the market's own admission that identity assurance is a tier rather than a boolean.
Article 26 sets the bar for an advanced electronic signature: *"(a) it is uniquely linked to the
signatory; (b) it is capable of identifying the signatory; (c) it is created using electronic signature
creation data that the signatory can, with a high level of confidence, use under his sole control; and
(d) it is linked to the data signed therewith in such a way that any subsequent change in the data is
detectable."* Requirement (a) is the one C5 cannot meet, and the reason eIDAS has three tiers is that
most signatures do not meet all four.

### 2.4 The distinction that resolves the whole question

NIST SP 800-63 splits assurance into two independent axes, and conflating them is the error at the
heart of the "approved by Dana" problem.

**Identity Assurance Level (IAL)** is about proofing the human, from SP 800-63A:

> **IAL1**: There is no requirement to link the applicant to a specific real-life identity. Any
> attributes provided in conjunction with the subject's activities are self-asserted or should be
> treated as self-asserted.
>
> **IAL2**: Evidence supports the real-world existence of the claimed identity and verifies that the
> applicant is appropriately associated with this real-world identity.

**Authenticator Assurance Level (AAL)** is about the strength of the credential, from SP 800-63B:

> **AAL1**: AAL1 provides some assurance that the claimant controls an authenticator bound to the
> subscriber's account.
>
> **AAL2**: AAL2 provides high confidence that the claimant controls an authenticator(s) bound to the
> subscriber's account.

Read those two together and the problem is precise. **Every authenticator level, including AAL3, is
about control of a credential bound to an account.** None of them says the account belongs to a
particular human. That is IAL's job, and IAL1 is the explicit statement that nobody checked.

C3's sessions are IAL1. They are not even AAL1 in a meaningful sense, because the token is minted by a
command-line flag. Adding TOTP, or WebAuthn, or a signed email link moves AAL and leaves IAL at 1. That
is the single most important sentence in this report for section 6.

Note on currency: SP 800-63-3 was superseded by SP 800-63-4 on 1 August 2025. The IAL and AAL framing
is unchanged and the older revision is the one most citations still use, so I have quoted 800-63-3 and
flagged the successor. The C7 implementation should read 800-63-4.

---

## 3. What the honest record looks like

### 3.1 The principle

Record what was observed, not what was inferred.

What C5 observes: a request arrived bearing a valid session credential; the session row named a subject
and a role; the decision was "approved"; it happened at a timestamp; the action authorised was X.

What C5 does **not** observe: a person.

Every design choice below follows from refusing to let the second list leak into the first.

### 3.2 The field design

`event_type` stays `'approval'`, because the event is an approval and the six-value CHECK constraint in
`002_audit_events.sql` should not be widened for a naming preference. The distinction lives in the
payload.

```json
{
  "kind": "authorization",
  "decision": "approved",
  "approval_id": "apr_01J...",
  "job_id": "job_01J...",
  "action": "invoice.pay",
  "action_digest": "sha256:9f2c...",
  "meaning": "authorises payment of invoice 4471 to Acme Supply, up to 12,400 USD",
  "identity": {
    "assurance": "unverified",
    "basis": "development_session_token",
    "assurance_version": 1,
    "claimed_subject": "dana",
    "claimed_role": "executive",
    "session_id": "sess_01J...",
    "session_source": "development",
    "verified_at": null,
    "verified_by": null,
    "verified_level": null
  },
  "part11_11_50_manifestation": {
    "printed_name_of_signer": { "present": false, "reason": "identity not verified" },
    "date_and_time":        { "present": true, "value": "2026-10-16T18:14:07.123456Z" },
    "meaning":              { "present": true, "value": "authorises payment of invoice 4471" }
  },
  "reviewer_note": null
}
```

Notes on the fields that earn their place:

- **`meaning`** is §11.50(a)(3) and costs nothing. It is also the field a client will actually use,
  because "what did this approval authorise" is the question asked two years later.
- **`action_digest`** binds the approval to the specific thing approved. Without it, an approval row
  says a person approved *something*, and the link to the invoice is a foreign key that can be
  re-pointed. This is the §11.70 concern, answered for the payload rather than the signature.
- **`claimed_subject` and `claimed_role`** are prefixed with `claimed` on purpose. The prefix is the
  caveat, in the field name, where it cannot be dropped by a UI that forgets to read a footnote. A
  reader grepping the export for `subject` finds `claimed_subject` and learns something true.
- **`part11_11_50_manifestation`** makes the gap machine-readable. This is the field that stops a
  future reader from assuming the record satisfies §11.50. It is also the field that a C7 migration
  updates by appending, not by editing.
- **`assurance_version`** exists so the meaning of the `assurance` enum can be revised without
  ambiguity about what an old row meant.

### 3.3 Distinguishing verified from unverified with no later schema change

The enum is written now, with all four values, and only one of them is currently reachable:

| `identity.assurance` | Means | Reachable |
|---|---|---|
| `unverified` | A session credential was presented. No person was identified. | C5, today |
| `provider_verified` | An identity provider asserted the subject and the assertion was cryptographically verified. | C7 (Cognito) |
| `proofed` | The subject was identity-proofed to a documented level, IAL2-equivalent or better. | future |
| `re_attested` | A later event restates this approval under a stronger assurance. | C7 migration |

Two rules make the enum do real work rather than decorate the record.

**Rule 1: unknown values resolve downward, never upward.** A reader that meets an `assurance` value it
does not recognise must treat it as `unverified`. A reader that meets a missing `identity` object must
treat it as `unverified`. This is fail-closed, and it means adding a value in C7 cannot accidentally
promote an old row.

**Rule 2: an approval's assurance never rises on read.** The display path must read `assurance` from the
record and never from the `sessions` table. The reason is already written down in this repository, for
a different field. `017_sessions.sql` says of `access_roots`: *"A later change to a group must not
change what an event recorded last week meant: the audit record has to be reconstructable as at the
time it was written, and a permission read live at comparison time is a permission that has since
moved."* Substitute identity for permission and the argument is identical. **An identity read live at
display time is an identity that was not verified when the act happened.**

### 3.4 Wording that is true

The record is read by humans, so the sentence matters as much as the field.

**Do not write:** *"Approved by Dana at 2:14pm."* It names a person the system cannot identify, and a
client will believe it. This is the failure mode named in `docs/handoff-c4.md`.

**Also do not write:** *"A person approved an action."* This is what `audit_feed` says today
(`002_audit_events.sql`, the `approval` branch). It is better than naming Dana and it is still an
assertion that a person did it. The system does not know that.

**Write instead, in the UI:** *"Approved at 2:14pm under the executive role. The person who approved
was not identified: sign-in is not enabled yet, so this decision is recorded against a role rather than
a person."*

**Write instead, in the export and the API:** *"Authorization recorded at 2026-10-16T18:14:07Z for
`invoice.pay`. Session identity assurance: unverified. The approval is attributable to a session
holding the executive role, not to an identified individual."*

**Write instead, in the banner above the Approve button:** *"Identity not verified. This approval will
be recorded against a role, not a person."* A person clicking Approve should be told, before they
click, what the record will say about them. That is both honest and a useful pressure toward C7.

The concrete change to `audit_feed`'s summary expression, in the same style as the existing `case`
block:

```sql
when event_type = 'approval' then
  case
    when payload -> 'identity' ->> 'assurance' in ('provider_verified','proofed','re_attested')
      then 'an identified person ' || outcome || ' an action'
    else 'an approval was recorded under a role (identity not verified)'
  end
```

That expression is a fallback-safe version of Rule 1: anything unexpected lands in the honest branch.

### 3.5 A schema finding that C5 has to decide

**`audit_events.approval` (jsonb) is not covered by the hash chain.**

This is verifiable from the code. The canonical string at chain_version 2, in
`db/migrations/013_chain_covers_redaction.sql` and mirrored in `services/audit/audit.py`, covers
exactly: `ts_iso`, `event_type`, `actor`, `actor_role`, `deployment_id`, `data_class`, `outcome`,
`tool`, `model`, `request_id`, `payload_sha256`, `payload_bytes`, `prev_hash`, `redaction_count`,
`redactions`, `unverified_card_shapes`, `egress_allowed`. The `approval` column is not among them.
`services/audit/audit.py` sets `KNOWN_CHAIN_VERSION = 2`.

So a value written into `approval` can be edited by hand and every chain check still passes. This is
the same defect class that migration 013 was written to fix for the four redaction columns, and 013's
own comment describes the consequence exactly: *"a field the chain does not cover is a field that can
be rewritten while every check passes, and the check would report the record intact."*

There is also no writer yet. Nothing in `services/` inserts an `event_type = 'approval'` row, so C5 is
the first, and the choice is still open.

**Recommendation:** put the approval record in `payload`. Its SHA-256 is already in the chain via
`payload_sha256`, so it is covered by commitment with no schema change and no new chain version. Then
either drop the `approval` column or, if it is kept for query convenience, bump to chain_version 3 and
bring it in using 013's technique (flatten to sorted `key=value` pairs, version the formula per row,
never recompute old rows with the new formula). Do not leave an unchained column that the next
developer will reasonably assume is part of the record.

---

## 4. What the chain does and does not prove

Worth stating before the migration design, because the migration's honesty depends on it.

The chain is a simple hash chain: each row's hash covers the previous row's hash. That proves ordering,
and it detects any edit to a row that is not accompanied by a recomputation of every later row.

On its own, a simple hash chain does **not** stop an operator who controls the database from rewriting
history wholesale and recomputing every hash forward. The chain is tamper-evident only against someone
who cannot rewrite the tail, which in a single-box deployment is nobody with the database credentials.

The design already accounts for this, and the mitigation is the right one. `docs/audit-evidence.md`
sends an **anchor** offsite: *"the chain head at a moment in time: the last row's hash and the event
count"*, written to the client's bucket, and *"Nobody, once it is in the bucket. That is the point."*
Once an anchor exists that the box cannot reach, a rewritten chain fails the count and the head hash.
The residual exposure is the window between anchors, and that window should be named in the client
contract rather than left implicit.

The stronger construction is a Merkle tree with signed tree heads rather than a linear chain, which is
what RFC 9162 specifies for Certificate Transparency. Its §2.1.4: *"Merkle consistency proofs prove the
append-only property of the tree."* A log with a Merkle tree can hand a sceptical third party a short
proof that today's tree contains yesterday's, without revealing the entries. A linear chain cannot do
that; it can only be re-verified end to end.

This is an upgrade to consider, not a change to make now. Anchoring is sufficient for the current
threat model, and moving to a Merkle tree is a C7-or-later decision that would itself need a
chain_version bump. But the anchor should be built, because without it the chain's guarantee is weaker
than the word "verifiable" suggests.

---

## 5. Migration when identity arrives at C7

### 5.1 What the audit standards permit

Every source that requires immutability also provides for correction, and they agree on the mechanism.

- **21 CFR 11.10(e):** audit trails must be secure, computer-generated and time-stamped, and *"Record
  changes shall not obscure previously recorded information."*
- **MHRA GXP Data Integrity 6.13:** audit trails retain *"previous and original data"*; changes are
  associated with the person making them; *"The reason for any change, should also be recorded."*
- **PIC/S PI 041-1:** *"The audit trail should include all previous values and record changes should not
  overwrite or obscure previously recorded information."*
- **SEC 17 CFR 240.17a-4(f)(2)(i)(A)** is the clearest regulatory statement that an audit trail is an
  accepted alternative to immutable media. It permits an electronic recordkeeping system that
  *"maintains a complete time-stamped audit trail that includes: (1) All modifications to and deletions
  of the record or any part thereof; (2) The date and time of actions that create, modify, or delete the
  record; (3) If applicable, the identity of the individual creating, modifying, or deleting the
  record."* Subparagraph (B) offers the alternative of *"a non-rewriteable, non-erasable format."* The
  SEC amended this in 2022 to allow the audit-trail route explicitly, which is a useful precedent: the
  regulator preferred a complete trail of changes over write-once media.

Read together: **append a corrective record, never edit the original, and record the reason.** That is
exactly what an append-only chain can do. The constraint is not an obstacle to correction; it is the
shape correction is supposed to take.

Note the *"if applicable"* in 17a-4(f)(2)(i)(A)(3). Even the SEC's audit-trail rule treats the identity
of the actor as conditional. It is the only place in this research where a regulator concedes that
identity may not be recordable, and it is worth knowing that the concession exists and is narrow.

### 5.2 This repository already has the pattern

`db/migrations/013_chain_covers_redaction.sql` is the template, and it solved the general problem
already. Its own reasoning:

> Why this needs a version rather than an edit. A row's stored hash was computed from a canonical
> string, and verification recomputes that string and compares. Change the formula and every row written
> under the old one fails, which is indistinguishable from tampering: the verifier would report a chain
> break on rows nobody touched, and the first real break would be lost in the noise. So the formula is
> versioned and each row records which one produced its hash.

That is the answer to "how do you change what the chain covers without rewriting it", and it is built,
tested, and in production on the development instance. Record identity is the same problem shape.

`019_the_credential_is_not_the_record.sql` supplies the second pattern, for the case where a defect
cannot be repaired. Its sessions were revoked rather than migrated, *"because their credentials are the
values already written into the chain, so there is nothing to hash and nothing to rotate: the only
honest move is to stop honouring them. Their rows stay, because the chain is hash-linked and rewriting
it would break the verification the whole record rests on."* The exposure was recorded in
`docs/review-log.md` *"rather than tidied away"*.

That instinct is the one C7 needs. Hold onto it for section 5.4.

### 5.3 The mechanism: a superseding event

For an individual approval that needs a stronger statement later, append a new event that points at the
old one.

```json
{
  "kind": "reattestation",
  "supersedes_event_id": 1043,
  "supersedes_row_hash": "3b1f...",
  "original_assurance": "unverified",
  "new_assurance": "provider_verified",
  "basis": "sessions.source changed to provider at C7; subject resolved to a Cognito sub",
  "resolved_subject": "cognito|us-east-1|<sub-uuid>",
  "attested_by": "cognito|us-east-1|<sub-uuid>",
  "reason": "identity provider established; historical approval re-stated under verified subject",
  "limits": "establishes that the account holding session sess_01J... is now known to belong to this subject; does not establish that this subject personally made the decision"
}
```

Three details that make this work rather than merely look right:

- **Point at both the id and the row_hash.** The id alone can be re-pointed by anyone who can insert a
  row, because ids are sequential and guessable. Including the target's `row_hash` makes the reference
  itself tamper-evident: an attacker who re-points the link must also produce a row whose hash matches,
  which means rewriting the chain.
- **Carry `limits`.** A re-attestation that overstates what it establishes is the same defect as the
  original sentence, one layer up. The field forces the author to write down what the event does not
  prove, and it is the field an auditor will read first.
- **Do not use `event_type = 'approval'`.** A re-attestation is not an approval. Add `'reattestation'`
  to the CHECK constraint in `002_audit_events.sql`. That is a DDL change and it does not touch a single
  existing row, so it is safe under the append-only trigger.

RFC 9162's model is the same idea done cryptographically: a log never deletes or rewrites an entry, it
appends a statement and proves the new tree is consistent with the old. A revocation in Certificate
Transparency does not remove the certificate; it adds a signed statement about it.

### 5.4 The limit of migration, stated plainly

**You cannot upgrade a historical approval's assurance.**

When C7 lands, the honest outcome for every approval recorded during C5 is that it remains
`unverified` forever, and a `reattestation` event is appended that says which account held the session.
That is all that can truthfully be said, for three reasons:

1. The session token was a bearer credential. Whoever held it could approve. There is no evidence about
   who that was, and there never will be.
2. If the deployment ran on a shared workstation, which is the small-business case the product targets,
   the token may have been pasted onto a colleague's screen. Nothing in the record distinguishes that.
3. Even a subsequent statement by a named person that they made the decision is a new claim made later,
   not evidence about the original act. It is admissible as a re-attestation and it is not a
   retroactive verification.

So the C7 migration is not "make the old approvals verified". It is "record, once, that the old
approvals are unverifiable, and make the boundary visible". A client reading a C7-era export should be
able to see a clean line: everything before the cutover is role-attributed, everything after is
person-attributed, and the cutover event says when and why.

That boundary is worth building deliberately. It is the difference between a system that accumulated a
year of weak records and hid it, and one that has a dated, explainable discontinuity.

### 5.5 If the chain formula itself must change

Only if the `approval` column is brought into the chain, or a new field needs coverage. Then follow 013
exactly: add `chain_version 3`, extend `audit_row_canonical` with a version branch, extend
`row_canonical` in `services/audit/audit.py` to match field for field, raise `KNOWN_CHAIN_VERSION` to
3, and let new rows carry 3 while old rows keep the version they were written with. The two functions
must change in the same commit, because a drift between the Postgres writer and the Python verifier
reports untouched history as tampering.

**GDPR note, for completeness.** Article 16 gives a data subject the right to rectification of
inaccurate data, and it is sometimes raised as a reason to edit an audit log. It is not. Article 16
concerns inaccurate personal data; a correctly-recorded fact that a session authorised an action is not
inaccurate, and the Appendix to Article 17(3) preserves processing necessary for compliance with a
legal obligation. The practitioner answer, and the one the audit standards above require anyway, is to
append a corrective record rather than to alter the original. I found no supervisory authority ruling
on the specific tension between Article 16 and immutable audit logs, so this is the well-supported
reading rather than a settled one.

---

## 6. The middle option: what a lightweight identity step actually proves

The question is whether C5 should build something cheap that closes part of the gap. The answer depends
entirely on which axis it moves.

**Every option below moves AAL. None of them moves IAL except the last two, and those are not cheap.**
That is the finding, and it is why the recommendation is not to build one in C5.

| Mechanism | What it actually proves | What it does not prove | Build cost | Auditor view |
|---|---|---|---|---|
| **Signed email link** (magic link, HMAC, short expiry) | Control of a mailbox at that moment, and that mail to that address is readable by whoever clicked | That the named person read it. Mail is forwarded, shared, and read on a phone someone else is holding | Low in code, but this deployment has no mail path at all: no SMTP service in the compose file, and a self-hosted box has no deliverability story. Outbound mail means either a relay through the client's own server or an external provider, which is a network-egress decision the product's constraint makes expensive | Proves possession of a mailbox, not identity. FDA's shared-account reasoning applies unchanged if the inbox is shared, which in a small business it often is |
| **TOTP** (authenticator app) | Possession of the shared secret, and that whoever holds it also knows the password if used as a second factor | Anything about who holds the device. The secret is enrolable by whoever is signed in when enrolment happens | Low to moderate. Enrolment, recovery codes, clock skew, and a binding to an account are all needed | Moves AAL from 1 to 2. IAL unchanged. An auditor will accept it as strong authentication and will still ask who the account belongs to |
| **SMS OTP** | Control of a phone number at that moment | Identity. SIM swap and number recycling are live attacks, and NIST has treated SMS as a restricted authenticator since 800-63B | Low, and adds a paid provider and a data-residency question | Weakest of the authenticators here, and the one most likely to be questioned |
| **WebAuthn / passkeys** | Possession of a private key bound to an account, with phishing resistance and origin binding | Identity. A synced passkey proves control of a credential synced through a platform account, which is an account, not a person | Moderate to high: registration ceremony, recovery, lost-device path, and credential storage | The strongest authentication available, and an auditor will be pleased. It answers "is this the same account" and not "who is this" |
| **Signed PDF returned**, with a CA-issued certificate bound to a proofed identity | Control of a private key, and if the certificate was issued after identity proofing, that a certification authority associated the key with a proofed person | Nothing extra beyond what the CA's proofing did, which is why the CA's process is the whole question | High: key issuance, proofing, revocation, verification, long-term validation | The only mechanism here that moves IAL, because eIDAS Article 26(a) becomes satisfiable. This is what qualified electronic signature means, and it is why QES is expensive |
| **Full IdP** (Cognito, as planned) | An asserted subject with a verified token, plus whatever proofing the pool configuration performs | Identity, if the pool self-asserts attributes and never proofs them. Cognito can be configured either way, and the default is self-asserted | C7 scope already | Depends entirely on pool configuration. A self-signup Cognito pool is IAL1 with better cryptography |

### 6.1 The distinction, stated the way the question asks

*Proves possession of a mailbox* means: at 2:14pm, someone who could read mail sent to
`dana@example.com` clicked a link. It is evidence about **a mailbox and a moment**. It is genuinely
useful, because it establishes that the approval was not forged by the system itself, and it gives an
investigator a place to look.

*Proves a person's identity* means: the system knows which human being made the decision, to a stated
level of confidence, established through evidence that was validated against a real-world source. Per
SP 800-63A that requires identity resolution, validation of evidence against authoritative sources, and
verification that the person presenting the evidence is the person the evidence describes. In a
business context it usually also requires that the organisation has a record of which human holds which
account, which is an HR process more than a technical one.

Those are different claims, and the gap between them is not closed by better cryptography. **A perfect
implementation of a signed email link proves a mailbox with cryptographic certainty and still proves
nothing about the human.** This is why the honest record format matters more than the mechanism: the
format is what stops a strong mechanism from being read as a claim it never made.

### 6.2 The cheapest thing worth building in C5

Not an authenticator. Two smaller things:

1. **The approval gate should require the operator to explicitly confirm the action, not just click a
   button.** A typed confirmation of the action, or a re-entry of the role, is not identity and does not
   pretend to be. It creates a second, deliberate act, which is what the record will describe.
2. **A `reviewer_note` field.** A free-text field where the approver can write why. MHRA 6.13 requires
   the reason for a change to be recorded, and this is the C5 half of that requirement. It costs one
   column in a payload and it is the field a client's auditor will find most useful, because it is the
   only part of the record that came from a human rather than from the system.

Neither is identity. Both are honest, and both survive into C7 unchanged.

---

## 7. What comparable products do

### 7.1 E-signature services

The pattern across the market is the same, and it is worth naming because it is the commercial proof
that identity assurance is a separately-priced tier rather than a default.

**Standard electronic signature, which is the default, establishes control of an email inbox.** The
audit artifact records what happened mechanically. DocuSign's Certificate of Completion documentation
states that it *"Provides details about each signer on the document. This information includes the
signer's IP address and other identifying information"*, and DocuSign's identity-verification glossary
material states *"We record the signer's IP address and the date and time of the signature."* What the
certificate does **not** contain is any statement that the signer's identity was established, because in
the default flow nothing established it.

**Identity verification is a separate product with several levels.** DocuSign sells ID Verification as an
add-on with options including knowledge-based authentication, an access code delivered out of band,
government-ID document verification, and selfie/liveness matching. Dropbox Sign and Adobe Acrobat Sign
offer the same shape: an audit trail by default, and a configurable verification step on top. Adobe
markets its digital-signature tier separately again, which adds a certificate rather than an identity
check.

Two things follow. First, the market's own product structure concedes the point in section 6: a
signature event and an identity check are different products, and the second is optional. Second, the
audit certificate records the **mechanism** ("an access code was sent and entered") rather than a
conclusion ("this was Dana"). That is the pattern worth copying, and it is the same discipline as
recording `basis` and `assurance` rather than a bare name.

**Source quality caveat.** DocuSign's support pages are JavaScript-rendered and I could not retrieve the
full text of the Certificate of Completion page, only the official search-result excerpts quoted above.
These are vendor help-centre documents, not specifications. I did not verify every verification option
currently sold, and product tiers change. Treat this subsection as a description of the pattern rather
than a current feature matrix.

### 7.2 ERP and accounting software

The pattern is the account, and nothing above the account.

Oracle's NetSuite documentation describes an Audit Trail feature built on System Notes, and System Notes
records the date of the change, the user who made it, and the role of that user. Approval routing in
NetSuite records which account approved. Oracle's Audit Enablement documentation is the primary source
available without a login.

**My hypothesis, stated as a hypothesis:** none of these systems records whether the approver's identity
was verified, because all of them treat "an authenticated account performed the approval" as
sufficient and none of them has a field for the strength of the identification behind the account. If
that is right, then a small-business client comparing this product to NetSuite would see an unfamiliar
field, and the field is the differentiator rather than a compliance tax.

**I could not verify that hypothesis.** NetSuite's, SAP's, Workday's, Coupa's, QuickBooks Online's,
Xero's and Sage Intacct's approval-record contents are behind product logins or documented only in
marketing material, and what I found was secondary commentary rather than vendor specification. It
should be tested before it is put in front of a client. The cheap test is to ask a client's accountant
to show the approval history on a journal entry and look for any field that describes how the approver
was identified. I expect none, and I would rather the claim be checked than asserted.

### 7.3 Why this matters for pricing

If no ERP records identity assurance, then this field is not table stakes and its absence will not be
noticed by a buyer. Its presence is visible only to someone who has read Part 11 or been through a GxP
audit. That is an argument for building it correctly and not for building it loudly. The value is that
the record survives scrutiny, not that it advertises itself.

---

## 8. Recommendation for C5

**Build the record. Do not build an authenticator.**

Five concrete decisions.

**1. Record the approval in `payload`, not in the `approval` column.** The `approval` jsonb column is
outside the hash chain at chain_version 2, verified from `audit_row_canonical` and `row_canonical`.
Using `payload` gets the record covered by `payload_sha256` with no schema change and no new chain
version. If `approval` is kept for query convenience, bring it into the chain with a version 3 bump
using migration 013's technique. Do not leave it unchained.

**2. Ship the `identity` object with the full four-value enum, and only `unverified` reachable.** The
enum is the mechanism that lets C7 land without a schema change. Unknown values resolve to
`unverified`, and the display path reads `assurance` from the record and never joins to `sessions`.

**3. Ship `part11_11_50_manifestation` with the name marked absent and the reason stated.** This is
what makes the gap legible to a future reader instead of a footnote someone removes from a UI. It also
gives C7's migration a field to flip by appending rather than editing.

**4. Change the language in the UI, the API and `audit_feed`.** No sentence names a person. The Approve
button carries a banner saying the approval will be recorded against a role. `audit_feed`'s `approval`
branch stops saying *"a person approved an action"* and says what is true, with the fail-closed
`case` from section 3.4.

**5. Put the boundary in the client contract, now.** One short paragraph: approvals recorded before
sign-in is enabled are attributed to a role and cannot be retroactively attributed to a person. It is
much easier to say this before the client has a year of records than after. `019`'s instinct applies:
record the limitation rather than tidy it away.

**What C5 should not do:** add TOTP, WebAuthn, or an email-link flow. Each moves AAL and leaves IAL at
1, each adds an enrolment and recovery surface that C7 will replace or duplicate, and each makes the
record *look* stronger than it is, which is the exact failure this whole exercise is about. The
cheapest honest upgrade is not a better authenticator; it is a record that says what happened.

---

## 9. What I could not verify

Stated so the reader knows the shape of the evidence, not only its content.

| Item | Status |
|---|---|
| **SOC 2 Trust Services Criteria normative text** | Copyrighted and sold by AICPA. I did not read it and have not quoted it. The publicly described structure (CC6 logical access, CC7 monitoring) is what section 2.2 is based on |
| **ISO/IEC 27001:2022 and 27002:2022 normative control text** | Behind the ISO paywall. Control numbers and titles are verified as consistent across several secondary sources; the wording of the requirements is not verified, and I assert nothing about it |
| **Does ISO 27001 require identity proofing of a human?** | Could not determine. A.5.16 Identity management is where it would live. Needs someone with the standard in hand |
| **DocuSign Certificate of Completion full text** | Page is JavaScript-rendered; only official search-result excerpts were retrievable. Quoted as such |
| **Dropbox Sign and Adobe Acrobat Sign audit-record field lists** | Not verified from vendor documentation. Described only at the level of product shape, which is consistent across secondary sources |
| **ERP and accounting approval-record contents** | Not verified, as detailed in section 7.2. Section 7.2's hypothesis is labelled as unverified and should be tested before external use |
| **21 CFR Part 11 §11.100(c)** | Requires signers to certify to the agency that their electronic signatures are the legally binding equivalent of handwritten signatures. It is quoted in my source but I have not analysed its applicability, and it may be a live obligation for a regulated client rather than a dormant one |
| **SEC 17a-4's applicability** | It binds broker-dealers. I cite it as evidence of regulatory reasoning about audit trails versus WORM media, not as a rule that applies to this product |
| **EU legal texts** | EUR-Lex returned HTTP 202 with an empty body for every retrieval attempt. eIDAS Article 26 and GDPR Article 16 are therefore quoted from the retained-EU-law text on legislation.gov.uk, which is the UK's official version. The Article 26 text is identical to the EU original for the purposes of this quotation; the UK retained version should not be relied on for anything beyond the quoted words |
| **SP 800-63 revision currency** | SP 800-63-3 was superseded on 2025-08-01 by SP 800-63-4. I quoted -3 because it remains the most-cited framing. C7 should work from -4 |
| **NIST SP 800-53 control quotes** | Taken from the official OSCAL JSON in the usnistgov/oscal-content repository, which is the machine-readable publication of the catalogue. Operational parameter placeholders (`{{ insert: param, ... }}`) appear in the control statements because they are organisationaly-defined values |

---

## 10. Sources

Primary regulatory and standards text:

- 21 CFR Part 11, *Electronic Records; Electronic Signatures* — https://www.ecfr.gov/current/title-21/chapter-I/subchapter-A/part-11
  (§11.3, §11.10, §11.50, §11.70, §11.100, §11.200, §11.300; retrieved via the eCFR versioner API)
- FDA, *Part 11, Electronic Records; Electronic Signatures — Scope and Application*, September 2003 — https://www.fda.gov/regulatory-information/search-fda-guidance-documents/part-11-electronic-records-electronic-signatures-scope-and-application
- FDA, *Data Integrity and Compliance With Drug CGMP: Questions and Answers*, December 2018 — https://www.fda.gov/media/119267/download
- MHRA, *GXP Data Integrity Guidance and Definitions*, Revision 1, March 2018 — https://assets.publishing.service.gov.uk/government/uploads/system/uploads/attachment_data/file/687246/MHRA_GxP_data_integrity_guide_March_edited_Final.pdf
- PIC/S, *PI 041-1: Good Practices for Data Management and Integrity*, 1 July 2021 — https://picscheme.org/docview/4234
- 45 CFR §164.312, HIPAA Security Rule technical safeguards — https://www.ecfr.gov/current/title-45/section-164.312
- 17 CFR §240.17a-4, records preservation, paragraph (f) — https://www.ecfr.gov/current/title-17/section-240.17a-4
- NIST SP 800-53 Rev. 5 — https://csrc.nist.gov/pubs/sp/800/53/r5/upd1/final (control text retrieved from https://raw.githubusercontent.com/usnistgov/oscal-content/main/nist.gov/SP800-53/rev5/json/NIST_SP-800-53_rev5_catalog.json)
- NIST SP 800-63-3, *Digital Identity Guidelines* — https://pages.nist.gov/800-63-3/
  (800-63A for IAL: https://pages.nist.gov/800-63-3/sp800-63a.html, 800-63B for AAL: https://pages.nist.gov/800-63-3/sp800-63b.html)
- NIST SP 800-63-4, the current revision — https://pages.nist.gov/800-63-4/
- eIDAS, Regulation (EU) 910/2014, Article 26 — https://www.legislation.gov.uk/eur/2014/910/article/26
- GDPR, Regulation (EU) 2016/679, Article 16 — https://www.legislation.gov.uk/eur/2016/679/article/16
- RFC 9162, *Certificate Transparency Version 2.0* — https://www.rfc-editor.org/rfc/rfc9162.html

Standards bodies and vendor documentation:

- ISO/IEC 27001 — https://www.iso.org/standard/27001
- AICPA SOC 2 and Trust Services Criteria — https://www.aicpa-cima.com/topic/audit-assurance/audit-and-assurance-greater-than-soc-2
- DocuSign, Certificate of Completion — https://support.docusign.com/s/document-item?language=en_US&bundleId=oeq1643226594604&topicId=gpa1578456339545.html
- DocuSign, Identity Verification glossary — https://support.docusign.com/s/document-item?language=en_US&bundleId=miz1695056924393&topicId=amm1695057214224.html
- Oracle NetSuite, Audit Enablement — https://docs.oracle.com/en/cloud/saas/netsuite/ns-online-help/section_4604128838.html

Internal documents this report depends on:

- `db/migrations/002_audit_events.sql` — the table, the append-only trigger, the chain function, `audit_feed`
- `db/migrations/013_chain_covers_redaction.sql` — the versioned-formula migration pattern
- `db/migrations/017_sessions.sql` — the `source` provenance column and the snapshot argument
- `db/migrations/018_jobs.sql` — the job state machine and `awaiting_approval`
- `db/migrations/019_the_credential_is_not_the_record.sql` and `020_a_job_holds_a_hash_too.sql` — the revoke-rather-than-migrate pattern
- `services/audit/audit.py` — `row_canonical`, `KNOWN_CHAIN_VERSION`
- `docs/audit-evidence.md` — the anchor design
- `docs/handoff-c4.md` — the originating statement of the wording problem
