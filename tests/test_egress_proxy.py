"""The egress service: the allowlist, the token and the one fetch verb."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "services" / "egress"))

import proxy  # noqa: E402


class FakeResponse:
    def __init__(self, data=b"hello", status=200, content_type="text/plain", url="https://example.com/"):
        self.data, self.status = data, status
        self.headers = {"Content-Type": content_type}
        self.url = url

    def read(self, n=-1):
        return self.data if n < 0 else self.data[:n]

    def geturl(self):
        return self.url

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


def make_service(*, opener=None, allowlist=("example.com",)):
    return proxy.EgressService(allowlist, "secret", opener=opener)


def call(service, url, token="secret"):
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    return service.handle("POST", "/fetch", headers, json.dumps({"url": url}).encode())


def test_health_has_no_token_and_fetch_requires_one():
    service = make_service()
    assert service.handle("GET", "/health", {}, b"")[0] == 200
    assert call(service, "https://example.com", token=None)[0] == 401
    assert call(service, "https://example.com", token="wrong")[0] == 401


def test_an_unlisted_host_is_refused_before_any_fetch():
    called = []
    service = make_service(opener=lambda request, timeout: called.append(request))
    status, payload = call(service, "https://not-example.com/")
    assert status == 403
    assert "allowlist" in payload["detail"]
    assert called == []


def test_http_credentials_and_non_default_ports_are_refused():
    service = make_service()
    assert call(service, "http://example.com/")[0] == 403
    assert call(service, "https://user:pass@example.com/")[0] == 403
    assert call(service, "https://example.com:8443/")[0] == 403


def test_a_wildcard_allowlist_entry_covers_a_subdomain_but_not_the_bare_host():
    service = make_service(allowlist=("*.example.com",))
    assert service._validate("https://docs.example.com/")
    with pytest.raises(proxy.EgressError):
        service._validate("https://example.com/")


def test_fetch_returns_the_text_and_the_metadata():
    service = make_service(opener=lambda request, timeout: FakeResponse(b"hello web"))
    status, result = call(service, "https://example.com/page")
    assert status == 200
    assert result["text"] == "hello web"
    assert result["bytes"] == 9
    assert result["status"] == 200


def test_a_response_over_the_byte_cap_is_refused():
    service = make_service(opener=lambda request, timeout: FakeResponse(b"x" * 20))
    service.max_bytes = 10
    status, payload = call(service, "https://example.com/")
    assert status == 403
    assert "larger" in payload["detail"]


def test_a_network_failure_is_a_typed_egress_error():
    def broken(request, timeout):
        raise OSError("no route")
    service = make_service(opener=broken)
    status, payload = call(service, "https://example.com/")
    assert status == 403
    assert "OSError" in payload["detail"]
