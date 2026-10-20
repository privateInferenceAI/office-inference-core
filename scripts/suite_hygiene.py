#!/usr/bin/env python3
"""The checks that keep a suite run from being taken under contention, and the record of where it was taken.

**Why this is a module rather than two lines in each caller.** A suite run starts in one of three ways: the host
script (`scripts/run_answer_suite.sh`), the runner it launches (`tests/fixtures/data_suite_run.py`), or a bare
`docker run` of that runner from a shell. The first two have to agree on one lock name, one pair of thresholds and
one definition of what a run context contains, or the rule that admitted a run and the record that describes it
can disagree. The logic lives here, in the standard library only, so the host script and the container runner can
share it and the tests can exercise every refusal without a database.

**What is enforced where.** The host script holds an flock for its whole life and refuses to start when the
one-minute load average or `MemAvailable` says the box is busy. The runner holds a Postgres advisory lock so a
direct `docker run` is caught too. Neither check is advisory: a run that fails one does not start, because a
measurement taken under load is not repaired by a caveat.

Usage on the box, from the deployment root:

    python3 scripts/suite_hygiene.py gate
    python3 scripts/suite_hygiene.py preflight --root /opt/oi-core --out /tmp/suite/run-context.json \
        --compose-file deploy/office/compose/docker-compose.yml --env-file rendered/env

The runner calls the functions directly; the subcommands exist so the shell script and an operator use the same
implementation as the tests.
"""
from __future__ import annotations

import argparse
import contextlib
import json
import math
import os
import socket
import subprocess
import sys
from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

PROC_LOADAVG = Path("/proc/loadavg")
PROC_MEMINFO = Path("/proc/meminfo")
CLOUD_INSTANCE_ID = Path("/var/lib/cloud/data/instance-id")

# **The defaults are the values the 2026-10-20 decision records.** The reference box is an m7i.4xlarge with 16
# vCPUs, so a one-minute load above 2.0 means something other than the suite is working. Raising either limit is
# a decision to measure under contention, not a tuning knob, and belongs in the decision record.
MAX_LOAD_VARIABLE = "OI_SUITE_MAX_LOAD"
MIN_AVAILABLE_MB_VARIABLE = "OI_SUITE_MIN_AVAILABLE_MB"
DEFAULT_MAX_LOAD = 2.0
DEFAULT_MIN_AVAILABLE_MB = 2048

# **One key for every deployment that shares a Postgres.** The value is the date the rule was adopted, not a hash,
# so a person reading `pg_locks` can recognize it. It has to stay stable: changing it silently stops excluding the
# older runners, which is the failure this lock exists to prevent.
ADVISORY_LOCK_KEY = 20261020
LOCK_EXIT_CODE = 4

# Names that describe the runner or the host rather than the answer path, and names that hold credentials. Neither
# belongs in a context record that can end up committed with a measurement.
NON_ANSWER_VARIABLES = (
    "OI_ROOT", "OI_UI_ROOT", "OI_API_URL", "OI_RUN_CONTEXT", "OI_INSTANCE_ID",
    "OI_SUITE_", "OI_TEST_", "OI_ASSERTION_",
)
SECRET_MARKERS = ("SECRET", "TOKEN", "PASSWORD", "PASSWD", "PRIVATE_KEY", "CREDENTIAL")

REQUIRED_CONTEXT_KEYS = (
    "timestamp", "git_commit", "oi_env", "images",
    "load_average", "mem_available_mb", "hostname", "instance_id",
)


class GateRefused(RuntimeError):
    """The box is not quiet enough to start a run, or the signal to tell is missing."""


class SuiteRefused(RuntimeError):
    """The run cannot take its serial lock, or cannot reach the database to try."""


class SuiteLockBusy(SuiteRefused):
    """Another suite run holds the database lock."""


@dataclass(frozen=True)
class GateSettings:
    max_load: float = DEFAULT_MAX_LOAD
    min_available_mb: int = DEFAULT_MIN_AVAILABLE_MB


def _number(environ: Mapping[str, str], variable: str, cast: Callable[[str], Any], default: Any) -> Any:
    raw = (environ.get(variable) or "").strip()
    if not raw:
        return default
    try:
        value = cast(raw)
    except ValueError:
        raise ValueError(f"{variable} must be a number, got {raw!r}") from None
    if isinstance(value, float) and not math.isfinite(value):
        raise ValueError(f"{variable} must be a finite number, got {raw!r}")
    if value <= 0:
        raise ValueError(f"{variable} must be greater than zero, got {raw!r}")
    return value


def settings_from_env(environ: Mapping[str, str] | None = None) -> GateSettings:
    """The limits the run is admitted under, read from the environment the script runs in.

    A mistyped limit is an error rather than a fallback to the default. Falling back would quietly widen the gate
    that the operator believed they had set, which is the same class of mistake as a check that passes while
    testing nothing.
    """
    environ = os.environ if environ is None else environ
    return GateSettings(
        max_load=_number(environ, MAX_LOAD_VARIABLE, float, DEFAULT_MAX_LOAD),
        min_available_mb=_number(environ, MIN_AVAILABLE_MB_VARIABLE, int, DEFAULT_MIN_AVAILABLE_MB),
    )


def read_load_average(path: Path | str = PROC_LOADAVG) -> float | None:
    """The one-minute load average, or None when it cannot be read."""
    try:
        return float(Path(path).read_text(encoding="utf-8").split()[0])
    except (OSError, IndexError, ValueError):
        return None


def read_mem_available_mb(path: Path | str = PROC_MEMINFO) -> int | None:
    """MemAvailable in megabytes, or None when it cannot be read. `/proc/meminfo` reports kilobytes."""
    try:
        for line in Path(path).read_text(encoding="utf-8").splitlines():
            if line.startswith("MemAvailable:"):
                return int(line.split()[1]) // 1024
    except (OSError, IndexError, ValueError):
        return None
    return None


def check_gate(load_average: float | None, mem_available_mb: int | None,
               settings: GateSettings | None = None) -> dict:
    """Refuse when the box is busy, naming what was observed. Returns the observation on success.

    A missing signal is a refusal, not a pass. `/proc` being unreadable on the box means the check cannot tell
    whether the measurement is serial, and a run that cannot prove it is serial is the case the rule is for.
    """
    settings = settings or GateSettings()
    problems = []
    if load_average is None:
        problems.append(f"could not read a load average from {PROC_LOADAVG}")
    elif load_average > settings.max_load:
        problems.append(f"load average {load_average:.2f} is above the {settings.max_load:.2f} limit for a run")
    if mem_available_mb is None:
        problems.append(f"could not read MemAvailable from {PROC_MEMINFO}")
    elif mem_available_mb < settings.min_available_mb:
        problems.append(f"MemAvailable {mem_available_mb} MB is below the {settings.min_available_mb} MB floor")
    if problems:
        raise GateRefused(
            "; ".join(problems)
            + f". The limits are {MAX_LOAD_VARIABLE} and {MIN_AVAILABLE_MB_VARIABLE}; raising either is a decision "
              "to measure under contention, not a fix. Wait for the box to go idle and start again."
        )
    return {"load_average": load_average, "mem_available_mb": mem_available_mb,
            "gates": {"max_load": settings.max_load, "min_available_mb": settings.min_available_mb}}


def oi_environment(environ: Mapping[str, str]) -> dict[str, str]:
    """The `OI_*` variables that describe the answer path, without the runner's own plumbing or credentials."""
    selected = {}
    for key, value in sorted(environ.items()):
        if not key.startswith("OI_"):
            continue
        if key.startswith(NON_ANSWER_VARIABLES):
            continue
        if any(marker in key.upper() for marker in SECRET_MARKERS):
            continue
        selected[key] = value
    return selected


def git_commit(root: Path | str) -> str | None:
    """The commit the checkout is on, or None. `safe.directory` is set because the eval image's uid is not the
    checkout's owner, and a dubious-ownership error would otherwise turn a recorded commit into a blank."""
    command = ["git", "-c", "safe.directory=*", "-C", str(root), "rev-parse", "HEAD"]
    try:
        completed = subprocess.run(command, capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.SubprocessError):
        return None
    if completed.returncode != 0:
        return None
    return completed.stdout.strip() or None


def host_name() -> str:
    try:
        return socket.gethostname()
    except OSError:
        return "unknown"


def instance_id(environ: Mapping[str, str] | None = None,
                paths: tuple[Path | str, ...] | None = None) -> str | None:
    """The box's instance id when one is readable. `OI_INSTANCE_ID` wins, so a deployment can name it without
    cloud-init, then the file cloud-init writes on EC2. No metadata call: this must not wait on a network."""
    environ = os.environ if environ is None else environ
    named = (environ.get("OI_INSTANCE_ID") or "").strip()
    if named:
        return named
    for path in (paths if paths is not None else (CLOUD_INSTANCE_ID,)):
        try:
            value = Path(path).read_text(encoding="utf-8").strip()
        except OSError:
            continue
        if value:
            return value
    return None


def build_context(*, timestamp: str | None = None, git_commit: str | None = None,
                  oi_env: Mapping[str, str] | None = None,
                  images: Mapping[str, Any] | None = None,
                  load_average: float | None = None, mem_available_mb: int | None = None,
                  hostname: str | None = None, instance_id: str | None = None,
                  gates: GateSettings | None = None, oi_env_source: str | None = None) -> dict:
    """The run context. Every required key is present even when its value is unknown, so a missing fact reads as
    null rather than as a field the reader has to notice is absent."""
    return {
        "timestamp": timestamp or datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "git_commit": git_commit,
        "oi_env": dict(sorted((oi_env or {}).items())),
        "oi_env_source": oi_env_source,
        "images": dict(images or {}),
        "load_average": load_average,
        "mem_available_mb": mem_available_mb,
        "hostname": hostname if hostname is not None else host_name(),
        "instance_id": instance_id,
        "gates": ({"max_load": gates.max_load, "min_available_mb": gates.min_available_mb}
                  if gates is not None else None),
    }


class SuiteLock:
    """A held advisory lock. Releasing twice is harmless; the connection close would release it anyway."""

    def __init__(self, connection: Any, key: int = ADVISORY_LOCK_KEY):
        self._connection = connection
        self._key = key
        self._released = False

    def release(self) -> None:
        if self._released:
            return
        self._released = True
        try:
            with self._connection.cursor() as cursor:
                cursor.execute("select pg_advisory_unlock(%s)", (self._key,))
            self._connection.commit()
        except Exception:  # noqa: BLE001 - closing the session is the backstop that actually releases it
            pass
        finally:
            self._connection.close()


@contextlib.contextmanager
def hold_advisory_lock(connect: Callable[[], Any],
                       key: int = ADVISORY_LOCK_KEY) -> Iterator[SuiteLock]:
    """Take the database lock for the duration of the block, or refuse with a sentence a person can act on.

    `pg_try_advisory_lock` is session-scoped, so the connection stays open for the whole run and the lock goes
    with it however the run ends. That property is why this is a context manager: a caller cannot forget to
    release it on the exception path.
    """
    try:
        connection = connect()
    except Exception as exc:  # noqa: BLE001 - a driver error is a refusal, not a traceback for the operator
        detail = " ".join(str(exc).split()) or type(exc).__name__
        raise SuiteRefused(
            f"could not reach Postgres to take the suite lock (key {key}): {detail}") from None
    try:
        with connection.cursor() as cursor:
            cursor.execute("select pg_try_advisory_lock(%s)", (key,))
            row = cursor.fetchone()
        connection.commit()
    except Exception as exc:  # noqa: BLE001
        connection.close()
        detail = " ".join(str(exc).split()) or type(exc).__name__
        raise SuiteRefused(f"could not take the suite lock (key {key}): {detail}") from None
    if not (row and row[0]):
        connection.close()
        raise SuiteLockBusy(
            f"another suite run holds the Postgres advisory lock (key {key}). Two runs against one deployment "
            "make both measurements unusable; wait for the other run to finish, then start again."
        )
    lock = SuiteLock(connection, key)
    try:
        yield lock
    finally:
        lock.release()


def run_command(command: list[str], timeout: float = 20.0) -> str | None:
    """Best-effort command output. Absence is recorded as absent, never raised: the context must not stop a run
    because Docker was slow to answer a question about itself."""
    try:
        completed = subprocess.run(command, capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.SubprocessError):
        return None
    return completed.stdout if completed.returncode == 0 else None


def compose_service_images(config_json: str) -> dict[str, str]:
    """The `api` and `eval` image references from `docker compose config --format json`."""
    try:
        document = json.loads(config_json)
    except ValueError:
        return {}
    services = document.get("services") or {}
    resolved = {}
    for name in ("api", "eval"):
        image = (services.get(name) or {}).get("image")
        if image:
            resolved[name] = image
    return resolved


def image_record(reference: str | None) -> dict:
    """An image's name, content id and repo digests, as far as Docker will say."""
    record: dict[str, Any] = {"name": reference, "id": None, "digests": []}
    if not reference:
        return record
    identity = run_command(["docker", "image", "inspect", reference, "--format",
                            '{{.Id}}|{{join .RepoDigests ","}}'])
    if identity is None:
        return record
    image_id, _, digest_text = identity.strip().partition("|")
    if image_id:
        record["id"] = image_id
    if digest_text:
        record["digests"] = [digest for digest in digest_text.split(",") if digest]
    return record


def compose_oi_environment(compose_args: list[str]) -> tuple[dict[str, str], str]:
    """The `OI_*` variables the running API was started with. The API container is where the answer path's flags
    actually live; the host shell usually has none of them. Falling back to the process environment is recorded
    as such, so a context is never silently thinner than it looks."""
    output = run_command(["docker", "compose", *compose_args, "exec", "-T", "api", "env"])
    if output is None:
        return oi_environment(os.environ), "process environment"
    values = {}
    for line in output.splitlines():
        key, separator, value = line.partition("=")
        if separator and key.startswith("OI_"):
            values[key] = value
    return oi_environment(values), "api container"


def context_for_runner(root: Path | str = Path("/w"), environ: Mapping[str, str] | None = None) -> dict:
    """The context a run records. When the host script wrote one, that record is reused verbatim, so the run's
    JSON and `run-context.json` cannot describe different boxes. A direct `docker run` builds what it can see."""
    environ = dict(os.environ if environ is None else environ)
    reference = (environ.get("OI_RUN_CONTEXT") or "").strip()
    if reference:
        try:
            return json.loads(Path(reference).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            pass  # a bad reference must not lose the run; the local facts below are the fallback
    return build_context(
        git_commit=git_commit(root),
        oi_env=oi_environment(environ),
        oi_env_source="runner process environment",
        images={},
        load_average=read_load_average(),
        mem_available_mb=read_mem_available_mb(),
        hostname=host_name(),
        instance_id=instance_id(environ),
    )


def preflight(root: Path | str, out: Path | str, compose_file: str = "", env_file: str = "",
              loadavg_path: Path | str = PROC_LOADAVG, meminfo_path: Path | str = PROC_MEMINFO,
              instance_id_path: Path | str | None = None) -> dict:
    """Gate the box, then write `run-context.json` into the output directory.

    The gate runs before the Docker queries so a busy box is refused in milliseconds, and the context is written
    only for a run that was admitted. A context that cannot be written is a refusal: the rule is that a run
    without a context is a smoke test, and a smoke test is not what this script exists to produce.
    """
    settings = settings_from_env(os.environ)
    observed = check_gate(read_load_average(loadavg_path), read_mem_available_mb(meminfo_path), settings)

    compose_args: list[str] = []
    if compose_file:
        compose_args += ["-f", compose_file]
    if env_file:
        compose_args += ["--env-file", env_file]

    images: dict[str, Any] = {}
    if compose_args:
        config_text = run_command(["docker", "compose", *compose_args, "config", "--format", "json"])
        if config_text:
            images = {name: image_record(reference)
                      for name, reference in compose_service_images(config_text).items()}

    if compose_args:
        oi_env, source = compose_oi_environment(compose_args)
    else:
        oi_env, source = oi_environment(os.environ), "process environment"

    context = build_context(
        git_commit=git_commit(root),
        oi_env=oi_env,
        oi_env_source=source,
        images=images,
        load_average=observed["load_average"],
        mem_available_mb=observed["mem_available_mb"],
        hostname=host_name(),
        instance_id=instance_id(paths=((instance_id_path,) if instance_id_path else None)),
        gates=settings,
    )
    Path(out).write_text(json.dumps(context, indent=2) + "\n", encoding="utf-8")
    return context


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    commands = parser.add_subparsers(dest="command", required=True)

    gate = commands.add_parser("gate", help="refuse when the box is too busy for a serial run")
    gate.add_argument("--loadavg", default=str(PROC_LOADAVG))
    gate.add_argument("--meminfo", default=str(PROC_MEMINFO))

    preflight_parser = commands.add_parser(
        "preflight", help="gate the box and write the run context the suite records")
    preflight_parser.add_argument("--root", default=".")
    preflight_parser.add_argument("--out", required=True)
    preflight_parser.add_argument("--compose-file", default="")
    preflight_parser.add_argument("--env-file", default="")
    preflight_parser.add_argument("--loadavg", default=str(PROC_LOADAVG))
    preflight_parser.add_argument("--meminfo", default=str(PROC_MEMINFO))
    preflight_parser.add_argument("--instance-id-file", default="")

    args = parser.parse_args(argv)
    try:
        if args.command == "gate":
            settings = settings_from_env(os.environ)
            observed = check_gate(read_load_average(args.loadavg),
                                  read_mem_available_mb(args.meminfo), settings)
            print(json.dumps(observed, indent=2))
            return 0
        context = preflight(
            root=args.root,
            out=args.out,
            compose_file=args.compose_file,
            env_file=args.env_file,
            loadavg_path=args.loadavg,
            meminfo_path=args.meminfo,
            instance_id_path=args.instance_id_file or None,
        )
        print(f"run context: {args.out}")
        print(json.dumps({"load_average": context["load_average"],
                          "mem_available_mb": context["mem_available_mb"],
                          "git_commit": context["git_commit"],
                          "images": sorted(context["images"])}, indent=2))
        return 0
    except GateRefused as exc:
        print(f"refusing to start: {exc}", file=sys.stderr)
        return LOCK_EXIT_CODE
    except ValueError as exc:
        print(f"refusing to start: {exc}", file=sys.stderr)
        return 2
    except OSError as exc:
        print(f"refusing to start: could not write the run context: {exc}", file=sys.stderr)
        return LOCK_EXIT_CODE


if __name__ == "__main__":
    raise SystemExit(main())
