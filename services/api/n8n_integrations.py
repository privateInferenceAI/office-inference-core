"""Signed per-workflow integrations for the client-owned n8n instance.

**What this module is.** The API half of decision 0011: n8n starts governed work by calling a signed
endpoint, and each workflow carries its own integration identity, task, role, document roots and named
owner. The registry is rendered configuration, not a hidden default: no entry means no integration, and an
invalid file refuses to load rather than falling back to something permissive.

**Why the owner is in the registry.** The workflow runs when nobody is logged in, so there is no live
session to consult at 3am. Provisioning validates the declared roots against the owner's roots before the
entry is written; this module keeps the validation function so the deployment script and the tests use the
same comparison rather than reimplementing it.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping

MAX_SKEW_SECONDS = 300
MAX_BODY_BYTES = 64 * 1024
ID_PATTERN = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")
SIGNATURE_PREFIX = "sha256="


class RegistryError(Exception):
    """The rendered integration registry is invalid. Startup refuses rather than guessing."""


class IntegrationRefused(Exception):
    """The request is not from the integration it claims to be, or it is stale."""


@dataclass(frozen=True)
class Integration:
    id: str
    secret: str
    task: str
    role: str
    roots: frozenset[str]
    owner_subject: str
    enabled: bool = True

    def automation_subject(self, deployment_id: str) -> str:
        return f"automation+{self.id}@{deployment_id}"


def load(path: str | Path) -> dict[str, Integration]:
    """Load the rendered registry. A missing file means no integrations, an invalid file is a refusal."""
    p = Path(path)
    if not p.exists():
        return {}
    try:
        raw = json.loads(p.read_text())
    except Exception as exc:  # noqa: BLE001 - the message is the response
        raise RegistryError(f"the n8n integration registry is not valid JSON: {exc}") from exc
    entries = raw.get("integrations") if isinstance(raw, dict) else None
    if not isinstance(entries, list):
        raise RegistryError("the n8n integration registry must be an object with an 'integrations' list")
    out: dict[str, Integration] = {}
    for index, entry in enumerate(entries):
        where = f"integration {index}"
        if not isinstance(entry, dict):
            raise RegistryError(f"{where} must be an object")
        integration_id = str(entry.get("id") or "").strip()
        if not ID_PATTERN.match(integration_id):
            raise RegistryError(
                f"{where} has an invalid id {integration_id!r}; use lowercase letters, digits, '-' or '_'")
        if integration_id in out:
            raise RegistryError(f"integration id {integration_id!r} appears twice")
        where = f"integration {integration_id!r}"
        secret = str(entry.get("secret") or "")
        if len(secret) < 32:
            raise RegistryError(f"{where} needs a secret of at least 32 characters")
        task = str(entry.get("task") or "").strip()
        if not task:
            raise RegistryError(f"{where} must declare the task it starts")
        role = str(entry.get("role") or "").strip()
        if not role:
            raise RegistryError(f"{where} must declare the role the job runs under")
        roots_raw = entry.get("roots")
        if not isinstance(roots_raw, list) or not roots_raw:
            raise RegistryError(f"{where} must declare at least one document root")
        roots = []
        for root in roots_raw:
            value = str(root or "").strip()
            if not value:
                raise RegistryError(f"{where} has an empty document root")
            roots.append(value)
        owner = str(entry.get("owner_subject") or "").strip()
        if not owner:
            raise RegistryError(f"{where} must name the owner who requested the automation")
        out[integration_id] = Integration(
            id=integration_id, secret=secret, task=task, role=role, roots=frozenset(roots),
            owner_subject=owner, enabled=bool(entry.get("enabled", True)))
    return out


def recompute_signature(secret: str, timestamp: str, body: bytes) -> str:
    """The one definition of the signature both sides use."""
    return hmac.new(secret.encode(), str(timestamp).encode() + b"." + body, hashlib.sha256).hexdigest()


def verify_bearer(integration: Integration, authorization: str) -> None:
    """Verify the shared-secret bearer alternative, for callers that cannot compute an HMAC.

    **Why this exists.** n8n's task runner disallows the Node crypto module, so a workflow cannot build the
    signed payload without extra credentials plumbing. The integration endpoint is on the internal network and
    the secret never leaves the deployment; the bearer is the same secret as the HMAC key. The HMAC path stays
    for callers that can sign, and the request path prefers it when the signature headers are present.
    """
    value = str(authorization or "").strip()
    if not value.startswith("Bearer "):
        raise IntegrationRefused("missing bearer credential")
    supplied = value[len("Bearer "):].strip()
    if not hmac.compare_digest(supplied, integration.secret):
        raise IntegrationRefused("the bearer credential does not match")


def verify_request(integration: Integration, *, timestamp: str, signature: str, body: bytes,
                   now: float | None = None, max_skew: int = MAX_SKEW_SECONDS) -> None:
    """Verify a signed request, or raise IntegrationRefused with the reason.

    The timestamp is inside the signed material, so replaying a captured request later fails even when the
    signature is valid. The window is five minutes; the clock is the server's.
    """
    if not timestamp or not signature:
        raise IntegrationRefused("missing signature headers")
    try:
        stamped = int(str(timestamp).strip())
    except ValueError as exc:
        raise IntegrationRefused("the timestamp is not an integer") from exc
    current = time.time() if now is None else now
    if abs(current - stamped) > max_skew:
        raise IntegrationRefused("the timestamp is outside the allowed window")
    supplied = str(signature).strip()
    if supplied.startswith(SIGNATURE_PREFIX):
        supplied = supplied[len(SIGNATURE_PREFIX):]
    expected = recompute_signature(integration.secret, str(timestamp).strip(), body)
    if not hmac.compare_digest(expected, supplied):
        raise IntegrationRefused("the signature does not match")


# --------------------------------------------------------------------------- owner validation


def _normalise_users(registry: Mapping) -> dict[str, list[str]]:
    """The subject-to-groups map, accepting the rendered registry's shape without guessing wildly."""
    users = registry.get("users") if isinstance(registry, Mapping) else None
    out: dict[str, list[str]] = {}
    if isinstance(users, dict):
        for subject, record in users.items():
            groups = record.get("groups") if isinstance(record, dict) else None
            out[str(subject)] = [str(g) for g in (groups or [])]
    elif isinstance(users, list):
        for record in users:
            if not isinstance(record, dict):
                continue
            subject = str(record.get("subject") or "").strip()
            if not subject:
                continue
            out[subject] = [str(g) for g in (record.get("groups") or [])]
    return out


def owner_roots(registry: Mapping, subject: str) -> frozenset[str] | None:
    """The roots a subject holds in the rendered access registry, or None when the subject is unknown."""
    users = _normalise_users(registry)
    groups = _normalise_groups(registry)
    if subject not in users:
        return None
    roots: set[str] = set()
    for group in users[subject]:
        roots.update(groups.get(group, set()))
    return frozenset(roots)


def _normalise_groups(registry: Mapping) -> dict[str, set[str]]:
    groups = registry.get("groups") if isinstance(registry, Mapping) else None
    out: dict[str, set[str]] = {}
    if isinstance(groups, dict):
        for name, record in groups.items():
            roots = record.get("roots") if isinstance(record, dict) else record
            out[str(name)] = {str(r) for r in (roots or [])}
    elif isinstance(groups, list):
        for record in groups:
            if not isinstance(record, dict):
                continue
            name = str(record.get("name") or record.get("id") or "").strip()
            if name:
                out[name] = {str(r) for r in (record.get("roots") or [])}
    return out


def owner_problems(integration: Integration, registry: Mapping) -> list[str]:
    """Provisioning's check: the owner must exist and must already hold every root the workflow asks for.

    This is the per-workflow privilege bound. A workflow cannot be provisioned with more document access
    than the person who requested it has, and a subject the deployment does not declare cannot own one.
    """
    roots = owner_roots(registry, integration.owner_subject)
    if roots is None:
        return [f"owner {integration.owner_subject!r} is not declared in the access registry"]
    missing = sorted(set(integration.roots) - set(roots))
    if missing:
        return [f"owner {integration.owner_subject!r} does not hold root(s) {missing}"]
    return []
