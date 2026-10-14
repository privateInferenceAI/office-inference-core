"""Where a request goes, decided by a pure function over facts the caller already holds.

**The gate is consulted whenever a request would reach a remote model, and the triggers choose a lane only
inside what it permitted.** A trigger is a cost decision; the gate is a data-boundary decision. Settling the
boundary before asking how hard a question is, is what makes the answer to "why did this content reach an
external model" a sentence about a declared permission rather than a story about a heuristic firing.

**The boundary is one-way.** No trigger may move content toward a more exposed lane. Escalation only ever
happens within what the gate allowed: if a trigger wants the frontier lane and the gate refuses, the request is
answered on the default lane and the refusal is announced.

**Every predicate is over something already known.** The task list is a lookup, and the user's request is a
field. Nothing here asks a model how hard a question is, which is the property the requirement is aimed at.

**Pure on purpose.** No database handle and no HTTP client, because the table of request-to-lane cases is the
compliance artifact and it has to be testable without a deployment.
"""
from __future__ import annotations

from dataclasses import dataclass, field

TRIGGER_AGENTIC = 'agentic_task'
TRIGGER_USER = 'user_requested_stronger'
TRIGGERS = (TRIGGER_AGENTIC, TRIGGER_USER)

# **There was a third trigger here and it was removed, not tuned.** It escalated when retrieval returned more
# than one distinct document, on the theory that several documents means an answer needs reconciling. Two
# measurements killed it. The first: a plain single-fact question returned SIX distinct documents while the
# question that genuinely needed reconciling returned FOUR, because this corpus is small and one fact is
# legitimately spread across several files. The second: every combination of a document count from 1 to 6 and a
# score floor from 0.00 to 0.60 either fired on a single-fact question or missed a synthesis one, with no
# separating rule in the whole table. The evidence is in tests/fixtures/trigger_rules.py.

AGENTIC_TASKS = frozenset({
    'summarise_across_documents',
    'reconcile',
    'compare',
    'draft',
    'extract_table',
    'multi_step',
})


def declared_tasks(manifest: dict) -> frozenset[str]:
    """The tasks this deployment declares, read from its manifest.

    **Why the list moved out of this file.** The six names above were hardcoded here and used for one thing:
    to decide whether a request escalates. Everything else about them was missing. A client could not
    discover them, nothing labelled them in the client's words, nothing said what they touch, and the
    manifest, which is what defines the deployment, knew nothing about them. So the client app's Do screen
    would have had to be written into the app, which breaks the rule that the app is a function of the
    manifest.

    **What happens when a manifest declares none.** An absent block falls back to the set above, because a
    deployment that predates this must keep behaving exactly as it did. **An empty list is not an absent
    block**: a deployment that declares `tasks: []` has said it has no tasks and gets no escalation. That
    distinction is the whole reason this reads the key rather than the value.
    """
    agentic = manifest.get('agentic')
    if not isinstance(agentic, dict) or 'tasks' not in agentic:
        return AGENTIC_TASKS
    out = set()
    for entry in agentic.get('tasks') or []:
        if isinstance(entry, str) and entry:
            out.add(entry)
        elif isinstance(entry, dict) and entry.get('id'):
            out.add(entry['id'])
    return frozenset(out)


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


def gate(data_class: str, frontier_classes) -> tuple:
    """May this content reach a REMOTE model, and why not when it may not.

    **Refuses rather than defaulting.** An empty egress declaration means nothing leaves, which is the office
    shape and the safe direction for a cloud deployment to fail in.

    **This is asked about the frontier lane only, and that matters.** The earlier version took a
    deployment-wide flag saying whether a local model existed anywhere and returned permitted immediately when
    it did. So on any deployment declaring a local model the gate was skipped entirely and every trigger ran
    unconditionally, which inverted the one-way boundary this module exists to enforce. Whether a local model
    exists is a fact about where a request can be answered, not a fact about whether content may leave.
    """
    allowed = tuple(frontier_classes or ())
    if data_class in allowed:
        return True, 'the deployment permits ' + data_class + ' on a remote lane'
    listed = list(allowed) or 'nothing'
    return False, ('the deployment permits ' + str(listed) + ' on a remote lane, and this request is '
                   + data_class)


def route(manifest: dict, *, data_class: str, task: str = '', user_wants_stronger: bool = False,
          model_name: str = '', default_lane: str = 'frontier', frontier_model: str = '') -> Decision:
    """The whole decision, in one pure function.

    Order matters and is the point: the lane a trigger would use is settled first, and the gate is consulted
    before that lane can be reached.
    """
    egress = ((manifest.get('access') or {}).get('egress') or {})

    # Which trigger, if any, wants the frontier lane. None of them is consulted about anything else.
    wanted = ''
    why = ''
    if task and task in declared_tasks(manifest):
        wanted = TRIGGER_AGENTIC
        why = 'the task ' + task + ' is on the agentic pack list'
    elif user_wants_stronger:
        wanted = TRIGGER_USER
        why = 'the user asked for the stronger model'

    if not wanted:
        # **No trigger, and the boundary is still consulted.** The first version returned the default lane
        # without asking, on the reasoning that nothing had escalated. That left the boundary guarding only
        # escalations: on a deployment whose ordinary model is remote, a request whose class the manifest does
        # not permit went straight to the provider with `escalated: false` and no notice. The page promised a
        # data boundary and the code drew one around a door nobody had to use.
        #
        # Found by the C3 panel's fidelity checker, who ran a plain request against a forbidden class and
        # watched it leave.
        if default_lane == 'frontier':
            permitted, why_not = gate(data_class, egress.get('frontier_classes'))
            if not permitted:
                return Decision(lane='none', model=model_name, escalated=False, reason=why_not,
                                notice='This question cannot be answered, because its content may not '
                                       'be sent to the model that would answer it.',
                                escalation_requested=user_wants_stronger,
                                steps=('gate: ' + why_not, 'no trigger fired, and the default lane is remote'))
            return Decision(lane=default_lane, model=model_name, escalated=False, reason='no trigger fired',
                            escalation_requested=user_wants_stronger,
                            steps=('gate: permitted', 'no trigger fired: the ordinary model answers'))
        # A local default lane reaches no remote model, so there is nothing for the gate to decide.
        return Decision(lane=default_lane, model=model_name, escalated=False, reason='no trigger fired',
                        escalation_requested=user_wants_stronger,
                        steps=('no trigger fired: the answer is produced on this deployment',))

    # A trigger wants the frontier lane, so the boundary decides whether it may have it.
    permitted, why_not = gate(data_class, egress.get('frontier_classes'))
    if not permitted:
        # **Every refused escalation is announced, not only the one the user asked for.** A trigger that fired
        # and was refused is a decision the user would otherwise not know about: they asked a question the
        # deployment decided needed the stronger model, and it was answered on the default lane instead. Silence
        # there is the same defect as a silent downgrade, one step removed.
        if user_wants_stronger:
            note = 'You asked for the stronger model, and this content may not be sent to it.'
        else:
            note = ('This question would normally be answered by the stronger model, and this content may not '
                    'be sent to it.')
        if default_lane == 'local':
            note += ' The answer below comes from the model this deployment serves locally.'
        else:
            note += ' This deployment has no local model, so no answer could be produced.'
        return Decision(lane=default_lane if default_lane == 'local' else 'none', model=model_name,
                        escalated=False, reason=why_not, notice=note,
                        escalation_requested=user_wants_stronger,
                        steps=('gate: ' + why_not, 'trigger ' + wanted + ' was refused by the gate'))

    # Permitted, so it escalates. **The model changes only when the deployment has a separate frontier entry**,
    # which the renderer publishes as `<role>-frontier`. Where it does not, there is no stronger model to call
    # and the notice says so instead of claiming one answered.
    if frontier_model:
        notice = ('This is an agentic task, so the stronger model answered.' if wanted == TRIGGER_AGENTIC
                  else 'You asked for the stronger model, so it answered.')
        return Decision(lane='frontier', model=frontier_model, escalated=True, trigger=wanted, reason=why,
                        notice=notice, escalation_requested=user_wants_stronger,
                        steps=('gate: permitted', 'trigger: ' + wanted))

    notice = ('This is an agentic task. This deployment is configured with a single model, so the same model '
              'answered.' if wanted == TRIGGER_AGENTIC
              else ('You asked for the stronger model. This deployment is configured with a single model, so '
                    'the same model answered.'))
    return Decision(lane='frontier', model=model_name, escalated=True, trigger=wanted,
                    reason=why + ', and this deployment has a single model',
                    notice=notice, escalation_requested=user_wants_stronger,
                    steps=('gate: permitted', 'trigger: ' + wanted, 'no separate frontier model is configured'))


def audit_facts(decision: Decision, data_class: str) -> dict:
    """The routing half of the record. Two facts, never one."""
    return {
        'lane': decision.lane,
        'data_class': data_class,
        'escalated': decision.escalated,
        'trigger': decision.trigger,
        'reason': decision.reason,
        'escalation_requested': decision.escalation_requested,
        'routing_version': 2,
    }
