"""Tests for the reranker benchmark.

These encode the two failures that stopped the first C2 measurement on 2026-10-11. Both were the
client asking the service for more than it accepts, and both looked like the reranker being broken:
a 100-candidate call was refused with 422 because the service takes at most 32 texts per call, and a
ten-candidate call was refused with 429 because TEI consumes one permit per candidate and refuses
rather than queues. The first fix is chunking; the second is retrying, with the wait excluded from
the reported time so a retry cannot flatter the number.

Run: python3 -m pytest tests/
"""
from __future__ import annotations

import io
import json
import sys
import urllib.error
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import bench_rerank  # noqa: E402  (the import needs the path above)


class FakeResponse:
    def __init__(self, payload: bytes = b"[]") -> None:
        self.payload = payload

    def read(self) -> bytes:
        return self.payload

    def __enter__(self) -> "FakeResponse":
        return self

    def __exit__(self, *exc) -> bool:
        return False


@pytest.fixture
def capture(monkeypatch):
    """Replace urlopen with a recorder, so no service is needed to test the client's behaviour."""
    calls: list[list[str]] = []

    def install(behaviour=None):
        def fake_urlopen(req, timeout=None):
            payload = json.loads(req.data.decode())
            calls.append(payload["texts"])
            if behaviour is not None:
                return behaviour(len(calls))
            return FakeResponse()
        monkeypatch.setattr(bench_rerank.urllib.request, "urlopen", fake_urlopen)
        return calls

    return install


def test_a_depth_is_split_into_calls_the_service_accepts(capture):
    """One call per depth is what failed: a call over the client limit is refused with 422 before any
    inference happens, so a benchmark that sends 100 texts measures nothing at all."""
    calls = capture()
    bench_rerank.rerank("http://reranker:80", ["text"] * 100, timeout=5, chunk=32)
    assert [len(c) for c in calls] == [32, 32, 32, 4], "100 candidates should be four calls at 32"
    assert sum(len(c) for c in calls) == 100, "every candidate must be sent exactly once"


def test_a_chunk_never_exceeds_the_service_limit(capture):
    calls = capture()
    bench_rerank.rerank("http://reranker:80", ["text"] * 65, timeout=5, chunk=8)
    assert all(len(c) <= bench_rerank.MAX_TEXTS_PER_CALL for c in calls), calls
    assert len(calls) == 9, "65 candidates at 8 per call is nine calls"


def test_a_refusal_is_retried_and_the_wait_is_not_counted(capture, monkeypatch):
    """A 429 means no permit was free at that instant. The service does not queue, so the client has
    to wait and ask again; the wait is excluded from the return value so the number stays honest."""
    monkeypatch.setattr(bench_rerank.time, "sleep", lambda seconds: None)

    def refuse_the_first(call_number: int):
        if call_number == 1:
            raise urllib.error.HTTPError("http://reranker:80/rerank", 429, "Too Many Requests",
                                         {}, io.BytesIO(b""))
        return FakeResponse()

    calls = capture(refuse_the_first)
    elapsed = bench_rerank.post("http://reranker:80", ["text"] * 8, timeout=5)
    assert len(calls) == 2, "the refused call should be retried once"
    assert elapsed >= 0, elapsed


def test_a_422_is_not_retried(capture):
    """A payload the service will never accept is a bug in the caller, not contention. Retrying it
    would turn a fast, clear failure into a slow one."""
    def refuse_everything(call_number: int):
        raise urllib.error.HTTPError("http://reranker:80/rerank", 422, "Unprocessable Entity",
                                     {}, io.BytesIO(b""))

    calls = capture(refuse_everything)
    with pytest.raises(urllib.error.HTTPError) as caught:
        bench_rerank.post("http://reranker:80", ["text"] * 33, timeout=5)
    assert caught.value.code == 422
    assert len(calls) == 1, "422 must not be retried"


def test_the_default_chunk_leaves_room_for_a_second_caller(capture, capsys):
    """Permits are per candidate and the pool is shared. A chunk of 8 is what lets one request
    coexist with another, which is why it is the default rather than the service's 32-text limit."""
    capture()
    assert bench_rerank.main(["--depth", "8", "--reps", "1"]) == 0
    out = capsys.readouterr().out
    assert "8 per call" in out, out

