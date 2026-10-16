#!/usr/bin/env python3
"""The C5-C acceptance run: ten register rows, one line each, no database and no Microsoft tenant.

    python3 tests/fixtures/intake_acceptance.py

**What this is for.** The rows in `docs/requirements.yaml` are the specification and each one names a test that
must exist and must be able to fail. Those tests are in `tests/test_intake_*.py`, where they run under pytest
with the rest of the suite. This script is the same properties, in one run, printout and exit code, so a
deployment can be shown passing in a single command and so the runbook can paste the result.

**Its one limitation is stated at the top of its own output.** There is no Microsoft tenant here. The tenant
is `tests/fixtures/intake_env.py`, which implements the delegated-permission semantics from Microsoft's
documentation: `/me` answers and everything else is refused. That models the contract faithfully and it is not
the contract. A real tenant is the deployment step, and the runbook says which rows are covered by it.
"""
from __future__ import annotations

import contextlib
import hashlib
import io
import os
import shutil
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tests" / "fixtures"))
sys.path.insert(0, str(ROOT / "services" / "intake"))
sys.path.insert(0, str(ROOT / "services" / "ingestion"))

import intake_env  # noqa: E402

env = intake_env
config_module = intake_env.load("config")
content = intake_env.load("content")
folder = intake_env.load("folder")
graph = intake_env.load("graph")
intake = intake_env.load("intake")
monitor = intake_env.load("monitor")
net = intake_env.load("net")
provenance = intake_env.load("provenance")
reach = intake_env.load("reach")
secretstore = intake_env.load("secretstore")
sender = intake_env.load("sender")

INTAKE_DIR = ROOT / "services" / "intake"

results: list[tuple[str, bool, str]] = []


def row(name: str, ok: bool, evidence: str) -> None:
    results.append((name, ok, evidence))


def quietly(function, *args, **kwargs):
    """Run a command-line entry point with its output swallowed.

    The fetch command prints its summary as JSON, which is right for a timer and wrong in the middle of a
    report. Swallowing it here rather than adding a quiet flag keeps the CLI's interface for operators only.
    """
    with contextlib.redirect_stdout(io.StringIO()):
        return function(*args, **kwargs)


def ingest_hasher():
    try:
        import ingest  # noqa: PLC0415
        return ingest.sha256_of
    except ImportError:
        return None


def setup(root: Path, server, *, transport: bool = True) -> tuple[Path, object, object]:
    """One client environment. `transport=False` leaves whichever transport the caller installed in place.

    That parameter exists because the first version of this probe called `install` here and then recorded
    nothing: the cord check needs the transport it wrapped, and this helper quietly replaced it.
    """
    if transport:
        env.install(server)
    config_path = env.environment(root, server)
    env.write_credential(config_path)
    config = config_module.load(config_path)
    assert not config.problems, config.problems
    return config_path, config, provenance.Store(config.state_dir)


# --------------------------------------------------------------------------- the ten rows

def check_owned(root: Path) -> None:
    server = env.FakeGraph(messages=env.sample_messages()).start()
    try:
        asked = env.install_recording(server)
        config_path, config, store = setup(root, server, transport=False)
        intake.poll_once(config, store)

        owners = {item["owner"] for item in config_module.ownership(config)}
        hosts = {url.split("/")[2] for url in asked}
        refused = False
        try:
            net.request("https://intake.officeinference.com/v1.0/me/messages")
        except net.HostRefused:
            refused = True

        ok = owners == {"client"} and hosts == {"graph.microsoft.com", "login.microsoftonline.com"} \
            and refused and bool(asked)
        row("CON-OWNED-001", ok,
            f"five components owned by {{{','.join(sorted(owners))}}}; "
            f"{len(asked)} request(s) to {','.join(sorted(hosts))}; a host we control refused before a socket")
    finally:
        server.stop()
        net.set_transport(None)


def check_least(root: Path) -> None:
    server = env.FakeGraph(messages=env.sample_messages()).start()
    try:
        _, config, _ = setup(root, server)
        token = graph.acquire_token(config, secretstore.load(config.secret_path))
        report = reach.enumerate_reach(config, token)
        ok = report["ok"] and report["reached"] == ["own_mailbox"]
        row("CON-LEAST-001", ok,
            f"{len(report['probes'])} probe(s), reached: {','.join(report['reached']) or 'none'}; "
            f"granted scopes {','.join(report['granted_scopes'])}")
    finally:
        server.stop()
        net.set_transport(None)


def check_intake(root: Path) -> None:
    server = env.FakeGraph(messages=env.sample_messages()).start()
    try:
        _, config, store = setup(root, server)
        intake.poll_once(config, store)

        hasher = ingest_hasher()
        rows = [r for r in store.entries() if r["status"] == "deposited"]
        named = all(r["sender_from"] and r["message_id"] and r["received_at"] and len(r["content_sha256"]) == 64
                    for r in rows)
        equal = 0
        for r in rows:
            landed = config.documents_root / r["relpath"]
            if hasher is not None:
                digest, size = hasher(landed)
                if digest == r["content_sha256"] and size == r["bytes"]:
                    equal += 1
            elif provenance.sha256_file(landed)[0] == r["content_sha256"]:
                equal += 1
        ok = bool(rows) and named and equal == len(rows)
        how = "the ingestion worker's own sha256_of" if hasher else "provenance.sha256_file"
        row("CON-INTAKE-001", ok,
            f"{len(rows)} row(s) with sender, message id, arrival time and a 64-character hash; "
            f"{equal} verified equal to {how} of the deposited file")
    finally:
        server.stop()
        net.set_transport(None)


def check_dupe(root: Path) -> None:
    server = env.FakeGraph(messages=env.sample_messages(include_duplicate=True)).start()
    try:
        _, config, store = setup(root, server)
        summary = intake.poll_once(config, store)
        same = [r for r in store.entries()
                if r["content_sha256"] == provenance.sha256_bytes(env.invoice_pdf())]
        duplicates = [r for r in same if r["status"] == "duplicate"]
        originals = [r for r in same if r["status"] == "deposited"]
        on_disk = list(config.documents_root.rglob("INV-2026-0041.pdf"))
        notices = {notice["intake_id"] for notice in store.notices()}
        ok = (len(same) == 2 and len(duplicates) == 1 and len(originals) == 1
              and duplicates[0]["duplicate_of"] == originals[0]["intake_id"]
              and len(on_disk) == 1 and duplicates[0]["intake_id"] not in notices)
        row("CON-DUPE-001", ok,
            f"the same bytes twice: {len(originals)} deposited, {len(duplicates)} recorded as a duplicate "
            f"pointing at {duplicates[0]['duplicate_of'] if duplicates else 'nothing'}; "
            f"{len(on_disk)} copy on disk, no second job notice")
    finally:
        server.stop()
        net.set_transport(None)


def check_inject(root: Path) -> None:
    server = env.FakeGraph(messages=env.sample_messages()).start()
    try:
        _, config, store = setup(root, server)
        intake.poll_once(config, store)
        attacked = next(r for r in store.entries() if r["message_id"] == "<urgent@attacker.example>")
        names = {flag["name"] for flag in attacked["content_flags"]}
        writes = [c for c in server.calls
                  if c["method"] != "GET" and not c["path"].endswith("/oauth2/v2.0/token")]
        actions = {tuple(notice["requested_actions"]) for notice in store.notices()}
        stored = str(attacked)
        ok = ("ignore_previous" in names and "exfiltrate_document" in names and writes == []
              and actions in ({()}, set()) and "Ignore all previous instructions" not in stored)
        row("CON-INJECT-001", ok,
            f"signals {','.join(sorted(names))}; {len(writes)} write request(s) to the mail API; "
            f"requested actions {actions or '{()}'}; the instruction text is not in the row")
    finally:
        server.stop()
        net.set_transport(None)


def check_sender(root: Path) -> None:
    server = env.FakeGraph(messages=env.sample_messages()).start()
    try:
        _, config, store = setup(root, server)
        intake.poll_once(config, store)
        by_id = {r["message_id"]: r for r in store.entries()}
        known = by_id["<inv-41@supplier.example>"]
        unknown = by_id["<payme@nowhere.example>"]
        forged = by_id["<fwd-99@mailer.example>"]
        ok = (known["verdict"] == "trusted" and unknown["verdict"] == "marked"
              and forged["verdict"] == "marked"
              and forged["sender_envelope"] != forged["sender_from"]
              and all(r["auth_dmarc"] for r in (known, unknown, forged))
              and all(r["requires_review"] is True for r in store.entries()))
        row("CON-SENDER-001", ok,
            f"known supplier {known['verdict']}; unknown sender {unknown['verdict']}; "
            f"envelope {forged['sender_envelope']} vs header {forged['sender_from']} -> {forged['verdict']}; "
            f"DMARC recorded on all three; every row requires review")
    finally:
        server.stop()
        net.set_transport(None)


def check_folder(root: Path) -> None:
    server = env.FakeGraph()          # not started: the folder channel reaches nothing
    config_path = env.environment(root, server)
    config = config_module.load(config_path)
    share = Path(config.intake["share"])
    share.mkdir(parents=True, exist_ok=True)

    source = root / "scanner.pdf"
    source.write_bytes(env.invoice_pdf("1234.00", "ACC-1"))

    process = subprocess.Popen(
        [sys.executable, str(INTAKE_DIR / "intake.py"), "watch-folder",
         "--config", str(config_path), "--iterations", "60", "--poll-seconds", "1"],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    watchers = 0
    delivered = False
    try:
        time.sleep(1.2)
        subprocess.run([sys.executable, "-c",
                        "import shutil,sys; shutil.copy2(sys.argv[1], sys.argv[2])",
                        str(source), str(share / source.name)], check=True)
        deadline = time.time() + 25
        while time.time() < deadline:
            for entry in Path(f"/proc/{process.pid}/fd").iterdir() if Path(f"/proc/{process.pid}/fd").exists() else []:
                try:
                    watchers += 1 if "inotify" in os.readlink(entry) else 0
                except OSError:
                    pass
            if list(Path(config.documents_root).rglob("*.pdf")):
                delivered = True
                break
            time.sleep(0.2)
    finally:
        process.terminate()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()

    rows = provenance.Store(config.state_dir).entries()
    ok = delivered and watchers == 0 and bool(rows) and all(r["channel"] == "folder" for r in rows)
    row("CON-FOLDER-001", ok,
        f"a second process copied a file into the share; the connector took it without restarting "
        f"({len(rows)} row); {watchers} inotify descriptor(s) held while it ran")


def check_silent(root: Path) -> None:
    """Three failures, and the two that produce no error at all are the ones that matter."""
    findings = []
    ok = True

    # 1. A revoked scope: the grant works, the read succeeds, the folder is empty.
    server = env.FakeGraph(messages=env.sample_messages()).start()
    try:
        server.empty_folder = True
        server.scopes = ""
        config_path, config, store = setup(root / "a", server)
        _seed(store, config, count=8, days_ago=5)
        quietly(intake.main, ["fetch", "--config", str(config_path)])
        heartbeat = monitor.read_heartbeat(config.state_dir)
        alerts = {a.kind for a in monitor.evaluate(config, store)}
        findings.append(f"revoked scope: no error logged ({heartbeat['last_error'] is None}), "
                        f"alerts {','.join(sorted(alerts)) or 'none'}")
        ok = ok and heartbeat["last_error"] is None and "volume_floor" in alerts
    finally:
        server.stop()
        net.set_transport(None)

    # 2. A pulled licence: a 403 with a healthy token.
    server = env.FakeGraph(messages=env.sample_messages()).start()
    try:
        server.mailbox_status = 403
        config_path, config, store = setup(root / "b", server)
        _seed(store, config, count=7, days_ago=3)
        code = quietly(intake.main, ["fetch", "--config", str(config_path)])
        alerts = {a.kind for a in monitor.evaluate(config, store)}
        heartbeat = monitor.read_heartbeat(config.state_dir)
        findings.append(f"pulled licence: exit {code}, kind {heartbeat['last_error']['kind']}, "
                        f"alerts {','.join(sorted(alerts))}")
        ok = ok and code == 2 and "mailbox_not_readable" in alerts and "volume_floor" in alerts
    finally:
        server.stop()
        net.set_transport(None)

    # 3. A mail rule diverting invoices: everything succeeds and nothing arrives.
    server = env.FakeGraph(messages=env.sample_messages()).start()
    try:
        server.empty_folder = True
        config_path, config, store = setup(root / "c", server)
        _seed(store, config, count=9, days_ago=4)
        code = quietly(intake.main, ["fetch", "--config", str(config_path)])
        heartbeat = monitor.read_heartbeat(config.state_dir)
        alerts = {a.kind for a in monitor.evaluate(config, store)}
        findings.append(f"diverted mail: exit {code}, no error logged "
                        f"({heartbeat['last_error'] is None}), alerts {','.join(sorted(alerts))}")
        ok = ok and code == 0 and heartbeat["last_error"] is None and "volume_floor" in alerts
    finally:
        server.stop()
        net.set_transport(None)

    row("CON-SILENT-001", ok, "; ".join(findings))


def _seed(store, config, *, count: int, days_ago: float) -> None:
    when = datetime.now(timezone.utc) - timedelta(days=days_ago)
    for index in range(count):
        digest = hashlib.sha256(f"{days_ago}-{index}".encode()).hexdigest()
        store.append(provenance.build_record(
            deployment_id=config.deployment_id, mailbox=config.mailbox.get("address"),
            folder=config.mailbox.get("folder"), channel="graph",
            message_id=f"<seed-{days_ago}-{index}@supplier.example>", provider_id=None,
            received_at=(when + timedelta(minutes=index)).isoformat(),
            observed_at=(when + timedelta(minutes=index)).isoformat(),
            sender={"header_from": "billing@supplier.example", "envelope_from": "bounce@supplier.example",
                    "reply_to": None, "auth": {"spf": "pass", "dkim": "pass", "dmarc": "pass"},
                    "allowlisted": True, "aligned": True, "verdict": "trusted", "reasons": []},
            content_scan={"signals": []}, raw_message_sha256=digest, deposit_row=None, duplicate_of=None,
            credential={}, filename="invoice.pdf", media_type="application/pdf",
            content_sha256=digest, bytes_=1200))


def check_credential(root: Path) -> None:
    """No key in the repository, no key the application can read, and a rotation that needs no redeploy."""
    hits = []
    for path in sorted(INTAKE_DIR.rglob("*")):
        if path.is_file() and "-----BEGIN" in path.read_text(errors="replace"):
            hits.append(str(path.relative_to(ROOT)))

    server = env.FakeGraph(messages=env.sample_messages()).start()
    try:
        _, config, store = setup(root, server)
        intake.poll_once(config, store)
        first = server.token_requests[-1]["client_secret"]

        rotated = secretstore.load(config.secret_path)
        secretstore.write(config.secret_path, {
            "client_id": rotated.client_id, "tenant_id": rotated.tenant_id,
            "refresh_token": "refresh-token-rotated", "client_secret": "the-new-secret"})
        intake.poll_once(config, store)
        second = server.token_requests[-1]["client_secret"]

        journal = store.journal.read_text()
        leaked = "the-new-secret" in journal or "not-a-real-secret" in journal

        weak = config.secret_path + ".weak"
        shutil.copy(config.secret_path, weak)
        os.chmod(weak, 0o644)
        refused = False
        try:
            secretstore.load(weak)
        except secretstore.CredentialRefused:
            refused = True
        os.remove(weak)

        api = __import__("yaml").safe_load(
            (ROOT / "deploy" / "office" / "compose" / "docker-compose.yml").read_text())["services"]["api"]
        api_env = " ".join(api.get("environment") or {})
        api_ok = "credential" not in api_env.lower() and "INTAKE" not in api_env
    finally:
        server.stop()
        net.set_transport(None)

    ok = hits == [] and first == "not-a-real-secret" and second == "the-new-secret" \
        and not leaked and refused and api_ok
    row("SEC-CON-001", ok,
        f"{len(hits)} key(s) in the service; a rotation took effect on the next poll with no restart "
        f"({first} -> {second}); the value is not in the record; mode 0644 refused; "
        f"the api container declares no access to the store")


def check_scope(root: Path) -> None:
    server = env.FakeGraph(messages=env.sample_messages()).start()
    try:
        _, config, _ = setup(root, server)
        token = graph.acquire_token(config, secretstore.load(config.secret_path))
        report = reach.enumerate_reach(config, token)
        blunt = env.FakeGraph(messages=env.sample_messages())
        blunt.application_permission = True
        blunt.start()
        try:
            env.install(blunt)
            config_path = env.environment(root / "wide", blunt)
            env.write_credential(config_path)
            wide_config = config_module.load(config_path)
            wide = reach.enumerate_reach(wide_config, graph.acquire_token(
                wide_config, secretstore.load(wide_config.secret_path)))
        finally:
            blunt.stop()

        refused_write = False
        try:
            net.request("https://graph.microsoft.com/v1.0/me/sendMail", method="POST")
        except net.MethodRefused:
            refused_write = True

        ok = (report["ok"] and report["reached"] == ["own_mailbox"]
              and not wide["ok"] and "second_mailbox" in wide["reached"] and refused_write)
        row("SEC-INTAKE-002", ok,
            f"a second mailbox, the directory, the profile and another service refused; a write method "
            f"refused in code; and the enumeration caught a tenant-wide credential "
            f"(reached {','.join(wide['reached'])})")
    finally:
        server.stop()
        net.set_transport(None)


# --------------------------------------------------------------------------- the run

def main() -> int:
    print("C5-C intake acceptance")
    print(f"  started {datetime.now(timezone.utc).isoformat(timespec='seconds')}")
    print("  **the tenant is simulated.** tests/fixtures/intake_env.py implements the delegated-permission")
    print("  semantics from Microsoft's documentation. No Microsoft tenant is contacted, so the rows below")
    print("  are proved against the contract rather than against the service. See the runbook for which")
    print("  part of each row needs a real tenant.")
    print()

    with tempfile.TemporaryDirectory() as workspace:
        root = Path(workspace)
        # **One directory per check.** The first version shared one tree, so the folder check read the rows
        # four earlier checks had written and reported a graph arrival as its own evidence.
        for check in (check_owned, check_least, check_intake, check_dupe, check_inject, check_sender,
                      check_folder, check_silent, check_credential, check_scope):
            workspace = root / check.__name__
            workspace.mkdir(parents=True, exist_ok=True)
            try:
                check(workspace)
            except Exception as exc:  # noqa: BLE001 - a check that raises is a failure with a reason
                row(check.__name__, False, f"raised {type(exc).__name__}: {exc}")

    width = max(len(name) for name, _, _ in results)
    for name, ok, evidence in results:
        print(f"{'PASS' if ok else 'FAIL'}  {name:<{width}}  {evidence}")

    failed = [name for name, ok, _ in results if not ok]
    print()
    print(f"{len(results) - len(failed)} PASS, {len(failed)} FAIL"
          + (f"  ({', '.join(failed)})" if failed else ""))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
