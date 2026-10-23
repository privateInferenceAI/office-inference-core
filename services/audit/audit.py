#!/usr/bin/env python3
"""Audit sink CLI.

The one place that writes to, reads from, and exports the append-only audit record. Everything
else in the system (the assistant API, the agent runtime, workflows) records through this, so the
chain has a single writer path rather than several.

Subcommands:

    migrate      apply db/migrations/*.sql in order, recording what was applied
    record       append one event
    reattest     append a statement about an earlier event, without touching it
    list         show recent events
    backfill     copy the gateway's model calls into the audit record (idempotent)
    export       write a day's events as JSONL plus a manifest a third party can verify
    verify       recompute the chain in an export file and report whether it is intact

The hash chain: each row hashes its own fields plus the previous row's hash. Recomputing the chain
from an export detects any edit, insertion, or deletion, provided the verifier has an earlier
manifest to compare against. That is what makes this evidence rather than a log file.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
from datetime import date, datetime, timezone
from pathlib import Path

# Where the signing key is. A file rather than a variable, and the same file the API reads.
ASSERTION_KEY = Path(os.environ.get("OI_ASSERTION_KEY", "/app/rendered/assertion.key"))


def _verifier():
    """The statement verifier, imported where it lives.

    **Loaded lazily because the tool is also imported by tests that have no statement to verify.** The first
    version imported it at module level and broke four test modules at collection, which is a real signal: a
    tool whose import requires the credential machinery cannot be tested without it.

    It sits beside this file in the image, because the Dockerfile copies it, and one directory over in the
    checkout.
    """
    try:
        import assertion  # noqa: F401
        return assertion
    except ImportError:
        pass
    gateway = Path(__file__).resolve().parent.parent / "gateway"
    if (gateway / "assertion.py").exists():
        sys.path.insert(0, str(gateway))
        import assertion  # noqa: F401
        return assertion
    die("the statement verifier is not available beside this tool, so a signed record cannot be checked. "
        "The image copies services/gateway/assertion.py next to audit.py; the checkout has it one "
        "directory over.")

try:
    import psycopg
    from psycopg.types.json import Jsonb
except ModuleNotFoundError as exc:  # pragma: no cover - the message is the point
    # **A missing driver used to surface as a bare ModuleNotFoundError three frames deep**, which reads as a
    # broken tool rather than as the wrong image. It bit a C2 acceptance probe run under the ingestion image,
    # whose entrypoint has no Postgres driver, and the failure looked like the audit sink refusing a write.
    # The audit tool needs the session or eval image, and saying so costs one message.
    die("no Postgres driver in this image, so an audit record cannot be written. The audit tool needs an "
        "image that carries psycopg: oi-core/session:local or oi-core/eval:local. The ingestion image's "
        f"entrypoint does not. Original error: {exc}")

DSN = os.environ.get("DATABASE_URL")
MIGRATIONS_DIR = Path(os.environ.get("MIGRATIONS_DIR", "/app/migrations"))
EXPORTS_DIR = Path(os.environ.get("EXPORTS_DIR", "/app/exports"))
DEPLOYMENT_ID = os.environ.get("DEPLOYMENT_ID", "unknown")


def die(message: str, code: int = 1) -> None:
    print(f"error: {message}", file=sys.stderr)
    raise SystemExit(code)


def connect() -> psycopg.Connection:
    if not DSN:
        die("DATABASE_URL is not set")
    return psycopg.connect(DSN, autocommit=True)


def canonical_json(value) -> str:
    """The canonical form used for hashing. Any verifier can reproduce it exactly."""
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def sha256_hex(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def redaction_summary(redactions) -> str:
    """The redaction counts as `key=value` pairs in key order, or empty.

    Flattened rather than serialised as JSON, because the writer is Postgres and the verifier is Python and
    `jsonb::text` puts a space after every colon and comma. Matching that textually would mean reproducing one
    language's serialiser in the other, and the day either changed, every row would fail verification at once.
    Sorted pairs are a form both produce exactly.
    """
    if not redactions:
        return ""
    return ",".join(f"{key}={redactions[key]}" for key in sorted(redactions))


def approval_digest(approval) -> str | None:
    """The digest the chain covers when a row carries an approval, or None when it carries none.

    **Why a digest and not the JSON itself.** `audit_events.approval` is jsonb, and the chain is computed in two
    languages: the trigger writes each row's hash in Postgres, and `cmd_verify` recomputes it in Python. A jsonb
    value rendered as text has a space after every colon and comma and a key order the caller did not choose;
    `canonical_json` sorts keys and separates without spaces. Matching those textually would mean reproducing
    one language's serialiser inside the other, and the day either changed, every row would fail at once.
    sha256 over the canonical form is the one thing both produce exactly from the same value, so chain version 3
    covers this digest and verification compares the column to the stored JSON.

    Postgres never computes it. The writer does, and the trigger refuses an approval that arrived without one:
    a digest computed from jsonb::text would be a different byte string from the one this verifier hashes, which
    is a wrong digest rather than a missing one and fails years later with no explanation.
    """
    if approval is None:
        return None
    return sha256_hex(canonical_json(approval))


def row_canonical(row: dict) -> str:
    """The canonical string a row's hash was computed from, for that row's chain version.

    Must match `audit_row_canonical` in the database, field for field, for every version.

    The version is read PER ROW rather than assumed, and that is the whole reason this function takes a
    parameter. Recomputing every row with the newest formula would fail every row written before the formula
    changed, and a verifier that reports untouched history as tampering is one whose real findings nobody
    reads. Version 1 is the original thirteen fields; version 2 adds the redaction summary; version 3 adds the
    approval digest.

    **A null and an empty approval digest are the same field here, deliberately.** The database writes
    `coalesce(approval_sha256, '')`, because `concat_ws` skips a null argument and with it the separator, which
    would drop the field entirely rather than write it empty. This side appends the empty string for null so the
    two agree. A row with no approval therefore ends in a trailing '|', on both sides.
    """
    version = row.get("chain_version") or 1
    base = "|".join([
        row["ts_iso"], row["event_type"], row["actor"], row.get("actor_role") or "",
        row["deployment_id"], row["data_class"], row["outcome"], row.get("tool") or "",
        row.get("model") or "", row.get("request_id") or "", row["payload_sha256"],
        str(row["payload_bytes"]), row.get("prev_hash") or "",
    ])
    if version < 2:
        return base
    allowed = row.get("egress_allowed")
    version_two = "|".join([
        base,
        str(row.get("redaction_count") or 0),
        redaction_summary(row.get("redactions")),
        str(row.get("unverified_card_shapes") or 0),
        "" if allowed is None else ("true" if allowed else "false"),
    ])
    if version < 3:
        return version_two
    version_three = version_two + "|" + (row.get("approval_sha256") or "")
    if version < 4:
        return version_three
    return version_three + "|" + (row.get("prompt_sha256") or "") + "|" + (row.get("completion_sha256") or "")


# The newest chain version this verifier understands. A row claiming a higher one cannot be checked, and saying
# so is better than computing a hash from the wrong formula and calling the mismatch tampering.
KNOWN_CHAIN_VERSION = 4


# --------------------------------------------------------------------------- migrate

def cmd_migrate(args) -> int:
    with connect() as conn, conn.cursor() as cur:
        cur.execute("""
            create table if not exists schema_migrations (
              filename   text primary key,
              checksum   text not null,
              applied_at timestamptz not null default now()
            )""")
        cur.execute("select filename, checksum from schema_migrations")
        applied = dict(cur.fetchall())

        files = sorted(MIGRATIONS_DIR.glob("*.sql"))
        if not files:
            die(f"no migrations found in {MIGRATIONS_DIR}")

        ran = 0
        for path in files:
            checksum = sha256_hex(path.read_text())
            if path.name in applied:
                if applied[path.name] != checksum:
                    die(f"{path.name} was applied but has changed since. "
                        f"Applied {applied[path.name][:12]}, now {checksum[:12]}. "
                        "Write a new migration instead of editing an applied one.")
                print(f"skip    {path.name} (already applied)")
                continue
            cur.execute(path.read_text())
            cur.execute("insert into schema_migrations (filename, checksum) values (%s, %s)",
                        (path.name, checksum))
            print(f"applied {path.name}")
            ran += 1
        print(f"{ran} migration(s) applied, {len(applied)} already present")
    return 0


# --------------------------------------------------------------------------- record

def insert_event(cur, *, event_type, actor, actor_role, deployment_id, data_class, outcome,
                 tool=None, model=None, request_id=None, stored_payload=None, payload_sha256=None,
                 prompt_sha256=None, completion_sha256=None, redaction_count=0, redactions=None,
                 unverified_card_shapes=0, egress_allowed=None, approval=None) -> dict:
    """Write one event on a caller's cursor, so the row shares their transaction.

    **Why this takes a cursor, and it is the whole point.** `cmd_record` opens its own connection and the
    assistant API reaches it through `subprocess.run`, so every audit row was committed in a transaction of
    its own. That is fine for a record of what happened and wrong for a record that must not be able to
    disagree with the thing it describes: a crash between the state change and the audit write left a job
    whose row said one thing and whose history said another, and nothing could detect it.

    **WF-ENGINE-001 is the requirement this satisfies.** A step's state change and its audit row are now one
    transaction, so either both are present or neither is. Code we own can do that; every off-the-shelf
    workflow engine reports over a webhook from outside the transaction, which is the reason the engine is
    ours rather than adopted.

    **The hash definition is not duplicated by this.** `audit_row_canonical` and the `audit_events_chain`
    trigger remain the only places that decide what a row hash covers. This function writes values; it does
    not compute the chain. A second implementation of the chain would be a second answer to what the record
    means, which is what `cmd_record`'s own comment forbids.

    **`approval` is covered by the chain as of migration 025, and the digest is computed HERE.** That column was
    a jsonb field with an approval event type and it was not among the fields the canonical function took, so a
    value written there could be edited by hand while every chain check passed. That is AUD-APPROVAL-001, the
    same defect migration 013 closed for the redaction columns. Version 3 covers
    `sha256(canonical_json(approval))` rather than the JSON, because the trigger would have to hash
    `jsonb::text` and the Python verifier hashes `canonical_json`; see `approval_digest`.

    The concentration is the point: the trigger **refuses** a row whose approval arrived without a digest, so a
    caller cannot skip it, and this function computes it so a caller never supplies it.
    """
    approval_sha256 = approval_digest(approval)
    cur.execute("""
        insert into audit_events
          (event_type, actor, actor_role, deployment_id, data_class, outcome,
           tool, model, request_id, approval, approval_sha256, payload_sha256, payload,
           prompt_sha256, completion_sha256,
           redaction_count, redactions, unverified_card_shapes, egress_allowed)
        values (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
        returning id, ts_iso, row_hash""",
        (event_type, actor, actor_role, deployment_id, data_class,
         outcome, tool, model, request_id,
         Jsonb(approval) if approval is not None else None, approval_sha256, payload_sha256,
         Jsonb(stored_payload) if stored_payload is not None else None,
         prompt_sha256, completion_sha256,
         redaction_count, Jsonb(redactions) if redactions is not None else None,
         unverified_card_shapes, egress_allowed))
    row = cur.fetchone()
    return {"id": row[0], "ts_iso": row[1], "row_hash": row[2],
            "payload_sha256": payload_sha256, "approval_sha256": approval_sha256,
            "prompt_sha256": prompt_sha256, "completion_sha256": completion_sha256,
            "redaction_count": redaction_count, "redactions": redactions,
            "egress_allowed": egress_allowed}


def authenticate_actor(args, command: str = "record") -> None:
    """Resolve the actor, or refuse before anything touches the database.

    An actor is an identity, not a word someone types, and there are exactly two ways to have one.

    **A signed statement, which is what a deployment does.** The assistant API resolves a session and mints a
    statement naming the actor it found; this verifies the signature against the same key and takes the actor
    from inside it. The actor is therefore not something the caller typed, and a caller cannot name somebody
    else.

    **--dev, which is the developer with no client data.** It stays because a person at a terminal has no session
    to resolve, and it says out loud what it is.

    The guard comes first either way, so a refusal cannot leave a half-written event behind. `reattest` uses the
    same function: a statement about who held a session is worth exactly as much as the identity that made it,
    and no more.
    """
    if getattr(args, "assertion", None):
        try:
            verifier = _verifier()
            key = verifier.load_key(getattr(args, "assertion_key", None) or ASSERTION_KEY)
            payload = verifier.verify_audit(key, args.assertion)
        except Exception as exc:  # noqa: BLE001 - a refused statement is a refusal, whatever raised it
            die(f"the audit statement was refused: {exc}")
        args.actor = payload["actor"]
        args.actor_role = payload.get("actor_role") or args.actor_role
        if payload.get("deployment_id"):
            args.deployment_id = payload["deployment_id"]
    elif not getattr(args, "dev", False):
        die(f"{command} refuses a hand-typed actor outside development mode. A deployment records through "
            "the assistant API, which signs a statement naming the actor it resolved from a session. Pass "
            "--dev when you are the developer and the box holds no client data, or --assertion with a "
            "statement the API minted.")
    if getattr(args, "dev", False):
        print("warning: --dev is set, so this event's actor is whatever was typed rather than an "
              "authenticated identity", file=sys.stderr)


def cmd_record(args) -> int:
    authenticate_actor(args)

    payload = None
    if args.payload:
        raw = sys.stdin.read() if args.payload == "-" else args.payload
        if args.payload.startswith("@"):
            raw = Path(args.payload[1:]).read_text()
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError as exc:
            die(f"--payload is not valid JSON: {exc}")

    mode = content_mode(args.data_class)
    if args.store_payload and mode == "hash":
        print("warning: --store-payload is storing content that the manifest's content mode would "
              "hash, so this event keeps more than the deployment's policy asks for", file=sys.stderr)
        mode = "full"

    stored_payload = apply_content_mode(payload, mode)

    # The hashes are recorded whatever the mode. That is the point of having them: the record can
    # prove what was said without keeping it.
    if args.prompt == "-":
        if args.payload == "-":
            raise SystemExit(
                "--prompt - and --payload - both want stdin. Redirect one of them from a file instead: a "
                "record that silently took the wrong stream would put the payload where the prompt belongs.")
        args.prompt = sys.stdin.read()
    prompt_sha = sha256_hex(args.prompt) if args.prompt else None
    completion_sha = sha256_hex(args.completion) if args.completion else None
    if mode != "hash":
        if prompt_sha or completion_sha:
            stored_payload = dict(stored_payload or {})
            if prompt_sha:
                stored_payload["prompt"] = apply_content_mode(args.prompt, mode)
            if completion_sha:
                stored_payload["completion"] = apply_content_mode(args.completion, mode)

    payload_sha = sha256_hex(canonical_json(stored_payload if stored_payload is not None else {}))
    approval = json.loads(args.approval) if args.approval else None

    # The redaction summary, written to its own columns and NOT into the payload. It is metadata about the
    # request rather than content, so the content mode must not govern it: on the default hash mode the payload
    # is hashed and not stored, and a summary that lived there would leave a deployment with no record at all
    # that anything had been redacted. The probe found that on its first run.
    redactions = json.loads(args.redactions) if args.redactions else None
    if redactions is not None and not isinstance(redactions, dict):
        die('--redactions takes a JSON object of kinds to counts, as {"ssn": 2}')
    redaction_count = sum(int(v) for v in redactions.values()) if redactions else 0
    egress_allowed = None if args.egress_allowed is None else args.egress_allowed == "true"

    with connect() as conn, conn.cursor() as cur:
        written = insert_event(
            cur, event_type=args.type, actor=args.actor, actor_role=args.actor_role,
            deployment_id=args.deployment_id, data_class=args.data_class, outcome=args.outcome,
            tool=args.tool, model=args.model, request_id=args.request_id,
            stored_payload=stored_payload, payload_sha256=payload_sha,
            prompt_sha256=prompt_sha, completion_sha256=completion_sha,
            redaction_count=redaction_count, redactions=redactions,
            unverified_card_shapes=args.unverified_card_shapes, egress_allowed=egress_allowed,
            approval=approval)
    print(json.dumps({**written, "content_mode": mode}, indent=2))
    return 0


# --------------------------------------------------------------------------- reattest

#: The four assurance values an approval record may carry, in the order `services/api/approvals.py` defines them.
#: Copied rather than imported because the audit tool is shipped in its own image and must not depend on the API's
#: package layout. `tests/test_audit_reattestation.py` compares the two tuples, so the copy cannot drift.
ASSURANCE_VALUES = ("unverified", "provider_verified", "proofed", "re_attested")

#: What a re-attestation can honestly claim about itself. It is not a level on the same scale as the other three:
#: it marks a statement appended later, and it never raises what the original row says.
DEFAULT_REATTESTATION_ASSURANCE = "re_attested"


def cmd_reattest(args) -> int:
    """Append a statement about an earlier event, and never touch that event.

    **APR-UPGRADE-001, including the limit it records.** A C5-era approval cannot be upgraded: the session token
    was a bearer credential, the workstation is often shared, and a later statement by a named person is a new
    claim rather than evidence about the original act. So this appends a row that says what is known now and what
    it does not establish, and the original keeps its own assurance forever. Nothing here updates a row; the
    append-only trigger would refuse it if it tried.

    **The link carries the id AND the row hash.** An id alone can be re-pointed by anyone who can insert, because
    ids are sequential and guessable. Checking the hash against the row as it stands now makes the reference
    itself tamper-evident: an attacker who re-points the link has to produce the row it names, which means
    rewriting the chain.

    **`limits` is required rather than optional.** A re-attestation that overstates what it establishes is the
    same defect as the original sentence one layer up, and the field forces the author to write down what the
    event does not prove. It is also the field an auditor reads first.

    **The statement is stored whatever the content mode.** It is metadata about the record rather than client
    content, exactly like the redaction summary, and the default mode would otherwise append a row that says
    nothing. It is also the only copy: the payload is where the statement lives.
    """
    authenticate_actor(args, "reattest")

    with connect() as conn, conn.cursor() as cur:
        cur.execute("""
            select event_type, row_hash, payload -> 'approval' -> 'identity' ->> 'assurance'
            from audit_events where id = %s""", (args.supersedes_event_id,))
        found = cur.fetchone()
        if found is None:
            die(f"no event with id {args.supersedes_event_id}, so there is nothing to re-attest")
        superseded_type, row_hash, stated_assurance = found

        if row_hash != args.supersedes_row_hash:
            die(f"event {args.supersedes_event_id} hashes to {row_hash}, and --supersedes-row-hash says "
                f"{args.supersedes_row_hash}. A re-attestation names the id and the row hash together so the "
                "link cannot be re-pointed at a different row, and refusing a mismatch is the point of the "
                "second field.")

        # The original's assurance is read from the row rather than taken on trust. A statement that misstates
        # what the earlier record claimed is worse than no statement, because it would be believed.
        original_assurance = args.original_assurance or stated_assurance or "unverified"
        if stated_assurance and args.original_assurance and stated_assurance != args.original_assurance:
            die(f"event {args.supersedes_event_id} records its assurance as {stated_assurance} and "
                f"--original-assurance says {args.original_assurance}. The re-attestation has to state what the "
                "earlier record said, not what it would be convenient for it to have said.")

        attested_by = args.attested_by or args.actor
        if attested_by != args.actor:
            die(f"--attested-by is {attested_by} and the authenticated actor is {args.actor}. A statement about "
                "who held a session is worth exactly the identity that signed it, so this command records the "
                "signer and refuses to attribute the claim to somebody else.")

        payload = {
            "kind": "reattestation",
            "supersedes_event_id": args.supersedes_event_id,
            "supersedes_row_hash": args.supersedes_row_hash,
            "original_assurance": original_assurance,
            "new_assurance": args.new_assurance,
            "basis": args.basis,
            "resolved_subject": args.resolved_subject,
            "attested_by": attested_by,
            "reason": args.reason,
            "limits": args.limits,
        }
        payload_sha = sha256_hex(canonical_json(payload))

        written = insert_event(
            cur, event_type="reattestation", actor=args.actor,
            actor_role=getattr(args, "actor_role", None),
            deployment_id=args.deployment_id, data_class=args.data_class, outcome=args.outcome,
            stored_payload=payload, payload_sha256=payload_sha)

    print(json.dumps({
        "id": written["id"],
        "row_hash": written["row_hash"],
        "supersedes_event_id": args.supersedes_event_id,
        "supersedes_event_type": superseded_type,
        "supersedes_row_hash": args.supersedes_row_hash,
        "payload_sha256": payload_sha,
    }, indent=2))
    return 0


# --------------------------------------------------------------------------- purge

def cmd_purge(args) -> int:
    """Remove events past their retention window, and leave a tombstone that explains the gap.

    A retention purge is a deliberate hole in the chain. Without a tombstone the hole is
    indistinguishable from tampering: rows would simply be missing, and the only artifact that could
    tell the difference is an anchor, which would report the loss without knowing why.

    The tombstone goes in before the rows come out, so its prev_hash is the chain head before the
    purge and its row_hash is the head after. The two hashes the record needs are therefore the row's
    own columns rather than a copy that could drift from them.

    Everything happens in one transaction that switches the maintenance setting on for its duration
    and off again at commit. That setting is what the append-only trigger checks.
    """
    cutoff = args.before
    try:
        datetime.fromisoformat(cutoff)
    except ValueError:
        die(f"--before must be an ISO date, got {cutoff!r}")

    with connect() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                select count(*), min(id), max(id) from audit_events
                where occurred_at < %s and event_type <> 'purge'""", (cutoff,))
            count, first_id, last_id = cur.fetchone()

            cur.execute("select id, row_hash from audit_events order by id desc limit 1")
            head = cur.fetchone()
            head_before = head[1] if head else None

        if count == 0:
            print(f"nothing to purge before {cutoff}: the chain head is {head_before}")
            return 0
        if args.dry_run:
            print(f"would purge {count} event(s) before {cutoff} (ids {first_id}..{last_id})")
            print(f"  the tombstone would chain from {head_before}")
            return 0

        payload = {"deleted_count": count, "first_id": first_id, "last_id": last_id,
                   "before": cutoff, "reason": args.reason}
        payload_sha = sha256_hex(canonical_json(payload))

        # An explicit transaction, because this connection is in autocommit mode: SET LOCAL outside a
        # transaction block is ignored with a warning, which is how the first version of this command
        # wrote a tombstone and then failed to delete anything. Either the tombstone and the deletion
        # both happen or neither does.
        with conn.transaction():
            with conn.cursor() as cur:
                cur.execute("set local oi.allow_audit_maintenance = 'on'")
                cur.execute("""
                    insert into audit_events
                      (event_type, actor, actor_role, deployment_id, data_class, outcome,
                       payload_sha256, payload)
                    values ('purge', %s, 'operator', %s, 'internal', 'ok', %s, %s)
                    returning id, ts_iso, prev_hash, row_hash""",
                    (args.actor, DEPLOYMENT_ID, payload_sha, Jsonb(payload)))
                tombstone = cur.fetchone()

                cur.execute("""
                    delete from audit_events where occurred_at < %s and event_type <> 'purge'""",
                    (cutoff,))
                deleted = cur.rowcount

    print(f"purged {deleted} event(s) before {cutoff}")
    print(json.dumps({"tombstone_id": tombstone[0], "ts_iso": tombstone[1],
                      "head_before": tombstone[2], "head_after": tombstone[3],
                      "deleted_count": count, "first_id": first_id, "last_id": last_id,
                      "reason": args.reason}, indent=2))
    print("prev_hash is the head before the purge and row_hash is the head after, so an export "
          "starting at this tombstone can be verified on its own.")
    return 0


# --------------------------------------------------------------------------- list

def cmd_list(args) -> int:
    with connect() as conn, conn.cursor() as cur:
        where, params = "", []
        if args.type:
            where, params = "where event_type = %s", [args.type]
        cur.execute(f"""
            select id, ts_iso, event_type, actor, data_class, outcome, coalesce(model, tool, '')
            from audit_events {where} order by id desc limit %s""", params + [args.limit])
        rows = cur.fetchall()
    print(f"{'id':>6}  {'when':<27}  {'type':<13}  {'actor':<22}  {'class':<13}  {'outcome':<7}  detail")
    for row in rows:
        print(f"{row[0]:>6}  {row[1]:<27}  {row[2]:<13}  {row[3]:<22}  {row[4]:<13}  {row[5]:<7}  {row[6]}")
    print(f"{len(rows)} event(s)")
    return 0


# --------------------------------------------------------------------------- backfill

def call_outcome(status: str | None, error_code: str | None, normalized_error: str | None) -> str:
    """What the chain should say happened, from what the gateway recorded.

    A refused call reaches the spend log with `status = 'failure'`, a zero-token usage object, and the
    reason in `metadata.error_information`. An earlier version of this tool recorded those as
    successes, which put "a model answered" into the record for a call the gateway refused. A record
    that says the wrong thing is worse than a gap in it.
    """
    if (status or "").lower() == "success":
        return "ok"
    code = str(error_code or normalized_error or "")
    # 4xx is the gateway refusing the caller: over budget, not permitted that model, a bad key. 5xx
    # and anything unrecognised is a failure rather than a decision, and the two should not look
    # alike to whoever reads this later.
    return "denied" if code.startswith("4") else "error"


# Pattern-based redaction for the "redacted" content mode. Deliberately small and deliberately
# documented as such: it catches the identifiers that show up in business documents, and it is not a
# compliance guarantee. SEC-REDACT-001 in C2 owns the real engine, which has to run before anything
# leaves for a frontier model rather than only on the way into the record.
REDACTION_PATTERNS = (
    ("ssn", re.compile(r"\b\d{3}-\d{2}-\d{4}\b")),
    ("card", re.compile(r"\b(?:\d[ -]?){13,16}\b")),
    ("email", re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.]{2,}\b")),
    ("phone", re.compile(r"\b(?:\+1[ -]?)?\(?\d{3}\)?[ -]?\d{3}[ -]?\d{4}\b")),
)

CONTENT_MODES = ("hash", "redacted", "full")


def content_mode(data_class: str) -> str:
    """The mode for a data class, from the rendered environment.

    The renderer writes CONTENT_MODE_<CLASS> for all four classes and CONTENT_DEFAULT for anything
    else, so this reads a decision the manifest made rather than one made here. An unset environment
    falls back to hashing, because storing content nobody asked to store is the failure that matters.
    """
    explicit = os.environ.get(f"CONTENT_MODE_{data_class.upper()}")
    if explicit in CONTENT_MODES:
        return explicit
    default = os.environ.get("CONTENT_DEFAULT")
    return default if default in CONTENT_MODES else "hash"


def redact(text: str) -> str:
    for label, pattern in REDACTION_PATTERNS:
        text = pattern.sub(f"[REDACTED-{label.upper()}]", text)
    return text


def apply_content_mode(value, mode: str):
    """What to store for a piece of content under a mode: nothing, a redacted copy, or the text."""
    if value is None:
        return None
    if mode == "hash":
        return None
    if mode == "redacted":
        return redact(value) if isinstance(value, str) else value
    return value


def role_from_alias(alias: str | None) -> str:
    """The role out of a key alias like 'deployment:analyst', and the gateway itself when there is no
    alias, because an unattributed event still beats an unrecorded one."""
    if not alias or ":" not in alias:
        return "gateway"
    return alias.rsplit(":", 1)[1]


def cmd_backfill(args) -> int:
    """Copy the gateway's model calls and its refusals into the audit record, once each.

    Rows older than the newest purge's cutoff are left alone. Without that, the next backfill would
    re-create the events a retention purge had just removed, because the gateway's own tables are not
    subject to our retention window. A purge that undoes itself on the next run is not a purge.
    """
    with connect() as conn, conn.cursor() as cur:
        cur.execute("""
            select max((payload ->> 'before')::timestamptz) from audit_events
            where event_type = 'purge' and payload ? 'before'""")
        watermark = cur.fetchone()[0]

        # One source, and it carries everything: status, usage, and, when something went wrong, the
        # error information and the alias of the key that asked. The gateway's separate error table
        # stays empty through these refusals, which is why this reads spend logs instead.
        cur.execute("""
            select s.request_id, s.model, s.total_tokens, s.prompt_tokens, s.completion_tokens,
                   s.spend, s.custom_llm_provider, s."startTime", s.api_key, s.status,
                   s.metadata -> 'error_information' ->> 'error_code',
                   s.metadata -> 'error_information' ->> 'error_class',
                   s.metadata -> 'error_information' ->> 'normalized_error',
                   s.metadata -> 'error_information' ->> 'error_message',
                   s.metadata -> 'error_information' ->> 'error_budget_limit',
                   s.metadata -> 'error_information' ->> 'error_budget_spend',
                   s.metadata ->> 'user_api_key_alias'
            from "LiteLLM_SpendLogs" s
            where s.request_id is not null
              and (%s::timestamptz is null or s."startTime" >= %s::timestamptz)
              and not exists (select 1 from audit_events a
                              where a.event_type = 'model_call' and a.request_id = s.request_id)
            order by s."startTime"
            limit %s""", (watermark, watermark, args.limit))
        rows = cur.fetchall()

        inserted, refused = 0, 0
        for (request_id, model, total, prompt_t, completion_t, spend, provider, started, api_key,
             status, error_code, error_class, normalized_error, error_message,
             budget_limit, budget_spend, key_alias) in rows:
            outcome = call_outcome(status, error_code, normalized_error)
            payload = {"model": model, "total_tokens": total, "prompt_tokens": prompt_t,
                       "completion_tokens": completion_t, "spend_usd": float(spend or 0),
                       "provider": provider, "key_alias": key_alias}
            if outcome != "ok":
                # The message is hashed rather than stored, because error strings are the field most
                # likely to quote a prompt back at us. The code and the class say what happened, and
                # the budget numbers are the two an operator actually wants.
                payload.update({"error_code": error_code, "error_class": error_class,
                                "normalized_error": normalized_error,
                                "error_message_sha256": sha256_hex(error_message or "")})
                if budget_limit or budget_spend:
                    payload["error_budget_limit"] = budget_limit
                    payload["error_budget_spend"] = budget_spend
            payload_sha = sha256_hex(canonical_json(payload))
            actor = "gateway-key:" + hashlib.sha256((api_key or "unknown").encode()).hexdigest()[:8]
            cur.execute("""
                insert into audit_events
                  (occurred_at, event_type, actor, actor_role, deployment_id, data_class,
                   outcome, model, request_id, payload_sha256, payload)
                values (%s,'model_call',%s,%s,%s,'internal',%s,%s,%s,%s,%s)""",
                (started, actor, role_from_alias(key_alias), DEPLOYMENT_ID, outcome, model,
                 request_id, payload_sha, Jsonb(payload)))
            if outcome == "ok":
                inserted += 1
            else:
                refused += 1

    print(f"backfilled {inserted} successful call(s), {refused} refused or failed")
    return 0


# --------------------------------------------------------------------------- export

def cmd_export(args) -> int:
    day = args.date or datetime.now(timezone.utc).date().isoformat()
    start = datetime.fromisoformat(day).replace(tzinfo=timezone.utc)
    out_dir = Path(args.out) if args.out else EXPORTS_DIR / day
    out_dir.mkdir(parents=True, exist_ok=True)
    data_path = out_dir / "audit-events.jsonl"

    with connect() as conn, conn.cursor() as cur:
        cur.execute("""
            select id, occurred_at, ts_iso, event_type, actor, actor_role, deployment_id,
                   data_class, outcome, tool, model, request_id, approval, approval_sha256,
                   payload_sha256, payload_bytes, payload, prev_hash, row_hash, chain_version,
                   prompt_sha256, completion_sha256,
                   redaction_count, redactions, unverified_card_shapes, egress_allowed
            from audit_events
            where occurred_at >= %s and occurred_at < %s + interval '1 day'
            order by id""", (start, start))
        columns = [c.name for c in cur.description]
        rows = [dict(zip(columns, r)) for r in cur.fetchall()]

    with data_path.open("w") as handle:
        for row in rows:
            row["occurred_at"] = row["occurred_at"].isoformat()
            handle.write(canonical_json(row) + "\n")

    manifest = {
        "deployment_id": DEPLOYMENT_ID,
        "date": day,
        "event_count": len(rows),
        "first_id": rows[0]["id"] if rows else None,
        # The chain anchor: the hash this export's first row chains back to. Without it an export
        # cannot be checked on its own, because a day's rows normally start mid-chain. A file whose
        # first line references a row outside the file is not evidence of tampering; it is Tuesday.
        "first_prev_hash": rows[0]["prev_hash"] if rows else None,
        "last_id": rows[-1]["id"] if rows else None,
        "last_row_hash": rows[-1]["row_hash"] if rows else None,
        "file": data_path.name,
        "file_sha256": sha256_hex(data_path.read_text()),
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }
    (out_dir / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")

    print(f"exported {len(rows)} event(s) for {day}")
    print(f"  {data_path}")
    print(f"  {out_dir / 'manifest.json'}")
    print(f"  last row hash {manifest['last_row_hash']}")
    return 0


# --------------------------------------------------------------------------- verify

def cmd_anchor(args) -> int:
    """Write the current chain head to a file intended to leave this machine.

    This is the only artifact that can later prove rows were not deleted from the end of the chain.
    Ship it somewhere the database owner cannot rewrite: the client's S3 bucket under Object Lock,
    or any second channel. An anchor kept beside the database it describes proves nothing.
    """
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    with connect() as conn, conn.cursor() as cur:
        cur.execute("select id, row_hash, occurred_at from audit_events order by id desc limit 1")
        last = cur.fetchone()
        cur.execute("select count(*) from audit_events")
        count = cur.fetchone()[0]
    anchor = {
        "deployment_id": DEPLOYMENT_ID,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "event_count": count,
        "last_id": last[0] if last else None,
        "last_row_hash": last[1] if last else None,
        "last_occurred_at": last[2].isoformat() if last else None,
    }
    name = out_dir / f"anchor-{anchor['created_at'][:10]}-{count:08d}.json"
    name.write_text(json.dumps(anchor, indent=2) + "\n")
    print(f"anchored {count} event(s), head {anchor['last_row_hash']}")
    print(f"  {name}")
    print("  Ship this file off this machine. Kept here, it proves nothing.")
    return 0


def cmd_verify(args) -> int:
    path = Path(args.file)
    if not path.exists():
        die(f"no such file: {path}")
    problems: list[str] = []
    notes: list[str] = []
    previous_hash = None

    # A corrupt file is one of the things a verifier exists to detect, so it has to be reported
    # rather than thrown as a traceback. Parse first, keep whatever is readable, and say plainly
    # which lines could not be read.
    lines: list[dict] = []
    for number, raw in enumerate(path.read_text().splitlines(), start=1):
        if not raw.strip():
            continue
        try:
            lines.append(json.loads(raw))
        except json.JSONDecodeError as exc:
            problems.append(f"line {number}: not valid JSON ({exc.msg} at column {exc.colno})")

    manifest_path = path.parent / "manifest.json"
    manifest = json.loads(manifest_path.read_text()) if manifest_path.exists() else None

    # The manifest records where this export starts. Checking line 1 against it proves continuity,
    # not completeness, because both came from the same export. That check was described as an
    # anchor, and it is not one. A real anchor is written earlier by the anchor job and passed in.
    has_manifest_start = bool(manifest and "first_prev_hash" in manifest)
    manifest_start = manifest.get("first_prev_hash") if has_manifest_start else None

    for index, row in enumerate(lines, start=1):
        if index == 1:
            if has_manifest_start:
                if row.get("prev_hash") != manifest_start:
                    problems.append("line 1: prev_hash does not match the manifest")
            elif row.get("prev_hash") is not None:
                notes.append("line 1 chains to a row outside this file and the manifest carries no "
                             "starting hash, so where this export starts cannot be shown")
        elif row.get("prev_hash") != previous_hash:
            problems.append(f"line {index}: prev_hash does not match the previous row")
        # A row from a newer writer cannot be checked by this verifier. Reported as an unverifiable row rather
        # than as a mismatch, because the two are different findings and only one of them is about tampering.
        # Saying nothing would be worse than either: the row would pass or fail for a reason nobody could see.
        if (row.get("chain_version") or 1) > KNOWN_CHAIN_VERSION:
            problems.append(
                f"line {index}: the row was written with chain version {row['chain_version']} and this "
                f"verifier understands up to {KNOWN_CHAIN_VERSION}, so its hash was not checked. Update the "
                f"verifier rather than treating this as a break.")
        elif sha256_hex(row_canonical(row)) != row["row_hash"]:
            problems.append(f"line {index}: row_hash does not match the row contents")

        # The payload sits inside the row, so when it is stored it must hash to payload_sha256.
        # Without this check, stored content can be rewritten while every hash still verifies,
        # which would make a retained payload worthless as evidence.
        payload = row.get("payload")
        if payload is not None and sha256_hex(canonical_json(payload)) != row.get("payload_sha256"):
            problems.append(f"line {index}: the stored payload does not match payload_sha256")

        # The approval column is covered the same way, and it was NOT until chain version 3: a row could carry
        # an approval, have it rewritten, and still verify, which is AUD-APPROVAL-001. The digest is inside the
        # canonical string, so editing it fails the row hash as well; editing the JSON alone leaves the row
        # hash valid and fails here. Both directions are checked rather than one.
        approval = row.get("approval")
        if approval is not None:
            if row.get("approval_sha256") is None:
                problems.append(
                    f"line {index}: the row carries an approval and no approval_sha256, so the approval was "
                    f"never covered by the chain and cannot be checked")
            elif sha256_hex(canonical_json(approval)) != row["approval_sha256"]:
                problems.append(f"line {index}: the stored approval does not match approval_sha256")
        previous_hash = row["row_hash"]

    # This file's own window, as the FILE states it. Both checks below need it: the anchor check to place
    # the anchored row, and the manifest check to notice that the manifest describes a longer window than
    # the file does. Defined here rather than inside either block, because a branch that skipped the anchor
    # used to leave it unset and the manifest check then raised.
    window_rows = [row for row in lines if row.get("id") is not None]
    window_first = window_rows[0]["id"] if window_rows else None
    window_last = window_rows[-1]["id"] if window_rows else None

    # The only check that can catch a deleted tail, because it compares this export against a hash
    # published earlier, somewhere the database owner does not control.
    #
    # **This compares POSITIONS, not counts, and the difference is the whole correctness of the check.**
    # An export is a window over the chain (first_id..last_id); an anchor is a point in it (last_id,
    # last_row_hash, event_count). The first version compared `len(lines)` against the anchor's
    # `event_count`, which is a cumulative whole-table count — a category error that is invisible on a
    # chain one day long, because there the export IS the chain. On a deployment older than a day it
    # reported "the chain is shorter than it was" against a file nobody had touched, and when the anchor
    # happened to be written after the window's last event it accused the client's file of losing rows.
    #
    # The vocabulary for the fix was already being recorded and simply never read: the anchor has carried
    # `last_id` since it was written, and the manifest carries `first_id` and `last_id`. With positions,
    # three relations exist and only one of them is a finding:
    #
    #   anchor before the window   the window is newer than the anchor, so the anchor says nothing about
    #                              this file in either direction. A note, not a verdict.
    #   anchor inside the window   the ordinary case. The anchored row must be present, which is what makes
    #                              a deleted tail visible: removing rows from the end removes the anchor.
    #   anchor beyond the window   the window is older than the anchor, which is the one genuine
    #                              "the chain was longer than this file shows".
    #
    # `event_count` deliberately plays no part in any of those. It is recorded for a person reading the
    # anchor, and nothing decides on it.
    if args.anchor:
        anchor_path = Path(args.anchor)
        if not anchor_path.exists():
            die(f"no such anchor file: {anchor_path}")
        anchor = json.loads(anchor_path.read_text())
        anchored_hash = anchor.get("last_row_hash")
        anchored_id = anchor.get("last_id")
        anchored_count = anchor.get("event_count")

        # **An empty file is the case that matters most.** With no rows there is no window to read, and an
        # empty export against an anchor saying rows existed is the loudest signal this check can receive.
        # Treating it as "no position, conclude nothing" would turn the worst possible finding into a note,
        # so the manifest's own window stands in.
        if window_first is None and manifest:
            window_first = manifest.get("first_id")
            window_last = manifest.get("last_id")

        if not anchored_hash:
            problems.append(f"the anchor {anchor_path.name} carries no last_row_hash, so it cannot "
                            "prove anything")
        elif window_first is None:
            # **The loudest case, and the one where a count comparison was accidentally right.** An empty
            # export against an anchor that records rows is the worst finding this check can receive, so
            # it must not degrade into a note just because there are no row ids to reason with.
            #
            # Time is the honest fallback. Both artifacts carry it: the anchor its `created_at`, the export
            # its day. An anchor written on or before the day this export claims to cover should be visible
            # in it. Ordering by time is a sound relation; ordering by a cumulative count was not, which is
            # the error this whole block replaced.
            anchored_day = str(anchor.get("created_at") or "")[:10]
            exported_day = str((manifest or {}).get("date") or "")
            if anchored_day and exported_day and anchored_day <= exported_day:
                problems.append(
                    f"the anchor records row {anchored_id} and was written on {anchored_day}, on or before "
                    f"the day this export covers ({exported_day}), and this export holds no rows at all. "
                    f"Rows that existed when the anchor was written are not in this file")
            elif any(row.get("row_hash") == anchored_hash for row in lines):
                notes.append("the anchored row is in this export, but neither artifact carries the row ids "
                             "needed to place it, so completeness cannot be shown")
            else:
                notes.append(f"the anchor {anchor_path.name} carries no row id and its row is not in this "
                             f"export, so nothing can be concluded about this file in either direction")
        elif anchored_id is None:
            notes.append(f"the anchor {anchor_path.name} carries no row id, so it cannot be placed against "
                         f"this window ({window_first}..{window_last}) and completeness cannot be shown")
        elif anchored_id < window_first:
            notes.append(f"the anchor was written before this export's window (anchored row {anchored_id}, "
                         f"this file starts at {window_first}), so it says nothing about this file. Verify "
                         f"it against a later export, or against one that covers row {anchored_id}")
        elif anchored_id > window_last:
            # The anchored row is past the end of this window. **This is where the check has to be honest
            # about a limit rather than choose a verdict**, and the limit is worth stating because it took a
            # wrong answer in each direction to find it.
            #
            # Two situations produce this shape and one anchor cannot separate them:
            #
            #   the chain grew         the anchor was taken after this window closed, and the extra rows
            #                          are simply later work. Nothing was lost.
            #   the file was truncated the window really did lose its tail, and the anchor's row is beyond
            #                          the shortened end.
            #
            # Counting cannot separate them: both make the anchor's count exceed this window's last row id.
            # Reporting it as loss accuses an honest file, which is what this check used to do. Reporting it
            # as a match claims a completeness the file has not demonstrated.
            #
            # What does separate them is the NEXT export: its opening `first_prev_hash` must equal this
            # file's closing `last_row_hash`. That is a two-file check, and the manual documents it as a
            # manual step. Until a verifier performs it, an auditor is told plainly what this file does and
            # does not establish.
            notes.append(
                f"the anchor records row {anchored_id} and this export ends at row {window_last}, so the "
                f"anchored row is past this window. Either the chain grew after this window closed or this "
                f"file lost its tail, and one anchor cannot tell them apart. Check the next day's export: "
                f"its first_prev_hash must equal this file's last_row_hash")
        elif not any(row.get("row_hash") == anchored_hash for row in lines):
            problems.append(f"the anchored row {anchored_hash[:12]}... (row {anchored_id}) falls inside "
                            f"this export's window {window_first}..{window_last} and is not in the file, so "
                            f"it has been removed from the middle of this export")
        else:
            # Name the two numbers for what they are. The anchor's `event_count` is how many rows the
            # chain held when it was written, which is not the row id, and printing them as "row 61 of 49"
            # reads as a contradiction rather than as two different facts.
            notes.append(f"matches the anchor written {anchor.get('created_at', 'at an unknown time')} "
                         f"({anchored_hash[:12]}..., the anchor's row {anchored_id}; the chain held "
                         f"{anchored_count} rows then)")
    else:
        notes.append("no external anchor was supplied, so completeness cannot be shown: deleting "
                     "rows from the end and re-exporting would still verify. Pass --anchor with the "
                     "file written by the anchor job")

    if manifest:
        if manifest.get("file_sha256") != sha256_hex(path.read_text()):
            problems.append("the file hash does not match the manifest")
        if manifest.get("last_row_hash") != previous_hash:
            problems.append("the final row hash does not match the manifest")
        if manifest.get("event_count") != len(lines):
            problems.append("the event count does not match the manifest")
        # The row ids, which is the check that catches a truncated tail even when the manifest was
        # regenerated to match. `event_count` and `last_row_hash` are both recomputable by whoever truncated
        # the file; `first_id` and `last_id` are the chain's own sequence numbers, and a file that ends at
        # row 47 while its manifest claims a window ending at row 49 has lost rows whatever the hashes say.
        #
        # Without this, the anchor was the only defence against a deleted tail, and it defends only when the
        # anchored row falls INSIDE the window -- truncation past the anchor's row moves it outside and the
        # anchor stops seeing it. This closes that.
        if manifest.get("last_id") is not None and window_last is not None \
                and manifest["last_id"] != window_last:
            problems.append(
                f"the manifest says this export ran to row {manifest['last_id']} and the file ends at row "
                f"{window_last}: rows are missing from the end of this file")
        if manifest.get("first_id") is not None and window_first is not None \
                and manifest["first_id"] != window_first:
            problems.append(
                f"the manifest says this export started at row {manifest['first_id']} and the file starts "
                f"at row {window_first}: rows are missing from the start of this file")
        if not has_manifest_start:
            notes.append("the manifest carries no starting hash, so this file cannot show where in "
                         "the chain it begins")
        elif manifest_start is not None:
            notes.append(f"chained from row {manifest.get('first_id') - 1} "
                         f"({manifest_start[:12]}...), which is not in this file")
    else:
        notes.append("no manifest found next to the file; only internal continuity was checked")

    if problems:
        print(f"FAIL {path}")
        for problem in problems:
            print(f"  - {problem}")
        return 1
    print(f"ok   {path}: {len(lines)} event(s), chain intact")
    for note in notes:
        print(f"     note: {note}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    """The command line, in one function so the tests can parse one without running it.

    `main` used to build and consume the parser together, which left no way to assert that a flag exists short of
    invoking the whole tool. A flag is part of the record's interface: a re-attestation is only complete because
    every one of its fields can be supplied, so the interface is worth a test.
    """
    parser = argparse.ArgumentParser(description="Audit sink CLI")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("migrate").set_defaults(func=cmd_migrate)

    record = sub.add_parser("record")
    record.add_argument("--type", required=True,
                        choices=["model_call", "tool_call", "approval", "access",
                                 "config_change", "login", "context_trim", "resume",
                                 # Recorded by `reattest`, but accepted here too so a caller that has already
                                 # built a statement can write one through the generic path. The generic path
                                 # does not check the superseded row; `reattest` is the command that does.
                                 "reattestation"])
    # Not required at the parser, because a signed statement names the actor and this command line does not.
    # The guard in cmd_record refuses the case where neither is present, and it refuses before the database is
    # touched, which is where that check belongs.
    record.add_argument("--actor", help="the actor, for a development record. A deployment passes --assertion "
                                       "instead and the actor comes from inside it")
    record.add_argument("--assertion", help="a statement the assistant API minted, naming the actor. The "
                                            "deployment path: the actor comes from inside the signature "
                                            "rather than from this command line")
    record.add_argument("--assertion-key", help="where the signing key is, for a test or a different layout")
    record.add_argument("--dev", action="store_true",
                        help="accept a hand-typed actor. Development only: a deployment takes the "
                             "actor from an authenticated session, which is the assistant API's job")
    record.add_argument("--actor-role")
    record.add_argument("--deployment-id", default=DEPLOYMENT_ID)
    record.add_argument("--data-class", default="internal",
                        choices=["public", "internal", "confidential", "regulated"])
    record.add_argument("--outcome", default="ok", choices=["ok", "denied", "error", "pending"])
    record.add_argument("--tool")
    record.add_argument("--model")
    record.add_argument("--request-id")
    record.add_argument("--approval", help="JSON describing the approval")
    record.add_argument("--payload", help="JSON, @file, or - for stdin")
    record.add_argument("--redactions", help="JSON object of kinds to counts removed before this request "
                                             "left, as {\"ssn\": 2}. Metadata, so recorded whatever the content "
                                             "mode: a deployment on hash mode would otherwise keep no record "
                                             "that anything was redacted. Never the values.")
    record.add_argument("--unverified-card-shapes", type=int, default=0,
                        help="long numbers redacted that failed the card check digit")
    record.add_argument("--egress-allowed", choices=["true", "false"],
                        help="whether the data-class gate permitted this request to leave")
    record.add_argument("--prompt", help="the prompt as sent. Its hash is always recorded; the text "
                                          "is stored only if the content mode allows it. **Pass `-` to read it "
                                          "from stdin**, which is not a convenience: a prompt on the command "
                                          "line is bounded by ARG_MAX, and a question past roughly 128 KB made "
                                          "the write fail with `Argument list too long` — so the request that "
                                          "most needed a record was the one that could not have one.")
    record.add_argument("--completion", help="the completion as returned, under the same rule")
    record.add_argument("--store-payload", action="store_true",
                        help="keep the payload itself, not just its hash. A policy decision.")
    record.set_defaults(func=cmd_record)

    reattest = sub.add_parser("reattest", help="append a statement about an earlier event, without touching it")
    reattest.add_argument("--supersedes-event-id", type=int, required=True,
                          help="the id of the event being re-attested")
    reattest.add_argument("--supersedes-row-hash", required=True,
                          help="that event's row_hash, checked against the row as it is now. The id alone could "
                               "be re-pointed; the hash is what makes the reference tamper-evident")
    reattest.add_argument("--original-assurance", choices=ASSURANCE_VALUES,
                          help="what the earlier record said about identity. Defaults to what the row itself "
                               "records, and a value that disagrees with the row is refused")
    reattest.add_argument("--new-assurance", choices=ASSURANCE_VALUES,
                          default=DEFAULT_REATTESTATION_ASSURANCE,
                          help="what this statement is made under. It does not change the original row's "
                               "assurance, which is the point of section 5.4")
    reattest.add_argument("--basis", required=True,
                          help="how the identity was established, as the design's 'basis' field records it")
    reattest.add_argument("--resolved-subject", required=True,
                          help="the account the session is now known to have belonged to")
    reattest.add_argument("--attested-by",
                          help="the person making this statement. Defaults to the authenticated actor, and any "
                               "other value is refused: the claim is worth the signer and nothing more")
    reattest.add_argument("--reason", required=True, help="why this statement is being appended now")
    reattest.add_argument("--limits", required=True,
                          help="what this statement does NOT establish. Required, because a re-attestation that "
                               "overstates itself is the same defect one layer up")
    reattest.add_argument("--assertion", help="a statement the assistant API minted, naming the actor")
    reattest.add_argument("--assertion-key", help="where the signing key is, for a test or a different layout")
    reattest.add_argument("--dev", action="store_true",
                          help="accept a hand-typed actor. Development only, as for record")
    reattest.add_argument("--actor", help="the actor, for a development re-attestation")
    reattest.add_argument("--actor-role")
    reattest.add_argument("--deployment-id", default=DEPLOYMENT_ID)
    reattest.add_argument("--data-class", default="internal",
                          choices=["public", "internal", "confidential", "regulated"])
    reattest.add_argument("--outcome", default="ok", choices=["ok", "denied", "error", "pending"])
    reattest.set_defaults(func=cmd_reattest)

    purge = sub.add_parser("purge", help="delete events past their retention window, leaving a tombstone")
    purge.add_argument("--before", required=True, metavar="DATE",
                       help="remove events that occurred before this ISO date")
    purge.add_argument("--reason", default="retention", help="why the purge is happening, recorded in the tombstone")
    purge.add_argument("--actor", default="retention-job", help="who or what is running it")
    purge.add_argument("--dry-run", action="store_true", help="report what would go, and change nothing")
    purge.set_defaults(func=cmd_purge)

    listing = sub.add_parser("list")
    listing.add_argument("--limit", type=int, default=20)
    listing.add_argument("--type")
    listing.set_defaults(func=cmd_list)

    backfill = sub.add_parser("backfill")
    backfill.add_argument("--limit", type=int, default=1000)
    backfill.set_defaults(func=cmd_backfill)

    export = sub.add_parser("export")
    export.add_argument("--date", help="YYYY-MM-DD, defaults to today in UTC")
    export.add_argument("--out")
    export.set_defaults(func=cmd_export)

    verify = sub.add_parser("verify")
    verify.add_argument("file")
    verify.add_argument("--anchor", help="anchor file written earlier by the anchor command; "
                                         "without it, completeness cannot be shown")
    verify.set_defaults(func=cmd_verify)

    anchor = sub.add_parser("anchor")
    # The default is the mounted exports directory, not a bare relative path: the tool runs as the
    # operator inside a container whose /app is root-owned, so a relative default died with a
    # PermissionError instead of writing anything.
    anchor.add_argument("--out", default="/app/exports/anchors",
                        help="directory to write the anchor into (default /app/exports/anchors, "
                             "which is the path mounted back to the host)")
    anchor.set_defaults(func=cmd_anchor)

    return parser


def main() -> int:
    args = build_parser().parse_args()
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
