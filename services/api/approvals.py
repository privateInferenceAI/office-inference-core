"""What an approval records, and what it refuses to claim.

**The one place in the product where a wrong record is worse than a missing one.** A client reads an approval
and believes it. Everything here exists to make the record say exactly what happened and no more, and the
whole design turns on a single uncomfortable fact: **this deployment cannot verify who a person is.**

Real authentication arrives in C7. Until then a session is a token that maps to a role, and the honest
description of what the system knows is "somebody holding this token asked for this". The record says that,
in those words, and the fields are named so that an interface cannot drop the caveat by choosing not to
render a paragraph.

**Why this is its own module rather than a dict built at the route.** Two writers will exist: this route now,
and the re-attestation path at C7. A shape defined twice is a shape that diverges, and a diverged audit record
is the failure this project has spent all day finding examples of.

**The research behind it** is `docs/design/c5-approval-record-identity.md`. The load-bearing citations:
21 CFR 11.100(b) makes identity verification a precondition to an electronic signature existing rather than
later hardening, and the FDA's 2003 enforcement discretion kept the signature rules when it relaxed everything
else around them. Section 11.50 wants a printed name, a timestamp and the meaning, and this records two of
three honestly.
"""
from __future__ import annotations

from typing import Optional

#: The four assurance values, and **only the first is reachable today.**
#:
#: `re_attested` is not a level on the same scale. It is a statement appended at C7 about what is known now
#: about an approval made earlier, and it can never raise the original's assurance, because the original token
#: was a bearer credential and the workstation is often shared. A later statement by a named person is a new
#: claim rather than evidence about the act.
ASSURANCE_UNVERIFIED = "unverified"
ASSURANCE_PROVIDER_VERIFIED = "provider_verified"
ASSURANCE_PROOFED = "proofed"
ASSURANCE_RE_ATTESTED = "re_attested"

ASSURANCE_VALUES = (ASSURANCE_UNVERIFIED, ASSURANCE_PROVIDER_VERIFIED, ASSURANCE_PROOFED,
                    ASSURANCE_RE_ATTESTED)

#: What this build can actually establish. One value, and the shortness of this tuple is the point.
REACHABLE_ASSURANCE = frozenset({ASSURANCE_UNVERIFIED})

#: How the system came to know the name it is about to write down. A role is not a person, and a bearer token
#: is not a signature, so the basis is recorded beside the claim rather than assumed from it.
BASIS_SESSION_TOKEN = "session_token"
BASIS_PROVIDER = "identity_provider"
BASIS_PROOFING = "identity_proofing"

DECISION_APPROVE = "approve"
DECISION_DENY = "deny"
DECISION_REQUEST_CHANGES = "request_changes"

DECISIONS = (DECISION_APPROVE, DECISION_DENY, DECISION_REQUEST_CHANGES)

#: **Two of the three decisions require a reason and approve does not.** A denial is the decision a client
#: learns from next time, so a blank one teaches nothing. An approval needs no justification to be
#: intelligible: the thing being approved is right there in the record.
DECISIONS_REQUIRING_REASON = frozenset({DECISION_DENY, DECISION_REQUEST_CHANGES})

#: The sentence a reader gets when this deployment cannot name the person.
UNVERIFIED_REASON = ("sign-in is not enabled on this deployment, so the system cannot verify who acted")


def resolve_assurance(value: Optional[str]) -> str:
    """A stored assurance value, **resolved downward when it is unknown.**

    This is the small function the whole design leans on. A reader that meets a value it does not recognise
    has two choices, and one of them fails safely. Resolving upward would let a typo, an old schema or a
    hand-edited row claim a verification that never happened, which is the exact failure the field exists to
    prevent. Anything unrecognised, missing or malformed becomes `unverified`.

    A future version that adds a value has to change this function, and that is deliberate: adding assurance
    should be a code change somebody reviews rather than a string somebody writes.
    """
    if isinstance(value, str) and value in ASSURANCE_VALUES:
        return value
    return ASSURANCE_UNVERIFIED


def identity_for(session) -> dict:
    """Who the system believes asked, and what it does not know about them.

    The fields are prefixed `claimed_` rather than `subject_` and `role`. **The caveat lives in the field
    name**, where a screen, an export or a third-party reader cannot drop it by leaving out a paragraph.

    `assurance` is `unverified` unconditionally today. It is written by calling `resolve_assurance` on the
    session's own claim rather than hard-coded, so that C7 changes one line rather than finding every place
    that assumed.
    """
    claimed = getattr(session, "assurance", None)
    return {
        "assurance": resolve_assurance(claimed),
        "basis": BASIS_SESSION_TOKEN,
        "claimed_subject": getattr(session, "subject", "") or "unknown",
        "claimed_role": getattr(session, "role", "") or "unknown",
    }


def part11_manifestation(decision: str, action_digest: str, signed_at: str) -> dict:
    """Section 11.50 wants three things, and this states which of them it can supply.

    `printed_name` is **null with the reason beside it** rather than absent. An absent field invites the
    reader to assume the record simply does not carry a name; a null with a reason tells them the record
    looked, could not find one, and is saying so.

    This is also the field C7 flips. A re-attestation appends a new record rather than editing this one, so
    the manifestation is a statement about what was true when the approval was made and stays true.
    """
    return {
        "printed_name": None,
        "name_absent_reason": UNVERIFIED_REASON,
        "signed_at": signed_at,
        "meaning": f"{decision} the action described by action_digest",
    }


def validate(decision: str, reason: str) -> Optional[str]:
    """The refusal a caller gets, or None when the decision is one this route may record.

    Returned rather than raised so the route decides the status code, and so the same function is the check
    the tests call. **A denial without a reason is refused rather than stored blank**: a blank reason in the
    record is indistinguishable from a client that declined to give one, and the next reader cannot tell.
    """
    if decision not in DECISIONS:
        return (f"'{decision}' is not a decision. The three are {', '.join(DECISIONS)}.")
    if decision in DECISIONS_REQUIRING_REASON and not (reason or "").strip():
        return (f"a '{decision}' needs a reason. It is what the next run learns from, and a blank one "
                f"cannot be told apart from a caller that declined to give one.")
    return None


def build_payload(session, job, decision: str, reason: str, note: str, action_digest: str,
                  signed_at: str) -> dict:
    """The record, in the shape the audit row will carry.

    **It goes in `payload`, not in `audit_events.approval`.** That column is a jsonb field with an approval
    event type and it is not among the eighteen fields the canonical hash function takes, so a value written
    there can be edited by hand while every chain check passes. It is the identical defect migration 013 was
    written to close for the redaction columns, found before C5 became its first writer.
    `payload_sha256` already covers payload, so this needs no schema change and no new chain version.
    """
    return {
        "approval": {
            "decision": decision,
            "reason": (reason or "").strip(),
            "note": (note or "").strip(),
            "action_digest": action_digest,
            "job_id": job.job_id,
            "task": job.task,
            "state_before": job.state,
            "identity": identity_for(session),
            "part11_11_50_manifestation": part11_manifestation(decision, action_digest, signed_at),
        },
        "stage": "approval",
        "job_id": job.job_id,
        "task": job.task,
        "decision": decision,
    }


def describe(record: dict) -> str:
    """The approval as a sentence a client reads, assembled from the record rather than beside it.

    **This exists because the feed used to lie.** Migration 002 renders an approval as
    `'a person ' || outcome || ' an action'`, which asserts a person the system has not identified. The
    replacement says what is known and states plainly what is not.

    It reads `assurance` from the record and never from the live session. A display that joined to the session
    would show today's assurance beside an act from last week, which is the same mistake `017_sessions.sql`
    names for permissions: a permission read live at comparison time is a permission that has since moved.
    """
    approval = (record or {}).get("approval") or {}
    decision = approval.get("decision") or "decided"
    identity = approval.get("identity") or {}
    when = (approval.get("part11_11_50_manifestation") or {}).get("signed_at") or ""
    role = identity.get("claimed_role") or "an unknown role"

    # **A lookup rather than `capitalize() + "d"`, which produced `Request_changesd`.** The suffix trick
    # works for approve and deny and fails on the third decision, and it failed in the one string a client
    # reads. A mapping is the same length and cannot be wrong about a word.
    past = {"approve": "Approved", "deny": "Denied", "request_changes": "Changes requested"}
    said = past.get(decision, decision.replace("_", " ").capitalize())

    if resolve_assurance(identity.get("assurance")) == ASSURANCE_UNVERIFIED:
        # No name. Not even the claimed one, because a sentence a client reads should not present an
        # unverified claim in the position where a verified name would sit.
        return (f"{said} at {when} under the {role} role. The person who decided was not "
                f"identified: {UNVERIFIED_REASON}.")

    return f"{said} at {when} by {identity.get('claimed_subject')} ({role})."
