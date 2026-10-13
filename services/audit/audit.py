#!/usr/bin/env python3
"""Audit sink CLI.

The one place that writes to, reads from, and exports the append-only audit record. Everything
else in the system (the assistant API, the agent runtime, workflows) records through this, so the
chain has a single writer path rather than several.

Subcommands:

    migrate      apply db/migrations/*.sql in order, recording what was applied
    record       append one event
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

import psycopg
from psycopg.types.json import Jsonb

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


def row_canonical(row: dict) -> str:
    """Must match the canonical string built by the audit_events_chain trigger, field for field."""
    parts = [
        row["ts_iso"], row["event_type"], row["actor"], row.get("actor_role") or "",
        row["deployment_id"], row["data_class"], row["outcome"], row.get("tool") or "",
        row.get("model") or "", row.get("request_id") or "", row["payload_sha256"],
        str(row["payload_bytes"]), row.get("prev_hash") or "",
    ]
    return "|".join(parts)


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

def cmd_record(args) -> int:
    # An actor is an identity, not a word someone types. Until the assistant API passes an
    # authenticated one (C3), recording from the command line is a development act, and it says so.
    # The guard comes first, before anything touches the database, so a refusal cannot leave a
    # half-written event behind.
    if not getattr(args, "dev", False):
        die("record refuses a hand-typed actor outside development mode. An actor comes from an "
            "authenticated session, so a deployment records through the API rather than this CLI. "
            "Pass --dev when you are the developer and the box holds no client data.")
    print("warning: --dev is set, so this event's actor is whatever was typed rather than an "
          "authenticated identity", file=sys.stderr)

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
        cur.execute("""
            insert into audit_events
              (event_type, actor, actor_role, deployment_id, data_class, outcome,
               tool, model, request_id, approval, payload_sha256, payload,
               prompt_sha256, completion_sha256,
               redaction_count, redactions, unverified_card_shapes, egress_allowed)
            values (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
            returning id, ts_iso, row_hash""",
            (args.type, args.actor, args.actor_role, args.deployment_id, args.data_class,
             args.outcome, args.tool, args.model, args.request_id,
             Jsonb(approval) if approval is not None else None, payload_sha,
             Jsonb(stored_payload) if stored_payload is not None else None,
             prompt_sha, completion_sha,
             redaction_count, Jsonb(redactions) if redactions is not None else None,
             args.unverified_card_shapes, egress_allowed))
        row = cur.fetchone()
    print(json.dumps({"id": row[0], "ts_iso": row[1], "row_hash": row[2],
                      "payload_sha256": payload_sha, "content_mode": mode,
                      "prompt_sha256": prompt_sha, "completion_sha256": completion_sha,
                      "redaction_count": redaction_count, "redactions": redactions,
                      "egress_allowed": egress_allowed}, indent=2))
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
                   data_class, outcome, tool, model, request_id, approval, payload_sha256,
                   payload_bytes, payload, prev_hash, row_hash
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
        if sha256_hex(row_canonical(row)) != row["row_hash"]:
            problems.append(f"line {index}: row_hash does not match the row contents")

        # The payload sits inside the row, so when it is stored it must hash to payload_sha256.
        # Without this check, stored content can be rewritten while every hash still verifies,
        # which would make a retained payload worthless as evidence.
        payload = row.get("payload")
        if payload is not None and sha256_hex(canonical_json(payload)) != row.get("payload_sha256"):
            problems.append(f"line {index}: the stored payload does not match payload_sha256")
        previous_hash = row["row_hash"]

    # The only check that can catch a deleted tail, because it compares this export against a hash
    # published earlier, somewhere the database owner does not control.
    if args.anchor:
        anchor_path = Path(args.anchor)
        if not anchor_path.exists():
            die(f"no such anchor file: {anchor_path}")
        anchor = json.loads(anchor_path.read_text())
        anchored_hash = anchor.get("last_row_hash")
        anchored_count = anchor.get("event_count")
        if not anchored_hash:
            problems.append(f"the anchor {anchor_path.name} carries no last_row_hash, so it cannot "
                            "prove anything")
        elif not any(row.get("row_hash") == anchored_hash for row in lines):
            problems.append(f"the anchored row {anchored_hash[:12]}... from {anchor_path.name} is "
                            "missing from this export, so rows that existed when the anchor was "
                            "written are gone")
        elif anchored_count is not None and len(lines) < anchored_count:
            problems.append(f"this export holds {len(lines)} rows and the anchor records "
                            f"{anchored_count}: the chain is shorter than it was")
        else:
            notes.append(f"matches the anchor written {anchor.get('created_at', 'at an unknown time')} "
                         f"({anchored_hash[:12]}..., {anchored_count} rows)")
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


def main() -> int:
    parser = argparse.ArgumentParser(description="Audit sink CLI")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("migrate").set_defaults(func=cmd_migrate)

    record = sub.add_parser("record")
    record.add_argument("--type", required=True,
                        choices=["model_call", "tool_call", "approval", "access",
                                 "config_change", "login"])
    record.add_argument("--actor", required=True)
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
                                          "is stored only if the content mode allows it")
    record.add_argument("--completion", help="the completion as returned, under the same rule")
    record.add_argument("--store-payload", action="store_true",
                        help="keep the payload itself, not just its hash. A policy decision.")
    record.set_defaults(func=cmd_record)

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

    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
