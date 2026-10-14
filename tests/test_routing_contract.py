"""The routing decision table as a contract, diffed on every commit.

**Why a golden file rather than assertions.** The table of request-to-lane cases IS the compliance artifact: it is
what an auditor reads to learn which requests reach an external model and why. An assertion proves a property to
a programmer. A diffed table shows a reviewer exactly what changed and when, which is a different and stronger
thing. Research on 2026-10-14 found this pattern in the wild as a declarative policy compiled to decision nodes
with structured audit traces (arXiv 2603.27299), and it is the cheapest way to turn "provable to an auditor"
from a claim into a build artifact.

**To change routing, change the code and this file in the same commit.** The test fails otherwise, which is the
point: a routing change that nobody looked at is the failure mode this prevents. Regenerate with:

    python3 tests/test_routing_contract.py --write
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "services" / "api"))

import routing  # noqa: E402

GOLDEN = ROOT / "tests" / "fixtures" / "routing_decision_table.json"

# The deployments a decision has to be read against, by the shape of their declarations rather than by client.
DEPLOYMENTS = {
    # Everything may leave. A cloud build with a local fallback.
    'permissive_with_local': {
        'manifest': {'access': {'egress': {'frontier_classes': ['public', 'internal', 'confidential',
                                                        'regulated']}}},
        'data_class': 'internal',
        'default_lane': 'local',
    },
    # Everything may leave, and there is no local model, so the ordinary model is remote.
    'permissive_remote_only': {
        'manifest': {'access': {'egress': {'frontier_classes': ['public', 'internal', 'confidential',
                                                        'regulated']}}},
        'data_class': 'internal',
        'default_lane': 'frontier',
    },
    # Only public may leave. A regulated question may not reach the frontier lane at all.
    'public_only': {
        'manifest': {'access': {'egress': {'frontier_classes': ['public']}}},
        'data_class': 'regulated',
        'default_lane': 'local',
    },
    # A deployment that declared nothing. The safe direction is that nothing leaves.
    'undeclared': {
        'manifest': {},
        'data_class': 'regulated',
        'default_lane': 'local',
    },
}

# Every trigger, every refusal, and the two audiences: a user asking, and a user asking for the strong model.
REQUESTS = {
    'plain': {},
    'agentic': {'task': 'reconcile'},
    'unknown_task': {'task': 'something_new'},
    'wants_stronger': {'user_wants_stronger': True},
    'agentic_and_wants_stronger': {'task': 'compare', 'user_wants_stronger': True},
}


def build() -> dict:
    """Every deployment against every request, as the facts an auditor would read."""
    table = {}
    for dname, deployment in DEPLOYMENTS.items():
        for rname, request in REQUESTS.items():
            decision = routing.route(
                deployment['manifest'],
                data_class=deployment['data_class'],
                default_lane=deployment['default_lane'],
                model_name='analyst',
                **request,
            )
            table[dname + ' | ' + rname] = {
                'lane': decision.lane,
                'escalated': decision.escalated,
                'trigger': decision.trigger,
                'escalation_requested': decision.escalation_requested,
                'told_user': bool(decision.notice),
                'reason': decision.reason,
            }
    return table


def main() -> int:
    table = build()
    rendered = json.dumps(table, indent=2, sort_keys=True) + "\n"
    if '--write' in sys.argv:
        GOLDEN.write_text(rendered)
        print('wrote ' + str(len(table)) + ' decisions to ' + str(GOLDEN))
        return 0
    if not GOLDEN.exists():
        print('the golden table is missing. Generate it: python3 tests/test_routing_contract.py --write')
        return 1
    expected = GOLDEN.read_text()
    if expected == rendered:
        print('the routing decision table is unchanged: ' + str(len(table)) + ' decisions')
        return 0
    # A diff, not just a failure, because the reviewer needs to see what moved.
    import difflib
    print('THE ROUTING DECISION TABLE CHANGED. Every line is a request that may now reach a different model.')
    print('Review the diff, and if it is intended, regenerate with --write in the same commit.')
    print()
    for line in difflib.unified_diff(expected.splitlines(), rendered.splitlines(),
                                     'committed', 'now', lineterm='', n=1):
        print(line)
    return 1


def test_the_decision_table_matches_the_committed_one():
    """The contract. A routing change without a table change fails here."""
    assert main() == 0


def test_no_trigger_adds_exposure():
    """The invariant underneath the whole table, asserted separately so it survives a regeneration."""
    for dname, deployment in DEPLOYMENTS.items():
        for rname, request in REQUESTS.items():
            decision = routing.route(
                deployment['manifest'], data_class=deployment['data_class'],
                default_lane=deployment['default_lane'], model_name='analyst', **request)
            if decision.escalated:
                permitted, _ = routing.gate('analyst', deployment['data_class'],
                                            ((deployment['manifest'].get('access') or {})
                                             .get('egress') or {}).get('frontier_classes'))
                assert permitted, dname + ' | ' + rname + ' escalated past a gate that refused'


def test_a_user_asking_for_the_stronger_model_is_always_told_something():
    """Decided 2026-10-14: never leave the user guessing about an escalation."""
    for dname, deployment in DEPLOYMENTS.items():
        decision = routing.route(deployment['manifest'], data_class=deployment['data_class'],
                                 default_lane=deployment['default_lane'], model_name='analyst',
                                 user_wants_stronger=True)
        if decision.escalated or decision.lane == 'none':
            assert decision.notice, dname + ' escalated or refused an escalation without telling the user'


if __name__ == "__main__":
    sys.exit(main())
