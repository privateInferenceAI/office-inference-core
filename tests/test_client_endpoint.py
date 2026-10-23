"""The client-owned endpoint check."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import check_client_endpoint as cord  # noqa: E402


def test_a_client_ip_or_domain_passes():
    assert cord.check("http://52.91.69.1:8080") == ""
    assert cord.check("https://gateway.client.example") == ""


def test_a_vendor_domain_is_refused():
    assert "domain we operate" in cord.check("https://mcp.officeinference.com")
    assert "domain we operate" in cord.check("https://officeinference.com")
    assert "domain we operate" in cord.check("https://api.privateinferenceai.com")


def test_no_hostname_is_refused():
    assert "no hostname" in cord.check("not a url")
