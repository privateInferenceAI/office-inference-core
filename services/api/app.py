"""The assistant API: one surface, sessions from tokens, and a cited answer.

**This is the shape the phase's acceptance describes**: every capability over one API with no user interface.
It is deliberately plain, because the three steps after it each add one thing to it, and a step that adds
streaming or routing to a service that has neither is a step that cannot be tested on its own.

Four decisions are built into this file, and each one came from running something rather than from reading:

* **A role comes from the session and never from the request.** The token resolves to a row; the row holds the
  role. A request that supplies its own role is refused and the refusal is written to the chain before the
  error returns, which `4.1` built and this wires in.
* **The model is chosen here, from the manifest, and never by the caller.** The gateway's own model allowlist
  turned out not to be enforced on the signed-statement path, so the choice lives in the one process that
  knows the session and the role. A request cannot name a model any more than it can name a role.
* **The call to the model is authenticated with a short-lived signed statement**, minted per request from a key
  this process holds and the gateway verifies. There is no long-lived model credential here to steal.
* **The answer is checked against what was retrieved before it is returned.** A sentence that no retrieved
  passage supports is marked rather than dropped, because a reply that reads cleanly and lost the wrong part
  is the failure this whole layer exists to prevent.

**What this step deliberately does not do.** It does not stream (that is `4.3`), it has no timeout budget or
degraded answer (that is `4.4`), and its routing is a read of the manifest rather than the three mechanical
triggers (that is `4.5`). Naming them here is how a reader knows the absence is a plan.
"""
from __future__ import annotations

import json
import os
import time
import subprocess
import sys
import uuid
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from sse_starlette.sse import EventSourceResponse

# This service's own directory, because the code is imported as modules rather than run as a package. Without
# it `import streaming` finds nothing, which is how the first deploy of the streaming step failed: the container
# restarted in a loop while the path was correct in the checkout and wrong in the image.
sys.path.insert(0, str(Path(__file__).resolve().parent))

# The shared services: retrieval for the search and the citation checks, ingestion for the endpoints Qdrant and
# TEI are reached through, session for who is asking, gateway for how a model call is authenticated, and egress
# for the redaction the stream applies.
sys.path.insert(0, "/app/services/retrieval")
sys.path.insert(0, "/app/services/ingestion")
sys.path.insert(0, "/app/services/session")
sys.path.insert(0, "/app/services/gateway")
sys.path.insert(0, "/app/services/egress")

import access as access_module  # noqa: E402
import assertion  # noqa: E402
import citations  # noqa: E402
import documents as documents_module  # noqa: E402
import redaction  # noqa: E402
import ingest  # noqa: E402
import retriever  # noqa: E402
import session as session_module  # noqa: E402
import budget as budget_module  # noqa: E402
import routing  # noqa: E402
import streaming  # noqa: E402

# **What a client is promised, declared rather than described.** These are attached with
# `responses={...}` so they reach `/openapi.json`, which is the artifact the client app is built from.
# They are deliberately NOT `response_model`: that would re-validate and filter every payload on the way
# out, making a second place where the wire format is decided, and the first place is the code that has
# been tested against a live deployment. `tests/test_api_contract.py` fails when a declaration and the
# code that produces the payload stop agreeing.
import models as contract  # noqa: E402

# **The pipeline every path that answers a question goes through.** `/ask`, `/ask/stream` and a job all
# answer questions, and the guards, their order, and the recording live in `answer.py` rather than in each
# handler. C3 shipped two defects whose shape was two implementations of one thing.
import answer as answer_module  # noqa: E402

# **Work with an identity and a history.** A job is not a longer answer: it is a record that can be
# watched, stopped, and read afterwards, and the state machine that keeps that honest is in this module.
import jobs as jobs_module  # noqa: E402

# **Reading the chain over the API, scoped to the session.** The one capability that could quietly widen
# access to everything the deployment has ever done, so its rule lives on its own with the argument written
# down rather than being a where-clause inside a handler.
import activity as activity_module  # noqa: E402

GATEWAY_URL = os.environ.get("OI_GATEWAY_URL", "http://litellm:4000")
DEPLOYMENT_ID = os.environ.get("DEPLOYMENT_ID", "unknown")
AUDIT_TOOL = os.environ.get("AUDIT_TOOL", "/app/audit.py")
KEY_PATH = os.environ.get("OI_ASSERTION_KEY", "/app/rendered/assertion.key")

# Which model role answers a question, per API role. The renderer writes one variable per role from the
# manifest's `models` block, so a deployment changes the model by changing the manifest and re-rendering.
# **This is the seam `INF-ROLE-001` is proved through**: the answer to "which model answered" comes from here
# and nowhere else.
# **A ceiling on the question, because the alternative was a bare 500.** There was no limit, so a 204 KB paste
# reached the audit writer and the write failed, which surfaced to the person as `Internal Server Error` with no
# explanation. An input limit is not a limitation to apologise for: every request has one, and stating it turns
# an unexplained failure into a sentence somebody can act on. 32,000 characters is roughly 8,000 tokens, which
# leaves room in a 32k-token window for the retrieved passages that share it.
MAX_QUESTION_CHARS = int(os.environ.get("OI_MAX_QUESTION_CHARS", "32000"))


def question_too_long(question: str):
    """The refusal for a question past the ceiling, or None.

    Returns the payload rather than raising so both handlers and the job path can use one wording, which is the
    same reason the answer pipeline is shared: two implementations of one refusal is how they drift apart.
    """
    if len(question) <= MAX_QUESTION_CHARS:
        return None
    return {
        "error": f"the question is {len(question):,} characters and this deployment accepts "
                 f"{MAX_QUESTION_CHARS:,}. Send the part you need answered, or point at the document instead "
                 f"of pasting it.",
        "kind": "question_too_long",
    }


DEFAULT_MODEL_ROLE = os.environ.get("OI_DEFAULT_MODEL_ROLE", "analyst")

# How many passages the answer is built from.
PASSAGES = int(os.environ.get("OI_PASSAGES", "6"))

STATE: dict = {}


def resolved_model(role: str) -> str:
    """The provider model a role points at, read from the manifest.

    **Why the answer carries both.** A role is a name in a manifest; a model is what actually ran. An
    answer that reports only the role cannot show that a manifest change took effect, which is the
    evidence INF-ROLE-001 asks for, and an auditor reading the row wants the model rather than the alias.
    """
    models = (STATE.get("manifest") or {}).get("models") or {}
    entry = models.get(role) or {}
    return str(entry.get("frontier") or entry.get("local") or role)


def role_lane(role: str) -> str:
    """Where the model this role points at actually runs, from the manifest.

    **Deployment-wide was wrong, and the panel proved it.** `local_lane_available()` answers whether ANY role
    declares a local model, and the routing used that as the default lane. On a hybrid manifest where the
    'analyst' role is frontier-only and other roles are local, it returned 'local' for a request that the
    gateway answered with a REMOTE model — so the gate was never consulted and the record claimed local
    residency while the content left the building. No shipped manifest has that shape, which is why it was
    latent, but the schema allows it and a client would eventually build it.
    """
    models = (STATE.get("manifest") or {}).get("models") or {}
    entry = models.get(role) or {}
    return "local" if entry.get("local") else "frontier"

def frontier_model_for(role: str) -> str:
    """The separate frontier model this deployment exposes for a role, or empty when there is none.

    **Empty is information, not a failure.** The renderer publishes `<role>-frontier` only when a role has
    both a local and a remote entry, because that is when the gateway names the remote one differently.
    Where it is empty the deployment has a single model, and the caller says so rather than claiming a
    stronger model answered. That claim was false on the cloud deployment, where both lanes resolve to the
    same model. Found by the C3 panel.
    """
    variable = "OI_MODEL_" + role.upper().replace("-", "_") + "_FRONTIER"
    return (os.environ.get(variable) or "").strip()

def local_lane_available() -> bool:
    """Whether this deployment declares a model it serves itself.

    Read from the manifest rather than guessed. A cloud deployment declares only remote models, so a
    routing decision to answer locally cannot be honoured there, and saying so is better than announcing
    a downgrade to a model that does not exist.
    """
    models = (STATE.get("manifest") or {}).get("models") or {}
    for entry in models.values():
        local = str((entry or {}).get("local") or "")
        if local:
            return True
    return False


def model_for(role: str) -> str:
    """The model this deployment answers with for an API role.

    Read from the environment the renderer wrote, which came from the manifest. A variable the deployment
    does not define falls back to the default model role rather than to a model name, because a model name
    hardcoded here would be the second source of truth this function exists to avoid.
    """
    variable = "OI_MODEL_" + role.upper().replace("-", "_")
    specific = os.environ.get(variable)
    if specific:
        return specific
    return os.environ.get(f"OI_MODEL_{DEFAULT_MODEL_ROLE.upper()}", DEFAULT_MODEL_ROLE)


def audit(event: str, actor: str, actor_role: str, outcome: str, payload: dict, **extra) -> None:
    """One row in the chain, through the tool that owns it, with a statement vouching for the actor.

    The API does not write the chain itself. The chain has one writer and one definition of its hash, and a
    second implementation here would be a second answer to what the record means.

    **The actor is certified rather than typed.** The audit tool refuses a hand-typed actor, and the first
    version of this passed the tool's development flag to get around that, which was the wrong shape: it made
    every production record carry a developer's escape. Instead the API signs a statement naming the actor it
    resolved from the session, and the tool verifies the signature and takes the actor from inside it. The
    actor in the row is now one that somebody proved, and a caller cannot name somebody else.
    """
    statement = assertion.mint_audit(STATE["key"], actor or "unknown", actor_role or "unknown",
                                     deployment_id=DEPLOYMENT_ID)
    command = [sys.executable, AUDIT_TOOL, "record", "--type", event,
               "--assertion", statement,
               "--outcome", outcome, "--deployment-id", DEPLOYMENT_ID,
               "--payload", json.dumps(payload, sort_keys=True)]
    if extra.get("model"):
        command += ["--model", extra["model"]]
    # **The columns the record has and the API was leaving empty.** Section 13e promises that the data
    # class, the egress verdict and the redaction counts have columns of their own, and the tool has
    # had the flags all along: the API simply never passed them, so 7,679 of 7,691 model-call rows
    # said nothing about whether the content was permitted to leave. A control whose evidence is a
    # blank column is a control an auditor cannot check. Found by the review panel.
    if extra.get("data_class"):
        command += ["--data-class", extra["data_class"]]
    if extra.get("egress_allowed") is not None:
        command += ["--egress-allowed", "true" if extra["egress_allowed"] else "false"]
    if extra.get("redactions"):
        command += ["--redactions", json.dumps(extra["redactions"], sort_keys=True)]
    if extra.get("unverified_card_shapes"):
        command += ["--unverified-card-shapes", str(extra["unverified_card_shapes"])]
    # **The request id, which the tool has a flag for and this never passed.**
    #
    # The API has minted a request id per call since the answer started carrying one, and it passed it to
    # this function on every model call. This function then did not put it on the command, so the column
    # stayed empty and the answer's id could not find its own row. The interface's lane claim and the
    # record's entry are two statements about one event, and the identifier is the only thing that lets
    # anything compare them. A blank column means nothing can.
    #
    # Found by the activity probe, whose BYREQUEST right posts an answer and then looks up the row by the id
    # that answer carried. It reported zero rows, which looks exactly like "the row was never written".
    if extra.get("request_id"):
        command += ["--request-id", str(extra["request_id"])]
    # **The prompt goes on stdin, not on the command line.** A command line is bounded by ARG_MAX, and a
    # question past roughly 128 KB made this write fail with `Argument list too long` — so the request that
    # most needed a record was the one that could not have one, and the failure surfaced as a bare HTTP 500
    # rather than as anything a person could read. The payload stays an argument because it is a few hundred
    # bytes of metadata by design; if it ever grows, it has the same treatment waiting for it.
    stdin_text = None
    if extra.get("prompt"):
        command += ["--prompt", "-"]
        stdin_text = extra["prompt"]
    env = dict(os.environ)
    env["DATABASE_URL"] = session_module.database_url()
    result = subprocess.run(command, capture_output=True, text=True, env=env, timeout=60,
                            input=stdin_text)
    if result.returncode != 0:
        # **Raised, not printed.** The first version wrote this to stderr and let the request continue, so a
        # deployment could answer a question it had no record of — which contradicts the one claim the whole
        # product rests on. A caller now gets an error it can see, and the deployment is visibly broken rather
        # than quietly unaccountable.
        raise RuntimeError(
            "the audit record could not be written, so this request was not answered: "
            + result.stderr.strip()[:300])

@asynccontextmanager
async def lifespan(app: FastAPI):
    """Load what a request needs, once, and refuse to start without it.

    A missing registry or signing key is a refusal at startup rather than a 500 per request, because a
    deployment that cannot say who may read what must not come up and answer from the built-in default.
    """
    registry_path = os.environ.get("ACCESS_REGISTRY", "/app/rendered/access.json")
    STATE["registry"] = access_module.load(registry_path)
    STATE["key"] = assertion.load_key(KEY_PATH)
    # What routing reads: the egress declaration decides whether a lane is permitted at all, and the
    # content default is the data class a request carries.
    manifest_path = os.environ.get("OI_MANIFEST", "/app/rendered/manifest.json")
    mp = Path(manifest_path)
    STATE["manifest"] = json.loads(mp.read_text()) if mp.exists() else {}
    STATE["services"] = ingest.Services()
    STATE["store"] = session_module.open_store()
    # What the shared answer pipeline needs, gathered once. `answer.py` takes this rather than reading the
    # app's globals, which is what lets the pipeline be tested without a running service.
    STATE["ctx"] = answer_module.Context(services=STATE["services"], manifest=STATE["manifest"],
                                         passages=PASSAGES)
    # **The job store, and the migration runs at startup rather than on a first request.** A table that
    # appears halfway through the first job is a table that did not exist when the job was created.
    migrations = os.environ.get("OI_MIGRATIONS", "/app/db/migrations")
    STATE["jobs"] = jobs_module.open_store(migrations_dir=migrations)
    print(f"api ready: registry={registry_path} model_role_default={DEFAULT_MODEL_ROLE} "
          f"answers_with={model_for(os.environ.get('OI_DEFAULT_API_ROLE', 'viewer'))} "
          f"jobs={'durable' if getattr(STATE['jobs'], 'durable', False) else 'in-memory'}", flush=True)
    yield
    STATE["jobs"].close()
    STATE["store"].close()


app = FastAPI(title="Office Inference assistant API", lifespan=lifespan)


@app.get("/health", responses={
    200: {"model": contract.Health,
          "description": "Liveness, and what the process loaded. `status` is `loading` until the "
                         "registry is read, which a client should say rather than treat as a fault."},
})
async def health():
    """Liveness, and what this process actually loaded."""
    registry = STATE.get("registry")
    return {
        "status": "ok" if registry is not None else "loading",
        "deployment_id": DEPLOYMENT_ID,
        "registry_declared": bool(registry.declared) if registry else None,
        "default_model_role": DEFAULT_MODEL_ROLE,
    }


def _resolve(request_token: str):
    """The session behind a token, or a refusal."""
    return session_module.resolve_token(STATE["store"], request_token)


@app.post("/ask", responses={
    200: {"model": contract.Answer | contract.Degraded,
          "description": "A cited answer, or a degraded one at the same status when the model would not "
                         "answer. **`kind` is what tells them apart**, and a client that renders a "
                         "degradation as an answer shows retrieved passages as though a model wrote them."},
    400: {"model": contract.Refusal, "description": "the body was not JSON, or no question was asked"},
    401: {"model": contract.Refusal, "description": "no session, an expired one, or a revoked one. "
                                                   "The three are one answer on purpose."},
    403: {"model": contract.Refusal,
          "description": "the request supplied its own authority, or the data boundary permits no lane "
                         "that can answer. The refusal is written to the chain before this returns."},
    413: {"model": contract.Refusal,
          "description": "the question is past the ceiling this deployment accepts. **A size refusal, not a "
                         "permission one**, so it is answered before any session is resolved."},
    502: {"model": contract.Refusal,
          "description": "retrieval failed, or the model could not be reached after the budget was spent"},
})
async def ask(request: Request):
    """A question in, a cited answer out.

    **The pipeline is in `answer.py` and this is a caller of it.** That is deliberate rather than tidy:
    this endpoint, the streaming one, and a job all answer questions, and C3 shipped two defects whose
    shape was two implementations of one thing. One implementation is how that stops.
    """
    # **The arrival clock, taken before anything else happens.** The budget is anchored here so retrieval
    # counts against the documented deadline. Taking it after retrieval made the time the client actually
    # waited invisible to the thing that is supposed to bound it.
    asked_at = time.monotonic()
    try:
        body = await request.json()
    except Exception:  # noqa: BLE001 - the message is the response
        return JSONResponse({"error": "the request body is not JSON"}, status_code=400)

    # **The same ceiling as the streaming path, checked in the same place for the same reason.** Both endpoints
    # answer questions, and one implementation of a refusal is how they stay the same refusal. It comes before
    # the session is resolved because the size of a question is not a permission question: a refusal that named
    # a session would tell an unauthenticated caller whether their session was valid.
    too_long = question_too_long((body.get("question") or "").strip())
    if too_long:
        return JSONResponse(too_long, status_code=413)

    token = body.get("session") or request.headers.get("X-OI-Session", "")
    # A request id, so the lane the interface reports can be tied to the row in the chain that recorded
    # it. Minted here rather than at the model call because the refusal paths need one too.
    request_id = uuid.uuid4().hex
    try:
        session, permit, model, result, decision, data_class = answer_module.prepare(
            token=token, question=(body.get("question") or "").strip(), body=body,
            ctx=STATE["ctx"], resolve=_resolve, model_for=model_for, role_lane=role_lane,
            frontier_model_for=frontier_model_for, local_lane_available=local_lane_available,
            audit=audit, endpoint="/ask", request_id=request_id)
    except answer_module.Refused as refused:
        return JSONResponse({**refused.payload, "request_id": request_id}, status_code=refused.status)

    request_budget = budget_module.Budget.for_lane(decision.lane, started=asked_at)
    generated, generation = _generate(session, permit, model, (body.get("question") or "").strip(),
                                      result.passages, request_budget)
    if generated is None:
        reason, kind, attempts, attempt_errors = generation if isinstance(generation, tuple) else (
            "unclassified", {}, 1, [])
        return JSONResponse(answer_module.degraded(
            reason=reason, detail=_degrade_detail(reason), passages=result.passages,
            request_budget=request_budget, attempts=attempts,
            threshold=request_budget.timed_out(), partial_output=False, lane=decision.lane,
            session=session, model=model, data_class=data_class, audit=audit,
            audit_facts=budget_module.audit_facts(
                reason=reason, matched=(kind or {}).get("matched", ""),
                retryable=bool((kind or {}).get("retryable")), attempts=attempts,
                threshold=request_budget.timed_out(), budget=request_budget, partial_output=False,
                errors=attempt_errors, lane=decision.lane, data_class=data_class)).payload,
            status_code=200)

    decided = answer_module.finish(
        question=(body.get("question") or "").strip(), answer=generated, session=session, permit=permit,
        model=model, result=result, decision=decision, data_class=data_class,
        request_budget=request_budget, redaction_report=None, audit=audit,
        resolved_model=resolved_model, request_id=request_id)
    return JSONResponse(decided.payload, status_code=decided.status)


# --------------------------------------------------------------------------- jobs
#
# A job is work with an identity and a history. Ask is one round trip that ends when the answer does; a job
# can be watched, stopped, and read afterwards, which is what a task that fails four times and changes
# approach on the fifth needs.
#
# **Every guard on `/ask` applies here, and it is applied by calling the same code rather than by repeating
# it.** C3 shipped a redaction on one path and a boundary on the other, and each passed every probe because
# each probe tested the endpoint its author had in mind. The step executor below goes through
# `answer_module.prepare`, so there is no second copy of the guards to keep in step.


def _find_job(job_id: str, session):
    """A job this session may read, or None.

    **A job belonging to somebody else answers the same way a job that does not exist does.** The
    alternative is an endpoint that tells a caller which job ids are real, which is the same reasoning the
    403 wording already follows.
    """
    job = STATE["jobs"].get(job_id)
    if job is None or job.deployment_id != DEPLOYMENT_ID or job.subject != session.subject:
        return None
    return job


def _session_for(request: Request, body: dict | None = None):
    """The session behind a request, or the refusal to return."""
    token = (body or {}).get("session") or request.headers.get("X-OI-Session", "") \
        or (request.query_params.get("session") or "")
    try:
        return _resolve(token), None
    except session_module.SessionError as exc:
        return None, JSONResponse({"error": str(exc), "kind": "no_session"}, status_code=401)


def _tasks_this_deployment_declares() -> list[dict]:
    """The tasks the manifest declares, which is what the interface offers.

    **Read from the manifest and not from a constant**, because the app is a function of the deployment. A
    client with no agentic pack gets an empty list and therefore no Do screen, which is the honest outcome
    rather than a menu of things that cannot run.
    """
    declared = (((STATE.get("manifest") or {}).get("agentic") or {}).get("tasks")) or []
    out = []
    for entry in declared:
        if isinstance(entry, str):
            # A bare list of ids is accepted, because that is what the routing trigger used to hold. It
            # carries no label, and a client falls back to the id rather than inventing one.
            out.append({"id": entry, "label": entry, "description": "", "tier": "",
                        "touches": [], "needs_approval": False})
        elif isinstance(entry, dict) and entry.get("id"):
            out.append({"id": entry["id"], "label": entry.get("label") or entry["id"],
                        "description": entry.get("description", ""), "tier": entry.get("tier", ""),
                        "touches": entry.get("touches") or [],
                        "needs_approval": bool(entry.get("needs_approval"))})
    return out


def _step_once(job, attempt):
    """One attempt: a question through the same pipeline `/ask` uses.

    **This is the seam C6 replaces.** Everything below is the C4 one-step executor: it resolves the
    session, retrieves, routes, calls the model, redacts, checks the citations and records the row, by
    calling the shared pipeline rather than by reimplementing it. C6 puts a tool loop here, and the state
    machine, the event log, the budgets and the interface do not change.
    """
    try:
        session = session_module.resolve_credential_hash(STATE["store"], job.credential_sha256)
    except session_module.SessionError as exc:
        # The session the job was created under has expired or been revoked. Terminal rather than
        # retryable: waiting does not bring a credential back.
        return jobs_module.StepOutcome(ok=False, failure_kind="no_session", failure_detail=str(exc),
                                       retryable=False)

    # **The pipeline is called with the session already resolved**, because a job holds a hash rather than
    # something it could present. `_resolve` would be the natural call and it takes a credential, which a job
    # deliberately does not have.
    body = {
        "question": job.input,
        "task": job.task,
        "resolved_session": session,
        # **The class the job was created under, not today's manifest.** A job outlives the request that
        # started it, and re-reading the manifest at execution time meant work created while a class was
        # permitted would run under a manifest that forbids it. The boundary probe found exactly that on this
        # path: /ask refused with a 403 and /jobs answered with a 200. Stating the class cannot widen
        # anything, because the gate compares it against the same allowlist.
        "data_class": job.data_class,
    }
    try:
        _sess, permit, model, result, decision, data_class = answer_module.prepare(
            token="", question=job.input, body=body, ctx=STATE["ctx"],
            # A session already resolved, so the pipeline does not try to resolve a credential it was not given.
            resolve=lambda _token: body.get("resolved_session") or session,
            model_for=model_for, role_lane=role_lane, frontier_model_for=frontier_model_for,
            local_lane_available=local_lane_available, audit=audit, endpoint=f"/jobs/{job.job_id}")
    except answer_module.Refused as refused:
        retryable = refused.status in (502, 503, 504)
        return jobs_module.StepOutcome(ok=False, failure_kind=refused.kind or "refused",
                                       failure_detail=str(refused.payload.get("error", ""))[:300],
                                       retryable=retryable,
                                       result={"refusal": refused.payload, "status": refused.status})

    asked_at = time.monotonic()
    request_budget = budget_module.Budget.for_lane(decision.lane, started=asked_at)
    generated, generation = _generate(session, permit, model, job.input, result.passages, request_budget)
    if generated is None:
        reason, kind, attempts, attempt_errors = generation if isinstance(generation, tuple) else (
            "unclassified", {}, 1, [])
        decided = answer_module.degraded(
            reason=reason, detail=_degrade_detail(reason), passages=result.passages,
            request_budget=request_budget, attempts=attempts, threshold=request_budget.timed_out(),
            partial_output=False, lane=decision.lane, session=session, model=model,
            data_class=data_class, audit=audit,
            audit_facts=budget_module.audit_facts(
                reason=reason, matched=(kind or {}).get("matched", ""),
                retryable=bool((kind or {}).get("retryable")), attempts=attempts,
                threshold=request_budget.timed_out(), budget=request_budget, partial_output=False,
                errors=attempt_errors, lane=decision.lane, data_class=data_class))
        # **A degraded answer completes the step rather than failing it.** Retrieval did the expensive part
        # and the client is entitled to their own material, so the job succeeds carrying the passages and a
        # typed reason. The alternative is a job that reports failure while holding the answer.
        return jobs_module.StepOutcome(ok=True, name="answer", lane=decided.lane, model=model,
                                       result=decided.payload, notice=decided.notice or "")

    decided = answer_module.finish(
        question=job.input, answer=generated, session=session, permit=permit, model=model, result=result,
        decision=decision, data_class=data_class, request_budget=request_budget, redaction_report=None,
        audit=audit, resolved_model=resolved_model, request_id=job.job_id)
    return jobs_module.StepOutcome(ok=True, name="answer", lane=decided.lane, model=model,
                                   result=decided.payload, notice=decided.notice or "")


@app.get("/activity", responses={
    200: {"model": contract.ActivityPage,
          "description": "One page of the audit chain, newest first, **scoped to what this session may see**. "
                         "The scope is applied inside the query rather than after it, and the response says "
                         "which actors it covered so a screen cannot show a subset as though it were the "
                         "whole record."},
    400: {"model": contract.Refusal,
          "description": "a limit above the cap, a limit that is not a number, or a filter value this "
                         "deployment does not use. **Refused rather than trimmed**: a caller that asked for "
                         "five thousand rows and silently received five hundred has been lied to about what "
                         "it read"},
    401: {"model": contract.Refusal, "description": "no session"},
})
async def activity(request: Request):
    """The record, readable without a shell on the box.

    **Why this endpoint exists at all.** The chain is the product's central claim, and until now the only way
    to read it was the audit CLI. That is an operator tool behind an SSH session, and the client app talks to
    this service over HTTP and nothing else. Activity is the screen that makes the product auditable to a
    client, and it had no endpoint.
    """
    session, refusal = _session_for(request)
    if refusal:
        return refusal

    query = request.query_params
    try:
        page = activity_module.read(
            session, database_url=activity_module.database_url(),
            limit=query.get("limit") or activity_module.DEFAULT_LIMIT,
            since=query.get("since"), type=query.get("type"), actor=query.get("actor"),
            outcome=query.get("outcome"), job=query.get("job"), request=query.get("request"))
    except activity_module.ActivityError as exc:
        return JSONResponse({"error": str(exc), "kind": "bad_request"}, status_code=400)

    # **Reading the record is itself recorded.** An auditor asking who looked at the chain is asking a
    # question this deployment should be able to answer, and a read that leaves no trace is the one action in
    # the system that a viewer could take without anybody being able to see it afterwards.
    audit("access", session.subject, session.role, "ok",
          {"stage": "activity_read", "returned": len(page.events), "filters": {
              k: v for k, v in query.items() if k in ("limit", "since", "type", "actor", "outcome", "job",
                                                      "request")},
           "scope_actors": page.scope.get("actors", [])})
    return JSONResponse(page.to_dict(), status_code=200)


@app.get("/documents/{doc_id:path}", responses={
    200: {"model": contract.Document,
          "description": "A document the answer cited, **as the system indexed it** rather than as the file "
                         "looks on disk: the text the model actually answered from, with its pages, already "
                         "redacted. This is what makes a citation checkable rather than something to believe."},
    401: {"model": contract.Refusal, "description": "no session"},
    404: {"model": contract.Refusal,
          "description": "**No such document, or none this session may read, and the two are one answer.** "
                         "Telling a caller that a document exists but is forbidden tells them it exists, "
                         "which is the same argument the session refusal makes and the same answer a job "
                         "belonging to somebody else gets."},
})
async def document(request: Request, doc_id: str):
    """One document, for a person checking the claim an answer made.

    **Why this exists.** The Ask screen has shown a chip per source since C4 and the chips were spans: a person
    could see that an answer rested on the travel policy and could not read the travel policy. A citation
    nobody can open asks to be believed, and this product's claim is that it does not.

    **The path is a path parameter, not a query value.** A document id is `company/travel-expenses-policy.md`
    and it has slashes in it, so the route takes the rest of the path rather than a segment.
    """
    session, refusal = _session_for(request)
    if refusal:
        return refusal

    found = documents_module.read(session, database_url=documents_module.database_url(), doc_id=doc_id)
    if found is None:
        return JSONResponse(
            {"error": "Permission denied on this resource (or it might not exist).",
             "kind": "not_found"}, status_code=404)

    # **Reading a document is recorded, like reading the record is.** The question an auditor asks is who
    # looked at what, and a document view that left no trace would be an action a viewer could take with
    # nobody able to see it afterwards.
    audit("access", session.subject, session.role, "ok",
          {"stage": "document_read", "doc_id": doc_id, "acl": found.get("acl", ""),
           "chunks": len(found.get("chunks") or []), "truncated": bool(found.get("truncated")),
           "redactions": found.get("redactions") or {}},
          data_class="internal", egress_allowed=False)
    return JSONResponse(found, status_code=200)


@app.get("/tasks", responses={
    200: {"model": contract.TaskList,
          "description": "The tasks this deployment declares, for an interface to offer. **Empty when the "
                         "deployment has no agentic pack**, which is the honest answer rather than a menu "
                         "of things that cannot run."},
    401: {"model": contract.Refusal, "description": "no session"},
})
async def tasks(request: Request):
    """What this deployment can be asked to do. Read from the manifest, never from a constant here."""
    session, refusal = _session_for(request)
    if refusal:
        return refusal
    declared = _tasks_this_deployment_declares()
    return {"tasks": declared, "agentic": bool(declared)}


@app.post("/jobs", responses={
    200: {"model": contract.Job, "description": "the job, already run to a terminal state by the one-step "
                                               "executor this phase ships"},
    400: {"model": contract.Refusal, "description": "no input, or no task"},
    401: {"model": contract.Refusal, "description": "no session"},
    403: {"model": contract.Refusal,
          "description": "the request supplied its own authority, or the task is not one this deployment "
                         "declares"},
    502: {"model": contract.Refusal, "description": "retrieval failed"},
})
async def create_job(request: Request):
    """Start a job, and run it. Returns the job rather than a promise of one."""
    try:
        body = await request.json()
    except Exception:  # noqa: BLE001 - the message is the response
        return JSONResponse({"error": "the request body is not JSON"}, status_code=400)

    # **The job's identifier, minted here rather than at the model call.** A job is its own identifier and the
    # response carries the job id, but the refusals this handler can produce need something to be found by, and
    # the request id is what the chain records for that. The first version used a name it had never bound.
    request_id = uuid.uuid4().hex

    session, refusal = _session_for(request, body)
    if refusal:
        return refusal

    # **A request that supplies its own authority is refused here too**, and this is the check that has to
    # be on both paths rather than on the one somebody was looking at.
    supplied = next((f for f in answer_module.AUTHORITY_FIELDS if body.get(f) is not None), None)
    if supplied:
        audit("access", session.subject, str(body.get(supplied)), "denied",
              {"supplied_field": supplied, "session_role": session.role,
                              "refusal": "a request may not supply its own authority", "endpoint": "/jobs"})
        return JSONResponse(
            {"error": "Permission denied on this resource (or it might not exist). A role comes from an "
                      "authenticated session and never from a request field.",
             "kind": "refused_authority_claim"}, status_code=403)

    task = (body.get("task") or "").strip()
    text = (body.get("input") or "").strip()
    if not task or not text:
        return JSONResponse({"error": "a job needs both a task and an input", "kind": "bad_request"},
                            status_code=400)

    declared = {t["id"] for t in _tasks_this_deployment_declares()}
    if task not in declared:
        # **The refusal names the task and lists what is declared.** A caller is not left guessing, and the
        # row exists so an operator can see what was asked for.
        audit("access", session.subject, session.role, "denied",
              {"stage": "job", "reason": "task_not_declared", "task": task,
               "declared": sorted(declared)}, tool=task)
        return JSONResponse(
            {"error": f"This deployment does not declare a task called '{task}'.",
             "kind": "no_such_task", "declared": sorted(declared)}, status_code=403)

    data_class = answer_module.data_class_for(body, STATE.get("manifest") or {})
    # **The boundary is consulted before the job exists.** The first version created the job and let its
    # single attempt discover the refusal, so the request answered HTTP 200 with a failed job row and the
    # refusal buried in the result, while `/ask` refused the same request with a 403. A job is not created
    # for work no lane is permitted to do.
    #
    # The role is the default API role, because that is the role `prepare` resolves a job's steps under. If
    # the two ever diverge the step would re-check and refuse, which is the safe direction rather than a
    # hole: this is an early refusal, not the only one.
    api_role = os.environ.get("OI_DEFAULT_API_ROLE", "viewer")
    try:
        answer_module.check_boundary(
            manifest=STATE.get("manifest") or {}, data_class=data_class, task=task,
            model_name=model_for(api_role), default_lane=role_lane(api_role),
            frontier_model=frontier_model_for(api_role), session=session, audit=audit,
            endpoint="/jobs", request_id=request_id)
    except answer_module.Refused as refused:
        return JSONResponse(refused.payload, status_code=refused.status)

    job = jobs_module.new_job(
        deployment_id=DEPLOYMENT_ID, subject=session.subject, actor_role=session.role,
        roots=session.permit().roots,
        # **The hash, not the credential.** The job needs to run after this request ends, and the first version
        # solved that by keeping the credential in the row, which made a job row a working identity.
        credential_sha256=session.credential_sha256, task=task, input=text, data_class=data_class)
    store = STATE["jobs"]
    store.put(job)
    jobs_module.run(job, store, _step_once, audit=audit)
    return JSONResponse(job.to_dict(include_result=True), status_code=200)


@app.get("/jobs/{job_id}", responses={
    200: {"model": contract.Job, "description": "the job, its attempts, and its result"},
    401: {"model": contract.Refusal, "description": "no session"},
    404: {"model": contract.Refusal, "description": "no such job, or one belonging to somebody else. "
                                                   "The two are one answer on purpose."},
})
async def get_job(job_id: str, request: Request):
    """One job, with every attempt it made. This is what a client that comes back reads."""
    session, refusal = _session_for(request)
    if refusal:
        return refusal
    job = _find_job(job_id, session)
    if job is None:
        return JSONResponse({"error": "no such job", "kind": "not_found"}, status_code=404)
    return JSONResponse(job.to_dict(include_result=True), status_code=200)


@app.get("/jobs/{job_id}/events", responses={
    200: {"description": "the job's progress as Server-Sent Events. **Replayable**: `?since=<seq>` returns "
                         "everything after that number, so a client that closed the tab catches up rather "
                         "than losing what happened."},
    401: {"model": contract.Refusal, "description": "no session"},
    404: {"model": contract.Refusal, "description": "no such job"},
})
async def job_events(job_id: str, request: Request):
    """The numbered log, replayed from where the caller stopped."""
    session, refusal = _session_for(request)
    if refusal:
        return refusal
    job = _find_job(job_id, session)
    if job is None:
        return JSONResponse({"error": "no such job", "kind": "not_found"}, status_code=404)

    try:
        since = max(0, int(request.query_params.get("since") or 0))
    except ValueError:
        return JSONResponse({"error": "since must be a number", "kind": "bad_request"}, status_code=400)

    events = STATE["jobs"].events_since(job_id, since=since)

    async def replay():
        for event in events:
            yield {"event": event["event"],
                   "data": json.dumps({"seq": event["seq"], **event["data"]}, sort_keys=True)}

    return EventSourceResponse(replay(), headers={"X-Accel-Buffering": "no", "Cache-Control": "no-store"})


@app.post("/jobs/{job_id}/{action}", responses={
    200: {"model": contract.Job, "description": "the job after the action"},
    400: {"model": contract.Refusal, "description": "an action that is not pause, resume or cancel"},
    401: {"model": contract.Refusal, "description": "no session"},
    404: {"model": contract.Refusal, "description": "no such job"},
    409: {"model": contract.Refusal, "description": "**a transition the machine refuses**, named. Resuming "
                                                   "a cancelled job is the one this exists for."},
})
async def control_job(job_id: str, action: str, request: Request):
    """Pause, resume, or cancel.

    **The refusal is the feature.** A handler that assigned the state directly would happily resume a
    cancelled job, and the record would then describe work that happened after somebody stopped it.
    """
    session, refusal = _session_for(request)
    if refusal:
        return refusal
    job = _find_job(job_id, session)
    if job is None:
        return JSONResponse({"error": "no such job", "kind": "not_found"}, status_code=404)

    store = STATE["jobs"]
    try:
        if action == "pause":
            jobs_module.pause(job, store)
        elif action == "resume":
            jobs_module.resume(job, store)
        elif action == "cancel":
            jobs_module.cancel(job, store)
        else:
            return JSONResponse({"error": f"'{action}' is not an action on a job. The three are pause, "
                                          "resume and cancel.", "kind": "bad_action"}, status_code=400)
    except jobs_module.TransitionRefused as refused:
        return JSONResponse({"error": str(refused), "kind": "transition_refused",
                             "was": refused.was, "wanted": refused.wanted}, status_code=409)

    audit("tool_call", session.subject, session.role, "ok",
          {"stage": "job", "job_id": job.job_id, "task": job.task, "state": job.state, "action": action,
           }, tool=job.task)
    return JSONResponse(job.to_dict(include_result=True), status_code=200)


def _generate(session, permit, model: str, question: str, passages: list, budget=None):
    """The model call, bounded, retried only while it is safe, and classified when it fails.

    Returns `(answer, error)`. Exactly one is None. A failure that is worth waiting for is retried inside the
    same budget rather than a fresh one, and a failure that can never succeed is not retried at all, because
    the wait would come out of the budget the degraded answer needs.
    """
    import urllib.error
    import urllib.request

    budget = budget or budget_module.Budget()
    context = "\n\n".join(f"[{n + 1}] {p.text}" for n, p in enumerate(passages))
    prompt = (
        "Answer the question using only the numbered passages below. Cite the passages you use as [n]. "
        "If they do not answer it, say so plainly. Do not use outside knowledge.\n\n"
        f"Passages:\n{context}\n\nQuestion: {question}"
    )
    # **Minted per attempt, not once.** A statement is spent by its first presentation, and the first
    # version of this minted one before the retry loop. So attempt one earned the real 429, the retry
    # replayed a spent assertion, and the 401 that came back classified as `unclassified`: a genuine
    # throttle was recorded as an unrecognised failure and the documented retry could never succeed.
    # Found by the C3 panel's executor, who provoked a real Bedrock throttle and read the reason.
    payload = {
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "max_tokens": int(os.environ.get("OI_MAX_TOKENS", "700")),
    }

    errors: list = []
    attempt = 0
    while attempt < budget_module.MAX_ATTEMPTS:
        attempt += 1
        if budget.past_reserve():
            # Not enough left to both wait and deliver. Surrender now so the degraded answer fits.
            break
        # A fresh statement for this attempt. Building it here is what makes a retry possible at all.
        attempt_statement = assertion.mint(STATE["key"], model, session_id=session.session_id)
        request = urllib.request.Request(
            GATEWAY_URL + "/v1/chat/completions", data=json.dumps(payload).encode(), method="POST",
            headers={"Content-Type": "application/json", assertion.HEADER: attempt_statement})
        try:
            with urllib.request.urlopen(request, timeout=max(1.0, budget.remaining())) as r:
                data = json.load(r)
            choices = data.get("choices") or []
            if not choices:
                return None, ("unclassified", "the gateway returned no choices", attempt, errors)
            return (choices[0].get("message") or {}).get("content", ""), None
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode(errors="replace")[:400]
            kind = budget_module.classify(detail)
            errors.append({"attempt": attempt, "status": exc.code, "reason": kind["reason"],
                           "matched": kind["matched"], "retryable": kind["retryable"]})
            retry_after = exc.headers.get("Retry-After") if exc.headers else None
            if not kind["retryable"]:
                return None, (kind["reason"], kind, attempt, errors)
            if attempt >= budget_module.MAX_ATTEMPTS:
                return None, (kind["reason"], kind, attempt, errors)
            wait = _backoff(attempt, retry_after)
            if wait >= budget.remaining() - budget.reserve:
                # The wait itself would eat the delivery reserve, so waiting is the wrong answer.
                return None, (kind["reason"], kind, attempt, errors)
            time.sleep(wait)
        except Exception as exc:  # noqa: BLE001 - a transport failure is a classification too
            kind = budget_module.classify(type(exc).__name__)
            errors.append({"attempt": attempt, "status": 0, "reason": kind["reason"],
                           "matched": kind["matched"], "retryable": kind["retryable"]})
            if not kind["retryable"] or attempt >= budget_module.MAX_ATTEMPTS:
                return None, (kind["reason"], kind, attempt, errors)
            time.sleep(_backoff(attempt, None))

    # **Three values, and the last classification is never discarded.** The first version returned a
    # two-tuple here while the caller unpacked three, and it fell back to a bare `unclassified` when
    # the loop broke before recording anything — so a provider throttle could still be recorded as an
    # unrecognised failure, which is the defect the retry fix was meant to remove.
    if errors:
        last = dict(errors[-1])
        return None, (last["reason"], last, attempt, errors)
    # No attempt was made at all: the deadline left no room. That is a timeout, and saying so is more
    # honest than `unclassified`, which reads as a failure nobody understands.
    return None, ("timeout", {"reason": "timeout", "matched": "", "retryable": True}, attempt, errors)


def _degrade_detail(reason: str) -> str:
    """One sentence a person reads, from a reason code."""
    return {
        "throttled": "the model provider is throttling this deployment, so the answer below comes from your "
                     "own documents rather than from a generated response",
        "budget": "this role's budget ceiling has been reached, so no model call was made",
        "provider_unavailable": "the model provider is unavailable, so the answer below comes from your own "
                                "documents",
        "unreachable": "the model provider could not be reached, so the answer below comes from your own "
                       "documents",
        "not_authorised": "the model provider refused this deployment's credential, which needs an operator",
        "refused_by_provider": "the model provider declined to answer this request",
        "bad_request": "the request was rejected as malformed, which is a defect rather than a condition",
        "unclassified": "the model call failed in a way this deployment does not recognise, so the answer "
                        "below comes from your own documents",
        "timeout": "the model did not answer within the time this deployment allows",
    }.get(reason, "the model call did not complete, so the answer below comes from your own documents")

def _backoff(attempt: int, retry_after: str | None) -> float:
    """How long to wait, honoured from the provider when it says, jittered when it does not."""
    import random

    if retry_after:
        try:
            return max(0.0, float(retry_after))
        except ValueError:
            pass
    return min(2.0 ** attempt * 0.25, 2.0) * (0.5 + random.random())

@app.post("/ask/stream", responses={
    200: {
        "model": contract.StreamStart | contract.StreamDelta | contract.StreamError
                 | contract.StreamDegraded | contract.StreamDone,
        "description": "Server-Sent Events. `event:` is one of `start`, `delta`, `error`, `degraded`, "
                       "`done`, and `data:` is a JSON object matching the corresponding model here. "
                       "**A client that branches only on `error` and `delta` renders the `degraded` event "
                       "as an empty answer**, which is why it is in this list.",
        "content": {"text/event-stream": {}},
    },
    400: {"model": contract.Refusal, "description": "the body was not JSON, or no question was asked"},
    401: {"model": contract.Refusal, "description": "no session, an expired one, or a revoked one"},
    403: {"model": contract.Refusal,
          "description": "**A refusal here is a JSON 403 before the first event, not an SSE error "
                         "event.** A client that only handles stream events never sees it."},
    502: {"model": contract.Refusal, "description": "retrieval failed before the stream began"},
})
async def ask_stream(request: Request):
    """The same question, answered in frames.

    **Redaction happens here, and it is why this endpoint is not a thin wrapper.** A guardrail that runs after
    delivery is audit-only: the bytes are already in the browser. So the response is redacted on the way out
    through a holdback buffer sized from the pattern set, and the probe asserts over every frame the client
    received rather than over the assembled answer.

    Four things are deliberate and each is invisible to a load test: the heartbeat that keeps an idle connection
    alive through prompt processing, the `Incremental` and `X-Accel-Buffering` headers that stop a proxy
    buffering the stream, the terminal error event that a spent status code makes necessary, and the token count
    taken from our own deltas rather than from the gateway, which reports usage in a final chunk an early
    disconnect never receives.
    """
    try:
        body = await request.json()
    except Exception:  # noqa: BLE001 - the message is the response
        return JSONResponse({"error": "the request body is not JSON"}, status_code=400)

    token = body.get("session") or request.headers.get("X-OI-Session", "")
    question = (body.get("question") or "").strip()

    # **Before the session is resolved, because the size of a question is not a permission question.** A
    # refusal that named a session for an oversized paste would leak whether the session was valid to somebody
    # who has not been authenticated for it.
    too_long = question_too_long(question)
    if too_long:
        return JSONResponse(too_long, status_code=413)
    if not question:
        return JSONResponse({"error": "no question was asked"}, status_code=400)

    try:
        session = _resolve(token)
    except session_module.SessionError as exc:
        return JSONResponse({"error": str(exc), "kind": "no_session"}, status_code=401)

    supplied = next((field for field in ("role", "roles", "actor_role", "groups", "roots", "acl")
                     if body.get(field) is not None), None)
    if supplied:
        try:
            session_module.check_role_claim(session, supplied, body.get(supplied), dev=True)
        except session_module.SessionError:
            audit("access", session.subject, str(body.get(supplied)), "denied",
                  {"supplied_field": supplied, "session_role": session.role,
                   "endpoint": "/ask/stream"})
            return JSONResponse(
                {"error": "Permission denied on this resource (or it might not exist). A role comes from an "
                          "authenticated session and never from a request field.",
                 "kind": "refused_authority_claim"},
                status_code=403)

    permit = session.permit()
    role = body.get("model_role") or os.environ.get("OI_DEFAULT_API_ROLE", "viewer")
    model = model_for(role)
    # The same decision the non-streaming path makes, so the two cannot drift. The gate decides the
    # lane and the triggers choose inside it; the stream then uses the lane thresholds rather than the
    # frontier ones.
    stream_decision = routing.route(
        STATE.get("manifest") or {},
        data_class=((STATE.get("manifest") or {}).get("access") or {}).get("data_class") or "regulated",
        task=body.get("task") or "",
        user_wants_stronger=bool(body.get("stronger")),
        model_name=model,
        default_lane=role_lane(role),
        frontier_model=frontier_model_for(role),
    )

    # **The streaming path needs the same guard as the JSON one, and it did not have it.** The first
    # version computed the routing decision here and then never read it again except to pass the lane
    # into a frame, so a data class the manifest forbids was refused with a 403 on `/ask` and answered
    # by the remote model on `/ask/stream` — with an audit row that said `egress_allowed: false` while
    # the content left the building. A boundary that holds on one endpoint and not the other is not a
    # boundary, and this is the same shape as the redaction defect the same review found: a control
    # implemented on the path somebody was looking at.
    #
    # Found by the C3 panel's fidelity checker, who ran a second API container against a manifest copy
    # with a forbidden class and read both answers.
    #
    # Refused before the response starts, so this is a status code rather than a frame: nothing has been
    # sent yet, which is the one moment a stream can still say no.
    stream_data_class = ((STATE.get("manifest") or {}).get("access") or {}).get("data_class") or "regulated"
    if stream_decision.lane == "none" or (
            stream_decision.lane == "local" and not local_lane_available()):
        audit("access", session.subject, session.role, "denied",
              {"stage": "routing", "reason": stream_decision.reason,
               "lane": stream_decision.lane, "data_class": stream_data_class,
               "escalation_requested": stream_decision.escalation_requested,
               },
              data_class=stream_data_class, egress_allowed=False)
        return JSONResponse(
            {"error": "Permission denied on this resource (or it might not exist). "
                      "This request needs a lane this deployment does not permit and has no "
                      "local model to answer with.",
             "kind": "no_permitted_lane", "reason": stream_decision.reason,
             "notice": stream_decision.notice},
            status_code=403)

    try:
        result = retriever.search(STATE["services"], question, permit.role, limit=PASSAGES, permit=permit)
    except Exception as exc:  # noqa: BLE001
        audit("access", session.subject, session.role, "error",
              {"stage": "retrieval", "error": type(exc).__name__})
        return JSONResponse({"error": f"retrieval failed: {exc}", "kind": "retrieval_failed"}, status_code=502)

    if not result.passages:
        audit("access", session.subject, session.role, "denied",
              {"stage": "retrieval", "reason": "no passage the session may read matched",
               })
        return JSONResponse({"answer": "No document you may read answers this question.", "citations": [],
                             "kind": "no_source"}, status_code=200)

    headers = {
        "Incremental": "?1",
        "X-Accel-Buffering": "no",
        "Cache-Control": "no-cache",
    }
    requested_at = time.monotonic()
    return EventSourceResponse(
        _frames(session, permit, model, question, result, requested_at, stream_decision.lane),
        headers=headers,
        ping=int(os.environ.get("OI_STREAM_PING_SECONDS", "15")),
    )


async def _frames(session, permit, model: str, question: str, result, requested_at: float,
                   lane: str = "frontier"):
    """The events, in the order a client should read them."""
    import urllib.error
    import urllib.request

    redactor = streaming.StreamRedactor()
    # Two starts, because they answer two questions. `started` is when the answer began being written;
    # `requested_at` is when the client asked. The first is what the model did, the second is what the
    # person waited. Reporting only the first understated the wait by the retrieval time, which on this
    # deployment is 350 to 600 milliseconds: more than the first token itself.
    started = time.monotonic()
    retrieval_ms = int((started - requested_at) * 1000)
    first_token_at = None
    deltas = 0

    # **The same budget the non-streaming path has, on the clock the client experiences.** The deadline is
    # anchored at `requested_at` rather than here, so retrieval counts against it: a budget that starts
    # after the work has begun is not a bound on the request.
    request_budget = budget_module.Budget.for_lane(lane, started=requested_at)
    errors: list = []
    emitted = False

    def frame(kind: str, data: dict) -> dict:
        return {"event": kind, "data": json.dumps(data, sort_keys=True)}

    yield frame("start", {"model": model, "role": session.role, "roots": sorted(permit.roots),
                          "citations": [{"index": n + 1, "doc_id": p.doc_id, "acl": p.acl,
                                         "page_start": p.page_start, "page_end": p.page_end}
                                        for n, p in enumerate(result.passages)]})

    context = "\n\n".join(f"[{n + 1}] {p.text}" for n, p in enumerate(result.passages))
    prompt = (
        "Answer the question using only the numbered passages below. Cite the passages you use as [n]. "
        "If they do not answer it, say so plainly. Do not use outside knowledge.\n\n"
        f"Passages:\n{context}\n\nQuestion: {question}"
    )
    # Minted per attempt here too, for the reason above: an assertion is spent once, so a retry that
    # replays one is a retry that cannot authenticate.
    payload = {
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "max_tokens": int(os.environ.get("OI_MAX_TOKENS", "700")),
        "stream": True,
    }
    def _request():
        """A fresh statement for this attempt, built at the moment of the attempt."""
        attempt_statement = assertion.mint(STATE["key"], model, session_id=session.session_id)
        return urllib.request.Request(
            GATEWAY_URL + "/v1/chat/completions", data=json.dumps(payload).encode(), method="POST",
            headers={"Content-Type": "application/json", assertion.HEADER: attempt_statement})

    upstream = _request()

    # **A read cannot be interrupted, so it is bounded instead.** The socket timeout is the time the budget
    # has left, and the watchdog below checks between reads. A generator that never yields cannot be
    # cancelled from outside, which is why the deadline lives in here rather than around the call.
    def _open():
        return urllib.request.urlopen(_request(), timeout=max(1.0, request_budget.remaining()))

    # **Retried here for the same reason the non-streaming path retries, and this path used not to.**
    # Research on 2026-10-14 found the inconsistency: an HTTPError before any answer frame went
    # straight to a degraded answer on the streaming path while the non-streaming path retried the
    # identical 429 three times, and the streaming path is the one real clients use. Retrying is safe
    # here because no answer byte has been emitted; the start frame carries citations, not text, and is
    # idempotent. After the first delta the rule is unchanged: an error event, never a retry.
    attempt = 0
    response = None
    while attempt < budget_module.MAX_ATTEMPTS:
        attempt += 1
        if request_budget.past_reserve():
            break
        try:
            response = _open()
            break
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode(errors="replace")[:400]
            kind = budget_module.classify(detail)
            errors.append({"attempt": attempt, "status": exc.code, "reason": kind["reason"],
                           "matched": kind["matched"], "retryable": kind["retryable"]})
            last_kind = kind
            if not kind["retryable"] or attempt >= budget_module.MAX_ATTEMPTS:
                break
            wait = _backoff(attempt, exc.headers.get("Retry-After") if exc.headers else None)
            if wait >= request_budget.remaining() - request_budget.reserve:
                break
            time.sleep(wait)
        except Exception as exc:  # noqa: BLE001
            last_kind = budget_module.classify(type(exc).__name__)
            errors.append({"attempt": attempt, "status": 0, "reason": last_kind["reason"],
                           "matched": last_kind["matched"], "retryable": last_kind["retryable"]})
            if not last_kind["retryable"] or attempt >= budget_module.MAX_ATTEMPTS:
                break
            time.sleep(_backoff(attempt, None))

    if response is None:
        kind = errors[-1] if errors else {"reason": "unclassified", "matched": "", "retryable": False}
        audit("model_call", session.subject, session.role, "denied",
              budget_module.audit_facts(reason=kind["reason"], matched=kind["matched"],
                                        retryable=kind["retryable"], attempts=attempt,
                                        threshold=request_budget.timed_out(), budget=request_budget,
                                        partial_output=False, errors=errors, lane=lane,
                                        data_class=((STATE.get("manifest") or {}).get("access") or {}).get(
                                            "data_class") or "regulated"), model=model)
        # No frame has gone out yet, so a degraded answer is still possible and is the better outcome.
        yield frame("degraded", budget_module.degraded(kind["reason"], _degrade_detail(kind["reason"]),
                                                      result.passages, request_budget, attempt,
                                                      request_budget.timed_out(), False, lane))
        return

    try:
        for raw in response:
            # **The watchdog, and the reason it is inside the loop.** A generator that never yields cannot be
            # cancelled from outside, so the deadline is enforced between reads rather than around them. The
            # socket timeout above bounds a single read; this bounds the whole stream and tells the three
            # thresholds apart, which a total cap alone cannot do.
            deadline = request_budget.timed_out()
            if deadline:
                tripped = deadline
                yield frame("error", {"kind": "stream_timeout", "threshold": tripped,
                                      "partial_output": emitted,
                                      "detail": "the stream stopped arriving within the time allowed"})
                audit("model_call", session.subject, session.role, "denied",
                      budget_module.audit_facts(reason="timeout", matched=tripped, retryable=False,
                                                attempts=1, threshold=tripped, budget=request_budget,
                                                partial_output=emitted, errors=errors), model=model)
                return
            try:
                response.fp.raw._sock.settimeout(max(1.0, request_budget.remaining()))
            except Exception:  # noqa: BLE001 - a socket we cannot reach is not a reason to fail the stream
                pass
            line = raw.decode("utf-8", errors="ignore").strip()
            if not line.startswith("data:"):
                continue
            data = line[5:].strip()
            if data == "[DONE]":
                break
            try:
                parsed = json.loads(data)
            except json.JSONDecodeError:
                continue
            delta = ((parsed.get("choices") or [{}])[0].get("delta") or {}).get("content") or ""
            if not delta:
                continue
            deltas += 1
            request_budget.note_chunk()
            if first_token_at is None:
                first_token_at = time.monotonic()
            # **The redactor decides what is safe to emit, and its failure is closed on purpose.**
            # Research on 2026-10-14 found fail-open branches in three separate LiteLLM streaming
            # paths, where an exception or an unrecognised event emitted the buffer unmasked, and found a
            # guardrail that logged success while scanning nothing. So the failure here is deliberate
            # rather than incidental: nothing raw is emitted, the client is told, and the record says so.
            try:
                safe = redactor.feed(delta)
            except Exception as exc:  # noqa: BLE001 - a redactor that cannot run must not pass text
                audit("model_call", session.subject, session.role, "error",
                      {"stage": "redaction", "error": type(exc).__name__,
                       "emitted_so_far": emitted}, model=model)
                yield frame("error", {"kind": "redaction_failed", "partial_output": emitted,
                                      "detail": "the response could not be filtered, so it was stopped "
                                                "rather than sent unfiltered"})
                return
            if safe:
                emitted = True
                yield frame("delta", {"text": safe})
        tail = redactor.finish()
        if tail:
            yield frame("delta", {"text": tail})
    except Exception as exc:  # noqa: BLE001 - a mid-stream failure cannot change the status code
        yield frame("error", {"kind": "stream_failed", "detail": type(exc).__name__})
        return

    report = redactor.report()
    audit("model_call", session.subject, session.role, "ok",
          {"stage": "ask_stream", "model": model, "deltas": deltas,
           "passages": len(result.passages), "redactions": report["redactions"],
           "holdback": report["holdback"]}, model=model,
          data_class=((STATE.get("manifest") or {}).get("access") or {}).get("data_class")
                     or "regulated",
          egress_allowed=(lane == "frontier"),
          redactions=report["redactions"],
          unverified_card_shapes=report.get("unverified_card_shapes", 0),
          prompt=redaction.redact(question)[0])
    yield frame("done", {"deltas": deltas, "redactions": report["redactions"],
                         # Time to first token as the caller experiences it, retrieval included. The
                         # model's own share sits beside it so a budget can tell the two apart.
                         "retrieval_ms": retrieval_ms,
                         "ttft_ms": int((first_token_at - requested_at) * 1000) if first_token_at else None,
                         "model_first_ms": int((first_token_at - started) * 1000) if first_token_at else None,
                         "total_ms": int((time.monotonic() - requested_at) * 1000)})
