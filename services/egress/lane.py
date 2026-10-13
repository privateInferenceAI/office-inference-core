"""Whether a request may leave, decided before anything is redacted or sent.

The gate and the redaction answer different questions and the order between them is the design. The gate asks
whether this request may go to a frontier model at all; the redaction asks what must not go inside a request
that may. Redacting first and then deciding would mean a regulated document is partly sent because a pattern
happened to catch the parts somebody thought of, and the client's decision was about the document.

**Fail closed, and the direction is the whole point.** An unset permission, an unknown class, a lane nobody
configured: every one of them refuses. The alternative fails open, which is a system that sends a client's
confidential document to a provider because a variable was misspelled, and it does so silently because the call
succeeds.

**What this is not.** It is not a second opinion on the client's decision. If a client permits `confidential` on
a frontier lane, this sends it, redacted. The system's job is to make the decision explicit, enforce it, and
record it, not to override it with a view about what the client should have chosen.
"""
from __future__ import annotations

import os
from dataclasses import dataclass

from redaction import DATA_CLASSES


@dataclass
class Decision:
    """Whether the request may leave, and why not if it may not."""

    allowed: bool
    data_class: str
    lane: str
    reason: str | None = None

    def as_record(self) -> dict:
        record = {"lane": self.lane, "data_class": self.data_class, "egress_allowed": self.allowed}
        if self.reason:
            record["egress_reason"] = self.reason
        return record


def permitted_classes(lane: str = "frontier") -> set[str]:
    """The classes a client has permitted on a lane, from the rendered environment.

    Read from the environment rather than written here, so it is a manifest decision with a schema, a diff, and
    a review, which is the same arrangement the content modes and the access labels already use. The variable
    is `FRONTIER_CLASSES` and its value is a comma-separated list.
    """
    raw = os.environ.get(f"{lane.upper()}_CLASSES")
    if raw is None:
        # An unset variable is an empty permission set, never a default. A default here would be a system that
        # sends regulated documents on a deployment that never configured anything.
        return set()
    return {item.strip().lower() for item in raw.split(",") if item.strip()}


def check(data_class: str, lane: str = "frontier",
          permitted: set[str] | None = None) -> Decision:
    """Whether a request of this class may leave on this lane.

    Four refusals and one permission, and each refusal names what is wrong rather than saying "denied", because
    a refusal a person cannot act on is one they route around.
    """
    data_class = (data_class or "").strip().lower()
    if not data_class:
        return Decision(False, data_class, lane,
                        "the request carries no data class, and an unclassified request cannot be shown to be "
                        "permitted")
    if data_class not in DATA_CLASSES:
        return Decision(False, data_class, lane,
                        f"{data_class!r} is not one of the four data classes ({', '.join(DATA_CLASSES)}), so "
                        f"nothing is known about whether it may leave")
    allowed = permitted_classes(lane) if permitted is None else permitted
    if not allowed:
        return Decision(False, data_class, lane,
                        f"no data classes are permitted on the {lane} lane. This is what an unconfigured "
                        f"deployment looks like, and it refuses rather than guessing a default.")
    if data_class not in allowed:
        return Decision(False, data_class, lane,
                        f"{data_class} is not permitted on the {lane} lane, which carries {', '.join(sorted(allowed))}")
    return Decision(True, data_class, lane)
