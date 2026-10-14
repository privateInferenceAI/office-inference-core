"""Does the real route to a ThrottlingException work, and does our classification read it?

**Why a probe rather than a guess.** The first method proposed was admission arithmetic: send a request whose
`input + max_tokens` exceeds the per-model quota. Bedrock validates `max_tokens` against the model output limit
first and returns a 400, so the value never reaches the quota check. This tries the honest route instead: real
concurrency against a real quota.

The quota arithmetic, read from Service Quotas on 2026-10-14: Nova Lite is 4,000,000 tokens per minute and a
single request may ask for at most 10,000, so exhausting it takes roughly four hundred maximum-size requests.
That is not reachable from a development box, and this probe says so instead of pretending.
"""
from __future__ import annotations

import concurrent.futures
import json
import os
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

W = Path('/w')
sys.path.insert(0, str(W / 'services' / 'gateway'))
sys.path.insert(0, str(W / 'services' / 'session'))
sys.path.insert(0, str(W / 'services' / 'api'))

import assertion  # noqa: E402
import budget as budget_module  # noqa: E402

GATEWAY = os.environ.get('OI_GATEWAY_URL', 'http://litellm:4000')
MODEL = os.environ.get('OI_DEFAULT_MODEL_ROLE', 'analyst')
CONCURRENCY = int(os.environ.get('OI_THROTTLE_CONCURRENCY', '8'))
MAX_TOKENS = int(os.environ.get('OI_THROTTLE_MAX_TOKENS', '9000'))


def one(index: int):
    key = assertion.load_key('/w/rendered/assertion.key')
    token = assertion.mint(key, MODEL, session_id='throttle-%d' % index)
    # A large prompt plus a large output ceiling: this is the closest a single request can get to the quota.
    filler = ('expense policy travel meals receipts ' * 900)
    body = {'model': MODEL, 'max_tokens': MAX_TOKENS,
            'messages': [{'role': 'user', 'content': filler + ' Say ok.'}]}
    request = urllib.request.Request(GATEWAY + '/v1/chat/completions',
                                     data=json.dumps(body).encode(), method='POST',
                                     headers={'Content-Type': 'application/json', assertion.HEADER: token})
    started = time.monotonic()
    try:
        with urllib.request.urlopen(request, timeout=180) as response:
            response.read()
            return {'status': response.status, 'seconds': time.monotonic() - started, 'body': ''}
    except urllib.error.HTTPError as exc:
        return {'status': exc.code, 'seconds': time.monotonic() - started,
                'body': exc.read().decode(errors='replace')[:400]}
    except Exception as exc:  # noqa: BLE001
        return {'status': 0, 'seconds': time.monotonic() - started, 'body': type(exc).__name__}


def main() -> int:
    print('sending %d concurrent request(s) of up to %d output tokens each' % (CONCURRENCY, MAX_TOKENS))
    started = time.monotonic()
    with concurrent.futures.ThreadPoolExecutor(max_workers=CONCURRENCY) as pool:
        results = list(pool.map(one, range(CONCURRENCY)))
    elapsed = time.monotonic() - started

    statuses = {}
    for result in results:
        statuses[result['status']] = statuses.get(result['status'], 0) + 1
    print('statuses:', statuses, 'in %.1fs' % elapsed)

    throttled = [r for r in results if 'Throttling' in r['body'] or 'Too many' in r['body']]
    if not throttled:
        print()
        print('NO THROTTLE PROVOKED. That is the expected outcome and it is not a failure of the system:')
        print('Nova Lite permits 4,000,000 tokens per minute and this sent %d requests of roughly 9,000.'
              % CONCURRENCY)
        print('Reaching the quota needs about four hundred of them. The method is sound; the ceiling is high.')
        return 2

    print()
    print('A REAL THROTTLE ARRIVED from the provider. What our classification makes of it:')
    for result in throttled[:2]:
        kind = budget_module.classify(result['body'])
        print('  status %d -> reason=%r retryable=%r matched=%r'
              % (result['status'], kind['reason'], kind['retryable'], kind['matched']))
        print('  provider said: ' + ' '.join(result['body'].split())[:200])
    return 0


if __name__ == "__main__":
    sys.exit(main())
