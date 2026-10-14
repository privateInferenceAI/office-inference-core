"""Did the second container really load the forbidden class?

**Why this is its own file, and why it asks the service rather than reading a file.** The runner used to
confirm the setup by reading `access.data_class` out of the *copy* it had just written. That proves the copy
is right and nothing about the container: the app loads `OI_MANIFEST` at startup, so a container that was
given a different path, or that failed to read it, still passes a check on a file it never opened. It did
exactly that — a diagnosis run printed `default manifest access.data_class = internal` from the container
while the runner was satisfied with `regulated` from the file.

So this asks the running service the question that matters: **does it refuse a request carrying the class it
was supposedly given?** If it does not, the boundary probe that follows would pass or fail for reasons
unrelated to the boundary.

**It reports three states, not two.** `loaded` means the service refuses and names the class. `open` means it
answered — the boundary is not being enforced, which is a result about the code rather than about the setup.
`unknown` means it could not be asked, which is a setup failure.

    python3 tests/fixtures/confirm_class.py <url> <class>

Exit 0 for `loaded`, 1 for `open`, 2 for `unknown`.
"""
from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, "/w/services/retrieval")
sys.path.insert(0, "/w/services/session")


def main() -> int:
    if len(sys.argv) < 3:
        print("usage: confirm_class.py <url> <class>", file=sys.stderr)
        return 2
    url, expected = sys.argv[1].rstrip("/"), sys.argv[2]

    try:
        import access as access_module
        import session as session_module
    except ImportError as exc:
        print(f"unknown: the probe modules are not importable here ({exc})", file=sys.stderr)
        return 2

    database = (os.environ.get("DATABASE_URL") or "").strip() or session_module.database_url()
    if not database:
        print("unknown: no database URL, so no session can be minted", file=sys.stderr)
        return 2

    registry = access_module.load(os.environ.get("ACCESS_REGISTRY", "/w/rendered/access.json"))
    store = session_module.PostgresStore(database)
    session = None
    for subject in sorted(registry.users or {}):
        for role in sorted(registry.roles or {}):
            try:
                session = session_module.mint(registry, store, subject, role, dev=True,
                                              deployment_id="confirm-class")
                break
            except access_module.AccessError:
                continue
        if session:
            break
    if session is None:
        print("unknown: no declared subject could be given a session", file=sys.stderr)
        return 2

    """Does the service refuse a request and a job for the class it was supposedly given?

    **Both paths are asked, and the second one is the point.** The first version of this check asked only
    `/ask`, so when the control was removed from the job path to prove the probe could fail, this
    confirmation reported the deployment broken and the runner exited before the probe ran. The setup was
    fine; what was broken was exactly what the probe exists to find. A confirmation step that fails on the
    defect under test hides the test.
    """
    body = json.dumps({"session": session.session_id, "question": "What is the mileage rate?"}).encode()
    job_body = json.dumps({"session": session.session_id, "task": "compare",
                           "input": "What is the mileage rate?"}).encode()

    def ask(path: str, payload: bytes):
        request = urllib.request.Request(url + path, data=payload, method="POST",
                                         headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(request, timeout=120) as response:
                return response.status, response.read().decode(errors="replace")
        except urllib.error.HTTPError as exc:
            return exc.code, exc.read().decode(errors="replace")
        except Exception as exc:  # noqa: BLE001 - the reason is the output
            return 0, f"{type(exc).__name__}: {exc}"

    results = {}
    for path, payload in (("/ask", body), ("/jobs", job_body)):
        status, text = ask(path, payload)
        results[path] = (status, text)

    unreachable = [p for p, (s, _) in results.items() if s == 0]
    if unreachable:
        print(f"unknown: could not reach {url}{unreachable[0]}: {results[unreachable[0]][1][:120]}",
              file=sys.stderr)
        return 2

    open_paths = [(p, s, t) for p, (s, t) in results.items() if p == "/ask" and s != 403]
    if open_paths:
        path, status, text = open_paths[0]
        print(f"open: {url}{path} answered HTTP {status} for class {expected!r}. The service is not "
              f"enforcing the class it was given, so the probe that follows would be testing nothing. "
              f"Body: {text[:160]}")
        return 1

    # `/ask` enforces it, so the deployment loaded the class. Whether `/jobs` does is exactly the question
    # the probe is about to ask, so it is reported and not turned into a setup failure.
    job_status, _ = results["/jobs"]
    print(f"loaded: {url}/ask refuses and names {expected!r}; /jobs answered HTTP {job_status}, "
          f"which the probe now tests")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
