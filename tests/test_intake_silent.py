"""The failures that leave a working credential and an empty queue. `CON-SILENT-001`.

The register's verify line names three: a revoked scope, a pulled licence and a mail rule diverting invoices.
"in every case the credential still works and the queue empties, so error logging reports nothing."

**Two of the three are indistinguishable from inside the connector**, and that is the finding the tests are
built around. A revoked scope and a diverting rule both present as a healthy token, a successful read, and zero
messages. Nothing raises, so nothing is logged, so a deployment built on exception logging reports health while
the client's invoices pile up somewhere a human has to find them. The signal has to be the absence of arrivals
rather than the presence of a fault.

The third, a pulled licence, does produce a 403 and this connector classifies it, so it is caught precisely
*and* by the floor. The tests assert both, because the register asks for the floor and a specific message is
worth more to the person who has to fix it.

**The recipient tests are half of this requirement.** An alert that routes to Office Inference reintroduces the
dependency the design removes, and it does it at the moment the client most needs to act without us.
"""
from __future__ import annotations

import hashlib
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tests" / "fixtures"))
sys.path.insert(0, str(ROOT / "services" / "intake"))

import intake_env  # noqa: E402

env = intake_env
config_module = intake_env.load("config")
graph = intake_env.load("graph")
intake = intake_env.load("intake")
monitor = intake_env.load("monitor")
net = intake_env.load("net")
provenance = intake_env.load("provenance")


def seed_arrivals(store, config, *, count: int, days_ago: float, verdict: str = "trusted") -> None:
    """Rows in the journal with the dates the floor is counted from. No mail server involved."""
    when = datetime.now(timezone.utc) - timedelta(days=days_ago)
    for index in range(count):
        digest = hashlib.sha256(f"{days_ago}-{index}".encode()).hexdigest()
        row = provenance.build_record(
            deployment_id=config.deployment_id, mailbox=config.mailbox.get("address"),
            folder=config.mailbox.get("folder"), channel="graph",
            message_id=f"<seed-{days_ago}-{index}@supplier.example>", provider_id=None,
            received_at=(when + timedelta(minutes=index)).isoformat(),
            observed_at=(when + timedelta(minutes=index)).isoformat(),
            sender={"header_from": "billing@supplier.example", "envelope_from": "bounce@supplier.example",
                    "reply_to": None, "auth": {"spf": "pass", "dkim": "pass", "dmarc": "pass"},
                    "allowlisted": True, "aligned": True, "verdict": verdict, "reasons": []},
            content_scan={"signals": []}, raw_message_sha256=digest, deposit_row=None,
            duplicate_of=None, credential={}, filename="invoice.pdf", media_type="application/pdf",
            content_sha256=digest, bytes_=1200)
        store.append(row)


def environment(tmp_path, server):
    env.install(server)
    config_path = env.environment(tmp_path, server)
    env.write_credential(config_path)
    config = config_module.load(config_path)
    assert not config.problems, config.problems
    return config_path, config, provenance.Store(config.state_dir)


@pytest.fixture
def tenant(tmp_path):
    server = env.FakeGraph(messages=env.sample_messages()).start()
    yield server, tmp_path
    server.stop()
    net.set_transport(None)


# --------------------------------------------------------------------------- the three failures

def test_a_revoked_scope_leaves_a_working_credential_and_an_empty_queue(tmp_path):
    """Nothing fails. The token is minted, the read succeeds, and no message is there.

    This is the failure error logging cannot see, and the assertion that matters is that the heartbeat carries
    **no error at all** while the queue is empty.
    """
    server = env.FakeGraph(messages=env.sample_messages()).start()
    try:
        server.empty_folder = True
        server.scopes = ""            # the scope is gone; the grant still works
        config_path, config, store = environment(tmp_path, server)
        seed_arrivals(store, config, count=8, days_ago=5)

        assert intake.main(["fetch", "--config", str(config_path)]) == 0

        heartbeat = monitor.read_heartbeat(config.state_dir)
        assert heartbeat["last_error"] is None, "an error was reported, so this is not the silent case"
        assert heartbeat["messages_seen"] == 0

        alerts = monitor.evaluate(config, store)
        assert any(alert.kind == "volume_floor" for alert in alerts), alerts
    finally:
        server.stop()
        net.set_transport(None)


def test_a_mail_rule_diverting_invoices_is_caught_by_the_floor(tmp_path):
    """Every request succeeds and the folder is empty. Indistinguishable from a quiet week."""
    server = env.FakeGraph(messages=env.sample_messages()).start()
    try:
        server.empty_folder = True
        config_path, config, store = environment(tmp_path, server)
        seed_arrivals(store, config, count=9, days_ago=4)

        assert intake.main(["fetch", "--config", str(config_path)]) == 0
        heartbeat = monitor.read_heartbeat(config.state_dir)
        assert heartbeat["last_error"] is None

        alerts = monitor.evaluate(config, store)
        floor = next(alert for alert in alerts if alert.kind == "volume_floor")
        assert "no invoice has arrived" in floor.sentence.lower()
        assert floor.detail["hours_since_last_arrival"] > config.intake["quiet_hours"]
    finally:
        server.stop()
        net.set_transport(None)


def test_a_pulled_licence_is_caught_precisely_and_by_the_floor(tmp_path):
    """The 403 case. The credential is fine, the mailbox is not readable, and nothing else looks wrong.

    Caught twice on purpose: the classification names the licence, which is what a person needs, and the floor
    catches it independently so a deployment whose error path is broken is still alerted.
    """
    server = env.FakeGraph(messages=env.sample_messages()).start()
    try:
        server.mailbox_status = 403
        config_path, config, store = environment(tmp_path, server)
        seed_arrivals(store, config, count=7, days_ago=3)

        assert intake.main(["fetch", "--config", str(config_path)]) == 2, \
            "a mail failure must exit non-zero, or a timer hides it"

        heartbeat = monitor.read_heartbeat(config.state_dir)
        assert heartbeat["last_error"]["kind"] == "mailbox_not_readable"
        assert "licence" in heartbeat["last_error"]["sentence"].lower()

        alerts = monitor.evaluate(config, store)
        kinds = {alert.kind for alert in alerts}
        assert "mailbox_not_readable" in kinds
        assert "volume_floor" in kinds
    finally:
        server.stop()
        net.set_transport(None)


def test_a_stale_heartbeat_is_an_alert_even_though_nothing_failed(tmp_path):
    """The connector stopped, or the box went down, and the queue simply stopped moving."""
    server = env.FakeGraph(messages=env.sample_messages())
    config_path, config, store = environment(tmp_path, server)
    seed_arrivals(store, config, count=5, days_ago=1)

    monitor.write_heartbeat(config.state_dir, {
        "at": (datetime.now(timezone.utc) - timedelta(hours=3)).isoformat(),
        "channel": "graph", "messages_seen": 0, "deposited": 0, "last_error": None})

    alerts = monitor.evaluate(config, store)
    stale = next(alert for alert in alerts if alert.kind == "stale_heartbeat")
    # The sentence has to say that the last poll succeeded, because that is the fact that sends a reader
    # looking in the right place instead of hunting for an exception that does not exist.
    assert "reported no error" in stale.sentence
    assert stale.detail["last_error"] is None


def test_a_connector_that_never_started_is_an_alert(tmp_path):
    server = env.FakeGraph(messages=env.sample_messages())
    _, config, store = environment(tmp_path, server)
    alerts = monitor.evaluate(config, store)
    assert any(alert.kind == "never_started" for alert in alerts)


def test_a_healthy_deployment_raises_nothing(tmp_path):
    """The control. A monitor that alerts on everything is a monitor nobody reads."""
    server = env.FakeGraph(messages=env.sample_messages()).start()
    try:
        config_path, config, store = environment(tmp_path, server)
        assert intake.main(["fetch", "--config", str(config_path)]) == 0
        alerts = monitor.evaluate(config, store)
        assert alerts == [], [alert.kind for alert in alerts]
        assert monitor.format_alerts(alerts) == "no alert"
    finally:
        server.stop()
        net.set_transport(None)


def test_a_sustained_decline_against_the_previous_window_is_an_alert(tmp_path):
    """A drop is a different shape from an absence, and a seasonal business needs both to be judged."""
    server = env.FakeGraph(messages=env.sample_messages())
    _, config, store = environment(tmp_path, server)
    seed_arrivals(store, config, count=30, days_ago=10)
    seed_arrivals(store, config, count=2, days_ago=2)

    alerts = monitor.evaluate(config, store)
    assert any(alert.kind == "volume_decline" for alert in alerts), [alert.kind for alert in alerts]


# --------------------------------------------------------------------------- the codes, in English

@pytest.mark.parametrize("code,kind,word", [
    ("AADSTS65001", "reapproval", "grant it again"),
    ("AADSTS700082", "reapproval", "not used for long enough"),
    ("AADSTS50173", "reapproval", "revoked"),
    ("AADSTS53003", "conditional_access", "Conditional Access"),
    ("AADSTS7000222", "credential_expired", "expired"),
    ("AADSTS700016", "application_removed", "no longer in your directory"),
])
def test_each_failure_is_one_sentence_a_client_can_act_on(code, kind, word):
    """The client never reads an AADSTS code. The code maps to an action."""
    classification = graph.classify_failure(400, f'{{"error_description": "{code}: something"}}',
                                            "ap@acmetool.example")
    assert classification["kind"] == kind
    assert word.lower() in classification["sentence"].lower()
    assert "AADSTS" not in classification["sentence"], "a code reached the sentence a client reads"


def test_an_unrecognised_code_says_so_rather_than_guessing():
    classification = graph.classify_failure(400, "AADSTS99999: who knows", "ap@acmetool.example")
    assert classification["kind"] == "unknown_code"
    assert "does not recognise" in classification["sentence"]


def test_a_broken_credential_reaches_the_heartbeat_with_its_sentence(tmp_path):
    server = env.FakeGraph(messages=env.sample_messages()).start()
    try:
        server.token_error = "AADSTS53003"
        config_path, config, store = environment(tmp_path, server)
        assert intake.main(["fetch", "--config", str(config_path)]) == 2

        heartbeat = monitor.read_heartbeat(config.state_dir)
        assert heartbeat["last_error"]["kind"] == "conditional_access"
        assert "Conditional Access" in heartbeat["last_error"]["sentence"]

        alerts = monitor.evaluate(config, store)
        alert = next(a for a in alerts if a.kind == "conditional_access")
        assert "AADSTS" not in alert.sentence
    finally:
        server.stop()
        net.set_transport(None)


# --------------------------------------------------------------------------- where the alert goes

def test_the_alert_goes_to_the_clients_address(tmp_path):
    server = env.FakeGraph(messages=env.sample_messages())
    _, config, store = environment(tmp_path, server)
    alerts = monitor.evaluate(config, store)
    record = monitor.deliver(alerts[0], config)

    assert record["recipients"] == ["office.manager@acmetool.example"]
    assert Path(record["file"]).exists()
    assert "office.manager@acmetool.example" in Path(record["file"]).read_text()
    assert "officeinference" not in Path(record["file"]).read_text()


def test_an_alert_recipient_on_a_domain_we_control_is_refused(tmp_path):
    """The rule is enforced rather than remembered, because a working alert to us looks exactly like a
    working alert to the client."""
    path = env.write_config(tmp_path, alerts={"recipients": ["alerts@officeinference.com"]})
    problems = config_module.load(path).problems
    assert any(problem.code == "alert-not-clients" for problem in problems)


def test_an_alert_with_nowhere_to_go_is_refused(tmp_path):
    path = env.write_config(tmp_path, alerts={"recipients": []})
    assert any(problem.code == "no-alert-recipient" for problem in config_module.load(path).problems)


def test_the_transport_is_where_the_deployment_says_it_is(tmp_path):
    """Delivery goes through the client's own relay when they configure one. The policy is the same either
    way, and the test asserts on the envelope rather than on a socket."""
    server = env.FakeGraph(messages=env.sample_messages())
    _, config, store = environment(tmp_path, server)
    sent = []
    record = monitor.deliver(monitor.evaluate(config, store)[0], config, transport=sent.append)

    assert sent and sent[0]["recipients"] == record["recipients"] == ["office.manager@acmetool.example"]
    assert sent[0]["deployment_id"] == config.deployment_id


def test_monitoring_a_deployment_that_cannot_be_checked_says_so(tmp_path, capsys):
    """A refused configuration must not be reported as healthy."""
    path = env.write_config(tmp_path, alerts={"recipients": ["alerts@officeinference.com"]})
    assert intake.main(["monitor", "--config", str(path)]) == 1
    assert "FAIL" in capsys.readouterr().err
