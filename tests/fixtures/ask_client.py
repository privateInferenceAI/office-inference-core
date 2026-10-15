"""Ask the assistant API the way a client would, and print what came back.

**Why this exists.** Stage 13b of the operator guide tells an operator to ask a question, and until now the
only thing in the repository that spoke HTTP to the API was a probe asserting specific rights. A guide that
names a command nobody can run is worse than a guide with a gap in it, so the command exists.

It mints a session for a declared subject, posts one question, and prints the answer and the fields that
matter. It asserts nothing: `interface_probe.py` does that.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

W = Path('/w')
sys.path.insert(0, str(W / 'services' / 'retrieval'))
sys.path.insert(0, str(W / 'services' / 'session'))

import access as access_module  # noqa: E402
import session as session_module  # noqa: E402

API = os.environ.get('OI_API_URL', 'http://api:8088')


def database_url() -> str:
    url = (os.environ.get('DATABASE_URL') or '').strip()
    if url:
        return url
    user = os.environ.get('POSTGRES_USER', '')
    password = os.environ.get('POSTGRES_PASSWORD', '')
    database = os.environ.get('POSTGRES_DB', '')
    return ('postgresql://' + user + ':' + password + '@postgres:5432/' + database) if user else ''


def mint(subject: str, role: str) -> str:
    registry = access_module.load(os.environ.get('ACCESS_REGISTRY', str(W / 'rendered' / 'access.json')))
    store = session_module.PostgresStore(database_url())
    try:
        session = session_module.mint(registry, store, subject, role, dev=True, deployment_id='ask-client')
        return session.credential
    finally:
        store.close()


def show(body: dict) -> None:
    width = 14
    label = lambda text: text.ljust(width)
    print(label('answer') + (body.get('answer') or '(none)'))
    print(label('model') + repr(body.get('model')) + '   (the role)')
    print(label('resolved') + repr(body.get('resolved_model')) + '   (what actually ran)')
    print(label('lane') + repr(body.get('lane')) + ' escalated=' + repr(body.get('escalated'))
          + ' trigger=' + repr(body.get('trigger')))
    print(label('notice') + repr(body.get('notice') or ''))
    print(label('kind') + repr(body.get('kind')) + ' reason=' + repr(body.get('reason'))
          + ' attempts=' + repr(body.get('attempts')))
    for n, c in enumerate(body.get('citations') or [], 1):
        print('  [' + str(n) + '] ' + str(c.get('doc_id')) + ' p' + str(c.get('page_start')))
    if body.get('unsupported'):
        print(label('unsupported') + str(len(body['unsupported'])) + ' sentence(s) the passages do not back')
    if body.get('passages'):
        print(label('passages') + str(len(body['passages'])) + ', because the model could not answer')


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('--question', '-q', required=True)
    parser.add_argument('--subject', default='probe-staff@officeinference.com')
    parser.add_argument('--model-role', default=None, help='the session role, not the model')
    parser.add_argument('--task', default='', help='a task name, for the agentic trigger')
    parser.add_argument('--stronger', action='store_true', help='ask for the stronger model')
    parser.add_argument('--stream', action='store_true', help='use /ask/stream and print the frames')
    parser.add_argument('--json', action='store_true', help='print the raw response')
    args = parser.parse_args()

    if not database_url():
        print('no database credentials, so no session can be minted', file=sys.stderr)
        return 2

    registry = access_module.load(os.environ.get('ACCESS_REGISTRY', str(W / 'rendered' / 'access.json')))
    role = args.model_role or next((n for n, r in sorted(registry.roles.items()) if not r),
                                   sorted(registry.roles)[0])
    token = mint(args.subject, role)
    body = {'session': token, 'question': args.question}
    if args.task:
        body['task'] = args.task
    if args.stronger:
        body['stronger'] = True

    path = '/ask/stream' if args.stream else '/ask'
    request = urllib.request.Request(API + path, data=json.dumps(body).encode(), method='POST',
                                     headers={'Content-Type': 'application/json'})
    started = time.monotonic()
    try:
        with urllib.request.urlopen(request, timeout=180) as response:
            if args.stream:
                for raw in response:
                    line = raw.decode('utf-8', errors='replace').rstrip()
                    if line.startswith('event:') or line.startswith('data:'):
                        print(line[:200])
                return 0
            payload = json.loads(response.read())
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode(errors='replace')
        print('HTTP ' + str(exc.code) + ' after %.2fs' % (time.monotonic() - started))
        print(raw[:600])
        return 1

    if args.json:
        print(json.dumps(payload, indent=2))
        return 0
    show(payload)
    print('elapsed       %.2fs' % (time.monotonic() - started))
    return 0


if __name__ == "__main__":
    sys.exit(main())
