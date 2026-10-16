"""Shared test setup, and one problem it exists to prevent.

**The problem.** The API's contract tests need to import `services/api/app.py`, which imports FastAPI and
`sse_starlette`. Those are in the API's image, not necessarily on a machine running the suite. When the
import fails, the contract tests skip, and a skip inside a green run reads as a pass. That is the failure
mode this project has already paid for twice: a check that reported success while testing nothing.

So there are two deliberate choices here, and one refusal.

* **`sse_starlette` is stubbed when it is missing.** The contract tests read the app's route declarations
  and response models. They never open a stream, so the transport is not what is under test, and stubbing
  it costs nothing. The stub raises if it is ever used, so a test that accidentally depends on real
  streaming fails loudly instead of passing against a placeholder.
* **FastAPI is genuinely required, and its absence is skipped rather than faked.** Without FastAPI there
  is no app object to ask for a schema, and a hand-written stand-in would be testing the stand-in. The
  skip carries the install line, so a reader knows what to run.
* **What is refused:** a stub for FastAPI, or a schema read from a checked-in copy instead of the app.
  Both would let the suite stay green while the contract it claims to check went unread.

The API's own directory layout is reproduced on `sys.path` here, because the app imports its neighbours
by module name (`import access`, `import routing`) and the image is what normally arranges that. A stale
`/app/services/...` entry is removed first for a reason worth stating: on the deployment box those paths
exist and hold the *container's* copy of the code, so a test could import a module from the image while
believing it had imported the checkout. That is the same class of mistake as a reviewer testing a tree
that is seventy-eight commits behind.
"""
from __future__ import annotations

import sys
import types
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]

# Where the app expects to find its neighbours. `/app/...` is the image's layout and must not win here.
SERVICE_DIRS = ("api", "retrieval", "ingestion", "session", "gateway", "egress")


def _points_at_the_checkout(entry: str) -> bool:
    return entry.startswith("/app/services")


def install_service_paths() -> None:
    """Put the checkout's service directories ahead of anything the image left on the path."""
    for entry in [e for e in sys.path if _points_at_the_checkout(e)]:
        sys.path.remove(entry)
    for name in reversed(SERVICE_DIRS):
        path = str(ROOT / "services" / name)
        if path in sys.path:
            sys.path.remove(path)
        sys.path.insert(0, path)
    # **`services/` itself, for the packages that have one.** `services/workflow` is a package because its
    # modules import each other relatively, so it is reached as `workflow.engine` with `services/` on the
    # path. Adding the package directory alone would make `import engine` work and every relative import
    # inside it fail, which reads as a broken module rather than as the wrong path.
    services = str(ROOT / "services")
    if services in sys.path:
        sys.path.remove(services)
    sys.path.insert(0, services)


def stub_sse_starlette() -> bool:
    """A stand-in for the SSE transport, so the app imports where the real one is absent.

    Returns whether a stub was installed. **It raises when used**, because the alternative is a test that
    silently exercises a placeholder and reports the contract as checked.
    """
    try:
        import sse_starlette  # noqa: F401
        return False
    except ImportError:
        pass

    module = types.ModuleType("sse_starlette")
    sse = types.ModuleType("sse_starlette.sse")

    class EventSourceResponse:  # noqa: D101 - the message is the documentation
        def __init__(self, *args, **kwargs):
            raise RuntimeError(
                "sse_starlette is stubbed in tests. This stub exists so the app can be imported to read "
                "its contract, and it cannot serve a stream. Install sse-starlette to exercise streaming.")

    sse.EventSourceResponse = EventSourceResponse
    module.sse = sse
    sys.modules["sse_starlette"] = module
    sys.modules["sse_starlette.sse"] = sse
    return True


@pytest.fixture(scope="session")
def api_app():
    """The real FastAPI app object, or a skip that names what to install.

    Session-scoped because importing it is the expensive part and nothing here mutates it. The contract
    tests deep-copy any schema they mutate, so sharing the object is safe.
    """
    install_service_paths()
    stub_sse_starlette()
    try:
        import app as app_module
    except ImportError as exc:
        pytest.skip(
            f"the API needs fastapi to be importable, and this interpreter does not have it ({exc}). "
            "Run: pip install 'fastapi==0.143.0' 'sse-starlette==3.0.2'"
        )
    return app_module.app
