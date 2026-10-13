"""The one entry point the assistant API calls before a frontier request goes anywhere.

The path in the design is `assistant API -> data-class check -> redaction -> gateway -> frontier model`, and
this is the middle two steps as a single call, because splitting them across two modules is how one of them ends
up not being called. A caller that has to remember to redact after checking will eventually check, log, and
send.

**The order is not interchangeable.** The gate first: a class the client has not permitted is refused before
redaction is attempted, because redacting a regulated document and sending the remainder would answer a
different question than the one the client decided. Then redaction. Then the hash, of the redacted text.

**What it returns is what gets sent.** `Prepared.payload` is the request to hand to the gateway, and there is no
path through this function that returns the original alongside it. That is deliberate: a function that returns
both invites a caller to send the wrong one, and the mistake would be invisible because both are valid requests.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field

import lane as lane_module
import redaction as redaction_module


@dataclass
class Prepared:
    """What may leave, what to send, and what the record will say."""

    allowed: bool
    decision: lane_module.Decision
    payload: dict | None = None
    redaction: redaction_module.Redaction = field(default_factory=redaction_module.Redaction)
    prompt_sha256: str | None = None
    # The exact string whose hash is recorded, so a caller passing it to the audit does not have to serialise
    # the payload a second time and get the same hash by luck. The first probe did serialise it again, with
    # different separators, and recorded a hash that did not match the one this module computed: the record
    # looked fine and proved nothing about what was sent.
    sent_text: str | None = None
    refusal: str | None = None

    def as_record(self) -> dict:
        """The audit fields. The data class and what was removed, and never the values."""
        record = dict(self.decision.as_record())
        record.update(self.redaction.as_record())
        if self.prompt_sha256:
            record["prompt_sha256"] = self.prompt_sha256
        if self.refusal:
            record["outcome"] = "denied"
        return record


def redaction_enabled() -> bool:
    """Whether the client left redaction on.

    Read from the environment the manifest renders, defaulting to on. The default matters more than the setting:
    a deployment that has never heard of this variable still redacts, so forgetting to configure it cannot
    become forgetting to redact.
    """
    return (os.environ.get("EGRESS_REDACTION") or "on").strip().lower() != "off"


def prepare(payload: dict, data_class: str, lane: str = "frontier",
            permitted: set[str] | None = None,
            redact_enabled: bool | None = None) -> Prepared:
    """Decide, redact, and return what to send.

    `payload` is the assembled request: the user's question and, importantly, the retrieved passages. Redacting
    only the question is the mistake this signature is shaped to prevent, because the passages are the part most
    likely to hold a client's identifiers.
    """
    decision = lane_module.check(data_class, lane, permitted)
    if not decision.allowed:
        # Nothing is redacted and nothing is hashed, because nothing is sent. A hash here would be a record of
        # content that never left, which is a second copy of it made for no reason.
        return Prepared(allowed=False, decision=decision, refusal=decision.reason)

    enabled = redaction_enabled() if redact_enabled is None else redact_enabled
    if enabled:
        cleaned, found = redaction_module.redact_request(payload)
    else:
        cleaned, found = payload, redaction_module.Redaction()

    # Serialised once, hashed, and handed out, so there is one string and one hash rather than two of each.
    sent = _stable(cleaned)
    return Prepared(
        allowed=True,
        decision=decision,
        payload=cleaned,
        redaction=found,
        # Of the redacted request, so the record proves what was sent rather than becoming a copy of what was
        # removed. The audit column already says it holds the hash of the prompt as sent.
        prompt_sha256=redaction_module.sha256_of(sent),
        sent_text=sent,
    )


def _stable(payload: dict) -> str:
    """The payload as a deterministic string, so the same request hashes the same way twice."""
    import json
    return json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
