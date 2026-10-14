"""What the assistant API promises a client, written down in a form a machine can read.

**Why these exist.** The client app lives in a separate repository and reaches this API over HTTP and
nothing else. That works only if the interface is a thing both sides hold. FastAPI publishes an OpenAPI
document at `/openapi.json`, and before these models existed that document listed three paths with **no
response bodies at all** — every response was an empty schema. A file like that tells a client which URLs
exist and nothing about what comes back, so a second repository would have been built against the manual's
prose and would have broken the first time a field moved.

**These are declarations, not the serialisation path.** They are attached with `responses={...}` so they
appear in the OpenAPI document. Nothing here is used as `response_model`, deliberately: a response model
filters and re-validates the payload on the way out, and this API already has four response shapes that
differ by status code and a `kind` field. Re-validating them would be a second place for the wire format
to be decided, and the first place is the code that has been tested against a live deployment. So a
change here is a documentation change, and `tests/test_api_contract.py` fails when the declaration and
the code stop agreeing.

**The field list is the interesting part.** `kind` decides whether a payload is an answer or a
degradation, and a client that renders one as the other shows retrieved passages as though a model had
written them. `lane` is what the residency indicator reports and has to match the audit row. `notice` is
what the user is told when a trigger fired or was refused. `redactions` is what the answer says was
replaced. Those four are the ones a screen branches on, and they are the four `scripts/fetch_openapi.py`
refuses to publish a contract without.
"""
from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, Field

# --------------------------------------------------------------------------- pieces


class Citation(BaseModel):
    """One passage the answer could cite. `index` is what the `[n]` markers in the answer refer to."""

    index: int
    doc_id: str = Field(description="the document, relative to the deployment's documents root")
    acl: str = Field(description="the access label the passage carries; the session's roots must include it")
    page_start: int
    page_end: int


class UnsupportedClaim(BaseModel):
    """A sentence no retrieved passage backs. Marked rather than dropped, so a client can show it."""

    text: str
    reason: str
    detail: str = ""


class Passage(BaseModel):
    """A retrieved passage in a degraded answer, with an excerpt so it can be read without another call."""

    index: int
    doc_id: str
    acl: str
    page_start: int
    page_end: int
    excerpt: str


class BudgetReport(BaseModel):
    """What the deadline did, recorded on every degraded answer.

    **`threshold` is null when no threshold tripped**, and that is the honest value: a budget refusal is
    not a timeout, and recording one as a threshold would blur the three the design distinguishes.
    """

    elapsed_ms: int
    remaining_ms: int
    first_token_ms: Optional[int] = None
    first_token_seconds: float
    stall_seconds: float
    total_seconds: float
    reserve_seconds: float
    taxonomy_version: str


class Redactions(BaseModel):
    """Kinds and counts of what redaction removed, from the answer and from the question.

    A free-form object rather than fixed fields, because the pattern set is a list in
    `services/egress/redaction.py` that grows. A client should render whatever keys it is given.
    """

    model_config = {"extra": "allow"}


# --------------------------------------------------------------------------- /ask


class Answer(BaseModel):
    """The answer to a question, with what it was built from and what it cost.

    **`kind` is declared on both this and `Degraded`, and that is a decision rather than symmetry.** The
    two share the 200 status and look alike otherwise, so a client has to be able to tell them apart
    without guessing from which fields are present. The service sets `kind` on a degraded payload and
    does not set it here, so the default is what a runtime payload will not carry: a client should
    branch on `kind == "degraded"` being absent-or-not, and a generated client built from this schema
    gets a discriminator it can switch on either way.
    """

    kind: Optional[Literal["answer"]] = Field(
        default=None, description="absent on a real answer; `degraded` on a degradation")
    request_id: Optional[str] = Field(
        default=None,
        description="**the id this answer is recorded under.** A client that reports a lane — 'left the "
                    "building' — needs to name the row that proves it, and without this the indicator's "
                    "claim and the record's entry are two statements nothing can compare")
    answer: str
    model: str = Field(description="the ROLE, as the manifest names it, not the model that ran")
    resolved_model: str = Field(
        description="the provider model behind that role. A client reading only `model` cannot see a "
                    "manifest swap take effect")
    role: str
    roots: list[str]
    citations: list[Citation]
    supported: list[str] = Field(description="sentences a retrieved passage backs")
    unsupported: list[UnsupportedClaim] = Field(description="sentences it does not, with the reason")
    redactions: Redactions
    notice: str = Field(description="what the user is told about routing, empty when there is nothing to say")
    lane: Literal["frontier", "local", "none"]
    escalated: bool
    trigger: str = Field(description="which trigger moved it, by name, or empty when none fired")
    reason: str
    denied: bool = Field(description="whether passages outside the role matched")
    disclosure: str = Field(description="what this deployment's policy tells a denied user")


class Refusal(BaseModel):
    """A refusal. `kind` is the machine-readable reason; the human wording is in `error`.

    **Both are always present**, because the app has to branch on the kind and the person has to read a
    sentence. The 403 wording is AIP-211's position: a permission refusal says the permission was denied
    or the resource might not exist, which is why it does not simply say "you may not read this".
    """

    error: str
    kind: Literal[
        "no_session",               # absent, expired or revoked; one answer on purpose
        "refused_authority_claim",  # the request supplied its own role
        "no_permitted_lane",        # the data boundary permits no lane that can answer
        "retrieval_failed",         # the store or the embedding endpoint failed
    ]


class Degraded(BaseModel):
    """The answer when the model will not answer, at HTTP 200.

    **`kind` is the field a client must branch on.** An answer and a degradation look alike otherwise,
    and a client that renders one as the other shows passages as though the model had written them.
    `notice` is absent here and deliberately: the manual records that a degraded payload carries no
    notice, which is why the interface probe reads a correct degradation as a silent downgrade.
    """

    kind: Literal["degraded"] = "degraded"
    reason: str = Field(description="throttled, timeout, budget, unclassified, and the like")
    lane: Literal["frontier", "local", "none"]
    detail: str
    passages: list[Passage]
    attempts: int
    threshold: Optional[str] = Field(
        default=None,
        description="which of the three thresholds tripped: first_token, stall, or total")
    partial_output: bool
    budget: BudgetReport


class NoSource(BaseModel):
    """Retrieval matched nothing the session may read. HTTP 200, because the request succeeded."""

    answer: str
    citations: list[Citation]
    kind: Literal["no_source"] = "no_source"
    role: str
    roots: list[str]


# --------------------------------------------------------------------------- /ask/stream
#
# **Declared as a union, and that mirrors a real trap.** The endpoint answers with Server-Sent Events,
# and a client that handles only `error` and `message` will not see a refusal: a refusal is a JSON 403
# before the first event, not an SSE error event. These models describe the payload of each event's
# `data` field for the events that do arrive.


class StreamStart(BaseModel):
    """Before the model is called. Carries the citations, so a client can show sources immediately."""

    model: str
    role: str
    roots: list[str]
    citations: list[Citation]


class StreamDelta(BaseModel):
    """A piece of the answer, **already redacted**. The redaction happens before the frame is emitted."""

    text: str


class StreamError(BaseModel):
    """Terminal, because the status code is already spent by the time one of these can be sent.

    **`partial_output` is optional because the frames are not uniform.** A timeout reports whether text
    had already reached the client; a mid-stream failure reports only its kind. Declaring it as always
    present would have been a contract the service does not honour, which is the class of mistake this
    whole file exists to stop.
    """

    kind: Literal["stream_timeout", "stream_failed", "redaction_failed"]
    detail: str = ""
    threshold: Optional[str] = None
    partial_output: Optional[bool] = None


class StreamDegraded(BaseModel):
    """The model could not answer at all. A client that branches only on the events above renders this
    as nothing, which reads to a user as an empty answer."""

    kind: Literal["degraded"] = "degraded"
    reason: str
    lane: str
    detail: str
    passages: list[Passage]
    attempts: int
    threshold: Optional[str] = None
    partial_output: bool
    budget: BudgetReport


class StreamDone(BaseModel):
    """The last event. `ttft_ms` is the caller's wait with retrieval included; `model_first_ms` is the
    model's own share. The two were once conflated."""

    deltas: int
    redactions: Redactions
    retrieval_ms: int
    ttft_ms: Optional[int] = None
    model_first_ms: Optional[int] = None
    total_ms: int


# --------------------------------------------------------------------------- /health


class Health(BaseModel):
    """Liveness, and what the process actually loaded.

    `deployment_id` is how the app knows which deployment answered, which is not decoration on a build
    that ships more than once. `status` is `loading` until the registry is read, and a client should say
    so rather than treat it as an error.
    """

    status: Literal["ok", "loading"]
    deployment_id: str
    registry_declared: Optional[bool] = None
    default_model_role: str


# --------------------------------------------------------------------------- jobs


class JobStep(BaseModel):
    """One attempt. **`failure` is typed rather than prose**, because an operator counts it and a client
    branches on it. 'It failed' is neither."""

    step_id: str
    attempt: int
    name: str
    state: Literal["running", "succeeded", "failed"]
    failure: Optional[dict] = Field(
        default=None,
        description="kind, detail, and whether retrying could help. Absent on an attempt that worked")
    lane: Optional[str] = None
    model: Optional[str] = None
    duration_ms: Optional[int] = None


class Job(BaseModel):
    """Work with an identity and a history.

    **`terminal` is told to the client rather than left to be inferred.** A screen that guesses whether a
    job has finished gets it wrong exactly once, and then nobody trusts the spinner.
    """

    job_id: str
    state: Literal["queued", "running", "paused", "awaiting_approval", "succeeded", "failed",
                   "cancelled"]
    task: str
    input: str
    attempts: int
    steps: list[JobStep]
    terminal: bool
    time_budget_s: int
    attempt_budget: int
    submitted_by: str
    role: str
    data_class: str
    started_at: Optional[str] = None
    finished_at: Optional[str] = None
    duration_ms: Optional[int] = None
    outcome_reason: Optional[str] = Field(
        default=None,
        description="why it ended, when it did not succeed. A typed reason such as attempts_exhausted")
    result: Optional[dict] = Field(
        default=None,
        description="the answer, when a step produced one. A client that comes back gets the result rather "
                    "than only the history of producing it")


class Task(BaseModel):
    """A thing this deployment can be asked to do.

    **Read from the manifest.** A deployment with no agentic pack declares none, and the interface then
    offers none, which is the honest outcome rather than a menu of things that cannot run.
    """

    id: str
    label: str
    description: str = ""
    tier: str = Field(default="", description="the model role that handles it, from the manifest")
    touches: list[str] = Field(default_factory=list)
    needs_approval: bool = False
