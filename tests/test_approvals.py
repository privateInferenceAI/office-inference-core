"""The approval record: what it says, and the four things it must refuse to say.

**Why this file is mostly about what the record does not claim.** An approval is the one artifact in the
product that a client reads and believes without checking, because checking it means re-doing the work it
authorises. Everything here exists so the record is exactly as strong as the evidence behind it and no
stronger, and the failures it guards against are all plausible lines of code rather than obvious bugs.

**The four refusals, each with a test that fails if it stops happening.**

* An unknown assurance value must resolve **downward**. Resolving upward would let a typo or an old schema
  claim a verification that never happened.
* A denial must carry a reason. A blank one cannot be told apart from a caller who declined to give one.
* A decision must quote the content it is about. Otherwise a draft that changed while somebody was reading it
  gets approved silently, and the record says they approved something they never saw.
* No sentence may name a person while the basis is a session token. The route used to be able to, and
  `audit_feed` still does.

**What is not tested here.** The HTTP route and the durable transaction. They are in
`tests/test_approval_http.py` and `tests/test_approval_durable.py`; the route is also exercised by
`tests/fixtures/approval_probe.py` against a live deployment.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "services" / "api"))

import approvals  # noqa: E402
import jobs  # noqa: E402


# --------------------------------------------------------------------------- helpers


class FakeSession:
    """A session with the two fields the record reads, and no more.

    Deliberately not the real `Session`. The record must be buildable from anything carrying a subject and a
    role, because C7 replaces the session and the record must not need rewriting when it does.
    """

    def __init__(self, subject="dana", role="staff", assurance=None):
        self.subject, self.role = subject, role
        if assurance is not None:
            self.assurance = assurance


def make_job(**kw) -> jobs.Job:
    base = dict(deployment_id="oi-test", subject="dana", actor_role="staff",
                roots={"company"}, credential_sha256="a" * 64, task="invoice_intake",
                input="invoice 2026-0417")
    base.update(kw)
    return jobs.new_job(**base)


def at_the_gate(action="send the email to ferguson@example.com"):
    """A job waiting for a decision, which is the only state a decision may be recorded in.

    **It goes through `running` on the way, and the first version of this helper did not.** It called
    `request_approval` on a freshly created job and eleven tests failed at once, because the machine refuses
    `queued -> awaiting_approval`. The machine is right and the helper was wrong: a job that has never run
    cannot be waiting to approve something it has not produced. The refusal is worth keeping in the machine
    even though it caught only a test, because the same call in a handler would put a job at a gate with
    nothing behind it.
    """
    job, store = make_job(), jobs.MemoryStore()
    jobs.transition(job, jobs.RUNNING)
    jobs.request_approval(job, store, action)
    return job, store


# --------------------------------------------------------------------------- the assurance field


def test_an_unknown_assurance_resolves_downward():
    """**The test the whole honesty design leans on.** A value nobody recognises must never become a claim.

    The failure this prevents is quiet: a new value written by a newer deployment, read by an older one, and
    rendered as whatever the reader happened to default to. Defaulting upward would report a verification
    that never happened, which is worse than reporting nothing, because nobody goes looking for a missing
    verification and everybody trusts a present one.
    """
    for unknown in ("PROOFED", "Proofed", "verified", "", None, "provider-verified", "re_attested "):
        assert approvals.resolve_assurance(unknown) == approvals.ASSURANCE_UNVERIFIED, (
            f"{unknown!r} was resolved to something other than unverified. An unrecognised value must resolve "
            f"downward, never upward.")


def test_a_known_assurance_survives_the_round_trip():
    """The downward rule must not be so eager that it discards a value this build does understand."""
    assert approvals.resolve_assurance("proofed") == "proofed"
    assert approvals.resolve_assurance("provider_verified") == "provider_verified"


def test_only_one_assurance_value_is_reachable_in_this_build():
    """**The shortness of this set is the point.** C7 raises it, and that should be a code change somebody
    reviews rather than a string somebody writes into a row."""
    assert approvals.REACHABLE_ASSURANCE == frozenset({approvals.ASSURANCE_UNVERIFIED,
                                                        approvals.ASSURANCE_PROVIDER_VERIFIED})


def test_the_record_marks_the_identity_unverified_and_says_why():
    job, _ = at_the_gate()
    payload = approvals.build_payload(FakeSession(), job, "approve", "", "", job.pending_action_digest,
                                      signed_at="2026-10-16T16:20:00+00:00")
    identity = payload["approval"]["identity"]
    assert identity["assurance"] == "unverified"
    assert identity["basis"] == approvals.BASIS_SESSION_TOKEN
    # The prefix is load-bearing: it is what survives a screen that chooses not to render a paragraph.
    assert "claimed_subject" in identity and "claimed_role" in identity
    assert "subject" not in identity and "role" not in identity


def test_the_part11_manifestation_states_the_name_is_absent_and_why():
    """Section 11.50 wants a printed name, and the honest answer is that there isn't one.

    A null with a reason tells the reader the record looked and is saying so. An absent field invites the
    reader to assume the record simply does not carry names, which is a different and much weaker statement.
    """
    job, _ = at_the_gate()
    payload = approvals.build_payload(FakeSession(), job, "approve", "", "", job.pending_action_digest,
                                      signed_at="2026-10-16T16:20:00+00:00")
    manifestation = payload["approval"]["part11_11_50_manifestation"]
    assert manifestation["printed_name"] is None
    assert manifestation["name_absent_reason"], "the reason must be stated, not left to the reader"
    assert manifestation["signed_at"] and manifestation["meaning"]


# --------------------------------------------------------------------------- the decisions


def test_a_denial_without_a_reason_is_refused():
    """**A blank reason is indistinguishable from a caller who declined to give one.** The next run learns
    from the reason, so a blank one teaches it nothing while looking like it tried."""
    problem = approvals.validate("deny", "")
    assert problem and "reason" in problem
    assert approvals.validate("deny", "   ") is not None, "whitespace is not a reason"


def test_a_change_request_without_a_reason_is_refused():
    assert approvals.validate("request_changes", "") is not None


def test_an_approval_needs_no_reason():
    """The thing being approved is in the record, so an approval does not have to justify itself."""
    assert approvals.validate("approve", "") is None


def test_an_unknown_decision_is_refused_and_the_three_are_named():
    problem = approvals.validate("maybe", "")
    assert problem
    for word in approvals.DECISIONS:
        assert word in problem, "the refusal must name the decisions that would have worked"


# --------------------------------------------------------------------------- the gate


def test_the_digest_is_written_before_the_state_moves():
    """**No window where the state says waiting and the digest is empty.**

    A job observable in `awaiting_approval` with nothing held is a job an approval could be recorded against
    with no content behind it, which is the record claiming more than happened.
    """
    job = make_job()
    assert job.state == jobs.QUEUED
    assert job.pending_action_digest == ""
    jobs.transition(job, jobs.RUNNING)
    jobs.request_approval(job, jobs.MemoryStore(), "send the email")
    assert job.state == jobs.AWAITING_APPROVAL
    assert job.pending_action_digest.startswith("sha256:")


def test_the_digest_is_over_the_content_not_an_identifier():
    """A digest of a row id would survive the row being edited, so an approval would verify against a draft
    that had since changed."""
    first = jobs.action_digest("send this")
    second = jobs.action_digest("send that")
    assert first != second
    assert jobs.action_digest("send this") == first, "the same content must give the same digest"


def test_a_decision_on_a_job_that_is_not_waiting_is_refused():
    job = make_job()
    with pytest.raises(jobs.TransitionRefused):
        jobs.decide(job, jobs.MemoryStore(), "approve", "sha256:whatever")


def test_a_decision_that_has_drifted_from_the_content_is_refused():
    """**The case this whole mechanism exists for.** A retry can rewrite a draft between the screen rendering
    and the button being pressed, and without this the approval would record a decision about content nobody
    saw."""
    job, store = at_the_gate("send the draft as it was")
    job.pending_action_digest = jobs.action_digest("send the draft as it is now")
    with pytest.raises(jobs.ApprovalDrift) as raised:
        jobs.decide(job, store, "approve", jobs.action_digest("send the draft as it was"))
    assert raised.value.waiting and raised.value.quoted, "both digests are reported so a screen can re-ask"
    assert job.state == jobs.AWAITING_APPROVAL, "a refused decision must not move the job"


def test_approving_resumes_the_job_and_clears_what_it_was_waiting_on():
    job, store = at_the_gate()
    digest = job.pending_action_digest
    jobs.decide(job, store, "approve", digest)
    assert job.state == jobs.RUNNING
    assert job.pending_action_digest == "", (
        "the digest belongs to the act that was approved; leaving it set would let the same approval be "
        "replayed against a later gate")


def test_denying_fails_the_job_with_a_reason_and_keeps_its_rows():
    job, store = at_the_gate()
    before = len(job.steps)
    jobs.decide(job, store, "deny", job.pending_action_digest)
    assert job.state == jobs.FAILED
    assert job.outcome_reason == "denied_at_the_gate"
    assert len(job.steps) >= before, "a decision is a fact about the work, not a reason to delete it"


def test_requesting_changes_is_distinguishable_from_a_denial():
    """Both stop the job and they mean different things to the next run."""
    denied, _ = at_the_gate()
    changed, _ = at_the_gate()
    jobs.decide(denied, jobs.MemoryStore(), "deny", denied.pending_action_digest)
    jobs.decide(changed, jobs.MemoryStore(), "request_changes", changed.pending_action_digest)
    assert denied.outcome_reason != changed.outcome_reason


def test_the_digest_is_published_only_while_the_job_is_waiting():
    """A client deciding needs to know what it is deciding about. Afterwards the digest is history and lives
    in the audit row instead."""
    job, store = at_the_gate()
    assert "pending_action_digest" in job.to_dict()
    jobs.decide(job, store, "approve", job.pending_action_digest)
    assert "pending_action_digest" not in job.to_dict()


# --------------------------------------------------------------------------- the sentence


def test_the_sentence_names_no_person_while_the_basis_is_a_session():
    """**UI-FEED-001, and the existing feed line fails it.** Migration 002 renders an approval as
    `'a person ' || outcome || ' an action'`, which asserts something the system has not established."""
    job, _ = at_the_gate()
    payload = approvals.build_payload(FakeSession(subject="dana"), job, "approve", "", "",
                                      job.pending_action_digest, signed_at="2026-10-16T16:20:00+00:00")
    sentence = approvals.describe(payload)

    assert "dana" not in sentence.lower(), (
        "an unverified claim must not sit in the position where a verified name would sit")
    assert "not identified" in sentence, "the sentence must say what is missing, not only omit it"
    assert "staff" in sentence, "the role is what the system does know and it is the useful half"


def test_the_sentence_does_name_a_person_once_identity_is_verified():
    """The other direction, so the rule above is not simply 'never name anybody'."""
    job, _ = at_the_gate()
    payload = approvals.build_payload(FakeSession(subject="dana", assurance="proofed"), job, "approve", "",
                                      "", job.pending_action_digest, signed_at="2026-10-16T16:20:00+00:00")
    payload["approval"]["identity"]["assurance"] = "proofed"
    assert "dana" in approvals.describe(payload).lower()


def test_the_record_lands_in_payload_which_the_chain_covers():
    """**AUD-APPROVAL-001.** The record must go where `payload_sha256` covers it, not into the
    `audit_events.approval` column, which is outside the canonical string and can be edited by hand while
    every chain check passes."""
    job, _ = at_the_gate()
    payload = approvals.build_payload(FakeSession(), job, "approve", "", "", job.pending_action_digest,
                                      signed_at="2026-10-16T16:20:00+00:00")
    assert "approval" in payload, "the record is part of the payload the hash covers"
    assert payload["stage"] == "approval"
    assert payload["job_id"] == job.job_id
