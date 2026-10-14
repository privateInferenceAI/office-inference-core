#!/usr/bin/env python3
"""The API's contract, as a file, fetched from the service that serves it.

**Why this exists.** The client app lives in its own repository and talks to this API over HTTP and
nothing else. That only works if the interface is a thing both sides can hold rather than a set of
conventions in one repo's source. This script turns the running service's own schema into
`docs/openapi.json`, which is the artifact the app builds its client from.

**Two ways to produce it, and the difference matters.**

* **From the service it is deployed as** (the default). `GET /openapi.json` over the wire, from inside
  the network the API is on. This is what a client actually receives, so a route that only exists in a
  stale image or behind a typo shows up here.
* **From the app object** (`--offline`). Imports the FastAPI app and asks it for its schema. No
  deployment needed, which is what lets a test suite check the contract on every commit.

Both must agree, and `--check` is how that is enforced: it fetches over the wire, compares against the
committed file, and exits non-zero when they differ. **A contract file that nothing compares against is
a document, not a contract**, and this project has already paid once for a page that described a service
it was not describing any more.

**The assertions are the point.** A schema that parses but has lost `/ask/stream` would be fetched,
committed, and reported as success. So the expected paths, the response fields a client branches on, and
the presence of the refusal status codes are all checked here, and a missing one is a non-zero exit
rather than a smaller file.

    python3 scripts/fetch_openapi.py                      # fetch and write docs/openapi.json
    python3 scripts/fetch_openapi.py --check              # compare, change nothing, exit 1 on drift
    python3 scripts/fetch_openapi.py --offline --out /tmp/x.json
    python3 scripts/fetch_openapi.py --url http://api:8088   # from inside the deployment network
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_OUT = ROOT / "docs" / "openapi.json"

# The paths a client may call. A fourth route is a deliberate act, so it is added here in the same
# commit that adds it, and this list is what makes "the app can only call the documented surface"
# checkable rather than a promise.
EXPECTED_PATHS = {"/health", "/ask", "/ask/stream"}

# **The fields the client app branches on.** This is the list that keeps the two repositories honest:
# `kind` decides whether a payload is an answer or a degradation, `lane` is what the residency
# indicator reports, `redactions` is what the answer says was replaced, and `notice` is what a user
# is told when a trigger fired or was refused. A rename that reaches the app without reaching here is
# a screen that renders nothing, which is the failure this file exists to catch.
REQUIRED_IN_ASK_RESPONSE = ("kind", "lane", "notice", "redactions")

# The statuses the app must handle rather than treat as transport errors. 401 is no session, 403 is a
# refusal with a reason, 502 is retrieval or the gateway failing.
REQUIRED_STATUSES = {"401", "403", "502"}

# What the identifier and access roots look like when the service is asked. `deployment_id` is how a
# client knows which deployment answered, which is not decoration on a build we ship more than once.
REQUIRED_IN_HEALTH = ("status", "deployment_id")


def fetch(url: str, timeout: int = 30) -> dict:
    """The schema the service serves, over the wire.

    **`urllib`, not `curl`, and the first version shelled out to curl.** It ran on a workstation and
    failed inside the service's own image with "curl is not installed", which is where this has to run:
    the API is on an internal network with no published port, so the check is executed from a container
    beside it. Reaching a URL is something Python already does, and the project's probes use the same
    library, so there is one way this codebase makes an HTTP call rather than two.
    """
    import urllib.error
    import urllib.request

    base = url.rstrip("/")
    target = f"{base}/openapi.json"
    try:
        with urllib.request.urlopen(target, timeout=timeout) as response:
            body = response.read().decode()
    except urllib.error.HTTPError as exc:
        raise SystemExit(f"{target} answered HTTP {exc.code}. The service is up and refusing this path.")
    except urllib.error.URLError as exc:
        raise SystemExit(f"could not reach {target}: {exc.reason}")
    if not body.strip():
        raise SystemExit(f"{target} returned nothing. Is the service up?")
    try:
        return json.loads(body)
    except json.JSONDecodeError as exc:
        raise SystemExit(f"{target} did not return JSON: {exc}")


def generate_offline() -> dict:
    """The schema from the app object, with the imports the container provides and a bare host does not."""
    for package in ("api", "retrieval", "ingestion", "session", "gateway", "egress"):
        path = ROOT / "services" / package
        if str(path) not in sys.path:
            sys.path.insert(0, str(path))
    try:
        import app as app_module  # noqa: PLC0415 - the path has to be set before this import
    except ImportError as exc:
        raise SystemExit(
            f"could not import the API to generate the schema offline: {exc}\n"
            "This needs fastapi and sse-starlette. Run it in the API image, or use the default (fetch)."
        )
    return app_module.app.openapi()


def resolve(node, schema: dict, seen: frozenset[str] = frozenset()):
    """Every `$ref` in a node, followed.

    **The first version of this check searched the response text for a field name and was wrong about
    every model that used one.** A response model is published as `{"$ref": "#/components/schemas/..."}`
    with its fields defined once under `components`, so the fields a client branches on were not in the
    text being searched, and the check reported a correct contract as a failure. A contract checker that
    cannot see the contract is worse than none: it fails for the wrong reason, and the next person
    weakens it rather than fixing it.
    """
    if isinstance(node, dict):
        ref = node.get("$ref")
        if isinstance(ref, str) and ref.startswith("#/") and ref not in seen:
            target = schema
            for part in ref[2:].split("/"):
                target = target.get(part, {}) if isinstance(target, dict) else {}
            return resolve(target, schema, seen | {ref})
        return {k: resolve(v, schema, seen) for k, v in node.items()}
    if isinstance(node, list):
        return [resolve(v, schema, seen) for v in node]
    return node


def fields_in(node) -> set[str]:
    """Every property name mentioned anywhere inside an already-resolved schema node."""
    found: set[str] = set()
    if isinstance(node, dict):
        properties = node.get("properties")
        if isinstance(properties, dict):
            found.update(properties)
        for value in node.values():
            found |= fields_in(value)
    elif isinstance(node, list):
        for value in node:
            found |= fields_in(value)
    return found


def failure(schema: dict) -> str:
    """The reason this schema is not the contract the app was promised, or an empty string."""
    paths = set(schema.get("paths", {}))
    missing = EXPECTED_PATHS - paths
    if missing:
        return f"the service does not serve {sorted(missing)}"
    extra = paths - EXPECTED_PATHS
    if extra:
        # Not fatal on its own, but it is how a debug route ends up in production. The project has
        # paid for a published port once already, and this is the same mistake in a smaller size.
        return (f"the service serves {sorted(extra)}, which is not in this script's expected set. "
                "Add it here in the same commit, or remove it before this becomes a contract")

    ask = schema["paths"].get("/ask", {}).get("post", {})
    if not ask:
        return "/ask is not a POST"
    # Resolved, so a field declared once under `components` counts the same as one written inline.
    ask_fields = fields_in(resolve(ask, schema))
    for field in REQUIRED_IN_ASK_RESPONSE:
        if field not in ask_fields:
            return f"no response schema on /ask mentions '{field}', which the client branches on"
    # **`kind` has to be on both shapes, not merely somewhere in the response.** An answer and a
    # degradation share the 200 status and look alike otherwise, so the discriminator is the mechanism a
    # client uses to tell them apart. The first version of this check searched the whole response for the
    # word, which passed with the field deleted from `Answer`, because `Degraded` still had one. A check
    # satisfied by a neighbouring model is not checking the model.
    schemas = schema.get("components", {}).get("schemas", {})
    for shape in ("Answer", "Degraded"):
        properties = (schemas.get(shape) or {}).get("properties") or {}
        if "kind" not in properties:
            return (f"the {shape} model declares no `kind`, so a client cannot tell an answer from a "
                    "degradation except by guessing which fields are present")
    for status in REQUIRED_STATUSES:
        if status not in ask.get("responses", {}):
            return f"/ask declares no {status} response, and the app has to handle one"
    if "error" not in fields_in(resolve(ask["responses"].get("403", {}), schema)):
        return "/ask's 403 declares no error field, so a client cannot show a refusal's wording"

    stream = schema["paths"].get("/ask/stream", {}).get("post", {})
    if not stream:
        return "/ask/stream is not a POST"
    # **The streaming path is checked for the same facts as the JSON one, not fewer.** C3 shipped a
    # redaction that existed on one endpoint and not the other and every probe passed, because each probe
    # tested the endpoint its author had in mind. A contract check that read only /ask would repeat it.
    stream_fields = fields_in(resolve(stream.get("responses", {}), schema))
    for field in ("redactions", "text", "citations"):
        if field not in stream_fields:
            return f"/ask/stream's declared events do not carry '{field}'"
    described = stream.get("responses", {}).get("200", {}).get("description") or ""
    for event in ("start", "delta", "error", "degraded", "done"):
        if event not in described:
            return f"/ask/stream declares no '{event}' event, and a client has to handle every one"

    health = schema["paths"].get("/health", {}).get("get", {})
    if not health:
        return "/health is not a GET"
    health_fields = fields_in(resolve(health.get("responses", {}), schema))
    for field in REQUIRED_IN_HEALTH:
        if field not in health_fields:
            return (f"/health does not declare '{field}' as a response field. FastAPI needs a declared "
                    "response model for this, rather than only a returned dictionary")
    return ""


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--url", default=os.environ.get("OI_API_URL", "http://api:8088"),
                       help="where the API is, from wherever this runs (default http://api:8088)")
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--offline", action="store_true",
                       help="generate from the app object instead of fetching from a service")
    parser.add_argument("--check", action="store_true",
                       help="compare the service against the committed file, change nothing")
    args = parser.parse_args()

    schema = generate_offline() if args.offline else fetch(args.url)
    reason = failure(schema)
    if reason:
        print(f"FAIL  {reason}", file=sys.stderr)
        return 1

    rendered = json.dumps(schema, indent=2, sort_keys=True) + "\n"
    source = "the app object" if args.offline else args.url
    paths = sorted(schema.get("paths", {}))

    if args.check:
        if not args.out.exists():
            print(f"FAIL  {args.out} does not exist. Run without --check to write it.", file=sys.stderr)
            return 1
        if args.out.read_text() != rendered:
            print(f"FAIL  {args.out} differs from what {source} serves. "
                  f"The app is built against the file, so this is a client that will call the wrong thing.",
                  file=sys.stderr)
            print(f"      paths now: {paths}", file=sys.stderr)
            return 1
        print(f"OK    {args.out} matches {source}  ({len(paths)} path(s): {', '.join(paths)})")
        return 0

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(rendered)
    try:
        shown = args.out.resolve().relative_to(ROOT)
    except ValueError:
        # A test or a probe writes somewhere else on purpose, and that is not a failure to report as one.
        shown = args.out
    print(f"wrote {shown}  from {source}")
    print(f"  openapi {schema.get('openapi')}  {len(paths)} path(s): {', '.join(paths)}")
    print(f"  {len(rendered.splitlines())} lines, {len(rendered)} bytes")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
