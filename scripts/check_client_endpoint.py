#!/usr/bin/env python3
"""Refuse a gateway or connector endpoint that lives on a domain we operate.

The ownership row is not satisfied by an endpoint merely being reachable. It has to be somewhere the client
controls. This check is deliberately small: an endpoint whose hostname is one of the vendor's domains is
refused, whatever its IP resolves to. A client endpoint on an IP or the client's own domain passes.

Usage:
    python3 scripts/check_client_endpoint.py https://gateway.client.example
"""
from __future__ import annotations

import sys
import urllib.parse

VENDOR_SUFFIXES = ("officeinference.com", "privateinferenceai.com")


def check(url: str) -> str:
    """An empty string when the endpoint is client-owned, otherwise the refusal sentence."""
    host = (urllib.parse.urlparse(url).hostname or "").lower().strip(".")
    if not host:
        return f"no hostname in {url!r}"
    for suffix in VENDOR_SUFFIXES:
        if host == suffix or host.endswith("." + suffix):
            return f"{host!r} is on a domain we operate; a client endpoint must not be"
    return ""


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if len(args) != 1:
        print("usage: check_client_endpoint.py <url>", file=sys.stderr)
        return 2
    problem = check(args[0])
    if problem:
        print(f"refused: {problem}")
        return 1
    print(f"client-owned: {args[0]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
