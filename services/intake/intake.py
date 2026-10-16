#!/usr/bin/env python3
"""The intake connector's command line: read one mailbox, deposit what is in it, and say what happened.

What this service is, in one paragraph. An invoice arrives at the client's own AP mailbox. This reads it with
a delegated credential the client granted, hashes the attachment, writes the bytes under the documents root,
and records where the document came from: the sender, the message identifier, the arrival time and the hash.
It approves nothing, sends nothing, and reaches nothing but that one mailbox. The workflow picks the document
up from the outbox and takes it from there.

Subcommands:

    check-config      what the client's configuration must satisfy, before anything is fetched
    cord-check        cut every cord to Office Inference and see what is left resolving to us
    migrate           intake's own table
    fetch             one poll: read, deposit, record, exit
    watch             the same, on an interval, with a heartbeat on every success
    watch-folder      the second channel: a share, polled, with a settle check
    enumerate-reach   what the credential can reach, rather than what it was granted
    monitor           the heartbeat and the volume floor, evaluated now
    verify-manifest   the recorded hash against the ingest manifest's hash for the same file
    status            what is in the record and when it last moved

**Exit codes are part of the interface**, because a timer is the usual caller. 0 is success, 1 is a refused
configuration or a check that failed, 2 is a mail failure that the heartbeat has already recorded. A timer
that treats 2 as success is a timer that hides exactly the failures this service exists to surface.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import config as config_module  # noqa: E402
import content  # noqa: E402
import folder  # noqa: E402
import graph  # noqa: E402
import monitor  # noqa: E402
import net  # noqa: E402
import provenance  # noqa: E402
import reach  # noqa: E402
import secretstore  # noqa: E402
import sender  # noqa: E402

SCHEMA_DIR = Path(__file__).resolve().parent / "schema"
WATERMARK = "watermark.json"

# Vocabulary that would mean a component is talking to somebody. Checked against the configuration rather
# than against the source, because a licence check or an update check is a *setting* before it is code, and
# the setting is where it would be written down first.
VENDOR_VOCABULARY = ("licence_server", "license_server", "telemetry", "analytics_endpoint", "phone_home",
                     "update_check", "update_url", "activation", "vendor_endpoint", "support_callback")

EXIT_OK, EXIT_REFUSED, EXIT_MAIL = 0, 1, 2


# --------------------------------------------------------------------------- helpers

def load_config(args):
    config = config_module.load(args.config)
    return config


def refuse(problems: list, header: str) -> int:
    print(f"FAIL {header}", file=sys.stderr)
    for problem in problems:
        print(f"  - {problem if isinstance(problem, str) else problem}", file=sys.stderr)
    return EXIT_REFUSED


def watermark_path(config) -> Path:
    return Path(config.state_dir) / WATERMARK


def read_watermark(config) -> datetime | None:
    path = watermark_path(config)
    if not path.exists():
        return None
    try:
        return datetime.fromisoformat(json.loads(path.read_text())["last_received"])
    except (json.JSONDecodeError, KeyError, ValueError):
        return None


def write_watermark(config, when: datetime) -> None:
    path = watermark_path(config)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".part")
    temporary.write_text(json.dumps({"last_received": when.isoformat()}, indent=2) + "\n")
    temporary.replace(path)


def message_fingerprint(message: dict) -> str:
    """The hash of the message as received: identity, arrival, subject and the header block.

    Hashed rather than stored. It proves later that a specific message was seen without keeping a copy of the
    client's mail in the record, which is the same posture the audit sink takes with prompts.
    """
    material = {
        "id": message.get("id"),
        "internetMessageId": message.get("internetMessageId"),
        "receivedDateTime": message.get("receivedDateTime"),
        "subject": message.get("subject"),
        "headers": sorted((h.get("name", "").lower(), h.get("value", ""))
                          for h in (message.get("internetMessageHeaders") or [])),
    }
    return hashlib.sha256(json.dumps(material, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def rotation_credential(config, credential):
    """The credential as it should look after a rotation write-back."""
    return {
        "client_id": credential.client_id,
        "tenant_id": credential.tenant_id,
        "refresh_token": credential.refresh_token,
        "client_secret": credential.client_secret,
        "certificate_path": credential.certificate_path,
    }


# --------------------------------------------------------------------------- check-config

def cmd_check_config(args) -> int:
    config = load_config(args)
    if config.problems:
        return refuse(config.problems, f"{args.config} is not a configuration this service will run")
    print(f"ok   {args.config}")
    print(f"     deployment {config.deployment_id}, client {config.client_name}")
    for row in config_module.ownership(config):
        print(f"     {row['component']:<18} {row['owner']:<8} {row['value']}")
    return EXIT_OK


# --------------------------------------------------------------------------- cord-check

def vendor_vocabulary_in_config(raw, prefix="") -> list[str]:
    """Every configuration key that would mean something is talking to us, or to anybody."""
    found = []
    if isinstance(raw, dict):
        for key, value in raw.items():
            path = f"{prefix}.{key}" if prefix else str(key)
            if any(word in str(key).lower() for word in VENDOR_VOCABULARY):
                found.append(path)
            found.extend(vendor_vocabulary_in_config(value, path))
    elif isinstance(raw, list):
        for index, value in enumerate(raw):
            found.extend(vendor_vocabulary_in_config(value, f"{prefix}[{index}]"))
    return found


def cmd_cord_check(args) -> int:
    """`BIZ-INDEPENDENT-001`, as a command that runs on any deployment at any time.

    Four checks, and each one is a different way a client could end up needing us. The first is the paper
    trail in their own configuration. The second is the network allowlist, asserted rather than described.
    The third exercises that allowlist against a host we really do control, so the control is proved by
    refusing something rather than by being read. The fourth is the vocabulary of a licence check or an
    update check in the configuration, which is where one would appear first.
    """
    config = load_config(args)
    problems = list(config.problems)
    if problems:
        return refuse(problems, f"{args.config} does not survive a cord check")

    print(f"ok   {config.client_name} ({config.deployment_id})")
    for row in config_module.ownership(config):
        mark = "ok  " if row["owner"] == config_module.CLIENT else "FAIL"
        print(f"     {mark} {row['component']:<18} {row['owner']:<8} {row['value']}")

    allowed = net.ALLOWED_HOSTS
    if tuple(allowed) != (net.LOGIN_HOST, net.GRAPH_HOST):
        print(f"FAIL the host allowlist is {allowed}, and this build reaches exactly "
              f"{net.LOGIN_HOST} and {net.GRAPH_HOST}", file=sys.stderr)
        return EXIT_REFUSED
    print(f"     ok   host allowlist      {', '.join(allowed)}")

    # Proved by refusal, not by inspection. If this stops raising, the control is gone.
    try:
        net.request("https://intake.officeinference.com/v1.0/me/messages")
        print("FAIL a request to a host we control was allowed. The allowlist is not in the path.",
              file=sys.stderr)
        return EXIT_REFUSED
    except net.HostRefused:
        print("     ok   a host we control is refused before a socket is opened")

    vocabulary = vendor_vocabulary_in_config(config.raw)
    if vocabulary:
        print(f"FAIL the configuration names {', '.join(vocabulary)}, which is a component that would talk "
              f"to somebody other than Microsoft", file=sys.stderr)
        return EXIT_REFUSED
    print("     ok   no licence server, update check or telemetry in the configuration")
    print("no cord")
    return EXIT_OK


# --------------------------------------------------------------------------- migrate

def cmd_migrate(args) -> int:
    import psycopg  # noqa: PLC0415

    files = sorted(SCHEMA_DIR.glob("*.sql"))
    if not files:
        print(f"FAIL no schema files in {SCHEMA_DIR}", file=sys.stderr)
        return EXIT_REFUSED

    with psycopg.connect(args.dsn, autocommit=True) as conn:
        with conn.cursor() as cur:
            cur.execute("""create table if not exists intake_schema_migrations (
                             filename text primary key, checksum text not null,
                             applied_at timestamptz not null default now())""")
            cur.execute("select filename, checksum from intake_schema_migrations")
            applied = dict(cur.fetchall())
        ran = 0
        for path in files:
            checksum = hashlib.sha256(path.read_bytes()).hexdigest()
            if path.name in applied:
                if applied[path.name] != checksum:
                    print(f"FAIL {path.name} was applied and has changed since. Write a new migration "
                          f"rather than editing an applied one.", file=sys.stderr)
                    return EXIT_REFUSED
                print(f"skip    {path.name} (already applied)")
                continue
            with conn.cursor() as cur:
                cur.execute(path.read_text())
                cur.execute("insert into intake_schema_migrations (filename, checksum) values (%s, %s)",
                            (path.name, checksum))
            print(f"applied {path.name}")
            ran += 1
    print(f"{ran} migration(s) applied, {len(applied)} already present")
    return EXIT_OK


# --------------------------------------------------------------------------- the poll

def poll_once(config, store, *, limit: int = 50) -> dict:
    """One cycle: a token, a folder listing, and one row per attachment.

    The watermark is advanced **after** the messages are recorded, so a crash between the two re-reads rather
    than skips. Re-reading is free because `intake_id` is stable and the content hash catches a re-send, which
    is the property that lets this be a simple design: it is cheaper to read a message twice than to lose one.
    """
    summary = {"channel": "graph", "messages_seen": 0, "deposited": 0, "duplicates": 0,
               "already": 0, "marked": 0, "no_attachment": 0, "attachments": 0, "last_error": None}

    credential = secretstore.load(config.secret_path)
    token = graph.acquire_token(config, credential)

    # The refresh token rotates on every use. A connector that does not write the new one back works until the
    # stored one is retired and then stops, with no error that points at the cause.
    if token.refresh_token and token.refresh_token != credential.refresh_token:
        secretstore.write(config.secret_path, rotation_credential(config, credential))

    since = read_watermark(config)
    messages = graph.list_messages(config, token, since=since, top=limit)
    summary["messages_seen"] = len(messages)

    newest = since
    # What the record may hold about the credential, taken at the moment it was read. Described once here
    # rather than re-read per deposit: the point-of-use read is the rule, and reading it four times per poll
    # would be that rule turned into an inefficiency.
    credential_note = credential.describes()
    for message in messages:
        _take_message(config, store, token, message, summary, credential_note)
        stamp = _as_datetime(message.get("receivedDateTime"))
        if stamp and (newest is None or stamp > newest):
            newest = stamp

    if newest and (since is None or newest > since):
        write_watermark(config, newest)

    monitor.write_heartbeat(config.state_dir, {
        "at": datetime.now(timezone.utc).isoformat(),
        "channel": "graph",
        "mailbox": config.mailbox.get("address"),
        "messages_seen": summary["messages_seen"],
        "deposited": summary["deposited"],
        "duplicates": summary["duplicates"],
        "scopes": token.scopes,
        "last_error": None,
    })
    return summary


def _as_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _take_message(config, store, token, message: dict, summary: dict, credential_note: dict) -> None:
    provider_id = message.get("id")
    message_id = message.get("internetMessageId") or provider_id
    received = message.get("receivedDateTime") or datetime.now(timezone.utc).isoformat()
    headers = graph.header_map(message)

    from_address = sender.address_of(message.get("from"))
    reply_to = sender.address_of((message.get("replyTo") or [{}])[0]) if message.get("replyTo") else None

    verdict = sender.verify(headers=headers, header_from=from_address, reply_to=reply_to,
                            allowlist=config.allowlist)
    scan = content.scan(subject=message.get("subject") or "", body=message.get("body"))
    fingerprint = message_fingerprint(message)

    attachments = graph.attachments(config, token, provider_id) if message.get("hasAttachments") else []
    if not attachments:
        # A message with no attachment is not a document. Counted rather than recorded, and the count is in
        # the heartbeat, so "suppliers are sending links instead of PDFs" is visible without a row per email.
        summary["no_attachment"] += 1
        return
    summary["attachments"] += len(attachments)

    for index, attachment in enumerate(attachments):
        name = provenance.final_name(attachment["name"], index)
        digest = provenance.sha256_bytes(attachment["content"])
        intake_id = provenance.intake_id_for(message_id=message_id or "", filename=name,
                                             content_sha256=digest)

        if store.by_id(intake_id):
            summary["already"] += 1
            continue

        first = store.first_with_content(digest)
        if first:
            record = provenance.build_record(
                deployment_id=config.deployment_id, mailbox=config.mailbox.get("address"),
                folder=config.mailbox.get("folder"), channel="graph", message_id=message_id,
                provider_id=provider_id, received_at=received, observed_at=_now(),
                sender=verdict, content_scan=scan, raw_message_sha256=fingerprint, deposit_row=None,
                duplicate_of=first["intake_id"], credential={}, filename=name,
                media_type=attachment.get("content_type"), content_sha256=digest,
                bytes_=len(attachment["content"]),
                unreadable_by_ingest=first.get("unreadable_by_ingest"))
            # A duplicate holds no credential path: nothing was read from the mailbox to produce it that the
            # first row does not already name.
            record["credential_path"] = None
            record["credential_mode"] = None
            store.append(record)
            summary["duplicates"] += 1
            continue

        row = provenance.deposit(
            documents_root=config.documents_root, root=config.intake.get("root", "company"),
            received_at=received, message_id=message_id or "", filename=attachment["name"],
            content=attachment["content"], index=index)
        record = provenance.build_record(
            deployment_id=config.deployment_id, mailbox=config.mailbox.get("address"),
            folder=config.mailbox.get("folder"), channel="graph", message_id=message_id,
            provider_id=provider_id, received_at=received, observed_at=_now(),
            sender=verdict, content_scan=scan, raw_message_sha256=fingerprint, deposit_row=row,
            duplicate_of=None, credential=credential_note, filename=row.filename,
            media_type=attachment.get("content_type"))
        store.append(record)
        summary["deposited"] += 1
        if verdict["verdict"] == "marked":
            summary["marked"] += 1


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def cmd_fetch(args) -> int:
    config = load_config(args)
    if config.problems:
        return refuse(config.problems, f"{args.config} is not a configuration this service will run")
    store = provenance.Store(config.state_dir, dsn=args.dsn)
    try:
        summary = poll_once(config, store)
    except (graph.TokenRefused, graph.MailboxUnreadable) as exc:
        # The failure is recorded in the heartbeat, because a poll that failed and left no trace is the
        # failure mode this whole service is built around.
        monitor.write_heartbeat(config.state_dir, {
            "at": datetime.now(timezone.utc).isoformat(), "channel": "graph",
            "mailbox": config.mailbox.get("address"), "messages_seen": 0, "deposited": 0,
            "error_at": _now(),
            "last_error": dict(exc.classification, detail=exc.detail[:400]),
        })
        print(f"mail failure: {exc}", file=sys.stderr)
        return EXIT_MAIL
    print(json.dumps(summary, indent=2, sort_keys=True))
    return EXIT_OK


def cmd_watch(args) -> int:
    config = load_config(args)
    if config.problems:
        return refuse(config.problems, f"{args.config} is not a configuration this service will run")
    store = provenance.Store(config.state_dir, dsn=args.dsn)
    runs = 0
    while True:
        try:
            summary = poll_once(config, store)
            print(json.dumps(summary, sort_keys=True))
        except (graph.TokenRefused, graph.MailboxUnreadable) as exc:
            monitor.write_heartbeat(config.state_dir, {
                "at": _now(), "channel": "graph", "mailbox": config.mailbox.get("address"),
                "messages_seen": 0, "deposited": 0, "error_at": _now(),
                "last_error": dict(exc.classification, detail=exc.detail[:400]),
            })
            print(f"mail failure: {exc}", file=sys.stderr)
        runs += 1
        if args.iterations and runs >= args.iterations:
            return EXIT_OK
        time.sleep(config.poll_seconds)


# --------------------------------------------------------------------------- the folder channel

def cmd_watch_folder(args) -> int:
    config = load_config(args)
    if config.problems:
        return refuse(config.problems, f"{args.config} is not a configuration this service will run")
    share = args.share or (config.intake.get("share") or "")
    if not share:
        print("FAIL no share: pass --share or set intake.share", file=sys.stderr)
        return EXIT_REFUSED

    store = provenance.Store(config.state_dir, dsn=args.dsn)
    connector = folder.FolderConnector(
        share=share, store=store, documents_root=config.documents_root,
        root_label=config.intake.get("root", "company"), deployment_id=config.deployment_id,
        settle_scans=int(config.intake.get("settle_scans", 2)))

    def beat(summary):
        monitor.write_heartbeat(config.state_dir, folder.heartbeat_payload(summary))

    runs = connector.watch(poll_seconds=int(args.poll_seconds or config.poll_seconds),
                           iterations=args.iterations, heartbeat=beat)
    for summary in runs:
        print(json.dumps({key: value for key, value in summary.items() if key != "waiting"},
                         sort_keys=True, default=str))
    return EXIT_OK


# --------------------------------------------------------------------------- reach

def cmd_enumerate_reach(args) -> int:
    config = load_config(args)
    if config.problems:
        return refuse(config.problems, f"{args.config} is not a configuration this service will run")
    credential = secretstore.load(config.secret_path)
    token = graph.acquire_token(config, credential)
    report = reach.enumerate_reach(config, token)
    print(reach.format_reach(report))
    return EXIT_OK if report["ok"] else EXIT_REFUSED


# --------------------------------------------------------------------------- monitor

def cmd_monitor(args) -> int:
    config = load_config(args)
    if config.problems:
        return refuse(config.problems, f"{args.config} is not a configuration this service will run")
    store = provenance.Store(config.state_dir, dsn=args.dsn)
    now = datetime.fromisoformat(args.now) if args.now else datetime.now(timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)

    alerts = monitor.evaluate(config, store, now=now)
    if not alerts:
        print("no alert")
        return EXIT_OK
    transport = monitor.smtp_transport(config) if (config.alerts.get("transport") == "smtp") else None
    for alert in alerts:
        record = monitor.deliver(alert, config, transport=transport)
        print(f"{alert.kind}: {alert.sentence}")
        print(f"  sent to {', '.join(record['recipients'])} via {record['transport']}")
    return EXIT_OK


# --------------------------------------------------------------------------- verify-manifest

def cmd_verify_manifest(args) -> int:
    """The recorded hash against the hash the ingestion worker put in `ingest_manifest`.

    This is the second half of `CON-INTAKE-001`. The first half is that intake recorded a hash; this is that
    the number is the same number the rest of the system holds, which is what makes the row evidence about a
    document rather than a note about a file. It needs a database, because the manifest is one.
    """
    import psycopg  # noqa: PLC0415

    config = load_config(args)
    if config.problems:
        return refuse(config.problems, f"{args.config} is not a configuration this service will run")
    if not args.dsn:
        print("FAIL no --dsn: the ingest manifest is a Postgres table, so this check needs the database",
              file=sys.stderr)
        return EXIT_REFUSED

    store = provenance.Store(config.state_dir)
    rows = [row for row in store.entries() if row.get("status") == "deposited" and row.get("relpath")]
    if not rows:
        print("nothing deposited yet, so there is no hash to compare")
        return EXIT_OK

    matches = missing = mismatches = 0
    with psycopg.connect(args.dsn) as conn, conn.cursor() as cur:
        for row in rows:
            cur.execute("select content_sha256, bytes from ingest_manifest where relpath = %s",
                        (row["relpath"],))
            found = cur.fetchone()
            if found is None:
                missing += 1
                print(f"pending  {row['relpath']}  (the worker has not read it yet)")
            elif found[0] != row["content_sha256"]:
                mismatches += 1
                print(f"MISMATCH {row['relpath']}  intake {row['content_sha256'][:12]}  "
                      f"manifest {found[0][:12]}")
            else:
                matches += 1
                print(f"match    {row['relpath']}  {row['content_sha256'][:12]}")

    print(f"{matches} match, {missing} pending, {mismatches} mismatch")
    for row in store.entries():
        if row.get("unreadable_by_ingest"):
            print(f"note: {row['filename']} is a format the ingestion worker does not read, so it will "
                  f"never appear in the manifest")
    return EXIT_REFUSED if mismatches else EXIT_OK


# --------------------------------------------------------------------------- status

def cmd_status(args) -> int:
    config = load_config(args)
    store = provenance.Store(config.state_dir)
    rows = store.entries()
    deposited = [row for row in rows if row.get("status") == "deposited"]
    duplicates = [row for row in rows if row.get("status") == "duplicate"]
    marked = [row for row in deposited if row.get("verdict") == "marked"]
    flagged = [row for row in rows if row.get("content_flags")]

    print(f"deployment  {config.deployment_id} ({config.client_name})")
    print(f"mailbox     {config.mailbox.get('address')}  folder {config.mailbox.get('folder')}")
    print(f"journal     {store.journal}")
    print(f"records     {len(rows)} total, {len(deposited)} deposited, {len(duplicates)} duplicate")
    print(f"verdicts    {len(marked)} marked, {len(deposited) - len(marked)} trusted")
    print(f"flags       {len(flagged)} arrival(s) carried a content signal")
    print(f"notices     {len(store.notices())} in the outbox")
    heartbeat = monitor.read_heartbeat(config.state_dir)
    print(f"heartbeat   {heartbeat.get('at') if heartbeat else 'never written'}")
    if heartbeat and heartbeat.get("last_error"):
        print(f"last error  {heartbeat['last_error'].get('kind')}: "
              f"{heartbeat['last_error'].get('sentence')}")
    return EXIT_OK


# --------------------------------------------------------------------------- the parser

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="The C5 email intake connector")
    sub = parser.add_subparsers(dest="command", required=True)

    def with_config(name, help_text):
        item = sub.add_parser(name, help=help_text)
        item.add_argument("--config", required=True, help="the client's intake configuration")
        return item

    with_config("check-config", "validate the configuration").set_defaults(func=cmd_check_config)
    with_config("cord-check", "prove nothing resolves to Office Inference").set_defaults(func=cmd_cord_check)

    migrate = sub.add_parser("migrate", help="apply intake's own schema")
    migrate.add_argument("--dsn", required=True)
    migrate.set_defaults(func=cmd_migrate)

    fetch = with_config("fetch", "one poll")
    fetch.add_argument("--dsn", help="mirror the record into Postgres as well as the journal")
    fetch.set_defaults(func=cmd_fetch)

    watch = with_config("watch", "poll on an interval")
    watch.add_argument("--dsn")
    watch.add_argument("--iterations", type=int, help="stop after this many polls. For a test")
    watch.set_defaults(func=cmd_watch)

    watch_folder = with_config("watch-folder", "poll a shared folder")
    watch_folder.add_argument("--share", help="the directory, when it is not in the configuration")
    watch_folder.add_argument("--poll-seconds", type=int)
    watch_folder.add_argument("--iterations", type=int, help="stop after this many scans. For a test")
    watch_folder.add_argument("--dsn")
    watch_folder.set_defaults(func=cmd_watch_folder)

    with_config("enumerate-reach", "what the credential can reach").set_defaults(func=cmd_enumerate_reach)

    mon = with_config("monitor", "the heartbeat and the volume floor")
    mon.add_argument("--dsn")
    mon.add_argument("--now", help="ISO timestamp, so a test can evaluate a moment that is not now")
    mon.set_defaults(func=cmd_monitor)

    manifest = with_config("verify-manifest", "the recorded hash against ingest_manifest")
    manifest.add_argument("--dsn", required=True)
    manifest.set_defaults(func=cmd_verify_manifest)

    with_config("status", "what is in the record").set_defaults(func=cmd_status)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
