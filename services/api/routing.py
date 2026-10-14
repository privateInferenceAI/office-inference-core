"""Where a request goes, decided by a pure function over facts the caller already holds.

**The gate comes first and the triggers choose only inside it.** A trigger is a cost decision; the gate is a
data-boundary decision. Settling the boundary before asking how hard a question is, is what makes the answer to
"why did this content reach an external model" a sentence about a declared permission rather than a story about
a heuristic firing.

**The boundary is one-way.** No trigger may move content toward a more exposed lane. A user asking for the
stronger model cannot reach the frontier lane from a deployment whose data class forbids it. Escalation only
ever happens within what the gate allowed.

**Every predicate is over something already known.** The task list is a lookup, synthesis is an integer count,
and the request for the stronger model is a field. Nothing here asks a model how hard a question is, which is
the property the requirement is aimed at.

**Pure on purpose.** No database handle and no HTTP client, because the table of request-to-lane cases is the
compliance artifact and it has to be testable without a deployment.
"""
from __future__ import annotations

from dataclasses import dataclass, field

TRIGGER_AGENTIC = 'agentic_task'
TRIGGER_SYNTHESIS = 'multi_document_synthesis'
TRIGGER_USER = 'user_requested_stronger'
TRIGGERS = (TRIGGER_AGENTIC, TRIGGER_SYNTHESIS, TRIGGER_USER)

# How many distinct documents retrieval must return before the synthesis trigger fires. **A proxy, and the
# record says so.** It stays because a deterministic predicate is worth more here than an accurate one.
SYNTHESIS_MIN_DOCUMENTS = 2
SYNTHESIS_MIN_SCORE = 0.2

AGENTIC_TASKS = frozenset({
    'summarise_across_documents',
    'reconcile',
    'compare',
    'draft',
    'extract_table',
    'multi_step',
})


@dataclass(frozen=True)
class Decision:
    """Where one request goes, why, and what the caller should tell the user."""

    lane: str
    model: str
    escalated: bool
    trigger: str = ''
    reason: str = ''
    notice: str = ''
    escalation_requested: bool = False
    synthesis_is_proxy: bool = False
    steps: tuple = field(default_factory=tuple)


def gate(model: str, data_class: str, frontier_classes, *, remote: bool = True) -> tuple:
    """Whether this content may reach this model, and why not when it may not.

    **Refuses rather than defaulting.** An empty egress declaration means nothing leaves, which is the office
    shape and the safe direction for a cloud deployment to fail in. A model served locally is not an egress
    question at all.
    """
    if not remote:
        return True, 'the model is served on this deployment, so no content leaves'
    allowed = tuple(frontier_classes or ())
    if data_class in allowed:
        return True, 'the deployment permits ' + data_class + ' on a remote lane'
    listed = list(allowed) or 'nothing'
    return False, ('the deployment permits ' + str(listed) + ' on a remote lane, and this request is '
                   + data_class)


def synthesis_documents(passages, min_score: float = SYNTHESIS_MIN_SCORE) -> int:
    """How many distinct documents cleared the floor. The integer the second trigger is decided on."""
    seen = set()
    for passage in passages or ():
        score = getattr(passage, 'score', None)
        if score is not None and score < min_score:
            continue
        doc = getattr(passage, 'doc_id', '')
        if doc:
            seen.add(doc)
    return len(seen)


def route(manifest: dict, *, data_class: str, task: str = '', user_wants_stronger: bool = False,
          passages=None, model_name: str = '', local: bool = False) -> Decision:
    """The whole decision, in one pure function.

    Order matters and is the point: the gate is consulted first, so no trigger can move content to a lane the
    deployment has not permitted.
    """
    egress = ((manifest.get('access') or {}).get('egress') or {})
    permitted, why = gate(model_name, data_class, egress.get('frontier_classes'), remote=not local)
    steps = ['gate: ' + why]

    if not permitted:
        # A refused escalation goes to the local lane rather than nowhere: the content is already on this box,
        # and the user is told rather than left guessing. Decided 2026-10-14; design note section 7h.
        return Decision(
            lane='local', model=model_name, escalated=False, reason=why,
            notice=('You asked for the stronger model, and this content may not be sent to it. The answer '
                    'below comes from the model this deployment serves locally.'),
            escalation_requested=user_wants_stronger, steps=tuple(steps))

    if task and task in AGENTIC_TASKS:
        steps.append('trigger: ' + task + ' is on the agentic list')
        return Decision(lane='frontier', model=model_name, escalated=True, trigger=TRIGGER_AGENTIC,
                        reason='the task ' + task + ' is on the agentic pack list',
                        notice='This is an agentic task, so the stronger model answered.',
                        escalation_requested=user_wants_stronger, steps=tuple(steps))

    documents = synthesis_documents(passages)
    if documents >= SYNTHESIS_MIN_DOCUMENTS:
        steps.append('trigger: ' + str(documents) + ' distinct documents cleared the floor')
        return Decision(lane='frontier', model=model_name, escalated=True, trigger=TRIGGER_SYNTHESIS,
                        reason=str(documents) + ' distinct documents need reconciling',
                        notice='This answer draws on ' + str(documents) + ' documents, so the stronger model '
                               'was used to reconcile them.',
                        escalation_requested=user_wants_stronger, synthesis_is_proxy=True,
                        steps=tuple(steps))

    if user_wants_stronger:
        steps.append('trigger: the user asked for the stronger model')
        return Decision(lane='frontier', model=model_name, escalated=True, trigger=TRIGGER_USER,
                        reason='the user asked for the stronger model',
                        notice='You asked for the stronger model, so it answered.',
                        escalation_requested=True, steps=tuple(steps))

    steps.append('no trigger fired')
    return Decision(lane='local', model=model_name, escalated=False, reason='no trigger fired',
                    escalation_requested=user_wants_stronger, steps=tuple(steps))


def audit_facts(decision: Decision, data_class: str) -> dict:
    """The routing half of the record. Two facts, never one."""
    return {
        'lane': decision.lane,
        'data_class': data_class,
        'escalated': decision.escalated,
        'trigger': decision.trigger,
        'reason': decision.reason,
        'escalation_requested': decision.escalation_requested,
        'synthesis_is_proxy': decision.synthesis_is_proxy,
        'routing_version': 1,
    }
