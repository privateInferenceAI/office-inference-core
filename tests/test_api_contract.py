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
