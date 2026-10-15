#!/usr/bin/env python3
"""Streaming, through the interface the manual names.

Four rights, each with the wrong path it rules out beside it. **Every call goes over HTTP to the running
service and every assertion is over the raw bytes the client received**, which is the whole point: an answer
assembled from a stream is exactly what an audit-only control redacts, so a test that reassembles first would
pass on the design this one exists to replace.

    | right       | the act                                       | the wrong path it rules out            |
    |-------------|-----------------------------------------------|----------------------------------------|
    | TTFT        | stream a long answer, timestamp every frame   | a response that arrives whole          |
    | INCREMENTAL | count the frames and their spacing            | one frame containing the whole answer  |
    | REDACTED    | ask a question whose answer holds a canary    | PII reaching the browser mid-stream    |
    | END-TO-END  | ask a normal question                         | a stream that lost the answer or its   |
    |             |                                               | citations                              |

Run from the deployment root on the box:

    cd /opt/oi-core
    alias oi='docker compose -f deploy/office/compose/docker-compose.yml --env-file rendered/env'
    oi build api && oi up -d api
    docker run --rm --network ai-net --user "$(id -u):$(id -g)" \
      -v /opt/oi-core:/w -w /w --env-file rendered/env \
      --entrypoint python oi-core/session:local /w/tests/fixtures/stream_probe.py

The canary document is `fixtures/corpus/canary-pii.md`, which carries two reserved social security numbers
that belong to nobody. It has to be ingested for the REDACTED right to mean anything: run
`docker exec ingestion python3 /app/ingest.py --documents /documents --json` first. If it is not in the index
the right reports that rather than passing.

Exit codes: 0 when every right held, 1 when one did not, 2 when nothing could be tested.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

W = Path("/w")
sys.path.insert(0, str(W / "services" / "retrieval"))
sys.path.insert(0, str(W / "services" / "session"))

import access as access_module  # noqa: E402
import session as session_module  # noqa: E402

API = os.environ.get("OI_API_URL", "http://api:8088")

# The two reserved numbers the canary document carries. Neither belongs to a person.
CANARIES = ("219-09-9999", "078-05-1120")


def database_url() -> str:
    url = (os.environ.get("DATABASE_URL") or "").strip()
    if url:
        return url
    user = os.environ.get("POSTGRES_USER", "")
    password = os.environ.get("POSTGRES_PASSWORD", "")
    database = os.environ.get("POSTGRES_DB", "")
    if not (user and database):
        return ""
    return "postgresql://" + user + ":" + password + "@postgres:5432/" + database


def stream(session_id: str, question: str, timeout: int = 300):
    """One streamed request. Returns the frames, their arrival times, and the raw bytes.

    **The raw bytes are kept rather than parsed.** The assertion is about what the client received, so it is
    made against the bytes and not against a reassembled string.
    """
    body = json.dumps({"session": session_id, "question": question}).encode()
    request = urllib.request.Request(API + "/ask/stream", data=body, method="POST",
                                     headers={"Content-Type": "application/json"})
    raw = b""
    arrivals = []
    started = time.monotonic()
    with urllib.request.urlopen(request, timeout=timeout) as response:
        headers = {k.lower(): v for k, v in response.headers.items()}
        for chunk in response:
            raw += chunk
            arrivals.append(time.monotonic() - started)
    text = raw.decode("utf-8", errors="replace")
    deltas = [json.loads(line[6:].strip()) for line in text.splitlines()
              if line.startswith("data:") and line[6:].strip().startswith("{")]
    return {
        "raw": raw, "text": text, "deltas": deltas, "arrivals": arrivals,
        "total": time.monotonic() - started, "headers": headers,
    }


class Ledger:
    def __init__(self) -> None:
        self.rows = []

    def record(self, right: str, held: bool, observed: str, wrong_path: str) -> None:
        self.rows.append((right, held, observed, wrong_path))
        print(("ok   " if held else "FAIL ") + right.ljust(11) + " " + observed)
        print(" " * 12 + "if it were absent: " + wrong_path)

    def skipped(self, right: str, why: str) -> None:
        print("skip " + right.ljust(11) + " " + why)

    def failures(self) -> int:
        return sum(1 for _, held, _, _ in self.rows if not held)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--subject", default="probe-staff@officeinference.com")
    args = parser.parse_args()

    ledger = Ledger()
    if not database_url():
        print("FAILED with 1 problem: no database credentials, so no session can be established",
              file=sys.stderr)
        return 2

    registry = access_module.load(os.environ.get("ACCESS_REGISTRY", str(W / "rendered" / "access.json")))
    store = session_module.PostgresStore(database_url())
    role = next((n for n, r in sorted(registry.roles.items()) if not r), sorted(registry.roles)[0])
    session = session_module.mint(registry, store, args.subject, role, dev=True, deployment_id="probe")

    # ------------------------------------------------------------------ END-TO-END, which also proves the citations
    result = stream(session.credential, "What is the mileage rate for reimbursement?")
    started = next((d for d in result["deltas"] if "citations" in d), {})
    answer = "".join(d.get("text", "") for d in result["deltas"])
    cites = started.get("citations") or []
    ledger.record("END-TO-END", bool(answer) and bool(cites),
                  "%d frame(s), %d citation(s), answer starts %r"
                  % (len(result["deltas"]), len(cites), answer[:60]),
                  "a stream that lost the answer or its citations is not the same capability")

    # ------------------------------------------------------------------ TTFT
    long_q = "Summarise what the fee schedule says about travel and expenses, in detail."
    result = stream(session.credential, long_q)
    first = next((d for d in result["deltas"] if d.get("text")), None)
    done = next((d for d in result["deltas"] if "total_ms" in d), {})
    # **`ttft_ms` rather than `first_token_ms`, and the rename is the point.** The first version of this
    # asserted on a field the API had while measuring from the wrong clock, and this probe kept asserting on
    # the old name after the API started reporting the caller's wait. It failed with `first token at Nonems`,
    # which is a probe testing a field name rather than the behaviour it names.
    server_first = done.get("ttft_ms")
    total_ms = done.get("total_ms") or 0
    held = bool(first) and server_first is not None and total_ms > 0 and server_first < total_ms
    ledger.record("TTFT", held,
                  "first token at %sms of %sms total, %d frame(s)" % (server_first, total_ms, len(result["deltas"])),
                  "a response that arrives whole has a first token at the total time, which is not streaming")

    # ------------------------------------------------------------------ INCREMENTAL
    distinct = len({round(a, 3) for a in result["arrivals"]})
    spread = (result["arrivals"][-1] - result["arrivals"][0]) if len(result["arrivals"]) > 1 else 0.0
    buffering = result["headers"].get("x-accel-buffering")
    incremental = len(result["deltas"]) > 1 and distinct > 1
    ledger.record("INCREMENTAL", incremental,
                  "%d frame(s) at %d distinct arrival times, spread %.2fs, X-Accel-Buffering=%r"
                  % (len(result["deltas"]), distinct, spread, buffering),
                  "one frame containing the whole answer means something upstream is buffering")

    # ------------------------------------------------------------------ REDACTED
    result = stream(session.credential, "What is the primary contact reference for the Acme engagement?")
    text = result["text"]
    present = [c for c in CANARIES if c in text]
    # The canary has to be in the INDEX for this to mean anything, so the passage is checked for first. A
    # question that never retrieved it proves nothing, and passing on that would be the empty-index failure
    # this project has already made once.
    done = next((d for d in result["deltas"] if "redactions" in d), {})
    if not done.get("redactions"):
        ledger.record("REDACTED", False,
                      "the stream reported no redactions, so either the canary is not in the index or the "
                      "redactor did not run"
                      if present else
                      "the canary was not retrieved, so this proves nothing. Ingest "
                      "fixtures/corpus/canary-pii.md first: docker exec ingestion python3 /app/ingest.py "
                      "--documents /documents --json",
                      "an index without the canary makes this right pass for the wrong reason")
    else:
        ledger.record("REDACTED", not present,
                      "canary in any frame: %s; the stream reported redactions %s"
                      % (present or "no", done.get("redactions")),
                      "a value that reaches the browser mid-stream cannot be recalled, so the control has to "
                      "be before the frame rather than after the answer")

    store.close()
    print()
    failures = ledger.failures()
    if failures:
        print("FAILED with " + str(failures) + " problem(s)")
        return 1
    print("PASSED: " + str(len(ledger.rows)) + " right(s) held against the streaming service")
    return 0


if __name__ == "__main__":
    sys.exit(main())