#!/usr/bin/env python3
"""Redaction before a frontier request leaves, proved against the deployed store and the real audit sink.

SEC-REDACT-001's verify line has two halves: a question containing an SSN reaches Bedrock with the pattern
replaced, and the audit shows the data class.

The first half is checkable here and the second half is checkable exactly. The frontier lane itself is Phase 4,
so there is no Bedrock call to intercept; what can be shown is the payload this module hands to the gateway,
which is the argument the gateway would send. That is the right thing to prove at this layer, because if the
redaction is wrong here the call is wrong whatever the gateway does.

The second half is proved against the real sink rather than described: this writes an audit event and reads it
back from Postgres, so what is verified is the record the deployment actually keeps.

Run from the repo root on the box:

    cd /opt/oi-core
    docker run --rm --network ai-net --user "$(id -u):$(id -g)" \\
      -v /opt/oi-core:/w:ro -v /tmp:/probe:ro -w /w \\
      --entrypoint python oi-core/ingestion:local /tmp/egress_acceptance.py
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

sys.path.insert(0, "/w/services/egress")
sys.path.insert(0, "/w/services/audit")


def load_env(path: Path = Path("/w/rendered/env")) -> dict[str, str]:
    try:
        text = path.read_text()
    except PermissionError:
        print(f"cannot read {path}: mode 600 owned by the operator. Add --user \"$(id -u):$(id -g)\"")
        return {}
    out: dict[str, str] = {}
    for line in text.splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, _, value = line.partition("=")
            out[key.strip()] = value.strip().strip('"').strip("'")
    return out


env = load_env()
# The deployment's own policy, so this proves the manifest decision rather than a value typed into a probe.
for key in ("FRONTIER_CLASSES", "EGRESS_REDACTION"):
    if key in env:
        os.environ[key] = env[key]
if env and not os.environ.get("DATABASE_URL"):
    from urllib.parse import quote
    os.environ["DATABASE_URL"] = (
        f"postgresql://{quote(env['POSTGRES_USER'], safe='')}:"
        f"{quote(env['POSTGRES_PASSWORD'], safe='')}@postgres:5432/{env['POSTGRES_DB']}")

print(f"the deployment permits on the frontier lane: {os.environ.get('FRONTIER_CLASSES')!r}")
print(f"redaction: {os.environ.get('EGRESS_REDACTION')!r}")

import egress  # noqa: E402

SSN = "123-45-6789"
CARD = "4111 1111 1111 1111"

# A request shaped like a real one: the user's question and, crucially, the retrieved passages. The passages
# are where a client's identifiers actually live, and a redactor that covers only the question does nothing.
REQUEST = {
    "model": "anthropic.claude-sonnet-4-5",
    "messages": [{"role": "user", "content": f"What does the file say about {SSN}?"}],
    "passages": [{"doc_id": "company/client-file.pdf", "page": 4,
                  "text": f"Account holder SSN {SSN}, card on file {CARD}, rate 145.00 per hour."}],
}


def main() -> int:
    problems: list[str] = []

    print()
    print("=== the request as assembled ===")
    print(f"  question: {REQUEST['messages'][0]['content']}")
    print(f"  passage : {REQUEST['passages'][0]['text']}")

    prepared = egress.prepare(REQUEST, "internal")
    print()
    print("=== RET half one: what would reach Bedrock ===")
    if not prepared.allowed:
        print(f"  REFUSED: {prepared.refusal}")
        problems.append(f"internal is permitted on this deployment but the request was refused: "
                        f"{prepared.refusal}")
    else:
        print(f"  question: {prepared.payload['messages'][0]['content']}")
        print(f"  passage : {prepared.payload['passages'][0]['text']}")
        sent = json.dumps(prepared.payload)
        for name, value in (("SSN", SSN), ("card", CARD)):
            if value in sent:
                problems.append(f"the {name} reached the payload that would be sent")
                print(f"  FAIL  the {name} is still there")
            else:
                print(f"  ok    the {name} is replaced")
        # The figures must survive. A redactor that removes the fee schedule's rates makes the product useless.
        if "145.00" not in sent:
            problems.append("a fee figure was redacted, which breaks every answer about money")
        else:
            print("  ok    the rate 145.00 survived, because a figure is not an identifier")

    print()
    print("=== and a class the deployment does not permit ===")
    regulated = egress.prepare(REQUEST, "regulated")
    print(f"  allowed: {regulated.allowed}")
    print(f"  reason : {regulated.refusal}")
    if regulated.allowed:
        problems.append("a regulated request was allowed on a lane that does not carry regulated")
    if regulated.payload is not None:
        problems.append("a refused request returned a payload, and a caller with both will send the wrong one")

    print()
    print("=== RET half two: the audit shows the data class, from the real sink ===")
    record = prepared.as_record()
    print(f"  the fields this produces: {json.dumps(record, sort_keys=True)}")

    # Written through the deployment's own CLI rather than by an insert this probe wrote for itself. The audit
    # module is the single writer path for the hash chain, and an event inserted around it would be a row the
    # chain does not cover, which is exactly the property the sink exists to have.
    request_id = f"egress-acceptance-{os.getpid()}"
    audit_cli = os.environ.get("AUDIT_CLI", "/w/services/audit/audit.py")
    command = [
        sys.executable, audit_cli, "record", "--dev",
        "--type", "model_call", "--actor", "egress-acceptance", "--actor-role", "system",
        "--data-class", "internal", "--outcome", "ok", "--model", REQUEST["model"],
        "--request-id", request_id,
        "--prompt", json.dumps(prepared.payload, sort_keys=True),
        "--payload", json.dumps(record, sort_keys=True),
    ]
    import subprocess
    written = subprocess.run(command, capture_output=True, text=True)
    print(f"  audit record exit: {written.returncode}")
    for line in (written.stdout + written.stderr).strip().splitlines():
        print(f"    {line}")
    if written.returncode != 0:
        problems.append(f"the audit CLI refused the event, so there is nothing in the sink to check: "
                        f"{written.stderr.strip()[:200]}")

    try:
        import psycopg
        connection = psycopg.connect(os.environ["DATABASE_URL"])
        with connection.cursor() as cur:
            cur.execute("""select data_class, outcome, model, prompt_sha256, payload
                           from audit_events where request_id = %s""", (request_id,))
            row = cur.fetchone()
        if not row:
            problems.append("the audit event could not be read back, so the record does not show anything")
        else:
            data_class, outcome, model, prompt_sha, payload = row
            print(f"  read back: data_class={data_class} outcome={outcome} model={model}")
            print(f"  prompt_sha256={prompt_sha[:16]}...")
            print(f"  payload={json.dumps(payload, sort_keys=True)}")
            if data_class != "internal":
                problems.append(f"the audit shows {data_class!r} rather than the data class of the request")
            if prompt_sha != prepared.prompt_sha256:
                problems.append("the recorded hash is not the hash of what was sent, so the record does not "
                                "prove what left")
            if SSN in json.dumps(payload):
                problems.append("the audit payload holds the SSN, so the record kept what it removed")
            if "ssn" not in (payload or {}).get("redactions", {}):
                problems.append("the record does not say that an SSN was removed")
        connection.close()
    except ImportError as exc:
        print(f"  psycopg is not in this image ({exc}), so the sink could not be read")
        problems.append("the audit half could not be checked because the driver is missing from this image")

    print()
    if problems:
        print(f"FAILED with {len(problems)} problem(s):")
        for problem in problems:
            print(f"  - {problem}")
        return 1
    print("PASSED: the identifiers are replaced before the request would leave, the figures survive, a class")
    print("the deployment does not permit is refused with no payload, and the audit records the class and what")
    print("was removed without recording the values")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
