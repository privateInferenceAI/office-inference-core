"""A stub that speaks the provider's failure shapes, so the failure paths can be tested every commit.

**This is not evidence of a real throttle and the runbook says so.** It is how the classification, the retry
rules and the degraded answer are exercised in CI without waiting for a provider to misbehave. The real
throttle is provoked against Bedrock, with real load, once per release.

Reached by pointing a model at it through the rendered gateway config: `api_base` is the field LiteLLM honours
for this, and it lives in a generated file rather than in an environment variable, so what a test run pointed at
is a thing somebody can read afterwards.

Usage, from the deployment root:

    docker run -d --name oi-fault-stub --network ai-net -p 8099:8099 \
      -v /opt/oi-core/tests/faultinject:/app:ro -w /app --entrypoint uvicorn \
      python:3.12-slim fault_stub:app --host 0.0.0.0 --port 8099

    # Then render a config whose model points at it, and restart the gateway:
    OI_FAULT_ENDPOINT=http://oi-fault-stub:8099 python3 scripts/render_manifest.py \
      --manifest manifests/dev-cloud.yaml

The scenario is chosen per request with a header, so one stub covers the whole taxonomy without a restart:

    curl -H "x-fault: throttle"      ...   # 429 with Retry-After
    curl -H "x-fault: throttle-hard" ...   # 429 with no Retry-After, the spend-cap shape
    curl -H "x-fault: unavailable"   ...   # 503
    curl -H "x-fault: capacity"      ...   # 529
    curl -H "x-fault: malformed"     ...   # a chunk that is not JSON
    curl -H "x-fault: midstream"     ...   # 200, two chunks, then an error event
    curl -H "x-fault: stall"         ...   # 200, two chunks, then nothing
    curl -H "x-fault: ok"            ...   # a normal short answer, so a test can prove the stub is not the fault
"""
from __future__ import annotations

import json
import os
import threading
import time

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, StreamingResponse

app = FastAPI(title='fault injection stub')

# The body AWS returns for a quota throttle, close enough that the classifier is tested against the real shape
# rather than against a shape invented here.
THROTTLE_BODY = {
    'message': 'Too many requests, please wait before trying again.',
    '__type': 'ThrottlingException',
}


def _error(status: int, kind: str, retry_after: str | None = None):
    headers = {'Retry-After': retry_after} if retry_after else {}
    return JSONResponse({'message': f'{kind}: injected by the stub', '__type': kind}, status_code=status,
                        headers=headers)


@app.get('/health')
async def health():
    return {'status': 'ok', 'stub': True}


@app.post('/model/{model_id}/invoke')
@app.post('/model/{model_id}/invoke-with-response-stream')
async def invoke(model_id: str, request: Request):
    fault = request.headers.get('x-fault', 'ok')

    if fault == 'throttle':
        return _error(429, 'ThrottlingException', retry_after='1')
    if fault == 'throttle-hard':
        # The spend-cap shape: a 429 that retrying cannot fix, and no Retry-After to say so.
        return _error(429, 'enforced_spend_limit_reached')
    if fault == 'unavailable':
        return _error(503, 'ServiceUnavailableException')
    if fault == 'capacity':
        return _error(529, 'ModelNotReadyException')
    if fault == 'malformed':
        return StreamingResponse(_malformed(), media_type='application/json')
    if fault == 'midstream':
        return StreamingResponse(_midstream(), media_type='application/json')
    if fault == 'stall':
        return StreamingResponse(_stall(), media_type='application/json')
    return JSONResponse({'output': {'message': {'content': [{'text': 'stub answer'}]}}})


def _frame(text: str) -> bytes:
    payload = {'output': {'message': {'content': [{'text': text}]}}}
    return ("data: " + json.dumps(payload) + "\n\n").encode()


def _malformed():
    yield _frame('the first part, ')
    yield b'data: {this is not json}\n\n'


def _midstream():
    yield _frame('the answer starts ')
    yield b'event: error\ndata: {"__type": "InternalServerException"}\n\n'


def _stall():
    yield _frame('a few tokens, ')
    yield _frame('then nothing at all. ')
    # Long enough that a healthy budget must notice. The watchdog polls between reads, so this is the case it
    # exists for.
    time.sleep(float(os.environ.get('OI_STUB_STALL_SECONDS', '30')))
    yield _frame('and finally more, long after anybody was waiting.')
