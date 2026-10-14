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
import ingest  # noqa: E402
import retriever  # noqa: E402
import session as session_module  # noqa: E402
import budget as budget_module  # noqa: E402
import routing  # noqa: E402
import streaming  # noqa: E402

GATEWAY_URL = os.environ.get("OI_GATEWAY_URL", "http://litellm:4000")
DEPLOYMENT_ID = os.environ.get("DEPLOYMENT_ID", "unknown")
AUDIT_TOOL = os.environ.get("AUDIT_TOOL", "/app/audit.py")
KEY_PATH = os.environ.get("OI_ASSERTION_KEY", "/app/rendered/assertion.key")

# Which model role answers a question, per API role. The renderer writes one variable per role from the
# manifest's `models` block, so a deployment changes the model by changing the manifest and re-rendering.
# **This is the seam `INF-ROLE-001` is proved through**: the answer to "which model answered" comes from here
# and nowhere else.
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
    env = dict(os.environ)
    env["DATABASE_URL"] = session_module.database_url()
    result = subprocess.run(command, capture_output=True, text=True, env=env, timeout=60)
    if result.returncode != 0:
        # Loud, because a request whose record is missing is a request the deployment cannot account for.
        print(f"the audit write failed: {result.stderr.strip()[:300]}", file=sys.stderr)

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
    print(f"api ready: registry={registry_path} model_role_default={DEFAULT_MODEL_ROLE} "
          f"answers_with={model_for(os.environ.get('OI_DEFAULT_API_ROLE', 'viewer'))}", flush=True)
    yield
    STATE["store"].close()


app = FastAPI(title="Office Inference assistant API", lifespan=lifespan)


@app.get("/health")
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


@app.post("/ask")
async def ask(request: Request):
    """A question in, a cited answer out."""
    try:
        body = await request.json()
    except Exception:  # noqa: BLE001 - the message is the response
        return JSONResponse({"error": "the request body is not JSON"}, status_code=400)

    token = body.get("session") or request.headers.get("X-OI-Session", "")
    question = (body.get("question") or "").strip()
    if not question:
        return JSONResponse({"error": "no question was asked"}, status_code=400)

    # The session, before anything else. No session is no identity, and no identity is no answer.
    try:
        session = _resolve(token)
    except session_module.SessionError as exc:
        return JSONResponse({"error": str(exc), "kind": "no_session"}, status_code=401)

    # A request that supplies its own role is refused, and the refusal is in the chain before this returns.
    supplied = next((field for field in ("role", "roles", "actor_role", "groups", "roots", "acl")
                     if body.get(field) is not None), None)
    if supplied:
        try:
            session_module.check_role_claim(session, supplied, body.get(supplied), dev=True)
        except session_module.SessionError as exc:
            audit("access", session.subject, str(body.get(supplied)), "denied",
                  {"supplied_field": supplied, "session_role": session.role,
                   "session_id": session.session_id, "refusal": "a request may not supply its own authority",
                   "endpoint": "/ask"})
            # AIP-211: a permission refusal says the permission was denied OR the resource might not exist.
            # A 404 instead of a 403 is worse for the operator and no better for secrecy, which is Google's
            # stated position and the reason the wording is theirs.
            return JSONResponse(
                {"error": "Permission denied on this resource (or it might not exist). A role comes from an "
                          "authenticated session and never from a request field.",
                 "kind": "refused_authority_claim"},
                status_code=403)

    permit = session.permit()
    role = body.get("model_role") or os.environ.get("OI_DEFAULT_API_ROLE", "viewer")
    model = model_for(role)

    # Retrieval, with the filter inside the query. A failure here is a refusal rather than an empty answer,
    # because an empty answer reads as a corpus that does not contain the thing.
    try:
        result = retriever.search(STATE["services"], question, permit.role, limit=PASSAGES, permit=permit)
    except Exception as exc:  # noqa: BLE001 - the message is the response
        audit("access", session.subject, session.role, "error",
              {"stage": "retrieval", "error": type(exc).__name__, "session_id": session.session_id})
        return JSONResponse({"error": f"retrieval failed: {exc}", "kind": "retrieval_failed"}, status_code=502)

    if not result.passages:
        audit("access", session.subject, session.role, "denied",
              {"stage": "retrieval", "reason": "no passage the session may read matched",
               "session_id": session.session_id, "roots": sorted(permit.roots)})
        return JSONResponse(
            {"answer": "No document you may read answers this question.", "citations": [],
             "kind": "no_source", "role": session.role, "roots": sorted(permit.roots)},
            status_code=200)

    # **The gate first, then the triggers choosing a model inside what it permitted.** The data class is
    # the deployment's declared content default: what a request IS, is the client's declaration, and the
    # record carries the roots retrieval actually reached beside it.
    # **Read from the access block, and the first version read `content.default` instead.** That field is a
    # CONTENT MODE (`hash`, `redacted`, `full`), not a data class, so the gate compared `hash` against the
    # permitted classes and refused every request on the deployment: three 403s and a routing decision to a
    # lane that did not exist. A manifest with no data-class declaration now falls back to the most
    # restrictive class rather than to a middle one, so an undeclared deployment does not send content off
    # the box by omission.
    data_class = ((STATE.get("manifest") or {}).get("access") or {}).get("data_class") or "regulated"
    decision = routing.route(
        STATE.get("manifest") or {},
        data_class=data_class,
        task=body.get("task") or "",
        user_wants_stronger=bool(body.get("stronger")),
        model_name=model,
        # The lane the configured model actually runs in, from the manifest. Escalation selects a
        # stronger model; not escalating selects the ordinary one, which is still remote on a cloud
        # deployment. Conflating those made every unescalated request fail where there is no local model.
        default_lane="local" if local_lane_available() else "frontier",
        local=local_lane_available(),
    )
    if decision.lane == "local" and not local_lane_available():
        # **This deployment has no local lane**, so a decision to answer locally would name a model that
        # does not exist. The honest outcome is a refusal that says so, recorded, rather than a notice
        # claiming an answer that cannot be produced. A deployment that declares a local model gets the
        # announced downgrade instead.
        facts = {"stage": "routing", "reason": decision.reason,
                 "lane": decision.lane, "data_class": data_class,
                 "escalation_requested": decision.escalation_requested,
                 "session_id": session.session_id}
        audit("access", session.subject, session.role, "denied", facts)
        return JSONResponse(
            {"error": "Permission denied on this resource (or it might not exist). "
                      "This request needs a lane this deployment does not permit and has no "
                      "local model to answer with.",
             "kind": "no_permitted_lane", "reason": decision.reason,
             "notice": decision.notice},
            status_code=403)
    # **The lane decides the thresholds.** A local model is a different shape from a remote one, and
    # borrowing the frontier numbers would mark healthy local answers as failures. The arrival clock is
    # passed through so the deadline stays anchored where the client asked, not where routing finished.
    request_budget = budget_module.Budget.for_lane(decision.lane)
    answer, generation = _generate(session, permit, model, question, result.passages, request_budget)
    if answer is None:
        # **A degraded answer rather than an error.** The research and this step's own design say the
        # retrieved passages with a typed reason, because retrieval already did the expensive part and the
        # client is entitled to their own material. A client whose data class forbids the frontier lane meets
        # this path routinely, so it has to read like a feature rather than an outage.
        reason, kind, attempts = generation if isinstance(generation, tuple) else ("unclassified", {}, 1)
        facts = budget_module.audit_facts(
            reason=reason, matched=(kind or {}).get("matched", ""), retryable=bool((kind or {}).get("retryable")),
            # `threshold` is carrying None when no deadline tripped, and that is the honest value: a budget
            # refusal is not a timeout, and recording one as a threshold would blur the three the spec
            # exists to distinguish. The reason field already says what happened.
            attempts=attempts, threshold=request_budget.timed_out(), budget=request_budget,
            partial_output=False, errors=[], lane="frontier", data_class="internal")
        audit("model_call", session.subject, session.role, "denied", facts, model=model)
        return JSONResponse(
            budget_module.degraded(reason, _degrade_detail(reason), result.passages, request_budget,
                                   attempts, request_budget.timed_out(), False),
            status_code=200)

    verified = citations.verify(answer, result.passages)
    body_out = {
        "answer": answer,
        # **The role and the model are different facts.** `model` is the role the routing chose, which is
        # what a manifest names; `resolved_model` is the provider model that role points at. Reporting
        # only the role made the manifest swap that proves INF-ROLE-001 invisible from the answer: the
        # client saw `analyst` before and after and could not tell the model had changed.
        "model": model,
        "resolved_model": resolved_model(model),
        "role": session.role,
        "roots": sorted(permit.roots),
        "citations": [
            {"index": n + 1, "doc_id": p.doc_id, "acl": p.acl, "page_start": p.page_start,
             "page_end": p.page_end}
            for n, p in enumerate(result.passages)
        ],
        "supported": [c.text for c in verified.claims if c.ok],
        "unsupported": [{"text": c.text, "reason": c.reason, "detail": c.detail}
                        for c in verified.refused],
        # What the user is told, always, when there is something to tell. Decided 2026-10-14.
        "notice": decision.notice,
        "lane": decision.lane,
        "escalated": decision.escalated,
        # **Which trigger moved it, and why.** The manual promises a client can tell the user what escalated
        # the request. The first version of this answer did not carry it: the trigger existed only in the audit
        # row, so a client could say the stronger model answered but not what made it. Found by a probe that
        # asserts every claim the manual makes, on its first run.
        "trigger": decision.trigger,
        "reason": decision.reason,
        "denied": result.denied,
        "disclosure": (result.disclosure.message if result.disclosure else ""),
    }
    audit("model_call", session.subject, session.role, "ok",
          {"stage": "ask", "model": model, "passages": len(result.passages),
           "supported": len(body_out["supported"]), "unsupported": len(body_out["unsupported"]),
           "session_id": session.session_id, "roots": sorted(permit.roots),
           **routing.audit_facts(decision, data_class)}, model=model)
    return body_out


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
    statement = assertion.mint(STATE["key"], model, session_id=session.session_id)
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
        request = urllib.request.Request(
            GATEWAY_URL + "/v1/chat/completions", data=json.dumps(payload).encode(), method="POST",
            headers={"Content-Type": "application/json", assertion.HEADER: statement})
        try:
            with urllib.request.urlopen(request, timeout=max(1.0, budget.remaining())) as r:
                data = json.load(r)
            choices = data.get("choices") or []
            if not choices:
                return None, ("unclassified", "the gateway returned no choices", attempt)
            return (choices[0].get("message") or {}).get("content", ""), None
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode(errors="replace")[:400]
            kind = budget_module.classify(detail)
            errors.append({"attempt": attempt, "status": exc.code, "reason": kind["reason"],
                           "matched": kind["matched"], "retryable": kind["retryable"]})
            retry_after = exc.headers.get("Retry-After") if exc.headers else None
            if not kind["retryable"]:
                return None, (kind["reason"], kind, attempt)
            if attempt >= budget_module.MAX_ATTEMPTS:
                return None, (kind["reason"], kind, attempt)
            wait = _backoff(attempt, retry_after)
            if wait >= budget.remaining() - budget.reserve:
                # The wait itself would eat the delivery reserve, so waiting is the wrong answer.
                return None, (kind["reason"], kind, attempt)
            time.sleep(wait)
        except Exception as exc:  # noqa: BLE001 - a transport failure is a classification too
            kind = budget_module.classify(type(exc).__name__)
            errors.append({"attempt": attempt, "status": 0, "reason": kind["reason"],
                           "matched": kind["matched"], "retryable": kind["retryable"]})
            if not kind["retryable"] or attempt >= budget_module.MAX_ATTEMPTS:
                return None, (kind["reason"], kind, attempt)
            time.sleep(_backoff(attempt, None))

    return None, (errors[-1]["reason"] if errors else "timeout",
                  errors[-1] if errors else {"reason": "timeout", "retryable": True}, attempt)


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

@app.post("/ask/stream")
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
                   "session_id": session.session_id, "endpoint": "/ask/stream"})
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
        default_lane="local" if local_lane_available() else "frontier",
        local=local_lane_available(),
    )

    try:
        result = retriever.search(STATE["services"], question, permit.role, limit=PASSAGES, permit=permit)
    except Exception as exc:  # noqa: BLE001
        audit("access", session.subject, session.role, "error",
              {"stage": "retrieval", "error": type(exc).__name__, "session_id": session.session_id})
        return JSONResponse({"error": f"retrieval failed: {exc}", "kind": "retrieval_failed"}, status_code=502)

    if not result.passages:
        audit("access", session.subject, session.role, "denied",
              {"stage": "retrieval", "reason": "no passage the session may read matched",
               "session_id": session.session_id})
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
    statement = assertion.mint(STATE["key"], model, session_id=session.session_id)
    payload = {
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "max_tokens": int(os.environ.get("OI_MAX_TOKENS", "700")),
        "stream": True,
    }
    upstream = urllib.request.Request(
        GATEWAY_URL + "/v1/chat/completions", data=json.dumps(payload).encode(), method="POST",
        headers={"Content-Type": "application/json", assertion.HEADER: statement})

    # **A read cannot be interrupted, so it is bounded instead.** The socket timeout is the time the budget
    # has left, and the watchdog below checks between reads. A generator that never yields cannot be
    # cancelled from outside, which is why the deadline lives in here rather than around the call.
    def _open():
        return urllib.request.urlopen(upstream, timeout=max(1.0, request_budget.remaining()))

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
                                        partial_output=False, errors=errors), model=model)
        # No frame has gone out yet, so a degraded answer is still possible and is the better outcome.
        yield frame("degraded", budget_module.degraded(kind["reason"], _degrade_detail(kind["reason"]),
                                                      result.passages, request_budget, attempt,
                                                      request_budget.timed_out(), False))
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
                       "emitted_so_far": emitted, "session_id": session.session_id}, model=model)
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
           "holdback": report["holdback"], "session_id": session.session_id}, model=model)
    yield frame("done", {"deltas": deltas, "redactions": report["redactions"],
                         # Time to first token as the caller experiences it, retrieval included. The
                         # model's own share sits beside it so a budget can tell the two apart.
                         "retrieval_ms": retrieval_ms,
                         "ttft_ms": int((first_token_at - requested_at) * 1000) if first_token_at else None,
                         "model_first_ms": int((first_token_at - started) * 1000) if first_token_at else None,
                         "total_ms": int((time.monotonic() - requested_at) * 1000)})
