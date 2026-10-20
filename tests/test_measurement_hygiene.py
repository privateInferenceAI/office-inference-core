"""Measurement hygiene: one run at a time, and a context record that says where a run was taken.

**Why these tests exist.** The rule they guard is not a preference. On 2026-10-20 the same configuration scored 53
of 53 on five runs while the box was otherwise idle, then 48 to 51 of 53 in a window when a second suite was
running against the same deployment, with N06 and N07 failing in the lower runs. The run files recorded no load, no
commit, no flags and no images, so afterwards the window could not be separated from the result. A lock that is not
taken, a gate that passes on a busy box, or a context that silently loses a key is that failure again, and it would
look like a clean measurement.

Everything here runs without a database. The lock is tested against a connection double that records the SQL, the
gate against fake `/proc` files, and the host lock by running the script itself with the lock held.
"""
from __future__ import annotations

import ast
import fcntl
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import acceptance_panel  # noqa: E402
import suite_hygiene  # noqa: E402


def proc_files(tmp_path: Path, load: str = "0.42 0.30 0.20 1/320 12345",
               available_kb: int = 16 * 1024 * 1024) -> tuple[Path, Path]:
    """Fake `/proc/loadavg` and `/proc/meminfo` so the gate is exercised without the machine's real load."""
    loadavg = tmp_path / "loadavg"
    loadavg.write_text(load + "\n", encoding="utf-8")
    meminfo = tmp_path / "meminfo"
    meminfo.write_text(f"MemTotal: 65536000 kB\nMemAvailable: {available_kb} kB\n", encoding="utf-8")
    return loadavg, meminfo


def observe(loadavg: Path, meminfo: Path, settings: suite_hygiene.GateSettings | None = None) -> dict:
    return suite_hygiene.check_gate(suite_hygiene.read_load_average(loadavg),
                                    suite_hygiene.read_mem_available_mb(meminfo), settings)


# --------------------------------------------------------------------------- the load and memory gate

def test_the_gate_passes_on_a_quiet_box(tmp_path: Path) -> None:
    loadavg, meminfo = proc_files(tmp_path)
    observed = observe(loadavg, meminfo, suite_hygiene.GateSettings())
    assert observed["load_average"] == pytest.approx(0.42)
    assert observed["mem_available_mb"] == 16 * 1024
    assert observed["gates"] == {"max_load": 2.0, "min_available_mb": 2048}


def test_the_gate_refuses_a_busy_box_and_names_the_observed_load(tmp_path: Path) -> None:
    loadavg, meminfo = proc_files(tmp_path, load="3.50 2.80 2.10 2/300 999")
    with pytest.raises(suite_hygiene.GateRefused) as caught:
        observe(loadavg, meminfo, suite_hygiene.GateSettings(max_load=2.0, min_available_mb=2048))
    message = str(caught.value)
    assert "load average 3.50" in message
    assert "2.00 limit" in message
    assert "OI_SUITE_MAX_LOAD" in message


def test_the_gate_refuses_below_the_memory_floor_and_names_the_observation(tmp_path: Path) -> None:
    loadavg, meminfo = proc_files(tmp_path, available_kb=900 * 1024)
    with pytest.raises(suite_hygiene.GateRefused) as caught:
        observe(loadavg, meminfo, suite_hygiene.GateSettings(max_load=2.0, min_available_mb=2048))
    message = str(caught.value)
    assert "MemAvailable 900 MB" in message
    assert "2048 MB floor" in message
    assert "OI_SUITE_MIN_AVAILABLE_MB" in message


def test_a_missing_signal_is_a_refusal_not_a_pass() -> None:
    """If `/proc` cannot be read, the check cannot prove the run is serial, so it must not admit it."""
    with pytest.raises(suite_hygiene.GateRefused) as caught:
        suite_hygiene.check_gate(None, 4096, suite_hygiene.GateSettings())
    assert "could not read a load average" in str(caught.value)
    with pytest.raises(suite_hygiene.GateRefused) as caught:
        suite_hygiene.check_gate(0.1, None, suite_hygiene.GateSettings())
    assert "could not read MemAvailable" in str(caught.value)


def test_the_limits_are_read_from_the_environment() -> None:
    settings = suite_hygiene.settings_from_env({
        "OI_SUITE_MAX_LOAD": "4.5",
        "OI_SUITE_MIN_AVAILABLE_MB": "512",
    })
    assert settings == suite_hygiene.GateSettings(max_load=4.5, min_available_mb=512)


def test_the_defaults_are_the_limits_the_decision_records() -> None:
    assert suite_hygiene.settings_from_env({}) == suite_hygiene.GateSettings(max_load=2.0,
                                                                            min_available_mb=2048)


@pytest.mark.parametrize("variable,value", [
    ("OI_SUITE_MAX_LOAD", "two"),
    ("OI_SUITE_MAX_LOAD", "nan"),
    ("OI_SUITE_MIN_AVAILABLE_MB", "2.5"),
    ("OI_SUITE_MIN_AVAILABLE_MB", "0"),
])
def test_a_mistyped_limit_is_refused_rather_than_ignored(variable: str, value: str) -> None:
    """Falling back to the default would widen the gate the operator believed they had set."""
    with pytest.raises(ValueError, match=variable):
        suite_hygiene.settings_from_env({variable: value})


# --------------------------------------------------------------------------- the Postgres advisory lock

class FakeCursor:
    def __init__(self, connection: "FakeConnection"):
        self.connection = connection

    def __enter__(self) -> "FakeCursor":
        return self

    def __exit__(self, *exc_info) -> bool:
        return False

    def execute(self, statement: str, parameters=None) -> None:
        self.connection.statements.append((statement, parameters))

    def fetchone(self):
        return self.connection.results.pop(0)


class FakeConnection:
    """Enough of a psycopg connection to say whether the lock was taken and released."""

    def __init__(self, held: bool = True):
        self.statements: list[tuple[str, object]] = []
        self.results: list[tuple[bool]] = [(held,)]
        self.commits = 0
        self.closed = False

    def cursor(self) -> FakeCursor:
        return FakeCursor(self)

    def commit(self) -> None:
        self.commits += 1

    def close(self) -> None:
        self.closed = True


def test_the_run_takes_the_advisory_lock_by_key_and_releases_it() -> None:
    connection = FakeConnection(held=True)
    with suite_hygiene.hold_advisory_lock(lambda: connection) as lock:
        assert isinstance(lock, suite_hygiene.SuiteLock)
    statements = [statement for statement, _ in connection.statements]
    assert any("pg_try_advisory_lock" in statement for statement in statements)
    assert any("pg_advisory_unlock" in statement for statement in statements)
    attempt = next(parameters for statement, parameters in connection.statements
                   if "pg_try_advisory_lock" in statement)
    assert attempt == (suite_hygiene.ADVISORY_LOCK_KEY,)
    assert connection.closed is True


def test_a_held_lock_refuses_before_the_run_starts() -> None:
    connection = FakeConnection(held=False)
    with pytest.raises(suite_hygiene.SuiteLockBusy) as caught:
        with suite_hygiene.hold_advisory_lock(lambda: connection):
            pytest.fail("the suite body must not run while another run holds the lock")
    message = str(caught.value)
    assert "another suite run holds" in message
    assert str(suite_hygiene.ADVISORY_LOCK_KEY) in message
    assert connection.closed is True


def test_an_unreachable_database_is_a_sentence_not_a_traceback() -> None:
    def connect():
        raise OSError("connection refused")

    with pytest.raises(suite_hygiene.SuiteRefused) as caught:
        with suite_hygiene.hold_advisory_lock(connect):
            pytest.fail("no body without a lock")
    assert "could not reach Postgres" in str(caught.value)
    assert "connection refused" in str(caught.value)


def test_the_lock_is_released_when_the_run_raises() -> None:
    connection = FakeConnection(held=True)
    with pytest.raises(RuntimeError, match="boom"):
        with suite_hygiene.hold_advisory_lock(lambda: connection):
            raise RuntimeError("boom")
    assert any("pg_advisory_unlock" in statement for statement, _ in connection.statements)
    assert connection.closed is True


# --------------------------------------------------------------------------- the run context

def test_the_context_carries_every_required_key() -> None:
    context = suite_hygiene.build_context(
        timestamp="2026-10-20T20:00:00Z",
        git_commit="a" * 40,
        oi_env={"OI_TOOL_LOOP": "on"},
        images={"api": {"name": "oi-core/api:local", "id": "sha256:" + "b" * 64, "digests": []}},
        load_average=0.42,
        mem_available_mb=16384,
        hostname="oi-core-dev",
        instance_id="i-096bebd46308d1673",
        gates=suite_hygiene.GateSettings(),
    )
    missing = set(suite_hygiene.REQUIRED_CONTEXT_KEYS) - set(context)
    assert not missing, f"context is missing {sorted(missing)}"
    assert context["git_commit"] == "a" * 40
    assert context["oi_env"] == {"OI_TOOL_LOOP": "on"}
    assert context["gates"] == {"max_load": 2.0, "min_available_mb": 2048}


def test_the_context_records_answer_flags_and_leaves_secrets_out() -> None:
    selected = suite_hygiene.oi_environment({
        "OI_TOOL_LOOP": "on",
        "OI_TOOL_QUERY": "off",
        "OI_PASSAGES": "6",
        "OI_ASSERTION_SECRET": "not-recorded",
        "OI_TOKEN": "not-recorded",
        "OI_ROOT": "/opt/oi-core",
        "OI_API_URL": "http://api:8088",
        "DATABASE_URL": "postgresql://user:password@postgres/db",
        "PATH": "/usr/bin",
    })
    assert selected == {"OI_PASSAGES": "6", "OI_TOOL_LOOP": "on", "OI_TOOL_QUERY": "off"}


def test_the_runner_reuses_the_context_file_the_script_wrote(tmp_path: Path) -> None:
    written = {
        "timestamp": "2026-10-20T20:00:00Z", "git_commit": "c" * 40, "oi_env": {}, "images": {},
        "load_average": 0.3, "mem_available_mb": 30000, "hostname": "oi-core-dev",
        "instance_id": "i-0123456789abcdef0", "gates": {"max_load": 2.0, "min_available_mb": 2048},
    }
    path = tmp_path / "run-context.json"
    path.write_text(json.dumps(written), encoding="utf-8")
    context = suite_hygiene.context_for_runner(root=tmp_path, environ={"OI_RUN_CONTEXT": str(path)})
    assert context == written


def test_a_direct_runner_builds_a_context_with_the_required_keys(tmp_path: Path) -> None:
    context = suite_hygiene.context_for_runner(root=tmp_path, environ={"OI_TOOL_LOOP": "on"})
    assert set(suite_hygiene.REQUIRED_CONTEXT_KEYS) <= set(context)
    assert context["oi_env"] == {"OI_TOOL_LOOP": "on"}
    assert context["images"] == {}
    assert context["gates"] is None


def test_preflight_writes_the_context_after_the_gate_passes(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.delenv("OI_SUITE_MAX_LOAD", raising=False)
    monkeypatch.delenv("OI_SUITE_MIN_AVAILABLE_MB", raising=False)
    loadavg, meminfo = proc_files(tmp_path)
    out = tmp_path / "out" / "run-context.json"
    out.parent.mkdir()
    context = suite_hygiene.preflight(root=tmp_path, out=out, loadavg_path=loadavg,
                                      meminfo_path=meminfo,
                                      instance_id_path=tmp_path / "instance-id")
    assert json.loads(out.read_text(encoding="utf-8")) == context
    assert set(suite_hygiene.REQUIRED_CONTEXT_KEYS) <= set(context)


def test_preflight_writes_nothing_when_the_gate_refuses(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.delenv("OI_SUITE_MAX_LOAD", raising=False)
    monkeypatch.delenv("OI_SUITE_MIN_AVAILABLE_MB", raising=False)
    loadavg, meminfo = proc_files(tmp_path, load="9.00 8.00 7.00 2/300 999")
    out = tmp_path / "out" / "run-context.json"
    out.parent.mkdir()
    with pytest.raises(suite_hygiene.GateRefused):
        suite_hygiene.preflight(root=tmp_path, out=out, loadavg_path=loadavg, meminfo_path=meminfo)
    assert not out.exists(), "a refused run must not leave a context that looks like a measurement"


def test_the_committed_runner_takes_the_lock_and_records_the_context() -> None:
    """A wiring guard, not a behaviour test.

    The runner cannot be imported here without a database driver and a mounted checkout, so its source is checked
    for the two facts that make the rule real. Without this, deleting the lock or the context key from the runner
    would leave every test above passing, because they test the helpers it calls.
    """
    source = (ROOT / "tests" / "fixtures" / "data_suite_run.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    with_calls = [item.context_expr for node in ast.walk(tree) if isinstance(node, ast.With)
                  for item in node.items]
    assert any(isinstance(call, ast.Call) and getattr(call.func, "attr", "") == "hold_advisory_lock"
               for call in with_calls)
    dict_keys = {key.value for node in ast.walk(tree) if isinstance(node, ast.Dict)
                 for key in node.keys if isinstance(key, ast.Constant) and isinstance(key.value, str)}
    assert "context" in dict_keys


# --------------------------------------------------------------------------- the host script's flock

needs_host_lock = pytest.mark.skipif(
    not (shutil.which("bash") and shutil.which("flock")),
    reason="the host lock is bash plus util-linux flock",
)


def run_suite_script(root: Path, out: Path, lock: Path, **environment: str) -> subprocess.CompletedProcess:
    env = dict(os.environ, OI_ROOT=str(root), OI_SUITE_LOCK=str(lock))
    env.update(environment)
    return subprocess.run(["bash", str(ROOT / "scripts" / "run_answer_suite.sh"), "1", str(out)],
                          capture_output=True, text=True, env=env, timeout=60)


@needs_host_lock
def test_the_suite_script_fails_fast_when_another_suite_holds_the_lock(tmp_path: Path) -> None:
    lock = tmp_path / "suite.lock"
    lock.write_text("4242\n", encoding="utf-8")
    with open(lock, "a", encoding="utf-8") as handle:
        fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        completed = run_suite_script(tmp_path / "deployment", tmp_path / "out", lock)
    assert completed.returncode == 3
    assert "another answer suite holds" in completed.stderr
    assert str(lock) in completed.stderr
    assert "4242" in completed.stderr, "the holder's pid is the part that tells the operator what to wait for"


@needs_host_lock
def test_the_suite_script_takes_the_lock_before_it_reads_the_deployment(tmp_path: Path) -> None:
    """No rendered environment means exit 2, and the pid in the lock file proves the lock was taken first."""
    deployment = tmp_path / "deployment"
    deployment.mkdir()
    lock = tmp_path / "suite.lock"
    completed = run_suite_script(deployment, tmp_path / "out", lock)
    assert completed.returncode == 2
    assert "no rendered environment" in completed.stderr
    assert lock.read_text(encoding="utf-8").strip().isdigit()


@needs_host_lock
@pytest.mark.skipif(not Path("/proc/meminfo").exists(), reason="the gate reads /proc/meminfo")
def test_the_suite_script_refuses_to_start_when_the_box_is_busy(tmp_path: Path) -> None:
    deployment = tmp_path / "deployment"
    (deployment / "rendered").mkdir(parents=True)
    (deployment / "rendered" / "env").write_text("", encoding="utf-8")
    (deployment / "scripts").symlink_to(ROOT / "scripts", target_is_directory=True)
    out = tmp_path / "out"
    completed = run_suite_script(deployment, out, tmp_path / "suite.lock",
                                 OI_SUITE_MIN_AVAILABLE_MB="100000000")
    assert completed.returncode == 4
    assert "refusing to start" in completed.stderr
    assert "MemAvailable" in completed.stderr
    assert "OI_SUITE_MIN_AVAILABLE_MB" in completed.stderr
    assert not (out / "run-context.json").exists()


# --------------------------------------------------------------------------- the acceptance panel's warning

def test_an_older_run_without_a_context_warns_and_still_counts() -> None:
    run = {"ran_at": "2026-10-19T10:00:00Z", "duration_seconds": 60,
           "results": [{"score": {"pass": True}, "answer": {"kind": "answer", "elapsed_ms": 1000}}]}
    warnings = acceptance_panel.context_warnings([run], names=["run-1.json"])
    assert len(warnings) == 1
    assert "run-1.json" in warnings[0]
    assert "no run context" in warnings[0]
    assert acceptance_panel.score_totals([run])["passed"] == 1


def test_a_run_taken_above_the_load_limit_names_both_numbers() -> None:
    run = {"context": {"load_average": 5.2, "gates": {"max_load": 2.0}}}
    warnings = acceptance_panel.context_warnings([run], names=["run-3.json"])
    assert warnings == [
        "run-3.json: started at load average 5.20, above the 2.00 limit; the run may have contended with other work"
    ]


def test_a_run_within_the_limit_does_not_warn() -> None:
    run = {"context": {"load_average": 0.4, "gates": {"max_load": 2.0}}}
    assert acceptance_panel.context_warnings([run]) == []


def test_the_panel_includes_the_warnings_without_failing_old_runs(monkeypatch) -> None:
    monkeypatch.setattr(acceptance_panel, "spend_totals", lambda start, end: {"available": False})
    run = {"ran_at": "2026-10-19T10:00:00Z", "duration_seconds": 60,
           "results": [{"score": {"pass": True}, "answer": {"kind": "answer", "elapsed_ms": 1000}}]}
    panel = acceptance_panel.build([run], names=["run-1.json"])
    assert panel["accuracy"]["scored"] == 1
    assert panel["accuracy"]["passed"] == 1
    assert panel["warnings"] and "run-1.json" in panel["warnings"][0]
