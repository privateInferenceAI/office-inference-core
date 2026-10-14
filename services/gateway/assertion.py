"""The assertion the API presents, and the hook the gateway verifies it with.

**One secret, and it is not a model credential.** The assistant API holds a signing key and nothing else: it
cannot call a model with it, and it cannot read the ones that can. Per request it signs a short-lived statement
of who is calling and in what role, and the gateway resolves that into the identity its budget checks use.

This is the shape a transaction token describes, and the reason to bother is the comparison. The alternative
was to mount the per-role virtual keys into the API, which would have put every role's long-lived bearer
token in one container that also renders client documents into prompts. Whoever reads that container reads the
whole deployment. Here they read a key that mints statements, and statements expire.

**Three constraints, each of which was a decision and not a default:**

* **Short life.** Five minutes. The API mints one per request, so the window is the request rather than the
  deployment, and a key that leaks is worth minutes of one role.
* **The role is inside the signed payload and nowhere else.** A header carrying the role would be a header a
  caller could edit, which is the same failure as a role in a request body.
* **A nonce, so a statement is used once.** Without it a captured assertion is replayable for its whole life,
  which turns a five-minute window into an hour of somebody else's clicking.

**The master-key exception is the part that is easy to miss and expensive to miss.** With `custom_auth`
enabled the gateway sends the master key through the hook like any other credential. A hook that only
understands role assertions therefore refuses it, and every management endpoint, `/team/new` included, becomes
unreachable. The hook receives `api_key`, so it recognises the master key and returns an administrator.
Without that, enabling this feature locks a deployment out of its own budget administration. Found by running
it, not by reading the documentation, which does not mention it.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import time
from pathlib import Path

# The signing key, one file, mode 600. The API reads it; the gateway reads the same file, which is what makes
# them agree without either holding the other's copy.
DEFAULT_KEY_PATH = os.environ.get("OI_ASSERTION_KEY", "/app/rendered/assertion.key")

# How long a statement is worth presenting. A request takes seconds; five minutes leaves room for a retry
# without leaving a window worth stealing.
MAX_AGE_SECONDS = int(os.environ.get("OI_ASSERTION_MAX_AGE", "300"))

# How far in the future a statement may be dated before it is refused. It exists because the expiry check is
# one-sided: a clock 10 minutes fast would otherwise mint statements that outlive their own lifetime.
CLOCK_SKEW_SECONDS = 60

# Where the nonces already spent are remembered. One file, because this deployment is one host, and the
# alternative is a Redis nobody wants to operate for one set membership.
DEFAULT_NONCE_PATH = os.environ.get("OI_ASSERTION_NONCES", "/tmp/oi-assertions-seen.json")

HEADER = "X-OI-Assertion"


def load_key(path: str | Path | None = None) -> bytes:
    """The signing key, or a refusal naming what to run.

    Absent is an error rather than a default. A default would be a shared secret in source, which is the one
    thing this file exists to avoid.
    """
    target = Path(path or DEFAULT_KEY_PATH)
    if not target.exists():
        raise AssertionError_(
            f"no assertion key at {target}. The renderer writes it: "
            f"python3 scripts/render_manifest.py --manifest <the one you deployed>"
        )
    key = target.read_text().strip()
    if len(key) < 32:
        raise AssertionError_(
            f"the assertion key at {target} is {len(key)} characters, which is too short to be the one the "
            f"renderer writes. A truncated key would be a weak secret nobody noticed."
        )
    return key.encode()


class AssertionError_(Exception):
    """A statement that cannot be minted or cannot be believed."""


def _b64(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def _unb64(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def mint(key: bytes, role: str, *, session_id: str = "", now: float | None = None) -> str:
    """One statement, signed. The payload is the authority; the headers beside it are not."""
    issued = int(now if now is not None else time.time())
    payload = {
        "role": role,
        "session_id": session_id,
        "iat": issued,
        "exp": issued + MAX_AGE_SECONDS,
        "nonce": os.urandom(12).hex(),
    }
    body = _b64(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode())
    signature = _b64(hmac.new(key, body.encode(), hashlib.sha256).digest())
    return f"{body}.{signature}"


def verify(key: bytes, token: str, *, now: float | None = None) -> dict:
    """The payload, or a refusal that says which check failed.

    Every check names itself in the message, because the person reading it is looking at a 401 in a log rather
    than at this file.
    """
    if not token or "." not in token:
        raise AssertionError_("no assertion was presented")
    body, _, signature = token.partition(".")
    expected = _b64(hmac.new(key, body.encode(), hashlib.sha256).digest())
    if not hmac.compare_digest(signature, expected):
        raise AssertionError_("the assertion signature does not match the key this gateway holds")
    try:
        payload = json.loads(_unb64(body))
    except (ValueError, json.JSONDecodeError) as exc:
        raise AssertionError_(f"the assertion payload is not readable: {exc}") from exc

    moment = now if now is not None else time.time()
    if payload.get("exp", 0) < moment:
        raise AssertionError_(
            f"the assertion expired at {payload.get('exp')}: the clocks differ, or a statement was kept "
            f"rather than minted per request")
    if payload.get("iat", 0) > moment + CLOCK_SKEW_SECONDS:
        raise AssertionError_(
            f"the assertion is dated {int(payload.get('iat', 0) - moment)}s in the future, which is a clock "
            f"disagreement rather than a forged statement")
    if not payload.get("role"):
        raise AssertionError_("the assertion names no role")
    return payload


def spend_nonce(payload: dict, path: str | Path | None = None) -> None:
    """Remember a statement as used, and refuse one that has been.

    The window kept is the assertion lifetime plus the skew, because a nonce older than that cannot be
    presented again anyway: its expiry check refuses it first. So the file stays small without a cleanup job.
    """
    target = Path(path or DEFAULT_NONCE_PATH)
    cutoff = time.time() - MAX_AGE_SECONDS - CLOCK_SKEW_SECONDS
    seen: dict = {}
    if target.exists():
        try:
            seen = json.loads(target.read_text())
        except (ValueError, OSError):
            # A corrupt file is a reason to start the window again rather than to refuse every request. The
            # replay window it protects is minutes wide and the file is written on every request.
            seen = {}
    seen = {nonce: stamp for nonce, stamp in seen.items() if stamp >= cutoff}
    nonce = payload.get("nonce", "")
    if nonce in seen:
        raise AssertionError_(f"assertion {nonce} was already presented: a statement is spent once")
    seen[nonce] = time.time()
    target.write_text(json.dumps(seen))
    target.chmod(0o600)
