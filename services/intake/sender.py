"""Whether the sender can be verified, and what is recorded when it cannot.

`CON-SENDER-001` says an invoice whose sender cannot be verified is marked rather than trusted. The word that
carries the requirement is *marked*. A connector that refuses unknown senders drops real invoices, because
suppliers change billing addresses, use a payment portal's domain, or send through a mailing house. A
connector that trusts them is a queue an attacker can post into. So nothing is refused here and nothing is
approved here: a verdict is recorded, and a human reads it at the approval gate.

**Three facts are gathered per message, and the third is the one that matters most.**

1. `Authentication-Results`, written by the receiving mail system, carrying SPF, DKIM and DMARC.
2. The header `From`, which is what a person reading the message sees.
3. The **envelope sender**, from `Return-Path`, which is the address the delivering server actually used and
   is the one SPF authenticated.

The two senders can differ, and when they do the difference is either benign forwarding or the oldest trick
in invoice fraud. `Return-Path` is a fact about the delivery rather than a claim in the message, which is why
it is read here rather than the `From` alone.

**A missing fact is not a pass.** No `Return-Path` means the alignment check could not run, so the message is
marked. The failure this avoids is the common one: a verifier that treats an absent value as a match, so a
message that carefully omits its envelope sender is the one that gets trusted.
"""
from __future__ import annotations

import re

# `Authentication-Results: mx.example.com; spf=pass smtp.mailfrom=...; dkim=pass header.d=...; dmarc=pass ...`
# The property names are lower case in practice and the values are not, so both are normalised.
AUTH_RESULT = re.compile(r"\b(spf|dkim|dmarc)\s*=\s*([A-Za-z]+)")

ENVELOPE_HEADERS = ("return-path", "x-envelope-from", "x-sender", "envelope-from", "x-original-from")

ADDRESS = re.compile(r"[<\s]*([\w.+-]+@[\w.-]+\.[A-Za-z]{2,})[>\s]*")


def parse_auth_results(values: list[str] | None) -> dict:
    """SPF, DKIM and DMARC from the receiving system's own header.

    The first `Authentication-Results` header is the one used, and that ordering is deliberate. Headers are
    prepended as a message travels, so the first is the one written by the system that accepted the message
    for this mailbox. A later one was written further from the mailbox, and on a forged message that is
    exactly where the forgery would be.
    """
    results = {"spf": "none", "dkim": "none", "dmarc": "none"}
    if not values:
        return results
    for name, value in AUTH_RESULT.findall(values[0]):
        results[name.lower()] = value.lower()
    return results


def envelope_sender(headers: dict[str, list[str]]) -> str | None:
    """The address the delivering server used, from whichever header carries it."""
    for name in ENVELOPE_HEADERS:
        for value in headers.get(name) or []:
            found = ADDRESS.search(value or "")
            if found:
                # A null reverse path (`Return-Path: <>`) is a bounce, not a sender. It parses to nothing and
                # is reported as absent, which marks the message rather than trusting it.
                return found.group(1).lower()
    return None


def address_of(value) -> str:
    """The address out of a Graph recipient object, a header value, or a bare string."""
    if isinstance(value, dict):
        email = value.get("emailAddress") or {}
        address = email.get("address") or ""
    else:
        address = str(value or "")
    found = ADDRESS.search(address)
    return (found.group(1) if found else address).lower()


def domain_of(address: str) -> str:
    return (address or "").rsplit("@", 1)[-1].lower()


def verify(*, headers: dict[str, list[str]], header_from: str, reply_to: str | None,
           allowlist: list[str]) -> dict:
    """The verdict, with every reason it is not `trusted` named.

    Four conditions have to hold together for `trusted`, and each is a separate way to be wrong:

    * **DMARC passes.** SPF and DKIM alone are each defeatable; DMARC is the one that ties the domain in the
      visible `From` to a result the receiver produced.
    * **The envelope sender matches the header sender.** This is the forged-forward case.
    * **The sender's domain is on the client's allowlist.** Verification proves a message really came from
      `supplier.example`; it does not prove `supplier.example` is a supplier.
    * **Neither fact is missing.** Absent is not a pass.
    """
    auth = parse_auth_results(headers.get("authentication-results"))
    envelope = envelope_sender(headers)
    from_domain = domain_of(header_from)
    envelope_domain = domain_of(envelope) if envelope else None

    if envelope is None:
        aligned = None
    else:
        aligned = envelope_domain == from_domain

    allowlisted = bool(from_domain) and from_domain in allowlist

    reasons: list[str] = []
    if auth["dmarc"] != "pass":
        reasons.append(f"dmarc_{auth['dmarc']}")
    if aligned is None:
        reasons.append("envelope_sender_missing")
    elif not aligned:
        reasons.append("envelope_differs_from_header")
    if not allowlisted:
        reasons.append("sender_not_on_allowlist")

    return {
        "header_from": header_from,
        "envelope_from": envelope,
        "reply_to": reply_to,
        "from_domain": from_domain,
        "auth": auth,
        "allowlisted": allowlisted,
        "aligned": aligned,
        "verdict": "trusted" if not reasons else "marked",
        "reasons": reasons,
        # Intake has no approval capability, so this is a constant rather than a decision. A component that
        # could approve what it just read is a component the message can talk into approving.
        "requires_review": True,
    }
