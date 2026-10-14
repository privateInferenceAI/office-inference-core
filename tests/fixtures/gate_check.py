"""Does the data boundary actually hold on a plain request?"""
import json, sys, urllib.request, urllib.error
sys.path.insert(0, '/w/services/retrieval'); sys.path.insert(0, '/w/services/session')
sys.path.insert(0, '/w/services/api')
import access, session as sm, routing

manifest = json.load(open('/w/rendered/manifest.json'))
data_class = ((manifest.get('access') or {}).get('data_class') or 'regulated')
frontier = ((manifest.get('access') or {}).get('egress') or {}).get('frontier_classes')
print('this deployment: data_class=%r frontier_classes=%r' % (data_class, frontier))

d = routing.route(manifest, data_class='regulated', model_name='analyst', default_lane='frontier')
print('plain request, class not permitted -> lane=%r' % d.lane)
print('  notice: %s' % d.notice[:80])

# And what the service does, with a manifest the deployment does not have: the refusal is the point.
reg = access.load('/w/rendered/access.json')
store = sm.PostgresStore(sm.database_url())
s = sm.mint(reg, store, 'probe-staff@officeinference.com', next(iter(sorted(reg.roles))), dev=True,
            deployment_id='gate-check')
body = json.dumps({'session': s.session_id, 'question': 'What is the mileage rate?'}).encode()
req = urllib.request.Request('http://api:8088/ask', data=body, method='POST',
                             headers={'Content-Type': 'application/json'})
try:
    with urllib.request.urlopen(req, timeout=120) as r:
        got = json.loads(r.read())
        print('live /ask (permitted class) -> HTTP %s lane=%r kind=%r' % (r.status, got.get('lane'), got.get('kind')))
except urllib.error.HTTPError as e:
    print('live /ask -> HTTP %s %s' % (e.code, e.read().decode()[:90]))
store.close()