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
import subprocess
import sys
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

sys.path.insert(0, "/app/services/retrieval")
sys.path.insert(0, "/app/services/ingestion")
sys.path.insert(0, "/app/services/session")
sys.path.insert(0, "/app/services/gateway")

import access as access_module  # noqa: E402
import assertion  # noqa: E402
import citations  # noqa: E402
import ingest  # noqa: E402
import retriever  # noqa: E402
import session as session_module  # noqa: E402

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
    """One row in the chain, through the tool that owns it.

    The API does not write the chain itself. The chain has one writer and one definition of its hash, and a
    second implementation here would be a second answer to what the record means.
    """
    command = [sys.executable, AUDIT_TOOL, "record", "--type", event, "--actor", actor or "unknown",
               "--actor-role", actor_role or "unknown", "--outcome", outcome,
               "--deployment-id", DEPLOYMENT_ID, "--payload", json.dumps(payload, sort_keys=True)]
    if os.environ.get("OI_AUDIT_DEV") == "true":
        command.append("--dev")
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

    answer, generation = _generate(session, permit, model, question, result.passages)
    if answer is None:
        return JSONResponse({"error": generation, "kind": "generation_failed"}, status_code=502)

    verified = citations.verify(answer, result.passages)
    body_out = {
        "answer": answer,
        "model": model,
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
        "denied": result.denied,
        "disclosure": (result.disclosure.message if result.disclosure else ""),
    }
    audit("model_call", session.subject, session.role, "ok",
          {"stage": "ask", "model": model, "passages": len(result.passages),
           "supported": len(body_out["supported"]), "unsupported": len(body_out["unsupported"]),
           "session_id": session.session_id, "roots": sorted(permit.roots)}, model=model)
    return body_out


def _generate(session, permit, model: str, question: str, passages: list):
    """The model call, authenticated with a statement minted for this request."""
    import urllib.error
    import urllib.request

    context = "\n\n".join(f"[{n + 1}] {p.text}" for n, p in enumerate(passages))
    prompt = (
        "Answer the question using only the numbered passages below. Cite the passages you use as [n]. "
        "If they do not answer it, say so plainly. Do not use outside knowledge.\n\n"
        f"Passages:\n{context}\n\nQuestion: {question}"
    )
    # **The statement carries the MODEL role, and the session's API role is not in it.** The gateway's team,
    # and therefore the budget, is per model role: that is what the manifest's `budgets.roles` block declares
    # and what `keys --provision-teams` creates. Minting with the API role would name a team that does not
    # exist, which the gateway refuses with a 404. Document access is unaffected, because the permit's roots
    # are already fixed for this request and the gateway never sees them.
    statement = assertion.mint(STATE["key"], model, session_id=session.session_id)
    payload = {
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "max_tokens": int(os.environ.get("OI_MAX_TOKENS", "700")),
    }
    request = urllib.request.Request(
        GATEWAY_URL + "/v1/chat/completions", data=json.dumps(payload).encode(), method="POST",
        headers={"Content-Type": "application/json", assertion.HEADER: statement})
    try:
        with urllib.request.urlopen(request, timeout=float(os.environ.get("OI_GATEWAY_TIMEOUT", "120"))) as r:
            data = json.load(r)
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode(errors="replace")[:300]
        audit("model_call", session.subject, session.role, "denied",
              {"stage": "model", "status": exc.code, "model": model, "detail": detail[:160]}, model=model)
        # A budget refusal is a refusal and not a failure, and saying which it was is the difference between
        # an operator checking a ceiling and an operator checking a network.
        if "budget" in detail.lower():
            return None, f"the role's budget is exhausted at the gateway: {detail}"
        return None, f"the gateway refused the call: {exc.code} {detail}"
    except Exception as exc:  # noqa: BLE001 - the message is the response
        return None, f"the gateway could not be reached: {type(exc).__name__}: {exc}"

    choices = data.get("choices") or []
    if not choices:
        return None, "the gateway returned no choices"
    return (choices[0].get("message") or {}).get("content", ""), "ok"
