"""Can any integer rule separate single-fact questions from genuine synthesis?

Trigger 2 decides synthesis on a count of distinct documents. The first measurement showed the count inverts: a
plain single-fact question returns more documents than the question that genuinely needs reconciling, because the
corpus is small and one fact is legitimately spread across several files.

This asks the narrower question: is there ANY combination of a distinct-document count and a score floor that
fires on the synthesis shapes and not on the single-fact ones? It prints every candidate rule and whether it
separates them, so the answer is a table rather than an opinion.
"""
import os, sys

sys.path.insert(0, '/w/services/retrieval')
sys.path.insert(0, '/w/services/session')
sys.path.insert(0, '/w/services/ingestion')
import access, ingest, retriever, session as sm

# What each question shape actually is, so a rule can be scored against the truth rather than against a guess.
SHAPES = [
    ('single-fact',   'What is the mileage rate for reimbursement?',                  'single'),
    ('single-fact-2', 'What is the daily meal allowance?',                            'single'),
    ('definition',    'What does the expense policy say about receipts?',              'single'),
    ('absent',        'What is the policy on lunar travel?',                           'single'),
    ('two-doc',       'Compare the travel policy and the expense policy on receipts.', 'synthesis'),
    ('multi-doc',     'Summarise everything the company documents say about reimbursement.', 'synthesis'),
]

DOC_RULES = [1, 2, 3, 4, 5, 6]
SCORE_FLOORS = [0.0, 0.45, 0.5, 0.55, 0.6]


def passages_for(registry, question):
    subject = sorted(registry.users)[0]
    store = sm.PostgresStore(sm.database_url())
    try:
        session = sm.mint(registry, store, subject, next(iter(sorted(registry.roles))), dev=True,
                          deployment_id='trigger-rule')
        permit = session.permit()
    finally:
        store.close()
    result = retriever.search(ingest.Services(), question, permit.role, limit=6, permit=permit)
    return [(p.doc_id, getattr(p, 'score', 0.0) or 0.0) for p in result.passages]


def main():
    registry = access.load(os.environ.get('ACCESS_REGISTRY', '/w/rendered/access.json'))
    data = {}
    for name, question, truth in SHAPES:
        rows = passages_for(registry, question)
        data[name] = (truth, rows)
        print('%-14s %-9s %d passage(s), %d distinct doc(s)' % (name, truth, len(rows), len({d for d, _ in rows})))
    print()
    print('%-8s %-7s %-9s %s' % ('docs>=', 'floor', 'separates', 'fires on'))
    print('-' * 62)
    winners = []
    for floor in SCORE_FLOORS:
        for cut in DOC_RULES:
            fired = {}
            for name, (truth, rows) in data.items():
                kept = {d for d, s in rows if s >= floor}
                fired[name] = len(kept) >= cut
            singles = [n for n, (t, _) in data.items() if t == 'single']
            synth = [n for n, (t, _) in data.items() if t == 'synthesis']
            fp = [n for n in singles if fired[n]]
            fn = [n for n in synth if not fired[n]]
            ok = not fp and not fn
            if ok:
                winners.append((cut, floor))
            detail = ''
            if fp:
                detail += 'single-fact: ' + ', '.join(fp)
            if fn:
                detail += ('  ' if detail else '') + 'missed: ' + ', '.join(fn)
            print('%-8d %-7.2f %-9s %s' % (cut, floor, 'YES' if ok else 'no', detail[:48]))
    print()
    if winners:
        print('rules that separate cleanly:', winners)
    else:
        print('**no candidate rule separates them.** Every combination either fires on a single-fact question')
        print('or misses a genuine synthesis one. The count is not a usable proxy on this corpus.')


if __name__ == "__main__":
    main()
