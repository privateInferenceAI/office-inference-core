"""The chain row, written on the engine's cursor, inside the engine's transaction.

**One writer, and this is not a second one.** `services/audit/audit.py` owns the chain: the INSERT statement
lives in `insert_event`, the hash is computed by the `audit_events_chain` trigger from `audit_row_canonical`,
and the actor guard is `assertion.verify_audit`. This module calls all three and adds nothing to any of them.
It exists because the engine holds a cursor and `cmd_record` cannot take one — the CLI opens its own
connection, which is exactly what made the audit row a separate transaction from the state change.

**Why the actor is a statement rather than a string.** `cmd_record` refuses a hand-typed actor, and the reason
is not ceremony: an actor in the chain is a claim about who did something, and a caller that can type one can
name somebody else. The engine takes a signed statement, verifies it against the deployment's key, and records
the actor from *inside* the verified payload. A caller cannot pass a name here even if it wants to.

**Why `payload_sha256` is computed here and not by the tool.** Migration 013's rule: the hash covers the
payload *after* the content mode has been applied, and only the caller knows the mode. On the default hash
mode the stored payload is null and the digest is the digest of nothing, which is the correct record of a
deployment that chose not to keep the content.
"""
from __future__ import annotations

import importlib.util
import os
import sys
from pathlib import Path
from typing import Optional

_AUDIT = None


class ChainRefused(Exception):
    """A row this module will not write. Always a refusal about the actor or the tool, never about the data."""


def _candidates() -> list[str]:
    env = (os.environ.get("AUDIT_TOOL") or "").strip()
    here = Path(__file__).resolve().parent
    return [path for path in (env, "/app/audit.py", str(here.parent / "audit" / "audit.py")) if path]


def audit_tool():
    """`audit.py`, loaded from wherever this deployment keeps it.

    **Loaded by path rather than by import.** In the API's container the tool is mounted at `/app/audit.py`;
    in the checkout it is `services/audit/audit.py`; the probe reaches it through the mounted checkout. An
    `import audit` would work in one of those three and fail with a traceback in the others, which reads as a
    broken engine rather than as the wrong path.

    Loaded once per process, because a module reload per row would re-run the tool's import guards and its
    driver check on every step.
    """
    global _AUDIT
    if _AUDIT is not None:
        return _AUDIT
    tried = []
    for path in _candidates():
        tried.append(path)
        target = Path(path)
        if not target.is_file():
            continue
        try:
            spec = importlib.util.spec_from_file_location("oi_audit_tool", target)
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
        except SystemExit as refused:  # audit.py dies() rather than raising when a driver is missing
            raise ChainRefused(f"the audit tool at {path} refused to load: {refused}") from refused
        except Exception as exc:  # noqa: BLE001 - any failure to load is a refusal to write
            raise ChainRefused(f"the audit tool at {path} could not be loaded: {exc}") from exc
        _AUDIT = module
        return module
    raise ChainRefused(
        "no audit tool was found, so no chain row can be written. Looked at: " + ", ".join(tried)
        + ". The API container mounts it at /app/audit.py; the checkout has it at services/audit/audit.py.")


def verify_statement(statement: str, *, key_path: Optional[str] = None) -> dict:
    """The claims inside a signed statement, or a refusal that says which check failed.

    Deliberately the same three checks `cmd_record` applies, in the same order: the signature and expiry
    (`verify`), the purpose (`audit`, so a statement minted for a model call cannot write a chain row), and the
    presence of an actor. A statement is good for the one thing it was minted for.
    """
    tool = audit_tool()
    verifier = tool._verifier()
    key = verifier.load_key(key_path or tool.ASSERTION_KEY)
    try:
        claims = verifier.verify_audit(key, statement)
    except Exception as exc:  # noqa: BLE001 - a refused statement is a refusal, whatever raised it
        raise ChainRefused(f"the audit statement was refused: {exc}") from exc
    return claims


def record(cur, *, statement: str, event_type: str, outcome: str, data_class: str,
           deployment_id: str = "", payload: Optional[dict] = None, tool_name: str = "",
           model: str = "", request_id: str = "", redactions: Optional[dict] = None,
           egress_allowed: Optional[bool] = None, unverified_card_shapes: int = 0,
           key_path: Optional[str] = None) -> dict:
    """Write one chain row on `cur`, in whatever transaction `cur` belongs to.

    The caller owns the transaction and therefore owns the atomicity. That is the point of the function: the
    engine puts this call and the state write inside one `with store.conn.transaction()`, and a process killed
    between them leaves neither, because Postgres rolls back a transaction whose connection died.
    """
    tool = audit_tool()
    claims = verify_statement(statement, key_path=key_path)

    mode = tool.content_mode(data_class)
    stored = tool.apply_content_mode(payload, mode)
    digest = tool.sha256_hex(tool.canonical_json(stored if stored is not None else {}))

    written = tool.insert_event(
        cur,
        event_type=event_type,
        # From inside the verified statement. Not from the caller, which is why there is no `actor=` argument.
        actor=claims["actor"],
        actor_role=claims.get("actor_role") or "",
        deployment_id=claims.get("deployment_id") or deployment_id,
        data_class=data_class,
        outcome=outcome,
        tool=tool_name or None,
        model=model or None,
        request_id=request_id or None,
        stored_payload=stored,
        payload_sha256=digest,
        redactions=redactions,
        unverified_card_shapes=unverified_card_shapes,
        egress_allowed=egress_allowed,
    )
    written["content_mode"] = mode
    return written
