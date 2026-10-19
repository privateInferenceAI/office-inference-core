"""The API's published contract, and the checker that refuses to publish a bad one.

**Why this file exists.** The client app lives in a separate repository and is built against
`docs/openapi.json`. That arrangement has one failure mode worth a test: the contract can look fine and
be wrong. It can list the paths and lose the field a screen branches on, and a file that only parses
passes every check anyone would think to write.

So this does two jobs.

**One: it proves the checker can fail.** A check that cannot fail is not a check, and this project has
already shipped a probe that printed the same three lines whatever the service did. Each mutation below
is a real way the contract could go wrong, applied to a real generated schema, and the checker has to
reject every one. Removing the control and watching the test fail is the point of the file.

**Two: it holds the declaration to the code.** `services/api/models.py` says what a client receives.
The handlers in `services/api/app.py` decide what it receives. Those are two places that can disagree,
and the disagreement is invisible until a client renders an empty field, so the payload keys are read
out of the source and compared against the declared models.

**What this file does not do** is talk to a deployment. That is deliberate: this runs on every commit,
and the live comparison is `scripts/fetch_openapi.py --check`, which is a probe because it needs a
service to fetch from.
"""
from __future__ import annotations

import ast
import copy
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import fetch_openapi  # noqa: E402

MODELS = ROOT / "services" / "api" / "models.py"
APP = ROOT / "services" / "api" / "app.py"
# The answer pipeline, which is where the payload a client receives is built. `/ask`, the stream and a job
# all go through it, so the answer's shape is decided once.
PIPELINE = ROOT / "services" / "api" / "answer.py"


@pytest.fixture
def generated_schema(api_app):
    """A function that returns a fresh deep copy of the app's schema, one per call.

    **The copy is not tidiness, and the first version of this file needed it.** FastAPI caches the schema
    it builds, so `app.openapi()` hands back the same nested objects every time. The mutation tests below
    delete keys from what they are given, and without a copy the first deletion persisted into every later
    test: the suite failed five ways, all of them this file's own fault, and a reader would have concluded
    the contract checker was broken. A test that changes its subject has to own its subject.

    **A function rather than the schema itself**, because one test checks that a fresh schema passes
    unmutated and others need one to damage. Handing out one shared object would make the order the tests
    run in decide their result, which is a defect that shows up as a flake.
    """
    def build() -> dict:
        return copy.deepcopy(api_app.openapi())
    return build


# --------------------------------------------------------------------------- the checker can fail
#
# Each case removes one control from a schema that otherwise passes, and asserts the checker names it.
# The list is the contract's own list of things that matter: the paths a client may call, the field it
# branches on, the statuses it must handle, the refusal wording, and the streaming events.


def test_the_unmutated_schema_passes(generated_schema):
    """Without this, every mutation below would pass for the wrong reason."""
    assert fetch_openapi.failure(generated_schema()) == ""


def test_a_missing_streaming_path_is_caught(generated_schema):
    schema = generated_schema()
    del schema["paths"]["/ask/stream"]
    assert "/ask/stream" in fetch_openapi.failure(schema)


def test_an_undeclared_extra_path_is_caught(generated_schema):
    """A debug route that reaches production is the mistake this project has already paid for once."""
    schema = generated_schema()
    schema["paths"]["/debug/state"] = {"get": {"responses": {}}}
    assert "/debug/state" in fetch_openapi.failure(schema)


def test_losing_the_field_a_screen_branches_on_is_caught(generated_schema):
    """`kind` is what tells an answer from a degradation. Lose it and the screen shows passages as prose.

    **Removed from `Answer` and not from `Degraded`, deliberately.** The first version of the checker
    searched the whole response for the word `kind`, so deleting it from one model passed because the
    other still had one. A check satisfied by a neighbouring model is not checking the model.
    """
    schema = generated_schema()
    del schema["components"]["schemas"]["Answer"]["properties"]["kind"]
    assert "kind" in fetch_openapi.failure(schema)


def test_losing_a_status_the_app_must_handle_is_caught(generated_schema):
    schema = generated_schema()
    del schema["paths"]["/ask"]["post"]["responses"]["403"]
    assert "403" in fetch_openapi.failure(schema)


def test_a_forgotten_redaction_count_on_the_stream_is_caught(generated_schema):
    """The C3 defect's shape: a control present on one endpoint and absent on the other."""
    schema = generated_schema()
    del schema["components"]["schemas"]["StreamDone"]["properties"]["redactions"]
    assert "redactions" in fetch_openapi.failure(schema)


def test_a_health_with_no_declared_body_is_caught(generated_schema):
    """This is what the service published before this change: three paths, empty response bodies."""
    schema = generated_schema()
    schema["paths"]["/health"]["get"]["responses"]["200"] = {"description": "Successful Response"}
    reason = fetch_openapi.failure(schema)
    assert "/health does not declare" in reason, (
        f"a health response with no body was accepted, or was rejected for a different reason: {reason!r}")


# --------------------------------------------------------------------------- the declaration and the code


def declared_fields(schema: dict, model_name: str) -> set[str]:
    """The properties Pydantic publishes for one model, from the schema rather than by importing it."""
    return set(schema["components"]["schemas"][model_name]["properties"])


def assigned_dict_keys(function: str, marker: str, path: Path | None = None) -> set[str]:
    """The string keys of the dict literal assigned to `marker` inside `function`, read from the source.

    Static rather than by running the handler, because running it needs a session, a store, a vector
    index and a model. The question here is narrow and textual: does the payload this function builds
    still carry the fields a client was promised.

    **`path` defaults to the handler and is passed explicitly for the answer.** The payload moved into the
    pipeline when `/ask` and a job started sharing one implementation, so a caller that assumed `app.py`
    would be asserting against a function that no longer exists there — which fails, but with a message
    about a missing variable rather than about a missing field.
    """
    tree = ast.parse((path or APP).read_text())
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == function:
            for inner in ast.walk(node):
                if not isinstance(inner, ast.Assign):
                    continue
                for target in inner.targets:
                    if isinstance(target, ast.Name) and target.id == marker:
                        if isinstance(inner.value, ast.Dict):
                            return {k.value for k in inner.value.keys
                                    if isinstance(k, ast.Constant) and isinstance(k.value, str)}
    raise AssertionError(f"no dict literal assigned to {marker} in {function}() in {path or APP}")


# Fields a model declares that a payload correctly does not carry.
#
# **This list has to stay nearly empty, and each entry needs a reason.** Its whole purpose is to stop the
# payload test from being the place where a missing field is explained away. `Answer.kind` is here because
# it is a discriminator with a null default: it is declared so a generated client can switch on it, and a
# real answer does not send it. An entry without that shape belongs in the handler, not here.
DECLARED_BUT_ABSENT = {
    ("Answer", "kind"),
}


def test_the_answer_payload_carries_every_field_the_model_declares(generated_schema):
    """The direction that breaks a client: a declared field the handler does not send.

    **Read from the pipeline rather than from the handler, because that is where the payload is built.**
    `/ask`, `/ask/stream` and a job all answer through `answer.py`, so the answer's shape is decided in one
    place. Asserting it here means a field added to the model and not to the pipeline fails, whichever
    endpoint a client happened to use.
    """
    declared = declared_fields(generated_schema(), "Answer")
    actual = assigned_dict_keys("finish", "payload", path=PIPELINE)
    # `request_id` is set on the payload after the literal, by the statement that follows it, so it is
    # declared and sent rather than undeclared. It is named here rather than exempted: exempting a field
    # that IS sent would hide the opposite mistake.
    actual = actual | {"request_id"}
    missing = {f for f in declared - actual if ("Answer", f) not in DECLARED_BUT_ABSENT}
    assert not missing, (
        f"models.Answer declares {sorted(missing)}, which the answer pipeline never sets. A client reading "
        "one of those gets a hole, and the schema said it would be there")
    extra = actual - declared
    assert not extra, (
        f"the pipeline sets {sorted(extra)}, which models.Answer does not declare, so it is undocumented "
        "and a client has no reason to expect it")


def test_the_stream_frames_carry_what_the_stream_models_declare(generated_schema):
    """The `done` frame against its model, which is the one a client reads timings out of."""
    declared = declared_fields(generated_schema(), "StreamDone")
    source = APP.read_text()
    start = source.index('frame("done"')
    segment = source[start:source.index("})", start)]
    for field in declared:
        assert f'"{field}"' in segment, (
            f"models.StreamDone declares '{field}' and the done frame does not carry it")


def test_the_generator_script_reaches_the_same_schema(api_app):
    """`scripts/fetch_openapi.py` is what CI and the box actually run, so it has to find the app too.

    Without this, the tests could pass against an app object they imported themselves while the script
    that writes the committed file failed on the same machine. That is the "confirm a tester is testing
    what you think" lesson, applied to a build step rather than a probe.
    """
    from_script = fetch_openapi.generate_offline()
    assert set(from_script["paths"]) == set(api_app.openapi()["paths"])
    assert "Answer" in from_script["components"]["schemas"]


# --------------------------------------------------------------------------- the audit columns the API sets
#
# **Why this is here.** The API mints a request id per call and passes it to `audit()`, and `audit()` did not
# put it on the audit tool's command line. The column stayed empty, so the id an answer carries could not find
# the row that recorded it. Every test passed. The activity probe found it on the box, by posting an answer
# and then looking up the row by the id that answer carried — and it reported zero rows, which looks exactly
# like "the row was never written".
#
# The general shape is the C3 lesson again: the flag existed, the value existed, and nothing connected them.
# So this asserts the connection for every column the API is given a value for, rather than for the one that
# was broken.


def test_every_audit_column_the_api_is_handed_reaches_the_tool():
    """A value that goes into `audit()`'s keyword arguments has to reach the command line.

    The mapping is read from the source: for each `extra.get("<name>")` the function checks, there must be a
    `--<name-with-dashes>` on the command. That is the property that failed, stated generally enough that the
    next column added inherits it.
    """
    import re

    source = APP.read_text()
    start = source.index("def audit(event: str")
    body = source[start:source.index("\ndef ", start + 10)]
    checked = set(re.findall(r'extra\.get\("([a-z_]+)"\)', body))
    passed = set(re.findall(r'"--([a-z-]+)"', body))
    # Underscores on the Python side, dashes on the command line.
    as_flags = {name.replace("_", "-") for name in checked}
    missing = as_flags - passed
    assert not missing, (
        f"audit() reads {sorted(missing)} and never passes the flag for it, so the column stays empty while "
        "the value is computed and thrown away. This is the defect that left request_id blank on every row")
    assert "request-id" in passed, "the request id specifically is not passed to the tool"


def test_a_tool_call_record_names_the_tool():
    """**The tool keyword every tool call passes was silently discarded.** The loop's audit wrapper calls
    `audit(..., tool=name)`, and `audit()` never read that keyword, so every `tool_call` row in the chain
    named no tool while the column and the CLI flag had both existed from the start. The test above cannot
    catch this, because it only checks the keywords `audit()` reads: a keyword it ignores is invisible to it.
    Found on 2026-10-19 while adding the row query, when its own calls could not be counted by tool.
    """
    source = APP.read_text()
    start = source.index("def audit(event: str")
    body = source[start:source.index("\ndef ", start + 10)]
    assert 'extra.get("tool")' in body, "audit() ignores the tool= keyword every tool call passes"
    assert '"--tool"' in body, "audit() reads the tool name and does not pass it to the tool"


def test_the_answer_and_its_row_share_one_identifier():
    """The id in the response and the id in the record are the same value, produced once.

    The lane claim and the record's entry are two statements about one event, and the identifier is the only
    thing that lets anything compare them. A test can only assert the agreement if both sides carry it.
    """
    pipeline = PIPELINE.read_text()
    assert "request_id=request_id" in pipeline, "the answer does not record the id it carries"
    assert '"request_id": request_id' in pipeline or 'payload["request_id"]' in pipeline, (
        "the answer does not carry the id it records")


def test_a_refusal_carries_the_id_of_the_row_it_wrote():
    """**The FORGED check measures this**, and it failed because the two halves were disconnected.

    The handler appends a request id to what it returns, so a client can name the refusal. The row written
    inside the pipeline did not have it, so the response and the record were two statements nothing could
    compare — the same shape as the request id never reaching the audit tool at all, one layer down.

    This reads both call sites rather than running one, because a refusal is cheap to reason about and expensive
    to arrange: the assertion is that the id is passed in and that every refusal path uses it.
    """
    pipeline = PIPELINE.read_text()
    app_source = APP.read_text()

    assert "request_id: str = \"\"" in pipeline, "prepare() does not accept a request id"
    assert 'audit=audit, endpoint="/ask", request_id=request_id' in app_source, (
        "the /ask handler mints a request id and does not pass it to the pipeline, so a refusal's row cannot "
        "be tied to the refusal")

    # Every refusal the pipeline raises names it: the authority claim and the no-permitted-lane one.
    refusals = pipeline.count('"request_id": request_id')
    assert refusals >= 2, f"only {refusals} refusal path(s) name the request id, expected at least 2"

    # And both refusals put it on a row.
    assert pipeline.count("request_id=request_id") >= 3, (
        "a refusal records its class but not the id, so the row cannot be found from the response")


def test_an_oversized_question_is_refused_by_size_and_not_by_session():
    """**The alternative was a bare 500 with no explanation.**

    There was no ceiling on a question, so a 204 KB paste reached the audit writer and the write failed with
    `Argument list too long` — the request that most needed a record was the one that could not have one, and
    the person saw `Internal Server Error`. An input limit is not a limitation to apologise for; stating it turns
    an unexplained failure into a sentence somebody can act on.

    The order is the part worth holding: the ceiling is checked **before** the session resolves, because a
    refusal that named a session for an oversized paste would tell an unauthenticated caller whether their
    session was valid.
    """
    source = APP.read_text()

    assert "MAX_QUESTION_CHARS" in source, "there is no ceiling on a question"
    # Both endpoints that answer questions.
    assert source.count("too_long = question_too_long(") == 2, (
        "the ceiling is checked on fewer than both question endpoints, so one path accepts a paste the other "
        "refuses")
    assert '"kind": "question_too_long"' in source, "the refusal has no kind for a client to act on"

    # The refusal comes before the session is resolved on the request path.
    ask_body = source[source.index("async def ask(request: Request)"):]
    ceiling_at = ask_body.index("too_long = question_too_long(")
    session_at = ask_body.index("_session_for(")
    assert ceiling_at < session_at, (
        "the size ceiling is checked after the session is resolved, so it reports on a session for a request "
        "that was never about permission")


def test_a_prompt_reaches_the_audit_tool_on_stdin():
    """A prompt on a command line is bounded by ARG_MAX, and a large question is exactly when the record is
    most needed. The payload stays an argument because it is metadata by design."""
    source = APP.read_text()
    assert 'command += ["--prompt", "-"]' in source, "the prompt is passed as a command-line argument"
    assert "input=stdin_text" in source, "nothing is written to the tool's stdin"


def test_a_record_that_cannot_be_written_stops_the_answer():
    """**A failed record was printed to stderr and the request continued.** A deployment could answer a question
    it had no record of, which contradicts the one claim the product rests on."""
    source = APP.read_text()
    failure = source[source.index("if result.returncode != 0:"):]
    failure = failure[:failure.index("\n\n")]
    assert "raise RuntimeError" in failure, "a failed audit write is still swallowed"
    assert "print(" not in failure, "the failure is still only printed"
