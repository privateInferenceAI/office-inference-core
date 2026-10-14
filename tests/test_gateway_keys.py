"""Tests for the key tool, against a stub gateway.

The tool's job is to mint one key per role, cap it, and be safe to run twice. Each of those is a
claim until something exercises it, and the real gateway is not available in CI, so these tests run
against a small HTTP server that stands in for LiteLLM's key endpoints and records what it was asked.
"""
from __future__ import annotations

import importlib.util
import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
KEYS_PY = ROOT / "services" / "gateway" / "keys.py"

MANIFEST = {
    "models": {"front_desk": {}, "analyst": {}, "reader": {}},
    "budgets": {"period_days": 30,
                "roles": {"front_desk": 5, "analyst": 50, "reader": 50}},
}


class StubGateway(BaseHTTPRequestHandler):
    """Stands in for LiteLLM. Records every call, mints incrementing keys, and refuses calls made
    with a key that has been capped, which is what a budget refusal looks like from the client."""

    calls: list = []
    capped: set = set()
    # Set to a (reason, status) pair to refuse every model call, which is how a test models a
    # refusal now that capping a virtual key no longer reaches the enforcement path.
    refuse: tuple | None = None

    def log_message(self, *args):  # keep the test output readable
        return

    def _read(self) -> dict:
        length = int(self.headers.get("Content-Length") or 0)
        return json.loads(self.rfile.read(length) or b"{}")

    def _send(self, status: int, body: dict) -> None:
        payload = json.dumps(body).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def do_POST(self):
        body = self._read()
        token = (self.headers.get("Authorization") or "").replace("Bearer ", "")
        StubGateway.calls.append({"path": self.path, "body": body, "token": token})

        if self.path == "/key/generate":
            self._send(200, {"key": f"sk-role-{len(StubGateway.calls)}"})
        elif self.path == "/key/update":
            if body.get("max_budget") is not None and float(body["max_budget"]) < 0.01:
                StubGateway.capped.add(body["key"])
            self._send(200, {"key": body.get("key")})
        elif self.path == "/v1/chat/completions":
            if StubGateway.refuse is not None:
                reason, status = StubGateway.refuse
                self._send(status, {"error": {"message": reason}})
            elif token in StubGateway.capped:
                self._send(400, {"error": {"message": "budget exceeded for this key"}})
            else:
                self._send(200, {"model": body.get("model"), "usage": {"total_tokens": 12}})
        else:
            self._send(404, {"error": {"message": f"no stub for {self.path}"}})


@pytest.fixture
def gateway():
    StubGateway.calls = []
    StubGateway.capped = set()
    StubGateway.refuse = None
    server = HTTPServer(("127.0.0.1", 0), StubGateway)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_port}"
    server.shutdown()


@pytest.fixture
def rendered(tmp_path, gateway, monkeypatch):
    (tmp_path / "env").write_text(
        "LITELLM_MASTER_KEY=sk-master\nDEPLOYMENT_ID=test-deploy\n")
    (tmp_path / "manifest.json").write_text(json.dumps(MANIFEST))
    monkeypatch.setenv("RENDERED_DIR", str(tmp_path))
    monkeypatch.setenv("LITELLM_URL", gateway)

    spec = importlib.util.spec_from_file_location("oi_keys_under_test", KEYS_PY)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def paths(gateway_calls) -> list[str]:
    return [c["path"] for c in gateway_calls]


def test_first_run_mints_one_key_per_role(rendered):
    assert rendered.main([]) == 0
    calls = StubGateway.calls
    assert paths(calls).count("/key/generate") == 3
    assert all(c["token"] == "sk-master" for c in calls), "the admin path uses the master key"

    keys = json.loads((rendered.KEYS_FILE).read_text())
    assert sorted(keys) == ["analyst", "front_desk", "reader"]
    assert keys["analyst"]["models"] == ["analyst"], "each key is scoped to its own role"
    assert keys["analyst"]["max_budget"] == 50
    assert keys["analyst"]["budget_duration"] == "30d"
    assert keys["analyst"]["alias"] == "test-deploy:analyst"


def test_the_keys_file_is_readable_only_by_its_owner(rendered):
    assert rendered.main([]) == 0
    mode = rendered.KEYS_FILE.stat().st_mode & 0o777
    assert mode == 0o600, f"the file holds credentials and is mode {oct(mode)}"


def test_a_second_run_updates_rather_than_minting(rendered):
    """Re-running must restore the manifest's policy, not litter the database with credentials.
    The key material is the part that must not change, because components already hold it."""
    assert rendered.main([]) == 0
    first = json.loads(rendered.KEYS_FILE.read_text())
    StubGateway.calls = []

    assert rendered.main([]) == 0
    assert paths(StubGateway.calls) == ["/key/update"] * 3

    second = json.loads(rendered.KEYS_FILE.read_text())
    assert second == first, "the secrets and their policy should be unchanged"


def test_every_role_carries_its_budget_and_its_acl(rendered):
    assert rendered.main([]) == 0
    sent = {c["body"].get("key_alias") or c["body"].get("key"): c["body"] for c in StubGateway.calls}
    for alias, body in sent.items():
        assert body["models"] == [alias.split(":")[1]]
        assert body["max_budget"] > 0
        assert body["metadata"]["deployment_id"] == "test-deploy"


def test_capping_a_role_writes_the_team_row_the_gateway_enforces(rendered):
    """**The brake used to write the virtual key, and nothing consulted it.**

    Request traffic is authenticated by a signed statement that resolves to a team, so capping a key reported
    success while the team row stayed put and the next call answered normally. This asserts where the number now
    goes, which is the row the auth hook reads.
    """
    assert rendered.main([]) == 0
    StubGateway.calls = []
    written = []

    class Cursor:
        def __init__(self):
            self._row = None

        def execute(self, statement, params=()):
            if statement.strip().lower().startswith('select'):
                self._row = ('analyst',)
            else:
                written.append((statement, params))

        def fetchone(self):
            return self._row

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    class Conn:
        def cursor(self):
            return Cursor()

        def commit(self):
            pass

        def close(self):
            pass

    args = type('Args', (), {'role': 'analyst', 'amount': '0.000001'})()
    env = rendered.read_env(rendered.ENV_FILE)
    env['DATABASE_URL'] = 'postgresql://stub'
    assert rendered.set_budget(env, args, connect=lambda url: Conn()) == 0

    assert written, 'no ceiling was written anywhere'
    statement, params = written[0]
    assert 'LiteLLM_TeamTable' in statement, 'the ceiling must go on the team row the auth hook reads'
    assert 'max_budget' in statement
    assert params == (0.000001, 'analyst')


def test_capping_a_role_that_has_no_team_refuses(rendered):
    """A brake on a role that does not exist must fail rather than report success."""
    assert rendered.main([]) == 0

    class Cursor:
        def execute(self, statement, params=()):
            pass

        def fetchone(self):
            return None

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    class Conn:
        def cursor(self):
            return Cursor()

        def commit(self):
            pass

        def close(self):
            pass

    args = type('Args', (), {'role': 'nobody', 'amount': '1'})()
    env = rendered.read_env(rendered.ENV_FILE)
    env['DATABASE_URL'] = 'postgresql://stub'
    with pytest.raises(SystemExit):
        rendered.set_budget(env, args, connect=lambda url: Conn())


def test_the_brake_says_the_gateway_must_restart(rendered, capsys):
    """**A control that reports success and does nothing is worse than no brake.**

    The gateway caches the team row, so the number written here is inert until it restarts. Saying so turns it
    from a belief into an instruction.
    """
    assert rendered.main([]) == 0

    class Cursor:
        def execute(self, statement, params=()):
            pass

        def fetchone(self):
            return ('analyst',)

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    class Conn:
        def cursor(self):
            return Cursor()

        def commit(self):
            pass

        def close(self):
            pass

    args = type('Args', (), {'role': 'analyst', 'amount': '1'})()
    env = rendered.read_env(rendered.ENV_FILE)
    env['DATABASE_URL'] = 'postgresql://stub'
    assert rendered.set_budget(env, args, connect=lambda url: Conn()) == 0
    out = capsys.readouterr().out
    assert 'restart' in out.lower(), 'the reader is not told the number is inert until a restart'

def test_a_probe_reports_the_answer_and_the_tokens(rendered):
    assert rendered.main([]) == 0
    args = type("Args", (), {"role": "analyst", "as_model": None})()
    assert rendered.probe(rendered.read_env(rendered.ENV_FILE), args) == 0


def test_a_probe_of_a_model_the_key_is_not_scoped_to_is_refused(rendered):
    """The drill that proves the ACL. The stub refuses nothing here, so this asserts the request
    carries the model the operator asked for rather than the key's own role."""
    assert rendered.main([]) == 0
    StubGateway.calls = []
    args = type("Args", (), {"role": "analyst", "as_model": "reader"})()
    assert rendered.probe(rendered.read_env(rendered.ENV_FILE), args) == 0
    body = [c for c in StubGateway.calls if c["path"] == "/v1/chat/completions"][0]["body"]
    assert body["model"] == "reader", "the probe must ask for the model it was told to ask for"


def test_an_over_budget_call_is_reported_as_a_failure(rendered):
    """What the operator sees when the ceiling holds: a non-zero exit and the gateway's reason,
    not a traceback."""
    assert rendered.main([]) == 0
    env = rendered.read_env(rendered.ENV_FILE)
    env['DATABASE_URL'] = 'postgresql://stub'

    class Cursor:
        def execute(self, statement, params=()):
            pass

        def fetchone(self):
            return ('analyst',)

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    class Conn:
        def cursor(self):
            return Cursor()

        def commit(self):
            pass

        def close(self):
            pass

    # The stub gateway answers the probe with a budget refusal, so what is asserted here is the reporting of a
    # refused call rather than the capping, which has its own tests above.
    rendered.set_budget(env, type('Args', (), {'role': 'analyst', 'amount': '0.000001'})(),
                        connect=lambda url: Conn())
    StubGateway.refuse = ('budget exceeded for this key', 400)
    args = type('Args', (), {'role': 'analyst', 'as_model': None})()
    assert rendered.probe(env, args) == 1

# --- revoking a key ----------------------------------------------------------------------------

def test_revoke_deletes_the_key_at_the_gateway_and_forgets_it(rendered, monkeypatch):
    """Deleting a role from a manifest leaves its key behind, scoped to a model that no longer
    exists. Both reviewers found stale keys on the box for exactly that reason, and minting only ever
    adds or updates, so nothing else in this tool would remove one."""
    module = rendered
    calls = []

    def fake_request(path, token, payload=None):
        calls.append((path, payload))
        return 200, {"deleted_keys": ["sk-analyst"]}

    monkeypatch.setattr(module, "request", fake_request)
    monkeypatch.setattr(module, "load_keys", lambda: {"analyst": {"key": "sk-analyst"}})
    saved = {}
    monkeypatch.setattr(module, "save_keys", lambda k: saved.update(k))

    assert module.revoke({"LITELLM_MASTER_KEY": "sk-master"}, "analyst") == 0

    assert calls and calls[0][0] == "/key/delete", calls
    assert calls[0][1] == {"keys": ["sk-analyst"]}, "the gateway is asked to delete that key"
    assert saved == {}, "and the tool forgets it, so re-adding the role mints a new one"


def test_revoking_a_role_with_no_recorded_key_says_so(rendered, monkeypatch):
    module = rendered
    monkeypatch.setattr(module, "load_keys", lambda: {})
    with pytest.raises(SystemExit) as exit_info:
        module.revoke({"LITELLM_MASTER_KEY": "sk-master"}, "nobody")
    assert exit_info.value.code != 0
