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
TRIGGER_USER = 'user_requested_stronger'
TRIGGERS = (TRIGGER_AGENTIC, TRIGGER_USER)

# **There was a third trigger here and it was removed, not tuned.** It escalated when retrieval returned more
# than one distinct document, on the theory that several documents means the answer needs reconciling. Two
# measurements killed it. The first: a plain single-fact question returned SIX distinct documents while the
# question that genuinely needed reconciling returned FOUR, because this corpus is small and one fact is
# legitimately spread across several files. The second: every combination of a document count from 1 to 6 and a
# score floor from 0.00 to 0.60 either fired on a single-fact question or missed a synthesis one, with no
# separating rule in the whole table.
#
# So the proxy was not badly calibrated, it was unusable, and an unusable predicate that fires on everything is
# worse than no trigger: it escalates cost silently and makes the audit row meaningless. The evidence is in
# `tests/fixtures/trigger_rules.py`, which prints the whole table.

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


def route(manifest: dict, *, data_class: str, task: str = '', user_wants_stronger: bool = False,
          model_name: str = '', default_lane: str = 'frontier', local: bool = False) -> Decision:
    """The whole decision, in one pure function.

    Order matters and is the point: the gate is consulted first, so no trigger can move content to a lane the
    deployment has not permitted.

    **`default_lane` is where the deployment's configured model runs**, taken from the manifest rather than
    assumed. Not escalating does NOT mean answering locally: escalation selects a stronger model, and no
    escalation selects the ordinary one, which on a cloud deployment is still remote. Conflating the two was a
    defect that made every unescalated request fail on a deployment with no local model.
    """
    egress = ((manifest.get('access') or {}).get('egress') or {})
    permitted, why = gate(model_name, data_class, egress.get('frontier_classes'), remote=not local)
    steps = ['gate: ' + why]

    if not permitted:
        # A refused escalation is told to the user rather than silently applied. Where it is answered depends
        # on whether the deployment has a lane the content may use at all, which is the caller's fact.
        note = ''
        if user_wants_stronger:
            note = ('You asked for the stronger model, and this content may not be sent to it.')
            if default_lane == 'local':
                note += ' The answer below comes from the model this deployment serves locally.'
            else:
                note += (' This deployment has no local model, so no answer could be produced.')
        return Decision(
            lane=default_lane if default_lane == 'local' else 'none', model=model_name, escalated=False,
            reason=why, notice=note, escalation_requested=user_wants_stronger, steps=tuple(steps))

    if task and task in AGENTIC_TASKS:
        steps.append('trigger: ' + task + ' is on the agentic list')
        return Decision(lane='frontier', model=model_name, escalated=True, trigger=TRIGGER_AGENTIC,
                        reason='the task ' + task + ' is on the agentic pack list',
                        notice='This is an agentic task, so the stronger model answered.',
                        escalation_requested=user_wants_stronger, steps=tuple(steps))

    if user_wants_stronger:
        steps.append('trigger: the user asked for the stronger model')
        return Decision(lane='frontier', model=model_name, escalated=True, trigger=TRIGGER_USER,
                        reason='the user asked for the stronger model',
                        notice='You asked for the stronger model, so it answered.',
                        escalation_requested=True, steps=tuple(steps))

    steps.append('no trigger fired: the ordinary model answers')
    return Decision(lane=default_lane, model=model_name, escalated=False, reason='no trigger fired',
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
        'routing_version': 1,
    }
