"""The credential, read from the client's secret store at the point of use.

`SEC-CON-001` asks for three things and they are all about the moment of use rather than the moment of
configuration.

**Not in the image.** Nothing here is baked in. The Dockerfile copies code and the container is given a
*path*, so an image that leaks teaches an attacker where to look and not what to take.

**Not in the environment.** An environment variable is visible in `docker inspect`, in `/proc/<pid>/environ`,
in a crash dump and in anything that prints the process's surroundings. The path is in the environment and the
value is in a file.

**Not cached.** This module holds nothing between calls, and that is the difference between a rotation that
takes effect and a rotation that needs a redeploy. The client's administrator replaces the file and the next
token request uses the new value. A module-level cache would turn a two-minute rotation into a maintenance
window, and worse, it would make the rotation look successful while the old credential was still in use.

The mode check is the fourth thing, and it is the one that catches a mistake rather than an attack. A
credential file written by a script running as root with a default umask is mode 0644, and the secret is then
readable by every account on the box. Refusing to read it is a loud failure at the right moment.
"""
from __future__ import annotations

import json
import os
import stat
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

# The bits that mean somebody other than the owner can read the file.
GROUP_AND_OTHER = stat.S_IRWXG | stat.S_IRWXO

REQUIRED = ("client_id", "tenant_id", "refresh_token")


class CredentialRefused(RuntimeError):
    """The credential cannot be used, with the reason named rather than a bare permission error."""


@dataclass
class Credential:
    client_id: str
    tenant_id: str
    refresh_token: str
    client_secret: str | None
    certificate_path: str | None
    path: str
    mode: str
    mtime: str

    def describes(self) -> dict:
        """What may be recorded about this credential. Never the secret, never the refresh token.

        The mtime is here because a rotation is an event a client may have to prove, and the file's
        modification time is the only part of that event the record can hold without holding the credential.
        """
        return {
            "path": self.path,
            "mode": self.mode,
            "modified_at": self.mtime,
            "has_client_secret": bool(self.client_secret),
            "has_certificate": bool(self.certificate_path),
        }


def load(path: str | os.PathLike) -> Credential:
    """Read the credential file. Every call re-reads it, which is what makes rotation work."""
    target = Path(path)
    try:
        info = target.stat()
    except FileNotFoundError:
        raise CredentialRefused(
            f"no credential at {target}. The deployment's secret store is the client's, and the intake "
            "service is given its path rather than its contents.") from None

    if not stat.S_ISREG(info.st_mode):
        raise CredentialRefused(f"{target} is not a regular file")

    if info.st_mode & GROUP_AND_OTHER:
        raise CredentialRefused(
            f"{target} is mode {stat.S_IMODE(info.st_mode):04o}, so an account other than the one running "
            "intake can read it. A rotation should write it 0600; until then this refuses to use it rather "
            "than reading a credential that is not private.")

    try:
        raw = json.loads(target.read_text())
    except json.JSONDecodeError as exc:
        raise CredentialRefused(f"{target} is not valid JSON: {exc}") from None
    if not isinstance(raw, dict):
        raise CredentialRefused(f"{target} holds {type(raw).__name__} rather than an object")

    missing = [key for key in REQUIRED if not raw.get(key)]
    if missing:
        raise CredentialRefused(
            f"{target} is missing {', '.join(missing)}. A credential needs the client id, the tenant it "
            "belongs to, and the refresh token minted by the one interactive consent.")

    return Credential(
        client_id=raw["client_id"],
        tenant_id=raw["tenant_id"],
        refresh_token=raw["refresh_token"],
        client_secret=raw.get("client_secret"),
        certificate_path=raw.get("certificate_path"),
        path=str(target),
        mode=f"{stat.S_IMODE(info.st_mode):04o}",
        mtime=datetime.fromtimestamp(info.st_mtime, timezone.utc).isoformat(),
    )


def write(path: str | os.PathLike, credential: dict, mode: int = 0o600) -> None:
    """Write a credential file, for the installer and for the rotation drill.

    It exists here, beside the reader, so the mode the reader enforces and the mode the writer sets are one
    fact rather than two that agree today. A rotation tool that wrote 0644 would be refused by the reader
    five minutes later, and the failure would land on whoever ran the rotation rather than on this file.
    """
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_suffix(target.suffix + ".part")
    temporary.write_text(json.dumps(credential, indent=2) + "\n")
    os.chmod(temporary, mode)
    os.replace(temporary, target)
