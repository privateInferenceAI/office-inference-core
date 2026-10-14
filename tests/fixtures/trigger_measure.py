"""What retrieval actually returns, per question shape.

Trigger 2 escalates when the answer needs synthesis across documents. It is deciding on an integer: how many
distinct document ids come back. The first threshold was 2, chosen without measuring, and it fired on every
question because the retriever returns up to 6 passages and the corpus has more than one document.

So this measures the integers. No model is called and nothing is generated: the question goes to the retriever
and the passages are counted. Run it with no arguments on the deployment.
"""
import json, os, sys, urllib.request

sys.path.insert(0, '/w/services/retrieval')
sys.path.insert(0, '/w/services/session')
import access, retriever, session as sm

# One question per shape, including the shapes the old triggers were meant to catch and the ones they wrongly
# caught. `mileage` is the plain single-fact question that escalated on the first live run.
QUESTIONS = [
    ('single-fact',   'What is the mileage rate for reimbursement?'),
    ('single-fact-2', 'What is the daily meal allowance?'),
    ('definition',    'What does the expense policy say about receipts?'),
    ('two-doc',       'Compare the travel policy and the expense policy on receipts.'),
    ('multi-doc',     'Summarise everything the company documents say about reimbursement.'),
    ('broad',         'What are all the rules about expenses?'),
    ('absent',        'What is the policy on lunar travel?'),
    ('off-topic',     'How do I bake sourdough bread?'),
]

LIMITS = [4, 6, 10]


def search(registry, question, want):
    # The widest permitted session, so the count is about the question rather than about a role's roots.
    subject = sorted(registry.users)[0]
    store = sm.PostgresStore(sm.database_url())
    try:
        session = sm.mint(registry, store, subject, next(iter(sorted(registry.roles))), dev=True,
                          deployment_id='trigger-measure')
        permit = session.permit()
    finally:
        store.close()
    result = retriever.search(None, question, permit.role, limit=want, permit=permit)
    return result


def main():
    registry = access.load(os.environ.get('ACCESS_REGISTRY', '/w/rendered/access.json'))
    print('%-14s %-6s %6s %8s %8s %8s' % ('shape', 'limit', 'docs', 'passages', 'min score', 'max score'))
    print('-' * 60)
    rows = {}
    for shape, question in QUESTIONS:
        for want in LIMITS:
            try:
                result = search(registry, question, want)
            except Exception as exc:  # noqa: BLE001
                print('%-14s %-6s FAILED %s' % (shape, want, str(exc)[:60]))
                continue
            passages = result.passages
            docs = sorted({p.doc_id for p in passages})
            scores = [getattr(p, 'score', 0.0) or 0.0 for p in passages]
            print('%-14s %-6d %6d %8d %8.3f %8.3f' % (
                shape, want, len(docs), len(passages),
                min(scores) if scores else 0.0, max(scores) if scores else 0.0))
            if want == 6:
                rows[shape] = {'docs': len(docs), 'passages': len(passages), 'ids': docs,
                               'scores': [round(s, 3) for s in scores]}
    print()
    print('at the deployment limit of 6, the distinct-document counts are:')
    for shape, row in rows.items():
        print('  %-14s docs=%d passages=%d floors=%s' % (shape, row['docs'], row['passages'], row['scores']))
    print()
    print(json.dumps(rows, indent=2)[:1200])


if __name__ == "__main__":
    main()
