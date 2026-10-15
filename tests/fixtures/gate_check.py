"""Does the data boundary hold on BOTH endpoints, for a class that may not leave?

**Why this probe looks the way it does.** The first version called `routing.route()` in-process and posted once to
`/ask`. It printed the same three lines whatever the service did, so it could not have caught the two defects the
C3 panel found: the JSON path returning the PII canary unredacted, and the streaming path having **no egress gate
at all**. The manual told readers to run this one "to check the claim rather than take it", and a probe that cannot
fail is not a probe.

**A boundary is only real if it holds on every path to the model.** Both defects were a control implemented on the
endpoint somebody was looking at. So this asks **both** endpoints, and fails if one refuses and the other answers.

**Run it with the runner, which sets up the forbidden-class deployment it needs:**

    bash tests/fixtures/gate_check_run.sh

**The class has to be real, not simulated.** This asserts the refusal *names the class*, so an API that quietly
fell back to the real manifest fails here rather than passing for the wrong reason. That mistake was made once
already, while reproducing the bypass by hand.
"""
import json, os, sys, urllib.request, urllib.error

TARGET = os.environ.get('GATE_CHECK_URL', 'http://gate-check-tmp-api:8088')
EXPECTED_CLASS = os.environ.get('GATE_CHECK_CLASS', 'regulated')
PERMITTED = os.environ.get('GATE_CHECK_PERMITTED_URL', 'http://api:8088')


def fail(msg):
    print('FAILED: ' + msg)
    raise SystemExit(1)


def post(url, body, timeout=120):
    req = urllib.request.Request(url, data=json.dumps(body).encode(), method='POST',
                                 headers={'Content-Type': 'application/json'})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, r.read().decode(errors='replace')
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode(errors='replace')
    except Exception as exc:
        return None, type(exc).__name__ + ': ' + str(exc)


def reason_of(text):
    try:
        return json.loads(text).get('reason') or json.loads(text).get('kind') or ''
    except Exception:
        return text[:80].replace(chr(10), ' ')


print('target: %s   expecting refusals that name %r' % (TARGET, EXPECTED_CLASS))

sys.path.insert(0, '/w/services/retrieval'); sys.path.insert(0, '/w/services/session')
import access, session as sm

reg = access.load('/w/rendered/access.json')
store = sm.PostgresStore(sm.database_url())
session = sm.mint(reg, store, 'probe-staff@officeinference.com', next(iter(sorted(reg.roles))), dev=True,
                  deployment_id='gate-check')
body = {'session': session.credential, 'question': 'What is the mileage rate?'}

codes = {}
for label, url in (('/ask', TARGET + '/ask'), ('/ask/stream', TARGET + '/ask/stream')):
    code, text = post(url, body)
    codes[label] = code
    reason = reason_of(text)
    print('  forbidden class on %-11s -> HTTP %s  %s' % (label, code, reason[:96]))
    # The class must be named, or the deployment under test is not the one we think it is.
    if code == 403 and EXPECTED_CLASS not in text:
        fail('%s refused, but the refusal does not name %r, so this deployment did not load the class it was '
             'given. The run would have passed for the wrong reason' % (label, EXPECTED_CLASS))
store.close()

open_paths = [p for p, c in codes.items() if c != 403]
if open_paths:
    fail('**the boundary holds on one endpoint and not on %s.** A class that may not leave was answered by the '
         'model on: %s' % (', '.join(open_paths), ', '.join(open_paths)))

# The permitted path still answers, and still says what it redacted. A guard that breaks the happy path is its
# own defect, and a client that cannot see what was removed cannot tell.
code, text = post(PERMITTED + '/ask', body)
if code != 200:
    fail('a permitted request no longer answers: HTTP %s %s' % (code, text[:140]))
got = json.loads(text)
print('  permitted class on /ask        -> HTTP %s lane=%r kind=%r' % (code, got.get('lane'), got.get('kind')))
if 'redactions' not in got:
    fail('the answer carries no redactions field, so a client cannot tell what was removed')
print('    redactions: %r' % (got.get('redactions'),))

print('PASSED: refused on both endpoints for a class that may not leave, and the permitted path still answers')
