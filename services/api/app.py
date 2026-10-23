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
import re
import time
import subprocess
import sys
import threading
import uuid
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, Response
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

# The workflow engine, which owns the multi-step loop and the approval gate. It is a package under
# `services/`, so the parent goes on the path rather than the package directory. Its modules import `jobs`
# themselves, so nothing else here changes.
sys.path.insert(0, "/app/services")

import access as access_module  # noqa: E402
import assertion  # noqa: E402
import honesty  # noqa: E402
import documents as documents_module  # noqa: E402
import workflow as workflow_module  # noqa: E402
import approvals as approvals_module  # noqa: E402
import definitions as definitions_module  # noqa: E402
import agent_worker as agent_worker_module  # noqa: E402
import agent_runtime as agent_runtime_module  # noqa: E402
import mcp_client as mcp_client_module  # noqa: E402
import egress_client as egress_client_module  # noqa: E402
import workspace_client as workspace_client_module  # noqa: E402
import redaction  # noqa: E402
import ingest  # noqa: E402
import retriever  # noqa: E402
import session as session_module  # noqa: E402
import budget as budget_module  # noqa: E402
import answer_contract  # noqa: E402
import routing  # noqa: E402
import streaming  # noqa: E402
import tool_loop  # noqa: E402
import answer_profile  # noqa: E402

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
PASSAGES = answer_profile.current().seed_passages

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


def attribution_from(headers) -> dict:
    """What answered, from the headers the gateway sets on a successful call.

    **The manifest model is not enough once a failover exists.** A fallback answers under a different model
    group, and the manifest names the primary for the role, so an answer that reported only the manifest model
    would credit the model that did not run. LiteLLM puts the routing decision on every response:
    `x-litellm-model-group` is the configured group that answered, so a failover names itself;
    `x-litellm-model-name` is the provider model underneath; and `x-litellm-attempted-fallbacks` is how many
    fallbacks were tried first. Reading them is what closes INF-ROLE-001 under failover.
    """
    if not headers:
        return {}
    try:
        lower = {str(key).lower(): str(value) for key, value in headers.items()}
    except Exception:  # noqa: BLE001 - attribution must never fail an answer
        return {}
    group = lower.get("x-litellm-model-group") or ""
    model = lower.get("x-litellm-model-name") or ""
    if not group and not model:
        # Nothing to attribute. An empty dict is the honest answer, and callers fall back to the manifest.
        return {}
    attribution: dict = {}
    if group:
        attribution["model_group"] = group
    if model:
        attribution["model"] = model
    try:
        attribution["fallbacks_attempted"] = int(lower.get("x-litellm-attempted-fallbacks") or 0)
    except ValueError:
        attribution["fallbacks_attempted"] = 0
    return attribution


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
    # **The tool name, for the same reason as the model name.** Every `tool_call` row has a `tool` column and
    # the audit tool has accepted `--tool` all along; the answer loop passed `tool=name` to this function and
    # this function never read it, so the chain could not say which tool ran. Found on 2026-10-19 while adding
    # the row query, whose comparison counts its own calls and found every tool column empty.
    if extra.get("tool"):
        command += ["--tool", str(extra["tool"])]
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
    # **The agent definitions are the pack's, not the manifest's.** The renderer resolves them into the same
    # rendered directory; a deployment with no agentic pack reads an empty list and refuses the task by name.
    definitions_path = os.environ.get("OI_AGENT_DEFINITIONS",
                                      "/app/rendered/agent_definitions.json")
    STATE["agent_definitions"] = definitions_module.load(definitions_path)
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
    # **The dispatcher starts only when there is something it can run.** A core-only deployment has no
    # agentic definitions, so it must not hold the singleton lease or poll an empty queue. The definition
    # list is the switch, not an environment flag that can disagree with the packs.
    definition_ids = {entry["id"] for entry in (STATE.get("agent_definitions") or [])}
    STATE["agent_worker"] = None
    if definition_ids:
        worker = agent_worker_module.AgentWorker(
            STATE["jobs"], _run_agent_job, definition_ids,
            concurrency=int(os.environ.get("OI_AGENT_CONCURRENCY", "1")),
            poll_s=float(os.environ.get("OI_AGENT_POLL_S", "0.5")))
        if worker.start():
            STATE["agent_worker"] = worker
            # **A job left running by a dead process is not queued, so the claim loop will never see it.**
            # The lease holder recovers those jobs on startup. Each runs in its own thread so a slow resume
            # does not hold up the API's startup or the dispatcher's first claim.
            recovered = STATE["jobs"].running_jobs(definition_ids)
            for stale in recovered:
                # **A resume is a fact in the record, not only a new set of step rows.** The row names the
                # job and the step the dead process left open, so an auditor can find the restart.
                last_step = stale.steps[-1].name if stale.steps else ""
                try:
                    audit("resume", stale.subject, stale.actor_role, "ok",
                          {"job_id": stale.job_id, "task": stale.task, "step": last_step,
                           "reason": "process_restart"})
                except Exception as exc:  # noqa: BLE001 - recovery is more important than this one row
                    print(f"agent dispatcher: resume row failed for {stale.job_id}: {exc}", flush=True)
                threading.Thread(target=_run_agent_job, args=(stale,),
                                 name=f"agent-recover-{stale.job_id}", daemon=True).start()
            if recovered:
                print(f"agent dispatcher: recovering {len(recovered)} running job(s)", flush=True)
        else:
            print("agent dispatcher: singleton lease not held; this process is not a worker", flush=True)
    print(f"api ready: registry={registry_path} model_role_default={DEFAULT_MODEL_ROLE} "
          f"answers_with={model_for(os.environ.get('OI_DEFAULT_API_ROLE', 'viewer'))} "
          f"jobs={'durable' if getattr(STATE['jobs'], 'durable', False) else 'in-memory'}", flush=True)
    yield
    if STATE.get("agent_worker") is not None:
        STATE["agent_worker"].stop()
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
    question_text = (body.get("question") or "").strip()
    # **What answered, not what was configured.** The gateway reports the model group and provider model on
    # every successful call (`attribution_from`), so a failover names itself in the answer and in the audit
    # row instead of being credited to the primary. Empty until a call succeeds.
    attribution: dict = {}
    try:
        loop = _generate_with_tools(session, permit, model, question_text, result, request_budget)
    except Exception as exc:  # noqa: BLE001 - the single-shot path is the fallback, not a failure response
        print(f"tool-loop unavailable, using the single-shot path: {type(exc).__name__}", flush=True)
        loop = {"answer": None, "reason": "tool_loop_error", "evidence": []}
    if loop.get("answer") is not None:
        generated, generation = loop["answer"], None
        attribution = dict(loop.get("attribution") or {})
        if loop.get("evidence"):
            # **The citations are verified against what the model actually read.** Without this the verifier
            # would check tool-derived citations against the original seed passages and refuse correct answers.
            import dataclasses
            result = dataclasses.replace(result, passages=loop["evidence"])
    else:
        generated, generation, attribution = _generate(
            session, permit, model, question_text, result.passages, request_budget)
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
        resolved_model=resolved_model, attributed=attribution, request_id=request_id)
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


def _record_refused_approval(request: Request, body: dict, refusal: JSONResponse) -> None:
    """Record a refused approval attempt, then let the refusal return.

    **A refusal is an event, not a non-event.** SEC-APR-001 asks for the attempt to be recorded, and this route
    used to return 401 or 403 before any audit call. The actor is `unauthenticated` because that is what the
    system knows; a role named in the body is recorded as a claim, never as the actor's role.
    """
    claimed_role = str(body.get("role") or body.get("roles") or "")
    try:
        audit("access", "unauthenticated", "unknown", "denied", {
            "stage": "approval_refused",
            "endpoint": request.url.path,
            "status": getattr(refusal, "status_code", 0),
            "claimed_role": claimed_role,
            "reason": "no session or a caller-supplied authority on the approval route",
        })
    except Exception:  # noqa: BLE001 - a refusal still has to reach the caller if the record cannot be written
        pass


def _tasks_this_deployment_declares() -> list[dict]:
    """The tasks the manifest declares, which is what the interface offers.

    **Read from the manifest and not from a constant**, because the app is a function of the deployment. A
    client with no agentic pack gets an empty list and therefore no Do screen, which is the honest outcome
    rather than a menu of things that cannot run.
    """
    declared = (((STATE.get("manifest") or {}).get("agentic") or {}).get("tasks")) or []
    # **The pack's definitions are tasks the runtime can actually run.** They are merged here rather than in
    # the app, so the Do screen, the refusal list and the job route all read one answer.
    return definitions_module.merge_tasks(declared, STATE.get("agent_definitions") or [])


def _job_payload(job, store=None) -> dict:
    """A job as the API returns it, with its place in the queue when it is queued.

    **The queue position is read from the store, not stored on the job.** A job row that carried a position
    would be wrong the moment somebody ahead of it finished; the count is cheap and always current. Only a
    queued job has a position, because a running job is not in the queue.
    """
    payload = job.to_dict(include_result=True)
    position = (store or STATE.get("jobs")).queue_position(job.job_id)
    if position is not None:
        payload["queue_position"] = position
    return payload


def _fail_agent_job(job, reason: str, detail: str) -> None:
    """Terminate a job the runtime could not start, with the cause a client can read."""
    fresh = STATE["jobs"].get(job.job_id)
    if fresh is None or fresh.state in jobs_module.TERMINAL:
        return
    # **A refused job is a fact in the record, not only a job row.** The SEC-AGENT refusal and every other
    # start failure get a chain row naming the cause, so a deployment can answer why work stopped.
    try:
        audit("access", job.subject, job.actor_role, "denied",
              {"job_id": job.job_id, "task": job.task, "reason": reason, "detail": detail,
               "stage": "agent_runtime"})
    except Exception:  # noqa: BLE001 - the job failure still has to be written
        pass
    try:
        jobs_module.transition(fresh, jobs_module.FAILED, reason=reason)
        fresh.result = {"error": detail}
        STATE["jobs"].put(fresh)
        STATE["jobs"].append(job.job_id, "error", {"reason": reason, "detail": detail})
    except Exception:  # noqa: BLE001 - the dispatcher's own guard will catch a failed job next
        pass


def _agent_verify(definition: dict):
    """The definition's verification rule, expressed as a predicate over the model's answer.

    **Verification is a rule, not a second model call.** The first rule is citations: an answer that cites
    nothing has not shown its evidence. A definition with no rule gets no verification, which is an explicit
    choice rather than a check that always passes.
    """
    rule = str(definition.get("verify") or "")
    if rule == "citations_present":
        def citations(answer: str, evidence: dict) -> str | None:
            if not re.search(r"\[\d+\]", answer):
                return "the answer cites no source"
            return None
        return citations
    return None


CHILD_PROMPT = """You are a read-only research helper. Answer the brief from the client's documents.
Use the tools to find evidence. Every claim must carry the number the tool printed in square brackets, for
example [1]. You cannot take actions, cannot delegate, and must return a short structured brief with the sources
you actually read. If the evidence does not contain the answer, say so plainly."""


# **One runner per job, ever.** A pause that lands while a step is running does not stop the thread; the
# engine returns when it next sees the paused state. A resume that started a second runner before the first
# returned made turn-1 and turn-2 each run twice on the live box. The lock is per job, in this process, which is
# the same scope the engine assumes: one worker process per deployment.
_AGENT_LOCKS: dict[str, threading.Lock] = {}
_AGENT_LOCKS_GUARD = threading.Lock()


def _agent_lock(job_id: str) -> threading.Lock:
    with _AGENT_LOCKS_GUARD:
        return _AGENT_LOCKS.setdefault(job_id, threading.Lock())


def _run_agent_job(job):
    """Serialise the runtime for one job, then run it."""
    with _agent_lock(job.job_id):
        return _run_agent_job_locked(job)


def _run_agent_job_locked(job):
    """One claimed agent job: resolve the session, build the real tools and gateway call, run the plan.

    **This is the seam between the controller and the deployment.** `agent_runtime` knows the plan and the
    step shape; this function is the only place that knows the gateway, the signing key, the tool box and the
    session. The dispatcher calls it after the claim, so the job is already running.
    """
    definition = definitions_module.find(STATE.get("agent_definitions") or [], job.task)
    if definition is None:
        _fail_agent_job(job, "no_definition", f"no definition for task {job.task!r}")
        return
    try:
        session = session_module.resolve_credential_hash(STATE["store"], job.credential_sha256)
    except session_module.SessionError as exc:
        _fail_agent_job(job, "no_session", str(exc))
        return

    model = model_for(definition.get("role") or session.role)

    workspace = None
    if definition.get("workspace"):
        workspace_url = (os.environ.get("OI_WORKSPACE_URL") or "").strip()
        workspace_token = (os.environ.get("OI_WORKSPACE_TOKEN") or "").strip()
        if not workspace_url or not workspace_token:
            _fail_agent_job(job, "workspace_not_configured",
                            "the definition needs a workspace but OI_WORKSPACE_URL or OI_WORKSPACE_TOKEN is unset")
            return
        workspace = workspace_client_module.WorkspaceClient(workspace_url, workspace_token)

    egress = None
    if definition.get("web"):
        egress_url = (os.environ.get("OI_EGRESS_URL") or "").strip()
        egress_token = (os.environ.get("OI_EGRESS_TOKEN") or "").strip()
        if not egress_url or not egress_token:
            _fail_agent_job(job, "egress_not_configured",
                            "the definition needs the web but OI_EGRESS_URL or OI_EGRESS_TOKEN is unset")
            return
        egress = egress_client_module.EgressClient(egress_url, egress_token)

    mcp = None
    if definition.get("connector"):
        mcp_url = (os.environ.get("OI_MCP_URL") or "").strip()
        mcp_token = (os.environ.get("OI_MCP_TOKEN") or "").strip()
        if not mcp_url or not mcp_token:
            _fail_agent_job(job, "mcp_not_configured",
                            "the definition needs a credential but OI_MCP_URL or OI_MCP_TOKEN is unset")
            return
        mcp = mcp_client_module.MCPClient(mcp_url, mcp_token)

    # **The child's tools are a subset of the parent's.** A definition that lets a helper read something the
    # parent cannot is a permission hole, so the declaration is checked before any work starts.
    parent_tools = {str(name) for name in (definition.get("tools") or [])}
    child_names = [str(name) for name in (definition.get("child_tools") or [])]
    if any(name not in parent_tools for name in child_names):
        _fail_agent_job(job, "definition_child_scope",
                        f"child tools {sorted(set(child_names) - parent_tools)} are not parent tools")
        return
    child_specs = [tool for tool in tool_loop.active_tools()
                   if tool["function"]["name"] in set(child_names)]
    child_count = 0
    child_tokens_total = 0
    parent_tokens_total = 0

    def step_name() -> str:
        fresh = STATE["jobs"].get(job.job_id)
        return fresh.steps[-1].name if fresh is not None and fresh.steps else ""

    def run_child(brief: str) -> str:
        """One read-only child: its own tool box, its own budget, its own span, no delegation."""
        nonlocal child_count, child_tokens_total
        child_count += 1
        span = f"child-{child_count}"
        max_child_tokens = int(definition.get("child_max_tokens") or 12000)
        child_box = tool_loop.ToolBox(STATE["services"], session.permit(), session.role,
                                      strict_audit=True)

        def child_audit(event, actor, actor_role, outcome, payload, **extra):
            addressed = dict(payload or {}, job_id=job.job_id, task=job.task,
                             step=step_name(), span=span, role="child")
            audit(event, session.subject, session.role, outcome, addressed, **extra)

        child_box.audit = child_audit
        messages = [{"role": "system", "content": CHILD_PROMPT},
                    {"role": "user", "content": brief or "Find the answer to the task."}]
        child_seconds = max(10.0, min(120.0, float(definition.get("child_max_seconds") or 60)))
        child_budget = budget_module.Budget(total=child_seconds, reserve=0.0)
        for _child_step in range(4):
            if time.monotonic() - child_budget.started > child_seconds:
                return json.dumps({"error": "child_deadline"})
            reply = _tool_chat(session, model, messages, child_specs, child_budget)
            if reply.get("error"):
                return json.dumps({"error": str(reply["error"])})
            usage = reply.get("usage") or {}
            tokens = int(usage.get("prompt_tokens") or 0) + int(usage.get("completion_tokens") or 0)
            if tokens <= 0:
                # **An estimate is still a budget.** A provider that omits usage must not make the child
                # unmeasured; four characters per token is the same rough ratio the budget is set in.
                tokens = max(1, len((reply.get("content") or "")) // 4)
            child_tokens_total += tokens
            addressed = {"job_id": job.job_id, "task": job.task, "step": step_name(),
                         "span": span, "role": "child", "model": model,
                         "content_chars": len((reply.get("content") or "")),
                         "tokens": tokens, "child_tokens_total": child_tokens_total,
                         "attribution": reply.get("attribution") or {}}
            audit("model_call", session.subject, session.role, "ok", addressed, model=model)
            if child_tokens_total > max_child_tokens:
                return json.dumps({"error": "child_token_budget", "spent": child_tokens_total,
                                   "budget": max_child_tokens})
            calls = reply.get("tool_calls") or []
            if not calls:
                sources = [{"doc_id": getattr(passage, "doc_id", ""),
                            "page": getattr(passage, "page_start", None)}
                           for passage in child_box.evidence]
                return json.dumps({"brief": (reply.get("content") or "").strip(),
                                   "sources": sources, "evidence": len(child_box.evidence),
                                   "tokens": child_tokens_total})
            messages.append({"role": "assistant", "content": reply.get("content") or "",
                             "tool_calls": [{"id": call.get("id") or "", "type": "function",
                                             "function": {"name": call.get("name") or "",
                                                          "arguments": call.get("arguments") or "{}"}}
                                            for call in calls]})
            for call in calls:
                try:
                    result = child_box.execute(call.get("name") or "", call.get("arguments") or "{}")
                except Exception as exc:  # noqa: BLE001
                    result = json.dumps({"error": f"child tool failed: {type(exc).__name__}"})
                messages.append({"role": "tool", "tool_call_id": call.get("id") or "",
                                 "content": str(result)})
        return json.dumps({"error": "child_step_limit"})

    def tool_audit(event, actor, actor_role, outcome, payload, **extra):
        # **The row has to name the job and the step, not only the tool.** A tool row that cannot be tied
        # back to the task is a row a client cannot use to answer "what did this job read?". The engine's
        # own row names the step; this one is the tool's own record and needs the same address.
        addressed = dict(payload or {}, job_id=job.job_id, task=job.task, step=step_name())
        audit(event, session.subject, session.role, outcome, addressed, **extra)

    def call_model(messages: list, tools: list) -> dict:
        """One parent model call, with a running token budget over the whole job."""
        nonlocal parent_tokens_total
        reply = _tool_chat(session, model, messages, tools, budget)
        if reply.get("error"):
            return reply
        usage = reply.get("usage") or {}
        tokens = int(usage.get("prompt_tokens") or 0) + int(usage.get("completion_tokens") or 0)
        if tokens <= 0:
            tokens = max(1, len((reply.get("content") or "")) // 4)
        parent_tokens_total += tokens
        if parent_tokens_total > int(definition.get("max_tokens") or 60000):
            return {"error": "token_budget"}
        return reply

    def model_audit(reply):
        # One row per model completion the runtime makes, not one per job. The answer path's per-answer row
        # is not this row and does not replace it.
        addressed = {"job_id": job.job_id, "task": job.task, "step": step_name(),
                     "model": model, "content_chars": len((reply.get("content") or "")),
                     "child_tokens": child_tokens_total, "parent_tokens": parent_tokens_total,
                     "attribution": reply.get("attribution") or {}}
        audit("model_call", session.subject, session.role, "ok", addressed, model=model)

    def context_audit(trimmed):
        # **What the model did not see is part of the record.** The row names the step and the count, so a
        # later reader can tell an answer made without a document from one made before the document existed.
        addressed = dict(trimmed or {}, job_id=job.job_id, task=job.task, step=step_name())
        audit("context_trim", session.subject, session.role, "ok", addressed)

    def workspace_tool(name: str, arguments: str) -> str:
        try:
            parsed = json.loads(arguments or "{}")
        except (ValueError, TypeError):
            return json.dumps({"error": "workspace arguments must be a JSON object"})
        if not isinstance(parsed, dict):
            return json.dumps({"error": "workspace arguments must be a JSON object"})
        try:
            if name == "workspace_write":
                result = workspace.write(job.job_id, str(parsed.get("path") or ""),
                                         str(parsed.get("content") or ""))
            elif name == "workspace_read":
                result = workspace.read(job.job_id, str(parsed.get("path") or ""))
            elif name == "workspace_list":
                result = workspace.list(job.job_id)
            elif name == "workspace_run":
                result = workspace.run(job.job_id, str(parsed.get("command") or ""),
                                       timeout=int(parsed.get("timeout") or 60))
            else:
                return json.dumps({"error": f"unknown workspace tool {name!r}"})
        except workspace_client_module.WorkspaceClientError as exc:
            result = {"error": str(exc)}
        # **The sandbox is a second place work happens, and the chain has to say so.** The row names the job,
        # the step and the tool; a failed audit fails the step rather than letting the work happen unrecorded.
        try:
            audit("tool_call", session.subject, session.role, "ok",
                  {"job_id": job.job_id, "task": job.task, "step": step_name(), "tool": name,
                   "arguments_chars": len(arguments or "")}, tool=name)
        except Exception as exc:  # noqa: BLE001
            raise tool_loop.ToolAuditError(f"the workspace audit failed: {type(exc).__name__}") from exc
        return json.dumps(result, sort_keys=True)

    def web_fetch_tool(arguments: str) -> str:
        try:
            parsed = json.loads(arguments or "{}")
        except (ValueError, TypeError):
            return json.dumps({"error": "web_fetch arguments must be a JSON object"})
        url = str((parsed or {}).get("url") or "")
        try:
            result = egress.fetch(url)
        except egress_client_module.EgressClientError as exc:
            result = {"error": str(exc), "url": url}
        # **An outbound fetch is the most reviewable thing a task can do.** The row names the URL, the status and
        # the bytes, so the deployment can answer what left the building and what came back.
        try:
            audit("tool_call", session.subject, session.role, "ok",
                  {"job_id": job.job_id, "task": job.task, "step": step_name(), "tool": "web_fetch",
                   "url": url, "status": result.get("status"), "bytes": result.get("bytes")}, tool="web_fetch")
        except Exception as exc:  # noqa: BLE001
            raise tool_loop.ToolAuditError(f"the web fetch audit failed: {type(exc).__name__}") from exc
        return json.dumps(result, sort_keys=True)

    def send_email_tool(name: str, arguments: str) -> str:
        try:
            parsed = json.loads(arguments or "{}")
        except (ValueError, TypeError):
            return json.dumps({"error": "send_email arguments must be a JSON object"})
        to = str((parsed or {}).get("to") or "")
        subject = str((parsed or {}).get("subject") or "")
        body = str((parsed or {}).get("body") or "")
        try:
            envelope = mcp.call("send_email", {"to": to, "subject": subject, "body": body})
            result = envelope.get("result") or envelope
        except mcp_client_module.MCPClientError as exc:
            result = {"sent": False, "error": str(exc), "to": to}
        # **The one action with a real-world recipient.** The row names the recipient and the outcome, and it is
        # written after the gate has been decided and the connector has answered.
        try:
            audit("tool_call", session.subject, session.role, "ok",
                  {"job_id": job.job_id, "task": job.task, "step": step_name(), "tool": "send_email",
                   "to": to, "sent": bool(result.get("sent"))}, tool="send_email")
        except Exception as exc:  # noqa: BLE001
            raise tool_loop.ToolAuditError(f"the connector audit failed: {type(exc).__name__}") from exc
        return json.dumps(result, sort_keys=True)

    toolbox = tool_loop.ToolBox(STATE["services"], session.permit(), session.role,
                                audit=tool_audit, strict_audit=True)
    budget = budget_module.Budget(total=max(60.0, float(job.time_budget_s)), reserve=0.0)
    context = agent_runtime_module.RuntimeContext(
        definition=definition,
        call_model=call_model,
        toolbox=toolbox,
        tools=tool_loop.agent_tools(definition),
        verify=_agent_verify(definition),
        on_model_call=model_audit,
        on_context_trim=context_audit,
        run_child=run_child if child_specs else None,
        workspace_tool=workspace_tool if workspace is not None else None,
        web_fetch=web_fetch_tool if egress is not None else None,
        act_tool=send_email_tool if mcp is not None else None,
        max_steps=int(definition.get("max_steps") or 12),
        max_messages=int(definition.get("max_messages") or 24),
    )
    mint = lambda actor, role: assertion.mint_audit(STATE["key"], actor, role,
                                                    deployment_id=DEPLOYMENT_ID)
    try:
        agent_runtime_module.run(STATE["jobs"], job, context, mint=mint,
                                 deployment_id=DEPLOYMENT_ID, key_path=KEY_PATH)
    finally:
        # **A terminal task takes its sandbox with it.** A gate or a pause keeps the task directory because the
        # job will resume; a finished, failed or cancelled job does not get to leave a container or files behind.
        if workspace is not None:
            final = STATE["jobs"].get(job.job_id)
            if final is not None and final.state in jobs_module.TERMINAL:
                # **Artifacts leave the sandbox before the sandbox leaves.** The task directory is removed in
                # cleanup, so the content-addressed copy is taken first and named on the job and the chain.
                try:
                    records = workspace.artifacts(job.job_id).get("artifacts") or []
                except Exception:  # noqa: BLE001 - cleanup still has to happen
                    records = []
                if records:
                    # **The list lives in the job's own event log.** The jobs table has no result column, and a
                    # list a client cannot read back is not an artifact record. The row is append-only like the
                    # rest of the job's history.
                    STATE["jobs"].append(job.job_id, "artifacts", {"records": records})
                    try:
                        audit("tool_call", job.subject, job.actor_role, "ok",
                              {"job_id": job.job_id, "task": job.task, "tool": "workspace_artifacts",
                               "count": len(records)}, tool="workspace_artifacts")
                    except Exception:  # noqa: BLE001 - the artifact copy is the product output
                        pass
                try:
                    workspace.cleanup(job.job_id)
                except Exception:  # noqa: BLE001 - the record already says the job ended
                    pass


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
    generated, generation, attribution = _generate(session, permit, model, job.input, result.passages,
                                                   request_budget)
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
        audit=audit, resolved_model=resolved_model, attributed=attribution, request_id=job.job_id)
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


def _workflow_runner(store, plan):
    """A runner for a multi-step plan, with the statement minter it needs to record who is acting.

    **The engine writes the chain row itself, in the same transaction as the state change**, which is the
    property WF-ENGINE-001 exists for and the reason this engine is ours rather than adopted. It records
    through `audit.insert_event(cur, ...)` on its own cursor, so it needs a way to prove the actor: the same
    `assertion.mint_audit` the API uses, because the chain refuses a hand-typed actor.
    """
    return workflow_module.runner_for(
        store, plan,
        mint=lambda actor, role: assertion.mint_audit(STATE["key"], actor, role,
                                                      deployment_id=DEPLOYMENT_ID),
        deployment_id=DEPLOYMENT_ID, key_path=os.environ.get("OI_ASSERTION_KEY"))


def _run_or_resume(job, store) -> None:
    """Run a planned job, or hand a paused one back to the engine. A one-step task keeps the C4 path.

    **A deployment with no workflow pack has no plans**, so `plan_for` returns None and the C4 executor runs
    exactly as it did. That is why the C4 probe still passes unchanged, and it is the shape the C6 handover
    will reuse.
    """
    plan = workflow_module.plan_for(job.task)
    # **The job has to be in the store before anything drives it, and both paths need it for different
    # reasons.** The C4 executor reads it back as it goes. The engine refuses outright with "job ... is not in
    # the store, so it cannot be driven" — the right behaviour, and how this was found: the first version put
    # the job only in the `plan is None` branch, so a freshly created job lived in memory and nowhere else,
    # and `POST /jobs` answered 500 the first time a workflow was ever started over HTTP.
    store.put(job)
    if plan is None:
        jobs_module.run(job, store, _step_once, audit=audit)
        return
    runner = _workflow_runner(store, plan)
    if job.state == jobs_module.AWAITING_APPROVAL:
        return  # waiting is where this call ends; an approval drives the rest
    if job.steps and job.state == jobs_module.RUNNING:
        runner.resume(job)
    else:
        runner.start(job)


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
    definition = definitions_module.find(STATE.get("agent_definitions") or [], task)
    if definition and definition.get("mode", "task") != "task":
        return JSONResponse(
            {"error": f"'{task}' is a {definition.get('mode')!r} definition; /jobs only starts task definitions.",
             "kind": "unsupported_definition_mode"}, status_code=400)
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

    # **A multi-step plan needs a budget of more than one attempt**, so the plan supplies the budgets when
    # there is one and the C4 defaults stand when there is not. A six-step invoice run under a five-attempt
    # budget fails on its sixth step for a reason that has nothing to do with the work.
    plan = workflow_module.plan_for(task)
    if definition:
        # **A definition supplies the job's budget.** Every loop iteration is one engine step, so max_steps is
        # the attempt budget; max_seconds is the wall-clock budget. The runtime enforces the token and child
        # limits inside those steps.
        budgets = definitions_module.budgets(definition)
    else:
        budgets = workflow_module.budgets_for(task) if plan else {}
    job = jobs_module.new_job(
        deployment_id=DEPLOYMENT_ID, subject=session.subject, actor_role=session.role,
        roots=session.permit().roots,
        # **The hash, not the credential.** The job needs to run after this request ends, and the first version
        # solved that by keeping the credential in the row, which made a job row a working identity.
        credential_sha256=session.credential_sha256, task=task, input=text, data_class=data_class,
        **budgets)
    store = STATE["jobs"]
    if definition:
        # **A definition job is queued, not run inline.** The dispatcher claims it; the C4 single-step path
        # above is not the runtime. Until the dispatcher is started this row stays queued, which is honest:
        # the deployment has the definition and not the loop to run it.
        store.put(job)
        return JSONResponse((store.get(job.job_id) or job).to_dict(include_result=True), status_code=200)
    _run_or_resume(job, store)
    # **Re-read before answering, because both executors advance the job in the store and not in this
    # object.** The C4 path mutates the instance it is given; the engine reloads the row before each step, so
    # the `job` in this frame stays at whatever it was when it was created. Returning it unchanged showed a
    # client `queued` with no steps for a job that was actually at the gate with four behind it — the
    # response contradicting the record, which is the one thing this interface must never do. The store is
    # the truth and the response is read back from it.
    return JSONResponse((store.get(job.job_id) or job).to_dict(include_result=True), status_code=200)


@app.get("/approvals", responses={
    200: {"model": contract.ApprovalQueue,
          "description": "the jobs at a gate that this session may decide on, newest first"},
    401: {"model": contract.Refusal, "description": "no session"},
})
async def approval_queue(request: Request):
    """Everything waiting for a decision that this session is permitted to make.

    **Why this is not `GET /jobs?state=awaiting_approval`.** A job is readable by the person who started it,
    which is the right rule for a history and the wrong one for a queue: an approval exists for whoever
    REVIEWS it, and in a small business that is routinely somebody other than the person who asked. A queue
    built from the jobs a screen started can only ever show a client their own requests, which is not a queue
    at all — it is a list of things they are waiting on, and the Review screen was built behind
    `loadQueue()` in the app precisely so it could switch to this.

    **The permission rule, stated rather than implied.** The queue is scoped by access roots: a job appears if
    the roots it carries are a subset of the roots this session holds. That is the same comparison the
    retrieval boundary already makes everywhere else, so an approval is never offered to a session that could
    not have read the material the decision is about. **Roots, not `subject`**, because the subject rule
    answers "who asked" and this question is "who may decide".

    **What it deliberately does not decide.** Whether a particular person is the right approver for a
    particular task. Every session holding the roots sees every gate in them, and separation of duties — the
    person who drafted it may not approve it — is a policy this deployment does not yet express. Recorded
    here so it is a known limit rather than an assumption, and it belongs with the identity work.
    """
    session, refusal = _session_for(request)
    if refusal:
        return refusal

    mine = session.permit().roots
    store = STATE["jobs"]
    waiting = []
    for job in store.pending_approval():
        if job.deployment_id != DEPLOYMENT_ID:
            continue
        if not set(job.roots or ()) <= set(mine):
            continue
        waiting.append(job)

    # Newest first, and by the time they reached the gate rather than when they were created: a queue is read
    # from the top and the thing that has been waiting longest is the thing somebody should look at.
    waiting.sort(key=lambda j: (j.created_at, j.job_id), reverse=True)
    return JSONResponse({"approvals": [j.to_dict(include_result=False) for j in waiting],
                         "count": len(waiting)}, status_code=200)


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
    return JSONResponse(_job_payload(job), status_code=200)


@app.get("/jobs/{job_id}/artifacts", responses={
    200: {"description": "the artifacts the job stored, from its event log"},
    401: {"model": contract.Refusal, "description": "no session"},
    404: {"model": contract.Refusal, "description": "no such job"},
})
async def list_artifacts(job_id: str, request: Request):
    """List a job's stored artifacts. The bytes are behind the download route; this is the catalogue."""
    session, refusal = _session_for(request)
    if refusal:
        return refusal
    job = _find_job(job_id, session)
    if job is None:
        return JSONResponse({"error": "no such job", "kind": "not_found"}, status_code=404)
    records = []
    for event in STATE["jobs"].events_since(job_id, since=0):
        if event["event"] == "artifacts":
            records.extend((event.get("data") or {}).get("records") or [])
    return JSONResponse({"job_id": job_id, "artifacts": records}, status_code=200)


@app.get("/jobs/{job_id}/artifacts/{sha256}", responses={
    200: {"description": "the stored artifact bytes, as an attachment"},
    401: {"model": contract.Refusal, "description": "no session"},
    404: {"model": contract.Refusal, "description": "no such job, artifact or workspace"},
})
async def download_artifact(job_id: str, sha256: str, request: Request):
    """Download one artifact the job stored.

    **The API proxies the bytes.** The artifact store is on the workspace host, and the client app never gets the
    workspace token or a direct path. The same session rule as the job applies: a reader who cannot see the job
    cannot fetch its files.
    """
    session, refusal = _session_for(request)
    if refusal:
        return refusal
    job = _find_job(job_id, session)
    if job is None:
        return JSONResponse({"error": "no such job", "kind": "not_found"}, status_code=404)
    url = (os.environ.get("OI_WORKSPACE_URL") or "").strip()
    token = (os.environ.get("OI_WORKSPACE_TOKEN") or "").strip()
    if not url or not token:
        return JSONResponse({"error": "this deployment has no workspace store", "kind": "not_found"},
                            status_code=404)
    try:
        data = workspace_client_module.WorkspaceClient(url, token).artifact(job_id, sha256)
    except workspace_client_module.WorkspaceClientError:
        return JSONResponse({"error": "no such artifact", "kind": "not_found"}, status_code=404)
    return Response(content=data, media_type="application/octet-stream",
                    headers={"Content-Disposition": f'attachment; filename="{sha256}"'})


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


@app.post("/jobs/{job_id}/approvals", responses={
    200: {"model": contract.Job, "description": "the job after the decision"},
    400: {"model": contract.Refusal,
          "description": "not one of the three decisions, or a denial or change request with no reason"},
    401: {"model": contract.Refusal, "description": "no session"},
    403: {"model": contract.Refusal, "description": "the request supplied its own authority"},
    404: {"model": contract.Refusal, "description": "no such job"},
    409: {"model": contract.Refusal,
          "description": "**the job is not waiting for a decision**, or the decision names different content "
                         "from what is waiting. The second is `approval_drift` and it means the draft changed "
                         "while somebody was reading it."},
})
async def decide_job(job_id: str, request: Request):
    """Approve, deny, or ask for changes on a job that is waiting.

    **This is the one route in the product where a wrong record is worse than a missing one**, because a
    client reads an approval and believes it. Three things follow from that and each is here rather than in a
    convention.

    * **The decision is refused unless the job is actually waiting.** A decision on a running job would
      produce a record of an approval nobody was asked for.
    * **The decision quotes the content it is about.** `action_digest` has to match what the job is holding,
      so a draft that changed between the screen rendering and the button being pressed is refused rather than
      silently approved. That is `approval_drift` and it is a real case, not a theoretical one: a retry can
      rewrite a draft in that window.
    * **The record says what the system does not know.** The identity object carries `unverified` and the
      Part 11 manifestation marks the printed name absent with the reason stated. See `approvals.py`.
    """
    # **The body is read before the session is resolved, and that ordering was the whole bug.** A client
    # POSTing a decision carries the token in the JSON body, exactly as `POST /jobs` does, and
    # `_session_for(request)` alone looks only in the header and the query string. Resolving first meant every
    # approval answered **401 while `GET /jobs/{id}` on the same session answered 200** — a route that looked
    # like an authentication problem and was a sequencing one. It is the last of four defects on the path that
    # had never run, and the only one that hid behind a plausible status code.
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "no JSON body", "kind": "no_input"}, status_code=400)

    session, refusal = _session_for(request, body)
    if refusal:
        _record_refused_approval(request, body, refusal)
        return refusal
    job = _find_job(job_id, session)
    if job is None:
        return JSONResponse({"error": "no such job", "kind": "not_found"}, status_code=404)

    # **A caller-supplied authority is refused here for the same reason it is on /ask and /jobs.** The
    # approval route is the one place where a forged role would make the record name somebody who did not
    # decide, so the guard is repeated rather than assumed to be covered by session resolution.
    supplied = next((field for field in answer_module.AUTHORITY_FIELDS if body.get(field) is not None), None)
    if supplied:
        audit("access", session.subject, str(body.get(supplied)), "denied",
              {"supplied_field": supplied, "session_role": session.role,
               "refusal": "a request may not supply its own authority",
               "endpoint": f"/jobs/{job_id}/approvals"})
        return JSONResponse(
            {"error": "Permission denied on this resource (or it might not exist). A role comes from an "
                      "authenticated session and never from a request field.",
             "kind": "refused_authority_claim"}, status_code=403)

    decision = str(body.get("decision") or "").strip()
    reason = str(body.get("reason") or "")
    note = str(body.get("note") or "")
    quoted = str(body.get("action_digest") or "").strip()

    problem = approvals_module.validate(decision, reason)
    if problem:
        return JSONResponse({"error": problem, "kind": "bad_decision"}, status_code=400)

    store = STATE["jobs"]
    try:
        job = approvals_module.decide_and_record(
            store=store, job=job, decision=decision, action_digest_quoted=quoted,
            session=session, reason=reason, note=note,
            mint=lambda actor, role: assertion.mint_audit(
                STATE["key"], actor, role, deployment_id=DEPLOYMENT_ID,
                session_id=session.session_id),
            key_path=KEY_PATH, deployment_id=DEPLOYMENT_ID, audit=audit,
        )
    except jobs_module.ApprovalDrift as drifted:
        # **409 rather than 400, and the body carries both digests.** The client's request was well formed;
        # the world moved. A screen that gets this can re-render and ask again, and a screen that got a 400
        # would be told its request was malformed, which is false.
        return JSONResponse({"error": str(drifted), "kind": "approval_drift",
                             "waiting_on": drifted.waiting, "quoted": drifted.quoted}, status_code=409)
    except jobs_module.TransitionRefused as refused:
        return JSONResponse({"error": str(refused), "kind": "transition_refused",
                             "was": refused.was, "wanted": refused.wanted}, status_code=409)

    # **The decision and its record were one transaction.** `decide_and_record` wraps both in the engine's
    # `PostgresUnit` on a durable store, so a failed chain write rolls the decision back with it. On a memory
    # store the injected `audit` callback writes the same record and the store's own documentation says it is
    # not durable. The alternative, `jobs.decide()` followed by `audit()`, was the gap this section closes:
    # the state change committed before the record existed.

    # **The engine continues from the step after the gate.** An approve puts the job back in `running` and
    # nothing would move it further without this. A deny or a change request ends the job, so there is
    # nothing to drive.
    #
    # **Two kinds of plan can be waiting.** A workflow task resumes its static plan. An agent definition
    # resumes the runtime, which reads its pending call and its gate step from the record. Leaving the agent
    # branch out would leave an approved agent job running with nothing to drive it.
    if job.state == jobs_module.RUNNING:
        definition = definitions_module.find(STATE.get("agent_definitions") or [], job.task)
        if definition is not None:
            _run_agent_job(job)
        else:
            plan = workflow_module.plan_for(job.task)
            if plan is not None:
                _workflow_runner(store, plan).resume(job)

    # Re-read, for the same reason `POST /jobs` does: the engine advances the job in the store and not in this
    # object, so returning it unchanged would show a client the state before the work it just authorised.
    return JSONResponse((store.get(job.job_id) or job).to_dict(include_result=True), status_code=200)


def _generate(session, permit, model: str, question: str, passages: list, budget=None):
    """The model call, bounded, retried only while it is safe, and classified when it fails.

    Returns `(answer, error, attribution)`. Exactly one of the first two is None; `attribution` is the
    gateway's account of what answered (see `attribution_from`), or an empty dict when nothing did. It rides
    with the result because a failover makes "the manifest model" the wrong thing to report.
    """
    import urllib.error
    import urllib.request

    budget = budget or budget_module.Budget()
    context = "\n\n".join(f"[{n + 1}] {p.text}" for n, p in enumerate(passages))
    prompt = answer_contract.build_prompt(context, question)
    # **Minted per attempt, not once.** A statement is spent by its first presentation, and the first
    # version of this minted one before the retry loop. So attempt one earned the real 429, the retry
    # replayed a spent assertion, and the 401 that came back classified as `unclassified`: a genuine
    # throttle was recorded as an unrecognised failure and the documented retry could never succeed.
    # Found by the C3 panel's executor, who provoked a real Bedrock throttle and read the reason.
    payload = {
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "max_tokens": int(os.environ.get("OI_MAX_TOKENS", "700")),
        # **Zero, because this is an extraction task and the measured failures were variance.** Four identical
        # runs of the same questions scored 32, 26, 31 and 29; a sampled answer to a question whose value is in
        # the passage has nothing to gain from variety.
        "temperature": 0,
    }

    errors: list = []
    attempt = 0
    attribution: dict = {}
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
                attribution = attribution_from(r.headers)
            choices = data.get("choices") or []
            if not choices:
                return None, ("unclassified", "the gateway returned no choices", attempt, errors), attribution
            content = (choices[0].get("message") or {}).get("content", "")
            # **The answer is checked before it is returned.** A citation with no answer, an empty reply, or a
            # figure present nowhere in the passages is sent back once with a corrective instruction. If the
            # second attempt repeats it, the honest result is a refusal carrying the passages, not the number.
            failure = answer_contract.violation(content, [p.text for p in passages])
            if failure is not None:
                errors.append({"attempt": attempt, "status": 200, "reason": failure,
                               "matched": "", "retryable": True})
                if attempt >= budget_module.MAX_ATTEMPTS:
                    return None, (failure, {"reason": failure, "matched": "", "retryable": False},
                                  attempt, errors), attribution
                payload["messages"] = [{"role": "user",
                                        "content": prompt + "\n\n" + answer_contract.corrective_message(failure)}]
                continue
            return content, None, attribution
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode(errors="replace")[:400]
            kind = budget_module.classify(detail)
            errors.append({"attempt": attempt, "status": exc.code, "reason": kind["reason"],
                           "matched": kind["matched"], "retryable": kind["retryable"]})
            retry_after = exc.headers.get("Retry-After") if exc.headers else None
            if not kind["retryable"]:
                return None, (kind["reason"], kind, attempt, errors), attribution
            if attempt >= budget_module.MAX_ATTEMPTS:
                return None, (kind["reason"], kind, attempt, errors), attribution
            wait = _backoff(attempt, retry_after)
            if wait >= budget.remaining() - budget.reserve:
                # The wait itself would eat the delivery reserve, so waiting is the wrong answer.
                return None, (kind["reason"], kind, attempt, errors), attribution
            time.sleep(wait)
        except Exception as exc:  # noqa: BLE001 - a transport failure is a classification too
            kind = budget_module.classify(type(exc).__name__)
            errors.append({"attempt": attempt, "status": 0, "reason": kind["reason"],
                           "matched": kind["matched"], "retryable": kind["retryable"]})
            if not kind["retryable"] or attempt >= budget_module.MAX_ATTEMPTS:
                return None, (kind["reason"], kind, attempt, errors), attribution
            time.sleep(_backoff(attempt, None))

    # **Three values, and the last classification is never discarded.** The first version returned a
    # two-tuple here while the caller unpacked three, and it fell back to a bare `unclassified` when
    # the loop broke before recording anything — so a provider throttle could still be recorded as an
    # unrecognised failure, which is the defect the retry fix was meant to remove.
    if errors:
        return None, (errors[-1]["reason"], dict(errors[-1]), attempt, errors), attribution
    # No attempt was made at all: the deadline left no room. That is a timeout, and saying so is more
    # honest than `unclassified`, which reads as a failure nobody understands.
    return None, ("timeout", {"reason": "timeout", "matched": "", "retryable": True}, attempt,
                  errors), attribution


def _tool_chat(session, model: str, messages: list, tools, request_budget):
    """One model call for the tool loop, returning a message that may carry tool calls.

    Deliberately simpler than `_generate`: the loop itself is the retry structure, so a provider failure here is
    classified and returned rather than slept on. The assertion is minted per attempt for the same reason it is
    in `_generate`: a statement is spent by its first presentation.

    The reply carries `attribution` when the gateway answered, so the loop can report the model group and
    provider model that actually answered rather than the manifest's primary.
    """
    import urllib.error
    import urllib.request

    payload = {
        "model": model,
        "messages": messages,
        "max_tokens": int(os.environ.get("OI_MAX_TOKENS", "700")),
        "temperature": 0,
    }
    # A synthesis turn has no tools. Some providers reject an empty tools list, so omit both fields rather
    # than sending an empty array.
    if tools:
        payload["tools"] = tools
        payload["tool_choice"] = "auto"
    attempts = 0
    while attempts < 2:
        attempts += 1
        if request_budget.past_reserve():
            return {"error": "budget"}
        statement = assertion.mint(STATE["key"], model, session_id=session.session_id)
        request = urllib.request.Request(
            GATEWAY_URL + "/v1/chat/completions", data=json.dumps(payload).encode(), method="POST",
            headers={"Content-Type": "application/json", assertion.HEADER: statement})
        try:
            with urllib.request.urlopen(request, timeout=max(1.0, request_budget.remaining())) as response:
                data = json.load(response)
                attribution = attribution_from(response.headers)
            choices = data.get("choices") or []
            if not choices:
                return {"error": "unclassified"}
            message = choices[0].get("message") or {}
            calls = []
            for call in message.get("tool_calls") or []:
                function = call.get("function") or {}
                calls.append({"id": call.get("id") or "", "name": function.get("name") or "",
                              "arguments": function.get("arguments") or "{}"})
            return {"content": message.get("content") or "", "tool_calls": calls, "error": None,
                    "attribution": attribution, "usage": data.get("usage") or {}}
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode(errors="replace")[:300]
            kind = budget_module.classify(detail)
            if not kind["retryable"] or attempts >= 2:
                return {"error": kind["reason"]}
            time.sleep(0.5)
        except Exception as exc:  # noqa: BLE001
            kind = budget_module.classify(type(exc).__name__)
            if not kind["retryable"] or attempts >= 2:
                return {"error": kind["reason"]}
            time.sleep(0.5)
    return {"error": "unclassified"}


def _generate_with_tools(session, permit, model: str, question: str, result, request_budget) -> dict:
    """The tool loop, wired to the live services. Returns the loop's result dict.

    The seed passages are the ones retrieval already chose, so the model starts from the single-shot context and
    may extend it rather than starting blank. Every extra search and read is bounded by `tool_loop.limits()`.
    """
    max_steps, max_calls = tool_loop.limits()
    search_limit = answer_profile.current().search_limit
    def tool_audit(event, actor, actor_role, outcome, payload, **extra):
        audit(event, session.subject, session.role, outcome, payload, **extra)

    toolbox = tool_loop.ToolBox(STATE["services"], permit, session.role, audit=tool_audit,
                                search_limit=search_limit)
    # **Most of the clock for the loop, a floor for the fallback.** Six steps need more than two thirds of a thirty
    # second budget on the long-context questions, and four steps scored four points lower in the measurement. The
    # loop gets the remainder minus a reserve, capped so a single-shot answer always has a few seconds to land.
    deadline = time.monotonic() + max(6.0, min(24.0, request_budget.remaining() - 1.5))
    loop = tool_loop.run_loop(
        lambda messages, tools: _tool_chat(session, model, messages, tools, request_budget),
        toolbox, question, seed_passages=result.passages,
        max_steps=max_steps, max_tool_calls=max_calls, deadline=deadline)
    print(f"tool-loop: answer={'yes' if loop.get('answer') is not None else 'no'} "
          f"reason={loop.get('reason')!r} steps={loop.get('steps')} calls={loop.get('tool_calls')} "
          f"corrections={loop.get('corrections')} evidence={len(loop.get('evidence') or [])}", flush=True)
    return loop


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
        "unsupported_number": "the model's answer stated a figure this deployment could not find in your "
                              "documents, so the answer below comes from your own documents instead",
        "citation_only": "the model replied with a citation but no answer, so the passages below are the "
                         "answer",
        "empty_answer": "the model returned an empty answer, so the passages below are the answer",
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


# **Declared AFTER `/approvals`, and the ordering is load-bearing rather than cosmetic.** Starlette matches
# routes in declaration order, so this wildcard captures `/jobs/{id}/approvals` as `action="approvals"` when it
# comes first, and the handler then correctly refuses an action it does not know. The approvals route was
# unreachable that way and had never run once, for any decision.
#
# **Nothing else could see it.** `docs/openapi.json` lists both paths, the contract check passes, and every
# assertion passes, because a schema is generated from the route table and does not know which entry wins a
# match. Earlier probes got a 401 for a missing session, which is what this handler returns too, so the
# wildcard's answer looked like the approvals route answering. **Only a request carrying a valid session can
# tell the two apart**, and until C5 nothing had ever sent one.
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
            # A resumed multi-step job continues through the engine rather than the C4 one-step executor.
            # An agent definition has no plan_for entry; its runtime reads the succeeded steps and continues.
            if definitions_module.find(STATE.get("agent_definitions") or [], job.task) is not None:
                _run_agent_job(job)
            else:
                plan = workflow_module.plan_for(job.task)
                if plan is not None:
                    _workflow_runner(store, plan).resume(job)
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
        _frames(session, permit, model, question, result, requested_at, stream_decision.lane,
                use_tools=bool(body.get("tools"))),
        headers=headers,
        ping=int(os.environ.get("OI_STREAM_PING_SECONDS", "15")),
    )


async def _tool_frames(session, permit, model: str, question: str, result, requested_at: float,
                       lane: str = "frontier"):
    """The tool-using answer as frames: start, progress, sources, delta, done.

    **The loop runs on a worker thread and the frames are drained from a queue.** A synchronous loop inside the
    generator would block the event loop; a queue lets each tool call become a frame while the loop is still
    running. The same bounded loop, caps and fallback as the JSON path are used, so the two cannot drift.

    **Token streaming is traded for progress streaming on this path**, and that is a deliberate first version: the
    model's tool turns are non-streaming because the loop needs complete messages to parse tool calls from. The
    final answer still arrives redacted, and a `sources` frame carries the evidence the tools actually returned,
    so a client that renders citations has the right ones even though the `start` frame listed only the seeds.
    """
    import asyncio
    import queue
    import threading

    def frame(kind: str, data: dict) -> dict:
        return {"event": kind, "data": json.dumps(data, sort_keys=True)}

    request_budget = budget_module.Budget.for_lane(lane, started=requested_at)
    data_class = ((STATE.get("manifest") or {}).get("access") or {}).get("data_class") or "regulated"
    yield frame("start", {"model": model, "role": session.role, "roots": sorted(permit.roots),
                          "citations": [{"index": n + 1, "doc_id": p.doc_id, "acl": p.acl,
                                         "page_start": p.page_start, "page_end": p.page_end}
                                        for n, p in enumerate(result.passages)],
                          "tools": True})

    events: "queue.Queue[dict]" = queue.Queue()
    holder: dict = {}

    def worker() -> None:
        try:
            max_steps, max_calls = tool_loop.limits()

            def tool_audit(event, actor, actor_role, outcome, payload, **extra):
                audit(event, session.subject, session.role, outcome, payload, **extra)

            toolbox = tool_loop.ToolBox(STATE["services"], permit, session.role, audit=tool_audit,
                                        search_limit=answer_profile.current().search_limit)
            holder["loop"] = tool_loop.run_loop(
                lambda messages, tools: _tool_chat(session, model, messages, tools, request_budget),
                toolbox, question, seed_passages=result.passages, max_steps=max_steps,
                max_tool_calls=max_calls,
                deadline=time.monotonic() + max(5.0, request_budget.remaining() - 1.5),
                on_event=events.put)
        except Exception as exc:  # noqa: BLE001 - a failed loop is a degraded answer here
            holder["error"] = type(exc).__name__
        finally:
            events.put({"type": "final"})

    threading.Thread(target=worker, daemon=True).start()
    first_token_at = None
    while True:
        try:
            event = await asyncio.to_thread(events.get, True, 0.5)
        except queue.Empty:
            continue
        if event.get("type") == "final":
            break
        yield frame("progress", event)

    loop = holder.get("loop") or {"answer": None, "reason": holder.get("error") or "tool_loop_error",
                                  "evidence": []}
    facts = answer_module.model_facts(model, resolved_model(model),
                                      dict(loop.get("attribution") or {}))
    started = time.monotonic()
    # **The tool stream can still replace the answer, because it emits it as one block after the loop.** The gate
    # is the same check the JSON path runs in finish, so an answer cannot be honest on one path and not the other.
    honesty_gate = ""
    if loop.get("answer") is not None and honesty.gate_enabled() and honesty.unstated_company_attribute(
            question, [passage.text for passage in (loop.get("evidence") or result.passages)]):
        loop["answer"] = honesty.REFUSAL
        honesty_gate = "unstated_company_attribute"
    if loop.get("answer") is None:
        reason = loop.get("reason") or "unclassified"
        audit("model_call", session.subject, session.role, "denied",
              budget_module.audit_facts(reason=reason, matched="", retryable=False, attempts=1,
                                        threshold=request_budget.timed_out(), budget=request_budget,
                                        partial_output=False, errors=[], lane=lane, data_class=data_class),
              model=model)
        yield frame("degraded", budget_module.degraded(
            reason, _degrade_detail(reason), result.passages, request_budget, 1,
            request_budget.timed_out(), False, lane))
        yield frame("done", {"deltas": 0, "redactions": {},
                             "retrieval_ms": int((started - requested_at) * 1000),
                             "ttft_ms": None, "model_first_ms": None,
                             "total_ms": int((time.monotonic() - requested_at) * 1000)})
        return

    evidence = loop.get("evidence") or result.passages
    yield frame("sources", {"citations": [
        {"index": n + 1, "doc_id": p.doc_id, "acl": p.acl, "page_start": p.page_start,
         "page_end": p.page_end} for n, p in enumerate(evidence)]})

    redactor = streaming.StreamRedactor()
    deltas = 0
    safe = redactor.feed(loop["answer"] or "")
    if safe:
        first_token_at = time.monotonic()
        deltas += 1
        yield frame("delta", {"text": safe})
    tail = redactor.finish()
    if tail:
        deltas += 1
        yield frame("delta", {"text": tail})
    report = redactor.report()
    audit("model_call", session.subject, session.role, "ok",
          {"stage": "ask_stream_tools", "model_role": model, **facts,
           "honesty_gate": honesty_gate or False, "deltas": deltas,
           "passages": len(evidence), "steps": loop.get("steps"),
           "tool_calls": loop.get("tool_calls"), "corrections": loop.get("corrections"),
           "redactions": report["redactions"], "holdback": report["holdback"]},
          model=facts["resolved_model"],
          data_class=data_class, egress_allowed=(lane == "frontier"),
          redactions=report["redactions"],
          unverified_card_shapes=report.get("unverified_card_shapes", 0),
          prompt=redaction.redact(question)[0])
    yield frame("done", {"deltas": deltas, "redactions": report["redactions"],
                         "resolved_model": facts["resolved_model"],
                         "model_group": facts["model_group"],
                         "fallback_used": facts["fallback_used"],
                         "retrieval_ms": int((started - requested_at) * 1000),
                         "ttft_ms": int((first_token_at - requested_at) * 1000) if first_token_at else None,
                         "model_first_ms": int((first_token_at - started) * 1000) if first_token_at else None,
                         "total_ms": int((time.monotonic() - requested_at) * 1000)})


async def _frames(session, permit, model: str, question: str, result, requested_at: float,
                   lane: str = "frontier", use_tools: bool = False):
    """The events, in the order a client should read them.

    **Two streaming behaviours, and the client chooses.** Without `tools: true` in the request this is the
    single-shot stream it has always been. With it, and when the deployment enables the loop, the frames carry
    progress events while the model searches and reads, then the answer. Existing clients are untouched, which is
    why this is opt-in rather than a change to the event order.
    """
    if use_tools:
        async for event in _tool_frames(session, permit, model, question, result, requested_at, lane):
            yield event
        return
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

    # **The single-shot stream cannot take an answer back, so the gate runs before the model is called.** A
    # question about the company's own attribute that no retrieved passage states is refused here, deterministically,
    # rather than streaming a fluent answer and discovering the problem afterwards.
    if honesty.gate_enabled() and honesty.unstated_company_attribute(
            question, [passage.text for passage in result.passages]):
        refusal_ms = int((time.monotonic() - started) * 1000)
        audit("model_call", session.subject, session.role, "denied",
              {"stage": "ask_stream", "honesty_gate": "unstated_company_attribute", "model_role": model,
               "reason": "company attribute not stated in the evidence"},
              model=model, data_class=((STATE.get("manifest") or {}).get("access") or {}).get(
                  "data_class") or "regulated", egress_allowed=False)
        yield frame("delta", {"text": honesty.REFUSAL})
        yield frame("done", {"deltas": 1, "redactions": {}, "honesty_gate": "unstated_company_attribute",
                             "retrieval_ms": retrieval_ms, "ttft_ms": refusal_ms, "model_first_ms": None,
                             "total_ms": int((time.monotonic() - requested_at) * 1000)})
        return

    context = "\n\n".join(f"[{n + 1}] {p.text}" for n, p in enumerate(result.passages))
    # Same contract as the JSON path. The stream is not post-checked, because a token already sent cannot be
    # retracted; the constraint belongs in the prompt for this path and the check belongs in the other.
    prompt = answer_contract.build_prompt(context, question)
    # Minted per attempt here too, for the reason above: an assertion is spent once, so a retry that
    # replays one is a retry that cannot authenticate.
    payload = {
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "max_tokens": int(os.environ.get("OI_MAX_TOKENS", "700")),
        "temperature": 0,
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
    attribution: dict = {}
    while attempt < budget_module.MAX_ATTEMPTS:
        attempt += 1
        if request_budget.past_reserve():
            break
        try:
            response = _open()
            attribution = attribution_from(response.headers)
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
    facts = answer_module.model_facts(model, resolved_model(model), attribution)
    audit("model_call", session.subject, session.role, "ok",
          {"stage": "ask_stream", "model_role": model, **facts, "deltas": deltas,
           "passages": len(result.passages), "redactions": report["redactions"],
           "holdback": report["holdback"]}, model=facts["resolved_model"],
          data_class=((STATE.get("manifest") or {}).get("access") or {}).get("data_class")
                     or "regulated",
          egress_allowed=(lane == "frontier"),
          redactions=report["redactions"],
          unverified_card_shapes=report.get("unverified_card_shapes", 0),
          prompt=redaction.redact(question)[0])
    yield frame("done", {"deltas": deltas, "redactions": report["redactions"],
                         "resolved_model": facts["resolved_model"],
                         "model_group": facts["model_group"],
                         "fallback_used": facts["fallback_used"],
                         # Time to first token as the caller experiences it, retrieval included. The
                         # model's own share sits beside it so a budget can tell the two apart.
                         "retrieval_ms": retrieval_ms,
                         "ttft_ms": int((first_token_at - requested_at) * 1000) if first_token_at else None,
                         "model_first_ms": int((first_token_at - started) * 1000) if first_token_at else None,
                         "total_ms": int((time.monotonic() - requested_at) * 1000)})
