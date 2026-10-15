"""Provoke a REAL provider throttle, and check what the deployment makes of it.

**The request ceiling, not the token ceiling.** The first version of this sized its load against the tokens
per minute quota: Nova Lite allows 4,000,000, a request may ask for at most 10,000, so exhausting it takes
roughly four hundred maximum-size requests. That is out of reach from a development box and the probe said so,
which made it useless as a test. The requests-per-minute ceiling is the cheap route, read from Service Quotas:

    Nova Lite, on-demand              200 requests per minute
    Nova Micro, on-demand             200
    Nova Pro, on-demand                25
    Nova Pro, latency-optimized         1

So a burst of several hundred small concurrent calls reaches the ceiling in seconds and costs a fraction of a
cent, because each call generates one token.

**It asks the API while the burst is running**, because a throttle that has already passed proves nothing. What
it is looking for is a degraded answer whose reason is a real classification, and all three attempts present:

    reason    | attempts |  matched   |  threshold
    ---------- | -------- | ---------- | -----------
     throttled | 3        | throttling | first_token

**`reason: unclassified` with `attempts: 2` is a defect, not a throttle.** It means the retry failed to
authenticate, which is what a spent signed statement produces: the first attempt earns the real 429 and the
retry replays a statement the gateway has already seen. That was a real bug, found by running this.
"""
from __future__ import annotations

import argparse
import concurrent.futures
import json
import os
import sys
import threading
import time
import urllib.error
import urllib.request
from collections import Counter
from pathlib import Path

W = Path('/w')
sys.path.insert(0, str(W / 'services' / 'retrieval'))
sys.path.insert(0, str(W / 'services' / 'session'))
sys.path.insert(0, str(W / 'services' / 'gateway'))

import access as access_module  # noqa: E402
import assertion  # noqa: E402
import session as session_module  # noqa: E402

GATEWAY = os.environ.get('OI_GATEWAY_URL', 'http://litellm:4000')
API = os.environ.get('OI_API_URL', 'http://api:8088')


def database_url() -> str:
    url = (os.environ.get('DATABASE_URL') or '').strip()
    if url:
        return url
    user = os.environ.get('POSTGRES_USER', '')
    password = os.environ.get('POSTGRES_PASSWORD', '')
    database = os.environ.get('POSTGRES_DB', '')
    return ('postgresql://' + user + ':' + password + '@postgres:5432/' + database) if user else ''


def burn(model: str, key, stop: threading.Event, results: list, index: int) -> None:
    """Small calls, as fast as the ceiling allows. One output token, so the cost is rounding error."""
    while not stop.is_set():
        token = assertion.mint(key, model, session_id='burst-' + str(index))
        body = json.dumps({'model': model,
                           'messages': [{'role': 'user', 'content': 'ok'}],
                           'max_tokens': 1}).encode()
        request = urllib.request.Request(GATEWAY + '/v1/chat/completions', data=body, method='POST',
                                         headers={'Content-Type': 'application/json',
                                                  assertion.HEADER: token})
        try:
            with urllib.request.urlopen(request, timeout=20) as response:
                results.append(response.status)
        except urllib.error.HTTPError as exc:
            results.append(exc.code)
        except Exception:  # noqa: BLE001
            results.append(0)


def ask(token: str, question: str):
    body = json.dumps({'session': token, 'question': question}).encode()
    request = urllib.request.Request(API + '/ask', data=body, method='POST',
                                     headers={'Content-Type': 'application/json'})
    try:
        with urllib.request.urlopen(request, timeout=180) as response:
            return response.status, json.loads(response.read())
    except urllib.error.HTTPError as exc:
        return exc.code, {'error': exc.read().decode(errors='replace')[:200]}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('--concurrency', type=int, default=60)
    parser.add_argument('--seconds', type=float, default=12.0)
    parser.add_argument('--asks', type=int, default=6)
    parser.add_argument('--model', default=os.environ.get('OI_DEFAULT_MODEL_ROLE', 'analyst'))
    parser.add_argument('--question', default='What is the mileage rate for reimbursement?')
    args = parser.parse_args()

    if not database_url():
        print('no database credentials', file=sys.stderr)
        return 2

    key = assertion.load_key(os.environ.get('OI_ASSERTION_KEY', '/w/rendered/assertion.key'))
    registry = access_module.load(os.environ.get('ACCESS_REGISTRY', str(W / 'rendered' / 'access.json')))
    store = session_module.PostgresStore(database_url())
    role = next((n for n, r in sorted(registry.roles.items()) if not r), sorted(registry.roles)[0])
    session = session_module.mint(registry, store, 'probe-staff@officeinference.com', role, dev=True,
                                  deployment_id='throttle-probe')

    stop = threading.Event()
    results: list = []
    pool = concurrent.futures.ThreadPoolExecutor(max_workers=args.concurrency)
    futures = [pool.submit(burn, args.model, key, stop, results, i) for i in range(args.concurrency)]
    print('bursting %d thread(s) for %.0fs against %s' % (args.concurrency, args.seconds, args.model))
    time.sleep(args.seconds)

    print('asking the API %d time(s) while the pressure holds' % args.asks)
    seen = []
    for _ in range(args.asks):
        status, body = ask(session.credential, args.question)
        seen.append((status, body.get('kind'), body.get('reason'), body.get('attempts'),
                     body.get('threshold')))

    stop.set()
    pool.shutdown(wait=False)
    store.close()

    for status, kind, reason, attempts, threshold in seen:
        print('  HTTP %s kind=%r reason=%r attempts=%r threshold=%r'
              % (status, kind, reason, attempts, threshold))
    print('burst outcomes:', dict(Counter(results)))

    degraded = [row for row in seen if row[1] == 'degraded']
    typed = [row for row in degraded if row[2] and row[2] != 'unclassified']
    print()
    if typed:
        print('A REAL THROTTLE, CORRECTLY CLASSIFIED. The provider refused, the deployment degraded,'
              'and the reason is a real classification rather than an unrecognised failure.')
        return 0
    if degraded:
        print('IT DEGRADED, BUT THE REASON IS unclassified. That is a defect rather than a throttle: it is what'
              'a retry that cannot authenticate produces, because the first attempt earns the real 429 and the'
              'retry replays a spent statement and gets a 401.')
        return 1
    print('NO THROTTLE PROVOKED. The burst answered every call, so the ceiling was not reached: raise'
          ' --concurrency or --seconds, or the provider is not throttling this deployment at all.')
    return 2


if __name__ == "__main__":
    sys.exit(main())
