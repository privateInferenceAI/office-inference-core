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
    payload = None
    if args.payload:
        raw = sys.stdin.read() if args.payload == "-" else args.payload
        if args.payload.startswith("@"):
            raw = Path(args.payload[1:]).read_text()
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError as exc:
            die(f"--payload is not valid JSON: {exc}")

    payload_sha = sha256_hex(canonical_json(payload if payload is not None else {}))
    approval = json.loads(args.approval) if args.approval else None

    with connect() as conn, conn.cursor() as cur:
        cur.execute("""
            insert into audit_events
              (event_type, actor, actor_role, deployment_id, data_class, outcome,
               tool, model, request_id, approval, payload_sha256, payload)
            values (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
            returning id, ts_iso, row_hash""",
            (args.type, args.actor, args.actor_role, args.deployment_id, args.data_class,
             args.outcome, args.tool, args.model, args.request_id,
             Jsonb(approval) if approval is not None else None, payload_sha,
             Jsonb(payload) if (payload is not None and args.store_payload) else None))
        row = cur.fetchone()
    print(json.dumps({"id": row[0], "ts_iso": row[1], "row_hash": row[2],
                      "payload_sha256": payload_sha}, indent=2))
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

def cmd_backfill(args) -> int:
    """Copy the gateway's model calls into the audit record, once each."""
    with connect() as conn, conn.cursor() as cur:
        cur.execute("""
            select s.request_id, s.model, s.total_tokens, s.prompt_tokens, s.completion_tokens,
                   s.spend, s.custom_llm_provider, s."startTime", s.api_key
            from "LiteLLM_SpendLogs" s
            where s.request_id is not null
              and not exists (select 1 from audit_events a
                              where a.event_type = 'model_call' and a.request_id = s.request_id)
            order by s."startTime"
            limit %s""", (args.limit,))
        rows = cur.fetchall()

        inserted = 0
        for (request_id, model, total, prompt_t, completion_t, spend, provider,
             started, api_key) in rows:
            payload = {"model": model, "total_tokens": total, "prompt_tokens": prompt_t,
                       "completion_tokens": completion_t, "spend_usd": float(spend or 0),
                       "provider": provider}
            payload_sha = sha256_hex(canonical_json(payload))
            actor = "gateway-key:" + hashlib.sha256((api_key or "unknown").encode()).hexdigest()[:8]
            cur.execute("""
                insert into audit_events
                  (occurred_at, event_type, actor, actor_role, deployment_id, data_class,
                   outcome, model, request_id, payload_sha256, payload)
                values (%s,'model_call',%s,'gateway',%s,'internal','ok',%s,%s,%s,%s)""",
                (started, actor, DEPLOYMENT_ID, model, request_id, payload_sha, Jsonb(payload)))
            inserted += 1
    print(f"backfilled {inserted} model call(s); {len(rows) - inserted} skipped")
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

def cmd_verify(args) -> int:
    path = Path(args.file)
    lines = [json.loads(line) for line in path.read_text().splitlines() if line.strip()]
    problems: list[str] = []
    previous_hash = None

    for index, row in enumerate(lines, start=1):
        if row.get("prev_hash") != previous_hash:
            problems.append(f"line {index}: prev_hash does not match the previous row")
        if sha256_hex(row_canonical(row)) != row["row_hash"]:
            problems.append(f"line {index}: row_hash does not match the row contents")
        previous_hash = row["row_hash"]

    manifest_path = path.parent / "manifest.json"
    if manifest_path.exists():
        manifest = json.loads(manifest_path.read_text())
        if manifest.get("file_sha256") != sha256_hex(path.read_text()):
            problems.append("the file hash does not match the manifest")
        if manifest.get("last_row_hash") != previous_hash:
            problems.append("the final row hash does not match the manifest")
        if manifest.get("event_count") != len(lines):
            problems.append("the event count does not match the manifest")

    if problems:
        print(f"FAIL {path}")
        for problem in problems:
            print(f"  - {problem}")
        return 1
    print(f"ok   {path}: {len(lines)} event(s), chain intact")
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
    record.add_argument("--store-payload", action="store_true",
                        help="keep the payload itself, not just its hash. A policy decision.")
    record.set_defaults(func=cmd_record)

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
    verify.set_defaults(func=cmd_verify)

    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
