"""Answering a question: one implementation, called by every path that answers one.

**Why this module exists at all.** `POST /ask`, `POST /ask/stream` and a job all answer a question, and
they have to do it the same way. C3 proved what happens otherwise: a redaction that existed on the
streaming path and not the JSON one, and a data boundary that existed on the JSON path and not the
streaming one. **Both passed every probe, because each probe tested the endpoint its author had in
mind.** The shape of that defect is two implementations of one thing, and the fix is one implementation.

So the order of operations lives here exactly once:

1. the session resolves, or there is no answer
2. a request that supplies its own authority is refused, and recorded, before the error returns
3. retrieval runs with the permission filter inside the query
4. the data boundary decides which lane may answer, and a lane of `none` refuses
5. the model is called with its own budget, and a call that fails degrades rather than erroring
6. the answer is redacted before anybody sees it
7. the citations are checked against the redacted text that is actually sent
8. the row is written, with the data class and the egress verdict

**What a caller gets back is a decision, not a response.** `Answer` carries a status code, the payload,
and the facts the caller needs to record or to stream. A caller that wants a `JSONResponse` builds one; a
caller that wants to write a job step reads the fields.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Callable, Optional

import access as access_module
import budget as budget_module
import citations as citations_module
import redaction
import retriever
import routing
import session as session_module


@dataclass
class Answer:
    """What one question came to. `status` is the HTTP status the caller should use."""

    status: int
    payload: dict
    # The facts a caller records or renders. Present whether the answer succeeded or not, because a
    # refusal is a thing that has to be reported just as precisely as an answer.
    lane: str = "none"
    model: str = ""
    data_class: str = "regulated"
    egress: bool = False
    kind: str = ""          # "answer", "degraded", "no_source", or the refusal kind
    session: object = None
    notice: str = ""

    @property
    def ok(self) -> bool:
        return self.status == 200


@dataclass
class Context:
    """Everything a question needs, gathered once so every path gathers it the same way."""

    services: object
    manifest: dict = field(default_factory=dict)
    passages: int = 6


class Refused(Exception):
    """A refusal with a status, so a caller cannot accidentally turn one into an answer."""

    def __init__(self, status: int, payload: dict, kind: str = ""):
        self.status, self.payload, self.kind = status, payload, kind
        super().__init__(payload.get("error", kind or "refused"))


# The fields a request may not supply. A role comes from a session, and the list is here rather than
# inline in a handler so every path refuses the same set.
AUTHORITY_FIELDS = ("role", "roles", "actor_role", "groups", "roots", "acl")


def data_class_for(body: dict, manifest: dict) -> str:
    """The class a request carries.

    **The request may state it, and that is not the caller choosing its own permission.** The class a piece
    of content IS belongs to the client's declaration rather than to the caller, so it is stored on the job
    when the work is created and read back here. What the caller cannot do is name a class the deployment
    has not declared, because the gate compares whatever comes out of this against the manifest's own
    allowlist: stating a class only ever moves a request toward being refused.

    **Why the job carries its class rather than reading today's manifest.** A job outlives the request that
    started it, and the manifest can change underneath it. Re-reading the manifest at execution time meant a
    job created while a class was permitted would run under a manifest that forbids it — which is the defect
    the boundary probe found on the job path, in the exact shape C3 shipped twice. The class is fixed when
    the work is created and the gate is consulted against it every time the work runs.
    """
    stated = (body or {}).get("data_class")
    if isinstance(stated, str) and stated in ENGINE_CLASSES:
        return stated
    return ((manifest or {}).get("access") or {}).get("data_class") or "regulated"


# The classes the deployment's own vocabulary uses. A stated class outside this set is ignored rather than
# passed through, so a typo cannot produce a class the gate has never been told about.
ENGINE_CLASSES = frozenset({"public", "internal", "confidential", "regulated"})


def _data_class(manifest: dict) -> str:
    """The class a request carries when nothing states one. Kept for callers that have only a manifest."""
    return data_class_for({}, manifest)


def check_boundary(*, manifest: dict, data_class: str, task: str, model_name: str, default_lane: str,
                   frontier_model: str, session, audit, endpoint: str, request_id: str = ""):
    """Step 4 on its own: which lane may answer, and a refusal when none may.

    **A separate function because a job has to be refused before it is created.** The job path ran this
    inside its first attempt and stored a job whose single step failed, answering HTTP 200 — so the request
    the gate exists to refuse produced a job row and a cheerful status code, with the refusal buried in the
    result. `/ask` refused the same request with a 403.

    That is the C3 defect shape a third time: a control present on the path somebody was looking at. The fix
    is not to make the job's status code depend on its outcome, which would conflate a refusal with a
    failure; it is to consult the boundary **before the work exists**, so a job is never created for
    something no lane is permitted to do.

    **`none` refuses rather than falling through.** The first version of this guard covered only the `local`
    case, so a decision of `none` skipped it and the ordinary remote model answered anyway while the notice
    claimed no answer could be produced. A refusal that is not one, with a message saying it was.
    """
    decision = routing.route(
        manifest or {}, data_class=data_class, task=task, user_wants_stronger=False,
        model_name=model_name, default_lane=default_lane, frontier_model=frontier_model)

    if decision.lane == "none" or (decision.lane == "local" and not local_lane_available()):
        audit("access", session.subject, session.role, "denied",
              {"stage": "routing", "reason": decision.reason, "lane": decision.lane,
               "data_class": data_class, "escalation_requested": decision.escalation_requested,
               "endpoint": endpoint},
              data_class=data_class, egress_allowed=False, request_id=request_id)
        raise Refused(403, {
            "error": "Permission denied on this resource (or it might not exist). This request needs a "
                     "lane this deployment does not permit and has no local model to answer with.",
            "kind": "no_permitted_lane", "reason": decision.reason, "notice": decision.notice,
            "request_id": request_id},
            "no_permitted_lane")
    return decision


def prepare(*, token: str, question: str, body: dict, ctx: Context, resolve, model_for, role_lane,
            frontier_model_for, local_lane_available, audit, endpoint: str, request_id: str = "") -> tuple:
    """Steps 1 to 4: resolve the session, refuse a supplied authority, retrieve, and route.

    Returns `(session, permit, model, result, decision, data_class)`. Raises `Refused` with the status and
    the payload already built, so a caller cannot forget a guard by handling a falsy return instead.

    **The helpers are passed in rather than imported**, because they read the app's loaded state and its
    environment. That keeps this module free of a module-level dependency on a running service, which is
    what lets it be tested without one.
    """
    if not question:
        raise Refused(400, {"error": "no question was asked"}, "bad_request")

    try:
        session = resolve(token)
    except session_module.SessionError as exc:
        raise Refused(401, {"error": str(exc), "kind": "no_session"}, "no_session") from exc

    supplied = next((f for f in AUTHORITY_FIELDS if body.get(f) is not None), None)
    if supplied:
        # **Recorded before the error returns.** A refusal that is not in the chain is a control nobody
        # can audit, and the row has to exist whether or not the caller reads the response.
        try:
            session_module.check_role_claim(session, supplied, body.get(supplied), dev=True)
        except session_module.SessionError:
            audit("access", session.subject, str(body.get(supplied)), "denied",
                  {"supplied_field": supplied, "session_role": session.role,
                                      "refusal": "a request may not supply its own authority", "endpoint": endpoint},
                  request_id=request_id)
            # AIP-211: this says the permission was denied OR the resource might not exist. A 404 instead
            # of a 403 is worse for the operator and no better for secrecy.
            raise Refused(403, {
                "error": "Permission denied on this resource (or it might not exist). A role comes from "
                         "an authenticated session and never from a request field.",
                # **The same id the response carries, so the refusal can be found afterwards.** The handler
                # appends a request id to what it returns; without it here the row and the response were two
                # statements nothing could compare, which is what the C3 FORGED check measures.
                "request_id": request_id,
                "kind": "refused_authority_claim"}, "refused_authority_claim")

    permit = session.permit()
    role = body.get("model_role") or os.environ.get("OI_DEFAULT_API_ROLE", "viewer")
    model = model_for(role)

    try:
        result = retriever.search(ctx.services, question, permit.role, limit=ctx.passages, permit=permit)
    except Exception as exc:  # noqa: BLE001 - the message is the response
        audit("access", session.subject, session.role, "error",
              {"stage": "retrieval", "error": type(exc).__name__}, request_id=request_id)
        raise Refused(502, {"error": f"retrieval failed: {exc}", "kind": "retrieval_failed"},
                      "retrieval_failed") from exc

    if not result.passages:
        # A 200 with an honest sentence rather than an error: the request succeeded and the corpus does
        # not contain the thing, which is a different fact from the service being broken.
        # **The request id is what makes the answer findable in the record.** Every other audited path on
        # this route passes one, and this branch did not: a `no_source` answer carried an id in its body and
        # left a row that no lookup could reach, which is what `activity_probe.py`'s BYREQUEST right caught on
        # 2026-10-16. The reader-facing consequence is worse than the probe's, because the answer is the one a
        # person is most likely to ask about: "the system said it had nothing" is a claim to check.
        audit("access", session.subject, session.role, "denied",
              {"stage": "retrieval", "reason": "no passage the session may read matched",
               "roots": sorted(permit.roots)}, request_id=request_id)
        raise Refused(200, {"answer": "No document you may read answers this question.", "citations": [],
                            "kind": "no_source", "role": session.role,
                            "roots": sorted(permit.roots)}, "no_source")

    data_class = data_class_for(body, ctx.manifest)
    # **The boundary, in one place, so a job can consult it before the work exists.** `user_wants_stronger`
    # is a property of the request rather than of the boundary check, so it is applied here where the whole
    # request is known; the job path asks the same question with the task alone.
    decision = routing.route(
        ctx.manifest or {}, data_class=data_class, task=body.get("task") or "",
        user_wants_stronger=bool(body.get("stronger")), model_name=model,
        default_lane=role_lane(role), frontier_model=frontier_model_for(role))
    if decision.lane == "none" or (decision.lane == "local" and not local_lane_available()):
        # Through the same refusal path a job creation takes, so the two cannot say different things.
        check_boundary(manifest=ctx.manifest, data_class=data_class, task=body.get("task") or "",
                       model_name=model, default_lane=role_lane(role),
                       frontier_model=frontier_model_for(role), session=session, audit=audit,
                       endpoint=endpoint, request_id=request_id)

    return session, permit, model, result, decision, data_class


def finish(*, question: str, answer: str, session, permit, model, result, decision, data_class,
           request_budget, redaction_report, audit, resolved_model, request_id: str = "") -> Answer:
    """Steps 6 to 8: redact, check the citations against what is sent, and record it.

    **The redaction happens before the checks, and the order was a defect.** A citation verified in text
    the client never receives is verified against the wrong thing. This is the sequence the JSON path
    ended up with after the C3 panel found the canary coming back in clear.

    **The question is redacted as well as the answer**, and the record hashes the redacted form. The audit
    tool hashes the text it is handed, and handing it the raw question made `prompt_sha256` a hash of the
    original: a comparable value, which is exactly what the manual says it must not be. A hash cannot be
    read back, but it can be compared against a guess. Found by the C3 panel, who hashed both forms.
    """
    answer, report = redaction.redact(answer)
    safe_question, question_report = redaction.redact(question)
    report = redaction.Redaction(
        counts={**report.counts,
                **{k: report.counts.get(k, 0) + v for k, v in question_report.counts.items()}},
        unverified_cards=report.unverified_cards + question_report.unverified_cards)
    if redaction_report is not None:
        report = redaction.Redaction(
            counts={**report.counts,
                    **{k: report.counts.get(k, 0) + v for k, v in redaction_report.counts.items()}},
            unverified_cards=report.unverified_cards + redaction_report.unverified_cards)

    verified = citations_module.verify(answer, result.passages)
    payload = {
        "answer": answer,
        # **The role and the model are different facts.** `model` is what the manifest names;
        # `resolved_model` is what actually ran. An answer carrying only the role made a manifest swap
        # invisible, which is the seam INF-ROLE-001 is proved through.
        "model": model,
        "resolved_model": resolved_model(model),
        "role": session.role,
        "roots": sorted(permit.roots),
        "citations": [{"index": n + 1, "doc_id": p.doc_id, "acl": p.acl,
                       "page_start": p.page_start, "page_end": p.page_end}
                      for n, p in enumerate(result.passages)],
        "supported": [c.text for c in verified.claims if c.ok],
        "unsupported": [{"text": c.text, "reason": c.reason, "detail": c.detail}
                        for c in verified.refused],
        "redactions": report.counts,
        "notice": decision.notice,
        "lane": decision.lane,
        "escalated": decision.escalated,
        "trigger": decision.trigger,
        "reason": decision.reason,
        "denied": result.denied,
        "disclosure": (result.disclosure.message if result.disclosure else ""),
    }
    if request_id:
        payload["request_id"] = request_id

    audit("model_call", session.subject, session.role, "ok",
          {"stage": "ask", "model": model, "passages": len(result.passages),
           "supported": len(payload["supported"]), "unsupported": len(payload["unsupported"]),
           "roots": sorted(permit.roots),
           **routing.audit_facts(decision, data_class)},
          model=model, data_class=data_class, egress_allowed=(decision.lane == "frontier"),
          redactions=report.counts, unverified_card_shapes=report.unverified_cards,
          request_id=request_id, prompt=safe_question)

    return Answer(status=200, payload=payload, lane=decision.lane, model=model, data_class=data_class,
                  egress=(decision.lane == "frontier"), kind="answer", session=session,
                  notice=decision.notice)


def degraded(*, reason, detail, passages, request_budget, attempts, threshold, partial_output, lane,
             session, model, data_class, audit, audit_facts) -> Answer:
    """The answer when the model will not answer, at HTTP 200.

    **`kind: degraded` is the field a client must branch on.** An answer and a degradation look alike
    otherwise, and a client that renders one as the other shows retrieved passages as though a model had
    written them.
    """
    audit("model_call", session.subject, session.role, "denied",
          audit_facts, model=model)
    return Answer(status=200,
                  payload=budget_module.degraded(reason, detail, passages, request_budget, attempts,
                                                 threshold, partial_output, lane),
                  lane=lane, model=model, data_class=data_class, egress=(lane == "frontier"),
                  kind="degraded", session=session)
