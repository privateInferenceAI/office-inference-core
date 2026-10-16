"""The heartbeat, the volume floor, and where an alert goes. `CON-SILENT-001`.

**The failures this exists for produce no error.** A revoked scope, a pulled licence and a mail rule that
diverts invoices to a subfolder all leave a credential that authenticates and a queue that is empty. Error
logging reports nothing in any of the three, and the deployment looks like a quiet week. That is the trap the
requirement is written around, and every "we log exceptions" answer walks into it.

So the signal is the **absence of a success** rather than the presence of a failure:

* Every successful poll writes a heartbeat with a timestamp. A separate check alerts when the newest
  heartbeat is older than the poll interval allows. A connector that stopped without failing anything is
  caught by arithmetic rather than by an exception.
* Every arrival is counted. No invoice for longer than the quiet period is a fault rather than a slow month,
  and a sustained drop against the previous window is the second signal.

**Where the alert goes is part of the requirement.** Recipients come from the client's own configuration file,
and `config.validate` refuses one on a domain we control. An alert that routes to Office Inference quietly
reintroduces the dependency the whole design exists to remove, and it does it at the moment the client most
needs to be able to act without us.
"""
from __future__ import annotations

import json
import smtplib
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage
from pathlib import Path

# How long a heartbeat may go unwritten before it is a fault. Three poll intervals, floored at five minutes,
# because one missed poll is a timeout and three is a connector that is not running.
STALE_FLOOR_SECONDS = 300
STALE_POLL_MULTIPLIER = 3

HEARTBEAT = "heartbeat.json"


@dataclass
class Alert:
    kind: str
    sentence: str
    detail: dict = field(default_factory=dict)

    def as_record(self, recipients: list[str]) -> dict:
        return {"kind": self.kind, "sentence": self.sentence, "detail": self.detail,
                "recipients": list(recipients)}


def heartbeat_path(state_dir: str | Path) -> Path:
    return Path(state_dir) / HEARTBEAT


def write_heartbeat(state_dir: str | Path, payload: dict) -> Path:
    """One write per successful poll. Atomic, so a reader never sees half a heartbeat."""
    path = heartbeat_path(state_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".part")
    temporary.write_text(json.dumps(payload, indent=2, sort_keys=True, default=str) + "\n")
    temporary.replace(path)
    return path


def read_heartbeat(state_dir: str | Path) -> dict | None:
    path = heartbeat_path(state_dir)
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text())
    except json.JSONDecodeError:
        return None


def _age_seconds(heartbeat: dict, now: datetime) -> float | None:
    try:
        when = datetime.fromisoformat(heartbeat["at"])
    except (KeyError, ValueError, TypeError):
        return None
    if when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)
    return (now - when).total_seconds()


def evaluate(config, store, *, now: datetime | None = None) -> list[Alert]:
    """Every alert this deployment should be raising right now."""
    now = now or datetime.now(timezone.utc)
    alerts: list[Alert] = []
    mailbox = config.mailbox.get("address") or "the mailbox"

    heartbeat = read_heartbeat(config.state_dir)
    if heartbeat is None:
        alerts.append(Alert(
            "never_started",
            f"The intake connector has never written a heartbeat. Nothing has been read from {mailbox} "
            f"since this deployment started.",
            {"state_dir": str(config.state_dir)}))
    else:
        age = _age_seconds(heartbeat, now)
        allowed = max(STALE_FLOOR_SECONDS, config.poll_seconds * STALE_POLL_MULTIPLIER)
        if age is None:
            alerts.append(Alert("heartbeat_unreadable",
                                "The intake heartbeat exists and cannot be read, so whether the connector is "
                                "running cannot be established.",
                                {"heartbeat": heartbeat}))
        elif age > allowed:
            # The sentence names the trap. A stale heartbeat with no error beside it is the shape of every
            # silent failure, and saying so is what stops the reader looking for an exception.
            last_error = heartbeat.get("last_error")
            alerts.append(Alert(
                "stale_heartbeat",
                f"Nothing has been read from {mailbox} for {int(age // 60)} minutes. "
                + ("The last poll failed and the reason is on this alert." if last_error else
                   "The last poll succeeded and reported no error, which is how a revoked permission, a "
                   "removed licence and a mail rule all look from here."),
                {"age_seconds": int(age), "allowed_seconds": allowed, "last_error": last_error,
                 "last_success": heartbeat.get("at")}))

        last_error = heartbeat.get("last_error") or {}
        if last_error:
            kind = last_error.get("kind") or "error"
            alerts.append(Alert(kind, last_error.get("sentence") or
                                f"Reading {mailbox} failed and the heartbeat carries no sentence for it.",
                                {"code": last_error.get("code"), "detail": last_error.get("detail")}))

    alerts.extend(_volume(config, store, now, mailbox))
    return alerts


def _volume(config, store, now: datetime, mailbox: str) -> list[Alert]:
    """The floor, and the drop. Both are counted from the journal rather than from a counter in memory."""
    floor = config.intake.get("volume_floor") or {}
    quiet_hours = float(config.intake.get("quiet_hours", 36))
    expected = float(floor.get("expected_per_day", 0) or 0)
    decline_days = int(floor.get("decline_days", 7))
    decline_fraction = float(floor.get("decline_fraction", 0.8))

    alerts: list[Alert] = []
    entries = store.entries()
    if not entries:
        # Nothing has ever arrived, so there is no rhythm to be below. The heartbeat covers the case where
        # the connector is not running; this one covers the case where it is running and mail is not coming.
        return alerts

    last = store.last_arrival()
    if last is not None and (now - last).total_seconds() / 3600 > quiet_hours:
        hours = int((now - last).total_seconds() // 3600)
        alerts.append(Alert(
            "volume_floor",
            f"No invoice has arrived for {hours} hours, which is longer than this deployment's quiet period "
            f"of {int(quiet_hours)}. The connector is reading {mailbox} and the mail is not reaching it.",
            {"hours_since_last_arrival": hours, "quiet_hours": quiet_hours, "last_arrival": last.isoformat()}))

    if expected > 0 and decline_days > 0:
        recent_start = now - timedelta(days=decline_days)
        previous_start = recent_start - timedelta(days=decline_days)
        recent = len(store.arrivals_between(recent_start, now))
        previous = len(store.arrivals_between(previous_start, recent_start))
        # Compared against the previous window rather than against the expectation alone, because a client
        # whose volume is seasonal would otherwise be alerted every slow month. The expectation only has to
        # be big enough for the comparison to mean something.
        meaningful = previous >= max(3.0, expected * decline_days * 0.5)
        if meaningful and recent <= previous * (1 - decline_fraction):
            alerts.append(Alert(
                "volume_decline",
                f"Invoice arrivals have fallen to {recent} in the last {decline_days} days from {previous} in "
                f"the {decline_days} before that. That is the shape of a mail rule diverting invoices rather "
                f"than of a quiet period.",
                {"recent": recent, "previous": previous, "decline_days": decline_days,
                 "decline_fraction": decline_fraction}))
    return alerts


def deliver(alert: Alert, config, *, transport=None) -> dict:
    """Send one alert to the addresses the client configured.

    The recipients are read from the client's file every time rather than captured at startup, so a client who
    changes the address after we leave is not waiting for a restart to see it take effect.
    """
    recipients = list(config.alerts.get("recipients") or [])
    record = alert.as_record(recipients)
    record["sent_at"] = datetime.now(timezone.utc).isoformat()
    record["deployment_id"] = config.deployment_id
    record["mailbox"] = config.mailbox.get("address")

    if transport is not None:
        transport(record)
        record["transport"] = "transport"
    else:
        outbox = Path(config.state_dir) / "alerts"
        outbox.mkdir(parents=True, exist_ok=True)
        name = f"{record['sent_at'].replace(':', '').replace('-', '')}-{alert.kind}.json"
        (outbox / name).write_text(json.dumps(record, indent=2, sort_keys=True, default=str) + "\n")
        record["transport"] = "outbox"
        record["file"] = str(outbox / name)
    return record


def smtp_transport(config):
    """A transport that mails through the client's own relay, when they configure one.

    Deliberately a function that returns a function rather than a branch inside `deliver`, so the tests
    exercise the same delivery path the deployment uses and the only thing swapped is where the bytes go.
    No credentials by default: this is the client's relay, on the client's network.
    """
    smtp = config.alerts.get("smtp") or {}
    host = smtp.get("host")
    port = int(smtp.get("port", 25))
    sender = smtp.get("from") or f"intake@{config.mailbox.get('address', 'localhost').split('@')[-1]}"

    def send(record: dict) -> None:
        message = EmailMessage()
        message["From"] = sender
        message["To"] = ", ".join(record["recipients"])
        message["Subject"] = f"Intake alert for {record.get('mailbox')}: {record['kind']}"
        message.set_content(record["sentence"] + "\n\n" + json.dumps(record["detail"], indent=2, default=str))
        with smtplib.SMTP(host, port, timeout=30) as client:
            client.send_message(message)

    return send


def format_alerts(alerts: list[Alert]) -> str:
    if not alerts:
        return "no alert"
    return "\n".join(f"{alert.kind}: {alert.sentence}" for alert in alerts)
