"""The Graph calls: one token, one folder listing, one attachment fetch. And the error codes, in English.

Three things here are shaped by findings rather than by the API.

**The refresh token rotates.** Microsoft states that a refresh token "replaces itself with a fresh token upon
every use", so a connector that reads the credential once and keeps using the stored refresh token is a
connector that works until the stored one is retired and then stops with no visible cause. The new token is
written back to the client's own store immediately after a successful grant. `secretstore.write` sets the same
mode the reader enforces, so the write-back cannot loosen the file.

**The failure codes are classified here rather than printed at the caller.** A client never reads
`AADSTS65001`, and a support call that starts with a code wastes the first ten minutes. Every failure becomes
one of six kinds and one sentence that names the action.

**Nothing here decides whether a message is wanted.** The scope of this module is the mailbox: get a token,
list the folder, fetch the bytes. Deciding what a message means is `sender.py` and `content.py`, and keeping
those apart is what lets each be read on its own.
"""
from __future__ import annotations

import base64
import json
import re
import urllib.parse
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

import net

GRAPH = "https://graph.microsoft.com/v1.0"

# What the token endpoint needs in a client-credentials-with-refresh-token grant. `offline_access` is what
# makes a refresh token come back at all; without it the grant succeeds and the connector dies in an hour.
TOKEN_TEMPLATE = "https://login.microsoftonline.com/{tenant}/oauth2/v2.0/token"
GRAPH_SCOPE = "https://graph.microsoft.com/Mail.Read offline_access"

# The fields a message needs for provenance and for the sender decision.
#
# **`bodyPreview` is not enough, and that is the one selection here worth arguing about.** It is capped at
# 255 characters, so an instruction placed below the fold is invisible to a scan that reads only the preview.
# The full `body` is selected, scanned, and then **not stored**: the record keeps the signals that fired and
# a hash of what matched, never the text. Reading the whole body and keeping none of it is the difference
# between a scan that works and a scan that works on the messages the sender did not think about.
MESSAGE_SELECT = ",".join([
    "id", "internetMessageId", "subject", "from", "sender", "replyTo", "toRecipients",
    "receivedDateTime", "hasAttachments", "internetMessageHeaders", "bodyPreview", "body",
])

# How far back a first poll looks when the deployment has no watermark yet. Bounded, because a mailbox with
# five years of history in it would otherwise be ingested on the day the client turns the connector on.
FIRST_LOOKBACK_DAYS = 14


class TokenRefused(RuntimeError):
    """The token request failed. Carries the classification, not just the text."""

    def __init__(self, classification: dict, detail: str):
        super().__init__(classification["sentence"])
        self.classification = classification
        self.detail = detail


class MailboxUnreadable(RuntimeError):
    """The token is healthy and the mailbox is not readable. This is the licence case."""

    def __init__(self, classification: dict, detail: str):
        super().__init__(classification["sentence"])
        self.classification = classification
        self.detail = detail


@dataclass
class Token:
    access_token: str
    refresh_token: str | None
    scopes: list[str]
    expires_at: str
    tenant: str

    def record(self) -> dict:
        """What may be written down. The token itself never is."""
        return {"scopes": self.scopes, "expires_at": self.expires_at,
                "rotated_refresh_token": bool(self.refresh_token)}


# --------------------------------------------------------------------------- the codes, in English

AADSTS_ACTIONS = {
    "AADSTS700082": ("reapproval",
                     "The connection to {mailbox} has stopped because it was not used for long enough. "
                     "Sign in as {mailbox} once and approve the connection again. It takes about two minutes."),
    "AADSTS50173": ("reapproval",
                    "The connection to {mailbox} has been revoked, usually by a password change or by an "
                    "administrator signing out all sessions. It needs approving again."),
    "AADSTS65001": ("reapproval",
                    "The permission to read {mailbox} has been withdrawn. An administrator can grant it "
                    "again from the app registration's permissions page."),
    "AADSTS53003": ("conditional_access",
                    "A sign-in policy is blocking the connection to {mailbox}. Ask your administrator "
                    "whether a Conditional Access policy was changed or added recently, and whether {mailbox} "
                    "is excluded from it."),
    "AADSTS7000222": ("credential_expired",
                      "The application credential for {mailbox} has expired and needs to be replaced. This "
                      "is the one maintenance item a client secret has and a certificate does not."),
    "AADSTS700016": ("application_removed",
                     "The application that reads {mailbox} is no longer in your directory. It has to be "
                     "created again, because an app registration cannot be restored or moved between "
                     "tenants."),
    "AADSTS500011": ("application_removed",
                     "The application that reads {mailbox} is no longer in your directory. It has to be "
                     "created again."),
}

AADSTS_PATTERN = re.compile(r"(AADSTS\d{5})")


def classify_failure(status: int, body: str, mailbox: str) -> dict:
    """One of six kinds and one sentence, from whatever the endpoint said.

    The order matters. An `AADSTS` code is the most specific thing available and wins over the status. A 403
    with a healthy token is the licence case, which no code describes because nothing failed: the credential
    is fine and the mailbox is not readable, and only a volume check would otherwise notice.
    """
    found = AADSTS_PATTERN.search(body or "")
    code = found.group(1) if found else None
    if code and code in AADSTS_ACTIONS:
        kind, template = AADSTS_ACTIONS[code]
        return {"kind": kind, "code": code, "sentence": template.format(mailbox=mailbox)}
    if code:
        return {"kind": "unknown_code", "code": code,
                "sentence": f"Reading {mailbox} failed with {code}, which this build does not recognise. "
                            f"Send the code to your support provider."}
    if status == 403:
        return {"kind": "mailbox_not_readable", "code": None,
                "sentence": f"Authentication to {mailbox} is working but the mailbox cannot be read. Check "
                            f"that its licence is still assigned, because a mailbox without one returns "
                            f"exactly this."}
    if status == 401:
        return {"kind": "reapproval", "code": None,
                "sentence": f"The connection to {mailbox} was refused. It usually needs approving again."}
    return {"kind": "error", "code": None,
            "sentence": f"Reading {mailbox} failed with HTTP {status}. This is not one of the known "
                        f"conditions, and the detail is in the record."}


# --------------------------------------------------------------------------- the token

def acquire_token(config, credential, *, now: datetime | None = None) -> Token:
    """A refresh-token grant against the client's own tenant."""
    now = now or datetime.now(timezone.utc)
    tenant = config.tenant_id or credential.tenant_id
    url = TOKEN_TEMPLATE.format(tenant=tenant)

    form = {
        "client_id": credential.client_id,
        "grant_type": "refresh_token",
        "refresh_token": credential.refresh_token,
        "scope": GRAPH_SCOPE,
    }
    if credential.client_secret:
        form["client_secret"] = credential.client_secret
    elif credential.certificate_path:
        form["client_assertion_type"] = "urn:ietf:params:oauth:client-assertion-type:jwt-bearer"
        form["client_assertion"] = _certificate_assertion(credential)

    body = urllib.parse.urlencode(form).encode()
    try:
        response = net.request(url, method="POST", data=body, timeout=30,
                               headers={"Content-Type": "application/x-www-form-urlencoded"})
    except net.HttpError as exc:
        raise TokenRefused(classify_failure(exc.status, exc.body, config.mailbox.get("address", "the mailbox")),
                           exc.body) from None

    payload = response.json() or {}
    if not payload.get("access_token"):
        raise TokenRefused(
            classify_failure(response.status, json.dumps(payload), config.mailbox.get("address", "the mailbox")),
            json.dumps(payload))

    scopes = (payload.get("scope") or "").split()
    return Token(
        access_token=payload["access_token"],
        refresh_token=payload.get("refresh_token"),
        scopes=scopes,
        expires_at=(now + timedelta(seconds=int(payload.get("expires_in", 3600)))).isoformat(),
        tenant=tenant,
    )


def _certificate_assertion(credential) -> str:
    """A signed client assertion from the client's certificate.

    Not implemented, and refused loudly rather than faked. A client assertion is a JWT signed with the
    certificate's private key, and a placeholder that produced a well-formed but unsigned token would fail at
    Microsoft with an error that reads like a configuration mistake. The research prefers certificates over
    client secrets; this build ships the secret path and says so instead of shipping a stub that appears to
    support the better one.
    """
    raise TokenRefused(
        {"kind": "unsupported_credential", "code": None,
         "sentence": "This build reads a client secret. The certificate path is refused rather than faked: a "
                     "client assertion has to be signed with the certificate's key and a stub would fail at "
                     "Microsoft with an error that reads like a misconfiguration."},
        f"certificate_path={credential.certificate_path}")


# --------------------------------------------------------------------------- the mailbox

def _headers(token: Token) -> dict:
    return {"Authorization": f"Bearer {token.access_token}"}


def list_messages(config, token: Token, *, since: datetime | None = None, top: int = 50) -> list[dict]:
    """One folder's messages, oldest first, from a watermark.

    The folder comes from the configuration rather than from code, because the folder is where the client's
    supplier invoices already arrive and no software knows that better than they do.
    """
    folder = config.mailbox.get("folder") or "Inbox"
    if since is None:
        since = datetime.now(timezone.utc) - timedelta(days=FIRST_LOOKBACK_DAYS)
    params = {
        "$select": MESSAGE_SELECT,
        "$top": str(top),
        # The watermark is `receivedDateTime` and the filter is inclusive-by-one-second on purpose: a message
        # that arrived in the same second the last poll ended would otherwise be skipped, and the dedupe by
        # content hash makes re-reading a message harmless while skipping one is not.
        "$filter": f"receivedDateTime ge {since.astimezone(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')}",
        "$orderby": "receivedDateTime asc",
    }
    url = f"{GRAPH}/me/mailFolders/{urllib.parse.quote(folder)}/messages?{urllib.parse.urlencode(params)}"
    try:
        response = net.request(url, headers=_headers(token), timeout=60)
    except net.HttpError as exc:
        raise MailboxUnreadable(
            classify_failure(exc.status, exc.body, config.mailbox.get("address", "the mailbox")),
            exc.body) from None
    return (response.json() or {}).get("value") or []


def attachments(config, token: Token, message_id: str) -> list[dict]:
    """Every attachment on a message, with its bytes decoded.

    An attachment whose `@odata.type` is not `fileAttachment` is skipped rather than decoded: an item
    attachment is a nested message or a calendar event, it has no `contentBytes`, and treating one as a file
    would deposit a zero-byte document.
    """
    url = f"{GRAPH}/me/messages/{urllib.parse.quote(message_id)}/attachments"
    try:
        response = net.request(url, headers=_headers(token), timeout=60)
    except net.HttpError as exc:
        raise MailboxUnreadable(
            classify_failure(exc.status, exc.body, config.mailbox.get("address", "the mailbox")),
            exc.body) from None

    out = []
    for item in (response.json() or {}).get("value") or []:
        if item.get("@odata.type") != "#microsoft.graph.fileAttachment":
            continue
        if not item.get("contentBytes"):
            continue
        out.append({
            "name": item.get("name") or "attachment",
            "content_type": item.get("contentType") or "application/octet-stream",
            "content": base64.b64decode(item["contentBytes"]),
            "declared_size": item.get("size"),
        })
    return out


def header_map(message: dict) -> dict[str, list[str]]:
    """`internetMessageHeaders` as a case-insensitive name to values map.

    A list of values per name rather than one, because `Received` and `Authentication-Results` legitimately
    repeat, and the first `Authentication-Results` is the one written by the receiving system this deployment
    trusts. Collapsing to one value would silently keep the last, which is the one nearest the sender.
    """
    out: dict[str, list[str]] = {}
    for header in message.get("internetMessageHeaders") or []:
        name = (header.get("name") or "").lower()
        if name:
            out.setdefault(name, []).append(header.get("value") or "")
    return out


def received_at(message: dict) -> str:
    return message.get("receivedDateTime") or ""
